from __future__ import annotations

import mimetypes

from fastapi import APIRouter, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse

from api.support import require_admin, require_identity, resolve_image_base_url
from services.video.composition.composition_service import video_composition_service
from services.video.composition.ai_media_service import VideoCompositionAIMediaService
from services.video.composition.models import AudioTranscriptionRequest, VideoCompositionTaskRequest, VoiceoverGenerationRequest
from services.platform.log_service import AuxiliaryCallLog
from services.canvas.canvas_task_limiter import CanvasTaskLimitError, CanvasTaskLimiterUnavailable


def create_router() -> APIRouter:
    router = APIRouter(prefix="/api/video-compositions", tags=["video-compositions"])

    def ai_service() -> VideoCompositionAIMediaService:
        return VideoCompositionAIMediaService(
            video_composition_service.settings,
            video_composition_service.storage,
        )

    @router.get("/capabilities")
    async def composition_capabilities(authorization: str | None = Header(default=None)):
        require_identity(authorization)
        return await run_in_threadpool(ai_service().capabilities)

    @router.post("/audio-assets")
    async def upload_audio_asset(
        request: Request,
        file: UploadFile = File(...),
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        filename = str(file.filename or "audio")
        max_bytes = video_composition_service.settings.max_upload_mb * 1024 * 1024
        payload = await file.read(max_bytes + 1)
        if len(payload) > max_bytes:
            raise HTTPException(status_code=413, detail={"error": f"音频文件不能超过 {video_composition_service.settings.max_upload_mb} MB"})
        try:
            asset = await run_in_threadpool(
                video_composition_service.storage.save_audio,
                payload,
                filename,
                owner_id=str(identity.get("id") or "anonymous"),
                base_url=resolve_image_base_url(request),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        return {
            "id": asset.relative_path,
            "title": filename,
            "url": asset.url,
            "storageRel": asset.relative_path,
            "duration": asset.duration,
            "size": asset.size,
            "waveform": asset.waveform,
        }

    @router.post("/voiceovers")
    async def generate_voiceover(
        request: Request,
        body: VoiceoverGenerationRequest,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        service = ai_service()
        model = str(body.model or service.settings.tts_model)
        call_log = AuxiliaryCallLog(
            operation="video_composition_tts",
            identity=identity,
            model=model,
            workflow_id=body.workflow_id,
            node_id=body.node_id,
        )
        try:
            asset = await run_in_threadpool(
                service.generate_voiceover,
                text=body.text,
                voice=body.voice,
                model=body.model,
                speech_rate=body.speech_rate,
                emotion=body.emotion,
                emotion_scale=body.emotion_scale,
                owner_id=str(identity.get("id") or "anonymous"),
                base_url=resolve_image_base_url(request),
            )
        except ValueError as exc:
            call_log.failure(exc, metadata={"text_chars": len(body.text), "voice": body.voice})
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        except Exception as exc:
            call_log.failure(
                exc,
                upstream_ids=getattr(exc, "upstream_task_ids", None),
                metadata={"text_chars": len(body.text), "voice": body.voice},
            )
            raise
        call_metadata = asset.metadata if isinstance(asset.metadata, dict) else {}
        call_log.success(
            cost=call_metadata.get("cost"),
            upstream_ids=call_metadata.get("upstream_ids"),
            usage=call_metadata.get("usage"),
            metadata={"text_chars": len(body.text), "voice": body.voice},
        )
        return {
            "id": asset.relative_path,
            "title": "AI 旁白",
            "url": asset.url,
            "storageRel": asset.relative_path,
            "duration": asset.duration,
            "size": asset.size,
            "waveform": asset.waveform,
            "script": body.text,
            "voice": body.voice,
        }

    @router.post("/transcriptions")
    async def transcribe_audio(
        body: AudioTranscriptionRequest,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        service = ai_service()
        call_log = AuxiliaryCallLog(
            operation="audio_transcription",
            identity=identity,
            model=service.settings.asr_model,
            workflow_id=body.workflow_id,
            node_id=body.node_id,
            media_id=body.storage_rel,
        )
        try:
            result = await run_in_threadpool(
                service.transcribe,
                storage_rel=body.storage_rel,
                owner_id=str(identity.get("id") or "anonymous"),
                language=body.language,
                prompt=body.prompt,
                offset=body.offset,
            )
        except ValueError as exc:
            call_log.failure(exc, metadata={"language": body.language})
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        except Exception as exc:
            call_log.failure(exc, metadata={"language": body.language})
            raise
        call_metadata = result.pop("_call_metadata", {}) if isinstance(result, dict) else {}
        call_log.success(
            cost=call_metadata.get("cost") if isinstance(call_metadata, dict) else None,
            upstream_ids=call_metadata.get("upstream_ids") if isinstance(call_metadata, dict) else None,
            usage=call_metadata.get("usage") if isinstance(call_metadata, dict) else None,
            metadata={
                "language": body.language,
                "cue_count": len(result.get("cues") or []) if isinstance(result, dict) else 0,
            },
        )
        return result

    @router.get("/assets/{asset_path:path}", include_in_schema=False)
    async def get_composition_asset(asset_path: str):
        path = video_composition_service.storage.local_file(asset_path)
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return FileResponse(path, media_type=media_type)

    @router.post("/tasks")
    async def create_composition_task(
        request: Request,
        body: VideoCompositionTaskRequest,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        try:
            return await run_in_threadpool(
                video_composition_service.submit_task,
                identity,
                client_task_id=body.client_task_id,
                workflow_id=body.workflow_id,
                timeline=body.timeline,
                base_url=resolve_image_base_url(request),
                source=body.source,
                canvas_units=body.canvas_units,
            )
        except (CanvasTaskLimitError, CanvasTaskLimiterUnavailable) as exc:
            raise HTTPException(status_code=int(getattr(exc, "status_code", 429)), detail={"error": str(exc)}) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.get("/tasks")
    async def list_composition_tasks(
        workflow_id: str = Query(default="", max_length=191),
        limit: int = Query(default=50, ge=1, le=200),
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        return await run_in_threadpool(
            video_composition_service.list_tasks,
            identity,
            workflow_id=workflow_id,
            limit=limit,
        )

    @router.get("/tasks/{task_id}")
    async def get_composition_task(task_id: str, authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        task = await run_in_threadpool(video_composition_service.get_task, identity, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail={"error": "composition task not found"})
        return task

    @router.delete("/tasks/{task_id}")
    async def delete_composition_task(task_id: str, authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        try:
            deleted = await run_in_threadpool(video_composition_service.delete_task, identity, task_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail={"error": str(exc)}) from exc
        if not deleted:
            raise HTTPException(status_code=404, detail={"error": "composition task not found"})
        return {"ok": True}

    @router.post("/tasks/{task_id}/cancel")
    async def cancel_composition_task(task_id: str, authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        try:
            task = await run_in_threadpool(video_composition_service.cancel_task, identity, task_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail={"error": str(exc)}) from exc
        if task is None:
            raise HTTPException(status_code=404, detail={"error": "composition task not found"})
        return task

    @router.post("/tasks/{task_id}/retry")
    async def retry_composition_task(task_id: str, authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        try:
            task = await run_in_threadpool(video_composition_service.retry_task, identity, task_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail={"error": str(exc)}) from exc
        if task is None:
            raise HTTPException(status_code=404, detail={"error": "composition task not found"})
        return task

    @router.get("/queue")
    async def composition_queue(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return await run_in_threadpool(video_composition_service.monitoring_snapshot)

    return router
