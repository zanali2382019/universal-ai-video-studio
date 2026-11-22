"""Media Asset Management routes for Studio WebUI."""

from typing import Optional
from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.templating import Jinja2Templates

from src.web.dependencies import ServiceContainer, get_service_container

router = APIRouter()
templates = Jinja2Templates(directory="src/web/templates")


@router.get("/assets", response_class=HTMLResponse)
async def list_assets(
    request: Request,
    channel_id: Optional[str] = None,
    asset_type: Optional[str] = None,
    services: ServiceContainer = Depends(get_service_container),
):
    channels = services.channel_service.list_channels(include_inactive=True)

    filter_ch = channel_id.strip() if channel_id and channel_id.strip() else None
    filter_type = asset_type.strip() if asset_type and asset_type.strip() else None

    if filter_ch:
        try:
            assets = services.asset_service.list_assets(
                channel_id=filter_ch, asset_type=filter_type
            )
        except Exception:
            assets = []
    else:
        assets = []
        for ch in channels:
            try:
                ch_assets = services.asset_service.list_assets(
                    channel_id=ch.id, asset_type=filter_type
                )
                assets.extend(ch_assets)
            except Exception:
                pass

    return templates.TemplateResponse(
        request=request,
        name="assets.html",
        context={
            "assets": assets,
            "channels": channels,
            "selected_channel_id": filter_ch,
            "selected_type": filter_type,
        },
    )


@router.get("/assets/{channel_id}/{asset_id}/file")
async def get_asset_file(
    channel_id: str,
    asset_id: str,
    services: ServiceContainer = Depends(get_service_container),
):
    asset = services.asset_service.get_asset(channel_id, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")

    local_path = services.storage.get_local_path(asset.storage_key)
    if not local_path.exists():
        raise HTTPException(status_code=404, detail="Physical media file not found on disk")

    return FileResponse(
        path=local_path,
        filename=asset.file_name or f"{asset.id}",
        media_type=asset.mime_type or "application/octet-stream",
    )
