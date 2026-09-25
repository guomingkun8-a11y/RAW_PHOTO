from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from services.image.image_task_queue import RedisImageTaskQueue


@dataclass(frozen=True)
class AudioDelivery:
    key: str
    token: str


@dataclass
class RedisAudioGenerationQueue(RedisImageTaskQueue):
    queue_name: str = "ai_audio_generation_tasks"

    def __post_init__(self) -> None:
        super().__post_init__()
        self._pending_key = f"{self.queue_name}:pending"
        self._processing_key = f"{self.queue_name}:processing"
        self._leases_key = f"{self.queue_name}:leases"
        self._delayed_key = f"{self.queue_name}:delayed"
        self._enqueue_script = self._client.register_script("""
            if redis.call('HEXISTS', KEYS[3], ARGV[1]) == 1 then return 0 end
            if redis.call('SADD', KEYS[2], ARGV[1]) == 0 then return 0 end
            if tonumber(ARGV[2]) > tonumber(ARGV[3]) then
                redis.call('ZADD', KEYS[4], ARGV[2], ARGV[1])
            else redis.call('RPUSH', KEYS[1], ARGV[1]) end
            return 1
        """)
        self._reserve_script = self._client.register_script("""
            local due = redis.call('ZRANGEBYSCORE', KEYS[5], '-inf', ARGV[1], 'LIMIT', 0, 100)
            for _, key in ipairs(due) do
                redis.call('ZREM', KEYS[5], key)
                redis.call('RPUSH', KEYS[1], key)
            end
            local key = redis.call('LPOP', KEYS[1])
            if not key then return nil end
            redis.call('SREM', KEYS[2], key)
            if redis.call('HEXISTS', KEYS[3], key) == 1 then return nil end
            redis.call('HSET', KEYS[3], key, ARGV[3])
            redis.call('ZADD', KEYS[4], ARGV[2], key)
            return key
        """)
        self._settle_script = self._client.register_script("""
            if redis.call('HGET', KEYS[3], ARGV[1]) ~= ARGV[2] then return 0 end
            redis.call('HDEL', KEYS[3], ARGV[1])
            redis.call('ZREM', KEYS[4], ARGV[1])
            if ARGV[3] ~= '' then
                redis.call('SADD', KEYS[2], ARGV[1])
                redis.call('ZADD', KEYS[5], ARGV[3], ARGV[1])
            end
            return 1
        """)
        self._renew_script = self._client.register_script("""
            if redis.call('HGET', KEYS[1], ARGV[1]) ~= ARGV[2] then return 0 end
            redis.call('ZADD', KEYS[2], ARGV[3], ARGV[1])
            return 1
        """)
        self._recover_script = self._client.register_script("""
            local expired = redis.call('ZRANGEBYSCORE', KEYS[4], '-inf', ARGV[1], 'LIMIT', 0, 100)
            for _, key in ipairs(expired) do
                redis.call('HDEL', KEYS[3], key)
                redis.call('ZREM', KEYS[4], key)
                if redis.call('SADD', KEYS[2], key) == 1 then redis.call('RPUSH', KEYS[1], key) end
            end
            return #expired
        """)
        self._renew_slot_script = self._client.register_script("""
            local expiry = redis.call('ZSCORE', KEYS[1], ARGV[1])
            if not expiry or tonumber(expiry) <= tonumber(ARGV[2]) then return 0 end
            redis.call('ZADD', KEYS[1], ARGV[3], ARGV[1])
            redis.call('EXPIRE', KEYS[1], ARGV[4])
            return 1
        """)

    def _keys(self) -> list[str]:
        return [self.queue_name, self._pending_key, self._processing_key, self._leases_key, self._delayed_key]

    def enqueue(self, task_key: str, priority: str = "agent", *, delay_secs: float = 0) -> None:
        if task_key:
            now = time.time()
            self._enqueue_script(keys=[self.queue_name, self._pending_key, self._processing_key, self._delayed_key],
                                 args=[task_key, now + max(0, delay_secs), now])

    def reserve(self, timeout_secs: int = 5, lease_secs: int = 90) -> AudioDelivery | None:
        deadline = time.monotonic() + max(0, timeout_secs)
        while True:
            token = uuid.uuid4().hex
            now = time.time()
            key = self._reserve_script(keys=self._keys(), args=[now, now + lease_secs, token])
            if key:
                return AudioDelivery(str(key), token)
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.1)

    def ack(self, delivery: AudioDelivery) -> bool:
        return bool(self._settle_script(keys=self._keys(), args=[delivery.key, delivery.token, ""]))

    def retry(self, delivery: AudioDelivery, delay_secs: float = 1) -> bool:
        return bool(self._settle_script(keys=self._keys(),
                                       args=[delivery.key, delivery.token, time.time() + max(0, delay_secs)]))

    def renew_delivery(self, delivery: AudioDelivery, lease_secs: int = 90) -> bool:
        return bool(self._renew_script(keys=[self._processing_key, self._leases_key],
                                       args=[delivery.key, delivery.token, time.time() + lease_secs]))

    def recover_deliveries(self) -> int:
        return int(self._recover_script(keys=self._keys(), args=[time.time()]))

    def renew_slot(self, token: str) -> bool:
        now = int(time.time())
        lease = max(60, self.slot_lease_secs)
        return bool(self._renew_slot_script(keys=[self._slot_key], args=[token, now, now + lease, lease]))

    def queue_depths(self) -> dict[str, int]:
        return {"ready": int(self._client.llen(self.queue_name)),
                "delayed": int(self._client.zcard(self._delayed_key))}

    def processing_count(self) -> int:
        return int(self._client.hlen(self._processing_key))

