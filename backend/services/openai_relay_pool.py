from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from collections.abc import Iterator as IteratorABC, Mapping
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import HTTPException

from services.config import config


_CURRENT_RELAY_ACCOUNT: ContextVar["RelayAccount | None"] = ContextVar("current_relay_account", default=None)
_POOL_LOCK = threading.Lock()
_LOCAL_INFLIGHT: dict[str, dict[str, float]] = {}
_LOCAL_COOLDOWNS: dict[str, float] = {}
_LOCAL_ROTATION_INDEX = 0
_REDIS_CLIENTS: dict[str, Any] = {}
_REDIS_DISABLED_UNTIL: dict[str, float] = {}
_REDIS_SCRIPTS: dict[str, Any] = {}


@dataclass(frozen=True)
class RelayAccount:
    id: str
    api_key: str
    base_url: str
    name: str = ""
    max_concurrency: int = 1


class RelayLease:
    def __init__(self, account: RelayAccount, release_callback: Callable[[bool, Exception | None], None]) -> None:
        self.account = account
        self._release_callback = release_callback
        self._released = False

    def release(self, success: bool = True, exc: Exception | None = None) -> None:
        if self._released:
            return
        self._released = True
        self._release_callback(success, exc)


class RelaySubmittedHTTPException(HTTPException):
    pass


def current_relay_account() -> RelayAccount | None:
    return _CURRENT_RELAY_ACCOUNT.get()


def _clean(value: object, default: str = "") -> str:
    text = str(value if value is not None else default).strip()
    return text or default


def _positive_int(value: object, default: int, minimum: int = 1) -> int:
    try:
        normalized = int(value)
    except (OverflowError, TypeError, ValueError):
        normalized = default
    return max(minimum, normalized)


def _bool(value: object, default: bool = False) -> bool:
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
        return default
    if value is None:
        return default
    return bool(value)


def _normalize_api_keys(relay_settings: Mapping[str, object]) -> list[RelayAccount]:
    base_url = _clean(relay_settings.get("base_url")).rstrip("/")
    legacy_api_key = _clean(relay_settings.get("api_key"))
    raw_api_keys = relay_settings.get("api_keys")
    api_key_concurrency = _positive_int(
        relay_settings.get("api_key_concurrency"),
        1,
        1,
    )
    normalized: list[RelayAccount] = []
    seen: set[str] = set()

    def add(api_key: object, name: object = "", base_url_override: object = "", max_concurrency: object = None) -> None:
        key = _clean(api_key)
        if not key or key in seen:
            return
        seen.add(key)
        account_base_url = _clean(base_url_override, base_url).rstrip("/") or base_url
        account_id = hashlib.sha256(f"{account_base_url}|{key}".encode("utf-8")).hexdigest()[:16]
        normalized.append(
            RelayAccount(
                id=account_id,
                api_key=key,
                base_url=account_base_url,
                name=_clean(name),
                max_concurrency=_positive_int(
                    max_concurrency if max_concurrency is not None else api_key_concurrency,
                    api_key_concurrency,
                    1,
                ),
            )
        )

    add(legacy_api_key, name="legacy")
    if isinstance(raw_api_keys, str):
        raw_api_keys = raw_api_keys.replace("\r", "\n").replace(";", "\n").replace(",", "\n").split("\n")
    if isinstance(raw_api_keys, (list, tuple, set)):
        for index, item in enumerate(raw_api_keys, start=1):
            if isinstance(item, dict):
                add(
                    item.get("api_key") or item.get("key") or item.get("value"),
                    name=item.get("name") or f"relay-{index}",
                    base_url_override=item.get("base_url") or base_url,
                    max_concurrency=item.get("max_concurrency"),
                )
            else:
                add(item, name=f"relay-{index}")
    return normalized


def _relay_pool_enabled(relay_settings: Mapping[str, object]) -> bool:
    return _bool(relay_settings.get("api_key_pool_distributed"), False)


def _redis_queue_settings() -> dict[str, object]:
    return config.get_image_task_queue_settings()


def _redis_pool_identity(relay_settings: Mapping[str, object]) -> str:
    base_url = _clean(relay_settings.get("base_url")).rstrip("/")
    return hashlib.sha256(base_url.encode("utf-8")).hexdigest()[:12]


def _redis_client(relay_settings: Mapping[str, object]) -> Any | None:
    if not _relay_pool_enabled(relay_settings):
        return None
    queue_settings = _redis_queue_settings()
    redis_url = _clean(queue_settings.get("redis_url"))
    if not redis_url:
        return None
    now = time.monotonic()
    with _POOL_LOCK:
        disabled_until = _REDIS_DISABLED_UNTIL.get(redis_url, 0.0)
        if now < disabled_until:
            return None
        cached = _REDIS_CLIENTS.get(redis_url)
        if cached is not None:
            return cached
    try:
        import redis

        client = redis.Redis.from_url(
            redis_url,
            decode_responses=True,
            protocol=2,
            socket_connect_timeout=0.5,
            socket_timeout=0.5,
            health_check_interval=30,
        )
        client.ping()
    except Exception:
        with _POOL_LOCK:
            _REDIS_DISABLED_UNTIL[redis_url] = now + 30.0
        return None
    with _POOL_LOCK:
        _REDIS_CLIENTS[redis_url] = client
    return client


def _redis_script(client: Any, redis_url: str) -> Any:
    with _POOL_LOCK:
        script = _REDIS_SCRIPTS.get(redis_url)
        if script is not None:
            return script
    script = client.register_script(
        """
        redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[1])
        local cooldown_until = tonumber(redis.call('GET', KEYS[2]) or '0')
        if cooldown_until > tonumber(ARGV[1]) then
            return 0
        end
        if redis.call('ZCARD', KEYS[1]) < tonumber(ARGV[2]) then
            redis.call('ZADD', KEYS[1], ARGV[3], ARGV[4])
            redis.call('EXPIRE', KEYS[1], ARGV[5])
            return 1
        end
        return 0
        """
    )
    with _POOL_LOCK:
        _REDIS_SCRIPTS[redis_url] = script
    return script


def _redis_keys(queue_name: str, pool_id: str, account_id: str) -> tuple[str, str]:
    prefix = f"{queue_name}:relay:{pool_id}:{account_id}"
    return f"{prefix}:slots", f"{prefix}:cooldown"


def _local_ordered_accounts(accounts: list[RelayAccount]) -> list[RelayAccount]:
    global _LOCAL_ROTATION_INDEX
    if not accounts:
        return []
    with _POOL_LOCK:
        start = _LOCAL_ROTATION_INDEX % len(accounts)
        _LOCAL_ROTATION_INDEX += 1
    return accounts[start:] + accounts[:start]


def _cleanup_local_state(now: float) -> None:
    expired_slots: list[str] = []
    for account_id, slots in _LOCAL_INFLIGHT.items():
        expired_tokens = [token for token, expires_at in slots.items() if expires_at <= now]
        for token in expired_tokens:
            slots.pop(token, None)
        if not slots:
            expired_slots.append(account_id)
    for account_id in expired_slots:
        _LOCAL_INFLIGHT.pop(account_id, None)
    expired_cooldowns = [account_id for account_id, until in _LOCAL_COOLDOWNS.items() if until <= now]
    for account_id in expired_cooldowns:
        _LOCAL_COOLDOWNS.pop(account_id, None)


def _local_acquire(relay_settings: Mapping[str, object], account: RelayAccount, lease_secs: int) -> RelayLease | None:
    now = time.time()
    token = uuid.uuid4().hex
    with _POOL_LOCK:
        _cleanup_local_state(now)
        cooldown_until = _LOCAL_COOLDOWNS.get(account.id, 0.0)
        if cooldown_until > now:
            return None
        slots = _LOCAL_INFLIGHT.setdefault(account.id, {})
        if len(slots) >= account.max_concurrency:
            return None
        slots[token] = now + lease_secs

    def release(success: bool, exc: Exception | None) -> None:
        cooldown_secs = _relay_cooldown_secs(relay_settings, exc) if not success else 0
        with _POOL_LOCK:
            current_slots = _LOCAL_INFLIGHT.get(account.id)
            if current_slots is not None:
                current_slots.pop(token, None)
                if not current_slots:
                    _LOCAL_INFLIGHT.pop(account.id, None)
            if cooldown_secs > 0:
                _LOCAL_COOLDOWNS[account.id] = time.time() + cooldown_secs

    return RelayLease(account, release)


def _redis_acquire(
    relay_settings: Mapping[str, object],
    client: Any,
    account: RelayAccount,
    lease_secs: int,
) -> RelayLease | None:
    if client is None:
        return None
    queue_settings = _redis_queue_settings()
    redis_url = _clean(queue_settings.get("redis_url"))
    queue_name = _clean(queue_settings.get("queue_name"), "ai_image_tasks")
    pool_id = _redis_pool_identity(relay_settings)
    slot_key, cooldown_key = _redis_keys(queue_name, pool_id, account.id)
    token = uuid.uuid4().hex
    script = _redis_script(client, redis_url)
    now = int(time.time())
    try:
        result = script(
            keys=[slot_key, cooldown_key],
            args=[now, account.max_concurrency, now + lease_secs, token, lease_secs],
        )
    except Exception:
        with _POOL_LOCK:
            _REDIS_DISABLED_UNTIL[redis_url] = time.monotonic() + 30.0
        return None
    if int(result or 0) != 1:
        return None

    def release(success: bool, exc: Exception | None) -> None:
        cooldown_secs = _relay_cooldown_secs(relay_settings, exc) if not success else 0
        try:
            client.zrem(slot_key, token)
            if cooldown_secs > 0:
                client.set(cooldown_key, int(time.time()) + cooldown_secs, ex=max(cooldown_secs, lease_secs))
        except Exception:
            with _POOL_LOCK:
                _REDIS_DISABLED_UNTIL[redis_url] = time.monotonic() + 30.0

    return RelayLease(account, release)


def _relay_cooldown_secs(relay_settings: Mapping[str, object], exc: Exception | None) -> int:
    default = _positive_int(relay_settings.get("api_key_pool_cooldown_secs"), 60, 1)
    if not isinstance(exc, HTTPException):
        return 0
    status = int(getattr(exc, "status_code", 500) or 500)
    if status in {401, 403}:
        return max(default, 600)
    if status in {429, 503, 529}:
        return default
    detail_text = _relay_error_text(exc)
    if status in {408, 409, 425, 500, 502, 504} and any(
        marker in detail_text
        for marker in ("rate limit", "rate-limited", "too many requests", "queue", "busy", "limit", "overloaded", "排队", "限流", "繁忙")
    ):
        return default
    return 0


def _relay_error_text(exc: HTTPException) -> str:
    try:
        detail = json.dumps(exc.detail, ensure_ascii=False)
    except Exception:
        detail = str(exc.detail)
    return detail.lower()


def _should_retry_with_next_key(exc: HTTPException) -> bool:
    if isinstance(exc, RelaySubmittedHTTPException):
        return False
    status = int(getattr(exc, "status_code", 500) or 500)
    if status in {401, 403, 429, 503, 529}:
        return True
    if status in {408, 409, 425, 500, 502, 504}:
        detail_text = _relay_error_text(exc)
        return any(
            marker in detail_text
            for marker in ("rate limit", "rate-limited", "too many requests", "queue", "busy", "limit", "overloaded", "排队", "限流", "繁忙")
        )
    return False


def _wrap_iterator_with_lease(iterator: IteratorABC, lease: RelayLease) -> IteratorABC:
    error: Exception | None = None
    try:
        for item in iterator:
            yield item
    except Exception as exc:  # pragma: no cover - delegated to caller
        error = exc
        raise
    finally:
        lease.release(error is None, error if isinstance(error, HTTPException) else None)


def acquire_relay_lease(relay_settings: Mapping[str, object], *, excluded_account_ids: set[str] | None = None) -> RelayLease | None:
    accounts = [
        account
        for account in _normalize_api_keys(relay_settings)
        if account.id not in set(excluded_account_ids or set())
    ]
    if not accounts:
        return None
    lease_secs = _positive_int(relay_settings.get("api_key_pool_lease_secs"), 600, 60)
    deadline = time.monotonic() + _positive_int(relay_settings.get("api_key_pool_acquire_timeout_secs"), 5, 1)
    queue_settings = _redis_queue_settings()
    redis_url = _clean(queue_settings.get("redis_url"))
    redis_client = _redis_client(relay_settings)
    while time.monotonic() <= deadline:
        if redis_client is not None and redis_url and time.monotonic() < _REDIS_DISABLED_UNTIL.get(redis_url, 0.0):
            redis_client = None
        ordered_accounts = _local_ordered_accounts(accounts)
        for account in ordered_accounts:
            if redis_client is not None:
                lease = _redis_acquire(relay_settings, redis_client, account, lease_secs)
                if redis_url and time.monotonic() < _REDIS_DISABLED_UNTIL.get(redis_url, 0.0):
                    redis_client = None
                    lease = None
            else:
                lease = _local_acquire(relay_settings, account, lease_secs)
            if lease is not None:
                return lease
        time.sleep(0.1)
    return None


def run_with_relay_pool(relay_settings: Mapping[str, object], operation: str, action: Callable[[], Any]) -> Any:
    if not _bool(relay_settings.get("enabled"), False) or not _clean(relay_settings.get("base_url")):
        return action()
    accounts = _normalize_api_keys(relay_settings)
    if not accounts:
        return action()
    max_attempts = min(
        len(accounts),
        _positive_int(relay_settings.get("api_key_pool_max_attempts"), len(accounts), 1),
    )
    excluded_account_ids: set[str] = set()
    last_exc: HTTPException | None = None
    for _attempt in range(max_attempts):
        lease = acquire_relay_lease(relay_settings, excluded_account_ids=excluded_account_ids)
        if lease is None:
            break
        token = _CURRENT_RELAY_ACCOUNT.set(lease.account)
        try:
            result = action()
        except HTTPException as exc:
            last_exc = exc
            lease.release(False, exc)
            _CURRENT_RELAY_ACCOUNT.reset(token)
            if _should_retry_with_next_key(exc):
                excluded_account_ids.add(lease.account.id)
                continue
            raise
        except Exception:
            lease.release(False, None)
            _CURRENT_RELAY_ACCOUNT.reset(token)
            raise
        else:
            _CURRENT_RELAY_ACCOUNT.reset(token)
            if isinstance(result, IteratorABC):
                return _wrap_iterator_with_lease(result, lease)
            lease.release(True, None)
            return result
    if last_exc is not None:
        raise last_exc
    raise HTTPException(status_code=429, detail={"error": f"{operation} is waiting for a free relay api key"})
