from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from typing import Any, Iterator

from curl_cffi import CurlMime, requests
from fastapi import HTTPException

from services.platform.config import config
from services.image.image_size import canvas_media_request_size, normalize_image_size
from services.image.image_prompt_compliance import ensure_image_prompt_engineered
from services.providers.openai_relay_pool import (
    RelaySubmissionUnknownHTTPException,
    RelaySubmittedHTTPException,
    current_relay_account,
    run_with_relay_pool,
)
from services.platform.proxy_service import proxy_settings
from services.image import reference_image_uploader
from utils.log import logger


STREAM_TIMEOUT_SECONDS = 300
REQUEST_TIMEOUT_SECONDS = 300
TT_IMAGE_2_5_VERSIONS = {
    "gpt-image-2.5-flare": "flare",
    "gpt-image-2.5-sunburst": "sunburst",
}
MEDIA_IMAGE_MODEL_ALIASES = {
    "gemini-3.1-flash-image-preview": "banana-2",
    "gpt-image-2": "tt-image-2",
    "gpt-image-2.5": "tt-image-2.5",
    **{model: "tt-image-2.5" for model in TT_IMAGE_2_5_VERSIONS},
}
MEDIA_IMAGE_MODELS = {
    "banana-2",
    "mj_imagine",
    "qwen-image",
    "tt-image-2",
    "tt-image-2.5",
    "wan2.6-image",
    "wan2.7-image",
}
MEDIA_IMAGE_MODEL_PREFIXES = (
    "doubao-seedream-",
    "kling-",
    "vidu-image-",
)
MEDIA_STATUS_PATHS = (
    "/v1/media/status",
    "/v1/skills/task-status",
)
MEDIA_PENDING_STATUS_MARKERS = (
    "pending",
    "queued",
    "queue",
    "waiting",
    "running",
    "processing",
    "in_progress",
    "in progress",
    "submitted",
    "created",
    "starting",
    "started",
    "\u7b49\u5f85",
    "\u6392\u961f",
    "\u5904\u7406\u4e2d",
    "\u751f\u6210\u4e2d",
    "\u8fdb\u884c\u4e2d",
)
MEDIA_SUCCESS_STATUS_MARKERS = (
    "success",
    "succeeded",
    "done",
    "complete",
    "completed",
    "\u5b8c\u6210",
    "\u6210\u529f",
)
MEDIA_FAILURE_STATUS_MARKERS = (
    "failed",
    "fail",
    "error",
    "failure",
    "\u5931\u8d25",
    "\u9519\u8bef",
    "\u5f02\u5e38",
)
MEDIA_IMAGE_ASPECT_RATIOS = {
    "1:1": 1,
    "2:3": 2 / 3,
    "3:2": 3 / 2,
    "3:4": 3 / 4,
    "4:3": 4 / 3,
    "4:5": 4 / 5,
    "5:4": 5 / 4,
    "9:16": 9 / 16,
    "16:9": 16 / 9,
    "21:9": 21 / 9,
    "1:4": 1 / 4,
    "4:1": 4,
    "1:8": 1 / 8,
    "8:1": 8,
}
MEDIA_IMAGE_SIZE_OPTIONS = ("0.5K", "1K", "2K", "4K")
MEDIA_IMAGE_QUALITY_OPTIONS = ("auto", "high", "medium", "low")
MEDIA_IMAGE_THINKING_OPTIONS = ("minimal", "high")
MAX_MEDIA_REFERENCE_IMAGES = 14


def settings() -> dict[str, object]:
    return config.get_openai_relay_settings()


def _relay_has_api_key(relay: dict[str, object]) -> bool:
    if str(relay.get("api_key") or "").strip():
        return True
    api_keys = relay.get("api_keys")
    if isinstance(api_keys, str):
        return bool(api_keys.strip())
    if isinstance(api_keys, (list, tuple, set)):
        return any(str(item or "").strip() for item in api_keys)
    return False


def _active_relay_settings() -> dict[str, object]:
    relay = settings()
    account = current_relay_account()
    if account is None:
        return relay
    next_relay = dict(relay)
    next_relay["base_url"] = account.base_url
    next_relay["api_key"] = account.api_key
    return next_relay


def is_enabled() -> bool:
    relay = settings()
    return bool(relay.get("enabled") and relay.get("base_url") and _relay_has_api_key(relay))


def _url(path: str) -> str:
    relay = _active_relay_settings()
    base_url = str(relay.get("base_url") or "").strip().rstrip("/")
    if not base_url:
        raise HTTPException(status_code=500, detail={"error": "openai_relay.base_url is required"})
    normalized_path = "/" + path.strip("/")
    if base_url.endswith("/v1") and normalized_path.startswith("/v1/"):
        normalized_path = normalized_path.removeprefix("/v1")
    return f"{base_url}{normalized_path}"


def _headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    relay = _active_relay_settings()
    api_key = str(relay.get("api_key") or "").strip()
    if not api_key:
        raise HTTPException(status_code=500, detail={"error": "openai_relay.api_key is required"})
    return {
        "Authorization": f"Bearer {api_key}",
        **(extra or {}),
    }


def _try_response_json(response: requests.Response) -> Any:
    try:
        return response.json()
    except Exception:
        return None


def _error_detail(response: requests.Response) -> Any:
    data = _try_response_json(response)
    if data is not None:
        return data
    preview = str(response.text or "").strip()
    if len(preview) > 500:
        preview = preview[:500] + "...[truncated]"
    return {"error": {"message": preview or f"HTTP {response.status_code}"}}


def _response_json_object(response: requests.Response) -> dict[str, Any]:
    data = _try_response_json(response)
    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail={"error": "relay response is not a JSON object"})
    return data


def _raise_for_status(response: requests.Response) -> None:
    if 200 <= response.status_code < 300:
        return
    raise HTTPException(status_code=response.status_code, detail=_error_detail(response))


def _iter_openai_sse(response: requests.Response) -> Iterator[dict[str, Any]]:
    try:
        for raw_line in response.iter_lines():
            if not raw_line:
                continue
            line = raw_line.decode("utf-8", errors="ignore") if isinstance(raw_line, bytes) else str(raw_line)
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                parsed = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                yield parsed
    finally:
        response.close()


def _json_post(path: str, body: dict[str, Any]) -> dict[str, Any] | Iterator[dict[str, Any]]:
    stream = bool(body.get("stream"))
    response = requests.post(
        _url(path),
        headers=_headers({"Content-Type": "application/json"}),
        json=body,
        stream=stream,
        timeout=STREAM_TIMEOUT_SECONDS if stream else REQUEST_TIMEOUT_SECONDS,
        **proxy_settings.build_session_kwargs(),
    )
    _raise_for_status(response)
    if stream:
        return _iter_openai_sse(response)
    return _response_json_object(response)


def list_models() -> dict[str, Any]:
    def execute() -> dict[str, Any]:
        response = requests.get(
            _url("/v1/models"),
            headers=_headers(),
            timeout=REQUEST_TIMEOUT_SECONDS,
            **proxy_settings.build_session_kwargs(),
        )
        _raise_for_status(response)
        return _response_json_object(response)

    return run_with_relay_pool(settings(), "list_models", execute)


def list_media_voices(model: str) -> dict[str, Any]:
    selected_model = str(model or "").strip()
    if not selected_model:
        raise HTTPException(status_code=400, detail={"error": "model is required"})

    def execute() -> dict[str, Any]:
        response = requests.get(
            _url("/v1/skills/voices"),
            headers=_headers({"Accept": "application/json"}),
            params={"model": selected_model},
            timeout=60,
            **proxy_settings.build_session_kwargs(),
        )
        _raise_for_status(response)
        return _response_json_object(response)

    return run_with_relay_pool(settings(), "list_media_voices", execute)


def image_generations(body: dict[str, Any]) -> dict[str, Any] | Iterator[dict[str, Any]]:
    def execute() -> dict[str, Any] | Iterator[dict[str, Any]]:
        payload = {
            key: value
            for key, value in body.items()
            if key not in {
                "base_url",
                "progress_callback",
                "prompt_engine_mode",
                "subject_mutation_policy",
                "aspect_ratio",
                "image_size",
                "thinking_level",
            } and value is not None
        }
        payload["prompt"] = ensure_image_prompt_engineered(
            str(payload.get("prompt") or ""),
            prompt_engine_mode=str(body.get("prompt_engine_mode") or "professional"),
            subject_mutation_policy=str(body.get("subject_mutation_policy") or "preserve"),
        )
        if _is_media_image_model(str(payload.get("model") or "")):
            return _media_image_generation({**body, "prompt": payload["prompt"]})
        result = _json_post("/v1/images/generations", payload)
        return _with_response_cost(result) if isinstance(result, dict) else result

    return run_with_relay_pool(settings(), "image_generations", execute)


def _image_bytes_to_data_url(image_data: bytes, mime_type: str | None) -> str:
    return f"data:{mime_type or 'image/png'};base64,{base64.b64encode(image_data).decode('ascii')}"


def _is_media_image_model(model: str) -> bool:
    normalized = model.strip().lower()
    return (
        normalized in MEDIA_IMAGE_MODEL_ALIASES
        or normalized in MEDIA_IMAGE_MODELS
        or normalized.startswith(MEDIA_IMAGE_MODEL_PREFIXES)
    )


def _upstream_media_model(model: object) -> str:
    value = str(model or "").strip()
    normalized = value.lower()
    return MEDIA_IMAGE_MODEL_ALIASES.get(normalized, value)


def _reference_image_urls(body: dict[str, Any]) -> list[str]:
    image_urls = [
        str(url).strip()
        for url in body.get("image_urls") or []
        if str(url).strip().lower().startswith(("http://", "https://"))
    ]
    local_images = list(body.get("images") or [])
    if local_images:
        try:
            uploaded_urls = reference_image_uploader.upload_images(local_images)
        except Exception as exc:
            if not image_urls:
                raise HTTPException(
                    status_code=502,
                    detail={"error": f"reference image upload failed: {exc}"},
                ) from exc
            uploaded_urls = []
        image_urls.extend(url for url in uploaded_urls if url and url not in image_urls)
    return image_urls


def _aspect_ratio_from_size(size: object) -> str:
    value = str(size or "").strip().lower()
    if not value or value == "auto":
        return "auto"
    matched = re.match(r"^(\d+)\s*x\s*(\d+)$", value)
    if not matched:
        return "auto"
    width = max(1, int(matched.group(1)))
    height = max(1, int(matched.group(2)))
    ratio = width / height
    return min(MEDIA_IMAGE_ASPECT_RATIOS, key=lambda item: abs(MEDIA_IMAGE_ASPECT_RATIOS[item] - ratio))


def _image_size_tier_from_size(size: object) -> str:
    value = str(size or "").strip().lower()
    matched = re.match(r"^(\d+)\s*x\s*(\d+)$", value)
    if not matched:
        return "1K"
    width = max(1, int(matched.group(1)))
    height = max(1, int(matched.group(2)))
    longest = max(width, height)
    if longest >= 3000:
        return "4K"
    if longest >= 2000:
        return "2K"
    if longest <= 768:
        return "0.5K"
    return "1K"


def _media_option(value: object, allowed: tuple[str, ...], default: str, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return default
    by_lower = {item.lower(): item for item in allowed}
    selected = by_lower.get(normalized.lower())
    if selected is None:
        raise HTTPException(
            status_code=400,
            detail={"error": f"{field_name} must be one of: {', '.join(allowed)}"},
        )
    return selected


def _media_aspect_ratio(body: dict[str, Any], requested_size: str) -> str:
    explicit = str(body.get("aspect_ratio") or "").strip()
    if explicit:
        by_lower = {item.lower(): item for item in MEDIA_IMAGE_ASPECT_RATIOS}
        selected = by_lower.get(explicit.lower())
        if selected is None:
            raise HTTPException(
                status_code=400,
                detail={"error": f"aspect_ratio must be one of: {', '.join(MEDIA_IMAGE_ASPECT_RATIOS)}"},
            )
        return selected
    return _aspect_ratio_from_size(requested_size)


def _media_image_size(body: dict[str, Any], requested_size: str) -> str:
    explicit = str(body.get("image_size") or "").strip()
    return _media_option(
        explicit,
        MEDIA_IMAGE_SIZE_OPTIONS,
        _image_size_tier_from_size(requested_size),
        "image_size",
    )


def _media_quality(body: dict[str, Any]) -> str:
    requested = str(body.get("quality") or "").strip()
    if requested.lower() == "standard":
        requested = "auto"
    return _media_option(requested, MEDIA_IMAGE_QUALITY_OPTIONS, "auto", "quality")


def _media_thinking_level(body: dict[str, Any]) -> str:
    return _media_option(
        body.get("thinking_level"),
        MEDIA_IMAGE_THINKING_OPTIONS,
        "minimal",
        "thinking_level",
    )


def _tt_image_size(body: dict[str, Any], requested_size: str) -> str:
    aspect_ratio = str(body.get("aspect_ratio") or "").strip()
    if aspect_ratio:
        target = canvas_media_request_size(
            aspect_ratio,
            body.get("image_size") or _media_image_size(body, requested_size),
        )
        if target is None:
            raise HTTPException(
                status_code=400,
                detail={"error": "tt-image-2 aspect ratio must be between 1:3 and 3:1"},
            )
        return target
    expected_size = normalize_image_size(body.get("expected_size"))
    return expected_size or normalize_image_size(requested_size) or requested_size


def _media_batch_prompt(prompt: str, image_index: int, image_count: int) -> str:
    if image_count <= 1:
        return prompt
    current = min(image_count, max(1, image_index + 1))
    pattern = re.compile(
        rf"((?:这是|当前是|批量差异：)\s*第)\s*\d+\s*/\s*{image_count}(\s*张)"
    )
    rewritten, replacements = pattern.subn(
        lambda match: f"{match.group(1)} {current}/{image_count}{match.group(2)}",
        prompt,
    )
    if replacements:
        return rewritten
    return (
        f"{prompt}\n\n"
        f"批量生成执行说明：这是第 {current}/{image_count} 张独立成品图；"
        "只输出当前这一张，不要拼图、分屏或多面板。"
    )


def _media_image_params(
    body: dict[str, Any],
    *,
    upstream_model: str,
    requested_size: str,
    reference_urls: list[str],
) -> dict[str, Any]:
    params: dict[str, Any]
    if upstream_model.lower() == "tt-image-2.5":
        params = {
            "background": "auto",
            "quality": _media_quality(body),
            "resolution": _media_image_size(body, requested_size),
        }
        aspect_ratio = _media_aspect_ratio(body, requested_size)
        if aspect_ratio != "auto":
            params["aspect_ratio"] = aspect_ratio
        version = TT_IMAGE_2_5_VERSIONS.get(str(body.get("model") or "").strip().lower())
        # Unversioned legacy requests keep the provider's default version.
        if version:
            params["version"] = version
    elif upstream_model.lower() == "tt-image-2":
        params = {
            "n": 1,
            "quality": _media_quality(body),
            "size": _tt_image_size(body, requested_size),
        }
    else:
        params = {
            "aspectRatio": _media_aspect_ratio(body, requested_size),
            "imageSize": _media_image_size(body, requested_size),
            "n": 1,
            "quality": _media_quality(body),
            "size": requested_size,
            "thinkingLevel": _media_thinking_level(body),
        }
    if reference_urls:
        params["images"] = reference_urls
    return params


def _extract_media_task_id(data: dict[str, Any]) -> str:
    candidates = [
        data.get("task_id"),
        data.get("id"),
    ]
    nested = data.get("data")
    if isinstance(nested, dict):
        candidates.extend([
            nested.get("task_id"),
            nested.get("id"),
            nested.get("任务id"),
        ])
        task_ids = nested.get("任务ids")
        if isinstance(task_ids, list) and task_ids:
            candidates.append(task_ids[0])
    for candidate in candidates:
        value = str(candidate or "").strip()
        if value:
            return value
    raise HTTPException(status_code=502, detail={"error": "media generation response did not include task_id"})


def _sanitize_reconciliation_value(value: object, *, depth: int = 0) -> object:
    if depth >= 5:
        return "[truncated]"
    if isinstance(value, dict):
        sanitized: dict[str, object] = {}
        for key, item in list(value.items())[:50]:
            normalized_key = str(key)
            if any(marker in normalized_key.lower() for marker in ("authorization", "api_key", "apikey", "secret", "token")):
                sanitized[normalized_key] = "[redacted]"
            else:
                sanitized[normalized_key] = _sanitize_reconciliation_value(item, depth=depth + 1)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_reconciliation_value(item, depth=depth + 1) for item in value[:20]]
    if isinstance(value, str):
        return value[:2000]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:500]


def _submission_reconciliation_metadata(
    response: requests.Response,
    response_data: object,
    request_payload: dict[str, Any],
    *,
    image_index: int,
    requested_count: int,
) -> dict[str, Any]:
    digest_payload = {
        "model": request_payload.get("model"),
        "prompt": request_payload.get("prompt"),
        "params": request_payload.get("params"),
        "image_index": image_index,
        "requested_count": requested_count,
    }
    request_digest = hashlib.sha256(
        json.dumps(digest_payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    request_id = ""
    response_headers = {
        str(key).lower(): value
        for key, value in getattr(response, "headers", {}).items()
    }
    for header in ("x-request-id", "request-id", "x-trace-id", "trace-id"):
        request_id = str(response_headers.get(header) or "").strip()
        if request_id:
            break
    account = current_relay_account()
    return {
        "http_status": int(response.status_code),
        "upstream_request_id": request_id,
        "request_digest": request_digest,
        "relay_account_id": str(getattr(account, "id", "") or ""),
        "relay_account_name": str(getattr(account, "name", "") or ""),
        "model": str(request_payload.get("model") or ""),
        "submitted_at": int(time.time()),
        "image_index": image_index,
        "requested_count": requested_count,
        "upstream_response": _sanitize_reconciliation_value(response_data),
    }


def _media_status_payload(data: dict[str, Any]) -> dict[str, Any]:
    nested = data.get("data")
    return nested if isinstance(nested, dict) else data


def _normalize_media_cost(value: object) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        if not text:
            return None
        matched = re.search(r"-?\d+(?:\.\d+)?", text)
        if not matched:
            return None
        try:
            number = float(matched.group(0))
        except ValueError:
            return None
        return int(number) if number.is_integer() else number
    return None


def _iter_cost_candidates(value: object) -> Iterator[object]:
    if not isinstance(value, (dict, list)):
        return
    if isinstance(value, dict):
        if "cost" in value:
            yield value.get("cost")
        for key in ("data", "result", "usage", "meta", "metadata", "billing"):
            nested = value.get(key)
            if nested is value:
                continue
            yield from _iter_cost_candidates(nested)
        return
    for item in value:
        yield from _iter_cost_candidates(item)


def _media_cost(data: dict[str, Any]) -> int | float | None:
    payload = _media_status_payload(data)
    for candidate in _iter_cost_candidates(payload):
        cost = _normalize_media_cost(candidate)
        if cost is not None:
            return cost
    if payload is not data:
        for candidate in _iter_cost_candidates(data):
            cost = _normalize_media_cost(candidate)
            if cost is not None:
                return cost
    return None


def _dict_keys(value: object) -> list[str]:
    if not isinstance(value, dict):
        return []
    return sorted(str(key) for key in value.keys())


def _has_nested_key(value: object, key_name: str) -> bool:
    if isinstance(value, dict):
        return any(str(key) == key_name or _has_nested_key(item, key_name) for key, item in value.items())
    if isinstance(value, list):
        return any(_has_nested_key(item, key_name) for item in value)
    return False


def _log_missing_media_cost(task_id: str, data: dict[str, Any]) -> None:
    payload = _media_status_payload(data)
    result = payload.get("result") if isinstance(payload, dict) else None
    logger.warning({
        "event": "media_status_cost_missing",
        "task_id": task_id,
        "status": str(payload.get("status") or payload.get("state") or "") if isinstance(payload, dict) else "",
        "top_level_keys": _dict_keys(data),
        "payload_keys": _dict_keys(payload),
        "result_keys": _dict_keys(result),
        "has_any_cost_key": _has_nested_key(data, "cost"),
    })


def _with_response_cost(data: dict[str, Any]) -> dict[str, Any]:
    if data.get("cost") is not None:
        return data
    cost = _media_cost(data)
    if cost is None:
        return data
    return {**data, "cost": cost}


def _media_result_urls(value: object) -> list[str]:
    """Extract every generated media URL from the relay's status shapes."""

    direct_keys = ("result_url", "resultUrl", "output_url", "outputUrl", "audio_url", "audioUrl", "url")
    collection_keys = ("result_urls", "resultUrls", "urls", "images", "outputs", "artifacts", "data", "result")
    found: list[str] = []

    def add_url(candidate: object) -> None:
        if not isinstance(candidate, str):
            return
        url = candidate.strip()
        if url.startswith(("http://", "https://")) and url not in found:
            found.append(url)

    def visit(candidate: object) -> None:
        if isinstance(candidate, str):
            add_url(candidate)
            return
        if isinstance(candidate, list):
            for item in candidate:
                visit(item)
            return
        if not isinstance(candidate, dict):
            return
        for key in direct_keys:
            if key in candidate:
                value = candidate.get(key)
                if isinstance(value, list):
                    visit(value)
                elif isinstance(value, dict):
                    visit(value)
                else:
                    add_url(value)
        for key in collection_keys:
            if key in candidate:
                visit(candidate.get(key))

    visit(value)
    return found


def _media_result_url(data: dict[str, Any]) -> str:
    return (_media_result_urls(_media_status_payload(data)) or [""])[0]


def _media_count(value: object) -> int:
    try:
        count = int(value or 1)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail={"error": "n must be an integer"}) from exc
    if count < 1 or count > 4:
        raise HTTPException(status_code=400, detail={"error": "n must be between 1 and 4"})
    return count


def _media_error_text(data: dict[str, Any]) -> str:
    payload = _media_status_payload(data)
    for key in ("error", "message", "msg", "fail_reason", "failure_reason", "status"):
        value = payload.get(key)
        if value:
            return str(value)
    return json.dumps(data, ensure_ascii=False)[:500]


def _media_status_text(data: dict[str, Any]) -> str:
    payload = _media_status_payload(data)
    parts = [
        str(value)
        for key in ("status", "state", "error", "message", "msg")
        if (value := payload.get(key))
    ]
    return " ".join(parts).strip().casefold()


def _media_status_has_marker(data: dict[str, Any], markers: tuple[str, ...]) -> bool:
    status = _media_status_text(data)
    return any(marker in status for marker in markers)


def _media_task_failed(data: dict[str, Any]) -> bool:
    return _media_status_has_marker(data, MEDIA_FAILURE_STATUS_MARKERS)


def _media_task_finished(data: dict[str, Any]) -> bool:
    payload = _media_status_payload(data)
    if _media_task_failed(data):
        return True
    if _media_status_has_marker(data, MEDIA_PENDING_STATUS_MARKERS):
        return False
    if _media_result_url(data):
        return True
    if payload.get("is_final") is True:
        return True
    progress = str(payload.get("progress") or "").strip().rstrip("%")
    if progress == "100":
        return True
    return _media_status_has_marker(data, MEDIA_SUCCESS_STATUS_MARKERS)


def _get_media_status(task_id: str) -> dict[str, Any]:
    for index, path in enumerate(MEDIA_STATUS_PATHS):
        response = requests.get(
            _url(path),
            headers=_headers(),
            params={"task_id": task_id},
            timeout=30,
            **proxy_settings.build_session_kwargs(),
        )
        if response.status_code in {404, 405} and index < len(MEDIA_STATUS_PATHS) - 1:
            continue
        _raise_for_status(response)
        return _response_json_object(response)
    raise HTTPException(status_code=502, detail={"error": "media status endpoint is unavailable"})


def generate_media_task(
    body: dict[str, Any],
    *,
    operation: str = "media_generation",
    timeout_seconds: int = REQUEST_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    request_payload = {
        "model": str(body.get("model") or "").strip(),
        "prompt": str(body.get("prompt") or "").strip(),
        "params": dict(body.get("params") or {}),
    }
    if not request_payload["model"]:
        raise HTTPException(status_code=400, detail={"error": "model is required"})
    if not request_payload["prompt"]:
        raise HTTPException(status_code=400, detail={"error": "prompt is required"})
    poll_timeout = max(30, min(1800, int(timeout_seconds or REQUEST_TIMEOUT_SECONDS)))

    def execute() -> dict[str, Any]:
        response = requests.post(
            _url("/v1/media/generate"),
            headers=_headers({"Accept": "application/json", "Content-Type": "application/json"}),
            json=request_payload,
            timeout=min(60, poll_timeout),
            **proxy_settings.build_session_kwargs(),
        )
        _raise_for_status(response)
        response_data = _response_json_object(response)
        try:
            task_id = _extract_media_task_id(response_data)
        except HTTPException as exc:
            raise RelaySubmissionUnknownHTTPException(
                detail={"error": "media generation was accepted but the response did not include task_id"},
                reconciliation={
                    "http_status": int(response.status_code),
                    "model": request_payload["model"],
                    "submitted_at": int(time.time()),
                    "upstream_response": _sanitize_reconciliation_value(response_data),
                },
            ) from exc

        deadline = time.monotonic() + poll_timeout
        last_status: dict[str, Any] = {}
        try:
            while time.monotonic() <= deadline:
                last_status = _get_media_status(task_id)
                if not _media_task_finished(last_status):
                    time.sleep(2)
                    continue

                payload = _media_status_payload(last_status)
                result_urls = _media_result_urls(payload)
                if _media_task_failed(last_status):
                    error = HTTPException(
                        status_code=422,
                        detail={"error": f"media generation failed: {_media_error_text(last_status)}"},
                    )
                    error.upstream_finished = True
                    raise error
                if not result_urls:
                    error = HTTPException(
                        status_code=502,
                        detail={"error": "media generation completed without a result URL"},
                    )
                    error.upstream_finished = True
                    raise error

                return {
                    "task_id": task_id,
                    "state": str(payload.get("state") or "success"),
                    "status": str(payload.get("status") or payload.get("state") or "success"),
                    "is_final": bool(payload.get("is_final", True)),
                    "progress": payload.get("progress") or "100%",
                    "result_url": result_urls[0],
                    "result_type": str(payload.get("result_type") or ""),
                    "error": "",
                    "cost": _media_cost(last_status),
                }

            raise HTTPException(
                status_code=504,
                detail={"error": f"media generation timed out: {_media_error_text(last_status)}"},
            )
        except RelaySubmittedHTTPException:
            raise
        except HTTPException as exc:
            upstream_finished = bool(getattr(exc, "upstream_finished", False))
            raise RelaySubmittedHTTPException(
                status_code=exc.status_code,
                detail=exc.detail,
                upstream_task_ids=[task_id],
                submission_uncertain=not upstream_finished,
                upstream_finished=upstream_finished,
            ) from exc
        except Exception as exc:
            raise RelaySubmittedHTTPException(
                status_code=502,
                detail={"error": f"media status request failed: {exc}"},
                upstream_task_ids=[task_id],
                submission_uncertain=True,
            ) from exc

    return run_with_relay_pool(settings(), operation, execute)


def _poll_media_image_task(task_id: str) -> dict[str, Any]:
    deadline = time.time() + REQUEST_TIMEOUT_SECONDS
    last_status: dict[str, Any] = {}
    while time.time() <= deadline:
        data = _get_media_status(task_id)
        last_status = data
        if not _media_task_finished(data):
            time.sleep(2)
            continue
        result_urls = _media_result_urls(_media_status_payload(data))
        if result_urls and not _media_task_failed(data):
            cost = _media_cost(data)
            if cost is None:
                _log_missing_media_cost(task_id, data)
            return {"result_url": result_urls[0], "result_urls": result_urls, "cost": cost}
        error = HTTPException(
            status_code=422,
            detail={"error": f"media generation failed: {_media_error_text(data)}"},
        )
        error.upstream_finished = True
        raise error
    raise HTTPException(status_code=504, detail={"error": f"media generation timed out: {_media_error_text(last_status)}"})


def _media_image_generation(body: dict[str, Any]) -> dict[str, Any]:
    model = str(body.get("model") or "").strip()
    upstream_model = _upstream_media_model(model)
    reference_urls = _reference_image_urls(body)
    prompt = ensure_image_prompt_engineered(
        str(body.get("prompt") or "").strip(),
        has_reference=bool(reference_urls),
        preserve_subject=bool(body.get("preserve_subject") or body.get("preserve_product")),
        prompt_engine_mode=str(body.get("prompt_engine_mode") or "professional"),
        subject_mutation_policy=str(body.get("subject_mutation_policy") or "preserve"),
    )
    if not prompt:
        raise HTTPException(status_code=400, detail={"error": "prompt is required"})
    requested_size = str(body.get("size") or "auto").strip() or "auto"
    reference_count = len(reference_urls)
    if reference_count > MAX_MEDIA_REFERENCE_IMAGES:
        raise HTTPException(
            status_code=400,
            detail={"error": f"supports at most {MAX_MEDIA_REFERENCE_IMAGES} reference images"},
        )
    requested_count = _media_count(body.get("n"))
    params = _media_image_params(
        body,
        upstream_model=upstream_model,
        requested_size=requested_size,
        reference_urls=reference_urls,
    )
    task_ids: list[str] = []
    for image_index in range(requested_count):
        try:
            request_payload = {
                "model": upstream_model,
                "prompt": _media_batch_prompt(prompt, image_index, requested_count),
                "params": dict(params),
            }
            response = requests.post(
                _url("/v1/media/generate"),
                headers=_headers({"Content-Type": "application/json"}),
                json=request_payload,
                timeout=REQUEST_TIMEOUT_SECONDS,
                **proxy_settings.build_session_kwargs(),
            )
            _raise_for_status(response)
            response_data = _try_response_json(response)
            if not isinstance(response_data, dict):
                response_data = {"raw_text": str(response.text or "")[:2000]}
            try:
                task_id = _extract_media_task_id(response_data)
            except HTTPException as exc:
                reconciliation = _submission_reconciliation_metadata(
                    response,
                    response_data,
                    request_payload,
                    image_index=image_index,
                    requested_count=requested_count,
                )
                if task_ids:
                    reconciliation["accepted_task_ids"] = list(task_ids)
                raise RelaySubmissionUnknownHTTPException(
                    detail={"error": "media generation was accepted but the response did not include task_id"},
                    reconciliation=reconciliation,
                ) from exc
            task_ids.append(task_id)
        except RelaySubmissionUnknownHTTPException:
            raise
        except HTTPException as exc:
            if task_ids:
                raise RelaySubmittedHTTPException(
                    status_code=exc.status_code,
                    detail=exc.detail,
                    upstream_task_ids=task_ids,
                    submission_uncertain=True,
                ) from exc
            raise
    progress_callback = body.get("progress_callback")
    if callable(progress_callback):
        progress_callback("image_stream_resolve_start")
    result_urls: list[str] = []
    total_cost = 0.0
    has_complete_cost = True
    for task_id in task_ids:
        try:
            media_result = _poll_media_image_task(task_id)
        except HTTPException as exc:
            upstream_finished = bool(getattr(exc, "upstream_finished", False))
            raise RelaySubmittedHTTPException(
                status_code=exc.status_code,
                detail=exc.detail,
                upstream_task_ids=task_ids,
                submission_uncertain=not upstream_finished,
                upstream_finished=upstream_finished,
            ) from exc
        urls = media_result.get("result_urls") or [media_result.get("result_url")]
        result_urls.extend(str(url).strip() for url in urls[:1] if str(url or "").strip())
        cost = media_result.get("cost")
        if isinstance(cost, (int, float)) and not isinstance(cost, bool):
            total_cost += float(cost)
        else:
            has_complete_cost = False
    result = {
        "created": int(time.time()),
        "data": [{"url": url} for url in result_urls],
        "_media_task_id": task_ids[0],
        "_media_task_ids": task_ids,
    }
    if has_complete_cost:
        result["cost"] = total_cost
    return result


def _image_generations_with_reference_images(
    body: dict[str, Any],
    _fields: dict[str, str],
) -> dict[str, Any] | Iterator[dict[str, Any]]:
    payload: dict[str, Any] = {
        key: value
        for key, value in body.items()
        if key not in {
            "images",
            "image_urls",
            "mask",
            "base_url",
            "progress_callback",
            "preserve_subject",
            "preserve_product",
            "prompt_engine_mode",
            "subject_mutation_policy",
            "aspect_ratio",
            "image_size",
            "thinking_level",
        } and value is not None
    }
    payload["prompt"] = ensure_image_prompt_engineered(
        str(payload.get("prompt") or ""),
        has_reference=bool((body.get("images") or []) or (body.get("image_urls") or [])),
        preserve_subject=bool(body.get("preserve_subject") or body.get("preserve_product")),
        prompt_engine_mode=str(body.get("prompt_engine_mode") or "professional"),
        subject_mutation_policy=str(body.get("subject_mutation_policy") or "preserve"),
    )
    image_urls = [
        str(url).strip()
        for url in body.get("image_urls") or []
        if str(url).strip().lower().startswith(("http://", "https://"))
    ]
    local_images = list(body.get("images") or [])
    if _relay_uses_generations_for_image_edits() and local_images:
        try:
            uploaded_urls = reference_image_uploader.upload_images(local_images)
        except Exception as exc:
            if not image_urls:
                raise HTTPException(
                    status_code=502,
                    detail={"error": f"reference image upload failed: {exc}"},
                ) from exc
            uploaded_urls = []
        image_urls.extend(
            url
            for url in uploaded_urls
            if url and url.lower().startswith(("http://", "https://")) and url not in image_urls
        )
    data_url_images = [] if _relay_uses_generations_for_image_edits() else [
        _image_bytes_to_data_url(image_data, mime_type)
        for image_data, _filename, mime_type in local_images
    ]
    images = image_urls or data_url_images
    if not images:
        raise HTTPException(status_code=400, detail={"error": "image file or image_url is required"})
    if _relay_uses_generations_for_image_edits() and not image_urls:
        raise HTTPException(
            status_code=400,
            detail={"error": "this relay requires public http(s) image_url references for image edits; configure image_reference_upload"},
        )
    payload["images"] = images
    result = _json_post("/v1/images/generations", payload)
    return _with_response_cost(result) if isinstance(result, dict) else result


def _image_edit_should_fallback(exc: HTTPException) -> bool:
    if exc.status_code == 404:
        return True
    if exc.status_code != 400:
        return False
    try:
        detail_text = json.dumps(exc.detail, ensure_ascii=False)
    except Exception:
        detail_text = str(exc.detail)
    return "JSON" in detail_text or "request body" in detail_text.lower() or "/v1/images/edits" in detail_text


def _relay_uses_generations_for_image_edits() -> bool:
    return False


def requires_public_image_urls() -> bool:
    return is_enabled() and _relay_uses_generations_for_image_edits()


def supports_image_edit_masks(model: str | None = None) -> bool:
    return False


def _image_edits_multipart(
    body: dict[str, Any],
    fields: dict[str, str],
) -> dict[str, Any] | Iterator[dict[str, Any]]:
    multipart = CurlMime()
    has_files = False
    for image_data, filename, mime_type in body.get("images") or []:
        multipart.addpart(
            name="image",
            filename=filename or "image.png",
            content_type=mime_type or "image/png",
            data=image_data,
        )
        has_files = True
    for mask_data, filename, mime_type in body.get("mask") or []:
        multipart.addpart(
            name="mask",
            filename=filename or "mask.png",
            content_type=mime_type or "image/png",
            data=mask_data,
        )
        has_files = True
    if not has_files:
        multipart.close()
        raise HTTPException(status_code=400, detail={"error": "image file or image_url is required"})

    stream = str(fields.get("stream") or "").strip().lower() in {"1", "true", "yes", "on"}
    try:
        response = requests.post(
            _url("/v1/images/edits"),
            headers=_headers(),
            data=fields,
            multipart=multipart,
            stream=stream,
            timeout=STREAM_TIMEOUT_SECONDS if stream else REQUEST_TIMEOUT_SECONDS,
            **proxy_settings.build_session_kwargs(),
        )
    finally:
        multipart.close()
    _raise_for_status(response)
    if stream:
        return _iter_openai_sse(response)
    return _with_response_cost(_response_json_object(response))


def image_edits(body: dict[str, Any]) -> dict[str, Any] | Iterator[dict[str, Any]]:
    def execute() -> dict[str, Any] | Iterator[dict[str, Any]]:
        engineered_prompt = ensure_image_prompt_engineered(
            str(body.get("prompt") or ""),
            has_reference=bool((body.get("images") or []) or (body.get("image_urls") or [])),
            preserve_subject=bool(body.get("preserve_subject") or body.get("preserve_product")),
            prompt_engine_mode=str(body.get("prompt_engine_mode") or "professional"),
            subject_mutation_policy=str(body.get("subject_mutation_policy") or "preserve"),
        )
        engineered_body = {**body, "prompt": engineered_prompt}
        if _is_media_image_model(str(body.get("model") or "")):
            return _media_image_generation(engineered_body)
        fields = {
            key: str(value)
            for key, value in engineered_body.items()
            if key not in {
                "images",
                "image_urls",
                "mask",
                "base_url",
                "progress_callback",
                "preserve_subject",
                "preserve_product",
                "prompt_engine_mode",
                "subject_mutation_policy",
                "aspect_ratio",
                "image_size",
                "thinking_level",
            } and value is not None
        }
        if _relay_uses_generations_for_image_edits():
            return _image_generations_with_reference_images(engineered_body, fields)
        try:
            return _image_edits_multipart(engineered_body, fields)
        except HTTPException as exc:
            if not _image_edit_should_fallback(exc):
                raise
            return _image_generations_with_reference_images(engineered_body, fields)

    return run_with_relay_pool(settings(), "image_edits", execute)
