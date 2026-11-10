from __future__ import annotations

import json
import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Mapping
from urllib.parse import urlsplit


_MAX_BODY_BYTES = 8 * 1024
_MAX_RESULTS = 5
_MAX_QUERY_CHARS = 1000
_PROCESS_API_KEY = secrets.token_urlsafe(32)
_bridge_lock = threading.Lock()
_bridge: SearchBridge | None = None


def _load_web_search():
    import services.ecommerce.cow_agent_runtime_service  # noqa: F401

    from services.ecommerce.cow_agent_extended_tools import load_admin_tool_environment

    load_admin_tool_environment()
    from agent.tools.web_search import web_search

    return web_search


def search_available() -> bool:
    if str(os.getenv("DSH_AGENT_SEARCH_ENABLED", "true")).strip().lower() in {
        "0", "false", "no", "off",
    }:
        return False
    try:
        return bool(_load_web_search().configured_providers())
    except Exception:
        return False


class SearchBridge:
    def __init__(self, search_tool: Any = None) -> None:
        self._search_tool = search_tool
        self._search_slots = threading.BoundedSemaphore(4)
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler_type())
        self._server.daemon_threads = True
        self._server.block_on_close = False
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="dsh-search-bridge",
            daemon=True,
        )
        self._thread.start()
        self._closed = False

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    @property
    def api_key(self) -> str:
        return _PROCESS_API_KEY

    def _handler_type(self):
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def setup(self) -> None:
                super().setup()
                self.connection.settimeout(10)

            def log_message(self, _format: str, *_args: object) -> None:
                return

            def do_POST(self) -> None:
                if self.path != "/search":
                    self._respond(404, {"error": "Not found."})
                    return
                self._search()

            def do_GET(self) -> None:
                self._reject_method()

            def do_HEAD(self) -> None:
                self._reject_method()

            def do_PUT(self) -> None:
                self._reject_method()

            def do_PATCH(self) -> None:
                self._reject_method()

            def do_DELETE(self) -> None:
                self._reject_method()

            def do_OPTIONS(self) -> None:
                self._reject_method()

            def do_TRACE(self) -> None:
                self._reject_method()

            def _reject_method(self) -> None:
                if self.path != "/search":
                    self._respond(404, {"error": "Not found."})
                else:
                    self._respond(405, {"error": "Method not allowed."})

            def _search(self) -> None:
                authorization = self.headers.get("Authorization", "")
                scheme, _, credential = authorization.partition(" ")
                supplied_key = credential.encode("utf-8") if scheme.lower() == "bearer" else b""
                if not secrets.compare_digest(supplied_key, bridge.api_key.encode("ascii")):
                    self._respond(401, {"error": "Unauthorized."})
                    return
                if self.headers.get("Transfer-Encoding"):
                    self._respond(400, {"error": "Invalid request body."})
                    return
                try:
                    length = int(self.headers.get("Content-Length", ""))
                except (TypeError, ValueError):
                    self._respond(400, {"error": "Invalid request body."})
                    return
                if length < 0 or length > _MAX_BODY_BYTES:
                    self._respond(413, {"error": "Request body exceeds 8 KB."})
                    return
                try:
                    payload = json.loads(self.rfile.read(length))
                except (json.JSONDecodeError, UnicodeDecodeError, ValueError, RecursionError):
                    self._respond(400, {"error": "Invalid request body."})
                    return
                if not isinstance(payload, dict):
                    self._respond(400, {"error": "Invalid search request."})
                    return
                query = payload.get("query")
                count = payload.get("count")
                if (
                    not isinstance(query, str)
                    or not 1 <= len(query.strip()) <= _MAX_QUERY_CHARS
                    or not isinstance(count, int)
                    or isinstance(count, bool)
                    or not 1 <= count <= _MAX_RESULTS
                ):
                    self._respond(400, {"error": "query must be 1-1000 characters and count must be 1-5."})
                    return
                try:
                    query.encode("utf-8", errors="strict")
                except UnicodeEncodeError:
                    self._respond(400, {"error": "Invalid search request."})
                    return
                if not bridge._search_slots.acquire(blocking=False):
                    self._respond(429, {"error": "Search capacity reached; retry later."})
                    return
                try:
                    results = bridge._execute_search(query.strip(), count)
                except Exception:
                    self._respond(502, {"error": "Search failed."})
                else:
                    self._respond(200, {"results": results})
                finally:
                    bridge._search_slots.release()

            def _respond(self, status: int, payload: dict[str, object]) -> None:
                body = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("ascii")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

        return Handler

    def _execute_search(self, query: str, count: int) -> list[dict[str, object]]:
        tool = self._search_tool
        if tool is None:
            tool = _load_web_search().WebSearch()
        response = tool.execute({"query": query, "count": count})
        if getattr(response, "status", None) != "success":
            raise RuntimeError("Search provider failed")
        payload = getattr(response, "result", None)
        items = payload.get("results") if isinstance(payload, Mapping) else None
        if not isinstance(items, list):
            raise RuntimeError("Search provider returned an invalid response")

        results = []
        for item in items:
            if not isinstance(item, Mapping):
                continue
            url = item.get("url")
            if not isinstance(url, str) or not self._safe_url(url):
                continue
            results.append({
                "url": url[:2048],
                "title": self._text(item.get("title"), 300),
                "snippet": self._text(item.get("snippet"), 1000),
                "date": None,
            })
            if len(results) == count:
                break
        return results

    @staticmethod
    def _text(value: object, limit: int) -> str:
        return value[:limit] if isinstance(value, str) else ""

    @staticmethod
    def _safe_url(value: str) -> bool:
        if len(value) > 2048 or any(
            char.isspace() or char == "\\" or ord(char) < 32 or ord(char) == 127
            for char in value
        ):
            return False
        try:
            parsed = urlsplit(value)
            port = parsed.port
            return (
                parsed.scheme.lower() in {"http", "https"}
                and bool(parsed.hostname)
                and parsed.username is None
                and parsed.password is None
                and (port is None or 1 <= port <= 65535)
            )
        except ValueError:
            return False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)


def bridge_settings() -> dict[str, str]:
    if not search_available():
        return {"base_url": "", "api_key": ""}
    global _bridge
    with _bridge_lock:
        if _bridge is None:
            _bridge = SearchBridge()
        return {"base_url": _bridge.base_url, "api_key": _bridge.api_key}


def close_search_bridge() -> None:
    global _bridge
    with _bridge_lock:
        bridge, _bridge = _bridge, None
    if bridge is not None:
        bridge.close()
