"""Tests for SQLite connection manager, WAL mode, pragmas, transactions, and migrations."""
import unittest
import tempfile
import sqlite3
import gc
from pathlib import Path
from src.database.connection import DatabaseManager
from src.database.migrations import MigrationManager


class TestDatabaseConnection(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = Path(self.temp_dir.name) / "test_studio.db"
        self.db_manager = DatabaseManager(db_path=self.db_path, busy_timeout_ms=10000)

    def tearDown(self):
        gc.collect()
        self.temp_dir.cleanup()

    def test_non_existent_db_creation_at_startup(self):
        """Verify connecting to a non-existent DB creates it and parent directories."""
        self.assertFalse(self.db_path.exists())
        with self.db_manager.connection() as conn:
            result = conn.execute("SELECT 1;").fetchone()[0]
            self.assertEqual(result, 1)
        self.assertTrue(self.db_path.exists())

    def test_pragmas_wal_and_busy_timeout(self):
        """Verify mandatory PRAGMAs: WAL mode, busy_timeout 10000, foreign_keys ON (ADR-007)."""
        pragmas = self.db_manager.get_pragmas()
        self.assertEqual(pragmas["journal_mode"], "WAL")
        self.assertEqual(pragmas["busy_timeout"], 10000)
        self.assertTrue(pragmas["foreign_keys"])

    def test_migrations_execution_and_idempotency(self):
        """Verify migrations run cleanly and running them again is a safe no-op."""
        migration_manager = MigrationManager(self.db_manager)
        
        # First run: applies migration 1
        applied_first = migration_manager.run_migrations()
        self.assertIn(1, applied_first)

        # Verify tables created
        with self.db_manager.connection() as conn:
            tables = [
                row[0]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()
            ]
            self.assertIn("schema_migrations", tables)
            self.assertIn("channels", tables)
            self.assertIn("projects", tables)

        # Second run: should be idempotent (no new migrations)
        applied_second = migration_manager.run_migrations()
        self.assertEqual(applied_second, [])

    def test_transaction_integrity_and_rollback(self):
        """Verify atomic transactions rollback completely when an exception occurs."""
        migration_manager = MigrationManager(self.db_manager)
        migration_manager.run_migrations()

        # Insert a valid channel first
        with self.db_manager.transaction() as conn:
            conn.execute(
                """
                INSERT INTO channels (id, name, handle, niche, created_at, updated_at)
                VALUES ('ch_tx_test', 'TX Test Channel', '@tx_test', 'testing', 'now', 'now');
                """
            )

        # Attempt transaction that fails halfway: second insert violates unique handle constraint
        with self.assertRaises(sqlite3.IntegrityError):
            with self.db_manager.transaction() as conn:
                conn.execute(
                    """
                    INSERT INTO channels (id, name, handle, niche, created_at, updated_at)
                    VALUES ('ch_rollback_1', 'Rollback 1', '@rollback_unique', 'testing', 'now', 'now');
                    """
                )
                # This must fail due to duplicate handle '@tx_test'
                conn.execute(
                    """
                    INSERT INTO channels (id, name, handle, niche, created_at, updated_at)
                    VALUES ('ch_rollback_2', 'Rollback 2', '@tx_test', 'testing', 'now', 'now');
                    """
                )

        # Verify ch_rollback_1 was rolled back completely and does not exist in DB
        with self.db_manager.connection() as conn:
            row = conn.execute("SELECT * FROM channels WHERE id = 'ch_rollback_1';").fetchone()
            self.assertIsNone(row)


if __name__ == "__main__":
    unittest.main()
