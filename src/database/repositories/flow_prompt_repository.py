"""Repository for managing versioned scene prompts and Google Flow packages (Task 3.11)."""

import json
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

from src.database.connection import DatabaseManager
from src.core.logger import get_logger

logger = get_logger("studio.database.flow_prompt_repository")


class FlowPromptRepository:
    """Manages SQLite storage for versioned scene prompts and prompt packages."""

    def __init__(self, db_manager: DatabaseManager):
        self.db_manager = db_manager

    # -------------------------------------------------------------------------
    # Scene Prompts CRUD & Versioning
    # -------------------------------------------------------------------------

    def save_scene_prompt(
        self,
        id: str,
        scene_id: str,
        channel_id: str,
        project_id: str,
        version: int,
        visual_prompt: str,
        generation_mode: str = "text_to_video",
        master_prompt: str = "",
        negative_prompt: str = "",
        camera_instructions: str = "",
        lighting_instructions: str = "",
        motion_instructions: str = "",
        continuity_instructions: str = "",
        generation_specs_json: Optional[Dict[str, Any]] = None,
        character_refs_json: Optional[List[Dict[str, Any]]] = None,
        style_refs_json: Optional[List[Dict[str, Any]]] = None,
        is_active: bool = True,
    ) -> Dict[str, Any]:
        """Save a new prompt version for a scene."""
        now_utc = datetime.now(timezone.utc).isoformat()
        specs_str = json.dumps(generation_specs_json or {})
        chars_str = json.dumps(character_refs_json or [])
        styles_str = json.dumps(style_refs_json or [])

        with self.db_manager.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO scene_prompts (
                    id, scene_id, channel_id, project_id, version,
                    generation_mode, master_prompt, visual_prompt, negative_prompt,
                    camera_instructions, lighting_instructions, motion_instructions,
                    continuity_instructions, generation_specs_json, character_refs_json,
                    style_refs_json, is_active, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    id, scene_id, channel_id, project_id, version,
                    generation_mode, master_prompt, visual_prompt, negative_prompt,
                    camera_instructions, lighting_instructions, motion_instructions,
                    continuity_instructions, specs_str, chars_str,
                    styles_str, 1 if is_active else 0, now_utc, now_utc
                ),
            )
            conn.commit()

        logger.info(f"Saved prompt version {version} for scene '{scene_id}' (prompt_id={id}).")
        return self.get_scene_prompt(scene_id, version=version)  # type: ignore

    def get_scene_prompt(self, scene_id: str, version: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """Retrieve a scene prompt by scene_id and optional version (defaults to latest active version)."""
        with self.db_manager.connection() as conn:
            cursor = conn.cursor()
            if version is not None:
                row = cursor.execute(
                    "SELECT * FROM scene_prompts WHERE scene_id = ? AND version = ?;",
                    (scene_id, version),
                ).fetchone()
            else:
                row = cursor.execute(
                    "SELECT * FROM scene_prompts WHERE scene_id = ? AND is_active = 1 ORDER BY version DESC LIMIT 1;",
                    (scene_id,),
                ).fetchone()

            if not row:
                return None
            return self._row_to_dict(row)

    def get_latest_version_number(self, scene_id: str) -> int:
        """Get the latest version number recorded for a scene prompt."""
        with self.db_manager.connection() as conn:
            cursor = conn.cursor()
            row = cursor.execute(
                "SELECT MAX(version) as max_v FROM scene_prompts WHERE scene_id = ?;",
                (scene_id,),
            ).fetchone()
            if row and row["max_v"] is not None:
                return int(row["max_v"])
            return 0

    def list_scene_prompt_history(self, scene_id: str) -> List[Dict[str, Any]]:
        """Return all historical prompt versions for a scene, newest first."""
        with self.db_manager.connection() as conn:
            cursor = conn.cursor()
            rows = cursor.execute(
                "SELECT * FROM scene_prompts WHERE scene_id = ? ORDER BY version DESC;",
                (scene_id,),
            ).fetchall()
            return [self._row_to_dict(r) for r in rows]

    def get_project_latest_prompts(self, project_id: str) -> List[Dict[str, Any]]:
        """Get the latest active prompt for each scene in a project."""
        with self.db_manager.connection() as conn:
            cursor = conn.cursor()
            rows = cursor.execute(
                """
                SELECT sp.* FROM scene_prompts sp
                INNER JOIN (
                    SELECT scene_id, MAX(version) as max_version
                    FROM scene_prompts
                    WHERE project_id = ? AND is_active = 1
                    GROUP BY scene_id
                ) latest ON sp.scene_id = latest.scene_id AND sp.version = latest.max_version
                ORDER BY sp.scene_id ASC;
                """,
                (project_id,),
            ).fetchall()
            return [self._row_to_dict(r) for r in rows]

    # -------------------------------------------------------------------------
    # Prompt Packages Storage
    # -------------------------------------------------------------------------

    def save_prompt_package(
        self,
        id: str,
        channel_id: str,
        project_id: str,
        package_version: int,
        title: str,
        content_hash: str,
        json_storage_key: str,
        markdown_storage_key: str,
        flow_project_name: str = "",
        flow_workspace_ref: str = "",
        scene_count: int = 0,
        metadata_json: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Record an exported PromptPackage manifest."""
        now_utc = datetime.now(timezone.utc).isoformat()
        meta_str = json.dumps(metadata_json or {})

        with self.db_manager.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO prompt_packages (
                    id, channel_id, project_id, package_version, title,
                    flow_project_name, flow_workspace_ref, content_hash,
                    json_storage_key, markdown_storage_key, scene_count,
                    metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    id, channel_id, project_id, package_version, title,
                    flow_project_name, flow_workspace_ref, content_hash,
                    json_storage_key, markdown_storage_key, scene_count,
                    meta_str, now_utc
                ),
            )
            conn.commit()

        logger.info(f"Registered prompt package '{id}' (v{package_version}) for project '{project_id}'.")
        return self.get_prompt_package(id)  # type: ignore

    def get_prompt_package(self, package_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a prompt package record by ID."""
        with self.db_manager.connection() as conn:
            cursor = conn.cursor()
            row = cursor.execute(
                "SELECT * FROM prompt_packages WHERE id = ?;",
                (package_id,),
            ).fetchone()
            if not row:
                return None
            return self._package_row_to_dict(row)

    def get_latest_project_package(self, project_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve the latest package created for a project."""
        with self.db_manager.connection() as conn:
            cursor = conn.cursor()
            row = cursor.execute(
                "SELECT * FROM prompt_packages WHERE project_id = ? ORDER BY package_version DESC LIMIT 1;",
                (project_id,),
            ).fetchone()
            if not row:
                return None
            return self._package_row_to_dict(row)

    def get_next_package_version(self, project_id: str) -> int:
        """Get the next sequential package version number for a project."""
        with self.db_manager.connection() as conn:
            cursor = conn.cursor()
            row = cursor.execute(
                "SELECT MAX(package_version) as max_v FROM prompt_packages WHERE project_id = ?;",
                (project_id,),
            ).fetchone()
            if row and row["max_v"] is not None:
                return int(row["max_v"]) + 1
            return 1

    # -------------------------------------------------------------------------
    # Internal Helpers
    # -------------------------------------------------------------------------

    @staticmethod
    def _row_to_dict(row: Any) -> Dict[str, Any]:
        d = dict(row)
        for key in ["generation_specs_json", "character_refs_json", "style_refs_json"]:
            if isinstance(d.get(key), str):
                try:
                    d[key] = json.loads(d[key])
                except Exception:
                    d[key] = {} if "specs" in key else []
        d["is_active"] = bool(d.get("is_active", 1))
        return d

    @staticmethod
    def _package_row_to_dict(row: Any) -> Dict[str, Any]:
        d = dict(row)
        if isinstance(d.get("metadata_json"), str):
            try:
                d["metadata_json"] = json.loads(d["metadata_json"])
            except Exception:
                d["metadata_json"] = {}
        return d

