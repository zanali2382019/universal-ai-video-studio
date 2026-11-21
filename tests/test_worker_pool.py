"""Unit and integration tests for WorkerPool, HeartbeatDaemon, TaskReaper, and Multiprocessing (Task 3.5 — Milestone 3)."""
import unittest
import tempfile
import gc
import os
import time
import threading
import multiprocessing
from datetime import datetime, timezone, timedelta
from pathlib import Path

from src.database.connection import DatabaseManager
from src.database.migrations import MigrationManager
from src.database.repositories.channel_repository import ChannelRepository
from src.database.repositories.task_repository import TaskRepository
from src.services.task_service import TaskService
from src.services.heartbeat_daemon import HeartbeatDaemon
from src.services.task_reaper import TaskReaper
from src.services.worker_pool import WorkerPool, WorkerThread, RetryPolicy
from src.database.models import ChannelCreate, TaskCreate
from src.core.constants import TaskStatus, TaskType, TaskPriority
from src.core.exceptions import StaleExecutionTokenException, ValidationException


class TestWorkerPoolAndReaper(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root_path = Path(self.temp_dir.name)
        self.db_path = self.root_path / "test_worker_pool.db"

        self.db_manager = DatabaseManager(db_path=self.db_path, busy_timeout_ms=15000)
        self.migration_manager = MigrationManager(self.db_manager)
        self.migration_manager.run_migrations()

        self.channel_repo = ChannelRepository(db_manager=self.db_manager)
        self.task_repo = TaskRepository(db_manager=self.db_manager)
        self.task_service = TaskService(task_repository=self.task_repo, channel_repository=self.channel_repo)

        # Create test channels
        self.channel_repo.create(
            ChannelCreate(id="ch_alpha", name="Channel Alpha", handle="@alpha", niche="tech")
        )
        self.channel_repo.create(
            ChannelCreate(id="ch_beta", name="Channel Beta", handle="@beta", niche="gaming")
        )

    def tearDown(self):
        gc.collect()
        self.temp_dir.cleanup()

    def test_worker_claim_and_execution(self):
        """Scenario 1: Worker claims a queued task, executes it, and marks it COMPLETED."""
        task = self.task_service.create_task(
            channel_id="ch_alpha",
            task_input={"task_type": TaskType.GENERAL, "idempotency_key": "wp_task_01"},
        )
        self.assertEqual(task.status, TaskStatus.QUEUED)

        executed_tasks = []

        def custom_handler(t, heartbeat):
            executed_tasks.append(t.id)
            return {"status": "done"}

        pool = WorkerPool(
            task_repository=self.task_repo,
            task_service=self.task_service,
            concurrency=1,
            task_handler=custom_handler,
            poll_interval_sec=0.1,
        )
        pool.start()

        # Wait for task execution
        time.sleep(0.5)
        pool.shutdown(timeout_sec=2.0)

        self.assertIn(task.id, executed_tasks)
        updated = self.task_service.get_task(task.id)
        self.assertEqual(updated.status, TaskStatus.COMPLETED)
        # Verify lease was automatically released
        self.assertIsNone(self.task_repo.get_active_channel_lease("ch_alpha"))

    def test_intra_channel_single_worker_lock(self):
        """Scenario 2: Prevent two workers from owning the same channel concurrently."""
        t1 = self.task_service.create_task(
            channel_id="ch_alpha",
            task_input={"task_type": TaskType.GENERAL, "idempotency_key": "intra_1", "priority": 20},
        )
        t2 = self.task_service.create_task(
            channel_id="ch_alpha",
            task_input={"task_type": TaskType.GENERAL, "idempotency_key": "intra_2", "priority": 10},
        )

        running_concurrency = []
        concurrency_lock = threading.Lock()
        max_concurrent_for_alpha = 0

        def slow_handler(t, heartbeat):
            nonlocal max_concurrent_for_alpha
            with concurrency_lock:
                running_concurrency.append(t.id)
                if len(running_concurrency) > max_concurrent_for_alpha:
                    max_concurrent_for_alpha = len(running_concurrency)
            time.sleep(0.3)
            with concurrency_lock:
                running_concurrency.remove(t.id)
            return {"status": "ok"}

        pool = WorkerPool(
            task_repository=self.task_repo,
            task_service=self.task_service,
            concurrency=2,  # 2 workers competing
            task_handler=slow_handler,
            poll_interval_sec=0.05,
        )
        pool.start()
        time.sleep(1.0)
        pool.shutdown(timeout_sec=2.0)

        # Max concurrent tasks for ch_alpha must NEVER exceed 1
        self.assertEqual(max_concurrent_for_alpha, 1)
        self.assertEqual(self.task_service.get_task(t1.id).status, TaskStatus.COMPLETED)
        self.assertEqual(self.task_service.get_task(t2.id).status, TaskStatus.COMPLETED)

    def test_inter_channel_parallelism_under_worker_limit(self):
        """Scenario 3: Tasks from different channels execute concurrently up to concurrency limit."""
        t_alpha = self.task_service.create_task(
            channel_id="ch_alpha",
            task_input={"task_type": TaskType.GENERAL, "idempotency_key": "inter_alpha"},
        )
        t_beta = self.task_service.create_task(
            channel_id="ch_beta",
            task_input={"task_type": TaskType.GENERAL, "idempotency_key": "inter_beta"},
        )

        concurrency_seen = 0
        lock = threading.Lock()
        active = []

        def parallel_handler(t, heartbeat):
            nonlocal concurrency_seen
            with lock:
                active.append(t.id)
                if len(active) > concurrency_seen:
                    concurrency_seen = len(active)
            time.sleep(0.3)
            with lock:
                active.remove(t.id)
            return {"status": "parallel_done"}

        pool = WorkerPool(
            task_repository=self.task_repo,
            task_service=self.task_service,
            concurrency=2,
            task_handler=parallel_handler,
            poll_interval_sec=0.05,
        )
        pool.start()
        time.sleep(0.6)
        pool.shutdown(timeout_sec=2.0)

        # Both channels ran concurrently (concurrency_seen == 2)
        self.assertEqual(concurrency_seen, 2)
        self.assertEqual(self.task_service.get_task(t_alpha.id).status, TaskStatus.COMPLETED)
        self.assertEqual(self.task_service.get_task(t_beta.id).status, TaskStatus.COMPLETED)

    def test_heartbeat_lease_renewal(self):
        """Scenario 4: Heartbeat daemon successfully extends lease expiration."""
        task = self.task_repo.create_task(
            TaskCreate(channel_id="ch_alpha", task_type=TaskType.GENERAL, idempotency_key="hb_test")
        )
        claimed = self.task_repo.atomic_claim_next_task(worker_id="w_hb", lease_duration_sec=2)
        initial_lease = self.task_repo.get_active_channel_lease("ch_alpha")
        self.assertIsNotNone(initial_lease)
        initial_expiry = initial_lease.lease_expires_at

        # Start heartbeat pulsing every 0.1s extending by 5s
        daemon = HeartbeatDaemon(
            task_repository=self.task_repo,
            channel_id="ch_alpha",
            task_id=task.id,
            worker_id="w_hb",
            execution_token=claimed.execution_token,
            pulse_interval_sec=0.1,
            lease_duration_sec=5,
        )
        daemon.start()
        time.sleep(0.3)
        daemon.stop()

        updated_lease = self.task_repo.get_active_channel_lease("ch_alpha")
        self.assertIsNotNone(updated_lease)
        self.assertGreater(updated_lease.lease_expires_at, initial_expiry)
        self.assertFalse(daemon.is_lease_lost)

    def test_heartbeat_rejected_with_stale_token(self):
        """Scenario 5: Heartbeat detects stale token and flags is_lease_lost."""
        task = self.task_repo.create_task(
            TaskCreate(channel_id="ch_alpha", task_type=TaskType.GENERAL, idempotency_key="hb_stale")
        )
        claimed = self.task_repo.atomic_claim_next_task(worker_id="w_stale", lease_duration_sec=10)

        lease_lost_callback_called = False

        def on_lost():
            nonlocal lease_lost_callback_called
            lease_lost_callback_called = True

        # Heartbeat configured with token 99 (mismatched)
        daemon = HeartbeatDaemon(
            task_repository=self.task_repo,
            channel_id="ch_alpha",
            task_id=task.id,
            worker_id="w_stale",
            execution_token=99,  # Stale token
            pulse_interval_sec=0.1,
            lease_duration_sec=10,
            on_lease_lost=on_lost,
        )
        daemon.start()
        time.sleep(0.3)
        daemon.stop()

        self.assertTrue(daemon.is_lease_lost)
        self.assertTrue(lease_lost_callback_called)

    def test_lease_expiry_and_reaper_recovery(self):
        """Scenario 6: TaskReaper detects expired lease, increments token, and reschedules with backoff."""
        task = self.task_repo.create_task(
            TaskCreate(channel_id="ch_alpha", task_type=TaskType.GENERAL, idempotency_key="reap_test")
        )
        # Claim with short lease (e.g. 1 second)
        claimed = self.task_repo.atomic_claim_next_task(worker_id="w_die", lease_duration_sec=1)
        self.assertEqual(claimed.execution_token, 1)

        # Artificially expire the lease in SQLite
        expired_past = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
        with self.db_manager.transaction() as conn:
            conn.execute(
                "UPDATE active_channel_leases SET lease_expires_at = ? WHERE channel_id = ?;",
                (expired_past, "ch_alpha"),
            )
            conn.execute(
                "UPDATE tasks SET lease_expires_at = ? WHERE id = ?;",
                (expired_past, task.id),
            )

        # Run TaskReaper
        reaper = TaskReaper(task_repository=self.task_repo, task_service=self.task_service)
        reaped_ids = reaper.reap_expired_tasks()

        self.assertIn(task.id, reaped_ids)
        reaped_task = self.task_repo.get_by_id(task.id)
        self.assertEqual(reaped_task.status, TaskStatus.RETRY_PENDING)
        self.assertEqual(reaped_task.execution_token, 2)  # Incremented token!
        self.assertEqual(reaped_task.retry_count, 1)
        self.assertIsNotNone(reaped_task.scheduled_at)

    def test_reaper_cannot_delete_newer_lease(self):
        """Scenario 7: Reaper with old token cannot touch a newer worker's active lease."""
        task = self.task_repo.create_task(
            TaskCreate(channel_id="ch_alpha", task_type=TaskType.GENERAL, idempotency_key="reaper_guard")
        )
        c1 = self.task_repo.atomic_claim_next_task(worker_id="w1", lease_duration_sec=1)
        # Simulate legitimate reap
        self.task_repo.reap_stale_task(task.id, "w1", 1)

        # Worker 2 claims reaped task -> token = 3
        c2 = self.task_repo.atomic_claim_next_task(worker_id="w2", lease_duration_sec=60)
        self.assertEqual(c2.execution_token, 3)

        # Delayed reaper run targeting w1 and token 1
        reaped = self.task_repo.reap_stale_task(
            task_id=task.id,
            stale_worker_id="w1",
            stale_token=1,
            new_status=TaskStatus.RETRY_PENDING,
        )
        self.assertFalse(reaped)

        # Worker 2's lease remains untouched and valid
        lease = self.task_repo.get_active_channel_lease("ch_alpha")
        self.assertIsNotNone(lease)
        self.assertEqual(lease.worker_id, "w2")
        self.assertEqual(lease.execution_token, 3)

    def test_stale_worker_prevented_from_completing(self):
        """Scenario 8: Stale worker whose lease was lost/reaped is blocked from completing."""
        task = self.task_service.create_task(
            channel_id="ch_alpha",
            task_input={"task_type": TaskType.GENERAL, "idempotency_key": "stale_complete_block"},
        )
        c = self.task_repo.atomic_claim_next_task(worker_id="w_slow", lease_duration_sec=60)
        token = c.execution_token

        # Simulate reaper reclaiming the task (token becomes 2)
        self.task_repo.reap_stale_task(task.id, "w_slow", token)

        # Worker 2 claims it (status becomes RUNNING, token becomes 3)
        c2 = self.task_repo.atomic_claim_next_task(worker_id="w_fast", lease_duration_sec=60)
        self.assertEqual(c2.execution_token, 3)

        # w_slow wakes up and attempts to complete using stale token=1 on RUNNING task
        with self.assertRaises(StaleExecutionTokenException):
            self.task_service.transition_status(
                task_id=task.id,
                new_status=TaskStatus.COMPLETED,
                execution_token=token,
                result={"result": "garbage"},
            )

    def test_retry_and_backoff_evaluation(self):
        """Scenario 9: RetryPolicy computes exponential backoff with jitter and max backoff cap."""
        policy = RetryPolicy(base_backoff_sec=5.0, max_backoff_sec=60.0)
        task = self.task_repo.create_task(
            TaskCreate(channel_id="ch_alpha", task_type=TaskType.GENERAL, idempotency_key="backoff_calc")
        )

        res = policy.evaluate_retry(task, error=Exception("timeout"))
        self.assertEqual(res["target_status"], TaskStatus.RETRY_PENDING)
        self.assertIsNotNone(res["scheduled_at"])

        # Check ambiguous external state
        res_ambiguous = policy.evaluate_retry(task, is_unknown_external_state=True)
        self.assertEqual(res_ambiguous["target_status"], TaskStatus.RETRY_PENDING_APPROVAL)
        self.assertIn("Automatic retry suspended", res_ambiguous["error_message"])

    def test_retry_after_and_ttl_enforcement(self):
        """Scenario 10: RetryPolicy respects Retry-After delta-seconds, HTTP-date, and 24h TaskTTL."""
        policy = RetryPolicy(task_ttl_hours=1.0)  # 1 hour TTL for testing
        task = self.task_repo.create_task(
            TaskCreate(channel_id="ch_alpha", task_type=TaskType.GENERAL, idempotency_key="retry_after_ttl")
        )

        # 1. Delta-seconds
        res_delta = policy.evaluate_retry(task, retry_after_header="120")
        self.assertEqual(res_delta["target_status"], TaskStatus.RETRY_PENDING)

        # 2. HTTP-date in future
        future_http = (datetime.now(timezone.utc) + timedelta(seconds=60)).strftime("%a, %d %b %Y %H:%M:%S GMT")
        res_http = policy.evaluate_retry(task, retry_after_header=future_http)
        self.assertEqual(res_http["target_status"], TaskStatus.RETRY_PENDING)

        # 3. Exceeds TTL (> 1 hour TTL)
        res_ttl = policy.evaluate_retry(task, retry_after_header="7200")
        self.assertEqual(res_ttl["target_status"], TaskStatus.FAILED)
        self.assertIn("exceeds task maximum time-to-live", res_ttl["error_message"])

    def test_graceful_shutdown(self):
        """Scenario 11: WorkerPool shuts down cleanly and allows active task to complete."""
        task = self.task_service.create_task(
            channel_id="ch_alpha",
            task_input={"task_type": TaskType.GENERAL, "idempotency_key": "shutdown_test"},
        )

        started_event = threading.Event()

        def slow_task(t, heartbeat):
            started_event.set()
            time.sleep(0.3)
            return {"res": "ok"}

        pool = WorkerPool(
            task_repository=self.task_repo,
            task_service=self.task_service,
            concurrency=1,
            task_handler=slow_task,
            poll_interval_sec=0.05,
        )
        pool.start()
        started_event.wait(timeout=2.0)

        # Trigger shutdown while task is running
        pool.shutdown(timeout_sec=3.0)

        self.assertFalse(pool.is_running)
        updated = self.task_service.get_task(task.id)
        self.assertEqual(updated.status, TaskStatus.COMPLETED)

    def test_no_new_claims_during_shutdown(self):
        """Scenario 12: Workers do not claim new tasks after shutdown event is set."""
        shutdown_evt = threading.Event()
        shutdown_evt.set()  # Already shut down

        w = WorkerThread(
            worker_id="w_noop",
            task_repository=self.task_repo,
            task_service=self.task_service,
            shutdown_event=shutdown_evt,
        )
        self.task_service.create_task(
            channel_id="ch_alpha",
            task_input={"task_type": TaskType.GENERAL, "idempotency_key": "no_claim_key"},
        )

        w.start()
        w.join(timeout=0.5)

        # Task remains in QUEUED; was never claimed
        t = self.task_repo.get_by_idempotency_key("ch_alpha", "no_claim_key")
        self.assertEqual(t.status, TaskStatus.QUEUED)

    def test_multiprocessing_concurrency(self):
        """Scenario 13: Multi-process concurrency using multiprocessing.Process against shared SQLite file."""
        # Create 4 tasks across 4 channels
        task_ids = []
        for i in range(4):
            ch = f"ch_mp_{i}"
            self.channel_repo.create(ChannelCreate(id=ch, name=f"Ch {i}", handle=f"@{ch}", niche="mp"))
            t = self.task_service.create_task(
                channel_id=ch,
                task_input={"task_type": TaskType.GENERAL, "idempotency_key": f"mp_task_{i}"},
            )
            task_ids.append(t.id)

        # Launch WorkerPool with backend='process' (2 independent OS processes)
        pool = WorkerPool(
            task_repository=self.task_repo,
            task_service=self.task_service,
            concurrency=2,
            backend="process",
            poll_interval_sec=0.05,
        )
        pool.start()

        # Wait for processes to consume all 4 tasks
        time.sleep(2.0)
        pool.shutdown(timeout_sec=4.0)

        # All 4 tasks must be COMPLETED across process boundaries!
        for tid in task_ids:
            task = self.task_service.get_task(tid)
            self.assertEqual(task.status, TaskStatus.COMPLETED)
            self.assertIsNotNone(task.result)
            self.assertIn("worker_pid", task.result)

    def test_multiple_sqlite_connections_on_real_temp_db(self):
        """Scenario 14: Multiple SQLite connections concurrently perform atomic claims and updates."""
        # Create 6 tasks across 6 channels
        for i in range(6):
            ch = f"ch_conn_{i}"
            self.channel_repo.create(ChannelCreate(id=ch, name=f"Ch {i}", handle=f"@{ch}", niche="test"))
            self.task_repo.create_task(
                TaskCreate(channel_id=ch, task_type=TaskType.GENERAL, idempotency_key=f"conn_task_{i}")
            )

        claimed = []
        claim_lock = threading.Lock()

        def independent_client(worker_id: str):
            # Each thread opens its own connection via DatabaseManager
            mgr = DatabaseManager(db_path=self.db_path, busy_timeout_ms=15000)
            repo = TaskRepository(db_manager=mgr)
            task = repo.atomic_claim_next_task(worker_id=worker_id, lease_duration_sec=30)
            if task:
                with claim_lock:
                    claimed.append(task.id)

        threads = [threading.Thread(target=independent_client, args=(f"client_{i}",)) for i in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Exactly 6 unique tasks claimed
        self.assertEqual(len(claimed), 6)
        self.assertEqual(len(set(claimed)), 6)

    def test_sqlite_busy_handling_in_worker_pool(self):
        """Scenario 15: Lock contention resilience under heavy concurrent pool operations."""
        # Create tasks
        for i in range(8):
            ch = f"ch_busy_{i}"
            self.channel_repo.create(ChannelCreate(id=ch, name=f"Ch {i}", handle=f"@{ch}", niche="busy"))
            self.task_service.create_task(
                channel_id=ch,
                task_input={"task_type": TaskType.GENERAL, "idempotency_key": f"busy_task_{i}"},
            )

        pool = WorkerPool(
            task_repository=self.task_repo,
            task_service=self.task_service,
            concurrency=4,  # 4 concurrent workers
            poll_interval_sec=0.02,
        )
        pool.start()
        time.sleep(1.5)
        pool.shutdown(timeout_sec=3.0)

        # All tasks successfully completed without any unhandled DatabaseLockedException
        for i in range(8):
            t = self.task_repo.get_by_idempotency_key(f"ch_busy_{i}", f"busy_task_{i}")
            self.assertEqual(t.status, TaskStatus.COMPLETED)


if __name__ == "__main__":
    unittest.main()
