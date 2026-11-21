"""Unit tests for Task 3.8 Batch 1 Fixes."""
import unittest
import tempfile
import threading
from pathlib import Path

from src.core.config import get_settings
from src.database.connection import DatabaseManager
from src.database.migrations import MigrationManager
from src.database.repositories.channel_repository import ChannelRepository
from src.database.repositories.project_repository import ProjectRepository
from src.database.repositories.scene_repository import SceneRepository
from src.database.repositories.asset_repository import AssetRepository
from src.database.repositories.license_repository import LicenseRepository
from src.database.repositories.task_repository import TaskRepository
from src.database.models import (
    ChannelCreate,
    ProjectCreate,
    SceneCreate,
    MediaAssetCreate,
    AssetLicenseCreate,
    TaskCreate,
)
from src.services.license_service import LicenseService
from src.services.task_service import TaskService
from src.services.worker_pool import WorkerPool, WorkerThread
from src.rendering.render_task_handler import VideoRenderTaskHandler
from src.rendering.timeline_renderer import TimelineRenderer
from src.core.constants import (
    TaskType,
    TaskStatus,
    AssetType,
    LegalBasis,
    VerificationStatus,
)
from src.core.exceptions import (
    ValidationException,
    LicenseVerificationException,
)


class TestTask38Batch1Fixes(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root_path = Path(self.temp_dir.name)
        self.db_path = self.root_path / "test_studio.db"
        self.channels_dir = self.root_path / "channels"
        self.channels_dir.mkdir(parents=True, exist_ok=True)

        self.settings = get_settings()
        self._orig_channels_root = self.settings.channels_root
        self.settings.channels_root = self.channels_dir

        self.db_manager = DatabaseManager(db_path=self.db_path)
        MigrationManager(self.db_manager).run_migrations()

        self.channel_repo = ChannelRepository(self.db_manager)
        self.project_repo = ProjectRepository(self.db_manager)
        self.scene_repo = SceneRepository(self.db_manager)
        self.asset_repo = AssetRepository(self.db_manager)
        self.license_repo = LicenseRepository(self.db_manager)
        self.task_repo = TaskRepository(self.db_manager)

        self.channel_id = "ch_test_38"
        self.channel_repo.create(
            ChannelCreate(id=self.channel_id, name="Test Ch", handle="@testch38", niche="test")
        )

    def _create_asset(self, asset_id: str, asset_type: AssetType = AssetType.IMAGE) -> None:
        self.asset_repo.create(
            MediaAssetCreate(
                id=asset_id,
                channel_id=self.channel_id,
                file_name=f"{asset_id}.dat",
                file_path=f"/media/{asset_id}.dat",
                file_sha256="0" * 64,
                file_size_bytes=1024,
                mime_type="image/jpeg" if asset_type == AssetType.IMAGE else "audio/wav",
                asset_type=asset_type,
            )
        )

    def tearDown(self):
        self.settings.channels_root = self._orig_channels_root
        self.temp_dir.cleanup()

    def test_01_render_task_handler_init_with_db_manager(self):
        """Fix 1: VideoRenderTaskHandler initializes cleanly when passed db_manager."""
        handler = VideoRenderTaskHandler(db_manager=self.db_manager)
        self.assertIsNotNone(handler.license_service)
        self.assertIsInstance(handler.license_service, LicenseService)
        self.assertEqual(handler.license_service.db_manager, self.db_manager)

    def test_02_worker_pool_unhandled_task_transitions_to_failed(self):
        """Fix 2: Unhandled task types fail fast and are NOT marked COMPLETED."""
        task_service = TaskService(task_repository=self.task_repo, channel_repository=self.channel_repo)

        # Create an unhandled task (IMAGE_GENERATION)
        task = task_service.create_task(
            channel_id=self.channel_id,
            task_input={"task_type": TaskType.IMAGE_GENERATION, "idempotency_key": "img_task_1"},
        )

        shutdown_event = threading.Event()
        worker = WorkerThread(
            worker_id="w_test",
            task_repository=self.task_repo,
            task_service=task_service,
            shutdown_event=shutdown_event,
            poll_interval_sec=0.05,
        )

        # Claim and execute task
        claimed = self.task_repo.atomic_claim_next_task(worker_id="w_test", lease_duration_sec=30)
        self.assertIsNotNone(claimed)
        worker._process_task(claimed)

        # Verify task is FAILED, not COMPLETED
        updated_task = task_service.get_task(task.id)
        self.assertEqual(updated_task.status, TaskStatus.FAILED)
        self.assertIn("no registered execution handler", updated_task.error_message)

    def test_03_license_verification_rejects_empty_proof_assertion(self):
        """Fix 3: Rejecting user responsibility assertion without proof evidence."""
        self._create_asset("ast_test_01")
        license_service = LicenseService(db_manager=self.db_manager)
        lic = license_service.register_license(
            channel_id=self.channel_id,
            license_input={
                "id": "lic_test_01",
                "asset_id": "ast_test_01",
                "legal_basis": LegalBasis.COMMERCIAL_LICENSE,
            },
        )

        # Assertion with "I accept responsibility" but no proof file/sha/source_uri must be rejected
        with self.assertRaises(ValidationException) as ctx:
            license_service.verify_license(
                channel_id=self.channel_id,
                license_id=lic.id,
                legal_basis=LegalBasis.COMMERCIAL_LICENSE,
                verified_by="user_tester",
                notes="I accept responsibility for this asset",
                proof_file_path=None,
                proof_sha256=None,
            )
        self.assertIn("Mere assertion of responsibility", str(ctx.exception))

    def test_04_timeline_renderer_blocks_unverified_audio_asset(self):
        """Fix 4: Timeline rendering blocks export if an audio asset is unverified."""
        self._create_asset("ast_audio_unverified", AssetType.AUDIO)
        self._create_asset("ast_visual_verified", AssetType.IMAGE)
        license_service = LicenseService(db_manager=self.db_manager)
        renderer = TimelineRenderer(license_service=license_service, task_repository=self.task_repo)

        # Register unverified audio asset
        lic = license_service.register_license(
            channel_id=self.channel_id,
            license_input={
                "id": "lic_audio_01",
                "asset_id": "ast_audio_unverified",
                "legal_basis": LegalBasis.COMMERCIAL_LICENSE,
            },
        )

        from src.rendering.timeline_renderer import SceneRenderItem
        scenes = [
            SceneRenderItem(
                asset_path=Path("dummy.jpg"),
                duration_sec=3.0,
                asset_id="ast_visual_verified",
            )
        ]

        # Verify visual asset license
        vis_lic = license_service.register_license(
            channel_id=self.channel_id,
            license_input={
                "id": "lic_vis_01",
                "asset_id": "ast_visual_verified",
                "legal_basis": LegalBasis.PROPRIETARY_OWNERSHIP,
            },
        )
        license_service.verify_license(
            channel_id=self.channel_id,
            license_id=vis_lic.id,
            legal_basis=LegalBasis.PROPRIETARY_OWNERSHIP,
            verified_by="admin",
            notes="Original company asset",
            proof_file_path="/proof/original.pdf",
        )

        # Rendering with unverified audio asset must be blocked by export gate
        with self.assertRaises(LicenseVerificationException) as ctx:
            renderer.render_timeline(
                scenes=scenes,
                output_path=Path("out.mp4"),
                channel_id=self.channel_id,
                audio_asset_ids=["ast_audio_unverified"],
            )
        self.assertIn("Asset license is not verified", str(ctx.exception))
        self.assertEqual(ctx.exception.details.get("asset_id"), "ast_audio_unverified")


if __name__ == "__main__":
    unittest.main()
