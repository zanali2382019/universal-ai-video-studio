"""Repository layer package for structured data access."""
from src.database.repositories.base import BaseRepository
from src.database.repositories.channel_repository import ChannelRepository
from src.database.repositories.scoped_repository import ScopedRepository
from src.database.repositories.intelligence_repository import IntelligenceRepository

__all__ = [
    "BaseRepository",
    "ChannelRepository",
    "ScopedRepository",
    "IntelligenceRepository",
]

