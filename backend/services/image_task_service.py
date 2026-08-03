from __future__ import annotations

import json
import threading
import time
import base64
import atexit
import os
import socket
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from services import openai_relay_service
from services.config import DATA_DIR, config
from services.content_filter import request_text
from services.generation_monitoring_service import generation_monitoring_service
from services.log_service import LOG_TYPE_CALL, log_service
from services.image_library_service import image_library_service
from services.image_prompt_compliance import sanitize_image_prompt
from services.image_size import normalize_image_size
from services.image_task_assets import decode_task_payload, normalize_task_result, prepare_task_payload
from services.image_task_queue import CeleryImageTaskQueue, ImageTaskQueue, RedisImageTaskQueue
from services.image_task_store import DatabaseImageTaskStore, ImageTaskStore, JsonImageTaskStore, can_claim_task_fairly
from services.product_image_compositor import build_preserve_subject_mask, build_preserve_subject_prompt
from services.protocol import openai_v1_image_edit, openai_v1_image_generations

TASK_STATUS_QUEUED = "queued"
TASK_STATUS_RUNNING = "running"
TASK_STATUS_SUCCESS = "success"
TASK_STATUS_ERROR = "error"
TASK_STATUS_CANCELED = "canceled"
TERMINAL_STATUSES = {TASK_STATUS_SUCCESS, TASK_STATUS_ERROR, TASK_STATUS_CANCELED}
UNFINISHED_STATUSES = {TASK_STATUS_QUEUED, TASK_STATUS_RUNNING}
DEFAULT_EMPTY_TASK_LIST_LIMIT = 200
CONTENT_POLICY_ERROR_MARKERS = (
    "内容安全",
    "安全策略",
    "提示已被",
    "content policy",
    "content_policy",
    "safety policy",
    "policy violation",
    "moderation",
    "blocked",
)


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _timestamp(value: object) -> float:
    if not isinstance(value, str) or not value.strip():
        return 0.0
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value[:26], fmt).timestamp()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _task_activity_ts(task: dict[str, Any]) -> float:
    timestamps = (
        task.get("updated_ts"),
        task.get("started_ts"),
        task.get("created_ts"),
    )
    for value in timestamps:
        if isinstance(value, (int, float)) and float(value) > 0:
            return float(value)
    for key in ("updated_at", "started_at", "created_at"):
        value = task.get(key)
        parsed = _timestamp(value)
        if parsed > 0:
            return parsed
    return 0.0


def _positive_int(value: object, default: int = 1) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def _clean(value: object, default: str = "") -> str:
    return str(value or default).strip()


def _is_content_policy_error_message(value: object) -> bool:
    text = _clean(value).lower()
    return bool(text and any(marker in text for marker in CONTENT_POLICY_ERROR_MARKERS))


def _owner_id(identity: dict[str, object]) -> str:
    return _clean(identity.get("id")) or "anonymous"


def _owner_activity_counts(items: list[tuple[str, dict[str, Any]]]) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for _key, task in items:
        owner_id = _clean(task.get("owner_id")) or "anonymous"
        activity = counts.setdefault(owner_id, {"queued_tasks": 0, "running_tasks": 0})
        status = task.get("status")
        if status == TASK_STATUS_QUEUED:
            activity["queued_tasks"] += 1
        elif status == TASK_STATUS_RUNNING:
            activity["running_tasks"] += 1
    return counts


def _active_owner_count(items: list[tuple[str, dict[str, Any]]], candidate_owner_id: str = "") -> int:
    owners = {
        _clean(task.get("owner_id")) or "anonymous"
        for _key, task in items
        if task.get("status") in {TASK_STATUS_QUEUED, TASK_STATUS_RUNNING}
    }
    owner_id = _clean(candidate_owner_id)
    if owner_id:
        owners.add(owner_id)
    return len(owners)


def _task_key(owner_id: str, task_id: str) -> str:
    return f"{owner_id}:{task_id}"


def _collect_image_urls(data: list[Any]) -> list[str]:
    urls: list[str] = []
    for item in data:
        if isinstance(item, dict):
            url = item.get("url")
            if isinstance(url, str) and url:
                    urls.append(url)
    return urls


def _encode_binary_tuple(item: tuple[bytes, str, str]) -> dict[str, str]:
    image_data, filename, mime_type = item
    return {
        "__image_input__": "1",
        "data": base64.b64encode(image_data).decode("ascii"),
        "filename": filename,
        "mime_type": mime_type,
    }


def _decode_binary_tuple(item: dict[str, str]) -> tuple[bytes, str, str]:
    return (
        base64.b64decode(str(item.get("data") or "")),
        str(item.get("filename") or "image.png"),
        str(item.get("mime_type") or "image/png"),
    )


def _encode_payload(value: Any) -> Any:
    if isinstance(value, tuple) and len(value) == 3 and isinstance(value[0], (bytes, bytearray)):
        return _encode_binary_tuple((bytes(value[0]), str(value[1] or "image.png"), str(value[2] or "image/png")))
    if isinstance(value, list):
        return [_encode_payload(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _encode_payload(item) for key, item in value.items() if key != "progress_callback"}
    return value


def _decode_payload(value: Any) -> Any:
    return decode_task_payload(value)


def _identity_snapshot(identity: dict[str, object]) -> dict[str, object]:
    return {
        "id": _clean(identity.get("id")) or "anonymous",
        "name": _clean(identity.get("name")),
        "username": _clean(identity.get("username")),
        "role": _clean(identity.get("role"), "user"),
    }


def _public_task(
    task: dict[str, Any],
    batch_progress: dict[str, int | str] | None = None,
) -> dict[str, Any]:
    item = {
        "id": task.get("id"),
        "status": task.get("status"),
        "mode": task.get("mode"),
        "model": task.get("model"),
        "size": task.get("size"),
        "quality": task.get("quality"),
        "created_at": task.get("created_at"),
        "updated_at": task.get("updated_at"),
    }
    if task.get("batch_id"):
        item["batch_id"] = task.get("batch_id")
        item["batch_index"] = task.get("batch_index")
        item["batch_total"] = task.get("batch_total")
    if batch_progress:
        item["batch_progress"] = batch_progress
    if task.get("conversation_id"):
        item["conversation_id"] = task.get("conversation_id")
    if task.get("product_id"):
        item["product_id"] = task.get("product_id")
    if task.get("template_id"):
        item["template_id"] = task.get("template_id")
    if task.get("data") is not None:
        item["data"] = task.get("data")
    if task.get("usage") is not None:
        item["usage"] = task.get("usage")
    if task.get("error"):
        item["error"] = task.get("error")
    if task.get("progress"):
        item["progress"] = task.get("progress")
    if task.get("duration_ms") is not None:
        item["duration_ms"] = task.get("duration_ms")
    if isinstance(task.get("stage_timings_ms"), dict):
        item["stage_timings_ms"] = task.get("stage_timings_ms")
    if task.get("attempts") is not None:
        item["attempts"] = int(task.get("attempts") or 0)
    if task.get("max_retries"):
        item["max_retries"] = task.get("max_retries")
    if task.get("status") in (TASK_STATUS_RUNNING, TASK_STATUS_QUEUED):
        if task.get("status") == TASK_STATUS_RUNNING:
            # RUNNING 状态仅在 started_ts 被设置后（image_stream_resolve_start）才计时
            base_ts = task.get("started_ts")
        else:
            # QUEUED 状态从 created_ts 开始计时（排队等待中）
            base_ts = task.get("created_ts") or task.get("updated_ts")
        if base_ts:
            item["elapsed_secs"] = round(time.time() - base_ts, 1)
    return item


def _monitoring_event_task(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": task.get("id"),
        "owner_id": task.get("owner_id"),
        "status": task.get("status"),
        "mode": task.get("mode"),
        "model": task.get("model"),
        "product_id": task.get("product_id"),
        "template_id": task.get("template_id"),
        "image_count": task.get("image_count") or 1,
        "duration_ms": task.get("duration_ms"),
        "stage_timings_ms": task.get("stage_timings_ms"),
        "error": task.get("error"),
        "created_at": task.get("created_at"),
        "updated_at": task.get("updated_at"),
    }


class ImageTaskService:
    def __init__(
        self,
        path: Path,
        *,
        generation_handler: Callable[[dict[str, Any]], dict[str, Any]] = openai_v1_image_generations.handle,
        edit_handler: Callable[[dict[str, Any]], dict[str, Any]] = openai_v1_image_edit.handle,
        relay_enabled_getter: Callable[[], bool] | None = None,
        retention_days_getter: Callable[[], int] | None = None,
        task_store: ImageTaskStore | None = None,
        task_queue: ImageTaskQueue | None = None,
        run_inline: bool | None = None,
        max_retries_getter: Callable[[], int] | None = None,
    ):
        self.path = path
        self.generation_handler = generation_handler
        self.edit_handler = edit_handler
        self.relay_enabled_getter = relay_enabled_getter or openai_relay_service.is_enabled
        self.retention_days_getter = retention_days_getter or (lambda: config.image_retention_days)
        self.task_store = task_store or JsonImageTaskStore(path)
        self._row_level_store = bool(getattr(self.task_store, "row_level", False))
        self._is_worker_process = os.getenv("IMAGE_TASK_WORKER_PROCESS", "").strip().lower() in {"1", "true", "yes"}
        self._recover_on_start = self._is_worker_process and os.getenv(
            "IMAGE_TASK_SKIP_STARTUP_RECOVERY", ""
        ).strip().lower() not in {"1", "true", "yes"}
        self.task_queue = task_queue
        self.run_inline = (task_queue is None) if run_inline is None else bool(run_inline)
        self.max_retries_getter = max_retries_getter or (lambda: int(config.get_image_task_queue_settings().get("max_retries") or 0))
        self._lock = threading.RLock()
        queue_settings = config.get_image_task_queue_settings()
        self._worker_concurrency = max(1, int(queue_settings.get("worker_concurrency") or 1))
        self._configured_total_concurrency = max(0, int(queue_settings.get("total_concurrency") or 0))
        self._stale_running_timeout_secs = max(60, int(queue_settings.get("stale_running_timeout_secs") or 1800))
        self._maintenance_interval_secs = max(30, min(300, max(30, self._stale_running_timeout_secs // 4)))
        self._worker_heartbeat_secs = max(10, min(60, max(10, self._maintenance_interval_secs // 2)))
        self._total_concurrency = max(
            1,
            int(getattr(self.task_queue, "max_concurrency", 0) or _resolve_total_concurrency(queue_settings, self._worker_concurrency)),
        )
        self._local_concurrency_limit = max(1, min(self._worker_concurrency, self._total_concurrency))
        self._run_semaphore = threading.BoundedSemaphore(self._local_concurrency_limit)
        self._owner_concurrency = max(1, int(queue_settings.get("owner_concurrency") or 2))
        self._dynamic_owner_concurrency_enabled = bool(queue_settings.get("dynamic_owner_concurrency_enabled"))
        self._dynamic_owner_concurrency_threshold = max(
            1,
            int(queue_settings.get("dynamic_owner_concurrency_threshold") or 10),
        )
        self._dynamic_owner_concurrency_max = max(
            self._owner_concurrency,
            int(queue_settings.get("dynamic_owner_concurrency_max") or self._owner_concurrency),
        )
        self._owner_pending_limit = max(1, int(queue_settings.get("owner_pending_limit") or 50))
        self._tasks: dict[str, dict[str, Any]] = {}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            if self._row_level_store:
                if self._recover_on_start:
                    self.task_store.recover_unfinished(
                        requeue=not self.run_inline,
                        message=(
                            "worker restarted, unfinished image task was requeued"
                            if not self.run_inline
                            else "worker restarted, unfinished image task was interrupted"
                        ),
                    )
                try:
                    retention_days = max(1, int(self.retention_days_getter()))
                except Exception:
                    retention_days = 30
                self.task_store.cleanup_before(
                    datetime.fromtimestamp(time.time() - retention_days * 86400)
                )
            else:
                self._tasks = self._load_locked()
                changed = self._recover_unfinished_locked(requeue=not self.run_inline)
                changed = self._cleanup_locked() or changed
                if changed:
                    self._save_locked()
        if self.task_queue is not None and not self.run_inline and self._recover_on_start:
            self.requeue_unfinished()

    def submit_generation(
        self,
        identity: dict[str, object],
        *,
        client_task_id: str,
        prompt: str,
        model: str,
        size: str | None,
        quality: str = "auto",
        base_url: str = "",
        conversation_id: str = "",
        turn_id: str = "",
        product_id: int = 0,
        template_id: int = 0,
        batch_id: str = "",
        batch_index: int = 0,
        batch_total: int = 1,
        reference_upload_ms: int = 0,
        reference_cache_hits: int = 0,
    ) -> dict[str, Any]:
        safe_prompt = sanitize_image_prompt(prompt, image_count=batch_total, image_index=batch_index)
        payload = {
            "prompt": safe_prompt,
            "model": model,
            "n": 1,
            "size": normalize_image_size(size),
            "quality": quality,
            "response_format": "url",
            "base_url": base_url,
            "conversation_id": conversation_id,
            "turn_id": turn_id,
            "product_id": product_id,
            "template_id": template_id,
            "batch_id": batch_id,
            "batch_index": batch_index,
            "batch_total": batch_total,
            "reference_upload_ms": max(0, int(reference_upload_ms or 0)),
            "reference_cache_hits": max(0, int(reference_cache_hits or 0)),
        }
        return self._submit(identity, client_task_id=client_task_id, mode="generate", payload=payload)

    def submit_edit(
        self,
        identity: dict[str, object],
        *,
        client_task_id: str,
        prompt: str,
        model: str,
        size: str | None,
        quality: str = "auto",
        base_url: str = "",
        images: list[tuple[bytes, str, str]] | None = None,
        masks: list[tuple[bytes, str, str]] | None = None,
        image_urls: list[str] | None = None,
        preserve_subject: bool = False,
        conversation_id: str = "",
        turn_id: str = "",
        product_id: int = 0,
        template_id: int = 0,
        batch_id: str = "",
        batch_index: int = 0,
        batch_total: int = 1,
        reference_upload_ms: int = 0,
        reference_cache_hits: int = 0,
    ) -> dict[str, Any]:
        image_inputs = images or []
        safe_prompt = sanitize_image_prompt(prompt, image_count=batch_total, image_index=batch_index)
        effective_prompt = build_preserve_subject_prompt(safe_prompt) if preserve_subject else safe_prompt
        effective_masks = list(masks or [])
        supports_auto_mask = not self.relay_enabled_getter() or openai_relay_service.supports_image_edit_masks(model)
        if preserve_subject and supports_auto_mask and len(image_inputs) == 1 and not effective_masks:
            preserve_mask = build_preserve_subject_mask(image_inputs[0])
            if preserve_mask is not None:
                effective_masks.append(preserve_mask)
        payload = {
            "prompt": effective_prompt,
            "images": image_inputs,
            "mask": effective_masks,
            "image_urls": image_urls or [],
            "model": model,
            "n": 1,
            "size": normalize_image_size(size),
            "quality": quality,
            "response_format": "url",
            "base_url": base_url,
            "preserve_subject": bool(preserve_subject and (image_inputs or image_urls)),
            "conversation_id": conversation_id,
            "turn_id": turn_id,
            "product_id": product_id,
            "template_id": template_id,
            "batch_id": batch_id,
            "batch_index": batch_index,
            "batch_total": batch_total,
            "reference_upload_ms": max(0, int(reference_upload_ms or 0)),
            "reference_cache_hits": max(0, int(reference_cache_hits or 0)),
        }
        return self._submit(identity, client_task_id=client_task_id, mode="edit", payload=payload)

    def list_tasks(self, identity: dict[str, object], task_ids: list[str], *, limit: int | None = None) -> dict[str, Any]:
        owner = _owner_id(identity)
        requested_ids = [_clean(task_id) for task_id in task_ids if _clean(task_id)]
        with self._lock:
            self._refresh_locked()
            page_limit = min(500, _positive_int(limit, DEFAULT_EMPTY_TASK_LIST_LIMIT))
            if self._row_level_store:
                raw_items = self.task_store.list_tasks(
                    owner,
                    requested_ids or None,
                    limit=None if requested_ids else page_limit + 1,
                )
                indexed = {_clean(task.get("id")): task for task in raw_items}
                batch_ids = {str(task.get("batch_id")) for task in raw_items if task.get("batch_id")}
                batch_progress = {
                    batch_id: self.task_store.get_batch_progress(owner, batch_id)
                    for batch_id in batch_ids
                }
                items = [
                    _public_task(indexed[task_id], batch_progress.get(str(indexed[task_id].get("batch_id"))))
                    for task_id in requested_ids
                    if task_id in indexed
                ]
                missing_ids = [task_id for task_id in requested_ids if task_id not in indexed]
                if not requested_ids:
                    has_more = len(raw_items) > page_limit
                    items = sorted(
                        (
                            _public_task(task, batch_progress.get(str(task.get("batch_id"))))
                            for task in raw_items[:page_limit]
                        ),
                        key=lambda item: str(item.get("updated_at") or ""),
                        reverse=True,
                    )
                    return {"items": items, "missing_ids": [], "has_more": has_more, "limit": page_limit}
                return {"items": items, "missing_ids": missing_ids}
            if self._cleanup_locked():
                self._save_locked()
            items = []
            missing_ids = []
            for task_id in requested_ids:
                task = self._tasks.get(_task_key(owner, task_id))
                if task is None:
                    missing_ids.append(task_id)
                else:
                    items.append(_public_task(task))
            if not requested_ids:
                raw_items = sorted(
                    (
                        task
                        for task in self._tasks.values()
                        if task.get("owner_id") == owner
                    ),
                    key=lambda item: str(item.get("updated_at") or ""),
                    reverse=True,
                )
                has_more = len(raw_items) > page_limit
                items = [_public_task(task) for task in raw_items[:page_limit]]
                return {"items": items, "missing_ids": [], "has_more": has_more, "limit": page_limit}
            return {"items": items, "missing_ids": missing_ids}

    def cancel_task(self, identity: dict[str, object], task_id: str) -> dict[str, Any]:
        owner = _owner_id(identity)
        normalized_task_id = _clean(task_id)
        if not normalized_task_id:
            raise ValueError("task_id is required")
        key = _task_key(owner, normalized_task_id)
        with self._lock:
            self._refresh_locked()
            task = self._get_task_locked(key)
            if task is None:
                raise ValueError("task not found")
            if task.get("status") in TERMINAL_STATUSES:
                return _public_task(task)

            started_ts = task.get("started_ts") or task.get("created_ts") or time.time()
            try:
                duration_ms = int(max(0, time.time() - float(started_ts)) * 1000)
            except (TypeError, ValueError):
                duration_ms = 0
            task.update(
                {
                    "status": TASK_STATUS_CANCELED,
                    "error": "任务已中止",
                    "data": [],
                    "progress": "canceled",
                    "duration_ms": duration_ms,
                    "updated_at": _now_iso(),
                    "updated_ts": time.time(),
                }
            )
            if self._row_level_store:
                self.task_store.save_task(key, task)
            else:
                self._save_locked()
            public = _public_task(task)
        self._record_monitoring_event(key)
        return public

    def monitoring_task_events(self) -> list[dict[str, Any]]:
        with self._lock:
            self._refresh_locked()
            if self._row_level_store:
                return [_monitoring_event_task(task) for task in self.task_store.list_terminal()]
            return [
                _monitoring_event_task(task)
                for task in self._tasks.values()
                if task.get("status") in TERMINAL_STATUSES
            ]

    def requeue_unfinished(self) -> int:
        if self.task_queue is None:
            return 0
        queued = 0
        with self._lock:
            self._refresh_locked()
            items = self.task_store.list_unfinished() if self._row_level_store else list(self._tasks.items())
            for key, task in items:
                if task.get("status") != TASK_STATUS_QUEUED:
                    continue
                if not isinstance(task.get("payload"), dict):
                    continue
                self.task_queue.enqueue(key)
                queued += 1
        return queued

    def recover_stale_unfinished(self) -> int:
        stale_cutoff = time.time() - self._stale_running_timeout_secs
        recovered = 0
        queued_keys: list[str] = []
        with self._lock:
            self._refresh_locked()
            items = self.task_store.list_unfinished() if self._row_level_store else list(self._tasks.items())
            for key, task in items:
                if task.get("status") != TASK_STATUS_RUNNING:
                    continue
                if _task_activity_ts(task) > stale_cutoff:
                    continue
                duration_ms = int(max(0.0, time.time() - _task_activity_ts(task)) * 1000)
                requeue = self.task_queue is not None and not self.run_inline
                updates = {
                    "status": TASK_STATUS_QUEUED if requeue else TASK_STATUS_ERROR,
                    "error": "" if requeue else "image task timed out",
                    "progress": "stale_requeued" if requeue else "stale_timeout",
                    "duration_ms": None if requeue else duration_ms,
                    "started_ts": None if requeue else task.get("started_ts"),
                    "started_at": "" if requeue else task.get("started_at"),
                    "updated_at": _now_iso(),
                    "updated_ts": time.time(),
                }
                updated = self._persist_updates_locked(key, updates, expected_status=TASK_STATUS_RUNNING)
                if updated is None:
                    continue
                recovered += 1
                if self.task_queue is not None and not self.run_inline:
                    queued_keys.append(key)
        for key in queued_keys:
            self.task_queue.enqueue(key)
        return recovered

    def process_queued_task(self, task_key: str) -> dict[str, Any] | None:
        key = _clean(task_key)
        if not key:
            return None
        with self._lock:
            self._refresh_locked()
            task = self._get_task_locked(key)
            if task is None or task.get("status") != TASK_STATUS_QUEUED:
                return _public_task(task) if task else None
            payload = _decode_payload(task.get("payload") or {})
            identity = task.get("identity") if isinstance(task.get("identity"), dict) else {}
            identity = _identity_snapshot({**identity, "id": task.get("owner_id") or identity.get("id")})
            mode = "edit" if task.get("mode") == "edit" else "generate"
            model = _clean(task.get("model"), "gpt-image-2")
        if not isinstance(payload, dict) or not payload.get("prompt"):
            self._update_task_unless_canceled(
                key,
                status=TASK_STATUS_ERROR,
                error="queued image task is missing payload",
                data=[],
                duration_ms=0,
            )
            self._record_monitoring_event(key)
            return self.list_tasks(identity, [_clean(task.get("id"))])["items"][0]
        self._run_task(key, mode, payload, identity, model)
        result = self.list_tasks(identity, [_clean(task.get("id"))])
        return (result.get("items") or [None])[0]

    def work_once(self, timeout_secs: int = 5) -> dict[str, Any] | None:
        if self.task_queue is None:
            raise RuntimeError("image task queue is not configured")
        task_key = self.task_queue.dequeue(timeout_secs)
        if not task_key:
            return None
        return self.process_queued_task(task_key)

    def _worker_thread_count(self) -> int:
        return max(1, min(self._worker_concurrency, self._total_concurrency))

    def _effective_owner_concurrency(self, active_owner_count: int) -> int:
        if not self._dynamic_owner_concurrency_enabled:
            return self._owner_concurrency
        owner_count = max(1, int(active_owner_count or 1))
        if owner_count > self._dynamic_owner_concurrency_threshold:
            return self._owner_concurrency
        elastic_share = max(1, self._total_concurrency // owner_count)
        return max(
            self._owner_concurrency,
            min(self._dynamic_owner_concurrency_max, elastic_share),
        )

    def work_forever(self, stop_event: threading.Event | None = None, timeout_secs: int = 5) -> None:
        if self.task_queue is None:
            raise RuntimeError("image task queue is not configured")

        shutdown_event = stop_event or threading.Event()
        heartbeat = getattr(self.task_queue, "touch_worker", None)
        heartbeat_enabled = callable(heartbeat)
        worker_id = f"{socket.gethostname()}:{os.getpid()}"
        heartbeat_interval = max(10, self._worker_heartbeat_secs)
        maintenance_interval = max(30, self._maintenance_interval_secs)

        def _worker_loop() -> None:
            while not shutdown_event.is_set():
                try:
                    self.work_once(timeout_secs)
                except Exception as exc:
                    try:
                        log_service.add(LOG_TYPE_CALL, "image worker loop failed", {"error": str(exc)})
                    except Exception:
                        pass
                    if shutdown_event.is_set():
                        break
                    time.sleep(2)

        def _heartbeat_loop() -> None:
            if not heartbeat_enabled:
                return
            while not shutdown_event.is_set():
                try:
                    heartbeat(worker_id, timeout_secs=max(60, heartbeat_interval * 4))
                except Exception as exc:
                    try:
                        log_service.add(LOG_TYPE_CALL, "image worker heartbeat failed", {"error": str(exc)})
                    except Exception:
                        pass
                if shutdown_event.wait(timeout=heartbeat_interval):
                    return

        def _maintenance_loop() -> None:
            while not shutdown_event.is_set():
                if shutdown_event.wait(timeout=maintenance_interval):
                    return
                try:
                    recovered = self.recover_stale_unfinished()
                    if recovered:
                        try:
                            log_service.add(
                                LOG_TYPE_CALL,
                                "image worker recovered stale tasks",
                                {
                                    "recovered": recovered,
                                    "stale_running_timeout_secs": self._stale_running_timeout_secs,
                                },
                            )
                        except Exception:
                            pass
                except Exception as exc:
                    try:
                        log_service.add(LOG_TYPE_CALL, "image worker maintenance failed", {"error": str(exc)})
                    except Exception:
                        pass

        worker_count = self._worker_thread_count()

        threads: list[threading.Thread] = []
        for index in range(worker_count):
            thread = threading.Thread(
                target=_worker_loop,
                name=f"image-worker-{index + 1}",
                daemon=True,
            )
            thread.start()
            threads.append(thread)

        maintenance_thread = threading.Thread(target=_maintenance_loop, name="image-worker-maintenance", daemon=True)
        maintenance_thread.start()
        threads.append(maintenance_thread)

        if heartbeat_enabled:
            heartbeat_thread = threading.Thread(target=_heartbeat_loop, name="image-worker-heartbeat", daemon=True)
            heartbeat_thread.start()
            threads.append(heartbeat_thread)

        try:
            while not shutdown_event.is_set():
                shutdown_event.wait(timeout=0.5)
        finally:
            if stop_event is None:
                shutdown_event.set()
            for thread in threads:
                thread.join(timeout=max(1, int(timeout_secs)))
            forget_worker = getattr(self.task_queue, "forget_worker", None)
            if callable(forget_worker):
                try:
                    forget_worker(worker_id)
                except Exception:
                    pass

    def monitoring_snapshot(self) -> dict[str, Any]:
        try:
            self.recover_stale_unfinished()
        except Exception:
            pass
        with self._lock:
            self._refresh_locked()
            items = self.task_store.list_unfinished() if self._row_level_store else list(self._tasks.items())
            now = time.time()
            queued_tasks = 0
            running_tasks = 0
            stale_running_tasks = 0
            owner_activity = _owner_activity_counts(items)
            active_owner_count = len(
                [
                    owner_id
                    for owner_id, activity in owner_activity.items()
                    if activity["queued_tasks"] or activity["running_tasks"]
                ]
            )
            effective_owner_concurrency = self._effective_owner_concurrency(active_owner_count or 1)
            for _key, task in items:
                status = task.get("status")
                if status == TASK_STATUS_QUEUED:
                    queued_tasks += 1
                elif status == TASK_STATUS_RUNNING:
                    running_tasks += 1
                    if now - _task_activity_ts(task) >= self._stale_running_timeout_secs:
                        stale_running_tasks += 1
            queue = self.task_queue
            try:
                queue_depth = int(getattr(queue, "queue_depth", lambda: 0)() or 0) if queue is not None else 0
            except Exception:
                queue_depth = 0
            try:
                active_slots = int(getattr(queue, "active_slot_count", lambda: 0)() or 0) if queue is not None else 0
            except Exception:
                active_slots = 0
            try:
                active_workers = int(getattr(queue, "active_worker_count", lambda: 0)() or 0) if queue is not None else 0
            except Exception:
                active_workers = 0
            return {
                "enabled": queue is not None,
                "executor": config.get_image_task_queue_settings().get("executor") if queue is not None else "inline",
                "queue_depth": queue_depth,
                "queued_tasks": queued_tasks,
                "running_tasks": running_tasks,
                "stale_running_tasks": stale_running_tasks,
                "owner_activity": [
                    {
                        "owner_id": owner_id,
                        "queued_tasks": activity["queued_tasks"],
                        "running_tasks": activity["running_tasks"],
                        "active_tasks": activity["queued_tasks"] + activity["running_tasks"],
                    }
                    for owner_id, activity in sorted(
                        owner_activity.items(),
                        key=lambda item: (-item[1]["running_tasks"], -item[1]["queued_tasks"], item[0]),
                    )
                ],
                "active_slots": active_slots,
                "slot_limit": self._total_concurrency,
                "active_workers": active_workers,
                "worker_concurrency": self._worker_concurrency,
                "local_concurrency_limit": self._local_concurrency_limit,
                "configured_total_concurrency": self._configured_total_concurrency,
                "total_concurrency": self._total_concurrency,
                "owner_concurrency": self._owner_concurrency,
                "effective_owner_concurrency": effective_owner_concurrency,
                "dynamic_owner_concurrency_enabled": self._dynamic_owner_concurrency_enabled,
                "dynamic_owner_concurrency_threshold": self._dynamic_owner_concurrency_threshold,
                "dynamic_owner_concurrency_max": self._dynamic_owner_concurrency_max,
                "active_owner_count": active_owner_count,
                "owner_pending_limit": self._owner_pending_limit,
                "stale_running_timeout_secs": self._stale_running_timeout_secs,
                "worker_heartbeat_secs": self._worker_heartbeat_secs,
            }

    def close(self) -> None:
        close = getattr(self.task_store, "close", None)
        if callable(close):
            close()

    def _submit(
        self,
        identity: dict[str, object],
        *,
        client_task_id: str,
        mode: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        task_id = _clean(client_task_id)
        if not task_id:
            raise ValueError("client_task_id is required")
        owner = _owner_id(identity)
        key = _task_key(owner, task_id)
        now = _now_iso()
        should_start = False
        should_enqueue = False
        with self._lock:
            self._refresh_locked()
            cleaned = self._cleanup_locked()
            task = self._get_task_locked(key)
            if task is not None:
                if cleaned:
                    self._save_locked()
                return _public_task(task)
            if self._row_level_store:
                unfinished_items = self.task_store.list_unfinished()
                active_owner_count = _active_owner_count(unfinished_items, owner)
                active_count = self.task_store.count_tasks(owner, {TASK_STATUS_QUEUED, TASK_STATUS_RUNNING})
                running_count = self.task_store.count_tasks(owner, {TASK_STATUS_RUNNING})
            else:
                unfinished_items = list(self._tasks.items())
                active_owner_count = _active_owner_count(unfinished_items, owner)
                active_count = sum(
                    1
                    for task_item in self._tasks.values()
                    if task_item.get("owner_id") == owner
                    and task_item.get("status") in {TASK_STATUS_QUEUED, TASK_STATUS_RUNNING}
                )
                running_count = sum(
                    1
                    for task_item in self._tasks.values()
                    if task_item.get("owner_id") == owner and task_item.get("status") == TASK_STATUS_RUNNING
                )
            if active_count >= self._owner_pending_limit:
                raise ValueError("user task queue is full; wait for existing tasks to finish")
            effective_owner_concurrency = self._effective_owner_concurrency(active_owner_count)
            if running_count >= effective_owner_concurrency:
                payload["progress"] = "waiting_for_user_concurrency"
            task = {
                "id": task_id,
                "owner_id": owner,
                "status": TASK_STATUS_QUEUED,
                "mode": mode,
                "model": _clean(payload.get("model"), "gpt-image-2"),
                "size": _clean(payload.get("size")),
                "quality": _clean(payload.get("quality"), "auto"),
                "prompt": _clean(payload.get("prompt")),
                "conversation_id": _clean(payload.get("conversation_id")),
                "turn_id": _clean(payload.get("turn_id")),
                "product_id": int(payload.get("product_id") or 0),
                "template_id": int(payload.get("template_id") or 0),
                "batch_id": _clean(payload.get("batch_id")),
                "batch_index": max(0, int(payload.get("batch_index") or 0)),
                "batch_total": max(1, int(payload.get("batch_total") or 1)),
                "image_count": _positive_int(payload.get("n"), 1),
                "attempts": 0,
                "max_retries": max(0, int(self.max_retries_getter())),
                "progress": _clean(payload.get("progress"), "queued"),
                "stage_timings_ms": {
                    "upload": max(0, int(payload.get("reference_upload_ms") or 0)),
                    "queue": 0,
                    "generation": 0,
                    "save": 0,
                },
                "identity": _identity_snapshot(identity),
                "payload": _encode_payload(
                    prepare_task_payload(payload, owner_id=owner, task_id=task_id)
                    if self._row_level_store
                    else payload
                ),
                "created_at": now,
                "updated_at": now,
                "created_ts": time.time(),
            }
            if self._row_level_store:
                task, created = self.task_store.create_task(key, task)
                if not created:
                    return _public_task(task)
            else:
                self._tasks[key] = task
                self._save_locked()
            should_enqueue = self.task_queue is not None and not self.run_inline
            should_start = not should_enqueue

        if should_enqueue and self.task_queue is not None:
            self.task_queue.enqueue(key)
        elif should_start:
            thread = threading.Thread(
                target=self._run_task,
                args=(key, mode, payload, dict(identity), _clean(payload.get("model"), "gpt-image-2")),
                name=f"image-task-{task_id[:16]}",
                daemon=True,
            )
            thread.start()
        return _public_task(task)

    def _run_task(
        self,
        key: str,
        mode: str,
        payload: dict[str, Any],
        identity: dict[str, object],
        model: str,
    ) -> None:
        started = time.time()
        if not self._start_task(key):
            if self.task_queue is not None and not self.run_inline:
                with self._lock:
                    pending = self._get_task_locked(key)
                if pending is not None and pending.get("status") == TASK_STATUS_QUEUED:
                    time.sleep(1)
                    self.task_queue.enqueue(key)
            return
        with self._lock:
            self._refresh_locked()
            running_task = dict(self._get_task_locked(key) or {})
        stage_timings = dict(running_task.get("stage_timings_ms") or {})
        generation_started = time.time()
        generation_recorded = False
        # 创建进度回调，每个步骤完成后更新任务状态
        def progress_callback(step: str) -> None:
            if step == "image_stream_resolve_start":
                if not self._update_task_unless_canceled(key, started_ts=time.time()):
                    return
            self._update_task_unless_canceled(key, progress=step)
        # 将进度回调添加到 payload 中（handler 会提取并传递给 ConversationRequest）
        handler_payload = {
            key: value
            for key, value in payload.items()
            if key not in {
                "preserve_subject",
                "conversation_id",
                "turn_id",
                "product_id",
                "template_id",
                "reference_upload_ms",
                "reference_cache_hits",
            }
        }
        payload_with_progress = {**handler_payload, "progress_callback": progress_callback}
        try:
            if self.relay_enabled_getter():
                handler = openai_relay_service.image_edits if mode == "edit" else openai_relay_service.image_generations
            else:
                handler = self.edit_handler if mode == "edit" else self.generation_handler
            self._run_semaphore.acquire()
            slot_token = f"{os.getpid()}:{threading.get_ident()}:{key}"
            acquire_slot = getattr(self.task_queue, "acquire_slot", None)
            release_slot = getattr(self.task_queue, "release_slot", None)
            distributed_slot = False
            requeue_due_to_slots = False
            try:
                if callable(acquire_slot):
                    distributed_slot = bool(acquire_slot(slot_token, timeout_secs=max(5, min(15, self._worker_heartbeat_secs))))
                    if not distributed_slot:
                        requeue_due_to_slots = True
                if not requeue_due_to_slots:
                    with self._lock:
                        self._refresh_locked()
                        task = self._get_task_locked(key)
                        if task is None or task.get("status") == TASK_STATUS_CANCELED:
                            return
                    result = handler(payload_with_progress)
            finally:
                if distributed_slot and callable(release_slot):
                    release_slot(slot_token)
                self._run_semaphore.release()
            if requeue_due_to_slots:
                with self._lock:
                    self._refresh_locked()
                    pending = self._get_task_locked(key)
                    if pending is not None and pending.get("status") == TASK_STATUS_RUNNING and self.task_queue is not None and not self.run_inline:
                        updated = self._update_task_unless_canceled(
                            key,
                            status=TASK_STATUS_QUEUED,
                            error="",
                            progress="waiting_for_slot",
                            started_ts=None,
                            started_at="",
                            duration_ms=None,
                        )
                        if updated:
                            self.task_queue.enqueue(key)
                return
            if not isinstance(result, dict):
                raise RuntimeError("image task returned streaming result unexpectedly")
            data = result.get("data")
            account_email = _clean(result.get("_account_email") or result.get("account_email"))
            if not isinstance(data, list) or not data:
                upstream = _clean(result.get("message"))
                if upstream:
                    message = upstream
                else:
                    message = "号池中没有可用账号或所有账号均被限流，请检查号池状态（账号额度、是否被封禁、是否到达生图上限）"
                error = RuntimeError(message)
                if account_email:
                    setattr(error, "account_email", account_email)
                raise error
            generation_ms = int((time.time() - generation_started) * 1000)
            stage_timings["generation"] = max(0, int(stage_timings.get("generation") or 0)) + generation_ms
            generation_recorded = True
            save_started = time.time()
            if self._row_level_store:
                data = normalize_task_result(
                    data,
                    owner_id=_owner_id(identity),
                    task_id=_clean(key.rsplit(":", 1)[-1]),
                    base_url=_clean(payload.get("base_url")),
                )
            usage = result.get("usage")
            stage_timings["save"] = max(0, int(stage_timings.get("save") or 0)) + int((time.time() - save_started) * 1000)
            duration_ms = int((time.time() - started) * 1000)
            if not self._update_task_unless_canceled(
                key,
                status=TASK_STATUS_SUCCESS,
                data=data,
                usage=usage,
                error="",
                duration_ms=duration_ms,
                stage_timings_ms=stage_timings,
            ):
                return
            self._record_monitoring_event(key)
            self._record_library_result(key, identity, request_text(payload.get("prompt")), _clean(payload.get("base_url")))
            self._log_call(
                identity,
                mode,
                model,
                started,
                "调用完成",
                request_preview=request_text(payload.get("prompt")),
                urls=_collect_image_urls(data),
                account_email=account_email,
            )
        except Exception as exc:
            error_message = str(exc) or "image task failed"
            account_email = _clean(getattr(exc, "account_email", ""))
            conversation_id = _clean(getattr(exc, "conversation_id", ""))
            duration_ms = int((time.time() - started) * 1000)
            if not generation_recorded:
                generation_ms = int((time.time() - generation_started) * 1000)
                stage_timings["generation"] = max(0, int(stage_timings.get("generation") or 0)) + generation_ms
            if self._retry_task(key, error_message, duration_ms, stage_timings):
                self._log_call(
                    identity,
                    mode,
                    model,
                    started,
                    "call failed (retry queued)",
                    request_preview=request_text(payload.get("prompt")),
                    status="failed",
                    error=error_message,
                    account_email=account_email,
                )
                return
            if not self._update_task_unless_canceled(
                key,
                status=TASK_STATUS_ERROR,
                error=error_message,
                data=[],
                duration_ms=duration_ms,
                stage_timings_ms=stage_timings,
                **({"conversation_id": conversation_id} if conversation_id else {}),
            ):
                return
            self._record_monitoring_event(key)
            self._log_call(
                identity,
                mode,
                model,
                started,
                "调用失败",
                request_preview=request_text(payload.get("prompt")),
                status="failed",
                error=error_message,
                account_email=account_email,
            )

    def _log_call(
        self,
        identity: dict[str, object],
        mode: str,
        model: str,
        started: float,
        suffix: str,
        *,
        request_preview: str = "",
        status: str = "success",
        error: str = "",
        urls: list[str] | None = None,
        account_email: str = "",
    ) -> None:
        endpoint = "/v1/images/edits" if mode == "edit" else "/v1/images/generations"
        summary_prefix = "图生图" if mode == "edit" else "文生图"
        detail = {
            "key_id": identity.get("id"),
            "key_name": identity.get("name"),
            "role": identity.get("role"),
            "endpoint": endpoint,
            "model": model,
            "started_at": datetime.fromtimestamp(started).strftime("%Y-%m-%d %H:%M:%S"),
            "ended_at": _now_iso(),
            "duration_ms": int((time.time() - started) * 1000),
            "status": status,
        }
        if request_preview:
            detail["request_text"] = request_preview
        if error:
            detail["error"] = error
        if account_email:
            detail["account_email"] = account_email
        if urls:
            detail["urls"] = list(dict.fromkeys(urls))
        try:
            log_service.add(LOG_TYPE_CALL, f"{summary_prefix}{suffix}", detail)
        except Exception:
            pass

    def _start_task(self, key: str) -> bool:
        with self._lock:
            self._refresh_locked()
            task = self._get_task_locked(key)
            if task is None or task.get("status") != TASK_STATUS_QUEUED:
                return False
            owner_id = _clean(task.get("owner_id")) or "anonymous"
            if not self._row_level_store:
                all_active_tasks = list(self._tasks.items())
                effective_owner_concurrency = self._effective_owner_concurrency(
                    _active_owner_count(all_active_tasks, owner_id),
                )
                active_tasks = [
                    task_item
                    for _key, task_item in all_active_tasks
                    if task_item.get("owner_id") == owner_id
                    and task_item.get("status") in {TASK_STATUS_QUEUED, TASK_STATUS_RUNNING}
                ]
                if not can_claim_task_fairly(active_tasks, task, effective_owner_concurrency):
                    return False
            updates = {
                "status": TASK_STATUS_RUNNING,
                "error": "",
                "started_ts": time.time(),
                "started_at": _now_iso(),
                "updated_at": _now_iso(),
                "updated_ts": time.time(),
            }
            stage_timings = dict(task.get("stage_timings_ms") or {})
            if not int(stage_timings.get("queue") or 0):
                try:
                    stage_timings["queue"] = max(0, int((time.time() - float(task.get("created_ts") or time.time())) * 1000))
                except (TypeError, ValueError):
                    stage_timings["queue"] = 0
            updates["stage_timings_ms"] = stage_timings
            if self._row_level_store:
                effective_owner_concurrency = self._effective_owner_concurrency(
                    _active_owner_count(self.task_store.list_unfinished(), owner_id),
                )
                return self.task_store.claim_task(
                    key,
                    owner_concurrency=effective_owner_concurrency,
                    updates=updates,
                ) is not None
            return self._persist_updates_locked(key, updates, expected_status=TASK_STATUS_QUEUED) is not None

    def _update_task(self, key: str, **updates: Any) -> None:
        with self._lock:
            self._refresh_locked()
            updates["updated_at"] = _now_iso()
            updates["updated_ts"] = time.time()
            self._persist_updates_locked(key, updates)

    def _update_task_unless_canceled(self, key: str, **updates: Any) -> bool:
        with self._lock:
            self._refresh_locked()
            updates["updated_at"] = _now_iso()
            updates["updated_ts"] = time.time()
            return self._persist_updates_locked(key, updates, reject_status=TASK_STATUS_CANCELED) is not None

    def _record_monitoring_event(self, key: str) -> None:
        try:
            with self._lock:
                self._refresh_locked()
                task = dict(self._get_task_locked(key) or {})
            generation_monitoring_service.record_task_event(_monitoring_event_task(task))
        except Exception:
            pass

    def _load_locked(self) -> dict[str, dict[str, Any]]:
        try:
            raw_items = list(self.task_store.load_all().values())
        except Exception:
            return {}
        if not isinstance(raw_items, list):
            return {}
        tasks: dict[str, dict[str, Any]] = {}
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            task_id = _clean(item.get("id"))
            owner = _clean(item.get("owner_id"))
            if not task_id or not owner:
                continue
            status = _clean(item.get("status"))
            if status not in {TASK_STATUS_QUEUED, TASK_STATUS_RUNNING, TASK_STATUS_SUCCESS, TASK_STATUS_ERROR, TASK_STATUS_CANCELED}:
                status = TASK_STATUS_ERROR
            task = {
                "id": task_id,
                "owner_id": owner,
                "status": status,
                "mode": "edit" if item.get("mode") == "edit" else "generate",
                "model": _clean(item.get("model"), "gpt-image-2"),
                "size": _clean(item.get("size")),
                "quality": _clean(item.get("quality"), "auto"),
                "prompt": _clean(item.get("prompt")),
                "conversation_id": _clean(item.get("conversation_id")),
                "turn_id": _clean(item.get("turn_id")),
                "product_id": int(item.get("product_id") or 0),
                "template_id": int(item.get("template_id") or 0),
                "batch_id": _clean(item.get("batch_id")),
                "batch_index": max(0, int(item.get("batch_index") or 0)),
                "batch_total": max(1, int(item.get("batch_total") or 1)),
                "image_count": _positive_int(item.get("image_count"), 1),
                "attempts": int(item.get("attempts") or 0),
                "max_retries": int(item.get("max_retries") or 0),
                "progress": _clean(item.get("progress")),
                "created_at": _clean(item.get("created_at"), _now_iso()),
                "updated_at": _clean(item.get("updated_at"), _clean(item.get("created_at"), _now_iso())),
                "created_ts": item.get("created_ts"),
                "updated_ts": item.get("updated_ts"),
                "started_ts": item.get("started_ts"),
                "duration_ms": item.get("duration_ms"),
                "stage_timings_ms": item.get("stage_timings_ms") if isinstance(item.get("stage_timings_ms"), dict) else {},
            }
            identity = item.get("identity")
            if isinstance(identity, dict):
                task["identity"] = _identity_snapshot(identity)
            payload = item.get("payload")
            if isinstance(payload, dict):
                task["payload"] = payload
            data = item.get("data")
            if isinstance(data, list):
                task["data"] = data
            usage = item.get("usage")
            if isinstance(usage, dict):
                task["usage"] = usage
            error = _clean(item.get("error"))
            if error:
                task["error"] = error
            tasks[_task_key(owner, task_id)] = task
        return tasks

    def _record_library_result(
        self,
        key: str,
        identity: dict[str, object],
        prompt: str,
        base_url: str,
        ) -> None:
        try:
            with self._lock:
                self._refresh_locked()
                task = dict(self._get_task_locked(key) or {})
            image_library_service.record_task_result(
                identity=identity,
                task=task,
                prompt=prompt,
                base_url=base_url or config.base_url,
            )
        except Exception:
            pass

    def _retry_task(
        self,
        key: str,
        error_message: str,
        duration_ms: int,
        stage_timings_ms: dict[str, int] | None = None,
    ) -> bool:
        if self.task_queue is None or self.run_inline:
            return False
        if _is_content_policy_error_message(error_message):
            return False
        should_enqueue = False
        with self._lock:
            self._refresh_locked()
            task = self._get_task_locked(key)
            if task is None or task.get("status") == TASK_STATUS_CANCELED:
                return False
            max_retries = max(0, int(task.get("max_retries") or self.max_retries_getter()))
            if self._row_level_store:
                updated = self.task_store.retry_task(
                    key,
                    max_retries=max_retries,
                    updates={
                        "error": error_message,
                        "progress": "retrying",
                        "duration_ms": duration_ms,
                        "stage_timings_ms": dict(stage_timings_ms or {}),
                        "updated_at": _now_iso(),
                        "updated_ts": time.time(),
                    },
                )
                should_enqueue = updated is not None
            else:
                attempts = int(task.get("attempts") or 0) + 1
                if attempts > max_retries:
                    return False
                task.update(
                    {
                        "status": TASK_STATUS_QUEUED,
                        "attempts": attempts,
                        "error": error_message,
                        "progress": f"retrying:{attempts}/{max_retries}",
                        "duration_ms": duration_ms,
                        "stage_timings_ms": dict(stage_timings_ms or {}),
                        "updated_at": _now_iso(),
                        "updated_ts": time.time(),
                    }
                )
                self._save_locked()
                should_enqueue = True
        if should_enqueue and self.task_queue is not None:
            self.task_queue.enqueue(key)
        return should_enqueue

    def _save_locked(self) -> None:
        if not self._row_level_store:
            self.task_store.save_all(self._tasks)

    def _refresh_locked(self) -> None:
        if getattr(self.task_store, "shared", False) and not self._row_level_store:
            self._tasks = self._load_locked()

    def _get_task_locked(self, key: str) -> dict[str, Any] | None:
        if self._row_level_store:
            return self.task_store.get_task(key)
        return self._tasks.get(key)

    def _persist_updates_locked(
        self,
        key: str,
        updates: dict[str, Any],
        *,
        expected_status: str | None = None,
        reject_status: str | None = None,
    ) -> dict[str, Any] | None:
        if self._row_level_store:
            return self.task_store.update_task(
                key,
                updates,
                expected_status=expected_status,
                reject_status=reject_status,
            )
        task = self._tasks.get(key)
        if task is None:
            return None
        if expected_status is not None and task.get("status") != expected_status:
            return None
        if reject_status is not None and task.get("status") == reject_status:
            return None
        task.update(updates)
        self._save_locked()
        return task

    def _recover_unfinished_locked(self, *, requeue: bool = False) -> bool:
        if self._row_level_store:
            return bool(
                self.task_store.recover_unfinished(
                    requeue=requeue,
                    message=(
                        "service restarted, unfinished image task was requeued"
                        if requeue
                        else "service restarted, unfinished image task was interrupted"
                    ),
                )
            )
        changed = False
        for task in self._tasks.values():
            if task.get("status") in UNFINISHED_STATUSES:
                task["status"] = TASK_STATUS_QUEUED if requeue else TASK_STATUS_ERROR
                task["error"] = (
                    "service restarted, unfinished image task was requeued"
                    if requeue
                    else "服务已重启，未完成的图片任务已中断"
                )
                task["updated_at"] = _now_iso()
                task["updated_ts"] = time.time()
                changed = True
        return changed

    def _cleanup_locked(self) -> bool:
        if self._row_level_store:
            try:
                retention_days = max(1, int(self.retention_days_getter()))
            except Exception:
                retention_days = 30
            return bool(
                self.task_store.cleanup_before(
                    datetime.fromtimestamp(time.time() - retention_days * 86400)
                )
            )
        try:
            retention_days = max(1, int(self.retention_days_getter()))
        except Exception:
            retention_days = 30
        cutoff = time.time() - retention_days * 86400
        removed_keys = [
            key
            for key, task in self._tasks.items()
            if task.get("status") in TERMINAL_STATUSES and _timestamp(task.get("updated_at")) < cutoff
        ]
        for key in removed_keys:
            self._tasks.pop(key, None)
        if removed_keys:
            try:
                self.task_store.delete_keys(removed_keys)
            except Exception:
                pass
        return bool(removed_keys)

    def resume_poll(
        self,
        identity: dict[str, object],
        task_id: str,
        extra_timeout_secs: float = 30.0,
    ) -> dict[str, Any]:
        """恢复对已超时任务的轮询，额外等待 extra_timeout_secs 秒。"""
        owner = _owner_id(identity)
        key = _task_key(owner, _clean(task_id))
        with self._lock:
            task = self._get_task_locked(key)
            if task is None:
                raise ValueError("task not found")
            if task.get("status") != TASK_STATUS_ERROR:
                raise ValueError("task is not in error state")
            error_msg = _clean(task.get("error"))
            if "超时" not in error_msg:
                raise ValueError("task error is not a timeout error")
            if self.relay_enabled_getter():
                raise ValueError("当前为转发模式，超时任务不支持继续等待，请重新生成这一张")
            conversation_id = _clean(task.get("conversation_id"))
            if not conversation_id:
                raise ValueError("task has no conversation_id")
            mode = task.get("mode", "generate")
            model = task.get("model", "gpt-image-2")
            # 将任务状态重置为 running
            self._update_task(key, status=TASK_STATUS_RUNNING, error="")

        # 启动新线程继续轮询
        thread = threading.Thread(
            target=self._run_resume_poll,
            args=(key, conversation_id, extra_timeout_secs, dict(identity), mode, model),
            name=f"image-resume-{_clean(task_id)[:16]}",
            daemon=True,
        )
        thread.start()
        return _public_task(task)

    def _run_resume_poll(
        self,
        key: str,
        conversation_id: str,
        extra_timeout_secs: float,
        identity: dict[str, object],
        mode: str,
        model: str,
    ) -> None:
        """后台线程：继续轮询已有 conversation_id 的图片结果。"""
        started = time.time()
        backend = None
        try:
            from services.openai_backend_api import OpenAIBackendAPI
            from services.protocol.conversation import format_image_result

            backend = OpenAIBackendAPI()
            file_ids, sediment_ids = backend._poll_image_results(
                conversation_id,
                extra_timeout_secs,
            )
            if not file_ids and not sediment_ids:
                raise RuntimeError(
                    f"继续等待 {extra_timeout_secs} 秒后仍未找到图片结果。"
                )

            image_urls = backend.resolve_conversation_image_urls(
                conversation_id, file_ids, sediment_ids, poll=False,
            )
            if not image_urls:
                raise RuntimeError("图片 URL 解析失败")

            image_items = [
                {"b64_json": __import__("base64").b64encode(image_data).decode("ascii")}
                for image_data in backend.download_image_bytes(image_urls)
            ]
            # 获取 task 的原始 prompt（从 _public_task 的 mode 判断）
            with self._lock:
                task = self._get_task_locked(key)
                quality = _clean(task.get("quality"), "auto") if task else "auto"
                size = _clean(task.get("size")) if task else None
            data = format_image_result(
                image_items,
                "",  # prompt 已不重要，结果已经拿到了
                "b64_json",
                "",
                int(time.time()),
            )["data"]
            if not self._update_task_unless_canceled(key, status=TASK_STATUS_SUCCESS, data=data, error="", duration_ms=int((time.time() - started) * 1000)):
                return
            self._record_monitoring_event(key)
            self._log_call(
                identity,
                mode,
                model,
                started,
                "调用完成（续轮询）",
                status="success",
                urls=_collect_image_urls(data),
            )
        except Exception as exc:
            error_message = str(exc) or "resume poll failed"
            duration_ms = int((time.time() - started) * 1000)
            if not self._update_task_unless_canceled(key, status=TASK_STATUS_ERROR, error=error_message, data=[], duration_ms=duration_ms):
                return
            self._record_monitoring_event(key)
            self._log_call(
                identity,
                mode,
                model,
                started,
                "调用失败（续轮询）",
                status="failed",
                error=error_message,
            )
        finally:
            if backend is not None:
                backend.close()


def _resolve_total_concurrency(settings: dict[str, object], worker_concurrency: int) -> int:
    configured = max(0, int(settings.get("total_concurrency") or 0))
    if configured > 0:
        return configured
    try:
        from services.account_service import account_service

        accounts = account_service.list_accounts()
    except Exception:
        accounts = []
    available_accounts = 0
    for account in accounts:
        if not isinstance(account, dict):
            continue
        status = _clean(account.get("status"))
        if status in {"绂佺敤", "闄愭祦", "寮傚父"}:
            continue
        try:
            quota = int(account.get("quota") or 0)
        except (TypeError, ValueError):
            quota = 0
        if quota <= 0:
            continue
        available_accounts += 1
    if available_accounts > 0:
        per_account = max(1, int(config.image_account_concurrency or 1))
        return max(1, available_accounts * per_account)
    return max(1, int(worker_concurrency or config.image_account_concurrency or 1))


def _build_default_task_store(path: Path, queue_enabled: bool) -> ImageTaskStore:
    settings = config.get_image_task_queue_settings()
    database_url = _clean(settings.get("database_url"))
    try:
        store = DatabaseImageTaskStore(database_url)
        if store.is_empty() and path.exists():
            legacy_tasks = JsonImageTaskStore(path).load_all()
            if legacy_tasks:
                store.save_all(legacy_tasks)
        return store
    except Exception:
        if queue_enabled:
            raise
        return JsonImageTaskStore(path)


def _build_default_task_queue() -> ImageTaskQueue | None:
    settings = config.get_image_task_queue_settings()
    if not settings.get("enabled"):
        return None
    queue_class = CeleryImageTaskQueue if settings.get("executor") == "celery" else RedisImageTaskQueue
    worker_concurrency = max(1, int(settings.get("worker_concurrency") or 1))
    total_concurrency = _resolve_total_concurrency(settings, worker_concurrency)
    return queue_class(
        redis_url=_clean(settings.get("redis_url"), "redis://127.0.0.1:6379/0"),
        queue_name=_clean(settings.get("queue_name"), "ai_image_tasks"),
        max_concurrency=total_concurrency,
        slot_lease_secs=max(60, int(settings.get("slot_lease_secs") or 7200)),
    )


def create_image_task_service() -> ImageTaskService:
    path = DATA_DIR / "image_tasks.json"
    queue = _build_default_task_queue()
    queue_enabled = queue is not None
    settings = config.get_image_task_queue_settings()
    return ImageTaskService(
        path,
        task_store=_build_default_task_store(path, queue_enabled),
        task_queue=queue,
        run_inline=not queue_enabled,
        max_retries_getter=lambda: int(settings.get("max_retries") or 0),
    )


image_task_service = create_image_task_service()
atexit.register(image_task_service.close)
