from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from api.prompt_analysis import PromptAnalysisProduct
from api.support import require_identity, resolve_image_base_url
from services.ecommerce.cow_agent_runtime_service import resume_cow_agent_run, start_cow_agent_run
from services.ecommerce.agent_queue_service import AgentConversationBusy, AgentQueueFull, AgentQueueUnavailable
from services.ecommerce.ecommerce_agent_memory_service import ProfessionalAgentConversationBusy
from services.ecommerce.professional_folder_service import (
    FOLDER_MAX_FILE_BYTES,
    FOLDER_MAX_ITEMS,
    FOLDER_MAX_TOTAL_BYTES,
    professional_folder_asset_service,
)


class ImageAgentReference(BaseModel):
    name: str = Field(default="reference.png", max_length=120)
    type: str = Field(default="image/png", max_length=80)
    data_url: str = Field(default="", alias="dataUrl")
    url: str = Field(default="", max_length=2000)
    role: str = Field(default="reference", max_length=80)

    class Config:
        populate_by_name = True


def _usable_agent_images(images: list[ImageAgentReference]) -> list[ImageAgentReference]:
    return [
        image
        for image in images
        if image.data_url.strip() or image.url.strip()
    ]


class ImageAgentConversationContext(BaseModel):
    user_request: str = Field(default="", alias="userRequest", max_length=1600)
    scene_name: str = Field(default="", alias="sceneName", max_length=160)
    proposal_summary: str = Field(default="", alias="proposalSummary", max_length=700)
    visual_direction: str = Field(default="", alias="visualDirection", max_length=500)
    result_status: str = Field(default="", alias="resultStatus", max_length=80)
    assistant_message: str = Field(default="", alias="assistantMessage", max_length=800)
    suggestions: list[str] = Field(default_factory=list, max_length=4)
    creative_brief: dict[str, str] = Field(default_factory=dict, alias="creativeBrief")

    class Config:
        populate_by_name = True


class ImageAgentRequest(BaseModel):
    prompt: str = Field(default="", max_length=8000)
    mode: Literal["generate", "edit"] = "generate"
    model: str = Field(default="gpt-image-2", max_length=160)
    size: str = Field(default="", max_length=40)
    quality: str = Field(default="auto", max_length=40)
    count: int = Field(default=1, ge=1, le=300)
    scene_type: str = Field(default="auto", alias="sceneType", max_length=100)
    platform: str = Field(default="general", max_length=80)
    preserve_subject: bool = Field(default=True, alias="preserveSubject")
    inherit_reference_images: bool = Field(default=False, alias="inheritReferenceImages")
    planner_model: str = Field(default="", alias="plannerModel", max_length=160)
    agent_engine: Literal["cowagent"] = Field(default="cowagent", alias="agentEngine")
    conversation_id: str = Field(default="", alias="conversationId", max_length=191)
    turn_id: str = Field(default="", alias="turnId", max_length=191)
    project_id: str = Field(default="", alias="projectId", max_length=191)
    brand_id: str = Field(default="", alias="brandId", max_length=191)
    folder_id: str = Field(default="", alias="folderId", max_length=191)
    use_long_term_memory: bool = Field(default=True, alias="useLongTermMemory")
    product: PromptAnalysisProduct | None = None
    images: list[ImageAgentReference] = Field(default_factory=list, max_length=4)
    conversation_context: list[ImageAgentConversationContext] = Field(default_factory=list, alias="conversationContext", max_length=6)

    class Config:
        populate_by_name = True


class ImageAgentResumeRequest(BaseModel):
    message: str = Field(default="", max_length=4000)
    images: list[ImageAgentReference] = Field(default_factory=list, max_length=4)
    folder_id: str = Field(default="", alias="folderId", max_length=191)

    class Config:
        populate_by_name = True


class ImageAgentBatchRetryRequest(BaseModel):
    model: str = Field(default="gpt-image-2", max_length=160)
    size: str = Field(default="", max_length=40)
    quality: str = Field(default="auto", max_length=40)


class ImageAgentMemoryCreateRequest(BaseModel):
    content: str = Field(min_length=1, max_length=12000)
    category: str = Field(default="note", max_length=48)
    scope: Literal["user", "brand", "project", "conversation"] = "user"
    scope_id: str = Field(default="", alias="scopeId", max_length=191)
    conversation_id: str = Field(default="", alias="conversationId", max_length=191)

    class Config:
        populate_by_name = True


class ImageAgentMemoryUpdateRequest(BaseModel):
    content: str = Field(min_length=1, max_length=12000)
    category: str = Field(default="note", max_length=48)
    scope: Literal["user", "brand", "project", "conversation"] = "user"
    scope_id: str = Field(default="", alias="scopeId", max_length=191)

    class Config:
        populate_by_name = True


class ImageAgentMemoryReviewRequest(BaseModel):
    decision: Literal["approve", "reject"]


def create_router() -> APIRouter:
    router = APIRouter()

    @router.post("/api/image-agent/runs")
    async def start_image_agent(
        body: ImageAgentRequest,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        usable_images = _usable_agent_images(body.images)
        if not body.prompt.strip() and not usable_images and not body.folder_id.strip():
            raise HTTPException(status_code=400, detail={"error": "prompt or reference image is required"})
        if (
            body.mode == "edit"
            and not usable_images
            and not body.folder_id.strip()
            and not body.inherit_reference_images
        ):
            raise HTTPException(status_code=400, detail={"error": "reference images are required for edit mode"})
        if body.folder_id.strip():
            owner_id = str(identity.get("id") or identity.get("username") or "anonymous")
            folder = await run_in_threadpool(
                professional_folder_asset_service.get_folder,
                body.folder_id,
                owner_id=owner_id,
                include_items=False,
            )
            if folder is None:
                raise HTTPException(status_code=404, detail={"error": "folder asset not found"})
        payload = body.model_dump(mode="python", by_alias=False)
        payload["images"] = [image.model_dump(mode="python", by_alias=False) for image in usable_images]
        payload["conversation_context"] = [item.model_dump(mode="python", by_alias=False) for item in body.conversation_context]
        try:
            return await run_in_threadpool(
                start_cow_agent_run,
                payload,
                identity=identity,
                base_url=resolve_image_base_url(request),
            )
        except HTTPException:
            raise
        except (AgentConversationBusy, ProfessionalAgentConversationBusy) as exc:
            raise HTTPException(status_code=409, detail={"error": str(exc)}) from exc
        except AgentQueueFull as exc:
            raise HTTPException(status_code=429, detail={"error": str(exc)}) from exc
        except AgentQueueUnavailable as exc:
            raise HTTPException(status_code=503, detail={"error": str(exc)}) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail={"error": str(exc)}) from exc

    @router.post("/api/image-agent/runs/{run_id}/resume")
    async def resume_image_agent(
        run_id: str,
        body: ImageAgentResumeRequest,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        usable_images = _usable_agent_images(body.images)
        try:
            resumed = await run_in_threadpool(
                resume_cow_agent_run,
                run_id,
                body.message,
                identity=identity,
                images=[image.model_dump(mode="python", by_alias=False) for image in usable_images],
                folder_id=body.folder_id,
                base_url=resolve_image_base_url(request),
            )
            if resumed is None:
                raise KeyError("agent run not found")
            return resumed
        except KeyError as exc:
            raise HTTPException(status_code=404, detail={"error": str(exc)}) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail={"error": str(exc)}) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail={"error": str(exc)}) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail={"error": str(exc)}) from exc

    @router.post("/api/image-agent/folders")
    async def upload_agent_folder(
        request: Request,
        files: list[UploadFile] = File(...),
        relative_names: list[str] = Form(default=[]),
        folder_name: str = Form(default="上传文件夹"),
        conversation_id: str = Form(default=""),
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        if not files or len(files) > FOLDER_MAX_ITEMS:
            raise HTTPException(status_code=400, detail={"error": f"folder image count must be between 1 and {FOLDER_MAX_ITEMS}"})
        payloads: list[tuple[bytes, str, str, str]] = []
        total_bytes = 0
        try:
            for index, item in enumerate(files):
                payload = await item.read()
                filename = str(item.filename or "image.png")
                relative_name = relative_names[index] if index < len(relative_names) else filename
                if not payload:
                    continue
                if len(payload) > FOLDER_MAX_FILE_BYTES:
                    raise HTTPException(status_code=400, detail={"error": f"{filename} exceeds the 50MB file limit"})
                total_bytes += len(payload)
                if total_bytes > FOLDER_MAX_TOTAL_BYTES:
                    raise HTTPException(status_code=400, detail={"error": "folder exceeds the 500MB total size limit"})
                payloads.append((payload, filename, str(item.content_type or "image/png"), relative_name))
        finally:
            for item in files:
                await item.close()
        if not payloads:
            raise HTTPException(status_code=400, detail={"error": "folder contains no files"})
        try:
            owner_id = str(identity.get("id") or identity.get("username") or "anonymous")
            return await run_in_threadpool(
                professional_folder_asset_service.create_folder,
                owner_id=owner_id,
                conversation_id=conversation_id,
                name=folder_name,
                files=payloads,
                base_url=resolve_image_base_url(request),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail={"error": f"folder upload failed: {exc}"}) from exc

    @router.get("/api/image-agent/folders")
    async def list_agent_folders(
        conversation_id: str = Query(default="", alias="conversationId", max_length=191),
        limit: int = Query(default=50, ge=1, le=200),
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        owner_id = str(identity.get("id") or identity.get("username") or "anonymous")
        return {
            "items": await run_in_threadpool(
                professional_folder_asset_service.list_folders,
                owner_id=owner_id,
                conversation_id=conversation_id,
                limit=limit,
            )
        }

    @router.get("/api/image-agent/folders/{folder_id}")
    async def get_agent_folder(folder_id: str, authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        owner_id = str(identity.get("id") or identity.get("username") or "anonymous")
        folder = await run_in_threadpool(professional_folder_asset_service.get_folder, folder_id, owner_id=owner_id, include_items=True)
        if folder is None:
            raise HTTPException(status_code=404, detail={"error": "folder asset not found"})
        return folder

    @router.delete("/api/image-agent/folders/{folder_id}")
    async def delete_agent_folder(folder_id: str, authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        owner_id = str(identity.get("id") or identity.get("username") or "anonymous")
        deleted = await run_in_threadpool(professional_folder_asset_service.delete_folder, folder_id, owner_id=owner_id)
        if not deleted:
            raise HTTPException(status_code=404, detail={"error": "folder asset not found"})
        return {"ok": True}

    @router.get("/api/image-agent/batch-plans/{plan_id}")
    async def get_agent_batch_plan(plan_id: str, authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        owner_id = str(identity.get("id") or identity.get("username") or "anonymous")
        plan = await run_in_threadpool(professional_folder_asset_service.get_batch_plan, plan_id, owner_id=owner_id)
        if plan is None:
            raise HTTPException(status_code=404, detail={"error": "batch plan not found"})
        return plan

    @router.post("/api/image-agent/batch-plans/{plan_id}/items/{item_id}/retry")
    async def retry_agent_batch_item(
        plan_id: str,
        item_id: int,
        body: ImageAgentBatchRetryRequest,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        owner_id = str(identity.get("id") or identity.get("username") or "anonymous")
        try:
            return await run_in_threadpool(
                professional_folder_asset_service.retry_batch_item,
                plan_id,
                item_id,
                owner_id=owner_id,
                identity=identity,
                base_url=resolve_image_base_url(request),
                model=body.model,
                size=body.size,
                quality=body.quality,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail={"error": str(exc)}) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail={"error": str(exc)}) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail={"error": str(exc)}) from exc

    @router.get("/api/image-agent/memory")
    async def list_image_agent_memory(
        conversation_id: str = Query(default="", alias="conversationId", max_length=191),
        project_id: str = Query(default="", alias="projectId", max_length=191),
        brand_id: str = Query(default="", alias="brandId", max_length=191),
        include_pending: bool = Query(default=False, alias="includePending"),
        limit: int = Query(default=80, ge=1, le=200),
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        from services.ecommerce.ecommerce_agent_memory_service import ecommerce_agent_memory_service

        owner_id = str(identity.get("id") or identity.get("username") or "anonymous")
        items = await run_in_threadpool(
            ecommerce_agent_memory_service.list_long_term_memories,
            owner_id=owner_id,
            conversation_id=conversation_id,
            project_id=project_id,
            brand_id=brand_id,
            limit=limit,
            include_pending=include_pending,
        )
        return {
            "items": items,
            "counts": {
                "active": sum(1 for item in items if item.get("status") == "active" and item.get("confirmed")),
                "pendingReview": sum(1 for item in items if item.get("status") == "pending_review"),
            },
        }

    @router.post("/api/image-agent/memory")
    async def create_image_agent_memory(
        body: ImageAgentMemoryCreateRequest,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        from services.ecommerce.ecommerce_agent_memory_service import ecommerce_agent_memory_service

        owner_id = str(identity.get("id") or identity.get("username") or "anonymous")
        scope_id = body.scope_id
        if body.scope in {"conversation", "project"} and not scope_id:
            scope_id = body.conversation_id
        try:
            item = await run_in_threadpool(
                ecommerce_agent_memory_service.upsert_long_term_memory,
                owner_id=owner_id,
                content=body.content,
                category=body.category,
                scope=body.scope,
                scope_id=scope_id,
                source_conversation_id=body.conversation_id,
                metadata={"manual": True},
                confidence=1.0,
                confirmed=True,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        return {"item": item}

    @router.patch("/api/image-agent/memory/{memory_id}")
    async def update_image_agent_memory(
        memory_id: int,
        body: ImageAgentMemoryUpdateRequest,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        from services.ecommerce.ecommerce_agent_memory_service import ecommerce_agent_memory_service

        owner_id = str(identity.get("id") or identity.get("username") or "anonymous")
        try:
            item = await run_in_threadpool(
                ecommerce_agent_memory_service.update_long_term_memory,
                owner_id=owner_id,
                memory_id=memory_id,
                content=body.content,
                category=body.category,
                scope=body.scope,
                scope_id=body.scope_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail={"error": str(exc)}) from exc
        if item is None:
            raise HTTPException(status_code=404, detail={"error": "memory not found"})
        return {"item": item}

    @router.post("/api/image-agent/memory/{memory_id}/review")
    async def review_image_agent_memory(
        memory_id: int,
        body: ImageAgentMemoryReviewRequest,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        from services.ecommerce.ecommerce_agent_memory_service import ecommerce_agent_memory_service

        owner_id = str(identity.get("id") or identity.get("username") or "anonymous")
        try:
            item = await run_in_threadpool(
                ecommerce_agent_memory_service.review_long_term_memory,
                owner_id=owner_id,
                memory_id=memory_id,
                decision=body.decision,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        if item is None:
            raise HTTPException(status_code=404, detail={"error": "memory not found"})
        return {"item": item}

    @router.delete("/api/image-agent/memory/{memory_id}")
    async def delete_image_agent_memory(
        memory_id: int,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        from services.ecommerce.ecommerce_agent_memory_service import ecommerce_agent_memory_service

        owner_id = str(identity.get("id") or identity.get("username") or "anonymous")
        deleted = await run_in_threadpool(
            ecommerce_agent_memory_service.delete_long_term_memory,
            owner_id=owner_id,
            memory_id=memory_id,
        )
        if not deleted:
            raise HTTPException(status_code=404, detail={"error": "memory not found"})
        return {"ok": True}

    @router.delete("/api/image-agent/memory")
    async def clear_image_agent_memory(
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        from services.ecommerce.ecommerce_agent_memory_service import ecommerce_agent_memory_service

        owner_id = str(identity.get("id") or identity.get("username") or "anonymous")
        deleted = await run_in_threadpool(
            ecommerce_agent_memory_service.delete_all_long_term_memories,
            owner_id=owner_id,
        )
        return {"ok": True, "deleted": deleted}

    return router
