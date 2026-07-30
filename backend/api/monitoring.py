from __future__ import annotations

import json

from fastapi import APIRouter, Header
from fastapi.concurrency import run_in_threadpool

from api.support import require_admin
from services.cache_utils import TTLCache
from services.generation_monitoring_service import generation_monitoring_service
from services.image_task_service import image_task_service


_SUMMARY_CACHE = TTLCache[str, dict[str, object]](ttl_seconds=3.0, max_items=32)


def _summary_cache_key(tasks: list[dict[str, object]], queue_snapshot: dict[str, object]) -> str:
    normalized_tasks = sorted(
        (
            (
                str(task.get("owner_id") or ""),
                str(task.get("id") or ""),
                str(task.get("status") or ""),
                str(task.get("updated_at") or ""),
                str(task.get("duration_ms") or ""),
                str(task.get("error") or ""),
            )
            for task in tasks
            if isinstance(task, dict)
        ),
        key=lambda item: item,
    )
    try:
        return json.dumps(
            {"tasks": normalized_tasks, "snapshot": queue_snapshot or {}},
            ensure_ascii=False,
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        )
    except Exception:
        return str((normalized_tasks, queue_snapshot or {}))


def _build_summary() -> dict[str, object]:
    tasks = image_task_service.monitoring_task_events()
    queue_snapshot = image_task_service.monitoring_snapshot()
    cache_key = _summary_cache_key(tasks, queue_snapshot)
    cached = _SUMMARY_CACHE.get(cache_key)
    if cached is not None:
        return cached
    generation_monitoring_service.sync_task_events(tasks)
    result = generation_monitoring_service.summary(queue_snapshot)
    return _SUMMARY_CACHE.set(cache_key, result)


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/monitoring/summary")
    async def monitoring_summary(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return await run_in_threadpool(_build_summary)

    return router
