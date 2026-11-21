"""Database layer package exporting models, connection manager, migrations, and repositories."""
from src.database.connection import DatabaseManager, get_db_manager
from src.database.migrations import MigrationManager
from src.database.models import ChannelModel, ChannelCreate, ChannelUpdate
from src.database.repositories.base import BaseRepository
from src.database.repositories.channel_repository import ChannelRepository
from src.database.repositories.scoped_repository import ScopedRepository, ScopedProjectRepository

__all__ = [
    "DatabaseManager",
    "get_db_manager",
    "MigrationManager",
    "ChannelModel",
    "ChannelCreate",
    "ChannelUpdate",
    "BaseRepository",
    "ChannelRepository",
    "ScopedRepository",
    "ScopedProjectRepository",
]
