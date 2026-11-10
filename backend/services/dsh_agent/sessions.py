from __future__ import annotations

import atexit
from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
from pathlib import Path
import re
import threading
import time
from typing import Any, Callable


def owner_root(data_root: Path, owner_id: str) -> Path:
    if not owner_id.strip():
        raise ValueError("authenticated owner is required")
    return data_root / "users" / hashlib.sha256(owner_id.encode("utf-8")).hexdigest()


def user_home(home_root: Path, owner_id: str) -> Path:
    return home_root / "users" / hashlib.sha256(owner_id.encode("utf-8")).hexdigest()


def validate_session_id(session_id: str) -> None:
    if not re.fullmatch(r"session-[a-f0-9]{32}", session_id):
        raise ValueError("invalid DSH session id")


@dataclass
class RuntimeEntry:
    harness: Any
    session: Any = None
    initialized: bool = False
    busy: bool = True
    last_used: float = field(default_factory=time.monotonic)


class RuntimePool:
    """Bounded per-conversation runtimes; never share a live session between users."""

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str, str], RuntimeEntry] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._reaper: threading.Thread | None = None
        self.idle_secs = 180.0

    @staticmethod
    def _close(entry: RuntimeEntry) -> None:
        try:
            entry.harness.close()
        except Exception:
            pass

    @contextmanager
    def borrow(self, key: tuple[str, str, str], factory: Callable[[], Any], *, limit: int, idle_secs: float):
        stale = []
        with self._lock:
            self.idle_secs = max(10, idle_secs)
            related = [(k, e) for k, e in self._entries.items() if k[:2] == key[:2]]
            if any(e.busy for _, e in related):
                raise RuntimeError("DSH session is already processing")
            entry = self._entries.get(key)
            if entry is not None:
                entry.busy = True
            else:
                evicted = related
                if not evicted and len(self._entries) >= max(1, limit):
                    idle = [(k, e) for k, e in self._entries.items() if not e.busy]
                    if not idle:
                        raise RuntimeError("DSH runtime capacity reached")
                    evicted = [min(idle, key=lambda item: item[1].last_used)]
                entry = RuntimeEntry(factory())
                # A rotated key/model must release the previous runtime's session write lease.
                for old_key, old_entry in evicted:
                    del self._entries[old_key]
                    stale.append(old_entry)
                self._entries[key] = entry
            if self._reaper is None or not self._reaper.is_alive():
                self._stop.clear()
                self._reaper = threading.Thread(target=self._reap, name="dsh-runtime-idle-reaper", daemon=True)
                self._reaper.start()
        for old_entry in stale:
            self._close(old_entry)
        try:
            yield entry
        except BaseException:
            with self._lock:
                if self._entries.get(key) is entry:
                    del self._entries[key]
            self._close(entry)
            raise
        else:
            with self._lock:
                entry.busy = False
                entry.last_used = time.monotonic()

    def _reap(self) -> None:
        while not self._stop.wait(5):
            stale = []
            with self._lock:
                for key, entry in list(self._entries.items()):
                    if not entry.busy and time.monotonic() - entry.last_used >= self.idle_secs:
                        stale.append(self._entries.pop(key))
            for entry in stale:
                self._close(entry)

    def discard(self, session_id: str) -> None:
        entries = []
        with self._lock:
            for key, entry in list(self._entries.items()):
                if key[1] == session_id:
                    if entry.busy:
                        raise RuntimeError("conversation is processing")
                    entries.append(self._entries.pop(key))
        for entry in entries:
            self._close(entry)

    def close(self) -> None:
        self._stop.set()
        with self._lock:
            entries = list(self._entries.values())
            self._entries.clear()
        for entry in entries:
            self._close(entry)
        if self._reaper is not None:
            self._reaper.join(timeout=1)


runtime_pool = RuntimePool()
atexit.register(runtime_pool.close)
