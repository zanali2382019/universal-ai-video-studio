"""Audit integrity service implementing SQLite Transactional Outbox, Hash Chaining, and Crash Recovery."""
import os
import sys
import json
import hashlib
import logging
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

from src.database.repositories.audit_repository import AuditRepository, GENESIS_HASH
from src.database.models import AuditEventOutboxModel, AuditChainStateModel
from src.core.config import get_settings
from src.core.exceptions import AuditIntegrityException
from src.core.logger import get_logger

logger = get_logger("studio.services.audit")

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

_lock_registry = threading.local()


class AuditService:
    """Manages secure, append-only, hash-chained audit logging backed by Transactional Outbox."""

    def __init__(
        self,
        audit_repository: Optional[AuditRepository] = None,
        service_logger: Optional[logging.Logger] = None,
    ) -> None:
        self._repo = audit_repository or AuditRepository()
        self._logger = service_logger or logger

    @contextmanager
    def _channel_lock(self, channel_id: str):
        """Re-entrant cross-process single-writer lock per channel using an OS-level file lock."""
        if not hasattr(_lock_registry, "active_locks"):
            _lock_registry.active_locks = {}

        current_depth = _lock_registry.active_locks.get(channel_id, 0)
        if current_depth > 0:
            _lock_registry.active_locks[channel_id] = current_depth + 1
            try:
                yield
            finally:
                _lock_registry.active_locks[channel_id] -= 1
            return

        settings = get_settings()
        audit_dir = settings.channels_root / channel_id / "audit"
        audit_dir.mkdir(parents=True, exist_ok=True)
        lock_file_path = audit_dir / ".audit_lock"

        lock_file = open(lock_file_path, "a+b")
        try:
            if sys.platform == "win32":
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
            else:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)

            _lock_registry.active_locks[channel_id] = 1
            yield
        finally:
            _lock_registry.active_locks[channel_id] = 0
            try:
                if sys.platform == "win32":
                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            lock_file.close()

    def record_event(
        self,
        channel_id: str,
        action: str,
        actor: str,
        payload: Dict[str, Any],
        idempotency_key: str,
    ) -> AuditEventOutboxModel:
        """Atomically record an audit event in SQLite outbox and flush to disk log."""
        clean_ch = channel_id.strip()

        with self._channel_lock(clean_ch):
            # Check idempotency
            existing = self._repo.get_event_by_idempotency_key(clean_ch, idempotency_key)
            if existing:
                self._logger.debug(f"Idempotent audit event hit: {idempotency_key}")
                self.drain_outbox(clean_ch)
                return existing

            state = self._repo.get_or_init_chain_state(clean_ch)
            if state.is_locked:
                raise AuditIntegrityException(
                    f"Export gate is HARD LOCKED for channel '{clean_ch}': {state.lock_reason}",
                    channel_id=clean_ch,
                )

            seq = state.last_seq + 1
            prev_hash = state.latest_audit_hash
            payload_str = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            now_utc = datetime.now(timezone.utc).isoformat()

            # Hash calculation: SHA256(prev_hash + canonical_json)
            hash_input = (prev_hash + payload_str).encode("utf-8")
            entry_hash = hashlib.sha256(hash_input).hexdigest()

            event = AuditEventOutboxModel(
                seq=seq,
                idempotency_key=idempotency_key,
                channel_id=clean_ch,
                action=action,
                actor=actor,
                payload_json=payload_str,
                previous_hash=prev_hash,
                entry_hash=entry_hash,
                file_flushed=False,
                created_at=now_utc,
            )

            # Insert into SQLite outbox
            self._repo.insert_outbox_event(event)

            # Flush outbox to disk log
            self.drain_outbox(clean_ch)
            return event

    def drain_outbox(self, channel_id: str) -> int:
        """Flush pending unflushed outbox events to license_audit.log with crash recovery."""
        clean_ch = channel_id.strip()
        flushed_count = 0

        with self._channel_lock(clean_ch):
            unflushed = self._repo.get_unflushed_events(clean_ch)
            if not unflushed:
                return 0

            settings = get_settings()
            log_path = settings.channels_root / clean_ch / "audit" / "license_audit.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)

            # Read existing file content to analyze EOF state
            existing_lines: List[str] = []
            trailing_bytes = b""
            last_valid_offset = 0

            if log_path.exists():
                with open(log_path, "rb") as f:
                    content = f.read()
                    last_newline = content.rfind(b"\n")
                    if last_newline != -1:
                        last_valid_offset = last_newline + 1
                        trailing_bytes = content[last_valid_offset:]
                        valid_part = content[:last_valid_offset].decode("utf-8")
                        existing_lines = [l for l in valid_part.splitlines() if l.strip()]
                    else:
                        trailing_bytes = content

            next_event = unflushed[0]
            expected_line = self._format_log_line(next_event)
            expected_bytes = expected_line.encode("utf-8")

            # Check if trailing bytes exist (Torn Write check)
            if trailing_bytes:
                if expected_bytes.startswith(trailing_bytes):
                    # Strict prefix match: genuine torn write of next event!
                    self._logger.warning(
                        f"Torn write detected for event seq={next_event.seq} on channel '{clean_ch}'. "
                        "Truncating trailing partial bytes and re-writing clean entry."
                    )
                    with open(log_path, "r+b") as f:
                        f.seek(last_valid_offset)
                        f.truncate()
                        f.flush()
                        os.fsync(f.fileno())
                else:
                    # Trailing bytes do NOT match prefix of expected next event -> Corruption / Tampering!
                    self._repo.lock_channel_export(
                        clean_ch,
                        "Audit log tail contains invalid / corrupted bytes that do not match pending outbox prefix.",
                    )
                    raise AuditIntegrityException(
                        "Audit log tail corruption detected. Export gate hard-locked.",
                        channel_id=clean_ch,
                    )

            # Check if event was ALREADY flushed to file before a crash (Crash between fsync and SQLite update)
            if existing_lines:
                last_line = existing_lines[-1]
                try:
                    parsed_last = json.loads(last_line)
                    if parsed_last.get("seq") == next_event.seq:
                        # Compare fields 100%
                        if (
                            parsed_last.get("entry_hash") == next_event.entry_hash
                            and parsed_last.get("previous_hash") == next_event.previous_hash
                            and parsed_last.get("action") == next_event.action
                        ):
                            # Already flushed! Just update DB
                            self._repo.mark_event_flushed(clean_ch, next_event.seq)
                            flushed_count += 1
                            unflushed = unflushed[1:]
                        else:
                            self._repo.lock_channel_export(
                                clean_ch,
                                f"Audit log seq={next_event.seq} entry hash mismatch with SQLite outbox.",
                            )
                            raise AuditIntegrityException(
                                "Audit log entry mismatch with SQLite outbox.",
                                channel_id=clean_ch,
                            )
                except json.JSONDecodeError:
                    self._repo.lock_channel_export(clean_ch, "Corrupt JSON in audit log tail.")
                    raise AuditIntegrityException("Corrupt JSON in audit log tail.", channel_id=clean_ch)

            # Flush remaining unflushed events in sequential order
            if unflushed:
                with open(log_path, "a+b") as f:
                    for ev in unflushed:
                        line_str = self._format_log_line(ev)
                        f.write(line_str.encode("utf-8"))
                        f.flush()
                        os.fsync(f.fileno())
                        self._repo.mark_event_flushed(clean_ch, ev.seq)
                        flushed_count += 1

            return flushed_count

    def verify_audit_log_integrity(self, channel_id: str) -> bool:
        """Verify sequential hash chaining from genesis to tip against SQLite anchor.

        Raises:
            AuditIntegrityException: If any tampering or discrepancy is detected.
        """
        clean_ch = channel_id.strip()

        with self._channel_lock(clean_ch):
            state = self._repo.get_or_init_chain_state(clean_ch)
            if state.is_locked:
                raise AuditIntegrityException(
                    f"Export gate is already locked: {state.lock_reason}",
                    channel_id=clean_ch,
                )

            # Drain any pending outbox events first
            self.drain_outbox(clean_ch)

            settings = get_settings()
            log_path = settings.channels_root / clean_ch / "audit" / "license_audit.log"

            if not log_path.exists():
                if state.last_seq == 0:
                    return True
                self._repo.lock_channel_export(clean_ch, "Audit log file missing while records exist in SQLite.")
                raise AuditIntegrityException("Audit log file missing.", channel_id=clean_ch)

            with open(log_path, "r", encoding="utf-8") as f:
                lines = [l for l in f.read().splitlines() if l.strip()]

            if len(lines) != state.last_seq:
                self._repo.lock_channel_export(
                    clean_ch,
                    f"Audit log record count mismatch (File has {len(lines)} lines, DB expected {state.last_seq}).",
                )
                raise AuditIntegrityException(
                    f"Audit log line count {len(lines)} does not match DB expected {state.last_seq}.",
                    channel_id=clean_ch,
                )

            expected_prev_hash = GENESIS_HASH

            for idx, line in enumerate(lines, start=1):
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError as e:
                    self._repo.lock_channel_export(clean_ch, f"Corrupted JSON record at line {idx}: {e}")
                    raise AuditIntegrityException(f"Corrupted JSON record at line {idx}: {e}", channel_id=clean_ch)

                seq = rec.get("seq")
                prev_h = rec.get("previous_hash")
                entry_h = rec.get("entry_hash")
                payload = rec.get("payload", {})

                if seq != idx:
                    self._repo.lock_channel_export(clean_ch, f"Sequence gap at line {idx}: got seq {seq}")
                    raise AuditIntegrityException(f"Sequence gap at line {idx}: got seq {seq}", channel_id=clean_ch)

                if prev_h != expected_prev_hash:
                    self._repo.lock_channel_export(
                        clean_ch,
                        f"Previous hash broken at line {idx}. Expected {expected_prev_hash}, got {prev_h}",
                    )
                    raise AuditIntegrityException(
                        f"Previous hash broken at line {idx}.", channel_id=clean_ch
                    )

                # Canonical recalculation
                canonical_payload = json.dumps(payload, sort_keys=True, separators=(",", ":"))
                recomputed = hashlib.sha256((prev_h + canonical_payload).encode("utf-8")).hexdigest()

                if recomputed != entry_h:
                    self._repo.lock_channel_export(
                        clean_ch,
                        f"Tampered entry hash at line {idx}. Recomputed {recomputed} != {entry_h}",
                    )
                    raise AuditIntegrityException(
                        f"Tampered entry hash at line {idx}.", channel_id=clean_ch
                    )

                expected_prev_hash = entry_h

            # Verify tip matches anchor
            if expected_prev_hash != state.latest_audit_hash:
                self._repo.lock_channel_export(
                    clean_ch,
                    f"Tip hash mismatch with SQLite anchor. Log: {expected_prev_hash}, DB: {state.latest_audit_hash}",
                )
                raise AuditIntegrityException("Audit tip hash mismatch with DB anchor.", channel_id=clean_ch)

            return True

    def is_export_locked(self, channel_id: str) -> Tuple[bool, Optional[str]]:
        """Check whether export gate is locked for a channel."""
        return self._repo.is_export_locked(channel_id.strip())

    def _format_log_line(self, event: AuditEventOutboxModel) -> str:
        """Format an audit outbox event into a canonical newline-delimited JSON string."""
        record = {
            "seq": event.seq,
            "timestamp": event.created_at,
            "channel_id": event.channel_id,
            "action": event.action,
            "actor": event.actor,
            "payload": json.loads(event.payload_json),
            "previous_hash": event.previous_hash,
            "entry_hash": event.entry_hash,
        }
        return json.dumps(record, ensure_ascii=False) + "\n"

