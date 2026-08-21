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
        os.getenv("QDRANT_FOLDER_COLLECTION")
        or os.getenv("GMKRAW_QDRANT_FOLDER_COLLECTION")
        or "raw_professional_folder_assets"
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


def _point_id(folder_id: str, item_id: int) -> str:
    return str(uuid5(NAMESPACE_URL, f"raw-folder:{folder_id}:{int(item_id)}"))


def upsert(
    *,
    folder_id: str,
    item_id: int,
    vector: list[float],
    payload: Mapping[str, Any],
) -> bool:
    if not vector or not ensure_collection(len(vector)):
        return False
    return _request(
        "PUT",
        "/points?wait=true",
        json={
            "points": [{
                "id": _point_id(folder_id, item_id),
                "vector": vector,
                "payload": {**dict(payload), "folderId": folder_id, "itemId": int(item_id)},
            }]
        },
    ) is not None


def search(
    *,
    vector: list[float],
    owner_id: str,
    folder_id: str,
    limit: int,
) -> list[dict[str, Any]]:
    if not vector or not enabled():
        return []
    body = _request(
        "POST",
        "/points/search",
        json={
            "vector": vector,
            "limit": max(1, min(30, int(limit or 8))),
            "with_payload": True,
            "filter": {
                "must": [
                    {"key": "ownerId", "match": {"value": owner_id}},
                    {"key": "folderId", "match": {"value": folder_id}},
                ]
            },
        },
    )
    result = body.get("result") if isinstance(body, dict) else []
    return [item for item in result if isinstance(item, dict)] if isinstance(result, list) else []


def delete_folder(*, owner_id: str, folder_id: str) -> bool:
    if not enabled():
        return False
    return _request(
        "POST",
        "/points/delete?wait=true",
        json={
            "filter": {
                "must": [
                    {"key": "ownerId", "match": {"value": owner_id}},
                    {"key": "folderId", "match": {"value": folder_id}},
                ]
            }
        },
    ) is not None
