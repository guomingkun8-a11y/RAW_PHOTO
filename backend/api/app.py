from __future__ import annotations

from contextlib import asynccontextmanager
from threading import Event

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from api import ai, business, image_conversations, image_library, image_tasks, image_uploads, monitoring, prompt_analysis, system
from api.errors import install_exception_handlers
from api.support import resolve_web_asset
from services.config import config
from services.database_maintenance import ensure_database_ready, start_database_maintenance_scheduler
from services.image_storage_service import image_storage_service
from services.image_service import start_image_cleanup_scheduler
from utils.log import logger


def create_app() -> FastAPI:
    app_version = config.app_version

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        stop_event = Event()
        try:
            database_result = ensure_database_ready()
            logger.info({"event": "database_ready", **database_result})
        except Exception as exc:
            logger.info({"event": "database_ready_failed", "error": str(exc)})
        database_thread = start_database_maintenance_scheduler(stop_event)
        cleanup_thread = start_image_cleanup_scheduler(stop_event)
        image_storage_service.cleanup_old_images()
        try:
            yield
        finally:
            stop_event.set()
            database_thread.join(timeout=1)
            cleanup_thread.join(timeout=1)

    app = FastAPI(title="image-generation-api", version=app_version, lifespan=lifespan)
    install_exception_handlers(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(ai.create_router())
    app.include_router(business.create_router())
    app.include_router(image_tasks.create_router())
    app.include_router(image_uploads.create_router())
    app.include_router(image_conversations.create_router())
    app.include_router(image_library.create_router())
    app.include_router(prompt_analysis.create_router())
    app.include_router(monitoring.create_router())
    app.include_router(system.create_router(app_version))

    @app.api_route("/{full_path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    async def serve_web(full_path: str):
        asset = resolve_web_asset(full_path)
        if asset is not None:
            return FileResponse(asset)
        if full_path.strip("/").startswith("_next/"):
            raise HTTPException(status_code=404, detail="Not Found")
        fallback = resolve_web_asset("")
        if fallback is None:
            raise HTTPException(status_code=404, detail="Not Found")
        return FileResponse(fallback)

    return app
