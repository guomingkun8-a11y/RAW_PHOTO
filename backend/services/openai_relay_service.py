from __future__ import annotations

import base64
import json
import re
import time
from typing import Any, Iterator

from curl_cffi import CurlMime, requests
from fastapi import HTTPException

from services.config import config
from services.openai_relay_pool import RelaySubmittedHTTPException, current_relay_account, run_with_relay_pool
from services.proxy_service import proxy_settings
from services import reference_image_uploader


STREAM_TIMEOUT_SECONDS = 300
REQUEST_TIMEOUT_SECONDS = 300
MEDIA_IMAGE_MODELS = {
    "gemini-3.1-flash-image-preview",
    "mj_imagine",
    "qwen-image",
    "wan2.6-image",
    "wan2.7-image",
}
MEDIA_IMAGE_MODEL_PREFIXES = (
    "doubao-seedream-",
    "kling-",
    "vidu-image-",
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


def image_generations(body: dict[str, Any]) -> dict[str, Any] | Iterator[dict[str, Any]]:
    def execute() -> dict[str, Any] | Iterator[dict[str, Any]]:
        payload = {
            key: value
            for key, value in body.items()
            if key not in {"base_url", "progress_callback"} and value is not None
        }
        if _is_media_image_model(str(payload.get("model") or "")):
            return _media_image_generation(body)
        return _json_post("/v1/images/generations", payload)

    return run_with_relay_pool(settings(), "image_generations", execute)


def _image_bytes_to_data_url(image_data: bytes, mime_type: str | None) -> str:
    return f"data:{mime_type or 'image/png'};base64,{base64.b64encode(image_data).decode('ascii')}"


def _is_media_image_model(model: str) -> bool:
    normalized = model.strip().lower()
    return normalized in MEDIA_IMAGE_MODELS or normalized.startswith(MEDIA_IMAGE_MODEL_PREFIXES)


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


def _media_status_payload(data: dict[str, Any]) -> dict[str, Any]:
    nested = data.get("data")
    return nested if isinstance(nested, dict) else data


def _media_result_url(data: dict[str, Any]) -> str:
    payload = _media_status_payload(data)
    for key in ("result_url", "resultUrl", "output_url", "url"):
        value = payload.get(key)
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            return value
    for key in ("result_urls", "resultUrls", "urls"):
        value = payload.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str) and item.startswith(("http://", "https://")):
                    return item
    result = payload.get("result")
    if isinstance(result, dict):
        return _media_result_url(result)
    if isinstance(result, list):
        for item in result:
            if isinstance(item, str) and item.startswith(("http://", "https://")):
                return item
            if isinstance(item, dict):
                url = _media_result_url(item)
                if url:
                    return url
    return ""


def _media_error_text(data: dict[str, Any]) -> str:
    payload = _media_status_payload(data)
    for key in ("error", "message", "msg", "fail_reason", "failure_reason", "status"):
        value = payload.get(key)
        if value:
            return str(value)
    return json.dumps(data, ensure_ascii=False)[:500]


def _media_task_finished(data: dict[str, Any]) -> bool:
    payload = _media_status_payload(data)
    if payload.get("is_final") is True:
        return True
    progress = str(payload.get("progress") or "").strip().rstrip("%")
    if progress == "100":
        return True
    status = str(payload.get("status") or payload.get("state") or "").lower()
    return any(marker in status for marker in ("success", "succeeded", "done", "complete", "failed", "error", "完成", "失败"))


def _media_task_failed(data: dict[str, Any]) -> bool:
    payload = _media_status_payload(data)
    status = str(payload.get("status") or payload.get("state") or payload.get("error") or "").lower()
    return any(marker in status for marker in ("failed", "fail", "error", "失败"))


def _poll_media_image_task(task_id: str) -> str:
    deadline = time.time() + REQUEST_TIMEOUT_SECONDS
    last_status: dict[str, Any] = {}
    while time.time() <= deadline:
        response = requests.get(
            _url("/v1/skills/task-status"),
            headers=_headers(),
            params={"task_id": task_id},
            timeout=30,
            **proxy_settings.build_session_kwargs(),
        )
        _raise_for_status(response)
        data = _response_json_object(response)
        last_status = data
        if not _media_task_finished(data):
            time.sleep(2)
            continue
        result_url = _media_result_url(data)
        if result_url and not _media_task_failed(data):
            return result_url
        raise HTTPException(status_code=502, detail={"error": f"media generation failed: {_media_error_text(data)}"})
    raise HTTPException(status_code=504, detail={"error": f"media generation timed out: {_media_error_text(last_status)}"})


def _media_image_generation(body: dict[str, Any]) -> dict[str, Any]:
    model = str(body.get("model") or "").strip()
    prompt = str(body.get("prompt") or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail={"error": "prompt is required"})
    params: dict[str, Any] = {
        "aspectRatio": _aspect_ratio_from_size(body.get("size")),
        "imageSize": _image_size_tier_from_size(body.get("size")),
    }
    reference_urls = _reference_image_urls(body)
    if reference_urls:
        params["images"] = reference_urls
    response = requests.post(
        _url("/v1/media/generate"),
        headers=_headers({"Content-Type": "application/json"}),
        json={"model": model, "prompt": prompt, "params": params},
        timeout=REQUEST_TIMEOUT_SECONDS,
        **proxy_settings.build_session_kwargs(),
    )
    _raise_for_status(response)
    task_id = _extract_media_task_id(_response_json_object(response))
    progress_callback = body.get("progress_callback")
    if callable(progress_callback):
        progress_callback("image_stream_resolve_start")
    try:
        result_url = _poll_media_image_task(task_id)
    except HTTPException as exc:
        raise RelaySubmittedHTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return {"created": int(time.time()), "data": [{"url": result_url}], "_media_task_id": task_id}


def _image_generations_with_reference_images(
    body: dict[str, Any],
    _fields: dict[str, str],
) -> dict[str, Any] | Iterator[dict[str, Any]]:
    payload: dict[str, Any] = {
        key: value
        for key, value in body.items()
        if key not in {"images", "image_urls", "mask", "base_url", "progress_callback", "preserve_subject", "preserve_product"} and value is not None
    }
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
    return _json_post("/v1/images/generations", payload)


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
    return _response_json_object(response)


def image_edits(body: dict[str, Any]) -> dict[str, Any] | Iterator[dict[str, Any]]:
    def execute() -> dict[str, Any] | Iterator[dict[str, Any]]:
        if _is_media_image_model(str(body.get("model") or "")):
            return _media_image_generation(body)
        fields = {
            key: str(value)
            for key, value in body.items()
            if key not in {"images", "image_urls", "mask", "base_url", "progress_callback", "preserve_subject", "preserve_product"} and value is not None
        }
        if _relay_uses_generations_for_image_edits():
            return _image_generations_with_reference_images(body, fields)
        try:
            return _image_edits_multipart(body, fields)
        except HTTPException as exc:
            if not _image_edit_should_fallback(exc):
                raise
            return _image_generations_with_reference_images(body, fields)

    return run_with_relay_pool(settings(), "image_edits", execute)
