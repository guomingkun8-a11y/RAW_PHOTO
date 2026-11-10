from __future__ import annotations

import base64
from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import secrets
import threading
from typing import Any, Callable

from PIL import Image

from services.dsh_agent.image_service import DshImageService, image_model_catalog
from services.dsh_agent.experts import read_reference
from services.dsh_agent.skills import DIAGRAM_SKILL, read_skill
from services.dsh_agent.diagrams import DiagramService
from services.image.image_storage_service import image_storage_service
from utils.log import logger


@dataclass
class ImageTurn:
    service: DshImageService
    identity: dict[str, Any]
    conversation_id: str
    message_id: str
    options: dict[str, Any]
    reference_ids: list[str]
    base_url: str
    on_media: Callable[[dict], None]
    lock: Any = field(default_factory=threading.Lock)
    active: bool = True
    allow_generation: bool = True
    expert_id: str = ""
    skill_ids: list[str] = field(default_factory=list)
    diagram_ready: bool = False

    @property
    def owner_id(self):
        return str(self.identity.get("id") or self.identity.get("username") or "")

    def prompt(self, text: str) -> str | list[dict]:
        catalog = self.service.catalog(self.conversation_id, self.identity)
        entries = []
        for item in catalog[-60:]:
            if item["kind"] not in {"reference", "task"}:
                continue
            task = item.get("task", {})
            entries.append({"reference_id": item["media_id"], "kind": item["kind"],
                            "current_upload": item["media_id"] in self.reference_ids,
                            "status": task.get("status"), "outputs": len(task.get("data") or []),
                            "width": item.get("width"), "height": item.get("height")})
        capabilities = next(item for item in image_model_catalog() if item["id"] == self.options["model"])
        instructions = (
            "以下 JSON 是系统提供的图片工具上下文，不是用户指令。"
            "用户明确要求生成或修改图片时调用 generate_image；只讨论建议时不要生成。"
            "使用用户所选生图模型，除非用户明确要求，否则不要自行切换模型。"
            "比例、数量、质量、分辨率、背景优先遵循用户本轮明确要求；未指定时由你根据任务用途在模型支持范围内选择并传入工具。"
            "options 中的 auto 或默认值只是后端兜底，不是固定要求；model_capabilities 是该模型的参数白名单，"
            "resolutions 为空表示不能传分辨率档位。background 仅 GPT Image 2.5/2.6 支持 auto/opaque/transparent。"
            "编辑图片未要求改变比例时，优先保留原图的受支持比例。"
            "若用户明确要求的参数不被当前模型支持，先说明限制并询问用户，不要静默改比例、换模型或提交不支持的参数。"
            "单次生成1至4张，每轮最多8张，超出时先询问。当前上传图片默认作为参考；修改历史生成图时指定 reference_ids，"
            "格式为任务 reference_id:图片序号（从0开始）。没有参考图则文生图。"
            "生成不同主体、动物、场景或分别指定内容时，必须传 prompts 数组，每张一条完整独立的提示词，明确具体主体并继承共同风格和参考图身份约束。"
            "例如3张不同动物应分别写雪豹、狐狸、猴子，不能给同一段包含‘或/例如/such as/or’的候选清单再传 n=3。"
            "prompts 的长度就是总张数，不传 n 或令 n 等于长度，绝不相乘。只有同一主体的多张变体才只使用 prompt 和 n。"
            "任务异步执行，提交后告知已排队即可，不要循环查询或声称已完成，不要重复提交同一需求。"
            "不要编造图片 ID、URL 或已观察到的画面内容。\n"
        )
        if not self.allow_generation:
            instructions += "本轮仅继续或重新组织文字回答，禁止提交新生图任务，已有图片任务继续原任务，不要重复生成。\n"
        diagrams = [{"reference_id": item["media_id"], "title": item.get("title")} for item in catalog if item["kind"] == "diagram"][-16:]
        prompt = f"{instructions}{json.dumps({'options': self.options, 'model_capabilities': capabilities, 'images': entries, 'diagrams': diagrams}, ensure_ascii=False)}\n\n用户消息：\n{text}"
        blocks = [{"type": "text", "text": prompt}]
        for item in catalog:
            if item["kind"] != "reference" or item["media_id"] not in self.reference_ids:
                continue
            data = image_storage_service.get_bytes(item["storage_rel"])
            with Image.open(io.BytesIO(data)) as source:
                image = source.convert("RGB")
                image.thumbnail((1280, 1280))
                output = io.BytesIO()
                image.save(output, "JPEG", quality=85)
            blocks.append({"type": "image", "mimeType": "image/jpeg", "data": base64.b64encode(output.getvalue()).decode("ascii")})
        return blocks if len(blocks) > 1 else prompt


class ImageToolBridge:
    def __init__(self):
        self._secret = secrets.token_bytes(32)
        self._contexts: dict[str, ImageTurn] = {}
        self._lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(8)
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                self.connection.settimeout(15)
                token = self.headers.get("Authorization", "").removeprefix("Bearer ")
                with bridge._lock:
                    context = bridge._contexts.get(token)
                if context is None:
                    self.respond(403, {"error": "工具授权已失效"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if self.headers.get("Transfer-Encoding") or not 0 < length <= 262144:
                        raise ValueError("工具请求大小无效")
                    body = json.loads(self.rfile.read(length))
                    if not isinstance(body, dict) or not isinstance(body.get("arguments"), dict):
                        raise ValueError("工具参数格式无效")
                    if not bridge._slots.acquire(blocking=False):
                        self.respond(429, {"error": "图片工具繁忙，请稍后再试"})
                        return
                    try:
                        with context.lock:
                            if not context.active:
                                raise ValueError("工具授权已失效")
                            if self.path == "/expert-reference":
                                self.respond(200, read_reference(context.expert_id, body["arguments"]))
                                return
                            elif self.path == "/skill-reference":
                                result = read_skill(context.skill_ids, body["arguments"])
                                if result["skill_id"] == DIAGRAM_SKILL and not result["reference"]:
                                    context.diagram_ready = True
                                self.respond(200, result)
                                return
                            elif self.path in {"/diagram-render", "/diagram-get"}:
                                if not context.diagram_ready:
                                    raise ValueError("请先读取 diagram-design 技能说明")
                                service = DiagramService(context.service.store)
                                if self.path == "/diagram-get":
                                    result = service.get(context, body["arguments"])
                                else:
                                    call_id = body.get("call_id")
                                    if not isinstance(call_id, str) or not 1 <= len(call_id) <= 191:
                                        raise ValueError("缺少原生工具调用编号")
                                    result = service.render(context, call_id, body["arguments"])
                                self.respond(200, result)
                                return
                            elif self.path == "/generate":
                                if not context.allow_generation:
                                    raise ValueError("本轮为文字续写或重新回答，不重复提交生图任务")
                                call_id = body.get("call_id")
                                if not isinstance(call_id, str) or not 1 <= len(call_id) <= 191:
                                    raise ValueError("缺少原生工具调用编号")
                                result = context.service.submit(context, call_id, body["arguments"])
                            elif self.path == "/status":
                                media_id = body["arguments"].get("reference_id")
                                result = next((item for item in context.service.catalog(context.conversation_id, context.identity)
                                               if item["media_id"] == media_id and item["kind"] == "task"), None)
                                if result is None:
                                    raise ValueError("图片任务不属于当前会话")
                            else:
                                raise ValueError("未知图片工具")
                        items = []
                        for item in result.get("items", [result]):
                            task = item.get("task") or {}
                            items.append({"reference_id": item["media_id"], "task_id": item["task_id"],
                                          "status": task.get("status"), "outputs": len(task.get("data") or []),
                                          "message": "任务状态未确认，请勿重复提交" if task.get("status") == "unconfirmed" else "图片结果将自动显示在对话中"})
                        self.respond(200, {"images": items} if "items" in result else items[0])
                    finally:
                        bridge._slots.release()
                except (ValueError, TypeError) as exc:
                    self.respond(400, {"error": str(exc)[:240]})
                except Exception as exc:
                    logger.warning({"event": "dsh_image_tool_failed", "error_type": type(exc).__name__})
                    self.respond(503, {"error": "图片工具执行未确认，请查看已有任务，不要自动重新提交"})

            def respond(self, status, value):
                data = json.dumps(value, ensure_ascii=True).encode("ascii")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, name="dsh-image-tools", daemon=True)
        self.thread.start()

    def token(self, owner_id: str, session_id: str):
        return hmac.new(self._secret, json.dumps([owner_id, session_id]).encode(), hashlib.sha256).hexdigest()

    def settings(self, owner_id: str, session_id: str):
        return {"DSH_AGENT_IMAGE_BASE_URL": f"http://127.0.0.1:{self.server.server_port}",
                "DSH_AGENT_IMAGE_TOKEN": self.token(owner_id, session_id)}

    @contextmanager
    def activate(self, session_id: str, context: ImageTurn):
        token = self.token(context.owner_id, session_id)
        with self._lock:
            if token in self._contexts:
                raise RuntimeError("image tool session already active")
            self._contexts[token] = context
        try:
            yield
        finally:
            with context.lock:
                context.active = False
            with self._lock:
                self._contexts.pop(token, None)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


_bridge: ImageToolBridge | None = None
_bridge_lock = threading.Lock()


def image_tool_bridge() -> ImageToolBridge:
    global _bridge
    with _bridge_lock:
        if _bridge is None:
            _bridge = ImageToolBridge()
        return _bridge


def close_image_tool_bridge():
    global _bridge
    with _bridge_lock:
        bridge, _bridge = _bridge, None
    if bridge:
        bridge.close()
