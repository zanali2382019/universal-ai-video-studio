"""Audit repository managing transactional outbox, sequence allocation, and chain state."""
from datetime import datetime, timezone
from typing import Optional, List, Tuple

from src.database.repositories.base import BaseRepository
from src.database.models import AuditEventOutboxModel, AuditChainStateModel
from src.core.logger import get_logger

logger = get_logger("studio.database.audit_repository")

GENESIS_HASH = "0" * 64


class AuditRepository(BaseRepository):
    """Handles audit transactional outbox and chain state in SQLite."""

    def get_or_init_chain_state(self, channel_id: str) -> AuditChainStateModel:
        """Get or initialize the audit chain state for a channel."""
        sql = "SELECT * FROM audit_chain_state WHERE channel_id = ?;"
        with self.db_manager.connection() as conn:
            cursor = conn.cursor()
            row = cursor.execute(sql, (channel_id,)).fetchone()
            if row:
                return AuditChainStateModel.from_row(row)

        now_utc = datetime.now(timezone.utc).isoformat()
        insert_sql = """
        INSERT OR IGNORE INTO audit_chain_state (
            channel_id, last_seq, latest_audit_hash, is_locked, lock_reason, updated_at
        ) VALUES (?, 0, ?, 0, NULL, ?);
        """
        with self.db_manager.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(insert_sql, (channel_id, GENESIS_HASH, now_utc))

        with self.db_manager.connection() as conn:
            row = conn.cursor().execute(sql, (channel_id,)).fetchone()
            return AuditChainStateModel.from_row(row)

    def is_export_locked(self, channel_id: str) -> Tuple[bool, Optional[str]]:
        """Check if channel has an active audit integrity export lock."""
        state = self.get_or_init_chain_state(channel_id)
        return state.is_locked, state.lock_reason

    def lock_channel_export(self, channel_id: str, reason: str) -> None:
        """Hard-lock channel export due to integrity violation."""
        self.get_or_init_chain_state(channel_id)
        now_utc = datetime.now(timezone.utc).isoformat()
        sql = """
        UPDATE audit_chain_state
        SET is_locked = 1, lock_reason = ?, updated_at = ?
        WHERE channel_id = ?;
        """
        with self.db_manager.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (reason, now_utc, channel_id))
        logger.critical(f"Channel '{channel_id}' export gate HARD LOCKED: {reason}")

    def insert_outbox_event(self, event: AuditEventOutboxModel) -> None:
        """Insert a new audit event into the outbox within the caller's transaction."""
        sql = """
        INSERT INTO audit_events_outbox (
            seq, idempotency_key, channel_id, action, actor,
            payload_json, previous_hash, entry_hash, file_flushed, created_at
        ) VALUES (
            :seq, :idempotency_key, :channel_id, :action, :actor,
            :payload_json, :previous_hash, :entry_hash, :file_flushed, :created_at
        );
        """
        params = {
            "seq": event.seq,
            "idempotency_key": event.idempotency_key,
            "channel_id": event.channel_id,
            "action": event.action,
            "actor": event.actor,
            "payload_json": event.payload_json,
            "previous_hash": event.previous_hash,
            "entry_hash": event.entry_hash,
            "file_flushed": 1 if event.file_flushed else 0,
            "created_at": event.created_at,
        }
        with self.db_manager.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            # Update audit_chain_state
            conn.execute(
                """
                UPDATE audit_chain_state
                SET last_seq = ?, latest_audit_hash = ?, updated_at = ?
                WHERE channel_id = ?;
                """,
                (event.seq, event.entry_hash, event.created_at, event.channel_id),
            )

    def get_unflushed_events(self, channel_id: str) -> List[AuditEventOutboxModel]:
        """Fetch all unflushed events for a channel in strict sequential order."""
        sql = """
        SELECT * FROM audit_events_outbox
        WHERE channel_id = ? AND file_flushed = 0
        ORDER BY seq ASC;
        """
        with self.db_manager.connection() as conn:
            cursor = conn.cursor()
            rows = cursor.execute(sql, (channel_id,)).fetchall()
            return [AuditEventOutboxModel.from_row(r) for r in rows]

    def mark_event_flushed(self, channel_id: str, seq: int) -> None:
        """Mark an outbox event as flushed to disk."""
        sql = "UPDATE audit_events_outbox SET file_flushed = 1 WHERE channel_id = ? AND seq = ?;"
        with self.db_manager.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (channel_id, seq))

    def get_event_by_idempotency_key(
        self, channel_id: str, idempotency_key: str
    ) -> Optional[AuditEventOutboxModel]:
        """Retrieve existing event by idempotency key."""
        sql = "SELECT * FROM audit_events_outbox WHERE channel_id = ? AND idempotency_key = ?;"
        with self.db_manager.connection() as conn:
            cursor = conn.cursor()
            row = cursor.execute(sql, (channel_id, idempotency_key)).fetchone()
            if not row:
                return None
            return AuditEventOutboxModel.from_row(row)

