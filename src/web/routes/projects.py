"""Project management routes for Studio WebUI."""

import re
import uuid
from typing import Optional
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from src.web.dependencies import ServiceContainer, get_service_container

router = APIRouter()
templates = Jinja2Templates(directory="src/web/templates")


@router.get("/projects", response_class=HTMLResponse)
async def list_projects(
    request: Request,
    channel_id: Optional[str] = None,
    services: ServiceContainer = Depends(get_service_container),
):
    channels = services.channel_service.list_channels(include_inactive=True)

    filter_ch = channel_id.strip() if channel_id and channel_id.strip() else None

    if filter_ch:
        try:
            projects = services.project_service.list_projects(channel_id=filter_ch)
        except Exception:
            projects = []
    else:
        projects = []
        for ch in channels:
            ch_id = getattr(ch, "id", None) or getattr(ch, "channel_id", "")
            try:
                projs = services.project_service.list_projects(channel_id=ch_id)
                projects.extend(projs)
            except Exception:
                pass

    scene_counts = {}
    for p in projects:
        p_id = getattr(p, "id", None) or getattr(p, "project_id", "")
        ch_id = getattr(p, "channel_id", "")
        try:
            scenes = services.scene_service.list_scenes(ch_id, p_id)
            scene_counts[p_id] = len(scenes)
        except Exception:
            scene_counts[p_id] = 0

    return templates.TemplateResponse(
        request=request,
        name="projects.html",
        context={
            "projects": projects,
            "channels": channels,
            "selected_channel_id": filter_ch,
            "scene_counts": scene_counts,
        },
    )


@router.post("/projects/create", response_class=RedirectResponse)
async def create_project(
    request: Request,
    channel_id: str = Form(...),
    name: str = Form(...),
    project_id: Optional[str] = Form(None),
    target_aspect_ratio: str = Form("9:16"),
    target_duration: float = Form(15.0),
    description: Optional[str] = Form(None),
    services: ServiceContainer = Depends(get_service_container),
):
    clean_ch = channel_id.strip()
    clean_name = name.strip()
    clean_desc = (description or "").strip()

    if project_id and project_id.strip():
        clean_id = project_id.strip()
    else:
        slug = re.sub(r"[^a-zA-Z0-9_\-]", "_", clean_name.lower())[:30]
        clean_id = f"prj_{slug}_{uuid.uuid4().hex[:6]}" if len(slug) < 3 else f"prj_{slug}"

    proj = services.project_service.create_project(
        channel_id=clean_ch,
        project_input={
            "id": clean_id,
            "channel_id": clean_ch,
            "title": clean_name,
            "description": clean_desc,
            "aspect_ratio": target_aspect_ratio.strip(),
            "target_duration_sec": target_duration,
        },
    )

    proj_id = getattr(proj, "id", None) or getattr(proj, "project_id", clean_id)
    return RedirectResponse(
        url=f"/projects/{proj.channel_id}/{proj_id}", status_code=303
    )


@router.get("/projects/{channel_id}/{project_id}", response_class=HTMLResponse)
async def get_project_detail(
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
        name="project_detail.html",
        context={
            "project": project,
            "scenes": scenes,
            "flow_status": flow_status,
            "active_takes": active_takes,
        },
    )
