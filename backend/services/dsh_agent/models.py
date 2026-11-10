from __future__ import annotations

import json
import os
from urllib.parse import urlsplit, urlunsplit


DEFAULT_MODEL = "tt-5.6-sol"
MAX_OUTPUT_TOKENS = 8192
COMPACTION_HEADROOM = 8192
COMPACTION_RATIO = 0.8
MODELS = (
    {"id": "tt-5.6-sol", "label": "GPT 5.6 Sol", "protocol": "openai"},
    {"id": "gem-3.5-flash", "label": "GEM 3.5 Flash", "protocol": "gemini"},
    {"id": "gem-3.8-flash", "label": "GEM 3.8 Flash", "protocol": "gemini"},
    {"id": "tt-6-sol", "label": "GPT 6 Sol", "protocol": "openai"},
    {"id": "tt-6.1-sol", "label": "GPT 6.1 Sol", "protocol": "openai"},
    {"id": "deepseek-v4.1-flash", "label": "DS-V4.1-Flash", "protocol": "openai"},
    {"id": "deepseek-v4-flash", "label": "DS-V4-Flash", "protocol": "openai"},
)


def resolve_model(model: str | None = None) -> str:
    value = (model if model is not None else os.getenv("DSH_AGENT_MODEL") or DEFAULT_MODEL).strip()
    if value not in {item["id"] for item in MODELS}:
        raise ValueError("unsupported DSH chat model")
    return value


def model_protocol(model: str) -> str:
    model = resolve_model(model)
    return next(item["protocol"] for item in MODELS if item["id"] == model)


def runtime_provider(model: str) -> str:
    if resolve_model(model) == DEFAULT_MODEL:
        return "deepseek-official"
    return "google" if model_protocol(model) == "gemini" else "lingke-openai"


def context_budget(model: str) -> dict:
    model = resolve_model(model)
    overrides = json.loads(os.getenv("DSH_AGENT_MODEL_CONTEXT_WINDOWS") or "{}")
    if not isinstance(overrides, dict) or set(overrides) - {item["id"] for item in MODELS}:
        raise ValueError("invalid DSH_AGENT_MODEL_CONTEXT_WINDOWS model mapping")
    window = overrides.get(model, int(os.getenv("DSH_AGENT_CONTEXT_WINDOW") or "128000"))
    if isinstance(window, bool) or not isinstance(window, int) or window < 32768:
        raise ValueError("DSH context window must be an integer of at least 32768 tokens")
    return {
        "context_window": window,
        "max_output_tokens": MAX_OUTPUT_TOKENS,
        "compact_at_tokens": int(min(window * COMPACTION_RATIO, window - MAX_OUTPUT_TOKENS - COMPACTION_HEADROOM)),
        "limit_source": "deployment",
    }


def relay_providers(base_url: str) -> dict[str, dict]:
    parsed = urlsplit(base_url.rstrip("/"))
    path = parsed.path.rstrip("/")
    if path.endswith(("/v1", "/v1beta")):
        path = path.rsplit("/", 1)[0]
    root = urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
    common = {
        "apiKeyEnv": "DSH_AGENT_RELAY_API_KEY",
        # Deployment limits, not claims about the upstream models' full capacity.
        "defaultContextWindow": context_budget(DEFAULT_MODEL)["context_window"],
        "defaultMaxTokens": MAX_OUTPUT_TOKENS,
        "timeoutMs": 120000,
        "streamIdleTimeoutMs": 120000,
        "retryPolicy": {"mode": "normal", "maxRetries": 1},
    }
    return {
        "lingke-openai": {
            **common,
            "api": "openai-completions",
            "baseURL": f"{root}/v1",
            "models": [
                {"id": item["id"], "name": item["label"], "contextWindow": context_budget(item["id"])["context_window"], "maxTokens": MAX_OUTPUT_TOKENS, "input": ["text"] if item["id"].startswith("deepseek-") else ["text", "image"], **(
                    {"compat": {"thinkingFormat": "deepseek"}} if item["id"].startswith("deepseek-") else {}
                )}
                for item in MODELS if item["protocol"] == "openai"
            ],
        },
        "google": {
            **common,
            "baseURL": f"{root}/v1beta",
            "models": [{"id": item["id"], "name": item["label"], "contextWindow": context_budget(item["id"])["context_window"], "maxTokens": MAX_OUTPUT_TOKENS, "input": ["text", "image"]} for item in MODELS if item["protocol"] == "gemini"],
        },
    }
