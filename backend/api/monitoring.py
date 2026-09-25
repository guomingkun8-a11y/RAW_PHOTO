from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.concurrency import run_in_threadpool

from api.support import require_admin
from services.audio.audio_generation_monitoring_service import audio_generation_monitoring_service
from services.audio.audio_generation_task_service import audio_generation_task_service
from services.ecommerce.agent_queue_service import agent_queue_service
from services.image.generation_monitoring_service import generation_monitoring_service
from services.image.image_task_service import image_task_service
from services.video.video_generation_monitoring_service import video_generation_monitoring_service
from services.video.video_generation_service import video_generation_task_service


def _parse_range_datetime(value: str, field: str) -> datetime | None:
    clean_value = str(value or "").strip()
    if not clean_value:
        return None
    try:
        parsed = datetime.fromisoformat(clean_value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": f"invalid {field}"}) from exc
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def _build_summary(
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    source: str = "image",
) -> dict[str, object]:
    normalized_source = source if source in {"image", "video", "audio"} else "image"
    if normalized_source == "video":
        try:
            queue_snapshot = video_generation_task_service.monitoring_snapshot()
        except Exception as exc:
            queue_snapshot = {
                "enabled": False,
                "queue_enabled": False,
                "available": False,
                "error": str(exc)[:300],
            }
        summary = video_generation_monitoring_service.summary(
            queue_snapshot,
            start_at=start_at,
            end_at=end_at,
        )
        summary["video_generation_queue"] = queue_snapshot
    elif normalized_source == "audio":
        try:
            queue_snapshot = audio_generation_task_service.monitoring_snapshot()
        except Exception as exc:
            queue_snapshot = {
                "enabled": False,
                "queue_enabled": False,
                "available": False,
                "error": str(exc)[:300],
            }
        summary = audio_generation_monitoring_service.summary(
            queue_snapshot,
            start_at=start_at,
            end_at=end_at,
        )
        summary["audio_generation_queue"] = queue_snapshot
    else:
        queue_snapshot = image_task_service.monitoring_snapshot()
        summary = generation_monitoring_service.summary(queue_snapshot, start_at=start_at, end_at=end_at)
        summary["source"] = "image"
    try:
        summary["agent_queue"] = agent_queue_service.snapshot()
    except Exception as exc:
        summary["agent_queue"] = {
            "enabled": bool(agent_queue_service.settings.enabled),
            "available": False,
            "error": str(exc)[:300],
        }
    if normalized_source == "image":
        try:
            summary["video_generation_queue"] = video_generation_task_service.monitoring_snapshot()
        except Exception as exc:
            summary["video_generation_queue"] = {
                "enabled": False,
                "available": False,
                "error": str(exc)[:300],
            }
    summary["source"] = normalized_source
    return summary


def _validated_range(start_at_value: str, end_at_value: str) -> tuple[datetime | None, datetime | None]:
    start_at = _parse_range_datetime(start_at_value, "startAt")
    end_at = _parse_range_datetime(end_at_value, "endAt")
    if start_at and end_at and start_at >= end_at:
        raise HTTPException(status_code=400, detail={"error": "startAt must be earlier than endAt"})
    return start_at, end_at


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/monitoring/summary")
    async def monitoring_summary(
        start_at_value: str = Query(default="", alias="startAt", max_length=40),
        end_at_value: str = Query(default="", alias="endAt", max_length=40),
        source: str = Query(default="image", pattern="^(image|video|audio)$"),
        authorization: str | None = Header(default=None),
    ):
        require_admin(authorization)
        start_at, end_at = _validated_range(start_at_value, end_at_value)
        if source in {"video", "audio"}:
            return await run_in_threadpool(_build_summary, start_at, end_at, source)
        return await run_in_threadpool(_build_summary, start_at, end_at)

    @router.get("/api/monitoring/tasks")
    async def monitoring_tasks(
        start_at_value: str = Query(default="", alias="startAt", max_length=40),
        end_at_value: str = Query(default="", alias="endAt", max_length=40),
        owner_id: str = Query(default="", alias="ownerId", max_length=191),
        status: str = Query(default="all", pattern="^(all|success|error|canceled|queued|running)$"),
        source: str = Query(default="image", pattern="^(image|video|audio)$"),
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        query_text: str = Query(default="", alias="q", max_length=191),
        cost_only: bool = Query(default=False, alias="costOnly"),
        include_references: bool = Query(default=False, alias="includeReferences"),
        cursor_at_value: str = Query(default="", alias="cursorAt", max_length=40),
        cursor_key: str = Query(default="", alias="cursorKey", max_length=383),
        authorization: str | None = Header(default=None),
    ):
        require_admin(authorization)
        start_at, end_at = _validated_range(start_at_value, end_at_value)
        if bool(cursor_at_value) != bool(cursor_key):
            raise HTTPException(status_code=400, detail={"error": "cursorAt and cursorKey must be provided together"})
        cursor = None
        if cursor_at_value:
            cursor_at = _parse_range_datetime(cursor_at_value, "cursorAt")
            cursor = {"event_at": cursor_at.isoformat(), "task_key": cursor_key}
        task_service = {
            "video": video_generation_monitoring_service,
            "audio": audio_generation_monitoring_service,
        }.get(source, generation_monitoring_service)
        return await run_in_threadpool(
            task_service.task_details,
            start_at=start_at,
            end_at=end_at,
            owner_id=owner_id,
            status=status,
            limit=limit,
            offset=offset,
            query_text=query_text,
            cost_only=cost_only,
            include_references=include_references,
            cursor=cursor,
        )

    return router
