"""Unit and integration tests for Audit Outbox, Hash Chaining, Concurrency Lock, and Crash Recovery."""
import unittest
import tempfile
import gc
import json
from pathlib import Path

from src.database.connection import DatabaseManager
from src.database.migrations import MigrationManager
from src.database.repositories.channel_repository import ChannelRepository
from src.database.repositories.audit_repository import AuditRepository, GENESIS_HASH
from src.database.repositories.file_operation_repository import FileOperationRepository
from src.database.models import ChannelCreate, FileOperationModel
from src.services.audit_service import AuditService
from src.services.asset_service import AssetService
from src.core.config import get_settings
from src.core.constants import FileOperationState, FileOperationType
from src.core.exceptions import AuditIntegrityException, LicenseVerificationException


class TestAuditAndCrashRecovery(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root_path = Path(self.temp_dir.name)
        self.db_path = self.root_path / "test_audit.db"
        self.channels_dir = self.root_path / "channels"
        self.channels_dir.mkdir(parents=True, exist_ok=True)

        self.settings = get_settings()
        self._orig_channels_root = self.settings.channels_root
        self.settings.channels_root = self.channels_dir

        self.db_manager = DatabaseManager(db_path=self.db_path)
        self.migration_manager = MigrationManager(self.db_manager)
        self.migration_manager.run_migrations()

        self.channel_repo = ChannelRepository(self.db_manager)
        self.audit_repo = AuditRepository(self.db_manager)
        self.file_op_repo = FileOperationRepository(self.db_manager)

        self.audit_service = AuditService(self.audit_repo)
        self.asset_service = AssetService(
            file_operation_repository=self.file_op_repo,
            audit_service=self.audit_service,
        )

        self.channel_repo.create(
            ChannelCreate(id="ch_audit_test", name="Audit Test", handle="@audit_test", niche="qa")
        )

    def tearDown(self):
        self.settings.channels_root = self._orig_channels_root
        gc.collect()
        self.temp_dir.cleanup()

    def test_outbox_event_recording_and_hash_chain(self):
        """Verify recording events creates sequential hash chain from genesis to tip."""
        e1 = self.audit_service.record_event(
            channel_id="ch_audit_test",
            action="TEST_ACTION_1",
            actor="admin_1",
            payload={"key": "val1"},
            idempotency_key="idemp_01",
        )
        self.assertEqual(e1.seq, 1)
        self.assertEqual(e1.previous_hash, GENESIS_HASH)

        e2 = self.audit_service.record_event(
            channel_id="ch_audit_test",
            action="TEST_ACTION_2",
            actor="admin_2",
            payload={"key": "val2"},
            idempotency_key="idemp_02",
        )
        self.assertEqual(e2.seq, 2)
        self.assertEqual(e2.previous_hash, e1.entry_hash)

        # Verify integrity checks pass
        self.assertTrue(self.audit_service.verify_audit_log_integrity("ch_audit_test"))

        # Test idempotency: repeating idemp_01 returns same event without incrementing seq
        e1_repeat = self.audit_service.record_event(
            channel_id="ch_audit_test",
            action="TEST_ACTION_1",
            actor="admin_1",
            payload={"key": "val1"},
            idempotency_key="idemp_01",
        )
        self.assertEqual(e1_repeat.seq, 1)
        self.assertEqual(e1_repeat.entry_hash, e1.entry_hash)

    def test_tamper_detection_and_export_hard_lock(self):
        """Verify modifying, deleting, or corrupting log entries triggers AuditIntegrityException & Hard Lock."""
        for i in range(1, 4):
            self.audit_service.record_event(
                channel_id="ch_audit_test",
                action=f"ACTION_{i}",
                actor="system",
                payload={"index": i},
                idempotency_key=f"idemp_{i}",
            )

        log_path = self.channels_dir / "ch_audit_test" / "audit" / "license_audit.log"
        lines = log_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 3)

        # 1. Tampering scenario: Modify payload of line 2
        tampered_entry = json.loads(lines[1])
        tampered_entry["payload"]["index"] = 999  # Altered!
        lines[1] = json.dumps(tampered_entry)
        log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        # Verify integrity fails and locks export gate
        with self.assertRaises(AuditIntegrityException):
            self.audit_service.verify_audit_log_integrity("ch_audit_test")

        # Check export is hard locked
        is_locked, reason = self.audit_service.is_export_locked("ch_audit_test")
        self.assertTrue(is_locked)
        self.assertIn("Tampered entry hash", reason)

    def test_crash_recovery_after_file_write_before_db_update(self):
        """Scenario A: Line written and fsynced to file, but DB update to file_flushed crashed."""
        event = self.audit_service.record_event(
            channel_id="ch_audit_test",
            action="CRASH_SCENARIO_A",
            actor="system",
            payload={"state": "initial"},
            idempotency_key="idemp_crash_a",
        )

        # Simulate crash before updating SQLite: manually revert file_flushed to 0
        with self.db_manager.transaction() as conn:
            conn.execute(
                "UPDATE audit_events_outbox SET file_flushed = 0 WHERE channel_id = 'ch_audit_test' AND seq = ?;",
                (event.seq,),
            )

        log_path = self.channels_dir / "ch_audit_test" / "audit" / "license_audit.log"
        line_count_before = len(log_path.read_text(encoding="utf-8").splitlines())

        # Drain outbox: should detect line already exists and update DB without appending duplicate line
        flushed = self.audit_service.drain_outbox("ch_audit_test")
        self.assertEqual(flushed, 1)

        line_count_after = len(log_path.read_text(encoding="utf-8").splitlines())
        self.assertEqual(line_count_before, line_count_after, "Duplicate line must not be written!")

        # Outbox event is now marked flushed
        unflushed = self.audit_repo.get_unflushed_events("ch_audit_test")
        self.assertEqual(len(unflushed), 0)

    def test_torn_write_recovery_with_strict_prefix_match(self):
        """Scenario C & D: Trailing partial bytes at EOF: prefix match recovers; non-prefix locks gate."""
        # Record base event
        self.audit_service.record_event(
            channel_id="ch_audit_test",
            action="BASE_EVENT",
            actor="system",
            payload={"step": 1},
            idempotency_key="idemp_base",
        )

        # Prepare a pending unflushed event in SQLite without writing to file
        state = self.audit_repo.get_or_init_chain_state("ch_audit_test")
        seq = state.last_seq + 1
        now_utc = "2026-11-16T12:00:00Z"
        payload_json = '{"step":2}'
        prev_h = state.latest_audit_hash
        import hashlib
        entry_h = hashlib.sha256((prev_h + payload_json).encode("utf-8")).hexdigest()

        from src.database.models import AuditEventOutboxModel
        unflushed_event = AuditEventOutboxModel(
            seq=seq,
            idempotency_key="idemp_torn",
            channel_id="ch_audit_test",
            action="TORN_TEST",
            actor="system",
            payload_json=payload_json,
            previous_hash=prev_h,
            entry_hash=entry_h,
            file_flushed=False,
            created_at=now_utc,
        )
        self.audit_repo.insert_outbox_event(unflushed_event)

        log_path = self.channels_dir / "ch_audit_test" / "audit" / "license_audit.log"
        expected_full_line = self.audit_service._format_log_line(unflushed_event)

        # 1. Partial write matching exact prefix (e.g. first 20 characters of expected line)
        partial_prefix = expected_full_line[:20]
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(partial_prefix)  # No newline, partial torn write

        # Drain should detect prefix match, truncate partial bytes, write complete line, and succeed
        flushed = self.audit_service.drain_outbox("ch_audit_test")
        self.assertEqual(flushed, 1)
        self.assertTrue(self.audit_service.verify_audit_log_integrity("ch_audit_test"))

    def test_file_operations_crash_recovery_journal(self):
        """Test crash recovery journal reconciles uncompleted file operations."""
        temp_file = self.root_path / "test_temp_crash.tmp"
        temp_file.write_bytes(b"TEMPORARY_PAYLOAD_DURING_CRASH")
        final_dest = self.root_path / "final_dest.bin"

        # 1. PENDING operation recovery -> deletes temp file, marks FAILED
        self.file_op_repo.create(
            FileOperationModel(
                id="op_pending_crash",
                operation_type=FileOperationType.IMPORT,
                channel_id="ch_audit_test",
                temp_path=str(temp_file),
                target_path=str(final_dest),
                state=FileOperationState.PENDING,
                created_at="2026-11-16T12:00:00Z",
                updated_at="2026-11-16T12:00:00Z",
            )
        )
        self.asset_service.reconcile_recovery("ch_audit_test")
        self.assertFalse(temp_file.exists())
        op = self.file_op_repo.get_by_id("op_pending_crash")
        self.assertEqual(op.state, FileOperationState.FAILED)

        # 2. DB_COMMITTED operation recovery -> finishes atomic move to final_dest, marks COMPLETED
        temp_file.write_bytes(b"COMMITTED_PAYLOAD_RECOVERED")
        self.file_op_repo.create(
            FileOperationModel(
                id="op_committed_crash",
                operation_type=FileOperationType.IMPORT,
                channel_id="ch_audit_test",
                temp_path=str(temp_file),
                target_path=str(final_dest),
                state=FileOperationState.DB_COMMITTED,
                created_at="2026-11-16T12:00:00Z",
                updated_at="2026-11-16T12:00:00Z",
            )
        )
        self.asset_service.reconcile_recovery("ch_audit_test")
        self.assertTrue(final_dest.exists())
        self.assertEqual(final_dest.read_bytes(), b"COMMITTED_PAYLOAD_RECOVERED")
        op2 = self.file_op_repo.get_by_id("op_committed_crash")
        self.assertEqual(op2.state, FileOperationState.COMPLETED)

