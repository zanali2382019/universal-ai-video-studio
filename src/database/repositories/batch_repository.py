"""Repository for Batch Production Jobs (Task 3.15b)."""
import json
import uuid
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

from src.database.connection import DatabaseManager
from src.database.repositories.base import BaseRepository
from src.database.models import BatchJobModel, BatchJobCreate
from src.core.logger import get_logger

logger = get_logger("studio.database.batch_repository")


class BatchRepository(BaseRepository):
    """Database repository for batch production jobs."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None) -> None:
        super().__init__(db_manager)

    @property
    def db(self) -> DatabaseManager:
        return self.db_manager

    def create_batch_job(self, batch_data: BatchJobCreate) -> BatchJobModel:
        """Create and persist a new batch job."""
        job_id = batch_data.id or f"batch_{uuid.uuid4().hex[:12]}"
        now_utc = datetime.now(timezone.utc).isoformat()
        task_ids_json = json.dumps(batch_data.task_ids or [])
        metadata_json = json.dumps(batch_data.metadata or {})
        total_items = len(batch_data.task_ids)

        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO batch_jobs (
                    id, channel_id, name, status, total_items,
                    completed_items, failed_items, task_ids_json,
                    metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, 'queued', ?, 0, 0, ?, ?, ?, ?);
                """,
                (
                    job_id,
                    batch_data.channel_id,
                    batch_data.name,
                    total_items,
                    task_ids_json,
                    metadata_json,
                    now_utc,
                    now_utc,
                ),
            )

        logger.info(f"Created batch job '{job_id}' for channel '{batch_data.channel_id}' with {total_items} items.")
        return self.get_by_id(job_id)

    def get_by_id(self, batch_id: str) -> Optional[BatchJobModel]:
        """Retrieve batch job by ID."""
        clean_id = (batch_id or "").strip()
        with self.db.connection() as conn:
            cursor = conn.cursor()
            row = cursor.execute("SELECT * FROM batch_jobs WHERE id = ?;", (clean_id,)).fetchone()
            if not row:
                return None
            return BatchJobModel.from_row(row)

    def list_by_channel(self, channel_id: Optional[str] = None) -> List[BatchJobModel]:
        """List batch jobs optionally filtered by channel."""
        with self.db.connection() as conn:
            cursor = conn.cursor()
            if channel_id:
                rows = cursor.execute(
                    "SELECT * FROM batch_jobs WHERE channel_id = ? ORDER BY created_at DESC;",
                    (channel_id,),
                ).fetchall()
            else:
                rows = cursor.execute(
                    "SELECT * FROM batch_jobs ORDER BY created_at DESC;"
                ).fetchall()
            return [BatchJobModel.from_row(row) for row in rows]

    def update_batch_job(
        self,
        batch_id: str,
        status: Optional[str] = None,
        completed_items: Optional[int] = None,
        failed_items: Optional[int] = None,
        task_ids: Optional[List[str]] = None,
        total_items: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[BatchJobModel]:
        """Update batch job status, progress counters, or metadata."""
        clean_id = (batch_id or "").strip()
        now_utc = datetime.now(timezone.utc).isoformat()

        updates = ["updated_at = ?"]
        params = [now_utc]

        if status is not None:
            updates.append("status = ?")
            params.append(status)
        if completed_items is not None:
            updates.append("completed_items = ?")
            params.append(completed_items)
        if failed_items is not None:
            updates.append("failed_items = ?")
            params.append(failed_items)
        if task_ids is not None:
            updates.append("task_ids_json = ?")
            params.append(json.dumps(task_ids))
        if total_items is not None:
            updates.append("total_items = ?")
            params.append(total_items)
        if metadata is not None:
            updates.append("metadata_json = ?")
            params.append(json.dumps(metadata))

        params.append(clean_id)
        query = f"UPDATE batch_jobs SET {', '.join(updates)} WHERE id = ?;"

        with self.db.transaction() as conn:
            conn.execute(query, tuple(params))

        return self.get_by_id(clean_id)
