from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import Field

from api.support import require_admin, require_identity
from services.audio.audio_generation_task_service import audio_generation_task_service
from services.audio.generation_service import audio_generation_service
from services.audio.models import (
    AUDIO_GENERATION_MODEL,
    AudioGenerationModelName,
    AudioGenerationRequest,
)


class AudioGenerationTaskRequest(AudioGenerationRequest):
    client_task_id: str = Field(..., min_length=1, max_length=191)
    conversation_id: str = Field(default="", max_length=191)
    turn_id: str = Field(default="", max_length=191)


def _parse_task_ids(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def create_router() -> APIRouter:
    router = APIRouter(prefix="/api/audio-generation", tags=["audio-generation"])

    @router.get("/voices")
    async def list_voices(
        model: AudioGenerationModelName = Query(default=AUDIO_GENERATION_MODEL),
        authorization: str | None = Header(default=None),
    ):
        require_identity(authorization)
        try:
            return await run_in_threadpool(audio_generation_service.list_voices, model)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.get("/tasks")
    async def list_audio_generation_tasks(
        ids: str = Query(default=""),
        limit: int | None = Query(default=None, ge=1, le=500),
        all_owners: bool = Query(default=False),
        owner_id: str = Query(default=""),
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
        if cursor.strip():
            options["cursor"] = cursor.strip()
        if status.strip():
            options["status_filter"] = status.strip()
        if q.strip():
            options["query_filter"] = q.strip()
        try:
            return await run_in_threadpool(
                audio_generation_task_service.list_tasks,
                identity,
                _parse_task_ids(ids),
                **options,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.post("/tasks")
    async def create_audio_generation_task(
        body: AudioGenerationTaskRequest,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        try:
            return await run_in_threadpool(
                audio_generation_task_service.submit_task,
                identity,
                client_task_id=body.client_task_id,
                prompt=body.prompt,
                model=body.model,
                params=body.params.model_dump(exclude_none=True),
                conversation_id=body.conversation_id,
                turn_id=body.turn_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.post("/tasks/{task_id}/cancel")
    async def cancel_audio_generation_task(
        task_id: str,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        try:
            return await run_in_threadpool(audio_generation_task_service.cancel_task, identity, task_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.post("/tasks/{task_id}/reconcile")
    async def reconcile_audio_generation_task(
        task_id: str,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        try:
            return await run_in_threadpool(audio_generation_task_service.reconcile_task, identity, task_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.delete("/tasks/{task_id}")
    async def delete_audio_generation_task(
        task_id: str,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        try:
            return await run_in_threadpool(audio_generation_task_service.delete_task, identity, task_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.get("/queue")
    async def audio_generation_queue(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return await run_in_threadpool(audio_generation_task_service.monitoring_snapshot)

    return router
