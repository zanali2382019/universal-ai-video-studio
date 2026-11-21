"""Channel repository for CRUD operations and channel filesystem initialization."""
import json
import sqlite3
import shutil
from datetime import datetime, timezone
from typing import Optional, List
from pathlib import Path

from src.database.repositories.base import BaseRepository
from src.database.models import ChannelModel, ChannelCreate, ChannelUpdate
from src.core.exceptions import ChannelNotFoundException, ValidationException, StudioException
from src.core.config import get_settings
from src.core.logger import get_logger

logger = get_logger("studio.database.channel_repository")


class ChannelRepository(BaseRepository):
    """Handles channel persistence, querying, and workspace directory initialization."""

    def create(self, channel_data: ChannelCreate) -> ChannelModel:
        """Create a new channel in database and initialize its local workspace hierarchy.

        Args:
            channel_data: Input validated payload.

        Returns:
            ChannelModel: The persisted channel entity.

        Raises:
            ValidationException: If channel ID or handle already exists.
        """
        now_utc = datetime.now(timezone.utc).isoformat()
        locales_json = json.dumps(channel_data.supported_locales)

        sql = """
        INSERT INTO channels (
            id, name, handle, niche, default_language, supported_locales,
            coppa_default, synthetic_media_declaration, watermark_path,
            watermark_position, primary_font, primary_color, default_aspect_ratio,
            default_template_id, default_tts_voice_id, default_generation_mode,
            is_active, created_at, updated_at
        ) VALUES (
            :id, :name, :handle, :niche, :default_language, :supported_locales,
            :coppa_default, :synthetic_media_declaration, :watermark_path,
            :watermark_position, :primary_font, :primary_color, :default_aspect_ratio,
            :default_template_id, :default_tts_voice_id, :default_generation_mode,
            1, :created_at, :updated_at
        );
        """

        params = {
            "id": channel_data.id,
            "name": channel_data.name,
            "handle": channel_data.handle,
            "niche": channel_data.niche,
            "default_language": channel_data.default_language,
            "supported_locales": locales_json,
            "coppa_default": channel_data.coppa_default,
            "synthetic_media_declaration": 1 if channel_data.synthetic_media_declaration else 0,
            "watermark_path": channel_data.watermark_path,
            "watermark_position": channel_data.watermark_position,
            "primary_font": channel_data.primary_font,
            "primary_color": channel_data.primary_color,
            "default_aspect_ratio": channel_data.default_aspect_ratio.value,
            "default_template_id": channel_data.default_template_id,
            "default_tts_voice_id": channel_data.default_tts_voice_id,
            "default_generation_mode": channel_data.default_generation_mode.value,
            "created_at": now_utc,
            "updated_at": now_utc,
        }

        try:
            with self.db_manager.transaction() as conn:
                cursor = conn.cursor()
                cursor.execute(sql, params)
        except sqlite3.IntegrityError as e:
            err_msg = str(e).lower()
            if "channels.id" in err_msg or "primary key" in err_msg:
                raise ValidationException(
                    f"Channel with ID '{channel_data.id}' already exists.",
                    details={"field": "id", "value": channel_data.id},
                ) from e
            if "channels.handle" in err_msg or "unique" in err_msg:
                raise ValidationException(
                    f"Channel handle '{channel_data.handle}' is already registered.",
                    details={"field": "handle", "value": channel_data.handle},
                ) from e
            raise ValidationException(f"Database integrity violation creating channel: {e}") from e

        # Initialize physical workspace directories for the channel (ADR-005)
        try:
            self._initialize_channel_workspace(channel_data.id)
        except Exception as fs_err:
            logger.error(
                f"Failed to initialize workspace directories for channel '{channel_data.id}'. "
                f"Executing compensating database rollback: {fs_err}"
            )
            try:
                with self.db_manager.transaction() as rollback_conn:
                    rollback_conn.execute("DELETE FROM channels WHERE id = ?;", (channel_data.id,))
            except Exception as rollback_err:
                logger.error(f"Compensating DB delete failed for channel '{channel_data.id}': {rollback_err}")
            
            self._cleanup_channel_workspace(channel_data.id)
            raise StudioException(
                f"Failed to initialize workspace directories for channel '{channel_data.id}': {fs_err}",
                error_code="FILESYSTEM_INITIALIZATION_ERROR",
                details={"channel_id": channel_data.id, "error": str(fs_err)},
            ) from fs_err

        created = self.get_by_id(channel_data.id)
        if not created:
            raise ChannelNotFoundException(channel_data.id)
        logger.info(f"Successfully created channel '{created.id}' ({created.handle})")
        return created

    def get_by_id(self, channel_id: str) -> Optional[ChannelModel]:
        """Retrieve a channel by its unique ID."""
        sql = "SELECT * FROM channels WHERE id = ?;"
        with self.db_manager.connection() as conn:
            cursor = conn.cursor()
            row = cursor.execute(sql, (channel_id,)).fetchone()
            if not row:
                return None
            return ChannelModel.from_row(row)

    def get_by_handle(self, handle: str) -> Optional[ChannelModel]:
        """Retrieve a channel by its YouTube handle."""
        clean_handle = handle.strip()
        if not clean_handle.startswith("@"):
            clean_handle = f"@{clean_handle}"

        sql = "SELECT * FROM channels WHERE handle = ?;"
        with self.db_manager.connection() as conn:
            cursor = conn.cursor()
            row = cursor.execute(sql, (clean_handle,)).fetchone()
            if not row:
                return None
            return ChannelModel.from_row(row)

    def list_all(self, include_inactive: bool = False) -> List[ChannelModel]:
        """List all channels."""
        if include_inactive:
            sql = "SELECT * FROM channels ORDER BY name ASC;"
        else:
            sql = "SELECT * FROM channels WHERE is_active = 1 ORDER BY name ASC;"

        with self.db_manager.connection() as conn:
            cursor = conn.cursor()
            rows = cursor.execute(sql).fetchall()
            return [ChannelModel.from_row(row) for row in rows]

    def update(self, channel_id: str, updates: ChannelUpdate) -> ChannelModel:
        """Update fields of an existing channel."""
        existing = self.get_by_id(channel_id)
        if not existing:
            raise ChannelNotFoundException(channel_id)

        update_dict = updates.model_dump(exclude_unset=True)
        if not update_dict:
            return existing

        fields: List[str] = []
        params = {"channel_id": channel_id}

        for k, v in update_dict.items():
            if k == "supported_locales" and isinstance(v, list):
                fields.append("supported_locales = :supported_locales")
                params["supported_locales"] = json.dumps(v)
            elif k == "synthetic_media_declaration" and isinstance(v, bool):
                fields.append("synthetic_media_declaration = :synthetic_media_declaration")
                params["synthetic_media_declaration"] = 1 if v else 0
            elif k == "is_active" and isinstance(v, bool):
                fields.append("is_active = :is_active")
                params["is_active"] = 1 if v else 0
            elif k in ("default_aspect_ratio", "default_generation_mode") and hasattr(v, "value"):
                fields.append(f"{k} = :{k}")
                params[k] = v.value
            else:
                fields.append(f"{k} = :{k}")
                params[k] = v

        fields.append("updated_at = :updated_at")
        params["updated_at"] = datetime.now(timezone.utc).isoformat()

        sql = f"UPDATE channels SET {', '.join(fields)} WHERE id = :channel_id;"

        try:
            with self.db_manager.transaction() as conn:
                cursor = conn.cursor()
                cursor.execute(sql, params)
        except sqlite3.IntegrityError as e:
            if "channels.handle" in str(e).lower():
                raise ValidationException(
                    f"Channel handle update conflicts with existing handle: {e}",
                    details={"field": "handle"},
                ) from e
            raise ValidationException(f"Channel update failed: {e}") from e

        updated = self.get_by_id(channel_id)
        logger.info(f"Updated channel '{channel_id}' successfully.")
        return updated

    def exists(self, channel_id: str) -> bool:
        """Check if a channel ID exists in the database."""
        sql = "SELECT 1 FROM channels WHERE id = ? LIMIT 1;"
        with self.db_manager.connection() as conn:
            cursor = conn.cursor()
            row = cursor.execute(sql, (channel_id,)).fetchone()
            return row is not None

    def delete(self, channel_id: str) -> bool:
        """Delete a channel by its unique ID.

        Args:
            channel_id: Channel ID to delete.

        Returns:
            bool: True if a channel was deleted, False if it was not found.
        """
        sql = "DELETE FROM channels WHERE id = ?;"
        with self.db_manager.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (channel_id,))
            deleted = cursor.rowcount > 0
            if deleted:
                logger.info(f"Deleted channel '{channel_id}' from database.")
            return deleted

    def _initialize_channel_workspace(self, channel_id: str) -> Path:
        """Create the channel's isolated directory tree on disk according to ADR-005 and unified spec."""
        settings = get_settings()
        channel_dir = settings.channels_root / channel_id
        directories = [
            channel_dir / "config",
            channel_dir / "branding",
            channel_dir / "templates",
            channel_dir / "characters",
            channel_dir / "media_vault" / "originals" / "images",
            channel_dir / "media_vault" / "originals" / "audio",
            channel_dir / "media_vault" / "originals" / "video_clips",
            channel_dir / "media_vault" / "originals" / "fonts",
            channel_dir / "media_vault" / "proxies",
            channel_dir / "media_vault" / "licenses" / "proofs",
            channel_dir / "media_vault" / "cache",
            channel_dir / "projects",
            channel_dir / "exports" / "videos",
            channel_dir / "exports" / "audio_masters",
            channel_dir / "exports" / "thumbnails",
            channel_dir / "exports" / "packages",
            channel_dir / "audit",
        ]
        for d in directories:
            d.mkdir(parents=True, exist_ok=True)
        return channel_dir

    def _cleanup_channel_workspace(self, channel_id: str) -> bool:
        """Safely delete channel directory tree on disk."""
        settings = get_settings()
        channel_dir = settings.channels_root / channel_id
        if channel_dir.exists():
            shutil.rmtree(channel_dir, ignore_errors=True)
            logger.info(f"Cleaned up physical workspace for channel '{channel_id}'.")
            return True
        return False

