"""Unit and integration tests for Task Checkpoints, Two-Phase Journaling, and Crash Recovery (Task 3.5 — Milestone 4)."""
import unittest
import tempfile
import gc
import os
import shutil
from pathlib import Path
from datetime import datetime, timezone

from src.database.connection import DatabaseManager
from src.database.migrations import MigrationManager
from src.database.repositories.channel_repository import ChannelRepository
from src.database.repositories.task_repository import TaskRepository
from src.database.repositories.file_operation_repository import FileOperationRepository
from src.database.repositories.asset_repository import AssetRepository
from src.database.repositories.audit_repository import AuditRepository
from src.services.audit_service import AuditService
from src.services.checkpoint_service import CheckpointService
from src.services.recovery_service import RecoveryService, RecoverySummary
from src.database.models import (
    ChannelCreate,
    TaskCreate,
    MediaAssetCreate,
    FileOperationModel,
)
from src.core.constants import (
    TaskStatus,
    TaskType,
    AssetType,
    FileOperationState,
    FileOperationType,
)
from src.core.exceptions import (
    StaleExecutionTokenException,
    AuditIntegrityException,
    AssetPathCollisionException,
)
from src.utils.file_utils import calculate_file_sha256, safe_resolve_path
from src.core.config import get_settings


class TestCheckpointAndRecovery(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root_path = Path(self.temp_dir.name)
        self.db_path = self.root_path / "test_recovery.db"
        self.channels_dir = self.root_path / "channels"
        self.channels_dir.mkdir(parents=True, exist_ok=True)

        self.settings = get_settings()
        self._orig_channels_root = self.settings.channels_root
        self.settings.channels_root = self.channels_dir

        self.db_manager = DatabaseManager(db_path=self.db_path, busy_timeout_ms=15000)
        self.migration_manager = MigrationManager(self.db_manager)
        self.migration_manager.run_migrations()

        self.channel_repo = ChannelRepository(self.db_manager)
        self.task_repo = TaskRepository(self.db_manager)
        self.file_op_repo = FileOperationRepository(self.db_manager)
        self.asset_repo = AssetRepository(self.db_manager)
        self.audit_repo = AuditRepository(self.db_manager)
        self.audit_service = AuditService(self.audit_repo)

        self.checkpoint_service = CheckpointService(
            task_repository=self.task_repo,
            file_operation_repository=self.file_op_repo,
            asset_repository=self.asset_repo,
            audit_service=self.audit_service,
            db_manager=self.db_manager,
        )

        self.recovery_service = RecoveryService(
            file_operation_repository=self.file_op_repo,
            asset_repository=self.asset_repo,
            task_repository=self.task_repo,
            audit_repository=self.audit_repo,
            audit_service=self.audit_service,
            db_manager=self.db_manager,
        )

        # Create test channel
        self.channel_id = "ch_recovery_test"
        self.channel_repo.create(
            ChannelCreate(id=self.channel_id, name="Recovery Test", handle="@recovery", niche="testing")
        )
        self.channel_dir = self.channels_dir / self.channel_id
        (self.channel_dir / "media_vault" / "originals" / "images").mkdir(parents=True, exist_ok=True)
        (self.channel_dir / "media_vault" / "cache").mkdir(parents=True, exist_ok=True)
        (self.channel_dir / "media_vault" / "quarantine").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.settings.channels_root = self._orig_channels_root
        gc.collect()
        self.temp_dir.cleanup()

    def test_checkpoint_protocol_end_to_end(self):
        """Scenario 1: End-to-end 5-step checkpoint protocol execution."""
        task = self.task_repo.create_task(
            TaskCreate(channel_id=self.channel_id, task_type=TaskType.IMAGE_GENERATION, idempotency_key="cp_e2e_1")
        )
        claimed = self.task_repo.atomic_claim_next_task(worker_id="w_e2e", lease_duration_sec=60)
        self.assertEqual(claimed.execution_token, 1)

        target_rel = Path("media_vault/originals/images/rendered_frame_01.png")

        def mock_work(temp_file: Path):
            temp_file.write_bytes(b"PNG_SAMPLE_DATA_BYTES")
            return {"width": 1920, "height": 1080}

        cp = self.checkpoint_service.execute_step(
            task=claimed,
            step_name="step_render_frame",
            step_index=1,
            work_fn=mock_work,
            target_rel_path=target_rel,
            actor="test_worker",
        )

        # Verify checkpoint persisted
        self.assertIsNotNone(cp)
        self.assertEqual(cp.step_name, "step_render_frame")
        self.assertEqual(cp.step_index, 1)
        self.assertEqual(cp.execution_token, 1)
        self.assertEqual(cp.step_data["width"], 1920)

        # Verify target file exists in originals
        target_abs = self.channel_dir / target_rel
        self.assertTrue(target_abs.exists())
        self.assertEqual(target_abs.read_bytes(), b"PNG_SAMPLE_DATA_BYTES")

        # Verify file operation completed
        op_id = f"op_{task.id}_step_render_frame_tok1"
        op = self.file_op_repo.get_by_id(op_id)
        self.assertEqual(op.state, FileOperationState.COMPLETED)

        # Verify fast-forward if step is re-executed
        cp2 = self.checkpoint_service.execute_step(
            task=claimed,
            step_name="step_render_frame",
            step_index=1,
            work_fn=mock_work,
            target_rel_path=target_rel,
        )
        self.assertEqual(cp2.id, cp.id)

    def test_crash_before_file_writing(self):
        """Scenario 2: Crash occurs before file writing (operation left in PENDING)."""
        temp_file = self.channel_dir / "media_vault" / "cache" / "pending_crash.tmp"
        target_file = self.channel_dir / "media_vault" / "originals" / "images" / "target.png"

        self.file_op_repo.create(
            FileOperationModel(
                id="op_crash_pending",
                operation_type=FileOperationType.RENDER,
                channel_id=self.channel_id,
                temp_path=str(temp_file),
                target_path=str(target_file),
                state=FileOperationState.PENDING,
                created_at="2026-11-16T12:00:00Z",
                updated_at="2026-11-16T12:00:00Z",
            )
        )

        summary = self.recovery_service.reconcile_recovery(self.channel_id)
        self.assertEqual(summary.reconciled_ops, 1)
        self.assertEqual(summary.failed_ops, 1)

        op = self.file_op_repo.get_by_id("op_crash_pending")
        self.assertEqual(op.state, FileOperationState.FAILED)
        self.assertFalse(target_file.exists())

    def test_crash_after_file_writing_before_db_commit(self):
        """Scenario 3: Crash occurs after file writing but before DB commit (FILE_WRITTEN)."""
        temp_file = self.channel_dir / "media_vault" / "cache" / "written_crash.tmp"
        target_file = self.channel_dir / "media_vault" / "originals" / "images" / "target_written.png"
        payload = b"INTACT_VALID_DATA_FROM_CRASHED_STEP"
        temp_file.write_bytes(payload)
        expected_sha = calculate_file_sha256(temp_file)

        self.file_op_repo.create(
            FileOperationModel(
                id="op_crash_written",
                operation_type=FileOperationType.RENDER,
                channel_id=self.channel_id,
                temp_path=str(temp_file),
                target_path=str(target_file),
                file_sha256=expected_sha,
                file_size_bytes=len(payload),
                state=FileOperationState.FILE_WRITTEN,
                created_at="2026-11-16T12:00:00Z",
                updated_at="2026-11-16T12:00:00Z",
            )
        )

        # Recovery should finalize move atomically without re-generating
        summary = self.recovery_service.reconcile_recovery(self.channel_id)
        self.assertEqual(summary.reconciled_ops, 1)
        self.assertEqual(summary.failed_ops, 0)
        self.assertTrue(target_file.exists())
        self.assertEqual(target_file.read_bytes(), payload)
        self.assertFalse(temp_file.exists())

        op = self.file_op_repo.get_by_id("op_crash_written")
        self.assertEqual(op.state, FileOperationState.COMPLETED)

    def test_crash_after_db_commit_before_file_finalization(self):
        """Scenario 4: Crash occurs after DB commit before file finalization (DB_COMMITTED)."""
        temp_file = self.channel_dir / "media_vault" / "cache" / "commit_crash.tmp"
        target_file = self.channel_dir / "media_vault" / "originals" / "images" / "target_committed.png"
        payload = b"COMMITTED_PAYLOAD_READY_FOR_MOVE"
        temp_file.write_bytes(payload)

        self.file_op_repo.create(
            FileOperationModel(
                id="op_crash_committed",
                operation_type=FileOperationType.RENDER,
                channel_id=self.channel_id,
                temp_path=str(temp_file),
                target_path=str(target_file),
                state=FileOperationState.DB_COMMITTED,
                created_at="2026-11-16T12:00:00Z",
                updated_at="2026-11-16T12:00:00Z",
            )
        )

        summary = self.recovery_service.reconcile_recovery(self.channel_id)
        self.assertEqual(summary.reconciled_ops, 1)
        self.assertTrue(target_file.exists())
        self.assertEqual(target_file.read_bytes(), payload)

        op = self.file_op_repo.get_by_id("op_crash_committed")
        self.assertEqual(op.state, FileOperationState.COMPLETED)

    def test_crash_after_file_finalization_before_audit_flush(self):
        """Scenario 5: File was finalized at target path before audit flush."""
        target_file = self.channel_dir / "media_vault" / "originals" / "images" / "target_finalized.png"
        target_file.write_bytes(b"ALREADY_AT_FINAL_DESTINATION")

        self.file_op_repo.create(
            FileOperationModel(
                id="op_crash_finalized",
                operation_type=FileOperationType.RENDER,
                channel_id=self.channel_id,
                temp_path=str(self.channel_dir / "media_vault" / "cache" / "dummy.tmp"),
                target_path=str(target_file),
                state=FileOperationState.FILE_FINALIZED,
                created_at="2026-11-16T12:00:00Z",
                updated_at="2026-11-16T12:00:00Z",
            )
        )

        summary = self.recovery_service.reconcile_recovery(self.channel_id)
        self.assertEqual(summary.reconciled_ops, 1)
        op = self.file_op_repo.get_by_id("op_crash_finalized")
        self.assertEqual(op.state, FileOperationState.COMPLETED)

    def test_repeated_recovery_runs_idempotency(self):
        """Scenario 6: Running reconcile_recovery multiple times produces consistent idempotent outcomes."""
        temp_file = self.channel_dir / "media_vault" / "cache" / "idemp_crash.tmp"
        target_file = self.channel_dir / "media_vault" / "originals" / "images" / "target_idemp.png"
        temp_file.write_bytes(b"IDEMPOTENCY_PAYLOAD")
        sha = calculate_file_sha256(temp_file)

        self.file_op_repo.create(
            FileOperationModel(
                id="op_idemp_test",
                operation_type=FileOperationType.RENDER,
                channel_id=self.channel_id,
                temp_path=str(temp_file),
                target_path=str(target_file),
                file_sha256=sha,
                state=FileOperationState.FILE_WRITTEN,
                created_at="2026-11-16T12:00:00Z",
                updated_at="2026-11-16T12:00:00Z",
            )
        )

        # Run 1: Reconciles operation
        s1 = self.recovery_service.reconcile_recovery(self.channel_id)
        self.assertEqual(s1.reconciled_ops, 1)

        # Run 2: Zero pending operations; no-op
        s2 = self.recovery_service.reconcile_recovery(self.channel_id)
        self.assertEqual(s2.reconciled_ops, 0)

        # Run 3: Still clean and idempotent
        s3 = self.recovery_service.reconcile_recovery(self.channel_id)
        self.assertEqual(s3.reconciled_ops, 0)

    def test_missing_files_recovery(self):
        """Scenario 7: Both temp file and target file are missing."""
        missing_temp = self.channel_dir / "media_vault" / "cache" / "nonexistent.tmp"
        missing_target = self.channel_dir / "media_vault" / "originals" / "images" / "missing.png"

        self.file_op_repo.create(
            FileOperationModel(
                id="op_missing_files",
                operation_type=FileOperationType.RENDER,
                channel_id=self.channel_id,
                temp_path=str(missing_temp),
                target_path=str(missing_target),
                file_sha256="abc123expectedhash",
                state=FileOperationState.FILE_WRITTEN,
                created_at="2026-11-16T12:00:00Z",
                updated_at="2026-11-16T12:00:00Z",
            )
        )

        summary = self.recovery_service.reconcile_recovery(self.channel_id)
        self.assertEqual(summary.failed_ops, 1)
        op = self.file_op_repo.get_by_id("op_missing_files")
        self.assertEqual(op.state, FileOperationState.FAILED)
        self.assertIn("Neither target nor temporary file found", op.error_message)

    def test_unexpected_unowned_files_quarantine(self):
        """Scenario 8: Unowned unexpected file at target path with SHA-256 mismatch is quarantined, NOT deleted."""
        target_file = self.channel_dir / "media_vault" / "originals" / "images" / "corrupted_target.png"
        target_file.write_bytes(b"CORRUPTED_OR_UNKNOWN_BYTES_AT_TARGET")

        self.file_op_repo.create(
            FileOperationModel(
                id="op_quarantine_test",
                operation_type=FileOperationType.RENDER,
                channel_id=self.channel_id,
                temp_path=str(self.channel_dir / "media_vault" / "cache" / "temp.tmp"),
                target_path=str(target_file),
                file_sha256="expected_different_sha256",
                state=FileOperationState.FILE_WRITTEN,
                created_at="2026-11-16T12:00:00Z",
                updated_at="2026-11-16T12:00:00Z",
            )
        )

        summary = self.recovery_service.reconcile_recovery(self.channel_id)
        self.assertEqual(summary.quarantined_files, 1)
        self.assertEqual(summary.failed_ops, 1)

        # Target file was NOT deleted in place; moved to quarantine
        self.assertFalse(target_file.exists())
        quarantine_files = list((self.channel_dir / "media_vault" / "quarantine").glob("*.quarantine_*"))
        self.assertEqual(len(quarantine_files), 1)
        self.assertEqual(quarantine_files[0].read_bytes(), b"CORRUPTED_OR_UNKNOWN_BYTES_AT_TARGET")

    def test_sha256_mismatch_in_temp_file(self):
        """Scenario 9: Temp file in cache has corrupted SHA-256."""
        temp_file = self.channel_dir / "media_vault" / "cache" / "corrupt_temp.tmp"
        target_file = self.channel_dir / "media_vault" / "originals" / "images" / "target_clean.png"
        temp_file.write_bytes(b"CORRUPTED_TEMP_PAYLOAD")

        self.file_op_repo.create(
            FileOperationModel(
                id="op_corrupt_temp",
                operation_type=FileOperationType.RENDER,
                channel_id=self.channel_id,
                temp_path=str(temp_file),
                target_path=str(target_file),
                file_sha256="expected_sha_that_does_not_match_temp",
                state=FileOperationState.FILE_WRITTEN,
                created_at="2026-11-16T12:00:00Z",
                updated_at="2026-11-16T12:00:00Z",
            )
        )

        summary = self.recovery_service.reconcile_recovery(self.channel_id)
        self.assertEqual(summary.failed_ops, 1)
        self.assertEqual(summary.cleaned_temps, 1)
        # Corrupted temp was cleaned; target was never created
        self.assertFalse(temp_file.exists())
        self.assertFalse(target_file.exists())

    def test_original_media_preservation_absolute_rule(self):
        """Scenario 10: Valid existing original media asset is NEVER deleted or overwritten on hash mismatch."""
        target_rel = "media_vault/originals/images/hero_character.png"
        target_file = self.channel_dir / target_rel
        original_bytes = b"CRITICAL_PROPRIETARY_ORIGINAL_HERO_ASSET"
        target_file.write_bytes(original_bytes)
        original_sha = calculate_file_sha256(target_file)

        # Register this original in media_assets
        self.asset_repo.create(
            MediaAssetCreate(
                id="ast_hero_original",
                channel_id=self.channel_id,
                file_name="hero_character.png",
                file_path=target_rel,
                file_sha256=original_sha,
                file_size_bytes=len(original_bytes),
                mime_type="image/png",
                asset_type=AssetType.IMAGE,
            )
        )

        # Now simulate an interrupted operation targeting the exact same path with a DIFFERENT hash
        temp_file = self.channel_dir / "media_vault" / "cache" / "rogue_temp.tmp"
        temp_file.write_bytes(b"ROGUE_COMPETING_BYTES")

        self.file_op_repo.create(
            FileOperationModel(
                id="op_rogue_overwrite",
                operation_type=FileOperationType.RENDER,
                channel_id=self.channel_id,
                temp_path=str(temp_file),
                target_path=str(target_file),
                file_sha256="mismatched_rogue_hash_123",
                state=FileOperationState.FILE_WRITTEN,
                created_at="2026-11-16T12:00:00Z",
                updated_at="2026-11-16T12:00:00Z",
            )
        )

        summary = self.recovery_service.reconcile_recovery(self.channel_id)
        self.assertEqual(summary.failed_ops, 1)

        # ABSOLUTE RULE CHECK: Original file was NOT deleted, NOT overwritten, NOT moved to quarantine!
        self.assertTrue(target_file.exists())
        self.assertEqual(target_file.read_bytes(), original_bytes)
        # Existing asset record intact
        asset = self.asset_repo.get_by_id(self.channel_id, "ast_hero_original")
        self.assertIsNotNone(asset)
        self.assertEqual(asset.file_sha256, original_sha)

    def test_stale_execution_token_during_recovery(self):
        """Scenario 11: Interrupted operation from an old token does not corrupt a reaped task."""
        task = self.task_repo.create_task(
            TaskCreate(channel_id=self.channel_id, task_type=TaskType.GENERAL, idempotency_key="tok_stale_rec")
        )
        c1 = self.task_repo.atomic_claim_next_task(worker_id="w_old", lease_duration_sec=10)
        self.assertEqual(c1.execution_token, 1)

        # Worker crashed during step 1 with token 1
        target_file = self.channel_dir / "media_vault" / "originals" / "images" / "stale_step.png"
        target_file.write_bytes(b"STALE_STEP_BYTES")
        sha = calculate_file_sha256(target_file)

        self.file_op_repo.create(
            FileOperationModel(
                id=f"op_{task.id}_step1_tok1",
                operation_type=FileOperationType.RENDER,
                channel_id=self.channel_id,
                temp_path=str(self.channel_dir / "media_vault" / "cache" / "dummy.tmp"),
                target_path=str(target_file),
                file_sha256=sha,
                state=FileOperationState.FILE_WRITTEN,
                created_at="2026-11-16T12:00:00Z",
                updated_at="2026-11-16T12:00:00Z",
            )
        )

        # Task was reaped and rescheduled -> token becomes 2!
        self.task_repo.reap_stale_task(task.id, "w_old", stale_token=1)
        refreshed_task = self.task_repo.get_by_id(task.id)
        self.assertEqual(refreshed_task.execution_token, 2)

        # Run recovery
        self.recovery_service.reconcile_recovery(self.channel_id)

        # Checkpoints for token 1 must NOT be saved for task with token 2!
        cps = self.task_repo.get_checkpoints(task.id)
        self.assertEqual(len(cps), 0)

    def test_audit_integrity_mismatch_hard_locks_recovery(self):
        """Scenario 12: Recovery strictly halts and raises AuditIntegrityException when channel is export locked."""
        # Hard lock export gate
        self.audit_repo.lock_channel_export(self.channel_id, "Tampered chain detected")

        # Recovery on locked channel must immediately raise AuditIntegrityException
        with self.assertRaises(AuditIntegrityException):
            self.recovery_service.reconcile_recovery(self.channel_id)

    def test_reconcile_interrupted_running_tasks(self):
        """Scenario 13: Tasks left in RUNNING during unexpected crash are safely re-scheduled with bumped token."""
        task = self.task_repo.create_task(
            TaskCreate(channel_id=self.channel_id, task_type=TaskType.GENERAL, idempotency_key="crash_running_task")
        )
        c = self.task_repo.atomic_claim_next_task(worker_id="w_crashed", lease_duration_sec=60)
        self.assertEqual(c.status, TaskStatus.RUNNING)
        self.assertEqual(c.execution_token, 1)

        # Recovery detects interrupted running task and reschedules it to RETRY_PENDING
        summary = self.recovery_service.reconcile_recovery(self.channel_id)
        self.assertEqual(summary.reconciled_tasks, 1)

        t_after = self.task_repo.get_by_id(task.id)
        self.assertEqual(t_after.status, TaskStatus.RETRY_PENDING)
        self.assertEqual(t_after.execution_token, 2)  # Zombie worker fenced out!

    def test_checkpoint_rejection_when_heartbeat_lease_lost(self):
        """Scenario 14: Step execution aborts and deletes temp file when heartbeat lease is lost."""
        task = self.task_repo.create_task(
            TaskCreate(channel_id=self.channel_id, task_type=TaskType.GENERAL, idempotency_key="lease_lost_step")
        )
        claimed = self.task_repo.atomic_claim_next_task(worker_id="w_lost", lease_duration_sec=60)

        from src.services.heartbeat_daemon import HeartbeatDaemon
        heartbeat = HeartbeatDaemon(
            task_repository=self.task_repo,
            channel_id=self.channel_id,
            task_id=claimed.id,
            worker_id="w_lost",
            execution_token=claimed.execution_token,
        )
        # Artificially set lease lost
        heartbeat._lease_lost_event.set()

        target_rel = Path("media_vault/originals/images/should_not_exist.png")

        def work(p):
            p.write_bytes(b"DATA")
            return {}

        with self.assertRaises(StaleExecutionTokenException):
            self.checkpoint_service.execute_step(
                task=claimed,
                step_name="step_lease_lost",
                step_index=1,
                work_fn=work,
                target_rel_path=target_rel,
                heartbeat=heartbeat,
            )

        self.assertFalse((self.channel_dir / target_rel).exists())

    def test_crash_during_recovery_itself_resumption(self):
        """Scenario 15: Interrupted recovery pass resumes seamlessly on next recovery pass."""
        # Create 2 uncompleted operations
        t1 = self.channel_dir / "media_vault" / "cache" / "r1.tmp"
        t1.write_bytes(b"DATA_1")
        sha1 = calculate_file_sha256(t1)
        dest1 = self.channel_dir / "media_vault" / "originals" / "images" / "dest1.png"

        t2 = self.channel_dir / "media_vault" / "cache" / "r2.tmp"
        t2.write_bytes(b"DATA_2")
        sha2 = calculate_file_sha256(t2)
        dest2 = self.channel_dir / "media_vault" / "originals" / "images" / "dest2.png"

        self.file_op_repo.create(
            FileOperationModel(
                id="op_rec_pass1",
                operation_type=FileOperationType.RENDER,
                channel_id=self.channel_id,
                temp_path=str(t1),
                target_path=str(dest1),
                file_sha256=sha1,
                state=FileOperationState.FILE_WRITTEN,
                created_at="2026-11-16T12:00:00Z",
                updated_at="2026-11-16T12:00:00Z",
            )
        )
        self.file_op_repo.create(
            FileOperationModel(
                id="op_rec_pass2",
                operation_type=FileOperationType.RENDER,
                channel_id=self.channel_id,
                temp_path=str(t2),
                target_path=str(dest2),
                file_sha256=sha2,
                state=FileOperationState.FILE_WRITTEN,
                created_at="2026-11-16T12:00:00Z",
                updated_at="2026-11-16T12:00:00Z",
            )
        )

        # Simulate first recovery pass reconciling op 1 only (simulating crash before op 2)
        op1 = self.file_op_repo.get_by_id("op_rec_pass1")
        summary_partial = RecoverySummary()
        self.recovery_service._reconcile_single_file_op(op1, summary_partial, self.settings)
        self.assertEqual(summary_partial.reconciled_ops, 1)
        self.assertTrue(dest1.exists())

        # Second recovery pass starts afresh
        summary_full = self.recovery_service.reconcile_recovery(self.channel_id)
        # Should reconcile op 2 without re-processing or corrupting op 1
        self.assertEqual(summary_full.reconciled_ops, 1)
        self.assertTrue(dest2.exists())
        self.assertEqual(self.file_op_repo.get_by_id("op_rec_pass1").state, FileOperationState.COMPLETED)
        self.assertEqual(self.file_op_repo.get_by_id("op_rec_pass2").state, FileOperationState.COMPLETED)


if __name__ == "__main__":
    unittest.main()
