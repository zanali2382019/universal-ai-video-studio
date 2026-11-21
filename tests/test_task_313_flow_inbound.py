"""Unit and integration tests for Google Flow Inbound Integration (Task 3.13).

Verifies:
1. MediaValidator technical validation with ffprobe & Pillow and corrupted file rejection.
2. MediaMatcher naming conventions, scene ID resolution, and take number parsing.
3. Google Takeout archive (.zip) and Flow JSON metadata parsing and ingestion.
4. Streaming SHA-256 deduplication and strict Idempotency (re-import produces SKIPPED_DUPLICATE).
5. Generation History, Edit Versions (Take 1 vs Take 2), and active take promotion.
6. Scene completion status tracking and missing scenes reporting.
7. Dry-run execution ensuring zero side effects in DB or storage.
"""

import os
import json
import zipfile
import tempfile
import unittest
from pathlib import Path
from PIL import Image

from src.core.config import get_settings
from src.database.connection import DatabaseManager
from src.database.migrations import MigrationManager
from src.database.models import ChannelCreate, ProjectCreate, SceneCreate
from src.database.repositories.channel_repository import ChannelRepository
from src.database.repositories.project_repository import ProjectRepository
from src.database.repositories.scene_repository import SceneRepository
from src.database.repositories.flow_inbound_repository import FlowInboundRepository
from src.storage.service import StorageService
from src.rendering.ffmpeg_wrapper import FFmpegWrapper
from src.rendering.probe_service import MediaProbeService
from src.flow.inbound.schemas import (
    InboundSourceType,
    InboundItemStatus,
)
from src.flow.inbound.validator import MediaValidator, compute_streaming_sha256
from src.flow.inbound.matcher import MediaMatcher
from src.flow.inbound.takeout_parser import TakeoutParser
from src.flow.inbound.importer import FlowInboundService


class TestTask313FlowInbound(unittest.TestCase):
    """Test suite for Google Flow Inbound Integration."""

    @classmethod
    def setUpClass(cls):
        cls.ffmpeg = FFmpegWrapper()

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_data_dir = Path(self.temp_dir.name)
        os.environ["STUDIO_DATA_DIR"] = str(self.test_data_dir)
        get_settings(reload=True)

        self.db_path = self.test_data_dir / "test_inbound.db"
        self.db_manager = DatabaseManager(db_path=self.db_path)
        self.migration_mgr = MigrationManager(self.db_manager)
        self.migration_mgr.run_migrations()

        self.storage = StorageService(local_base_dir=self.test_data_dir)
        self.probe = MediaProbeService(self.ffmpeg)
        self.validator = MediaValidator(self.probe)
        self.inbound_repo = FlowInboundRepository(self.db_manager)
        self.channel_repo = ChannelRepository(self.db_manager)
        self.project_repo = ProjectRepository(self.db_manager)
        self.scene_repo = SceneRepository(self.db_manager)

        self.inbound_service = FlowInboundService(
            db_manager=self.db_manager,
            storage_service=self.storage,
            validator=self.validator,
        )

        # Seed channel and project
        self.channel_id = "ch_test_dream"
        self.channel_repo.create(
            ChannelCreate(id=self.channel_id, name="Test Dream", handle="@TestDreamInbound", niche="Animation")
        )
        self.project_id = "proj_inbound_01"
        self.project_repo.create(
            ProjectCreate(id=self.project_id, channel_id=self.channel_id, title="Tommy Episode 3 Inbound Test")
        )

        # Seed 3 project scenes
        self.scene_ids = ["sc_test_01", "sc_test_02", "sc_test_03"]
        for idx, sid in enumerate(self.scene_ids, 1):
            self.scene_repo.create(
                SceneCreate(
                    id=sid,
                    project_id=self.project_id,
                    channel_id=self.channel_id,
                    sequence_number=idx,
                    title=f"Scene {idx}",
                    narration_text=f"Narration for scene {idx}",
                    visual_prompt=f"Visual prompt for scene {idx}",
                )
            )

    def tearDown(self):
        self.temp_dir.cleanup()
        get_settings(reload=True)

    def _create_synthetic_video(self, output_path: Path, color: str = "blue", duration_sec: float = 1.0) -> Path:
        """Helper to generate a lightweight valid MP4 clip via FFmpeg."""
        return self.ffmpeg.create_synthetic_video(
            output_path=output_path,
            duration_sec=duration_sec,
            width=320,
            height=240,
            fps=24,
            color=color,
        )

    def _create_synthetic_image(self, output_path: Path, color: str = "green", size=(320, 240)) -> Path:
        """Helper to generate a lightweight valid PNG image via Pillow."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        img = Image.new("RGB", size, color=color)
        img.save(output_path, format="PNG")
        return output_path

    def _create_corrupt_file(self, output_path: Path) -> Path:
        """Helper to generate an invalid / corrupted media file."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(b"CORRUPT_GARBAGE_HEADER_NOT_A_VALID_MEDIA_FILE_7777777777")
        return output_path

    # =========================================================================
    # Test 1: Technical Media Validation
    # =========================================================================
    def test_01_media_validation_video_image_and_corruption(self):
        """Test technical media validation for videos, images, and corrupted files."""
        # 1. Valid video
        vid_path = self.test_data_dir / "valid_clip.mp4"
        self._create_synthetic_video(vid_path, color="cyan", duration_sec=1.5)
        res_vid = self.validator.validate_file(vid_path)
        self.assertTrue(res_vid.is_valid)
        self.assertEqual(res_vid.media_type, "video")
        self.assertEqual(res_vid.width, 320)
        self.assertEqual(res_vid.height, 240)
        self.assertAlmostEqual(res_vid.duration_sec, 1.5, delta=0.2)
        self.assertTrue(len(res_vid.sha256) == 64)

        # 2. Valid image
        img_path = self.test_data_dir / "valid_ref.png"
        self._create_synthetic_image(img_path, color="yellow", size=(400, 300))
        res_img = self.validator.validate_file(img_path)
        self.assertTrue(res_img.is_valid)
        self.assertEqual(res_img.media_type, "image")
        self.assertEqual(res_img.width, 400)
        self.assertEqual(res_img.height, 300)

        # 3. Corrupt file
        bad_path = self.test_data_dir / "broken_clip.mp4"
        self._create_corrupt_file(bad_path)
        res_bad = self.validator.validate_file(bad_path)
        self.assertFalse(res_bad.is_valid)
        self.assertIn("Corrupt or unprobeable", str(res_bad.error_message))

    # =========================================================================
    # Test 2: Naming Convention Matcher
    # =========================================================================
    def test_02_matcher_naming_conventions_and_versions(self):
        """Test matching filenames to project scenes, sequence indices, and takes."""
        known_scenes = [
            {"id": "sc_test_01", "sequence_number": 1},
            {"id": "sc_test_02", "sequence_number": 2},
            {"id": "sc_test_03", "sequence_number": 3},
        ]
        matcher = MediaMatcher(known_scenes=known_scenes)

        # Exact scene_id match
        m1 = matcher.match_file("sc_test_01.mp4")
        self.assertIsNotNone(m1)
        self.assertEqual(m1.scene_id, "sc_test_01")
        self.assertEqual(m1.take_number, 1)
        self.assertEqual(m1.asset_role, "video")

        # Scene with version suffix _v2
        m2 = matcher.match_file("sc_test_02_v2.mp4")
        self.assertIsNotNone(m2)
        self.assertEqual(m2.scene_id, "sc_test_02")
        self.assertEqual(m2.take_number, 2)

        # Sequence-based matching (e.g. scene_3_take4.mp4)
        m3 = matcher.match_file("scene_3_take4.mp4")
        self.assertIsNotNone(m3)
        self.assertEqual(m3.scene_id, "sc_test_03")
        self.assertEqual(m3.take_number, 4)

        # Image asset role
        m4 = matcher.match_file("sc_test_01_keyframe.png")
        self.assertIsNotNone(m4)
        self.assertEqual(m4.scene_id, "sc_test_01")
        self.assertEqual(m4.asset_role, "image")

    # =========================================================================
    # Test 3: Takeout Archive (.zip) and Flow JSON Ingestion
    # =========================================================================
    def test_03_takeout_zip_archive_ingestion(self):
        """Test importing a Google Takeout zip archive containing media and JSON metadata."""
        # 1. Prepare synthetic assets in a temp staging dir
        staging_dir = self.test_data_dir / "staging_takeout"
        v1_path = self._create_synthetic_video(staging_dir / "clip_s1.mp4", color="green", duration_sec=1.0)
        v2_path = self._create_synthetic_video(staging_dir / "clip_s2.mp4", color="blue", duration_sec=1.0)
        ref_path = self._create_synthetic_image(staging_dir / "character_ref.png", color="purple")

        # 2. Create Takeout JSON metadata file
        metadata = {
            "flow_project_name": "Tommy Episode 3",
            "scenes": [
                {
                    "scene_id": "sc_test_01",
                    "file_path": "clip_s1.mp4",
                    "prompt": "Tommy in green field",
                    "take_number": 1,
                    "seed": 4242,
                    "flow_notes": "First generation batch",
                },
                {
                    "scene_id": "sc_test_02",
                    "file_path": "clip_s2.mp4",
                    "prompt": "Tommy looking at clouds",
                    "take_number": 1,
                },
                {
                    "scene_id": "sc_test_01",
                    "file_path": "character_ref.png",
                    "take_number": 1,
                    "asset_role": "image",
                },
            ],
        }
        with open(staging_dir / "takeout_manifest.json", "w", encoding="utf-8") as f:
            json.dump(metadata, f)

        # 3. Zip into Takeout archive
        zip_path = self.test_data_dir / "GoogleFlow_Takeout_Ep3.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.write(staging_dir / "takeout_manifest.json", "takeout_manifest.json")
            zf.write(v1_path, "clip_s1.mp4")
            zf.write(v2_path, "clip_s2.mp4")
            zf.write(ref_path, "character_ref.png")

        # 4. Ingest via FlowInboundService
        report = self.inbound_service.import_assets(
            channel_id=self.channel_id,
            project_id=self.project_id,
            source_path=zip_path,
        )

        self.assertEqual(report.source_type, InboundSourceType.TAKEOUT_ARCHIVE)
        self.assertEqual(report.imported_count, 3)
        self.assertEqual(report.duplicate_count, 0)
        self.assertEqual(report.failed_count, 0)

        # Scene 3 has no video yet: must be in missing_scenes
        self.assertIn("sc_test_03", report.missing_scenes)
        self.assertNotIn("sc_test_01", report.missing_scenes)
        self.assertNotIn("sc_test_02", report.missing_scenes)

        # Verify active takes in DB
        active_s1 = self.inbound_repo.get_active_take(self.project_id, "sc_test_01", "video")
        self.assertIsNotNone(active_s1)
        self.assertEqual(active_s1["take_number"], 1)
        self.assertTrue(active_s1["is_active"])
        self.assertIn("media_vault/flow_inbound", active_s1["storage_key"])

        # Verify storage existence
        self.assertTrue(self.storage.exists(active_s1["storage_key"]))

    # =========================================================================
    # Test 4: Streaming SHA-256 Deduplication & Idempotency
    # =========================================================================
    def test_04_idempotency_and_duplicate_detection(self):
        """Test that re-running import on the same source skips duplicates without creating new entries."""
        # 1. Create a drop folder with one video
        drop_dir = self.test_data_dir / "manual_drop_folder"
        vid_path = self._create_synthetic_video(drop_dir / "sc_test_01.mp4", color="magenta", duration_sec=1.0)

        # First import
        report1 = self.inbound_service.import_assets(
            channel_id=self.channel_id,
            project_id=self.project_id,
            source_path=drop_dir,
        )
        self.assertEqual(report1.imported_count, 1)
        self.assertEqual(report1.duplicate_count, 0)
        self.assertEqual(report1.items[0].status, InboundItemStatus.IMPORTED)

        # Count takes in database
        takes_after_first = self.inbound_repo.list_takes_for_scene(self.project_id, "sc_test_01", "video")
        self.assertEqual(len(takes_after_first), 1)

        # Second import on identical source (Idempotency test)
        report2 = self.inbound_service.import_assets(
            channel_id=self.channel_id,
            project_id=self.project_id,
            source_path=drop_dir,
        )
        self.assertEqual(report2.imported_count, 0)
        self.assertEqual(report2.duplicate_count, 1)
        self.assertEqual(report2.items[0].status, InboundItemStatus.SKIPPED_DUPLICATE)

        # Database rows MUST remain exactly 1
        takes_after_second = self.inbound_repo.list_takes_for_scene(self.project_id, "sc_test_01", "video")
        self.assertEqual(len(takes_after_second), 1)

    # =========================================================================
    # Test 5: Generation History & Edit Versioning (Take 1 vs Take 2)
    # =========================================================================
    def test_05_generation_history_edit_versions_and_active_promotion(self):
        """Test handling multiple takes/versions for a scene and switching active take."""
        drop_dir = self.test_data_dir / "versioning_drop"
        # Take 1
        self._create_synthetic_video(drop_dir / "sc_test_01.mp4", color="red", duration_sec=1.0)
        self.inbound_service.import_assets(
            channel_id=self.channel_id,
            project_id=self.project_id,
            source_path=drop_dir,
        )

        active_t1 = self.inbound_repo.get_active_take(self.project_id, "sc_test_01", "video")
        self.assertEqual(active_t1["take_number"], 1)

        # Take 2 (different color = different SHA-256)
        drop_v2 = self.test_data_dir / "versioning_drop_v2"
        self._create_synthetic_video(drop_v2 / "sc_test_01_v2.mp4", color="gold", duration_sec=1.2)
        report_v2 = self.inbound_service.import_assets(
            channel_id=self.channel_id,
            project_id=self.project_id,
            source_path=drop_v2,
        )
        self.assertEqual(report_v2.updated_version_count, 1)
        self.assertEqual(report_v2.items[0].status, InboundItemStatus.UPDATED_VERSION)

        # Verify two takes exist
        all_takes = self.inbound_repo.list_takes_for_scene(self.project_id, "sc_test_01", "video")
        self.assertEqual(len(all_takes), 2)

        # Verify Take 2 automatically became active
        active_now = self.inbound_repo.get_active_take(self.project_id, "sc_test_01", "video")
        self.assertEqual(active_now["take_number"], 2)

        # Switch active take back to Take 1
        success = self.inbound_service.set_active_take(self.project_id, "sc_test_01", take_number=1)
        self.assertTrue(success)

        active_reverted = self.inbound_repo.get_active_take(self.project_id, "sc_test_01", "video")
        self.assertEqual(active_reverted["take_number"], 1)

    # =========================================================================
    # Test 6: Rejection of Corrupt Media Files
    # =========================================================================
    def test_06_corrupt_media_file_rejection(self):
        """Test that invalid media files are rejected cleanly without breaking the batch."""
        mixed_dir = self.test_data_dir / "mixed_drop"
        # 1 valid clip
        self._create_synthetic_video(mixed_dir / "sc_test_01.mp4", color="blue", duration_sec=1.0)
        # 1 corrupt clip
        self._create_corrupt_file(mixed_dir / "sc_test_02.mp4")

        report = self.inbound_service.import_assets(
            channel_id=self.channel_id,
            project_id=self.project_id,
            source_path=mixed_dir,
        )

        self.assertEqual(report.imported_count, 1)
        self.assertEqual(report.failed_count, 1)

        # Valid scene is imported
        self.assertIsNotNone(self.inbound_repo.get_active_take(self.project_id, "sc_test_01", "video"))
        # Corrupt scene is NOT imported
        self.assertIsNone(self.inbound_repo.get_active_take(self.project_id, "sc_test_02", "video"))

    # =========================================================================
    # Test 7: Dry-Run Execution
    # =========================================================================
    def test_07_dry_run_execution(self):
        """Test that dry-run validates and previews without modifying database or storage."""
        dry_dir = self.test_data_dir / "dry_run_drop"
        self._create_synthetic_video(dry_dir / "sc_test_01.mp4", color="orange", duration_sec=1.0)

        report = self.inbound_service.import_assets(
            channel_id=self.channel_id,
            project_id=self.project_id,
            source_path=dry_dir,
            dry_run=True,
        )

        self.assertTrue(report.is_dry_run)
        self.assertEqual(report.imported_count, 1)

        # In DB: 0 takes should be written
        takes = self.inbound_repo.list_takes_for_project(self.project_id)
        self.assertEqual(len(takes), 0)

        # In DB: 0 inbound session records should be written
        sessions = self.inbound_repo.list_inbound_records_for_project(self.project_id)
        self.assertEqual(len(sessions), 0)


if __name__ == "__main__":
    unittest.main()
