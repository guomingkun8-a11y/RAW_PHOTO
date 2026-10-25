from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from api.support import require_admin, require_identity
from services.platform.content_filter import check_request
from services.platform.log_service import LoggedCall
from services.video.video_generation_service import video_generation_task_service
from services.canvas.canvas_task_limiter import CanvasTaskLimitError, CanvasTaskLimiterUnavailable
from services.ecommerce.video_analysis_queue_service import VideoAnalysisQueueUnavailable
from api.support import resolve_image_base_url
from services.ecommerce.professional_video_service import (
    VIDEO_MAX_FILE_BYTES,
    VIDEO_MAX_TOTAL_BYTES,
    ProfessionalVideoUploadError,
    professional_video_asset_service,
)
from services.platform.config import config
from services.video.composition.composition_service import video_composition_service


class VideoGenerationTaskRequest(BaseModel):
    client_task_id: str = Field(..., min_length=1, max_length=191)
    prompt: str = Field(..., min_length=1, max_length=12000)
    model: str = Field(..., min_length=1, max_length=191)
    mode: Literal["text_to_video", "image_to_video", "video_to_video"] | None = None
    aspect_ratio: str = Field(default="16:9", max_length=40)
    duration_secs: Annotated[int, Field(ge=1, le=60)] | Literal["auto"] = 5
    quality: str = Field(default="standard", max_length=80)
    resolution: str = Field(default="", max_length=80)
    image_urls: list[str] = Field(default_factory=list, max_length=30)
    reference_video_urls: list[str] = Field(default_factory=list, max_length=30)
    reference_audio_urls: list[str] = Field(default_factory=list, max_length=30)
    params: dict[str, Any] = Field(default_factory=dict)
    conversation_id: str = Field(default="", max_length=191)
    turn_id: str = Field(default="", max_length=191)
    source: Literal["standard", "canvas"] = "standard"
    canvas_units: int = Field(default=1, ge=1, le=8)


class VideoGenerationTaskQueryRequest(BaseModel):
    ids: list[str] = Field(default_factory=list, min_length=1, max_length=500)


class VideoGenerationAnalysisAssetRequest(BaseModel):
    conversation_id: str = Field(default="", max_length=191)


def _parse_task_ids(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/video-generation/models")
    async def list_video_generation_models(authorization: str | None = Header(default=None)):
        require_identity(authorization)
        settings = config.get_public_video_generation_settings()
        models = settings.get("models")
        has_api_key = bool(settings.get("has_api_key"))
        api_key_count = int(settings.get("api_key_count") or 0)
        configured = bool(str(settings.get("base_url") or "").strip() and (has_api_key or api_key_count > 0))
        image_upload = config.get_public_image_reference_upload_settings()
        video_upload = config.get_public_video_upload_settings()
        relay = config.get_public_openai_relay_settings()
        chat_enabled = bool(
            relay.get("enabled")
            and str(relay.get("base_url") or "").strip()
            and (relay.get("has_api_key") or int(relay.get("api_key_count") or 0) > 0)
        )
        return {
            "enabled": bool(settings.get("enabled")),
            "queue_enabled": bool(settings.get("queue_enabled")),
            "configured": configured,
            "has_api_key": has_api_key,
            "api_key_count": api_key_count,
            "chat_enabled": chat_enabled,
            "reference_image_upload_enabled": bool(image_upload.get("enabled")),
            "video_upload_enabled": bool(video_upload.get("enabled")),
            "models": models if isinstance(models, list) else [],
        }

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
                mode=body.mode or (
                    "video_to_video" if body.reference_video_urls
                    else "image_to_video" if body.image_urls
                    else "text_to_video"
                ),
                aspect_ratio=body.aspect_ratio,
                duration_secs=body.duration_secs,
                quality=body.quality,
                resolution=body.resolution,
                image_urls=body.image_urls,
                reference_video_urls=body.reference_video_urls,
                reference_audio_urls=body.reference_audio_urls,
                params=body.params,
                conversation_id=body.conversation_id,
                turn_id=body.turn_id,
                source=body.source,
                canvas_units=body.canvas_units if body.source == "canvas" else 1,
            )
        except (CanvasTaskLimitError, CanvasTaskLimiterUnavailable) as exc:
            raise HTTPException(status_code=int(getattr(exc, "status_code", 429)), detail={"error": str(exc)}) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.post("/api/video-generation/reference-videos")
    async def upload_video_generation_reference_videos(
        videos: list[UploadFile] = File(...),
        conversation_id: str = Form(default="", max_length=191),
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        if not videos or len(videos) > 10:
            raise HTTPException(status_code=400, detail={"error": "视频参考素材最多上传 10 个"})
        payloads: list[tuple[bytes, str, str]] = []
        total_bytes = 0
        try:
            for index, item in enumerate(videos, start=1):
                payload = await item.read()
                filename = str(item.filename or f"reference-{index}.mp4").strip() or f"reference-{index}.mp4"
                mime_type = str(item.content_type or "video/mp4").strip() or "video/mp4"
                if not payload:
                    raise HTTPException(status_code=400, detail={"error": f"{filename} 为空"})
                if len(payload) > VIDEO_MAX_FILE_BYTES:
                    raise HTTPException(status_code=400, detail={"error": f"{filename} 超过 300MB"})
                total_bytes += len(payload)
                if total_bytes > VIDEO_MAX_TOTAL_BYTES:
                    raise HTTPException(status_code=400, detail={"error": "视频参考素材总大小超过 600MB"})
                payloads.append((payload, filename, mime_type))
        finally:
            for item in videos:
                await item.close()
        owner_id = str(identity.get("id") or identity.get("username") or "anonymous")
        try:
            results = await run_in_threadpool(
                professional_video_asset_service.upload_many,
                owner_id=owner_id,
                conversation_id=conversation_id,
                videos=payloads,
                max_items=10,
            )
        except ProfessionalVideoUploadError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        return {
            "items": [item.to_public() for item in results],
            "total": len(results),
            "uploaded": sum(1 for item in results if not item.cached),
            "cache_hits": sum(1 for item in results if item.cached),
        }

    @router.post("/api/video-generation/reference-audios")
    async def upload_video_generation_reference_audios(
        request: Request,
        files: list[UploadFile] = File(...),
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        if not files or len(files) > 10:
            raise HTTPException(status_code=400, detail={"error": "音频参考素材最多上传 10 个"})
        max_bytes = video_composition_service.settings.max_upload_mb * 1024 * 1024
        items: list[dict[str, object]] = []
        try:
            for index, item in enumerate(files, start=1):
                filename = str(item.filename or f"reference-{index}.mp3").strip() or f"reference-{index}.mp3"
                payload = await item.read(max_bytes + 1)
                if len(payload) > max_bytes:
                    raise HTTPException(status_code=413, detail={"error": f"{filename} 不能超过 {video_composition_service.settings.max_upload_mb} MB"})
                try:
                    asset = await run_in_threadpool(
                        video_composition_service.storage.save_audio,
                        payload,
                        filename,
                        owner_id=str(identity.get("id") or identity.get("username") or "anonymous"),
                        base_url=resolve_image_base_url(request),
                    )
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
                items.append({
                    "id": asset.relative_path,
                    "title": filename,
                    "url": asset.url,
                    "storageRel": asset.relative_path,
                    "duration": asset.duration,
                    "size": asset.size,
                    "waveform": asset.waveform,
                })
        finally:
            for item in files:
                await item.close()
        return {"items": items, "total": len(items)}

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

    @router.post("/api/video-generation/tasks/{task_id}/analysis-asset")
    async def register_video_result_for_analysis(
        task_id: str,
        body: VideoGenerationAnalysisAssetRequest,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        try:
            return await run_in_threadpool(
                video_generation_task_service.register_result_for_analysis,
                identity,
                task_id,
                conversation_id=body.conversation_id,
                base_url=resolve_image_base_url(request),
            )
        except VideoAnalysisQueueUnavailable as exc:
            raise HTTPException(status_code=503, detail={"error": str(exc)}) from exc
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

