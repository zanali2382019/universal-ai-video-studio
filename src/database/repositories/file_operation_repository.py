"""File operations journal repository for tracking two-phase file mutations and crash recovery."""
from datetime import datetime, timezone
from typing import Optional, List

from src.database.repositories.base import BaseRepository
from src.database.models import FileOperationModel
from src.core.constants import FileOperationState
from src.core.logger import get_logger

logger = get_logger("studio.database.file_operation_repository")


class FileOperationRepository(BaseRepository):
    """Manages persistent file operation journaling in SQLite."""

    def create(self, op: FileOperationModel) -> FileOperationModel:
        """Insert a new file operation journal entry."""
        sql = """
        INSERT INTO file_operations (
            id, operation_type, channel_id, asset_id, source_path,
            temp_path, target_path, file_sha256, file_size_bytes,
            state, error_message, created_at, updated_at
        ) VALUES (
            :id, :operation_type, :channel_id, :asset_id, :source_path,
            :temp_path, :target_path, :file_sha256, :file_size_bytes,
            :state, :error_message, :created_at, :updated_at
        );
        """
        params = {
            "id": op.id,
            "operation_type": op.operation_type.value,
            "channel_id": op.channel_id,
            "asset_id": op.asset_id,
            "source_path": op.source_path,
            "temp_path": op.temp_path,
            "target_path": op.target_path,
            "file_sha256": op.file_sha256,
            "file_size_bytes": op.file_size_bytes,
            "state": op.state.value,
            "error_message": op.error_message,
            "created_at": op.created_at,
            "updated_at": op.updated_at,
        }

        with self.db_manager.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, params)

        return op

    def get_by_id(self, operation_id: str) -> Optional[FileOperationModel]:
        """Retrieve a journal entry by ID."""
        sql = "SELECT * FROM file_operations WHERE id = ?;"
        with self.db_manager.connection() as conn:
            cursor = conn.cursor()
            row = cursor.execute(sql, (operation_id,)).fetchone()
            if not row:
                return None
            return FileOperationModel.from_row(row)

    def list_uncompleted(self, channel_id: Optional[str] = None) -> List[FileOperationModel]:
        """List all pending, non-terminal operations (PENDING, FILE_WRITTEN, DB_COMMITTED, FILE_FINALIZED)."""
        terminal_states = f"('{FileOperationState.COMPLETED.value}', '{FileOperationState.FAILED.value}')"
        if channel_id:
            sql = f"SELECT * FROM file_operations WHERE channel_id = ? AND state NOT IN {terminal_states} ORDER BY created_at ASC;"
            params = (channel_id,)
        else:
            sql = f"SELECT * FROM file_operations WHERE state NOT IN {terminal_states} ORDER BY created_at ASC;"
            params = ()

        with self.db_manager.connection() as conn:
            cursor = conn.cursor()
            rows = cursor.execute(sql, params).fetchall()
            return [FileOperationModel.from_row(r) for r in rows]

    def update_state(
        self,
        operation_id: str,
        state: FileOperationState,
        error_message: Optional[str] = None,
        file_sha256: Optional[str] = None,
        file_size_bytes: Optional[int] = None,
    ) -> None:
        """Update state of an operation journal entry."""
        now_utc = datetime.now(timezone.utc).isoformat()
        fields = ["state = :state", "updated_at = :updated_at"]
        params = {"operation_id": operation_id, "state": state.value, "updated_at": now_utc}

        if error_message is not None:
            fields.append("error_message = :error_message")
            params["error_message"] = error_message
        if file_sha256 is not None:
            fields.append("file_sha256 = :file_sha256")
            params["file_sha256"] = file_sha256
        if file_size_bytes is not None:
            fields.append("file_size_bytes = :file_size_bytes")
            params["file_size_bytes"] = file_size_bytes

        sql = f"UPDATE file_operations SET {', '.join(fields)} WHERE id = :operation_id;"
        with self.db_manager.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, params)

