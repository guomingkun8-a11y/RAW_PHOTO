from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass
from threading import Lock
from uuid import uuid4


class CanvasAISchedulerError(RuntimeError):
    status_code = 429


class CanvasAISchedulerUnavailable(CanvasAISchedulerError):
    status_code = 503


@dataclass(frozen=True)
class CanvasAISlot:
    owner_id: str
    token: str
    acquired_at: float


def _clean(value: object, default: str = "") -> str:
    text = str(value if value is not None else default).strip()
    return text or default


def _digest(value: object) -> str:
    return hashlib.sha256(_clean(value, "anonymous").encode("utf-8")).hexdigest()[:32]


class CanvasAIScheduler:
    """A small Redis semaphore for canvas assistant and storyboard calls."""

    _admit_source = """
        local now = tonumber(ARGV[1])
        redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now)
        redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', now)
        if redis.call('ZCARD', KEYS[2]) >= tonumber(ARGV[2]) then return 0 end
        if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[3]) then return 0 end
        redis.call('ZADD', KEYS[1], ARGV[4], ARGV[5])
        redis.call('ZADD', KEYS[2], ARGV[4], ARGV[5])
        redis.call('EXPIRE', KEYS[1], ARGV[6])
        redis.call('EXPIRE', KEYS[2], ARGV[6])
        return 1
    """
    _waiting_source = """
        local now = tonumber(ARGV[1])
        redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now)
        redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', now)
        if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[2]) then return 0 end
        if redis.call('ZCARD', KEYS[2]) >= tonumber(ARGV[3]) then return 0 end
        redis.call('ZADD', KEYS[1], ARGV[4], ARGV[5])
        redis.call('ZADD', KEYS[2], ARGV[4], ARGV[5])
        redis.call('EXPIRE', KEYS[1], ARGV[6])
        redis.call('EXPIRE', KEYS[2], ARGV[6])
        return 1
    """
    _remove_waiting_source = """
        redis.call('ZREM', KEYS[1], ARGV[1])
        redis.call('ZREM', KEYS[2], ARGV[1])
        return 1
    """
    _release_source = """
        redis.call('ZREM', KEYS[1], ARGV[1])
        redis.call('ZREM', KEYS[2], ARGV[1])
        return 1
    """

    def __init__(
        self,
        *,
        redis_url: str | None = None,
        namespace: str | None = None,
        total_limit: int | None = None,
        owner_limit: int | None = None,
        owner_pending_limit: int | None = None,
        acquire_timeout_secs: float | None = None,
        lease_secs: int | None = None,
        client=None,
    ) -> None:
        self.redis_url = (
            redis_url
            or os.getenv("CANVAS_AI_REDIS_URL")
            or os.getenv("CANVAS_REDIS_URL")
            or os.getenv("REDIS_URL")
            or "redis://127.0.0.1:6379/0"
        ).strip()
        self.namespace = _clean(namespace or os.getenv("CANVAS_AI_QUEUE_NAME"), "canvas_ai_tasks")
        self.total_limit = max(1, int(total_limit if total_limit is not None else os.getenv("CANVAS_AI_TOTAL_CONCURRENCY", "8")))
        self.owner_limit = max(1, int(owner_limit if owner_limit is not None else os.getenv("CANVAS_AI_OWNER_CONCURRENCY", "2")))
        self.owner_pending_limit = max(1, int(owner_pending_limit if owner_pending_limit is not None else os.getenv("CANVAS_AI_OWNER_PENDING_LIMIT", "4")))
        self.max_pending = max(self.owner_pending_limit, int(os.getenv("CANVAS_AI_PENDING_LIMIT", str(self.total_limit * 4))))
        self.acquire_timeout_secs = max(0.2, float(acquire_timeout_secs if acquire_timeout_secs is not None else os.getenv("CANVAS_AI_ACQUIRE_TIMEOUT_SECS", "120")))
        self.lease_secs = max(60, int(lease_secs if lease_secs is not None else os.getenv("CANVAS_AI_LEASE_SECS", "900")))
        self._client = client
        self._client_lock = Lock()
        self._scripts: dict[str, object] = {}

    def _redis(self):
        if self._client is not None:
            return self._client
        with self._client_lock:
            if self._client is not None:
                return self._client
            try:
                import redis

                client = redis.Redis.from_url(
                    self.redis_url,
                    decode_responses=True,
                    protocol=2,
                    socket_connect_timeout=2,
                    socket_timeout=5,
                    health_check_interval=30,
                )
                client.ping()
            except Exception as exc:
                raise CanvasAISchedulerUnavailable(f"canvas_ai Redis 不可用：{exc}") from exc
            self._client = client
            return client

    def _script(self, name: str, source: str):
        script = self._scripts.get(name)
        if script is None:
            script = self._redis().register_script(source)
            self._scripts[name] = script
        return script

    def _keys(self, owner_id: str) -> tuple[str, str, str, str, str]:
        prefix = self.namespace
        owner = _digest(owner_id)
        return (
            f"{prefix}:active:global",
            f"{prefix}:active:owner:{owner}",
            f"{prefix}:waiting:global",
            f"{prefix}:waiting:owner:{owner}",
            f"{prefix}:metrics:latency",
        )

    def acquire(self, owner_id: str, operation: str = "assistant") -> CanvasAISlot:
        normalized_owner = _clean(owner_id, "anonymous")
        token = uuid4().hex
        keys = self._keys(normalized_owner)
        now = int(time.time())
        wait_expiry = now + max(60, int(self.acquire_timeout_secs) + 30)
        try:
            admitted = self._script("waiting", self._waiting_source)(
                keys=[keys[2], keys[3]],
                args=[now, self.max_pending, self.owner_pending_limit, wait_expiry, token, self.lease_secs * 2],
            )
        except CanvasAISchedulerUnavailable:
            raise
        except Exception as exc:
            raise CanvasAISchedulerUnavailable(f"canvas_ai Redis 操作失败：{exc}") from exc
        if int(admitted or 0) != 1:
            raise CanvasAISchedulerError(
                f"画布智能助手排队已满（单用户最多等待 {self.owner_pending_limit} 个请求）"
            )

        deadline = time.monotonic() + self.acquire_timeout_secs
        try:
            while time.monotonic() < deadline:
                acquired = self._script("admit", self._admit_source)(
                    keys=[keys[0], keys[1]],
                    args=[
                        int(time.time()),
                        self.owner_limit,
                        self.total_limit,
                        int(time.time()) + self.lease_secs,
                        token,
                        self.lease_secs * 2,
                    ],
                )
                if int(acquired or 0) == 1:
                    self._script("remove_waiting", self._remove_waiting_source)(
                        keys=[keys[2], keys[3]], args=[token]
                    )
                    return CanvasAISlot(normalized_owner, token, time.monotonic())
                time.sleep(0.1)
        except Exception as exc:
            self._remove_waiting(normalized_owner, token)
            if isinstance(exc, CanvasAISchedulerError):
                raise
            raise CanvasAISchedulerUnavailable(f"canvas_ai 调度失败：{exc}") from exc
        self._remove_waiting(normalized_owner, token)
        raise CanvasAISchedulerError("画布智能助手等待超时，请稍后重试")

    def _remove_waiting(self, owner_id: str, token: str) -> None:
        keys = self._keys(owner_id)
        try:
            self._script("remove_waiting", self._remove_waiting_source)(keys=[keys[2], keys[3]], args=[token])
        except Exception:
            pass

    def release(self, slot: CanvasAISlot, *, failed: bool = False, operation: str = "assistant") -> None:
        keys = self._keys(slot.owner_id)
        elapsed_ms = max(0, int((time.monotonic() - slot.acquired_at) * 1000))
        try:
            self._script("release", self._release_source)(keys=[keys[0], keys[1]], args=[slot.token])
            client = self._redis()
            pipe = client.pipeline(transaction=False)
            pipe.lpush(keys[4], elapsed_ms)
            pipe.ltrim(keys[4], 0, 1999)
            pipe.expire(keys[4], 7 * 86400)
            if failed:
                pipe.incr(f"{self.namespace}:metrics:failed")
                pipe.expire(f"{self.namespace}:metrics:failed", 7 * 86400)
            pipe.execute()
        except Exception:
            return

    def snapshot(self) -> dict[str, object]:
        try:
            client = self._redis()
            global_key = f"{self.namespace}:active:global"
            waiting_key = f"{self.namespace}:waiting:global"
            now = int(time.time())
            client.zremrangebyscore(global_key, "-inf", now)
            client.zremrangebyscore(waiting_key, "-inf", now)
            latencies = [float(value) for value in client.lrange(f"{self.namespace}:metrics:latency", 0, 1999)]
            latencies.sort()
            p95 = latencies[max(0, int(len(latencies) * 0.95 + 0.999) - 1)] if latencies else 0
            return {
                "queue": self.namespace,
                "available": True,
                "waiting": int(client.zcard(waiting_key) or 0),
                "running": int(client.zcard(global_key) or 0),
                "failed": int(client.get(f"{self.namespace}:metrics:failed") or 0),
                "p95_ms": round(p95),
                "concurrency": self.total_limit,
                "owner_concurrency": self.owner_limit,
                "owner_pending_limit": self.owner_pending_limit,
            }
        except Exception as exc:
            return {
                "queue": self.namespace,
                "available": False,
                "waiting": 0,
                "running": 0,
                "failed": 0,
                "p95_ms": 0,
                "concurrency": self.total_limit,
                "owner_concurrency": self.owner_limit,
                "owner_pending_limit": self.owner_pending_limit,
                "error": str(exc)[:300],
            }


canvas_ai_scheduler = CanvasAIScheduler()
