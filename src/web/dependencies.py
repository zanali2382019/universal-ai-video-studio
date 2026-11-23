"""
Dependency injection providers for Studio WebUI.
Exposes standard Service Layer instances without direct database manipulation.
"""

from typing import Optional
from fastapi import Request

from src.database.connection import DatabaseManager
from src.database.repositories.channel_repository import ChannelRepository
from src.database.repositories.project_repository import ProjectRepository
from src.database.repositories.scene_repository import SceneRepository
from src.database.repositories.asset_repository import AssetRepository
from src.database.repositories.license_repository import LicenseRepository
from src.database.repositories.file_operation_repository import FileOperationRepository
from src.database.repositories.audit_repository import AuditRepository
from src.database.repositories.flow_inbound_repository import FlowInboundRepository
from src.database.repositories.flow_prompt_repository import FlowPromptRepository
from src.database.repositories.batch_repository import BatchRepository
from src.database.repositories.task_repository import TaskRepository
from src.database.repositories.export_repository import ExportRepository

from src.storage.service import StorageService
from src.services.channel_service import ChannelService
from src.services.project_service import ProjectService
from src.services.scene_service import SceneService
from src.services.asset_service import AssetService
from src.services.license_service import LicenseService
from src.services.audit_service import AuditService
from src.services.task_service import TaskService
from src.services.batch_service import BatchService
from src.services.render_service import RenderService
from src.services.export_service import ExportService
from src.creative.channel_profile_service import ChannelProfileService
from src.creative.character_bible_service import CharacterBibleService
from src.creative.rules_engine import CreativeRulesEngine
from src.flow.prompt_service import FlowPromptService
from src.flow.inbound.importer import FlowInboundService
from src.flow.inbound.validator import MediaValidator


class ServiceContainer:
    """Central container managing initialized Studio services."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None, storage_service: Optional[StorageService] = None):
        self.db = db_manager or DatabaseManager()
        self.storage = storage_service or StorageService()

        # Repositories
        self.channel_repo = ChannelRepository(self.db)
        self.project_repo = ProjectRepository(self.db)
        self.scene_repo = SceneRepository(self.db)
        self.asset_repo = AssetRepository(self.db)
        self.license_repo = LicenseRepository(self.db)
        self.file_op_repo = FileOperationRepository(self.db)
        self.audit_repo = AuditRepository(self.db)
        self.flow_inbound_repo = FlowInboundRepository(self.db)
        self.flow_prompt_repo = FlowPromptRepository(self.db)
        self.batch_repo = BatchRepository(self.db)
        self.task_repo = TaskRepository(self.db)
        self.export_repo = ExportRepository(self.db)

        # Core Domain Services
        self.audit_service = AuditService(self.audit_repo)
        self.license_service = LicenseService(self.license_repo, self.audit_service)
        self.channel_service = ChannelService(self.channel_repo)
        self.project_service = ProjectService(self.project_repo, self.channel_repo)
        self.scene_service = SceneService(self.scene_repo, self.project_repo)
        self.task_service = TaskService(
            task_repository=self.task_repo,
            channel_repository=self.channel_repo,
            project_repository=self.project_repo,
            scene_repository=self.scene_repo,
        )
        self.batch_service = BatchService(
            batch_repository=self.batch_repo,
            task_service=self.task_service,
            channel_repository=self.channel_repo,
        )
        self.asset_service = AssetService(
            asset_repository=self.asset_repo,
            license_repository=self.license_repo,
            file_operation_repository=self.file_op_repo,
            audit_service=self.audit_service,
            storage_service=self.storage,
        )
        self.render_service = RenderService(
            db_manager=self.db,
            storage_service=self.storage,
            task_service=self.task_service,
            project_service=self.project_service,
            scene_service=self.scene_service,
            asset_service=self.asset_service,
            license_service=self.license_service,
            flow_inbound_repo=self.flow_inbound_repo,
        )
        self.export_service = ExportService(
            db_manager=self.db,
            export_repository=self.export_repo,
            license_service=self.license_service,
            project_repository=self.project_repo,
            scene_repository=self.scene_repo,
            channel_repository=self.channel_repo,
        )

        # Creative & Flow Services
        self.channel_profile_service = ChannelProfileService(self.storage)
        self.character_bible_service = CharacterBibleService(self.storage)
        self.rules_engine = CreativeRulesEngine()

        self.flow_prompt_service = FlowPromptService(
            db_manager=self.db,
            storage_service=self.storage,
            channel_profile_service=self.channel_profile_service,
            character_bible_service=self.character_bible_service,
            rules_engine=self.rules_engine,
        )

        self.media_validator = MediaValidator()
        self.flow_inbound_service = FlowInboundService(
            db_manager=self.db,
            storage_service=self.storage,
            validator=self.media_validator,
        )


_container_instance: Optional[ServiceContainer] = None


def init_service_container(db_manager: Optional[DatabaseManager] = None, storage_service: Optional[StorageService] = None) -> ServiceContainer:
    """Initialize or retrieve the global ServiceContainer outside of requests."""
    global _container_instance
    _container_instance = ServiceContainer(db_manager, storage_service)
    return _container_instance


def get_service_container(request: Request) -> ServiceContainer:
    """FastAPI dependency to retrieve the active ServiceContainer."""
    if hasattr(request.app.state, "service_container") and request.app.state.service_container is not None:
        return request.app.state.service_container
    global _container_instance
    if _container_instance is None:
        _container_instance = ServiceContainer()
    return _container_instance


def reset_service_container(container: Optional[ServiceContainer] = None) -> None:
    """Reset global container instance (useful for test isolation)."""
    global _container_instance
    _container_instance = container
