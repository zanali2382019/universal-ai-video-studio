"""Batch production web routes for Studio WebUI (Task 3.15b)."""
from typing import Optional, List
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from src.core.logger import get_logger
from src.web.dependencies import ServiceContainer, get_service_container

router = APIRouter()
templates = Jinja2Templates(directory="src/web/templates")
logger = get_logger("studio.web.routes.batch")


@router.get("/batch", response_class=HTMLResponse)
async def batch_list_view(
    request: Request,
    channel_id: Optional[str] = None,
    services: ServiceContainer = Depends(get_service_container),
):
    channels = services.channel_service.list_channels(include_inactive=True)
    all_projects = []
    for ch in channels:
        try:
            projs = services.project_service.list_projects(ch.id)
            all_projects.extend(projs)
        except Exception:
            pass

    batches = services.batch_service.list_batches(channel_id=channel_id or None)

    error_msg = request.query_params.get("error")
    success_msg = request.query_params.get("success")

    return templates.TemplateResponse(
        request=request,
        name="batch.html",
        context={
            "channels": channels,
            "projects": all_projects,
            "batches": batches,
            "selected_channel": channel_id,
            "error_msg": error_msg,
            "success_msg": success_msg,
        },
    )


@router.post("/batch/create")
async def create_batch_job(
    request: Request,
    services: ServiceContainer = Depends(get_service_container),
):
    try:
        form = await request.form()
        channel_id = str(form.get("channel_id", "")).strip()
        name = str(form.get("name", "")).strip()
        mode = str(form.get("mode", "preview")).strip()
        aspect_ratio = str(form.get("aspect_ratio", "9:16")).strip()
        aspect_mode = str(form.get("aspect_mode", "cover")).strip()
        transition = str(form.get("transition", "fade")).strip()
        burn_subtitles = form.get("burn_subtitles") in ("true", "True", True, "on", "1")

        project_ids = form.getlist("project_ids")
        if not project_ids and "project_ids" in form:
            val = form.get("project_ids")
            if isinstance(val, str):
                project_ids = [val]

        render_requests = []
        for pid in project_ids:
            if not str(pid).strip():
                continue
            render_requests.append({
                "project_id": str(pid).strip(),
                "mode": mode,
                "profile": aspect_ratio,
                "aspect_ratio": aspect_ratio,
                "aspect_mode": aspect_mode,
                "transition": transition,
                "burn_subtitles": burn_subtitles,
            })

        if not render_requests:
            accept = request.headers.get("accept", "")
            if "application/json" in accept:
                return JSONResponse({"status": "error", "error": "Please select at least one project"}, status_code=400)
            return RedirectResponse(
                url=f"/batch?channel_id={channel_id}&error=Please+select+at+least+one+project",
                status_code=303,
            )

        batch_result = services.batch_service.create_batch(
            channel_id=channel_id,
            name=name,
            render_requests=render_requests,
        )

        accept = request.headers.get("accept", "")
        if "application/json" in accept:
            return JSONResponse(batch_result)

        return RedirectResponse(
            url=f"/batch/{batch_result['id']}?success=Batch+created+successfully",
            status_code=303,
        )
    except Exception as e:
        logger.error(f"Error creating batch job: {e}")
        accept = request.headers.get("accept", "")
        if "application/json" in accept:
            return JSONResponse({"status": "error", "error": str(e)}, status_code=400)
        err_enc = str(e).replace(" ", "+")
        return RedirectResponse(
            url=f"/batch?error={err_enc}",
            status_code=303,
        )


@router.get("/batch/{batch_id}", response_class=HTMLResponse)
async def batch_detail_view(
    batch_id: str,
    request: Request,
    services: ServiceContainer = Depends(get_service_container),
):
    try:
        batch = services.batch_service.get_batch_status(batch_id)
    except Exception as e:
        return RedirectResponse(url=f"/batch?error={str(e).replace(' ', '+')}", status_code=303)

    return templates.TemplateResponse(
        request=request,
        name="batch_detail.html",
        context={
            "batch": batch,
            "success_msg": request.query_params.get("success"),
            "error_msg": request.query_params.get("error"),
        },
    )
