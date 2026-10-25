"""Health and workload checks used by the monitoring worker."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import os
import re
import threading
import time
from typing import Any

import requests
from sqlalchemy import create_engine, text

from services.platform.enterprise_schema import resolve_enterprise_database_url


_CREDENTIALS = re.compile(r"(?P<scheme>[a-z][a-z0-9+.-]*://)(?P<credentials>[^/@\s]+@)", re.I)
_engine = None
_engine_url = ""
_redis_client = None
_redis_url = ""
_resource_lock = threading.Lock()


def _safe_error(exc: Exception) -> str:
    return _CREDENTIALS.sub(r"\g<scheme>***@", str(exc))[:300]


def _database_engine():
    global _engine, _engine_url
    database_url = resolve_enterprise_database_url()
    if _engine is not None and _engine_url == database_url:
        return _engine
    with _resource_lock:
        if _engine is not None and _engine_url == database_url:
            return _engine
        if _engine is not None:
            _engine.dispose()
        _engine = create_engine(
            database_url,
            pool_pre_ping=True,
            pool_recycle=3600,
            pool_size=1,
            max_overflow=0,
        )
        _engine_url = database_url
        return _engine


def _redis():
    global _redis_client, _redis_url
    redis_url = str(os.getenv("REDIS_URL") or os.getenv("IMAGE_TASK_REDIS_URL") or "").strip()
    if not redis_url:
        raise RuntimeError("Redis URL is not configured")
    if _redis_client is not None and _redis_url == redis_url:
        return _redis_client
    with _resource_lock:
        if _redis_client is not None and _redis_url == redis_url:
            return _redis_client
        import redis

        _redis_client = redis.Redis.from_url(
            redis_url,
            decode_responses=True,
            protocol=2,
            socket_connect_timeout=2,
            socket_timeout=3,
            health_check_interval=30,
        )
        _redis_url = redis_url
        return _redis_client


def _probe_database() -> dict[str, object]:
    started = time.perf_counter()
    try:
        with _database_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
        return {
            "available": True,
            "latency_ms": round((time.perf_counter() - started) * 1000),
        }
    except Exception as exc:
        return {
            "available": False,
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "error": _safe_error(exc),
        }


def _probe_redis() -> dict[str, object]:
    global _redis_client
    started = time.perf_counter()
    try:
        _redis().ping()
        return {
            "available": True,
            "latency_ms": round((time.perf_counter() - started) * 1000),
        }
    except Exception as exc:
        _redis_client = None
        return {
            "available": False,
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "error": _safe_error(exc),
        }


def _probe_application() -> tuple[dict[str, object], dict[str, object]]:
    started = time.perf_counter()
    base_url = str(os.getenv("GMKRAW_BASE_URL") or "http://127.0.0.1:8000").rstrip("/")
    token = str(os.getenv("WECOM_ALERT_INTERNAL_TOKEN") or "").strip()
    if not token:
        return (
            {
                "available": False,
                "latency_ms": 0,
                "error": "WECOM_ALERT_INTERNAL_TOKEN is not configured",
            },
            {"items": []},
        )
    try:
        response = requests.get(
            f"{base_url}/api/alerts/internal-snapshot",
            headers={"X-Alert-Monitor-Token": token},
            timeout=5,
        )
        if response.status_code != 200:
            raise RuntimeError(f"application health endpoint returned HTTP {response.status_code}")
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise RuntimeError("application health endpoint returned invalid data")
        return (
            {
                "available": True,
                "latency_ms": round((time.perf_counter() - started) * 1000),
            },
            payload,
        )
    except Exception as exc:
        return (
            {
                "available": False,
                "latency_ms": round((time.perf_counter() - started) * 1000),
                "error": _safe_error(exc),
            },
            {"items": []},
        )


def collect_health_snapshot(
    *,
    queue_loader: Callable[[], dict[str, object]] | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    database = _probe_database()
    redis_result = _probe_redis()
    if queue_loader is None:
        application, queues = _probe_application()
    else:
        application = {"available": True, "latency_ms": 0}
        try:
            queues = queue_loader()
        except Exception as exc:
            queues = {"items": [], "error": _safe_error(exc)}
    return {
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "duration_ms": round((time.perf_counter() - started) * 1000),
        "database": database,
        "redis": redis_result,
        "application": application,
        "queues": list(queues.get("items") or []),
        **({"queue_error": queues["error"]} if queues.get("error") else {}),
    }
