from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Protocol


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class AgentMemoryItem:
    namespace: str
    key: str
    value: Any
    created_at: str
    updated_at: str


class AgentMemory(Protocol):
    def get(self, namespace: str, key: str, default: Any = None) -> Any:
        ...

    def set(self, namespace: str, key: str, value: Any) -> AgentMemoryItem:
        ...

    def delete(self, namespace: str, key: str) -> bool:
        ...

    def list(self, namespace: str) -> list[AgentMemoryItem]:
        ...


class InMemoryAgentMemory:
    """Thread-safe short-term memory with namespace isolation."""

    def __init__(self, *, max_items_per_namespace: int = 100) -> None:
        self.max_items_per_namespace = max(1, int(max_items_per_namespace))
        self._items: dict[str, dict[str, AgentMemoryItem]] = {}
        self._lock = RLock()

    def get(self, namespace: str, key: str, default: Any = None) -> Any:
        with self._lock:
            item = self._items.get(namespace, {}).get(key)
            return deepcopy(item.value) if item is not None else deepcopy(default)

    def set(self, namespace: str, key: str, value: Any) -> AgentMemoryItem:
        now = _utc_now()
        with self._lock:
            bucket = self._items.setdefault(namespace, {})
            previous = bucket.get(key)
            item = AgentMemoryItem(
                namespace=namespace,
                key=key,
                value=deepcopy(value),
                created_at=previous.created_at if previous is not None else now,
                updated_at=now,
            )
            bucket[key] = item
            while len(bucket) > self.max_items_per_namespace:
                oldest_key = min(bucket, key=lambda item_key: bucket[item_key].updated_at)
                bucket.pop(oldest_key, None)
            return deepcopy(item)

    def delete(self, namespace: str, key: str) -> bool:
        with self._lock:
            bucket = self._items.get(namespace)
            if not bucket or key not in bucket:
                return False
            del bucket[key]
            if not bucket:
                self._items.pop(namespace, None)
            return True

    def list(self, namespace: str) -> list[AgentMemoryItem]:
        with self._lock:
            items = self._items.get(namespace, {}).values()
            return deepcopy(sorted(items, key=lambda item: item.updated_at, reverse=True))
