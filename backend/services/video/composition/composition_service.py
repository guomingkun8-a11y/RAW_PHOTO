from __future__ import annotations

import atexit
from datetime import datetime
import os
import threading
import time
from typing import Any

from services.platform.config import DATA_DIR, config
from services.platform.enterprise_schema import resolve_enterprise_database_url
from services.video.composition.models import TimelineDocument
from services.video.composition.queue import RedisVideoCompositionQueue
from services.video.composition.renderer import VideoCompositionRenderer
from services.video.composition.settings import VideoCompositionSettings, load_video_composition_settings
from services.video.composition.storage import VideoCompositionStorage
from services.video.composition.task_store import VideoCompositionTaskStore
from services.canvas.canvas_task_limiter import CanvasTaskReservation, canvas_task_limiter


TERMINAL_STATUSES = {"success", "error", "canceled"}


def _clean(value: object, default: str = "", limit: int = 2000) -> str:
    text = str(value if value is not None else default).strip()
    return (text or default)[:limit]


def _owner_id(identity: dict[str, object]) -> str:
    return _clean(identity.get("id"), "anonymous", 191)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _default_database_url(settings: VideoCompositionSettings) -> str:
    if settings.database_url:
        return settings.database_url
    try:
        return resolve_enterprise_database_url()
    except RuntimeError:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{DATA_DIR / 'video_composition_tasks.db'}"


def _public_task(task: dict[str, Any]) -> dict[str, Any]:
    result = dict(task)
    result.pop("owner_id", None)
    result.pop("timeline", None)
    result.pop("canvas_reservation_token", None)
    return result


class VideoCompositionTaskService:
    def __init__(
        self,
        *,
        settings: VideoCompositionSettings | None = None,
        task_store: VideoCompositionTaskStore | None = None,
        task_queue: RedisVideoCompositionQueue | None = None,
        renderer: VideoCompositionRenderer | None = None,
        run_inline: bool | None = None,
    ):
        self.settings = settings or load_video_composition_settings()
        self.task_store = task_store or VideoCompositionTaskStore(_default_database_url(self.settings))
        self.storage = renderer.storage if renderer is not None else VideoCompositionStorage(self.settings)
        self.renderer = renderer or VideoCompositionRenderer(self.storage)
        self.task_queue = task_queue
        if self.task_queue is None and self.settings.queue_enabled:
            try:
                self.task_queue = RedisVideoCompositionQueue(
                    redis_url=self.settings.redis_url,
                    queue_name=self.settings.queue_name,
                    max_concurrency=self.settings.worker_concurrency,
                    adaptive_concurrency_enabled=False,
                )
            except Exception:
                self.task_queue = None
        self.run_inline = bool(run_inline) if run_inline is not None else self.task_queue is None
        self._submission_locks = tuple(threading.Lock() for _ in range(64))
        self._closed = False

    def submit_task(
        self,
        identity: dict[str, object],
        *,
        client_task_id: str,
        workflow_id: str,
        timeline: TimelineDocument,
        base_url: str,
        source: str = "standard",
        canvas_units: int = 1,
    ) -> dict[str, Any]:
        if not self.settings.enabled:
            raise ValueError("视频合成服务尚未启用")
        if not self.settings.ffmpeg_available:
            raise ValueError("FFmpeg 未安装或路径未配置")
        owner_id = _owner_id(identity)
        task_id = _clean(client_task_id, limit=191)
        if not task_id:
            raise ValueError("合成任务 ID 不能为空")
        lock = self._submission_locks[hash(owner_id) % len(self._submission_locks)]
        with lock:
            return self._submit_task_locked(
                owner_id=owner_id,
                task_id=task_id,
                workflow_id=workflow_id,
                timeline=timeline,
                base_url=base_url,
                source=source,
                canvas_units=canvas_units,
            )

    def _submit_task_locked(
        self,
        *,
        owner_id: str,
        task_id: str,
        workflow_id: str,
        timeline: TimelineDocument,
        base_url: str,
        source: str,
        canvas_units: int,
    ) -> dict[str, Any]:
        key = f"{owner_id}:{task_id}"
        existing = self.task_store.get(key)
        if existing is not None:
            return _public_task(existing)
        if self.task_store.count_pending(owner_id) >= self.settings.max_pending_per_owner:
            raise ValueError(f"当前最多同时等待 {self.settings.max_pending_per_owner} 个合成任务")

        normalized_source = "canvas" if _clean(source).lower() == "canvas" else "standard"
        normalized_canvas_units = max(1, int(canvas_units or 1))
        reservation: CanvasTaskReservation | None = None
        if normalized_source == "canvas":
            reservation = canvas_task_limiter.reserve(owner_id, units=normalized_canvas_units)

        created_at = _now()
        task: dict[str, Any] = {
            "id": task_id,
            "owner_id": owner_id,
            "workflow_id": _clean(workflow_id, limit=191),
            "status": "queued",
            "progress": "等待合成",
            "timeline": timeline.model_dump(mode="json", by_alias=True),
            "duration": timeline.duration(),
            "created_at": created_at,
            "updated_at": created_at,
            "base_url": _clean(base_url, config.base_url, 1000),
            "attempts": 0,
            "source": normalized_source,
            "canvas_units": normalized_canvas_units,
            "canvas_reservation_token": reservation.token if reservation else "",
        }
        try:
            saved, created = self.task_store.create(key, task)
            if not created:
                if reservation:
                    canvas_task_limiter.release(reservation)
                return _public_task(saved)
            if self.task_queue is not None:
                try:
                    self.task_queue.enqueue(key, priority="standard")
                except Exception as exc:
                    updated = self.task_store.update(key, {"status": "error", "progress": "合成失败", "error": str(exc)})
                    self._release_canvas_reservation(updated or task)
                    raise ValueError(f"合成队列不可用：{exc}") from exc
            elif self.run_inline:
                self._run_task(key)
        except Exception:
            if reservation:
                self._release_canvas_reservation(self.task_store.get(key) or task)
            raise
        return _public_task(self.task_store.get(key) or task)

    def get_task(self, identity: dict[str, object], task_id: str) -> dict[str, Any] | None:
        task = self.task_store.get(f"{_owner_id(identity)}:{_clean(task_id, limit=191)}")
        return _public_task(task) if task else None

    def list_tasks(self, identity: dict[str, object], *, workflow_id: str = "", limit: int = 50) -> dict[str, Any]:
        owner = _owner_id(identity)
        items = self.task_store.list(owner, workflow_id=_clean(workflow_id, limit=191), limit=limit)
        return {"items": [_public_task(item) for item in items], "total": len(items)}

    def delete_task(self, identity: dict[str, object], task_id: str) -> bool:
        owner_id = _owner_id(identity)
        key = f"{owner_id}:{_clean(task_id, limit=191)}"
        task = self.task_store.get(key)
        if not task:
            return False
        if task.get("status") in {"queued", "running"}:
            raise ValueError("合成任务仍在运行，完成后才能删除")
        storage_rel = _clean(task.get("storage_rel"), limit=1000)
        if storage_rel:
            self.storage.delete_result(storage_rel, owner_id=owner_id)
        return self.task_store.delete(key)

    def cancel_task(self, identity: dict[str, object], task_id: str) -> dict[str, Any] | None:
        key = f"{_owner_id(identity)}:{_clean(task_id, limit=191)}"
        task = self.task_store.get(key)
        if not task:
            return None
        if task.get("status") == "running":
            raise ValueError("合成已经开始，当前版本只能取消尚未执行的任务")
        if task.get("status") in TERMINAL_STATUSES:
            return _public_task(task)
        updated = self.task_store.update(
            key,
            {"status": "canceled", "progress": "已取消", "finished_at": _now()},
            expected_status="queued",
        )
        self._release_canvas_reservation(updated)
        return _public_task(updated) if updated else _public_task(self.task_store.get(key) or task)

    def retry_task(self, identity: dict[str, object], task_id: str) -> dict[str, Any] | None:
        owner_id = _owner_id(identity)
        key = f"{owner_id}:{_clean(task_id, limit=191)}"
        task = self.task_store.get(key)
        if not task:
            return None
        if task.get("status") not in {"error", "canceled"}:
            raise ValueError("只有失败或已取消的任务可以重试")
        if self.task_store.count_pending(owner_id) >= self.settings.max_pending_per_owner:
            raise ValueError(f"当前最多同时等待 {self.settings.max_pending_per_owner} 个合成任务")
        reservation: CanvasTaskReservation | None = None
        if _clean(task.get("source")).lower() == "canvas":
            reservation = canvas_task_limiter.reserve(owner_id, units=max(1, int(task.get("canvas_units") or 1)))
        try:
            updated = self.task_store.update(
                key,
                {
                    "status": "queued",
                    "progress": "等待重新合成",
                    "error": "",
                    "attempts": 0,
                    "finished_at": "",
                    "canvas_reservation_token": reservation.token if reservation else "",
                },
                expected_status=_clean(task.get("status")),
            )
        except Exception:
            if reservation:
                canvas_task_limiter.release(reservation)
            raise
        if updated is None:
            if reservation:
                canvas_task_limiter.release(reservation)
            return _public_task(self.task_store.get(key) or task)
        if self.task_queue is not None:
            self.task_queue.enqueue(key, priority="standard")
        elif self.run_inline:
            self._run_task(key)
        return _public_task(self.task_store.get(key) or updated)

    def _run_task(self, key: str) -> None:
        started = time.time()
        task = self.task_store.update(
            key,
            {"status": "running", "progress": "正在准备素材"},
            expected_status="queued",
        )
        if task is None:
            return
        try:
            timeline = TimelineDocument.model_validate(task.get("timeline") or {})
            self.task_store.update(key, {"progress": "正在合成视频"}, expected_status="running")
            result = self.renderer.render(
                timeline,
                owner_id=_clean(task.get("owner_id"), "anonymous"),
                task_id=_clean(task.get("id"), "composition"),
                base_url=_clean(task.get("base_url"), config.base_url),
            )
            self.task_store.update(
                key,
                {
                    "status": "success",
                    "progress": "合成完成",
                    "result_url": result.get("result_url", ""),
                    "resultUrl": result.get("resultUrl", ""),
                    "storage_rel": result.get("storage_rel", ""),
                    "duration": result.get("duration", 0),
                    "width": result.get("width", 0),
                    "height": result.get("height", 0),
                    "size": result.get("size", 0),
                    "duration_ms": int((time.time() - started) * 1000),
                    "finished_at": _now(),
                },
                expected_status="running",
            )
        except Exception as exc:
            current = self.task_store.get(key) or task
            attempts = int(current.get("attempts") or 0) + 1
            error = _clean(exc, "视频合成失败", 4000)
            if self.task_queue is not None and attempts <= self.settings.max_retries:
                updated = self.task_store.update(
                    key,
                    {
                        "status": "queued",
                        "progress": f"合成失败，准备重试 {attempts}/{self.settings.max_retries}",
                        "error": error,
                        "attempts": attempts,
                    },
                    expected_status="running",
                )
                if updated is not None:
                    self.task_queue.enqueue(key, priority="standard")
            else:
                self.task_store.update(
                    key,
                    {
                        "status": "error",
                        "progress": "合成失败",
                        "error": error,
                        "attempts": attempts,
                        "duration_ms": int((time.time() - started) * 1000),
                        "finished_at": _now(),
                    },
                    expected_status="running",
                )
        finally:
            self._release_canvas_reservation(self.task_store.get(key))

    @staticmethod
    def _release_canvas_reservation(task: dict[str, Any] | None) -> None:
        if not isinstance(task, dict) or task.get("status") not in TERMINAL_STATUSES:
            return
        token = _clean(task.get("canvas_reservation_token"))
        if token:
            canvas_task_limiter.release(
                owner_id=_clean(task.get("owner_id"), "anonymous", 191),
                token=token,
            )

    def process_queued_task(self, task_key: str) -> dict[str, Any] | None:
        key = _clean(task_key, limit=383)
        if not key:
            return None
        self._run_task(key)
        task = self.task_store.get(key)
        return _public_task(task) if task else None

    def work_once(self, timeout_secs: int = 5) -> dict[str, Any] | None:
        if self.task_queue is None:
            raise RuntimeError("video composition queue is not configured")
        task_key = self.task_queue.dequeue(timeout_secs)
        if not task_key:
            return None
        return self.process_queued_task(task_key)

    def work_forever(self, stop_event: threading.Event | None = None, timeout_secs: int = 5) -> None:
        if self.task_queue is None:
            raise RuntimeError("video composition queue is not configured")
        shutdown = stop_event or threading.Event()
        worker_id = f"{os.getpid()}:video-composition"
        self.requeue_unfinished()

        def loop(index: int) -> None:
            worker_name = f"{worker_id}:{index}"
            while not shutdown.is_set():
                try:
                    self.task_queue.touch_worker(worker_name, timeout_secs=60)
                    self.work_once(timeout_secs)
                except Exception:
                    shutdown.wait(1.0)
            self.task_queue.forget_worker(worker_name)

        threads = [
            threading.Thread(target=loop, args=(index,), name=f"video-composition-worker-{index + 1}", daemon=True)
            for index in range(self.settings.worker_concurrency)
        ]
        for thread in threads:
            thread.start()
        while not shutdown.is_set():
            shutdown.wait(0.5)
        for thread in threads:
            thread.join(timeout=max(1, timeout_secs))

    def monitoring_snapshot(self) -> dict[str, Any]:
        queue = self.task_queue
        try:
            depth = int(queue.queue_depth()) if queue else 0
        except Exception:
            depth = 0
        try:
            workers = int(queue.active_worker_count()) if queue else 0
        except Exception:
            workers = 0
        counts = self.task_store.status_counts()
        samples = sorted(self.task_store.duration_samples())
        p95 = samples[max(0, min(len(samples) - 1, int(len(samples) * 0.95 + 0.999) - 1))] if samples else 0
        return {
            "enabled": self.settings.enabled,
            "queue_enabled": bool(queue),
            "queue_name": self.settings.queue_name,
            "queue_depth": depth,
            "active_workers": workers,
            "worker_concurrency": self.settings.worker_concurrency,
            "ffmpeg_available": self.settings.ffmpeg_available,
            "tasks": self.task_store.status_counts(),
            "queue_depth": depth,
            "queued_tasks": int(counts.get("queued", 0)),
            "running_tasks": int(counts.get("running", 0)),
            "failed_tasks": int(counts.get("error", 0)),
            "p95_ms": int(p95),
            "total_concurrency": self.settings.worker_concurrency,
            "max_retries": self.settings.max_retries,
            "stale_running_timeout_secs": self.settings.stale_running_timeout_secs,
        }

    def requeue_unfinished(self) -> int:
        if self.task_queue is None:
            return 0
        now = datetime.now()
        count = 0
        for task in self.task_store.list_by_status(["queued", "running"]):
            key = f"{_clean(task.get('owner_id'), 'anonymous', 191)}:{_clean(task.get('id'), limit=191)}"
            status = _clean(task.get("status"))
            if status == "running":
                try:
                    updated_at = datetime.fromisoformat(_clean(task.get("updated_at")).replace("Z", "+00:00")).replace(tzinfo=None)
                except ValueError:
                    updated_at = datetime.min
                if (now - updated_at).total_seconds() < self.settings.stale_running_timeout_secs:
                    continue
                recovered = self.task_store.update(
                    key,
                    {"status": "queued", "progress": "检测到中断，已重新排队", "error": ""},
                    expected_status="running",
                )
                if recovered is None:
                    continue
            self.task_queue.enqueue(key, priority="standard")
            count += 1
        return count

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        close = getattr(self.task_store, "close", None)
        if close:
            close()


video_composition_service = VideoCompositionTaskService()
atexit.register(video_composition_service.close)
