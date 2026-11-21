"""Worker pool, execution thread/process, heartbeat integration, and graceful shutdown."""
import os
import time
import random
import signal
import uuid
import logging
import threading
import multiprocessing
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Optional, Dict, Any, Callable, List, Union

from src.database.connection import DatabaseManager
from src.database.repositories.task_repository import TaskRepository
from src.services.task_service import TaskService
from src.services.heartbeat_daemon import HeartbeatDaemon
from src.database.models import TaskModel
from src.core.constants import TaskStatus
from src.core.exceptions import (
    StaleExecutionTokenException,
    TaskNotFoundException,
    ValidationException,
)
from src.core.logger import get_logger

logger = get_logger("studio.services.worker_pool")

DEFAULT_LEASE_DURATION_SEC = 60
DEFAULT_PULSE_INTERVAL_SEC = 15.0
DEFAULT_POLL_INTERVAL_SEC = 0.5
DEFAULT_TASK_TTL_HOURS = 24.0
MAX_LONG_RETRY_DELAY_SEC = 1800.0  # 30 minutes ceiling before requiring operator approval


class RetryPolicy:
    """Computes retry scheduling, backoff, and TTL enforcement according to Section 6 of design doc."""

    def __init__(
        self,
        base_backoff_sec: float = 5.0,
        max_backoff_sec: float = 300.0,
        task_ttl_hours: float = DEFAULT_TASK_TTL_HOURS,
    ) -> None:
        self.base_backoff_sec = base_backoff_sec
        self.max_backoff_sec = max_backoff_sec
        self.task_ttl_hours = task_ttl_hours

    def evaluate_retry(
        self,
        task: TaskModel,
        error: Optional[Exception] = None,
        retry_after_header: Optional[str] = None,
        is_unknown_external_state: bool = False,
    ) -> Dict[str, Any]:
        """Determine next task status, scheduled_at time, and error reason.

        Returns:
            Dict with keys: "target_status", "scheduled_at", "error_message"
        """
        now_dt = datetime.now(timezone.utc)

        # 1. Parse TaskTTL limit
        try:
            created_dt = datetime.fromisoformat(task.created_at)
            if created_dt.tzinfo is None:
                created_dt = created_dt.replace(tzinfo=timezone.utc)
        except Exception:
            created_dt = now_dt

        ttl_limit_dt = created_dt + timedelta(hours=self.task_ttl_hours)

        # 2. Check ambiguous external state (UNKNOWN_EXTERNAL_STATE)
        if is_unknown_external_state:
            return {
                "target_status": TaskStatus.RETRY_PENDING_APPROVAL,
                "scheduled_at": None,
                "error_message": (
                    f"Uncertain external generation state detected for task '{task.id}'. "
                    f"Automatic retry suspended to prevent potential financial double-billing. "
                    f"Operator approval required."
                ),
            }

        # 2b. Check non-retryable errors (unregistered handler or validation error)
        if error and ("no registered execution handler" in str(error) or isinstance(error, ValidationException)):
            return {
                "target_status": TaskStatus.FAILED,
                "scheduled_at": None,
                "error_message": str(error),
            }

        # 3. Calculate delay: provider Retry-After header or exponential backoff
        effective_delay: float = 0.0

        if retry_after_header:
            effective_delay = self._parse_retry_after(retry_after_header, now_dt)
        else:
            # Internal exponential backoff with +/- 20% jitter
            next_retry = task.retry_count + 1
            raw_backoff = min(self.max_backoff_sec, self.base_backoff_sec * (2 ** next_retry))
            effective_delay = raw_backoff * random.uniform(0.8, 1.2)

        scheduled_dt = now_dt + timedelta(seconds=effective_delay)

        # 4. Check Universal TTL (24h)
        if scheduled_dt > ttl_limit_dt:
            return {
                "target_status": TaskStatus.FAILED,
                "scheduled_at": None,
                "error_message": (
                    f"Retry delay ({effective_delay:.1f}s) exceeds task maximum time-to-live "
                    f"(TaskTTL = {self.task_ttl_hours}h)"
                ),
            }

        # 5. Check Max Retries Limit
        if task.retry_count + 1 >= task.max_retries:
            return {
                "target_status": TaskStatus.FAILED,
                "scheduled_at": None,
                "error_message": f"Task exceeded maximum retry attempts ({task.retry_count + 1}/{task.max_retries})",
            }

        # 6. Check Long Delay (> 1800s requires approval)
        if effective_delay > MAX_LONG_RETRY_DELAY_SEC:
            return {
                "target_status": TaskStatus.RETRY_PENDING_APPROVAL,
                "scheduled_at": scheduled_dt.isoformat(),
                "error_message": (
                    f"Long retry delay requested by provider ({effective_delay:.1f}s > 1800s). "
                    f"Suspended for operator review."
                ),
            }

        # 7. Standard retry pending
        return {
            "target_status": TaskStatus.RETRY_PENDING,
            "scheduled_at": scheduled_dt.isoformat(),
            "error_message": f"Scheduled retry {task.retry_count + 1} after {effective_delay:.1f}s: {error or 'Transient failure'}",
        }

    def _parse_retry_after(self, header_val: str, now_dt: datetime) -> float:
        """Parse RFC 7231 / RFC 9110 delta-seconds or HTTP-date."""
        clean = header_val.strip()
        # Case 1: Integer delta-seconds
        if clean.isdigit():
            return float(clean)

        # Case 2: HTTP-date format (e.g. Wed, 21 Oct 2026 07:28:00 GMT)
        try:
            target_dt = parsedate_to_datetime(clean)
            if target_dt.tzinfo is None:
                target_dt = target_dt.replace(tzinfo=timezone.utc)
            delta_sec = (target_dt - now_dt).total_seconds()
            if delta_sec <= 0:
                # Clock skew: server clock slightly behind or time expired -> apply safety jitter
                return random.uniform(3.0, 7.0)
            return delta_sec
        except Exception:
            # Malformed date: fall back to internal base backoff with jitter
            return self.base_backoff_sec * random.uniform(0.8, 1.2)


class WorkerThread:
    """Thread-based worker claiming and executing tasks with Heartbeat and Fencing Tokens."""

    def __init__(
        self,
        worker_id: str,
        task_repository: TaskRepository,
        task_service: TaskService,
        shutdown_event: threading.Event,
        task_handler: Optional[Callable[[TaskModel, HeartbeatDaemon], Dict[str, Any]]] = None,
        lease_duration_sec: int = DEFAULT_LEASE_DURATION_SEC,
        pulse_interval_sec: float = DEFAULT_PULSE_INTERVAL_SEC,
        poll_interval_sec: float = DEFAULT_POLL_INTERVAL_SEC,
        channel_id: Optional[str] = None,
    ) -> None:
        self.worker_id = worker_id
        self._task_repo = task_repository
        self._task_service = task_service
        self._shutdown_event = shutdown_event
        self._task_handler = task_handler or self._default_handler
        self.lease_duration_sec = lease_duration_sec
        self.pulse_interval_sec = pulse_interval_sec
        self.poll_interval_sec = poll_interval_sec
        self.channel_id = channel_id

        self.retry_policy = RetryPolicy()
        self._thread: Optional[threading.Thread] = None
        self._current_task_id: Optional[str] = None
        self._is_busy = False

    @property
    def is_busy(self) -> bool:
        return self._is_busy

    @property
    def current_task_id(self) -> Optional[str]:
        return self._current_task_id

    def start(self) -> None:
        """Start worker thread execution."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run_loop, name=f"Worker-{self.worker_id[:8]}", daemon=True)
        self._thread.start()
        logger.info(f"Worker thread '{self.worker_id}' started.")

    def join(self, timeout: Optional[float] = None) -> None:
        """Wait for worker thread to terminate."""
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def _run_loop(self) -> None:
        """Main worker polling and execution loop."""
        while not self._shutdown_event.is_set():
            claimed_task = None
            try:
                claimed_task = self._task_repo.atomic_claim_next_task(
                    worker_id=self.worker_id,
                    lease_duration_sec=self.lease_duration_sec,
                    channel_id=self.channel_id,
                )
            except Exception as e:
                logger.error(f"Worker '{self.worker_id}' error claiming task: {e}")

            if claimed_task is None:
                # No eligible task available; back off poll interval
                if self._shutdown_event.wait(timeout=self.poll_interval_sec):
                    break
                continue

            # Process claimed task
            self._is_busy = True
            self._current_task_id = claimed_task.id
            try:
                self._process_task(claimed_task)
            finally:
                self._is_busy = False
                self._current_task_id = None

        logger.info(f"Worker thread '{self.worker_id}' loop ended gracefully.")

    def _process_task(self, task: TaskModel) -> None:
        """Execute task with active heartbeat daemon and strict fencing token protection."""
        logger.info(
            f"Worker '{self.worker_id}' executing task '{task.id}' "
            f"(token={task.execution_token}, channel='{task.channel_id}')."
        )

        heartbeat = HeartbeatDaemon(
            task_repository=self._task_repo,
            channel_id=task.channel_id,
            task_id=task.id,
            worker_id=self.worker_id,
            execution_token=task.execution_token,
            pulse_interval_sec=self.pulse_interval_sec,
            lease_duration_sec=self.lease_duration_sec,
        )
        heartbeat.start()

        try:
            # Execute handler
            result = self._task_handler(task, heartbeat)

            # Check if lease was lost during execution
            if heartbeat.is_lease_lost:
                logger.critical(
                    f"Worker '{self.worker_id}' discarding output for task '{task.id}': "
                    f"Lease was lost during execution (Fencing Token guard)."
                )
                return

            # Task succeeded: transition to COMPLETED passing execution_token
            heartbeat.stop()
            self._task_service.transition_status(
                task_id=task.id,
                new_status=TaskStatus.COMPLETED,
                execution_token=task.execution_token,
                worker_id=self.worker_id,
                result=result or {"status": "success"},
            )
            logger.info(f"Worker '{self.worker_id}' completed task '{task.id}' successfully.")

        except StaleExecutionTokenException as e:
            heartbeat.stop()
            logger.error(f"Worker '{self.worker_id}' aborted task '{task.id}': Stale token: {e}")
            # Fencing violation: worker halts execution immediately; do not touch DB
        except Exception as e:
            heartbeat.stop()
            logger.warning(f"Worker '{self.worker_id}' task '{task.id}' failed with exception: {e}")
            if not heartbeat.is_lease_lost:
                # Apply retry policy
                retry_eval = self.retry_policy.evaluate_retry(task, error=e)
                try:
                    self._task_service.transition_status(
                        task_id=task.id,
                        new_status=retry_eval["target_status"],
                        execution_token=task.execution_token,
                        worker_id=self.worker_id,
                        error_message=retry_eval["error_message"],
                        scheduled_at=retry_eval["scheduled_at"],
                    )
                except Exception as err:
                    logger.error(f"Failed to record retry transition for task '{task.id}': {err}")

    def _default_handler(self, task: TaskModel, heartbeat: HeartbeatDaemon) -> Dict[str, Any]:
        """Default handler when no specific task handler is configured."""
        from src.core.constants import TaskType
        if task.task_type != TaskType.GENERAL:
            raise RuntimeError(
                f"Worker '{self.worker_id}' has no registered execution handler for task '{task.id}' "
                f"of type '{task.task_type.value}'. Cannot mark unexecuted task as completed."
            )
        return {"task_id": task.id, "executed_by": self.worker_id, "timestamp": time.time()}


def _process_worker_entrypoint(
    db_path_str: str,
    worker_id: str,
    shutdown_flag: Any,
    lease_duration_sec: int,
    pulse_interval_sec: float,
    poll_interval_sec: float,
    channel_id: Optional[str] = None,
) -> None:
    """Pickleable entrypoint for independent OS worker processes."""
    db_path = Path(db_path_str)
    db_manager = DatabaseManager(db_path=db_path, busy_timeout_ms=15000)
    task_repo = TaskRepository(db_manager=db_manager)
    task_service = TaskService(task_repository=task_repo)

    retry_policy = RetryPolicy()
    logger.info(f"OS WorkerProcess '{worker_id}' (PID {os.getpid()}) started.")

    while not shutdown_flag.is_set():
        claimed_task = None
        try:
            claimed_task = task_repo.atomic_claim_next_task(
                worker_id=worker_id,
                lease_duration_sec=lease_duration_sec,
                channel_id=channel_id,
            )
        except Exception as e:
            logger.error(f"Process worker '{worker_id}' error claiming task: {e}")

        if claimed_task is None:
            time.sleep(poll_interval_sec)
            continue

        # Execute claimed task
        heartbeat = HeartbeatDaemon(
            task_repository=task_repo,
            channel_id=claimed_task.channel_id,
            task_id=claimed_task.id,
            worker_id=worker_id,
            execution_token=claimed_task.execution_token,
            pulse_interval_sec=pulse_interval_sec,
            lease_duration_sec=lease_duration_sec,
        )
        heartbeat.start()

        try:
            from src.core.constants import TaskType
            if claimed_task.task_type != TaskType.GENERAL:
                raise RuntimeError(
                    f"OS WorkerProcess '{worker_id}' has no registered execution handler for task '{claimed_task.id}' "
                    f"of type '{claimed_task.task_type.value}'. Cannot mark unexecuted task as completed."
                )
            # Simulate work for general benchmark tasks
            time.sleep(0.05)

            if not heartbeat.is_lease_lost:
                heartbeat.stop()
                task_service.transition_status(
                    task_id=claimed_task.id,
                    new_status=TaskStatus.COMPLETED,
                    execution_token=claimed_task.execution_token,
                    worker_id=worker_id,
                    result={"worker_pid": os.getpid(), "worker_id": worker_id},
                )
        except Exception as e:
            heartbeat.stop()
            if not heartbeat.is_lease_lost:
                retry_eval = retry_policy.evaluate_retry(claimed_task, error=e)
                try:
                    task_service.transition_status(
                        task_id=claimed_task.id,
                        new_status=retry_eval["target_status"],
                        execution_token=claimed_task.execution_token,
                        worker_id=worker_id,
                        error_message=retry_eval["error_message"],
                        scheduled_at=retry_eval["scheduled_at"],
                    )
                except Exception:
                    pass

    logger.info(f"OS WorkerProcess '{worker_id}' (PID {os.getpid()}) exited cleanly.")


class WorkerPool:
    """Manages worker concurrency, task dispatching, heartbeat pulses, and graceful shutdown."""

    def __init__(
        self,
        task_repository: TaskRepository,
        task_service: TaskService,
        concurrency: int = 2,
        backend: str = "thread",  # "thread" or "process"
        lease_duration_sec: int = DEFAULT_LEASE_DURATION_SEC,
        pulse_interval_sec: float = DEFAULT_PULSE_INTERVAL_SEC,
        poll_interval_sec: float = DEFAULT_POLL_INTERVAL_SEC,
        task_handler: Optional[Callable[[TaskModel, HeartbeatDaemon], Dict[str, Any]]] = None,
        channel_id: Optional[str] = None,
    ) -> None:
        # Enforce concurrency ceiling: default 2, ceiling min(CPU cores, 4)
        cpu_ceiling = min(os.cpu_count() or 2, 4)
        self.concurrency = max(1, min(concurrency, cpu_ceiling))
        self.backend = backend.lower()
        self._task_repo = task_repository
        self._task_service = task_service
        self.lease_duration_sec = lease_duration_sec
        self.pulse_interval_sec = pulse_interval_sec
        self.poll_interval_sec = poll_interval_sec
        self._task_handler = task_handler
        self.channel_id = channel_id

        self._shutdown_event = threading.Event()
        self._mp_shutdown_flag = multiprocessing.Event()
        self._thread_workers: List[WorkerThread] = []
        self._process_workers: List[multiprocessing.Process] = []
        self._is_running = False

    @property
    def is_running(self) -> bool:
        return self._is_running

    def start(self) -> None:
        """Start all workers in the pool."""
        if self._is_running:
            return

        self._shutdown_event.clear()
        self._mp_shutdown_flag.clear()
        self._is_running = True

        if self.backend == "process":
            db_path_str = str(self._task_repo.db_manager.db_path)
            for i in range(self.concurrency):
                w_id = f"worker_proc_{i}_{uuid.uuid4().hex[:6]}"
                p = multiprocessing.Process(
                    target=_process_worker_entrypoint,
                    args=(
                        db_path_str,
                        w_id,
                        self._mp_shutdown_flag,
                        self.lease_duration_sec,
                        self.pulse_interval_sec,
                        self.poll_interval_sec,
                        self.channel_id,
                    ),
                    name=f"WorkerProcess-{i}",
                )
                p.daemon = True
                p.start()
                self._process_workers.append(p)
            logger.info(f"WorkerPool started {self.concurrency} worker process(es).")
        else:
            for i in range(self.concurrency):
                w_id = f"worker_th_{i}_{uuid.uuid4().hex[:6]}"
                w = WorkerThread(
                    worker_id=w_id,
                    task_repository=self._task_repo,
                    task_service=self._task_service,
                    shutdown_event=self._shutdown_event,
                    task_handler=self._task_handler,
                    lease_duration_sec=self.lease_duration_sec,
                    pulse_interval_sec=self.pulse_interval_sec,
                    poll_interval_sec=self.poll_interval_sec,
                    channel_id=self.channel_id,
                )
                w.start()
                self._thread_workers.append(w)
            logger.info(f"WorkerPool started {self.concurrency} worker thread(s).")

    def shutdown(self, timeout_sec: float = 10.0) -> None:
        """Execute graceful shutdown: signal workers, stop claiming, wait for active tasks."""
        if not self._is_running:
            return

        logger.info(f"Initiating graceful shutdown for WorkerPool (timeout={timeout_sec}s)...")
        self._shutdown_event.set()
        self._mp_shutdown_flag.set()

        start_time = time.time()

        if self.backend == "process":
            for p in self._process_workers:
                remaining = max(0.1, timeout_sec - (time.time() - start_time))
                p.join(timeout=remaining)
                if p.is_alive():
                    logger.warning(f"Terminating unresponsive worker process {p.pid}...")
                    p.terminate()
            self._process_workers.clear()
        else:
            for w in self._thread_workers:
                remaining = max(0.1, timeout_sec - (time.time() - start_time))
                w.join(timeout=remaining)
            self._thread_workers.clear()

        self._is_running = False
        logger.info("WorkerPool graceful shutdown completed.")

