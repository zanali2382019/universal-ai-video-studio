"""Video preview and HTTP Range Streaming routes for Studio WebUI (Task 3.15b)."""
from pathlib import Path
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Request, Depends, HTTPException, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from src.core.config import get_settings
from src.core.logger import get_logger
from src.web.dependencies import ServiceContainer, get_service_container
from src.web.streaming import create_range_stream_response

router = APIRouter()
templates = Jinja2Templates(directory="src/web/templates")
logger = get_logger("studio.web.routes.preview")


@router.get("/preview", response_class=HTMLResponse)
async def preview_player_view(
    request: Request,
    project_id: Optional[str] = None,
    take_id: Optional[str] = None,
    task_id: Optional[str] = None,
    asset_id: Optional[str] = None,
    services: ServiceContainer = Depends(get_service_container),
):
    settings = get_settings()
    channels = services.channel_service.list_channels(include_inactive=True)

    preview_items: List[Dict[str, Any]] = []

    # 1. Collect completed render outputs
    try:
        render_tasks = services.render_service.list_renders()
        for rt in render_tasks:
            if rt.get("status") == "completed" and rt.get("output_path"):
                p_out = Path(rt["output_path"])
                if p_out.exists():
                    preview_items.append({
                        "id": f"task_{rt['task_id']}",
                        "title": f"Render: {p_out.name} ({rt.get('project_id')})",
                        "category": "Render Output",
                        "stream_url": f"/preview/stream?type=task&task_id={rt['task_id']}",
                        "duration_sec": rt.get("duration_sec"),
                        "file_size": rt.get("file_size_bytes"),
                    })
    except Exception as e:
        logger.debug(f"Could not load render outputs for preview: {e}")

    # 2. Collect Active Flow takes
    for ch in channels:
        try:
            projs = services.project_service.list_projects(ch.id)
            for p in projs:
                takes = services.flow_inbound_repo.list_takes_for_project(p.id)
                for t in takes:
                    if t.get("is_active"):
                        preview_items.append({
                            "id": f"take_{t['id']}",
                            "title": f"Flow Take: Scene {t.get('scene_id')} - Take #{t.get('take_number')} ({p.title})",
                            "category": "Flow Active Take",
                            "stream_url": f"/preview/stream?type=take&take_id={t['id']}",
                            "duration_sec": t.get("duration_sec"),
                            "file_size": None,
                        })
        except Exception:
            pass

    # 3. Check for any final_video.mp4 or export videos directly in project folders & export folders
    for ch in channels:
        ch_dir = settings.channels_root / ch.id
        if ch_dir.exists():
            for mp4_file in ch_dir.glob("projects/*/*.mp4"):
                item_id = f"proj_{mp4_file.parent.name}_{mp4_file.stem}"
                if not any(it["id"] == item_id for it in preview_items):
                    preview_items.append({
                        "id": item_id,
                        "title": f"Project Video: {mp4_file.parent.name}/{mp4_file.name}",
                        "category": "Project Video",
                        "stream_url": f"/preview/stream?type=project&channel_id={ch.id}&project_id={mp4_file.parent.name}&filename={mp4_file.name}",
                        "duration_sec": None,
                        "file_size": mp4_file.stat().st_size,
                    })

            for mp4_file in ch_dir.glob("exports/videos/*.mp4"):
                item_id = f"export_{mp4_file.stem}"
                if not any(it["id"] == item_id for it in preview_items):
                    preview_items.append({
                        "id": item_id,
                        "title": f"Exported Video: {mp4_file.name}",
                        "category": "Exported Video",
                        "stream_url": f"/preview/stream?type=export&channel_id={ch.id}&filename={mp4_file.name}",
                        "duration_sec": None,
                        "file_size": mp4_file.stat().st_size,
                    })

    # Determine currently selected item
    selected_stream_url = None
    selected_title = "Select media to preview"

    if task_id:
        selected_stream_url = f"/preview/stream?type=task&task_id={task_id}"
        selected_title = f"Render Task: {task_id}"
    elif take_id:
        selected_stream_url = f"/preview/stream?type=take&take_id={take_id}"
        selected_title = f"Flow Take: {take_id}"
    elif asset_id:
        selected_stream_url = f"/preview/stream?type=asset&asset_id={asset_id}"
        selected_title = f"Media Asset: {asset_id}"
    elif project_id:
        # Match project
        for it in preview_items:
            if project_id in it["title"]:
                selected_stream_url = it["stream_url"]
                selected_title = it["title"]
                break

    if not selected_stream_url and preview_items:
        selected_stream_url = preview_items[0]["stream_url"]
        selected_title = preview_items[0]["title"]

    return templates.TemplateResponse(
        request=request,
        name="preview.html",
        context={
            "preview_items": preview_items,
            "selected_stream_url": selected_stream_url,
            "selected_title": selected_title,
        },
    )


@router.get("/preview/stream")
async def stream_video(
    request: Request,
    type: str = "task",
    task_id: Optional[str] = None,
    take_id: Optional[str] = None,
    project_id: Optional[str] = None,
    channel_id: Optional[str] = None,
    filename: Optional[str] = None,
    asset_id: Optional[str] = None,
    key: Optional[str] = None,
    services: ServiceContainer = Depends(get_service_container),
):
    settings = get_settings()
    allowed_dirs = [settings.channels_root, settings.project_root]

    target_path: Optional[Path] = None

    if type == "task" and task_id:
        prog = services.render_service.get_render_progress(task_id)
        out_p = prog.get("output_path")
        if out_p:
            target_path = Path(out_p)

    elif type == "take" and take_id:
        take = services.flow_inbound_repo.get_take_by_id(take_id)
        if take:
            storage_key = take.get("storage_key")
            if storage_key:
                try:
                    p = services.storage.get_local_path(storage_key)
                    if p.exists():
                        target_path = p
                except Exception:
                    pass
            if not target_path and storage_key and Path(storage_key).exists():
                target_path = Path(storage_key)

    elif type == "project" and channel_id and project_id and filename:
        ch_dir = settings.channels_root / channel_id
        cand = ch_dir / "projects" / project_id / filename
        if cand.exists():
            target_path = cand

    elif type == "export" and channel_id and filename:
        ch_dir = settings.channels_root / channel_id
        cand = ch_dir / "exports" / "videos" / filename
        if cand.exists():
            target_path = cand

    elif type == "asset" and asset_id:
        all_assets = []
        for ch in services.channel_service.list_channels(include_inactive=True):
            try:
                all_assets.extend(services.asset_repo.list_by_channel(ch.id))
            except Exception:
                pass
        match_a = next((a for a in all_assets if a.id == asset_id), None)
        if match_a:
            cand = settings.channels_root / match_a.channel_id / match_a.file_path
            if cand.exists():
                target_path = cand

    elif type == "key" and key:
        try:
            p = services.storage.get_local_path(key)
            if p.exists():
                target_path = p
        except Exception:
            pass

    if not target_path or not target_path.exists():
        return Response(content="Media file not found", status_code=404)

    return create_range_stream_response(
        file_path=target_path,
        request=request,
        allowed_base_dirs=allowed_dirs,
        media_type="video/mp4",
    )
