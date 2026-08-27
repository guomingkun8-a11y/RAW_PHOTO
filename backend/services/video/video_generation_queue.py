from __future__ import annotations

from dataclasses import dataclass

from services.image.image_task_queue import RedisImageTaskQueue


@dataclass
class RedisVideoGenerationQueue(RedisImageTaskQueue):
    queue_name: str = "ai_video_generation_tasks"

