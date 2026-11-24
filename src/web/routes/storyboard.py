"""Storyboard routes for Studio WebUI."""

import re
import uuid
import urllib.parse
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from src.core.logger import get_logger
from src.database.models import AssetType, LegalBasis, ProjectUpdate
from src.web.dependencies import ServiceContainer, get_service_container

router = APIRouter()
templates = Jinja2Templates(directory="src/web/templates")
logger = get_logger("studio.web.routes.storyboard")


@router.get("/storyboard/{channel_id}/{project_id}", response_class=HTMLResponse)
async def view_storyboard(
    channel_id: str,
    project_id: str,
    request: Request,
    services: ServiceContainer = Depends(get_service_container),
):
    project = services.project_service.get_project(channel_id, project_id)
    scenes = services.scene_service.list_scenes(channel_id, project_id)
    flow_status = services.flow_inbound_service.get_project_flow_status(
        channel_id, project_id
    )

    active_takes = {}
    for sc in flow_status.get("scenes", []):
        if sc.get("has_video"):
            active_takes[sc["scene_id"]] = sc.get("active_take_id") or "active"

    # Fetch available characters from CharacterBibleService
    characters = []
    try:
        characters = services.character_bible_service.list_characters(channel_id)
    except Exception as e:
        logger.warning(f"Failed to fetch characters for storyboard: {e}")

    error_msg = request.query_params.get("error")
    success_msg = request.query_params.get("success")

    # Check for existing project voiceover asset
    voiceover_asset = None
    if project and project.metadata_json and project.metadata_json.get("voiceover_asset_id"):
        try:
            voiceover_asset = services.asset_service.get_asset(channel_id, project.metadata_json["voiceover_asset_id"])
        except Exception:
            pass

    narrated_scenes_count = sum(1 for s in scenes if (getattr(s, "narration_text", None) or "").strip())

    return templates.TemplateResponse(
        request=request,
        name="storyboard.html",
        context={
            "project": project,
            "scenes": scenes,
            "active_takes": active_takes,
            "characters": characters,
            "error_msg": error_msg,
            "success_msg": success_msg,
            "voiceover_asset": voiceover_asset,
            "narrated_scenes_count": narrated_scenes_count,
        },
    )


@router.post("/storyboard/{channel_id}/{project_id}/scenes/create", response_class=RedirectResponse)
async def create_scene(
    channel_id: str,
    project_id: str,
    scene_id: Optional[str] = Form(None),
    title: Optional[str] = Form(None),
    duration_sec: float = Form(6.0),
    visual_prompt: str = Form(""),
    narration_text: str = Form(""),
    character_id: Optional[str] = Form(None),
    outfit_id: Optional[str] = Form(None),
    services: ServiceContainer = Depends(get_service_container),
):
    """Create a new scene within the project via Service Layer."""
    clean_ch = channel_id.strip()
    clean_prj = project_id.strip()

    existing = services.scene_service.list_scenes(clean_ch, clean_prj)
    next_seq = len(existing) + 1

    # Clean scene ID or auto-generate sequential identifier
    if scene_id and scene_id.strip():
        clean_sc_id = re.sub(r"[^a-zA-Z0-9_\-]", "_", scene_id.strip()).strip("_")
        if not clean_sc_id:
            short_proj = re.sub(r"[^a-zA-Z0-9]+", "", clean_prj.replace("proj_", "").replace("prj_", ""))[:8] or "scene"
            clean_sc_id = f"sc_{short_proj}_{next_seq:02d}"
    else:
        short_proj = re.sub(r"[^a-zA-Z0-9]+", "", clean_prj.replace("proj_", "").replace("prj_", ""))[:8] or "scene"
        clean_sc_id = f"sc_{short_proj}_{next_seq:02d}"

    # Prevent collision with any existing ID
    existing_ids = {getattr(s, "id", None) or getattr(s, "scene_id", "") for s in existing}
    if clean_sc_id in existing_ids:
        clean_sc_id = f"{clean_sc_id}_{uuid.uuid4().hex[:4]}"

    # Optional metadata
    meta = {}
    if character_id and character_id.strip():
        meta["character_id"] = character_id.strip()
    if outfit_id and outfit_id.strip():
        meta["outfit_id"] = outfit_id.strip()

    services.scene_service.create_scene(
        channel_id=clean_ch,
        project_id=clean_prj,
        scene_input={
            "id": clean_sc_id,
            "project_id": clean_prj,
            "channel_id": clean_ch,
            "sequence_number": next_seq,
            "title": (title or f"Scene {next_seq}").strip(),
            "narration_text": narration_text.strip(),
            "visual_prompt": visual_prompt.strip(),
            "duration_seconds": max(0.5, float(duration_sec)),
            "metadata": meta,
        },
    )

    logger.info(f"Created scene '{clean_sc_id}' (seq={next_seq}) for project '{clean_prj}'.")
    return RedirectResponse(
        url=f"/storyboard/{clean_ch}/{clean_prj}", status_code=303
    )


@router.post("/storyboard/{channel_id}/{project_id}/scenes/{scene_id}/delete", response_class=RedirectResponse)
async def delete_scene(
    channel_id: str,
    project_id: str,
    scene_id: str,
    services: ServiceContainer = Depends(get_service_container),
):
    """Safely delete a scene via Service Layer."""
    clean_ch = channel_id.strip()
    clean_prj = project_id.strip()
    clean_sc = scene_id.strip()

    try:
        services.scene_service.delete_scene(clean_ch, clean_sc)
        logger.info(f"Deleted scene '{clean_sc}' from project '{clean_prj}'.")
    except Exception as e:
        logger.warning(f"Could not delete scene '{clean_sc}': {e}")

    return RedirectResponse(
        url=f"/storyboard/{clean_ch}/{clean_prj}", status_code=303
    )


@router.post("/storyboard/{channel_id}/{project_id}/reorder", response_class=RedirectResponse)
async def reorder_storyboard(
    channel_id: str,
    project_id: str,
    scene_id: str = Form(...),
    direction: str = Form(...),
    services: ServiceContainer = Depends(get_service_container),
):
    scenes = services.scene_service.list_scenes(channel_id, project_id)
    scene_ids = [getattr(s, "id", None) or getattr(s, "scene_id", "") for s in scenes]

    if scene_id in scene_ids:
        idx = scene_ids.index(scene_id)
        if direction == "up" and idx > 0:
            scene_ids[idx - 1], scene_ids[idx] = scene_ids[idx], scene_ids[idx - 1]
        elif direction == "down" and idx < len(scene_ids) - 1:
            scene_ids[idx + 1], scene_ids[idx] = scene_ids[idx], scene_ids[idx + 1]

        services.scene_service.reorder_scenes(channel_id, project_id, scene_ids)

    return RedirectResponse(
        url=f"/storyboard/{channel_id}/{project_id}", status_code=303
    )


@router.post("/storyboard/{channel_id}/{project_id}/generate-voiceover", response_class=RedirectResponse)
async def generate_project_voiceover(
    channel_id: str,
    project_id: str,
    voice_name: str = Form("ar-SA-HamedNeural"),
    language: str = Form("ar"),
    speed_rate: int = Form(0),
    services: ServiceContainer = Depends(get_service_container),
):
    """Aggregate scene narrations and generate project voiceover via VoiceSynthesisService."""
    clean_ch = channel_id.strip()
    clean_prj = project_id.strip()

    try:
        scenes = services.scene_service.list_scenes(clean_ch, clean_prj)
        if not scenes:
            return RedirectResponse(
                url=f"/storyboard/{clean_ch}/{clean_prj}?error=Project+has+no+scenes+to+synthesize",
                status_code=303,
            )

        synth_res = services.voice_service.synthesize_project_voiceover(
            channel_id=clean_ch,
            project_id=clean_prj,
            scenes=scenes,
            language=language,
            voice_name=voice_name,
            rate=speed_rate,
        )

        audio_file_path = Path(synth_res["output_path"])

        # Ingest into MAM via AssetService
        media_asset = services.asset_service.ingest_asset(
            channel_id=clean_ch,
            source_path=audio_file_path,
            asset_type=AssetType.AUDIO,
            asset_category="voiceover",
            project_id=clean_prj,
            legal_basis=LegalBasis.PROPRIETARY_OWNERSHIP,
            proof_file_path="AI Voiceover Generated via Studio WebUI",
            actor="studio_webui",
        )

        # Verify license under ADR-008
        if media_asset.license_reference:
            try:
                services.license_service.verify_license(
                    channel_id=clean_ch,
                    license_id=media_asset.license_reference,
                    legal_basis=LegalBasis.PROPRIETARY_OWNERSHIP,
                    verified_by="studio_webui",
                    commercial_use_allowed=True,
                    notes=f"AI Voiceover synthesized with voice '{voice_name}' and approved for timeline",
                )
            except Exception as lic_err:
                logger.warning(f"Auto-license verification notice: {lic_err}")

        # Update project metadata to attach this voiceover
        curr_project = services.project_service.get_project(clean_ch, clean_prj)
        meta = dict(curr_project.metadata_json or {})
        meta["voiceover_asset_id"] = media_asset.id
        meta["voiceover_track_path"] = media_asset.file_path
        meta["voiceover_language"] = language
        meta["voiceover_voice"] = voice_name

        services.project_service.update_project(
            clean_ch,
            clean_prj,
            ProjectUpdate(metadata=meta),
        )

        logger.info(f"Synthesized voiceover asset '{media_asset.id}' for project '{clean_prj}'.")
        return RedirectResponse(
            url=f"/storyboard/{clean_ch}/{clean_prj}?success=AI+Voiceover+generated+and+linked+successfully",
            status_code=303,
        )
    except Exception as e:
        logger.error(f"Voiceover generation failed for project '{clean_prj}': {e}", exc_info=True)
        err_msg = urllib.parse.quote_plus(f"Voiceover generation failed: {str(e)}")
        return RedirectResponse(
            url=f"/storyboard/{clean_ch}/{clean_prj}?error={err_msg}",
            status_code=303,
        )
