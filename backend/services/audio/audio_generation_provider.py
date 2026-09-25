from __future__ import annotations

import hashlib
import json
import math
import threading
import time
from typing import Any, Callable, Iterator
from urllib.parse import urljoin

from curl_cffi import requests

from services.platform.config import config
from services.platform.proxy_service import proxy_settings


REQUEST_TIMEOUT_SECONDS = 120
RESULT_URL_GRACE_SECONDS = 90
_API_KEY_LOCK = threading.Lock()
_API_KEY_INDEX = 0


class AudioGenerationProviderError(RuntimeError):
    """A retry resumes the known task; uncertain submissions must not be repeated."""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        submission_uncertain: bool = False,
        cost: int | float | None = None,
        upstream_task_id: str = "",
        credential_id: str = "",
        upstream_finished: bool = False,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.submission_uncertain = submission_uncertain
        self.cost = cost
        self.upstream_task_id = upstream_task_id
        self.credential_id = credential_id
        self.upstream_finished = upstream_finished


def _clean(value: object, default: str = "", limit: int = 12000) -> str:
    text = str(value if value is not None else default).strip()
    return (text or default)[:limit]


def _settings() -> dict[str, object]:
    return config.get_audio_generation_settings()


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
        raise AudioGenerationProviderError("audio_generation.base_url is required")
    normalized = "/" + _clean(path).lstrip("/")
    if base_url.endswith("/v1") and normalized.startswith("/v1/"):
        normalized = normalized.removeprefix("/v1")
    return urljoin(base_url + "/", normalized.lstrip("/"))


def _next_api_key(settings: dict[str, object]) -> str:
    global _API_KEY_INDEX
    api_keys = _api_keys(settings)
    if not api_keys:
        raise AudioGenerationProviderError("audio_generation.api_key is required")
    with _API_KEY_LOCK:
        api_key = api_keys[_API_KEY_INDEX % len(api_keys)]
        _API_KEY_INDEX += 1
    return api_key


def _credential_id(api_key: str) -> str:
    return "sha256:" + hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def _resolve_api_key(settings: dict[str, object], credential_id: str) -> str:
    for api_key in _api_keys(settings):
        if credential_id and _credential_id(api_key) == credential_id:
            return api_key
    raise AudioGenerationProviderError(
        "the original audio generation credential is missing or unavailable",
        credential_id=credential_id,
    )


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def _try_json(response: requests.Response) -> Any:
    try:
        return response.json()
    except Exception:
        return None


def _redact_credentials(value: Any, api_keys: list[str]) -> Any:
    if isinstance(value, str):
        for api_key in sorted(api_keys, key=len, reverse=True):
            value = value.replace(api_key, "[REDACTED]")
        return value
    if isinstance(value, dict):
        return {
            _redact_credentials(key, api_keys): _redact_credentials(item, api_keys)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_credentials(item, api_keys) for item in value]
    return value


def _response_payload(
    response: requests.Response,
    *,
    settings: dict[str, object],
    submitting: bool,
    credential_id: str,
    upstream_task_id: str = "",
) -> dict[str, Any]:
    data = _redact_credentials(_try_json(response), _api_keys(settings))
    status_code = int(response.status_code)
    if not 200 <= status_code < 300:
        payload = data if isinstance(data, dict) else {}
        response_task_id = _extract_task_id(payload) if submitting else ""
        uncertain = submitting and not response_task_id and (
            status_code == 408 or status_code >= 500 or status_code < 400
        )
        retryable = (
            status_code == 429
            or bool(response_task_id)
            or (not submitting and (status_code == 408 or status_code >= 500))
        )
        message = _error_text(payload) if payload else "upstream request failed"
        raise AudioGenerationProviderError(
            f"audio generation HTTP {status_code}: {message}",
            retryable=retryable,
            submission_uncertain=uncertain,
            cost=_extract_cost(payload),
            upstream_task_id=upstream_task_id or response_task_id,
            credential_id=credential_id,
        )
    if not isinstance(data, dict):
        raise AudioGenerationProviderError(
            "audio generation response is not a JSON object",
            retryable=not submitting,
            submission_uncertain=submitting,
            upstream_task_id=upstream_task_id,
            credential_id=credential_id,
        )
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
        for key in ("task_id", "taskId"):
            raw = item.get(key)
            value = _clean(raw, limit=191) if isinstance(raw, (str, int)) and not isinstance(raw, bool) else ""
            if value:
                return value
    for item in (_nested_dict(data), data):
        raw = item.get("id")
        if isinstance(raw, (str, int)) and not isinstance(raw, bool) and _clean(raw):
            return _clean(raw, limit=191)
    return ""


def _extract_cost(data: dict[str, Any]) -> int | float | None:
    for item in _iter_nested(data):
        if not isinstance(item, dict) or "cost" not in item:
            continue
        value = item.get("cost")
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, (int, float)) and value >= 0 and (isinstance(value, int) or math.isfinite(value)):
            return value
        if isinstance(value, str):
            text = value.strip().replace(",", "")
            if not text:
                continue
            try:
                number = float(text)
            except ValueError:
                continue
            if math.isfinite(number) and number >= 0:
                return int(number) if number.is_integer() else number
    return None


def _extract_audio_url(data: dict[str, Any]) -> str:
    for item in _iter_nested(data):
        if not isinstance(item, dict):
            continue
        for key in ("audio_url", "audioUrl", "result_url", "resultUrl", "output_url", "outputUrl", "url"):
            value = _clean(item.get(key), limit=2000)
            if value.startswith(("http://", "https://")):
                return value
        for key in ("audio_urls", "audioUrls", "result_urls", "resultUrls", "urls"):
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


def _normalized_status(data: dict[str, Any]) -> str:
    payload = _nested_dict(data)
    values = {
        _clean(payload.get(key), limit=120).casefold().replace("-", "_").replace(" ", "_")
        for key in ("status", "state", "status_group", "task_status", "taskStatus")
        if _clean(payload.get(key))
    }
    if values & {"failed", "fail", "failure", "error", "rejected", "失败", "错误"}:
        return "failed"
    if values & {"canceled", "cancelled", "aborted", "已取消", "取消"}:
        return "canceled"
    if values & {"processing", "running", "in_progress", "generating", "处理中", "生成中"}:
        return "running"
    if values & {"pending", "queued", "waiting", "submitted", "created", "排队中", "等待中"}:
        return "queued"
    if "finalizing" in values:
        return "finalizing"
    if values & {"success", "succeed", "succeeded", "done", "complete", "completed", "finished", "成功", "完成"}:
        return "success"
    if payload.get("is_final") is True:
        return "finished"
    if not values and _clean(payload.get("progress")).rstrip("%") in {"100", "100.0"}:
        return "finished"
    return "unknown"


def _is_finished(data: dict[str, Any]) -> bool:
    return _normalized_status(data) in {"success", "failed", "canceled", "finished"}


def _is_failed(data: dict[str, Any]) -> bool:
    return _normalized_status(data) in {"failed", "canceled"}


def _error_text(data: dict[str, Any]) -> str:
    for item in _iter_nested(data):
        if not isinstance(item, dict):
            continue
        for key in ("error", "message", "msg", "fail_reason", "failure_reason"):
            value = _clean(item.get(key), limit=1000)
            if value:
                return value
    return json.dumps(data, ensure_ascii=False)[:1000]


def submit_audio_generation(body: dict[str, Any]) -> dict[str, Any]:
    upstream_task_id = _clean(body.get("upstream_task_id"), limit=191)
    previous_cost = _extract_cost({"cost": body.get("previous_cost")})
    submission_state = _clean(body.get("submission_state")).lower()
    if upstream_task_id or submission_state not in {"", "pending", "not_started", "not_submitted", "rejected"}:
        raise AudioGenerationProviderError(
            "an existing or uncertain audio submission must not be resubmitted",
            submission_uncertain=not bool(upstream_task_id),
            upstream_task_id=upstream_task_id,
            credential_id=_clean(body.get("credential_id")),
            cost=previous_cost,
        )
    settings = _settings()
    if not (settings.get("enabled") and _clean(settings.get("base_url")) and _api_keys(settings)):
        raise AudioGenerationProviderError("audio generation provider is not configured")
    prompt = _clean(body.get("prompt"), limit=100_000)
    model = _clean(body.get("model"), limit=191)
    if not prompt:
        raise AudioGenerationProviderError("prompt is required")
    if not model:
        raise AudioGenerationProviderError("model is required")
    api_key = _next_api_key(settings)
    credential_id = _credential_id(api_key)
    raw_params = body.get("params")
    params_source = raw_params if isinstance(raw_params, dict) else {}
    params = {
        key: value
        for key, value in params_source.items()
        if value is not None and _clean(key)
    }
    url = _url(settings, settings.get("submit_path"))
    headers = _headers(api_key)
    session_kwargs = proxy_settings.build_session_kwargs()
    _checkpoint(body.get("checkpoint_callback"))
    submission_started_callback = body.get("submission_started_callback")
    if callable(submission_started_callback):
        try:
            if submission_started_callback() is False:
                raise AudioGenerationProviderError("audio submission was fenced before POST")
        except Exception:
            raise AudioGenerationProviderError(
                "audio submission was not authorized or could not be persisted before POST",
                credential_id=credential_id,
                cost=previous_cost,
            ) from None
    try:
        response = requests.post(
            url,
            headers=headers,
            json={"model": model, "prompt": prompt, "params": params},
            timeout=REQUEST_TIMEOUT_SECONDS,
            allow_redirects=False,
            **session_kwargs,
        )
    except (requests.exceptions.RequestException, OSError):
        raise AudioGenerationProviderError(
            "audio submission transport failed; upstream acceptance is unknown",
            submission_uncertain=True,
            credential_id=credential_id,
            cost=previous_cost,
        ) from None
    data = _response_payload(
        response, settings=settings, submitting=True, credential_id=credential_id,
    )
    upstream_task_id = _extract_task_id(data)
    cost = _extract_cost(data)
    if cost is None:
        cost = previous_cost
    if not upstream_task_id:
        raise AudioGenerationProviderError(
            "audio generation response did not include task_id; upstream acceptance is unknown",
            submission_uncertain=True,
            credential_id=credential_id,
            cost=cost,
        )
    submission_callback = body.get("submission_callback")
    if callable(submission_callback):
        try:
            accepted = submission_callback(upstream_task_id, credential_id=credential_id, cost=cost)
        except Exception:
            raise AudioGenerationProviderError(
                "failed to persist the upstream audio submission; resume this task only",
                retryable=True,
                upstream_task_id=upstream_task_id,
                credential_id=credential_id,
                cost=cost,
            ) from None
        if accepted is False:
            raise AudioGenerationProviderError(
                "upstream audio submission persistence was fenced; resume this task only",
                upstream_task_id=upstream_task_id,
                credential_id=credential_id,
                cost=cost,
            )
    return {"upstream_task_id": upstream_task_id, "credential_id": credential_id, "cost": cost, "raw": data}


def get_audio_generation_status(
    upstream_task_id: str,
    credential_id: str = "",
    *,
    checkpoint_callback: Callable[[], None] | None = None,
) -> dict[str, Any]:
    settings = _settings()
    task_id = _clean(upstream_task_id, limit=191)
    credential_id = _clean(credential_id)
    if not task_id:
        raise AudioGenerationProviderError("upstream_task_id is required")
    try:
        if not (settings.get("enabled") and _clean(settings.get("base_url")) and _api_keys(settings)):
            raise AudioGenerationProviderError("audio generation provider is not configured")
        api_key = _resolve_api_key(settings, credential_id)
        url = _url(settings, settings.get("status_path"))
    except AudioGenerationProviderError as exc:
        exc.upstream_task_id = task_id
        exc.credential_id = credential_id
        raise
    session_kwargs = proxy_settings.build_session_kwargs()
    _checkpoint(checkpoint_callback)
    try:
        response = requests.get(
            url,
            headers=_headers(api_key),
            params={"task_id": task_id},
            timeout=30,
            allow_redirects=False,
            **session_kwargs,
        )
    except (requests.exceptions.RequestException, OSError):
        raise AudioGenerationProviderError(
            "audio status query transport failed; resume this task only",
            retryable=True,
            upstream_task_id=task_id,
            credential_id=credential_id,
        ) from None
    data = _response_payload(
        response, settings=settings, submitting=False,
        upstream_task_id=task_id, credential_id=credential_id,
    )
    normalized_status = _normalized_status(data)
    return {
        "upstream_task_id": task_id,
        "credential_id": credential_id,
        "status": normalized_status,
        "finished": normalized_status in {"success", "failed", "canceled", "finished"},
        "failed": normalized_status in {"failed", "canceled"},
        "audio_url": _extract_audio_url(data),
        "cover_url": _extract_cover_url(data),
        "cost": _extract_cost(data),
        "error": _error_text(data) if _is_failed(data) else "",
        "raw": data,
    }


def run_audio_generation(body: dict[str, Any]) -> dict[str, Any]:
    """Submit once or resume polling; transient errors are scheduled by the service."""
    settings = _settings()
    upstream_task_id = _clean(body.get("upstream_task_id"), limit=191)
    credential_id = _clean(body.get("credential_id"))
    cost = _extract_cost({"cost": body.get("previous_cost")})
    submitted: dict[str, Any] = {}
    try:
        poll_timeout = max(30, int(settings.get("poll_timeout_secs") or 900))
        interval = max(1, int(settings.get("poll_interval_secs") or 3))
        raw_started = body.get("poll_started_ts")
        poll_started_ts: float | None = None
        if raw_started is not None and raw_started != "":
            try:
                poll_started_ts = float(raw_started)
            except (TypeError, ValueError, OverflowError):
                raise AudioGenerationProviderError("poll_started_ts must be a finite positive timestamp") from None
            if isinstance(raw_started, bool) or not math.isfinite(poll_started_ts) or poll_started_ts <= 0:
                raise AudioGenerationProviderError("poll_started_ts must be a finite positive timestamp")
            if time.time() - poll_started_ts >= poll_timeout:
                raise AudioGenerationProviderError("audio generation polling deadline exceeded")
        if upstream_task_id:
            _resolve_api_key(settings, credential_id)
        else:
            submitted = submit_audio_generation(body)
            upstream_task_id = _clean(submitted.get("upstream_task_id"), limit=191)
            credential_id = _clean(submitted.get("credential_id"))
            submitted_cost = _extract_cost(submitted)
            if submitted_cost is not None:
                cost = submitted_cost
        # Preserve the original wall-clock budget across worker restarts; use a
        # monotonic deadline within this run so clock changes cannot extend it.
        elapsed = max(0.0, time.time() - poll_started_ts) if poll_started_ts is not None else 0.0
        deadline = time.monotonic() + max(0.0, poll_timeout - elapsed)
        result_wait_started: float | None = None
        while time.monotonic() < deadline:
            status = get_audio_generation_status(
                upstream_task_id, credential_id, checkpoint_callback=body.get("checkpoint_callback"),
            )
            current_cost = _extract_cost(status)
            if current_cost is not None:
                changed = current_cost != cost
                cost = current_cost
                if changed:
                    _notify_callback(body, "cost_callback", cost)
            if status.get("failed"):
                raise AudioGenerationProviderError(
                    _clean(status.get("error"), "audio generation failed", 1000),
                    upstream_finished=True,
                )
            if not status.get("finished"):
                _notify_callback(body, "progress_callback", "polling")
            else:
                audio_url = _clean(status.get("audio_url"), limit=2000)
                if audio_url:
                    return {
                        "upstream_task_id": upstream_task_id,
                        "credential_id": credential_id,
                        "audio_url": audio_url,
                        "cover_url": _clean(status.get("cover_url"), limit=2000),
                        "cost": cost,
                        "raw": {"submit": submitted.get("raw"), "status": status.get("raw")},
                    }
                now = time.monotonic()
                if result_wait_started is None:
                    result_wait_started = now
                if now - result_wait_started >= RESULT_URL_GRACE_SECONDS:
                    raise AudioGenerationProviderError(
                        "audio generation finished without audio_url", upstream_finished=True,
                    )
                _notify_callback(body, "progress_callback", "finalizing")
            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(interval, remaining))
                _checkpoint(body.get("checkpoint_callback"))
        raise AudioGenerationProviderError("audio generation polling deadline exceeded")
    except AudioGenerationProviderError as exc:
        if exc.cost is None:
            exc.cost = cost
        if not exc.upstream_task_id:
            exc.upstream_task_id = upstream_task_id
        if not exc.credential_id:
            exc.credential_id = credential_id
        raise


def _checkpoint(callback: Callable[[], None] | None) -> None:
    # Lease-loss exceptions belong to the service and must not become retries.
    if callable(callback):
        callback()


def _notify_callback(body: dict[str, Any], name: str, value: Any) -> None:
    callback = body.get(name)
    if not callable(callback):
        return
    try:
        accepted = callback(value)
    except Exception:
        raise AudioGenerationProviderError(f"audio generation {name} failed", retryable=True) from None
    if name == "cost_callback" and accepted is False:
        raise AudioGenerationProviderError("audio generation cost persistence was fenced")
