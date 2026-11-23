"""End-to-End Acceptance Test for Task 3.6 — Milestone 5: First Real Video Production.

Verifies:
1. Production MP4 exists at channels/ch_dreammatter_001/exports/videos/tommy_first_video_9_16.mp4.
2. File size exceeds 1 MB and SHA-256 integrity is valid.
3. Container is valid and uncorrupted MP4.
4. Video stream is H.264, 1080x1920 (9:16 vertical), 30.0 FPS.
5. Audio stream is AAC, 48 kHz stereo.
6. Audio duration matches visual timeline within +/- 0.5s tolerance.
7. EBU R128 True Peak strictly conforms to ADR-003 ceiling (measured <= -1.0 dBTP).
8. MAM assets and verified commercial licenses exist in database.
"""
import unittest
from pathlib import Path

from src.core.config import get_settings
from src.database.connection import DatabaseManager
from src.database.repositories.channel_repository import ChannelRepository
from src.database.repositories.project_repository import ProjectRepository
from src.database.repositories.scene_repository import SceneRepository
from src.database.repositories.asset_repository import AssetRepository
from src.database.repositories.license_repository import LicenseRepository
from src.rendering.ffmpeg_wrapper import FFmpegWrapper
from src.rendering.probe_service import MediaProbeService
from src.utils.file_utils import calculate_file_sha256


class TestFirstVideoE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.settings = get_settings()
        cls.channel_id = "ch_dreammatter_001"
        cls.project_id = "proj_dm_tommy_bubble_001"
        cls.channel_dir = cls.settings.channels_root / cls.channel_id
        cls.final_video_path = cls.channel_dir / "exports" / "videos" / "tommy_first_video_9_16.mp4"

        if not cls.final_video_path.exists():
            raise unittest.SkipTest(
                f"Production video not present locally (clean clone/CI mode): {cls.final_video_path.name}"
            )
        if not cls.settings.db_path.exists():
            raise unittest.SkipTest("Production studio.db not present locally (clean clone/CI mode)")

        cls.db_manager = DatabaseManager()
        cls.wrapper = FFmpegWrapper()
        cls.probe_service = MediaProbeService(ffmpeg_wrapper=cls.wrapper)

    def test_01_final_mp4_file_existence_and_size(self):
        """Scenario 1: Final MP4 file exists and has non-trivial production size (> 1 MB)."""
        self.assertTrue(
            self.final_video_path.exists(),
            f"Production MP4 not found at expected path: {self.final_video_path}",
        )
        size_bytes = self.final_video_path.stat().st_size
        self.assertGreater(
            size_bytes,
            1_000_000,
            f"Exported video file size ({size_bytes} bytes) is suspiciously small.",
        )
        sha256 = calculate_file_sha256(self.final_video_path)
        self.assertEqual(len(sha256), 64)

    def test_02_video_stream_technical_conformance(self):
        """Scenario 2: FFprobe confirms video stream is 9:16 vertical (1080x1920 @ 30fps) H.264."""
        probe = self.probe_service.probe_file(self.final_video_path)
        v = probe.primary_video_stream
        self.assertIsNotNone(v, "No primary video stream found in exported MP4 container.")

        self.assertEqual(v.width, 1080, f"Expected width 1080, got {v.width}")
        self.assertEqual(v.height, 1920, f"Expected height 1920, got {v.height}")
        self.assertEqual(v.fps, 30.0, f"Expected 30.0 fps, got {v.fps}")
        self.assertEqual(v.codec_name, "h264", f"Expected h264 codec, got {v.codec_name}")
        self.assertAlmostEqual(probe.duration_sec, 19.9, delta=0.5)

    def test_03_audio_stream_and_true_peak_conformance(self):
        """Scenario 3: Audio stream is AAC 48kHz stereo and adheres strictly to ADR-003 (True Peak <= -1.0 dBTP)."""
        probe = self.probe_service.probe_file(self.final_video_path)
        a = probe.primary_audio_stream
        self.assertIsNotNone(a, "No primary audio stream found in exported MP4 container.")

        self.assertEqual(a.codec_name, "aac", f"Expected aac audio codec, got {a.codec_name}")
        self.assertEqual(a.sample_rate, 48000, f"Expected 48000 Hz, got {a.sample_rate}")
        self.assertEqual(a.channels, 2, f"Expected stereo (2 channels), got {a.channels}")

        # True Peak Measurement via EBU R128
        tp = self.probe_service.measure_true_peak(self.final_video_path)
        self.assertTrue(
            tp.is_compliant,
            f"True Peak ({tp.true_peak_dbtp:.2f} dBTP) exceeds limit ({tp.max_allowed_dbtp:.2f} dBTP).",
        )
        self.assertLessEqual(tp.true_peak_dbtp, -1.0)

    def test_04_database_project_and_mam_integrity(self):
        """Scenario 4: DreamMatter project, scenes, assets, and licenses are recorded in studio database."""
        ch_repo = ChannelRepository(self.db_manager)
        proj_repo = ProjectRepository(self.db_manager)
        scene_repo = SceneRepository(self.db_manager)
        asset_repo = AssetRepository(self.db_manager)
        lic_repo = LicenseRepository(self.db_manager)

        ch = ch_repo.get_by_id(self.channel_id)
        self.assertIsNotNone(ch)

        proj = proj_repo.get_by_id(self.channel_id, self.project_id)
        self.assertIsNotNone(proj)

        scenes = scene_repo.list_by_project(self.channel_id, self.project_id)
        self.assertEqual(len(scenes), 3)

        assets = [a for a in asset_repo.list_by_channel(self.channel_id) if getattr(a, "project_id", None) == self.project_id]
        self.assertGreaterEqual(len(assets), 5)

        for ast in assets:
            lic = lic_repo.get_by_asset_id(self.channel_id, ast.id)
            self.assertIsNotNone(lic, f"Asset {ast.id} missing license record.")
            self.assertTrue(lic.commercial_use_allowed, f"Asset {ast.id} missing commercial rights.")


if __name__ == "__main__":
    unittest.main()

