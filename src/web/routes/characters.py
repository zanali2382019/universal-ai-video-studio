"""Character Bible routes for Studio WebUI."""

from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from src.web.dependencies import ServiceContainer, get_service_container

router = APIRouter()
templates = Jinja2Templates(directory="src/web/templates")


@router.get("/characters", response_class=HTMLResponse)
async def list_characters(
    request: Request,
    services: ServiceContainer = Depends(get_service_container),
):
    characters = services.character_bible_service.list_characters()
    return templates.TemplateResponse(
        request=request,
        name="characters.html",
        context={
            "characters": characters,
        },
    )


@router.get("/characters/{character_id}", response_class=HTMLResponse)
async def get_character_detail(
    character_id: str,
    request: Request,
    services: ServiceContainer = Depends(get_service_container),
):
    character = services.character_bible_service.get_character(character_id)
    return templates.TemplateResponse(
        request=request,
        name="character_detail.html",
        context={
            "character": character,
        },
    )
