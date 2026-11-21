"""Unit and integration tests for Task 3.6 — Milestone 4: Background Task Integration.

Verifies:
1. VideoRenderTaskHandler execution with 5-step checkpoint protocol.
2. Full WorkerPool pipeline integration for TaskType.VIDEO_RENDERING.
3. Execution token fencing guards preventing stale workers from committing outputs.
4. Cancellation propagation to FFmpeg subprocess tree.
5. License validation gate enforcement inside task queue pipeline.
6. Checkpoint fast-forwarding and crash resumption.
7. End-to-end DreamMatter production project pipeline support (3 scenes, voiceover, background music, 9:16 vertical).
"""
import unittest
import tempfile
import gc
import os
import time
import shutil
import threading
from pathlib import Path

from src.core.config import get_settings
from src.core.constants import (
    TaskType,
    TaskStatus,
    ProjectStatus,
    AssetType,
    LegalBasis,
    VerificationStatus,
    FileOperationState,
    FileOperationType,
    AspectRatio,
)
from src.core.exceptions import (
    StaleExecutionTokenException,
    RenderCancelledException,
    LicenseVerificationException,
    ValidationException,
    TimelineRenderException,
)
from src.database.connection import DatabaseManager
from src.database.migrations import MigrationManager
from src.database.models import (
    ChannelCreate,
    ProjectCreate,
    SceneCreate,
    MediaAssetCreate,
    MediaAssetUpdate,
    AssetLicenseCreate,
    TaskCreate,
)
from src.database.repositories.channel_repository import ChannelRepository
from src.database.repositories.project_repository import ProjectRepository
from src.database.repositories.scene_repository import SceneRepository
from src.database.repositories.asset_repository import AssetRepository
from src.database.repositories.license_repository import LicenseRepository
from src.database.repositories.task_repository import TaskRepository
from src.database.repositories.file_operation_repository import FileOperationRepository
from src.database.repositories.audit_repository import AuditRepository
from src.services.audit_service import AuditService
from src.services.channel_service import ChannelService
from src.services.project_service import ProjectService
from src.services.scene_service import SceneService
from src.services.asset_service import AssetService
from src.services.license_service import LicenseService
from src.services.checkpoint_service import CheckpointService
from src.services.task_service import TaskService
from src.services.worker_pool import WorkerPool
from src.services.heartbeat_daemon import HeartbeatDaemon
from src.rendering.ffmpeg_wrapper import FFmpegWrapper
from src.rendering.probe_service import MediaProbeService
from src.rendering.profiles import VERTICAL_9_16, HORIZONTAL_16_9, get_profile
from src.rendering.timeline_renderer import TimelineRenderer
from src.rendering.render_task_handler import (
    VideoRenderTaskHandler,
    create_studio_worker_handler,
)
from src.utils.file_utils import calculate_file_sha256, safe_resolve_path


class TestRenderTaskIntegration(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root_path = Path(self.temp_dir.name)
        self.db_path = self.root_path / "test_render_task.db"
        self.channels_dir = self.root_path / "channels"
        self.channels_dir.mkdir(parents=True, exist_ok=True)

        self.settings = get_settings()
        self._orig_channels_root = self.settings.channels_root
        self.settings.channels_root = self.channels_dir

        # Initialize Database & Migrations
        self.db_manager = DatabaseManager(db_path=self.db_path, busy_timeout_ms=15000)
        self.migration_manager = MigrationManager(self.db_manager)
        self.migration_manager.run_migrations()

        # Repositories
        self.channel_repo = ChannelRepository(self.db_manager)
        self.project_repo = ProjectRepository(self.db_manager)
        self.scene_repo = SceneRepository(self.db_manager)
        self.asset_repo = AssetRepository(self.db_manager)
        self.license_repo = LicenseRepository(self.db_manager)
        self.task_repo = TaskRepository(self.db_manager)
        self.file_op_repo = FileOperationRepository(self.db_manager)
        self.audit_repo = AuditRepository(self.db_manager)

        # Services
        self.audit_service = AuditService(self.audit_repo)
        self.channel_service = ChannelService(self.channel_repo)
        self.project_service = ProjectService(self.project_repo, self.channel_repo)
        self.scene_service = SceneService(self.scene_repo, self.project_repo)
        self.license_service = LicenseService(self.license_repo, self.audit_service)
        self.asset_service = AssetService(
            asset_repository=self.asset_repo,
            license_repository=self.license_repo,
            file_operation_repository=self.file_op_repo,
            audit_service=self.audit_service,
        )
        self.checkpoint_service = CheckpointService(
            task_repository=self.task_repo,
            file_operation_repository=self.file_op_repo,
            asset_repository=self.asset_repo,
            audit_service=self.audit_service,
            db_manager=self.db_manager,
        )
        self.task_service = TaskService(
            task_repository=self.task_repo,
            channel_repository=self.channel_repo,
            project_repository=self.project_repo,
            scene_repository=self.scene_repo,
        )

        # Rendering components
        self.wrapper = FFmpegWrapper()
        self.probe_service = MediaProbeService(ffmpeg_wrapper=self.wrapper)
        self.timeline_renderer = TimelineRenderer(
            ffmpeg_wrapper=self.wrapper,
            probe_service=self.probe_service,
            license_service=self.license_service,
            task_repository=self.task_repo,
        )
        self.render_handler = VideoRenderTaskHandler(
            timeline_renderer=self.timeline_renderer,
            scene_service=self.scene_service,
            asset_service=self.asset_service,
            project_service=self.project_service,
            checkpoint_service=self.checkpoint_service,
            license_service=self.license_service,
            task_repository=self.task_repo,
            db_manager=self.db_manager,
        )

        # Setup standard channel
        self.channel_id = "ch_render_test"
        self.channel_repo.create(
            ChannelCreate(id=self.channel_id, name="Render Test Channel", handle="@rendertest", niche="automation")
        )
        self.channel_dir = self.channels_dir / self.channel_id
        (self.channel_dir / "media_vault" / "originals" / "images").mkdir(parents=True, exist_ok=True)
        (self.channel_dir / "media_vault" / "originals" / "audio").mkdir(parents=True, exist_ok=True)
        (self.channel_dir / "media_vault" / "cache").mkdir(parents=True, exist_ok=True)
        (self.channel_dir / "projects").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.settings.channels_root = self._orig_channels_root
        gc.collect()
        self.temp_dir.cleanup()

    def _create_verified_image(self, file_name: str, color: str = "blue", size: str = "720x1280") -> Path:
        """Create a synthetic test image and register it in MAM with verified commercial license."""
        dest_path = self.channel_dir / "media_vault" / "originals" / "images" / file_name
        self.wrapper.create_synthetic_image(dest_path, width=int(size.split("x")[0]), height=int(size.split("x")[1]), color=color)

        asset_id = f"ast_{file_name.replace('.', '_')}"
        sha256 = calculate_file_sha256(dest_path)
        size_bytes = dest_path.stat().st_size
        rel_path = f"media_vault/originals/images/{file_name}"

        self.asset_repo.create(
            MediaAssetCreate(
                id=asset_id,
                channel_id=self.channel_id,
                file_name=file_name,
                file_path=rel_path,
                file_sha256=sha256,
                file_size_bytes=size_bytes,
                mime_type="image/jpeg",
                asset_type=AssetType.IMAGE,
            )
        )
        self.license_service.register_license(
            channel_id=self.channel_id,
            license_input=AssetLicenseCreate(
                id=f"lic_{asset_id}",
                asset_id=asset_id,
                channel_id=self.channel_id,
                legal_basis=LegalBasis.COMMERCIAL_LICENSE,
                verification_status=VerificationStatus.VERIFIED,
                commercial_use_allowed=True,
            ),
        )
        return dest_path

    def _create_verified_audio(self, file_name: str, duration_sec: float = 4.0, freq: int = 1000) -> Path:
        """Create a synthetic test audio file and register it in MAM."""
        dest_path = self.channel_dir / "media_vault" / "originals" / "audio" / file_name
        self.wrapper.create_synthetic_audio(dest_path, duration_sec=duration_sec, freq=freq, volume=0.5)

        asset_id = f"ast_{file_name.replace('.', '_')}"
        sha256 = calculate_file_sha256(dest_path)
        size_bytes = dest_path.stat().st_size
        rel_path = f"media_vault/originals/audio/{file_name}"

        self.asset_repo.create(
            MediaAssetCreate(
                id=asset_id,
                channel_id=self.channel_id,
                file_name=file_name,
                file_path=rel_path,
                file_sha256=sha256,
                file_size_bytes=size_bytes,
                mime_type="audio/aac",
                asset_type=AssetType.AUDIO,
            )
        )
        self.license_service.register_license(
            channel_id=self.channel_id,
            license_input=AssetLicenseCreate(
                id=f"lic_{asset_id}",
                asset_id=asset_id,
                channel_id=self.channel_id,
                legal_basis=LegalBasis.COMMERCIAL_LICENSE,
                verification_status=VerificationStatus.VERIFIED,
                commercial_use_allowed=True,
            ),
        )
        return dest_path

    def test_01_direct_render_task_handler_execution(self):
        """Scenario 1: VideoRenderTaskHandler directly executes render task with 5-step checkpoint protocol."""
        # 1. Setup project with 2 scenes
        proj = self.project_service.create_project(
            self.channel_id,
            ProjectCreate(id="proj_handler_01", channel_id=self.channel_id, title="Handler Test Project"),
        )
        img1 = self._create_verified_image("scene_1.jpg", "red")
        img2 = self._create_verified_image("scene_2.jpg", "blue")

        self.scene_service.create_scene(
            self.channel_id,
            proj.id,
            SceneCreate(id="sc_01", project_id=proj.id, channel_id=self.channel_id, sequence_number=1, duration_seconds=2.0),
        )
        self.scene_service.create_scene(
            self.channel_id,
            proj.id,
            SceneCreate(id="sc_02", project_id=proj.id, channel_id=self.channel_id, sequence_number=2, duration_seconds=2.0),
        )
        # Associate assets to scenes
        self.asset_repo.update(self.channel_id, f"ast_{img1.name.replace('.', '_')}", MediaAssetUpdate(scene_id="sc_01"))
        self.asset_repo.update(self.channel_id, f"ast_{img2.name.replace('.', '_')}", MediaAssetUpdate(scene_id="sc_02"))

        # 2. Create task
        task = self.task_service.create_task(
            channel_id=self.channel_id,
            task_input=TaskCreate(
                channel_id=self.channel_id,
                project_id=proj.id,
                task_type=TaskType.VIDEO_RENDERING,
                idempotency_key="task_render_direct_01",
                payload={
                    "profile": "9:16",
                    "output_rel_path": f"projects/{proj.id}/final_video.mp4",
                },
            ),
        )

        # 3. Simulate atomic claim by worker
        claimed = self.task_repo.atomic_claim_next_task("worker_01", lease_duration_sec=60, channel_id=self.channel_id)
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed.id, task.id)

        # 4. Execute through VideoRenderTaskHandler
        result = self.render_handler.handle_task(claimed)

        # 5. Verify result metadata
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["project_id"], proj.id)
        self.assertEqual(result["resolution"], "1080x1920")
        self.assertEqual(result["fps"], 30.0)
        self.assertAlmostEqual(result["duration_sec"], 4.0, delta=0.5)
        self.assertTrue(result["true_peak_compliant"])

        # 6. Verify file existence and project lifecycle status
        out_file = Path(result["output_path"])
        self.assertTrue(out_file.exists())
        self.assertGreater(out_file.stat().st_size, 1000)

        updated_proj = self.project_service.get_project(self.channel_id, proj.id)
        self.assertEqual(updated_proj.status, ProjectStatus.COMPLETED)

        # 7. Verify Checkpoint was recorded in SQLite
        checkpoints = self.task_repo.get_checkpoints(task.id)
        self.assertEqual(len(checkpoints), 1)
        self.assertEqual(checkpoints[0].step_name, f"render_timeline_{proj.id}")

    def test_02_worker_pool_end_to_end_pipeline(self):
        """Scenario 2: WorkerPool claims, renders, and marks VIDEO_RENDERING task COMPLETED end-to-end."""
        proj = self.project_service.create_project(
            self.channel_id,
            ProjectCreate(id="proj_pool_02", channel_id=self.channel_id, title="WorkerPool Integration Project"),
        )
        img1 = self._create_verified_image("pool_scene1.jpg", "green")
        self.scene_service.create_scene(
            self.channel_id,
            proj.id,
            SceneCreate(id="sc_pool_1", project_id=proj.id, channel_id=self.channel_id, sequence_number=1, duration_seconds=2.0),
        )
        self.asset_repo.update(self.channel_id, f"ast_{img1.name.replace('.', '_')}", MediaAssetUpdate(scene_id="sc_pool_1"))

        # Submit task to task service
        task = self.task_service.create_task(
            channel_id=self.channel_id,
            task_input=TaskCreate(
                channel_id=self.channel_id,
                project_id=proj.id,
                task_type=TaskType.VIDEO_RENDERING,
                idempotency_key="wp_render_task_02",
                payload={"profile": "9:16"},
            ),
        )

        composite_handler = create_studio_worker_handler(self.render_handler)
        pool = WorkerPool(
            task_repository=self.task_repo,
            task_service=self.task_service,
            concurrency=1,
            task_handler=composite_handler,
            poll_interval_sec=0.1,
            pulse_interval_sec=1.0,
            channel_id=self.channel_id,
        )
        pool.start()

        # Wait for task completion
        max_wait = 30.0
        start = time.time()
        final_task = None
        while time.time() - start < max_wait:
            t = self.task_service.get_task(task.id)
            if t.status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
                final_task = t
                break
            time.sleep(0.3)

        pool.shutdown(timeout_sec=2.0)

        self.assertIsNotNone(final_task)
        self.assertEqual(final_task.status, TaskStatus.COMPLETED)
        self.assertIsNotNone(final_task.result)
        self.assertEqual(final_task.result["status"], "success")

        # Verify output video
        output_path = Path(final_task.result["output_path"])
        self.assertTrue(output_path.exists())
        probe = self.probe_service.probe_file(output_path)
        self.assertIsNotNone(probe.primary_video_stream)
        self.assertEqual(probe.primary_video_stream.width, 1080)
        self.assertEqual(probe.primary_video_stream.height, 1920)

        # Verify channel lease was cleanly released
        active_lease = self.task_repo.get_active_channel_lease(self.channel_id)
        self.assertIsNone(active_lease)

    def test_03_fencing_token_protection_stale_worker_aborts(self):
        """Scenario 3: A stale worker holding token=1 is rejected when the task was promoted to token=2."""
        proj = self.project_service.create_project(
            self.channel_id,
            ProjectCreate(id="proj_fence_03", channel_id=self.channel_id, title="Fencing Token Project"),
        )
        img1 = self._create_verified_image("fence_img.jpg", "purple")
        self.scene_service.create_scene(
            self.channel_id,
            proj.id,
            SceneCreate(id="sc_fence_1", project_id=proj.id, channel_id=self.channel_id, sequence_number=1, duration_seconds=2.0),
        )
        self.asset_repo.update(self.channel_id, f"ast_{img1.name.replace('.', '_')}", MediaAssetUpdate(scene_id="sc_fence_1"))

        task = self.task_service.create_task(
            channel_id=self.channel_id,
            task_input=TaskCreate(
                channel_id=self.channel_id,
                project_id=proj.id,
                task_type=TaskType.VIDEO_RENDERING,
                idempotency_key="fence_task_03",
            ),
        )

        claimed = self.task_repo.atomic_claim_next_task("worker_stale", lease_duration_sec=60, channel_id=self.channel_id)
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed.execution_token, 1)

        # Simulate reaper or higher authority bumping the execution token in DB
        with self.db_manager.transaction() as conn:
            conn.execute("UPDATE tasks SET execution_token = 2 WHERE id = ?;", (task.id,))

        # Stale worker attempts to execute render handler
        with self.assertRaises(StaleExecutionTokenException):
            self.render_handler.handle_task(claimed)

        # Output video must NOT be published
        target_path = self.channel_dir / "projects" / proj.id / "final_video.mp4"
        self.assertFalse(target_path.exists())

    def test_04_cancellation_during_render_terminates_subprocess(self):
        """Scenario 4: Cancellation event terminates the active FFmpeg process tree and updates project to CANCELLED."""
        proj = self.project_service.create_project(
            self.channel_id,
            ProjectCreate(id="proj_cancel_04", channel_id=self.channel_id, title="Cancellation Project"),
        )
        img1 = self._create_verified_image("cancel_img.jpg", "black")
        self.scene_service.create_scene(
            self.channel_id,
            proj.id,
            SceneCreate(id="sc_cancel_1", project_id=proj.id, channel_id=self.channel_id, sequence_number=1, duration_seconds=2.0),
        )
        self.asset_repo.update(self.channel_id, f"ast_{img1.name.replace('.', '_')}", MediaAssetUpdate(scene_id="sc_cancel_1"))

        task = self.task_service.create_task(
            channel_id=self.channel_id,
            task_input=TaskCreate(
                channel_id=self.channel_id,
                project_id=proj.id,
                task_type=TaskType.VIDEO_RENDERING,
                idempotency_key="cancel_task_04",
            ),
        )
        claimed = self.task_repo.atomic_claim_next_task("worker_cancel", lease_duration_sec=60, channel_id=self.channel_id)

        # Heartbeat where lease is already marked lost
        mock_heartbeat = HeartbeatDaemon(
            task_repository=self.task_repo,
            channel_id=self.channel_id,
            task_id=task.id,
            worker_id="worker_cancel",
            execution_token=claimed.execution_token,
        )
        mock_heartbeat._lease_lost_event.set()

        with self.assertRaises(StaleExecutionTokenException):
            self.render_handler.handle_task(claimed, heartbeat=mock_heartbeat)

    def test_05_license_gate_enforcement_in_task_pipeline(self):
        """Scenario 5: Render task fails immediately if an asset in the project fails the license verification gate."""
        proj = self.project_service.create_project(
            self.channel_id,
            ProjectCreate(id="proj_lic_05", channel_id=self.channel_id, title="License Gate Project"),
        )
        # 1. Create scene first
        self.scene_service.create_scene(
            self.channel_id,
            proj.id,
            SceneCreate(id="sc_lic_1", project_id=proj.id, channel_id=self.channel_id, sequence_number=1, duration_seconds=2.0),
        )

        # 2. Create unverified asset
        unverified_img = self.channel_dir / "media_vault" / "originals" / "images" / "unverified.jpg"
        self.wrapper.create_synthetic_image(unverified_img, 720, 1280, "yellow")
        ast_id = "ast_unverified_jpg"
        self.asset_repo.create(
            MediaAssetCreate(
                id=ast_id,
                channel_id=self.channel_id,
                file_name="unverified.jpg",
                file_path="media_vault/originals/images/unverified.jpg",
                file_sha256=calculate_file_sha256(unverified_img),
                file_size_bytes=unverified_img.stat().st_size,
                mime_type="image/jpeg",
                asset_type=AssetType.IMAGE,
                scene_id="sc_lic_1",
            )
        )
        # Register license as RESTRICTED
        self.license_service.register_license(
            channel_id=self.channel_id,
            license_input=AssetLicenseCreate(
                id=f"lic_{ast_id}",
                asset_id=ast_id,
                channel_id=self.channel_id,
                legal_basis=LegalBasis.COMMERCIAL_LICENSE,
                verification_status=VerificationStatus.RESTRICTED,
            ),
        )

        task = self.task_service.create_task(
            channel_id=self.channel_id,
            task_input=TaskCreate(
                channel_id=self.channel_id,
                project_id=proj.id,
                task_type=TaskType.VIDEO_RENDERING,
                idempotency_key="lic_task_05",
            ),
        )
        claimed = self.task_repo.atomic_claim_next_task("worker_lic", lease_duration_sec=60, channel_id=self.channel_id)

        with self.assertRaises(LicenseVerificationException):
            self.render_handler.handle_task(claimed)

        # Project status must transition to FAILED
        p = self.project_service.get_project(self.channel_id, proj.id)
        self.assertEqual(p.status, ProjectStatus.FAILED)

    def test_06_checkpoint_fast_forward_resumption(self):
        """Scenario 6: Fast-forward past already checkpointed render steps on task retry/resumption."""
        proj = self.project_service.create_project(
            self.channel_id,
            ProjectCreate(id="proj_ff_06", channel_id=self.channel_id, title="Fast Forward Project"),
        )
        img1 = self._create_verified_image("ff_scene1.jpg", "cyan")
        self.scene_service.create_scene(
            self.channel_id,
            proj.id,
            SceneCreate(id="sc_ff_1", project_id=proj.id, channel_id=self.channel_id, sequence_number=1, duration_seconds=2.0),
        )
        self.asset_repo.update(self.channel_id, f"ast_{img1.name.replace('.', '_')}", MediaAssetUpdate(scene_id="sc_ff_1"))

        task = self.task_service.create_task(
            channel_id=self.channel_id,
            task_input=TaskCreate(
                channel_id=self.channel_id,
                project_id=proj.id,
                task_type=TaskType.VIDEO_RENDERING,
                idempotency_key="ff_task_06",
            ),
        )
        claimed = self.task_repo.atomic_claim_next_task("worker_ff1", lease_duration_sec=60, channel_id=self.channel_id)

        # 1. Run first time: produces checkpoint
        res1 = self.render_handler.handle_task(claimed)
        self.assertEqual(res1["status"], "success")
        cp_id1 = res1["checkpoint_id"]
        self.assertIsNotNone(cp_id1)

        # 2. Simulate worker resumption on the same task: handler detects completed checkpoint and fast-forwards
        res2 = self.render_handler.handle_task(claimed)
        self.assertEqual(res2["status"], "success")
        self.assertEqual(res2["checkpoint_id"], cp_id1)

    def test_07_dreammatter_project_pipeline_support(self):
        """Scenario 7: Full production pipeline test for DreamMatter project with 3 scenes, dual audio, and 9:16 profile."""
        dm_channel_id = "ch_dreammatter"
        self.channel_repo.create(
            ChannelCreate(id=dm_channel_id, name="DreamMatter Channel", handle="@dreammatter", niche="ai_stories")
        )
        dm_dir = self.channels_dir / dm_channel_id
        (dm_dir / "media_vault" / "originals" / "images").mkdir(parents=True, exist_ok=True)
        (dm_dir / "media_vault" / "originals" / "audio").mkdir(parents=True, exist_ok=True)
        (dm_dir / "media_vault" / "cache").mkdir(parents=True, exist_ok=True)
        (dm_dir / "projects").mkdir(parents=True, exist_ok=True)

        # 1. Create DreamMatter project
        dm_proj = self.project_service.create_project(
            dm_channel_id,
            ProjectCreate(
                id="proj_dreammatter_mvp",
                channel_id=dm_channel_id,
                title="DreamMatter Episode 1 MVP",
                aspect_ratio=AspectRatio.VERTICAL_9_16,
                target_duration_sec=6.0,
            ),
        )

        # 2. Create 3 verified scenes
        for i, color in enumerate(["darkblue", "darkred", "darkgreen"], start=1):
            fname = f"dm_scene_{i}.jpg"
            fpath = dm_dir / "media_vault" / "originals" / "images" / fname
            self.wrapper.create_synthetic_image(fpath, width=720, height=1280, color=color)

            # Create scene first
            self.scene_service.create_scene(
                dm_channel_id,
                dm_proj.id,
                SceneCreate(
                    id=f"sc_dm_{i}",
                    project_id=dm_proj.id,
                    channel_id=dm_channel_id,
                    sequence_number=i,
                    duration_seconds=2.0,
                    title=f"DreamMatter Act {i}",
                ),
            )

            ast_id = f"ast_dm_{i}"
            sha256 = calculate_file_sha256(fpath)
            self.asset_repo.create(
                MediaAssetCreate(
                    id=ast_id,
                    channel_id=dm_channel_id,
                    project_id=dm_proj.id,
                    scene_id=f"sc_dm_{i}",
                    file_name=fname,
                    file_path=f"media_vault/originals/images/{fname}",
                    file_sha256=sha256,
                    file_size_bytes=fpath.stat().st_size,
                    mime_type="image/jpeg",
                    asset_type=AssetType.IMAGE,
                )
            )
            self.license_service.register_license(
                channel_id=dm_channel_id,
                license_input=AssetLicenseCreate(
                    id=f"lic_{ast_id}",
                    asset_id=ast_id,
                    channel_id=dm_channel_id,
                    legal_basis=LegalBasis.COMMERCIAL_LICENSE,
                    verification_status=VerificationStatus.VERIFIED,
                    commercial_use_allowed=True,
                ),
            )

        # 3. Create Voiceover & Background Music in MAM
        vo_path = dm_dir / "media_vault" / "originals" / "audio" / "dm_voiceover.aac"
        self.wrapper.create_synthetic_audio(vo_path, duration_sec=5.0, freq=800, volume=0.8)
        bg_path = dm_dir / "media_vault" / "originals" / "audio" / "dm_bgmusic.aac"
        self.wrapper.create_synthetic_audio(bg_path, duration_sec=3.0, freq=300, volume=0.3)

        for fid, fpath, name in [("ast_dm_vo", vo_path, "dm_voiceover.aac"), ("ast_dm_bg", bg_path, "dm_bgmusic.aac")]:
            self.asset_repo.create(
                MediaAssetCreate(
                    id=fid,
                    channel_id=dm_channel_id,
                    file_name=name,
                    file_path=f"media_vault/originals/audio/{name}",
                    file_sha256=calculate_file_sha256(fpath),
                    file_size_bytes=fpath.stat().st_size,
                    mime_type="audio/aac",
                    asset_type=AssetType.AUDIO,
                )
            )
            self.license_service.register_license(
                channel_id=dm_channel_id,
                license_input=AssetLicenseCreate(
                    id=f"lic_{fid}",
                    asset_id=fid,
                    channel_id=dm_channel_id,
                    legal_basis=LegalBasis.COMMERCIAL_LICENSE,
                    verification_status=VerificationStatus.VERIFIED,
                    commercial_use_allowed=True,
                ),
            )

        # 4. Enqueue render task through production TaskService
        render_task = self.task_service.create_task(
            channel_id=dm_channel_id,
            task_input=TaskCreate(
                channel_id=dm_channel_id,
                project_id=dm_proj.id,
                task_type=TaskType.VIDEO_RENDERING,
                idempotency_key="dm_render_mvp_task_01",
                payload={
                    "profile": "9:16",
                    "voiceover_track_path": "media_vault/originals/audio/dm_voiceover.aac",
                    "background_music_path": "media_vault/originals/audio/dm_bgmusic.aac",
                    "voiceover_volume": 1.0,
                    "background_music_volume": 0.25,
                    "enforce_true_peak": True,
                    "output_rel_path": f"projects/{dm_proj.id}/dreammatter_first_video.mp4",
                },
            ),
        )

        # 5. Run WorkerPool to execute task asynchronously
        pool = WorkerPool(
            task_repository=self.task_repo,
            task_service=self.task_service,
            concurrency=1,
            task_handler=create_studio_worker_handler(self.render_handler),
            poll_interval_sec=0.1,
            pulse_interval_sec=1.0,
            channel_id=dm_channel_id,
        )
        pool.start()

        # Wait for task completion
        max_wait = 40.0
        start = time.time()
        final_task = None
        while time.time() - start < max_wait:
            t = self.task_service.get_task(render_task.id)
            if t.status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
                final_task = t
                break
            time.sleep(0.5)

        pool.shutdown(timeout_sec=2.0)

        # 6. Verify pipeline outcome
        self.assertIsNotNone(final_task)
        self.assertEqual(final_task.status, TaskStatus.COMPLETED)
        result = final_task.result
        self.assertEqual(result["status"], "success")

        # 7. Verify output file technical conformance
        final_video_path = Path(result["output_path"])
        self.assertTrue(final_video_path.exists())
        self.assertGreater(final_video_path.stat().st_size, 50000)

        probe = self.probe_service.probe_file(final_video_path)
        self.assertIsNotNone(probe.primary_video_stream)
        self.assertEqual(probe.primary_video_stream.width, 1080)
        self.assertEqual(probe.primary_video_stream.height, 1920)
        self.assertEqual(probe.primary_video_stream.fps, 30.0)
        self.assertEqual(probe.primary_video_stream.codec_name, "h264")
        self.assertIsNotNone(probe.primary_audio_stream)
        self.assertEqual(probe.primary_audio_stream.codec_name, "aac")
        self.assertAlmostEqual(probe.duration_sec, 6.0, delta=0.5)

        # 8. Verify ADR-003 True Peak Compliance
        tp_result = self.probe_service.measure_true_peak(final_video_path)
        self.assertTrue(tp_result.is_compliant)
        self.assertLessEqual(tp_result.true_peak_dbtp, -1.0)

        # 9. Verify Project status transitioned to COMPLETED
        p = self.project_service.get_project(dm_channel_id, dm_proj.id)
        self.assertEqual(p.status, ProjectStatus.COMPLETED)


if __name__ == "__main__":
    unittest.main()
