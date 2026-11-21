"""Scene management service ensuring project-scoped sequential scene operations."""
import logging
from typing import Optional, List, Dict, Any, Union
from pydantic import ValidationError

from src.database.repositories.scene_repository import SceneRepository
from src.database.repositories.project_repository import ProjectRepository
from src.database.models import SceneModel, SceneCreate, SceneUpdate
from src.core.exceptions import (
    SceneNotFoundException,
    ProjectNotFoundException,
    ValidationException,
)
from src.core.logger import get_logger, log_context

logger = get_logger("studio.services.scene")


class SceneService:
    """Provides business operations for scene administration."""

    def __init__(
        self,
        scene_repository: Optional[SceneRepository] = None,
        project_repository: Optional[ProjectRepository] = None,
        service_logger: Optional[logging.Logger] = None,
    ) -> None:
        self._repo = scene_repository or SceneRepository()
        self._project_repo = project_repository or ProjectRepository()
        self._logger = service_logger or logger

    def create_scene(
        self,
        channel_id: str,
        project_id: str,
        scene_input: Union[SceneCreate, Dict[str, Any]],
    ) -> SceneModel:
        """Create a new scene within a project."""
        clean_ch = (channel_id or "").strip()
        clean_prj = (project_id or "").strip()
        if not clean_ch or not clean_prj:
            raise ValidationException("Channel ID and Project ID cannot be empty.")

        if not self._project_repo.exists(clean_ch, clean_prj):
            raise ProjectNotFoundException(clean_prj)

        if isinstance(scene_input, dict):
            payload = dict(scene_input)
            payload["channel_id"] = clean_ch
            payload["project_id"] = clean_prj
            try:
                data = SceneCreate(**payload)
            except ValidationError as err:
                raise ValidationException(f"Invalid scene payload: {err}") from err
        elif isinstance(scene_input, SceneCreate):
            if scene_input.channel_id != clean_ch or scene_input.project_id != clean_prj:
                raise ValidationException("Scene scope does not match channel or project context.")
            data = scene_input
        else:
            raise ValidationException("Unsupported scene input type.")

        with log_context(channel_id=clean_ch, project_id=clean_prj):
            self._logger.info(
                f"Creating scene '{data.id}' (seq={data.sequence_number}) for project '{clean_prj}'."
            )
            created = self._repo.create(data)
            return created

    def get_scene(self, channel_id: str, scene_id: str) -> SceneModel:
        """Retrieve a scene by ID."""
        clean_ch = (channel_id or "").strip()
        clean_sc = (scene_id or "").strip()
        if not clean_ch or not clean_sc:
            raise ValidationException("Channel ID and Scene ID must be non-empty.")

        scene = self._repo.get_by_id(clean_ch, clean_sc)
        if not scene:
            raise SceneNotFoundException(clean_sc)
        return scene

    def list_scenes(self, channel_id: str, project_id: str) -> List[SceneModel]:
        """List scenes for a project ordered by sequence."""
        clean_ch = (channel_id or "").strip()
        clean_prj = (project_id or "").strip()
        if not clean_ch or not clean_prj:
            raise ValidationException("Channel ID and Project ID must be non-empty.")

        if not self._project_repo.exists(clean_ch, clean_prj):
            raise ProjectNotFoundException(clean_prj)

        return self._repo.list_by_project(clean_ch, clean_prj)

    def update_scene(
        self,
        channel_id: str,
        scene_id: str,
        updates: Union[SceneUpdate, Dict[str, Any]],
    ) -> SceneModel:
        """Update scene properties."""
        clean_ch = (channel_id or "").strip()
        clean_sc = (scene_id or "").strip()
        self.get_scene(clean_ch, clean_sc)

        if isinstance(updates, dict):
            try:
                update_data = SceneUpdate(**updates)
            except ValidationError as err:
                raise ValidationException(f"Invalid scene update payload: {err}") from err
        elif isinstance(updates, SceneUpdate):
            update_data = updates
        else:
            raise ValidationException("Unsupported update payload type.")

        return self._repo.update(clean_ch, clean_sc, update_data)

    def reorder_scenes(
        self,
        channel_id: str,
        project_id: str,
        scene_ids_in_order: List[str],
    ) -> List[SceneModel]:
        """Reorder scenes in a project."""
        clean_ch = (channel_id or "").strip()
        clean_prj = (project_id or "").strip()
        if not clean_ch or not clean_prj:
            raise ValidationException("Channel ID and Project ID must be non-empty.")

        if not self._project_repo.exists(clean_ch, clean_prj):
            raise ProjectNotFoundException(clean_prj)

        existing = self.list_scenes(clean_ch, clean_prj)
        existing_ids = {s.id for s in existing}
        if set(scene_ids_in_order) != existing_ids:
            raise ValidationException(
                "Provided scene IDs for reordering do not exactly match existing project scenes.",
                details={"provided": scene_ids_in_order, "existing": list(existing_ids)},
            )

        with log_context(channel_id=clean_ch, project_id=clean_prj):
            self._logger.info(f"Reordering {len(scene_ids_in_order)} scenes for project '{clean_prj}'.")
            return self._repo.reorder_scenes(clean_ch, clean_prj, scene_ids_in_order)

    def delete_scene(self, channel_id: str, scene_id: str) -> bool:
        """Delete a scene."""
        clean_ch = (channel_id or "").strip()
        clean_sc = (scene_id or "").strip()
        self.get_scene(clean_ch, clean_sc)

        with log_context(channel_id=clean_ch):
            return self._repo.delete(clean_ch, clean_sc)

