from __future__ import annotations

from dataclasses import dataclass
import socket
import time
import uuid


class ImageTaskQueueError(RuntimeError):
    pass


class ImageTaskQueue:
    def enqueue(self, task_key: str) -> None:
        raise NotImplementedError

    def dequeue(self, timeout_secs: int = 5) -> str | None:
        raise NotImplementedError

    def queue_depth(self) -> int:
        raise NotImplementedError

    def active_slot_count(self) -> int:
        raise NotImplementedError

    def touch_worker(self, worker_id: str, timeout_secs: int = 60) -> None:
        raise NotImplementedError

    def active_worker_count(self) -> int:
        raise NotImplementedError

    def forget_worker(self, worker_id: str) -> None:
        raise NotImplementedError


@dataclass
class RedisImageTaskQueue(ImageTaskQueue):
    redis_url: str
    queue_name: str = "ai_image_tasks"
    max_concurrency: int = 0
    slot_lease_secs: int = 7200

    def __post_init__(self) -> None:
        try:
            import redis
        except Exception as exc:
            raise ImageTaskQueueError("redis package is required for Redis image task queue") from exc
        # RESP2 keeps compatibility with older local Redis services while
        # remaining fully supported by Redis 7 in the enterprise stack.
        self._client = redis.Redis.from_url(self.redis_url, decode_responses=True, protocol=2)
        self._slot_key = f"{self.queue_name}:slots"
        self._worker_key = f"{self.queue_name}:workers"
        self._acquire_slot_script = self._client.register_script(
            """
            redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[1])
            if redis.call('ZCARD', KEYS[1]) < tonumber(ARGV[2]) then
                redis.call('ZADD', KEYS[1], ARGV[3], ARGV[4])
                redis.call('EXPIRE', KEYS[1], ARGV[5])
                return 1
            end
            return 0
            """
        )

    def enqueue(self, task_key: str) -> None:
        value = str(task_key or "").strip()
        if not value:
            return
        self._client.rpush(self.queue_name, value)

    def dequeue(self, timeout_secs: int = 5) -> str | None:
        timeout = max(1, int(timeout_secs or 5))
        try:
            item = self._client.blpop(self.queue_name, timeout=timeout)
        except (TimeoutError, socket.timeout):
            return None
        except Exception as exc:
            if "timeout reading from socket" in str(exc).lower():
                return None
            raise
        if not item:
            return None
        _, task_key = item
        return str(task_key or "").strip() or None

    def queue_depth(self) -> int:
        return int(self._client.llen(self.queue_name) or 0)

    def active_slot_count(self) -> int:
        now = int(time.time())
        self._client.zremrangebyscore(self._slot_key, "-inf", now)
        return int(self._client.zcard(self._slot_key) or 0)

    def acquire_slot(self, token: str, timeout_secs: float = 30.0) -> bool:
        limit = max(0, int(self.max_concurrency or 0))
        if limit <= 0:
            return True
        token = str(token or uuid.uuid4().hex)
        deadline = time.monotonic() + max(0.1, float(timeout_secs))
        lease_secs = max(60, int(self.slot_lease_secs or 7200))
        while time.monotonic() < deadline:
            now = int(time.time())
            result = self._acquire_slot_script(
                keys=[self._slot_key],
                args=[now, limit, now + lease_secs, token, lease_secs],
            )
            if int(result or 0) == 1:
                return True
            time.sleep(0.25)
        return False

    def release_slot(self, token: str) -> None:
        if token:
            self._client.zrem(self._slot_key, token)

    def touch_worker(self, worker_id: str, timeout_secs: int = 60) -> None:
        value = str(worker_id or "").strip()
        if not value:
            return
        ttl = max(30, int(timeout_secs or 60))
        expires_at = int(time.time()) + ttl
        self._client.zadd(self._worker_key, {value: expires_at})
        self._client.expire(self._worker_key, ttl * 2)

    def active_worker_count(self) -> int:
        now = int(time.time())
        self._client.zremrangebyscore(self._worker_key, "-inf", now)
        return int(self._client.zcard(self._worker_key) or 0)

    def forget_worker(self, worker_id: str) -> None:
        value = str(worker_id or "").strip()
        if value:
            self._client.zrem(self._worker_key, value)


@dataclass
class CeleryImageTaskQueue(RedisImageTaskQueue):
    def enqueue(self, task_key: str) -> None:
        value = str(task_key or "").strip()
        if not value:
            return
        try:
            from services.celery_app import celery_app

            celery_app.send_task(
                "image_tasks.process",
                args=[value],
                queue=self.queue_name,
            )
        except Exception as exc:
            raise ImageTaskQueueError(f"failed to enqueue Celery image task: {exc}") from exc

    def dequeue(self, timeout_secs: int = 5) -> str | None:
        raise ImageTaskQueueError("Celery queue is consumed by the Celery worker")
