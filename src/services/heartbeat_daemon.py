"""Heartbeat daemon pulsing lease renewals and detecting stale execution tokens."""
import time
import threading
from typing import Optional, Callable
from src.database.repositories.task_repository import TaskRepository
from src.core.exceptions import StaleExecutionTokenException
from src.core.logger import get_logger

logger = get_logger("studio.services.heartbeat")


class HeartbeatDaemon:
    """Manages periodic lease renewal pulses in a background thread for an active running task.

    Ensures that a worker continuously asserts channel lease ownership in SQLite.
    If renewal fails (lease expired or was revoked by TaskReaper), signals the worker
    to immediately halt execution and prevent writing corrupted outputs.
    """

    def __init__(
        self,
        task_repository: TaskRepository,
        channel_id: str,
        task_id: str,
        worker_id: str,
        execution_token: int,
        pulse_interval_sec: float = 15.0,
        lease_duration_sec: int = 60,
        on_lease_lost: Optional[Callable[[], None]] = None,
    ) -> None:
        self._task_repo = task_repository
        self.channel_id = channel_id
        self.task_id = task_id
        self.worker_id = worker_id
        self.execution_token = execution_token
        self.pulse_interval_sec = pulse_interval_sec
        self.lease_duration_sec = lease_duration_sec
        self.on_lease_lost = on_lease_lost

        self._stop_event = threading.Event()
        self._lease_lost_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def is_lease_lost(self) -> bool:
        """Return True if lease renewal failed or token was invalidated."""
        return self._lease_lost_event.is_set()

    def start(self) -> None:
        """Start the background heartbeat renewal loop."""
        if self._thread is not None and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._lease_lost_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name=f"Heartbeat-{self.task_id[:8]}-{self.worker_id[:8]}",
            daemon=True,
        )
        self._thread.start()
        logger.debug(
            f"Started HeartbeatDaemon for task '{self.task_id}' (worker='{self.worker_id}', "
            f"token={self.execution_token}, interval={self.pulse_interval_sec}s)."
        )

    def stop(self, timeout: float = 2.0) -> None:
        """Stop the heartbeat renewal loop gracefully."""
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self._thread = None
        logger.debug(f"Stopped HeartbeatDaemon for task '{self.task_id}'.")

    def _run_loop(self) -> None:
        """Periodic loop renewing lease until stopped or lease is lost."""
        while not self._stop_event.is_set():
            # Wait for next pulse interval (responsive to stop_event)
            if self._stop_event.wait(timeout=self.pulse_interval_sec):
                break

            # Pulse renewal
            try:
                renewed = self._task_repo.renew_lease(
                    channel_id=self.channel_id,
                    task_id=self.task_id,
                    worker_id=self.worker_id,
                    execution_token=self.execution_token,
                    lease_duration_sec=self.lease_duration_sec,
                )
                if not renewed:
                    self._handle_lost_lease("Lease renewal query matched 0 rows (expired or reaped).")
                    break

                logger.debug(
                    f"Heartbeat pulse succeeded for task '{self.task_id}' (lease extended by {self.lease_duration_sec}s)."
                )
            except StaleExecutionTokenException as e:
                self._handle_lost_lease(f"Stale execution token detected during heartbeat: {e}")
                break
            except Exception as e:
                logger.warning(f"Unexpected error pulsing heartbeat for task '{self.task_id}': {e}")
                # Transient DB error: do not immediately terminate, retry next pulse unless stopped

    def _handle_lost_lease(self, reason: str) -> None:
        """Mark lease as lost and trigger callback to abort worker execution."""
        logger.error(
            f"CRITICAL: Lease LOST for task '{self.task_id}' on channel '{self.channel_id}' "
            f"(worker='{self.worker_id}', token={self.execution_token}). Reason: {reason}"
        )
        self._lease_lost_event.set()
        if self.on_lease_lost:
            try:
                self.on_lease_lost()
            except Exception as err:
                logger.error(f"Error in on_lease_lost callback: {err}")

