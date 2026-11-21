"""Media asset repository for MAM, tracking metadata, SHA-256 deduplication, and active references."""
import json
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

from src.database.repositories.base import BaseRepository
from src.database.models import MediaAssetModel, MediaAssetCreate, MediaAssetUpdate
from src.core.constants import AssetType
from src.core.exceptions import AssetNotFoundException, ValidationException
from src.core.logger import get_logger

logger = get_logger("studio.database.asset_repository")


class AssetRepository(BaseRepository):
    """Handles media asset database records, deduplication lookup, and dependency queries."""

    def create(self, asset_data: MediaAssetCreate) -> MediaAssetModel:
        """Register a new media asset.

        Args:
            asset_data: MediaAssetCreate payload.

        Returns:
            MediaAssetModel: Persisted asset model.
        """
        now_utc = datetime.now(timezone.utc).isoformat()
        metadata_str = json.dumps(asset_data.metadata)

        sql = """
        INSERT INTO media_assets (
            id, channel_id, project_id, scene_id, character_id, file_name,
            file_path, file_sha256, file_size_bytes, mime_type, asset_type,
            asset_category, storage_provider, storage_key, license_reference,
            is_active, is_archived, metadata_json, created_at, updated_at
        ) VALUES (
            :id, :channel_id, :project_id, :scene_id, :character_id, :file_name,
            :file_path, :file_sha256, :file_size_bytes, :mime_type, :asset_type,
            :asset_category, :storage_provider, :storage_key, :license_reference,
            1, 0, :metadata_json, :created_at, :updated_at
        );
        """

        params = {
            "id": asset_data.id,
            "channel_id": asset_data.channel_id,
            "project_id": asset_data.project_id,
            "scene_id": asset_data.scene_id,
            "character_id": asset_data.character_id,
            "file_name": asset_data.file_name,
            "file_path": asset_data.file_path,
            "file_sha256": asset_data.file_sha256,
            "file_size_bytes": asset_data.file_size_bytes,
            "mime_type": asset_data.mime_type,
            "asset_type": asset_data.asset_type.value,
            "asset_category": asset_data.asset_category,
            "storage_provider": asset_data.storage_provider or "local",
            "storage_key": asset_data.storage_key or asset_data.file_path,
            "license_reference": asset_data.license_reference or "",
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
            if "primary key" in err_msg or "unique" in err_msg:
                raise ValidationException(
                    f"Asset ID '{asset_data.id}' already exists.",
                    details={"id": asset_data.id},
                ) from e
            if "foreign key" in err_msg:
                raise ValidationException(
                    f"Invalid channel or project reference creating asset: {e}",
                    details={"channel_id": asset_data.channel_id},
                ) from e
            raise ValidationException(f"Database error registering asset: {e}") from e

        created = self.get_by_id(asset_data.channel_id, asset_data.id)
        if not created:
            raise AssetNotFoundException(asset_data.id)
        return created

    def get_by_id(self, channel_id: str, asset_id: str) -> Optional[MediaAssetModel]:
        """Retrieve an asset by ID strictly scoped to a channel."""
        sql = "SELECT * FROM media_assets WHERE id = ? AND channel_id = ?;"
        with self.db_manager.connection() as conn:
            cursor = conn.cursor()
            row = cursor.execute(sql, (asset_id, channel_id)).fetchone()
            if not row:
                return None
            return MediaAssetModel.from_row(row)

    def get_by_sha256(self, channel_id: str, file_sha256: str) -> Optional[MediaAssetModel]:
        """Find an existing active asset with matching SHA-256 for deduplication within the channel."""
        sql = "SELECT * FROM media_assets WHERE channel_id = ? AND file_sha256 = ? AND is_active = 1 LIMIT 1;"
        with self.db_manager.connection() as conn:
            cursor = conn.cursor()
            row = cursor.execute(sql, (channel_id, file_sha256)).fetchone()
            if not row:
                return None
            return MediaAssetModel.from_row(row)

    def get_by_path(self, channel_id: str, file_path: str) -> Optional[MediaAssetModel]:
        """Find an asset with matching relative or normalized file_path within the channel."""
        sql = "SELECT * FROM media_assets WHERE channel_id = ? AND (file_path = ? OR file_path LIKE '%' || ?) LIMIT 1;"
        norm_path = file_path.replace("\\", "/")
        with self.db_manager.connection() as conn:
            cursor = conn.cursor()
            row = cursor.execute(sql, (channel_id, norm_path, Path(file_path).name)).fetchone()
            if not row:
                return None
            return MediaAssetModel.from_row(row)

    def get_by_storage_key(self, channel_id: str, storage_key: str) -> Optional[MediaAssetModel]:
        """Find an asset by its storage key within the channel."""
        norm_key = storage_key.replace("\\", "/")
        sql = "SELECT * FROM media_assets WHERE channel_id = ? AND storage_key = ? LIMIT 1;"
        with self.db_manager.connection() as conn:
            cursor = conn.cursor()
            row = cursor.execute(sql, (channel_id, norm_key)).fetchone()
            if not row:
                return None
            return MediaAssetModel.from_row(row)

    def list_by_channel(
        self,
        channel_id: str,
        asset_type: Optional[AssetType] = None,
        include_archived: bool = False,
    ) -> List[MediaAssetModel]:
        """List assets for a channel with optional type filtering."""
        clauses = ["channel_id = ?"]
        params: List[Any] = [channel_id]

        if not include_archived:
            clauses.append("is_archived = 0")
        if asset_type is not None:
            clauses.append("asset_type = ?")
            params.append(asset_type.value)

        sql = f"SELECT * FROM media_assets WHERE {' AND '.join(clauses)} ORDER BY created_at DESC;"

        with self.db_manager.connection() as conn:
            cursor = conn.cursor()
            rows = cursor.execute(sql, params).fetchall()
            return [MediaAssetModel.from_row(r) for r in rows]

    def list_by_project(self, channel_id: str, project_id: str) -> List[MediaAssetModel]:
        """List all assets linked to a specific project."""
        sql = "SELECT * FROM media_assets WHERE channel_id = ? AND project_id = ? ORDER BY created_at DESC;"
        with self.db_manager.connection() as conn:
            cursor = conn.cursor()
            rows = cursor.execute(sql, (channel_id, project_id)).fetchall()
            return [MediaAssetModel.from_row(r) for r in rows]

    def update(
        self,
        channel_id: str,
        asset_id: str,
        updates: MediaAssetUpdate,
    ) -> MediaAssetModel:
        """Update fields of an existing asset."""
        existing = self.get_by_id(channel_id, asset_id)
        if not existing:
            raise AssetNotFoundException(asset_id)

        update_dict = updates.model_dump(exclude_unset=True)
        if not update_dict:
            return existing

        fields: List[str] = []
        params: Dict[str, Any] = {"asset_id": asset_id, "channel_id": channel_id}

        for k, v in update_dict.items():
            if k == "metadata" and isinstance(v, dict):
                fields.append("metadata_json = :metadata_json")
                params["metadata_json"] = json.dumps(v)
            elif k in ("is_active", "is_archived") and isinstance(v, bool):
                fields.append(f"{k} = :{k}")
                params[k] = 1 if v else 0
            else:
                fields.append(f"{k} = :{k}")
                params[k] = v

        fields.append("updated_at = :updated_at")
        params["updated_at"] = datetime.now(timezone.utc).isoformat()

        sql = f"UPDATE media_assets SET {', '.join(fields)} WHERE id = :asset_id AND channel_id = :channel_id;"

        with self.db_manager.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, params)

        updated = self.get_by_id(channel_id, asset_id)
        return updated or existing

    def delete(self, channel_id: str, asset_id: str) -> bool:
        """Permanently remove asset record from database."""
        sql = "DELETE FROM media_assets WHERE id = ? AND channel_id = ?;"
        with self.db_manager.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (asset_id, channel_id))
            return cursor.rowcount > 0

    def get_active_references(self, channel_id: str, asset_id: str) -> Dict[str, List[str]]:
        """Query active references to an asset across projects, scenes, and channel branding.

        Returns:
            Dict[str, List[str]]: Dictionary mapping domain entity to list of referencing IDs.
        """
        refs: Dict[str, List[str]] = {
            "projects": [],
            "scenes": [],
            "channels": [],
        }

        with self.db_manager.connection() as conn:
            cursor = conn.cursor()

            # 1. Check direct project links
            p_rows = cursor.execute(
                "SELECT id FROM projects WHERE channel_id = ? AND id IN (SELECT project_id FROM media_assets WHERE id = ?);",
                (channel_id, asset_id),
            ).fetchall()
            for r in p_rows:
                refs["projects"].append(r["id"])

            # 2. Check scene links
            s_rows = cursor.execute(
                "SELECT id FROM scenes WHERE channel_id = ? AND id IN (SELECT scene_id FROM media_assets WHERE id = ?);",
                (channel_id, asset_id),
            ).fetchall()
            for r in s_rows:
                refs["scenes"].append(r["id"])

            # 3. Check channel branding references (watermark, font)
            c_rows = cursor.execute(
                "SELECT id FROM channels WHERE id = ? AND (watermark_path LIKE '%' || ? || '%' OR primary_font LIKE '%' || ? || '%');",
                (channel_id, asset_id, asset_id),
            ).fetchall()
            for r in c_rows:
                refs["channels"].append(r["id"])

        return refs

