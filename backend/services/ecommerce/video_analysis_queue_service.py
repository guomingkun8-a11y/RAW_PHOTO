from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
import socket
import threading
import time
from typing import Any, Mapping
from uuid import uuid4

from services.platform.config import config


def _clean(value: object, default: str = "") -> str:
    text = str(value if value is not None else default).strip()
    return text or default


def _scope_key(value: object) -> str:
    return hashlib.sha256(_clean(value, "anonymous").encode("utf-8")).hexdigest()[:24]


@dataclass(frozen=True)
class VideoAnalysisQueueSettings:
    enabled: bool
    redis_url: str
    queue_name: str
    worker_concurrency: int
    total_concurrency: int
    owner_concurrency: int
    owner_pending_limit: int
    slot_lease_secs: int
    job_retention_secs: int
    max_retries: int
    retry_base_delay_secs: int
    retry_max_delay_secs: int
    dead_letter_retention_secs: int


def video_analysis_queue_settings() -> VideoAnalysisQueueSettings:
    settings = config.get_video_analysis_settings()
    return VideoAnalysisQueueSettings(
        enabled=bool(settings.get("enabled") and settings.get("queue_enabled")),
        redis_url=_clean(settings.get("redis_url"), "redis://127.0.0.1:6379/0"),
        queue_name=_clean(settings.get("queue_name"), "professional_video_analysis_jobs"),
        worker_concurrency=max(1, int(settings.get("worker_concurrency") or 1)),
        total_concurrency=max(1, int(settings.get("total_concurrency") or 1)),
        owner_concurrency=max(1, int(settings.get("owner_concurrency") or 1)),
        owner_pending_limit=max(1, int(settings.get("owner_pending_limit") or 1)),
        slot_lease_secs=max(60, int(settings.get("slot_lease_secs") or 1800)),
        job_retention_secs=max(3600, int(settings.get("job_retention_secs") or 86400)),
        max_retries=max(0, int(settings.get("max_retries") or 0)),
        retry_base_delay_secs=max(1, int(settings.get("retry_base_delay_secs") or 5)),
        retry_max_delay_secs=max(1, int(settings.get("retry_max_delay_secs") or 120)),
        dead_letter_retention_secs=max(3600, int(settings.get("dead_letter_retention_secs") or 604800)),
    )


@dataclass(frozen=True)
class VideoAnalysisQueueMessage:
    message_id: str
    video_id: str
    owner_id: str
    attempt: int = 0
    conversation_id: str = ""


@dataclass(frozen=True)
class VideoAnalysisExecutionLease:
    token: str
    owner_key: str


class VideoAnalysisQueueUnavailable(RuntimeError):
    pass


class VideoAnalysisQueueFull(RuntimeError):
    pass


class RedisVideoAnalysisQueue:
    GROUP_NAME = "professional-video-analysis-workers"

    def __init__(self) -> None:
        self._client_instance = None
        self._client_lock = threading.Lock()
        self._scripts: dict[str, Any] = {}

    @property
    def settings(self) -> VideoAnalysisQueueSettings:
        return video_analysis_queue_settings()

    def _client(self):
        cached = self._client_instance
        if cached is not None:
            return cached
        with self._client_lock:
            if self._client_instance is not None:
                return self._client_instance
            try:
                import redis

                client = redis.Redis.from_url(
                    self.settings.redis_url,
                    decode_responses=True,
                    protocol=2,
                    socket_connect_timeout=2,
                    socket_timeout=10,
                    health_check_interval=30,
                )
                client.ping()
            except Exception as exc:
                raise VideoAnalysisQueueUnavailable(f"video analysis Redis is unavailable: {exc}") from exc
            self._client_instance = client
            return client

    def ensure_ready(self) -> None:
        if not self.settings.enabled:
            raise VideoAnalysisQueueUnavailable("video analysis queue is disabled")
        client = self._client()
        try:
            client.xgroup_create(self.settings.queue_name, self.GROUP_NAME, id="0", mkstream=True)
        except Exception as exc:
            if "BUSYGROUP" not in str(exc):
                raise VideoAnalysisQueueUnavailable(f"video analysis queue initialization failed: {exc}") from exc

    def _script(self, name: str, source: str):
        cached = self._scripts.get(name)
        if cached is not None:
            return cached
        script = self._client().register_script(source)
        self._scripts[name] = script
        return script

    def _pending_key(self, owner_id: str) -> str:
        return f"{self.settings.queue_name}:pending:{_scope_key(owner_id)}"

    def _worker_key(self) -> str:
        return f"{self.settings.queue_name}:workers"

    def _retry_key(self) -> str:
        return f"{self.settings.queue_name}:retry"

    def _dead_letter_key(self) -> str:
        return f"{self.settings.queue_name}:dead"

    @staticmethod
    def _message_from_entry(message_id: object, fields: Mapping[str, Any]) -> VideoAnalysisQueueMessage:
        try:
            attempt = max(0, int(fields.get("attempt") or 0))
        except (TypeError, ValueError):
            attempt = 0
        return VideoAnalysisQueueMessage(
            message_id=_clean(message_id),
            video_id=_clean(fields.get("video_id")),
            owner_id=_clean(fields.get("owner_id"), "anonymous"),
            attempt=attempt,
            conversation_id=_clean(fields.get("conversation_id")),
        )

    def reserve_pending(self, *, video_id: str, owner_id: str) -> None:
        settings = self.settings
        now = int(time.time())
        expires_at = now + settings.job_retention_secs
        script = self._script(
            "reserve_pending",
            """
            redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[1])
            if redis.call('ZSCORE', KEYS[1], ARGV[3]) then
                return 1
            end
            if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[2]) then
                return 0
            end
            redis.call('ZADD', KEYS[1], ARGV[4], ARGV[3])
            redis.call('EXPIRE', KEYS[1], ARGV[5])
            return 1
            """,
        )
        result = script(
            keys=[self._pending_key(owner_id)],
            args=[now, settings.owner_pending_limit, video_id, expires_at, settings.job_retention_secs],
        )
        if int(result or 0) != 1:
            raise VideoAnalysisQueueFull("video analysis queue is full for this user")

    def release_pending(self, *, video_id: str, owner_id: str) -> None:
        try:
            self._client().zrem(self._pending_key(owner_id), video_id)
        except Exception:
            pass

    def enqueue(self, *, video_id: str, owner_id: str, attempt: int = 0, conversation_id: str = "") -> str:
        self.ensure_ready()
        self.reserve_pending(video_id=video_id, owner_id=owner_id)
        try:
            message_id = self._client().xadd(
                self.settings.queue_name,
                {
                    "video_id": _clean(video_id),
                    "owner_id": _clean(owner_id, "anonymous"),
                    "attempt": max(0, int(attempt)),
                    "conversation_id": _clean(conversation_id),
                },
            )
        except Exception:
            self.release_pending(video_id=video_id, owner_id=owner_id)
            raise
        return _clean(message_id)

    def _consumer_name(self, suffix: str = "") -> str:
        tail = f"-{suffix}" if suffix else ""
        return f"{socket.gethostname()}-{os.getpid()}{tail}"

    def dequeue(self, *, consumer: str, timeout_secs: int = 5) -> VideoAnalysisQueueMessage | None:
        self.ensure_ready()
        messages = self._client().xreadgroup(
            self.GROUP_NAME,
            self._consumer_name(consumer),
            {self.settings.queue_name: ">"},
            count=1,
            block=max(1, int(timeout_secs)) * 1000,
        )
        if not messages:
            return None
        _stream, entries = messages[0]
        if not entries:
            return None
        message_id, fields = entries[0]
        return self._message_from_entry(message_id, fields)

    def claim_stale(self, *, consumer: str) -> VideoAnalysisQueueMessage | None:
        self.ensure_ready()
        client = self._client()
        try:
            result = client.xautoclaim(
                self.settings.queue_name,
                self.GROUP_NAME,
                self._consumer_name(consumer),
                min_idle_time=self.settings.slot_lease_secs * 1000,
                start_id="0-0",
                count=1,
            )
            entries = result[1] if isinstance(result, (list, tuple)) and len(result) > 1 else []
        except Exception:
            return None
        if not entries:
            return None
        message_id, fields = entries[0]
        return self._message_from_entry(message_id, fields)

    def ack(self, message: VideoAnalysisQueueMessage) -> None:
        pipe = self._client().pipeline(transaction=True)
        pipe.xack(self.settings.queue_name, self.GROUP_NAME, message.message_id)
        pipe.xdel(self.settings.queue_name, message.message_id)
        pipe.execute()

    def retry_delay(self, attempt: int) -> int:
        normalized_attempt = max(1, int(attempt))
        settings = self.settings
        return min(
            settings.retry_max_delay_secs,
            settings.retry_base_delay_secs * (2 ** (normalized_attempt - 1)),
        )

    def schedule_retry(
        self,
        message: VideoAnalysisQueueMessage,
        *,
        attempt: int,
        reason: str,
        delay_secs: float,
    ) -> None:
        retry_payload = json.dumps(
            {
                "retry_id": uuid4().hex,
                "video_id": message.video_id,
                "owner_id": message.owner_id,
                "attempt": max(0, int(attempt)),
                "conversation_id": message.conversation_id,
                "reason": _clean(reason)[:2000],
                "source_message_id": message.message_id,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        available_at = time.time() + max(0.1, float(delay_secs))
        pipe = self._client().pipeline(transaction=True)
        pipe.zadd(self._retry_key(), {retry_payload: available_at})
        pipe.expire(self._retry_key(), self.settings.dead_letter_retention_secs)
        pipe.xack(self.settings.queue_name, self.GROUP_NAME, message.message_id)
        pipe.xdel(self.settings.queue_name, message.message_id)
        pipe.execute()

    def defer(self, message: VideoAnalysisQueueMessage, *, reason: str = "execution slot unavailable") -> None:
        self.schedule_retry(message, attempt=message.attempt, reason=reason, delay_secs=1.0)

    def promote_due_retries(self, *, limit: int = 100) -> int:
        self.ensure_ready()
        script = self._script(
            "promote_due_retries",
            """
            local rows = redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', ARGV[1], 'LIMIT', 0, ARGV[2])
            local promoted = 0
            for _, encoded in ipairs(rows) do
                if redis.call('ZREM', KEYS[1], encoded) == 1 then
                    local payload = cjson.decode(encoded)
                    redis.call(
                        'XADD', KEYS[2], '*',
                        'video_id', payload.video_id,
                        'owner_id', payload.owner_id,
                        'attempt', tostring(payload.attempt or 0),
                        'conversation_id', payload.conversation_id or '',
                        'last_error', payload.reason or ''
                    )
                    promoted = promoted + 1
                end
            end
            return promoted
            """,
        )
        result = script(
            keys=[self._retry_key(), self.settings.queue_name],
            args=[time.time(), max(1, int(limit))],
        )
        return max(0, int(result or 0))

    def dead_letter(self, message: VideoAnalysisQueueMessage, *, attempt: int, error: str) -> str:
        pipe = self._client().pipeline(transaction=True)
        pipe.xadd(
            self._dead_letter_key(),
            {
                "video_id": message.video_id,
                "owner_id": message.owner_id,
                "attempt": max(0, int(attempt)),
                "conversation_id": message.conversation_id,
                "error": _clean(error)[:12000],
                "source_message_id": message.message_id,
                "failed_at": int(time.time()),
            },
            maxlen=10000,
            approximate=True,
        )
        pipe.expire(self._dead_letter_key(), self.settings.dead_letter_retention_secs)
        pipe.xack(self.settings.queue_name, self.GROUP_NAME, message.message_id)
        pipe.xdel(self.settings.queue_name, message.message_id)
        results = pipe.execute()
        return _clean(results[0] if results else "")

    def acquire_execution(self, *, owner_id: str) -> VideoAnalysisExecutionLease | None:
        settings = self.settings
        token = uuid4().hex
        owner_key = _scope_key(owner_id)
        prefix = settings.queue_name
        global_slots = f"{prefix}:slots:global"
        owner_slots = f"{prefix}:slots:owner:{owner_key}"
        now = int(time.time())
        expires_at = now + settings.slot_lease_secs
        script = self._script(
            "acquire_execution",
            """
            redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[1])
            redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', ARGV[1])
            if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[2]) then
                return 0
            end
            if redis.call('ZCARD', KEYS[2]) >= tonumber(ARGV[3]) then
                return 0
            end
            redis.call('ZADD', KEYS[1], ARGV[4], ARGV[5])
            redis.call('ZADD', KEYS[2], ARGV[4], ARGV[5])
            redis.call('EXPIRE', KEYS[1], ARGV[6] * 2)
            redis.call('EXPIRE', KEYS[2], ARGV[6] * 2)
            return 1
            """,
        )
        acquired = script(
            keys=[global_slots, owner_slots],
            args=[now, settings.total_concurrency, settings.owner_concurrency, expires_at, token, settings.slot_lease_secs],
        )
        if int(acquired or 0) != 1:
            return None
        return VideoAnalysisExecutionLease(token=token, owner_key=owner_key)

    def renew_execution(self, lease: VideoAnalysisExecutionLease) -> bool:
        settings = self.settings
        prefix = settings.queue_name
        now = int(time.time())
        expires_at = now + settings.slot_lease_secs
        script = self._script(
            "renew_execution",
            """
            if not redis.call('ZSCORE', KEYS[1], ARGV[2]) then
                return 0
            end
            redis.call('ZADD', KEYS[1], ARGV[1], ARGV[2])
            redis.call('ZADD', KEYS[2], ARGV[1], ARGV[2])
            return 1
            """,
        )
        result = script(
            keys=[f"{prefix}:slots:global", f"{prefix}:slots:owner:{lease.owner_key}"],
            args=[expires_at, lease.token],
        )
        return int(result or 0) == 1

    def release_execution(self, lease: VideoAnalysisExecutionLease) -> None:
        settings = self.settings
        prefix = settings.queue_name
        try:
            pipe = self._client().pipeline(transaction=True)
            pipe.zrem(f"{prefix}:slots:global", lease.token)
            pipe.zrem(f"{prefix}:slots:owner:{lease.owner_key}", lease.token)
            pipe.execute()
        except Exception:
            pass

    def touch_worker(self, worker_id: str, *, ttl_secs: int = 30) -> None:
        normalized = _clean(worker_id)
        if not normalized:
            return
        ttl = max(15, int(ttl_secs))
        expires_at = int(time.time()) + ttl
        pipe = self._client().pipeline(transaction=False)
        pipe.zadd(self._worker_key(), {normalized: expires_at})
        pipe.expire(self._worker_key(), ttl * 2)
        pipe.execute()

    def forget_worker(self, worker_id: str) -> None:
        normalized = _clean(worker_id)
        if normalized:
            self._client().zrem(self._worker_key(), normalized)

    def active_worker_count(self) -> int:
        now = int(time.time())
        client = self._client()
        client.zremrangebyscore(self._worker_key(), "-inf", now)
        return int(client.zcard(self._worker_key()) or 0)

    def snapshot(self) -> dict[str, Any]:
        settings = self.settings
        if not settings.enabled:
            return {"enabled": False}
        client = self._client()
        now = int(time.time())
        global_key = f"{settings.queue_name}:slots:global"
        client.zremrangebyscore(global_key, "-inf", now)
        groups = client.xinfo_groups(settings.queue_name)
        group = next((item for item in groups if item.get("name") == self.GROUP_NAME), {})
        pending = int(group.get("pending") or 0)
        lag_value = group.get("lag")
        lag = int(lag_value) if lag_value is not None else max(0, int(client.xlen(settings.queue_name) or 0) - pending)
        return {
            "enabled": True,
            "queue": settings.queue_name,
            "lag": lag,
            "pending": pending,
            "active": int(client.zcard(global_key) or 0),
            "activeWorkers": self.active_worker_count(),
            "workerConcurrency": settings.worker_concurrency,
            "totalConcurrency": settings.total_concurrency,
            "ownerConcurrency": settings.owner_concurrency,
            "ownerPendingLimit": settings.owner_pending_limit,
            "scheduledRetries": int(client.zcard(self._retry_key()) or 0),
            "deadLetters": int(client.xlen(self._dead_letter_key()) or 0),
            "maxRetries": settings.max_retries,
        }


video_analysis_queue_service = RedisVideoAnalysisQueue()
