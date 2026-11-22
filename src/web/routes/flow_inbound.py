"""Google Flow Inbound & Take management routes for Studio WebUI."""

from pathlib import Path
from typing import Optional, Dict, Any, List
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from src.web.dependencies import ServiceContainer, get_service_container

router = APIRouter()
templates = Jinja2Templates(directory="src/web/templates")


@router.get("/flow/inbound/{channel_id}/{project_id}", response_class=HTMLResponse)
async def get_flow_inbound(
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

    takes_by_scene: Dict[str, List[Any]] = {}
    active_takes: Dict[str, str] = {}

    for sc in scenes:
        sc_id = getattr(sc, "id", None) or getattr(sc, "scene_id", "")
        takes = services.flow_inbound_repo.list_takes_for_scene(
            project_id=project_id, scene_id=sc_id, asset_role="video"
        )
        takes_by_scene[sc_id] = takes
        for tk in takes:
            if tk.get("is_active"):
                active_takes[sc_id] = tk.get("take_id") or str(tk.get("id", ""))

    return templates.TemplateResponse(
        request=request,
        name="flow_inbound.html",
        context={
            "project": project,
            "scenes": scenes,
            "flow_status": flow_status,
            "takes_by_scene": takes_by_scene,
            "active_takes": active_takes,
            "import_report": None,
        },
    )


@router.post("/flow/inbound/{channel_id}/{project_id}/import", response_class=HTMLResponse)
async def post_flow_import(
    channel_id: str,
    project_id: str,
    request: Request,
    import_path: str = Form(...),
    dry_run: Optional[str] = Form(None),
    services: ServiceContainer = Depends(get_service_container),
):
    project = services.project_service.get_project(channel_id, project_id)
    scenes = services.scene_service.list_scenes(channel_id, project_id)

    is_dry_run = dry_run == "true"
    src_path = Path(import_path.strip())

    import_report_dict = {
        "dry_run": is_dry_run,
        "imported_count": 0,
        "matched_scenes": 0,
        "skipped_count": 0,
        "errors": [],
    }

    if src_path.exists():
        try:
            report = services.flow_inbound_service.import_assets(
                channel_id=channel_id,
                project_id=project_id,
                source_path=src_path,
                dry_run=is_dry_run,
                actor="studio_web",
            )
            import_report_dict["imported_count"] = (
                report.would_import_count if is_dry_run else report.imported_count
            )
            import_report_dict["matched_scenes"] = report.matched_scenes_count
            import_report_dict["skipped_count"] = report.skipped_count
            import_report_dict["errors"] = report.errors
        except Exception as e:
            import_report_dict["errors"].append(str(e))
    else:
        import_report_dict["errors"].append(f"Directory not found on server: {src_path}")

    flow_status = services.flow_inbound_service.get_project_flow_status(
        channel_id, project_id
    )

    takes_by_scene: Dict[str, List[Any]] = {}
    active_takes: Dict[str, str] = {}

    for sc in scenes:
        sc_id = getattr(sc, "id", None) or getattr(sc, "scene_id", "")
        takes = services.flow_inbound_repo.list_takes_for_scene(
            project_id=project_id, scene_id=sc_id, asset_role="video"
        )
        takes_by_scene[sc_id] = takes
        for tk in takes:
            if tk.get("is_active"):
                active_takes[sc_id] = tk.get("take_id") or str(tk.get("id", ""))

    return templates.TemplateResponse(
        request=request,
        name="flow_inbound.html",
        context={
            "project": project,
            "scenes": scenes,
            "flow_status": flow_status,
            "takes_by_scene": takes_by_scene,
            "active_takes": active_takes,
            "import_report": import_report_dict,
        },
    )


@router.post("/flow/inbound/{channel_id}/{project_id}/activate_take", response_class=RedirectResponse)
async def post_activate_take(
    channel_id: str,
    project_id: str,
    scene_id: str = Form(...),
    take_id: str = Form(...),
    services: ServiceContainer = Depends(get_service_container),
):
    # Find take_number from take_id in repo
    takes = services.flow_inbound_repo.list_takes_for_scene(
        project_id=project_id, scene_id=scene_id, asset_role="video"
    )
    for tk in takes:
        if (tk.get("take_id") == take_id) or (str(tk.get("id", "")) == take_id):
            take_num = tk.get("take_number", 1)
            services.flow_inbound_service.set_active_take(
                project_id=project_id,
                scene_id=scene_id,
                take_number=take_num,
                asset_role="video",
            )
            break

    return RedirectResponse(
        url=f"/flow/inbound/{channel_id}/{project_id}", status_code=303
    )
