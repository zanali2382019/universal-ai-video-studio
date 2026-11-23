"""Video rendering service orchestrating Render Queue, WorkerPool, TimelineRenderer, and ADR-008 Gates."""
import os
import uuid
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any, Union

from src.core.constants import TaskType, TaskStatus, AspectRatio
from src.core.exceptions import (
    ValidationException,
    ProjectNotFoundException,
    ChannelNotFoundException,
    LicenseVerificationException,
)
from src.core.logger import get_logger
from src.database.connection import DatabaseManager
from src.database.models import TaskModel, TaskCreate
from src.database.repositories.task_repository import TaskRepository
from src.database.repositories.project_repository import ProjectRepository
from src.database.repositories.channel_repository import ChannelRepository
from src.database.repositories.scene_repository import SceneRepository
from src.database.repositories.asset_repository import AssetRepository
from src.database.repositories.license_repository import LicenseRepository
from src.database.repositories.audit_repository import AuditRepository
from src.database.repositories.file_operation_repository import FileOperationRepository
from src.database.repositories.flow_inbound_repository import FlowInboundRepository
from src.storage.service import StorageService
from src.services.task_service import TaskService
from src.services.project_service import ProjectService
from src.services.scene_service import SceneService
from src.services.asset_service import AssetService
from src.services.license_service import LicenseService
from src.services.audit_service import AuditService
from src.services.checkpoint_service import CheckpointService
from src.services.worker_pool import WorkerPool
from src.rendering.profiles import get_profile, VERTICAL_9_16, RenderProfile
from src.rendering.timeline_renderer import TimelineRenderer
from src.rendering.render_task_handler import VideoRenderTaskHandler, create_studio_worker_handler

logger = get_logger("studio.services.render")


class RenderService:
    """Manages render submission, license validation gates, execution via WorkerPool, and progress monitoring."""

    def __init__(
        self,
        db_manager: Optional[DatabaseManager] = None,
        storage_service: Optional[StorageService] = None,
        task_service: Optional[TaskService] = None,
        project_service: Optional[ProjectService] = None,
        scene_service: Optional[SceneService] = None,
        asset_service: Optional[AssetService] = None,
        license_service: Optional[LicenseService] = None,
        flow_inbound_repo: Optional[FlowInboundRepository] = None,
        timeline_renderer: Optional[TimelineRenderer] = None,
        worker_concurrency: int = 2,
        auto_start_workers: bool = True,
        service_logger: Optional[logging.Logger] = None,
    ) -> None:
        self.db = db_manager or DatabaseManager()
        self.storage = storage_service or StorageService()
        self._logger = service_logger or logger

        # Repositories
        self.task_repo = TaskRepository(self.db)
        self.channel_repo = ChannelRepository(self.db)
        self.project_repo = ProjectRepository(self.db)
        self.scene_repo = SceneRepository(self.db)
        self.asset_repo = AssetRepository(self.db)
        self.license_repo = LicenseRepository(self.db)
        self.audit_repo = AuditRepository(self.db)
        self.file_op_repo = FileOperationRepository(self.db)
        self.flow_inbound_repo = flow_inbound_repo or FlowInboundRepository(self.db)

        # Services
        self.audit_service = AuditService(self.audit_repo)
        self.license_service = license_service or LicenseService(self.license_repo, self.audit_service)
        self.task_service = task_service or TaskService(
            task_repository=self.task_repo,
            channel_repository=self.channel_repo,
            project_repository=self.project_repo,
            scene_repository=self.scene_repo,
        )
        self.project_service = project_service or ProjectService(self.project_repo, self.channel_repo)
        self.scene_service = scene_service or SceneService(self.scene_repo, self.project_repo)
        self.asset_service = asset_service or AssetService(
            asset_repository=self.asset_repo,
            license_repository=self.license_repo,
            file_operation_repository=self.file_op_repo,
            audit_service=self.audit_service,
            storage_service=self.storage,
        )
        self.checkpoint_service = CheckpointService(db_manager=self.db)

        # Rendering & Worker
        self.timeline_renderer = timeline_renderer or TimelineRenderer(
            license_service=self.license_service,
            task_repository=self.task_repo,
            storage_service=self.storage,
        )
        self.render_handler = VideoRenderTaskHandler(
            timeline_renderer=self.timeline_renderer,
            scene_service=self.scene_service,
            asset_service=self.asset_service,
            project_service=self.project_service,
            checkpoint_service=self.checkpoint_service,
            license_service=self.license_service,
            task_repository=self.task_repo,
            db_manager=self.db,
        )
        self._worker_handler = create_studio_worker_handler(self.render_handler)
        self._worker_pool = WorkerPool(
            task_repository=self.task_repo,
            task_service=self.task_service,
            concurrency=worker_concurrency,
            backend="thread",
            task_handler=self._worker_handler,
        )

        if auto_start_workers:
            self.start_worker_pool()

    def start_worker_pool(self) -> None:
        """Start the background worker pool if not running."""
        if not self._worker_pool.is_running:
            try:
                self._worker_pool.start()
                self._logger.info("RenderService WorkerPool background threads started.")
            except Exception as e:
                self._logger.warning(f"WorkerPool start notice: {e}")

    def stop_worker_pool(self, timeout_sec: float = 5.0) -> None:
        """Stop background worker pool gracefully."""
        if self._worker_pool.is_running:
            self._worker_pool.shutdown(timeout_sec=timeout_sec)
            self._logger.info("RenderService WorkerPool stopped.")

    def submit_render(
        self,
        channel_id: str,
        project_id: str,
        mode: str = "preview",  # "preview" or "export"
        profile: str = "vertical_9_16",
        aspect_mode: str = "cover",
        transition: str = "fade",
        transition_duration_sec: float = 0.5,
        burn_subtitles: bool = True,
        voiceover_track_path: Optional[str] = None,
        background_music_path: Optional[str] = None,
        voiceover_volume: float = 1.0,
        background_music_volume: float = 0.2,
        enforce_true_peak: bool = True,
        priority: int = 10,
        output_rel_path: Optional[str] = None,
    ) -> TaskModel:
        """Validate prerequisites, enforce ADR-008 export gate, and enqueue video rendering task.

        Args:
            channel_id: Owning channel ID.
            project_id: Target project ID.
            mode: "preview" (bypasses commercial license gate) or "export" (strict ADR-008 check).
            profile: Profile key (e.g. "9:16", "16:9", "1:1", "vertical_9_16").
            aspect_mode: "cover", "contain", "blur_pad", or "fit".
            transition: Transition name (e.g. "fade", "crossfade", "cut").
            burn_subtitles: Whether to burn subtitles.
            ...

        Returns:
            Created TaskModel in QUEUED status.
        """
        clean_ch = (channel_id or "").strip()
        clean_proj = (project_id or "").strip()

        # Validate channel & project exist
        channel = self.channel_repo.get_by_id(clean_ch)
        if not channel:
            raise ChannelNotFoundException(clean_ch)

        project = self.project_repo.get_by_id(clean_ch, clean_proj)
        if not project:
            raise ProjectNotFoundException(clean_proj, clean_ch)

        # Validate & resolve render profile
        norm_profile_str = profile
        if profile in ("9:16", "vertical"):
            norm_profile_str = "vertical_9_16"
        elif profile in ("16:9", "horizontal"):
            norm_profile_str = "horizontal_16_9"
        elif profile in ("1:1", "square"):
            norm_profile_str = "square_1_1"

        try:
            render_profile = get_profile(norm_profile_str)
        except Exception:
            render_profile = VERTICAL_9_16

        # Strict ADR-008 Gate Enforcement for "export" mode
        clean_mode = (mode or "preview").strip().lower()
        if clean_mode == "export":
            # Collect project assets
            asset_ids_to_check: List[str] = []
            scenes = self.scene_repo.list_by_project(clean_ch, clean_proj)
            for s in scenes:
                if getattr(s, "metadata_json", None) and isinstance(s.metadata_json, dict):
                    if "asset_id" in s.metadata_json:
                        asset_ids_to_check.append(s.metadata_json["asset_id"])

            all_assets = self.asset_repo.list_by_channel(clean_ch)
            for a in all_assets:
                if getattr(a, "project_id", None) == clean_proj:
                    if a.id not in asset_ids_to_check:
                        asset_ids_to_check.append(a.id)

            # Also check Flow inbound active takes
            try:
                flow_takes = self.flow_inbound_repo.list_takes_for_project(clean_proj)
                for t in flow_takes:
                    if t.get("is_active") and t.get("asset_id"):
                        if t["asset_id"] not in asset_ids_to_check:
                            asset_ids_to_check.append(t["asset_id"])
            except Exception:
                pass

            if not asset_ids_to_check:
                # If no assets found at all, check if scenes exist
                if not scenes:
                    raise ValidationException(f"Project '{clean_proj}' has no scenes or assets to render.")
                raise LicenseVerificationException(
                    asset_id="UNLINKED_SCENES",
                    reason=f"Project '{clean_proj}' has {len(scenes)} scene(s) but none have verified commercial assets attached.",
                )

            # Perform license gate check
            self.license_service.check_export_eligibility(clean_ch, asset_ids_to_check)

        # Format output filename
        if not output_rel_path:
            out_filename = f"final_{clean_mode}_{render_profile.aspect_ratio.replace(':', '_')}.mp4"
            output_rel_path = f"projects/{clean_proj}/{out_filename}"

        payload = {
            "project_id": clean_proj,
            "mode": clean_mode,
            "profile": render_profile.name,
            "aspect_ratio": render_profile.aspect_ratio,
            "aspect_mode": aspect_mode,
            "transition": transition,
            "transition_duration_sec": transition_duration_sec,
            "burn_subtitles": burn_subtitles,
            "voiceover_track_path": voiceover_track_path,
            "background_music_path": background_music_path,
            "voiceover_volume": voiceover_volume,
            "background_music_volume": background_music_volume,
            "enforce_true_peak": enforce_true_peak,
            "output_rel_path": output_rel_path,
        }

        idempotency_key = f"render_{clean_proj}_{clean_mode}_{uuid.uuid4().hex[:8]}"

        task_input = TaskCreate(
            channel_id=clean_ch,
            project_id=clean_proj,
            task_type=TaskType.VIDEO_RENDERING,
            idempotency_key=idempotency_key,
            status=TaskStatus.QUEUED,
            priority=priority,
            payload=payload,
        )

        task = self.task_service.create_task(channel_id=clean_ch, task_input=task_input)

        # Ensure worker pool is active
        self.start_worker_pool()

        return task

    def get_render_progress(self, task_id: str) -> Dict[str, Any]:
        """Query real-time status, phase, progress %, and metrics for a render task."""
        task = self.task_service.get_task(task_id)

        status_val = task.status.value
        payload = task.payload or {}
        result = task.result or {}

        # Determine phase
        phase = "unknown"
        if task.status == TaskStatus.QUEUED:
            phase = "queued"
        elif task.status == TaskStatus.PENDING:
            phase = "pending"
        elif task.status == TaskStatus.RUNNING:
            phase = task.current_step or "rendering"
        elif task.status == TaskStatus.COMPLETED:
            phase = "completed"
        elif task.status == TaskStatus.FAILED:
            phase = "failed"
        elif task.status == TaskStatus.CANCELLED:
            phase = "cancelled"

        # Determine progress percent
        progress_percent = None
        if task.status == TaskStatus.COMPLETED:
            progress_percent = 100.0
        elif task.status == TaskStatus.QUEUED or task.status == TaskStatus.PENDING:
            progress_percent = 0.0
        elif task.status == TaskStatus.RUNNING:
            if result.get("progress_percent") is not None:
                progress_percent = float(result["progress_percent"])
            elif task.total_steps > 0 and task.completed_steps is not None:
                progress_percent = round((task.completed_steps / task.total_steps) * 100.0, 1)
            else:
                progress_percent = None  # Indeterminate progress

        # Audio measurements: LUFS and True Peak ONLY if actually measured
        lufs = result.get("integrated_loudness_lufs")
        true_peak = result.get("true_peak_dbtp")

        # Cache hit info
        cache_hit = result.get("cache_hit", False)

        return {
            "task_id": task.id,
            "channel_id": task.channel_id,
            "project_id": task.project_id or payload.get("project_id"),
            "status": status_val,
            "phase": phase,
            "progress_percent": progress_percent,
            "is_indeterminate": progress_percent is None,
            "cache_hit": cache_hit,
            "lufs": lufs,
            "true_peak": true_peak,
            "error_message": task.error_message,
            "output_path": result.get("output_path"),
            "file_size_bytes": result.get("file_size_bytes"),
            "duration_sec": result.get("duration_sec"),
            "created_at": task.created_at,
            "claimed_at": task.claimed_at,
            "payload": payload,
            "result": result,
        }

    def cancel_render(self, task_id: str, reason: str = "Cancelled by user via WebUI") -> TaskModel:
        """Cancel render task with Windows-safe termination."""
        return self.task_service.cancel_task(task_id, reason=reason)

    def retry_render(self, task_id: str) -> TaskModel:
        """Retry a failed or cancelled render task by enqueuing a fresh task."""
        old_task = self.task_service.get_task(task_id)
        payload = dict(old_task.payload or {})
        channel_id = old_task.channel_id
        project_id = old_task.project_id or payload.get("project_id")

        new_idempotency_key = f"retry_{old_task.id}_{uuid.uuid4().hex[:6]}"
        task_input = TaskCreate(
            channel_id=channel_id,
            project_id=project_id,
            task_type=TaskType.VIDEO_RENDERING,
            idempotency_key=new_idempotency_key,
            status=TaskStatus.QUEUED,
            priority=old_task.priority,
            payload=payload,
        )
        new_task = self.task_service.create_task(channel_id=channel_id, task_input=task_input)
        self.start_worker_pool()
        return new_task

    def list_renders(
        self,
        channel_id: Optional[str] = None,
        status: Optional[TaskStatus] = None,
        project_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List render tasks."""
        tasks = self.task_repo.list_tasks(
            channel_id=channel_id,
            status=status,
            project_id=project_id,
        )
        render_tasks = [t for t in tasks if t.task_type == TaskType.VIDEO_RENDERING]
        return [self.get_render_progress(t.id) for t in render_tasks]
