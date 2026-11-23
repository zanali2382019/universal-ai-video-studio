"""Export & Publishing WebUI Router (Task 3.16).

Provides:
- GET /export: Platform profiles, package history, release staging.
- POST /export/preflight: Instant AJAX ADR-008 preflight audit.
- POST /export/generate: Full atomic package generation (preflight, thumbnail, quality gates, manifest, ZIP).
- GET /export/{package_id}: Detailed package inspection view (manifest, hashes, quality report, metadata).
- GET /export/{package_id}/download: Download verified release ZIP archive.
"""
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, Depends, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from src.core.logger import get_logger
from src.core.exceptions import ValidationException, LicenseVerificationException, MediaValidationException
from src.web.dependencies import ServiceContainer, get_service_container

logger = get_logger("studio.web.export")

router = APIRouter(prefix="/export", tags=["Export & Publishing"])
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


@router.get("", response_class=HTMLResponse)
async def export_dashboard(
    request: Request,
    channel_id: Optional[str] = None,
    container: ServiceContainer = Depends(get_service_container),
):
    """Main export dashboard showing platform profiles and existing packages."""
    channels = container.channel_service.list_channels()
    selected_channel = channel_id or (channels[0].id if channels else None)

    projects = []
    if selected_channel:
        projects = container.project_service.list_projects(selected_channel)

    profiles = container.export_service.list_available_profiles()
    packages = container.export_service.list_packages(channel_id=selected_channel)

    return templates.TemplateResponse(
        request=request,
        name="export.html",
        context={
            "request": request,
            "channels": channels,
            "selected_channel": selected_channel,
            "projects": projects,
            "profiles": profiles,
            "packages": packages,
        },
    )


@router.post("/preflight", response_class=JSONResponse)
async def check_preflight_ajax(
    request: Request,
    channel_id: str = Form(...),
    project_id: str = Form(...),
    container: ServiceContainer = Depends(get_service_container),
):
    """Run instant ADR-008 license preflight check."""
    report = container.export_service.run_license_preflight(channel_id, project_id)
    return JSONResponse(report.to_dict())


@router.post("/generate")
async def generate_package(
    request: Request,
    channel_id: str = Form(...),
    project_id: str = Form(...),
    profile_id: str = Form("youtube_shorts"),
    headline_ar: Optional[str] = Form(None),
    badge_text: Optional[str] = Form(None),
    video_file_path: Optional[str] = Form(None),
    enforce_license_gate: bool = Form(True),
    enforce_quality_gate: bool = Form(True),
    container: ServiceContainer = Depends(get_service_container),
):
    """Generate a complete release export package."""
    try:
        pkg = container.export_service.create_export_package(
            channel_id=channel_id,
            project_id=project_id,
            profile_id=profile_id,
            video_file_path=video_file_path.strip() if video_file_path else None,
            headline_ar=headline_ar.strip() if headline_ar else None,
            badge_text=badge_text.strip() if badge_text else None,
            enforce_license_gate=enforce_license_gate,
            enforce_quality_gate=enforce_quality_gate,
        )
        return RedirectResponse(url=f"/export/{pkg.id}", status_code=303)
    except LicenseVerificationException as e:
        logger.warning(f"License preflight failed during export generation: {e}")
        channels = container.channel_service.list_channels()
        projects = container.project_service.list_projects(channel_id)
        profiles = container.export_service.list_available_profiles()
        packages = container.export_service.list_packages(channel_id=channel_id)
        return templates.TemplateResponse(
            request=request,
            name="export.html",
            context={
                "request": request,
                "channels": channels,
                "selected_channel": channel_id,
                "projects": projects,
                "profiles": profiles,
                "packages": packages,
                "error_message": f"بوابة الترخيص (ADR-008): تم حظر التصدير — {e.message}",
            },
            status_code=400,
        )
    except (ValidationException, MediaValidationException) as e:
        logger.warning(f"Export validation failed: {e}")
        channels = container.channel_service.list_channels()
        projects = container.project_service.list_projects(channel_id)
        profiles = container.export_service.list_available_profiles()
        packages = container.export_service.list_packages(channel_id=channel_id)
        return templates.TemplateResponse(
            request=request,
            name="export.html",
            context={
                "request": request,
                "channels": channels,
                "selected_channel": channel_id,
                "projects": projects,
                "profiles": profiles,
                "packages": packages,
                "error_message": f"فشل فحص الجودة أو الإعدادات: {str(e)}",
            },
            status_code=400,
        )


@router.get("/{package_id}", response_class=HTMLResponse)
async def view_package_detail(
    request: Request,
    package_id: str,
    container: ServiceContainer = Depends(get_service_container),
):
    """Inspect complete package details, manifest, quality gates, and platform metadata."""
    package = container.export_service.get_package(package_id)
    if not package:
        raise HTTPException(status_code=404, detail="Export package not found.")

    channel = container.channel_service.get_channel(package.channel_id)
    project = container.project_service.get_project(package.channel_id, package.project_id)

    return templates.TemplateResponse(
        request=request,
        name="export_detail.html",
        context={
            "request": request,
            "package": package,
            "channel": channel,
            "project": project,
        },
    )


@router.get("/{package_id}/download")
async def download_package_zip(
    package_id: str,
    container: ServiceContainer = Depends(get_service_container),
):
    """Download package ZIP file."""
    package = container.export_service.get_package(package_id)
    if not package or not package.zip_path:
        raise HTTPException(status_code=404, detail="Export package ZIP not found.")

    if package.status != "completed":
        raise HTTPException(
            status_code=403,
            detail=f"Export package '{package.id}' cannot be downloaded because its status is '{package.status}'. Only verified 'completed' packages can be downloaded as release archives."
        )

    zip_file = Path(package.zip_path).resolve()
    if not zip_file.is_file():
        raise HTTPException(status_code=404, detail="ZIP archive file missing on disk.")

    return FileResponse(
        path=str(zip_file),
        media_type="application/zip",
        filename=f"{package.id}.zip",
    )
