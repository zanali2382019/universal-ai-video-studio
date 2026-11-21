"""Load testing, concurrency benchmarks, DAG pipeline resumption, and stress tests (Task 3.5 — Milestone 5)."""
import unittest
import tempfile
import gc
import os
import time
import threading
from pathlib import Path
from typing import List, Dict, Any

from src.database.connection import DatabaseManager
from src.database.migrations import MigrationManager
from src.database.repositories.channel_repository import ChannelRepository
from src.database.repositories.task_repository import TaskRepository
from src.database.repositories.file_operation_repository import FileOperationRepository
from src.database.repositories.asset_repository import AssetRepository
from src.database.repositories.audit_repository import AuditRepository
from src.services.task_service import TaskService
from src.services.audit_service import AuditService
from src.services.worker_pool import WorkerPool, WorkerThread
from src.services.task_reaper import TaskReaper
from src.services.checkpoint_service import CheckpointService
from src.services.recovery_service import RecoveryService
from src.database.models import ChannelCreate, TaskCreate
from src.core.constants import TaskStatus, TaskType, TaskPriority
from src.core.exceptions import StaleExecutionTokenException
from src.core.config import get_settings


class TestLoadAndPipelineResumption(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root_path = Path(self.temp_dir.name)
        self.db_path = self.root_path / "load_test.db"
        self.channels_dir = self.root_path / "channels"
        self.channels_dir.mkdir(parents=True, exist_ok=True)

        self.settings = get_settings()
        self._orig_channels_root = self.settings.channels_root
        self.settings.channels_root = self.channels_dir

        self.db_manager = DatabaseManager(db_path=self.db_path, busy_timeout_ms=15000)
        self.migration_manager = MigrationManager(self.db_manager)
        self.migration_manager.run_migrations()

        self.channel_repo = ChannelRepository(self.db_manager)
        self.task_repo = TaskRepository(self.db_manager)
        self.task_service = TaskService(task_repository=self.task_repo, channel_repository=self.channel_repo)
        self.file_op_repo = FileOperationRepository(self.db_manager)
        self.asset_repo = AssetRepository(self.db_manager)
        self.audit_repo = AuditRepository(self.db_manager)
        self.audit_service = AuditService(self.audit_repo)

        self.checkpoint_service = CheckpointService(
            task_repository=self.task_repo,
            file_operation_repository=self.file_op_repo,
            asset_repository=self.asset_repo,
            audit_service=self.audit_service,
            db_manager=self.db_manager,
        )

        self.recovery_service = RecoveryService(
            file_operation_repository=self.file_op_repo,
            asset_repository=self.asset_repo,
            task_repository=self.task_repo,
            audit_repository=self.audit_repo,
            audit_service=self.audit_service,
            db_manager=self.db_manager,
        )

        # Pre-create test channels
        for i in range(8):
            ch_id = f"ch_load_{i}"
            self.channel_repo.create(
                ChannelCreate(id=ch_id, name=f"Channel {i}", handle=f"@{ch_id}", niche="load_test")
            )
            ch_vault = self.channels_dir / ch_id / "media_vault"
            (ch_vault / "originals" / "images").mkdir(parents=True, exist_ok=True)
            (ch_vault / "cache").mkdir(parents=True, exist_ok=True)
            (ch_vault / "quarantine").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.settings.channels_root = self._orig_channels_root
        gc.collect()
        self.temp_dir.cleanup()

    def test_concurrent_workload_burst_throughput(self):
        """Scenario 1: 40 tasks across 8 channels under 4 concurrent workers (measuring throughput & zero failure)."""
        task_ids = []
        for i in range(40):
            ch = f"ch_load_{i % 8}"
            task = self.task_service.create_task(
                channel_id=ch,
                task_input={
                    "task_type": TaskType.GENERAL,
                    "idempotency_key": f"burst_task_{i}",
                    "priority": 10 + (i % 5),
                },
            )
            task_ids.append(task.id)

        start_time = time.time()

        pool = WorkerPool(
            task_repository=self.task_repo,
            task_service=self.task_service,
            concurrency=4,  # Maximum CPU ceiling
            poll_interval_sec=0.01,
        )
        pool.start()

        # Wait for all 40 tasks to complete
        timeout = 10.0
        deadline = time.time() + timeout
        completed = False
        while time.time() < deadline:
            incomplete = self.task_repo.list_tasks(status=TaskStatus.QUEUED) + self.task_repo.list_tasks(status=TaskStatus.RUNNING)
            if not incomplete:
                completed = True
                break
            time.sleep(0.05)

        elapsed = time.time() - start_time
        pool.shutdown(timeout_sec=2.0)

        self.assertTrue(completed, f"Not all tasks completed within {timeout}s")
        throughput = len(task_ids) / elapsed
        self.assertGreater(throughput, 5.0, f"Throughput {throughput:.2f} tasks/sec was too low")

        # Verify all 40 tasks are COMPLETED
        for tid in task_ids:
            t = self.task_repo.get_by_id(tid)
            self.assertEqual(t.status, TaskStatus.COMPLETED)

    def test_per_channel_strict_serialization_under_heavy_load(self):
        """Scenario 2: 12 tasks on a SINGLE channel under 4 workers: max concurrency must be exactly 1."""
        channel_id = "ch_load_0"
        task_ids = []
        for i in range(12):
            task = self.task_service.create_task(
                channel_id=channel_id,
                task_input={
                    "task_type": TaskType.GENERAL,
                    "idempotency_key": f"serial_task_{i}",
                    "priority": 10,
                },
            )
            task_ids.append(task.id)

        active_count = 0
        max_seen = 0
        lock = threading.Lock()

        def serial_check_handler(t, hb):
            nonlocal active_count, max_seen
            with lock:
                active_count += 1
                if active_count > max_seen:
                    max_seen = active_count
            time.sleep(0.05)
            with lock:
                active_count -= 1
            return {"status": "ok"}

        pool = WorkerPool(
            task_repository=self.task_repo,
            task_service=self.task_service,
            concurrency=4,  # 4 workers competing for 1 channel
            task_handler=serial_check_handler,
            poll_interval_sec=0.01,
        )
        pool.start()

        deadline = time.time() + 8.0
        while time.time() < deadline:
            remaining = [tid for tid in task_ids if self.task_repo.get_by_id(tid).status != TaskStatus.COMPLETED]
            if not remaining:
                break
            time.sleep(0.05)

        pool.shutdown(timeout_sec=2.0)

        # STRICT ASSERTION: max concurrent tasks for channel_id must NEVER exceed 1
        self.assertEqual(max_seen, 1, f"Channel concurrency violation: observed {max_seen} concurrent tasks")
        for tid in task_ids:
            self.assertEqual(self.task_repo.get_by_id(tid).status, TaskStatus.COMPLETED)

    def test_end_to_end_dag_pipeline_execution(self):
        """Scenario 3: Multi-stage pipeline (Script -> Audio -> Video) executing in topological order."""
        channel_id = "ch_load_1"

        # Stage 1: Script Generation
        t_script = self.task_service.create_task(
            channel_id=channel_id,
            task_input={"task_type": TaskType.SCRIPT_GENERATION, "idempotency_key": "dag_script_01"},
        )
        self.assertEqual(t_script.status, TaskStatus.QUEUED)

        # Stage 2: Audio Synthesis (depends on Stage 1)
        t_audio = self.task_service.create_task(
            channel_id=channel_id,
            task_input={
                "task_type": TaskType.VOICE_SYNTHESIS,
                "idempotency_key": "dag_audio_01",
                "dependencies": [t_script.id],
            },
        )
        self.assertEqual(t_audio.status, TaskStatus.PENDING)

        # Stage 3: Video Assembly (depends on Stage 2)
        t_video = self.task_service.create_task(
            channel_id=channel_id,
            task_input={
                "task_type": TaskType.VIDEO_RENDERING,
                "idempotency_key": "dag_video_01",
                "dependencies": [t_audio.id],
            },
        )
        self.assertEqual(t_video.status, TaskStatus.PENDING)

        execution_order = []
        lock = threading.Lock()

        def pipeline_handler(t, hb):
            with lock:
                execution_order.append(t.id)
            time.sleep(0.05)
            return {"stage": t.task_type.value}

        pool = WorkerPool(
            task_repository=self.task_repo,
            task_service=self.task_service,
            concurrency=2,
            task_handler=pipeline_handler,
            poll_interval_sec=0.02,
        )
        pool.start()

        deadline = time.time() + 6.0
        while time.time() < deadline:
            if (
                self.task_repo.get_by_id(t_script.id).status == TaskStatus.COMPLETED
                and self.task_repo.get_by_id(t_audio.id).status == TaskStatus.COMPLETED
                and self.task_repo.get_by_id(t_video.id).status == TaskStatus.COMPLETED
            ):
                break
            time.sleep(0.05)

        pool.shutdown(timeout_sec=2.0)

        # Verify topological execution sequence
        self.assertEqual(execution_order, [t_script.id, t_audio.id, t_video.id])
        self.assertEqual(self.task_repo.get_by_id(t_video.id).status, TaskStatus.COMPLETED)

    def test_controlled_worker_interruption_and_checkpoint_resumption(self):
        """Scenario 4: Interruption mid-execution with 3 steps: fast-forwards Step 1 and resumes cleanly from Step 2."""
        channel_id = "ch_load_2"
        task = self.task_service.create_task(
            channel_id=channel_id,
            task_input={"task_type": TaskType.IMAGE_GENERATION, "idempotency_key": "resumption_task_01", "total_steps": 3},
        )

        step_executions = {"step_1": 0, "step_2": 0, "step_3": 0}

        def step_1_work(p):
            step_executions["step_1"] += 1
            p.write_bytes(b"STEP_1_PROMPT_DATA")
            return {"step": 1}

        def step_2_work(p):
            step_executions["step_2"] += 1
            p.write_bytes(b"STEP_2_IMAGE_FRAME_DATA")
            return {"step": 2}

        def step_3_work(p):
            step_executions["step_3"] += 1
            p.write_bytes(b"STEP_3_UPSCALE_DATA")
            return {"step": 3}

        # Worker 1 claims task (token 1)
        w1_claim = self.task_repo.atomic_claim_next_task(worker_id="w_first", lease_duration_sec=1)
        self.assertEqual(w1_claim.execution_token, 1)

        # Worker 1 executes Step 1 successfully
        target_step1 = Path("media_vault/originals/images/res_step1.bin")
        self.checkpoint_service.execute_step(
            task=w1_claim,
            step_name="step_1",
            step_index=1,
            work_fn=step_1_work,
            target_rel_path=target_step1,
        )
        self.assertEqual(step_executions["step_1"], 1)

        # Worker 1 begins Step 2: writes temp file but CRASHES before commit
        ch_dir = self.channels_dir / channel_id
        temp_step2 = ch_dir / "media_vault" / "cache" / f"task_{task.id}_tok_1_crash_step_2.tmp"
        temp_step2.write_bytes(b"PARTIAL_STEP_2_DATA")

        # Worker 1 crashes. Time advances, lease expires
        time.sleep(1.1)

        # TaskReaper detects expired lease, reaps task and bumps token to 2
        reaper = TaskReaper(task_repository=self.task_repo, task_service=self.task_service, base_backoff_sec=0.01)
        reaped_ids = reaper.reap_expired_tasks()
        self.assertIn(task.id, reaped_ids)

        # Startup recovery reconciles uncompleted file operations
        self.recovery_service.reconcile_recovery(channel_id)

        # Allow minimal backoff delay to pass (0.01s base backoff)
        time.sleep(0.05)

        # Worker 2 claims the task (now with token 3, strictly fenced from Worker 1's token 1!)
        w2_claim = self.task_repo.atomic_claim_next_task(worker_id="w_second", lease_duration_sec=60)
        self.assertIsNotNone(w2_claim)
        self.assertEqual(w2_claim.execution_token, 3)

        # Worker 2 runs Step 1: MUST FAST-FORWARD without re-executing step_1_work!
        cp1_fast = self.checkpoint_service.execute_step(
            task=w2_claim,
            step_name="step_1",
            step_index=1,
            work_fn=step_1_work,
            target_rel_path=target_step1,
        )
        self.assertIsNotNone(cp1_fast)
        # Execution count for Step 1 must STILL BE 1 (fast-forwarded, $0 re-execution!)
        self.assertEqual(step_executions["step_1"], 1)

        # Worker 2 executes Step 2 and Step 3 cleanly
        target_step2 = Path("media_vault/originals/images/res_step2.bin")
        cp2 = self.checkpoint_service.execute_step(
            task=w2_claim,
            step_name="step_2",
            step_index=2,
            work_fn=step_2_work,
            target_rel_path=target_step2,
        )
        self.assertIsNotNone(cp2)

        target_step3 = Path("media_vault/originals/images/res_step3.bin")
        cp3 = self.checkpoint_service.execute_step(
            task=w2_claim,
            step_name="step_3",
            step_index=3,
            work_fn=step_3_work,
            target_rel_path=target_step3,
        )
        self.assertIsNotNone(cp3)

        # Complete task
        self.task_service.transition_status(
            task_id=task.id,
            new_status=TaskStatus.COMPLETED,
            execution_token=w2_claim.execution_token,
            result={"resumed": True, "checkpoints": 3},
        )

        final_task = self.task_repo.get_by_id(task.id)
        self.assertEqual(final_task.status, TaskStatus.COMPLETED)
        self.assertEqual(len(self.task_repo.get_checkpoints(task.id)), 3)

    def test_stale_worker_fencing_under_concurrent_race(self):
        """Scenario 5: Stale worker attempting late commits is completely rejected with StaleExecutionTokenException."""
        channel_id = "ch_load_3"
        task = self.task_repo.create_task(
            TaskCreate(channel_id=channel_id, task_type=TaskType.GENERAL, idempotency_key="race_fencing_01")
        )

        # Worker 1 claims task
        w1 = self.task_repo.atomic_claim_next_task(worker_id="w_slow", lease_duration_sec=1)
        self.assertEqual(w1.execution_token, 1)

        # Worker 1 hangs; lease is reaped and Worker 2 claims it -> token = 3
        self.task_repo.reap_stale_task(task.id, "w_slow", stale_token=1)
        w2 = self.task_repo.atomic_claim_next_task(worker_id="w_fast", lease_duration_sec=60)
        self.assertEqual(w2.execution_token, 3)

        # Worker 1 wakes up and attempts to save checkpoint with token 1
        with self.assertRaises(StaleExecutionTokenException):
            self.task_repo.save_checkpoint({
                "task_id": task.id,
                "step_name": "late_step",
                "step_index": 1,
                "step_data": {"late": True},
                "execution_token": 1,  # Stale token!
            })

        # Worker 1 attempts to complete task with token 1
        with self.assertRaises(StaleExecutionTokenException):
            self.task_service.transition_status(
                task_id=task.id,
                new_status=TaskStatus.COMPLETED,
                execution_token=1,  # Stale token!
            )

        # Worker 2 successfully completes task with token 3
        self.task_service.transition_status(
            task_id=task.id,
            new_status=TaskStatus.COMPLETED,
            execution_token=3,
            result={"worker": "w_fast"},
        )
        self.assertEqual(self.task_repo.get_by_id(task.id).status, TaskStatus.COMPLETED)

    def test_sqlite_wal_contention_and_busy_resilience(self):
        """Scenario 6: 10 concurrent threads hammering atomic transactions simultaneously; 0 unhandled locks."""
        # Pre-seed tasks
        for i in range(20):
            ch = f"ch_load_{i % 8}"
            self.task_repo.create_task(
                TaskCreate(channel_id=ch, task_type=TaskType.GENERAL, idempotency_key=f"wal_stress_{i}")
            )

        errors = []
        claimed_ids = []
        lock = threading.Lock()

        def hammer_worker(worker_idx: int):
            try:
                # Each thread uses its own connection via DatabaseManager
                mgr = DatabaseManager(db_path=self.db_path, busy_timeout_ms=15000)
                repo = TaskRepository(db_manager=mgr)
                for _ in range(5):
                    t = repo.atomic_claim_next_task(worker_id=f"hammer_{worker_idx}", lease_duration_sec=10)
                    if t:
                        with lock:
                            claimed_ids.append(t.id)
                        time.sleep(0.01)
                        repo.update_task_status(
                            task_id=t.id,
                            new_status=TaskStatus.COMPLETED,
                            execution_token=t.execution_token,
                            result={"worker": worker_idx},
                        )
            except Exception as e:
                with lock:
                    errors.append(e)

        threads = [threading.Thread(target=hammer_worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Zero unhandled lock exceptions under heavy concurrent contention
        self.assertEqual(len(errors), 0, f"Encountered concurrency errors: {errors}")
        self.assertGreater(len(claimed_ids), 0)

    def test_graceful_shutdown_under_active_load(self):
        """Scenario 7: Shutdown signal issued mid-flight allows in-flight tasks to finish without claiming new ones."""
        # Enqueue 10 tasks
        task_ids = []
        for i in range(10):
            t = self.task_service.create_task(
                channel_id=f"ch_load_{i % 4}",
                task_input={"task_type": TaskType.GENERAL, "idempotency_key": f"shut_load_{i}"},
            )
            task_ids.append(t.id)

        in_flight_seen = threading.Event()

        def slow_handler(t, hb):
            in_flight_seen.set()
            time.sleep(0.3)
            return {"done": True}

        pool = WorkerPool(
            task_repository=self.task_repo,
            task_service=self.task_service,
            concurrency=2,
            task_handler=slow_handler,
            poll_interval_sec=0.01,
        )
        pool.start()

        # Wait until at least one task is in-flight
        in_flight_seen.wait(timeout=2.0)

        # Trigger graceful shutdown immediately
        pool.shutdown(timeout_sec=3.0)
        self.assertFalse(pool.is_running)

        # Verify: completed tasks transitioned cleanly, unclaimed tasks remain QUEUED safely
        statuses = [self.task_repo.get_by_id(tid).status for tid in task_ids]
        self.assertIn(TaskStatus.COMPLETED, statuses)
        self.assertIn(TaskStatus.QUEUED, statuses)
        # None of the tasks should be left in an invalid state
        for s in statuses:
            self.assertIn(s, (TaskStatus.COMPLETED, TaskStatus.QUEUED))

    def test_multiprocessing_pool_load_execution(self):
        """Scenario 8: Multi-process WorkerPool (backend='process') completing concurrent tasks across channels."""
        task_ids = []
        for i in range(6):
            ch = f"ch_load_{i % 4}"
            t = self.task_service.create_task(
                channel_id=ch,
                task_input={"task_type": TaskType.GENERAL, "idempotency_key": f"mp_load_{i}"},
            )
            task_ids.append(t.id)

        pool = WorkerPool(
            task_repository=self.task_repo,
            task_service=self.task_service,
            concurrency=2,
            backend="process",
            poll_interval_sec=0.02,
        )
        pool.start()

        deadline = time.time() + 6.0
        while time.time() < deadline:
            remaining = [tid for tid in task_ids if self.task_repo.get_by_id(tid).status != TaskStatus.COMPLETED]
            if not remaining:
                break
            time.sleep(0.05)

        pool.shutdown(timeout_sec=3.0)

        for tid in task_ids:
            task = self.task_repo.get_by_id(tid)
            self.assertEqual(task.status, TaskStatus.COMPLETED)
            self.assertIsNotNone(task.result)
            self.assertIn("worker_pid", task.result)


if __name__ == "__main__":
    unittest.main()
