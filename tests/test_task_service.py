"""Unit and integration tests for TaskService, Dependency Management, and State Machine (Task 3.5 — Milestone 2)."""
import unittest
import tempfile
import gc
from pathlib import Path

from src.database.connection import DatabaseManager
from src.database.migrations import MigrationManager
from src.database.repositories.channel_repository import ChannelRepository
from src.database.repositories.project_repository import ProjectRepository
from src.database.repositories.scene_repository import SceneRepository
from src.database.repositories.task_repository import TaskRepository
from src.services.task_service import TaskService
from src.database.models import ChannelCreate, ProjectCreate, SceneCreate, TaskCreate
from src.core.constants import TaskStatus, TaskType, TaskPriority, SceneType
from src.core.exceptions import (
    TaskNotFoundException,
    ChannelNotFoundException,
    ProjectNotFoundException,
    SceneNotFoundException,
    ValidationException,
    InvalidStateTransitionException,
    CyclicDependencyException,
)


class TestTaskService(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root_path = Path(self.temp_dir.name)
        self.db_path = self.root_path / "test_task_service.db"

        self.db_manager = DatabaseManager(db_path=self.db_path, busy_timeout_ms=15000)
        self.migration_manager = MigrationManager(self.db_manager)
        self.migration_manager.run_migrations()

        self.channel_repo = ChannelRepository(db_manager=self.db_manager)
        self.project_repo = ProjectRepository(db_manager=self.db_manager)
        self.scene_repo = SceneRepository(db_manager=self.db_manager)
        self.task_repo = TaskRepository(db_manager=self.db_manager)

        self.task_service = TaskService(
            task_repository=self.task_repo,
            channel_repository=self.channel_repo,
            project_repository=self.project_repo,
            scene_repository=self.scene_repo,
        )

        # Seed two test channels
        self.channel_repo.create(
            ChannelCreate(id="ch_alpha", name="Channel Alpha", handle="@alpha", niche="science")
        )
        self.channel_repo.create(
            ChannelCreate(id="ch_beta", name="Channel Beta", handle="@beta", niche="gaming")
        )

        # Seed a project and scene under Channel Alpha
        self.project_repo.create(
            ProjectCreate(id="proj_alpha_01", channel_id="ch_alpha", title="Episode 1")
        )
        self.scene_repo.create(
            SceneCreate(
                id="sc_alpha_01",
                channel_id="ch_alpha",
                project_id="proj_alpha_01",
                sequence_number=1,
                title="Scene 1",
                scene_type=SceneType.STANDARD,
            )
        )

    def tearDown(self):
        gc.collect()
        self.temp_dir.cleanup()

    def test_create_task_via_task_service(self):
        """Scenario 1: Create a valid task through TaskService with full validation."""
        task = self.task_service.create_task(
            channel_id="ch_alpha",
            task_input={
                "task_type": TaskType.SCRIPT_GENERATION,
                "idempotency_key": "key_script_01",
                "project_id": "proj_alpha_01",
                "scene_id": "sc_alpha_01",
                "payload": {"prompt": "Write a script about quantum physics"},
                "priority": TaskPriority.HIGH.value,
            },
        )
        self.assertIsNotNone(task.id)
        self.assertEqual(task.channel_id, "ch_alpha")
        self.assertEqual(task.status, TaskStatus.QUEUED)
        self.assertEqual(task.project_id, "proj_alpha_01")
        self.assertEqual(task.scene_id, "sc_alpha_01")

        # Verify task is retrievable via service
        fetched = self.task_service.get_task(task.id)
        self.assertEqual(fetched.id, task.id)

    def test_prevent_duplicate_task_creation(self):
        """Scenario 2: Idempotent task creation returns existing task without duplicating."""
        task_data = {
            "task_type": TaskType.VOICE_SYNTHESIS,
            "idempotency_key": "idemp_voice_dup",
            "payload": {"voice": "en_us_male"},
        }
        t1 = self.task_service.create_task(channel_id="ch_alpha", task_input=task_data)
        t2 = self.task_service.create_task(channel_id="ch_alpha", task_input=task_data)

        self.assertEqual(t1.id, t2.id)
        # Total tasks for channel alpha should be exactly 1
        all_alpha = self.task_service.list_tasks(channel_id="ch_alpha")
        self.assertEqual(len(all_alpha), 1)

    def test_task_payload_validation(self):
        """Scenario 3: Validation of input data, missing channels, and empty inputs."""
        # Missing channel
        with self.assertRaises(ValidationException):
            self.task_service.create_task(
                channel_id="   ",
                task_input={"task_type": TaskType.GENERAL, "idempotency_key": "k1"},
            )

        # Non-existent channel
        with self.assertRaises(ChannelNotFoundException):
            self.task_service.create_task(
                channel_id="ch_ghost",
                task_input={"task_type": TaskType.GENERAL, "idempotency_key": "k2"},
            )

        # Invalid payload (missing idempotency_key)
        with self.assertRaises(ValidationException):
            self.task_service.create_task(
                channel_id="ch_alpha",
                task_input={"task_type": TaskType.GENERAL},
            )

    def test_channel_scoping_and_cross_channel_rejection(self):
        """Scenario 4: Project/Scene scope and cross-channel dependencies strictly enforced."""
        # 1. Project belonging to ch_beta cannot be used on ch_alpha
        self.project_repo.create(ProjectCreate(id="proj_beta_01", channel_id="ch_beta", title="Beta Proj"))
        with self.assertRaises(ProjectNotFoundException):
            self.task_service.create_task(
                channel_id="ch_alpha",
                task_input={
                    "task_type": TaskType.GENERAL,
                    "idempotency_key": "k_cross_proj",
                    "project_id": "proj_beta_01",
                },
            )

        # 2. Scene without project_id is rejected
        with self.assertRaises(ValidationException):
            self.task_service.create_task(
                channel_id="ch_alpha",
                task_input={
                    "task_type": TaskType.GENERAL,
                    "idempotency_key": "k_scene_no_proj",
                    "scene_id": "sc_alpha_01",
                },
            )

        # 3. Cross-channel dependency rejected (task on ch_alpha depending on task on ch_beta)
        beta_task = self.task_service.create_task(
            channel_id="ch_beta",
            task_input={"task_type": TaskType.GENERAL, "idempotency_key": "beta_task_01"},
        )
        with self.assertRaises(ValidationException) as ctx:
            self.task_service.create_task(
                channel_id="ch_alpha",
                task_input={"task_type": TaskType.GENERAL, "idempotency_key": "alpha_cross_dep"},
                dependencies=[beta_task.id],
            )
        self.assertIn("Cross-channel dependency prohibited", str(ctx.exception))

    def test_dependent_task_creation_and_initial_pending_state(self):
        """Scenario 5: Creating task B depending on task A puts B in PENDING state."""
        task_a = self.task_service.create_task(
            channel_id="ch_alpha",
            task_input={"task_type": TaskType.SCRIPT_GENERATION, "idempotency_key": "dep_task_a"},
        )
        self.assertEqual(task_a.status, TaskStatus.QUEUED)

        task_b = self.task_service.create_task(
            channel_id="ch_alpha",
            task_input={"task_type": TaskType.VOICE_SYNTHESIS, "idempotency_key": "dep_task_b"},
            dependencies=[task_a.id],
        )
        self.assertEqual(task_b.status, TaskStatus.PENDING)
        self.assertEqual(task_b.dependencies, [task_a.id])

    def test_prevent_execution_before_prerequisites(self):
        """Scenario 6: Task in PENDING cannot be transitioned to QUEUED or RUNNING while prerequisites are unmet."""
        task_a = self.task_service.create_task(
            channel_id="ch_alpha",
            task_input={"task_type": TaskType.SCRIPT_GENERATION, "idempotency_key": "unmet_a"},
        )
        task_b = self.task_service.create_task(
            channel_id="ch_alpha",
            task_input={"task_type": TaskType.VOICE_SYNTHESIS, "idempotency_key": "unmet_b"},
            dependencies=[task_a.id],
        )
        self.assertEqual(task_b.status, TaskStatus.PENDING)

        # Attempt to transition B directly to QUEUED while A is still QUEUED -> rejected!
        with self.assertRaises(ValidationException) as ctx:
            self.task_service.transition_status(task_id=task_b.id, new_status=TaskStatus.QUEUED)
        self.assertIn("must be 'completed'", str(ctx.exception))

        # Attempt to transition B directly to RUNNING -> rejected by state machine!
        with self.assertRaises(InvalidStateTransitionException):
            self.task_service.transition_status(task_id=task_b.id, new_status=TaskStatus.RUNNING)

    def test_cycle_detection(self):
        """Scenario 7: DAG cycle detection rejects self-loops and circular dependencies."""
        # 1. Self-loop
        with self.assertRaises(CyclicDependencyException):
            self.task_service.create_task(
                channel_id="ch_alpha",
                task_input={
                    "id": "tsk_self_loop",
                    "task_type": TaskType.GENERAL,
                    "idempotency_key": "self_loop_key",
                },
                dependencies=["tsk_self_loop"],
            )

        # 2. Cycle A -> B -> C -> A
        t_a = self.task_service.create_task(
            channel_id="ch_alpha",
            task_input={"id": "tsk_dag_a", "task_type": TaskType.GENERAL, "idempotency_key": "k_dag_a"},
        )
        t_b = self.task_service.create_task(
            channel_id="ch_alpha",
            task_input={"id": "tsk_dag_b", "task_type": TaskType.GENERAL, "idempotency_key": "k_dag_b"},
            dependencies=[t_a.id],
        )
        t_c = self.task_service.create_task(
            channel_id="ch_alpha",
            task_input={"id": "tsk_dag_c", "task_type": TaskType.GENERAL, "idempotency_key": "k_dag_c"},
            dependencies=[t_b.id],
        )

        # Now attempt to create task D that introduces a cycle or validate cycle
        # E.g. task A cannot depend on C
        with self.assertRaises(CyclicDependencyException) as ctx:
            # Validate DAG if we were to link A -> C
            self.task_service._validate_dag("ch_alpha", "tsk_dag_a", ["tsk_dag_c"])
        self.assertEqual(ctx.exception.error_code, "CYCLIC_DEPENDENCY")

    def test_reject_non_existent_dependency(self):
        """Scenario 8: Referencing a non-existent task ID in dependencies raises ValidationException."""
        with self.assertRaises(ValidationException) as ctx:
            self.task_service.create_task(
                channel_id="ch_alpha",
                task_input={"task_type": TaskType.GENERAL, "idempotency_key": "k_ghost_dep"},
                dependencies=["tsk_does_not_exist_999"],
            )
        self.assertIn("was not found", str(ctx.exception))

    def test_upstream_task_completion_unblocks_downstream(self):
        """Scenario 9: Upstream completion automatically promotes PENDING task to QUEUED."""
        task_1 = self.task_service.create_task(
            channel_id="ch_alpha",
            task_input={"task_type": TaskType.SCRIPT_GENERATION, "idempotency_key": "flow_t1"},
        )
        task_2 = self.task_service.create_task(
            channel_id="ch_alpha",
            task_input={"task_type": TaskType.VOICE_SYNTHESIS, "idempotency_key": "flow_t2"},
            dependencies=[task_1.id],
        )
        self.assertEqual(task_1.status, TaskStatus.QUEUED)
        self.assertEqual(task_2.status, TaskStatus.PENDING)

        # Worker claims task_1 -> RUNNING (token=1)
        claim = self.task_repo.atomic_claim_next_task(worker_id="w_ep01")
        self.assertEqual(claim.id, task_1.id)
        self.assertEqual(claim.status, TaskStatus.RUNNING)

        # Complete task_1
        self.task_service.transition_status(
            task_id=task_1.id,
            new_status=TaskStatus.COMPLETED,
            execution_token=claim.execution_token,
            result={"text": "completed"},
        )

        # Verify task_2 automatically promoted from PENDING to QUEUED!
        task_2_after = self.task_service.get_task(task_2.id)
        self.assertEqual(task_2_after.status, TaskStatus.QUEUED)

    def test_upstream_task_failure_cascades_cancellation(self):
        """Scenario 10: Upstream failure cascades cancellation to all direct and transitive downstream tasks."""
        # A -> B -> C
        t_a = self.task_service.create_task(
            channel_id="ch_alpha",
            task_input={"task_type": TaskType.SCRIPT_GENERATION, "idempotency_key": "casc_a"},
        )
        t_b = self.task_service.create_task(
            channel_id="ch_alpha",
            task_input={"task_type": TaskType.VOICE_SYNTHESIS, "idempotency_key": "casc_b"},
            dependencies=[t_a.id],
        )
        t_c = self.task_service.create_task(
            channel_id="ch_alpha",
            task_input={"task_type": TaskType.IMAGE_GENERATION, "idempotency_key": "casc_c"},
            dependencies=[t_b.id],
        )

        self.assertEqual(t_a.status, TaskStatus.QUEUED)
        self.assertEqual(t_b.status, TaskStatus.PENDING)
        self.assertEqual(t_c.status, TaskStatus.PENDING)

        # Worker claims t_a
        c_a = self.task_repo.atomic_claim_next_task(worker_id="w_fail")
        self.assertEqual(c_a.id, t_a.id)

        # Fail t_a
        self.task_service.transition_status(
            task_id=t_a.id,
            new_status=TaskStatus.FAILED,
            execution_token=c_a.execution_token,
            error_message="Fatal script generation crash",
        )

        # Both B and C must be cancelled via cascade!
        t_b_after = self.task_service.get_task(t_b.id)
        t_c_after = self.task_service.get_task(t_c.id)

        self.assertEqual(t_b_after.status, TaskStatus.CANCELLED)
        self.assertIn("terminated with state failed", t_b_after.error_message)

        self.assertEqual(t_c_after.status, TaskStatus.CANCELLED)
        self.assertIn("terminated with state", t_c_after.error_message)

    def test_allowed_state_transitions(self):
        """Scenario 11: Valid state transitions across the task lifecycle."""
        task = self.task_service.create_task(
            channel_id="ch_alpha",
            task_input={"task_type": TaskType.GENERAL, "idempotency_key": "valid_trans_key"},
        )
        self.assertEqual(task.status, TaskStatus.QUEUED)

        # Claim: QUEUED -> RUNNING (token 1)
        claim = self.task_repo.atomic_claim_next_task(worker_id="w_trans")
        self.assertEqual(claim.status, TaskStatus.RUNNING)

        # RUNNING -> RETRY_PENDING (transient error)
        t_retry = self.task_service.transition_status(
            task_id=task.id,
            new_status=TaskStatus.RETRY_PENDING,
            execution_token=claim.execution_token,
            error_message="Network glitch",
        )
        self.assertEqual(t_retry.status, TaskStatus.RETRY_PENDING)

        # RETRY_PENDING -> QUEUED (backoff elapsed)
        t_queued = self.task_service.transition_status(
            task_id=task.id,
            new_status=TaskStatus.QUEUED,
            execution_token=t_retry.execution_token,
        )
        self.assertEqual(t_queued.status, TaskStatus.QUEUED)

        # QUEUED -> RUNNING (re-claimed, token 2)
        claim2 = self.task_repo.atomic_claim_next_task(worker_id="w_trans")
        self.assertEqual(claim2.status, TaskStatus.RUNNING)

        # RUNNING -> COMPLETED
        t_done = self.task_service.transition_status(
            task_id=task.id,
            new_status=TaskStatus.COMPLETED,
            execution_token=claim2.execution_token,
            result={"status": "ok"},
        )
        self.assertEqual(t_done.status, TaskStatus.COMPLETED)

    def test_disallowed_state_transitions(self):
        """Scenario 12: Illegal state transitions are strictly blocked with InvalidStateTransitionException."""
        # 1. PENDING -> RUNNING (illegal: must go through QUEUED)
        t_root = self.task_service.create_task(
            channel_id="ch_alpha",
            task_input={"task_type": TaskType.GENERAL, "idempotency_key": "illegal_root"},
        )
        t_dep = self.task_service.create_task(
            channel_id="ch_alpha",
            task_input={"task_type": TaskType.GENERAL, "idempotency_key": "illegal_dep"},
            dependencies=[t_root.id],
        )
        self.assertEqual(t_dep.status, TaskStatus.PENDING)

        with self.assertRaises(InvalidStateTransitionException) as ctx:
            self.task_service.transition_status(task_id=t_dep.id, new_status=TaskStatus.RUNNING)
        self.assertEqual(ctx.exception.error_code, "INVALID_STATE_TRANSITION")

        # 2. QUEUED -> COMPLETED (illegal: must be RUNNING first)
        with self.assertRaises(InvalidStateTransitionException):
            self.task_service.transition_status(task_id=t_root.id, new_status=TaskStatus.COMPLETED)

        # 3. COMPLETED -> RUNNING (illegal: terminal state cannot transition)
        c = self.task_repo.atomic_claim_next_task(worker_id="w_term")
        self.task_service.transition_status(
            task_id=t_root.id, new_status=TaskStatus.COMPLETED, execution_token=c.execution_token
        )
        with self.assertRaises(InvalidStateTransitionException):
            self.task_service.transition_status(task_id=t_root.id, new_status=TaskStatus.RUNNING)

        # 4. CANCELLED -> QUEUED (illegal: terminal state)
        t_cancel = self.task_service.create_task(
            channel_id="ch_alpha",
            task_input={"task_type": TaskType.GENERAL, "idempotency_key": "illegal_cancel"},
        )
        self.task_service.cancel_task(t_cancel.id)
        with self.assertRaises(InvalidStateTransitionException):
            self.task_service.transition_status(task_id=t_cancel.id, new_status=TaskStatus.QUEUED)

    def test_pause_and_resume_handling(self):
        """Scenario 13: Pausing and resuming tasks correctly preserves or evaluates dependencies."""
        # 1. Pause and resume an independent task
        t_simple = self.task_service.create_task(
            channel_id="ch_alpha",
            task_input={"task_type": TaskType.GENERAL, "idempotency_key": "pause_simple"},
        )
        self.assertEqual(t_simple.status, TaskStatus.QUEUED)

        t_paused = self.task_service.pause_task(t_simple.id)
        self.assertEqual(t_paused.status, TaskStatus.PAUSED)

        t_resumed = self.task_service.resume_task(t_simple.id)
        self.assertEqual(t_resumed.status, TaskStatus.QUEUED)

        # 2. Pause and resume a dependent task with unmet prerequisites -> resumes to PENDING
        t_parent = self.task_service.create_task(
            channel_id="ch_alpha",
            task_input={"task_type": TaskType.GENERAL, "idempotency_key": "pause_parent"},
        )
        t_child = self.task_service.create_task(
            channel_id="ch_alpha",
            task_input={"task_type": TaskType.GENERAL, "idempotency_key": "pause_child"},
            dependencies=[t_parent.id],
        )
        self.assertEqual(t_child.status, TaskStatus.PENDING)

        self.task_service.pause_task(t_child.id)
        self.assertEqual(self.task_service.get_task(t_child.id).status, TaskStatus.PAUSED)

        # Resumes to PENDING because parent is not COMPLETED
        self.task_service.resume_task(t_child.id)
        self.assertEqual(self.task_service.get_task(t_child.id).status, TaskStatus.PENDING)

    def test_retry_pending_approval_workflow(self):
        """Scenario 14: RETRY_PENDING_APPROVAL can be approved (QUEUED) or completed manually."""
        task = self.task_service.create_task(
            channel_id="ch_alpha",
            task_input={"task_type": TaskType.IMAGE_GENERATION, "idempotency_key": "approval_task"},
        )
        claim = self.task_repo.atomic_claim_next_task(worker_id="w_appr")

        # Ambiguous provider timeout -> RETRY_PENDING_APPROVAL
        t_appr = self.task_service.transition_status(
            task_id=task.id,
            new_status=TaskStatus.RETRY_PENDING_APPROVAL,
            execution_token=claim.execution_token,
            error_message="Ambiguous external timeout",
        )
        self.assertEqual(t_appr.status, TaskStatus.RETRY_PENDING_APPROVAL)

        # Operator approves retry -> QUEUED
        t_approved = self.task_service.transition_status(
            task_id=task.id,
            new_status=TaskStatus.QUEUED,
            execution_token=t_appr.execution_token,
        )
        self.assertEqual(t_approved.status, TaskStatus.QUEUED)


if __name__ == "__main__":
    unittest.main()

