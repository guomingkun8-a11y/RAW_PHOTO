from __future__ import annotations

import json
import time
from typing import Any, Callable
from urllib.parse import urlsplit


def public_url(value: object) -> str:
    text = str(value or "")[:3000]
    try:
        parsed = urlsplit(text)
        if parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username and not parsed.password:
            return text
    except ValueError:
        pass
    return ""


class TurnEvents:
    def __init__(self, session_id: str, emit: Callable[[dict], None]) -> None:
        self.session_id = session_id
        self.emit = emit
        self.activities: dict[str, dict[str, Any]] = {}
        self.started = time.monotonic()
        self.timings: dict[str, int] = {}
        self.content = ""
        self.context: dict[str, Any] | None = None
        self.usage: dict[str, int] = {}
        self.children: set[str] = set()
        self.turn_ended = False

    def activity(self, id: str, kind: str, title: str, status: str = "running", **extra) -> None:
        activity = {"id": id, "kind": kind, "title": title, "status": status, **extra}
        if self.activities.get(id) != activity:
            self.activities[id] = activity
            self.emit({"type": "activity", "activity": activity})

    def mark(self, name: str) -> None:
        self.timings[name] = round((time.monotonic() - self.started) * 1000)

    def stage(self, key: str, title: str, status: str = "running") -> None:
        kind = {"queue": "queue", "runtime": "runtime", "analysis": "analysis"}.get(key, key)
        if status == "completed":
            self.mark({"queue": "queue_ms", "runtime": "runtime_ms"}.get(key, f"{key}_ms"))
        self.activity(key, kind, title, status)

    def notification(self, notice: Any) -> None:
        payload = getattr(notice, "payload", {})
        if not isinstance(payload, dict):
            return
        method = getattr(notice, "method", "")
        if method == "subagent.started" and payload.get("parentSessionId") == self.session_id:
            self.children.add(str(payload.get("childSessionId") or ""))
        if method != "session.event" or payload.get("sessionId") not in {self.session_id, *self.children}:
            return
        event = payload.get("event") or {}
        data = event.get("data") or {}
        if event.get("type") in {"assistant/message", "compaction/summary"}:
            usage = data.get("usage")
            if isinstance(usage, dict):
                for key in ("inputTokens", "outputTokens", "totalTokens", "cacheReadTokens", "cacheWriteTokens", "reasoningTokens"):
                    value = usage.get(key)
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        self.usage[key] = self.usage.get(key, 0) + max(0, int(value))
        if payload.get("sessionId") != self.session_id:
            return
        if event.get("type") == "turn/end":
            self.turn_ended = True
        if event.get("type") == "plugin:raw-photo/context":
            self.context = data
            self.emit({"type": "context", "context": data})
        elif event.get("type") in {"compaction/start", "compaction/end"}:
            ended = event["type"] == "compaction/end"
            failed = ended and bool(data.get("error"))
            self.activity("context", "context", "上下文压缩未完成" if failed else "上下文已压缩" if ended else "正在压缩较早的对话",
                          "error" if failed else "completed" if ended else "running")
        elif event.get("type") == "assistant/live-chunk":
            chunk = data.get("chunk") or {}
            if chunk.get("type") == "text-delta" and chunk.get("text"):
                if self.turn_ended and self.content:
                    self.content += "\n\n"
                    self.emit({"type": "delta", "text": "\n\n"})
                self.turn_ended = False
                if "first_text_ms" not in self.timings:
                    self.mark("first_text_ms")
                self.activity("analysis", "analysis", "问题分析", "completed")
                self.activity("answer", "answer", "正在生成回答")
                self.content += str(chunk["text"])
                self.emit({"type": "delta", "text": str(chunk["text"])})
            elif chunk.get("type") == "reasoning-delta":
                # Provider reasoning is private; expose the observable stage only.
                self.activity("analysis", "analysis", "正在分析问题")
        elif event.get("type") == "tool/call" and str(data.get("name") or "").startswith("agent_teams_"):
            self.activity(str(data.get("callId") or "team"), "team", "正在协调协作任务")
        elif event.get("type") == "tool/call" and data.get("name") == "read_expert_reference":
            self.activity(str(data.get("callId") or "expert"), "analysis", "正在查阅电商设计资料")
        elif event.get("type") == "tool/call" and data.get("name") == "read_skill":
            try:
                diagram = json.loads(data.get("arguments") or "{}").get("skill_id") == "diagram-design"
            except (ValueError, TypeError, AttributeError):
                diagram = False
            self.activity(str(data.get("callId") or "skill"), "diagram" if diagram else "analysis",
                          "正在查阅图表设计规则" if diagram else "正在读取技能资料")
        elif event.get("type") == "tool/call" and data.get("name") in {"render_diagram", "get_diagram"}:
            self.activity(str(data.get("callId") or "diagram"), "diagram",
                          "正在保存图示" if data["name"] == "render_diagram" else "正在读取图示原稿")
        elif event.get("type") == "tool/call" and data.get("name") in {"generate_image", "get_image_task"}:
            self.activity("analysis", "analysis", "问题分析", "completed")
            self.activity(str(data.get("callId") or "image"), "image",
                          "正在提交生图任务" if data["name"] == "generate_image" else "正在查询图片任务")
        elif event.get("type") == "tool/call" and data.get("name") in {"web_search", "web_fetch"}:
            call_id = str(data.get("callId") or "")
            try:
                args = json.loads(data.get("arguments") or "{}")
            except (ValueError, TypeError):
                args = {}
            kind = "search" if data["name"] == "web_search" else "fetch"
            detail = "、".join(str(q) for q in args.get("queries", [])) if kind == "search" else public_url(args.get("url"))
            self.activity("analysis", "analysis", "问题分析", "completed")
            self.activity(call_id, kind, "正在搜索网页" if kind == "search" else "正在读取网页", detail=detail[:600])
        elif event.get("type") == "tool/result":
            message = data.get("message") or {}
            call_id = str(message.get("toolCallId") or "")
            previous = self.activities.get(call_id)
            if not previous:
                return
            failed = bool(message.get("isError"))
            if previous["kind"] == "team":
                self.activity(call_id, "team", "协作操作未完成" if failed else "协作操作已完成", "error" if failed else "completed")
                return
            if previous["kind"] == "image":
                self.activity(call_id, "image", "图片工具调用失败" if failed else "图片任务已受理", "error" if failed else "completed")
                return
            if previous["kind"] == "diagram":
                self.activity(call_id, "diagram", "图示工具调用失败" if failed else "图示工具已完成", "error" if failed else "completed")
                return
            if previous["kind"] == "analysis":
                self.activity(call_id, "analysis", "技能资料读取失败" if failed else "技能资料已读取", "error" if failed else "completed")
                return
            meta = data.get("meta") or message.get("meta") or {}
            sources = []
            for source in meta.get("sources", []) if isinstance(meta, dict) else []:
                if isinstance(source, dict) and (url := public_url(source.get("url"))):
                    sources.append({"title": str(source.get("title") or url)[:200], "url": url})
            if not sources:
                import re

                content = message.get("content", [])
                text = "\n".join(str(block.get("text") or "") for block in content if isinstance(block, dict))
                for title, raw_url in re.findall(r"\[([^\]]{1,200})\]\((https?://[^\s)]+)\)", text):
                    url = public_url(raw_url)
                    if url:
                        sources.append({"title": title, "url": url})
            title = ("搜索失败" if failed else "搜索完成") if previous["kind"] == "search" else ("网页读取失败" if failed else "网页读取完成")
            self.activity(call_id, previous["kind"], title, "error" if failed else "completed", detail=previous.get("detail", ""), sources=sources[:10])

    def finish(self, *, failed: bool = False, stopped: bool = False) -> None:
        if self.context and self.context.get("compacting"):
            self.context = {**self.context, "compacting": False, "compaction_failed": True}
            self.emit({"type": "context", "context": self.context})
        for id, item in list(self.activities.items()):
            if item["status"] == "running":
                updated = dict(item)
                updated["status"] = "stopped" if stopped else "error" if failed else "completed"
                if not failed and id == "answer":
                    updated["title"] = "回答完成"
                self.activities[id] = updated
                self.emit({"type": "activity", "activity": updated})
        self.mark("total_ms")
        self.emit({"type": "timing", "timings": self.timings})
