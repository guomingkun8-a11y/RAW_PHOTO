from __future__ import annotations

import json
import re
from typing import Any

from curl_cffi import requests
from fastapi import HTTPException

from services.config import config
from services.proxy_service import proxy_settings


REQUEST_TIMEOUT_SECONDS = 120
MAX_REFERENCE_IMAGES = 4
MAX_IMAGE_DATA_URL_CHARS = 10 * 1024 * 1024
MAX_TOTAL_IMAGE_DATA_URL_CHARS = 24 * 1024 * 1024
DEFAULT_PROMPT_ANALYSIS_MODEL = "gpt-4o"


def _relay_settings() -> dict[str, object]:
    return config.get_openai_relay_settings()


def _relay_url(path: str) -> str:
    relay = _relay_settings()
    base_url = str(relay.get("base_url") or "").strip().rstrip("/")
    if not base_url:
        raise HTTPException(status_code=500, detail={"error": "openai_relay.base_url is required"})
    normalized_path = "/" + path.strip("/")
    if base_url.endswith("/v1") and normalized_path.startswith("/v1/"):
        normalized_path = normalized_path.removeprefix("/v1")
    return f"{base_url}{normalized_path}"


def _relay_headers() -> dict[str, str]:
    relay = _relay_settings()
    api_key = str(relay.get("api_key") or "").strip()
    if not api_key:
        raise HTTPException(status_code=500, detail={"error": "openai_relay.api_key is required"})
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def _prompt_analysis_model(model: str = "") -> str:
    relay = _relay_settings()
    return str(model or relay.get("prompt_analysis_model") or DEFAULT_PROMPT_ANALYSIS_MODEL).strip()


def _validate_images(images: list[dict[str, str]]) -> list[dict[str, str]]:
    if not images:
        raise HTTPException(status_code=400, detail={"error": "reference images are required"})
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
        raise HTTPException(
            status_code=502,
            detail={"error": "vision model did not return valid JSON", "preview": content[:500]},
        ) from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=502, detail={"error": "vision model JSON is not an object"})
    return parsed


def _chat_completion(payload: dict[str, Any]) -> dict[str, Any]:
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

    user_text = {
        "task": "Analyze reference product images and produce ecommerce prompt guidance.",
        "action": action,
        "current_prompt": prompt,
        "product_context": product_context,
        "requirements": [
            "Identify the visible product subject, material, package structure, logo/text areas, composition, lighting, and style.",
            "Do not invent claims that are not visible or provided in product_context.",
            "Preserve product shape, package text, logo, layout, and core identity in the optimized prompt.",
            "Return Chinese copy suitable for an AI ecommerce image generation tool.",
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
        "model": model,
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
        "temperature": 0.2,
        "max_tokens": 1800,
        "response_format": {"type": "json_object"},
    }

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
