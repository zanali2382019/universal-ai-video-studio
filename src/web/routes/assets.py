"""Media Asset Management routes for Studio WebUI."""

import logging
from typing import Any, List, Optional
from fastapi import APIRouter, Request, Depends, HTTPException, Form
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from src.core.constants import LegalBasis
from src.web.dependencies import ServiceContainer, get_service_container

logger = logging.getLogger("studio.web.routes.assets")
router = APIRouter()
templates = Jinja2Templates(directory="src/web/templates")


class AssetDisplayItem:
    """Wrapper around MediaAssetModel providing direct access to license status."""

    def __init__(self, asset: Any, license: Optional[Any] = None):
        self.asset = asset
        self.license = license
        self.license_id = getattr(license, "id", None)
        status = getattr(license, "verification_status", None)
        if hasattr(status, "value"):
            self.license_status = str(status.value).lower()
        elif status:
            self.license_status = str(status).lower()
        else:
            self.license_status = "unregistered"
        self.commercial_use_allowed = bool(getattr(license, "commercial_use_allowed", False))
        basis = getattr(license, "legal_basis", None)
        self.legal_basis = str(basis.value if hasattr(basis, "value") else (basis or "-"))

    def __getattr__(self, name: str) -> Any:
        return getattr(self.asset, name)


@router.get("/assets", response_class=HTMLResponse)
async def list_assets(
    request: Request,
    channel_id: Optional[str] = None,
    asset_type: Optional[str] = None,
    success_msg: Optional[str] = None,
    error_msg: Optional[str] = None,
    services: ServiceContainer = Depends(get_service_container),
):
    channels = services.channel_service.list_channels(include_inactive=True)

    filter_ch = channel_id.strip() if channel_id and channel_id.strip() else None
    filter_type = asset_type.strip() if asset_type and asset_type.strip() else None

    raw_assets = []
    if filter_ch:
        try:
            raw_assets = services.asset_service.list_assets(
                channel_id=filter_ch, asset_type=filter_type
            )
        except Exception:
            raw_assets = []
    else:
        for ch in channels:
            try:
                ch_assets = services.asset_service.list_assets(
                    channel_id=ch.id, asset_type=filter_type
                )
                raw_assets.extend(ch_assets)
            except Exception:
                pass

    assets: List[AssetDisplayItem] = []
    for asset in raw_assets:
        try:
            lic = services.license_service.get_license_by_asset(asset.channel_id, asset.id)
        except Exception:
            lic = None
        assets.append(AssetDisplayItem(asset, lic))

    return templates.TemplateResponse(
        request=request,
        name="assets.html",
        context={
            "assets": assets,
            "channels": channels,
            "selected_channel_id": filter_ch,
            "selected_type": filter_type,
            "success_msg": success_msg,
            "error_msg": error_msg,
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


@router.post("/assets/{asset_id}/verify-license", response_class=RedirectResponse)
async def verify_asset_license(
    asset_id: str,
    channel_id: Optional[str] = Form(None),
    legal_basis: str = Form("explicit_user_override_exception"),
    verified_by: str = Form("channel_owner"),
    notes: Optional[str] = Form(None),
    services: ServiceContainer = Depends(get_service_container),
):
    """Explicit owner license approval transitioning asset to VERIFIED (ADR-008)."""
    clean_aid = asset_id.strip()
    clean_ch = channel_id.strip() if channel_id and channel_id.strip() else None

    # Locate target asset
    target_asset = None
    if clean_ch:
        try:
            target_asset = services.asset_service.get_asset(clean_ch, clean_aid)
        except Exception:
            target_asset = None
    else:
        channels = services.channel_service.list_channels(include_inactive=True)
        for ch in channels:
            try:
                candidate = services.asset_service.get_asset(ch.id, clean_aid)
                if candidate:
                    clean_ch = ch.id
                    target_asset = candidate
                    break
            except Exception:
                continue

    if not target_asset or not clean_ch:
        return RedirectResponse(
            url=f"/assets?error_msg=Asset+{clean_aid}+not+found",
            status_code=303,
        )

    # Ensure a license record exists
    lic = services.license_service.get_license_by_asset(clean_ch, clean_aid)
    if not lic:
        lic = services.license_service.register_license(
            channel_id=clean_ch,
            license_input={
                "asset_id": clean_aid,
                "legal_basis": LegalBasis.EXPLICIT_USER_OVERRIDE_EXCEPTION,
                "source_uri": target_asset.file_path,
                "notes": notes or f"Owner registered license for asset {target_asset.file_name or clean_aid}",
            },
            actor=verified_by or "channel_owner",
        )

    try:
        lb_enum = LegalBasis(legal_basis)
    except Exception:
        lb_enum = LegalBasis.EXPLICIT_USER_OVERRIDE_EXCEPTION

    verification_notes = (notes or "").strip() or f"Owner explicit approval for production and commercial export: {target_asset.file_name or clean_aid}"

    services.license_service.verify_license(
        channel_id=clean_ch,
        license_id=lic.id,
        legal_basis=lb_enum,
        verified_by=verified_by or "channel_owner",
        proof_file_path=target_asset.file_path,
        proof_sha256=target_asset.file_sha256,
        commercial_use_allowed=True,
        notes=verification_notes,
    )

    logger.info(f"Owner verified license for asset '{clean_aid}' on channel '{clean_ch}'.")
    return RedirectResponse(
        url=f"/assets?channel_id={clean_ch}&success_msg=License+verified+successfully+for+asset+{clean_aid}",
        status_code=303,
    )
