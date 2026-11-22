"""Dashboard route for Studio WebUI."""

from typing import List, Dict, Any
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from src.web.dependencies import ServiceContainer, get_service_container

router = APIRouter()
templates = Jinja2Templates(directory="src/web/templates")


@router.get("/", response_class=RedirectResponse)
async def root_redirect():
    return RedirectResponse(url="/dashboard", status_code=303)


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_view(
    request: Request,
    services: ServiceContainer = Depends(get_service_container),
):
    channels = services.channel_service.list_channels(include_inactive=True)
    channel_map = {ch.id: ch.name for ch in channels}

    all_projects = []
    for ch in channels:
        try:
            projs = services.project_service.list_projects(ch.id)
            all_projects.extend(projs)
        except Exception:
            pass

    total_scenes_count = 0
    total_active_takes_count = 0
    project_flow_stats: List[Dict[str, Any]] = []

    for proj in all_projects:
        try:
            scenes = services.scene_service.list_scenes(proj.channel_id, proj.id)
        except Exception:
            scenes = []
        total_scenes_count += len(scenes)

        try:
            flow_status = services.flow_inbound_service.get_project_flow_status(
                proj.channel_id, proj.id
            )
            active_takes = flow_status.get("scenes_with_active_take", 0)
            comp_pct = flow_status.get("completion_percentage", 0.0)
        except Exception:
            active_takes = 0
            comp_pct = 0.0

        total_active_takes_count += active_takes

        aspect_val = proj.aspect_ratio.value if hasattr(proj.aspect_ratio, "value") else str(proj.aspect_ratio)
        status_val = proj.status.value if hasattr(proj.status, "value") else str(proj.status)

        project_flow_stats.append({
            "project_id": proj.id,
            "project_name": proj.title,
            "channel_id": proj.channel_id,
            "channel_name": channel_map.get(proj.channel_id, proj.channel_id),
            "aspect_ratio": aspect_val,
            "scene_count": len(scenes),
            "active_takes": active_takes,
            "completion_percentage": comp_pct,
            "status": status_val,
        })

    # Media assets count
    all_assets = []
    for ch in channels:
        try:
            assets = services.asset_service.list_assets(channel_id=ch.id)
            all_assets.extend(assets)
        except Exception:
            pass

    stats = {
        "channel_count": len(channels),
        "project_count": len(all_projects),
        "scene_count": total_scenes_count,
        "active_takes_count": total_active_takes_count,
        "asset_count": len(all_assets),
    }

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "stats": stats,
            "project_flow_stats": project_flow_stats,
        },
    )
