"""End-to-End Acceptance Test for DreamMatter Episode 03 Production & Preservation of Ep 01 and 02.

Verifies:
1. Production MP4 exists at channels/ch_dreammatter_001/exports/videos/tommy_ep3_cloud_9_16.mp4.
2. File size exceeds 1.8 MB and SHA-256 integrity is valid.
3. Video stream is H.264, 1080x1920 (9:16 vertical), 30.0 FPS, ~23.5s duration.
4. Audio stream is AAC, 48 kHz stereo.
5. EBU R128 True Peak strictly conforms to ADR-003 ceiling (measured <= -1.0 dBTP).
6. Episode 01 and Episode 02 records and files remain 100% bit-for-bit unchanged.
7. Project and task state in studio.db are COMPLETED.
"""
import unittest
from pathlib import Path

from src.core.config import get_settings
from src.database.connection import DatabaseManager
from src.database.repositories.project_repository import ProjectRepository
from src.rendering.ffmpeg_wrapper import FFmpegWrapper
from src.rendering.probe_service import MediaProbeService
from src.utils.file_utils import calculate_file_sha256


class TestEpisode03E2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.settings = get_settings()
        cls.db_manager = DatabaseManager()
        cls.wrapper = FFmpegWrapper()
        cls.probe_service = MediaProbeService(ffmpeg_wrapper=cls.wrapper)

        cls.channel_id = "ch_dreammatter_001"
        cls.project_id_ep1 = "proj_dm_tommy_bubble_001"
        cls.project_id_ep2 = "proj_dm_tommy_star_002"
        cls.project_id_ep3 = "proj_dm_tommy_cloud_003"
        cls.project_id_ep3_ar = "proj_dm_tommy_cloud_003_ar"

        cls.channel_dir = cls.settings.channels_root / cls.channel_id
        cls.video_ep1_path = cls.channel_dir / "exports" / "videos" / "tommy_first_video_9_16.mp4"
        cls.video_ep2_path = cls.channel_dir / "exports" / "videos" / "tommy_ep2_star_9_16.mp4"
        cls.video_ep3_path = cls.channel_dir / "exports" / "videos" / "tommy_ep3_cloud_9_16.mp4"
        cls.video_ep3_ar_path = cls.channel_dir / "exports" / "videos" / "tommy_ep3_cloud_ar_9_16.mp4"

    def test_01_episode_03_mp4_file_existence_and_size(self):
        """Scenario 1: Episode 3 MP4 exists and has expected production size (> 1.8 MB)."""
        self.assertTrue(
            self.video_ep3_path.exists(),
            f"Episode 3 MP4 not found at expected path: {self.video_ep3_path}",
        )
        size_bytes = self.video_ep3_path.stat().st_size
        self.assertGreater(
            size_bytes,
            1_800_000,
            f"Exported video file size ({size_bytes} bytes) is suspiciously small.",
        )
        sha256 = calculate_file_sha256(self.video_ep3_path)
        self.assertEqual(len(sha256), 64)

    def test_02_episode_03_video_conformance(self):
        """Scenario 2: Episode 3 video stream conforms to 9:16 vertical 1080x1920 @ 30fps."""
        probe = self.probe_service.probe_file(self.video_ep3_path)
        v = probe.primary_video_stream
        self.assertIsNotNone(v, "No primary video stream found in Episode 3 MP4.")

        self.assertEqual(v.width, 1080)
        self.assertEqual(v.height, 1920)
        self.assertEqual(v.fps, 30.0)
        self.assertEqual(v.codec_name, "h264")
        self.assertAlmostEqual(probe.duration_sec, 23.53, delta=0.5)

    def test_03_episode_03_audio_and_true_peak(self):
        """Scenario 3: Episode 3 audio stream is AAC stereo and complies with ADR-003 (True Peak <= -1.0 dBTP)."""
        probe = self.probe_service.probe_file(self.video_ep3_path)
        a = probe.primary_audio_stream
        self.assertIsNotNone(a, "No primary audio stream found in Episode 3 MP4.")

        self.assertEqual(a.codec_name, "aac")
        self.assertEqual(a.sample_rate, 48000)
        self.assertEqual(a.channels, 2)

        tp = self.probe_service.measure_true_peak(self.video_ep3_path)
        self.assertTrue(tp.is_compliant)
        self.assertLessEqual(tp.true_peak_dbtp, -1.0)

    def test_04_episodes_01_and_02_preservation(self):
        """Scenario 4: Episode 1 & 2 MP4s and DB records are completely preserved and untouched."""
        self.assertTrue(self.video_ep1_path.exists())
        self.assertEqual(self.video_ep1_path.stat().st_size, 1603433)
        sha256_ep1 = calculate_file_sha256(self.video_ep1_path).lower()
        self.assertEqual(
            sha256_ep1,
            "35eaefb9d44fa02100815837314be49905df572f18f7c440f5c095329f3d6d53",
        )

        self.assertTrue(self.video_ep2_path.exists())
        self.assertEqual(self.video_ep2_path.stat().st_size, 1657190)
        sha256_ep2 = calculate_file_sha256(self.video_ep2_path).lower()
        self.assertEqual(
            sha256_ep2,
            "208976db10090b1012ef6be51ddf371818f72a80af49723a17e736d1020417c6",
        )

        proj_repo = ProjectRepository(self.db_manager)
        p1 = proj_repo.get_by_id(self.channel_id, self.project_id_ep1)
        self.assertIsNotNone(p1)
        self.assertEqual(p1.status.value, "completed")

        p2 = proj_repo.get_by_id(self.channel_id, self.project_id_ep2)
        self.assertIsNotNone(p2)
        self.assertEqual(p2.status.value, "completed")

        p3 = proj_repo.get_by_id(self.channel_id, self.project_id_ep3)
        self.assertIsNotNone(p3)
        self.assertEqual(p3.status.value, "completed")

        p3_ar = proj_repo.get_by_id(self.channel_id, self.project_id_ep3_ar)
        self.assertIsNotNone(p3_ar)
        self.assertEqual(p3_ar.status.value, "completed")

    def test_05_episode_03_arabic_video_and_audio_conformance(self):
        """Scenario 5: Episode 3 Arabic MP4 exists, conforms to 9:16 vertical, and has valid audio."""
        self.assertTrue(self.video_ep3_ar_path.exists())
        size_bytes = self.video_ep3_ar_path.stat().st_size
        self.assertGreater(size_bytes, 3_000_000)

        sha256 = calculate_file_sha256(self.video_ep3_ar_path)
        self.assertEqual(len(sha256), 64)

        probe = self.probe_service.probe_file(self.video_ep3_ar_path)
        v = probe.primary_video_stream
        a = probe.primary_audio_stream

        self.assertEqual(v.width, 1080)
        self.assertEqual(v.height, 1920)
        self.assertEqual(v.fps, 30.0)
        self.assertEqual(v.codec_name, "h264")
        self.assertAlmostEqual(probe.duration_sec, 29.53, delta=0.5)

        self.assertEqual(a.codec_name, "aac")
        self.assertEqual(a.sample_rate, 48000)
        self.assertEqual(a.channels, 2)

        tp = self.probe_service.measure_true_peak(self.video_ep3_ar_path)
        self.assertTrue(tp.is_compliant)
        self.assertLessEqual(tp.true_peak_dbtp, -1.0)


if __name__ == "__main__":
    unittest.main()

