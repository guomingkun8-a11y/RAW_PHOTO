from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from api.support import require_admin, require_identity
from services.platform.content_filter import check_request
from services.platform.log_service import LoggedCall
from services.video.video_generation_service import video_generation_task_service


class VideoGenerationTaskRequest(BaseModel):
    client_task_id: str = Field(..., min_length=1, max_length=191)
    prompt: str = Field(..., min_length=1, max_length=12000)
    model: str = Field(..., min_length=1, max_length=191)
    mode: Literal["text_to_video", "image_to_video"] = "text_to_video"
    aspect_ratio: str = Field(default="16:9", max_length=40)
    duration_secs: Annotated[int, Field(ge=1, le=60)] | Literal["auto"] = 5
    quality: str = Field(default="standard", max_length=80)
    resolution: str = Field(default="", max_length=80)
    image_urls: list[str] = Field(default_factory=list, max_length=30)
    params: dict[str, Any] = Field(default_factory=dict)
    conversation_id: str = Field(default="", max_length=191)
    turn_id: str = Field(default="", max_length=191)


class VideoGenerationTaskQueryRequest(BaseModel):
    ids: list[str] = Field(default_factory=list, min_length=1, max_length=500)


def _parse_task_ids(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/video-generation/tasks")
    async def list_video_generation_tasks(
        ids: str = Query(default=""),
        limit: int | None = Query(default=None, ge=1, le=500),
        all_owners: bool = Query(default=False),
        owner_id: str = Query(default=""),
        conversation_id: str = Query(default="", max_length=191),
        cursor: str = Query(default="", max_length=2048),
        status: str = Query(default="", pattern="^(|queued|running|success|error|canceled)$"),
        q: str = Query(default="", max_length=200),
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        options: dict[str, Any] = {}
        if limit is not None:
            options["limit"] = limit
        if all_owners:
            options["include_all_owners"] = True
        if owner_id.strip():
            options["owner_id_filter"] = owner_id.strip()
        if conversation_id.strip():
            options["conversation_id_filter"] = conversation_id.strip()
        if cursor.strip():
            options["cursor"] = cursor.strip()
        if status.strip():
            options["status_filter"] = status.strip()
        if q.strip():
            options["query_filter"] = q.strip()
        try:
            return await run_in_threadpool(
                video_generation_task_service.list_tasks,
                identity,
                _parse_task_ids(ids),
                **options,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.post("/api/video-generation/tasks/query")
    async def query_video_generation_tasks(
        body: VideoGenerationTaskQueryRequest,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        return await run_in_threadpool(video_generation_task_service.list_tasks, identity, body.ids)

    @router.post("/api/video-generation/tasks")
    async def create_video_generation_task(
        body: VideoGenerationTaskRequest,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        try:
            await run_in_threadpool(check_request, body.prompt)
        except HTTPException as exc:
            LoggedCall(identity, "/api/video-generation/tasks", body.model, "video generation", request_text=body.prompt).log(
                "call failed",
                status="failed",
                error=str(exc.detail),
            )
            raise
        try:
            return await run_in_threadpool(
                video_generation_task_service.submit_task,
                identity,
                client_task_id=body.client_task_id,
                prompt=body.prompt,
                model=body.model,
                mode=body.mode,
                aspect_ratio=body.aspect_ratio,
                duration_secs=body.duration_secs,
                quality=body.quality,
                resolution=body.resolution,
                image_urls=body.image_urls,
                params=body.params,
                conversation_id=body.conversation_id,
                turn_id=body.turn_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.post("/api/video-generation/tasks/{task_id}/cancel")
    async def cancel_video_generation_task(
        task_id: str,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        try:
            return await run_in_threadpool(video_generation_task_service.cancel_task, identity, task_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.post("/api/video-generation/tasks/{task_id}/reconcile")
    async def reconcile_video_generation_task(
        task_id: str,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        try:
            return await run_in_threadpool(video_generation_task_service.reconcile_task, identity, task_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.delete("/api/video-generation/tasks/{task_id}")
    async def delete_video_generation_task(
        task_id: str,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        try:
            return await run_in_threadpool(video_generation_task_service.delete_task, identity, task_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.delete("/api/video-generation/conversations/{conversation_id}")
    async def delete_video_generation_conversation(
        conversation_id: str,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        try:
            return await run_in_threadpool(video_generation_task_service.delete_conversation, identity, conversation_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.get("/api/video-generation/queue")
    async def video_generation_queue(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return await run_in_threadpool(video_generation_task_service.monitoring_snapshot)

    return router

