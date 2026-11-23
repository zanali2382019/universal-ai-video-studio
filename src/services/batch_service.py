"""Batch production service for multi-project / multi-profile render workflows (Task 3.15b)."""
import uuid
import logging
from typing import Optional, List, Dict, Any

from src.core.constants import TaskType, TaskStatus
from src.core.exceptions import ValidationException, ChannelNotFoundException
from src.core.logger import get_logger
from src.database.models import BatchJobModel, BatchJobCreate, TaskCreate
from src.database.repositories.batch_repository import BatchRepository
from src.database.repositories.channel_repository import ChannelRepository
from src.services.task_service import TaskService

logger = get_logger("studio.services.batch")


class BatchService:
    """Orchestrates batch render jobs and monitors multi-item render queues."""

    def __init__(
        self,
        batch_repository: Optional[BatchRepository] = None,
        task_service: Optional[TaskService] = None,
        channel_repository: Optional[ChannelRepository] = None,
        service_logger: Optional[logging.Logger] = None,
    ) -> None:
        self.batch_repo = batch_repository or BatchRepository()
        self.task_service = task_service or TaskService()
        self.channel_repo = channel_repository or ChannelRepository()
        self._logger = service_logger or logger

    def create_batch(
        self,
        channel_id: str,
        name: str,
        render_requests: List[Dict[str, Any]],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create a batch job and enqueue individual render tasks with error isolation.

        Args:
            channel_id: Owning channel ID.
            name: Human-readable batch title.
            render_requests: List of render task payload dictionaries (each specifying project_id, profile, mode, etc.).
            metadata: Optional additional metadata.

        Returns:
            Dictionary with created BatchJobModel and list of spawned task IDs.
        """
        clean_ch = (channel_id or "").strip()
        channel = self.channel_repo.get_by_id(clean_ch)
        if not channel:
            raise ChannelNotFoundException(clean_ch)

        if not render_requests:
            raise ValidationException("Batch job must contain at least one render request.")

        batch_id = f"batch_{uuid.uuid4().hex[:12]}"
        created_task_ids: List[str] = []

        # Create batch record first (queued)
        batch_create = BatchJobCreate(
            id=batch_id,
            channel_id=clean_ch,
            name=name.strip() or f"Batch {batch_id}",
            task_ids=[],
            metadata=metadata or {},
        )
        self.batch_repo.create_batch_job(batch_create)

        # Enqueue individual render tasks with isolation
        for idx, req in enumerate(render_requests):
            project_id = (req.get("project_id") or "").strip()
            item_payload = dict(req)
            item_payload["batch_id"] = batch_id
            item_payload["batch_item_index"] = idx

            # Formulate idempotency key
            idempotency_key = f"{batch_id}_item_{idx}_{uuid.uuid4().hex[:6]}"

            task_input = TaskCreate(
                channel_id=clean_ch,
                project_id=project_id or None,
                task_type=TaskType.VIDEO_RENDERING,
                idempotency_key=idempotency_key,
                status=TaskStatus.QUEUED,
                priority=int(req.get("priority", 10)),
                payload=item_payload,
            )

            try:
                task = self.task_service.create_task(channel_id=clean_ch, task_input=task_input)
                created_task_ids.append(task.id)
            except Exception as e:
                self._logger.error(f"Failed to enqueue batch item {idx} for project '{project_id}': {e}")
                # We record failure or continue with other items for error isolation

        # Update batch job with total tasks
        self.batch_repo.update_batch_job(
            batch_id=batch_id,
            task_ids=created_task_ids,
            total_items=len(render_requests),
            status="queued" if created_task_ids else "failed",
        )

        return self.get_batch_status(batch_id)

    def get_batch_status(self, batch_id: str) -> Dict[str, Any]:
        """Query aggregate batch job status and per-item statuses."""
        batch = self.batch_repo.get_by_id(batch_id)
        if not batch:
            raise ValidationException(f"Batch job '{batch_id}' not found.")

        tasks_detail = []
        completed_count = 0
        failed_count = 0
        running_count = 0
        queued_count = 0
        cancelled_count = 0

        for tid in batch.task_ids:
            try:
                t = self.task_service.get_task(tid)
                st_val = t.status.value
                if t.status == TaskStatus.COMPLETED:
                    completed_count += 1
                elif t.status == TaskStatus.FAILED:
                    failed_count += 1
                elif t.status == TaskStatus.RUNNING:
                    running_count += 1
                elif t.status == TaskStatus.CANCELLED:
                    cancelled_count += 1
                else:
                    queued_count += 1

                tasks_detail.append({
                    "task_id": t.id,
                    "project_id": t.project_id,
                    "status": st_val,
                    "progress_percent": t.result.get("progress_percent") if t.result else (100 if t.status == TaskStatus.COMPLETED else 0),
                    "error_message": t.error_message,
                    "created_at": t.created_at,
                    "output_path": t.result.get("output_path") if t.result else None,
                })
            except Exception:
                failed_count += 1
                tasks_detail.append({
                    "task_id": tid,
                    "project_id": None,
                    "status": "not_found",
                    "progress_percent": 0,
                    "error_message": "Task record missing",
                    "created_at": None,
                    "output_path": None,
                })

        total = batch.total_items or len(batch.task_ids)
        # Determine aggregate status
        if total > 0 and completed_count == total:
            agg_status = "completed"
        elif total > 0 and failed_count == total:
            agg_status = "failed"
        elif running_count > 0:
            agg_status = "running"
        elif completed_count > 0 and (failed_count > 0 or cancelled_count > 0) and running_count == 0 and queued_count == 0:
            agg_status = "partial_failure"
        elif cancelled_count == total:
            agg_status = "cancelled"
        elif queued_count > 0:
            agg_status = "queued"
        else:
            agg_status = batch.status

        # Synchronize repository status if changed
        if agg_status != batch.status or completed_count != batch.completed_items or failed_count != batch.failed_items:
            self.batch_repo.update_batch_job(
                batch_id=batch.id,
                status=agg_status,
                completed_items=completed_count,
                failed_items=failed_count,
            )

        progress_pct = round((completed_count / total * 100), 1) if total > 0 else 0.0

        return {
            "id": batch.id,
            "channel_id": batch.channel_id,
            "name": batch.name,
            "status": agg_status,
            "total_items": total,
            "completed_items": completed_count,
            "failed_items": failed_count,
            "running_items": running_count,
            "queued_items": queued_count,
            "cancelled_items": cancelled_count,
            "progress_percent": progress_pct,
            "metadata": batch.metadata,
            "created_at": batch.created_at,
            "updated_at": batch.updated_at,
            "tasks": tasks_detail,
        }

    def list_batches(self, channel_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """List all batch jobs with updated counters."""
        batches = self.batch_repo.list_by_channel(channel_id=channel_id)
        results = []
        for b in batches:
            results.append(self.get_batch_status(b.id))
        return results
