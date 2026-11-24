"""Render Queue web routes for Studio WebUI (Task 3.15b)."""
from typing import Optional
from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from src.core.constants import TaskStatus
from src.core.exceptions import LicenseVerificationException, ValidationException
from src.core.logger import get_logger
from src.database.models import AssetType
from src.web.dependencies import ServiceContainer, get_service_container

router = APIRouter()
templates = Jinja2Templates(directory="src/web/templates")
logger = get_logger("studio.web.routes.render_queue")


@router.get("/render-queue", response_class=HTMLResponse)
async def render_queue_view(
    request: Request,
    channel_id: Optional[str] = None,
    status: Optional[str] = None,
    services: ServiceContainer = Depends(get_service_container),
):
    channels = services.channel_service.list_channels(include_inactive=True)

    # Collect all available projects
    all_projects = []
    for ch in channels:
        try:
            projs = services.project_service.list_projects(ch.id)
            all_projects.extend(projs)
        except Exception:
            pass

    # Status filter
    status_filter = None
    if status:
        try:
            status_filter = TaskStatus(status)
        except Exception:
            status_filter = None

    renders = services.render_service.list_renders(
        channel_id=channel_id or None,
        status=status_filter,
    )

    error_msg = request.query_params.get("error")
    success_msg = request.query_params.get("success")

    # Collect available audio assets across channels
    audio_assets = []
    try:
        if channel_id:
            audio_assets = services.asset_service.list_assets(channel_id=channel_id, asset_type=AssetType.AUDIO)
        else:
            for ch in channels:
                audio_assets.extend(services.asset_service.list_assets(channel_id=ch.id, asset_type=AssetType.AUDIO))
    except Exception as e:
        logger.warning(f"Could not load audio assets for render queue: {e}")

    return templates.TemplateResponse(
        request=request,
        name="render_queue.html",
        context={
            "channels": channels,
            "projects": all_projects,
            "renders": renders,
            "selected_channel": channel_id,
            "selected_status": status,
            "error_msg": error_msg,
            "success_msg": success_msg,
            "audio_assets": audio_assets,
        },
    )


@router.post("/render-queue/dispatch")
async def dispatch_render(
    request: Request,
    channel_id: str = Form(...),
    project_id: str = Form(...),
    mode: str = Form("preview"),
    aspect_ratio: str = Form("9:16"),
    aspect_mode: str = Form("cover"),
    transition: str = Form("fade"),
    transition_duration_sec: float = Form(0.5),
    burn_subtitles: bool = Form(True),
    voiceover_track_mode: str = Form("auto"),
    background_music_mode: str = Form("whimsical"),
    voiceover_volume: float = Form(1.0),
    background_music_volume: float = Form(0.2),
    services: ServiceContainer = Depends(get_service_container),
):
    try:
        clean_ch = channel_id.strip()
        clean_proj = project_id.strip()

        # 1. Resolve Voiceover Track
        resolved_vo_path = None
        if voiceover_track_mode == "auto":
            prj = services.project_service.get_project(clean_ch, clean_proj)
            if prj and prj.metadata_json and prj.metadata_json.get("voiceover_track_path"):
                resolved_vo_path = prj.metadata_json["voiceover_track_path"]
            else:
                # Search MAM for project voiceover audio asset
                ch_assets = services.asset_service.list_assets(clean_ch, asset_type=AssetType.AUDIO)
                for a in ch_assets:
                    if getattr(a, "project_id", None) == clean_proj and ("voice" in a.file_name.lower() or a.asset_category == "voiceover"):
                        resolved_vo_path = a.file_path
                        break
        elif voiceover_track_mode == "none":
            resolved_vo_path = None
        else:
            resolved_vo_path = voiceover_track_mode.strip()

        # 2. Resolve Background Music Track
        resolved_bgm_path = None
        if background_music_mode == "none":
            resolved_bgm_path = None
        elif background_music_mode in ("whimsical", "calm"):
            ch_assets = services.asset_service.list_assets(clean_ch, asset_type=AssetType.AUDIO)
            for a in ch_assets:
                fn = a.file_name.lower()
                if "bgmusic" in fn or "music" in fn:
                    resolved_bgm_path = a.file_path
                    break
            if not resolved_bgm_path:
                from src.core.config import get_settings
                settings = get_settings()
                cand = settings.channels_root / clean_ch / "media_vault" / "originals" / "audio" / "tommy_bgmusic.aac"
                if cand.exists():
                    resolved_bgm_path = str(cand)
        else:
            resolved_bgm_path = background_music_mode.strip()

        task = services.render_service.submit_render(
            channel_id=clean_ch,
            project_id=clean_proj,
            mode=mode,
            profile=aspect_ratio,
            aspect_mode=aspect_mode,
            transition=transition,
            transition_duration_sec=transition_duration_sec,
            burn_subtitles=burn_subtitles,
            voiceover_track_path=resolved_vo_path,
            background_music_path=resolved_bgm_path,
            voiceover_volume=max(0.0, min(2.0, float(voiceover_volume))),
            background_music_volume=max(0.0, min(2.0, float(background_music_volume))),
        )

        accept = request.headers.get("accept", "")
        if "application/json" in accept:
            return JSONResponse({
                "status": "success",
                "task_id": task.id,
                "project_id": project_id,
                "mode": mode,
            })

        return RedirectResponse(
            url=f"/render-queue?channel_id={channel_id}&success=Render+task+{task.id}+enqueued",
            status_code=303,
        )
    except LicenseVerificationException as e:
        logger.warning(f"Render dispatch rejected by ADR-008 license gate: {e}")
        accept = request.headers.get("accept", "")
        if "application/json" in accept:
            return JSONResponse(
                {"status": "error", "error": f"ADR-008 License Gate Rejection: {e.message}", "details": e.details},
                status_code=400,
            )
        err_enc = f"License Gate Rejection: {e.message}".replace(" ", "+")
        return RedirectResponse(
            url=f"/render-queue?channel_id={channel_id}&error={err_enc}",
            status_code=303,
        )
    except Exception as e:
        logger.error(f"Render dispatch error: {e}")
        accept = request.headers.get("accept", "")
        if "application/json" in accept:
            return JSONResponse({"status": "error", "error": str(e)}, status_code=400)
        err_enc = str(e).replace(" ", "+")
        return RedirectResponse(
            url=f"/render-queue?channel_id={channel_id}&error={err_enc}",
            status_code=303,
        )


@router.post("/render-queue/{task_id}/retry")
async def retry_render_task(
    task_id: str,
    services: ServiceContainer = Depends(get_service_container),
):
    try:
        new_task = services.render_service.retry_render(task_id)
        return RedirectResponse(
            url=f"/render-queue?success=Retried+as+task+{new_task.id}",
            status_code=303,
        )
    except Exception as e:
        logger.error(f"Failed to retry task '{task_id}': {e}")
        err_enc = str(e).replace(" ", "+")
        return RedirectResponse(
            url=f"/render-queue?error={err_enc}",
            status_code=303,
        )


@router.post("/render-queue/{task_id}/cancel")
async def cancel_render_task(
    task_id: str,
    services: ServiceContainer = Depends(get_service_container),
):
    try:
        services.render_service.cancel_render(task_id)
        return RedirectResponse(
            url=f"/render-queue?success=Task+{task_id}+cancelled",
            status_code=303,
        )
    except Exception as e:
        logger.error(f"Failed to cancel task '{task_id}': {e}")
        err_enc = str(e).replace(" ", "+")
        return RedirectResponse(
            url=f"/render-queue?error={err_enc}",
            status_code=303,
        )
