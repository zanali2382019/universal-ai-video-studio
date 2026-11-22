"""Scene editing routes for Studio WebUI."""

import uuid
from typing import Optional
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from src.web.dependencies import ServiceContainer, get_service_container

router = APIRouter()
templates = Jinja2Templates(directory="src/web/templates")


@router.get("/scenes/{channel_id}/{project_id}/{scene_id}", response_class=HTMLResponse)
async def get_scene_edit(
    channel_id: str,
    project_id: str,
    scene_id: str,
    request: Request,
    services: ServiceContainer = Depends(get_service_container),
):
    project = services.project_service.get_project(channel_id, project_id)
    scene = services.scene_service.get_scene(channel_id, scene_id)
    characters = services.character_bible_service.list_characters(channel_id=channel_id)

    # Fetch prompt version history
    try:
        prompt_history = services.flow_prompt_service.get_scene_prompt_history(scene_id)
    except Exception:
        prompt_history = []

    return templates.TemplateResponse(
        request=request,
        name="scene_edit.html",
        context={
            "project": project,
            "scene": scene,
            "characters": characters,
            "selected_character_id": getattr(scene, "character_id", None),
            "selected_outfit_id": getattr(scene, "outfit_id", None),
            "prompt_history": prompt_history,
        },
    )


@router.post("/scenes/{channel_id}/{project_id}/{scene_id}/edit_prompt", response_class=RedirectResponse)
async def post_scene_edit(
    channel_id: str,
    project_id: str,
    scene_id: str,
    script_text: str = Form(""),
    visual_prompt: str = Form(...),
    character_id: Optional[str] = Form(None),
    outfit_id: Optional[str] = Form(None),
    target_duration_sec: float = Form(5.0),
    modification_reason: str = Form("Prompt update via Studio WebUI"),
    services: ServiceContainer = Depends(get_service_container),
):
    clean_char = character_id.strip() if character_id and character_id.strip() else None
    clean_outfit = outfit_id.strip() if outfit_id and outfit_id.strip() else None

    # 1. Update scene in database via SceneService
    services.scene_service.update_scene(
        channel_id=channel_id,
        scene_id=scene_id,
        updates={
            "narration_text": script_text.strip(),
            "visual_prompt": visual_prompt.strip(),
            "duration_seconds": target_duration_sec,
        },
    )

    # 2. Record versioned prompt change in FlowPromptService / FlowPromptRepository
    try:
        current = services.flow_prompt_repo.get_scene_prompt(scene_id)
        if current:
            services.flow_prompt_service.modify_scene_prompt(
                scene_id=scene_id,
                visual_prompt=visual_prompt.strip(),
            )
        else:
            services.flow_prompt_repo.save_scene_prompt(
                id=f"prp_{uuid.uuid4().hex[:12]}",
                scene_id=scene_id,
                channel_id=channel_id,
                project_id=project_id,
                version=1,
                visual_prompt=visual_prompt.strip(),
            )
    except Exception:
        pass

    return RedirectResponse(
        url=f"/scenes/{channel_id}/{project_id}/{scene_id}", status_code=303
    )
