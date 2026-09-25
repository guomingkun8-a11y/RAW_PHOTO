from __future__ import annotations

from dataclasses import dataclass
import hashlib
import socket
import time
import uuid


class ImageTaskQueueError(RuntimeError):
    pass


@dataclass(frozen=True)
class ImageTaskDelivery:
    key: str
    token: str
    priority: str = "agent"


class ImageTaskQueue:
    def enqueue(self, task_key: str, priority: str = "agent") -> None:
        raise NotImplementedError

    def dequeue(self, timeout_secs: int = 5) -> str | None:
        raise NotImplementedError

    def queue_depth(self) -> int:
        raise NotImplementedError

    def queue_depths(self) -> dict[str, int]:
        return {"total": self.queue_depth()}

    def active_slot_count(self) -> int:
        raise NotImplementedError

    def touch_worker(self, worker_id: str, timeout_secs: int = 60) -> None:
        raise NotImplementedError

    def active_worker_count(self) -> int:
        raise NotImplementedError

    def forget_worker(self, worker_id: str) -> None:
        raise NotImplementedError

    def effective_concurrency_limit(self) -> int:
        return 0

    def record_provider_result(self, *, success: bool, throttled: bool = False) -> None:
        return None

    def adaptive_concurrency_snapshot(self) -> dict[str, object]:
        return {"enabled": False}

    def notify_task_update(self, task_key: str) -> None:
        return None

    def notification_cursors(self, task_keys: list[str]) -> dict[str, str]:
        return {str(task_key): "0-0" for task_key in task_keys if str(task_key).strip()}

    def wait_for_task_updates(
        self,
        task_keys: list[str],
        cursors: dict[str, str],
        timeout_secs: float = 15.0,
    ) -> bool:
        time.sleep(min(1.0, max(0.05, float(timeout_secs))))
        return False

    def acquire_owner_slot(
        self,
        owner_id: str,
        token: str,
        limit: int,
        timeout_secs: float = 5.0,
    ) -> bool:
        return True

    def release_owner_slot(self, owner_id: str, token: str) -> None:
        return None

    def acquire_maintenance_lock(self, token: str, timeout_secs: int = 60) -> bool:
        return True

    def release_maintenance_lock(self, token: str) -> None:
        return None


@dataclass
class RedisImageTaskQueue(ImageTaskQueue):
    redis_url: str
    queue_name: str = "ai_image_tasks"
    max_concurrency: int = 0
    slot_lease_secs: int = 7200
    adaptive_concurrency_enabled: bool = True
    adaptive_min_concurrency: int = 5
    adaptive_recovery_successes: int = 12
    adaptive_cooldown_secs: int = 30

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
        self._pending_key = f"{self.queue_name}:pending"
        self._processing_key = f"{self.queue_name}:processing"
        self._delivery_lease_key = f"{self.queue_name}:delivery_leases"
        self._task_priority_key = f"{self.queue_name}:task_priorities"
        self._maintenance_lock_key = f"{self.queue_name}:maintenance_lock"
        self._priority_queues = {
            "standard": f"{self.queue_name}:standard",
            "agent": self.queue_name,
            "batch": f"{self.queue_name}:batch",
        }
        self._adaptive_limit_key = f"{self.queue_name}:adaptive:limit"
        self._adaptive_success_key = f"{self.queue_name}:adaptive:successes"
        self._adaptive_cooldown_key = f"{self.queue_name}:adaptive:cooldown_until"
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
        self._enqueue_script = self._client.register_script(
            """
            if redis.call('HEXISTS', KEYS[3], ARGV[1]) == 1 then
                return 0
            end
            if redis.call('SADD', KEYS[2], ARGV[1]) == 0 then
                return 0
            end
            redis.call('HSET', KEYS[4], ARGV[1], ARGV[2])
            redis.call('RPUSH', KEYS[1], ARGV[1])
            return 1
            """
        )
        self._reserve_script = self._client.register_script(
            """
            local function queue_for(priority)
                if priority == 'standard' then return KEYS[1] end
                if priority == 'batch' then return KEYS[3] end
                return KEYS[2]
            end

            local expired = redis.call('ZRANGEBYSCORE', KEYS[6], '-inf', ARGV[1], 'LIMIT', 0, 100)
            for _, task_key in ipairs(expired) do
                redis.call('HDEL', KEYS[5], task_key)
                redis.call('ZREM', KEYS[6], task_key)
                local priority = redis.call('HGET', KEYS[7], task_key) or 'agent'
                if redis.call('SADD', KEYS[4], task_key) == 1 then
                    redis.call('RPUSH', queue_for(priority), task_key)
                end
            end

            local task_key = nil
            local priority = nil
            for queue_index = 1, 3 do
                repeat
                    task_key = redis.call('LPOP', KEYS[queue_index])
                until not task_key or redis.call('SISMEMBER', KEYS[4], task_key) == 1
                if task_key then
                    priority = queue_index == 1 and 'standard' or (queue_index == 3 and 'batch' or 'agent')
                    break
                end
            end
            if not task_key then return nil end

            redis.call('SREM', KEYS[4], task_key)
            if redis.call('HEXISTS', KEYS[5], task_key) == 1 then return nil end
            redis.call('HSET', KEYS[5], task_key, ARGV[3])
            redis.call('HSET', KEYS[7], task_key, priority)
            redis.call('ZADD', KEYS[6], ARGV[2], task_key)
            return {task_key, priority}
            """
        )
        self._settle_delivery_script = self._client.register_script(
            """
            if redis.call('HGET', KEYS[3], ARGV[1]) ~= ARGV[2] then return 0 end
            redis.call('HDEL', KEYS[3], ARGV[1])
            redis.call('ZREM', KEYS[4], ARGV[1])
            if ARGV[3] == '1' then
                redis.call('HSET', KEYS[5], ARGV[1], ARGV[4])
                if redis.call('SADD', KEYS[2], ARGV[1]) == 1 then
                    redis.call('RPUSH', KEYS[1], ARGV[1])
                end
            else
                redis.call('HDEL', KEYS[5], ARGV[1])
            end
            return 1
            """
        )
        self._renew_delivery_script = self._client.register_script(
            """
            if redis.call('HGET', KEYS[1], ARGV[1]) ~= ARGV[2] then return 0 end
            redis.call('ZADD', KEYS[2], ARGV[3], ARGV[1])
            return 1
            """
        )
        self._recover_deliveries_script = self._client.register_script(
            """
            local function queue_for(priority)
                if priority == 'standard' then return KEYS[1] end
                if priority == 'batch' then return KEYS[3] end
                return KEYS[2]
            end
            local expired = redis.call('ZRANGEBYSCORE', KEYS[6], '-inf', ARGV[1], 'LIMIT', 0, 100)
            for _, task_key in ipairs(expired) do
                redis.call('HDEL', KEYS[5], task_key)
                redis.call('ZREM', KEYS[6], task_key)
                local priority = redis.call('HGET', KEYS[7], task_key) or 'agent'
                if redis.call('SADD', KEYS[4], task_key) == 1 then
                    redis.call('RPUSH', queue_for(priority), task_key)
                end
            end
            return #expired
            """
        )
        self._release_lock_script = self._client.register_script(
            """
            if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
            return redis.call('DEL', KEYS[1])
            """
        )
        self._adaptive_throttle_script = self._client.register_script(
            """
            local current = tonumber(redis.call('GET', KEYS[1]) or ARGV[1])
            local reduced = math.max(tonumber(ARGV[2]), math.floor(current * 0.7))
            redis.call('SET', KEYS[1], reduced)
            redis.call('SET', KEYS[2], 0)
            redis.call('SET', KEYS[3], ARGV[3])
            return reduced
            """
        )
        self._adaptive_success_script = self._client.register_script(
            """
            local current = tonumber(redis.call('GET', KEYS[1]) or ARGV[1])
            if current >= tonumber(ARGV[1]) then
                redis.call('SET', KEYS[1], ARGV[1])
                redis.call('SET', KEYS[2], 0)
                return current
            end
            local cooldown_until = tonumber(redis.call('GET', KEYS[3]) or '0')
            if cooldown_until > tonumber(ARGV[2]) then
                return current
            end
            local successes = redis.call('INCR', KEYS[2])
            if successes >= tonumber(ARGV[3]) then
                current = math.min(tonumber(ARGV[1]), current + 1)
                redis.call('SET', KEYS[1], current)
                redis.call('SET', KEYS[2], 0)
            end
            return current
            """
        )

    def _event_key(self, task_key: str) -> str:
        digest = hashlib.sha256(str(task_key).encode("utf-8")).hexdigest()[:32]
        return f"{self.queue_name}:events:{digest}"

    @staticmethod
    def _normalize_priority(priority: object) -> str:
        value = str(priority or "agent").strip().lower()
        if value in {"high", "interactive", "standard"}:
            return "standard"
        if value in {"low", "bulk", "folder", "batch"}:
            return "batch"
        return "agent"

    def _queue_for_priority(self, priority: object) -> str:
        return self._priority_queues[self._normalize_priority(priority)]

    def enqueue(self, task_key: str, priority: str = "agent") -> None:
        value = str(task_key or "").strip()
        if not value:
            return
        normalized_priority = self._normalize_priority(priority)
        self._enqueue_script(
            keys=[
                self._queue_for_priority(normalized_priority),
                self._pending_key,
                self._processing_key,
                self._task_priority_key,
            ],
            args=[value, normalized_priority],
        )

    def _delivery_keys(self) -> list[str]:
        return [
            self._priority_queues["standard"],
            self._priority_queues["agent"],
            self._priority_queues["batch"],
            self._pending_key,
            self._processing_key,
            self._delivery_lease_key,
            self._task_priority_key,
        ]

    def reserve(self, timeout_secs: int = 5, lease_secs: int = 90) -> ImageTaskDelivery | None:
        deadline = time.monotonic() + max(0.0, float(timeout_secs or 0))
        lease = max(30, int(lease_secs or 90))
        while True:
            token = uuid.uuid4().hex
            now = time.time()
            result = self._reserve_script(
                keys=self._delivery_keys(),
                args=[now, now + lease, token],
            )
            if result:
                return ImageTaskDelivery(
                    key=str(result[0]),
                    token=token,
                    priority=self._normalize_priority(result[1]),
                )
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.1)

    def ack(self, delivery: ImageTaskDelivery) -> bool:
        return bool(
            self._settle_delivery_script(
                keys=[
                    self._queue_for_priority(delivery.priority),
                    self._pending_key,
                    self._processing_key,
                    self._delivery_lease_key,
                    self._task_priority_key,
                ],
                args=[delivery.key, delivery.token, "0", delivery.priority],
            )
        )

    def retry(self, delivery: ImageTaskDelivery, priority: str | None = None) -> bool:
        normalized_priority = self._normalize_priority(priority or delivery.priority)
        return bool(
            self._settle_delivery_script(
                keys=[
                    self._queue_for_priority(normalized_priority),
                    self._pending_key,
                    self._processing_key,
                    self._delivery_lease_key,
                    self._task_priority_key,
                ],
                args=[delivery.key, delivery.token, "1", normalized_priority],
            )
        )

    def renew_delivery(self, delivery: ImageTaskDelivery, lease_secs: int = 90) -> bool:
        return bool(
            self._renew_delivery_script(
                keys=[self._processing_key, self._delivery_lease_key],
                args=[delivery.key, delivery.token, time.time() + max(30, int(lease_secs or 90))],
            )
        )

    def recover_deliveries(self) -> int:
        return int(self._recover_deliveries_script(
            keys=self._delivery_keys(),
            args=[time.time()],
        ) or 0)

    def contains_task(self, task_key: str) -> bool:
        value = str(task_key or "").strip()
        if not value:
            return False
        pipe = self._client.pipeline(transaction=False)
        pipe.sismember(self._pending_key, value)
        pipe.hexists(self._processing_key, value)
        pending, processing = pipe.execute()
        return bool(pending or processing)

    def processing_count(self) -> int:
        return int(self._client.hlen(self._processing_key) or 0)

    def dequeue(self, timeout_secs: int = 5) -> str | None:
        delivery = self.reserve(timeout_secs=timeout_secs)
        if delivery is None:
            return None
        self.ack(delivery)
        return delivery.key

    def queue_depth(self) -> int:
        return sum(self.queue_depths().values())

    def queue_depths(self) -> dict[str, int]:
        pipe = self._client.pipeline(transaction=False)
        for priority in ("standard", "agent", "batch"):
            pipe.llen(self._priority_queues[priority])
        values = pipe.execute()
        return {
            priority: int(value or 0)
            for priority, value in zip(("standard", "agent", "batch"), values)
        }

    def active_slot_count(self) -> int:
        now = int(time.time())
        self._client.zremrangebyscore(self._slot_key, "-inf", now)
        return int(self._client.zcard(self._slot_key) or 0)

    def acquire_slot(self, token: str, timeout_secs: float = 30.0) -> bool:
        limit = self.effective_concurrency_limit()
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

    def _owner_slot_key(self, owner_id: str) -> str:
        digest = hashlib.sha256(str(owner_id or "anonymous").encode("utf-8")).hexdigest()[:32]
        return f"{self.queue_name}:owner_slots:{digest}"

    def acquire_owner_slot(
        self,
        owner_id: str,
        token: str,
        limit: int,
        timeout_secs: float = 5.0,
    ) -> bool:
        normalized_limit = max(1, int(limit or 1))
        deadline = time.monotonic() + max(0.1, float(timeout_secs))
        lease_secs = max(60, int(self.slot_lease_secs or 7200))
        slot_key = self._owner_slot_key(owner_id)
        while time.monotonic() < deadline:
            now = int(time.time())
            result = self._acquire_slot_script(
                keys=[slot_key],
                args=[now, normalized_limit, now + lease_secs, token, lease_secs],
            )
            if int(result or 0) == 1:
                return True
            time.sleep(0.1)
        return False

    def release_owner_slot(self, owner_id: str, token: str) -> None:
        if token:
            self._client.zrem(self._owner_slot_key(owner_id), token)

    def acquire_maintenance_lock(self, token: str, timeout_secs: int = 60) -> bool:
        value = str(token or "").strip()
        if not value:
            return False
        return bool(
            self._client.set(
                self._maintenance_lock_key,
                value,
                nx=True,
                ex=max(10, int(timeout_secs or 60)),
            )
        )

    def release_maintenance_lock(self, token: str) -> None:
        value = str(token or "").strip()
        if value:
            self._release_lock_script(keys=[self._maintenance_lock_key], args=[value])

    def effective_concurrency_limit(self) -> int:
        configured = max(0, int(self.max_concurrency or 0))
        if configured <= 0 or not self.adaptive_concurrency_enabled:
            return configured
        try:
            stored = self._client.get(self._adaptive_limit_key)
            if stored is None:
                return configured
            return max(
                min(configured, max(1, int(self.adaptive_min_concurrency or 1))),
                min(configured, int(stored)),
            )
        except Exception:
            return configured

    def record_provider_result(self, *, success: bool, throttled: bool = False) -> None:
        configured = max(0, int(self.max_concurrency or 0))
        if configured <= 0 or not self.adaptive_concurrency_enabled:
            return
        minimum = min(configured, max(1, int(self.adaptive_min_concurrency or 1)))
        now = int(time.time())
        try:
            if throttled:
                self._adaptive_throttle_script(
                    keys=[self._adaptive_limit_key, self._adaptive_success_key, self._adaptive_cooldown_key],
                    args=[configured, minimum, now + max(1, int(self.adaptive_cooldown_secs or 30))],
                )
            elif success:
                self._adaptive_success_script(
                    keys=[self._adaptive_limit_key, self._adaptive_success_key, self._adaptive_cooldown_key],
                    args=[configured, now, max(1, int(self.adaptive_recovery_successes or 12))],
                )
        except Exception:
            return

    def adaptive_concurrency_snapshot(self) -> dict[str, object]:
        configured = max(0, int(self.max_concurrency or 0))
        if configured <= 0 or not self.adaptive_concurrency_enabled:
            return {
                "enabled": False,
                "configured_limit": configured,
                "effective_limit": configured,
            }
        now = int(time.time())
        try:
            pipe = self._client.pipeline(transaction=False)
            pipe.get(self._adaptive_success_key)
            pipe.get(self._adaptive_cooldown_key)
            successes, cooldown_until = pipe.execute()
        except Exception:
            successes, cooldown_until = 0, 0
        cooldown_value = int(cooldown_until or 0)
        return {
            "enabled": True,
            "configured_limit": configured,
            "effective_limit": self.effective_concurrency_limit(),
            "minimum_limit": min(configured, max(1, int(self.adaptive_min_concurrency or 1))),
            "recovery_successes": int(successes or 0),
            "recovery_target": max(1, int(self.adaptive_recovery_successes or 12)),
            "cooldown_remaining_secs": max(0, cooldown_value - now),
        }

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

    def notify_task_update(self, task_key: str) -> None:
        value = str(task_key or "").strip()
        if not value:
            return
        key = self._event_key(value)
        pipe = self._client.pipeline(transaction=False)
        pipe.xadd(key, {"task_key": value}, maxlen=64, approximate=True)
        pipe.expire(key, 86400)
        pipe.execute()

    def notification_cursors(self, task_keys: list[str]) -> dict[str, str]:
        normalized = [str(task_key or "").strip() for task_key in task_keys]
        normalized = [task_key for task_key in normalized if task_key]
        if not normalized:
            return {}
        pipe = self._client.pipeline(transaction=False)
        for task_key in normalized:
            pipe.xrevrange(self._event_key(task_key), count=1)
        rows = pipe.execute()
        return {
            task_key: str(items[0][0]) if items else "0-0"
            for task_key, items in zip(normalized, rows)
        }

    def wait_for_task_updates(
        self,
        task_keys: list[str],
        cursors: dict[str, str],
        timeout_secs: float = 15.0,
    ) -> bool:
        streams = {
            self._event_key(task_key): str(cursors.get(task_key) or "0-0")
            for task_key in (str(value or "").strip() for value in task_keys)
            if task_key
        }
        if not streams:
            return False
        try:
            rows = self._client.xread(
                streams,
                count=1,
                block=max(1, int(float(timeout_secs) * 1000)),
            )
            return bool(rows)
        except (TimeoutError, socket.timeout):
            return False
        except Exception as exc:
            if "timeout reading from socket" in str(exc).lower():
                return False
            raise


@dataclass
class CeleryImageTaskQueue(RedisImageTaskQueue):
    def enqueue(self, task_key: str, priority: str = "agent") -> None:
        value = str(task_key or "").strip()
        if not value:
            return
        try:
            from services.platform.celery_app import celery_app

            celery_app.send_task(
                "image_tasks.process",
                args=[value],
                queue=self._queue_for_priority(priority),
            )
        except Exception as exc:
            raise ImageTaskQueueError(f"failed to enqueue Celery image task: {exc}") from exc

    def dequeue(self, timeout_secs: int = 5) -> str | None:
        raise ImageTaskQueueError("Celery queue is consumed by the Celery worker")
