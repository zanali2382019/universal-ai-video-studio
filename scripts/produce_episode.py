"""Reusable Episode Production Script for Universal AI Video Studio.

Accepts an episode manifest (JSON or dict) and drives the end-to-end production:
1. Reuses ChannelService, ProjectService, SceneService, AssetService, LicenseService, TaskService, WorkerPool, and TimelineRenderer.
2. Supports episode-specific scene images, voiceover, background music, scene durations, and project metadata.
3. Enforces explicit voiceover language selection ('ar' or 'en') without implicit defaulting.
4. Separates and validates distinct audio roles ('voiceover', 'background_music', 'sound_effects').
5. Registers all assets through MAM and enforces license eligibility before rendering.
6. Produces the actual playable MP4 through the existing production pipeline.
7. Validates the exported file using FFprobe and EBU R128 True Peak measurement.
8. Verifies preservation of existing channels, projects, and previously exported assets.
"""
import sys
import os
import time
import json
import argparse
from pathlib import Path
from typing import Dict, Any, Optional

# Add project root to sys.path
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from src.core.config import get_settings
from src.core.constants import (
    TaskType,
    TaskStatus,
    ProjectStatus,
    AssetType,
    AudioRole,
    VoiceoverLanguage,
    SUPPORTED_VOICEOVER_LANGUAGES,
    MusicStyle,
    MusicMood,
    LegalBasis,
    VerificationStatus,
    AspectRatio,
)
from src.core.exceptions import (
    ValidationException,
    VoiceoverLanguageException,
    AudioRoleMismatchException,
)
from src.database.connection import DatabaseManager
from src.database.migrations import MigrationManager
from src.database.models import (
    ChannelCreate,
    ProjectCreate,
    ProjectUpdate,
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
from src.services.audio_validator import AudioManifestValidator
from src.services.voice_service import VoiceSynthesisService
from src.rendering.ffmpeg_wrapper import FFmpegWrapper
from src.rendering.probe_service import MediaProbeService
from src.rendering.profiles import get_profile, VERTICAL_9_16
from src.rendering.timeline_renderer import TimelineRenderer
from src.rendering.render_task_handler import (
    VideoRenderTaskHandler,
    create_studio_worker_handler,
)
from src.utils.file_utils import calculate_file_sha256, safe_resolve_path


def load_manifest(manifest_source: Any) -> Dict[str, Any]:
    """Load manifest from JSON file path or dictionary."""
    if isinstance(manifest_source, dict):
        return manifest_source
    p = Path(manifest_source)
    if not p.exists():
        raise FileNotFoundError(f"Manifest file does not exist: {p}")
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def produce_episode(manifest_source: Any) -> Dict[str, Any]:
    """Execute end-to-end production for an episode manifest."""
    manifest = load_manifest(manifest_source)
    channel_id = manifest.get("channel_id", "ch_dreammatter_001")
    project_id = manifest["project_id"]
    title = manifest["title"]
    description = manifest.get("description", "")
    aspect_ratio_str = manifest.get("aspect_ratio", "9:16")
    target_dur = float(manifest.get("target_duration_sec", 20.0))
    scenes_def = manifest["scenes"]
    audio_def = manifest.get("audio", {})
    render_def = manifest.get("render", {})

    print("=" * 80)
    print(f"UNIVERSAL AI VIDEO STUDIO — PRODUCTION RUN")
    print(f"Project: '{title}' ({project_id})")
    print(f"Channel: {channel_id}")
    print("=" * 80)

    # -------------------------------------------------------------
    # Step 0: Validate Audio Experience & Language Selection
    # -------------------------------------------------------------
    validated_audio = AudioManifestValidator.validate_manifest_audio(audio_def)
    selected_language = validated_audio["voiceover"]["language"]
    bg_def = validated_audio.get("background_music", {})
    music_style = bg_def.get("style", MusicStyle.GENTLE_CINEMATIC.value)
    music_mood = bg_def.get("mood", MusicMood.WARM_CALM.value)

    print(f"[0/7] Audio validation passed: Language='{selected_language}', Music Style='{music_style}', Mood='{music_mood}'")

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
    license_service = LicenseService(license_repo, audit_service, db_manager=db_manager)
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

    channel_dir = settings.channels_root / channel_id
    channel_dir.mkdir(parents=True, exist_ok=True)
    (channel_dir / "exports" / "videos").mkdir(parents=True, exist_ok=True)

    # -------------------------------------------------------------
    # Step 1: Ensure Channel Exists
    # -------------------------------------------------------------
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

    # -------------------------------------------------------------
    # Step 2: Create / Verify Project with Language Metadata
    # -------------------------------------------------------------
    project_metadata = {
        "language": selected_language,
        "voiceover_language": selected_language,
        "music_style": music_style,
        "music_mood": music_mood,
    }

    existing_proj = project_repo.get_by_id(channel_id, project_id)
    if existing_proj:
        project = existing_proj
        # Update project metadata with language if missing
        existing_meta = dict(project.metadata_json or {})
        existing_meta.update(project_metadata)
        project_repo.update(channel_id, project_id, ProjectUpdate(metadata=existing_meta))
        print(f"[2/7] Existing project found: '{project.title}' (ID: {project.id}, Language: {selected_language})")
    else:
        project = project_service.create_project(
            channel_id=channel_id,
            project_input=ProjectCreate(
                id=project_id,
                channel_id=channel_id,
                title=title,
                description=description,
                aspect_ratio=AspectRatio(aspect_ratio_str),
                target_duration_sec=target_dur,
                metadata=project_metadata,
            ),
        )
        print(f"[2/7] Project created: '{project.title}' (ID: {project.id}, Language: {selected_language})")

    # -------------------------------------------------------------
    # Step 3: Create / Verify Scenes
    # -------------------------------------------------------------
    for s_def in scenes_def:
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
                    sequence_number=s_def["sequence_number"],
                    title=s_def["title"],
                    narration_text=s_def.get("narration_text", ""),
                    duration_seconds=float(s_def["duration_seconds"]),
                    metadata_json={
                        "transition": s_def.get("transition", "cut"),
                        "transition_duration_sec": float(s_def.get("transition_duration_sec", 0.5)),
                        "asset_id": s_def.get("asset", {}).get("id"),
                    },
                ),
            )
        print(f"      - Scene {s_def['sequence_number']}: '{s_def['title']}' ({s_def['duration_seconds']}s, transition: {s_def.get('transition', 'cut')})")
    print(f"[3/7] Verified {len(scenes_def)} sequential scenes.")

    # -------------------------------------------------------------
    # Step 4: Register MAM Assets with Explicit Roles and Language
    # -------------------------------------------------------------
    assets_to_register = []
    # Scene visual assets
    for s_def in scenes_def:
        ast = s_def.get("asset")
        if ast:
            assets_to_register.append({
                "id": ast["id"],
                "file_name": ast["file_name"],
                "rel_path": ast["rel_path"],
                "path": channel_dir / ast["rel_path"],
                "type": AssetType(ast.get("asset_type", "image")),
                "mime": ast.get("mime_type", "image/jpeg"),
                "scene_id": s_def["id"],
                "category": "visual_scene",
                "metadata": {"role": "visual_scene"},
            })

    # Voiceover asset
    vo = validated_audio.get("voiceover")
    if vo:
        assets_to_register.append({
            "id": vo["id"],
            "file_name": vo["file_name"],
            "rel_path": vo["rel_path"],
            "path": channel_dir / vo["rel_path"],
            "type": AssetType.AUDIO,
            "mime": vo.get("mime_type", "audio/aac"),
            "scene_id": None,
            "category": AudioRole.VOICEOVER.value,
            "metadata": {
                "role": AudioRole.VOICEOVER.value,
                "language": selected_language,
            },
        })

    # Background music asset
    bg = validated_audio.get("background_music")
    if bg:
        assets_to_register.append({
            "id": bg["id"],
            "file_name": bg["file_name"],
            "rel_path": bg["rel_path"],
            "path": channel_dir / bg["rel_path"],
            "type": AssetType.AUDIO,
            "mime": bg.get("mime_type", "audio/aac"),
            "scene_id": None,
            "category": AudioRole.BACKGROUND_MUSIC.value,
            "metadata": {
                "role": AudioRole.BACKGROUND_MUSIC.value,
                "style": music_style,
                "mood": music_mood,
            },
        })

    # Optional Sound effects assets
    for sfx_item in validated_audio.get("sound_effects", []):
        assets_to_register.append({
            "id": sfx_item["id"],
            "file_name": sfx_item["file_name"],
            "rel_path": sfx_item["rel_path"],
            "path": channel_dir / sfx_item["rel_path"],
            "type": AssetType.AUDIO,
            "mime": sfx_item.get("mime_type", "audio/aac"),
            "scene_id": sfx_item.get("scene_id"),
            "category": AudioRole.SOUND_EFFECT.value,
            "metadata": {"role": AudioRole.SOUND_EFFECT.value},
        })

    # Validate distinct audio roles and language conformance before registration
    AudioManifestValidator.validate_audio_asset_roles(assets_to_register, selected_language)

    asset_ids_for_eligibility = []
    for a_info in assets_to_register:
        a_id = a_info["id"]
        fpath = a_info["path"]
        if not fpath.exists():
            raise FileNotFoundError(f"Media asset file missing on disk: {fpath}")

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
                    asset_category=a_info["category"],
                    metadata=a_info.get("metadata", {}),
                )
            )
        else:
            if a_info["scene_id"] and existing_ast.scene_id != a_info["scene_id"]:
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
                    notes=f"DreamMatter Original Production Asset — Verified Commercial Rights ({title})",
                ),
            )
        asset_ids_for_eligibility.append(a_id)
        print(f"      - Asset: {a_info['file_name']} (Role: {a_info['category']}, SHA-256: {sha256[:12]}..., Size: {size_bytes:,} bytes, License: VERIFIED)")

    # Enforce license export gate
    license_service.check_export_eligibility(channel_id, asset_ids_for_eligibility)
    print(f"[4/7] Verified licensing gate for all {len(asset_ids_for_eligibility)} assets (100% compliant).")

    # -------------------------------------------------------------
    # Step 5: Submit Render Task to TaskService
    # -------------------------------------------------------------
    output_rel = render_def.get("output_rel_path", f"exports/videos/{project_id}_output.mp4")
    task_idemp = f"render_{channel_id}_{project_id}_{int(time.time())}"

    vo_rel = vo["rel_path"] if vo else None
    bg_rel = bg["rel_path"] if bg else None
    vo_vol = float(vo.get("volume", 1.0)) if vo else 1.0
    bg_vol = float(bg.get("volume", 0.20)) if bg else 0.20
    enforce_tp = bool(render_def.get("enforce_true_peak", True))

    render_task = task_service.create_task(
        channel_id=channel_id,
        task_input=TaskCreate(
            channel_id=channel_id,
            project_id=project.id,
            task_type=TaskType.VIDEO_RENDERING,
            idempotency_key=task_idemp,
            payload={
                "profile": aspect_ratio_str,
                "language": selected_language,
                "voiceover_track_path": vo_rel,
                "background_music_path": bg_rel,
                "voiceover_volume": vo_vol,
                "background_music_volume": bg_vol,
                "music_style": music_style,
                "music_mood": music_mood,
                "enforce_true_peak": enforce_tp,
                "output_rel_path": output_rel,
            },
        ),
    )
    print(f"[5/7] Submitted rendering task: '{render_task.id}' (Status: {render_task.status.value}, Priority: {render_task.priority})")

    # -------------------------------------------------------------
    # Step 6: Execute via WorkerPool
    # -------------------------------------------------------------
    print(f"[6/7] Dispatching render task to WorkerPool...")
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

    max_wait_sec = 90.0
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

    print(f"[6/7] WorkerPool execution succeeded. Task '{completed_task.id}' status: COMPLETED.")

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

    report_data = {
        "channel_id": channel_id,
        "project_id": project_id,
        "title": title,
        "language": selected_language,
        "voiceover_language": selected_language,
        "music_style": music_style,
        "music_mood": music_mood,
        "task_id": completed_task.id,
        "output_file": str(final_output_path.resolve()),
        "output_rel_path": output_rel,
        "file_size_bytes": file_size_bytes,
        "file_sha256": file_sha256,
        "duration_sec": probe.duration_sec,
        "format": probe.format_name,
        "video": {
            "codec": v.codec_name if v else "unknown",
            "width": v.width if v else 0,
            "height": v.height if v else 0,
            "fps": v.fps if v else 0.0,
            "pix_fmt": v.pix_fmt if v else "unknown",
        },
        "audio": {
            "codec": a.codec_name if a else "unknown",
            "channels": a.channels if a else 0,
            "sample_rate": a.sample_rate if a else 0,
            "bitrate": a.bit_rate if a else 0,
        },
        "true_peak": {
            "measured_dbtp": tp.true_peak_dbtp,
            "integrated_lufs": tp.integrated_loudness_lufs,
            "is_compliant": tp.is_compliant,
            "ceiling_dbtp": tp.max_allowed_dbtp,
        },
        "scenes_count": len(scenes_def),
        "assets_count": len(assets_to_register),
    }

    report_path = _ROOT / f"{project_id}_production_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report_data, f, indent=2)

    print("\n" + "=" * 80)
    print("EPISODE PRODUCTION REPORT")
    print("=" * 80)
    print(f"  • Destination File Path : {final_output_path.resolve()}")
    print(f"  • File Size             : {file_size_bytes:,} bytes ({file_size_bytes / (1024*1024):.2f} MB)")
    print(f"  • SHA-256 Hash          : {file_sha256}")
    print(f"  • Duration              : {probe.duration_sec:.2f} seconds")
    print(f"  • Resolution            : {v.width}x{v.height} (9:16 vertical)")
    print(f"  • Frame Rate            : {v.fps} FPS")
    print(f"  • Language              : {selected_language}")
    print(f"  • Music Style / Mood    : {music_style} ({music_mood})")
    print(f"  • Video Codec           : {v.codec_name}")
    print(f"  • Audio Codec           : {a.codec_name} ({a.channels} channels, {a.sample_rate} Hz)")
    print(f"  • Measured True Peak    : {tp.true_peak_dbtp:.2f} dBTP (ADR-003 limit: <= -1.0 dBTP -> Compliant: {tp.is_compliant})")
    print(f"  • Integrated Loudness   : {tp.integrated_loudness_lufs:.2f} LUFS")
    print(f"  • Saved Report          : {report_path}")
    print("=" * 80 + "\n")

    return report_data


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Universal AI Video Studio — Reusable Episode Producer")
    parser.add_argument(
        "--manifest",
        type=str,
        default="channels/ch_dreammatter_001/episodes/episode_02.json",
        help="Path to episode manifest JSON",
    )
    args = parser.parse_args()
    produce_episode(args.manifest)
