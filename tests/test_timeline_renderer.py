"""Comprehensive unit and integration tests for TimelineRenderer and RenderProfiles (Task 3.6 — Milestone 2)."""
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from src.core.exceptions import (
    TimelineRenderException,
    MediaValidationException,
    StaleExecutionTokenException,
    RenderCancelledException,
    LicenseVerificationException,
)
from src.rendering.ffmpeg_wrapper import FFmpegWrapper
from src.rendering.probe_service import MediaProbeService
from src.rendering.profiles import (
    RenderProfile,
    VERTICAL_9_16,
    HORIZONTAL_16_9,
    SQUARE_1_1,
    get_profile,
)
from src.rendering.timeline_renderer import (
    SceneRenderItem,
    TimelineRenderer,
)
from src.utils.file_utils import calculate_file_sha256


class TestTimelineRenderer(unittest.TestCase):
    """Test suite verifying RenderProfiles, scene normalization, timeline concatenation, and safety gates."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.test_dir = Path(self.temp_dir.name)
        self.wrapper = FFmpegWrapper(default_timeout_sec=30.0)
        self.probe_service = MediaProbeService(ffmpeg_wrapper=self.wrapper)
        self.renderer = TimelineRenderer(
            ffmpeg_wrapper=self.wrapper,
            probe_service=self.probe_service,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_01_render_profile_definitions_and_lookup(self):
        """Scenario 1: Standard profiles (9:16, 16:9, 1:1) are correctly configured and discoverable."""
        # Vertical 9:16 (Primary)
        self.assertEqual(VERTICAL_9_16.width, 1080)
        self.assertEqual(VERTICAL_9_16.height, 1920)
        self.assertEqual(VERTICAL_9_16.fps, 30.0)
        self.assertEqual(VERTICAL_9_16.aspect_ratio, "9:16")
        self.assertEqual(VERTICAL_9_16.resolution_str, "1080x1920")

        # Horizontal 16:9
        self.assertEqual(HORIZONTAL_16_9.width, 1920)
        self.assertEqual(HORIZONTAL_16_9.height, 1080)
        self.assertEqual(HORIZONTAL_16_9.fps, 30.0)
        self.assertEqual(HORIZONTAL_16_9.aspect_ratio, "16:9")

        # Square 1:1
        self.assertEqual(SQUARE_1_1.width, 1080)
        self.assertEqual(SQUARE_1_1.height, 1080)
        self.assertEqual(SQUARE_1_1.aspect_ratio, "1:1")

        # Profile registry lookup
        self.assertIs(get_profile("vertical_9_16"), VERTICAL_9_16)
        self.assertIs(get_profile("9:16"), VERTICAL_9_16)
        self.assertIs(get_profile("horizontal_16_9"), HORIZONTAL_16_9)
        self.assertIs(get_profile("16:9"), HORIZONTAL_16_9)
        self.assertIs(get_profile("square_1_1"), SQUARE_1_1)
        self.assertIs(get_profile("1:1"), SQUARE_1_1)

        with self.assertRaises(ValueError):
            get_profile("unknown_aspect_ratio_xyz")

    def test_02_normalize_single_image_to_9_16_video(self):
        """Scenario 2: Normalizes a static image to a 9:16 (1080x1920 @ 30fps) video segment."""
        # Create non-standard square image
        img_path = self.wrapper.create_synthetic_image(
            self.test_dir / "square_input.png",
            width=640,
            height=640,
            color="red",
        )
        seg_output = self.test_dir / "normalized_img_seg.mp4"

        res_path = self.renderer.normalize_scene(
            input_path=img_path,
            duration_sec=2.0,
            output_path=seg_output,
            profile=VERTICAL_9_16,
        )

        self.assertTrue(res_path.exists())
        probe = self.probe_service.probe_file(res_path)
        self.assertIsNotNone(probe.primary_video_stream)
        v = probe.primary_video_stream
        self.assertEqual(v.width, 1080)
        self.assertEqual(v.height, 1920)
        self.assertEqual(v.fps, 30.0)
        self.assertEqual(v.codec_name, "h264")
        self.assertAlmostEqual(probe.duration_sec, 2.0, delta=0.2)

    def test_03_normalize_single_video_to_9_16_video(self):
        """Scenario 3: Normalizes a video clip (1280x720) to 9:16 (1080x1920 @ 30fps) with safe padding."""
        raw_vid = self.wrapper.create_synthetic_video(
            self.test_dir / "raw_720p.mp4",
            width=1280,
            height=720,
            duration_sec=2.0,
            fps=24,  # Non-standard 24fps
        )
        seg_output = self.test_dir / "normalized_vid_seg.mp4"

        res_path = self.renderer.normalize_scene(
            input_path=raw_vid,
            duration_sec=1.5,
            output_path=seg_output,
            profile=VERTICAL_9_16,
        )

        self.assertTrue(res_path.exists())
        probe = self.probe_service.probe_file(res_path)
        v = probe.primary_video_stream
        self.assertEqual(v.width, 1080)
        self.assertEqual(v.height, 1920)
        self.assertEqual(v.fps, 30.0)  # Conformed to 30fps
        self.assertEqual(v.codec_name, "h264")
        self.assertAlmostEqual(probe.duration_sec, 1.5, delta=0.2)

    def test_04_multi_scene_timeline_rendering_hard_cuts(self):
        """Scenario 4: Sequences 3 scenes with hard cuts into a single playable 9:16 MP4 video."""
        img1 = self.wrapper.create_synthetic_image(self.test_dir / "s1.png", color="red")
        img2 = self.wrapper.create_synthetic_image(self.test_dir / "s2.png", color="green")
        img3 = self.wrapper.create_synthetic_image(self.test_dir / "s3.png", color="blue")

        scenes = [
            SceneRenderItem(asset_path=img1, duration_sec=1.5, transition="cut"),
            SceneRenderItem(asset_path=img2, duration_sec=2.0, transition="cut"),
            SceneRenderItem(asset_path=img3, duration_sec=1.5, transition="cut"),
        ]

        out_video = self.test_dir / "timeline_cuts.mp4"
        res = self.renderer.render_timeline(scenes=scenes, output_path=out_video, profile=VERTICAL_9_16)

        self.assertTrue(res.exists())
        probe = self.probe_service.probe_file(res)
        self.assertTrue(probe.has_video)
        self.assertTrue(probe.has_audio)  # Silent audio container conforms
        self.assertEqual(probe.primary_video_stream.width, 1080)
        self.assertEqual(probe.primary_video_stream.height, 1920)
        self.assertEqual(probe.primary_video_stream.fps, 30.0)
        self.assertAlmostEqual(probe.duration_sec, 5.0, delta=0.3)

    def test_05_multi_scene_timeline_rendering_fade_transitions(self):
        """Scenario 5: Sequences 3 scenes with fade transitions and verifies offset duration math."""
        img1 = self.wrapper.create_synthetic_image(self.test_dir / "fade1.png", color="red")
        img2 = self.wrapper.create_synthetic_image(self.test_dir / "fade2.png", color="green")
        img3 = self.wrapper.create_synthetic_image(self.test_dir / "fade3.png", color="blue")

        # 3 scenes: 2.0s each with 0.4s fade between (1->2 and 2->3)
        # Expected total duration = 2.0 + 2.0 + 2.0 - (0.4 * 2) = 5.2s
        scenes = [
            SceneRenderItem(asset_path=img1, duration_sec=2.0, transition="fade", transition_duration_sec=0.4),
            SceneRenderItem(asset_path=img2, duration_sec=2.0, transition="fade", transition_duration_sec=0.4),
            SceneRenderItem(asset_path=img3, duration_sec=2.0, transition="fade", transition_duration_sec=0.4),
        ]

        out_video = self.test_dir / "timeline_fade.mp4"
        res = self.renderer.render_timeline(scenes=scenes, output_path=out_video, profile=VERTICAL_9_16)

        self.assertTrue(res.exists())
        probe = self.probe_service.probe_file(res)
        self.assertEqual(probe.primary_video_stream.width, 1080)
        self.assertEqual(probe.primary_video_stream.height, 1920)
        self.assertAlmostEqual(probe.duration_sec, 5.2, delta=0.3)

    def test_06_timeline_rendering_with_external_audio(self):
        """Scenario 6: Muxes an external audio track (voiceover) onto the video timeline."""
        img1 = self.wrapper.create_synthetic_image(self.test_dir / "aud_s1.png", color="cyan")
        img2 = self.wrapper.create_synthetic_image(self.test_dir / "aud_s2.png", color="magenta")

        scenes = [
            SceneRenderItem(asset_path=img1, duration_sec=2.0, transition="cut"),
            SceneRenderItem(asset_path=img2, duration_sec=2.0, transition="cut"),
        ]

        audio_path = self.wrapper.create_synthetic_audio(
            self.test_dir / "voiceover.aac",
            duration_sec=4.0,
            sample_rate=48000,
        )

        out_video = self.test_dir / "timeline_with_audio.mp4"
        res = self.renderer.render_timeline(
            scenes=scenes,
            output_path=out_video,
            profile=VERTICAL_9_16,
            audio_track_path=audio_path,
        )

        self.assertTrue(res.exists())
        probe = self.probe_service.probe_file(res)
        self.assertTrue(probe.has_audio)
        self.assertEqual(probe.primary_audio_stream.sample_rate, 48000)
        self.assertEqual(probe.primary_audio_stream.channels, 2)
        self.assertAlmostEqual(probe.duration_sec, 4.0, delta=0.3)

    def test_07_license_gate_enforcement(self):
        """Scenario 7: License gate halts rendering if an asset fails commercial rights validation."""
        mock_license_svc = MagicMock()
        mock_license_svc.check_export_eligibility.side_effect = LicenseVerificationException(
            asset_id="asset_unverified_123",
            reason="License verification status is pending_review.",
        )

        renderer = TimelineRenderer(
            ffmpeg_wrapper=self.wrapper,
            probe_service=self.probe_service,
            license_service=mock_license_svc,
        )

        img = self.wrapper.create_synthetic_image(self.test_dir / "lic_test.png")
        scenes = [
            SceneRenderItem(asset_path=img, duration_sec=2.0, asset_id="asset_unverified_123"),
        ]

        out_video = self.test_dir / "blocked_output.mp4"
        with self.assertRaises(LicenseVerificationException):
            renderer.render_timeline(
                scenes=scenes,
                output_path=out_video,
                channel_id="ch_test",
            )

        self.assertFalse(out_video.exists(), "Blocked output video must never be published.")

    def test_08_task_fencing_protection(self):
        """Scenario 8: Stale worker with bumped execution token is fenced and cannot commit output."""
        mock_task_repo = MagicMock()
        mock_task = MagicMock()
        mock_task.execution_token = 5  # DB token was bumped to 5 by reaper
        mock_task_repo.get_by_id.return_value = mock_task

        renderer = TimelineRenderer(
            ffmpeg_wrapper=self.wrapper,
            probe_service=self.probe_service,
            task_repository=mock_task_repo,
        )

        img = self.wrapper.create_synthetic_image(self.test_dir / "fence_test.png")
        scenes = [SceneRenderItem(asset_path=img, duration_sec=1.0)]
        out_video = self.test_dir / "fenced_output.mp4"

        # Worker attempts to render with stale token = 3
        with self.assertRaises(StaleExecutionTokenException):
            renderer.render_timeline(
                scenes=scenes,
                output_path=out_video,
                task_id="tsk_test_123",
                execution_token=3,
            )

        self.assertFalse(out_video.exists(), "Fenced task output must not be committed to disk.")

    def test_09_original_media_protection(self):
        """Scenario 9: Source media in MAM is strictly read-only and remains byte-for-byte unmodified."""
        img1 = self.wrapper.create_synthetic_image(self.test_dir / "orig1.png", color="orange")
        img2 = self.wrapper.create_synthetic_image(self.test_dir / "orig2.png", color="purple")

        hash1_before = calculate_file_sha256(img1)
        hash2_before = calculate_file_sha256(img2)

        scenes = [
            SceneRenderItem(asset_path=img1, duration_sec=1.0),
            SceneRenderItem(asset_path=img2, duration_sec=1.0),
        ]
        out_video = self.test_dir / "readonly_test.mp4"
        self.renderer.render_timeline(scenes, out_video)

        # Re-compute hashes of original source files
        hash1_after = calculate_file_sha256(img1)
        hash2_after = calculate_file_sha256(img2)

        self.assertEqual(hash1_before, hash1_after, "Original asset 1 was modified!")
        self.assertEqual(hash2_before, hash2_after, "Original asset 2 was modified!")

    def test_10_safe_atomic_output_handling(self):
        """Scenario 10: Ensures clean atomic replacement with zero dangling temp files."""
        img = self.wrapper.create_synthetic_image(self.test_dir / "clean_test.png")
        scenes = [SceneRenderItem(asset_path=img, duration_sec=1.0)]
        out_video = self.test_dir / "atomic_clean.mp4"

        self.renderer.render_timeline(scenes, out_video)

        self.assertTrue(out_video.exists())
        # Assert no dangling .tmp files or _render_cache_ directories exist
        temp_files = list(self.test_dir.glob("*.tmp*"))
        temp_dirs = list(self.test_dir.glob("_render_cache_*"))
        self.assertEqual(len(temp_files), 0, f"Dangling temp files found: {temp_files}")
        self.assertEqual(len(temp_dirs), 0, f"Dangling temp cache dirs found: {temp_dirs}")

    def test_11_cancellation_during_render(self):
        """Scenario 11: Cancellation token aborts timeline rendering cleanly."""
        cancel_token = threading.Event()
        cancel_token.set()  # Cancelled prior to execution

        img = self.wrapper.create_synthetic_image(self.test_dir / "cancel_test.png")
        scenes = [SceneRenderItem(asset_path=img, duration_sec=2.0)]
        out_video = self.test_dir / "cancelled_output.mp4"

        with self.assertRaises(RenderCancelledException):
            self.renderer.render_timeline(
                scenes=scenes,
                output_path=out_video,
                cancellation_token=cancel_token,
            )

        self.assertFalse(out_video.exists())

    def test_12_render_project_timeline_integration(self):
        """Scenario 12: Seamlessly compiles timeline directly from project and scene entities."""
        mock_scene_svc = MagicMock()
        mock_asset_svc = MagicMock()

        # Create 2 synthetic scene images in channel workspace
        ws_dir = self.test_dir / "workspace"
        ws_dir.mkdir(parents=True, exist_ok=True)
        img1 = self.wrapper.create_synthetic_image(ws_dir / "img1.png", color="yellow")
        img2 = self.wrapper.create_synthetic_image(ws_dir / "img2.png", color="cyan")

        # Mock Scenes
        scene1 = MagicMock()
        scene1.id = "scn_001"
        scene1.sequence_number = 1
        scene1.duration_seconds = 2.0
        scene1.metadata_json = {"transition": "cut"}

        scene2 = MagicMock()
        scene2.id = "scn_002"
        scene2.sequence_number = 2
        scene2.duration_seconds = 2.0
        scene2.metadata_json = {"transition": "cut"}

        mock_scene_svc.list_scenes.return_value = [scene2, scene1]  # Out of order to test sorting

        # Mock Assets
        asset1 = MagicMock()
        asset1.id = "ast_001"
        asset1.scene_id = "scn_001"
        asset1.file_path = "img1.png"

        asset2 = MagicMock()
        asset2.id = "ast_002"
        asset2.scene_id = "scn_002"
        asset2.file_path = "img2.png"

        mock_asset_svc.list_assets.return_value = [asset1, asset2]

        out_video = self.test_dir / "project_timeline.mp4"
        res = self.renderer.render_project_timeline(
            channel_id="ch_dreammatter",
            project_id="prj_pilot_001",
            output_path=out_video,
            scene_service=mock_scene_svc,
            asset_service=mock_asset_svc,
            channel_workspace_dir=ws_dir,
            profile=VERTICAL_9_16,
        )

        self.assertTrue(res.exists())
        probe = self.probe_service.probe_file(res)
        self.assertEqual(probe.primary_video_stream.width, 1080)
        self.assertEqual(probe.primary_video_stream.height, 1920)
        self.assertAlmostEqual(probe.duration_sec, 4.0, delta=0.3)

    def test_13_dual_audio_mixing_voiceover_and_background_music(self):
        """Scenario 13: Mixes voiceover and background music with correct relative volume levels and True Peak limit."""
        img1 = self.wrapper.create_synthetic_image(self.test_dir / "m3_s1.png", color="teal")
        img2 = self.wrapper.create_synthetic_image(self.test_dir / "m3_s2.png", color="navy")

        scenes = [
            SceneRenderItem(asset_path=img1, duration_sec=2.0, transition="cut"),
            SceneRenderItem(asset_path=img2, duration_sec=2.0, transition="cut"),
        ]

        # Voiceover 3.0s
        vo_path = self.wrapper.create_synthetic_audio(self.test_dir / "vo_dual.aac", duration_sec=3.0, sample_rate=48000)
        # Background music 2.0s (will loop to match 4.0s)
        bg_path = self.wrapper.create_synthetic_audio(self.test_dir / "bg_dual.aac", duration_sec=2.0, sample_rate=48000)

        out_video = self.test_dir / "dual_audio_mix.mp4"
        res = self.renderer.render_timeline(
            scenes=scenes,
            output_path=out_video,
            voiceover_track_path=vo_path,
            background_music_path=bg_path,
            voiceover_volume=1.0,
            background_music_volume=0.2,
            profile=VERTICAL_9_16,
        )

        self.assertTrue(res.exists())
        probe = self.probe_service.probe_file(res)
        self.assertTrue(probe.has_video and probe.has_audio)
        self.assertAlmostEqual(probe.duration_sec, 4.0, delta=0.3)
        self.assertAlmostEqual(probe.primary_audio_stream.duration_sec, 4.0, delta=0.3)

        # True Peak compliance check (ADR-003)
        tp = self.probe_service.measure_true_peak(res)
        self.assertTrue(tp.is_compliant, f"True Peak {tp.true_peak_dbtp} exceeds -1.0 dBTP limit")
        self.assertLessEqual(tp.true_peak_dbtp, -1.0)

    def test_14_extreme_loudness_true_peak_limiting_enforcement(self):
        """Scenario 14: Overdriven voiceover (+10dB) is strictly limited to <= -1.0 dBTP True Peak."""
        img = self.wrapper.create_synthetic_image(self.test_dir / "overdriven_img.png")
        scenes = [SceneRenderItem(asset_path=img, duration_sec=2.0)]

        # Generate intentionally overdriven audio (+1.7 dBTP) violating ADR-003
        raw_loud = self.test_dir / "overdriven_raw.aac"
        self.wrapper.create_synthetic_audio(raw_loud, duration_sec=2.0, freq=1000, volume=10.0)

        # Verify raw audio violates ADR-003
        tp_raw = self.probe_service.measure_true_peak(raw_loud)
        self.assertFalse(tp_raw.is_compliant)
        self.assertGreater(tp_raw.true_peak_dbtp, -1.0)

        # Render timeline with limiter enabled
        out_video = self.test_dir / "clamped_audio.mp4"
        res = self.renderer.render_timeline(
            scenes=scenes,
            output_path=out_video,
            voiceover_track_path=raw_loud,
            enforce_true_peak=True,
            profile=VERTICAL_9_16,
        )

        self.assertTrue(res.exists())
        tp_clamped = self.probe_service.measure_true_peak(res)
        self.assertTrue(tp_clamped.is_compliant, f"Clamped True Peak {tp_clamped.true_peak_dbtp} violates -1.0 dBTP")
        self.assertLessEqual(tp_clamped.true_peak_dbtp, -1.0)

    def test_15_audio_video_synchronization_with_short_audio_and_loop(self):
        """Scenario 15: Audio-video sync is preserved: short voiceover pads to video duration, short music loops."""
        img1 = self.wrapper.create_synthetic_image(self.test_dir / "sync_1.png")
        img2 = self.wrapper.create_synthetic_image(self.test_dir / "sync_2.png")
        img3 = self.wrapper.create_synthetic_image(self.test_dir / "sync_3.png")

        # Total video duration: 6.0s
        scenes = [
            SceneRenderItem(asset_path=img1, duration_sec=2.0, transition="cut"),
            SceneRenderItem(asset_path=img2, duration_sec=2.0, transition="cut"),
            SceneRenderItem(asset_path=img3, duration_sec=2.0, transition="cut"),
        ]

        # Voiceover is only 2.5s (shorter than 6.0s)
        vo_short = self.wrapper.create_synthetic_audio(self.test_dir / "vo_short.aac", duration_sec=2.5)
        # Background music is only 1.5s (shorter than 6.0s)
        bg_short = self.wrapper.create_synthetic_audio(self.test_dir / "bg_short.aac", duration_sec=1.5)

        out_video = self.test_dir / "perfect_sync.mp4"
        res = self.renderer.render_timeline(
            scenes=scenes,
            output_path=out_video,
            voiceover_track_path=vo_short,
            background_music_path=bg_short,
            profile=VERTICAL_9_16,
        )

        probe = self.probe_service.probe_file(res)
        v_dur = probe.primary_video_stream.duration_sec
        a_dur = probe.primary_audio_stream.duration_sec

        # Video and audio durations match to prevent desync
        self.assertAlmostEqual(probe.duration_sec, 6.0, delta=0.2)
        self.assertAlmostEqual(v_dur, 6.0, delta=0.2)
        self.assertAlmostEqual(a_dur, 6.0, delta=0.2)
        self.assertAlmostEqual(v_dur, a_dur, delta=0.1)

    def test_16_project_timeline_with_voiceover_and_bg_music(self):
        """Scenario 16: Compiles project timeline with dual audio mixing from project entities."""
        mock_scene_svc = MagicMock()
        mock_asset_svc = MagicMock()

        ws_dir = self.test_dir / "ws_audio"
        ws_dir.mkdir(parents=True, exist_ok=True)
        img1 = self.wrapper.create_synthetic_image(ws_dir / "p_img1.png")
        img2 = self.wrapper.create_synthetic_image(ws_dir / "p_img2.png")

        scene1 = MagicMock(id="s1", sequence_number=1, duration_seconds=2.0, metadata_json={"transition": "cut"})
        scene2 = MagicMock(id="s2", sequence_number=2, duration_seconds=2.0, metadata_json={"transition": "cut"})
        mock_scene_svc.list_scenes.return_value = [scene1, scene2]

        asset1 = MagicMock(id="a1", scene_id="s1", file_path="p_img1.png")
        asset2 = MagicMock(id="a2", scene_id="s2", file_path="p_img2.png")
        mock_asset_svc.list_assets.return_value = [asset1, asset2]

        vo = self.wrapper.create_synthetic_audio(self.test_dir / "p_vo.aac", duration_sec=3.0)
        bg = self.wrapper.create_synthetic_audio(self.test_dir / "p_bg.aac", duration_sec=2.0)

        out_video = self.test_dir / "project_dual_audio.mp4"
        res = self.renderer.render_project_timeline(
            channel_id="ch_pilot",
            project_id="prj_001",
            output_path=out_video,
            scene_service=mock_scene_svc,
            asset_service=mock_asset_svc,
            channel_workspace_dir=ws_dir,
            voiceover_track_path=vo,
            background_music_path=bg,
            voiceover_volume=1.0,
            background_music_volume=0.25,
            profile=VERTICAL_9_16,
        )

        self.assertTrue(res.exists())
        probe = self.probe_service.probe_file(res)
        self.assertAlmostEqual(probe.duration_sec, 4.0, delta=0.2)
        tp = self.probe_service.measure_true_peak(res)
        self.assertTrue(tp.is_compliant)


if __name__ == "__main__":
    unittest.main()

