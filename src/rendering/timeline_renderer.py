"""
Timeline rendering engine for normalizing scene media, applying transitions, and compiling videos.
Fully supports Task 3.6 / Milestone 2 and Task 3.14 (Visual Transitions, Aspect Ratios, Arabic Subtitles,
Multitrack Audio Mixing with Ducking & EBU R128 Loudness, Incremental Caching, and Google Flow Integration).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from src.core.config import get_settings
from src.core.constants import LegalBasis, VerificationStatus
from src.core.exceptions import (
    FFmpegExecutionException,
    LicenseVerificationException,
    MediaValidationException,
    RenderCancelledException,
    RenderingError,
    RenderingException,
    StaleExecutionTokenException,
    TaskNotFoundException,
    TimelineRenderException,
)
from src.core.logger import get_logger
from src.database.repositories.flow_inbound_repository import FlowInboundRepository
from src.rendering.ffmpeg_wrapper import FFmpegWrapper
from src.rendering.probe_service import MediaProbeService
from src.rendering.profiles import HORIZONTAL_16_9, SQUARE_1_1, VERTICAL_9_16, RenderProfile
from src.rendering.render_cache import RenderCacheManager
from src.rendering.subtitles import SubtitleEngine, SubtitleItem, SubtitleStyle
from src.rendering.transitions import (
    SUPPORTED_ASPECT_MODES,
    SUPPORTED_TRANSITIONS,
    build_aspect_fit_filter,
    build_xfade_filter_complex,
)
from src.storage.service import StorageService

logger = get_logger("studio.rendering.timeline")


@dataclass
class SceneRenderItem:
    """Represents a scene segment in the rendering timeline.
    Compatible with both Milestone 2 (Task 3.6) and Task 3.14.
    """
    asset_path: Optional[Union[str, Path]] = None
    video_path: Optional[Union[str, Path]] = None
    duration_sec: float = 5.0
    duration: Optional[float] = None
    transition: str = "cut"
    transition_in: str = "cut"
    transition_out: str = "cut"
    transition_duration_sec: float = 0.5
    transition_duration: Optional[float] = None
    scene_id: Optional[str] = None
    asset_id: Optional[str] = None
    order: int = 1
    aspect_mode: str = "cover"  # cover, contain, blur_pad, fit/stretch
    subtitle_text: Optional[str] = None
    subtitle_words: Optional[List[Dict[str, Any]]] = None
    voiceover_path: Optional[Path] = None
    sfx_path: Optional[Path] = None
    asset_sha256: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        # Synchronize asset_path and video_path
        if self.video_path is not None and self.asset_path is None:
            self.asset_path = Path(self.video_path)
        elif self.asset_path is not None and self.video_path is None:
            self.video_path = Path(self.asset_path)
        elif self.asset_path is not None:
            self.asset_path = Path(self.asset_path)
            self.video_path = Path(self.video_path or self.asset_path)

        # Synchronize duration and duration_sec
        if self.duration is not None and self.duration > 0:
            self.duration_sec = float(self.duration)
        else:
            self.duration = float(self.duration_sec)

        if self.duration_sec <= 0:
            raise ValueError(f"Scene duration must be positive, got {self.duration_sec}")

        # Synchronize transitions
        if self.transition_out != "cut" and self.transition == "cut":
            self.transition = self.transition_out
        elif self.transition != "cut" and self.transition_out == "cut":
            self.transition_out = self.transition

        self.transition = self.transition.lower()
        self.transition_out = self.transition_out.lower()

        # Synchronize transition duration
        if self.transition_duration is not None:
            self.transition_duration_sec = float(self.transition_duration)
        else:
            self.transition_duration = float(self.transition_duration_sec)


@dataclass
class TimelineRenderProfile:
    """Target output profile specifications."""
    width: int = 1080
    height: int = 1920
    fps: int = 30
    video_codec: str = "libx264"
    audio_codec: str = "aac"
    pix_fmt: str = "yuv420p"
    video_bitrate: str = "8M"
    audio_bitrate: str = "192k"
    audio_sample_rate: int = 44100
    audio_channels: int = 2
    target_lufs: float = -14.0
    true_peak_db: float = -1.0
    preset: str = "medium"
    crf: int = 20

    def get_scaling_filter(self) -> str:
        return (
            f"scale={self.width}:{self.height}:force_original_aspect_ratio=decrease,"
            f"pad={self.width}:{self.height}:(ow-iw)/2:(oh-ih)/2,"
            f"setsar=1,fps={self.fps},format={self.pix_fmt}"
        )


class TimelineRenderer:
    """Production rendering engine for multi-scene timelines."""

    def __init__(
        self,
        ffmpeg_wrapper: Optional[FFmpegWrapper] = None,
        probe_service: Optional[MediaProbeService] = None,
        license_service: Optional[Any] = None,
        task_repository: Optional[Any] = None,
        cache_manager: Optional[RenderCacheManager] = None,
        storage_service: Optional[StorageService] = None,
        **kwargs,
    ) -> None:
        self.wrapper = ffmpeg_wrapper or FFmpegWrapper()
        self.ffmpeg = self.wrapper
        self.probe_service = probe_service or MediaProbeService(ffmpeg_wrapper=self.wrapper)
        self.license_service = license_service
        self.task_repo = task_repository
        self.cache = cache_manager or RenderCacheManager()
        self.storage = storage_service
        self.subtitles = SubtitleEngine()

    def normalize_scene(
        self,
        input_path: Optional[Union[SceneRenderItem, str, Path]] = None,
        duration_sec: Optional[Union[float, RenderProfile, TimelineRenderProfile]] = None,
        output_path: Optional[Union[str, Path]] = None,
        profile: Optional[Union[RenderProfile, TimelineRenderProfile]] = None,
        cancellation_token: Optional[threading.Event] = None,
        timeout_sec: float = 120.0,
        use_cache: bool = True,
        aspect_mode: Optional[str] = None,
        item: Optional[SceneRenderItem] = None,
        **kwargs,
    ) -> Path:
        """Normalize an image or video asset into a conforming video segment."""
        if cancellation_token and cancellation_token.is_set():
            raise RenderCancelledException("Normalization cancelled prior to start.")

        target_item = item or kwargs.get("input_path_or_item") or (input_path if isinstance(input_path, SceneRenderItem) else None)

        if target_item is not None:
            prof = duration_sec if isinstance(duration_sec, (RenderProfile, TimelineRenderProfile)) else (profile or VERTICAL_9_16)
            out_p = Path(output_path or kwargs.get("output_path"))
            in_p = Path(target_item.video_path or target_item.asset_path)
            dur = float(target_item.duration_sec)
            mode = aspect_mode or target_item.aspect_mode or "cover"
            item_obj = target_item
        else:
            in_p = Path(input_path or kwargs.get("input_path"))
            dur = float(duration_sec) if isinstance(duration_sec, (int, float)) else 5.0
            out_p = Path(output_path or kwargs.get("output_path"))
            prof = profile or VERTICAL_9_16
            mode = aspect_mode or "contain"
            item_obj = SceneRenderItem(
                asset_path=in_p,
                video_path=in_p,
                duration_sec=dur,
                aspect_mode=mode,
            )

        if not in_p.exists():
            raise TimelineRenderException(f"Input asset does not exist: '{in_p}'")

        out_p.parent.mkdir(parents=True, exist_ok=True)

        # Check Cache
        if use_cache:
            profile_dict = {
                "width": prof.width,
                "height": prof.height,
                "fps": prof.fps,
                "video_codec": getattr(prof, "video_codec", "libx264"),
                "pix_fmt": getattr(prof, "pix_fmt", "yuv420p"),
            }
            cached = self.cache.get_cached_scene(item_obj, profile_dict, mode)
            if cached and cached.exists() and cached.stat().st_size > 0:
                logger.debug(f"Reusing cached normalized scene: {cached}")
                shutil.copy2(cached, out_p)
                return out_p

        temp_output = out_p.parent / f"{out_p.stem}.tmp.{uuid.uuid4().hex[:8]}.mp4"
        is_image = in_p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}

        # Build aspect fit filter
        vf_filter = build_aspect_fit_filter(
            profile=prof,
            mode=mode,
            target_width=prof.width,
            target_height=prof.height,
            fps=int(prof.fps),
            pix_fmt=getattr(prof, "pix_fmt", "yuv420p"),
        )

        preset = getattr(prof, "preset", "medium")
        crf = getattr(prof, "crf", 20)
        vcodec = getattr(prof, "video_codec", "libx264")

        try:
            if is_image:
                cmd = [
                    "-y",
                    "-loop", "1",
                    "-t", str(dur),
                    "-i", str(in_p),
                    "-vf", vf_filter,
                    "-c:v", vcodec,
                    "-preset", preset,
                    "-crf", str(crf),
                    "-an",
                    str(temp_output),
                ]
            else:
                cmd = [
                    "-y",
                    "-t", str(dur),
                    "-i", str(in_p),
                    "-vf", vf_filter,
                    "-c:v", vcodec,
                    "-preset", preset,
                    "-crf", str(crf),
                    "-an",
                    str(temp_output),
                ]

            self.wrapper.run_ffmpeg(cmd, timeout_sec=timeout_sec, check=True, cancellation_token=cancellation_token)

            # Atomic publication
            os.replace(temp_output, out_p)

            if use_cache:
                self.cache.store_cached_scene(item_obj, profile_dict, out_p, mode)

            logger.debug(f"Normalized scene segment '{in_p.name}' -> '{out_p.name}' ({dur}s)")
            return out_p

        except RenderCancelledException:
            if temp_output.exists():
                try:
                    temp_output.unlink(missing_ok=True)
                except Exception:
                    pass
            raise
        except Exception as e:
            if temp_output.exists():
                try:
                    temp_output.unlink(missing_ok=True)
                except Exception:
                    pass
            raise TimelineRenderException(f"Failed to normalize scene '{in_p}': {e}") from e

    def concatenate_scenes(
        self,
        normalized_videos: Optional[List[Path]] = None,
        durations: Optional[List[float]] = None,
        transitions: Optional[Any] = None,
        output_path: Optional[Union[str, Path]] = None,
        profile: Optional[Union[RenderProfile, TimelineRenderProfile]] = None,
        segment_paths: Optional[List[Path]] = None,
        transition_durations: Optional[List[float]] = None,
        cancellation_token: Optional[threading.Event] = None,
        timeout_sec: float = 180.0,
    ) -> Path:
        """Concatenate normalized video segments into a single visual timeline."""
        if cancellation_token and cancellation_token.is_set():
            raise RenderCancelledException("Concatenation cancelled prior to start.")

        videos = normalized_videos or segment_paths
        if not videos:
            raise TimelineRenderException("Cannot concatenate scenes: video list is empty.")

        out_p = Path(output_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        prof = profile or VERTICAL_9_16

        temp_output = out_p.parent / f"{out_p.stem}.tmp.{uuid.uuid4().hex[:8]}.mp4"
        n = len(videos)

        if n == 1:
            shutil.copy2(videos[0], temp_output)
            os.replace(temp_output, out_p)
            return out_p

        # Normalize durations
        if durations is None or len(durations) != n:
            durations = []
            for p in videos:
                pr = self.probe_service.probe_file(p)
                durations.append(pr.duration_sec or 5.0)

        # Normalize transitions
        t_pairs: List[Tuple[str, float]] = []
        if transitions and isinstance(transitions[0], (tuple, list)):
            t_pairs = list(transitions)
        elif transitions:
            for i, t in enumerate(transitions):
                dur_val = transition_durations[i] if transition_durations and i < len(transition_durations) else 0.5
                t_pairs.append((str(t), float(dur_val)))
        else:
            t_pairs = [("cut", 0.5)] * (n - 1)

        has_xfade = any(name.lower() != "cut" and name.lower() in SUPPORTED_TRANSITIONS for name, _ in t_pairs)

        preset = getattr(prof, "preset", "medium")
        crf = getattr(prof, "crf", 20)
        vcodec = getattr(prof, "video_codec", "libx264")

        try:
            if not has_xfade:
                inputs: List[str] = []
                for p in videos:
                    inputs.extend(["-i", str(p)])

                filter_concat = "".join(f"[{i}:v]" for i in range(n)) + f"concat=n={n}:v=1:a=0[outv]"
                cmd = (
                    ["-y"]
                    + inputs
                    + [
                        "-filter_complex", filter_concat,
                        "-map", "[outv]",
                        "-c:v", vcodec,
                        "-preset", preset,
                        "-crf", str(crf),
                        "-an",
                        str(temp_output),
                    ]
                )
            else:
                filter_res = build_xfade_filter_complex(
                    num_inputs=n,
                    durations=durations,
                    transitions=[t[0] for t in t_pairs],
                    transition_durations=[t[1] for t in t_pairs],
                )
                inputs = []
                for p in videos:
                    inputs.extend(["-i", str(p)])

                cmd = (
                    ["-y"]
                    + inputs
                    + [
                        "-filter_complex", filter_res.filter_complex,
                        "-map", filter_res.final_map,
                        "-c:v", vcodec,
                        "-preset", preset,
                        "-crf", str(crf),
                        "-an",
                        str(temp_output),
                    ]
                )

            self.wrapper.run_ffmpeg(cmd, timeout_sec=timeout_sec, check=True, cancellation_token=cancellation_token)
            os.replace(temp_output, out_p)
            logger.info(f"Successfully concatenated {n} scenes into '{out_p.name}'.")
            return out_p
        except RenderCancelledException:
            if temp_output.exists():
                try:
                    temp_output.unlink(missing_ok=True)
                except Exception:
                    pass
            raise
        finally:
            if temp_output.exists():
                try:
                    temp_output.unlink(missing_ok=True)
                except Exception:
                    pass

    def render_timeline(
        self,
        scenes: List[SceneRenderItem],
        output_path: Union[str, Path],
        profile: Optional[Union[RenderProfile, TimelineRenderProfile]] = None,
        audio_track_path: Optional[Union[str, Path]] = None,
        voiceover_track_path: Optional[Union[str, Path]] = None,
        background_music_path: Optional[Union[str, Path]] = None,
        voiceover_volume: float = 1.0,
        background_music_volume: float = 0.2,
        music_volume: Optional[float] = None,
        ducking: bool = True,
        enforce_true_peak: bool = True,
        channel_id: Optional[str] = None,
        task_id: Optional[str] = None,
        execution_token: Optional[Union[int, str]] = None,
        cancellation_token: Optional[threading.Event] = None,
        timeout_sec: float = 300.0,
        audio_asset_ids: Optional[List[str]] = None,
        subtitle_style: Optional[SubtitleStyle] = None,
        burn_subtitles: bool = False,
        mode: str = "export",
        use_cache: bool = True,
    ) -> Path:
        """Full timeline rendering pipeline."""
        if not scenes:
            raise TimelineRenderException("Cannot render timeline: scenes list is empty.")

        if cancellation_token and cancellation_token.is_set():
            raise RenderCancelledException("Render cancelled prior to start.")

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        prof = profile or VERTICAL_9_16

        # Fencing check
        self._check_fencing(task_id, execution_token)

        # License Verification Gate (ADR-008)
        self._verify_licenses(scenes, channel_id, audio_asset_ids, mode)

        # Sort scenes by order
        sorted_scenes = sorted(scenes, key=lambda s: getattr(s, "order", 1))

        temp_dir = Path(tempfile.mkdtemp(prefix="timeline_render_"))
        temp_target = output_path.parent / f"{output_path.stem}.tmp_{uuid.uuid4().hex[:8]}.mp4"

        try:
            # 1. Normalize Scenes
            normalized_videos: List[Path] = []
            durations: List[float] = []
            transitions: List[Tuple[str, float]] = []

            for i, scene in enumerate(sorted_scenes):
                if cancellation_token and cancellation_token.is_set():
                    raise RenderCancelledException("Render cancelled during scene normalization.")
                self._check_fencing(task_id, execution_token)
                norm_out = temp_dir / f"norm_scene_{i:03d}_{scene.scene_id or 'sc'}.mp4"
                self.normalize_scene(
                    item=scene,
                    profile=prof,
                    output_path=norm_out,
                    cancellation_token=cancellation_token,
                    timeout_sec=timeout_sec,
                    use_cache=use_cache,
                )
                normalized_videos.append(norm_out)
                durations.append(scene.duration_sec)

                if i < len(sorted_scenes) - 1:
                    t_name = scene.transition_out or scene.transition or "cut"
                    t_dur = scene.transition_duration_sec or 0.5
                    transitions.append((t_name, t_dur))

            # 2. Concatenate Video Track
            concat_video = temp_dir / "concatenated_video.mp4"
            self.concatenate_scenes(
                normalized_videos=normalized_videos,
                durations=durations,
                transitions=transitions,
                output_path=concat_video,
                profile=prof,
                cancellation_token=cancellation_token,
                timeout_sec=timeout_sec,
            )

            # Probe effective visual duration
            vis_probe = self.probe_service.probe_file(concat_video)
            total_duration = vis_probe.duration_sec or self._calculate_effective_duration(durations, transitions)

            # 3. Subtitles Generation (ASS)
            ass_subtitle_file: Optional[Path] = None
            has_subtitles = burn_subtitles or any(s.subtitle_text for s in sorted_scenes)
            if has_subtitles and burn_subtitles:
                sub_items: List[SubtitleItem] = []
                current_time = 0.0
                for i, sc in enumerate(sorted_scenes):
                    sc_duration = sc.duration_sec
                    if sc.subtitle_text:
                        sub_items.append(
                            SubtitleItem(
                                index=i + 1,
                                start_sec=current_time,
                                end_sec=current_time + sc_duration,
                                text=sc.subtitle_text,
                                words=sc.subtitle_words,
                            )
                        )
                    if i < len(transitions):
                        t_name, t_dur = transitions[i]
                        overlap = t_dur if t_name.lower() != "cut" else 0.0
                        current_time += (sc_duration - overlap)
                    else:
                        current_time += sc_duration

                if sub_items:
                    ass_subtitle_file = temp_dir / "subtitles.ass"
                    self.subtitles.generate_ass(
                        items=sub_items,
                        output_path=ass_subtitle_file,
                        style=subtitle_style,
                        video_width=prof.width,
                        video_height=prof.height,
                    )

            # 4. Audio Multitrack Mixing
            bgm_p = Path(background_music_path) if background_music_path else None
            vo_p = Path(voiceover_track_path or audio_track_path) if (voiceover_track_path or audio_track_path) else None
            effective_music_volume = music_volume if music_volume is not None else background_music_volume

            final_audio = self._process_audio_mix(
                scenes=sorted_scenes,
                total_duration=total_duration,
                voiceover_track_path=vo_p,
                background_music_path=bgm_p,
                voiceover_volume=voiceover_volume,
                music_volume=effective_music_volume,
                ducking=ducking,
                enforce_true_peak=enforce_true_peak,
                profile=prof,
                temp_dir=temp_dir,
            )

            # Pre-publication fencing check
            self._check_fencing(task_id, execution_token)

            # 5. Final Mux and Subtitle Burning
            self._mux_and_finalize(
                video_path=concat_video,
                audio_path=final_audio,
                ass_subtitles=ass_subtitle_file,
                output_path=temp_target,
                profile=prof,
                total_duration=total_duration,
                cancellation_token=cancellation_token,
            )

            # 6. Atomic Publication
            if temp_target.exists():
                shutil.move(str(temp_target), str(output_path))
                logger.info(f"Rendered timeline successfully published to: {output_path}")

            return output_path

        except (RenderCancelledException, LicenseVerificationException, StaleExecutionTokenException, TaskNotFoundException):
            if temp_target.exists():
                try:
                    temp_target.unlink()
                except Exception:
                    pass
            raise
        except Exception as e:
            if temp_target.exists():
                try:
                    temp_target.unlink()
                except Exception:
                    pass
            raise TimelineRenderException(f"Timeline rendering failed: {e}") from e
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def _check_fencing(self, task_id: Optional[str], execution_token: Optional[Union[int, str]]) -> None:
        """Validate execution token against TaskRepository if configured."""
        if not task_id or execution_token is None or not self.task_repo:
            return

        task = getattr(self.task_repo, "get_by_id", None) or getattr(self.task_repo, "get_task", None)
        if task:
            t = task(task_id)
            if not t:
                raise TaskNotFoundException(task_id)
            if str(t.execution_token) != str(execution_token):
                logger.error(f"Fencing check failed: worker token {execution_token} != DB token {t.execution_token}")
                raise StaleExecutionTokenException(
                    task_id=task_id,
                    expected_token=execution_token,
                    actual_token=t.execution_token,
                    message=f"Fencing check failed for task '{task_id}': worker token {execution_token} is stale (DB token={t.execution_token}).",
                )

    def _verify_licenses(
        self,
        scenes: List[SceneRenderItem],
        channel_id: Optional[str],
        audio_asset_ids: Optional[List[str]],
        mode: str,
    ) -> None:
        """Verify ADR-008 licenses using license_service and scene metadata."""
        if self.license_service and channel_id:
            asset_ids = [s.asset_id for s in scenes if s.asset_id]
            if audio_asset_ids:
                for aid in audio_asset_ids:
                    if aid and aid not in asset_ids:
                        asset_ids.append(aid)

            if asset_ids:
                check_fn = getattr(self.license_service, "check_export_eligibility", None) or getattr(self.license_service, "verify_asset_for_export", None)
                if check_fn:
                    if hasattr(self.license_service, "check_export_eligibility"):
                        self.license_service.check_export_eligibility(channel_id, asset_ids)
                    else:
                        for aid in asset_ids:
                            self.license_service.verify_asset_for_export(channel_id, aid)

        # Check scene metadata for mode
        for scene in scenes:
            lic_status = scene.metadata.get("license_status")
            if not lic_status:
                continue

            lic_lower = lic_status.strip().lower()
            if mode == "export":
                valid_export_statuses = {"commercial_allowed", "owned", "creative_commons_cc0"}
                if lic_lower not in valid_export_statuses:
                    if lic_lower == "pending_review":
                        raise TimelineRenderException(
                            f"Export blocked by ADR-008: Scene {scene.scene_id} asset has license "
                            f"status '{lic_status}'. Verification required before commercial export."
                        )
                    if lic_lower in {"personal_only", "restricted", "unknown", "blocked"}:
                        raise TimelineRenderException(
                            f"Export blocked by ADR-008: Scene {scene.scene_id} asset has restricted "
                            f"license '{lic_status}'."
                        )
            elif mode == "preview":
                if lic_lower in {"restricted", "blocked"}:
                    raise TimelineRenderException(
                        f"Preview blocked: Scene {scene.scene_id} asset is explicitly restricted."
                    )

    def _calculate_effective_duration(
        self,
        durations: List[float],
        transitions: List[Tuple[str, float]],
    ) -> float:
        total = sum(durations)
        for t_name, t_dur in transitions:
            if t_name.lower() != "cut":
                total -= t_dur
        return max(0.1, total)

    def _process_audio_mix(
        self,
        scenes: List[SceneRenderItem],
        total_duration: float,
        voiceover_track_path: Optional[Path],
        background_music_path: Optional[Path],
        voiceover_volume: float,
        music_volume: float,
        ducking: bool,
        enforce_true_peak: bool,
        profile: Union[RenderProfile, TimelineRenderProfile],
        temp_dir: Path,
    ) -> Optional[Path]:
        sample_rate = getattr(profile, "audio_sample_rate", 44100)
        target_lufs = getattr(profile, "target_lufs", -14.0)
        true_peak_db = getattr(profile, "true_peak_db", -1.0)
        audio_channels = getattr(profile, "audio_channels", 2)

        has_per_scene_vo = any(s.voiceover_path and s.voiceover_path.exists() for s in scenes)
        has_global_vo = voiceover_track_path is not None and voiceover_track_path.exists()
        has_bgm = background_music_path is not None and background_music_path.exists()

        if not has_per_scene_vo and not has_global_vo and not has_bgm:
            return None

        # Build VO track
        vo_track: Optional[Path] = None
        if has_per_scene_vo:
            vo_track = temp_dir / "vo_assembled.wav"
            filter_parts = [f"aevalsrc=0:d={total_duration}:s={sample_rate}[base_silence]"]
            inputs = []
            in_idx = 0
            vo_indices = []
            current_time = 0.0

            for scene in scenes:
                if scene.voiceover_path and scene.voiceover_path.exists():
                    inputs.extend(["-i", str(scene.voiceover_path)])
                    delay_ms = int(current_time * 1000)
                    filter_parts.append(f"[{in_idx}:a]adelay={delay_ms}|{delay_ms}[vo_{in_idx}]")
                    vo_indices.append(f"[vo_{in_idx}]")
                    in_idx += 1
                current_time += scene.duration_sec

            if vo_indices:
                mix_inputs = "[base_silence]" + "".join(vo_indices)
                count = len(vo_indices) + 1
                filter_parts.append(f"{mix_inputs}amix=inputs={count}:duration=first:dropout_transition=0[vo_out]")
                vo_args = ["-y"] + inputs + [
                    "-filter_complex", ";".join(filter_parts),
                    "-map", "[vo_out]",
                    "-ar", str(sample_rate),
                    "-ac", str(audio_channels),
                    str(vo_track),
                ]
                self.wrapper.run_ffmpeg(vo_args)
        elif has_global_vo:
            vo_track = voiceover_track_path

        mixed_audio = temp_dir / "mixed_audio.wav"
        limiter_filter = ",alimiter=limit=0.85:level=false:asc=1" if enforce_true_peak else ""

        if vo_track and has_bgm:
            if ducking:
                filter_str = (
                    f"[1:a]volume={music_volume:.2f},aloop=loop=-1:size=2e+09[bgm_loop];"
                    f"[bgm_loop][0:a]sidechaincompress=threshold=0.125:ratio=4:attack=50:release=300[bgm_ducked];"
                    f"[0:a]volume={voiceover_volume:.2f},apad=whole_dur={total_duration:.2f}[vo_scaled];"
                    f"[vo_scaled][bgm_ducked]amix=inputs=2:duration=first:dropout_transition=2{limiter_filter}[mix_out]"
                )
            else:
                filter_str = (
                    f"[1:a]volume={music_volume:.2f},aloop=loop=-1:size=2e+09[bgm_loop];"
                    f"[0:a]volume={voiceover_volume:.2f},apad=whole_dur={total_duration:.2f}[vo_scaled];"
                    f"[vo_scaled][bgm_loop]amix=inputs=2:duration=first:dropout_transition=2{limiter_filter}[mix_out]"
                )

            mix_args = [
                "-y",
                "-i", str(vo_track),
                "-stream_loop", "-1",
                "-i", str(background_music_path),
                "-filter_complex", filter_str,
                "-map", "[mix_out]",
                "-t", str(total_duration),
                "-ar", str(sample_rate),
                "-ac", str(audio_channels),
                str(mixed_audio),
            ]
            self.wrapper.run_ffmpeg(mix_args)
        elif vo_track:
            filter_str = f"[0:a]volume={voiceover_volume:.2f},apad=whole_dur={total_duration:.2f}{limiter_filter}[outa]"
            mix_args = [
                "-y",
                "-i", str(vo_track),
                "-filter_complex", filter_str,
                "-map", "[outa]",
                "-t", str(total_duration),
                "-ar", str(sample_rate),
                "-ac", str(audio_channels),
                str(mixed_audio),
            ]
            self.wrapper.run_ffmpeg(mix_args)
        elif has_bgm:
            filter_str = f"[0:a]volume={music_volume:.2f}{limiter_filter}[outa]"
            bgm_args = [
                "-y",
                "-stream_loop", "-1",
                "-i", str(background_music_path),
                "-filter_complex", filter_str,
                "-map", "[outa]",
                "-t", str(total_duration),
                "-ar", str(sample_rate),
                "-ac", str(audio_channels),
                str(mixed_audio),
            ]
            self.wrapper.run_ffmpeg(bgm_args)

        if enforce_true_peak and hasattr(profile, "target_lufs"):
            normalized_audio = temp_dir / "final_ebur128_audio.wav"
            loudness_filter = (
                f"loudnorm=I={target_lufs}:TP={true_peak_db}:LRA=11,"
                f"alimiter=limit={10 ** (true_peak_db / 20.0)}:level=false:asc=1"
            )
            norm_args = [
                "-y",
                "-i", str(mixed_audio),
                "-filter:a", loudness_filter,
                "-ar", str(sample_rate),
                "-ac", str(audio_channels),
                str(normalized_audio),
            ]
            self.wrapper.run_ffmpeg(norm_args)
            return normalized_audio

        return mixed_audio

    def _mux_and_finalize(
        self,
        video_path: Path,
        audio_path: Optional[Path],
        ass_subtitles: Optional[Path],
        output_path: Path,
        profile: Union[RenderProfile, TimelineRenderProfile],
        total_duration: float,
        cancellation_token: Optional[threading.Event] = None,
    ) -> None:
        preset = getattr(profile, "preset", "medium")
        crf = getattr(profile, "crf", 20)
        vcodec = getattr(profile, "video_codec", "libx264")
        acodec = getattr(profile, "audio_codec", "aac")
        abitrate = getattr(profile, "audio_bitrate", "192k")
        sample_rate = getattr(profile, "audio_sample_rate", 44100)
        audio_channels = getattr(profile, "audio_channels", 2)

        args = ["-y", "-i", str(video_path)]

        if audio_path and audio_path.exists():
            args.extend(["-i", str(audio_path)])
        else:
            # Silent stereo audio track
            args.extend([
                "-f", "lavfi",
                "-i", f"anullsrc=channel_layout=stereo:sample_rate={sample_rate}",
            ])

        filter_parts = []
        if ass_subtitles and ass_subtitles.exists():
            sub_filter = self.subtitles.build_subtitle_filter(ass_subtitles)
            filter_parts.append(f"[0:v]{sub_filter}[v_sub]")
            v_map = "[v_sub]"
            c_v = [vcodec, "-preset", preset, "-crf", str(crf)]
        else:
            v_map = "0:v:0"
            c_v = ["copy"]

        if filter_parts:
            args.extend(["-filter_complex", ";".join(filter_parts)])

        args.extend(["-map", v_map, "-c:v"] + c_v)

        if audio_path and audio_path.exists():
            args.extend([
                "-map", "1:a:0",
                "-c:a", acodec,
                "-b:a", abitrate,
                "-ar", str(sample_rate),
                "-ac", str(audio_channels),
            ])
        else:
            args.extend([
                "-map", "1:a:0",
                "-c:a", acodec,
                "-b:a", abitrate,
                "-ar", str(sample_rate),
                "-ac", str(audio_channels),
            ])

        if total_duration > 0:
            args.extend(["-t", str(total_duration)])

        args.extend(["-f", "mp4", str(output_path)])
        self.wrapper.run_ffmpeg(args, cancellation_token=cancellation_token)

    def render_project_timeline(
        self,
        channel_id: str,
        project_id: str,
        output_path: Union[str, Path],
        scene_service: Any,
        asset_service: Any,
        channel_workspace_dir: Union[str, Path],
        profile: RenderProfile = VERTICAL_9_16,
        audio_track_path: Optional[Union[str, Path]] = None,
        voiceover_track_path: Optional[Union[str, Path]] = None,
        background_music_path: Optional[Union[str, Path]] = None,
        voiceover_volume: float = 1.0,
        background_music_volume: float = 0.2,
        enforce_true_peak: bool = True,
        task_id: Optional[str] = None,
        execution_token: Optional[Union[int, str]] = None,
        cancellation_token: Optional[threading.Event] = None,
        timeout_sec: float = 300.0,
        audio_asset_ids: Optional[List[str]] = None,
    ) -> Path:
        """Render complete video timeline directly from project and scene database entities."""
        scenes = scene_service.list_scenes(channel_id, project_id)
        if not scenes:
            raise TimelineRenderException(f"Project '{project_id}' on channel '{channel_id}' has no scenes to render.")

        sorted_scenes = sorted(scenes, key=lambda s: getattr(s, "sequence_number", 0))
        all_assets = asset_service.list_assets(channel_id)
        asset_by_scene = {a.scene_id: a for a in all_assets if getattr(a, "scene_id", None)}
        asset_by_id = {a.id: a for a in all_assets}

        scene_items: List[SceneRenderItem] = []
        for s in sorted_scenes:
            matched_asset = asset_by_scene.get(s.id)
            if not matched_asset and getattr(s, "metadata_json", None) and "asset_id" in s.metadata_json:
                matched_asset = asset_by_id.get(s.metadata_json["asset_id"])

            if not matched_asset:
                raise TimelineRenderException(
                    f"Scene '{s.id}' (sequence {getattr(s, 'sequence_number', 0)}) has no associated visual asset in MAM."
                )

            abs_asset_path = Path(channel_workspace_dir) / matched_asset.file_path
            meta = getattr(s, "metadata_json", {}) or {}
            transition = meta.get("transition", "cut") if isinstance(meta, dict) else "cut"
            trans_dur = float(meta.get("transition_duration_sec", 0.5)) if isinstance(meta, dict) else 0.5

            scene_items.append(
                SceneRenderItem(
                    asset_path=abs_asset_path,
                    video_path=abs_asset_path,
                    duration_sec=getattr(s, "duration_seconds", 5.0),
                    transition=transition,
                    transition_duration_sec=trans_dur,
                    scene_id=s.id,
                    asset_id=matched_asset.id,
                )
            )

        resolved_audio_asset_ids = list(audio_asset_ids or [])
        for a in all_assets:
            is_audio = getattr(a, "asset_type", None) and (
                getattr(a.asset_type, "value", str(a.asset_type)) == "audio"
            )
            if is_audio and getattr(a, "project_id", None) == project_id:
                if a.id not in resolved_audio_asset_ids:
                    resolved_audio_asset_ids.append(a.id)

        return self.render_timeline(
            scenes=scene_items,
            output_path=output_path,
            profile=profile,
            audio_track_path=audio_track_path,
            voiceover_track_path=voiceover_track_path,
            background_music_path=background_music_path,
            voiceover_volume=voiceover_volume,
            background_music_volume=background_music_volume,
            enforce_true_peak=enforce_true_peak,
            channel_id=channel_id,
            task_id=task_id,
            execution_token=execution_token,
            cancellation_token=cancellation_token,
            timeout_sec=timeout_sec,
            audio_asset_ids=resolved_audio_asset_ids,
        )

    def render_flow_project_timeline(
        self,
        flow_repo: FlowInboundRepository,
        flow_project_id: str,
        output_path: Path,
        scene_repo: Optional[Any] = None,
        storage_service: Optional[StorageService] = None,
        profile: Optional[Union[RenderProfile, TimelineRenderProfile]] = None,
        mode: str = "export",
        background_music_path: Optional[Path] = None,
        burn_subtitles: bool = True,
    ) -> Path:
        """Convenience integration: extracts active scene takes from FlowInboundRepository
        and renders the full timeline for a Google Flow imported project.
        """
        storage = storage_service or self.storage
        render_items: List[SceneRenderItem] = []

        if scene_repo:
            scenes = scene_repo.list_by_project(flow_project_id)
            if not scenes:
                raise TimelineRenderException(f"No scenes found for project {flow_project_id}")
            sorted_scenes = sorted(
                scenes,
                key=lambda s: getattr(s, "order_index", 0) if hasattr(s, "order_index") else s.get("order_index", 0),
            )
            for sc in sorted_scenes:
                scene_id = getattr(sc, "id", None) or (sc.get("id") if isinstance(sc, dict) else None)
                order = getattr(sc, "order_index", 0) if hasattr(sc, "order_index") else (sc.get("order_index", 0) if isinstance(sc, dict) else 0)
                sub_text = getattr(sc, "prompt_text", None) or (sc.get("prompt_text") if isinstance(sc, dict) else None)
                active_take = flow_repo.get_active_take(flow_project_id, scene_id)
                if not active_take:
                    raise TimelineRenderException(f"Scene {scene_id} has no active take.")
                video_p = self._resolve_take_file_path(active_take, storage)
                meta = self._parse_take_metadata(active_take)
                dur = active_take.get("duration_sec") or 2.0
                render_items.append(
                    SceneRenderItem(
                        scene_id=scene_id,
                        video_path=video_p,
                        asset_path=video_p,
                        duration_sec=float(dur),
                        order=int(order),
                        transition_out="fade",
                        transition_duration_sec=0.5,
                        aspect_mode="cover",
                        subtitle_text=sub_text,
                        asset_sha256=active_take.get("file_sha256"),
                        metadata={
                            "license_status": meta.get("license_status") or "commercial_allowed",
                            "take_id": active_take["id"],
                        },
                    )
                )
        else:
            takes = flow_repo.list_takes_for_project(flow_project_id)
            active_takes = [t for t in takes if t.get("is_active")]
            if not active_takes:
                raise TimelineRenderException(f"No active takes found for project {flow_project_id}")
            active_takes.sort(key=lambda t: t.get("scene_id"))
            for i, take in enumerate(active_takes):
                video_p = self._resolve_take_file_path(take, storage)
                meta = self._parse_take_metadata(take)
                dur = take.get("duration_sec") or 2.0
                sub_text = meta.get("prompt_text") or f"Scene {take['scene_id']}"
                render_items.append(
                    SceneRenderItem(
                        scene_id=take["scene_id"],
                        video_path=video_p,
                        asset_path=video_p,
                        duration_sec=float(dur),
                        order=i + 1,
                        transition_out="fade",
                        transition_duration_sec=0.5,
                        aspect_mode="cover",
                        subtitle_text=sub_text,
                        asset_sha256=take.get("file_sha256"),
                        metadata={
                            "license_status": meta.get("license_status") or "commercial_allowed",
                            "take_id": take["id"],
                        },
                    )
                )

        return self.render_timeline(
            scenes=render_items,
            output_path=output_path,
            profile=profile,
            background_music_path=background_music_path,
            burn_subtitles=burn_subtitles,
            mode=mode,
        )

    def _resolve_take_file_path(self, take: Dict[str, Any], storage: Optional[StorageService]) -> Path:
        storage_key = take.get("storage_key")
        if storage and storage_key:
            try:
                p = storage.get_local_path(storage_key)
                if p.exists():
                    return p
            except Exception:
                pass

        if storage_key and Path(storage_key).exists():
            return Path(storage_key)

        meta = self._parse_take_metadata(take)
        if meta.get("file_path") and Path(meta["file_path"]).exists():
            return Path(meta["file_path"])

        raise TimelineRenderException(f"Media file for take '{take.get('id')}' could not be located on disk.")

    def _parse_take_metadata(self, take: Dict[str, Any]) -> Dict[str, Any]:
        raw = take.get("metadata_json") or take.get("metadata") or {}
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except Exception:
                return {}
        return dict(raw) if isinstance(raw, dict) else {}
