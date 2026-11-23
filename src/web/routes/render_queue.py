"""Render Queue web routes for Studio WebUI (Task 3.15b)."""
from typing import Optional
from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from src.core.constants import TaskStatus
from src.core.exceptions import LicenseVerificationException, ValidationException
from src.core.logger import get_logger
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
    services: ServiceContainer = Depends(get_service_container),
):
    try:
        task = services.render_service.submit_render(
            channel_id=channel_id,
            project_id=project_id,
            mode=mode,
            profile=aspect_ratio,
            aspect_mode=aspect_mode,
            transition=transition,
            transition_duration_sec=transition_duration_sec,
            burn_subtitles=burn_subtitles,
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
