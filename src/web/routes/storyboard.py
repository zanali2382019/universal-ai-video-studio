"""Storyboard routes for Studio WebUI."""

from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from src.web.dependencies import ServiceContainer, get_service_container

router = APIRouter()
templates = Jinja2Templates(directory="src/web/templates")


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

    return templates.TemplateResponse(
        request=request,
        name="storyboard.html",
        context={
            "project": project,
            "scenes": scenes,
            "active_takes": active_takes,
        },
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
