"""SQLite connection and transaction manager with WAL mode and Busy Timeout."""
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Optional
from src.core.config import get_settings
from src.core.logger import get_logger
from src.core.exceptions import DatabaseLockedException, StudioException

logger = get_logger("studio.database.connection")


class DatabaseManager:
    """Manages SQLite database connections, lifecycle, and transaction integrity.

    Adheres to ADR-007:
    - Enforces Write-Ahead Logging (WAL mode)
    - Enforces busy_timeout = 10,000ms
    - Enforces foreign key constraints
    """

    def __init__(self, db_path: Optional[Path] = None, busy_timeout_ms: Optional[int] = None):
        settings = get_settings()
        self.db_path = Path(db_path) if db_path is not None else settings.db_path
        self.busy_timeout_ms = busy_timeout_ms if busy_timeout_ms is not None else settings.db_busy_timeout_ms
        self._local = threading.local()

        # Ensure parent directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def get_raw_connection(self) -> sqlite3.Connection:
        """Create a fresh raw SQLite connection with mandatory PRAGMA configurations.

        Uses isolation_level=None (autocommit mode) so PRAGMAs run outside any implicit transaction,
        while explicit transactions are cleanly managed via BEGIN/COMMIT/ROLLBACK.
        """
        try:
            conn = sqlite3.connect(
                self.db_path,
                timeout=self.busy_timeout_ms / 1000.0,
                check_same_thread=False,
                isolation_level=None,  # Autocommit mode: PRAGMAs run outside transaction
            )
            conn.row_factory = sqlite3.Row

            # Apply mandatory PRAGMAs outside any transaction
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms};")
            conn.execute("PRAGMA foreign_keys = ON;")
            conn.execute("PRAGMA synchronous = NORMAL;")

            return conn
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e).lower() or "busy" in str(e).lower():
                raise DatabaseLockedException(f"Database locked on connect: {e}") from e
            raise StudioException(f"Failed to connect to database at {self.db_path}: {e}") from e

    @contextmanager
    def connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager providing a configured SQLite connection."""
        conn = self.get_raw_connection()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager providing an atomic transaction with automatic rollback on error."""
        conn = self.get_raw_connection()
        try:
            conn.execute("BEGIN IMMEDIATE;")
            yield conn
            conn.execute("COMMIT;")
        except (sqlite3.OperationalError, sqlite3.DatabaseError) as e:
            try:
                conn.execute("ROLLBACK;")
            except Exception:
                pass
            if "database is locked" in str(e).lower() or "busy" in str(e).lower():
                raise DatabaseLockedException(f"Transaction aborted due to lock contention: {e}") from e
            raise
        except Exception:
            try:
                conn.execute("ROLLBACK;")
            except Exception:
                pass
            raise
        finally:
            conn.close()

    def get_pragmas(self) -> dict:
        """Inspect and return current PRAGMA values for auditing and verification."""
        with self.connection() as conn:
            journal_mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
            busy_timeout = conn.execute("PRAGMA busy_timeout;").fetchone()[0]
            foreign_keys = conn.execute("PRAGMA foreign_keys;").fetchone()[0]
            synchronous = conn.execute("PRAGMA synchronous;").fetchone()[0]
            return {
                "journal_mode": str(journal_mode).upper(),
                "busy_timeout": int(busy_timeout),
                "foreign_keys": int(foreign_keys) == 1,
                "synchronous": int(synchronous),
            }


# Singleton database manager accessor
_db_manager_instance: Optional[DatabaseManager] = None


def get_db_manager(db_path: Optional[Path] = None, reload: bool = False) -> DatabaseManager:
    """Retrieve or initialize the global DatabaseManager singleton."""
    global _db_manager_instance
    if _db_manager_instance is None or reload:
        _db_manager_instance = DatabaseManager(db_path=db_path)
    return _db_manager_instance
