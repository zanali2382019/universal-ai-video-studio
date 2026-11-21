"""Timeline rendering engine for normalizing scene media, applying transitions, and compiling videos."""
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Union, Any, Dict
import logging
import os
import shutil
import tempfile
import threading
import uuid

from src.core.exceptions import (
    TimelineRenderException,
    MediaValidationException,
    StaleExecutionTokenException,
    TaskNotFoundException,
    RenderCancelledException,
    LicenseVerificationException,
)
from src.core.logger import get_logger
from src.rendering.ffmpeg_wrapper import FFmpegWrapper
from src.rendering.probe_service import MediaProbeService
from src.rendering.profiles import RenderProfile, VERTICAL_9_16

logger = get_logger("studio.rendering.timeline")


@dataclass
class SceneRenderItem:
    """Individual scene specification for timeline compilation."""
    asset_path: Union[str, Path]
    duration_sec: float = 5.0
    transition: str = "cut"
    transition_duration_sec: float = 0.5
    scene_id: Optional[str] = None
    asset_id: Optional[str] = None

    def __post_init__(self):
        self.asset_path = Path(self.asset_path)
        if self.duration_sec <= 0:
            raise ValueError(f"Scene duration must be positive, got {self.duration_sec}")
        self.transition = self.transition.lower()
        if self.transition not in ("cut", "fade", "crossfade", "dissolve"):
            raise ValueError(f"Unsupported transition '{self.transition}'. Supported: 'cut', 'fade'")
        if self.transition in ("fade", "crossfade", "dissolve") and self.transition_duration_sec >= self.duration_sec:
            raise ValueError(
                f"Transition duration ({self.transition_duration_sec}s) must be strictly less than scene duration ({self.duration_sec}s)"
            )


class TimelineRenderer:
    """Renders scene timelines by normalizing visual media, sequencing cuts, and applying audio muxing."""

    def __init__(
        self,
        ffmpeg_wrapper: Optional[FFmpegWrapper] = None,
        probe_service: Optional[MediaProbeService] = None,
        license_service: Optional[Any] = None,
        task_repository: Optional[Any] = None,
    ) -> None:
        self.wrapper = ffmpeg_wrapper or FFmpegWrapper()
        self.probe_service = probe_service or MediaProbeService(ffmpeg_wrapper=self.wrapper)
        self.license_service = license_service
        self.task_repo = task_repository

    def normalize_scene(
        self,
        input_path: Union[str, Path],
        duration_sec: float,
        output_path: Union[str, Path],
        profile: RenderProfile = VERTICAL_9_16,
        cancellation_token: Optional[threading.Event] = None,
        timeout_sec: float = 120.0,
    ) -> Path:
        """Normalize an image or video asset into a conforming video segment matching profile geometry and framerate.

        Args:
            input_path: Absolute path to the source image or video file.
            duration_sec: Duration in seconds for the normalized segment.
            output_path: Target destination path for the normalized MP4 segment.
            profile: RenderProfile defining resolution (e.g. 1080x1920) and fps.
            cancellation_token: Optional Event to cancel processing.
            timeout_sec: Maximum execution duration before timing out.

        Returns:
            Path: Absolute path to the normalized video segment.
        """
        input_path = Path(input_path)
        if not input_path.exists():
            raise TimelineRenderException(f"Input asset does not exist: '{input_path}'")

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temp_output = output_path.parent / f"{output_path.stem}.tmp.{uuid.uuid4().hex[:8]}.mp4"

        is_image = input_path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
        scaling_filter = profile.get_scaling_filter()

        try:
            if is_image:
                cmd = [
                    "-y",
                    "-loop", "1",
                    "-t", str(duration_sec),
                    "-i", str(input_path),
                    "-vf", scaling_filter,
                    "-c:v", profile.video_codec,
                    "-preset", profile.preset,
                    "-crf", str(profile.crf),
                    "-an",
                    str(temp_output),
                ]
            else:
                cmd = [
                    "-y",
                    "-t", str(duration_sec),
                    "-i", str(input_path),
                    "-vf", scaling_filter,
                    "-c:v", profile.video_codec,
                    "-preset", profile.preset,
                    "-crf", str(profile.crf),
                    "-an",
                    str(temp_output),
                ]

            self.wrapper.run_ffmpeg(cmd, timeout_sec=timeout_sec, check=True, cancellation_token=cancellation_token)

            # Atomic publication
            os.replace(temp_output, output_path)
            logger.debug(f"Normalized scene segment '{input_path.name}' -> '{output_path.name}' ({duration_sec}s)")
            return output_path
        finally:
            if temp_output.exists():
                try:
                    temp_output.unlink(missing_ok=True)
                except Exception:
                    pass

    def concatenate_scenes(
        self,
        segment_paths: List[Path],
        output_path: Union[str, Path],
        transitions: Optional[List[str]] = None,
        transition_durations: Optional[List[float]] = None,
        durations: Optional[List[float]] = None,
        profile: RenderProfile = VERTICAL_9_16,
        cancellation_token: Optional[threading.Event] = None,
        timeout_sec: float = 180.0,
    ) -> Path:
        """Concatenate normalized video segments into a single visual timeline.

        Args:
            segment_paths: List of paths to normalized video segments.
            output_path: Destination path for concatenated video.
            transitions: Optional list of transition types per cut (e.g. ['cut', 'fade']).
            transition_durations: Optional list of durations per transition.
            durations: Optional list of segment durations (used for offset computation in xfade).
            profile: Target RenderProfile.
            cancellation_token: Optional cancellation event.
            timeout_sec: Maximum execution timeout.

        Returns:
            Path: Absolute path to the concatenated visual timeline video.
        """
        if not segment_paths:
            raise TimelineRenderException("Cannot concatenate scenes: segment_paths list is empty.")

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temp_output = output_path.parent / f"{output_path.stem}.tmp.{uuid.uuid4().hex[:8]}.mp4"

        # Single segment case: copy directly
        if len(segment_paths) == 1:
            shutil.copy2(segment_paths[0], temp_output)
            os.replace(temp_output, output_path)
            return output_path

        n = len(segment_paths)
        if transitions is None:
            transitions = ["cut"] * (n - 1)
        if transition_durations is None:
            transition_durations = [0.5] * (n - 1)

        has_xfade = any(t in ("fade", "crossfade", "dissolve") for t in transitions)

        try:
            if not has_xfade:
                # Fast and standard concat filter for hard cuts
                inputs: List[str] = []
                for p in segment_paths:
                    inputs.extend(["-i", str(p)])

                filter_concat = "".join(f"[{i}:v]" for i in range(n)) + f"concat=n={n}:v=1:a=0[outv]"
                cmd = (
                    ["-y"]
                    + inputs
                    + [
                        "-filter_complex", filter_concat,
                        "-map", "[outv]",
                        "-c:v", profile.video_codec,
                        "-preset", profile.preset,
                        "-crf", str(profile.crf),
                        str(temp_output),
                    ]
                )
            else:
                # Compute durations if not provided
                if durations is None or len(durations) != n:
                    durations = []
                    for p in segment_paths:
                        pr = self.probe_service.probe_file(p)
                        durations.append(pr.duration_sec or 5.0)

                inputs = []
                for p in segment_paths:
                    inputs.extend(["-i", str(p)])

                # Build xfade filter chain
                filter_parts = []
                current_v = "0:v"
                accumulated_time = durations[0]

                for i in range(n - 1):
                    next_v = f"{i + 1}:v"
                    out_v = f"v{i + 1}" if i < n - 2 else "outv"
                    t_type = "fade" if transitions[i] in ("fade", "crossfade", "dissolve") else "fade"
                    t_dur = min(transition_durations[i], durations[i] * 0.4, durations[i + 1] * 0.4)
                    offset = max(0.0, accumulated_time - t_dur)

                    filter_parts.append(
                        f"[{current_v}][{next_v}]xfade=transition={t_type}:duration={t_dur:.2f}:offset={offset:.2f}[{out_v}]"
                    )
                    current_v = out_v
                    accumulated_time = offset + durations[i + 1]

                filter_complex = ";".join(filter_parts)
                cmd = (
                    ["-y"]
                    + inputs
                    + [
                        "-filter_complex", filter_complex,
                        "-map", "[outv]",
                        "-c:v", profile.video_codec,
                        "-preset", profile.preset,
                        "-crf", str(profile.crf),
                        str(temp_output),
                    ]
                )

            self.wrapper.run_ffmpeg(cmd, timeout_sec=timeout_sec, check=True, cancellation_token=cancellation_token)
            os.replace(temp_output, output_path)
            logger.info(f"Successfully concatenated {n} scenes into '{output_path.name}'.")
            return output_path
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
        profile: RenderProfile = VERTICAL_9_16,
        audio_track_path: Optional[Union[str, Path]] = None,
        voiceover_track_path: Optional[Union[str, Path]] = None,
        background_music_path: Optional[Union[str, Path]] = None,
        voiceover_volume: float = 1.0,
        background_music_volume: float = 0.2,
        enforce_true_peak: bool = True,
        channel_id: Optional[str] = None,
        task_id: Optional[str] = None,
        execution_token: Optional[int] = None,
        cancellation_token: Optional[threading.Event] = None,
        timeout_sec: float = 300.0,
        audio_asset_ids: Optional[List[str]] = None,
    ) -> Path:
        """Render complete video timeline from scene specifications with licensing, fencing, and output validation.

        Args:
            scenes: List of SceneRenderItem specifications.
            output_path: Final destination path for the rendered MP4 video.
            profile: Target RenderProfile (default: 9:16 vertical 1080x1920 @ 30fps).
            audio_track_path: Legacy alias for primary voiceover/audio track.
            voiceover_track_path: Optional primary voiceover narration track.
            background_music_path: Optional secondary background music track.
            voiceover_volume: Gain factor for voiceover (default: 1.0).
            background_music_volume: Gain factor for background music (default: 0.2).
            enforce_true_peak: If True, applies lookahead True Peak limiter at <= -1.0 dBTP (ADR-003).
            channel_id: Channel ID for license validation and audit context.
            task_id: Active task ID for fencing checks.
            execution_token: Execution token to guard against stale workers committing outputs.
            cancellation_token: Token to cancel in-flight rendering.
            timeout_sec: Overall execution timeout.

        Returns:
            Path: Validated, playable output MP4 video file.
        """
        if not scenes:
            raise TimelineRenderException("Cannot render timeline: scenes list is empty.")

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # ---------------------------------------------------------------------
        # 1. License Gate Enforcement (ADR-008: Visual + Audio Assets)
        # ---------------------------------------------------------------------
        if self.license_service and channel_id:
            asset_ids = [s.asset_id for s in scenes if s.asset_id]
            if audio_asset_ids:
                for aid in audio_asset_ids:
                    if aid and aid not in asset_ids:
                        asset_ids.append(aid)

            if asset_ids:
                logger.info(f"Checking license export readiness for {len(asset_ids)} asset(s) (visual + audio) on channel '{channel_id}'.")
                self.license_service.check_export_eligibility(channel_id, asset_ids)

        # ---------------------------------------------------------------------
        # 2. Stale Worker Fencing Gate
        # ---------------------------------------------------------------------
        if self.task_repo and task_id and execution_token is not None:
            task = self.task_repo.get_by_id(task_id)
            if not task:
                raise TaskNotFoundException(task_id)
            if task.execution_token != execution_token:
                logger.error(
                    f"Task fencing violation: Worker token {execution_token} != DB token {task.execution_token} for task '{task_id}'."
                )
                raise StaleExecutionTokenException(
                    task_id=task_id,
                    expected_token=execution_token,
                    actual_token=task.execution_token,
                    message=f"Fencing check failed for task '{task_id}': worker token {execution_token} is stale (DB token={task.execution_token}).",
                )

        # ---------------------------------------------------------------------
        # 3. Create isolated working cache for intermediate segments
        # ---------------------------------------------------------------------
        temp_work_dir = output_path.parent / f"_render_cache_{uuid.uuid4().hex[:8]}"
        temp_work_dir.mkdir(parents=True, exist_ok=True)
        final_temp = output_path.parent / f"{output_path.stem}.final.tmp.{uuid.uuid4().hex[:8]}.mp4"

        try:
            # -----------------------------------------------------------------
            # 4. Normalize individual scene segments
            # -----------------------------------------------------------------
            normalized_segments: List[Path] = []
            segment_durations: List[float] = []
            transitions: List[str] = []
            transition_durations: List[float] = []

            for idx, scene in enumerate(scenes):
                if cancellation_token and cancellation_token.is_set():
                    raise RenderCancelledException("Timeline render cancelled prior to segment normalization.")

                seg_dest = temp_work_dir / f"scene_{idx:03d}.mp4"
                self.normalize_scene(
                    input_path=scene.asset_path,
                    duration_sec=scene.duration_sec,
                    output_path=seg_dest,
                    profile=profile,
                    cancellation_token=cancellation_token,
                    timeout_sec=timeout_sec,
                )
                normalized_segments.append(seg_dest)
                segment_durations.append(scene.duration_sec)

                if idx < len(scenes) - 1:
                    transitions.append(scene.transition)
                    transition_durations.append(scene.transition_duration_sec)

            # -----------------------------------------------------------------
            # 5. Concatenate visual timeline
            # -----------------------------------------------------------------
            visual_timeline = temp_work_dir / "visual_timeline.mp4"
            self.concatenate_scenes(
                segment_paths=normalized_segments,
                output_path=visual_timeline,
                transitions=transitions,
                transition_durations=transition_durations,
                durations=segment_durations,
                profile=profile,
                cancellation_token=cancellation_token,
                timeout_sec=timeout_sec,
            )

            # Probe visual timeline duration
            vis_probe = self.probe_service.probe_file(visual_timeline)
            vis_duration = vis_probe.duration_sec or sum(segment_durations)

            # -----------------------------------------------------------------
            # 6. Audio multiplexing (Voiceover, Background Music & True Peak Limiting)
            # -----------------------------------------------------------------
            effective_vo = voiceover_track_path or audio_track_path
            has_vo = bool(effective_vo and Path(effective_vo).exists())
            has_bg = bool(background_music_path and Path(background_music_path).exists())

            limiter_filter = ",alimiter=limit=0.85:level=false:asc=1" if enforce_true_peak else ""

            if has_vo and has_bg:
                logger.info(
                    f"Muxing voiceover '{Path(effective_vo).name}' (vol={voiceover_volume:.2f}) and background music "
                    f"'{Path(background_music_path).name}' (vol={background_music_volume:.2f}) onto visual timeline ({vis_duration:.2f}s)."
                )
                filter_audio = (
                    f"[1:a]volume={voiceover_volume:.2f},apad=whole_dur={vis_duration:.2f}[a_vo];"
                    f"[2:a]volume={background_music_volume:.2f}[a_bg];"
                    f"[a_vo][a_bg]amix=inputs=2:duration=first:dropout_transition=2{limiter_filter}[outa]"
                )
                mux_cmd = [
                    "-y",
                    "-i", str(visual_timeline),
                    "-i", str(effective_vo),
                    "-stream_loop", "-1",
                    "-i", str(background_music_path),
                    "-filter_complex", filter_audio,
                    "-map", "0:v:0",
                    "-map", "[outa]",
                    "-c:v", "copy",
                    "-c:a", profile.audio_codec,
                    "-b:a", profile.audio_bitrate,
                    "-ar", str(profile.audio_sample_rate),
                    "-ac", str(profile.audio_channels),
                    "-t", str(vis_duration),
                    str(final_temp),
                ]
            elif has_vo:
                logger.info(f"Muxing voiceover track '{Path(effective_vo).name}' onto timeline ({vis_duration:.2f}s).")
                filter_audio = f"[1:a]volume={voiceover_volume:.2f},apad=whole_dur={vis_duration:.2f}{limiter_filter}[outa]"
                mux_cmd = [
                    "-y",
                    "-i", str(visual_timeline),
                    "-i", str(effective_vo),
                    "-filter_complex", filter_audio,
                    "-map", "0:v:0",
                    "-map", "[outa]",
                    "-c:v", "copy",
                    "-c:a", profile.audio_codec,
                    "-b:a", profile.audio_bitrate,
                    "-ar", str(profile.audio_sample_rate),
                    "-ac", str(profile.audio_channels),
                    "-t", str(vis_duration),
                    str(final_temp),
                ]
            elif has_bg:
                logger.info(f"Muxing background music '{Path(background_music_path).name}' onto timeline ({vis_duration:.2f}s).")
                filter_audio = f"[1:a]volume={background_music_volume:.2f}{limiter_filter}[outa]"
                mux_cmd = [
                    "-y",
                    "-i", str(visual_timeline),
                    "-stream_loop", "-1",
                    "-i", str(background_music_path),
                    "-filter_complex", filter_audio,
                    "-map", "0:v:0",
                    "-map", "[outa]",
                    "-c:v", "copy",
                    "-c:a", profile.audio_codec,
                    "-b:a", profile.audio_bitrate,
                    "-ar", str(profile.audio_sample_rate),
                    "-ac", str(profile.audio_channels),
                    "-t", str(vis_duration),
                    str(final_temp),
                ]
            else:
                logger.debug(f"Adding silent audio track to conform timeline container ({vis_duration:.2f}s).")
                mux_cmd = [
                    "-y",
                    "-i", str(visual_timeline),
                    "-f", "lavfi",
                    "-i", f"anullsrc=channel_layout=stereo:sample_rate={profile.audio_sample_rate}",
                    "-c:v", "copy",
                    "-c:a", profile.audio_codec,
                    "-b:a", profile.audio_bitrate,
                    "-map", "0:v:0",
                    "-map", "1:a:0",
                    "-t", str(vis_duration),
                    str(final_temp),
                ]

            self.wrapper.run_ffmpeg(mux_cmd, timeout_sec=timeout_sec, check=True, cancellation_token=cancellation_token)

            # -----------------------------------------------------------------
            # 7. Pre-publication Fencing Check
            # -----------------------------------------------------------------
            if self.task_repo and task_id and execution_token is not None:
                task = self.task_repo.get_by_id(task_id)
                if not task or task.execution_token != execution_token:
                    raise StaleExecutionTokenException(
                        task_id=task_id,
                        expected_token=execution_token,
                        actual_token=task.execution_token if task else -1,
                        message="Pre-publication fencing check failed: Worker token is no longer valid.",
                    )

            # -----------------------------------------------------------------
            # 8. Atomic Publication & Final Media Validation
            # -----------------------------------------------------------------
            os.replace(final_temp, output_path)

            validation = self.probe_service.validate_media(
                file_path=output_path,
                expected_width=profile.width,
                expected_height=profile.height,
                expected_fps=profile.fps,
                expected_video_codec="h264",
                require_audio=True,
            )
            probe_data = validation.get("probe", {})
            dur = probe_data.get("duration_sec", 0.0) or vis_duration
            logger.info(
                f"Timeline rendered and validated successfully: '{output_path}' "
                f"({dur:.2f}s, {profile.resolution_str} @ {profile.fps}fps)."
            )
            return output_path
        finally:
            # Clean up intermediate workspace and dangling temporary files
            if temp_work_dir.exists():
                try:
                    shutil.rmtree(temp_work_dir, ignore_errors=True)
                except Exception:
                    pass
            if final_temp.exists():
                try:
                    final_temp.unlink(missing_ok=True)
                except Exception:
                    pass

    def render_project_timeline(
        self,
        channel_id: str,
        project_id: str,
        output_path: Union[str, Path],
        scene_service: Any,
        asset_service: Any,
        channel_workspace_dir: Path,
        profile: RenderProfile = VERTICAL_9_16,
        audio_track_path: Optional[Union[str, Path]] = None,
        voiceover_track_path: Optional[Union[str, Path]] = None,
        background_music_path: Optional[Union[str, Path]] = None,
        voiceover_volume: float = 1.0,
        background_music_volume: float = 0.2,
        enforce_true_peak: bool = True,
        task_id: Optional[str] = None,
        execution_token: Optional[int] = None,
        cancellation_token: Optional[threading.Event] = None,
        timeout_sec: float = 300.0,
        audio_asset_ids: Optional[List[str]] = None,
    ) -> Path:
        """Render complete video timeline directly from project and scene database entities.

        Args:
            channel_id: Channel ID owner.
            project_id: Project ID containing scenes.
            output_path: Final destination path.
            scene_service: SceneService instance to retrieve sequential scenes.
            asset_service: AssetService instance to resolve MAM asset references.
            channel_workspace_dir: Root workspace path for resolving relative asset paths.
            profile: Target RenderProfile.
            audio_track_path: Optional audio track (legacy alias for voiceover).
            voiceover_track_path: Optional primary voiceover narration track.
            background_music_path: Optional background music track.
            voiceover_volume: Voiceover gain factor.
            background_music_volume: Background music gain factor.
            enforce_true_peak: If True, applies True Peak limiter <= -1.0 dBTP.
            task_id: Active task ID for fencing.
            execution_token: Active execution token.
            cancellation_token: Optional cancellation event.
            timeout_sec: Timeout.

        Returns:
            Path: Rendered and validated playable MP4 video.
        """
        scenes = scene_service.list_scenes(channel_id, project_id)
        if not scenes:
            raise TimelineRenderException(f"Project '{project_id}' on channel '{channel_id}' has no scenes to render.")

        # Sort scenes by sequence_number ascending
        sorted_scenes = sorted(scenes, key=lambda s: s.sequence_number)

        # Retrieve assets for project
        all_assets = asset_service.list_assets(channel_id)
        asset_by_scene: Dict[str, Any] = {}
        asset_by_id: Dict[str, Any] = {a.id: a for a in all_assets}
        for a in all_assets:
            if a.scene_id:
                asset_by_scene[a.scene_id] = a

        scene_items: List[SceneRenderItem] = []
        for s in sorted_scenes:
            # Match asset via scene_id or metadata_json['asset_id']
            matched_asset = asset_by_scene.get(s.id)
            if not matched_asset and s.metadata_json and "asset_id" in s.metadata_json:
                matched_asset = asset_by_id.get(s.metadata_json["asset_id"])

            if not matched_asset:
                raise TimelineRenderException(
                    f"Scene '{s.id}' (sequence {s.sequence_number}) has no associated visual asset in MAM."
                )

            # Resolve absolute asset path
            abs_asset_path = Path(channel_workspace_dir) / matched_asset.file_path
            transition = s.metadata_json.get("transition", "cut") if s.metadata_json else "cut"
            trans_dur = float(s.metadata_json.get("transition_duration_sec", 0.5)) if s.metadata_json else 0.5

            scene_items.append(
                SceneRenderItem(
                    asset_path=abs_asset_path,
                    duration_sec=s.duration_seconds,
                    transition=transition,
                    transition_duration_sec=trans_dur,
                    scene_id=s.id,
                    asset_id=matched_asset.id,
                )
            )

        # Resolve audio asset IDs for licensing gate (ADR-008)
        resolved_audio_asset_ids = list(audio_asset_ids or [])
        for a in all_assets:
            is_audio = getattr(a, "asset_type", None) and (
                getattr(a.asset_type, "value", str(a.asset_type)) == "audio"
            )
            if is_audio and a.project_id == project_id:
                if a.id not in resolved_audio_asset_ids:
                    resolved_audio_asset_ids.append(a.id)
            for apath in [voiceover_track_path, background_music_path, audio_track_path]:
                if apath and (str(apath).endswith(a.file_path) or a.file_name in str(apath)):
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
