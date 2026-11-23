"""Error & Progress Monitoring web routes for Studio WebUI (Task 3.15b)."""
from typing import Optional
from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from src.core.logger import get_logger
from src.web.dependencies import ServiceContainer, get_service_container

router = APIRouter()
templates = Jinja2Templates(directory="src/web/templates")
logger = get_logger("studio.web.routes.monitor")


@router.get("/monitor", response_class=HTMLResponse)
async def monitor_dashboard_view(
    request: Request,
    services: ServiceContainer = Depends(get_service_container),
):
    renders = services.render_service.list_renders()
    batches = services.batch_service.list_batches()

    return templates.TemplateResponse(
        request=request,
        name="monitor.html",
        context={
            "renders": renders,
            "batches": batches,
        },
    )


@router.get("/monitor/{task_id}/status")
async def get_task_status_json(
    task_id: str,
    services: ServiceContainer = Depends(get_service_container),
):
    try:
        progress = services.render_service.get_render_progress(task_id)
        return JSONResponse(progress)
    except Exception as e:
        logger.error(f"Error fetching status for task '{task_id}': {e}")
        return JSONResponse({"status": "error", "error": str(e)}, status_code=404)


@router.get("/monitor/api/tasks")
async def list_tasks_status_json(
    services: ServiceContainer = Depends(get_service_container),
):
    renders = services.render_service.list_renders()
    return JSONResponse({"tasks": renders})
