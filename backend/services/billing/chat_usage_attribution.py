from __future__ import annotations

import threading
import uuid
from datetime import datetime
from typing import Any, Mapping

from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import sessionmaker

from services.billing.upstream_usage_models import UpstreamUsageAttribution
from services.platform.enterprise_schema import resolve_enterprise_database_url
from utils.log import logger


PROVIDER = "lk888"
_ENGINE_LOCK = threading.Lock()
_ENGINE = None
_SESSION = None
_TABLE_AVAILABLE: bool | None = None


def _clean(value: object, default: str = "", limit: int = 255) -> str:
    return (str(value if value is not None else "").strip() or default)[:limit]


def _engine_and_session():
    global _ENGINE, _SESSION
    if _ENGINE is not None and _SESSION is not None:
        return _ENGINE, _SESSION
    with _ENGINE_LOCK:
        if _ENGINE is None:
            _ENGINE = create_engine(resolve_enterprise_database_url(), pool_pre_ping=True, pool_recycle=3600)
            _SESSION = sessionmaker(bind=_ENGINE, expire_on_commit=False)
    return _ENGINE, _SESSION


def _value_from_mapping(value: Mapping[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        candidate = _clean(value.get(key))
        if candidate:
            return candidate
    return ""


def _extract_ids(value: object, *, depth: int = 0) -> tuple[str, str]:
    if depth > 5:
        return "", ""
    if isinstance(value, Mapping):
        task_id = _value_from_mapping(value, ("task_id", "taskId", "upstream_task_id", "upstreamTaskId"))
        request_id = _value_from_mapping(value, ("request_id", "requestId", "upstream_request_id", "upstreamRequestId"))
        request_id = request_id or _value_from_mapping(value, ("response_id", "responseId"))
        if not task_id:
            candidate = _value_from_mapping(value, ("id",))
            if candidate:
                normalized_candidate = candidate.lower()
                if normalized_candidate.startswith(("chatcmpl-", "resp_", "response-")):
                    request_id = request_id or candidate
                else:
                    task_id = candidate
        headers = value.get("headers")
        if isinstance(headers, Mapping):
            task_id = task_id or _value_from_mapping(headers, ("x-task-id", "task-id"))
            request_id = request_id or _value_from_mapping(
                headers,
                ("x-request-id", "request-id", "x-trace-id", "trace-id"),
            )
        nested_task, nested_request = "", ""
        for key in ("_gmkraw_relay_metadata", "metadata", "meta", "data", "result", "response"):
            nested = value.get(key)
            if nested is value:
                continue
            nested_task, nested_request = _extract_ids(nested, depth=depth + 1)
            if nested_task or nested_request:
                break
        return task_id or nested_task, request_id or nested_request
    if isinstance(value, list):
        for item in value:
            task_id, request_id = _extract_ids(item, depth=depth + 1)
            if task_id or request_id:
                return task_id, request_id
    return "", ""


def record_chat_attribution(
    *,
    owner_id: object,
    requested_model: object,
    local_task_id: object = "",
    response: object = None,
    upstream_task_id: object = "",
    upstream_request_id: object = "",
    local_source: object = "chat",
    model_type: object = "chat",
) -> None:
    """Persist a user-to-relay correlation without storing prompts or secrets."""

    clean_owner = _clean(owner_id, limit=191)
    if not clean_owner:
        return
    clean_source = _clean(local_source, "chat", 32)
    clean_local_task = _clean(local_task_id, limit=191) or f"{clean_source}:{uuid.uuid4().hex}"
    response_task, response_request = _extract_ids(response)
    clean_upstream_task = _clean(upstream_task_id, limit=255) or response_task
    clean_upstream_request = _clean(upstream_request_id, limit=255) or response_request
    clean_model = _clean(requested_model, limit=191)
    try:
        engine, session_factory = _engine_and_session()
        global _TABLE_AVAILABLE
        if _TABLE_AVAILABLE is None:
            _TABLE_AVAILABLE = "upstream_usage_attributions" in inspect(engine).get_table_names()
        if not _TABLE_AVAILABLE:
            return
        now = datetime.now()
        with session_factory.begin() as session:
            row = session.scalars(
                select(UpstreamUsageAttribution).where(
                    UpstreamUsageAttribution.provider == PROVIDER,
                    UpstreamUsageAttribution.local_source == clean_source,
                    UpstreamUsageAttribution.local_task_id == clean_local_task,
                )
            ).one_or_none()
            if row is None:
                row = UpstreamUsageAttribution(
                    provider=PROVIDER,
                    local_source=clean_source,
                    local_task_id=clean_local_task,
                    owner_id=clean_owner,
                    created_at=now,
                )
                session.add(row)
            row.owner_id = clean_owner
            row.requested_model = clean_model or row.requested_model
            row.model_type = _clean(model_type, "chat", 32).lower()
            row.upstream_task_id = clean_upstream_task or row.upstream_task_id
            row.upstream_request_id = clean_upstream_request or row.upstream_request_id
            row.updated_at = now
    except Exception as exc:
        # Attribution must never turn a successful model response into a failed chat.
        logger.warning({"event": "chat_usage_attribution_failed", "error": str(exc)[:300]})


def close() -> None:
    global _ENGINE, _SESSION, _TABLE_AVAILABLE
    with _ENGINE_LOCK:
        if _ENGINE is not None:
            _ENGINE.dispose()
        _ENGINE = None
        _SESSION = None
        _TABLE_AVAILABLE = None
