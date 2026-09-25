from __future__ import annotations

import atexit
import logging
import os
import random
import socket
import threading
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any
from uuid import uuid4

from services.image.image_task_queue import ImageTaskQueue
from services.platform.config import DATA_DIR, config
from services.platform.enterprise_schema import resolve_enterprise_database_url
from services.platform.log_service import LOG_TYPE_CALL, log_service
from services.audio.models import (
    AUDIO_GENERATION_MAX_CHARS,
    AudioGenerationRequest,
    DoubaoAudioGenerationParams,
)
from services.audio.audio_generation_provider import run_audio_generation, AudioGenerationProviderError
from services.audio.audio_generation_queue import RedisAudioGenerationQueue, AudioDelivery
from services.audio.audio_generation_storage import audio_generation_storage_service
from services.audio.audio_generation_task_store import DatabaseAudioGenerationTaskStore, AudioGenerationTaskStore, encode_cursor


TASK_STATUS_QUEUED = "queued"
TASK_STATUS_RUNNING = "running"
TASK_STATUS_SUCCESS = "success"
TASK_STATUS_ERROR = "error"
TASK_STATUS_CANCELED = "canceled"
TERMINAL_STATUSES = {TASK_STATUS_SUCCESS, TASK_STATUS_ERROR, TASK_STATUS_CANCELED}
UNFINISHED_STATUSES = {TASK_STATUS_QUEUED, TASK_STATUS_RUNNING}
DEFAULT_EMPTY_TASK_LIST_LIMIT = 200
LOGGER = logging.getLogger(__name__)


class LeaseLost(RuntimeError):
    pass


def _env_seconds(name: str, default: int, minimum: int = 1) -> int:
    return _positive_int(os.getenv(name), default, minimum)


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _clean(value: object, default: str = "", limit: int = 12000) -> str:
    text = str(value if value is not None else default).strip()
    return (text or default)[:limit]


def _owner_id(identity: dict[str, object]) -> str:
    return _clean(identity.get("id") or identity.get("username"), "anonymous", 191)


def _identity_snapshot(identity: dict[str, object]) -> dict[str, object]:
    return {
        "id": _owner_id(identity),
        "name": _clean(identity.get("name"), limit=191),
        "username": _clean(identity.get("username"), limit=191),
        "role": _clean(identity.get("role"), "user", 40),
    }


def _positive_int(value: object, default: int = 1, minimum: int = 1) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return max(minimum, default)
    return max(minimum, number)


def _task_key(owner_id: str, task_id: str) -> str:
    return f"{owner_id}:{task_id}"


def _public_task(task: dict[str, Any]) -> dict[str, Any]:
    snapshot = task.get("identity") if isinstance(task.get("identity"), dict) else {}
    owner_id = _clean(task.get("owner_id") or snapshot.get("id"), "anonymous", 191)
    owner_name = _clean(snapshot.get("name"), limit=191)
    owner_username = _clean(snapshot.get("username"), limit=191)
    item: dict[str, Any] = {
        "id": task.get("id"),
        "owner_id": owner_id,
        "owner_username": owner_username,
        "status": TASK_STATUS_CANCELED if task.get("cancel_requested") else task.get("status"),
        "mode": task.get("mode"),
        "model": task.get("model"),
        "prompt": task.get("prompt"),
        "params": task.get("params") if isinstance(task.get("params"), dict) else {},
        "created_at": task.get("created_at"),
        "updated_at": task.get("updated_at"),
    }
    if owner_name:
        item["owner_name"] = owner_name
    for key in (
        "conversation_id",
        "turn_id",
        "progress",
        "error",
        "upstream_task_id",
        "audio_url",
        "cover_url",
        "duration_ms",
        "cost",
        "attempts",
        "max_retries",
        "storage",
        "file_size",
        "storage_error",
        "cancellation_pending",
        "reconciliation_required",
        "storage_pending",
        "next_attempt_ts",
        "voice_id",
        "voice_label",
        "output_format",
    ):
        if task.get(key) is not None and task.get(key) != "":
            item[key] = task.get(key)
    if isinstance(task.get("data"), list):
        item["data"] = task.get("data")
    if task.get("cancel_requested"):
        item["cancellation_pending"] = task.get("status") in UNFINISHED_STATUSES
    if task.get("storage") in {"local", "oss"}:
        try:
            url = audio_generation_storage_service.resolve_result_url(task)
            item["audio_url"] = url
            item["data"] = [{"type": "audio", "url": url, "cover_url": task.get("cover_url", "")}]
        except Exception:
            item["audio_url"] = ""
            item["data"] = []
            item["storage_error"] = "Audio access temporarily unavailable"
    if task.get("status") in UNFINISHED_STATUSES:
        base_ts = task.get("started_ts") if task.get("status") == TASK_STATUS_RUNNING else task.get("created_ts")
        if base_ts:
            try:
                item["elapsed_secs"] = round(time.time() - float(base_ts), 1)
            except (TypeError, ValueError):
                pass
    return item


class AudioGenerationTaskService:
    def __init__(
        self,
        *,
        task_store: AudioGenerationTaskStore,
        task_queue: ImageTaskQueue | None = None,
        run_inline: bool | None = None,
        generation_handler: Callable[[dict[str, Any]], dict[str, Any]] = run_audio_generation,
        enabled_getter: Callable[[], bool] | None = None,
        max_retries_getter: Callable[[], int] | None = None,
        owner_pending_limit_getter: Callable[[], int] | None = None,
        owner_concurrency_getter: Callable[[], int] | None = None,
        stale_running_timeout_getter: Callable[[], int] | None = None,
        result_storage_handler: Callable[..., Any] | None = None,
        download_results_getter: Callable[[], bool] | None = None,
    ) -> None:
        self.task_store = task_store
        self.task_queue = task_queue
        self.run_inline = (task_queue is None) if run_inline is None else bool(run_inline)
        self.generation_handler = generation_handler
        self.enabled_getter = enabled_getter or (lambda: bool(config.get_audio_generation_settings().get("enabled")))
        self.max_retries_getter = max_retries_getter or (lambda: int(config.get_audio_generation_settings().get("max_retries") or 0))
        self.owner_pending_limit_getter = owner_pending_limit_getter or (
            lambda: int(config.get_audio_generation_settings().get("owner_pending_limit") or 8)
        )
        self.owner_concurrency_getter = owner_concurrency_getter or (
            lambda: int(config.get_audio_generation_settings().get("owner_concurrency") or 1)
        )
        self.stale_running_timeout_getter = stale_running_timeout_getter or (
            lambda: int(config.get_audio_generation_settings().get("stale_running_timeout_secs") or 3600)
        )
        self.result_storage_handler = result_storage_handler or audio_generation_storage_service.store_remote_audio
        self.download_results_getter = download_results_getter or (
            lambda: bool(config.get_audio_generation_settings().get("download_results"))
        )
        settings = config.get_audio_generation_settings()
        self._worker_concurrency = max(1, int(settings.get("worker_concurrency") or 1))
        self._lock = threading.RLock()
        self._recovery_cursor = ""
        self._recovery_lock = threading.Lock()
        self._stop = threading.Event()
        self._inline_threads: set[threading.Thread] = set()

    def submit_task(
        self,
        identity: dict[str, object],
        *,
        client_task_id: str,
        prompt: str,
        model: str,
        params: dict[str, Any] | None = None,
        conversation_id: str = "",
        turn_id: str = "",
    ) -> dict[str, Any]:
        if not self.enabled_getter():
            raise ValueError("audio generation is not enabled")
        task_id = _clean(client_task_id, limit=191)
        safe_prompt = _clean(prompt, limit=AUDIO_GENERATION_MAX_CHARS)
        safe_model = _clean(model, limit=191)
        if not task_id:
            raise ValueError("client_task_id is required")
        if not safe_prompt:
            raise ValueError("prompt is required")
        if not safe_model:
            raise ValueError("model is required")
        owner = _owner_id(identity)
        key = _task_key(owner, task_id)
        with self._lock:
            existing = self.task_store.get_task(key)
            if existing is not None:
                return _public_task(existing)
            request = AudioGenerationRequest.model_validate({
                "model": safe_model,
                "prompt": safe_prompt,
                "params": params or {},
            })
            provider_params = request.params.model_dump(exclude_none=True)
            if isinstance(request.params, DoubaoAudioGenerationParams) and request.params.emotion == "auto":
                provider_params.pop("emotion_scale", None)
            speaker_configs = provider_params.get("speaker_voice_configs")
            voice_ids = [
                _clean(item.get("voice_id"), limit=191)
                for item in speaker_configs or []
                if isinstance(item, dict) and _clean(item.get("voice_id"), limit=191)
            ]
            voice_id = _clean(provider_params.get("voice_id"), limit=191) or ",".join(voice_ids)
            normalized_mode = "dialogue" if speaker_configs else "single_voice"
            now = _now_iso()
            task = {
                "id": task_id,
                "owner_id": owner,
                "status": TASK_STATUS_QUEUED,
                "mode": normalized_mode,
                "model": request.model,
                "prompt": safe_prompt,
                "params": provider_params,
                "voice_id": voice_id,
                "output_format": _clean(provider_params.get("format"), "mp3", 32),
                "conversation_id": _clean(conversation_id, limit=191),
                "turn_id": _clean(turn_id, limit=191),
                "attempts": 0,
                "max_retries": max(0, int(self.max_retries_getter())),
                "progress": "queued",
                "identity": _identity_snapshot(identity),
                "created_at": now,
                "updated_at": now,
                "created_ts": time.time(),
            }
            task, created = self.task_store.create_task(key, task, pending_limit=max(1, int(self.owner_pending_limit_getter())))
            if not created:
                return _public_task(task)
        if self.task_queue is not None and not self.run_inline:
            self._enqueue(key)
        else:
            def execute_inline():
                try:
                    while not self._stop.is_set():
                        self._run_task(key)
                        current = self.task_store.get_task(key)
                        if not current or current.get("status") != TASK_STATUS_QUEUED:
                            break
                        self._stop.wait(max(0.1, float(current.get("next_attempt_ts") or 0) - time.time()))
                finally:
                    with self._lock:
                        self._inline_threads.discard(threading.current_thread())
            thread = threading.Thread(target=execute_inline, name=f"audio-generation-{task_id[:16]}", daemon=True)
            with self._lock:
                self._inline_threads.add(thread)
            thread.start()
        return _public_task(task)

    def list_tasks(
        self,
        identity: dict[str, object],
        task_ids: list[str],
        *,
        limit: int | None = None,
        include_all_owners: bool = False,
        owner_id_filter: str = "",
        conversation_id_filter: str = "",
        cursor: str = "",
        status_filter: str = "",
        query_filter: str = "",
    ) -> dict[str, Any]:
        owner = _owner_id(identity)
        is_admin = _clean(identity.get("role"), "user", 40) == "admin"
        requested_owner_id = _clean(owner_id_filter, limit=191)
        requested_conversation_id = _clean(conversation_id_filter, limit=191)
        requested_status = _clean(status_filter, limit=32)
        if requested_status and requested_status not in {
            TASK_STATUS_QUEUED, TASK_STATUS_RUNNING, TASK_STATUS_SUCCESS, TASK_STATUS_ERROR, TASK_STATUS_CANCELED,
        }:
            raise ValueError("invalid audio task status")
        requested_query = _clean(query_filter, limit=200)
        all_owners = is_admin and (include_all_owners or bool(requested_owner_id))
        scope_owner_id = requested_owner_id if all_owners and requested_owner_id else None if all_owners else owner
        requested_ids = [_clean(task_id, limit=191) for task_id in task_ids if _clean(task_id, limit=191)]
        page_limit = min(500, _positive_int(limit, DEFAULT_EMPTY_TASK_LIST_LIMIT, 1))
        raw_items = self.task_store.list_tasks(
            scope_owner_id,
            requested_ids or None,
            conversation_id=requested_conversation_id,
            limit=None if requested_ids else page_limit + 1,
            cursor=cursor,
            status="" if requested_ids else requested_status,
            query_text="" if requested_ids else requested_query,
        )
        indexed = {_clean(task.get("id"), limit=191): task for task in raw_items}
        if requested_ids:
            return {
                "items": [_public_task(indexed[task_id]) for task_id in requested_ids if task_id in indexed],
                "missing_ids": [task_id for task_id in requested_ids if task_id not in indexed],
            }
        has_more = len(raw_items) > page_limit
        items = [_public_task(task) for task in raw_items[:page_limit]]
        total = self.task_store.count_all_tasks(
            scope_owner_id,
            conversation_id=requested_conversation_id,
            status=requested_status,
            query_text=requested_query,
        )
        return {
            "items": items,
            "missing_ids": [],
            "has_more": has_more,
            "limit": page_limit,
            "total": total,
            "next_cursor": encode_cursor(raw_items[page_limit - 1]) if has_more else None,
        }

    def cancel_task(self, identity: dict[str, object], task_id: str) -> dict[str, Any]:
        owner = _owner_id(identity)
        normalized_task_id = _clean(task_id, limit=191)
        if not normalized_task_id:
            raise ValueError("task_id is required")
        key = _task_key(owner, normalized_task_id)
        updated = self.task_store.request_cancel(key)
        if updated is None:
            raise ValueError("task not found")
        if self.task_queue is not None:
            try:
                self.task_queue.notify_task_update(key)
            except Exception:
                pass
        return _public_task(updated)

    def reconcile_task(self, identity: dict[str, object], task_id: str) -> dict[str, Any]:
        owner = _owner_id(identity)
        normalized_task_id = _clean(task_id, limit=191)
        if not normalized_task_id:
            raise ValueError("task_id is required")
        key = _task_key(owner, normalized_task_id)
        with self._lock:
            task = self.task_store.get_task(key)
            if task is None:
                raise ValueError("task not found")
            if not task.get("reconciliation_required"):
                return _public_task(task)
            if not _clean(task.get("upstream_task_id"), limit=191):
                raise ValueError("upstream task id is unknown; automatic reconciliation is unavailable")
            updated = self.task_store.update_task(
                key,
                {
                    "status": TASK_STATUS_QUEUED,
                    "execution_token": "",
                    "progress": "reconciliation_queued",
                    "reconciliation_required": False,
                    "cancellation_pending": bool(task.get("cancel_requested")),
                    "poll_started_ts": time.time(),
                    "next_attempt_ts": time.time(),
                    "attempts": 0,
                    "error": "",
                    "updated_at": _now_iso(),
                    "updated_ts": time.time(),
                },
            )
            if updated is None:
                raise ValueError("task could not be queued for reconciliation")
        self._enqueue(key)
        return _public_task(updated)

    def delete_task(self, identity: dict[str, object], task_id: str) -> dict[str, Any]:
        owner = _owner_id(identity)
        normalized_task_id = _clean(task_id, limit=191)
        if not normalized_task_id:
            raise ValueError("task_id is required")
        key = _task_key(owner, normalized_task_id)
        with self._lock:
            task = self.task_store.get_task(key)
            if task is None:
                raise ValueError("task not found")
            if task.get("status") in UNFINISHED_STATUSES:
                raise ValueError("audio task is still in progress")
            if not self.task_store.delete_task(key):
                raise ValueError("task not found")
        return {"ok": True, "deleted": 1}

    def delete_conversation(self, identity: dict[str, object], conversation_id: str) -> dict[str, Any]:
        owner = _owner_id(identity)
        normalized_conversation_id = _clean(conversation_id, limit=191)
        if not normalized_conversation_id:
            raise ValueError("conversation_id is required")
        with self._lock:
            tasks = self.task_store.list_tasks(
                owner,
                conversation_id=normalized_conversation_id,
            )
            if not tasks:
                raise ValueError("audio conversation not found")
            if any(task.get("status") in UNFINISHED_STATUSES for task in tasks):
                raise ValueError("audio conversation has tasks still in progress")
            if any(task.get("reconciliation_required") for task in tasks):
                raise ValueError("audio conversation has tasks requiring billing reconciliation")
            deleted = sum(
                1
                for task in tasks
                if self.task_store.delete_task(_task_key(owner, _clean(task.get("id"), limit=191)))
            )
        return {"ok": True, "deleted": deleted}

    def process_queued_task(self, task_key: str) -> dict[str, Any] | None:
        key = _clean(task_key, limit=383)
        if not key:
            return None
        self._run_task(key)
        task = self.task_store.get_task(key)
        return _public_task(task) if task else None

    def work_once(self, timeout_secs: int = 5) -> dict[str, Any] | None:
        if self.task_queue is None:
            raise RuntimeError("audio generation task queue is not configured")
        delivery = self.task_queue.reserve(timeout_secs)
        if not delivery:
            return None
        self._run_task(delivery.key, delivery=delivery)
        task = self.task_store.get_task(delivery.key)
        if task and task.get("status") == TASK_STATUS_QUEUED:
            delay = max(1.0, float(task.get("next_attempt_ts") or 0) - time.time())
            self.task_queue.retry(delivery, delay)
        else:
            self.task_queue.ack(delivery)
        return _public_task(task) if task else None

    def work_forever(self, stop_event: threading.Event | None = None, timeout_secs: int = 5) -> None:
        if self.task_queue is None:
            raise RuntimeError("audio generation task queue is not configured")
        shutdown_event = stop_event or threading.Event()
        worker_id = f"{socket.gethostname()}:{os.getpid()}:audio-generation"

        def maintenance() -> None:
            last_recovery = 0.0
            while not shutdown_event.is_set():
                try:
                    for index in range(self._worker_concurrency):
                        self.task_queue.touch_worker(f"{worker_id}:{index}", timeout_secs=60)
                    if time.monotonic() - last_recovery >= 10:
                        self.task_queue.recover_deliveries()
                        self.recover_stale_unfinished()
                        self.task_store.cleanup_batch(
                            lambda task: audio_generation_storage_service.delete_stored_audio(
                                task,
                                owner_id=_clean(task.get("owner_id"), limit=191),
                            )
                        )
                        last_recovery = time.monotonic()
                except Exception:
                    LOGGER.exception("Audio worker maintenance failed")
                shutdown_event.wait(10)

        def _worker_loop(index: int) -> None:
            while not shutdown_event.is_set():
                try:
                    self.work_once(timeout_secs)
                except Exception as exc:
                    try:
                        log_service.add(LOG_TYPE_CALL, "audio generation worker loop failed", {"error": str(exc)})
                    except Exception:
                        pass
                    shutdown_event.wait(1.0)

        threads = [
            threading.Thread(target=_worker_loop, args=(index,), name=f"audio-generation-worker-{index + 1}", daemon=True)
            for index in range(self._worker_concurrency)
        ]
        heartbeat_thread = threading.Thread(target=maintenance, name="audio-worker-maintenance", daemon=True)
        heartbeat_thread.start()
        for thread in threads:
            thread.start()
        try:
            while not shutdown_event.is_set():
                shutdown_event.wait(0.5)
        finally:
            shutdown_event.set()
            self._stop.set()
            for thread in threads:
                thread.join(timeout=35)
            heartbeat_thread.join(timeout=10)
            for index in range(self._worker_concurrency):
                try:
                    self.task_queue.forget_worker(f"{worker_id}:{index}")
                except Exception:
                    pass

    def monitoring_snapshot(self) -> dict[str, Any]:
        queued_tasks = self.task_store.count_tasks(None, {TASK_STATUS_QUEUED})
        running_tasks = self.task_store.count_tasks(None, {TASK_STATUS_RUNNING})
        queue = self.task_queue
        queue_error = ""
        queue_depths: dict[str, int] = {}
        processing_tasks = 0
        active_slots = 0
        try:
            if queue is not None:
                raw_depths = getattr(queue, "queue_depths", lambda: {})() or {}
                queue_depths = {str(name): int(value or 0) for name, value in raw_depths.items()}
            queue_depth = sum(queue_depths.values())
        except Exception as exc:
            queue_depth = 0
            queue_error = str(exc)[:200]
        try:
            processing_tasks = int(getattr(queue, "processing_count", lambda: 0)() or 0) if queue is not None else 0
        except Exception as exc:
            queue_error = queue_error or str(exc)[:200]
        try:
            active_slots = int(getattr(queue, "active_slot_count", lambda: 0)() or 0) if queue is not None else 0
        except Exception as exc:
            queue_error = queue_error or str(exc)[:200]
        try:
            active_workers = int(getattr(queue, "active_worker_count", lambda: 0)() or 0) if queue is not None else 0
        except Exception:
            active_workers = 0
        total_concurrency = int(config.get_audio_generation_settings().get("total_concurrency") or 2)
        return {
            "enabled": bool(self.enabled_getter()),
            "queue_enabled": queue is not None,
            "queue_depth": queue_depth,
            "queue_depths": queue_depths,
            "processing_tasks": processing_tasks,
            "queued_tasks": queued_tasks,
            "running_tasks": running_tasks,
            "active_workers": active_workers,
            "worker_concurrency": self._worker_concurrency,
            "active_slots": active_slots,
            "slot_limit": total_concurrency,
            "configured_total_concurrency": total_concurrency,
            "total_concurrency": total_concurrency,
            "worker_heartbeat_secs": 10,
            "queue_error": queue_error,
            "owner_concurrency": max(1, int(self.owner_concurrency_getter())),
            "owner_pending_limit": max(1, int(self.owner_pending_limit_getter())),
            "stale_running_timeout_secs": max(60, int(self.stale_running_timeout_getter())),
        }

    def recover_stale_unfinished(self) -> int:
        if not self._recovery_lock.acquire(blocking=False):
            return 0
        recovered = 0
        try:
            rows = self.task_store.list_unfinished(limit=200, after_key=self._recovery_cursor)
            self._recovery_cursor = rows[-1][0] if len(rows) == 200 else ""
            for key, task in rows:
                if task.get("status") == TASK_STATUS_RUNNING:
                    timeout = 90 if task.get("execution_token") else max(60, int(self.stale_running_timeout_getter()))
                    task = self.task_store.recover_task(key, time.time() - timeout)
                    if task is None:
                        continue
                    recovered += 1
                if task.get("status") == TASK_STATUS_QUEUED:
                    self._enqueue(key, max(0.0, float(task.get("next_attempt_ts") or 0) - time.time()))
        finally:
            self._recovery_lock.release()
        return recovered

    def _enqueue(self, key: str, delay: float = 0) -> None:
        if self.task_queue is None:
            return
        try:
            self.task_queue.enqueue(key, delay_secs=delay)
        except Exception:
            # The committed queued row is the durable outbox; maintenance repairs it.
            LOGGER.exception("Audio dispatch failed; database task retained for recovery: %s", key)

    def close(self) -> None:
        self._stop.set()
        for thread in list(self._inline_threads):
            thread.join(timeout=5)
        close = getattr(self.task_store, "close", None)
        if callable(close):
            close()

    def _run_task(self, key: str, *, delivery: AudioDelivery | None = None) -> None:
        started = time.time()
        owner_id = ""
        token = uuid4().hex
        with self._lock:
            task = self.task_store.get_task(key)
            if task is None or task.get("status") != TASK_STATUS_QUEUED:
                return
            owner_id = _clean(task.get("owner_id"), "anonymous", 191)
            claimed = self.task_store.claim_task(
                key,
                owner_id=owner_id,
                owner_concurrency=max(1, int(self.owner_concurrency_getter())),
                updates={
                    "status": TASK_STATUS_RUNNING,
                    "progress": "starting",
                    "execution_token": token,
                    "heartbeat_ts": time.time(),
                    "error": "",
                    "started_at": _now_iso(),
                    "started_ts": time.time(),
                    "updated_at": _now_iso(),
                    "updated_ts": time.time(),
                },
            )
            if claimed is None:
                return
            task = claimed
        identity = task.get("identity") if isinstance(task.get("identity"), dict) else {"id": owner_id}

        lost_lease = threading.Event()
        heartbeat_done = threading.Event()

        def update(**changes):
            result = self.task_store.update_task(key, {"updated_at": _now_iso(), "updated_ts": time.time(), **changes},
                                                expected_token=token, expected_status=TASK_STATUS_RUNNING)
            if result is None:
                raise LeaseLost("Audio task ownership changed")
            return result

        def checkpoint():
            if lost_lease.is_set() or self._stop.is_set():
                raise LeaseLost("Audio worker stopping or lease unavailable")
            current = self.task_store.get_task(key)
            if not current or current.get("execution_token") != token or current.get("status") != TASK_STATUS_RUNNING:
                raise LeaseLost("Audio task is no longer owned by this worker")

        def heartbeat():
            while not heartbeat_done.wait(10):
                try:
                    if delivery and not self.task_queue.renew_delivery(delivery):
                        raise LeaseLost("Audio delivery expired")
                    if distributed_slot and not self.task_queue.renew_slot(slot_token):
                        raise LeaseLost("Audio capacity lease expired")
                    update(heartbeat_ts=time.time())
                except Exception:
                    lost_lease.set()
                    LOGGER.exception("Audio task heartbeat failed: %s", key)
                    return

        def progress_callback(step: str) -> None:
            checkpoint()
            update(progress=_clean(step, "running", 120))

        def submission_started_callback():
            checkpoint()
            update(submission_state="submitting", poll_started_ts=time.time(), progress="submitting")

        def submission_callback(upstream_task_id: str, credential_id: str = "", cost=None) -> None:
            update(upstream_task_id=_clean(upstream_task_id, limit=191), credential_id=credential_id,
                   cost=cost, submission_state="submitted", progress="submitted")

        handler_payload = {
            "prompt": task.get("prompt"),
            "model": task.get("model"),
            "params": task.get("params") if isinstance(task.get("params"), dict) else {},
            "progress_callback": progress_callback,
            "submission_callback": submission_callback,
            "submission_started_callback": submission_started_callback,
            "checkpoint_callback": checkpoint,
            "cost_callback": lambda cost: update(cost=cost),
            "upstream_task_id": task.get("upstream_task_id", ""),
            "credential_id": task.get("credential_id", ""),
            "submission_state": task.get("submission_state", ""),
            "previous_cost": task.get("cost"),
            "poll_started_ts": task.get("poll_started_ts"),
        }
        slot_token = f"{os.getpid()}:{threading.get_ident()}:{key}"
        acquire_slot = getattr(self.task_queue, "acquire_slot", None)
        release_slot = getattr(self.task_queue, "release_slot", None)
        distributed_slot = False
        heartbeat_thread = threading.Thread(target=heartbeat, name="audio-task-heartbeat", daemon=True)
        heartbeat_thread.start()
        try:
            if callable(acquire_slot):
                distributed_slot = bool(acquire_slot(slot_token, timeout_secs=5))
                if not distributed_slot:
                    update(status=TASK_STATUS_QUEUED, execution_token="", progress="waiting_for_slot",
                           next_attempt_ts=time.time() + 2)
                    return
            checkpoint()
            # Transfer retries reuse the persisted result, never a new paid generation.
            result = task.get("upstream_result") or self.generation_handler(handler_payload)
            if not isinstance(result, dict):
                raise RuntimeError("audio generation handler did not return an object")
            audio_url = _clean(result.get("audio_url"), limit=2000)
            if not audio_url:
                raise RuntimeError("audio generation result missing audio_url")
            checkpoint()
            current = self.task_store.get_task(key) or task
            update(upstream_result=result, upstream_task_id=result.get("upstream_task_id") or current.get("upstream_task_id", ""),
                   cost=result.get("cost"), submission_state="completed", reconciliation_required=False)
            source_audio_url = audio_url
            stored_audio = None
            storage_error = ""
            if self.download_results_getter():
                try:
                    stored_audio = self.result_storage_handler(
                        audio_url,
                        owner_id=owner_id,
                        task_id=_clean(task.get("id"), "audio-task", 191),
                        base_url=config.base_url,
                    )
                    audio_url = stored_audio.url
                except Exception as exc:
                    storage_error = _clean(str(exc), limit=1000)
                    update(storage_pending=True, storage_error=storage_error, progress="storage_pending")
                    LOGGER.error("Audio result persistence failed for %s: %s", key, storage_error)
                    if self._retry_task(key, storage_error, int((time.time() - started) * 1000),
                                        token=token, stage="storage"):
                        return
                    update(status=TASK_STATUS_ERROR, execution_token="", error="Audio generated but storage failed; retry storage only.")
                    return
            data = [{
                "type": "audio",
                "url": audio_url,
                "cover_url": _clean(result.get("cover_url"), limit=2000),
            }]
            duration_ms = int((time.time() - started) * 1000)
            checkpoint()
            update(
                status=TASK_STATUS_SUCCESS,
                execution_token="",
                progress="completed",
                audio_url=audio_url,
                cover_url=_clean(result.get("cover_url"), limit=2000),
                data=data,
                cost=result.get("cost"),
                upstream_task_id=_clean(result.get("upstream_task_id") or current.get("upstream_task_id"), limit=191),
                raw_result=result.get("raw") if isinstance(result.get("raw"), dict) else {},
                duration_ms=duration_ms,
                source_audio_url=source_audio_url if stored_audio is not None else "",
                storage=stored_audio.storage if stored_audio is not None else "remote",
                storage_rel=stored_audio.relative_path if stored_audio is not None else "",
                file_size=stored_audio.size if stored_audio is not None else None,
                storage_error=storage_error,
                storage_pending=False,
                reconciliation_required=False,
                completed_at=_now_iso(),
                **({"storage_object_key": getattr(stored_audio, "object_key", ""),
                    "storage_bucket": getattr(stored_audio, "bucket", "")} if stored_audio else {}),
                error="",
            )
            self._log_call(identity, task, started, "completed")
        except LeaseLost:
            LOGGER.warning("Audio execution stopped after lease/cancellation change: %s", key)
        except Exception as exc:
            error_message = str(exc) or "audio generation failed"
            duration_ms = int((time.time() - started) * 1000)
            current = self.task_store.get_task(key) or {}
            if current.get("execution_token") != token or current.get("status") != TASK_STATUS_RUNNING:
                return
            exception_upstream_id = _clean(getattr(exc, "upstream_task_id", ""), limit=191)
            exception_credential_id = _clean(getattr(exc, "credential_id", ""), limit=191)
            exception_cost = getattr(exc, "cost", None)
            recovery_metadata: dict[str, Any] = {}
            if exception_upstream_id:
                recovery_metadata.update(
                    upstream_task_id=exception_upstream_id,
                    submission_state="submitted",
                )
            if exception_credential_id:
                recovery_metadata["credential_id"] = exception_credential_id
            if exception_cost is not None:
                recovery_metadata["cost"] = exception_cost
            if recovery_metadata:
                current = update(**recovery_metadata)
            known_upstream = bool(current.get("upstream_task_id") or exception_upstream_id)
            if isinstance(exc, AudioGenerationProviderError):
                uncertain = bool(exc.submission_uncertain) and not known_upstream
            else:
                uncertain = current.get("submission_state") == "submitting" and not known_upstream
            retryable = bool(getattr(exc, "retryable", False)) and not uncertain
            if retryable and not known_upstream:
                current = update(submission_state="rejected")
            if retryable and self._retry_task(key, error_message, duration_ms, token=token):
                self._log_call(identity, task, started, "failed, retry queued", status="failed", error=error_message)
                return
            update(
                status=TASK_STATUS_ERROR,
                execution_token="",
                progress="submission_uncertain" if uncertain else "failed",
                reconciliation_required=uncertain or (known_upstream and not getattr(exc, "upstream_finished", False)),
                error=error_message,
                duration_ms=duration_ms,
                completed_at=_now_iso(),
            )
            self._log_call(identity, task, started, "failed", status="failed", error=error_message)
        finally:
            heartbeat_done.set()
            heartbeat_thread.join(timeout=15)
            if distributed_slot and callable(release_slot):
                try:
                    release_slot(slot_token)
                except Exception:
                    LOGGER.exception("Audio capacity release failed; lease will expire")

    def _update_unless_canceled(self, key: str, **updates: Any) -> bool:
        updates["updated_at"] = _now_iso()
        updates["updated_ts"] = time.time()
        updated = self.task_store.update_task(key, updates, reject_status=TASK_STATUS_CANCELED)
        if updated is not None and self.task_queue is not None:
            try:
                self.task_queue.notify_task_update(key)
            except Exception:
                pass
        return updated is not None

    def _retry_task(self, key: str, error_message: str, duration_ms: int, *, token: str, stage: str = "provider") -> bool:
        if self.task_queue is None or self.run_inline:
            return False
        with self._lock:
            task = self.task_store.get_task(key)
            if task is None or task.get("status") == TASK_STATUS_CANCELED:
                return False
            counter = "storage_attempts" if stage == "storage" else "attempts"
            attempts = int(task.get(counter) or 0) + 1
            max_retries = _env_seconds("AUDIO_GENERATION_STORAGE_MAX_RETRIES", 3, 0) if stage == "storage" else max(0, int(task.get("max_retries", self.max_retries_getter())))
            if attempts > max_retries:
                return False
            delay = min(_env_seconds("AUDIO_GENERATION_RETRY_MAX_DELAY_SECS", 120),
                        _env_seconds("AUDIO_GENERATION_RETRY_BASE_DELAY_SECS", 5) * 2 ** (attempts - 1))
            delay *= random.uniform(1, 1.2)
            updated = self.task_store.update_task(
                key,
                {
                    "status": TASK_STATUS_QUEUED,
                    counter: attempts,
                    "execution_token": "",
                    "next_attempt_ts": time.time() + delay,
                    "progress": f"retrying:{attempts}/{max_retries}",
                    "error": error_message,
                    "duration_ms": duration_ms,
                    "updated_at": _now_iso(),
                    "updated_ts": time.time(),
                },
                expected_token=token,
                expected_status=TASK_STATUS_RUNNING,
            )
            should_enqueue = updated is not None
        if should_enqueue and self.task_queue is not None:
            self._enqueue(key, delay)
        return should_enqueue

    def _log_call(
        self,
        identity: dict[str, object],
        task: dict[str, Any],
        started: float,
        suffix: str,
        *,
        status: str = "success",
        error: str = "",
    ) -> None:
        detail = {
            "key_id": identity.get("id"),
            "key_name": identity.get("name"),
            "role": identity.get("role"),
            "endpoint": "/api/audio-generation/tasks",
            "model": task.get("model"),
            "mode": task.get("mode"),
            "started_at": datetime.fromtimestamp(started).strftime("%Y-%m-%d %H:%M:%S"),
            "ended_at": _now_iso(),
            "duration_ms": int((time.time() - started) * 1000),
            "status": status,
        }
        if error:
            detail["error"] = error
        try:
            log_service.add(LOG_TYPE_CALL, f"audio generation {suffix}", detail)
        except Exception:
            pass


def _build_default_task_store() -> AudioGenerationTaskStore:
    settings = config.get_audio_generation_settings()
    try:
        default_database_url = resolve_enterprise_database_url()
    except Exception:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        default_database_url = f"sqlite:///{DATA_DIR / 'audio_generation_tasks.db'}"
    database_url = _clean(settings.get("database_url")) or default_database_url
    return DatabaseAudioGenerationTaskStore(database_url)


def _build_default_task_queue() -> ImageTaskQueue | None:
    settings = config.get_audio_generation_settings()
    if not bool(settings.get("enabled") and settings.get("queue_enabled")):
        return None
    total_concurrency = max(1, int(settings.get("total_concurrency") or 1))
    return RedisAudioGenerationQueue(
        redis_url=_clean(settings.get("redis_url"), "redis://127.0.0.1:6379/0"),
        queue_name=_clean(settings.get("queue_name"), "ai_audio_generation_tasks"),
        max_concurrency=total_concurrency,
        slot_lease_secs=max(60, int(settings.get("slot_lease_secs") or 3600)),
        adaptive_concurrency_enabled=False,
    )


def create_audio_generation_task_service() -> AudioGenerationTaskService:
    queue = _build_default_task_queue()
    return AudioGenerationTaskService(
        task_store=_build_default_task_store(),
        task_queue=queue,
        run_inline=queue is None,
    )


audio_generation_task_service = create_audio_generation_task_service()
atexit.register(audio_generation_task_service.close)
