"""Repository for Export Publishing Packages (Task 3.16)."""
import json
import uuid
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

from src.database.connection import DatabaseManager
from src.database.repositories.base import BaseRepository
from src.database.models import ExportPackageModel, ExportPackageCreate
from src.core.logger import get_logger

logger = get_logger("studio.database.export_repository")


class ExportRepository(BaseRepository):
    """Database repository for finalized export packages and manifests."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None) -> None:
        super().__init__(db_manager)

    @property
    def db(self) -> DatabaseManager:
        return self.db_manager

    def create_package(self, data: ExportPackageCreate) -> ExportPackageModel:
        """Insert and persist a new export package record."""
        package_id = data.id or f"pkg_{uuid.uuid4().hex[:12]}"
        now_utc = datetime.now(timezone.utc).isoformat()

        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO export_packages (
                    id, channel_id, project_id, platform, profile_name,
                    version, status, video_path, video_sha256,
                    thumbnail_path, thumbnail_sha256, manifest_path,
                    zip_path, zip_sha256, license_preflight_json,
                    quality_gates_json, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    package_id,
                    data.channel_id,
                    data.project_id,
                    data.platform,
                    data.profile_name,
                    data.version,
                    data.status,
                    data.video_path,
                    data.video_sha256,
                    data.thumbnail_path,
                    data.thumbnail_sha256,
                    data.manifest_path,
                    data.zip_path,
                    data.zip_sha256,
                    json.dumps(data.license_preflight or {}),
                    json.dumps(data.quality_gates or {}),
                    json.dumps(data.metadata or {}),
                    now_utc,
                    now_utc,
                ),
            )

        logger.info(f"Created export package record '{package_id}' for project '{data.project_id}'.")
        return self.get_by_id(package_id)

    def get_by_id(self, package_id: str) -> Optional[ExportPackageModel]:
        """Query export package by ID."""
        clean_id = (package_id or "").strip()
        with self.db.connection() as conn:
            cursor = conn.cursor()
            row = cursor.execute("SELECT * FROM export_packages WHERE id = ?;", (clean_id,)).fetchone()
            if not row:
                return None
            return ExportPackageModel.from_row(row)

    def list_by_project(self, project_id: str) -> List[ExportPackageModel]:
        """List export packages for a specific project."""
        clean_pid = (project_id or "").strip()
        with self.db.connection() as conn:
            cursor = conn.cursor()
            rows = cursor.execute(
                "SELECT * FROM export_packages WHERE project_id = ? ORDER BY created_at DESC;",
                (clean_pid,),
            ).fetchall()
            return [ExportPackageModel.from_row(r) for r in rows]

    def list_by_channel(self, channel_id: Optional[str] = None) -> List[ExportPackageModel]:
        """List export packages optionally filtered by channel."""
        clean_cid = (channel_id or "").strip()
        with self.db.connection() as conn:
            cursor = conn.cursor()
            if clean_cid:
                rows = cursor.execute(
                    "SELECT * FROM export_packages WHERE channel_id = ? ORDER BY created_at DESC;",
                    (clean_cid,),
                ).fetchall()
            else:
                rows = cursor.execute(
                    "SELECT * FROM export_packages ORDER BY created_at DESC;"
                ).fetchall()
            return [ExportPackageModel.from_row(r) for r in rows]

    def update_package(
        self,
        package_id: str,
        status: Optional[str] = None,
        video_path: Optional[str] = None,
        video_sha256: Optional[str] = None,
        thumbnail_path: Optional[str] = None,
        thumbnail_sha256: Optional[str] = None,
        manifest_path: Optional[str] = None,
        zip_path: Optional[str] = None,
        zip_sha256: Optional[str] = None,
        license_preflight: Optional[Dict[str, Any]] = None,
        quality_gates: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[ExportPackageModel]:
        """Update package paths, checksums, reports, and status."""
        clean_id = (package_id or "").strip()
        now_utc = datetime.now(timezone.utc).isoformat()

        updates = ["updated_at = ?"]
        params = [now_utc]

        if status is not None:
            updates.append("status = ?")
            params.append(status)
        if video_path is not None:
            updates.append("video_path = ?")
            params.append(video_path)
        if video_sha256 is not None:
            updates.append("video_sha256 = ?")
            params.append(video_sha256)
        if thumbnail_path is not None:
            updates.append("thumbnail_path = ?")
            params.append(thumbnail_path)
        if thumbnail_sha256 is not None:
            updates.append("thumbnail_sha256 = ?")
            params.append(thumbnail_sha256)
        if manifest_path is not None:
            updates.append("manifest_path = ?")
            params.append(manifest_path)
        if zip_path is not None:
            updates.append("zip_path = ?")
            params.append(zip_path)
        if zip_sha256 is not None:
            updates.append("zip_sha256 = ?")
            params.append(zip_sha256)
        if license_preflight is not None:
            updates.append("license_preflight_json = ?")
            params.append(json.dumps(license_preflight))
        if quality_gates is not None:
            updates.append("quality_gates_json = ?")
            params.append(json.dumps(quality_gates))
        if metadata is not None:
            updates.append("metadata_json = ?")
            params.append(json.dumps(metadata))

        params.append(clean_id)
        query = f"UPDATE export_packages SET {', '.join(updates)} WHERE id = ?;"

        with self.db.transaction() as conn:
            conn.execute(query, tuple(params))

        return self.get_by_id(clean_id)

    def delete_package(self, package_id: str) -> bool:
        """Delete an export package record by ID."""
        clean_id = (package_id or "").strip()
        with self.db.transaction() as conn:
            cursor = conn.execute("DELETE FROM export_packages WHERE id = ?;", (clean_id,))
            return cursor.rowcount > 0
