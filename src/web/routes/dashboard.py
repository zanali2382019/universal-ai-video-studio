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

    # Render & Batch stats
    active_renders_count = 0
    completed_renders_count = 0
    try:
        all_renders = services.render_service.list_renders()
        for r in all_renders:
            if r.get("status") in ("queued", "running", "pending"):
                active_renders_count += 1
            elif r.get("status") == "completed":
                completed_renders_count += 1
    except Exception:
        pass

    batch_jobs_count = 0
    try:
        batches = services.batch_service.list_batches()
        batch_jobs_count = len(batches)
    except Exception:
        pass

    stats = {
        "channel_count": len(channels),
        "project_count": len(all_projects),
        "scene_count": total_scenes_count,
        "active_takes_count": total_active_takes_count,
        "asset_count": len(all_assets),
        "active_renders_count": active_renders_count,
        "completed_renders_count": completed_renders_count,
        "batch_jobs_count": batch_jobs_count,
    }

    # Production Intelligence Summary (Task 3.17)
    primary_ch_id = channels[0].id if channels else ""
    intel_summary = {
        "storage_human": "0 B",
        "flow_credits": 0.0,
        "success_rate": 100.0,
        "estimated_cost": "$0.00",
    }
    if primary_ch_id:
        try:
            st_data = services.intelligence_service.get_channel_storage_breakdown(primary_ch_id)
            crd_data = services.intelligence_service.get_flow_credit_summary(primary_ch_id)
            rnd_data = services.intelligence_service.get_render_analytics(primary_ch_id)
            cst_data = services.intelligence_service.calculate_costs(primary_ch_id)
            intel_summary = {
                "storage_human": st_data.get("human_size", "0 B"),
                "flow_credits": crd_data.get("current_balance", 0.0),
                "success_rate": rnd_data.get("success_rate_percent", 100.0),
                "estimated_cost": f"{cst_data.get('estimated_cost', 0.0):.2f} {cst_data.get('currency', 'USD')}",
            }
        except Exception:
            pass

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "stats": stats,
            "project_flow_stats": project_flow_stats,
            "intel_summary": intel_summary,
        },
    )
