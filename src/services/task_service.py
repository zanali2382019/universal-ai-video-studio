"""Task service managing task lifecycle, dependency DAGs, state transitions, and channel scoping."""
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Union, Set
from pydantic import ValidationError

from src.database.repositories.task_repository import TaskRepository
from src.database.repositories.channel_repository import ChannelRepository
from src.database.repositories.project_repository import ProjectRepository
from src.database.repositories.scene_repository import SceneRepository
from src.database.models import TaskModel, TaskCreate, TaskUpdate
from src.core.constants import TaskStatus, TaskType, TaskPriority
from src.core.exceptions import (
    TaskNotFoundException,
    ChannelNotFoundException,
    ProjectNotFoundException,
    SceneNotFoundException,
    ValidationException,
    InvalidStateTransitionException,
    CyclicDependencyException,
    StaleExecutionTokenException,
)
from src.core.logger import get_logger, log_context

logger = get_logger("studio.services.task")

# Strict Task Lifecycle State Machine Transition Matrix
ALLOWED_TRANSITIONS: Dict[TaskStatus, Set[TaskStatus]] = {
    TaskStatus.PENDING: {
        TaskStatus.QUEUED,      # All prerequisite dependencies completed
        TaskStatus.CANCELLED,   # Prerequisite failed/cancelled, or operator cancellation
        TaskStatus.PAUSED,      # Operator pauses pipeline before queueing
    },
    TaskStatus.QUEUED: {
        TaskStatus.RUNNING,     # Worker claimed task via atomic claim
        TaskStatus.CANCELLED,   # Operator cancels before dispatch
        TaskStatus.PAUSED,      # Operator pauses queued task
    },
    TaskStatus.RUNNING: {
        TaskStatus.COMPLETED,              # Execution succeeded
        TaskStatus.FAILED,                 # Fatal error or max retries exceeded
        TaskStatus.RETRY_PENDING,          # Transient failure, backoff delay scheduled
        TaskStatus.RETRY_PENDING_APPROVAL, # Ambiguous external outcome or manual review
        TaskStatus.CANCELLED,              # Operator cancelled running task
        TaskStatus.PAUSED,                 # Operator paused running task
    },
    TaskStatus.RETRY_PENDING: {
        TaskStatus.QUEUED,      # Scheduled retry time reached, returned to queue
        TaskStatus.RUNNING,     # Directly claimed upon schedule
        TaskStatus.CANCELLED,   # Operator cancels while waiting
        TaskStatus.PAUSED,      # Operator pauses retry
        TaskStatus.FAILED,      # TaskTTL expired while in retry state
    },
    TaskStatus.RETRY_PENDING_APPROVAL: {
        TaskStatus.QUEUED,      # Operator approves retry
        TaskStatus.COMPLETED,   # Operator verified external result manually
        TaskStatus.CANCELLED,   # Operator rejects/cancels task
        TaskStatus.FAILED,      # Operator marks as failed
    },
    TaskStatus.PAUSED: {
        TaskStatus.PENDING,     # Resumed but prerequisites still pending
        TaskStatus.QUEUED,      # Resumed and all prerequisites completed
        TaskStatus.CANCELLED,   # Operator cancels while paused
    },
    TaskStatus.COMPLETED: set(),  # Terminal state: no outgoing transitions
    TaskStatus.FAILED: set(),     # Terminal state: no outgoing transitions
    TaskStatus.CANCELLED: set(),  # Terminal state: no outgoing transitions
}


class TaskService:
    """Provides business operations for task lifecycle, dependency DAGs, and state machine management."""

    def __init__(
        self,
        task_repository: Optional[TaskRepository] = None,
        channel_repository: Optional[ChannelRepository] = None,
        project_repository: Optional[ProjectRepository] = None,
        scene_repository: Optional[SceneRepository] = None,
        service_logger: Optional[logging.Logger] = None,
    ) -> None:
        self._task_repo = task_repository or TaskRepository()
        self._channel_repo = channel_repository or ChannelRepository()
        self._project_repo = project_repository or ProjectRepository()
        self._scene_repo = scene_repository or SceneRepository()
        self._logger = service_logger or logger

    def create_task(
        self,
        channel_id: str,
        task_input: Union[TaskCreate, Dict[str, Any]],
        dependencies: Optional[List[str]] = None,
    ) -> TaskModel:
        """Create a new task with strict validation, dependency DAG checking, and idempotency handling.

        Args:
            channel_id: Owning channel ID.
            task_input: TaskCreate instance or payload dictionary.
            dependencies: Optional list of prerequisite task IDs overriding/extending input.

        Returns:
            TaskModel: The created or existing idempotent task model.

        Raises:
            ChannelNotFoundException: If channel does not exist.
            ProjectNotFoundException: If project_id provided does not exist in channel.
            SceneNotFoundException: If scene_id provided does not exist in project.
            ValidationException: If input is invalid or dependencies violate constraints.
            CyclicDependencyException: If dependency DAG contains a directed cycle.
        """
        clean_ch = (channel_id or "").strip()
        if not clean_ch:
            raise ValidationException("Channel ID cannot be empty.")

        # 1. Verify channel existence
        if not self._channel_repo.exists(clean_ch):
            raise ChannelNotFoundException(clean_ch)

        # 2. Parse & normalize input
        if isinstance(task_input, dict):
            payload = dict(task_input)
            payload["channel_id"] = clean_ch
            if dependencies is not None:
                payload["dependencies"] = dependencies
            try:
                task_data = TaskCreate(**payload)
            except ValidationError as err:
                errors = err.errors()
                err_details = {
                    ".".join(str(loc) for loc in e.get("loc", [])): e.get("msg")
                    for e in errors
                }
                raise ValidationException(
                    f"Invalid task payload: {err.error_count()} validation error(s).",
                    details=err_details,
                ) from err
        elif isinstance(task_input, TaskCreate):
            if task_input.channel_id != clean_ch:
                raise ValidationException(
                    f"Task channel_id '{task_input.channel_id}' does not match context channel '{clean_ch}'."
                )
            if dependencies is not None:
                task_data = task_input.model_copy(update={"dependencies": dependencies})
            else:
                task_data = task_input
        else:
            raise ValidationException("Unsupported task input type.")

        # 3. Verify Project and Scene scoping if provided
        if task_data.project_id:
            clean_proj = task_data.project_id.strip()
            if not self._project_repo.exists(clean_ch, clean_proj):
                raise ProjectNotFoundException(clean_proj)

            if task_data.scene_id:
                clean_sc = task_data.scene_id.strip()
                scene_record = self._scene_repo.get_by_id(clean_ch, clean_sc)
                if not scene_record:
                    raise SceneNotFoundException(clean_sc)
                if scene_record.project_id != clean_proj:
                    raise ValidationException(
                        f"Scene '{clean_sc}' belongs to project '{scene_record.project_id}', not '{clean_proj}'."
                    )
        elif task_data.scene_id:
            raise ValidationException("scene_id requires project_id to be specified.")

        # 4. Service-Level Idempotency Check: return existing task if match found
        existing_task = self._task_repo.get_by_idempotency_key(clean_ch, task_data.idempotency_key)
        if existing_task:
            self._logger.info(
                f"Idempotent task match for key '{task_data.idempotency_key}' on channel '{clean_ch}'. "
                f"Reusing task '{existing_task.id}' (status={existing_task.status.value})."
            )
            return existing_task

        candidate_id = task_data.id or f"tsk_{uuid.uuid4().hex[:12]}"
        task_deps = list(dict.fromkeys(task_data.dependencies or []))  # deduplicate preserving order

        # 5. Dependency Validation
        initial_status = TaskStatus.QUEUED
        if task_deps:
            # Immediate self-loop check
            if candidate_id in task_deps:
                raise CyclicDependencyException(candidate_id, [candidate_id, candidate_id])

            all_deps_completed = True
            for dep_id in task_deps:
                dep_task = self._task_repo.get_by_id(dep_id)
                if not dep_task:
                    raise ValidationException(
                        f"Prerequisite task '{dep_id}' was not found.",
                        details={"missing_prerequisite": dep_id},
                    )

                # Strict Channel-Scoping: dependencies must belong to the exact same channel (ADR-005)
                if dep_task.channel_id != clean_ch:
                    raise ValidationException(
                        f"Cross-channel dependency prohibited: prerequisite task '{dep_id}' "
                        f"belongs to channel '{dep_task.channel_id}', not '{clean_ch}'.",
                        details={"task_id": candidate_id, "prerequisite_id": dep_id},
                    )

                # Prerequisite Deadlock / Failure check
                if dep_task.status in (TaskStatus.FAILED, TaskStatus.CANCELLED):
                    raise ValidationException(
                        f"Cannot create task depending on prerequisite '{dep_id}' because it has "
                        f"already terminated with status '{dep_task.status.value}'.",
                        details={"prerequisite_id": dep_id, "prerequisite_status": dep_task.status.value},
                    )

                if dep_task.status != TaskStatus.COMPLETED:
                    all_deps_completed = False

            # Cycle Detection / DAG Validation
            self._validate_dag(clean_ch, candidate_id, task_deps)

            # Determine initial state: PENDING if unmet dependencies exist, QUEUED if all are COMPLETED
            initial_status = TaskStatus.QUEUED if all_deps_completed else TaskStatus.PENDING

        # 6. Finalize Task Payload and Persist via Repository
        final_payload = task_data.model_copy(
            update={
                "id": candidate_id,
                "status": initial_status,
                "dependencies": task_deps,
            }
        )

        def pre_insert_dag_guard(conn):
            if task_deps:
                self._validate_dag(clean_ch, candidate_id, task_deps, conn=conn)

        created_task = self._task_repo.create_task(final_payload, pre_insert_callback=pre_insert_dag_guard)
        self._logger.info(
            f"Created task '{created_task.id}' [type={created_task.task_type.value}, "
            f"status={created_task.status.value}, deps={len(task_deps)}] on channel '{clean_ch}'."
        )
        return created_task

    def get_task(self, task_id: str) -> TaskModel:
        """Retrieve task by ID or raise TaskNotFoundException."""
        clean_id = (task_id or "").strip()
        task = self._task_repo.get_by_id(clean_id)
        if not task:
            raise TaskNotFoundException(clean_id)
        return task

    def list_tasks(
        self,
        channel_id: Optional[str] = None,
        status: Optional[TaskStatus] = None,
        project_id: Optional[str] = None,
    ) -> List[TaskModel]:
        """List tasks with optional filtering."""
        return self._task_repo.list_tasks(channel_id=channel_id, status=status, project_id=project_id)

    def transition_status(
        self,
        task_id: str,
        new_status: TaskStatus,
        execution_token: Optional[int] = None,
        worker_id: Optional[str] = None,
        result: Optional[Dict[str, Any]] = None,
        error_message: Optional[str] = None,
        error_details: Optional[Dict[str, Any]] = None,
        current_step: Optional[str] = None,
        completed_steps: Optional[int] = None,
        scheduled_at: Optional[str] = None,
    ) -> TaskModel:
        """Transition task status enforcing the strict lifecycle state machine and cascading effects.

        Args:
            task_id: Target task ID.
            new_status: Target TaskStatus.
            execution_token: Optional worker fencing token. If omitted for non-running tasks, current token is used.
            worker_id: Worker identifier.
            result: Result dictionary for completed tasks.
            error_message: Error description for failed/cancelled tasks.
            error_details: Structured error details.
            current_step: Execution step name.
            completed_steps: Number of completed steps.
            scheduled_at: Scheduled execution time for retries.

        Returns:
            TaskModel: Updated task model.

        Raises:
            TaskNotFoundException: If task does not exist.
            InvalidStateTransitionException: If transition is not allowed by state machine.
            ValidationException: If prerequisite conditions for target state are not satisfied.
            StaleExecutionTokenException: If execution_token does not match current task token.
        """
        task = self.get_task(task_id)
        current_status = task.status

        # 1. State Machine Transition Verification
        allowed = ALLOWED_TRANSITIONS.get(current_status, set())
        if new_status not in allowed:
            raise InvalidStateTransitionException(
                task_id=task.id,
                current_status=current_status.value,
                target_status=new_status.value,
                details={"allowed_transitions": [s.value for s in allowed]},
            )

        # 2. Target-Specific Validations
        if new_status == TaskStatus.QUEUED:
            # When transitioning to QUEUED (e.g. from PENDING), verify all dependencies are COMPLETED
            for dep_id in task.dependencies:
                dep_task = self._task_repo.get_by_id(dep_id)
                if not dep_task or dep_task.status != TaskStatus.COMPLETED:
                    dep_status_val = dep_task.status.value if dep_task else "NOT_FOUND"
                    raise ValidationException(
                        f"Cannot transition task '{task.id}' to QUEUED: "
                        f"prerequisite task '{dep_id}' is in status '{dep_status_val}' (must be 'completed').",
                        details={"unmet_prerequisite": dep_id, "prerequisite_status": dep_status_val},
                    )

        # 3. Resolve Execution Token
        # Workers performing operational transitions on RUNNING tasks must provide explicit execution_token
        if current_status == TaskStatus.RUNNING and new_status in (
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.RETRY_PENDING,
            TaskStatus.RETRY_PENDING_APPROVAL,
        ):
            if execution_token is None:
                raise ValidationException(
                    f"Operational transition of RUNNING task '{task.id}' to '{new_status.value}' "
                    f"requires an explicit execution_token to prevent fencing token bypass."
                )
        token_to_use = execution_token if execution_token is not None else task.execution_token

        # 4. Atomic Database Update via Repository
        updated_task = self._task_repo.update_task_status(
            task_id=task.id,
            new_status=new_status,
            execution_token=token_to_use,
            worker_id=worker_id,
            result=result,
            error_message=error_message,
            error_details=error_details,
            current_step=current_step,
            completed_steps=completed_steps,
            scheduled_at=scheduled_at,
        )

        self._logger.info(
            f"Task '{task.id}' transitioned from '{current_status.value}' to '{new_status.value}' "
            f"(token={token_to_use})."
        )

        # 5. Cascading Lifecycle Effects
        if new_status == TaskStatus.COMPLETED:
            self._on_task_completed(updated_task)
        elif new_status in (TaskStatus.FAILED, TaskStatus.CANCELLED):
            self._on_task_failed_or_cancelled(updated_task)

        return updated_task

    def cancel_task(
        self,
        task_id: str,
        reason: str = "Cancelled by user",
        execution_token: Optional[int] = None,
    ) -> TaskModel:
        """Cancel a task and cascade cancellation to all dependent tasks.

        Idempotent if task is already CANCELLED.
        """
        task = self.get_task(task_id)
        if task.status == TaskStatus.CANCELLED:
            self._logger.info(f"Task '{task_id}' is already CANCELLED. No-op.")
            return task

        if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
            raise InvalidStateTransitionException(
                task_id=task.id,
                current_status=task.status.value,
                target_status=TaskStatus.CANCELLED.value,
                details={"reason": "Cannot cancel a task that has already terminated."},
            )

        latest_task = self.get_task(task_id)
        token_to_use = execution_token if execution_token is not None else latest_task.execution_token

        try:
            return self.transition_status(
                task_id=task_id,
                new_status=TaskStatus.CANCELLED,
                execution_token=token_to_use,
                error_message=reason,
            )
        except StaleExecutionTokenException:
            if execution_token is None:
                # Concurrent worker claim bumped the token; retry cancel with fresh token
                fresh_task = self.get_task(task_id)
                return self.transition_status(
                    task_id=task_id,
                    new_status=TaskStatus.CANCELLED,
                    execution_token=fresh_task.execution_token,
                    error_message=reason,
                )
            raise

    def pause_task(
        self,
        task_id: str,
        reason: str = "Paused by user",
        execution_token: Optional[int] = None,
    ) -> TaskModel:
        """Pause a task currently in PENDING, QUEUED, RUNNING, or RETRY_PENDING."""
        task = self.get_task(task_id)
        if task.status == TaskStatus.PAUSED:
            return task

        return self.transition_status(
            task_id=task_id,
            new_status=TaskStatus.PAUSED,
            execution_token=execution_token,
            error_message=reason,
        )

    def resume_task(self, task_id: str) -> TaskModel:
        """Resume a PAUSED task back to QUEUED (if all dependencies met) or PENDING."""
        task = self.get_task(task_id)
        if task.status != TaskStatus.PAUSED:
            raise InvalidStateTransitionException(
                task_id=task.id,
                current_status=task.status.value,
                target_status="resumed",
                details={"reason": "Only PAUSED tasks can be resumed."},
            )

        # Check prerequisite status
        all_deps_met = True
        for dep_id in task.dependencies:
            dep_task = self._task_repo.get_by_id(dep_id)
            if not dep_task or dep_task.status != TaskStatus.COMPLETED:
                all_deps_met = False
                break

        target_status = TaskStatus.QUEUED if all_deps_met else TaskStatus.PENDING
        return self.transition_status(
            task_id=task_id,
            new_status=target_status,
            execution_token=task.execution_token,
        )

    def evaluate_prerequisites(self, task_id: str) -> TaskModel:
        """Evaluate dependencies for a PENDING task and promote to QUEUED if satisfied."""
        task = self.get_task(task_id)
        if task.status != TaskStatus.PENDING:
            return task

        all_completed = True
        for dep_id in task.dependencies:
            dep = self._task_repo.get_by_id(dep_id)
            if not dep or dep.status != TaskStatus.COMPLETED:
                all_completed = False
                break

        if all_completed:
            return self.transition_status(
                task_id=task.id,
                new_status=TaskStatus.QUEUED,
                execution_token=task.execution_token,
            )
        return task

    # -------------------------------------------------------------------------
    # Internal Dependency Resolution & Cascading Routines
    # -------------------------------------------------------------------------

    def _validate_dag(
        self,
        channel_id: str,
        new_task_id: str,
        dependencies: List[str],
        conn: Optional[Any] = None,
    ) -> None:
        """Validate that adding new_task_id with dependencies does not introduce a directed cycle.

        Uses Depth-First Search with 3-color graph traversal. If conn is provided,
        reads existing tasks within the active BEGIN IMMEDIATE transaction to prevent race conditions.
        """
        if not dependencies:
            return

        if new_task_id in dependencies:
            raise CyclicDependencyException(new_task_id, [new_task_id, new_task_id])

        # Load existing tasks for the channel (inside transaction if conn provided)
        if conn is not None:
            cursor = conn.cursor()
            rows = cursor.execute("SELECT * FROM tasks WHERE channel_id = ?;", (channel_id.strip(),)).fetchall()
            existing_tasks = [TaskModel.from_row(r) for r in rows]
        else:
            existing_tasks = self._task_repo.list_tasks(channel_id=channel_id)

        # Build adjacency graph: node -> list of dependencies
        adj: Dict[str, List[str]] = {}
        for t in existing_tasks:
            adj[t.id] = list(t.dependencies or [])

        # Add candidate node with proposed dependencies
        adj[new_task_id] = list(dependencies)

        # 3-color DFS: 0=WHITE (unvisited), 1=GRAY (in current recursion stack), 2=BLACK (finished)
        visited: Dict[str, int] = {node: 0 for node in adj}
        path: List[str] = []

        def dfs(node: str) -> Optional[List[str]]:
            visited[node] = 1
            path.append(node)

            for neighbor in adj.get(node, []):
                state = visited.get(neighbor, 0)
                if state == 1:
                    # Directed cycle detected!
                    cycle_start = path.index(neighbor) if neighbor in path else 0
                    return path[cycle_start:] + [neighbor]
                elif state == 0:
                    found_cycle = dfs(neighbor)
                    if found_cycle:
                        return found_cycle

            path.pop()
            visited[node] = 2
            return None

        # Check starting from new_task_id
        cycle = dfs(new_task_id)
        if cycle:
            raise CyclicDependencyException(new_task_id, cycle)

        # Check remaining components
        for node in list(adj.keys()):
            if visited.get(node, 0) == 0:
                cycle = dfs(node)
                if cycle:
                    raise CyclicDependencyException(new_task_id, cycle)

    def _on_task_completed(self, completed_task: TaskModel) -> List[TaskModel]:
        """Automatically promote downstream PENDING tasks to QUEUED when all prerequisites complete."""
        all_channel_tasks = self._task_repo.list_tasks(channel_id=completed_task.channel_id)
        task_map = {t.id: t for t in all_channel_tasks}
        unlocked: List[TaskModel] = []

        # Find direct dependents currently in PENDING
        pending_dependents = [
            t for t in all_channel_tasks
            if t.status == TaskStatus.PENDING and completed_task.id in t.dependencies
        ]

        for p_task in pending_dependents:
            all_deps_ok = True
            for dep_id in p_task.dependencies:
                if dep_id == completed_task.id:
                    continue
                dep_row = task_map.get(dep_id)
                if not dep_row or dep_row.status != TaskStatus.COMPLETED:
                    all_deps_ok = False
                    break

            if all_deps_ok:
                try:
                    promoted = self.transition_status(
                        task_id=p_task.id,
                        new_status=TaskStatus.QUEUED,
                        execution_token=p_task.execution_token,
                    )
                    unlocked.append(promoted)
                    task_map[p_task.id] = promoted
                    self._logger.info(
                        f"Prerequisite '{completed_task.id}' completed: "
                        f"promoted dependent task '{p_task.id}' to QUEUED."
                    )
                except Exception as e:
                    self._logger.error(f"Failed to promote dependent task '{p_task.id}' to QUEUED: {e}")

        return unlocked

    def _on_task_failed_or_cancelled(self, terminated_task: TaskModel) -> List[TaskModel]:
        """Cascade abortion: Cancel all direct and transitive downstream tasks in PENDING or QUEUED."""
        transitive_dependents = self._get_transitive_dependents(terminated_task.id, terminated_task.channel_id)
        aborted: List[TaskModel] = []

        for dep_task in transitive_dependents:
            if dep_task.status in (TaskStatus.PENDING, TaskStatus.QUEUED):
                reason = (
                    f"Prerequisite task '{terminated_task.id}' terminated "
                    f"with state {terminated_task.status.value}"
                )
                try:
                    cancelled = self._task_repo.update_task_status(
                        task_id=dep_task.id,
                        new_status=TaskStatus.CANCELLED,
                        execution_token=dep_task.execution_token,
                        error_message=reason,
                    )
                    aborted.append(cancelled)
                    self._logger.warning(
                        f"Cascaded cancellation to dependent task '{dep_task.id}' "
                        f"due to termination of prerequisite '{terminated_task.id}'."
                    )
                except Exception as e:
                    self._logger.error(f"Failed to cascade cancel task '{dep_task.id}': {e}")

        return aborted

    def _get_transitive_dependents(self, root_task_id: str, channel_id: str) -> List[TaskModel]:
        """Find all tasks in the channel that depend directly or transitively on root_task_id."""
        all_channel_tasks = self._task_repo.list_tasks(channel_id=channel_id)
        children_map: Dict[str, List[TaskModel]] = {}
        for t in all_channel_tasks:
            for dep in t.dependencies:
                children_map.setdefault(dep, []).append(t)

        transitive: List[TaskModel] = []
        visited: Set[str] = set()
        queue = [root_task_id]

        while queue:
            current = queue.pop(0)
            for child in children_map.get(current, []):
                if child.id not in visited:
                    visited.add(child.id)
                    transitive.append(child)
                    queue.append(child.id)

        return transitive

