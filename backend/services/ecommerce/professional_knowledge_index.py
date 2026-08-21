from __future__ import annotations

import os
from typing import Any, Mapping
from uuid import NAMESPACE_URL, uuid5

from curl_cffi import requests


REQUEST_TIMEOUT = 8


def _base_url() -> str:
    return str(os.getenv("QDRANT_URL") or os.getenv("GMKRAW_QDRANT_URL") or "").strip().rstrip("/")


def _collection() -> str:
    return str(
        os.getenv("QDRANT_KNOWLEDGE_COLLECTION")
        or os.getenv("GMKRAW_QDRANT_KNOWLEDGE_COLLECTION")
        or "raw_professional_knowledge"
    ).strip()


def enabled() -> bool:
    return bool(_base_url())


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
            body = response.json()
            return body if isinstance(body, dict) else {}
    except Exception:
        return None
    return None


def ensure_collection(vector_size: int) -> bool:
    if not enabled() or vector_size <= 0:
        return False
    if _request("GET", ""):
        return True
    return _request(
        "PUT",
        "",
        json={"vectors": {"size": int(vector_size), "distance": "Cosine"}},
    ) is not None


def _point_id(chunk_id: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"raw-professional-knowledge:{chunk_id}"))


def upsert(*, chunk_id: str, vector: list[float], payload: Mapping[str, Any]) -> bool:
    if not vector or not ensure_collection(len(vector)):
        return False
    return _request(
        "PUT",
        "/points?wait=true",
        json={
            "points": [{
                "id": _point_id(chunk_id),
                "vector": vector,
                "payload": {**dict(payload), "chunkId": chunk_id},
            }]
        },
    ) is not None


def search(*, vector: list[float], limit: int) -> list[dict[str, Any]]:
    if not vector or not enabled():
        return []
    body = _request(
        "POST",
        "/points/search",
        json={
            "vector": vector,
            "limit": max(1, min(50, int(limit or 10))),
            "with_payload": True,
        },
    )
    result = body.get("result") if isinstance(body, dict) else []
    return [item for item in result if isinstance(item, dict)] if isinstance(result, list) else []


def delete_chunks(chunk_ids: list[str]) -> bool:
    clean_ids = [str(chunk_id).strip() for chunk_id in chunk_ids if str(chunk_id).strip()]
    if not clean_ids or not enabled():
        return False
    return _request(
        "POST",
        "/points/delete?wait=true",
        json={"points": [_point_id(chunk_id) for chunk_id in clean_ids]},
    ) is not None
