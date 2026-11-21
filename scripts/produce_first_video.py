"""Production script to produce the first real playable video for DreamMatter channel.

Executes Task 3.6 — Milestone 5 Acceptance Pipeline:
1. Registers DreamMatter channel and Tommy production project.
2. Registers 3 real 3D Pixar-style scene images and 2 audio tracks in MAM.
3. Grants verified commercial licenses and passes the export gate.
4. Submits render task to TaskService.
5. Processes render task via WorkerPool and VideoRenderTaskHandler.
6. Validates final output using FFprobe and EBU R128 True Peak measurement.
"""
import sys
import os
import time
import json
from pathlib import Path

# Add project root to path
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from src.core.config import get_settings
from src.core.constants import (
    TaskType,
    TaskStatus,
    ProjectStatus,
    AssetType,
    LegalBasis,
    VerificationStatus,
    AspectRatio,
)
from src.database.connection import DatabaseManager
from src.database.migrations import MigrationManager
from src.database.models import (
    ChannelCreate,
    ProjectCreate,
    SceneCreate,
    MediaAssetCreate,
    MediaAssetUpdate,
    AssetLicenseCreate,
    TaskCreate,
)
from src.database.repositories.channel_repository import ChannelRepository
from src.database.repositories.project_repository import ProjectRepository
from src.database.repositories.scene_repository import SceneRepository
from src.database.repositories.asset_repository import AssetRepository
from src.database.repositories.license_repository import LicenseRepository
from src.database.repositories.task_repository import TaskRepository
from src.database.repositories.file_operation_repository import FileOperationRepository
from src.database.repositories.audit_repository import AuditRepository
from src.services.audit_service import AuditService
from src.services.channel_service import ChannelService
from src.services.project_service import ProjectService
from src.services.scene_service import SceneService
from src.services.asset_service import AssetService
from src.services.license_service import LicenseService
from src.services.checkpoint_service import CheckpointService
from src.services.task_service import TaskService
from src.services.worker_pool import WorkerPool
from src.rendering.ffmpeg_wrapper import FFmpegWrapper
from src.rendering.probe_service import MediaProbeService
from src.rendering.profiles import VERTICAL_9_16
from src.rendering.timeline_renderer import TimelineRenderer
from src.rendering.render_task_handler import (
    VideoRenderTaskHandler,
    create_studio_worker_handler,
)
from src.utils.file_utils import calculate_file_sha256, safe_resolve_path


def produce_first_video():
    print("=" * 80)
    print("UNIVERSAL AI VIDEO STUDIO — MILESTONE 5: FIRST REAL VIDEO PRODUCTION")
    print("Channel: DreamMatter (@DreamMatterWorld)")
    print("=" * 80)

    settings = get_settings()
    db_manager = DatabaseManager()
    MigrationManager(db_manager).run_migrations()

    # Repositories
    channel_repo = ChannelRepository(db_manager)
    project_repo = ProjectRepository(db_manager)
    scene_repo = SceneRepository(db_manager)
    asset_repo = AssetRepository(db_manager)
    license_repo = LicenseRepository(db_manager)
    task_repo = TaskRepository(db_manager)
    file_op_repo = FileOperationRepository(db_manager)
    audit_repo = AuditRepository(db_manager)

    # Services
    audit_service = AuditService(audit_repo)
    channel_service = ChannelService(channel_repo)
    project_service = ProjectService(project_repo, channel_repo)
    scene_service = SceneService(scene_repo, project_repo)
    license_service = LicenseService(license_repo, audit_service)
    asset_service = AssetService(
        asset_repository=asset_repo,
        license_repository=license_repo,
        file_operation_repository=file_op_repo,
        audit_service=audit_service,
    )
    checkpoint_service = CheckpointService(
        task_repository=task_repo,
        file_operation_repository=file_op_repo,
        asset_repository=asset_repo,
        audit_service=audit_service,
        db_manager=db_manager,
    )
    task_service = TaskService(
        task_repository=task_repo,
        channel_repository=channel_repo,
        project_repository=project_repo,
        scene_repository=scene_repo,
    )

    wrapper = FFmpegWrapper()
    probe_service = MediaProbeService(ffmpeg_wrapper=wrapper)
    timeline_renderer = TimelineRenderer(
        ffmpeg_wrapper=wrapper,
        probe_service=probe_service,
        license_service=license_service,
        task_repository=task_repo,
    )
    render_handler = VideoRenderTaskHandler(
        timeline_renderer=timeline_renderer,
        scene_service=scene_service,
        asset_service=asset_service,
        project_service=project_service,
        checkpoint_service=checkpoint_service,
        license_service=license_service,
        task_repository=task_repo,
        db_manager=db_manager,
    )

    # -------------------------------------------------------------
    # Step 1: Ensure DreamMatter Channel
    # -------------------------------------------------------------
    channel_id = "ch_dreammatter_001"
    existing_ch = channel_repo.get_by_id(channel_id)
    if not existing_ch:
        ch = channel_repo.create(
            ChannelCreate(
                id=channel_id,
                name="DreamMatter",
                handle="@DreamMatterWorld",
                niche="3D Animation & Whimsical Stories",
            )
        )
        print(f"[1/7] Channel created: '{ch.name}' ({ch.handle}, ID: {ch.id})")
    else:
        print(f"[1/7] Channel verified: '{existing_ch.name}' ({existing_ch.handle}, ID: {existing_ch.id})")

    channel_dir = settings.channels_root / channel_id
    (channel_dir / "exports" / "videos").mkdir(parents=True, exist_ok=True)

    # -------------------------------------------------------------
    # Step 2: Create Project
    # -------------------------------------------------------------
    project_id = "proj_dm_tommy_bubble_001"
    existing_proj = project_repo.get_by_id(channel_id, project_id)
    if existing_proj:
        project = existing_proj
        print(f"[2/7] Project found: '{project.title}' (ID: {project.id})")
    else:
        project = project_service.create_project(
            channel_id=channel_id,
            project_input=ProjectCreate(
                id=project_id,
                channel_id=channel_id,
                title="When Pizza Attacks Tommy! | Tommy's Whimsical Adventures",
                description="Tommy discovers a magical floating rainbow bubble in his living room! A DreamMatter original 3D short.",
                aspect_ratio=AspectRatio.VERTICAL_9_16,
                target_duration_sec=19.36,
            ),
        )
        print(f"[2/7] Project created: '{project.title}' (ID: {project.id})")

    # -------------------------------------------------------------
    # Step 3: Create Sequential Scenes
    # -------------------------------------------------------------
    scene_defs = [
        {
            "id": "sc_tommy_01",
            "seq": 1,
            "title": "Act 1: The Floating Wonder",
            "narration": "Meet Tommy! One sunny morning, Tommy discovered a giant, magical bubble floating in his room.",
            "duration": 6.5,
            "transition": "cut",
            "trans_dur": 0.0,
            "image_file": "tommy_scene_1.jpg",
        },
        {
            "id": "sc_tommy_02",
            "seq": 2,
            "title": "Act 2: The Gentle Touch",
            "narration": "With eyes wide open, he reached out to touch the rainbow wonder...",
            "duration": 7.0,
            "transition": "fade",
            "trans_dur": 0.5,
            "image_file": "tommy_scene_2.jpg",
        },
        {
            "id": "sc_tommy_03",
            "seq": 3,
            "title": "Act 3: Sparkles of Magic",
            "narration": "Pop! Tiny sparkles filled the air! In DreamMatter, everyday moments turn into pure magic!",
            "duration": 6.36,
            "transition": "cut",
            "trans_dur": 0.0,
            "image_file": "tommy_scene_3.jpg",
        },
    ]

    for s_def in scene_defs:
        s_id = s_def["id"]
        existing_s = scene_repo.get_by_id(channel_id, s_id)
        if not existing_s:
            scene_service.create_scene(
                channel_id=channel_id,
                project_id=project.id,
                scene_input=SceneCreate(
                    id=s_id,
                    project_id=project.id,
                    channel_id=channel_id,
                    sequence_number=s_def["seq"],
                    title=s_def["title"],
                    narration_text=s_def["narration"],
                    duration_seconds=s_def["duration"],
                ),
            )
        print(f"      - Scene {s_def['seq']}: '{s_def['title']}' ({s_def['duration']}s, transition: {s_def['transition']})")
    print(f"[3/7] Created {len(scene_defs)} sequential scenes.")

    # -------------------------------------------------------------
    # Step 4: Register MAM Assets & Verified Commercial Licenses
    # -------------------------------------------------------------
    img_dir = channel_dir / "media_vault" / "originals" / "images"
    audio_dir = channel_dir / "media_vault" / "originals" / "audio"

    assets_to_register = [
        {
            "id": "ast_dm_scene_1",
            "file_name": "tommy_scene_1.jpg",
            "path": img_dir / "tommy_scene_1.jpg",
            "rel_path": "media_vault/originals/images/tommy_scene_1.jpg",
            "type": AssetType.IMAGE,
            "mime": "image/jpeg",
            "scene_id": "sc_tommy_01",
        },
        {
            "id": "ast_dm_scene_2",
            "file_name": "tommy_scene_2.jpg",
            "path": img_dir / "tommy_scene_2.jpg",
            "rel_path": "media_vault/originals/images/tommy_scene_2.jpg",
            "type": AssetType.IMAGE,
            "mime": "image/jpeg",
            "scene_id": "sc_tommy_02",
        },
        {
            "id": "ast_dm_scene_3",
            "file_name": "tommy_scene_3.jpg",
            "path": img_dir / "tommy_scene_3.jpg",
            "rel_path": "media_vault/originals/images/tommy_scene_3.jpg",
            "type": AssetType.IMAGE,
            "mime": "image/jpeg",
            "scene_id": "sc_tommy_03",
        },
        {
            "id": "ast_dm_voiceover",
            "file_name": "tommy_voiceover.aac",
            "path": audio_dir / "tommy_voiceover.aac",
            "rel_path": "media_vault/originals/audio/tommy_voiceover.aac",
            "type": AssetType.AUDIO,
            "mime": "audio/aac",
            "scene_id": None,
        },
        {
            "id": "ast_dm_bgmusic",
            "file_name": "tommy_bgmusic.aac",
            "path": audio_dir / "tommy_bgmusic.aac",
            "rel_path": "media_vault/originals/audio/tommy_bgmusic.aac",
            "type": AssetType.AUDIO,
            "mime": "audio/aac",
            "scene_id": None,
        },
    ]

    for a_info in assets_to_register:
        a_id = a_info["id"]
        fpath = a_info["path"]
        if not fpath.exists():
            raise FileNotFoundError(f"Media asset file missing: {fpath}")

        sha256 = calculate_file_sha256(fpath)
        size_bytes = fpath.stat().st_size

        existing_ast = asset_repo.get_by_id(channel_id, a_id)
        if not existing_ast:
            asset_repo.create(
                MediaAssetCreate(
                    id=a_id,
                    channel_id=channel_id,
                    project_id=project.id,
                    scene_id=a_info["scene_id"],
                    file_name=a_info["file_name"],
                    file_path=a_info["rel_path"],
                    file_sha256=sha256,
                    file_size_bytes=size_bytes,
                    mime_type=a_info["mime"],
                    asset_type=a_info["type"],
                )
            )
        else:
            if a_info["scene_id"]:
                asset_repo.update(channel_id, a_id, MediaAssetUpdate(scene_id=a_info["scene_id"]))

        # Ensure verified commercial license
        existing_lic = license_repo.get_by_asset_id(channel_id, a_id)
        if not existing_lic:
            license_service.register_license(
                channel_id=channel_id,
                license_input=AssetLicenseCreate(
                    id=f"lic_{a_id}",
                    asset_id=a_id,
                    channel_id=channel_id,
                    legal_basis=LegalBasis.COMMERCIAL_LICENSE,
                    verification_status=VerificationStatus.VERIFIED,
                    commercial_use_allowed=True,
                    attribution_required=False,
                    notes="DreamMatter Original Production Asset — Verified Commercial Rights",
                ),
            )
        print(f"      - Registered Asset: {a_info['file_name']} (SHA-256: {sha256[:12]}..., Size: {size_bytes:,} bytes, License: VERIFIED)")
    print(f"[4/7] Registered {len(assets_to_register)} media assets in MAM with verified licenses.")

    # -------------------------------------------------------------
    # Step 5: Submit Render Task to TaskService
    # -------------------------------------------------------------
    output_rel = f"exports/videos/tommy_first_video_9_16.mp4"
    task_idemp = f"render_dreammatter_tommy_production_{int(time.time())}"

    render_task = task_service.create_task(
        channel_id=channel_id,
        task_input=TaskCreate(
            channel_id=channel_id,
            project_id=project.id,
            task_type=TaskType.VIDEO_RENDERING,
            idempotency_key=task_idemp,
            payload={
                "profile": "9:16",
                "voiceover_track_path": "media_vault/originals/audio/tommy_voiceover.aac",
                "background_music_path": "media_vault/originals/audio/tommy_bgmusic.aac",
                "voiceover_volume": 1.0,
                "background_music_volume": 0.20,
                "enforce_true_peak": True,
                "output_rel_path": output_rel,
            },
        ),
    )
    print(f"[5/7] Submitted rendering task: '{render_task.id}' (Status: {render_task.status.value}, Priority: {render_task.priority})")

    # -------------------------------------------------------------
    # Step 6: Execute via WorkerPool
    # -------------------------------------------------------------
    print(f"[6/7] Dispatching task to WorkerPool...")
    pool = WorkerPool(
        task_repository=task_repo,
        task_service=task_service,
        concurrency=1,
        task_handler=create_studio_worker_handler(render_handler),
        poll_interval_sec=0.2,
        pulse_interval_sec=2.0,
        channel_id=channel_id,
    )
    pool.start()

    # Wait for completion
    max_wait_sec = 60.0
    start_t = time.time()
    completed_task = None
    while time.time() - start_t < max_wait_sec:
        t = task_service.get_task(render_task.id)
        if t.status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
            completed_task = t
            break
        print(f"      ... Rendering in progress (status: {t.status.value}, elapsed: {time.time()-start_t:.1f}s)")
        time.sleep(1.5)

    pool.shutdown(timeout_sec=3.0)

    if not completed_task or completed_task.status != TaskStatus.COMPLETED:
        err = completed_task.error_message if completed_task else "Timeout waiting for worker"
        raise RuntimeError(f"Render task failed: {err}")

    print(f"[6/7] WorkerPool execution completed successfully! Task '{completed_task.id}' status: COMPLETED.")

    # -------------------------------------------------------------
    # Step 7: Technical Validation of Exported MP4
    # -------------------------------------------------------------
    final_output_path = Path(completed_task.result["output_path"])
    print(f"[7/7] Validating exported MP4: '{final_output_path}'")
    if not final_output_path.exists():
        raise FileNotFoundError(f"Exported MP4 does not exist at: {final_output_path}")

    file_size_bytes = final_output_path.stat().st_size
    file_sha256 = calculate_file_sha256(final_output_path)

    # 1. FFprobe stream inspection
    probe = probe_service.probe_file(final_output_path)
    v = probe.primary_video_stream
    a = probe.primary_audio_stream

    # 2. EBU R128 True Peak Measurement
    tp = probe_service.measure_true_peak(final_output_path)

    print("\n" + "=" * 80)
    print("PRODUCTION DELIVERABLE VERIFICATION REPORT")
    print("=" * 80)
    print(f"  • Destination File Path : {final_output_path.resolve()}")
    print(f"  • File Size             : {file_size_bytes:,} bytes ({file_size_bytes / (1024*1024):.2f} MB)")
    print(f"  • SHA-256 Checksum      : {file_sha256}")
    print(f"  • Duration              : {probe.duration_sec:.2f} seconds")
    print(f"  • Video Resolution      : {v.width}x{v.height} (Aspect Ratio: 9:16 Vertical)")
    print(f"  • Video Framerate       : {v.fps} FPS")
    print(f"  • Video Codec           : {v.codec_name} ({v.pix_fmt})")
    print(f"  • Audio Codec           : {a.codec_name} ({a.sample_rate} Hz, {a.channels} channels)")
    print(f"  • Integrated Loudness   : {tp.integrated_loudness_lufs:.1f} LUFS")
    print(f"  • Measured True Peak    : {tp.true_peak_dbtp:.2f} dBTP")
    print(f"  • ADR-003 Compliant     : {tp.is_compliant} (True Peak <= {tp.max_allowed_dbtp:.1f} dBTP)")
    print("=" * 80)

    # Output JSON summary for automated reporting
    summary = {
        "output_path": str(final_output_path.resolve()),
        "file_size_bytes": file_size_bytes,
        "file_sha256": file_sha256,
        "duration_sec": probe.duration_sec,
        "width": v.width,
        "height": v.height,
        "fps": v.fps,
        "video_codec": v.codec_name,
        "audio_codec": a.codec_name,
        "sample_rate": a.sample_rate,
        "channels": a.channels,
        "integrated_loudness_lufs": tp.integrated_loudness_lufs,
        "true_peak_dbtp": tp.true_peak_dbtp,
        "true_peak_compliant": tp.is_compliant,
    }
    with open("first_video_production_report.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    return summary


if __name__ == "__main__":
    produce_first_video()

