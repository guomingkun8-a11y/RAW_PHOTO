from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import re
from threading import Lock
from typing import Any, Mapping
from urllib.parse import urlparse
from uuid import uuid4

from dotenv import dotenv_values, set_key, unset_key

from agent.evolution.backup import restore_backup
from agent.tools.base_tool import BaseTool, ToolResult
from agent.tools.bash.bash import Bash
from agent.tools.browser.browser_service import BrowserService
from agent.tools.browser.browser_tool import BrowserTool
from agent.tools.edit.edit import Edit
from agent.tools.env_config.env_config import EnvConfig
from agent.tools.ls.ls import Ls
from agent.tools.mcp.mcp_client import McpClient
from agent.tools.scheduler.scheduler_service import SchedulerService
from agent.tools.scheduler.scheduler_tool import SchedulerTool
from agent.tools.scheduler.task_store import TaskStore
from agent.tools.search_files.search_files import SearchFiles
from agent.tools.send.send import Send
from agent.tools.web_fetch.web_fetch import WebFetch
from agent.tools.web_search.web_search import WebSearch
from agent.tools.write.write import Write
from bridge.context import Context, ContextType


_ADMIN_ENV_KEYS = {
    "BOCHA_API_KEY",
    "GEMINI_API_KEY",
    "LINKAI_API_KEY",
    "OPENAI_API_BASE",
    "OPENAI_API_KEY",
    "QIANFAN_API_KEY",
    "SERPAPI_API_KEY",
    "SKILL_IMAGE_GENERATION_MODEL",
    "SKILL_IMAGE_GENERATION_PROVIDER",
    "ZHIPUAI_API_KEY",
}
_SCHEDULER_LOCK = Lock()
_SCHEDULER_SERVICES: dict[str, SchedulerService] = {}
_MCP_LOCK = Lock()
_MCP_CLIENTS: dict[tuple[str, str], McpClient] = {}
_MAX_WORKSPACE_WRITE_BYTES = 1024 * 1024
_MAX_SEND_FILE_BYTES = 32 * 1024 * 1024
_WEB_RESEARCH_FETCH_CANDIDATES = 8
_WEB_RESEARCH_SOURCE_LIMIT = 3
_WEB_RESEARCH_EXCERPT_CHARS = 7000
_WEB_RESEARCH_LOW_VALUE_DOMAINS = {
    "facebook.com", "instagram.com", "reddit.com", "tiktok.com", "x.com", "youtube.com",
    "csstats.gg", "scope.gg", "steamcommunity.com", "tracker.gg",
}


def _clean(value: object, default: str = "", limit: int = 12000) -> str:
    text = str(value if value is not None else default).strip()
    return (text or default)[:limit]


def _owner_key(owner_id: str) -> str:
    return hashlib.sha256(owner_id.encode("utf-8")).hexdigest()[:24]


def _is_admin(runtime) -> bool:
    return _clean(runtime.identity.get("role"), limit=40).lower() == "admin"


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _workspace_path(runtime, value: object, *, default: str = ".") -> Path:
    raw = _clean(value, default, 3000)
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = runtime.workspace / candidate
    path = candidate.resolve(strict=False)
    root = runtime.workspace.resolve()
    if not _within(path, root):
        raise PermissionError("path is outside this user's CowAgent workspace")
    return path


def _tool_state_root() -> Path:
    root = Path(os.environ.get("COW_DATA_DIR") or Path.cwd() / "data" / "cowagent_runtime")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _admin_env_path() -> Path:
    path = _tool_state_root() / "raw-admin-tools.env"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.touch()
    return path


def load_admin_tool_environment() -> None:
    for key, value in dotenv_values(_admin_env_path()).items():
        if key in _ADMIN_ENV_KEYS and value:
            os.environ.setdefault(key, str(value))


def _masked(value: object) -> str:
    text = _clean(value, limit=4000)
    if len(text) <= 10:
        return "***"
    return f"{text[:4]}***{text[-4:]}"


class NativeWorkspaceTool(BaseTool):
    """Run an upstream CowAgent tool after forcing file paths into one workspace."""

    def __init__(self, runtime, native: BaseTool, *, default_path: str = "", allow_url: bool = False) -> None:
        self.runtime = runtime
        self.native = native
        self.name = native.name
        self.description = native.description
        self.params = native.params
        self.cwd = str(runtime.workspace)
        self.default_path = default_path
        self.allow_url = allow_url

    def get_json_schema(self) -> dict:
        return self.native.get_json_schema()

    def execute(self, params: dict) -> ToolResult:
        args = dict(params or {})
        if self.name == "write" and len(str(args.get("content") or "").encode("utf-8")) > _MAX_WORKSPACE_WRITE_BYTES:
            return ToolResult.fail("Error: write content exceeds the 1MB workspace limit")
        if self.name == "edit" and len(str(args.get("newText") or "").encode("utf-8")) > _MAX_WORKSPACE_WRITE_BYTES:
            return ToolResult.fail("Error: edit content exceeds the 1MB workspace limit")
        raw_path = args.get("path")
        if self.allow_url and _clean(raw_path).lower().startswith(("http://", "https://")):
            return self.native.execute(args)
        if raw_path is not None or self.default_path:
            try:
                args["path"] = str(_workspace_path(self.runtime, raw_path, default=self.default_path or "."))
            except PermissionError as exc:
                return ToolResult.fail(f"Error: {exc}")
        result = self.native.execute(args)
        if self.name == "send" and result.status == "success":
            return self._publish_sent_file(result)
        return result

    def _publish_sent_file(self, result: ToolResult) -> ToolResult:
        item = result.result if isinstance(result.result, Mapping) else {}
        path_value = _clean(item.get("path"), limit=3000)
        if path_value.lower().startswith(("http://", "https://")):
            return result
        path = Path(path_value)
        if item.get("file_type") != "image" or not path.is_file():
            return result
        if path.stat().st_size > _MAX_SEND_FILE_BYTES:
            return ToolResult.fail("Error: file exceeds the 32MB send limit")
        try:
            from services.image.image_storage_service import image_storage_service

            stored = image_storage_service.save(
                path.read_bytes(),
                base_url=_clean(self.runtime.run.request.get("base_url"), limit=1000) or None,
                asset_type="cowagent-send",
                cleanup=False,
            )
            published = dict(item)
            published["url"] = stored.url
            published["path"] = str(path.relative_to(self.runtime.workspace))
            return ToolResult.success(published, result.ext_data)
        except Exception as exc:
            return ToolResult.fail(f"Error publishing file: {exc}")

    def close(self):
        try:
            self.native.close()
        except Exception:
            pass


class RawMemoryGetTool(BaseTool):
    """CowAgent-compatible memory_get backed by RAW's MySQL/Qdrant memory."""

    name = "memory_get"
    description = "Retrieve durable RAW user memories from MySQL and Qdrant."
    params = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Memory id, topic, or search text."},
            "num_lines": {"type": "integer", "description": "Maximum number of memory records."},
        },
        "required": ["path"],
    }

    def __init__(self, runtime) -> None:
        self.runtime = runtime

    def execute(self, args: dict) -> ToolResult:
        query = _clean((args or {}).get("path"), limit=3000)
        if not query:
            return ToolResult.fail("Error: path parameter is required")
        limit = max(1, min(20, int((args or {}).get("num_lines") or 8)))
        if query.lower() in {"memory", "memory.md", "memory/"}:
            query = ""
        items = self.runtime.search_long_term(query, limit=limit)
        return ToolResult.success({"storage": "mysql-qdrant", "items": items})


class RawBashTool(BaseTool):
    name = Bash.name
    description = Bash.description + " RAW restricts this tool to administrators and explicit command requests."
    params = Bash.params

    def __init__(self, runtime) -> None:
        self.runtime = runtime
        self.native = Bash({"cwd": str(runtime.workspace)})
        self.cwd = str(runtime.workspace)

    def execute(self, params: dict) -> ToolResult:
        if not _is_admin(self.runtime):
            return ToolResult.fail("Error: bash is restricted to RAW administrators")
        current = self.runtime.user_message.lower()
        explicit = ("bash", "shell", "command", "terminal", "script", "ffmpeg", "imagemagick", "命令", "终端", "脚本")
        if not any(marker in current for marker in explicit):
            return ToolResult.fail("Error: bash requires an explicit command request in the current user message")
        return self.native.execute(dict(params or {}))

    def close(self):
        self.native.close()


class RawBrowserTool(BrowserTool):
    """Use CowAgent's browser with a profile and service isolated per RAW user."""

    def __init__(self, runtime) -> None:
        self.runtime = runtime
        super().__init__({
            "cwd": str(runtime.workspace),
            "user_data_dir": str(runtime.workspace / "browser-profile"),
            "persistent": True,
            "headless": True,
            "allow_private_targets": False,
            "idle_timeout": 300,
        })

    def _get_service(self) -> BrowserService:
        if self._service is None:
            self._service = BrowserService(self.config)
        return self._service

    def execute(self, args: dict) -> ToolResult:
        if _clean(args.get("action"), limit=40).lower() == "navigate":
            url = _clean(args.get("url"), limit=3000)
            if not url.lower().startswith(("http://", "https://")):
                return ToolResult.fail("Error: RAW browser navigation only allows HTTP and HTTPS URLs")
            try:
                from agent.tools.utils.url_safety import validate_url_safe

                validate_url_safe(url)
            except ValueError as exc:
                return ToolResult.fail(f"Error: {exc}")
        return super().execute(args)

    def close(self):
        if self._service is not None:
            try:
                self._service.close()
            except Exception:
                pass


class RawWebSearchTool(WebSearch):
    """Search broadly and pre-read useful, diverse sources for the Agent."""

    def __init__(self, runtime) -> None:
        self.runtime = runtime

    @staticmethod
    def _should_expand_query(query: str, user_set_count: object) -> bool:
        if user_set_count or not query or len(query) > 48:
            return False
        narrow_markers = (
            "天气", "价格", "股价", "汇率", "航班", "几点", "时间", "地址", "电话", "下载",
            "官网", "最新", "今天", "昨日", "本周", "多少", "怎么", "如何", "为什么", "哪一个",
            "weather", "price", "stock", "exchange rate", "flight", "download", "official site", "latest",
        )
        return not any(marker in query.lower() for marker in narrow_markers)

    @staticmethod
    def _supplemental_query(query: str) -> str:
        if re.search(r"[\u3400-\u9fff]", query):
            return f"{query} 官方 介绍 历史 现状"
        return f"{query} official overview history"

    def execute(self, params: dict) -> ToolResult:
        args = dict(params or {})
        user_set_count = re.search(r"\d+\s*(?:条|个|项|篇|results?)", self.runtime.user_message, flags=re.I)
        if self.runtime._web_research_requested and not user_set_count:
            try:
                args["count"] = max(10, int(args.get("count") or 10))
            except (TypeError, ValueError):
                args["count"] = 10
        result = super().execute(args)
        if (
            result.status != "success"
            or not self.runtime._web_research_requested
            or not isinstance(result.result, Mapping)
        ):
            return result

        payload = dict(result.result)
        rows = [dict(item) for item in list(payload.get("results") or []) if isinstance(item, Mapping)]
        query = _clean(args.get("query"), limit=500)
        queries = [query] if query else []
        if rows and self._should_expand_query(query, user_set_count) and not self.is_cancelled():
            expanded_query = self._supplemental_query(query)
            expanded = super().execute({**args, "query": expanded_query, "count": 10})
            if expanded.status == "success" and isinstance(expanded.result, Mapping):
                queries.append(expanded_query)
                seen_urls = {_clean(item.get("url"), limit=3000) for item in rows}
                for item in list(expanded.result.get("results") or []):
                    if not isinstance(item, Mapping):
                        continue
                    url = _clean(item.get("url"), limit=3000)
                    if not url or url in seen_urls:
                        continue
                    seen_urls.add(url)
                    rows.append(dict(item))

        candidates: list[tuple[int, str]] = []
        seen_domains: set[str] = set()
        for index, item in enumerate(rows):
            url = _clean(item.get("url"), limit=3000)
            domain = (urlparse(url).hostname or "").lower().removeprefix("www.")
            if (
                not url.lower().startswith(("http://", "https://"))
                or not domain
                or domain in seen_domains
                or domain in _WEB_RESEARCH_LOW_VALUE_DOMAINS
                or "/agecheck/" in url.lower()
            ):
                continue
            seen_domains.add(domain)
            candidates.append((index, url))
            if len(candidates) >= _WEB_RESEARCH_FETCH_CANDIDATES:
                break

        def fetch(candidate: tuple[int, str]) -> tuple[int, str, str]:
            index, url = candidate
            fetched = WebFetch({"cwd": str(self.runtime.workspace)}).execute({"url": url})
            content = _clean(fetched.result, limit=_WEB_RESEARCH_EXCERPT_CHARS)
            return index, fetched.status, content

        outcomes: list[tuple[int, str, str]] = []
        if candidates and not self.is_cancelled():
            with ThreadPoolExecutor(max_workers=min(4, len(candidates)), thread_name_prefix="raw-web-read") as pool:
                outcomes = [future.result() for future in [pool.submit(fetch, item) for item in candidates]]

        selected = 0
        for index, status, content in sorted(outcomes):
            if status == "success" and content and selected < _WEB_RESEARCH_SOURCE_LIMIT:
                rows[index]["fetchStatus"] = "read"
                rows[index]["readableContent"] = content
                selected += 1
            elif status != "success":
                rows[index]["fetchStatus"] = "unreadable"
                rows[index]["fetchError"] = content[:500]

        payload["results"] = rows
        payload["count"] = len(rows)
        payload["queries"] = queries
        payload["prefetchedSourceCount"] = selected
        payload["researchInstructions"] = (
            "Use readableContent as source evidence, not just snippets. If fewer than two useful sources were "
            "pre-read, use web_fetch or browser on additional result URLs before answering. Cite only sources "
            "whose content supports the claim and include their full URLs."
        )
        return ToolResult.success(payload, result.ext_data)


class RawEnvConfigTool(BaseTool):
    name = EnvConfig.name
    description = "Manage RAW administrator-owned integration keys. Values are always masked and never returned in full."
    params = EnvConfig.params

    def __init__(self, runtime) -> None:
        self.runtime = runtime

    def execute(self, args: dict) -> ToolResult:
        if not _is_admin(self.runtime):
            return ToolResult.fail("Error: env_config is restricted to RAW administrators")
        action = _clean(args.get("action"), limit=20).lower()
        key = _clean(args.get("key"), limit=100).upper()
        path = _admin_env_path()
        values = {k: str(v) for k, v in dotenv_values(path).items() if k in _ADMIN_ENV_KEYS and v is not None}
        if action == "list":
            return ToolResult.success({
                "variables": [{"key": name, "configured": bool(values.get(name) or os.environ.get(name))} for name in sorted(_ADMIN_ENV_KEYS)],
            })
        if not key or key not in _ADMIN_ENV_KEYS:
            return ToolResult.fail("Error: key is not in RAW's integration allowlist")
        if action == "get":
            value = values.get(key) or os.environ.get(key, "")
            return ToolResult.success({"key": key, "exists": bool(value), "value": _masked(value) if value else ""})
        if action == "set":
            value = _clean(args.get("value"), limit=12000)
            if not value:
                return ToolResult.fail("Error: value is required")
            set_key(str(path), key, value, quote_mode="always")
            os.environ[key] = value
            return ToolResult.success({"key": key, "configured": True, "value": _masked(value)})
        if action == "delete":
            unset_key(str(path), key)
            os.environ.pop(key, None)
            return ToolResult.success({"key": key, "configured": False})
        return ToolResult.fail("Error: action must be set, get, list, or delete")


class RawEvolutionUndoTool(BaseTool):
    name = "evolution_undo"
    description = "Restore this user's CowAgent memory and skill files from a self-evolution backup."
    params = {
        "type": "object",
        "properties": {"backup_id": {"type": "string"}},
        "required": ["backup_id"],
    }

    def __init__(self, runtime) -> None:
        self.runtime = runtime

    def execute(self, args: dict) -> ToolResult:
        backup_id = _clean(args.get("backup_id"), limit=160)
        if not re.fullmatch(r"[A-Za-z0-9._-]+", backup_id):
            return ToolResult.fail("Error: invalid backup_id")
        try:
            if restore_backup(str(self.runtime.workspace), backup_id):
                return ToolResult.success({"restored": True, "backup_id": backup_id})
            return ToolResult.fail(f"Backup not found: {backup_id}")
        except Exception as exc:
            return ToolResult.fail(f"Error restoring backup: {exc}")


def _append_scheduled_message(owner_id: str, conversation_id: str, text: str, task_id: str) -> None:
    from services.ecommerce.ecommerce_agent_memory_service import ecommerce_agent_memory_service

    ecommerce_agent_memory_service.append_message(
        owner_id=owner_id,
        conversation_id=conversation_id,
        message_key=f"scheduler:{task_id}:{uuid4().hex[:12]}",
        role="assistant",
        message_type="cow_message",
        content={"message": {"role": "assistant", "content": [{"type": "text", "text": text}]}},
        run_id=f"scheduler-{task_id}",
        turn_id=f"scheduler-{task_id}",
    )


def _scheduler_for(runtime) -> tuple[TaskStore, SchedulerService]:
    key = str(runtime.workspace.resolve())
    with _SCHEDULER_LOCK:
        service = _SCHEDULER_SERVICES.get(key)
        store = TaskStore(str(runtime.workspace / "scheduler" / "tasks.json"))
        if service is not None:
            return store, service

        identity = dict(runtime.identity)
        owner_id = runtime.owner_id
        default_conversation_id = runtime.conversation_id
        request_defaults = {
            key: runtime.run.request.get(key)
            for key in ("base_url", "count", "mode", "model", "planner_model", "quality", "size")
        }

        def execute_task(task: dict):
            action = task.get("action") if isinstance(task.get("action"), Mapping) else {}
            conversation_id = _clean(action.get("receiver"), default_conversation_id, 191)
            if action.get("type") == "agent_task":
                from services.ecommerce.cow_agent_runtime_service import start_cow_agent_run

                body = {
                    "prompt": _clean(action.get("task_description"), limit=8000),
                    "conversation_id": conversation_id,
                    "model": request_defaults.get("model"),
                    "planner_model": request_defaults.get("planner_model"),
                    "quality": request_defaults.get("quality"),
                    "size": request_defaults.get("size"),
                    "count": request_defaults.get("count") or 1,
                    "mode": request_defaults.get("mode") or "generate",
                }
                start_cow_agent_run(body, identity=identity, base_url=_clean(request_defaults.get("base_url"), limit=1000))
                return True
            _append_scheduled_message(
                owner_id,
                conversation_id,
                _clean(action.get("content"), "Scheduled reminder", 8000),
                _clean(task.get("id"), "task", 120),
            )
            return True

        service = SchedulerService(store, execute_task)
        service.start()
        _SCHEDULER_SERVICES[key] = service
        return store, service


class RawSchedulerTool(SchedulerTool):
    def __init__(self, runtime) -> None:
        super().__init__({"channel_type": "raw-web"})
        self.runtime = runtime

    def execute(self, params: dict) -> ToolResult:
        self.task_store, self.scheduler_service = _scheduler_for(self.runtime)
        self.current_context = Context(ContextType.TEXT, self.runtime.user_message, {
            "receiver": self.runtime.conversation_id,
            "session_id": self.runtime.conversation_id,
            "isgroup": False,
            "channel_type": "raw-web",
        })
        return super().execute(params)


def _mcp_config_path(runtime) -> Path:
    directory = _tool_state_root() / "raw-mcp" / _owner_key(runtime.owner_id)
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "mcp.json"


def _read_mcp_configs(runtime) -> dict[str, dict[str, Any]]:
    path = _mcp_config_path(runtime)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    servers = data.get("servers") if isinstance(data, Mapping) else None
    return {str(name): dict(item) for name, item in dict(servers or {}).items() if isinstance(item, Mapping)}


def _write_mcp_configs(runtime, servers: Mapping[str, Mapping[str, Any]]) -> None:
    path = _mcp_config_path(runtime)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps({"servers": servers}, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def _shutdown_mcp_client(runtime, server: str) -> None:
    key = (_owner_key(runtime.owner_id), server)
    with _MCP_LOCK:
        client = _MCP_CLIENTS.pop(key, None)
    if client is not None:
        client.shutdown()


def _mcp_client(runtime, server: str) -> McpClient:
    key = (_owner_key(runtime.owner_id), server)
    with _MCP_LOCK:
        existing = _MCP_CLIENTS.get(key)
    if existing is not None:
        return existing
    config = _read_mcp_configs(runtime).get(server)
    if not config:
        raise ValueError(f"MCP server is not configured: {server}")
    client = McpClient({"name": server, **config})
    if not client.initialize():
        raise RuntimeError(f"MCP server failed to initialize: {server}")
    with _MCP_LOCK:
        previous = _MCP_CLIENTS.setdefault(key, client)
    if previous is not client:
        client.shutdown()
    return previous


class RawMcpTool(BaseTool):
    name = "mcp"
    description = "Configure, inspect, and call user-isolated MCP servers. HTTP transports are available to users; stdio is administrator-only."
    params = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["configure", "remove", "list_servers", "list_tools", "call"]},
            "server": {"type": "string"},
            "transport": {"type": "string", "enum": ["sse", "streamable-http", "stdio"]},
            "url": {"type": "string"},
            "command": {"type": "string"},
            "args": {"type": "array", "items": {"type": "string"}},
            "headers": {"type": "object"},
            "env": {"type": "object"},
            "tool": {"type": "string"},
            "arguments": {"type": "object"},
        },
        "required": ["action"],
    }

    def __init__(self, runtime) -> None:
        self.runtime = runtime

    def execute(self, args: dict) -> ToolResult:
        action = _clean(args.get("action"), limit=40).lower()
        server = _clean(args.get("server"), limit=100)
        configs = _read_mcp_configs(self.runtime)
        if action == "list_servers":
            return ToolResult.success({"servers": [{"name": name, "transport": item.get("type")} for name, item in sorted(configs.items())]})
        if action == "configure":
            if not re.fullmatch(r"[A-Za-z0-9._-]+", server):
                return ToolResult.fail("Error: server must use letters, numbers, dot, underscore, or hyphen")
            transport = _clean(args.get("transport"), "streamable-http", 40).lower()
            if transport == "stdio" and not _is_admin(self.runtime):
                return ToolResult.fail("Error: stdio MCP servers are restricted to RAW administrators")
            if transport not in {"sse", "streamable-http", "stdio"}:
                return ToolResult.fail("Error: unsupported MCP transport")
            config: dict[str, Any] = {"type": transport}
            if transport == "stdio":
                command = _clean(args.get("command"), limit=1000)
                if not command:
                    return ToolResult.fail("Error: command is required for stdio MCP")
                config.update({"command": command, "args": list(args.get("args") or []), "env": dict(args.get("env") or {})})
            else:
                url = _clean(args.get("url"), limit=3000)
                if not url.lower().startswith(("http://", "https://")):
                    return ToolResult.fail("Error: an HTTP or HTTPS URL is required")
                config.update({"url": url, "headers": dict(args.get("headers") or {})})
            configs[server] = config
            _shutdown_mcp_client(self.runtime, server)
            _write_mcp_configs(self.runtime, configs)
            return ToolResult.success({"server": server, "configured": True, "transport": transport})
        if not server:
            return ToolResult.fail("Error: server is required")
        if action == "remove":
            configs.pop(server, None)
            _shutdown_mcp_client(self.runtime, server)
            _write_mcp_configs(self.runtime, configs)
            return ToolResult.success({"server": server, "configured": False})
        try:
            client = _mcp_client(self.runtime, server)
            if action == "list_tools":
                return ToolResult.success({"server": server, "tools": client.list_tools()})
            if action == "call":
                tool_name = _clean(args.get("tool"), limit=200)
                if not tool_name:
                    return ToolResult.fail("Error: tool is required")
                return ToolResult.success({"server": server, "tool": tool_name, "result": client.call_tool(tool_name, dict(args.get("arguments") or {}))})
        except Exception as exc:
            return ToolResult.fail(str(exc))
        return ToolResult.fail("Error: unknown MCP action")


def build_extended_cow_tools(runtime) -> list[BaseTool]:
    load_admin_tool_environment()
    workspace = str(runtime.workspace)
    tools: list[BaseTool] = [
        NativeWorkspaceTool(runtime, Ls({"cwd": workspace}), default_path="."),
        NativeWorkspaceTool(runtime, SearchFiles({"cwd": workspace}), default_path="."),
        NativeWorkspaceTool(runtime, Write({"cwd": workspace})),
        NativeWorkspaceTool(runtime, Edit({"cwd": workspace})),
        RawMemoryGetTool(runtime),
        RawBrowserTool(runtime),
        RawWebSearchTool(runtime),
        WebFetch({"cwd": workspace}),
        RawSchedulerTool(runtime),
        NativeWorkspaceTool(runtime, Send({"cwd": workspace}), allow_url=True),
        RawEvolutionUndoTool(runtime),
        RawMcpTool(runtime),
    ]
    if _is_admin(runtime):
        tools.extend([RawBashTool(runtime), RawEnvConfigTool(runtime)])
    return tools


def reset_extended_tool_services() -> None:
    with _SCHEDULER_LOCK:
        services = list(_SCHEDULER_SERVICES.values())
        _SCHEDULER_SERVICES.clear()
    for service in services:
        service.stop()
    with _MCP_LOCK:
        clients = list(_MCP_CLIENTS.values())
        _MCP_CLIENTS.clear()
    for client in clients:
        client.shutdown()
