from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from api.support import require_identity, resolve_image_base_url
from services.image_library_service import image_library_service


class ImageLibraryUpdateRequest(BaseModel):
    favorite: bool | None = None
    deleted: bool | None = None


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/image-library")
    async def list_image_library(
        request: Request,
        limit: int = Query(default=80, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
        cursor_created_at: str = Query(default=""),
        cursor_id: int = Query(default=0, ge=0),
        q: str = Query(default=""),
        product_id: int = Query(default=0, ge=0),
        template_id: int = Query(default=0, ge=0),
        favorite: bool = Query(default=False),
        include_deleted: bool = Query(default=False),
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        return await run_in_threadpool(
            image_library_service.list_images,
            identity=identity,
            base_url=resolve_image_base_url(request),
            limit=limit,
            offset=offset,
            cursor_created_at=cursor_created_at,
            cursor_id=cursor_id,
            query_text=q,
            product_id=product_id,
            template_id=template_id,
            favorite_only=favorite,
            include_deleted=include_deleted,
        )

    @router.patch("/api/image-library/{image_id}")
    async def update_image_library_item(
        image_id: int,
        body: ImageLibraryUpdateRequest,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        item = await run_in_threadpool(
            image_library_service.update_image,
            identity=identity,
            image_id=image_id,
            favorite=body.favorite,
            deleted=body.deleted,
        )
        if item is None:
            raise HTTPException(status_code=404, detail={"error": "image not found"})
        return item

    @router.get("/api/image-library/health")
    async def image_library_health(authorization: str | None = Header(default=None)):
        require_identity(authorization)
        return await run_in_threadpool(image_library_service.health_check)

    return router
