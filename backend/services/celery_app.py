from __future__ import annotations

import os
import sys

from celery import Celery

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from services.config import config


settings = config.get_image_task_queue_settings()
redis_url = str(settings.get("redis_url") or "redis://127.0.0.1:6379/0")
queue_name = str(settings.get("queue_name") or "ai_image_tasks")
worker_concurrency = max(1, int(settings.get("worker_concurrency") or 1))

celery_app = Celery("raw_photo_image_tasks", broker=redis_url)
celery_app.conf.update(
    task_default_queue=queue_name,
    task_default_exchange=queue_name,
    task_default_routing_key=queue_name,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
    broker_connection_retry_on_startup=True,
    broker_transport_options={"visibility_timeout": max(3600, int(settings.get("slot_lease_secs") or 7200))},
    task_routes={"image_tasks.process": {"queue": queue_name}},
    worker_concurrency=worker_concurrency,
)


@celery_app.task(
    name="image_tasks.process",
    bind=True,
    acks_late=True,
    reject_on_worker_lost=True,
)
def process_image_task(_task, task_key: str) -> dict[str, object]:
    from services.image_task_service import image_task_service

    result = image_task_service.process_queued_task(task_key)
    return result if isinstance(result, dict) else {}
