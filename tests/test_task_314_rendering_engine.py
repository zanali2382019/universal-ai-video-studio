"""
Automated Integration and Unit Tests for Task 3.14: Local Rendering, Audio & Subtitle Engine.
Tests visual transitions, aspect ratio fitting modes, Arabic subtitles (SRT/ASS),
audio multitrack mixing, EBU R128 loudness normalization, True Peak clamp,
incremental rendering cache, license verification gate (ADR-008), and Flow repository integration.
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import List

from src.core.config import Settings
from src.core.exceptions import RenderingError
from src.rendering.ffmpeg_wrapper import FFmpegWrapper
from src.database.connection import DatabaseManager
from src.database.migrations import MigrationManager
from src.database.models import ChannelCreate, ProjectCreate, SceneCreate
from src.database.repositories.channel_repository import ChannelRepository
from src.database.repositories.project_repository import ProjectRepository
from src.database.repositories.scene_repository import SceneRepository
from src.database.repositories.flow_inbound_repository import FlowInboundRepository
from src.rendering.render_cache import RenderCacheManager
from src.rendering.subtitles import SubtitleEngine, SubtitleItem, SubtitleStyle
from src.rendering.timeline_renderer import (
    SceneRenderItem,
    TimelineRenderer,
    TimelineRenderProfile,
)
from src.rendering.transitions import (
    SUPPORTED_ASPECT_MODES,
    SUPPORTED_TRANSITIONS,
    build_aspect_fit_filter,
    build_xfade_filter_complex,
)
from src.storage.service import StorageService


class TestTask314RenderingEngine(unittest.TestCase):
    """Test suite covering all Task 3.14 capabilities."""

    @classmethod
    def setUpClass(cls):
        cls.ffmpeg = FFmpegWrapper()

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.work_dir = Path(self.temp_dir.name)
        self.cache_dir = self.work_dir / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_mgr = RenderCacheManager(cache_dir=self.cache_dir)
        self.renderer = TimelineRenderer(
            ffmpeg_wrapper=self.ffmpeg,
            cache_manager=self.cache_mgr,
        )

    def tearDown(self):
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def _generate_synthetic_video(
        self,
        output_path: Path,
        duration: float = 1.0,
        width: int = 640,
        height: int = 360,
        color: str = "blue",
    ) -> Path:
        """Create a synthetic solid color / test pattern video using FFmpeg directly."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", f"color=c={color}:s={width}x{height}:d={duration}:r=25",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-an",
            str(output_path),
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if res.returncode != 0:
            raise RuntimeError(f"FFmpeg synthetic generation failed: {res.stderr.decode()}")
        return output_path

    def _generate_synthetic_audio(
        self,
        output_path: Path,
        duration: float = 1.0,
        freq: int = 440,
        volume: float = 0.5,
    ) -> Path:
        """Create a synthetic tone audio file using FFmpeg."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", f"sine=frequency={freq}:duration={duration}",
            "-filter:a", f"volume={volume}",
            "-ar", "44100",
            str(output_path),
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if res.returncode != 0:
            raise RuntimeError(f"FFmpeg synthetic audio generation failed: {res.stderr.decode()}")
        return output_path

    def _probe_video_dimensions(self, video_path: Path) -> tuple[int, int]:
        """Probe width and height via ffprobe."""
        cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=s=x:p=0",
            str(video_path),
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        dims = res.stdout.strip().split("x")
        return int(dims[0]), int(dims[1])

    def _probe_audio_true_peak(self, audio_or_video_path: Path) -> float:
        """Probe True Peak using FFmpeg ebur128 filter."""
        cmd = [
            "ffmpeg", "-i", str(audio_or_video_path),
            "-filter_complex", "ebur128=peak=true",
            "-f", "null", "-",
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        output = res.stderr
        # Parse True peak line: e.g. "Peak:           -1.2 dBFS" or "True peak:      -1.2 dBFS"
        tp = 0.0
        for line in output.splitlines():
            if "True peak:" in line or "Peak:" in line:
                parts = line.split(":")
                if len(parts) >= 2:
                    val_str = parts[1].replace("dBFS", "").replace("dBTP", "").strip()
                    try:
                        tp = float(val_str)
                        break
                    except ValueError:
                        pass
        return tp

    # =========================================================================
    # Test 1: Aspect Ratio Fitting Modes (cover, contain, blur_pad, fit)
    # =========================================================================
    def test_01_aspect_ratio_modes(self):
        """Verify cover, contain, blur_pad, and fit modes across 9:16 target."""
        raw_vid = self.work_dir / "raw_16_9.mp4"
        self._generate_synthetic_video(raw_vid, duration=1.0, width=640, height=360, color="red")

        target_width = 360
        target_height = 640  # 9:16 vertical
        profile = TimelineRenderProfile(width=target_width, height=target_height, fps=25)

        for mode in ["cover", "contain", "blur_pad", "fit"]:
            with self.subTest(mode=mode):
                item = SceneRenderItem(
                    scene_id=f"scene_{mode}",
                    video_path=raw_vid,
                    duration=1.0,
                    order=1,
                    aspect_mode=mode,
                )
                out_path = self.work_dir / f"norm_{mode}.mp4"
                res_path = self.renderer.normalize_scene(item, profile, out_path, use_cache=False)
                self.assertTrue(res_path.exists())
                w, h = self._probe_video_dimensions(res_path)
                self.assertEqual(w, target_width, f"Mode {mode} width mismatch")
                self.assertEqual(h, target_height, f"Mode {mode} height mismatch")

    # =========================================================================
    # Test 2: FFmpeg xfade Visual Transitions
    # =========================================================================
    def test_02_xfade_transitions(self):
        """Test supported xfade transitions and cut between two clips."""
        vid1 = self.work_dir / "clip1.mp4"
        vid2 = self.work_dir / "clip2.mp4"
        self._generate_synthetic_video(vid1, duration=1.2, width=320, height=320, color="green")
        self._generate_synthetic_video(vid2, duration=1.2, width=320, height=320, color="yellow")

        profile = TimelineRenderProfile(width=320, height=320, fps=25)
        transitions_to_test = ["fade", "wipeleft", "wiperight", "slideup", "slidedown", "circleopen", "smoothleft", "cut"]

        for trans in transitions_to_test:
            with self.subTest(transition=trans):
                out_video = self.work_dir / f"trans_{trans}.mp4"
                res = self.renderer.concatenate_scenes(
                    normalized_videos=[vid1, vid2],
                    durations=[1.2, 1.2],
                    transitions=[(trans, 0.4)],
                    output_path=out_video,
                    profile=profile,
                )
                self.assertTrue(res.exists())
                self.assertGreater(res.stat().st_size, 1000)
                w, h = self._probe_video_dimensions(res)
                self.assertEqual((w, h), (320, 320))

    # =========================================================================
    # Test 3: Arabic Subtitle Engine (SRT & ASS formatting without double-shaping)
    # =========================================================================
    def test_03_arabic_subtitles_formatting(self):
        """Verify SRT and ASS generation with RTL Arabic text and styling."""
        sub_engine = SubtitleEngine()
        arabic_text = "مرحباً بكم في استوديو الإنتاج الذكي"
        items = [
            SubtitleItem(
                index=1,
                start_seconds=0.0,
                end_seconds=2.5,
                text=arabic_text,
            )
        ]

        # SRT output
        srt_file = self.work_dir / "test.srt"
        sub_engine.generate_srt(items, srt_file)
        self.assertTrue(srt_file.exists())
        srt_content = srt_file.read_text(encoding="utf-8")
        self.assertIn("00:00:00,000 --> 00:00:02,500", srt_content)
        self.assertIn(arabic_text, srt_content)

        # ASS output
        ass_file = self.work_dir / "test.ass"
        style = SubtitleStyle(
            font_size=32,
            outline_width=2.5,
            back_color="&H80000000",
        )
        sub_engine.generate_ass(items, ass_file, style=style, play_res_x=1080, play_res_y=1920)
        self.assertTrue(ass_file.exists())
        ass_content = ass_file.read_text(encoding="utf-8")
        self.assertIn("[Script Info]", ass_content)
        self.assertIn("[V4+ Styles]", ass_content)
        self.assertIn("[Events]", ass_content)
        # Ensure raw Arabic Unicode is preserved in ASS (libass does the shaping)
        self.assertIn(arabic_text, ass_content)

    # =========================================================================
    # Test 4: Audio Multitrack Mixing, Ducking & EBU R128 Loudness Clamping
    # =========================================================================
    def test_04_audio_mixing_and_ebur128(self):
        """Verify voiceover + background music ducking, and True Peak clamping <= -1.0 dBTP."""
        vo_path = self.work_dir / "voiceover.wav"
        bgm_path = self.work_dir / "bgm.wav"
        vid_path = self.work_dir / "scene_vid.mp4"

        self._generate_synthetic_video(vid_path, duration=2.0, width=320, height=320, color="black")
        self._generate_synthetic_audio(vo_path, duration=1.5, freq=440, volume=0.8)
        self._generate_synthetic_audio(bgm_path, duration=3.0, freq=220, volume=0.7)

        scene = SceneRenderItem(
            scene_id="sc_audio",
            video_path=vid_path,
            duration=2.0,
            order=1,
            voiceover_path=vo_path,
            subtitle_text="اختبار هندسة الصوت والتطبيع",
        )

        out_render = self.work_dir / "rendered_audio_test.mp4"
        profile = TimelineRenderProfile(width=320, height=320, fps=25, target_lufs=-14.0, true_peak_db=-1.0)

        res = self.renderer.render_timeline(
            scenes=[scene],
            output_path=out_render,
            profile=profile,
            background_music_path=bgm_path,
            music_volume=0.2,
            ducking=True,
            burn_subtitles=True,
            mode="preview",
            use_cache=False,
        )

        self.assertTrue(res.exists())
        self.assertGreater(res.stat().st_size, 5000)

        # Check True Peak <= -1.0 dBTP (allowing +/- 0.5dB margin for lossy encoding / limiter response)
        true_peak = self._probe_audio_true_peak(res)
        self.assertLessEqual(true_peak, -0.8, f"True peak {true_peak} exceeded allowed limit of -1.0 dBTP")

    # =========================================================================
    # Test 5: Incremental Rendering & Fingerprint Cache
    # =========================================================================
    def test_05_incremental_cache(self):
        """Test cache hit on identical fingerprint and invalidation on change."""
        vid = self.work_dir / "cache_vid.mp4"
        self._generate_synthetic_video(vid, duration=1.0, width=320, height=240, color="purple")

        profile = TimelineRenderProfile(width=320, height=320, fps=25)
        item = SceneRenderItem(
            scene_id="cache_scene",
            video_path=vid,
            duration=1.0,
            order=1,
            aspect_mode="cover",
        )

        # First run: Cache Miss & Store
        out1 = self.work_dir / "cache_out1.mp4"
        self.renderer.normalize_scene(item, profile, out1, use_cache=True)
        self.assertTrue(out1.exists())
        mtime1 = out1.stat().st_mtime

        # Check cache directory contains the segment
        cached_files = list(self.cache_dir.glob("*.mp4"))
        self.assertEqual(len(cached_files), 1)

        # Second run: Cache Hit
        out2 = self.work_dir / "cache_out2.mp4"
        self.renderer.normalize_scene(item, profile, out2, use_cache=True)
        self.assertTrue(out2.exists())
        self.assertEqual(out1.stat().st_size, out2.stat().st_size)

        # Change aspect mode -> Invalidation / Cache Miss
        item.aspect_mode = "blur_pad"
        out3 = self.work_dir / "cache_out3.mp4"
        self.renderer.normalize_scene(item, profile, out3, use_cache=True)
        self.assertTrue(out3.exists())
        cached_files_after = list(self.cache_dir.glob("*.mp4"))
        self.assertEqual(len(cached_files_after), 2)

    # =========================================================================
    # Test 6: Atomic Publication & Error Recovery
    # =========================================================================
    def test_06_atomic_publication_and_recovery(self):
        """Verify atomic publication leaves no partial files on error."""
        vid = self.work_dir / "bad_input.mp4"
        vid.write_text("corrupted content", encoding="utf-8")

        item = SceneRenderItem(
            scene_id="sc_corrupted",
            video_path=vid,
            duration=1.0,
            order=1,
        )

        target_out = self.work_dir / "final_corrupt_test.mp4"
        with self.assertRaises(RenderingError):
            self.renderer.render_timeline(
                scenes=[item],
                output_path=target_out,
                mode="preview",
                use_cache=False,
            )

        # Confirm target output was NOT created
        self.assertFalse(target_out.exists())
        # Confirm no leftover .tmp files
        tmp_files = list(self.work_dir.glob("*.tmp_*"))
        self.assertEqual(len(tmp_files), 0)

    # =========================================================================
    # Test 7: License Verification Gate (ADR-008)
    # =========================================================================
    def test_07_license_verification_gate(self):
        """Ensure preview allows pending_review while export strictly blocks it."""
        vid = self.work_dir / "lic_vid.mp4"
        self._generate_synthetic_video(vid, duration=1.0, width=320, height=320, color="cyan")

        item = SceneRenderItem(
            scene_id="sc_lic",
            video_path=vid,
            duration=1.0,
            order=1,
            metadata={"license_status": "pending_review"},
        )

        out_preview = self.work_dir / "preview.mp4"
        out_export = self.work_dir / "export.mp4"

        # 1. Preview mode MUST succeed with pending_review
        res_preview = self.renderer.render_timeline(
            scenes=[item],
            output_path=out_preview,
            mode="preview",
            use_cache=False,
        )
        self.assertTrue(res_preview.exists())

        # 2. Export mode MUST raise RenderingError under ADR-008
        with self.assertRaises(RenderingError) as ctx:
            self.renderer.render_timeline(
                scenes=[item],
                output_path=out_export,
                mode="export",
                use_cache=False,
            )
        self.assertIn("ADR-008", str(ctx.exception))
        self.assertFalse(out_export.exists())

        # 3. Export mode with commercial_allowed MUST succeed
        item.metadata["license_status"] = "commercial_allowed"
        res_export = self.renderer.render_timeline(
            scenes=[item],
            output_path=out_export,
            mode="export",
            use_cache=False,
        )
        self.assertTrue(res_export.exists())

    # =========================================================================
    # Test 8: Flow Project Timeline Integration
    # =========================================================================
    def test_08_render_flow_project_timeline_integration(self):
        """Test rendering full timeline directly from FlowInboundRepository active takes."""
        db_path = self.work_dir / "flow_render.db"
        db_mgr = DatabaseManager(db_path)
        MigrationManager(db_mgr).apply_all_migrations()
        repo = FlowInboundRepository(db_mgr)

        channel_repo = ChannelRepository(db_mgr)
        project_repo = ProjectRepository(db_mgr)
        scene_repo = SceneRepository(db_mgr)

        ch_id = "dreammatter"
        channel_repo.create(ChannelCreate(id=ch_id, name="DreamMatter", handle="@DreamMatter", niche="Animation"))
        flow_proj_id = "flow_proj_100"
        project_repo.create(ProjectCreate(id=flow_proj_id, channel_id=ch_id, title="Flow Test Project"))

        scene_repo.create(SceneCreate(id="flow_sc_01", project_id=flow_proj_id, channel_id=ch_id, sequence_number=1, title="Scene 1"))
        scene_repo.create(SceneCreate(id="flow_sc_02", project_id=flow_proj_id, channel_id=ch_id, sequence_number=2, title="Scene 2"))

        vid_take1 = self.work_dir / "take1.mp4"
        vid_take2 = self.work_dir / "take2.mp4"
        self._generate_synthetic_video(vid_take1, duration=1.0, width=360, height=640, color="magenta")
        self._generate_synthetic_video(vid_take2, duration=1.0, width=360, height=640, color="orange")

        with db_mgr.transaction() as conn:
            conn.execute(
                """
                INSERT INTO media_assets (
                    id, channel_id, project_id, scene_id, file_name, file_path,
                    file_sha256, file_size_bytes, mime_type, asset_type, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'));
                """,
                ("asset_01", ch_id, flow_proj_id, "flow_sc_01", "take1.mp4", str(vid_take1), "dummy_sha1", 1000, "video/mp4", "video")
            )
            conn.execute(
                """
                INSERT INTO media_assets (
                    id, channel_id, project_id, scene_id, file_name, file_path,
                    file_sha256, file_size_bytes, mime_type, asset_type, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'));
                """,
                ("asset_02", ch_id, flow_proj_id, "flow_sc_02", "take2.mp4", str(vid_take2), "dummy_sha2", 1000, "video/mp4", "video")
            )

        # Register takes and activate them
        repo.create_take(
            take_id="take_01",
            channel_id="dreammatter",
            project_id=flow_proj_id,
            scene_id="flow_sc_01",
            prompt_version=1,
            take_number=1,
            asset_id="asset_01",
            asset_role="video",
            file_sha256="dummy_sha1",
            source_file_name="take1.mp4",
            storage_key=str(vid_take1),
            duration_sec=1.0,
            is_active=True,
            metadata={"license_status": "commercial_allowed", "prompt_text": "Tommy arrives in Dreamland"},
        )

        repo.create_take(
            take_id="take_02",
            channel_id="dreammatter",
            project_id=flow_proj_id,
            scene_id="flow_sc_02",
            prompt_version=1,
            take_number=1,
            asset_id="asset_02",
            asset_role="video",
            file_sha256="dummy_sha2",
            source_file_name="take2.mp4",
            storage_key=str(vid_take2),
            duration_sec=1.0,
            is_active=True,
            metadata={"license_status": "commercial_allowed", "prompt_text": "Zara discovers the star"},
        )

        out_flow_video = self.work_dir / "final_flow_render.mp4"
        profile = TimelineRenderProfile(width=360, height=640, fps=25)

        res = self.renderer.render_flow_project_timeline(
            flow_repo=repo,
            flow_project_id=flow_proj_id,
            output_path=out_flow_video,
            profile=profile,
            mode="export",
            burn_subtitles=True,
        )

        self.assertTrue(res.exists())
        self.assertGreater(res.stat().st_size, 5000)
        w, h = self._probe_video_dimensions(res)
        self.assertEqual((w, h), (360, 640))


if __name__ == "__main__":
    unittest.main()
