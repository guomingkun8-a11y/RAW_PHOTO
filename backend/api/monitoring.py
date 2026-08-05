from __future__ import annotations

from fastapi import APIRouter, Header
from fastapi.concurrency import run_in_threadpool

from api.support import require_admin
from services.generation_monitoring_service import generation_monitoring_service
from services.image_task_service import image_task_service


def _build_summary() -> dict[str, object]:
    tasks = image_task_service.monitoring_task_events()
    queue_snapshot = image_task_service.monitoring_snapshot()
    generation_monitoring_service.sync_task_events(tasks)
    return generation_monitoring_service.summary(queue_snapshot)


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/monitoring/summary")
    async def monitoring_summary(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return await run_in_threadpool(_build_summary)

    return router
