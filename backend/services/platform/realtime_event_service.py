from __future__ import annotations

import json
import queue
import threading
import time
import uuid
from collections.abc import Iterator, Mapping
from typing import Any


class RealtimeEventService:
    """Process-local SSE hub for lightweight user-facing state updates."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._subscribers: dict[str, tuple[str, queue.Queue[dict[str, Any]]]] = {}
        self._sequence = 0

    @staticmethod
    def _owner_id(identity: Mapping[str, Any] | None) -> str:
        if not identity:
            return ""
        return str(identity.get("id") or identity.get("username") or "").strip()

    def subscribe(self, identity: Mapping[str, Any] | None) -> tuple[str, queue.Queue[dict[str, Any]]]:
        token = uuid.uuid4().hex
        subscriber = queue.Queue(maxsize=128)
        with self._lock:
            self._subscribers[token] = (self._owner_id(identity), subscriber)
        return token, subscriber

    def unsubscribe(self, token: str) -> None:
        with self._lock:
            self._subscribers.pop(token, None)

    def publish(self, event_type: str, payload: Mapping[str, Any], *, owner_id: str = "") -> None:
        clean_type = str(event_type or "message").strip() or "message"
        target_owner = str(owner_id or "").strip()
        with self._lock:
            self._sequence += 1
            envelope = {
                "id": str(self._sequence),
                "event": clean_type,
                "data": dict(payload),
                "created_at": time.time(),
            }
            subscribers = list(self._subscribers.values())
        for subscriber_owner, subscriber in subscribers:
            if target_owner and subscriber_owner != target_owner:
                continue
            try:
                subscriber.put_nowait(envelope)
            except queue.Full:
                try:
                    subscriber.get_nowait()
                except queue.Empty:
                    pass
                try:
                    subscriber.put_nowait(envelope)
                except queue.Full:
                    pass

    def stream(self, identity: Mapping[str, Any] | None) -> Iterator[str]:
        token, subscriber = self.subscribe(identity)
        try:
            yield "retry: 5000\n\n"
            while True:
                try:
                    envelope = subscriber.get(timeout=15)
                except queue.Empty:
                    yield ": heartbeat\n\n"
                    continue
                yield (
                    f"id: {envelope['id']}\n"
                    f"event: {envelope['event']}\n"
                    f"data: {json.dumps(envelope['data'], ensure_ascii=False, separators=(',', ':'))}\n\n"
                )
        finally:
            self.unsubscribe(token)


realtime_event_service = RealtimeEventService()
