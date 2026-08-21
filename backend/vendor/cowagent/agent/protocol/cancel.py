"""
Cancel token registry for aborting in-flight agent runs.

A user cancel (web Cancel button, /cancel command) sets a threading.Event
that the agent loop polls at safe checkpoints. Tokens are keyed by
request_id (preferred) and tracked under session_id as a fallback. Entries
are released after the run completes to keep the registry bounded.

No project deps — importable from any layer without circular imports.
"""

from __future__ import annotations

import threading
from typing import Dict, Optional


class AgentCancelledError(Exception):
    """Raised inside the agent loop when a stop has been requested.

    The agent stream executor catches this, injects a "[Interrupted]" note
    into the message history (preserving tool_use/tool_result integrity)
    and returns a partial response to the caller.
    """


class _CancelEntry:
    __slots__ = ("event", "session_id")

    def __init__(self, session_id: Optional[str]):
        self.event = threading.Event()
        self.session_id = session_id


class CancelTokenRegistry:
    """In-process registry mapping request_id -> cancel Event.

    Thread-safe. Singleton via module-level ``_registry``.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._by_request: Dict[str, _CancelEntry] = {}
        # session_id -> set of request_ids currently in flight (usually 1).
        self._by_session: Dict[str, set] = {}

    def register(self, request_id: str, session_id: Optional[str] = None) -> threading.Event:
        """Create (or return existing) cancel event for a request.

        Returns the threading.Event the caller should poll via ``is_set()``.
        """
        if not request_id:
            return threading.Event()
        with self._lock:
            entry = self._by_request.get(request_id)
            if entry is None:
                entry = _CancelEntry(session_id)
                self._by_request[request_id] = entry
                if session_id:
                    self._by_session.setdefault(session_id, set()).add(request_id)
            return entry.event

    def get_event(self, request_id: str) -> Optional[threading.Event]:
        if not request_id:
            return None
        with self._lock:
            entry = self._by_request.get(request_id)
            return entry.event if entry else None

    def cancel_request(self, request_id: str) -> bool:
        """Trigger cancel for a specific request. Returns True when matched."""
        if not request_id:
            return False
        with self._lock:
            entry = self._by_request.get(request_id)
        if entry is None:
            return False
        entry.event.set()
        return True

    def cancel_session(self, session_id: str) -> int:
        """Trigger cancel for every in-flight request of a session.

        Returns the number of requests cancelled (0 when nothing was running).
        """
        if not session_id:
            return 0
        with self._lock:
            request_ids = list(self._by_session.get(session_id, ()))
            entries = [self._by_request[r] for r in request_ids if r in self._by_request]
        for entry in entries:
            entry.event.set()
        return len(entries)

    def unregister(self, request_id: str) -> None:
        """Remove an entry once the agent run is done. Safe to call twice."""
        if not request_id:
            return
        with self._lock:
            entry = self._by_request.pop(request_id, None)
            if entry and entry.session_id:
                bucket = self._by_session.get(entry.session_id)
                if bucket is not None:
                    bucket.discard(request_id)
                    if not bucket:
                        self._by_session.pop(entry.session_id, None)

    def has_active(self, session_id: str) -> bool:
        if not session_id:
            return False
        with self._lock:
            bucket = self._by_session.get(session_id)
            return bool(bucket)


_registry = CancelTokenRegistry()


def get_cancel_registry() -> CancelTokenRegistry:
    """Module-level accessor for the singleton registry."""
    return _registry
