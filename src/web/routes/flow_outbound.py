"""Google Flow Outbound routes for Studio WebUI."""

from fastapi import APIRouter, Request, Depends, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from src.web.dependencies import ServiceContainer, get_service_container

router = APIRouter()
templates = Jinja2Templates(directory="src/web/templates")


@router.get("/flow/outbound/{channel_id}/{project_id}", response_class=HTMLResponse)
async def get_flow_outbound(
    channel_id: str,
    project_id: str,
    request: Request,
    services: ServiceContainer = Depends(get_service_container),
):
    project = services.project_service.get_project(channel_id, project_id)

    package = None
    try:
        package = services.flow_prompt_service.build_package_from_project_id(
            channel_id=channel_id, project_id=project_id
        )
    except Exception:
        package = None

    return templates.TemplateResponse(
        request=request,
        name="flow_outbound.html",
        context={
            "project": project,
            "package": package,
        },
    )


@router.post("/flow/outbound/{channel_id}/{project_id}/generate", response_class=RedirectResponse)
async def generate_flow_outbound(
    channel_id: str,
    project_id: str,
    services: ServiceContainer = Depends(get_service_container),
):
    services.flow_prompt_service.build_package_from_project_id(
        channel_id=channel_id, project_id=project_id
    )
    return RedirectResponse(
        url=f"/flow/outbound/{channel_id}/{project_id}", status_code=303
    )


@router.get("/flow/outbound/{channel_id}/{project_id}/export/{format_type}")
async def export_flow_outbound(
    channel_id: str,
    project_id: str,
    format_type: str,
    services: ServiceContainer = Depends(get_service_container),
):
    package = services.flow_prompt_service.build_package_from_project_id(
        channel_id=channel_id, project_id=project_id
    )
    export_res = services.flow_prompt_service.export_package(package)

    clean_fmt = format_type.lower()
    if clean_fmt == "json":
        content = services.storage.load_bytes(export_res.json_storage_key).decode("utf-8")
        media_type = "application/json"
        filename = f"{project_id}_flow_prompts.json"
    else:
        content = services.storage.load_bytes(export_res.markdown_storage_key).decode("utf-8")
        media_type = "text/markdown; charset=utf-8"
        filename = f"{project_id}_flow_prompts.md"

    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
