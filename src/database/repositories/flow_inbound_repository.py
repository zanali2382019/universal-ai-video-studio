"""Repository for Google Flow Inbound Scene Takes and Ingestion Records (Task 3.13)."""
import json
import sqlite3
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

from src.database.connection import DatabaseManager
from src.database.repositories.base import BaseRepository
from src.core.logger import get_logger

logger = get_logger("studio.database.flow_inbound_repository")


class FlowInboundRepository(BaseRepository):
    """Database repository for Google Flow takes, versions, and inbound history."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None) -> None:
        super().__init__(db_manager)

    @property
    def db(self) -> DatabaseManager:
        """Alias for self.db_manager."""
        return self.db_manager

    def create_take(
        self,
        take_id: str,
        channel_id: str,
        project_id: str,
        scene_id: str,
        prompt_version: int,
        take_number: int,
        asset_id: str,
        asset_role: str,
        file_sha256: str,
        source_file_name: str,
        storage_key: str,
        duration_sec: Optional[float] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
        fps: Optional[float] = None,
        is_active: bool = True,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Insert a scene take/generation record.
        
        If is_active is True, automatically deactivates prior takes for this (project_id, scene_id, asset_role).
        """
        now_utc = datetime.now(timezone.utc).isoformat()
        metadata_json = json.dumps(metadata or {})

        with self.db.transaction() as conn:
            if is_active:
                conn.execute(
                    """
                    UPDATE flow_scene_takes
                    SET is_active = 0
                    WHERE project_id = ? AND scene_id = ? AND asset_role = ?;
                    """,
                    (project_id, scene_id, asset_role),
                )

            conn.execute(
                """
                INSERT INTO flow_scene_takes (
                    id, channel_id, project_id, scene_id, prompt_version,
                    take_number, asset_id, asset_role, file_sha256, source_file_name,
                    storage_key, duration_sec, width, height, fps,
                    is_active, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    take_id,
                    channel_id,
                    project_id,
                    scene_id,
                    prompt_version,
                    take_number,
                    asset_id,
                    asset_role,
                    file_sha256,
                    source_file_name,
                    storage_key,
                    duration_sec,
                    width,
                    height,
                    fps,
                    1 if is_active else 0,
                    metadata_json,
                    now_utc,
                ),
            )

        logger.info(
            f"Saved Flow scene take {take_id} (take #{take_number}, active={is_active}) "
            f"for scene '{scene_id}' in project '{project_id}'."
        )
        return self.get_take(take_id) or {}

    def get_take(self, take_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a specific take record by its ID."""
        with self.db.connection() as conn:
            row = conn.execute(
                "SELECT * FROM flow_scene_takes WHERE id = ?;", (take_id,)
            ).fetchone()
            return self._row_to_dict(row) if row else None

    def get_take_by_number(
        self, project_id: str, scene_id: str, take_number: int, asset_role: str = "video"
    ) -> Optional[Dict[str, Any]]:
        """Fetch a specific take by project, scene, take number, and role."""
        with self.db.connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM flow_scene_takes
                WHERE project_id = ? AND scene_id = ? AND take_number = ? AND asset_role = ?;
                """,
                (project_id, scene_id, take_number, asset_role),
            ).fetchone()
            return self._row_to_dict(row) if row else None

    def get_active_take(
        self, project_id: str, scene_id: str, asset_role: str = "video"
    ) -> Optional[Dict[str, Any]]:
        """Fetch the currently active take for a scene."""
        with self.db.connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM flow_scene_takes
                WHERE project_id = ? AND scene_id = ? AND asset_role = ? AND is_active = 1
                ORDER BY take_number DESC LIMIT 1;
                """,
                (project_id, scene_id, asset_role),
            ).fetchone()
            return self._row_to_dict(row) if row else None

    def find_take_by_sha256(self, channel_id: str, file_sha256: str) -> Optional[Dict[str, Any]]:
        """Find an existing take with matching content SHA-256 within the channel."""
        with self.db.connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM flow_scene_takes
                WHERE channel_id = ? AND file_sha256 = ?
                ORDER BY created_at ASC LIMIT 1;
                """,
                (channel_id, file_sha256),
            ).fetchone()
            return self._row_to_dict(row) if row else None

    def find_project_scene_take_by_sha256(
        self, project_id: str, scene_id: str, file_sha256: str, asset_role: str = "video"
    ) -> Optional[Dict[str, Any]]:
        """Find an existing take with matching SHA-256 specifically for this project and scene."""
        with self.db.connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM flow_scene_takes
                WHERE project_id = ? AND scene_id = ? AND file_sha256 = ? AND asset_role = ?
                LIMIT 1;
                """,
                (project_id, scene_id, file_sha256, asset_role),
            ).fetchone()
            return self._row_to_dict(row) if row else None

    def list_takes_for_scene(
        self, project_id: str, scene_id: str, asset_role: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """List all historical takes for a scene ordered by take_number ascending."""
        with self.db.connection() as conn:
            if asset_role:
                rows = conn.execute(
                    """
                    SELECT * FROM flow_scene_takes
                    WHERE project_id = ? AND scene_id = ? AND asset_role = ?
                    ORDER BY take_number ASC;
                    """,
                    (project_id, scene_id, asset_role),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM flow_scene_takes
                    WHERE project_id = ? AND scene_id = ?
                    ORDER BY take_number ASC;
                    """,
                    (project_id, scene_id),
                ).fetchall()
            return [self._row_to_dict(r) for r in rows]

    def list_takes_for_project(self, project_id: str) -> List[Dict[str, Any]]:
        """List all takes associated with a project."""
        with self.db.connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM flow_scene_takes
                WHERE project_id = ?
                ORDER BY scene_id ASC, take_number ASC;
                """,
                (project_id,),
            ).fetchall()
            return [self._row_to_dict(r) for r in rows]

    def get_next_take_number(
        self, project_id: str, scene_id: str, asset_role: str = "video"
    ) -> int:
        """Calculate the next take number (max + 1) for a scene."""
        with self.db.connection() as conn:
            row = conn.execute(
                """
                SELECT MAX(take_number) as max_take FROM flow_scene_takes
                WHERE project_id = ? AND scene_id = ? AND asset_role = ?;
                """,
                (project_id, scene_id, asset_role),
            ).fetchone()
            current_max = row["max_take"] if row and row["max_take"] is not None else 0
            return int(current_max) + 1

    def set_active_take(
        self, project_id: str, scene_id: str, take_number: int, asset_role: str = "video"
    ) -> bool:
        """Promote a specific take number to active, deactivating others."""
        with self.db.transaction() as conn:
            # Check target exists
            target = conn.execute(
                """
                SELECT id FROM flow_scene_takes
                WHERE project_id = ? AND scene_id = ? AND take_number = ? AND asset_role = ?;
                """,
                (project_id, scene_id, take_number, asset_role),
            ).fetchone()
            if not target:
                return False

            conn.execute(
                """
                UPDATE flow_scene_takes
                SET is_active = 0
                WHERE project_id = ? AND scene_id = ? AND asset_role = ?;
                """,
                (project_id, scene_id, asset_role),
            )
            conn.execute(
                """
                UPDATE flow_scene_takes
                SET is_active = 1
                WHERE project_id = ? AND scene_id = ? AND take_number = ? AND asset_role = ?;
                """,
                (project_id, scene_id, take_number, asset_role),
            )

        logger.info(
            f"Set take #{take_number} ({asset_role}) as active for scene '{scene_id}' in project '{project_id}'."
        )
        return True

    def create_inbound_record(
        self,
        record_id: str,
        channel_id: str,
        project_id: str,
        source_type: str,
        source_identifier: str,
        total_inspected: int,
        imported_count: int,
        duplicate_count: int,
        failed_count: int,
        package_id: Optional[str] = None,
        report_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Record an inbound batch import session."""
        now_utc = datetime.now(timezone.utc).isoformat()
        report_json = json.dumps(report_data or {})

        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO flow_inbound_records (
                    id, channel_id, project_id, package_id, source_type,
                    source_identifier, total_inspected, imported_count,
                    duplicate_count, failed_count, report_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    record_id,
                    channel_id,
                    project_id,
                    package_id,
                    source_type,
                    source_identifier,
                    total_inspected,
                    imported_count,
                    duplicate_count,
                    failed_count,
                    report_json,
                    now_utc,
                ),
            )

        return self.get_inbound_record(record_id) or {}

    def get_inbound_record(self, record_id: str) -> Optional[Dict[str, Any]]:
        """Fetch an inbound record by its ID."""
        with self.db.connection() as conn:
            row = conn.execute(
                "SELECT * FROM flow_inbound_records WHERE id = ?;", (record_id,)
            ).fetchone()
            return self._row_to_dict(row) if row else None

    def list_inbound_records_for_project(self, project_id: str) -> List[Dict[str, Any]]:
        """List inbound import records for a project in descending order."""
        with self.db.connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM flow_inbound_records
                WHERE project_id = ?
                ORDER BY created_at DESC;
                """,
                (project_id,),
            ).fetchall()
            return [self._row_to_dict(r) for r in rows]

    def _row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        """Convert a sqlite3.Row to dictionary and parse JSON fields."""
        d = dict(row)
        for key in ("metadata_json", "report_json"):
            if key in d and d[key]:
                try:
                    d[key] = json.loads(d[key])
                except (json.JSONDecodeError, TypeError):
                    pass
        if "is_active" in d:
            d["is_active"] = bool(d["is_active"])
        return d
