"""TaskReaper service for recovering expired leases, enforcing backoff, and TTL checks."""
import time
import threading
import random
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any

from src.database.repositories.task_repository import TaskRepository
from src.services.task_service import TaskService
from src.database.models import TaskModel
from src.core.constants import TaskStatus
from src.core.logger import get_logger

logger = get_logger("studio.services.reaper")

DEFAULT_TASK_TTL_HOURS = 24
DEFAULT_BASE_BACKOFF_SEC = 5.0
DEFAULT_MAX_BACKOFF_SEC = 300.0


class TaskReaper:
    """Detects and safely recovers tasks whose channel execution leases have expired.

    Guarantees:
    - Atomically increments execution_token on reap so stale workers cannot commit outputs.
    - Matches (task_id, worker_id, execution_token) so it CANNOT touch or delete a newer worker's lease.
    - Applies exponential backoff with jitter and enforces the universal 24-hour TaskTTL.
    - Purges expired orphan leases from active_channel_leases.
    """

    def __init__(
        self,
        task_repository: TaskRepository,
        task_service: Optional[TaskService] = None,
        task_ttl_hours: float = DEFAULT_TASK_TTL_HOURS,
        base_backoff_sec: float = DEFAULT_BASE_BACKOFF_SEC,
        max_backoff_sec: float = DEFAULT_MAX_BACKOFF_SEC,
    ) -> None:
        self._task_repo = task_repository
        self._task_service = task_service
        self.task_ttl_hours = task_ttl_hours
        self.base_backoff_sec = base_backoff_sec
        self.max_backoff_sec = max_backoff_sec

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def reap_expired_tasks(self) -> List[str]:
        """Perform a single scan-and-reap cycle for all expired running tasks.

        Returns:
            List[str]: List of task IDs successfully reaped during this pass.
        """
        now_dt = datetime.now(timezone.utc)
        now_utc = now_dt.isoformat()

        # 1. Clean expired orphan rows from active_channel_leases
        cleaned_leases = self._task_repo.clean_expired_leases(cutoff_utc=now_utc)
        if cleaned_leases > 0:
            logger.debug(f"Cleaned {cleaned_leases} expired orphan lease row(s).")

        # 2. Query expired running tasks
        sql = """
        SELECT * FROM tasks
        WHERE status = 'running'
          AND lease_expires_at IS NOT NULL
          AND lease_expires_at <= ?;
        """
        stale_tasks: List[TaskModel] = []
        with self._task_repo.db_manager.connection() as conn:
            cursor = conn.cursor()
            rows = cursor.execute(sql, (now_utc,)).fetchall()
            stale_tasks = [TaskModel.from_row(r) for r in rows]

        reaped_ids: List[str] = []

        for task in stale_tasks:
            reaped = self._reap_single_task(task, now_dt)
            if reaped:
                reaped_ids.append(task.id)

        if reaped_ids:
            logger.warning(f"TaskReaper reaped {len(reaped_ids)} expired task(s): {reaped_ids}")

        return reaped_ids

    def _reap_single_task(self, task: TaskModel, now_dt: datetime) -> bool:
        """Evaluate backoff & TTL, then atomically reap task matching stale token."""
        # 1. Check Universal TTL (24h)
        try:
            created_dt = datetime.fromisoformat(task.created_at)
            if created_dt.tzinfo is None:
                created_dt = created_dt.replace(tzinfo=timezone.utc)
        except Exception:
            created_dt = now_dt

        ttl_limit_dt = created_dt + timedelta(hours=self.task_ttl_hours)

        # 2. Calculate exponential backoff with jitter
        next_retry_num = task.retry_count + 1
        raw_backoff = min(self.max_backoff_sec, self.base_backoff_sec * (2 ** next_retry_num))
        # Add uniform jitter +/- 20%
        jittered_backoff = raw_backoff * random.uniform(0.8, 1.2)
        scheduled_dt = now_dt + timedelta(seconds=jittered_backoff)

        # 3. Determine target state (FAILED if TTL expired or retries exhausted, else RETRY_PENDING)
        if scheduled_dt > ttl_limit_dt:
            target_status = TaskStatus.FAILED
            sched_at = None
            reason = f"Retry delay exceeds task maximum time-to-live (TaskTTL = {self.task_ttl_hours}h)"
        elif next_retry_num >= task.max_retries:
            target_status = TaskStatus.FAILED
            sched_at = None
            reason = f"Task lease expired and retry limit reached ({next_retry_num}/{task.max_retries})"
        else:
            target_status = TaskStatus.RETRY_PENDING
            sched_at = scheduled_dt.isoformat()
            reason = f"Lease expired (worker '{task.worker_id}' timed out). Scheduled retry {next_retry_num} at {sched_at}"

        # 4. Atomic reap with token verification
        success = self._task_repo.reap_stale_task(
            task_id=task.id,
            stale_worker_id=task.worker_id or "",
            stale_token=task.execution_token,
            new_status=target_status,
            scheduled_at=sched_at,
            error_message=reason,
        )

        if success and target_status == TaskStatus.FAILED and self._task_service:
            # Cascade cancellation to downstream dependent tasks if task failed
            try:
                failed_task = self._task_repo.get_by_id(task.id)
                if failed_task:
                    self._task_service._on_task_failed_or_cancelled(failed_task)
            except Exception as e:
                logger.error(f"Failed to cascade abort from reaped task '{task.id}': {e}")

        return success

    def start_periodic(self, interval_sec: float = 10.0) -> None:
        """Start periodic background reaping daemon thread."""
        if self._thread is not None and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._periodic_loop,
            args=(interval_sec,),
            name="TaskReaper-Daemon",
            daemon=True,
        )
        self._thread.start()
        logger.info(f"Started periodic TaskReaper daemon (interval={interval_sec}s).")

    def stop_periodic(self, timeout: float = 3.0) -> None:
        """Stop background periodic reaper loop."""
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self._thread = None
        logger.info("Stopped TaskReaper daemon.")

    def _periodic_loop(self, interval_sec: float) -> None:
        while not self._stop_event.is_set():
            try:
                self.reap_expired_tasks()
            except Exception as e:
                logger.error(f"Error in TaskReaper cycle: {e}")

            if self._stop_event.wait(timeout=interval_sec):
                break

