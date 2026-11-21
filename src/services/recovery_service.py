"""Crash Recovery and Reconciliation service implementing two-phase journal recovery, MAM protection, and task recovery."""
import os
import shutil
import uuid
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field
from datetime import datetime, timezone

from src.database.repositories.file_operation_repository import FileOperationRepository
from src.database.repositories.asset_repository import AssetRepository
from src.database.repositories.task_repository import TaskRepository
from src.database.repositories.audit_repository import AuditRepository
from src.services.audit_service import AuditService
from src.database.models import (
    FileOperationModel,
    MediaAssetCreate,
    TaskCheckpointCreate,
    TaskModel,
)
from src.core.constants import (
    FileOperationState,
    TaskStatus,
    AssetType,
    LegalBasis,
    VerificationStatus,
)
from src.core.exceptions import (
    AuditIntegrityException,
    AssetPathCollisionException,
    StaleExecutionTokenException,
)
from src.utils.file_utils import calculate_file_sha256, safe_resolve_path
from src.core.config import get_settings
from src.core.logger import get_logger

logger = get_logger("studio.services.recovery")


@dataclass
class RecoverySummary:
    """Summary of operations performed during crash reconciliation."""
    reconciled_ops: int = 0
    adopted_assets: int = 0
    quarantined_files: int = 0
    failed_ops: int = 0
    cleaned_temps: int = 0
    reconciled_tasks: int = 0
    details: List[Dict[str, Any]] = field(default_factory=list)


class RecoveryService:
    """Orchestrates comprehensive startup recovery, crash reconciliation, and original media protection.

    Rules strictly enforced:
    - Never delete or overwrite files under channels/{channel_id}/media_vault/originals/!
    - Never delete an original because SHA-256 does not match!
    - Never replace an original based solely on a checkpoint!
    - Quarantine unexpected/unowned files with hash mismatch to media_vault/quarantine/.
    - Preserve audit evidence and respect export hard-locks.
    """

    def __init__(
        self,
        file_operation_repository: Optional[FileOperationRepository] = None,
        asset_repository: Optional[AssetRepository] = None,
        task_repository: Optional[TaskRepository] = None,
        audit_repository: Optional[AuditRepository] = None,
        audit_service: Optional[AuditService] = None,
        db_manager: Optional[Any] = None,
        service_logger: Optional[logging.Logger] = None,
    ) -> None:
        self.db_manager = (
            db_manager
            or (file_operation_repository and file_operation_repository.db_manager)
            or (audit_repository and audit_repository.db_manager)
            or (task_repository and task_repository.db_manager)
            or (asset_repository and asset_repository.db_manager)
        )
        from src.database.connection import DatabaseManager
        if self.db_manager is None:
            self.db_manager = DatabaseManager()

        self._file_op_repo = file_operation_repository or FileOperationRepository(db_manager=self.db_manager)
        self._asset_repo = asset_repository or AssetRepository(db_manager=self.db_manager)
        self._task_repo = task_repository or TaskRepository(db_manager=self.db_manager)
        self._audit_repo = audit_repository or AuditRepository(db_manager=self.db_manager)
        self._audit = audit_service or AuditService(audit_repository=self._audit_repo)
        self._logger = service_logger or logger

    def reconcile_recovery(self, channel_id: Optional[str] = None) -> RecoverySummary:
        """Execute full startup recovery and reconciliation for all uncompleted file operations and interrupted tasks.

        Args:
            channel_id: Optional channel filter. If None, reconciles all channels.

        Returns:
            RecoverySummary: Detailed counts and records of all reconciliation actions.

        Raises:
            AuditIntegrityException: If channel export gate is hard-locked due to audit tampering.
        """
        summary = RecoverySummary()
        settings = get_settings()

        # 1. Check Audit Integrity Export Lock
        if channel_id:
            is_locked, lock_reason = self._audit_repo.is_export_locked(channel_id)
            if is_locked:
                raise AuditIntegrityException(
                    f"Cannot perform recovery: Channel '{channel_id}' export gate is HARD LOCKED: {lock_reason}",
                    channel_id=channel_id,
                )

        uncompleted_ops = self._file_op_repo.list_uncompleted(channel_id)
        self._logger.info(
            f"Startup recovery scan: found {len(uncompleted_ops)} uncompleted file operation(s)"
            + (f" on channel '{channel_id}'." if channel_id else ".")
        )

        for op in uncompleted_ops:
            self._reconcile_single_file_op(op, summary, settings)

        # 2. Reconcile Interrupted Tasks
        self._reconcile_interrupted_tasks(channel_id, summary)

        self._logger.info(
            f"Startup recovery completed: reconciled {summary.reconciled_ops} file op(s) "
            f"({summary.adopted_assets} adopted, {summary.quarantined_files} quarantined, "
            f"{summary.cleaned_temps} temps cleaned, {summary.failed_ops} failed), "
            f"{summary.reconciled_tasks} task(s) adjusted."
        )
        return summary

    def _reconcile_single_file_op(
        self,
        op: FileOperationModel,
        summary: RecoverySummary,
        settings: Any,
    ) -> None:
        """Reconcile an interrupted file operation according to Section 5.3 Crash Consistency Matrix."""
        temp_path = Path(op.temp_path)
        target_path = Path(op.target_path)
        clean_ch = op.channel_id.strip()
        channel_dir = settings.channels_root / clean_ch

        # Verify audit lock for this specific operation's channel
        is_locked, lock_reason = self._audit_repo.is_export_locked(clean_ch)
        if is_locked:
            self._logger.critical(
                f"Skipping recovery for operation '{op.id}': Channel '{clean_ch}' is audit locked ({lock_reason})."
            )
            return

        # ----------------------------------------------------
        # State: PENDING
        # ----------------------------------------------------
        if op.state == FileOperationState.PENDING:
            # Operation interrupted before temporary file was written and verified
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)
                summary.cleaned_temps += 1

            self._file_op_repo.update_state(
                op.id,
                FileOperationState.FAILED,
                error_message="Recovered from crash during PENDING state before output verification",
            )
            summary.failed_ops += 1
            summary.reconciled_ops += 1
            summary.details.append({"op_id": op.id, "action": "FAIL_PENDING", "reason": "Interrupted in PENDING"})
            return

        # ----------------------------------------------------
        # State: FILE_WRITTEN
        # ----------------------------------------------------
        if op.state == FileOperationState.FILE_WRITTEN:
            expected_sha = op.file_sha256

            # Scenario A: Target file exists in originals/
            if target_path.exists():
                actual_sha = calculate_file_sha256(target_path)

                if expected_sha and actual_sha == expected_sha:
                    # Match! Safe adoption into media_assets and task checkpoints ($0 cost reuse)
                    self._adopt_file_and_complete_op(op, target_path, actual_sha, summary, clean_ch)
                    if temp_path.exists():
                        temp_path.unlink(missing_ok=True)
                        summary.cleaned_temps += 1
                else:
                    # Mismatch! Check ownership before touching
                    existing_asset = self._asset_repo.get_by_path(clean_ch, str(target_path))
                    if existing_asset:
                        # Owned by another asset: STRICT PRESERVATION, DO NOT DELETE!
                        self._logger.critical(
                            f"RECOVERY COLLISION GUARD: '{target_path}' is owned by asset '{existing_asset.id}'. "
                            f"Expected SHA '{expected_sha}', got '{actual_sha}'. Original asset preserved."
                        )
                        self._file_op_repo.update_state(
                            op.id,
                            FileOperationState.FAILED,
                            error_message=(
                                f"Collision with existing asset '{existing_asset.id}'. "
                                f"Original asset at '{target_path}' strictly preserved."
                            ),
                        )
                        if temp_path.exists():
                            temp_path.unlink(missing_ok=True)
                            summary.cleaned_temps += 1
                        summary.failed_ops += 1
                        summary.reconciled_ops += 1
                        summary.details.append(
                            {"op_id": op.id, "action": "PRESERVE_EXISTING_ASSET", "asset_id": existing_asset.id}
                        )
                    else:
                        # Unowned orphan file in originals/: SAFE QUARANTINE (do not delete in place)
                        quarantine_dir = channel_dir / "media_vault" / "quarantine"
                        quarantine_dir.mkdir(parents=True, exist_ok=True)
                        quarantine_filename = (
                            f"{target_path.stem}.quarantine_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}{target_path.suffix}"
                        )
                        quarantine_path = quarantine_dir / quarantine_filename
                        self._logger.warning(
                            f"Unowned file with mismatched hash at '{target_path}'. "
                            f"Moving to quarantine: '{quarantine_path}'."
                        )
                        shutil.move(str(target_path), str(quarantine_path))
                        summary.quarantined_files += 1

                        self._file_op_repo.update_state(
                            op.id,
                            FileOperationState.FAILED,
                            error_message=f"Hash mismatch: Unowned file quarantined to '{quarantine_path.name}'.",
                        )
                        if temp_path.exists():
                            temp_path.unlink(missing_ok=True)
                            summary.cleaned_temps += 1
                        summary.failed_ops += 1
                        summary.reconciled_ops += 1

                        # Audit quarantine event
                        self._audit.record_event(
                            channel_id=clean_ch,
                            action="QUARANTINE_UNEXPECTED_FILE",
                            actor="recovery_system",
                            payload={"op_id": op.id, "target_path": str(target_path), "quarantine_path": str(quarantine_path)},
                            idempotency_key=f"QUARANTINE:{op.id}",
                        )
                return

            # Scenario B: Target file does NOT exist in originals/
            if temp_path.exists():
                temp_sha = calculate_file_sha256(temp_path)
                if expected_sha and temp_sha == expected_sha:
                    # Temp file is intact: finalize move atomically
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(temp_path, target_path)
                    self._adopt_file_and_complete_op(op, target_path, temp_sha, summary, clean_ch)
                else:
                    # Temp file is corrupted or mismatched: safely clean temp and fail
                    temp_path.unlink(missing_ok=True)
                    summary.cleaned_temps += 1
                    self._file_op_repo.update_state(
                        op.id,
                        FileOperationState.FAILED,
                        error_message="Temporary holding file corrupted during crash",
                    )
                    summary.failed_ops += 1
                    summary.reconciled_ops += 1
            else:
                # Neither target nor temp exists
                self._file_op_repo.update_state(
                    op.id,
                    FileOperationState.FAILED,
                    error_message="Neither target nor temporary file found upon recovery",
                )
                summary.failed_ops += 1
                summary.reconciled_ops += 1
            return

        # ----------------------------------------------------
        # State: DB_COMMITTED
        # ----------------------------------------------------
        if op.state == FileOperationState.DB_COMMITTED:
            # DB committed, atomic rename might have crashed right before or during
            if not target_path.exists() and temp_path.exists():
                target_path.parent.mkdir(parents=True, exist_ok=True)
                os.replace(temp_path, target_path)
            elif temp_path.exists():
                temp_path.unlink(missing_ok=True)
                summary.cleaned_temps += 1

            if target_path.exists():
                self._file_op_repo.update_state(op.id, FileOperationState.COMPLETED)
                summary.reconciled_ops += 1
                summary.details.append({"op_id": op.id, "action": "FINALIZE_COMMITTED", "target": str(target_path)})
            else:
                self._file_op_repo.update_state(
                    op.id, FileOperationState.FAILED, error_message="Target missing after DB commit crash"
                )
                summary.failed_ops += 1
                summary.reconciled_ops += 1
            return

        # ----------------------------------------------------
        # State: FILE_FINALIZED
        # ----------------------------------------------------
        if op.state == FileOperationState.FILE_FINALIZED:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)
                summary.cleaned_temps += 1
            self._file_op_repo.update_state(op.id, FileOperationState.COMPLETED)
            summary.reconciled_ops += 1
            summary.details.append({"op_id": op.id, "action": "COMPLETE_FINALIZED"})
            return

    def _adopt_file_and_complete_op(
        self,
        op: FileOperationModel,
        target_path: Path,
        file_sha256: str,
        summary: RecoverySummary,
        channel_id: str,
    ) -> None:
        """Safely adopt a verified file into media_assets and task_checkpoints, then complete op."""
        # Check if asset already registered
        existing_asset = self._asset_repo.get_by_sha256(channel_id, file_sha256)
        if not existing_asset and op.asset_id:
            try:
                asset_size = target_path.stat().st_size
                asset_create = MediaAssetCreate(
                    id=op.asset_id,
                    channel_id=channel_id,
                    file_name=target_path.name,
                    file_path=str(target_path),
                    file_sha256=file_sha256,
                    file_size_bytes=asset_size,
                    mime_type="application/octet-stream",
                    asset_type=AssetType.IMAGE,
                )
                self._asset_repo.create(asset_create)
                summary.adopted_assets += 1
            except Exception as e:
                self._logger.warning(f"Could not adopt asset '{op.asset_id}' into media_assets: {e}")

        # Check if associated with task checkpoint: op_{task_id}_{step_name}_tok{token}
        if op.id.startswith("op_") and "_tok" in op.id:
            try:
                parts = op.id.split("_")
                # e.g. op_tsk_123_step1_tok1
                task_id = parts[1]
                tok_str = op.id.split("_tok")[-1]
                op_token = int(tok_str)

                task = self._task_repo.get_by_id(task_id)
                if task and task.execution_token == op_token:
                    # Token still matches! Persist checkpoint safely
                    step_name = "_".join(parts[2:-1]) if len(parts) > 3 else "recovered_step"
                    cp_create = TaskCheckpointCreate(
                        task_id=task_id,
                        step_name=step_name,
                        step_index=1,
                        step_data={"sha256": file_sha256, "target_path": str(target_path)},
                        execution_token=op_token,
                    )
                    self._task_repo.save_checkpoint(cp_create)
            except Exception as e:
                self._logger.warning(f"Could not adopt checkpoint for op '{op.id}': {e}")

        self._file_op_repo.update_state(op.id, FileOperationState.COMPLETED)
        summary.reconciled_ops += 1
        summary.details.append({"op_id": op.id, "action": "ADOPT_COMPLETED", "sha256": file_sha256})

        # Audit event
        try:
            self._audit.record_event(
                channel_id=channel_id,
                action="RECOVER_ADOPT_FILE",
                actor="recovery_system",
                payload={"op_id": op.id, "target_path": str(target_path), "sha256": file_sha256},
                idempotency_key=f"RECOVER_ADOPT:{op.id}",
            )
        except Exception as e:
            self._logger.warning(f"Could not record audit event for recovery: {e}")

    def _reconcile_interrupted_tasks(
        self,
        channel_id: Optional[str],
        summary: RecoverySummary,
    ) -> None:
        """Inspect and safely reconcile tasks that were left in RUNNING status during a crash."""
        running_tasks = self._task_repo.list_tasks(channel_id=channel_id, status=TaskStatus.RUNNING)
        if not running_tasks:
            return

        for task in running_tasks:
            # Check if all total_steps were already check-pointed
            cps = self._task_repo.get_checkpoints(task.id)
            if task.total_steps and len(cps) >= task.total_steps:
                # All steps check-pointed: mark completed
                self._task_repo.update_task_status(
                    task_id=task.id,
                    new_status=TaskStatus.COMPLETED,
                    execution_token=task.execution_token,
                    result={"recovered": True, "checkpoints": len(cps)},
                )
                summary.reconciled_tasks += 1
                summary.details.append({"task_id": task.id, "action": "COMPLETE_FULLY_CHECKPOINTED"})
            else:
                # Task was partially executed: transition to RETRY_PENDING with incremented token
                # This fences out the zombie worker process if it ever wakes up!
                self._task_repo.reap_stale_task(
                    task_id=task.id,
                    stale_worker_id=task.worker_id or "",
                    stale_token=task.execution_token,
                    new_status=TaskStatus.RETRY_PENDING,
                    error_message="Recovered interrupted task upon startup. Rescheduled for execution.",
                )
                summary.reconciled_tasks += 1
                summary.details.append({"task_id": task.id, "action": "RESCHEDULE_INTERRUPTED_TASK"})
