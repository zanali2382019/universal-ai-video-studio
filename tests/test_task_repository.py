"""Unit and integration tests for TaskRepository and Persistent Task Queue (Task 3.5 — Milestone 1)."""
import unittest
import tempfile
import gc
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from src.database.connection import DatabaseManager
from src.database.migrations import MigrationManager
from src.database.repositories.channel_repository import ChannelRepository
from src.database.repositories.task_repository import TaskRepository
from src.database.models import ChannelCreate, TaskCreate, TaskCheckpointCreate
from src.core.constants import TaskStatus, TaskType, TaskPriority
from src.core.exceptions import (
    TaskNotFoundException,
    StaleExecutionTokenException,
    ChannelLeaseConflictException,
    ValidationException,
)


class TestTaskRepository(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root_path = Path(self.temp_dir.name)
        self.db_path = self.root_path / "test_task_queue.db"

        self.db_manager = DatabaseManager(db_path=self.db_path, busy_timeout_ms=15000)
        self.migration_manager = MigrationManager(self.db_manager)
        self.migration_manager.run_migrations()

        self.channel_repo = ChannelRepository(db_manager=self.db_manager)
        self.task_repo = TaskRepository(db_manager=self.db_manager)

        # Create two test channels
        self.channel_repo.create(
            ChannelCreate(
                id="ch_alpha",
                name="Channel Alpha",
                handle="@alpha",
                niche="tech",
            )
        )
        self.channel_repo.create(
            ChannelCreate(
                id="ch_beta",
                name="Channel Beta",
                handle="@beta",
                niche="history",
            )
        )

    def tearDown(self):
        gc.collect()
        self.temp_dir.cleanup()

    def test_idempotent_task_creation(self):
        """Scenario 1: duplicate idempotency_key returns existing task without duplicate rows."""
        task_in = TaskCreate(
            channel_id="ch_alpha",
            task_type=TaskType.SCRIPT_GENERATION,
            idempotency_key="ep01_script_gen_key",
            payload={"topic": "AI in 2026"},
            priority=TaskPriority.HIGH.value,
        )

        task1 = self.task_repo.create_task(task_in)
        self.assertIsNotNone(task1.id)
        self.assertEqual(task1.status, TaskStatus.QUEUED)
        self.assertEqual(task1.idempotency_key, "ep01_script_gen_key")

        # Re-creating with exact same idempotency_key on same channel returns task1
        task2 = self.task_repo.create_task(task_in)
        self.assertEqual(task1.id, task2.id)

        # Total tasks for channel alpha should be exactly 1
        tasks_alpha = self.task_repo.list_tasks(channel_id="ch_alpha")
        self.assertEqual(len(tasks_alpha), 1)

        # Different channel using the same idempotency key is allowed and isolated
        task_beta_in = TaskCreate(
            channel_id="ch_beta",
            task_type=TaskType.SCRIPT_GENERATION,
            idempotency_key="ep01_script_gen_key",
            payload={"topic": "Roman Empire"},
        )
        task_beta = self.task_repo.create_task(task_beta_in)
        self.assertNotEqual(task1.id, task_beta.id)
        self.assertEqual(task_beta.channel_id, "ch_beta")

    def test_atomic_claim_concurrency(self):
        """Scenario 2: Multi-worker competition claims unique tasks without race conditions."""
        # Create 10 distinct tasks across 10 channels or distinct channels
        for i in range(10):
            ch_id = f"ch_worker_{i}"
            self.channel_repo.create(ChannelCreate(id=ch_id, name=f"Channel {i}", handle=f"@ch_{i}", niche="test"))
            self.task_repo.create_task(
                TaskCreate(
                    channel_id=ch_id,
                    task_type=TaskType.GENERAL,
                    idempotency_key=f"task_key_{i}",
                    payload={"index": i},
                )
            )

        claimed_tasks = []
        claimed_lock = threading.Lock()

        def worker_claim_target(worker_id: str):
            claimed = self.task_repo.atomic_claim_next_task(worker_id=worker_id, lease_duration_sec=30)
            if claimed:
                with claimed_lock:
                    claimed_tasks.append((worker_id, claimed.id))

        threads = []
        for i in range(10):
            t = threading.Thread(target=worker_claim_target, args=(f"worker_{i}",))
            threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All 10 tasks must be claimed, each exactly once
        self.assertEqual(len(claimed_tasks), 10)
        claimed_ids = [t[1] for t in claimed_tasks]
        self.assertEqual(len(set(claimed_ids)), 10)

    def test_intra_channel_serialization(self):
        """Scenario 3: Prevents two tasks from the same channel running concurrently."""
        t1 = self.task_repo.create_task(
            TaskCreate(
                channel_id="ch_alpha",
                task_type=TaskType.IMAGE_GENERATION,
                idempotency_key="alpha_task_1",
                priority=TaskPriority.HIGH.value,
            )
        )
        t2 = self.task_repo.create_task(
            TaskCreate(
                channel_id="ch_alpha",
                task_type=TaskType.VOICE_SYNTHESIS,
                idempotency_key="alpha_task_2",
                priority=TaskPriority.NORMAL.value,
            )
        )

        # Worker 1 claims task for ch_alpha
        claimed_1 = self.task_repo.atomic_claim_next_task(worker_id="w_alpha_1", lease_duration_sec=60)
        self.assertIsNotNone(claimed_1)
        self.assertEqual(claimed_1.id, t1.id)
        self.assertEqual(claimed_1.status, TaskStatus.RUNNING)

        # Lease is active for ch_alpha
        lease = self.task_repo.get_active_channel_lease("ch_alpha")
        self.assertIsNotNone(lease)
        self.assertEqual(lease.task_id, t1.id)
        self.assertEqual(lease.worker_id, "w_alpha_1")

        # Worker 2 attempts to claim next task. Must return None because ch_alpha is leased!
        claimed_2 = self.task_repo.atomic_claim_next_task(worker_id="w_alpha_2", lease_duration_sec=60)
        self.assertIsNone(claimed_2)

    def test_inter_channel_parallelism(self):
        """Scenario 4: Allows tasks from two different channels to run concurrently."""
        t_alpha = self.task_repo.create_task(
            TaskCreate(
                channel_id="ch_alpha",
                task_type=TaskType.VIDEO_RENDERING,
                idempotency_key="alpha_render",
            )
        )
        t_beta = self.task_repo.create_task(
            TaskCreate(
                channel_id="ch_beta",
                task_type=TaskType.VIDEO_RENDERING,
                idempotency_key="beta_render",
            )
        )

        # Worker 1 claims Alpha
        claim_1 = self.task_repo.atomic_claim_next_task(worker_id="worker_1", lease_duration_sec=60)
        self.assertIsNotNone(claim_1)
        self.assertEqual(claim_1.id, t_alpha.id)

        # Worker 2 claims Beta in parallel
        claim_2 = self.task_repo.atomic_claim_next_task(worker_id="worker_2", lease_duration_sec=60)
        self.assertIsNotNone(claim_2)
        self.assertEqual(claim_2.id, t_beta.id)

        # Both channels have active leases held concurrently
        lease_alpha = self.task_repo.get_active_channel_lease("ch_alpha")
        lease_beta = self.task_repo.get_active_channel_lease("ch_beta")
        self.assertIsNotNone(lease_alpha)
        self.assertIsNotNone(lease_beta)
        self.assertEqual(lease_alpha.worker_id, "worker_1")
        self.assertEqual(lease_beta.worker_id, "worker_2")

    def test_stale_worker_renew_lease_blocked(self):
        """Scenario 5: Worker with stale token or expired lease cannot renew lease."""
        task = self.task_repo.create_task(
            TaskCreate(
                channel_id="ch_alpha",
                task_type=TaskType.SCRIPT_GENERATION,
                idempotency_key="stale_renew_key",
            )
        )

        # Worker 1 claims task -> token = 1
        claim = self.task_repo.atomic_claim_next_task(worker_id="worker_old", lease_duration_sec=60)
        self.assertEqual(claim.execution_token, 1)

        # Attempt renew with stale token (e.g. token=0) -> blocked
        renew_res = self.task_repo.renew_lease(
            channel_id="ch_alpha",
            task_id=task.id,
            worker_id="worker_old",
            execution_token=0,
            lease_duration_sec=60,
        )
        self.assertFalse(renew_res)

        # Attempt renew with mismatched worker_id -> blocked
        renew_res_wrong_worker = self.task_repo.renew_lease(
            channel_id="ch_alpha",
            task_id=task.id,
            worker_id="worker_imposter",
            execution_token=1,
            lease_duration_sec=60,
        )
        self.assertFalse(renew_res_wrong_worker)

        # Legitimate renew with correct token & worker -> success
        renew_success = self.task_repo.renew_lease(
            channel_id="ch_alpha",
            task_id=task.id,
            worker_id="worker_old",
            execution_token=1,
            lease_duration_sec=60,
        )
        self.assertTrue(renew_success)

    def test_stale_worker_status_update_blocked(self):
        """Scenario 6: Old worker with stale token raises StaleExecutionTokenException on status update."""
        task = self.task_repo.create_task(
            TaskCreate(
                channel_id="ch_alpha",
                task_type=TaskType.SCRIPT_GENERATION,
                idempotency_key="stale_update_key",
            )
        )

        # Claim 1: token = 1
        claimed = self.task_repo.atomic_claim_next_task(worker_id="worker_slow", lease_duration_sec=10)
        self.assertEqual(claimed.execution_token, 1)

        # Simulate TaskReaper reaping the task due to stale timeout
        reaped = self.task_repo.reap_stale_task(
            task_id=task.id,
            stale_worker_id="worker_slow",
            stale_token=1,
            error_message="Lease timeout in test",
        )
        self.assertTrue(reaped)

        # Task in DB now has execution_token = 2 and status = RETRY_PENDING
        task_reaped = self.task_repo.get_by_id(task.id)
        self.assertEqual(task_reaped.execution_token, 2)
        self.assertEqual(task_reaped.status, TaskStatus.RETRY_PENDING)

        # Old worker wakes up and attempts to complete the task with stale token=1
        with self.assertRaises(StaleExecutionTokenException) as ctx:
            self.task_repo.update_task_status(
                task_id=task.id,
                new_status=TaskStatus.COMPLETED,
                execution_token=1,
                result={"data": "zombie output"},
            )
        self.assertIn("task '", str(ctx.exception))
        self.assertEqual(ctx.exception.error_code, "STALE_EXECUTION_TOKEN")
        self.assertEqual(ctx.exception.details["expected_token"], 2)
        self.assertEqual(ctx.exception.details["actual_token"], 1)

    def test_reaper_cannot_release_newer_worker_lease(self):
        """Scenario 7: Reaper targeting an old worker/token cannot touch a newer worker's lease."""
        task = self.task_repo.create_task(
            TaskCreate(
                channel_id="ch_alpha",
                task_type=TaskType.IMAGE_GENERATION,
                idempotency_key="reaper_safety_key",
            )
        )

        # Claim 1 by worker_1 (token=1)
        c1 = self.task_repo.atomic_claim_next_task(worker_id="worker_1", lease_duration_sec=10)
        self.assertEqual(c1.execution_token, 1)

        # Simulate legitimate reap of worker_1
        self.assertTrue(self.task_repo.reap_stale_task(task.id, "worker_1", 1))

        # Worker 2 now claims the reaped task (token: 1 -> reaped 2 -> claim 3)
        c2 = self.task_repo.atomic_claim_next_task(worker_id="worker_2", lease_duration_sec=60)
        self.assertIsNotNone(c2)
        self.assertEqual(c2.execution_token, 3)
        self.assertEqual(c2.worker_id, "worker_2")

        # Now an old/delayed reaper run attempts to reap worker_1 with token 1 again
        reap_delayed = self.task_repo.reap_stale_task(
            task_id=task.id,
            stale_worker_id="worker_1",
            stale_token=1,
            error_message="Delayed reaper run",
        )
        self.assertFalse(reap_delayed)

        # Verify worker 2's lease and task remain intact and RUNNING!
        active_lease = self.task_repo.get_active_channel_lease("ch_alpha")
        self.assertIsNotNone(active_lease)
        self.assertEqual(active_lease.worker_id, "worker_2")
        self.assertEqual(active_lease.execution_token, 3)

        task_after = self.task_repo.get_by_id(task.id)
        self.assertEqual(task_after.status, TaskStatus.RUNNING)
        self.assertEqual(task_after.execution_token, 3)

    def test_atomic_lease_release_on_completion(self):
        """Scenario 8: Completing task via update_task_status atomically removes row from active_channel_leases."""
        task1 = self.task_repo.create_task(
            TaskCreate(
                channel_id="ch_alpha",
                task_type=TaskType.SCRIPT_GENERATION,
                idempotency_key="task_done_release_1",
            )
        )
        task2 = self.task_repo.create_task(
            TaskCreate(
                channel_id="ch_alpha",
                task_type=TaskType.VOICE_SYNTHESIS,
                idempotency_key="task_done_release_2",
            )
        )

        # Claim task1
        claim1 = self.task_repo.atomic_claim_next_task(worker_id="worker_alpha", lease_duration_sec=60)
        self.assertIsNotNone(claim1)
        self.assertIsNotNone(self.task_repo.get_active_channel_lease("ch_alpha"))

        # While task1 is RUNNING, task2 cannot be claimed
        self.assertIsNone(self.task_repo.atomic_claim_next_task(worker_id="worker_beta"))

        # Complete task1
        completed_task = self.task_repo.update_task_status(
            task_id=task1.id,
            new_status=TaskStatus.COMPLETED,
            execution_token=claim1.execution_token,
            result={"text": "Script completed"},
        )
        self.assertEqual(completed_task.status, TaskStatus.COMPLETED)

        # active_channel_leases for ch_alpha must be empty now!
        self.assertIsNone(self.task_repo.get_active_channel_lease("ch_alpha"))

        # Now task2 can be claimed immediately
        claim2 = self.task_repo.atomic_claim_next_task(worker_id="worker_alpha", lease_duration_sec=60)
        self.assertIsNotNone(claim2)
        self.assertEqual(claim2.id, task2.id)

    def test_sqlite_busy_handling(self):
        """Scenario 9: High concurrency lock contention is handled gracefully by busy_timeout."""
        # Create multiple tasks across channels
        num_tasks = 8
        for i in range(num_tasks):
            ch = f"ch_contention_{i}"
            self.channel_repo.create(ChannelCreate(id=ch, name=f"Ch {i}", handle=f"@{ch}", niche="contention"))
            self.task_repo.create_task(
                TaskCreate(channel_id=ch, task_type=TaskType.GENERAL, idempotency_key=f"contention_{i}")
            )

        errors = []

        def aggressive_worker(w_id: str):
            try:
                for _ in range(5):
                    self.task_repo.atomic_claim_next_task(worker_id=w_id, lease_duration_sec=10)
                    time.sleep(0.01)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=aggressive_worker, args=(f"worker_{i}",)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # No DatabaseLockedException unhandled error occurred
        self.assertEqual(len(errors), 0)

    def test_cold_boot_lease_cleanup(self):
        """Scenario 10: Expired leases in active_channel_leases are purged automatically on boot/claim."""
        task = self.task_repo.create_task(
            TaskCreate(
                channel_id="ch_alpha",
                task_type=TaskType.VOICE_SYNTHESIS,
                idempotency_key="cold_boot_task",
            )
        )

        # Insert a simulated expired lease directly into active_channel_leases
        expired_past = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        with self.db_manager.transaction() as conn:
            conn.execute(
                """
                INSERT INTO active_channel_leases (channel_id, task_id, worker_id, execution_token, lease_expires_at)
                VALUES (?, ?, ?, ?, ?);
                """,
                ("ch_alpha", task.id, "crashed_worker", 99, expired_past),
            )

        # Before claim, active lease row exists in DB
        self.assertIsNotNone(self.task_repo.get_active_channel_lease("ch_alpha"))

        # Claiming should automatically purge the expired lease and allow claiming ch_alpha task
        claimed = self.task_repo.atomic_claim_next_task(worker_id="fresh_worker", lease_duration_sec=60)
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed.id, task.id)

        # Active lease is now owned by fresh_worker
        active_lease = self.task_repo.get_active_channel_lease("ch_alpha")
        self.assertIsNotNone(active_lease)
        self.assertEqual(active_lease.worker_id, "fresh_worker")

    def test_step_checkpointing_with_token(self):
        """Scenario 11: Checkpointing updates progress, enforces token, and supports idempotent upsert."""
        task = self.task_repo.create_task(
            TaskCreate(
                channel_id="ch_alpha",
                task_type=TaskType.VIDEO_RENDERING,
                idempotency_key="checkpoint_task",
                total_steps=5,
            )
        )

        claimed = self.task_repo.atomic_claim_next_task(worker_id="worker_cp", lease_duration_sec=60)
        token = claimed.execution_token

        # Save Step 1 checkpoint
        cp1 = self.task_repo.save_checkpoint(
            TaskCheckpointCreate(
                task_id=task.id,
                step_name="audio_synthesized",
                step_index=1,
                step_data={"audio_file": "voice.wav", "duration_sec": 45.2},
                execution_token=token,
            )
        )
        self.assertEqual(cp1.step_index, 1)

        # Verify task progress updated
        t_prog = self.task_repo.get_by_id(task.id)
        self.assertEqual(t_prog.current_step, "audio_synthesized")
        self.assertEqual(t_prog.completed_steps, 1)

        # Save Step 2 checkpoint
        self.task_repo.save_checkpoint(
            TaskCheckpointCreate(
                task_id=task.id,
                step_name="images_generated",
                step_index=2,
                step_data={"images_count": 8},
                execution_token=token,
            )
        )

        # Retrieve checkpoints list
        cps = self.task_repo.get_checkpoints(task.id)
        self.assertEqual(len(cps), 2)
        self.assertEqual(cps[0].step_name, "audio_synthesized")
        self.assertEqual(cps[1].step_name, "images_generated")

        # Attempt to save checkpoint with stale token -> StaleExecutionTokenException
        with self.assertRaises(StaleExecutionTokenException):
            self.task_repo.save_checkpoint(
                TaskCheckpointCreate(
                    task_id=task.id,
                    step_name="video_rendered",
                    step_index=3,
                    step_data={"status": "fake"},
                    execution_token=999,  # Mismatched token
                )
            )

    def test_fair_scheduling_round_robin(self):
        """Scenario 12: Fair scheduling favors channel with oldest last_task_dispatched_at."""
        # Channel Alpha has 3 tasks
        for i in range(3):
            self.task_repo.create_task(
                TaskCreate(
                    channel_id="ch_alpha",
                    task_type=TaskType.GENERAL,
                    idempotency_key=f"fair_alpha_{i}",
                    priority=TaskPriority.NORMAL.value,
                )
            )

        # Channel Beta has 1 task with same priority
        self.task_repo.create_task(
            TaskCreate(
                channel_id="ch_beta",
                task_type=TaskType.GENERAL,
                idempotency_key="fair_beta_0",
                priority=TaskPriority.NORMAL.value,
            )
        )

        # Claim 1: Claims alpha task 0, updates ch_alpha.last_task_dispatched_at
        c1 = self.task_repo.atomic_claim_next_task(worker_id="w1", lease_duration_sec=60)
        self.assertEqual(c1.channel_id, "ch_alpha")

        # Claim 2: Must claim beta task 0 (ch_beta has NULL last_task_dispatched_at)
        c2 = self.task_repo.atomic_claim_next_task(worker_id="w2", lease_duration_sec=60)
        self.assertEqual(c2.channel_id, "ch_beta")


if __name__ == "__main__":
    unittest.main()
