"""Core business service for managing channels in Universal AI Video Production Studio.

Follows the Service Layer Pattern (ADR-001):
- Decouples business logic and orchestration from data access (repositories) and presentations (CLI/GUI).
- Enforces strict multi-channel boundaries and validation (ADR-005).
- Ensures transactional consistency between relational database records and filesystem workspaces.
"""
import logging
from typing import Optional, List, Dict, Any, Union
from pydantic import ValidationError

from src.database.repositories.channel_repository import ChannelRepository
from src.database.models import ChannelModel, ChannelCreate, ChannelUpdate
from src.core.exceptions import (
    StudioException,
    ChannelNotFoundException,
    ValidationException,
)
from src.core.logger import get_logger, log_context

logger = get_logger("studio.services.channel")


class ChannelService:
    """Provides high-level business operations for multi-channel administration."""

    def __init__(
        self,
        repository: Optional[ChannelRepository] = None,
        service_logger: Optional[logging.Logger] = None,
    ) -> None:
        """Initialize ChannelService with dependency injection.

        Args:
            repository: ChannelRepository instance. If None, instantiates a default repository.
            service_logger: Logger instance. If None, uses default studio channel logger.
        """
        self._repo = repository or ChannelRepository()
        self._logger = service_logger or logger

    @property
    def repository(self) -> ChannelRepository:
        """Expose repository reference for explicit inspection if needed."""
        return self._repo

    def create_channel(
        self,
        channel_input: Union[ChannelCreate, Dict[str, Any]],
    ) -> ChannelModel:
        """Create and configure a new channel workspace and record.

        Transaction & Consistency Boundaries:
        1. Input Validation: Schema and business constraints (valid ID format, unique ID and handle).
        2. Persistence & Filesystem (Two-Phase / Compensating Transaction):
           - Phase 1: Channel record inserted in SQLite.
           - Phase 2: Workspace directory tree initialized on disk.
           - Compensation: If Phase 2 fails, the DB record is deleted (rolled back) and any partial
             directories are purged, guaranteeing no orphan records or corrupt states.

        Args:
            channel_input: ChannelCreate model or dictionary representing channel attributes.

        Returns:
            ChannelModel: The created and initialized channel entity.

        Raises:
            ValidationException: If input is invalid, or channel ID/handle already exists.
            StudioException: If workspace or database persistence encounters an unrecoverable failure.
        """
        # Validate and coerce payload
        if isinstance(channel_input, dict):
            try:
                data = ChannelCreate(**channel_input)
            except ValidationError as err:
                errors = err.errors()
                err_details = {
                    ".".join(str(loc) for loc in e.get("loc", [])): e.get("msg")
                    for e in errors
                }
                raise ValidationException(
                    f"Invalid channel payload: {err.error_count()} validation error(s).",
                    details=err_details,
                ) from err
        elif isinstance(channel_input, ChannelCreate):
            data = channel_input
        else:
            raise ValidationException(
                f"Unsupported channel input type: {type(channel_input).__name__}. "
                "Expected ChannelCreate model or dict."
            )

        channel_id = data.id.strip() if data.id else ""
        if not channel_id:
            raise ValidationException(
                "Channel ID cannot be empty or whitespace.",
                details={"field": "id"},
            )

        with log_context(channel_id=channel_id):
            self._logger.info(
                f"Initiating creation of channel '{channel_id}' with handle '{data.handle}'"
            )

            # Business Check 1: ID uniqueness
            if self._repo.exists(channel_id):
                self._logger.warning(f"Registration rejected: Channel ID '{channel_id}' already exists.")
                raise ValidationException(
                    f"Channel with ID '{channel_id}' already exists.",
                    details={"field": "id", "value": channel_id},
                )

            # Business Check 2: Handle uniqueness
            existing_handle_match = self._repo.get_by_handle(data.handle)
            if existing_handle_match is not None:
                self._logger.warning(
                    f"Registration rejected: Handle '{data.handle}' already registered to channel '{existing_handle_match.id}'."
                )
                raise ValidationException(
                    f"Channel handle '{data.handle}' is already registered.",
                    details={"field": "handle", "value": data.handle},
                )

            try:
                # Repository executes the dual-phase DB insert + filesystem initialization with rollback
                created_channel = self._repo.create(data)
                self._logger.info(
                    f"Channel '{created_channel.id}' ({created_channel.handle}) created successfully."
                )
                return created_channel
            except StudioException:
                # Re-raise studio exceptions (e.g. ValidationException, Filesystem error)
                raise
            except Exception as e:
                self._logger.error(
                    f"Unexpected failure during creation of channel '{channel_id}': {e}",
                    exc_info=True,
                )
                raise StudioException(
                    f"Unexpected error while creating channel '{channel_id}': {e}",
                    details={"channel_id": channel_id, "error": str(e)},
                ) from e

    def get_channel(self, channel_id: str) -> ChannelModel:
        """Retrieve a channel by its unique identifier.

        Args:
            channel_id: Channel ID (e.g. 'ch_dreammatter_001').

        Returns:
            ChannelModel: Channel entity.

        Raises:
            ValidationException: If channel_id is empty or invalid.
            ChannelNotFoundException: If no channel exists with given ID.
        """
        clean_id = (channel_id or "").strip()
        if not clean_id:
            raise ValidationException(
                "Channel ID must be a non-empty string.",
                details={"field": "channel_id"},
            )

        with log_context(channel_id=clean_id):
            channel = self._repo.get_by_id(clean_id)
            if not channel:
                self._logger.warning(f"Channel retrieval failed: ID '{clean_id}' does not exist.")
                raise ChannelNotFoundException(clean_id)

            self._logger.debug(f"Retrieved channel '{channel.id}' ({channel.handle}).")
            return channel

    def get_channel_by_handle(self, handle: str) -> ChannelModel:
        """Retrieve a channel by its public YouTube handle.

        Args:
            handle: Public handle (e.g. '@DreamMatterWorld' or 'DreamMatterWorld').

        Returns:
            ChannelModel: Channel entity.

        Raises:
            ValidationException: If handle is empty or invalid.
            ChannelNotFoundException: If no channel matches the handle.
        """
        clean_handle = (handle or "").strip()
        if not clean_handle:
            raise ValidationException(
                "Channel handle must be a non-empty string.",
                details={"field": "handle"},
            )

        channel = self._repo.get_by_handle(clean_handle)
        if not channel:
            self._logger.warning(f"Channel retrieval failed: handle '{clean_handle}' does not exist.")
            raise ChannelNotFoundException(clean_handle)

        with log_context(channel_id=channel.id):
            self._logger.debug(f"Retrieved channel '{channel.id}' via handle '{clean_handle}'.")
            return channel

    def list_channels(self, include_inactive: bool = False) -> List[ChannelModel]:
        """List channels registered in the studio.

        Args:
            include_inactive: When True, includes archived/soft-deleted channels.

        Returns:
            List[ChannelModel]: Ordered list of channels.
        """
        channels = self._repo.list_all(include_inactive=include_inactive)
        self._logger.debug(
            f"Listed {len(channels)} channels (include_inactive={include_inactive})."
        )
        return channels

    def update_channel(
        self,
        channel_id: str,
        updates: Union[ChannelUpdate, Dict[str, Any]],
    ) -> ChannelModel:
        """Update properties and configurations of an existing channel.

        Args:
            channel_id: Channel identifier to update.
            updates: ChannelUpdate model or dictionary of updated fields.

        Returns:
            ChannelModel: Updated channel entity.

        Raises:
            ValidationException: If payload or channel_id is invalid or handle conflicts.
            ChannelNotFoundException: If channel does not exist.
        """
        clean_id = (channel_id or "").strip()
        if not clean_id:
            raise ValidationException(
                "Channel ID must be a non-empty string.",
                details={"field": "channel_id"},
            )

        # Validate updates payload
        if isinstance(updates, dict):
            try:
                update_data = ChannelUpdate(**updates)
            except ValidationError as err:
                errors = err.errors()
                err_details = {
                    ".".join(str(loc) for loc in e.get("loc", [])): e.get("msg")
                    for e in errors
                }
                raise ValidationException(
                    f"Invalid update payload: {err.error_count()} error(s).",
                    details=err_details,
                ) from err
        elif isinstance(updates, ChannelUpdate):
            update_data = updates
        else:
            raise ValidationException(
                f"Unsupported update type: {type(updates).__name__}. "
                "Expected ChannelUpdate model or dict."
            )

        with log_context(channel_id=clean_id):
            # Check existence
            existing = self._repo.get_by_id(clean_id)
            if not existing:
                self._logger.warning(f"Update failed: Channel '{clean_id}' not found.")
                raise ChannelNotFoundException(clean_id)

            # Check handle uniqueness if handle is being updated
            if update_data.handle is not None:
                conflict = self._repo.get_by_handle(update_data.handle)
                if conflict and conflict.id != clean_id:
                    self._logger.warning(
                        f"Update rejected: Handle '{update_data.handle}' already belongs to '{conflict.id}'."
                    )
                    raise ValidationException(
                        f"Handle '{update_data.handle}' is already registered to another channel.",
                        details={"field": "handle", "conflicting_channel_id": conflict.id},
                    )

            self._logger.info(f"Applying updates to channel '{clean_id}'.")
            updated = self._repo.update(clean_id, update_data)
            self._logger.info(f"Channel '{clean_id}' updated successfully.")
            return updated

    def delete_channel(
        self,
        channel_id: str,
        hard_delete: bool = False,
        purge_files: bool = False,
    ) -> bool:
        """Deactivate (soft-delete) or permanently purge a channel.

        De-activation Policy:
        - Default (hard_delete=False): Marks `is_active = 0`. Channel data and workspace directories
          remain intact on disk. Safe and non-destructive.
        - Hard Delete (hard_delete=True): Permanently removes channel from database. If purge_files
          is True, also cleans up the physical workspace directory tree.

        Args:
            channel_id: Channel ID to delete.
            hard_delete: If True, executes permanent SQL deletion. Defaults to False (soft-delete).
            purge_files: If True and hard_delete is True, removes filesystem workspace directories.

        Returns:
            bool: True if operation succeeded.

        Raises:
            ValidationException: If channel_id is empty.
            ChannelNotFoundException: If channel does not exist.
        """
        clean_id = (channel_id or "").strip()
        if not clean_id:
            raise ValidationException(
                "Channel ID must be a non-empty string.",
                details={"field": "channel_id"},
            )

        with log_context(channel_id=clean_id):
            # Verify existence
            existing = self._repo.get_by_id(clean_id)
            if not existing:
                self._logger.warning(f"Deletion failed: Channel '{clean_id}' does not exist.")
                raise ChannelNotFoundException(clean_id)

            if not hard_delete:
                # Soft-delete: mark inactive
                self._logger.info(f"Soft-deleting channel '{clean_id}' (is_active=False).")
                self._repo.update(clean_id, ChannelUpdate(is_active=False))
                self._logger.info(f"Channel '{clean_id}' successfully deactivated.")
                return True

            # Hard delete
            self._logger.warning(
                f"Permanently deleting channel '{clean_id}' from database (purge_files={purge_files})."
            )
            deleted = self._repo.delete(clean_id)

            if purge_files:
                self._logger.warning(f"Purging workspace directories for channel '{clean_id}'.")
                self._repo._cleanup_channel_workspace(clean_id)

            self._logger.info(f"Channel '{clean_id}' permanently removed.")
            return deleted

    def validate_channel_exists(self, channel_id: str) -> bool:
        """Check whether a channel ID exists, raising ChannelNotFoundException if missing.

        Args:
            channel_id: Channel ID to check.

        Returns:
            bool: True if channel exists.

        Raises:
            ChannelNotFoundException: If channel does not exist.
            ValidationException: If channel_id is invalid.
        """
        clean_id = (channel_id or "").strip()
        if not clean_id:
            raise ValidationException(
                "Channel ID must be a non-empty string.",
                details={"field": "channel_id"},
            )

        if not self._repo.exists(clean_id):
            raise ChannelNotFoundException(clean_id)
        return True

