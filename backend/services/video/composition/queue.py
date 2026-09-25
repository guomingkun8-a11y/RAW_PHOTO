from __future__ import annotations

from dataclasses import dataclass

from services.image.image_task_queue import RedisImageTaskQueue


@dataclass
class RedisVideoCompositionQueue(RedisImageTaskQueue):
    queue_name: str = "ai_video_composition_tasks"
