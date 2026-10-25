"""Shared queue metrics for dashboards and scheduled alert checks."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from services.audio.audio_generation_monitoring_service import audio_generation_monitoring_service
from services.audio.audio_generation_task_service import audio_generation_task_service
from services.canvas.canvas_ai_scheduler import canvas_ai_scheduler
from services.ecommerce.agent_queue_service import agent_queue_service
from services.ecommerce.video_analysis_queue_service import video_analysis_queue_service
from services.image.generation_monitoring_service import generation_monitoring_service
from services.image.image_task_service import image_task_service
from services.platform.config import config
from services.video.composition.composition_service import video_composition_service
from services.video.video_generation_monitoring_service import video_generation_monitoring_service
from services.video.video_generation_service import video_generation_task_service


def _number(value: object) -> int:
    try:
        return max(0, int(float(value or 0)))
    except (TypeError, ValueError):
        return 0


def _largest(data: Mapping[str, object], *keys: str, fallback: int = 0) -> int:
    return max([_number(data.get(key)) for key in keys] + [_number(fallback)])


def _safe_snapshot(loader: Callable[[], Mapping[str, object]]) -> dict[str, object]:
    try:
        return dict(loader() or {})
    except Exception as exc:
        return {"available": False, "enabled": True, "error": str(exc)[:300]}


def _queue_item(
    *,
    queue: str,
    label: str,
    snapshot: Mapping[str, object] | None = None,
    failed: int = 0,
    p95_ms: int | float = 0,
    concurrency: int = 0,
    worker_required: bool = True,
) -> dict[str, object]:
    data = dict(snapshot or {})
    error = str(data.get("error") or data.get("queue_error") or "").strip()[:300]
    enabled = bool(data.get("enabled", True))
    return {
        "queue": queue,
        "label": label,
        "enabled": enabled,
        "available": bool(data.get("available", True)) and not error,
        "waiting": _largest(data, "queue_depth", "lag", "pending", "waiting", "queued_tasks"),
        "running": _largest(data, "running_tasks", "processing_tasks", "active", "running"),
        "failed": _largest(data, "failed_tasks", "failed", fallback=failed),
        "dead_letters": _largest(data, "deadLetters", "dead_letters"),
        "stale_tasks": _largest(data, "stale_running_tasks", "stale_tasks"),
        "p95_ms": _largest(data, "p95_ms", fallback=int(float(p95_ms or 0))),
        "concurrency": _largest(
            data,
            "total_concurrency",
            "totalConcurrency",
            "slot_limit",
            "concurrency",
            fallback=concurrency,
        ),
        "active_workers": _largest(data, "active_workers", "activeWorkers"),
        "worker_required": bool(worker_required),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        **({"error": error} if error else {}),
    }


def queue_overview() -> dict[str, object]:
    """Return a bounded operational snapshot for every asynchronous queue."""

    items: list[dict[str, object]] = []

    image_snapshot = _safe_snapshot(image_task_service.monitoring_snapshot)
    try:
        image_summary = generation_monitoring_service.summary(image_snapshot)
    except Exception:
        image_summary = {}
    image_settings = config.get_image_task_queue_settings()
    items.append(_queue_item(
        queue=str(image_settings.get("queue_name") or "ai_image_tasks"),
        label="图片生成",
        snapshot=image_snapshot,
        failed=_number(image_summary.get("total_failed")),
        p95_ms=_number((image_summary.get("task_latency") or {}).get("p95_ms")),
        concurrency=_number(image_snapshot.get("total_concurrency")),
        worker_required=str(image_snapshot.get("executor") or "inline") != "inline",
    ))

    video_snapshot = _safe_snapshot(video_generation_task_service.monitoring_snapshot)
    try:
        video_summary = video_generation_monitoring_service.summary(video_snapshot)
    except Exception:
        video_summary = {}
    video_settings = config.get_video_generation_settings()
    items.append(_queue_item(
        queue=str(video_settings.get("queue_name") or "ai_video_generation_tasks"),
        label="视频生成",
        snapshot=video_snapshot,
        failed=_number(video_summary.get("total_failed")),
        p95_ms=_number((video_summary.get("task_latency") or {}).get("p95_ms")),
        concurrency=_number(video_snapshot.get("total_concurrency")),
    ))

    audio_snapshot = _safe_snapshot(audio_generation_task_service.monitoring_snapshot)
    try:
        audio_summary = audio_generation_monitoring_service.summary(audio_snapshot)
    except Exception:
        audio_summary = {}
    audio_settings = config.get_audio_generation_settings()
    items.append(_queue_item(
        queue=str(audio_settings.get("queue_name") or "ai_audio_generation_tasks"),
        label="音频生成",
        snapshot=audio_snapshot,
        failed=_number(audio_summary.get("total_failed")),
        p95_ms=_number((audio_summary.get("task_latency") or {}).get("p95_ms")),
        concurrency=_number(audio_snapshot.get("total_concurrency")),
    ))

    composition_snapshot = _safe_snapshot(video_composition_service.monitoring_snapshot)
    composition_settings = getattr(video_composition_service, "settings", None)
    items.append(_queue_item(
        queue=str(getattr(composition_settings, "queue_name", "ai_video_composition_tasks")),
        label="视频合成",
        snapshot=composition_snapshot,
        concurrency=_number(composition_snapshot.get("total_concurrency")),
    ))

    analysis_snapshot = _safe_snapshot(video_analysis_queue_service.snapshot)
    analysis_settings = video_analysis_queue_service.settings
    items.append(_queue_item(
        queue=str(analysis_snapshot.get("queue") or analysis_settings.queue_name),
        label="视频解析",
        snapshot=analysis_snapshot,
        concurrency=_number(analysis_snapshot.get("totalConcurrency") or analysis_settings.total_concurrency),
    ))

    agent_snapshot = _safe_snapshot(agent_queue_service.snapshot)
    items.append(_queue_item(
        queue=str(agent_snapshot.get("queue") or agent_queue_service.settings.queue_name),
        label="专业智能体",
        snapshot=agent_snapshot,
        concurrency=_number(agent_snapshot.get("totalConcurrency") or agent_queue_service.settings.total_concurrency),
    ))

    canvas_snapshot = _safe_snapshot(canvas_ai_scheduler.snapshot)
    items.append(_queue_item(
        queue=str(canvas_snapshot.get("queue") or canvas_ai_scheduler.namespace),
        label="画布智能助手",
        snapshot=canvas_snapshot,
        concurrency=_number(canvas_snapshot.get("concurrency") or canvas_ai_scheduler.total_limit),
        worker_required=False,
    ))

    return {
        "items": items,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
