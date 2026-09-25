from __future__ import annotations

import json
from collections.abc import Iterator

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api.support import require_identity
from services.platform.content_filter import check_request
from services.video.video_agent_service import DEFAULT_MEDIA_ANALYSIS_PROMPT, video_agent_message_service


class VideoAgentImageAttachment(BaseModel):
    name: str = Field(default="image", max_length=191)
    filename: str = Field(default="", max_length=191)
    type: str = Field(default="image/jpeg", max_length=120)
    mime_type: str = Field(default="", alias="mimeType", max_length=120)
    url: str = Field(default="", max_length=4000)
    sha256: str = Field(default="", max_length=64)
    size: int = Field(default=0, ge=0)

    class Config:
        populate_by_name = True


class VideoAgentVideoAttachment(BaseModel):
    video_id: str = Field(default="", alias="videoId", max_length=191)
    name: str = Field(default="video.mp4", max_length=191)
    filename: str = Field(default="", max_length=191)
    type: str = Field(default="video/mp4", max_length=120)
    mime_type: str = Field(default="", alias="mimeType", max_length=120)
    url: str = Field(default="", max_length=4000)
    size: int = Field(default=0, ge=0)
    sha256: str = Field(default="", max_length=64)

    class Config:
        populate_by_name = True


class VideoAgentPlanRequest(BaseModel):
    prompt: str = Field(default="", max_length=12000)
    conversation_id: str = Field(default="", max_length=191)
    turn_id: str = Field(default="", max_length=191)
    reasoning: bool = False
    images: list[VideoAgentImageAttachment] = Field(default_factory=list, max_length=8)
    videos: list[VideoAgentVideoAttachment] = Field(default_factory=list, max_length=4)


def _error_message(exc: Exception) -> str:
    if isinstance(exc, HTTPException):
        detail = exc.detail
        if isinstance(detail, str):
            return detail
        if isinstance(detail, dict):
            error = detail.get("error")
            if isinstance(error, str):
                return error
            if isinstance(error, dict) and isinstance(error.get("message"), str):
                return str(error["message"])
    return str(exc).strip() or "推理请求失败"


def _sse_events(events: Iterator[dict[str, object]]) -> Iterator[str]:
    try:
        for event in events:
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
    except Exception as exc:
        yield f"data: {json.dumps({'type': 'error', 'message': _error_message(exc)}, ensure_ascii=False)}\n\n"
    finally:
        close = getattr(events, "close", None)
        if callable(close):
            close()


def _request_prompt(body: VideoAgentPlanRequest) -> str:
    prompt = body.prompt.strip()
    if prompt:
        return prompt
    if body.images or body.videos:
        return DEFAULT_MEDIA_ANALYSIS_PROMPT
    raise HTTPException(status_code=400, detail={"error": "prompt or media attachment is required"})


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/video-agent/plans")
    async def list_video_agent_plans(
        conversation_id: str = Query(default="", max_length=191),
        limit: int = Query(default=200, ge=1, le=500),
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        return await run_in_threadpool(
            video_agent_message_service.list_messages,
            identity=identity,
            conversation_id=conversation_id,
            limit=limit,
        )

    @router.delete("/api/video-agent/plans/{plan_id}")
    async def delete_video_agent_plan(
        plan_id: str,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        try:
            deleted = await run_in_threadpool(
                video_agent_message_service.delete_message,
                identity=identity,
                message_id=plan_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        if not deleted:
            raise HTTPException(status_code=404, detail={"error": "video agent record not found"})
        return {"ok": True, "deleted": deleted}

    @router.delete("/api/video-agent/conversations/{conversation_id}")
    async def delete_video_agent_conversation(
        conversation_id: str,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        try:
            deleted = await run_in_threadpool(
                video_agent_message_service.delete_conversation,
                identity=identity,
                conversation_id=conversation_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        if not deleted:
            raise HTTPException(status_code=404, detail={"error": "video agent conversation not found"})
        return {"ok": True, "deleted": deleted}

    @router.post("/api/video-agent/plans")
    async def create_video_agent_plan(
        body: VideoAgentPlanRequest,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        prompt = _request_prompt(body)
        try:
            await run_in_threadpool(check_request, prompt)
        except HTTPException:
            raise
        return await run_in_threadpool(
            video_agent_message_service.create_message,
            identity=identity,
            prompt=prompt,
            conversation_id=body.conversation_id,
            turn_id=body.turn_id,
            images=[item.model_dump(mode="python", by_alias=False) for item in body.images],
            videos=[item.model_dump(mode="python", by_alias=False) for item in body.videos],
            reasoning_enabled=body.reasoning,
        )

    @router.post("/api/video-agent/plans/stream")
    async def create_video_agent_reasoning_plan(
        body: VideoAgentPlanRequest,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        prompt = _request_prompt(body)
        if not body.reasoning:
            raise HTTPException(status_code=400, detail={"error": "reasoning must be enabled for this endpoint"})
        try:
            await run_in_threadpool(check_request, prompt)
        except HTTPException:
            raise
        events = await run_in_threadpool(
            video_agent_message_service.create_reasoning_message_stream,
            identity=identity,
            prompt=prompt,
            conversation_id=body.conversation_id,
            turn_id=body.turn_id,
            images=[item.model_dump(mode="python", by_alias=False) for item in body.images],
            videos=[item.model_dump(mode="python", by_alias=False) for item in body.videos],
        )
        return StreamingResponse(
            _sse_events(events),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
            },
        )

    return router
