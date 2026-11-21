"""Scoped repository base and implementations enforcing strict multi-channel logical isolation (ADR-005)."""
from typing import Optional, List, Dict, Any
from src.database.connection import DatabaseManager, get_db_manager
from src.core.exceptions import ChannelNotFoundException, ValidationException, StudioException
from src.core.logger import get_logger

logger = get_logger("studio.database.scoped_repository")


class ScopedRepository:
    """Base repository enforcing strict channel scoping on all queries and mutations.

    Prevents cross-channel data leaks by mandating an active channel_id
    and filtering every database query by that channel_id.
    """

    def __init__(self, channel_id: str, db_manager: DatabaseManager = None, verify_channel: bool = True):
        clean_id = (channel_id or "").strip()
        if not clean_id:
            raise ValidationException("channel_id is required and cannot be empty for scoped repository.")

        self.channel_id = clean_id
        self.db_manager = db_manager or get_db_manager()

        if verify_channel:
            self._verify_channel_exists()

    def _verify_channel_exists(self) -> None:
        """Ensure the channel actually exists before executing scoped operations."""
        sql = "SELECT 1 FROM channels WHERE id = ? LIMIT 1;"
        with self.db_manager.connection() as conn:
            cursor = conn.cursor()
            row = cursor.execute(sql, (self.channel_id,)).fetchone()
            if not row:
                raise ChannelNotFoundException(self.channel_id)


class ScopedProjectRepository(ScopedRepository):
    """Scoped repository for projects, enforcing channel isolation (ADR-005).

    Guarantees that Channel A can never read, modify, or list projects belonging to Channel B.
    """

    def create(self, project_id: str, title: str, aspect_ratio: str = "9:16") -> Dict[str, Any]:
        """Create a new project strictly bound to this repository's channel_id."""
        from datetime import datetime, timezone

        now_utc = datetime.now(timezone.utc).isoformat()
        sql = """
        INSERT INTO projects (id, channel_id, title, aspect_ratio, status, created_at, updated_at)
        VALUES (:id, :channel_id, :title, :aspect_ratio, 'draft', :created_at, :updated_at);
        """
        params = {
            "id": project_id,
            "channel_id": self.channel_id,
            "title": title,
            "aspect_ratio": aspect_ratio,
            "created_at": now_utc,
            "updated_at": now_utc,
        }

        with self.db_manager.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, params)

        return self.get_by_id(project_id)

    def get_by_id(self, project_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a project by ID, strictly filtered by this repository's channel_id.

        If the project exists but belongs to ANOTHER channel, this query returns None,
        preventing any cross-channel unauthorized data leakage.
        """
        sql = "SELECT * FROM projects WHERE id = :project_id AND channel_id = :channel_id;"
        with self.db_manager.connection() as conn:
            cursor = conn.cursor()
            row = cursor.execute(sql, {"project_id": project_id, "channel_id": self.channel_id}).fetchone()
            if not row:
                return None
            return dict(row)

    def list_all(self) -> List[Dict[str, Any]]:
        """List all projects strictly belonging to this repository's channel_id."""
        sql = "SELECT * FROM projects WHERE channel_id = :channel_id ORDER BY created_at DESC;"
        with self.db_manager.connection() as conn:
            cursor = conn.cursor()
            rows = cursor.execute(sql, {"channel_id": self.channel_id}).fetchall()
            return [dict(row) for row in rows]

