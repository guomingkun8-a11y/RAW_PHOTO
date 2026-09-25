from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass
from threading import Lock
from uuid import uuid4


class CanvasTaskLimitError(ValueError):
    """A canvas task cannot reserve another unit right now."""

    status_code = 429


class CanvasTaskLimiterUnavailable(CanvasTaskLimitError):
    status_code = 503


@dataclass(frozen=True)
class CanvasTaskReservation:
    owner_id: str
    token: str
    units: int


def _clean(value: object, default: str = "") -> str:
    text = str(value if value is not None else default).strip()
    return text or default


def _owner_key(owner_id: str) -> str:
    digest = hashlib.sha256(_clean(owner_id, "anonymous").encode("utf-8")).hexdigest()[:32]
    return digest


class CanvasTaskLimiter:
    """Redis-backed weighted leases shared by all canvas media task services."""

    _reserve_source = """
        local now = tonumber(ARGV[1])
        local expired = redis.call('ZRANGEBYSCORE', KEYS[3], '-inf', now)
        for _, token in ipairs(expired) do
            local units = tonumber(redis.call('HGET', KEYS[2], token) or '0')
            if units > 0 then redis.call('DECRBY', KEYS[1], units) end
            redis.call('HDEL', KEYS[2], token)
            redis.call('ZREM', KEYS[3], token)
        end
        if redis.call('HEXISTS', KEYS[2], ARGV[4]) == 1 then return 1 end
        local current = tonumber(redis.call('GET', KEYS[1]) or '0')
        if current + tonumber(ARGV[2]) > tonumber(ARGV[3]) then return 0 end
        redis.call('HSET', KEYS[2], ARGV[4], ARGV[2])
        redis.call('ZADD', KEYS[3], ARGV[5], ARGV[4])
        redis.call('INCRBY', KEYS[1], ARGV[2])
        redis.call('EXPIRE', KEYS[1], ARGV[6])
        redis.call('EXPIRE', KEYS[2], ARGV[6])
        redis.call('EXPIRE', KEYS[3], ARGV[6])
        return 1
    """
    _release_source = """
        local units = tonumber(redis.call('HGET', KEYS[2], ARGV[1]) or '0')
        if not units then return 0 end
        redis.call('HDEL', KEYS[2], ARGV[1])
        redis.call('ZREM', KEYS[3], ARGV[1])
        local remaining = redis.call('DECRBY', KEYS[1], units)
        if remaining <= 0 then redis.call('DEL', KEYS[1]) end
        return units
    """

    def __init__(
        self,
        *,
        redis_url: str | None = None,
        namespace: str | None = None,
        limit: int | None = None,
        batch_limit: int | None = None,
        lease_secs: int | None = None,
        client=None,
    ) -> None:
        self.redis_url = (
            redis_url
            or os.getenv("CANVAS_TASK_REDIS_URL")
            or os.getenv("CANVAS_REDIS_URL")
            or os.getenv("REDIS_URL")
            or os.getenv("IMAGE_TASK_REDIS_URL")
            or "redis://127.0.0.1:6379/0"
        ).strip()
        self.namespace = _clean(
            namespace or os.getenv("CANVAS_TASK_LIMITER_NAMESPACE"),
            "canvas_tasks",
        )
        self.limit = max(1, int(limit if limit is not None else os.getenv("CANVAS_TASK_USER_LIMIT", "6")))
        self.batch_limit = max(
            1,
            int(batch_limit if batch_limit is not None else os.getenv("CANVAS_TASK_BATCH_LIMIT", "8")),
        )
        self.lease_secs = max(60, int(lease_secs if lease_secs is not None else os.getenv("CANVAS_TASK_LEASE_SECS", "7200")))
        self._client = client
        self._client_lock = Lock()
        self._reserve_script = None
        self._release_script = None

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
                raise CanvasTaskLimiterUnavailable(f"画布任务限制 Redis 不可用：{exc}") from exc
            self._client = client
            return client

    def _scripts(self):
        client = self._redis()
        if self._reserve_script is None:
            self._reserve_script = client.register_script(self._reserve_source)
            self._release_script = client.register_script(self._release_source)
        return client, self._reserve_script, self._release_script

    def _keys(self, owner_id: str) -> tuple[str, str, str]:
        prefix = f"{self.namespace}:owner:{_owner_key(owner_id)}"
        return f"{prefix}:units", f"{prefix}:reservations", f"{prefix}:expires"

    def reserve(
        self,
        owner_id: str,
        *,
        units: int = 1,
        token: str | None = None,
        limit: int | None = None,
    ) -> CanvasTaskReservation:
        normalized_units = max(1, int(units or 1))
        normalized_limit = max(1, int(limit if limit is not None else self.limit))
        if normalized_units > self.batch_limit:
            raise CanvasTaskLimitError(
                f"本次画布批量任务最多提交 {self.batch_limit} 个并发单位"
            )
        if normalized_units > normalized_limit:
            raise CanvasTaskLimitError(
                f"本次画布任务需要 {normalized_units} 个并发单位，超过单用户上限 {normalized_limit}"
            )
        normalized_owner = _clean(owner_id, "anonymous")
        reservation_token = _clean(token) or uuid4().hex
        try:
            _, script, _ = self._scripts()
            result = script(
                keys=list(self._keys(normalized_owner)),
                args=[
                    int(time.time()),
                    normalized_units,
                    normalized_limit,
                    reservation_token,
                    int(time.time()) + self.lease_secs,
                    self.lease_secs * 2,
                ],
            )
        except CanvasTaskLimiterUnavailable:
            raise
        except Exception as exc:
            raise CanvasTaskLimiterUnavailable(f"画布任务限制 Redis 操作失败：{exc}") from exc
        if int(result or 0) != 1:
            raise CanvasTaskLimitError(
                f"画布任务已达到单用户上限（最多 {normalized_limit} 个并发单位），请等待当前任务完成"
            )
        return CanvasTaskReservation(normalized_owner, reservation_token, normalized_units)

    def release(self, reservation: CanvasTaskReservation | None = None, *, owner_id: str = "", token: str = "") -> None:
        if reservation is not None:
            owner_id = reservation.owner_id
            token = reservation.token
        normalized_token = _clean(token)
        if not normalized_token:
            return
        try:
            _, _, script = self._scripts()
            script(keys=list(self._keys(_clean(owner_id, "anonymous"))), args=[normalized_token])
        except Exception:
            # The lease has a TTL; a transient release failure cannot block a user forever.
            return

    def snapshot(self, owner_id: str | None = None) -> dict[str, object]:
        try:
            client = self._redis()
            if owner_id:
                keys = self._keys(owner_id)
                client.zremrangebyscore(keys[2], "-inf", int(time.time()))
                running = int(client.get(keys[0]) or 0)
            else:
                running = 0
            return {
                "available": True,
                "limit": self.limit,
                "batch_limit": self.batch_limit,
                "running": running,
                "queue_namespace": self.namespace,
            }
        except Exception as exc:
            return {
                "available": False,
                "limit": self.limit,
                "batch_limit": self.batch_limit,
                "running": 0,
                "queue_namespace": self.namespace,
                "error": str(exc)[:300],
            }


canvas_task_limiter = CanvasTaskLimiter()
