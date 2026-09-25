from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.concurrency import run_in_threadpool

from api.support import require_admin
from services.billing.upstream_usage_service import (
    UpstreamUsageError,
    UpstreamUsageNotConfigured,
    UpstreamUsageSyncInProgress,
    upstream_usage_service,
)


def _parse_range_datetime(value: str, field: str) -> datetime | None:
    clean_value = str(value or "").strip()
    if not clean_value:
        return None
    try:
        parsed = datetime.fromisoformat(clean_value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": f"invalid {field}"}) from exc
    return parsed.astimezone().replace(tzinfo=None) if parsed.tzinfo else parsed


def _validated_range(start_value: str, end_value: str) -> tuple[datetime | None, datetime | None]:
    start_at = _parse_range_datetime(start_value, "startAt")
    end_at = _parse_range_datetime(end_value, "endAt")
    if start_at and end_at and start_at >= end_at:
        raise HTTPException(status_code=400, detail={"error": "startAt must be earlier than endAt"})
    return start_at, end_at


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/billing/summary")
    async def billing_summary(
        source: str = Query(default="all", pattern="^(all|image|video|chat|audio)$"),
        start_value: str = Query(default="", alias="startAt", max_length=40),
        end_value: str = Query(default="", alias="endAt", max_length=40),
        authorization: str | None = Header(default=None),
    ):
        require_admin(authorization)
        start_at, end_at = _validated_range(start_value, end_value)
        return await run_in_threadpool(
            upstream_usage_service.summary,
            source=source,
            start_at=start_at,
            end_at=end_at,
        )

    @router.get("/api/billing/records")
    async def billing_records(
        source: str = Query(default="all", pattern="^(all|image|video|chat|audio)$"),
        start_value: str = Query(default="", alias="startAt", max_length=40),
        end_value: str = Query(default="", alias="endAt", max_length=40),
        query_text: str = Query(default="", alias="q", max_length=191),
        limit: int = Query(default=20, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
        authorization: str | None = Header(default=None),
    ):
        require_admin(authorization)
        start_at, end_at = _validated_range(start_value, end_value)
        return await run_in_threadpool(
            upstream_usage_service.list_records,
            source=source,
            start_at=start_at,
            end_at=end_at,
            query_text=query_text,
            limit=limit,
            offset=offset,
        )

    @router.get("/api/billing/sync-status")
    async def billing_sync_status(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return await run_in_threadpool(upstream_usage_service.status)

    @router.post("/api/billing/sync")
    async def billing_sync(
        days: int = Query(default=2, ge=1, le=30),
        authorization: str | None = Header(default=None),
    ):
        require_admin(authorization)
        try:
            result = await run_in_threadpool(
                upstream_usage_service.sync_usage,
                days=days,
                full_sync=days >= 30,
            )
        except UpstreamUsageSyncInProgress as exc:
            raise HTTPException(status_code=409, detail={"error": str(exc)}) from exc
        except UpstreamUsageNotConfigured as exc:
            raise HTTPException(status_code=503, detail={"error": str(exc)}) from exc
        except UpstreamUsageError as exc:
            raise HTTPException(status_code=502, detail={"error": str(exc)}) from exc
        return {**result, "sync": await run_in_threadpool(upstream_usage_service.status)}

    return router

