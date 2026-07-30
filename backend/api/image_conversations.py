from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from api.support import require_identity
from services.image_conversation_service import image_conversation_service


class ImageConversationUpsertRequest(BaseModel):
    conversation: dict[str, Any] = Field(default_factory=dict)


class ImageConversationRenameRequest(BaseModel):
    title: str = Field(default="", max_length=191)


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/image-conversations")
    async def list_image_conversations(
        limit: int = Query(default=500, ge=1, le=1000),
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        return await run_in_threadpool(
            image_conversation_service.list_conversations,
            identity=identity,
            limit=limit,
        )

    @router.put("/api/image-conversations/{conversation_id}")
    async def upsert_image_conversation(
        conversation_id: str,
        body: ImageConversationUpsertRequest,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        try:
            return await run_in_threadpool(
                image_conversation_service.upsert_conversation,
                identity=identity,
                conversation_id=conversation_id,
                payload=body.conversation,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.patch("/api/image-conversations/{conversation_id}")
    async def rename_image_conversation(
        conversation_id: str,
        body: ImageConversationRenameRequest,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        try:
            item = await run_in_threadpool(
                image_conversation_service.rename_conversation,
                identity=identity,
                conversation_id=conversation_id,
                title=body.title,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        if item is None:
            raise HTTPException(status_code=404, detail={"error": "conversation not found"})
        return item

    @router.delete("/api/image-conversations/{conversation_id}")
    async def delete_image_conversation(
        conversation_id: str,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        deleted = await run_in_threadpool(
            image_conversation_service.delete_conversation,
            identity=identity,
            conversation_id=conversation_id,
        )
        if not deleted:
            raise HTTPException(status_code=404, detail={"error": "conversation not found"})
        return {"ok": True}

    @router.delete("/api/image-conversations")
    async def clear_image_conversations(authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        deleted = await run_in_threadpool(
            image_conversation_service.clear_conversations,
            identity=identity,
        )
        return {"ok": True, "deleted": deleted}

    return router
