"""Repository for Production Intelligence, Flow Credit Ledger, and Cost Configurations (Task 3.17)."""
import uuid
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

from src.database.connection import DatabaseManager
from src.database.repositories.base import BaseRepository
from src.database.models import (
    FlowCreditTransactionModel,
    FlowCreditTransactionCreate,
    ProductionCostConfigModel,
    ProductionCostConfigUpdate,
)
from src.core.logger import get_logger

logger = get_logger("studio.database.intelligence_repository")


class IntelligenceRepository(BaseRepository):
    """Database repository for credit ledger transactions and channel cost configurations."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None) -> None:
        super().__init__(db_manager)

    @property
    def db(self) -> DatabaseManager:
        return self.db_manager

    # -------------------------------------------------------------------------
    # Flow Credit Ledger (Manual Entry Resource Tracking)
    # -------------------------------------------------------------------------
    def get_credit_balance(self, channel_id: str) -> float:
        """Fetch the current Google Flow credit balance for a channel.

        Returns:
            Current balance (float). Returns 0.0 if no entries exist.
        """
        with self.db.connection() as conn:
            row = conn.execute(
                """
                SELECT balance_after FROM flow_credit_ledger
                WHERE channel_id = ?
                ORDER BY created_at DESC, rowid DESC
                LIMIT 1;
                """,
                (channel_id,),
            ).fetchone()
            if row:
                return float(row["balance_after"])
            return 0.0

    def record_credit_transaction(
        self, data: FlowCreditTransactionCreate
    ) -> FlowCreditTransactionModel:
        """Record a manual Google Flow credit ledger transaction and compute balance.

        Args:
            data: FlowCreditTransactionCreate payload.

        Returns:
            FlowCreditTransactionModel representing the committed record.
        """
        tx_id = data.id or f"crd_{uuid.uuid4().hex[:10]}"
        now_utc = datetime.now(timezone.utc).isoformat()

        with self.db.transaction() as conn:
            # Fetch previous balance within transaction lock
            prev_row = conn.execute(
                """
                SELECT balance_after FROM flow_credit_ledger
                WHERE channel_id = ?
                ORDER BY created_at DESC, rowid DESC
                LIMIT 1;
                """,
                (data.channel_id,),
            ).fetchone()
            prev_balance = float(prev_row["balance_after"]) if prev_row else 0.0
            new_balance = round(prev_balance + data.credits_amount, 4)

            conn.execute(
                """
                INSERT INTO flow_credit_ledger (
                    id, channel_id, project_id, transaction_type,
                    credits_amount, balance_after, task_id, operator_note,
                    source, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    tx_id,
                    data.channel_id,
                    data.project_id,
                    data.transaction_type,
                    data.credits_amount,
                    new_balance,
                    data.task_id,
                    data.operator_note,
                    data.source or "manual_user_entry",
                    now_utc,
                ),
            )

        logger.info(
            f"Logged Flow credit transaction '{tx_id}' for channel '{data.channel_id}': "
            f"delta={data.credits_amount:+.2f}, balance_after={new_balance:.2f} (type={data.transaction_type})."
        )
        return self.get_credit_transaction(tx_id)

    def get_credit_transaction(self, tx_id: str) -> Optional[FlowCreditTransactionModel]:
        """Fetch a single credit ledger transaction by ID."""
        with self.db.connection() as conn:
            row = conn.execute(
                "SELECT * FROM flow_credit_ledger WHERE id = ?;", (tx_id,)
            ).fetchone()
            if row:
                return FlowCreditTransactionModel.from_row(row)
            return None

    def list_credit_transactions(
        self, channel_id: str, limit: int = 100
    ) -> List[FlowCreditTransactionModel]:
        """List recent credit ledger transactions for a channel."""
        with self.db.connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM flow_credit_ledger
                WHERE channel_id = ?
                ORDER BY created_at DESC, rowid DESC
                LIMIT ?;
                """,
                (channel_id, limit),
            ).fetchall()
            return [FlowCreditTransactionModel.from_row(r) for r in rows]

    # -------------------------------------------------------------------------
    # Production Cost Configurations
    # -------------------------------------------------------------------------
    def get_cost_config(self, channel_id: str) -> ProductionCostConfigModel:
        """Get or initialize the production cost configuration for a channel."""
        with self.db.connection() as conn:
            row = conn.execute(
                "SELECT * FROM production_cost_configurations WHERE channel_id = ?;",
                (channel_id,),
            ).fetchone()
            if row:
                return ProductionCostConfigModel.from_row(row)

        # Initialize default config if not existing
        config_id = f"cfg_{uuid.uuid4().hex[:10]}"
        now_utc = datetime.now(timezone.utc).isoformat()
        with self.db.transaction() as conn:
            chan_check = conn.execute("SELECT 1 FROM channels WHERE id = ?;", (channel_id,)).fetchone()
            if not chan_check:
                # Return in-memory default if channel is uncommitted or external
                return ProductionCostConfigModel(
                    id=config_id,
                    channel_id=channel_id,
                    cost_per_render_second=0.0,
                    cost_per_storage_gb_month=0.0,
                    cost_per_api_take=0.0,
                    currency="USD",
                    updated_at=now_utc,
                )

            conn.execute(
                """
                INSERT OR IGNORE INTO production_cost_configurations (
                    id, channel_id, cost_per_render_second, cost_per_storage_gb_month,
                    cost_per_api_take, currency, updated_at
                ) VALUES (?, ?, 0.0, 0.0, 0.0, 'USD', ?);
                """,
                (config_id, channel_id, now_utc),
            )

        with self.db.connection() as conn:
            row = conn.execute(
                "SELECT * FROM production_cost_configurations WHERE channel_id = ?;",
                (channel_id,),
            ).fetchone()
            return ProductionCostConfigModel.from_row(row)

    def set_cost_config(
        self, channel_id: str, updates: ProductionCostConfigUpdate
    ) -> ProductionCostConfigModel:
        """Update production cost unit rates for a channel."""
        current = self.get_cost_config(channel_id)
        now_utc = datetime.now(timezone.utc).isoformat()

        new_render = updates.cost_per_render_second if updates.cost_per_render_second is not None else current.cost_per_render_second
        new_storage = updates.cost_per_storage_gb_month if updates.cost_per_storage_gb_month is not None else current.cost_per_storage_gb_month
        new_api = updates.cost_per_api_take if updates.cost_per_api_take is not None else current.cost_per_api_take
        new_currency = (updates.currency or current.currency).strip().upper()

        with self.db.transaction() as conn:
            conn.execute(
                """
                UPDATE production_cost_configurations
                SET cost_per_render_second = ?,
                    cost_per_storage_gb_month = ?,
                    cost_per_api_take = ?,
                    currency = ?,
                    updated_at = ?
                WHERE channel_id = ?;
                """,
                (new_render, new_storage, new_api, new_currency, now_utc, channel_id),
            )

        logger.info(
            f"Updated cost configuration for channel '{channel_id}': "
            f"render_sec={new_render}, storage_gb={new_storage}, api_take={new_api}, curr={new_currency}"
        )
        return self.get_cost_config(channel_id)
