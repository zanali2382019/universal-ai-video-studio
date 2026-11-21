"""Task repository managing SQLite persistent queue, atomic claims, channel leases, and checkpoints."""
import json
import uuid
import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any, Union

from src.database.repositories.base import BaseRepository
from src.database.models import (
    TaskModel,
    TaskCreate,
    TaskUpdate,
    ActiveChannelLeaseModel,
    TaskCheckpointModel,
    TaskCheckpointCreate,
)
from src.core.constants import TaskStatus, TaskType
from src.core.exceptions import (
    TaskNotFoundException,
    StaleExecutionTokenException,
    ChannelLeaseConflictException,
    ValidationException,
    DatabaseLockedException,
)
from src.core.logger import get_logger

logger = get_logger("studio.database.task_repository")


class TaskRepository(BaseRepository):
    """Manages persistent task queue operations, atomic claims, and execution leasing in SQLite."""

    def create_task(
        self,
        task_input: Union[TaskCreate, Dict[str, Any]],
        pre_insert_callback: Optional[Any] = None,
    ) -> TaskModel:
        """Create a new task with idempotent deduplication.

        If a task with the same (channel_id, idempotency_key) already exists,
        it returns the existing task record without duplication.

        Args:
            task_input: TaskCreate instance or payload dictionary.

        Returns:
            TaskModel: The persisted or existing task model.
        """
        if isinstance(task_input, dict):
            task_data = TaskCreate(**task_input)
        elif isinstance(task_input, TaskCreate):
            task_data = task_input
        else:
            raise ValidationException("Unsupported task creation input type.")

        clean_ch = task_data.channel_id.strip()
        clean_key = task_data.idempotency_key.strip()
        if not clean_ch:
            raise ValidationException("channel_id is required.")
        if not clean_key:
            raise ValidationException("idempotency_key is required.")

        # 1. Idempotency Check: Return existing task if duplicate
        existing = self.get_by_idempotency_key(clean_ch, clean_key)
        if existing:
            logger.info(f"Idempotent task match found for key '{clean_key}' on channel '{clean_ch}'. Reusing task '{existing.id}'.")
            return existing

        task_id = task_data.id or f"tsk_{uuid.uuid4().hex[:12]}"
        now_utc = datetime.now(timezone.utc).isoformat()
        sched_at = task_data.scheduled_at or now_utc

        deps_json = json.dumps(task_data.dependencies)
        payload_json = json.dumps(task_data.payload)

        sql = """
        INSERT INTO tasks (
            id, channel_id, project_id, scene_id, task_type, idempotency_key,
            status, priority, dependencies_json, payload_json, retry_count,
            max_retries, scheduled_at, execution_token, total_steps,
            completed_steps, created_at, updated_at
        ) VALUES (
            :id, :channel_id, :project_id, :scene_id, :task_type, :idempotency_key,
            :status, :priority, :dependencies_json, :payload_json, 0,
            :max_retries, :scheduled_at, 0, :total_steps,
            0, :created_at, :updated_at
        );
        """
        params = {
            "id": task_id,
            "channel_id": clean_ch,
            "project_id": task_data.project_id,
            "scene_id": task_data.scene_id,
            "task_type": task_data.task_type.value,
            "idempotency_key": clean_key,
            "status": task_data.status.value,
            "priority": task_data.priority,
            "dependencies_json": deps_json,
            "payload_json": payload_json,
            "max_retries": task_data.max_retries,
            "scheduled_at": sched_at,
            "total_steps": task_data.total_steps,
            "created_at": now_utc,
            "updated_at": now_utc,
        }

        try:
            with self.db_manager.transaction() as conn:
                cursor = conn.cursor()
                if pre_insert_callback:
                    pre_insert_callback(conn)
                cursor.execute(sql, params)
        except sqlite3.IntegrityError as e:
            # Handle concurrent insertion of same idempotency key
            existing = self.get_by_idempotency_key(clean_ch, clean_key)
            if existing:
                return existing
            raise ValidationException(f"Failed to create task due to integrity violation: {e}") from e

        persisted = self.get_by_id(task_id)
        if not persisted:
            raise ValidationException(f"Task '{task_id}' was not found after creation.")
        return persisted

    def get_by_id(self, task_id: str) -> Optional[TaskModel]:
        """Retrieve a task by ID."""
        sql = "SELECT * FROM tasks WHERE id = ?;"
        with self.db_manager.connection() as conn:
            cursor = conn.cursor()
            row = cursor.execute(sql, (task_id.strip(),)).fetchone()
            if not row:
                return None
            return TaskModel.from_row(row)

    def get_by_idempotency_key(self, channel_id: str, idempotency_key: str) -> Optional[TaskModel]:
        """Retrieve a task by channel ID and idempotency key."""
        sql = "SELECT * FROM tasks WHERE channel_id = ? AND idempotency_key = ?;"
        with self.db_manager.connection() as conn:
            cursor = conn.cursor()
            row = cursor.execute(sql, (channel_id.strip(), idempotency_key.strip())).fetchone()
            if not row:
                return None
            return TaskModel.from_row(row)

    def atomic_claim_next_task(
        self,
        worker_id: str,
        lease_duration_sec: int = 60,
        channel_id: Optional[str] = None,
    ) -> Optional[TaskModel]:
        """Atomically claim the next eligible queued task using BEGIN IMMEDIATE.

        Enforces:
        - Intra-channel single-writer serialization: Only claims if the channel has NO active lease in active_channel_leases.
        - Relational lease reservation: Inserts into active_channel_leases with PRIMARY KEY (channel_id).
        - Fair scheduling: Prioritizes channels least recently dispatched.
        - Fencing token: Increments task's execution_token by 1 upon claim.

        Args:
            worker_id: Identifier of the worker claiming the task.
            lease_duration_sec: Duration in seconds for the lease.
            channel_id: Optional filter to claim exclusively from a specific channel.

        Returns:
            Optional[TaskModel]: Claimed task model with status='running' and incremented execution_token,
            or None if no eligible task is available.
        """
        now_dt = datetime.now(timezone.utc)
        now_utc = now_dt.isoformat()
        lease_expiry_dt = now_dt + timedelta(seconds=lease_duration_sec)
        lease_expiry_utc = lease_expiry_dt.isoformat()

        with self.db_manager.transaction() as conn:
            cursor = conn.cursor()

            # Clean up expired channel leases first
            cursor.execute("DELETE FROM active_channel_leases WHERE lease_expires_at <= ?;", (now_utc,))

            # Select candidate task query
            channel_filter = "AND t.channel_id = ?" if channel_id else ""
            query_params = [now_utc, now_utc]
            if channel_id:
                query_params.append(channel_id.strip())

            candidate_sql = f"""
            SELECT t.id, t.channel_id, t.execution_token
            FROM tasks t
            WHERE t.status IN ('queued', 'retry_pending')
              AND t.scheduled_at <= ?
              AND t.channel_id NOT IN (
                  SELECT channel_id FROM active_channel_leases WHERE lease_expires_at > ?
              )
              {channel_filter}
            ORDER BY (SELECT last_task_dispatched_at FROM channels WHERE channels.id = t.channel_id) ASC NULLS FIRST,
                     t.priority DESC,
                     t.scheduled_at ASC,
                     t.created_at ASC
            LIMIT 1;
            """

            candidate_row = cursor.execute(candidate_sql, query_params).fetchone()
            if not candidate_row:
                return None

            candidate_id = candidate_row["id"]
            cand_channel_id = candidate_row["channel_id"]
            new_token = candidate_row["execution_token"] + 1

            # 1. Update task row
            update_sql = """
            UPDATE tasks
            SET status = 'running',
                worker_id = ?,
                claimed_at = ?,
                heartbeat_at = ?,
                lease_expires_at = ?,
                execution_token = ?,
                updated_at = ?
            WHERE id = ? AND status IN ('queued', 'retry_pending');
            """
            cursor.execute(
                update_sql,
                (worker_id, now_utc, now_utc, lease_expiry_utc, new_token, now_utc, candidate_id),
            )
            if cursor.rowcount == 0:
                # Concurrent claim collision: another worker claimed candidate
                return None

            # 2. Insert lease into active_channel_leases
            lease_sql = """
            INSERT INTO active_channel_leases (
                channel_id, task_id, worker_id, execution_token, lease_expires_at
            ) VALUES (?, ?, ?, ?, ?);
            """
            try:
                cursor.execute(
                    lease_sql,
                    (cand_channel_id, candidate_id, worker_id, new_token, lease_expiry_utc),
                )
            except sqlite3.IntegrityError:
                # Channel lease already held by concurrent worker; roll back claim
                raise ChannelLeaseConflictException(cand_channel_id, candidate_id)

            # 3. Update channel's last_task_dispatched_at for fair scheduling
            cursor.execute(
                "UPDATE channels SET last_task_dispatched_at = ? WHERE id = ?;",
                (now_utc, cand_channel_id),
            )

            # Fetch updated row
            task_row = cursor.execute("SELECT * FROM tasks WHERE id = ?;", (candidate_id,)).fetchone()
            return TaskModel.from_row(task_row)

    def renew_lease(
        self,
        channel_id: str,
        task_id: str,
        worker_id: str,
        execution_token: int,
        lease_duration_sec: int = 60,
    ) -> bool:
        """Renew active lease and pulse heartbeat for a running task.

        Strictly enforces 4-key match: (channel_id, task_id, worker_id, execution_token).

        Args:
            channel_id: Channel ID.
            task_id: Task ID.
            worker_id: Worker ID pulsing heartbeat.
            execution_token: Assigned execution token.
            lease_duration_sec: Extension duration in seconds.

        Returns:
            bool: True if renewed, False if token/worker mismatch or lease expired.

        Raises:
            StaleExecutionTokenException: If task is found but token is stale.
        """
        now_dt = datetime.now(timezone.utc)
        now_utc = now_dt.isoformat()
        new_expiry_utc = (now_dt + timedelta(seconds=lease_duration_sec)).isoformat()

        with self.db_manager.transaction() as conn:
            cursor = conn.cursor()

            # Verify task current state
            task_row = cursor.execute(
                "SELECT execution_token, worker_id, status FROM tasks WHERE id = ?;",
                (task_id,),
            ).fetchone()
            if not task_row:
                raise TaskNotFoundException(task_id)

            if (
                task_row["execution_token"] != execution_token
                or task_row["worker_id"] != worker_id
                or task_row["status"] != TaskStatus.RUNNING.value
            ):
                return False

            # Update active_channel_leases
            cursor.execute(
                """
                UPDATE active_channel_leases
                SET lease_expires_at = ?
                WHERE channel_id = ?
                  AND task_id = ?
                  AND worker_id = ?
                  AND execution_token = ?;
                """,
                (new_expiry_utc, channel_id, task_id, worker_id, execution_token),
            )
            if cursor.rowcount == 0:
                return False

            # Update tasks table heartbeat
            cursor.execute(
                """
                UPDATE tasks
                SET heartbeat_at = ?,
                    lease_expires_at = ?,
                    updated_at = ?
                WHERE id = ?
                  AND worker_id = ?
                  AND execution_token = ?
                  AND status = 'running';
                """,
                (now_utc, new_expiry_utc, now_utc, task_id, worker_id, execution_token),
            )
            return cursor.rowcount > 0

    def release_lease(
        self,
        channel_id: str,
        task_id: str,
        worker_id: str,
        execution_token: int,
    ) -> bool:
        """Explicitly release channel lease matching all 4 keys.

        Args:
            channel_id: Channel ID.
            task_id: Task ID.
            worker_id: Worker ID.
            execution_token: Assigned token.

        Returns:
            bool: True if released, False if not found/mismatched.
        """
        sql = """
        DELETE FROM active_channel_leases
        WHERE channel_id = ?
          AND task_id = ?
          AND worker_id = ?
          AND execution_token = ?;
        """
        with self.db_manager.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (channel_id, task_id, worker_id, execution_token))
            return cursor.rowcount > 0

    def update_task_status(
        self,
        task_id: str,
        new_status: TaskStatus,
        execution_token: int,
        worker_id: Optional[str] = None,
        result: Optional[Dict[str, Any]] = None,
        error_message: Optional[str] = None,
        error_details: Optional[Dict[str, Any]] = None,
        current_step: Optional[str] = None,
        completed_steps: Optional[int] = None,
        scheduled_at: Optional[str] = None,
    ) -> TaskModel:
        """Update task status atomically with strict fencing token verification.

        If new_status is not RUNNING, automatically releases any held channel lease matching the token.

        Args:
            task_id: Task ID.
            new_status: New TaskStatus.
            execution_token: Fencing token of the worker.

        Returns:
            TaskModel: Updated task.

        Raises:
            TaskNotFoundException: If task does not exist.
            StaleExecutionTokenException: If task's token does not match execution_token.
        """
        now_utc = datetime.now(timezone.utc).isoformat()
        result_json = json.dumps(result) if result is not None else None
        error_details_json = json.dumps(error_details) if error_details is not None else None

        with self.db_manager.transaction() as conn:
            cursor = conn.cursor()

            task_row = cursor.execute("SELECT * FROM tasks WHERE id = ?;", (task_id,)).fetchone()
            if not task_row:
                raise TaskNotFoundException(task_id)

            current_token = task_row["execution_token"]
            if current_token != execution_token:
                raise StaleExecutionTokenException(task_id, current_token, execution_token)

            sql = """
            UPDATE tasks
            SET status = :status,
                result_json = COALESCE(:result_json, result_json),
                error_message = COALESCE(:error_message, error_message),
                error_details_json = COALESCE(:error_details_json, error_details_json),
                current_step = COALESCE(:current_step, current_step),
                completed_steps = COALESCE(:completed_steps, completed_steps),
                scheduled_at = COALESCE(:scheduled_at, scheduled_at),
                updated_at = :now_utc
            WHERE id = :task_id AND execution_token = :execution_token;
            """
            cursor.execute(
                sql,
                {
                    "status": new_status.value,
                    "result_json": result_json,
                    "error_message": error_message,
                    "error_details_json": error_details_json,
                    "current_step": current_step,
                    "completed_steps": completed_steps,
                    "scheduled_at": scheduled_at,
                    "now_utc": now_utc,
                    "task_id": task_id,
                    "execution_token": execution_token,
                },
            )

            # If task transitions out of RUNNING, atomically release lease
            if new_status != TaskStatus.RUNNING:
                cursor.execute(
                    "DELETE FROM active_channel_leases WHERE task_id = ? AND execution_token = ?;",
                    (task_id, execution_token),
                )

            updated_row = cursor.execute("SELECT * FROM tasks WHERE id = ?;", (task_id,)).fetchone()
            return TaskModel.from_row(updated_row)

    def reap_stale_task(
        self,
        task_id: str,
        stale_worker_id: str,
        stale_token: int,
        new_status: TaskStatus = TaskStatus.RETRY_PENDING,
        scheduled_at: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> bool:
        """Reap a stale task whose lease expired, incrementing execution_token.

        Strictly guarantees that TaskReaper cannot reap or touch a newer worker's lease.

        Args:
            task_id: Task ID.
            stale_worker_id: The worker ID that held the expired lease.
            stale_token: The token associated with the expired lease.
            new_status: Target status (default RETRY_PENDING).
            scheduled_at: Next scheduled execution time.
            error_message: Reason for reaping.

        Returns:
            bool: True if successfully reaped, False if token/worker changed (newer worker active).
        """
        now_utc = datetime.now(timezone.utc).isoformat()

        with self.db_manager.transaction() as conn:
            cursor = conn.cursor()

            # 1. Update task ONLY if still matching stale_token and status='running'
            cursor.execute(
                """
                UPDATE tasks
                SET status = :new_status,
                    retry_count = retry_count + CASE WHEN :new_status = 'retry_pending' THEN 1 ELSE 0 END,
                    execution_token = execution_token + 1,
                    scheduled_at = COALESCE(:scheduled_at, scheduled_at),
                    error_message = COALESCE(:error_message, error_message),
                    worker_id = NULL,
                    updated_at = :now_utc
                WHERE id = :task_id
                  AND execution_token = :stale_token
                  AND status = 'running';
                """,
                {
                    "new_status": new_status.value,
                    "scheduled_at": scheduled_at,
                    "error_message": error_message,
                    "now_utc": now_utc,
                    "task_id": task_id,
                    "stale_token": stale_token,
                },
            )
            if cursor.rowcount == 0:
                # Newer worker already took over or task status changed
                return False

            # 2. Delete lease ONLY matching stale_worker_id and stale_token
            cursor.execute(
                """
                DELETE FROM active_channel_leases
                WHERE task_id = ?
                  AND worker_id = ?
                  AND execution_token = ?;
                """,
                (task_id, stale_worker_id, stale_token),
            )
            return True

    def save_checkpoint(self, checkpoint_input: Union[TaskCheckpointCreate, Dict[str, Any]]) -> TaskCheckpointModel:
        """Record a step checkpoint with execution token verification.

        Args:
            checkpoint_input: TaskCheckpointCreate or dict payload.

        Returns:
            TaskCheckpointModel: Persisted checkpoint.

        Raises:
            TaskNotFoundException: If task not found.
            StaleExecutionTokenException: If worker holds a stale execution token.
        """
        if isinstance(checkpoint_input, dict):
            cp_data = TaskCheckpointCreate(**checkpoint_input)
        elif isinstance(checkpoint_input, TaskCheckpointCreate):
            cp_data = checkpoint_input
        else:
            raise ValidationException("Unsupported checkpoint input type.")

        cp_id = cp_data.id or f"cp_{uuid.uuid4().hex[:12]}"
        now_utc = datetime.now(timezone.utc).isoformat()
        data_json = json.dumps(cp_data.step_data)

        with self.db_manager.transaction() as conn:
            cursor = conn.cursor()

            task_row = cursor.execute(
                "SELECT execution_token, status FROM tasks WHERE id = ?;",
                (cp_data.task_id,),
            ).fetchone()
            if not task_row:
                raise TaskNotFoundException(cp_data.task_id)

            current_token = task_row["execution_token"]
            if current_token != cp_data.execution_token:
                raise StaleExecutionTokenException(cp_data.task_id, current_token, cp_data.execution_token)

            if task_row["status"] != TaskStatus.RUNNING.value:
                raise StaleExecutionTokenException(
                    cp_data.task_id,
                    current_token,
                    cp_data.execution_token,
                    "Cannot save checkpoint when task is not RUNNING",
                )

            # Insert or replace checkpoint
            sql = """
            INSERT INTO task_checkpoints (
                id, task_id, step_name, step_index, step_data_json, execution_token, created_at
            ) VALUES (
                :id, :task_id, :step_name, :step_index, :step_data_json, :execution_token, :created_at
            )
            ON CONFLICT(task_id, step_name) DO UPDATE SET
                step_index = excluded.step_index,
                step_data_json = excluded.step_data_json,
                execution_token = excluded.execution_token,
                created_at = excluded.created_at;
            """
            cursor.execute(
                sql,
                {
                    "id": cp_id,
                    "task_id": cp_data.task_id,
                    "step_name": cp_data.step_name,
                    "step_index": cp_data.step_index,
                    "step_data_json": data_json,
                    "execution_token": cp_data.execution_token,
                    "created_at": now_utc,
                },
            )

            # Update task progress
            cursor.execute(
                """
                UPDATE tasks
                SET current_step = ?,
                    completed_steps = MAX(completed_steps, ?),
                    updated_at = ?
                WHERE id = ? AND execution_token = ?;
                """,
                (cp_data.step_name, cp_data.step_index, now_utc, cp_data.task_id, cp_data.execution_token),
            )

            row = cursor.execute("SELECT * FROM task_checkpoints WHERE id = ?;", (cp_id,)).fetchone()
            return TaskCheckpointModel.from_row(row)

    def get_checkpoints(self, task_id: str) -> List[TaskCheckpointModel]:
        """Retrieve all checkpoints for a task ordered by step index."""
        sql = "SELECT * FROM task_checkpoints WHERE task_id = ? ORDER BY step_index ASC;"
        with self.db_manager.connection() as conn:
            cursor = conn.cursor()
            rows = cursor.execute(sql, (task_id.strip(),)).fetchall()
            return [TaskCheckpointModel.from_row(r) for r in rows]

    def get_active_channel_lease(self, channel_id: str) -> Optional[ActiveChannelLeaseModel]:
        """Query active lease for a channel."""
        sql = "SELECT * FROM active_channel_leases WHERE channel_id = ?;"
        with self.db_manager.connection() as conn:
            cursor = conn.cursor()
            row = cursor.execute(sql, (channel_id.strip(),)).fetchone()
            if not row:
                return None
            return ActiveChannelLeaseModel.from_row(row)

    def clean_expired_leases(self, cutoff_utc: Optional[str] = None) -> int:
        """Purge expired channel leases from active_channel_leases."""
        cutoff = cutoff_utc or datetime.now(timezone.utc).isoformat()
        with self.db_manager.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM active_channel_leases WHERE lease_expires_at <= ?;", (cutoff,))
            return cursor.rowcount

    def list_tasks(
        self,
        channel_id: Optional[str] = None,
        status: Optional[TaskStatus] = None,
        project_id: Optional[str] = None,
    ) -> List[TaskModel]:
        """List tasks with optional filters."""
        query = "SELECT * FROM tasks WHERE 1=1"
        params: List[Any] = []

        if channel_id:
            query += " AND channel_id = ?"
            params.append(channel_id.strip())
        if status:
            query += " AND status = ?"
            params.append(status.value)
        if project_id:
            query += " AND project_id = ?"
            params.append(project_id.strip())

        query += " ORDER BY priority DESC, created_at ASC;"

        with self.db_manager.connection() as conn:
            cursor = conn.cursor()
            rows = cursor.execute(query, params).fetchall()
            return [TaskModel.from_row(r) for r in rows]

