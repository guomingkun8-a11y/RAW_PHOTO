"""Associate relay chat task IDs with the current system user."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator, Mapping

from utils.log import logger


_chat_owner: ContextVar[tuple[str, str, str] | None] = ContextVar("chat_usage_owner", default=None)


@contextmanager
def chat_usage_scope(owner_id: str, source: str, local_task_id: str = "") -> Iterator[None]:
    owner = str(owner_id or "").strip()
    token = _chat_owner.set((owner, str(source or "chat")[:32], str(local_task_id or "")[:191]) if owner else None)
    try:
        yield
    finally:
        _chat_owner.reset(token)


def capture_chat_response(response: object, *, model: object, api_key: str) -> None:
    scope = _chat_owner.get()
    if scope is None or not 200 <= int(getattr(response, "status_code", 0) or 0) < 300:
        return
    headers = getattr(response, "headers", None)
    if not isinstance(headers, Mapping):
        return
    task_id = str(next((value for key, value in headers.items() if str(key).lower() == "x-task-id"), "") or "").strip()
    if not task_id:
        logger.warning({"event": "chat_usage_task_id_missing", "source": scope[1], "model": str(model or "")[:191]})
        return
    try:
        from services.billing.upstream_usage_service import upstream_usage_service

        upstream_usage_service.capture_chat_call(
            task_id=task_id,
            model=str(model or ""),
            owner_id=scope[0],
            source=scope[1],
            local_task_id=scope[2],
            api_key=api_key,
        )
    except Exception as exc:
        logger.error({"event": "chat_usage_capture_failed", "task_id": task_id[:255], "error": str(exc)[:300]})
