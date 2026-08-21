from __future__ import annotations

import os
from threading import RLock
import time
from typing import Any, Mapping

from curl_cffi import requests


REQUEST_TIMEOUT = 2.5
FAILURE_COOLDOWN_SECONDS = 30.0
_FAILURE_LOCK = RLock()
_FAILURE_UNTIL = 0.0


def _base_url() -> str:
    return str(os.getenv("QDRANT_URL") or os.getenv("GMKRAW_QDRANT_URL") or "").strip().rstrip("/")


def _collection() -> str:
    return str(os.getenv("QDRANT_COLLECTION") or os.getenv("GMKRAW_QDRANT_COLLECTION") or "raw_professional_memory").strip()


def enabled() -> bool:
    with _FAILURE_LOCK:
        return bool(_base_url()) and time.monotonic() >= _FAILURE_UNTIL


def _mark_available() -> None:
    global _FAILURE_UNTIL
    with _FAILURE_LOCK:
        _FAILURE_UNTIL = 0.0


def _mark_unavailable() -> None:
    global _FAILURE_UNTIL
    with _FAILURE_LOCK:
        _FAILURE_UNTIL = time.monotonic() + FAILURE_COOLDOWN_SECONDS


def _headers() -> dict[str, str]:
    api_key = str(os.getenv("QDRANT_API_KEY") or os.getenv("GMKRAW_QDRANT_API_KEY") or "").strip()
    return {"api-key": api_key} if api_key else {}


def _url(path: str) -> str:
    return f"{_base_url()}/collections/{_collection()}{path}"


def _request(method: str, path: str, **kwargs: Any) -> dict[str, Any] | None:
    if not enabled():
        return None
    try:
        response = requests.request(
            method,
            _url(path),
            headers={**_headers(), "Content-Type": "application/json"},
            timeout=REQUEST_TIMEOUT,
            **kwargs,
        )
        if 200 <= response.status_code < 300:
            _mark_available()
            body = response.json()
            return body if isinstance(body, dict) else {}
        if response.status_code >= 500 or response.status_code == 429:
            _mark_unavailable()
    except Exception:
        _mark_unavailable()
        return None
    return None


def ensure_collection(vector_size: int) -> bool:
    if not enabled() or vector_size <= 0:
        return False
    existing = _request("GET", "")
    if existing:
        return True
    body = _request(
        "PUT",
        "",
        json={"vectors": {"size": int(vector_size), "distance": "Cosine"}},
    )
    return body is not None


def upsert(memory_id: int, vector: list[float], payload: Mapping[str, Any]) -> bool:
    if not vector or not ensure_collection(len(vector)):
        return False
    body = _request(
        "PUT",
        "/points?wait=true",
        json={"points": [{"id": int(memory_id), "vector": vector, "payload": dict(payload)}]},
    )
    return body is not None


def search(
    *,
    vector: list[float],
    owner_id: str,
    scope_keys: list[str],
    limit: int,
) -> list[dict[str, Any]]:
    if not vector or not enabled():
        return []
    must: list[dict[str, Any]] = [{"key": "ownerId", "match": {"value": owner_id}}]
    if scope_keys:
        must.append({"key": "scopeKey", "match": {"any": scope_keys}})
    body = _request(
        "POST",
        "/points/search",
        json={
            "vector": vector,
            "limit": max(1, min(20, int(limit or 8))),
            "with_payload": True,
            "filter": {"must": must},
        },
    )
    result = body.get("result") if isinstance(body, dict) else []
    return [item for item in result if isinstance(item, dict)] if isinstance(result, list) else []


def delete(memory_id: int) -> bool:
    if not enabled():
        return False
    return _request(
        "POST",
        "/points/delete?wait=true",
        json={"points": [int(memory_id)]},
    ) is not None
