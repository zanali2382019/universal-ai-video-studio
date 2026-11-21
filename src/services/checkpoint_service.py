"""Task Checkpoint Service implementing the 5-step checkpoint protocol with fencing and journaling."""
import os
import uuid
import logging
from pathlib import Path
from typing import Optional, Dict, Any, Callable

from src.database.repositories.task_repository import TaskRepository
from src.database.repositories.file_operation_repository import FileOperationRepository
from src.database.repositories.asset_repository import AssetRepository
from src.services.audit_service import AuditService
from src.services.heartbeat_daemon import HeartbeatDaemon
from src.database.models import (
    TaskModel,
    TaskCheckpointCreate,
    TaskCheckpointModel,
    FileOperationModel,
)
from src.core.constants import (
    TaskStatus,
    FileOperationState,
    FileOperationType,
    CheckpointStepPhase,
)
from src.core.exceptions import (
    StaleExecutionTokenException,
    ValidationException,
    AssetPathCollisionException,
    AuditIntegrityException,
)
from src.utils.file_utils import calculate_file_sha256, safe_resolve_path
from src.core.config import get_settings
from src.core.logger import get_logger

logger = get_logger("studio.services.checkpoint")


class CheckpointService:
    """Orchestrates the 5-step checkpoint protocol with strict fencing and file operation journaling.

    Protocol Phases:
    1. PRE_STEP_INTENT: Declare intent, record PENDING file operation with deterministic ID.
    2. EXECUTE_WORK: Execute worker work_fn writing exclusively to scoped temp path in cache/.
    3. VERIFY_OUTPUT: Compute SHA-256 and size, transition file_operation to FILE_WRITTEN.
    4. ATOMIC_COMMIT: Verify token, move temp to originals/, transition to DB_COMMITTED -> FILE_FINALIZED -> COMPLETED.
    5. AUDIT_FLUSH: Record event in transactional outbox and flush to license_audit.log.
    """

    def __init__(
        self,
        task_repository: Optional[TaskRepository] = None,
        file_operation_repository: Optional[FileOperationRepository] = None,
        asset_repository: Optional[AssetRepository] = None,
        audit_service: Optional[AuditService] = None,
        db_manager: Optional[Any] = None,
        service_logger: Optional[logging.Logger] = None,
    ) -> None:
        self.db_manager = (
            db_manager
            or (task_repository and task_repository.db_manager)
            or (file_operation_repository and file_operation_repository.db_manager)
            or (asset_repository and asset_repository.db_manager)
        )
        from src.database.connection import DatabaseManager
        if self.db_manager is None:
            self.db_manager = DatabaseManager()

        self._task_repo = task_repository or TaskRepository(db_manager=self.db_manager)
        self._file_op_repo = file_operation_repository or FileOperationRepository(db_manager=self.db_manager)
        self._asset_repo = asset_repository or AssetRepository(db_manager=self.db_manager)
        self._audit = audit_service or AuditService()
        self._logger = service_logger or logger

    def execute_step(
        self,
        task: TaskModel,
        step_name: str,
        step_index: int,
        work_fn: Callable[[Path], Dict[str, Any]],
        target_rel_path: Path,
        operation_type: FileOperationType = FileOperationType.RENDER,
        heartbeat: Optional[HeartbeatDaemon] = None,
        asset_id: Optional[str] = None,
        actor: str = "worker",
    ) -> TaskCheckpointModel:
        """Execute a work step guarded by the 5-step checkpoint protocol.

        Args:
            task: Current running task model.
            step_name: Distinct name of the step (e.g. 'render_scene_1', 'generate_voice').
            step_index: Step sequence number (1-based).
            work_fn: Callable taking temp file Path, executing work and returning step data dict.
            target_rel_path: Channel-relative destination path in media_vault/originals/.
            operation_type: File operation type for journal.
            heartbeat: Optional active heartbeat daemon pulsing lease renewals.
            asset_id: Optional associated MAM asset ID.
            actor: Operator/worker initiating the step.

        Returns:
            TaskCheckpointModel: Confirmed step checkpoint.

        Raises:
            StaleExecutionTokenException: If fencing token is rejected or lease is lost.
            AssetPathCollisionException: If destination is owned by another asset with mismatched hash.
            ValidationException: If output verification fails.
        """
        settings = get_settings()
        clean_ch = task.channel_id.strip()
        channel_dir = settings.channels_root / clean_ch

        # 0. Fast-Forward: Check if step is already checkpointed for this task
        existing_checkpoints = self._task_repo.get_checkpoints(task.id)
        for cp in existing_checkpoints:
            if cp.step_name == step_name:
                self._logger.info(
                    f"Fast-forwarding step '{step_name}' (index {step_index}) for task '{task.id}': "
                    f"already completed in checkpoint '{cp.id}'."
                )
                return cp

        # Verify task is currently active
        if heartbeat and heartbeat.is_lease_lost:
            raise StaleExecutionTokenException(
                task.id,
                task.execution_token,
                task.execution_token,
                "Heartbeat reported lease lost before step start",
            )

        # Scoped naming per Section 4.1 of Architecture Design:
        # task_{task_id}_tok_{execution_token}_{exec_uuid}_{step_name}.tmp
        exec_uuid = uuid.uuid4().hex[:8]
        temp_rel_path = (
            Path("media_vault")
            / "cache"
            / f"task_{task.id}_tok_{task.execution_token}_{exec_uuid}_{step_name}.tmp"
        )
        temp_abs_path = safe_resolve_path(channel_dir, temp_rel_path)
        target_abs_path = safe_resolve_path(channel_dir, target_rel_path)

        # Deterministic file operation ID per Section 5.1:
        # op_{task_id}_{step_name}_tok{execution_token}
        op_id = f"op_{task.id}_{step_name}_tok{task.execution_token}"

        now_utc = task.updated_at

        # ========================================================
        # Phase 1: PRE_STEP_INTENT
        # ========================================================
        self._logger.debug(
            f"Phase 1 [{CheckpointStepPhase.PRE_STEP_INTENT.value}]: Task '{task.id}' "
            f"step '{step_name}' (token={task.execution_token})."
        )
        temp_abs_path.parent.mkdir(parents=True, exist_ok=True)
        target_abs_path.parent.mkdir(parents=True, exist_ok=True)

        op_record = FileOperationModel(
            id=op_id,
            operation_type=operation_type,
            channel_id=clean_ch,
            asset_id=asset_id,
            source_path=None,
            temp_path=str(temp_abs_path),
            target_path=str(target_abs_path),
            file_sha256=None,
            file_size_bytes=None,
            state=FileOperationState.PENDING,
            error_message=None,
            created_at=now_utc,
            updated_at=now_utc,
        )
        self._file_op_repo.create(op_record)

        # ========================================================
        # Phase 2: EXECUTE_WORK
        # ========================================================
        self._logger.debug(
            f"Phase 2 [{CheckpointStepPhase.EXECUTE_WORK.value}]: Executing work_fn for '{step_name}'."
        )
        try:
            work_output_data = work_fn(temp_abs_path) or {}
        except Exception as e:
            self._logger.error(f"Execution failed in work_fn for '{step_name}': {e}")
            self._file_op_repo.update_state(
                op_id, FileOperationState.FAILED, error_message=f"Work execution failed: {e}"
            )
            if temp_abs_path.exists():
                temp_abs_path.unlink(missing_ok=True)
            raise

        # Check lease state after work
        if heartbeat and heartbeat.is_lease_lost:
            if temp_abs_path.exists():
                temp_abs_path.unlink(missing_ok=True)
            self._file_op_repo.update_state(
                op_id, FileOperationState.FAILED, error_message="Lease lost during work execution"
            )
            raise StaleExecutionTokenException(
                task.id,
                task.execution_token,
                task.execution_token,
                "Heartbeat reported lease lost during work execution",
            )

        # ========================================================
        # Phase 3: VERIFY_OUTPUT
        # ========================================================
        self._logger.debug(
            f"Phase 3 [{CheckpointStepPhase.VERIFY_OUTPUT.value}]: Verifying temp file '{temp_abs_path}'."
        )
        if not temp_abs_path.exists() or temp_abs_path.stat().st_size == 0:
            error_msg = f"Work function for '{step_name}' produced no output or empty file at {temp_abs_path}."
            self._file_op_repo.update_state(
                op_id, FileOperationState.FAILED, error_message=error_msg
            )
            raise ValidationException(error_msg)

        file_sha256 = calculate_file_sha256(temp_abs_path)
        file_size = temp_abs_path.stat().st_size

        # Pre-replace SHA-256 commitment in file_operations journal
        self._file_op_repo.update_state(
            operation_id=op_id,
            state=FileOperationState.FILE_WRITTEN,
            file_sha256=file_sha256,
            file_size_bytes=file_size,
        )

        # ========================================================
        # Phase 4: ATOMIC_COMMIT
        # ========================================================
        self._logger.debug(
            f"Phase 4 [{CheckpointStepPhase.ATOMIC_COMMIT.value}]: Committing step '{step_name}'."
        )
        if heartbeat and heartbeat.is_lease_lost:
            if temp_abs_path.exists():
                temp_abs_path.unlink(missing_ok=True)
            raise StaleExecutionTokenException(
                task.id,
                task.execution_token,
                task.execution_token,
                "Heartbeat reported lease lost before atomic commit",
            )

        # Collision & Originals Protection Guard:
        if target_abs_path.exists():
            target_sha256 = calculate_file_sha256(target_abs_path)
            if target_sha256 != file_sha256:
                # Target exists with mismatched SHA-256: Check if owned by another asset
                existing_asset = self._asset_repo.get_by_path(clean_ch, str(target_rel_path))
                if existing_asset:
                    self._logger.critical(
                        f"Collision guard: target path '{target_rel_path}' is owned by asset "
                        f"'{existing_asset.id}'. Refusing to overwrite original!"
                    )
                    self._file_op_repo.update_state(
                        op_id,
                        FileOperationState.FAILED,
                        error_message=f"Collision with asset '{existing_asset.id}'. Original strictly preserved.",
                    )
                    if temp_abs_path.exists():
                        temp_abs_path.unlink(missing_ok=True)
                    raise AssetPathCollisionException(
                        clean_ch, str(target_rel_path), existing_asset.id
                    )
                else:
                    # Unowned unexpected file at target: Safe Quarantine
                    quarantine_dir = channel_dir / "media_vault" / "quarantine"
                    quarantine_dir.mkdir(parents=True, exist_ok=True)
                    quarantine_filename = (
                        f"{target_abs_path.stem}.quarantine_{uuid.uuid4().hex[:8]}{target_abs_path.suffix}"
                    )
                    quarantine_dest = quarantine_dir / quarantine_filename
                    self._logger.warning(
                        f"Unowned file with mismatched hash at '{target_abs_path}'. "
                        f"Moving to quarantine '{quarantine_dest}'."
                    )
                    os.replace(target_abs_path, quarantine_dest)

        # Update file_operations to DB_COMMITTED
        self._file_op_repo.update_state(op_id, FileOperationState.DB_COMMITTED)

        # Atomic replacement: temp_abs_path -> target_abs_path
        if temp_abs_path.exists():
            os.replace(temp_abs_path, target_abs_path)
        self._file_op_repo.update_state(op_id, FileOperationState.FILE_FINALIZED)

        # Record checkpoint matching execution_token
        checkpoint_input = TaskCheckpointCreate(
            task_id=task.id,
            step_name=step_name,
            step_index=step_index,
            step_data={
                "sha256": file_sha256,
                "size_bytes": file_size,
                "target_path": str(target_rel_path),
                **work_output_data,
            },
            execution_token=task.execution_token,
        )
        saved_checkpoint = self._task_repo.save_checkpoint(checkpoint_input)

        # Finalize file operation to COMPLETED
        self._file_op_repo.update_state(op_id, FileOperationState.COMPLETED)

        # ========================================================
        # Phase 5: AUDIT_FLUSH
        # ========================================================
        self._logger.debug(
            f"Phase 5 [{CheckpointStepPhase.AUDIT_FLUSH.value}]: Recording audit event for '{step_name}'."
        )
        self._audit.record_event(
            channel_id=clean_ch,
            action="TASK_STEP_CHECKPOINT",
            actor=actor,
            payload={
                "task_id": task.id,
                "step_name": step_name,
                "step_index": step_index,
                "execution_token": task.execution_token,
                "file_sha256": file_sha256,
                "target_path": str(target_rel_path),
            },
            idempotency_key=f"CP:{task.id}:{step_name}:tok_{task.execution_token}",
        )

        self._logger.info(
            f"Completed 5-step checkpoint protocol for task '{task.id}', "
            f"step '{step_name}' [index {step_index}] (token={task.execution_token})."
        )
        return saved_checkpoint
