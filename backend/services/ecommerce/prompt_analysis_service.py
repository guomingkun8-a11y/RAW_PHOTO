from __future__ import annotations

import json
import math
from pathlib import Path
import re
from typing import Any, Iterator

from curl_cffi import requests
from fastapi import HTTPException

from services.platform.config import config
from services.providers.openai_relay_pool import current_relay_account, run_with_relay_pool
from services.platform.proxy_service import proxy_settings


REQUEST_TIMEOUT_SECONDS = 120
MAX_REFERENCE_IMAGES = 6
MAX_IMAGE_DATA_URL_CHARS = 10 * 1024 * 1024
MAX_TOTAL_IMAGE_DATA_URL_CHARS = 24 * 1024 * 1024
DEFAULT_PROMPT_ANALYSIS_MODEL = "gpt-5.6-sol"
CHAT_MODEL_ALIASES = {
    "gpt-5.6-sol": "tt-5.6-sol",
}
LEGACY_CHAT_MODEL_ALIASES = {
    "gpt-4o": DEFAULT_PROMPT_ANALYSIS_MODEL,
    "gpt4o": DEFAULT_PROMPT_ANALYSIS_MODEL,
}
MULTI_IMAGE_REQUEST_RE = re.compile(
    r"(?:[2-9]|1\d|[二两三四五六七八九十])\s*(?:张|幅|款|版|组|个)|"
    r"多张|几张|多幅|几幅|多款|几款|多个版本|多种场景|不同场景|不同卖点|不同版本|不同风格"
)


def _relay_settings() -> dict[str, object]:
    return config.get_openai_relay_settings()


def is_prompt_analysis_enabled() -> bool:
    relay = _relay_settings()
    api_key = str(relay.get("api_key") or "").strip()
    api_keys = relay.get("api_keys")
    has_api_key = bool(api_key)
    if isinstance(api_keys, str):
        has_api_key = has_api_key or bool(api_keys.strip())
    elif isinstance(api_keys, (list, tuple, set)):
        has_api_key = has_api_key or any(str(item or "").strip() for item in api_keys)
    return bool(relay.get("enabled") and relay.get("base_url") and has_api_key)


def _active_relay_settings() -> dict[str, object]:
    relay = _relay_settings()
    account = current_relay_account()
    if account is None:
        return relay
    active = dict(relay)
    active["base_url"] = account.base_url
    active["api_key"] = account.api_key
    return active


def _relay_url(path: str) -> str:
    relay = _active_relay_settings()
    base_url = str(relay.get("base_url") or "").strip().rstrip("/")
    if not base_url:
        raise HTTPException(status_code=500, detail={"error": "openai_relay.base_url is required"})
    normalized_path = "/" + path.strip("/")
    if base_url.endswith("/v1") and normalized_path.startswith("/v1/"):
        normalized_path = normalized_path.removeprefix("/v1")
    return f"{base_url}{normalized_path}"


def _relay_headers() -> dict[str, str]:
    headers = _relay_auth_headers()
    headers["Content-Type"] = "application/json"
    return headers


def _relay_auth_headers() -> dict[str, str]:
    relay = _active_relay_settings()
    api_key = str(relay.get("api_key") or "").strip()
    if not api_key:
        raise HTTPException(status_code=500, detail={"error": "openai_relay.api_key is required"})
    return {"Authorization": f"Bearer {api_key}"}


def _prompt_analysis_model(model: str = "") -> str:
    relay = _relay_settings()
    selected = str(model or relay.get("prompt_analysis_model") or DEFAULT_PROMPT_ANALYSIS_MODEL).strip()
    return LEGACY_CHAT_MODEL_ALIASES.get(selected.lower(), selected)


def prompt_analysis_model(model: str = "") -> str:
    return _prompt_analysis_model(model)


def upstream_chat_model(model: object) -> str:
    value = str(model or "").strip()
    normalized = value.lower()
    return CHAT_MODEL_ALIASES.get(normalized, value)


def is_reasoning_chat_model(model: object) -> bool:
    value = str(model or "").strip().lower()
    return value.startswith("gpt-5") or value.startswith(("o1", "o3", "o4"))


def _normalize_response_cost(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) and number >= 0 else None
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
        return number if math.isfinite(number) and number >= 0 else None
    return None


def _iter_response_cost_candidates(value: object) -> Iterator[object]:
    if isinstance(value, dict):
        if "cost" in value:
            yield value.get("cost")
        for key in ("data", "result", "usage", "meta", "metadata", "billing"):
            nested = value.get(key)
            if nested is value:
                continue
            yield from _iter_response_cost_candidates(nested)
        return
    if isinstance(value, list):
        for item in value:
            yield from _iter_response_cost_candidates(item)


def response_cost(data: object) -> float | None:
    for candidate in _iter_response_cost_candidates(data):
        cost = _normalize_response_cost(candidate)
        if cost is not None:
            return cost
    return None


def _chat_payload_model(model: str) -> str:
    return upstream_chat_model(model)


def _apply_sampling_params(payload: dict[str, Any], *, model: str, temperature: float) -> None:
    if not is_reasoning_chat_model(model):
        payload["temperature"] = temperature


def validate_reference_images(
    images: list[dict[str, str]],
    *,
    required: bool = True,
) -> list[dict[str, str]]:
    if not images and required:
        raise HTTPException(status_code=400, detail={"error": "reference images are required"})
    if not images:
        return []
    if len(images) > MAX_REFERENCE_IMAGES:
        raise HTTPException(status_code=400, detail={"error": f"supports up to {MAX_REFERENCE_IMAGES} reference images"})

    total_chars = 0
    normalized: list[dict[str, str]] = []
    for index, image in enumerate(images, start=1):
        data_url = str(image.get("data_url") or image.get("dataUrl") or "").strip()
        if not data_url.startswith("data:image/") or ";base64," not in data_url:
            raise HTTPException(status_code=400, detail={"error": f"reference image {index} must be a data:image base64 URL"})
        if len(data_url) > MAX_IMAGE_DATA_URL_CHARS:
            raise HTTPException(status_code=413, detail={"error": f"reference image {index} is too large"})
        total_chars += len(data_url)
        normalized.append({
            "name": str(image.get("name") or f"reference-{index}.png").strip()[:120],
            "data_url": data_url,
        })

    if total_chars > MAX_TOTAL_IMAGE_DATA_URL_CHARS:
        raise HTTPException(status_code=413, detail={"error": "reference images are too large"})
    return normalized


def _validate_images(images: list[dict[str, str]]) -> list[dict[str, str]]:
    return validate_reference_images(images)


def _extract_message_content(data: dict[str, Any]) -> str:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise HTTPException(status_code=502, detail={"error": "vision model response has no choices"})
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(parts).strip()
    raise HTTPException(status_code=502, detail={"error": "vision model response content is empty"})


def _parse_json_content(content: str) -> dict[str, Any]:
    text = content.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.S)
    if fenced:
        text = fenced.group(1)
    else:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start:end + 1]
    try:
        parsed = json.loads(text)
    except Exception as exc:
        preview = content[:500]
        detail = {"error": "vision model did not return valid JSON", "preview": preview}
        if any(marker in preview for marker in ("？", "?", "请补充", "需要", "想要", "是指")):
            detail["clarificationQuestion"] = preview
        raise HTTPException(status_code=502, detail=detail) from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=502, detail={"error": "vision model JSON is not an object"})
    return parsed


def _chat_completion_once(payload: dict[str, Any]) -> dict[str, Any]:
    response = requests.post(
        _relay_url("/v1/chat/completions"),
        headers=_relay_headers(),
        json=payload,
        timeout=REQUEST_TIMEOUT_SECONDS,
        **proxy_settings.build_session_kwargs(),
    )
    if 200 <= response.status_code < 300:
        try:
            data = response.json()
        except Exception as exc:
            raise HTTPException(status_code=502, detail={"error": "vision model response is not JSON"}) from exc
        if not isinstance(data, dict):
            raise HTTPException(status_code=502, detail={"error": "vision model response is not a JSON object"})
        return data

    detail: Any
    try:
        detail = response.json()
    except Exception:
        detail = {"error": {"message": str(response.text or "")[:500] or f"HTTP {response.status_code}"}}
    raise HTTPException(status_code=response.status_code, detail=detail)


def _chat_completion(payload: dict[str, Any]) -> dict[str, Any]:
    return run_with_relay_pool(
        _relay_settings(),
        "prompt_analysis",
        lambda: _chat_completion_once(payload),
    )


def request_json_completion(
    *,
    model: str,
    system_prompt: str,
    content: str | list[dict[str, Any]],
    max_tokens: int = 1800,
    temperature: float = 0.2,
) -> dict[str, Any]:
    payload = {
        "model": _chat_payload_model(model),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content},
        ],
        "max_tokens": max(256, min(8000, int(max_tokens))),
        "response_format": {"type": "json_object"},
    }
    _apply_sampling_params(payload, model=model, temperature=temperature)
    try:
        data = _chat_completion(payload)
    except HTTPException as exc:
        if "response_format" not in str(exc.detail):
            raise
        payload.pop("response_format", None)
        data = _chat_completion(payload)
    return _parse_json_content(_extract_message_content(data))


def request_text_completion(
    *,
    model: str,
    system_prompt: str,
    content: str | list[dict[str, Any]],
    max_tokens: int = 1800,
    temperature: float = 0.3,
) -> str:
    payload = {
        "model": _chat_payload_model(model),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content},
        ],
        "max_tokens": max(256, min(8000, int(max_tokens))),
    }
    _apply_sampling_params(payload, model=model, temperature=temperature)
    data = _chat_completion(payload)
    return _extract_message_content(data)


def _audio_transcription_once(payload: dict[str, Any]) -> dict[str, Any]:
    file_path = Path(str(payload["file_path"]))
    with file_path.open("rb") as handle:
        response = requests.post(
            _relay_url("/v1/audio/transcriptions"),
            headers=_relay_auth_headers(),
            data={
                "model": str(payload.get("model") or "whisper-1"),
                "response_format": "json",
                **({"prompt": str(payload.get("prompt") or "")} if payload.get("prompt") else {}),
            },
            files={"file": (file_path.name, handle, str(payload.get("mime_type") or "audio/mpeg"))},
            timeout=int(payload.get("timeout") or REQUEST_TIMEOUT_SECONDS),
            **proxy_settings.build_session_kwargs(),
        )
    if 200 <= response.status_code < 300:
        try:
            data = response.json()
        except Exception as exc:
            raise HTTPException(status_code=502, detail={"error": "audio transcription response is not JSON"}) from exc
        if not isinstance(data, dict):
            raise HTTPException(status_code=502, detail={"error": "audio transcription response is not a JSON object"})
        return data
    try:
        detail = response.json()
    except Exception:
        detail = {"error": {"message": str(response.text or "")[:500] or f"HTTP {response.status_code}"}}
    raise HTTPException(status_code=response.status_code, detail=detail)


def request_audio_transcription(
    *,
    model: str,
    file_path: str | Path,
    prompt: str = "",
    mime_type: str = "audio/mpeg",
    timeout: int = REQUEST_TIMEOUT_SECONDS,
) -> str:
    payload = {
        "model": str(model or "whisper-1").strip() or "whisper-1",
        "file_path": str(file_path),
        "prompt": prompt,
        "mime_type": mime_type,
        "timeout": timeout,
    }
    data = run_with_relay_pool(
        _relay_settings(),
        "audio_transcription",
        lambda: _audio_transcription_once(payload),
    )
    text = data.get("text")
    if isinstance(text, str):
        return text.strip()
    segments = data.get("segments")
    if isinstance(segments, list):
        return "\n".join(str(item.get("text") or "").strip() for item in segments if isinstance(item, dict)).strip()
    return ""


def _normalize_result(parsed: dict[str, Any], model: str) -> dict[str, Any]:
    analysis = parsed.get("analysis") if isinstance(parsed.get("analysis"), dict) else {}
    suggestions = parsed.get("suggestions") if isinstance(parsed.get("suggestions"), list) else []
    suggestion_prompt = str(parsed.get("suggestionPrompt") or parsed.get("suggestion_prompt") or "").strip()
    optimized_prompt = str(parsed.get("optimizedPrompt") or parsed.get("optimized_prompt") or "").strip()
    negative_prompt = str(parsed.get("negativePrompt") or parsed.get("negative_prompt") or "").strip()
    if not optimized_prompt:
        raise HTTPException(status_code=502, detail={"error": "vision model response missing optimizedPrompt"})
    if not suggestion_prompt:
        suggestion_prompt = optimized_prompt
    return {
        "model": model,
        "analysis": {
            "subject": str(analysis.get("subject") or "").strip(),
            "materials": str(analysis.get("materials") or "").strip(),
            "style": str(analysis.get("style") or "").strip(),
            "composition": str(analysis.get("composition") or "").strip(),
            "textLogo": str(analysis.get("textLogo") or analysis.get("text_logo") or "").strip(),
            "risks": str(analysis.get("risks") or "").strip(),
        },
        "suggestions": [str(item).strip() for item in suggestions if str(item).strip()][:6],
        "suggestionPrompt": suggestion_prompt,
        "optimizedPrompt": optimized_prompt,
        "negativePrompt": negative_prompt,
    }


def _has_multi_image_request(prompt: str) -> bool:
    return bool(MULTI_IMAGE_REQUEST_RE.search(str(prompt or "")))


def analyze_image_prompt(body: dict[str, Any]) -> dict[str, Any]:
    if not _relay_settings().get("enabled"):
        raise HTTPException(status_code=400, detail={"error": "openai_relay is not enabled"})

    images = _validate_images(list(body.get("images") or []))
    action = str(body.get("action") or "optimize").strip()
    mode = str(body.get("mode") or "single").strip()
    prompt = str(body.get("prompt") or "").strip()
    product = body.get("product") if isinstance(body.get("product"), dict) else {}
    model = _prompt_analysis_model(str(body.get("model") or ""))
    if not model:
        raise HTTPException(status_code=500, detail={"error": "prompt analysis model is required"})

    product_context = {
        "name": str(product.get("name") or "").strip(),
        "sku": str(product.get("sku") or "").strip(),
        "brand": str(product.get("brand") or "").strip(),
        "category": str(product.get("category") or "").strip(),
        "selling_points": str(product.get("selling_points") or product.get("sellingPoints") or "").strip(),
    }
    multi_image_request = _has_multi_image_request(prompt)

    user_text = {
        "task": "Analyze reference product images and produce ecommerce prompt guidance.",
        "action": action,
        "current_prompt": prompt,
        "product_context": product_context,
        "multi_image_request": multi_image_request,
        "requirements": [
            "Identify the visible product subject, material, package structure, logo/text areas, composition, lighting, and style.",
            "Do not invent claims that are not visible or provided in product_context.",
            "Preserve product shape, package text, logo, layout, and core identity in the optimized prompt.",
            "Return Chinese copy suitable for an AI ecommerce image generation tool.",
            "If multi_image_request is true, optimizedPrompt must contain a numbered list of separate image directions matching the requested count when clear, or 4 directions when the count is unclear.",
            "Each numbered direction must derive its selling point, scene, and visible copy from the current product evidence, reference images, product_context, and user text.",
            "Do not use a fixed cross-category default set of selling points; never hard-code cleaning-power, drying-speed, gentleness, fragrance, or usage-scenario claims unless visible or provided.",
            "For multi_image_request, write each numbered item as 场景一/场景二 or 设计1/设计2 so downstream can assign one item to each image.",
        ],
        "json_schema": {
            "analysis": {
                "subject": "string",
                "materials": "string",
                "style": "string",
                "composition": "string",
                "textLogo": "string",
                "risks": "string",
            },
            "suggestions": ["string"],
            "suggestionPrompt": "string",
            "optimizedPrompt": "string",
            "negativePrompt": "string",
        },
    }

    content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                "你是资深 AI 电商图片 Prompt 设计师和视觉分析师。"
                "请基于用户上传的参考图进行真实图片分析，再输出严格 JSON。"
                "不要输出 Markdown，不要输出 JSON 以外的解释。\n\n"
                f"{json.dumps(user_text, ensure_ascii=False)}"
            ),
        }
    ]
    for image in images:
        content.append({
            "type": "image_url",
            "image_url": {"url": image["data_url"], "detail": "high"},
        })

    payload = {
        "model": _chat_payload_model(model),
        "messages": [
            {
                "role": "system",
                "content": "You analyze product reference images and return strict JSON only.",
            },
            {
                "role": "user",
                "content": content,
            },
        ],
        "max_tokens": 1800,
        "response_format": {"type": "json_object"},
    }
    _apply_sampling_params(payload, model=model, temperature=0.2)

    try:
        data = _chat_completion(payload)
    except HTTPException as exc:
        if "response_format" not in str(exc.detail):
            raise
        payload.pop("response_format", None)
        data = _chat_completion(payload)

    content_text = _extract_message_content(data)
    parsed = _parse_json_content(content_text)
    return _normalize_result(parsed, model)
