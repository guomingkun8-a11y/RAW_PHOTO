from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Generic, TypeVar

K = TypeVar("K")
V = TypeVar("V")


class TTLCache(Generic[K, V]):
    def __init__(self, ttl_seconds: float, max_items: int = 128):
        self.ttl_seconds = max(0.0, float(ttl_seconds))
        self.max_items = max(1, int(max_items))
        self._lock = threading.RLock()
        self._items: dict[K, tuple[float, V]] = {}

    def get(self, key: K) -> V | None:
        now = time.monotonic()
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            expires_at, value = item
            if expires_at <= now:
                self._items.pop(key, None)
                return None
            return value

    def set(self, key: K, value: V) -> V:
        now = time.monotonic()
        with self._lock:
            self._items[key] = (now + self.ttl_seconds, value)
            self._prune_locked(now)
        return value

    def get_or_set(self, key: K, builder: Callable[[], V]) -> V:
        cached = self.get(key)
        if cached is not None:
            return cached

        value = builder()
        now = time.monotonic()
        with self._lock:
            item = self._items.get(key)
            if item is not None:
                expires_at, cached_value = item
                if expires_at > now:
                    return cached_value
            self._items[key] = (now + self.ttl_seconds, value)
            self._prune_locked(now)
        return value

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def _prune_locked(self, now: float) -> None:
        expired = [key for key, (expires_at, _value) in self._items.items() if expires_at <= now]
        for key in expired:
            self._items.pop(key, None)

        if len(self._items) <= self.max_items:
            return

        overflow = len(self._items) - self.max_items
        for key in list(self._items.keys())[:overflow]:
            self._items.pop(key, None)
