"""Project repository handling persistence and logical channel isolation for video projects."""
import json
import sqlite3
from datetime import datetime, timezone
from typing import Optional, List
from pathlib import Path

from src.database.repositories.base import BaseRepository
from src.database.models import ProjectModel, ProjectCreate, ProjectUpdate
from src.core.exceptions import ProjectNotFoundException, ValidationException
from src.core.config import get_settings
from src.core.logger import get_logger

logger = get_logger("studio.database.project_repository")


class ProjectRepository(BaseRepository):
    """Manages project database operations with strict channel_id scoping."""

    def create(self, project_data: ProjectCreate) -> ProjectModel:
        """Create a new project scoped to a specific channel.

        Args:
            project_data: ProjectCreate payload.

        Returns:
            ProjectModel: Persisted project.

        Raises:
            ValidationException: If project ID already exists or channel does not exist.
        """
        now_utc = datetime.now(timezone.utc).isoformat()
        metadata_str = json.dumps(project_data.metadata)

        sql = """
        INSERT INTO projects (
            id, channel_id, title, description, aspect_ratio, status,
            target_duration_sec, is_archived, metadata_json, created_at, updated_at
        ) VALUES (
            :id, :channel_id, :title, :description, :aspect_ratio, 'draft',
            :target_duration_sec, 0, :metadata_json, :created_at, :updated_at
        );
        """

        params = {
            "id": project_data.id,
            "channel_id": project_data.channel_id,
            "title": project_data.title,
            "description": project_data.description,
            "aspect_ratio": project_data.aspect_ratio.value,
            "target_duration_sec": project_data.target_duration_sec,
            "metadata_json": metadata_str,
            "created_at": now_utc,
            "updated_at": now_utc,
        }

        try:
            with self.db_manager.transaction() as conn:
                cursor = conn.cursor()
                cursor.execute(sql, params)
        except sqlite3.IntegrityError as e:
            err_msg = str(e).lower()
            if "foreign key" in err_msg:
                raise ValidationException(
                    f"Channel '{project_data.channel_id}' does not exist.",
                    details={"field": "channel_id", "value": project_data.channel_id},
                ) from e
            if "primary key" in err_msg or "unique" in err_msg:
                raise ValidationException(
                    f"Project with ID '{project_data.id}' already exists.",
                    details={"field": "id", "value": project_data.id},
                ) from e
            raise ValidationException(f"Database integrity error creating project: {e}") from e

        # Initialize project working directories on disk
        self._initialize_project_directories(project_data.channel_id, project_data.id)

        created = self.get_by_id(project_data.channel_id, project_data.id)
        if not created:
            raise ProjectNotFoundException(project_data.id)
        return created

    def get_by_id(self, channel_id: str, project_id: str) -> Optional[ProjectModel]:
        """Retrieve a project by ID strictly scoped to a channel."""
        sql = "SELECT * FROM projects WHERE id = ? AND channel_id = ?;"
        with self.db_manager.connection() as conn:
            cursor = conn.cursor()
            row = cursor.execute(sql, (project_id, channel_id)).fetchone()
            if not row:
                return None
            return ProjectModel.from_row(row)

    def list_by_channel(
        self,
        channel_id: str,
        include_archived: bool = False,
    ) -> List[ProjectModel]:
        """List all projects for a channel."""
        if include_archived:
            sql = "SELECT * FROM projects WHERE channel_id = ? ORDER BY created_at DESC;"
            params = (channel_id,)
        else:
            sql = "SELECT * FROM projects WHERE channel_id = ? AND is_archived = 0 ORDER BY created_at DESC;"
            params = (channel_id,)

        with self.db_manager.connection() as conn:
            cursor = conn.cursor()
            rows = cursor.execute(sql, params).fetchall()
            return [ProjectModel.from_row(r) for r in rows]

    def update(
        self,
        channel_id: str,
        project_id: str,
        updates: ProjectUpdate,
    ) -> ProjectModel:
        """Update fields of an existing project scoped to a channel."""
        existing = self.get_by_id(channel_id, project_id)
        if not existing:
            raise ProjectNotFoundException(project_id)

        update_dict = updates.model_dump(exclude_unset=True)
        if not update_dict:
            return existing

        fields: List[str] = []
        params = {"project_id": project_id, "channel_id": channel_id}

        for k, v in update_dict.items():
            if k == "metadata" and isinstance(v, dict):
                fields.append("metadata_json = :metadata_json")
                params["metadata_json"] = json.dumps(v)
            elif k == "is_archived" and isinstance(v, bool):
                fields.append("is_archived = :is_archived")
                params["is_archived"] = 1 if v else 0
            elif k in ("aspect_ratio", "status") and hasattr(v, "value"):
                fields.append(f"{k} = :{k}")
                params[k] = v.value
            else:
                fields.append(f"{k} = :{k}")
                params[k] = v

        fields.append("updated_at = :updated_at")
        params["updated_at"] = datetime.now(timezone.utc).isoformat()

        sql = f"UPDATE projects SET {', '.join(fields)} WHERE id = :project_id AND channel_id = :channel_id;"

        with self.db_manager.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, params)

        updated = self.get_by_id(channel_id, project_id)
        return updated or existing

    def delete(self, channel_id: str, project_id: str) -> bool:
        """Delete a project record and its cascade dependencies in SQLite.

        Args:
            channel_id: Channel ID owner.
            project_id: Project ID to delete.

        Returns:
            bool: True if row was deleted.
        """
        sql = "DELETE FROM projects WHERE id = ? AND channel_id = ?;"
        with self.db_manager.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (project_id, channel_id))
            deleted = cursor.rowcount > 0

        if deleted:
            self._cleanup_project_directories(channel_id, project_id)
        return deleted

    def exists(self, channel_id: str, project_id: str) -> bool:
        """Check if a project exists within a channel."""
        sql = "SELECT 1 FROM projects WHERE id = ? AND channel_id = ? LIMIT 1;"
        with self.db_manager.connection() as conn:
            cursor = conn.cursor()
            row = cursor.execute(sql, (project_id, channel_id)).fetchone()
            return row is not None

    def _initialize_project_directories(self, channel_id: str, project_id: str) -> Path:
        """Initialize workspace directories for the project under channels/<channel_id>/projects/<project_id>/."""
        settings = get_settings()
        proj_dir = settings.channels_root / channel_id / "projects" / project_id
        (proj_dir / "working").mkdir(parents=True, exist_ok=True)
        (proj_dir / "output").mkdir(parents=True, exist_ok=True)
        return proj_dir

    def _cleanup_project_directories(self, channel_id: str, project_id: str) -> None:
        """Remove project-specific working and output directories."""
        import shutil
        settings = get_settings()
        proj_dir = settings.channels_root / channel_id / "projects" / project_id
        if proj_dir.exists():
            shutil.rmtree(proj_dir, ignore_errors=True)

