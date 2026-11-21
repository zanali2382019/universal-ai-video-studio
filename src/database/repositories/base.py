"""Base repository class providing common database operations."""
from src.database.connection import DatabaseManager, get_db_manager


class BaseRepository:
    """Abstract base repository holding DatabaseManager instance."""

    def __init__(self, db_manager: DatabaseManager = None):
        self.db_manager = db_manager or get_db_manager()

