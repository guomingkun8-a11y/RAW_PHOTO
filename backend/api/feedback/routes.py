from __future__ import annotations

from fastapi import APIRouter, File, Header, HTTPException, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response

from api.support import require_admin, require_identity, require_super_admin
from api.feedback.schemas import MessageInput, ReadInput, SettingsInput, ThreadInput, ThreadUpdate
from services.alerts.incident_store import alert_incident_store
from services.alerts.settings import AlertSettings
from services.feedback.realtime import FeedbackRealtime
from services.feedback.service import MAX_ATTACHMENT_BYTES, feedback_service


feedback_realtime = FeedbackRealtime(feedback_service)


def create_router():
    router = APIRouter(prefix="/api/feedback", tags=["feedback"])

    @router.get("/threads")
    def threads(authorization: str | None = Header(None), page: int = Query(1, ge=1), limit: int = Query(25, ge=1, le=50), status: str = Query("", max_length=20), category: str = Query("", max_length=20), search: str = Query("", max_length=100), unread_only: bool = False):
        return feedback_service.store.list_threads(require_identity(authorization), page=page, limit=limit, status=status, category=category, search=search.strip(), unread_only=unread_only)

    @router.get("/unread")
    def unread(authorization: str | None = Header(None)):
        return feedback_service.store.unread_count(require_identity(authorization))

    @router.post("/threads", status_code=201)
    async def create_thread(body: ThreadInput, authorization: str | None = Header(None)):
        identity = await run_in_threadpool(require_identity, authorization)
        await feedback_realtime.limit(identity, "thread")
        return await run_in_threadpool(feedback_service.store.create_thread, identity, body.model_dump())

    @router.get("/threads/{thread_id}")
    def thread(thread_id: str, authorization: str | None = Header(None)):
        return feedback_service.store.get_thread(require_identity(authorization), thread_id)

    @router.get("/threads/{thread_id}/messages")
    def messages(thread_id: str, authorization: str | None = Header(None), before: int = Query(0, ge=0), after: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100)):
        if before and after:
            raise HTTPException(422, "before 与 after 不能同时使用")
        return feedback_service.store.messages(require_identity(authorization), thread_id, before=before, after=after, limit=limit)

    @router.post("/threads/{thread_id}/messages", status_code=201)
    async def message(thread_id: str, body: MessageInput, authorization: str | None = Header(None)):
        identity = await run_in_threadpool(require_identity, authorization)
        await feedback_realtime.limit(identity)
        return await run_in_threadpool(feedback_service.store.send_message, identity, thread_id, body.model_dump())

    @router.post("/threads/{thread_id}/read")
    def read(thread_id: str, body: ReadInput, authorization: str | None = Header(None)):
        return feedback_service.store.mark_read(require_identity(authorization), thread_id, body.seq)

    @router.patch("/threads/{thread_id}")
    def update(thread_id: str, body: ThreadUpdate, authorization: str | None = Header(None)):
        return feedback_service.update_thread(require_identity(authorization), thread_id, body.model_dump(exclude_none=True))

    @router.post("/attachments", status_code=201)
    async def upload(file: UploadFile = File(...), authorization: str | None = Header(None)):
        identity = await run_in_threadpool(require_identity, authorization)
        await feedback_realtime.limit(identity, "upload")
        try:
            payload = await file.read(MAX_ATTACHMENT_BYTES + 1)
            return await run_in_threadpool(feedback_service.upload, identity, payload, file.filename)
        finally:
            await file.close()

    @router.get("/attachments/{attachment_id}/content")
    def content(attachment_id: str, authorization: str | None = Header(None)):
        payload, item = feedback_service.attachment_content(require_identity(authorization), attachment_id)
        return Response(payload, media_type=item["mime_type"], headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})

    @router.get("/settings")
    def settings(authorization: str | None = Header(None)):
        require_admin(authorization)
        webhooks = alert_incident_store.list_webhooks(AlertSettings.from_env(), purpose="feedback")
        return {**feedback_service.store.settings(), "webhooks": webhooks}

    @router.put("/settings")
    def update_settings(body: SettingsInput, authorization: str | None = Header(None)):
        require_super_admin(authorization)
        if body.notify_enabled:
            if not alert_incident_store.list_webhooks(AlertSettings.from_env(), active_only=True, purpose="feedback"):
                raise HTTPException(422, "请先在系统管理中指定接收反馈消息的机器人")
        return {**feedback_service.store.settings(body.model_dump()), "webhooks": alert_incident_store.list_webhooks(AlertSettings.from_env(), purpose="feedback")}

    from api.feedback.websocket import attach_websocket
    attach_websocket(router, feedback_service, feedback_realtime)
    return router

