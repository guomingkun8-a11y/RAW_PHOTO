from __future__ import annotations

from fastapi import APIRouter, Header
from fastapi.responses import StreamingResponse

from api.support import require_identity
from services.platform.realtime_event_service import realtime_event_service


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/events/stream")
    async def stream_events(authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        return StreamingResponse(
            realtime_event_service.stream(identity),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return router
