from __future__ import annotations

from typing import Any

from services.account_service import account_service
from services.cache_utils import TTLCache
from services.openai_backend_api import OpenAIBackendAPI
from utils.helper import CODEX_IMAGE_MODEL


_MODELS_CACHE = TTLCache[str, dict[str, Any]](ttl_seconds=5.0, max_items=8)


def _cache_key() -> str:
    return f"{id(OpenAIBackendAPI.list_models)}:{id(account_service.list_accounts)}"


def _build_models_result() -> dict[str, Any]:
    backend = OpenAIBackendAPI()
    try:
        result = backend.list_models()
    finally:
        backend.close()
    if not isinstance(result, dict):
        return result

    data = result.get("data")
    if not isinstance(data, list):
        return result

    result = dict(result)
    result["data"] = list(data)
    seen = {str(item.get("id") or "").strip() for item in result["data"] if isinstance(item, dict)}
    dynamic_models: set[str] = set()
    accounts = account_service.list_accounts()
    web_image_accounts = [
        account
        for account in accounts
        if isinstance(account, dict)
    ]
    codex_types = {
        normalized
        for account in accounts
        if isinstance(account, dict)
           and account_service._normalize_source_type(account.get("source_type")) == "codex"
           and (normalized := account_service._normalize_account_type(account.get("type")))
    }

    if web_image_accounts:
        dynamic_models.add("gpt-image-2")
    if codex_types & {"Plus", "Team", "Pro"}:
        dynamic_models.add(CODEX_IMAGE_MODEL)
    if "Plus" in codex_types:
        dynamic_models.add(f"plus-{CODEX_IMAGE_MODEL}")
    if "Team" in codex_types:
        dynamic_models.add(f"team-{CODEX_IMAGE_MODEL}")
    if "Pro" in codex_types:
        dynamic_models.add(f"pro-{CODEX_IMAGE_MODEL}")

    for model in sorted(dynamic_models):
        if model not in seen:
            result["data"].append({
                "id": model,
                "object": "model",
                "created": 0,
                "owned_by": "gmkraw",
                "permission": [],
                "root": model,
                "parent": None,
            })
    return result


def list_models() -> dict[str, Any]:
    return _MODELS_CACHE.get_or_set(_cache_key(), _build_models_result)
