"""Channel management routes for Studio WebUI."""

from typing import Optional
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from src.web.dependencies import ServiceContainer, get_service_container
from src.creative.schemas import ChannelProfile
from src.flow.schemas import VisualStyleReference

router = APIRouter()
templates = Jinja2Templates(directory="src/web/templates")


@router.get("/channels", response_class=HTMLResponse)
async def list_channels(
    request: Request,
    services: ServiceContainer = Depends(get_service_container),
):
    channels = services.channel_service.list_channels(include_inactive=True)
    profiles = {}
    project_counts = {}

    for ch in channels:
        ch_id = getattr(ch, "id", None) or getattr(ch, "channel_id", "")
        try:
            prof = services.channel_profile_service.get_profile(ch_id)
            profiles[ch_id] = prof
        except Exception:
            profiles[ch_id] = None

        try:
            projs = services.project_service.list_projects(channel_id=ch_id)
            project_counts[ch_id] = len(projs)
        except Exception:
            project_counts[ch_id] = 0

    return templates.TemplateResponse(
        request=request,
        name="channels.html",
        context={
            "channels": channels,
            "profiles": profiles,
            "project_counts": project_counts,
        },
    )


@router.post("/channels/create", response_class=RedirectResponse)
async def create_channel(
    request: Request,
    channel_id: str = Form(...),
    name: str = Form(...),
    description: Optional[str] = Form(None),
    services: ServiceContainer = Depends(get_service_container),
):
    clean_id = channel_id.strip()
    clean_name = name.strip()
    clean_desc = (description or "").strip()

    services.channel_service.create_channel(
        {
            "id": clean_id,
            "name": clean_name,
            "handle": f"@{clean_id}",
            "niche": "animation",
            "default_language": "ar",
        }
    )

    # Initialize a channel profile if not already present
    try:
        services.channel_profile_service.get_profile(clean_id)
    except Exception:
        pass

    return RedirectResponse(url=f"/channels/{clean_id}", status_code=303)


@router.get("/channels/{channel_id}", response_class=HTMLResponse)
async def get_channel_detail(
    channel_id: str,
    request: Request,
    services: ServiceContainer = Depends(get_service_container),
):
    channel = services.channel_service.get_channel(channel_id)
    try:
        profile = services.channel_profile_service.get_profile(channel_id)
    except Exception:
        profile = None

    try:
        projects = services.project_service.list_projects(channel_id=channel_id)
    except Exception:
        projects = []

    return templates.TemplateResponse(
        request=request,
        name="channel_detail.html",
        context={
            "channel": channel,
            "profile": profile,
            "projects": projects,
        },
    )


@router.post("/channels/{channel_id}/edit", response_class=RedirectResponse)
async def edit_channel_profile(
    channel_id: str,
    brand_description: str = Form(...),
    visual_style: str = Form(...),
    color_primary: str = Form(""),
    color_secondary: str = Form(""),
    color_accent: str = Form(""),
    color_background: str = Form(""),
    aspect_ratio: str = Form("9:16"),
    language: str = Form("en"),
    target_duration_sec: float = Form(5.0),
    branding_rules: str = Form(""),
    services: ServiceContainer = Depends(get_service_container),
):
    try:
        existing = services.channel_profile_service.get_profile(channel_id)
        ch_name = existing.name
        ch_handle = existing.handle
    except Exception:
        ch = services.channel_service.get_channel(channel_id)
        ch_name = ch.name
        ch_handle = ch.handle

    palette_list = [c.strip() for c in [color_primary, color_secondary, color_accent, color_background] if c.strip()]
    if not palette_list:
        palette_list = ["#1E3A8A", "#3B82F6", "#F59E0B", "#0F172A"]

    updated_visual_style = VisualStyleReference(
        style_name=f"{channel_id} Visual Style",
        style_prompt=visual_style.strip(),
        color_palette=palette_list,
    )

    profile = ChannelProfile(
        channel_id=channel_id,
        name=ch_name,
        handle=ch_handle,
        brand_description=brand_description.strip(),
        default_aspect_ratio=aspect_ratio.strip(),
        default_language=language.strip(),
        visual_style=updated_visual_style,
        color_palette={
            "primary": color_primary.strip(),
            "secondary": color_secondary.strip(),
            "accent": color_accent.strip(),
            "background": color_background.strip(),
        },
        production_preferences={
            "default_scene_duration_sec": target_duration_sec,
        },
    )

    services.channel_profile_service.save_profile(profile)
    return RedirectResponse(url=f"/channels/{channel_id}", status_code=303)
