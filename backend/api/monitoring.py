from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.concurrency import run_in_threadpool

from api.support import require_admin
from services.ecommerce.agent_queue_service import agent_queue_service
from services.image.generation_monitoring_service import generation_monitoring_service
from services.image.image_task_service import image_task_service
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


def _build_summary(start_at: datetime | None = None, end_at: datetime | None = None) -> dict[str, object]:
    tasks = image_task_service.monitoring_task_events()
    queue_snapshot = image_task_service.monitoring_snapshot()
    generation_monitoring_service.sync_task_events(tasks)
    summary = generation_monitoring_service.summary(queue_snapshot, start_at=start_at, end_at=end_at)
    try:
        summary["agent_queue"] = agent_queue_service.snapshot()
    except Exception as exc:
        summary["agent_queue"] = {
            "enabled": bool(agent_queue_service.settings.enabled),
            "available": False,
            "error": str(exc)[:300],
        }
    try:
        summary["video_generation_queue"] = video_generation_task_service.monitoring_snapshot()
    except Exception as exc:
        summary["video_generation_queue"] = {
            "enabled": False,
            "available": False,
            "error": str(exc)[:300],
        }
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
        authorization: str | None = Header(default=None),
    ):
        require_admin(authorization)
        start_at, end_at = _validated_range(start_at_value, end_at_value)
        return await run_in_threadpool(_build_summary, start_at, end_at)

    @router.get("/api/monitoring/tasks")
    async def monitoring_tasks(
        start_at_value: str = Query(default="", alias="startAt", max_length=40),
        end_at_value: str = Query(default="", alias="endAt", max_length=40),
        owner_id: str = Query(default="", alias="ownerId", max_length=191),
        status: str = Query(default="all", pattern="^(all|success|error)$"),
        limit: int = Query(default=100, ge=1, le=500),
        include_references: bool = Query(default=False, alias="includeReferences"),
        authorization: str | None = Header(default=None),
    ):
        require_admin(authorization)
        start_at, end_at = _validated_range(start_at_value, end_at_value)
        return await run_in_threadpool(
            generation_monitoring_service.task_details,
            start_at=start_at,
            end_at=end_at,
            owner_id=owner_id,
            status=status,
            limit=limit,
            include_references=include_references,
        )

    return router
