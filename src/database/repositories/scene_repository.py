"""Scene repository for CRUD operations and sequence management within projects."""
import json
import sqlite3
from datetime import datetime, timezone
from typing import Optional, List
from src.database.repositories.base import BaseRepository
from src.database.models import SceneModel, SceneCreate, SceneUpdate
from src.core.exceptions import SceneNotFoundException, ValidationException
from src.core.logger import get_logger

logger = get_logger("studio.database.scene_repository")


class SceneRepository(BaseRepository):
    """Handles scene persistence, ordering, and channel/project scoping."""

    def create(self, scene_data: SceneCreate) -> SceneModel:
        """Create a new scene within a project and channel.

        Args:
            scene_data: SceneCreate payload.

        Returns:
            SceneModel: Persisted scene.

        Raises:
            ValidationException: If sequence number is taken or foreign keys fail.
        """
        now_utc = datetime.now(timezone.utc).isoformat()
        metadata_str = json.dumps(scene_data.metadata)

        sql = """
        INSERT INTO scenes (
            id, project_id, channel_id, sequence_number, title, narration_text,
            visual_prompt, duration_seconds, scene_type, status, metadata_json,
            created_at, updated_at
        ) VALUES (
            :id, :project_id, :channel_id, :sequence_number, :title, :narration_text,
            :visual_prompt, :duration_seconds, :scene_type, 'draft', :metadata_json,
            :created_at, :updated_at
        );
        """

        params = {
            "id": scene_data.id,
            "project_id": scene_data.project_id,
            "channel_id": scene_data.channel_id,
            "sequence_number": scene_data.sequence_number,
            "title": scene_data.title,
            "narration_text": scene_data.narration_text,
            "visual_prompt": scene_data.visual_prompt,
            "duration_seconds": scene_data.duration_seconds,
            "scene_type": scene_data.scene_type.value,
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
                    f"Invalid project '{scene_data.project_id}' or channel '{scene_data.channel_id}'.",
                    details={"project_id": scene_data.project_id, "channel_id": scene_data.channel_id},
                ) from e
            if "sequence_number" in err_msg or "unique" in err_msg:
                raise ValidationException(
                    f"Scene sequence number {scene_data.sequence_number} already exists in project '{scene_data.project_id}'.",
                    details={"sequence_number": scene_data.sequence_number},
                ) from e
            raise ValidationException(f"Database error creating scene: {e}") from e

        created = self.get_by_id(scene_data.channel_id, scene_data.id)
        if not created:
            raise SceneNotFoundException(scene_data.id)
        return created

    def get_by_id(self, channel_id: str, scene_id: str) -> Optional[SceneModel]:
        """Retrieve a scene by ID strictly scoped to a channel."""
        sql = "SELECT * FROM scenes WHERE id = ? AND channel_id = ?;"
        with self.db_manager.connection() as conn:
            cursor = conn.cursor()
            row = cursor.execute(sql, (scene_id, channel_id)).fetchone()
            if not row:
                return None
            return SceneModel.from_row(row)

    def list_by_project(self, channel_id: str, project_id: str) -> List[SceneModel]:
        """List all scenes for a project ordered by sequence_number."""
        sql = "SELECT * FROM scenes WHERE channel_id = ? AND project_id = ? ORDER BY sequence_number ASC;"
        with self.db_manager.connection() as conn:
            cursor = conn.cursor()
            rows = cursor.execute(sql, (channel_id, project_id)).fetchall()
            return [SceneModel.from_row(r) for r in rows]

    def update(
        self,
        channel_id: str,
        scene_id: str,
        updates: SceneUpdate,
    ) -> SceneModel:
        """Update fields of an existing scene."""
        existing = self.get_by_id(channel_id, scene_id)
        if not existing:
            raise SceneNotFoundException(scene_id)

        update_dict = updates.model_dump(exclude_unset=True)
        if not update_dict:
            return existing

        fields: List[str] = []
        params = {"scene_id": scene_id, "channel_id": channel_id}

        for k, v in update_dict.items():
            if k == "metadata" and isinstance(v, dict):
                fields.append("metadata_json = :metadata_json")
                params["metadata_json"] = json.dumps(v)
            elif k in ("scene_type", "status") and hasattr(v, "value"):
                fields.append(f"{k} = :{k}")
                params[k] = v.value
            else:
                fields.append(f"{k} = :{k}")
                params[k] = v

        fields.append("updated_at = :updated_at")
        params["updated_at"] = datetime.now(timezone.utc).isoformat()

        sql = f"UPDATE scenes SET {', '.join(fields)} WHERE id = :scene_id AND channel_id = :channel_id;"

        try:
            with self.db_manager.transaction() as conn:
                cursor = conn.cursor()
                cursor.execute(sql, params)
        except sqlite3.IntegrityError as e:
            if "sequence_number" in str(e).lower() or "unique" in str(e).lower():
                raise ValidationException(
                    f"Scene sequence number conflict: {e}",
                    details={"scene_id": scene_id},
                ) from e
            raise ValidationException(f"Failed to update scene: {e}") from e

        updated = self.get_by_id(channel_id, scene_id)
        return updated or existing

    def reorder_scenes(
        self,
        channel_id: str,
        project_id: str,
        scene_ids_in_order: List[str],
    ) -> List[SceneModel]:
        """Atomically reorder scenes within a project to new sequence numbers 1..N.

        Args:
            channel_id: Channel ID.
            project_id: Project ID.
            scene_ids_in_order: List of scene IDs in desired order.

        Returns:
            List[SceneModel]: Updated scenes ordered by sequence_number.
        """
        now_utc = datetime.now(timezone.utc).isoformat()
        with self.db_manager.transaction() as conn:
            cursor = conn.cursor()
            # Step 1: Temporarily negate sequence numbers to avoid unique constraint violations
            for idx, s_id in enumerate(scene_ids_in_order, start=1):
                cursor.execute(
                    "UPDATE scenes SET sequence_number = ? WHERE id = ? AND channel_id = ? AND project_id = ?;",
                    (-idx, s_id, channel_id, project_id),
                )
            # Step 2: Set final positive sequence numbers
            for idx, s_id in enumerate(scene_ids_in_order, start=1):
                cursor.execute(
                    "UPDATE scenes SET sequence_number = ?, updated_at = ? WHERE id = ? AND channel_id = ? AND project_id = ?;",
                    (idx, now_utc, s_id, channel_id, project_id),
                )

        return self.list_by_project(channel_id, project_id)

    def delete(self, channel_id: str, scene_id: str) -> bool:
        """Delete a scene by ID."""
        sql = "DELETE FROM scenes WHERE id = ? AND channel_id = ?;"
        with self.db_manager.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (scene_id, channel_id))
            return cursor.rowcount > 0

    def exists(self, channel_id: str, scene_id: str) -> bool:
        """Check if a scene exists."""
        sql = "SELECT 1 FROM scenes WHERE id = ? AND channel_id = ? LIMIT 1;"
        with self.db_manager.connection() as conn:
            cursor = conn.cursor()
            row = cursor.execute(sql, (scene_id, channel_id)).fetchone()
            return row is not None

