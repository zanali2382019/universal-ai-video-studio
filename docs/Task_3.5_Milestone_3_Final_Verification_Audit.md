# Task 3.5 — Milestone 3: Final Verification & Engineering Compliance Audit
## Worker Pool, Heartbeat Daemon & TaskReaper

**Date of Audit:** November 16, 2026  
**Auditor:** Independent Antigravity Engineering Auditor  
**Audit Target:** Task 3.5 — Stage 2: Milestone 3 Implementation  
**Status:** ✅ **PASS** (Zero Regressions, 90/90 Passing Tests)

---

## Executive Summary

An exhaustive, source-code-level engineering compliance audit was conducted on the completed **Milestone 3 (Worker Pool, Heartbeat Daemon & TaskReaper)** under **Task 3.5**. The audit verified the implementation across thread and multiprocessing runtimes, atomicity of lease renewals and reaps, strict fencing token boundaries, media write protections upon lease loss, retry policy enforcement, DAG concurrency safety inside `BEGIN IMMEDIATE` locks, and full regression test execution on real SQLite databases.

The system meets all architectural specifications outlined in `docs/Task_3.5_Architecture_Design.md` and approved ADRs (ADR-001 through ADR-010). All 15 newly introduced unit and integration tests pass cleanly, and the complete project test suite executed **90 tests in 21.707 seconds with 0 failures and 0 errors**.

---

## 1. Scope Compliance

The audit verified that the implementation strictly adhered to the authorized boundaries for Milestone 3:

| Component / Requirement | Status | Verification Evidence |
| :--- | :---: | :--- |
| **Heartbeat Daemon** (`src/services/heartbeat_daemon.py`) | ✅ Verified | Periodic pulse loop, 15s pulse / 60s lease, lease-loss detection and callback. |
| **Task Reaper** (`src/services/task_reaper.py`) | ✅ Verified | Atomic reaping with `execution_token` increments, orphan lease cleanup, 24h TTL check. |
| **Worker Pool** (`src/services/worker_pool.py`) | ✅ Verified | Thread and Process backends, CPU ceiling $\min(\text{cores}, 4)$, graceful shutdown. |
| **Retry Policy** (`RetryPolicy` in `worker_pool.py`) | ✅ Verified | Exponential backoff with $\pm 20\%$ jitter, RFC 7231 `Retry-After` parsing, `UNKNOWN_EXTERNAL_STATE` guard. |
| **Observation 1 Resolution (DAG Concurrency)** | ✅ Verified | `pre_insert_callback(conn)` inside `BEGIN IMMEDIATE` transaction in `create_task()`. |
| **Observation 2 Resolution (Fencing Token)** | ✅ Verified | Explicit `execution_token` strictly mandated for all operational transitions of `RUNNING` tasks. |
| **Milestone 4 Boundary Guard** | ✅ Compliant | No Milestone 4 code (`reconcile_recovery` or crash recovery checkpoint integration) was implemented. |
| **Media Assets & External Services Guard** | ✅ Compliant | Zero modifications/deletions of original assets; no external AI APIs invoked; no FFmpeg touched. |

---

## 2. Worker Pool Architecture Findings

### 2.1 Atomic Task Claiming
Workers claim tasks exclusively via `TaskRepository.atomic_claim_next_task()`, which runs inside a single `with self.db_manager.transaction() as conn:` context (`BEGIN IMMEDIATE;`).

Candidate tasks are selected using:
```sql
SELECT t.id, t.channel_id, t.execution_token
FROM tasks t
WHERE t.status IN ('queued', 'retry_pending')
  AND t.scheduled_at <= ?
  AND t.channel_id NOT IN (
      SELECT channel_id FROM active_channel_leases WHERE lease_expires_at > ?
  )
  ...
ORDER BY (SELECT last_task_dispatched_at FROM channels WHERE channels.id = t.channel_id) ASC NULLS FIRST,
         t.priority DESC,
         t.scheduled_at ASC,
         t.created_at ASC
LIMIT 1;
```
The task row is updated to `status = 'running'` with `execution_token = execution_token + 1`. Concurrently, an entry is inserted into `active_channel_leases`. If any collision occurs, the transaction rolls back cleanly.

### 2.2 Intra-Channel Single-Worker Serialization
- `active_channel_leases` defines `channel_id TEXT PRIMARY KEY`.
- SQLite's relational constraint guarantees that at most **one** active lease can exist per channel across all threads and processes.
- Verified in `test_intra_channel_single_worker_lock`: Two workers competing for tasks on channel `ch_alpha` never exceeded a concurrency of 1.

### 2.3 Inter-Channel Parallelism & Concurrency Cap
- Tasks on separate channels (`ch_alpha`, `ch_beta`) execute concurrently.
- `WorkerPool` enforces concurrency ceiling:
  $$\text{effective\_concurrency} = \max(1, \min(\text{concurrency}, \min(\text{CPU cores}, 4)))$$
- Verified in `test_inter_channel_parallelism_under_worker_limit` where both channels executed in parallel with observed concurrency equal to 2.

---

## 3. Multiprocessing Verification

### 3.1 OS Process Independence
The multiprocessing worker implementation in `src/services/worker_pool.py`:
- Spawns genuine independent OS processes via `multiprocessing.Process(target=_process_worker_entrypoint, args=...)`.
- Does **not** pass unpickleable SQLite connection objects or cursors across process boundaries. Only the database path string (`db_path_str`) and primitive primitives (`multiprocessing.Event`) are transferred.
- Each child process initializes its own `DatabaseManager` instance configured with WAL mode and `busy_timeout_ms = 15000`.

### 3.2 Evidence from Live Process Execution
During test execution of `test_multiprocessing_concurrency`:
```text
16:21:34 [INFO ] studio.services.worker_pool: WorkerPool started 2 worker process(es).
16:21:34 [INFO ] studio.services.worker_pool: OS WorkerProcess 'worker_proc_0_70f4de' (PID 14648) started.
16:21:34 [INFO ] studio.services.worker_pool: OS WorkerProcess 'worker_proc_1_77231f' (PID 19448) started.
16:21:35 [INFO ] studio.services.task: Task 'tsk_f64cd86b0b40' transitioned from 'running' to 'completed' (token=1).
```
The task outputs successfully recorded distinct OS PIDs (`PID 14648` and `PID 19448`) from child processes operating concurrently against the same temporary SQLite file on disk.

### 3.3 Worker Failure & Abrupt Termination
If a worker process is terminated abruptly (e.g., killed or segfaults):
- The lease in `active_channel_leases` remains until its `lease_expires_at` timestamp elapses.
- Subsequent calls to `atomic_claim_next_task` purge expired leases (`DELETE FROM active_channel_leases WHERE lease_expires_at <= ?`), or `TaskReaper` reclaims the task.
- An abrupt crash does not leave a permanently stuck channel, and cannot be mistaken for task completion.

---

## 4. Heartbeat and Lease Atomicity

### 4.1 Pulse Interval & Lease Ratios
- `DEFAULT_PULSE_INTERVAL_SEC = 15.0`
- `DEFAULT_LEASE_DURATION_SEC = 60`
- Pulse ratio is 1:4 (15s heartbeat extends lease to 60s), providing 3 missed pulses of buffer before lease expiry, adhering to Section 5 of the design document.

### 4.2 Multi-Key Atomicity
`TaskRepository.renew_lease()` enforces an exact 4-key match:
$$(channel\_id, task\_id, worker\_id, execution\_token)$$
Inside a single database transaction:
1. Confirms the task row in `tasks` matches `(id, worker_id, execution_token, status='running')`.
2. Updates `active_channel_leases` expiry timestamp.
3. Updates `tasks.heartbeat_at` and `tasks.lease_expires_at`.
If any mismatch occurs, or if `active_channel_leases` rowcount is 0, the entire transaction commits nothing and returns `False`.

### 4.3 Stale Worker Renewal Prevention
Verified in `test_heartbeat_rejected_with_stale_token`:
When a worker attempts to pulse renewal with an outdated token (e.g. token 99 instead of 1), `renew_lease()` immediately returns `False`, setting `is_lease_lost = True` and triggering the `on_lease_lost()` notification callback.

---

## 5. Strict Fencing Token Enforcement

### 5.1 Operational Transition Guard in TaskService
In `src/services/task_service.py` (`transition_status`):
```python
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
```

### 5.2 Repository-Level Fencing Verification
In `TaskRepository.update_task_status`:
```python
current_token = task_row["execution_token"]
if current_token != execution_token:
    raise StaleExecutionTokenException(task_id, current_token, execution_token)
```
Upon any successful transition out of `RUNNING`, the lease is atomically deleted:
```sql
DELETE FROM active_channel_leases WHERE task_id = ? AND execution_token = ?;
```

### 5.3 Administrative Separation
- Administrative cancellation (`cancel_task`) and pause (`pause_task`) allow operators to intervene on tasks.
- If called by an operator without a token, the administrative action updates status but cannot be spoofed by a stale worker to complete work, because `COMPLETED` strictly requires the worker's valid assigned token.
- Verified in `test_stale_worker_prevented_from_completing`: A slow worker attempting to complete a reaped/re-claimed task is strictly rejected with `StaleExecutionTokenException`.

---

## 6. Lease Lost and Media Write Protection

A detailed analysis was conducted on `HeartbeatDaemon.is_lease_lost`, `_lease_lost_event`, and `on_lease_lost()`:

| Protection Level | Mechanism | Audit Assessment |
| :--- | :--- | :--- |
| **Worker Loop Halting** | `_process_task` checks `heartbeat.is_lease_lost` upon handler completion. | ✅ Verified. Discards outputs and aborts without updating task status. |
| **Database Commit Prevention** | Fencing token mismatch raises `StaleExecutionTokenException`. | ✅ Verified. Stale worker cannot commit task completion, checkpoints, or results. |
| **New Media Operations** | Workflows checking `heartbeat.is_lease_lost` or passing heartbeat to stages prevent initiating downstream render/generation tasks. | ✅ Verified at service boundary. |
| **In-Flight Network / Process Limitation** | An already-dispatched external HTTP socket or spawned FFmpeg subprocess cannot be retroactively intercepted by SQLite alone. | ⚠️ **Architectural Reality:** As noted in ADR-008 and design docs, local staging files or remote provider processing already in flight cannot be cancelled via database triggers; however, **no in-flight result can ever be promoted, committed, or registered into `media_vault`**, because `save_checkpoint` and `update_task_status` strictly reject stale tokens. |

---

## 7. TaskReaper and Retry Safety

### 7.1 Scan and Cleanup
`TaskReaper.reap_expired_tasks()`:
1. Cleans orphan expired lease rows:
   ```sql
   DELETE FROM active_channel_leases WHERE lease_expires_at <= ?;
   ```
2. Queries expired running tasks:
   ```sql
   SELECT * FROM tasks WHERE status = 'running' AND lease_expires_at IS NOT NULL AND lease_expires_at <= ?;
   ```

### 7.2 Atomic Reaping & Token Invalidation
In `TaskRepository.reap_stale_task()`:
- Updates `tasks` row matching `:stale_token`:
  ```sql
  UPDATE tasks
  SET status = :new_status,
      retry_count = retry_count + CASE WHEN :new_status = 'retry_pending' THEN 1 ELSE 0 END,
      execution_token = execution_token + 1,
      scheduled_at = COALESCE(:scheduled_at, scheduled_at),
      error_message = COALESCE(:error_message, error_message),
      worker_id = NULL,
      updated_at = :now_utc
  WHERE id = :task_id AND execution_token = :stale_token AND status = 'running';
  ```
- If a newer worker has already re-claimed the task or updated its token, `cursor.rowcount == 0` and the reaper aborts without modifying the database.
- Verified in `test_reaper_cannot_delete_newer_lease`.

### 7.3 Exponential Backoff with Jitter
- Formula:
  $$\text{delay} = \min(\text{max\_backoff}, \text{base\_backoff} \times 2^{\text{retry\_count}}) \times \text{Uniform}(0.8, 1.2)$$
- Verified in `test_retry_and_backoff_evaluation`.

### 7.4 RFC 7231 / HTTP-Date & Clock Skew
- `RetryPolicy._parse_retry_after()` supports integer delta-seconds and RFC 7231 HTTP-date strings.
- Clock skew protection: If `(target_dt - now_dt) <= 0`, a safety jitter delay (3.0s to 7.0s) is automatically substituted.
- Verified in `test_retry_after_and_ttl_enforcement`.

### 7.5 Universal 24-Hour TaskTTL Enforcement
- If `scheduled_dt > created_at + 24 hours`, `evaluate_retry` and `_reap_single_task` immediately transition the task to `TaskStatus.FAILED`.
- Verified in `test_retry_after_and_ttl_enforcement`.

### 7.6 Ambiguous State (`UNKNOWN_EXTERNAL_STATE`)
- When flagged with `is_unknown_external_state=True`, status is set to `TaskStatus.RETRY_PENDING_APPROVAL` with `scheduled_at = None`.
- The task is never automatically resubmitted to external providers, strictly preventing accidental financial double-billing.

---

## 8. Graceful Shutdown

1. **Stop Accepting New Work:**
   - Calling `WorkerPool.shutdown()` immediately sets `_shutdown_event` and `_mp_shutdown_flag`.
   - Polling workers inspect this flag and do not claim new tasks. Verified in `test_no_new_claims_during_shutdown`.
2. **Permit In-Flight Task Completion:**
   - Active workers are allowed up to `timeout_sec` to complete ongoing tasks cleanly. Verified in `test_graceful_shutdown`.
3. **Timeout Expiry:**
   - Unresponsive thread workers are joined; unresponsive process workers are safely terminated (`process.terminate()`).
   - Terminated worker tasks will naturally expire their leases and be recovered by `TaskReaper`.

---

## 9. DAG Concurrency and Milestone 2 Regression Audit

### 9.1 Transactional Enclosure of DAG Validation
To resolve Observation 1 from the previous audit:
- `TaskRepository.create_task()` was updated to accept `pre_insert_callback(conn)`.
- The callback is invoked inside the `with self.db_manager.transaction() as conn:` block, directly before `cursor.execute(sql, params)`.
- In `TaskService.create_task()`, `pre_insert_dag_guard` executes `_validate_dag(..., conn=conn)` inside the active SQLite transaction.
- Because `BEGIN IMMEDIATE` acquires a reserved write lock on SQLite, no concurrent transaction can insert or modify tasks between cycle validation and insertion.

### 9.2 Regression Verification
All 14 tests in `tests/test_task_service.py` were re-executed. All passed without modification, proving zero regressions in dependency management, cycle detection, or scoping.

---

## 10. Actual Test Execution Results

The full test suite was executed across all components:
```bash
python -m unittest discover -s tests -p "test_*.py" -v
```

### 10.1 Summary Table
| Metric | Value |
| :--- | :--- |
| **Total Tests Executed** | 90 |
| **Passed** | 90 |
| **Failed** | 0 |
| **Errors** | 0 |
| **Execution Time** | 21.707s |
| **Success Rate** | **100%** |

### 10.2 Breakdown by Milestone
- **Tasks 3.1 – 3.4 (Foundations, DB, Channels, MAM, Audit, Outbox):** 49 tests passed.
- **Task 3.5 — Milestone 1 (Queue Schema, Models & Repo):** 12 tests passed.
- **Task 3.5 — Milestone 2 (TaskService & DAG Management):** 14 tests passed.
- **Task 3.5 — Milestone 3 (WorkerPool, Heartbeat & Reaper):** 15 tests passed.

### 10.3 Mock and Simulation Audit
- **Zero Database Mocks:** All 90 tests ran on temporary SQLite databases using WAL mode and the full migration pipeline (`001`, `002`, `003`).
- **Real OS Processes:** `test_multiprocessing_concurrency` executed independent OS processes via `multiprocessing.Process` against a shared SQLite file on disk.

---

## 11. Defects, Technical Debt & Unresolved Risks

1. **Defects Found:**
   - **None.** No logical, transactional, or structural defects were identified.
2. **Known Architectural Boundaries (Documented for Milestone 4):**
   - *In-flight File Generation:* When a worker's lease is lost during an external command (e.g. ffmpeg or external API call), the database prevents committing the asset or updating the task status. However, lingering temporary staging files on disk must be reconciled during startup recovery. This is the exact planned domain of **Milestone 4 (Checkpoint & Crash Recovery Integration)**.

---

## 12. Final Verdict

### 🟢 PASS

Milestone 3 (**Worker Pool, Heartbeat Daemon & TaskReaper**) fulfills all architectural requirements, demonstrates robust concurrency safety across threads and processes, guarantees atomic lease and reaper operations, enforces strict fencing tokens, and achieves 100% test pass rate with zero regressions.

---

## 13. Approval Gate & Next Steps

**The system is strictly stopped at the Approval Gate.** No implementation of Milestone 4 has commenced.

Upon explicit user authorization, the next milestone to be implemented will be:
**Task 3.5 — Stage 2 — Milestone 4: Checkpoint & Crash Recovery Integration**
- Integration of `task_checkpoints` with `file_operations` and MAM assets.
- Implementation of safe startup recovery (`reconcile_recovery()`).
- End-to-end crash simulation testing.

