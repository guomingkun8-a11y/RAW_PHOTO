from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from api.support import require_identity
from services.image.image_conversation_service import image_conversation_service
from services.ecommerce.ecommerce_agent_memory_service import ecommerce_agent_memory_service


class ImageConversationUpsertRequest(BaseModel):
    conversation: dict[str, Any] = Field(default_factory=dict)


class ImageConversationRenameRequest(BaseModel):
    title: str = Field(default="", max_length=191)


def _require_storage_namespace(namespace: str | None, client: str | None) -> None:
    required = str(os.getenv("GMKRAW_REQUIRED_STORAGE_NAMESPACE") or "").strip()
    if required and str(namespace or "").strip() != required:
        raise HTTPException(status_code=409, detail={"error": "storage namespace mismatch"})
    required_client = str(os.getenv("GMKRAW_REQUIRED_STORAGE_CLIENT") or "").strip()
    if required_client and str(client or "").strip() != required_client:
        raise HTTPException(status_code=409, detail={"error": "storage client mismatch"})


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/image-conversations")
    async def list_image_conversations(
        limit: int = Query(default=50, ge=1, le=200),
        cursor_at: str = Query(default="", alias="cursorAt", max_length=40),
        cursor_id: str = Query(default="", alias="cursorId", max_length=191),
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        try:
            return await run_in_threadpool(
                image_conversation_service.list_conversations,
                identity=identity,
                limit=limit,
                cursor_at=cursor_at,
                cursor_id=cursor_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.get("/api/image-conversations/count")
    async def count_image_conversations(
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        count = await run_in_threadpool(
            image_conversation_service.count_conversations,
            identity=identity,
        )
        return {"count": count}

    @router.put("/api/image-conversations/{conversation_id}")
    async def upsert_image_conversation(
        conversation_id: str,
        body: ImageConversationUpsertRequest,
        authorization: str | None = Header(default=None),
        x_gmkraw_storage_namespace: str | None = Header(default=None),
        x_gmkraw_storage_client: str | None = Header(default=None),
    ):
        _require_storage_namespace(x_gmkraw_storage_namespace, x_gmkraw_storage_client)
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
        x_gmkraw_storage_namespace: str | None = Header(default=None),
        x_gmkraw_storage_client: str | None = Header(default=None),
    ):
        _require_storage_namespace(x_gmkraw_storage_namespace, x_gmkraw_storage_client)
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
        x_gmkraw_storage_namespace: str | None = Header(default=None),
        x_gmkraw_storage_client: str | None = Header(default=None),
    ):
        _require_storage_namespace(x_gmkraw_storage_namespace, x_gmkraw_storage_client)
        identity = require_identity(authorization)
        deleted = await run_in_threadpool(
            image_conversation_service.delete_conversation,
            identity=identity,
            conversation_id=conversation_id,
        )
        if not deleted:
            raise HTTPException(status_code=404, detail={"error": "conversation not found"})
        await run_in_threadpool(
            ecommerce_agent_memory_service.delete_conversation,
            owner_id=str(identity.get("id") or identity.get("username") or "anonymous"),
            conversation_id=conversation_id,
        )
        return {"ok": True}

    @router.delete("/api/image-conversations")
    async def clear_image_conversations(
        authorization: str | None = Header(default=None),
        x_gmkraw_storage_namespace: str | None = Header(default=None),
        x_gmkraw_storage_client: str | None = Header(default=None),
    ):
        _require_storage_namespace(x_gmkraw_storage_namespace, x_gmkraw_storage_client)
        identity = require_identity(authorization)
        deleted = await run_in_threadpool(
            image_conversation_service.clear_conversations,
            identity=identity,
        )
        await run_in_threadpool(
            ecommerce_agent_memory_service.clear_conversations,
            owner_id=str(identity.get("id") or identity.get("username") or "anonymous"),
        )
        return {"ok": True, "deleted": deleted}

    return router
