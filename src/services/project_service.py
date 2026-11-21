"""Project management service enforcing channel boundaries and project lifecycle."""
import logging
from typing import Optional, List, Dict, Any, Union
from pydantic import ValidationError

from src.database.repositories.project_repository import ProjectRepository
from src.database.repositories.channel_repository import ChannelRepository
from src.database.models import ProjectModel, ProjectCreate, ProjectUpdate
from src.core.constants import ProjectStatus
from src.core.exceptions import (
    ProjectNotFoundException,
    ChannelNotFoundException,
    ValidationException,
    StudioException,
)
from src.core.logger import get_logger, log_context

logger = get_logger("studio.services.project")


class ProjectService:
    """Provides business operations for video project management."""

    def __init__(
        self,
        project_repository: Optional[ProjectRepository] = None,
        channel_repository: Optional[ChannelRepository] = None,
        service_logger: Optional[logging.Logger] = None,
    ) -> None:
        self._repo = project_repository or ProjectRepository()
        self._channel_repo = channel_repository or ChannelRepository()
        self._logger = service_logger or logger

    def create_project(
        self,
        channel_id: str,
        project_input: Union[ProjectCreate, Dict[str, Any]],
    ) -> ProjectModel:
        """Create a new project scoped to a specific channel.

        Args:
            channel_id: Owning channel ID.
            project_input: ProjectCreate instance or dictionary.

        Returns:
            ProjectModel: Created project.
        """
        clean_channel_id = (channel_id or "").strip()
        if not clean_channel_id:
            raise ValidationException("Channel ID cannot be empty.")

        if not self._channel_repo.exists(clean_channel_id):
            raise ChannelNotFoundException(clean_channel_id)

        if isinstance(project_input, dict):
            payload = dict(project_input)
            payload["channel_id"] = clean_channel_id
            try:
                data = ProjectCreate(**payload)
            except ValidationError as err:
                errors = err.errors()
                err_details = {
                    ".".join(str(loc) for loc in e.get("loc", [])): e.get("msg")
                    for e in errors
                }
                raise ValidationException(
                    f"Invalid project payload: {err.error_count()} validation error(s).",
                    details=err_details,
                ) from err
        elif isinstance(project_input, ProjectCreate):
            if project_input.channel_id != clean_channel_id:
                raise ValidationException(
                    f"Project channel_id '{project_input.channel_id}' does not match context channel '{clean_channel_id}'."
                )
            data = project_input
        else:
            raise ValidationException("Unsupported project input type.")

        with log_context(channel_id=clean_channel_id, project_id=data.id):
            self._logger.info(f"Creating project '{data.id}' under channel '{clean_channel_id}'.")
            if self._repo.exists(clean_channel_id, data.id):
                raise ValidationException(
                    f"Project with ID '{data.id}' already exists in channel '{clean_channel_id}'.",
                    details={"project_id": data.id},
                )
            project = self._repo.create(data)
            self._logger.info(f"Project '{project.id}' created successfully.")
            return project

    def get_project(self, channel_id: str, project_id: str) -> ProjectModel:
        """Retrieve a project by ID, ensuring it belongs to the given channel."""
        clean_ch = (channel_id or "").strip()
        clean_prj = (project_id or "").strip()
        if not clean_ch or not clean_prj:
            raise ValidationException("Channel ID and Project ID must be non-empty.")

        with log_context(channel_id=clean_ch, project_id=clean_prj):
            project = self._repo.get_by_id(clean_ch, clean_prj)
            if not project:
                self._logger.warning(f"Project '{clean_prj}' not found under channel '{clean_ch}'.")
                raise ProjectNotFoundException(clean_prj)
            return project

    def list_projects(
        self,
        channel_id: str,
        include_archived: bool = False,
    ) -> List[ProjectModel]:
        """List all projects for a channel."""
        clean_ch = (channel_id or "").strip()
        if not clean_ch:
            raise ValidationException("Channel ID must be non-empty.")

        if not self._channel_repo.exists(clean_ch):
            raise ChannelNotFoundException(clean_ch)

        return self._repo.list_by_channel(clean_ch, include_archived=include_archived)

    def update_project(
        self,
        channel_id: str,
        project_id: str,
        updates: Union[ProjectUpdate, Dict[str, Any]],
    ) -> ProjectModel:
        """Update an existing project."""
        clean_ch = (channel_id or "").strip()
        clean_prj = (project_id or "").strip()
        if not clean_ch or not clean_prj:
            raise ValidationException("Channel ID and Project ID must be non-empty.")

        if isinstance(updates, dict):
            try:
                update_data = ProjectUpdate(**updates)
            except ValidationError as err:
                raise ValidationException(f"Invalid update payload: {err}") from err
        elif isinstance(updates, ProjectUpdate):
            update_data = updates
        else:
            raise ValidationException("Unsupported update payload type.")

        with log_context(channel_id=clean_ch, project_id=clean_prj):
            self.get_project(clean_ch, clean_prj)  # Check existence
            updated = self._repo.update(clean_ch, clean_prj, update_data)
            self._logger.info(f"Updated project '{clean_prj}' in channel '{clean_ch}'.")
            return updated

    def archive_project(self, channel_id: str, project_id: str) -> ProjectModel:
        """Mark a project as archived."""
        return self.update_project(
            channel_id,
            project_id,
            ProjectUpdate(is_archived=True, status=ProjectStatus.COMPLETED),
        )

    def delete_project(self, channel_id: str, project_id: str) -> bool:
        """Delete a project and its workspace working files."""
        clean_ch = (channel_id or "").strip()
        clean_prj = (project_id or "").strip()
        self.get_project(clean_ch, clean_prj)

        with log_context(channel_id=clean_ch, project_id=clean_prj):
            self._logger.warning(f"Deleting project '{clean_prj}' from channel '{clean_ch}'.")
            deleted = self._repo.delete(clean_ch, clean_prj)
            self._logger.info(f"Project '{clean_prj}' deleted.")
            return deleted

