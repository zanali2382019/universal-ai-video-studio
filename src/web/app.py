"""Studio WebUI FastAPI Application Factory.

Provides local WebUI integration with ServiceContainer, static assets,
and domain routes without direct database coupling.
"""

from pathlib import Path
from typing import Optional
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse

from src.database.connection import DatabaseManager
from src.storage.service import StorageService
from src.web.dependencies import init_service_container

from src.web.routes.dashboard import router as dashboard_router
from src.web.routes.channels import router as channels_router
from src.web.routes.projects import router as projects_router
from src.web.routes.storyboard import router as storyboard_router
from src.web.routes.scenes import router as scenes_router
from src.web.routes.characters import router as characters_router
from src.web.routes.flow_outbound import router as flow_outbound_router
from src.web.routes.flow_inbound import router as flow_inbound_router
from src.web.routes.assets import router as assets_router
from src.web.routes.render_queue import router as render_queue_router
from src.web.routes.batch import router as batch_router
from src.web.routes.preview import router as preview_router
from src.web.routes.monitor import router as monitor_router
from src.web.routes.export_packages import router as export_packages_router
from src.web.routes.intelligence import router as intelligence_router


def create_app(
    db_manager: Optional[DatabaseManager] = None,
    storage_service: Optional[StorageService] = None,
) -> FastAPI:
    """Create and configure the FastAPI application."""
    container = init_service_container(db_manager=db_manager, storage_service=storage_service)

    app = FastAPI(
        title="Universal AI Video Studio WebUI",
        description="Local WebUI Interface for Studio Channel, Project & Google Flow Management",
        version="1.0.0",
        docs_url="/api/docs",
        redoc_url=None,
    )

    # State
    app.state.service_container = container

    # Static files mounting
    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # Include routers
    app.include_router(dashboard_router)
    app.include_router(channels_router)
    app.include_router(projects_router)
    app.include_router(storyboard_router)
    app.include_router(scenes_router)
    app.include_router(characters_router)
    app.include_router(flow_outbound_router)
    app.include_router(flow_inbound_router)
    app.include_router(assets_router)
    app.include_router(render_queue_router)
    app.include_router(batch_router)
    app.include_router(preview_router)
    app.include_router(monitor_router)
    app.include_router(export_packages_router)
    app.include_router(intelligence_router)

    @app.get("/health")
    async def health_check():
        return JSONResponse({"status": "healthy", "service": "universal-ai-video-studio-web"})

    return app
