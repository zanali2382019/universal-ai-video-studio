"""Video render task handler bridging TaskService and WorkerPool to TimelineRenderer."""
import os
import time
import uuid
import logging
import threading
from pathlib import Path
from typing import Optional, Dict, Any, Union, Callable

from src.core.config import get_settings
from src.core.constants import (
    TaskType,
    TaskStatus,
    ProjectStatus,
    FileOperationType,
    AspectRatio,
)
from src.core.exceptions import (
    ValidationException,
    StaleExecutionTokenException,
    RenderCancelledException,
    TimelineRenderException,
    LicenseVerificationException,
    ProjectNotFoundException,
)
from src.core.logger import get_logger, log_context
from src.database.connection import DatabaseManager
from src.database.models import TaskModel, ProjectUpdate, TaskCheckpointModel
from src.database.repositories.task_repository import TaskRepository
from src.database.repositories.project_repository import ProjectRepository
from src.database.repositories.scene_repository import SceneRepository
from src.database.repositories.asset_repository import AssetRepository
from src.database.repositories.license_repository import LicenseRepository
from src.database.repositories.file_operation_repository import FileOperationRepository
from src.services.project_service import ProjectService
from src.services.scene_service import SceneService
from src.services.asset_service import AssetService
from src.services.license_service import LicenseService
from src.services.checkpoint_service import CheckpointService
from src.services.heartbeat_daemon import HeartbeatDaemon
from src.rendering.profiles import RenderProfile, VERTICAL_9_16, get_profile
from src.rendering.timeline_renderer import TimelineRenderer
from src.utils.file_utils import safe_resolve_path, calculate_file_sha256

logger = get_logger("studio.rendering.task_handler")


class VideoRenderTaskHandler:
    """Executes VIDEO_RENDERING background tasks dispatched to WorkerPool.

    Integrates:
    1. 5-step checkpoint protocol via CheckpointService (PRE_STEP_INTENT -> EXECUTE -> VERIFY -> COMMIT -> AUDIT).
    2. Strict execution-token fencing and active lease monitoring via HeartbeatDaemon.
    3. Cancellation propagation to FFmpeg process tree on lease loss or task abort.
    4. License validation gate before processing.
    5. Safe output isolation in cache/ and atomic publication to target destination.
    6. Project status synchronization (RENDERING -> COMPLETED / FAILED / CANCELLED).
    """

    def __init__(
        self,
        timeline_renderer: Optional[TimelineRenderer] = None,
        scene_service: Optional[SceneService] = None,
        asset_service: Optional[AssetService] = None,
        project_service: Optional[ProjectService] = None,
        checkpoint_service: Optional[CheckpointService] = None,
        license_service: Optional[LicenseService] = None,
        task_repository: Optional[TaskRepository] = None,
        db_manager: Optional[DatabaseManager] = None,
        service_logger: Optional[logging.Logger] = None,
    ) -> None:
        self.db_manager = (
            db_manager
            or (task_repository and task_repository.db_manager)
            or (checkpoint_service and checkpoint_service.db_manager)
            or DatabaseManager()
        )
        self._logger = service_logger or logger
        self.settings = get_settings()

        self.task_repo = task_repository or TaskRepository(db_manager=self.db_manager)
        self.license_service = license_service or LicenseService(
            license_repository=LicenseRepository(db_manager=self.db_manager),
            db_manager=self.db_manager,
        )
        self.timeline_renderer = timeline_renderer or TimelineRenderer(
            license_service=self.license_service,
            task_repository=self.task_repo,
        )

        self.scene_service = scene_service or SceneService(
            scene_repository=SceneRepository(db_manager=self.db_manager),
            project_repository=ProjectRepository(db_manager=self.db_manager),
        )
        self.asset_service = asset_service or AssetService(
            asset_repository=AssetRepository(db_manager=self.db_manager),
            file_operation_repository=FileOperationRepository(db_manager=self.db_manager),
        )
        self.project_service = project_service or ProjectService(
            project_repository=ProjectRepository(db_manager=self.db_manager),
        )
        self.checkpoint_service = checkpoint_service or CheckpointService(
            task_repository=self.task_repo,
            file_operation_repository=FileOperationRepository(db_manager=self.db_manager),
            asset_repository=AssetRepository(db_manager=self.db_manager),
            db_manager=self.db_manager,
        )

    def __call__(self, task: TaskModel, heartbeat: Optional[HeartbeatDaemon] = None) -> Dict[str, Any]:
        """Allow instance to be used directly as a callable worker task handler."""
        return self.handle_task(task, heartbeat)

    def handle_task(self, task: TaskModel, heartbeat: Optional[HeartbeatDaemon] = None) -> Dict[str, Any]:
        """Execute a video rendering task through the production pipeline.

        Args:
            task: Claimed TaskModel instance.
            heartbeat: Active HeartbeatDaemon pulsing lease renewals.

        Returns:
            Dict containing rendered video metadata and publication status.

        Raises:
            StaleExecutionTokenException: If worker token is stale or lease was lost.
            RenderCancelledException: If task was cancelled during execution.
            TimelineRenderException: If timeline compilation fails.
            ValidationException: If input validation fails.
        """
        # 1. Verify task type
        if task.task_type != TaskType.VIDEO_RENDERING:
            raise ValidationException(
                f"VideoRenderTaskHandler received invalid task_type '{task.task_type}' (expected '{TaskType.VIDEO_RENDERING.value}')."
            )

        # 2. Check initial fencing state
        if heartbeat and heartbeat.is_lease_lost:
            raise StaleExecutionTokenException(
                task_id=task.id,
                expected_token=task.execution_token,
                actual_token=task.execution_token,
                message="Heartbeat reported lease lost before render execution began.",
            )

        channel_id = task.channel_id.strip()
        payload = task.payload or {}
        project_id = (task.project_id or payload.get("project_id") or "").strip()

        if not project_id:
            raise ValidationException(f"Task '{task.id}' payload missing mandatory 'project_id'.")

        clean_ch = channel_id
        channel_dir = self.settings.channels_root / clean_ch

        with log_context(channel_id=clean_ch, project_id=project_id, task_id=task.id):
            self._logger.info(
                f"VideoRenderTaskHandler started for project '{project_id}' on channel '{clean_ch}' "
                f"(token={task.execution_token}, worker='{task.worker_id}')."
            )

            # 3. Resolve Render Profile
            profile_val = payload.get("profile", "9:16")
            if isinstance(profile_val, RenderProfile):
                profile = profile_val
            elif isinstance(profile_val, str):
                profile = get_profile(profile_val)
            elif isinstance(profile_val, dict):
                profile = RenderProfile(**profile_val)
            else:
                profile = VERTICAL_9_16

            # 4. Resolve audio inputs
            vo_path_raw = payload.get("voiceover_track_path") or payload.get("audio_track_path")
            voiceover_track_path = None
            if vo_path_raw:
                vo_path_obj = Path(vo_path_raw)
                if not vo_path_obj.is_absolute():
                    vo_path_obj = safe_resolve_path(channel_dir, vo_path_obj)
                voiceover_track_path = str(vo_path_obj)

            bg_path_raw = payload.get("background_music_path")
            background_music_path = None
            if bg_path_raw:
                bg_path_obj = Path(bg_path_raw)
                if not bg_path_obj.is_absolute():
                    bg_path_obj = safe_resolve_path(channel_dir, bg_path_obj)
                background_music_path = str(bg_path_obj)

            voiceover_volume = float(payload.get("voiceover_volume", 1.0))
            background_music_volume = float(payload.get("background_music_volume", 0.2))
            enforce_true_peak = bool(payload.get("enforce_true_peak", True))
            timeout_sec = float(payload.get("timeout_sec", 600.0))
            clean_mode = str(payload.get("mode", "preview")).strip().lower()

            payload_audio_assets = list(payload.get("audio_asset_ids") or [])
            if payload.get("voiceover_asset_id") and payload["voiceover_asset_id"] not in payload_audio_assets:
                payload_audio_assets.append(payload["voiceover_asset_id"])
            if payload.get("background_music_asset_id") and payload["background_music_asset_id"] not in payload_audio_assets:
                payload_audio_assets.append(payload["background_music_asset_id"])

            # 5. Determine target relative path in channel workspace
            output_rel_str = payload.get("output_rel_path")
            if output_rel_str:
                output_rel_path = Path(output_rel_str)
            else:
                output_rel_path = Path("projects") / project_id / "final_video.mp4"

            # 6. Set up cancellation coordination between Heartbeat and FFmpeg
            cancellation_event = threading.Event()
            if heartbeat:
                orig_on_lease_lost = heartbeat.on_lease_lost

                def _handle_heartbeat_lease_lost() -> None:
                    cancellation_event.set()
                    if orig_on_lease_lost:
                        orig_on_lease_lost()

                heartbeat.on_lease_lost = _handle_heartbeat_lease_lost

            # 7. Update project lifecycle to RENDERING
            try:
                self.project_service.update_project(
                    clean_ch,
                    project_id,
                    ProjectUpdate(status=ProjectStatus.RENDERING),
                )
            except Exception as e:
                self._logger.warning(f"Could not update project status to RENDERING: {e}")

            # 8. Define work function for CheckpointService 5-step protocol
            step_name = f"render_timeline_{project_id}"
            step_index = 1
            probe_summary: Dict[str, Any] = {}

            def _execute_render_step(temp_dest_path: Path) -> Dict[str, Any]:
                nonlocal probe_summary

                # Check lease state before invoking FFmpeg
                if heartbeat and heartbeat.is_lease_lost:
                    raise StaleExecutionTokenException(
                        task_id=task.id,
                        expected_token=task.execution_token,
                        actual_token=task.execution_token,
                        message="Heartbeat reported lease lost before FFmpeg execution.",
                    )

                # Render project timeline directly to scoped temp destination
                rendered_file = self.timeline_renderer.render_project_timeline(
                    channel_id=clean_ch,
                    project_id=project_id,
                    output_path=temp_dest_path,
                    scene_service=self.scene_service,
                    asset_service=self.asset_service,
                    channel_workspace_dir=channel_dir,
                    profile=profile,
                    voiceover_track_path=voiceover_track_path,
                    background_music_path=background_music_path,
                    voiceover_volume=voiceover_volume,
                    background_music_volume=background_music_volume,
                    enforce_true_peak=enforce_true_peak,
                    task_id=task.id,
                    execution_token=task.execution_token,
                    cancellation_token=cancellation_event,
                    timeout_sec=timeout_sec,
                    audio_asset_ids=payload_audio_assets,
                    mode=clean_mode,
                )

                # Collect probe and true-peak metrics
                probe_res = self.timeline_renderer.probe_service.probe_file(rendered_file)
                tp_res = self.timeline_renderer.probe_service.measure_true_peak(rendered_file)

                v_stream = probe_res.primary_video_stream
                a_stream = probe_res.primary_audio_stream

                probe_summary = {
                    "duration_sec": probe_res.duration_sec,
                    "width": v_stream.width if v_stream else profile.width,
                    "height": v_stream.height if v_stream else profile.height,
                    "fps": v_stream.fps if v_stream else profile.fps,
                    "video_codec": v_stream.codec_name if v_stream else profile.video_codec,
                    "audio_codec": a_stream.codec_name if a_stream else profile.audio_codec,
                    "true_peak_dbtp": tp_res.true_peak_dbtp,
                    "true_peak_compliant": tp_res.is_compliant,
                    "integrated_loudness_lufs": tp_res.integrated_loudness_lufs,
                }
                return probe_summary

            # 9. Execute guarded by 5-step Checkpoint protocol
            try:
                checkpoint = self.checkpoint_service.execute_step(
                    task=task,
                    step_name=step_name,
                    step_index=step_index,
                    work_fn=_execute_render_step,
                    target_rel_path=output_rel_path,
                    operation_type=FileOperationType.RENDER,
                    heartbeat=heartbeat,
                )
            except StaleExecutionTokenException:
                # Fencing violation: Worker must halt and NEVER touch DB or files
                self._logger.critical(
                    f"Fencing violation during render for task '{task.id}': Stale execution token."
                )
                raise
            except RenderCancelledException:
                self._logger.warning(f"Render cancelled for project '{project_id}' (task '{task.id}').")
                try:
                    self.project_service.update_project(
                        clean_ch,
                        project_id,
                        ProjectUpdate(status=ProjectStatus.CANCELLED),
                    )
                except Exception:
                    pass
                raise
            except Exception as e:
                self._logger.error(f"Render failed for project '{project_id}' (task '{task.id}'): {e}")
                try:
                    self.project_service.update_project(
                        clean_ch,
                        project_id,
                        ProjectUpdate(status=ProjectStatus.FAILED),
                    )
                except Exception:
                    pass
                raise

            # 10. Update project lifecycle to COMPLETED
            final_target_abs = safe_resolve_path(channel_dir, output_rel_path)
            try:
                self.project_service.update_project(
                    clean_ch,
                    project_id,
                    ProjectUpdate(status=ProjectStatus.COMPLETED),
                )
            except Exception as e:
                self._logger.warning(f"Could not update project status to COMPLETED: {e}")

            # 11. Build and return task result summary
            file_size = final_target_abs.stat().st_size if final_target_abs.exists() else 0
            file_hash = calculate_file_sha256(final_target_abs) if final_target_abs.exists() else ""

            if not probe_summary and final_target_abs.exists():
                try:
                    fast_probe = self.timeline_renderer.probe_service.probe_file(final_target_abs)
                    v_str = fast_probe.primary_video_stream
                    a_str = fast_probe.primary_audio_stream
                    probe_summary = {
                        "duration_sec": fast_probe.duration_sec,
                        "width": v_str.width if v_str else profile.width,
                        "height": v_str.height if v_str else profile.height,
                        "fps": v_str.fps if v_str else profile.fps,
                        "video_codec": v_str.codec_name if v_str else profile.video_codec,
                        "audio_codec": a_str.codec_name if a_str else profile.audio_codec,
                    }
                except Exception:
                    pass

            dur_disp = result_dur = probe_summary.get("duration_sec")
            dur_num = dur_disp if dur_disp is not None else 0.0

            result = {
                "status": "success",
                "project_id": project_id,
                "channel_id": clean_ch,
                "output_path": str(final_target_abs),
                "relative_path": str(output_rel_path),
                "file_size_bytes": file_size,
                "file_sha256": file_hash,
                "checkpoint_id": checkpoint.id if checkpoint else None,
                "duration_sec": result_dur,
                "resolution": f"{profile.width}x{profile.height}",
                "fps": profile.fps,
                "video_codec": probe_summary.get("video_codec"),
                "audio_codec": probe_summary.get("audio_codec"),
                "true_peak_dbtp": probe_summary.get("true_peak_dbtp"),
                "true_peak_compliant": probe_summary.get("true_peak_compliant", True),
                "aspect_ratio": profile.aspect_ratio,
            }

            self._logger.info(
                f"VideoRenderTaskHandler successfully completed render for project '{project_id}': "
                f"'{final_target_abs}' ({dur_num:.2f}s, {file_size} bytes)."
            )
            return result


def create_studio_worker_handler(
    render_handler: VideoRenderTaskHandler,
    fallback_handler: Optional[Callable[[TaskModel, HeartbeatDaemon], Dict[str, Any]]] = None,
) -> Callable[[TaskModel, HeartbeatDaemon], Dict[str, Any]]:
    """Create a composite worker handler that routes VIDEO_RENDERING tasks to VideoRenderTaskHandler.

    Args:
        render_handler: VideoRenderTaskHandler instance.
        fallback_handler: Optional handler for non-rendering task types.

    Returns:
        Callable handler compatible with WorkerPool.
    """
    def _composite_handler(task: TaskModel, heartbeat: HeartbeatDaemon) -> Dict[str, Any]:
        if task.task_type == TaskType.VIDEO_RENDERING:
            return render_handler.handle_task(task, heartbeat)
        elif fallback_handler:
            return fallback_handler(task, heartbeat)
        else:
            return {
                "task_id": task.id,
                "task_type": task.task_type.value,
                "status": "completed",
                "message": f"Default no-op handler executed for {task.task_type.value}.",
            }

    return _composite_handler
