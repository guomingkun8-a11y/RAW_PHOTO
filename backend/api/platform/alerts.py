"""Administrator API routes for operational alerting."""

from __future__ import annotations

import hmac
from functools import partial

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from api.support import require_super_admin
from services.alerts.alert_service import alert_service
from services.alerts.checks import collect_health_snapshot
from services.alerts.settings import AlertSettings
from services.platform.queue_monitoring_service import queue_overview


class AlertSettingsUpdate(BaseModel):
    enabled: bool | None = None
    check_interval_secs: int | None = Field(default=None, ge=10, le=3600)
    cooldown_secs: int | None = Field(default=None, ge=30, le=86400)
    repeat_secs: int | None = Field(default=None, ge=60, le=604800)
    queue_waiting_threshold: int | None = Field(default=None, ge=1, le=100000)
    dead_letter_threshold: int | None = Field(default=None, ge=1, le=100000)
    stale_task_threshold: int | None = Field(default=None, ge=1, le=100000)
    worker_missing_alert: bool | None = None
    recovery_notifications: bool | None = None


class AlertWebhookCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    url: str = Field(min_length=1, max_length=2048)


class AlertWebhookUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    enabled: bool | None = None
    receive_alerts: bool | None = None
    receive_feedback: bool | None = None


def create_router() -> APIRouter:
    router = APIRouter(prefix="/api/alerts", tags=["operational-alerts"])

    @router.get("/internal-snapshot", include_in_schema=False)
    async def internal_snapshot(
        monitor_token: str | None = Header(default=None, alias="X-Alert-Monitor-Token"),
    ):
        expected = AlertSettings.from_env().internal_token
        if not expected or not monitor_token or not hmac.compare_digest(expected, monitor_token):
            raise HTTPException(status_code=404, detail="Not Found")
        return await run_in_threadpool(queue_overview)

    @router.get("")
    async def alert_status(authorization: str | None = Header(default=None)):
        require_super_admin(authorization)
        return await run_in_threadpool(alert_service.admin_snapshot)

    @router.patch("/settings")
    async def update_alert_settings(
        body: AlertSettingsUpdate,
        authorization: str | None = Header(default=None),
    ):
        identity = require_super_admin(authorization)
        updates = body.model_dump(exclude_unset=True, exclude_none=True)
        if not updates:
            raise HTTPException(status_code=400, detail={"error": "没有需要保存的告警设置"})
        try:
            return await run_in_threadpool(
                alert_service.update_settings,
                updates,
                updated_by=str(identity.get("id") or identity.get("username") or "admin"),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.post("/webhooks")
    async def add_webhook(body: AlertWebhookCreate, authorization: str | None = Header(default=None)):
        require_super_admin(authorization)
        try:
            return await run_in_threadpool(alert_service.add_webhook, name=body.name, url=body.url)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.patch("/webhooks/{webhook_id}")
    async def update_webhook(
        webhook_id: str, body: AlertWebhookUpdate, authorization: str | None = Header(default=None)
    ):
        require_super_admin(authorization)
        try:
            result = await run_in_threadpool(
                alert_service.update_webhook, webhook_id, name=body.name, enabled=body.enabled,
                receive_alerts=body.receive_alerts, receive_feedback=body.receive_feedback,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        if result is None:
            raise HTTPException(status_code=404, detail={"error": "机器人不存在"})
        return result

    @router.delete("/webhooks/{webhook_id}")
    async def delete_webhook(webhook_id: str, authorization: str | None = Header(default=None)):
        require_super_admin(authorization)
        if not await run_in_threadpool(alert_service.delete_webhook, webhook_id):
            raise HTTPException(status_code=404, detail={"error": "机器人不存在"})
        return {"ok": True}

    @router.post("/webhooks/{webhook_id}/test")
    async def send_test_alert(webhook_id: str, authorization: str | None = Header(default=None)):
        require_super_admin(authorization)
        try:
            sent = await run_in_threadpool(alert_service.send_test, webhook_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail={"error": str(exc)}) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail={"error": str(exc)[:300]}) from exc
        return {"ok": bool(sent)}

    @router.post("/check")
    async def run_alert_check(authorization: str | None = Header(default=None)):
        require_super_admin(authorization)
        return await run_in_threadpool(
            alert_service.run_once,
            snapshot_loader=partial(collect_health_snapshot, queue_loader=queue_overview),
        )

    @router.get("/incidents")
    async def list_alert_incidents(
        limit: int = Query(default=30, ge=1, le=100),
        authorization: str | None = Header(default=None),
    ):
        require_super_admin(authorization)
        return await run_in_threadpool(alert_service.list_incidents, limit=limit)

    return router

