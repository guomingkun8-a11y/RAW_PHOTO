from __future__ import annotations

import atexit
import os
import socket
import threading
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

from services.image.image_task_queue import ImageTaskQueue
from services.platform.config import DATA_DIR, config
from services.platform.enterprise_schema import resolve_enterprise_database_url
from services.platform.log_service import LOG_TYPE_CALL, log_service
from services.video.video_generation_provider import run_video_generation
from services.video.video_generation_queue import RedisVideoGenerationQueue
from services.video.video_generation_task_store import DatabaseVideoGenerationTaskStore, VideoGenerationTaskStore


TASK_STATUS_QUEUED = "queued"
TASK_STATUS_RUNNING = "running"
TASK_STATUS_SUCCESS = "success"
TASK_STATUS_ERROR = "error"
TASK_STATUS_CANCELED = "canceled"
TERMINAL_STATUSES = {TASK_STATUS_SUCCESS, TASK_STATUS_ERROR, TASK_STATUS_CANCELED}
UNFINISHED_STATUSES = {TASK_STATUS_QUEUED, TASK_STATUS_RUNNING}
DEFAULT_EMPTY_TASK_LIST_LIMIT = 200


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
    item: dict[str, Any] = {
        "id": task.get("id"),
        "status": task.get("status"),
        "mode": task.get("mode"),
        "model": task.get("model"),
        "prompt": task.get("prompt"),
        "aspect_ratio": task.get("aspect_ratio"),
        "duration_secs": task.get("duration_secs"),
        "quality": task.get("quality"),
        "resolution": task.get("resolution"),
        "created_at": task.get("created_at"),
        "updated_at": task.get("updated_at"),
    }
    for key in (
        "conversation_id",
        "turn_id",
        "progress",
        "error",
        "upstream_task_id",
        "video_url",
        "cover_url",
        "duration_ms",
        "cost",
        "attempts",
        "max_retries",
    ):
        if task.get(key) is not None and task.get(key) != "":
            item[key] = task.get(key)
    if isinstance(task.get("image_urls"), list):
        item["image_urls"] = task.get("image_urls")
    if isinstance(task.get("data"), list):
        item["data"] = task.get("data")
    if task.get("status") in UNFINISHED_STATUSES:
        base_ts = task.get("started_ts") if task.get("status") == TASK_STATUS_RUNNING else task.get("created_ts")
        if base_ts:
            try:
                item["elapsed_secs"] = round(time.time() - float(base_ts), 1)
            except (TypeError, ValueError):
                pass
    return item


class VideoGenerationTaskService:
    def __init__(
        self,
        *,
        task_store: VideoGenerationTaskStore,
        task_queue: ImageTaskQueue | None = None,
        run_inline: bool | None = None,
        generation_handler: Callable[[dict[str, Any]], dict[str, Any]] = run_video_generation,
        enabled_getter: Callable[[], bool] | None = None,
        max_retries_getter: Callable[[], int] | None = None,
        owner_pending_limit_getter: Callable[[], int] | None = None,
        owner_concurrency_getter: Callable[[], int] | None = None,
        stale_running_timeout_getter: Callable[[], int] | None = None,
    ) -> None:
        self.task_store = task_store
        self.task_queue = task_queue
        self.run_inline = (task_queue is None) if run_inline is None else bool(run_inline)
        self.generation_handler = generation_handler
        self.enabled_getter = enabled_getter or (lambda: bool(config.get_video_generation_settings().get("enabled")))
        self.max_retries_getter = max_retries_getter or (lambda: int(config.get_video_generation_settings().get("max_retries") or 0))
        self.owner_pending_limit_getter = owner_pending_limit_getter or (
            lambda: int(config.get_video_generation_settings().get("owner_pending_limit") or 8)
        )
        self.owner_concurrency_getter = owner_concurrency_getter or (
            lambda: int(config.get_video_generation_settings().get("owner_concurrency") or 1)
        )
        self.stale_running_timeout_getter = stale_running_timeout_getter or (
            lambda: int(config.get_video_generation_settings().get("stale_running_timeout_secs") or 3600)
        )
        settings = config.get_video_generation_settings()
        self._worker_concurrency = max(1, int(settings.get("worker_concurrency") or 1))
        self._lock = threading.RLock()

    def submit_task(
        self,
        identity: dict[str, object],
        *,
        client_task_id: str,
        prompt: str,
        model: str,
        mode: str = "text_to_video",
        aspect_ratio: str = "16:9",
        duration_secs: int = 5,
        quality: str = "standard",
        resolution: str = "",
        image_urls: list[str] | None = None,
        params: dict[str, Any] | None = None,
        conversation_id: str = "",
        turn_id: str = "",
    ) -> dict[str, Any]:
        if not self.enabled_getter():
            raise ValueError("video generation is not enabled")
        task_id = _clean(client_task_id, limit=191)
        safe_prompt = _clean(prompt, limit=12000)
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
            active_count = self.task_store.count_tasks(owner, {TASK_STATUS_QUEUED, TASK_STATUS_RUNNING})
            if active_count >= max(1, int(self.owner_pending_limit_getter())):
                raise ValueError("user video generation queue is full; wait for existing tasks to finish")
            now = _now_iso()
            normalized_image_urls = [
                _clean(url, limit=2000)
                for url in list(image_urls or [])[:8]
                if _clean(url, limit=2000).startswith(("http://", "https://"))
            ]
            normalized_mode = _clean(mode, "image_to_video" if normalized_image_urls else "text_to_video", 40)
            if normalized_mode not in {"text_to_video", "image_to_video"}:
                normalized_mode = "image_to_video" if normalized_image_urls else "text_to_video"
            provider_params = {
                "mode": normalized_mode,
                "aspectRatio": _clean(aspect_ratio, "16:9", 40),
                "duration": _positive_int(duration_secs, 5, 1),
                "quality": _clean(quality, "standard", 80),
                **({"resolution": _clean(resolution, limit=80)} if _clean(resolution) else {}),
                **(dict(params or {}) if isinstance(params, dict) else {}),
            }
            task = {
                "id": task_id,
                "owner_id": owner,
                "status": TASK_STATUS_QUEUED,
                "mode": normalized_mode,
                "model": safe_model,
                "prompt": safe_prompt,
                "aspect_ratio": provider_params["aspectRatio"],
                "duration_secs": provider_params["duration"],
                "quality": provider_params["quality"],
                "resolution": provider_params.get("resolution", ""),
                "image_urls": normalized_image_urls,
                "params": provider_params,
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
            task, created = self.task_store.create_task(key, task)
            if not created:
                return _public_task(task)
        if self.task_queue is not None and not self.run_inline:
            self.task_queue.enqueue(key)
        else:
            threading.Thread(target=self._run_task, args=(key,), name=f"video-generation-{task_id[:16]}", daemon=True).start()
        return _public_task(task)

    def list_tasks(self, identity: dict[str, object], task_ids: list[str], *, limit: int | None = None) -> dict[str, Any]:
        owner = _owner_id(identity)
        requested_ids = [_clean(task_id, limit=191) for task_id in task_ids if _clean(task_id, limit=191)]
        page_limit = min(500, _positive_int(limit, DEFAULT_EMPTY_TASK_LIST_LIMIT, 1))
        raw_items = self.task_store.list_tasks(owner, requested_ids or None, limit=None if requested_ids else page_limit + 1)
        indexed = {_clean(task.get("id"), limit=191): task for task in raw_items}
        if requested_ids:
            return {
                "items": [_public_task(indexed[task_id]) for task_id in requested_ids if task_id in indexed],
                "missing_ids": [task_id for task_id in requested_ids if task_id not in indexed],
            }
        has_more = len(raw_items) > page_limit
        items = sorted((_public_task(task) for task in raw_items[:page_limit]), key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        return {"items": items, "missing_ids": [], "has_more": has_more, "limit": page_limit}

    def cancel_task(self, identity: dict[str, object], task_id: str) -> dict[str, Any]:
        owner = _owner_id(identity)
        normalized_task_id = _clean(task_id, limit=191)
        if not normalized_task_id:
            raise ValueError("task_id is required")
        key = _task_key(owner, normalized_task_id)
        with self._lock:
            task = self.task_store.get_task(key)
            if task is None:
                raise ValueError("task not found")
            if task.get("status") in TERMINAL_STATUSES:
                return _public_task(task)
            updated = self.task_store.update_task(
                key,
                {
                    "status": TASK_STATUS_CANCELED,
                    "error": "task canceled",
                    "progress": "canceled",
                    "updated_at": _now_iso(),
                    "updated_ts": time.time(),
                },
            )
        if self.task_queue is not None:
            try:
                self.task_queue.notify_task_update(key)
            except Exception:
                pass
        return _public_task(updated or task)

    def process_queued_task(self, task_key: str) -> dict[str, Any] | None:
        key = _clean(task_key, limit=383)
        if not key:
            return None
        self._run_task(key)
        task = self.task_store.get_task(key)
        return _public_task(task) if task else None

    def work_once(self, timeout_secs: int = 5) -> dict[str, Any] | None:
        if self.task_queue is None:
            raise RuntimeError("video generation task queue is not configured")
        task_key = self.task_queue.dequeue(timeout_secs)
        if not task_key:
            return None
        return self.process_queued_task(task_key)

    def work_forever(self, stop_event: threading.Event | None = None, timeout_secs: int = 5) -> None:
        if self.task_queue is None:
            raise RuntimeError("video generation task queue is not configured")
        shutdown_event = stop_event or threading.Event()
        worker_id = f"{socket.gethostname()}:{os.getpid()}:video-generation"

        def _worker_loop(index: int) -> None:
            while not shutdown_event.is_set():
                try:
                    if self.task_queue is not None:
                        try:
                            self.task_queue.touch_worker(f"{worker_id}:{index}", timeout_secs=60)
                        except TypeError:
                            self.task_queue.touch_worker(f"{worker_id}:{index}")
                    self.work_once(timeout_secs)
                except Exception as exc:
                    try:
                        log_service.add(LOG_TYPE_CALL, "video generation worker loop failed", {"error": str(exc)})
                    except Exception:
                        pass
                    shutdown_event.wait(1.0)

        threads = [
            threading.Thread(target=_worker_loop, args=(index,), name=f"video-generation-worker-{index + 1}", daemon=True)
            for index in range(self._worker_concurrency)
        ]
        for thread in threads:
            thread.start()
        try:
            while not shutdown_event.is_set():
                shutdown_event.wait(0.5)
        finally:
            shutdown_event.set()
            for thread in threads:
                thread.join(timeout=max(1, int(timeout_secs)))

    def monitoring_snapshot(self) -> dict[str, Any]:
        try:
            self.recover_stale_unfinished()
        except Exception:
            pass
        unfinished = self.task_store.list_unfinished()
        queued_tasks = sum(1 for _key, task in unfinished if task.get("status") == TASK_STATUS_QUEUED)
        running_tasks = sum(1 for _key, task in unfinished if task.get("status") == TASK_STATUS_RUNNING)
        queue = self.task_queue
        try:
            queue_depth = int(getattr(queue, "queue_depth", lambda: 0)() or 0) if queue is not None else 0
        except Exception:
            queue_depth = 0
        try:
            active_workers = int(getattr(queue, "active_worker_count", lambda: 0)() or 0) if queue is not None else 0
        except Exception:
            active_workers = 0
        return {
            "enabled": bool(self.enabled_getter()),
            "queue_enabled": queue is not None,
            "queue_depth": queue_depth,
            "queued_tasks": queued_tasks,
            "running_tasks": running_tasks,
            "active_workers": active_workers,
            "worker_concurrency": self._worker_concurrency,
            "owner_concurrency": max(1, int(self.owner_concurrency_getter())),
            "owner_pending_limit": max(1, int(self.owner_pending_limit_getter())),
        }

    def recover_stale_unfinished(self) -> int:
        stale_cutoff = time.time() - max(60, int(self.stale_running_timeout_getter()))
        recovered = 0
        for key, task in self.task_store.list_unfinished():
            if task.get("status") != TASK_STATUS_RUNNING:
                continue
            try:
                activity = float(task.get("updated_ts") or task.get("started_ts") or task.get("created_ts") or 0)
            except (TypeError, ValueError):
                activity = 0.0
            if activity > stale_cutoff:
                continue
            updated = self.task_store.update_task(
                key,
                {
                    "status": TASK_STATUS_QUEUED if self.task_queue is not None else TASK_STATUS_ERROR,
                    "progress": "stale_requeued" if self.task_queue is not None else "stale_timeout",
                    "error": "" if self.task_queue is not None else "video generation task timed out",
                    "updated_at": _now_iso(),
                    "updated_ts": time.time(),
                },
                expected_status=TASK_STATUS_RUNNING,
            )
            if updated is None:
                continue
            recovered += 1
            if self.task_queue is not None:
                self.task_queue.enqueue(key)
        return recovered

    def close(self) -> None:
        close = getattr(self.task_store, "close", None)
        if callable(close):
            close()

    def _run_task(self, key: str) -> None:
        started = time.time()
        owner_id = ""
        with self._lock:
            task = self.task_store.get_task(key)
            if task is None or task.get("status") != TASK_STATUS_QUEUED:
                return
            owner_id = _clean(task.get("owner_id"), "anonymous", 191)
            claimed = self.task_store.claim_task(
                key,
                owner_concurrency=max(1, int(self.owner_concurrency_getter())),
                updates={
                    "status": TASK_STATUS_RUNNING,
                    "progress": "starting",
                    "error": "",
                    "started_at": _now_iso(),
                    "started_ts": time.time(),
                    "updated_at": _now_iso(),
                    "updated_ts": time.time(),
                },
            )
            if claimed is None:
                if self.task_queue is not None and task.get("status") == TASK_STATUS_QUEUED:
                    time.sleep(0.1)
                    self.task_queue.enqueue(key)
                return
            task = claimed
        identity = task.get("identity") if isinstance(task.get("identity"), dict) else {"id": owner_id}

        def progress_callback(step: str) -> None:
            self._update_unless_canceled(key, progress=_clean(step, "running", 120))

        handler_payload = {
            "prompt": task.get("prompt"),
            "model": task.get("model"),
            "mode": task.get("mode"),
            "image_urls": task.get("image_urls") if isinstance(task.get("image_urls"), list) else [],
            "params": task.get("params") if isinstance(task.get("params"), dict) else {},
            "progress_callback": progress_callback,
        }
        slot_token = f"{os.getpid()}:{threading.get_ident()}:{key}"
        acquire_slot = getattr(self.task_queue, "acquire_slot", None)
        release_slot = getattr(self.task_queue, "release_slot", None)
        distributed_slot = False
        try:
            if callable(acquire_slot):
                distributed_slot = bool(acquire_slot(slot_token, timeout_secs=5))
                if not distributed_slot:
                    updated = self.task_store.update_task(
                        key,
                        {
                            "status": TASK_STATUS_QUEUED,
                            "progress": "waiting_for_slot",
                            "error": "",
                            "updated_at": _now_iso(),
                            "updated_ts": time.time(),
                        },
                        expected_status=TASK_STATUS_RUNNING,
                    )
                    if updated is not None and self.task_queue is not None:
                        self.task_queue.enqueue(key)
                    return
            self._update_unless_canceled(key, progress="submitting")
            result = self.generation_handler(handler_payload)
            if not isinstance(result, dict):
                raise RuntimeError("video generation handler did not return an object")
            video_url = _clean(result.get("video_url"), limit=2000)
            if not video_url:
                raise RuntimeError("video generation result missing video_url")
            data = [{
                "type": "video",
                "url": video_url,
                "cover_url": _clean(result.get("cover_url"), limit=2000),
            }]
            duration_ms = int((time.time() - started) * 1000)
            if not self._update_unless_canceled(
                key,
                status=TASK_STATUS_SUCCESS,
                progress="completed",
                video_url=video_url,
                cover_url=_clean(result.get("cover_url"), limit=2000),
                data=data,
                cost=result.get("cost"),
                upstream_task_id=_clean(result.get("upstream_task_id"), limit=191),
                raw_result=result.get("raw") if isinstance(result.get("raw"), dict) else {},
                duration_ms=duration_ms,
                error="",
            ):
                return
            self._log_call(identity, task, started, "completed")
        except Exception as exc:
            error_message = str(exc) or "video generation failed"
            duration_ms = int((time.time() - started) * 1000)
            if self._retry_task(key, error_message, duration_ms):
                self._log_call(identity, task, started, "failed, retry queued", status="failed", error=error_message)
                return
            if not self._update_unless_canceled(
                key,
                status=TASK_STATUS_ERROR,
                progress="failed",
                error=error_message,
                duration_ms=duration_ms,
            ):
                return
            self._log_call(identity, task, started, "failed", status="failed", error=error_message)
        finally:
            if distributed_slot and callable(release_slot):
                release_slot(slot_token)

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

    def _retry_task(self, key: str, error_message: str, duration_ms: int) -> bool:
        if self.task_queue is None or self.run_inline:
            return False
        with self._lock:
            task = self.task_store.get_task(key)
            if task is None or task.get("status") == TASK_STATUS_CANCELED:
                return False
            attempts = int(task.get("attempts") or 0) + 1
            max_retries = max(0, int(task.get("max_retries") or self.max_retries_getter()))
            if attempts > max_retries:
                return False
            updated = self.task_store.update_task(
                key,
                {
                    "status": TASK_STATUS_QUEUED,
                    "attempts": attempts,
                    "progress": f"retrying:{attempts}/{max_retries}",
                    "error": error_message,
                    "duration_ms": duration_ms,
                    "updated_at": _now_iso(),
                    "updated_ts": time.time(),
                },
                reject_status=TASK_STATUS_CANCELED,
            )
            should_enqueue = updated is not None
        if should_enqueue and self.task_queue is not None:
            self.task_queue.enqueue(key)
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
            "endpoint": "/api/video-generation/tasks",
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
            log_service.add(LOG_TYPE_CALL, f"video generation {suffix}", detail)
        except Exception:
            pass


def _build_default_task_store() -> VideoGenerationTaskStore:
    settings = config.get_video_generation_settings()
    try:
        default_database_url = resolve_enterprise_database_url()
    except Exception:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        default_database_url = f"sqlite:///{DATA_DIR / 'video_generation_tasks.db'}"
    database_url = _clean(settings.get("database_url")) or default_database_url
    return DatabaseVideoGenerationTaskStore(database_url)


def _build_default_task_queue() -> ImageTaskQueue | None:
    settings = config.get_video_generation_settings()
    if not bool(settings.get("enabled") and settings.get("queue_enabled")):
        return None
    total_concurrency = max(1, int(settings.get("total_concurrency") or 1))
    return RedisVideoGenerationQueue(
        redis_url=_clean(settings.get("redis_url"), "redis://127.0.0.1:6379/0"),
        queue_name=_clean(settings.get("queue_name"), "ai_video_generation_tasks"),
        max_concurrency=total_concurrency,
        slot_lease_secs=max(60, int(settings.get("slot_lease_secs") or 3600)),
        adaptive_concurrency_enabled=False,
    )


def create_video_generation_task_service() -> VideoGenerationTaskService:
    queue = _build_default_task_queue()
    return VideoGenerationTaskService(
        task_store=_build_default_task_store(),
        task_queue=queue,
        run_inline=queue is None,
    )


video_generation_task_service = create_video_generation_task_service()
atexit.register(video_generation_task_service.close)
