from __future__ import annotations

import json
import time
from typing import Any, Iterator
from urllib.parse import urljoin

from curl_cffi import requests
from fastapi import HTTPException

from services.platform.config import config
from services.platform.proxy_service import proxy_settings


REQUEST_TIMEOUT_SECONDS = 120


class VideoGenerationProviderError(RuntimeError):
    pass


def _clean(value: object, default: str = "", limit: int = 12000) -> str:
    text = str(value if value is not None else default).strip()
    return (text or default)[:limit]


def _settings() -> dict[str, object]:
    return config.get_video_generation_settings()


def _api_keys(settings: dict[str, object]) -> list[str]:
    keys = []
    api_key = _clean(settings.get("api_key"))
    if api_key:
        keys.append(api_key)
    raw_keys = settings.get("api_keys")
    if isinstance(raw_keys, str):
        candidates = raw_keys.replace("\r", "\n").replace(";", "\n").replace(",", "\n").split("\n")
    elif isinstance(raw_keys, (list, tuple, set)):
        candidates = list(raw_keys)
    else:
        candidates = []
    for item in candidates:
        key = _clean(item)
        if key and key not in keys:
            keys.append(key)
    return keys


def is_configured() -> bool:
    settings = _settings()
    return bool(settings.get("enabled") and _clean(settings.get("base_url")) and _api_keys(settings))


def _url(settings: dict[str, object], path: object) -> str:
    base_url = _clean(settings.get("base_url")).rstrip("/")
    if not base_url:
        raise VideoGenerationProviderError("video_generation.base_url is required")
    normalized = "/" + _clean(path).lstrip("/")
    if base_url.endswith("/v1") and normalized.startswith("/v1/"):
        normalized = normalized.removeprefix("/v1")
    return urljoin(base_url + "/", normalized.lstrip("/"))


def _headers(settings: dict[str, object]) -> dict[str, str]:
    api_keys = _api_keys(settings)
    if not api_keys:
        raise VideoGenerationProviderError("video_generation.api_key is required")
    return {
        "Authorization": f"Bearer {api_keys[0]}",
        "Content-Type": "application/json",
    }


def _try_json(response: requests.Response) -> Any:
    try:
        return response.json()
    except Exception:
        return None


def _raise_for_status(response: requests.Response) -> None:
    if 200 <= int(response.status_code) < 300:
        return
    data = _try_json(response)
    if data is None:
        text = _clean(response.text, limit=500)
        data = {"error": text or f"HTTP {response.status_code}"}
    raise HTTPException(status_code=response.status_code, detail=data)


def _json_object(response: requests.Response) -> dict[str, Any]:
    data = _try_json(response)
    if not isinstance(data, dict):
        raise VideoGenerationProviderError("video generation response is not a JSON object")
    return data


def _nested_dict(data: dict[str, Any]) -> dict[str, Any]:
    nested = data.get("data")
    return nested if isinstance(nested, dict) else data


def _iter_nested(value: object) -> Iterator[object]:
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _iter_nested(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_nested(item)


def _extract_task_id(data: dict[str, Any]) -> str:
    for item in _iter_nested(data):
        if not isinstance(item, dict):
            continue
        for key in ("task_id", "taskId", "id"):
            value = _clean(item.get(key), limit=191)
            if value:
                return value
    raise VideoGenerationProviderError("video generation response did not include task_id")


def _extract_cost(data: dict[str, Any]) -> int | float | None:
    for item in _iter_nested(data):
        if not isinstance(item, dict) or "cost" not in item:
            continue
        value = item.get("cost")
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, (int, float)) and value >= 0:
            return value
        if isinstance(value, str):
            text = value.strip().replace(",", "")
            if not text:
                continue
            try:
                number = float(text)
            except ValueError:
                continue
            if number >= 0:
                return int(number) if number.is_integer() else number
    return None


def _extract_video_url(data: dict[str, Any]) -> str:
    for item in _iter_nested(data):
        if not isinstance(item, dict):
            continue
        for key in ("video_url", "videoUrl", "result_url", "resultUrl", "output_url", "outputUrl", "url"):
            value = _clean(item.get(key), limit=2000)
            if value.startswith(("http://", "https://")):
                return value
        for key in ("video_urls", "videoUrls", "result_urls", "resultUrls", "urls"):
            values = item.get(key)
            if isinstance(values, list):
                for value in values:
                    url = _clean(value, limit=2000)
                    if url.startswith(("http://", "https://")):
                        return url
    return ""


def _extract_cover_url(data: dict[str, Any]) -> str:
    for item in _iter_nested(data):
        if not isinstance(item, dict):
            continue
        for key in ("cover_url", "coverUrl", "poster_url", "posterUrl", "thumbnail_url", "thumbnailUrl"):
            value = _clean(item.get(key), limit=2000)
            if value.startswith(("http://", "https://")):
                return value
    return ""


def _status_text(data: dict[str, Any]) -> str:
    payload = _nested_dict(data)
    return _clean(
        payload.get("status")
        or payload.get("state")
        or payload.get("task_status")
        or payload.get("taskStatus"),
        limit=120,
    ).lower()


def _is_finished(data: dict[str, Any]) -> bool:
    if _nested_dict(data).get("is_final") is True:
        return True
    progress = _clean(_nested_dict(data).get("progress")).rstrip("%")
    if progress == "100":
        return True
    status = _status_text(data)
    return any(marker in status for marker in ("success", "succeeded", "done", "complete", "completed", "failed", "error"))


def _is_failed(data: dict[str, Any]) -> bool:
    status = _status_text(data)
    return any(marker in status for marker in ("failed", "fail", "error"))


def _error_text(data: dict[str, Any]) -> str:
    for item in _iter_nested(data):
        if not isinstance(item, dict):
            continue
        for key in ("error", "message", "msg", "fail_reason", "failure_reason"):
            value = _clean(item.get(key), limit=1000)
            if value:
                return value
    return json.dumps(data, ensure_ascii=False)[:1000]


def submit_video_generation(body: dict[str, Any]) -> dict[str, Any]:
    settings = _settings()
    if not is_configured():
        raise VideoGenerationProviderError("video generation provider is not configured")
    prompt = _clean(body.get("prompt"), limit=12000)
    model = _clean(body.get("model"), limit=191)
    if not prompt:
        raise VideoGenerationProviderError("prompt is required")
    if not model:
        raise VideoGenerationProviderError("model is required")
    raw_params = body.get("params")
    params_source = raw_params if isinstance(raw_params, dict) else {}
    params = {
        key: value
        for key, value in params_source.items()
        if value is not None and _clean(key)
    }
    response = requests.post(
        _url(settings, settings.get("submit_path")),
        headers=_headers(settings),
        json={
            "model": model,
            "prompt": prompt,
            "params": params,
            **({"images": body.get("image_urls")} if body.get("image_urls") else {}),
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
        **proxy_settings.build_session_kwargs(),
    )
    _raise_for_status(response)
    data = _json_object(response)
    return {"upstream_task_id": _extract_task_id(data), "raw": data}


def get_video_generation_status(upstream_task_id: str) -> dict[str, Any]:
    settings = _settings()
    if not is_configured():
        raise VideoGenerationProviderError("video generation provider is not configured")
    task_id = _clean(upstream_task_id, limit=191)
    if not task_id:
        raise VideoGenerationProviderError("upstream_task_id is required")
    response = requests.get(
        _url(settings, settings.get("status_path")),
        headers=_headers(settings),
        params={"task_id": task_id},
        timeout=30,
        **proxy_settings.build_session_kwargs(),
    )
    _raise_for_status(response)
    data = _json_object(response)
    return {
        "finished": _is_finished(data),
        "failed": _is_failed(data),
        "video_url": _extract_video_url(data),
        "cover_url": _extract_cover_url(data),
        "cost": _extract_cost(data),
        "error": _error_text(data) if _is_failed(data) else "",
        "raw": data,
    }


def run_video_generation(body: dict[str, Any]) -> dict[str, Any]:
    settings = _settings()
    submitted = submit_video_generation(body)
    upstream_task_id = _clean(submitted.get("upstream_task_id"), limit=191)
    deadline = time.time() + max(30, int(settings.get("poll_timeout_secs") or 900))
    interval = max(1, int(settings.get("poll_interval_secs") or 3))
    last_status: dict[str, Any] = {}
    while time.time() <= deadline:
        status = get_video_generation_status(upstream_task_id)
        last_status = status
        if not status.get("finished"):
            progress_callback = body.get("progress_callback")
            if callable(progress_callback):
                progress_callback("polling")
            time.sleep(interval)
            continue
        if status.get("failed"):
            raise VideoGenerationProviderError(_clean(status.get("error"), "video generation failed", 1000))
        video_url = _clean(status.get("video_url"), limit=2000)
        if not video_url:
            raise VideoGenerationProviderError("video generation finished without video_url")
        return {
            "upstream_task_id": upstream_task_id,
            "video_url": video_url,
            "cover_url": _clean(status.get("cover_url"), limit=2000),
            "cost": status.get("cost"),
            "raw": {
                "submit": submitted.get("raw"),
                "status": status.get("raw"),
            },
        }
    raise VideoGenerationProviderError(f"video generation timed out: {_clean(last_status.get('raw') or last_status, limit=1000)}")
