from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import secrets
import threading
from urllib.parse import urlsplit, urlunsplit

import httpx

from services.billing.chat_usage import capture_chat_response, chat_usage_scope
from services.canvas.canvas_ai_scheduler import CanvasAIScheduler, CanvasAISchedulerError
from services.dsh_agent.teams import TeamControl, MODEL_REQUEST_LIMIT, MODEL_WRAP_UP_AT, WRAP_UP_PROMPT
from utils.log import logger


@dataclass
class GatewayTurn:
    control: TeamControl
    base_url: str
    api_key: str
    model: str
    local_task_id: str
    cancel: threading.Event
    calls: int = 0
    lock: object = field(default_factory=threading.Lock)


class TeamGateway:
    """Loopback-only model boundary: child requests cannot bypass attribution or limits."""

    def __init__(self):
        self.secret = secrets.token_bytes(32)
        self.contexts = {}
        self.controls = {}
        self.lock = threading.Lock()
        self.scheduler = CanvasAIScheduler(
            redis_url=os.getenv("DSH_AGENT_REDIS_URL") or None,
            namespace=(os.getenv("DSH_AGENT_QUEUE_NAME") or "dsh_agent_jobs") + ":team_requests",
            total_limit=8, owner_limit=3, owner_pending_limit=4, acquire_timeout_secs=60, lease_secs=300,
        )
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def respond(self, status, value):
                data = json.dumps(value, ensure_ascii=True).encode("ascii")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                self.connection.settimeout(15)
                token = self.headers.get("Authorization", "").removeprefix("Bearer ") or self.headers.get("x-goog-api-key", "")
                with bridge.lock:
                    turn = bridge.contexts.get(token)
                    registered = token in bridge.controls
                if self.path == "/register" and registered:
                    try:
                        length = int(self.headers.get("Content-Length", "0"))
                        if not 0 < length <= 1000:
                            raise ValueError("invalid size")
                        port = json.loads(self.rfile.read(length))["port"]
                        if not isinstance(port, int) or not 1 <= port <= 65535:
                            raise ValueError("invalid port")
                        with bridge.lock:
                            bridge.controls[token] = port
                        return self.respond(200, {"ok": True})
                    except (KeyError, ValueError, TypeError):
                        return self.respond(400, {"error": "invalid registration"})
                if turn is None or turn.cancel.is_set():
                    return self.respond(403, {"error": "协作运行授权已结束"})
                slot = None
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if self.headers.get("Transfer-Encoding") or not 0 < length <= 12_000_000:
                        return self.respond(400, {"error": "请求大小无效"})
                    data = self.rfile.read(length)
                    body = json.loads(data)
                    if not isinstance(body, dict):
                        raise ValueError("请求格式无效")
                    if self.path == "/web-result":
                        with turn.lock:
                            turn.control.web_result(str(body.get("session_id") or ""), bool(body.get("failed")), str(body.get("error") or "")[:1000], str(body.get("name") or ""))
                        return self.respond(200, {"ok": True})
                    if self.path == "/policy":
                        with turn.lock:
                            try:
                                arguments = body.get("arguments")
                                if not isinstance(arguments, dict):
                                    raise ValueError("工具参数无效")
                                name = str(body.get("name") or "")
                                if turn.control.failure:
                                    raise ValueError(turn.control.failure)
                                if name in {"web_search", "web_fetch"}:
                                    turn.control.check_web(name, arguments, str(body.get("session_id") or ""))
                                    failures = turn.control.web[str(body.get("session_id") or "")].failures
                                    return self.respond(200, {"allowed": True, "retry_after_ms": min(2000, 250 * 2 ** failures) if failures else 0})
                                else:
                                    turn.control.check(name, arguments, captain=body.get("session_id") == turn.control.session_id)
                                return self.respond(200, {"allowed": True})
                            except ValueError as exc:
                                return self.respond(200, {"allowed": False, "error": str(exc)})
                    path = urlsplit(self.path)
                    allowed = path.path == "/v1/chat/completions" and body.get("model") == turn.model
                    allowed = allowed or path.path in {
                        f"/v1beta/models/{turn.model}:streamGenerateContent",
                        f"/v1beta/models/{turn.model}:generateContent",
                    }
                    if not allowed:
                        raise ValueError("不允许该模型或接口")
                    with turn.lock:
                        if turn.calls >= MODEL_REQUEST_LIMIT:
                            turn.control.failure = "本次协作已达到模型请求上限，已停止运行。请缩小任务范围后重新规划；已有结果已保留。"
                            raise ValueError(turn.control.failure)
                        if turn.control.failure:
                            raise ValueError(turn.control.failure)
                        turn.calls += 1
                        turn.control.model_calls = turn.calls
                        if turn.calls >= MODEL_WRAP_UP_AT:
                            if path.path == "/v1/chat/completions":
                                body.setdefault("messages", []).append({"role": "system", "content": WRAP_UP_PROMPT})
                            else:
                                body.setdefault("systemInstruction", {}).setdefault("parts", []).append({"text": WRAP_UP_PROMPT})
                            data = json.dumps(body).encode("utf-8")
                    slot = bridge.scheduler.acquire(turn.control.owner_id, "dsh_team_request", cancel_event=turn.cancel)
                    if turn.cancel.is_set():
                        return self.respond(403, {"error": "运行已停止"})
                    parsed = urlsplit(turn.base_url.rstrip("/"))
                    prefix = parsed.path.rstrip("/")
                    if prefix.endswith(("/v1", "/v1beta")):
                        prefix = prefix.rsplit("/", 1)[0]
                    url = urlunsplit((parsed.scheme, parsed.netloc, prefix + path.path, path.query, ""))
                    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {turn.api_key}"}
                    if path.path.startswith("/v1beta/"):
                        headers["x-goog-api-key"] = turn.api_key
                    # Never retry a request after forwarding: the upstream may have charged it.
                    with httpx.Client(timeout=httpx.Timeout(120, connect=10), trust_env=False, follow_redirects=False) as client:
                        with client.stream("POST", url, headers=headers, content=data) as response:
                            with chat_usage_scope(turn.control.owner_id, "dsh_agent_teams", turn.local_task_id):
                                capture_chat_response(response, model=turn.model, api_key=turn.api_key)
                            self.send_response(response.status_code)
                            self.send_header("Content-Type", response.headers.get("Content-Type", "application/json"))
                            self.send_header("Cache-Control", "no-store")
                            self.send_header("Connection", "close")
                            self.end_headers()
                            for chunk in response.iter_bytes():
                                if turn.cancel.is_set():
                                    break
                                self.wfile.write(chunk)
                                self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                except (ValueError, CanvasAISchedulerError) as exc:
                    self.respond(429 if isinstance(exc, CanvasAISchedulerError) else 400, {"error": str(exc)[:240]})
                except Exception as exc:
                    logger.warning({"event": "dsh_team_gateway_failed", "error_type": type(exc).__name__})
                    self.close_connection = True
                finally:
                    if slot:
                        bridge.scheduler.release(slot, operation="dsh_team_request")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, name="dsh-team-gateway", daemon=True)
        self.thread.start()

    def settings(self, owner_id, session_id):
        token = hmac.new(self.secret, json.dumps([owner_id, session_id]).encode(), hashlib.sha256).hexdigest()
        with self.lock:
            self.controls.setdefault(token, None)
        return {"DSH_TEAMS_GATEWAY": f"http://127.0.0.1:{self.server.server_port}", "DSH_TEAMS_TOKEN": token}

    def stop(self, owner_id, session_id):
        token = self.settings(owner_id, session_id)["DSH_TEAMS_TOKEN"]
        with self.lock:
            port = self.controls.get(token)
        if port:
            try:
                with httpx.Client(timeout=5, trust_env=False) as client:
                    client.post(f"http://127.0.0.1:{port}/stop", headers={"Authorization": f"Bearer {token}"})
            except httpx.HTTPError:
                logger.warning({"event": "dsh_team_stop_control_unavailable"})

    @contextmanager
    def activate(self, turn: GatewayTurn):
        token = self.settings(turn.control.owner_id, turn.control.session_id)["DSH_TEAMS_TOKEN"]
        with self.lock:
            if token in self.contexts:
                raise RuntimeError("协作会话已在执行")
            self.contexts[token] = turn
        try:
            yield
        finally:
            with self.lock:
                self.contexts.pop(token, None)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


_gateway = None
_lock = threading.Lock()


def team_gateway() -> TeamGateway:
    global _gateway
    with _lock:
        if _gateway is None:
            _gateway = TeamGateway()
        return _gateway


def close_team_gateway():
    global _gateway
    with _lock:
        gateway, _gateway = _gateway, None
    if gateway:
        gateway.close()
