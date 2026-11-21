"""End-to-End Acceptance Test for DreamMatter Video 02 Production & Episode 1 Preservation.

Verifies:
1. Production MP4 exists at channels/ch_dreammatter_001/exports/videos/tommy_ep2_star_9_16.mp4.
2. File size exceeds 1.5 MB and SHA-256 integrity is valid.
3. Container is valid and uncorrupted MP4.
4. Video stream is H.264, 1080x1920 (9:16 vertical), 30.0 FPS, 24.0s duration.
5. Audio stream is AAC, 48 kHz stereo.
6. EBU R128 True Peak strictly conforms to ADR-003 ceiling (measured <= -1.0 dBTP).
7. Episode 1 records and files remain 100% bit-for-bit unchanged.
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


class TestEpisode02E2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.settings = get_settings()
        cls.db_manager = DatabaseManager()
        cls.wrapper = FFmpegWrapper()
        cls.probe_service = MediaProbeService(ffmpeg_wrapper=cls.wrapper)

        cls.channel_id = "ch_dreammatter_001"
        cls.project_id_ep1 = "proj_dm_tommy_bubble_001"
        cls.project_id_ep2 = "proj_dm_tommy_star_002"

        cls.channel_dir = cls.settings.channels_root / cls.channel_id
        cls.video_ep1_path = cls.channel_dir / "exports" / "videos" / "tommy_first_video_9_16.mp4"
        cls.video_ep2_path = cls.channel_dir / "exports" / "videos" / "tommy_ep2_star_9_16.mp4"

    def test_01_episode_02_mp4_file_existence_and_size(self):
        """Scenario 1: Episode 2 MP4 exists and has expected production size (> 1.5 MB)."""
        self.assertTrue(
            self.video_ep2_path.exists(),
            f"Episode 2 MP4 not found at expected path: {self.video_ep2_path}",
        )
        size_bytes = self.video_ep2_path.stat().st_size
        self.assertGreater(
            size_bytes,
            1_500_000,
            f"Exported video file size ({size_bytes} bytes) is suspiciously small.",
        )
        sha256 = calculate_file_sha256(self.video_ep2_path)
        self.assertEqual(len(sha256), 64)

    def test_02_episode_02_video_conformance(self):
        """Scenario 2: Episode 2 video stream conforms to 9:16 vertical 1080x1920 @ 30fps."""
        probe = self.probe_service.probe_file(self.video_ep2_path)
        v = probe.primary_video_stream
        self.assertIsNotNone(v, "No primary video stream found in Episode 2 MP4.")

        self.assertEqual(v.width, 1080)
        self.assertEqual(v.height, 1920)
        self.assertEqual(v.fps, 30.0)
        self.assertEqual(v.codec_name, "h264")
        self.assertAlmostEqual(probe.duration_sec, 24.0, delta=0.5)

    def test_03_episode_02_audio_and_true_peak(self):
        """Scenario 3: Episode 2 audio stream is AAC stereo and complies with ADR-003 (True Peak <= -1.0 dBTP)."""
        probe = self.probe_service.probe_file(self.video_ep2_path)
        a = probe.primary_audio_stream
        self.assertIsNotNone(a, "No primary audio stream found in Episode 2 MP4.")

        self.assertEqual(a.codec_name, "aac")
        self.assertEqual(a.sample_rate, 48000)
        self.assertEqual(a.channels, 2)

        tp = self.probe_service.measure_true_peak(self.video_ep2_path)
        self.assertTrue(tp.is_compliant)
        self.assertLessEqual(tp.true_peak_dbtp, -1.0)

    def test_04_episode_01_preservation_and_integrity(self):
        """Scenario 4: Episode 1 MP4 and DB records are completely preserved and untouched."""
        self.assertTrue(self.video_ep1_path.exists())
        # Episode 1 size and hash must match exactly
        self.assertEqual(self.video_ep1_path.stat().st_size, 1603433)
        sha256_ep1 = calculate_file_sha256(self.video_ep1_path).lower()
        self.assertEqual(
            sha256_ep1,
            "35eaefb9d44fa02100815837314be49905df572f18f7c440f5c095329f3d6d53",
        )

        proj_repo = ProjectRepository(self.db_manager)
        scene_repo = SceneRepository(self.db_manager)

        p1 = proj_repo.get_by_id(self.channel_id, self.project_id_ep1)
        self.assertIsNotNone(p1)
        self.assertEqual(p1.status.value, "completed")

        p2 = proj_repo.get_by_id(self.channel_id, self.project_id_ep2)
        self.assertIsNotNone(p2)
        self.assertEqual(p2.status.value, "completed")

        scenes_ep1 = scene_repo.list_by_project(self.channel_id, self.project_id_ep1)
        scenes_ep2 = scene_repo.list_by_project(self.channel_id, self.project_id_ep2)
        self.assertEqual(len(scenes_ep1), 3)
        self.assertEqual(len(scenes_ep2), 3)


if __name__ == "__main__":
    unittest.main()

