from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
import requests

from api.dsh_agent import routes
from services.dsh_agent import runtime
from services.dsh_agent.diagrams import DiagramService, validate_svg
from services.dsh_agent.image_service import DshImageService, normalize_options
from services.dsh_agent.image_tools import ImageToolBridge, ImageTurn, image_tool_bridge
from services.dsh_agent.sessions import RuntimePool
from services.dsh_agent.skills import DIAGRAM_SKILL, RESEARCH_SKILLS, STYLE_SKILL, read_skill, resolve_skills, skill_catalog, skill_policy
from services.dsh_agent.store import DshAgentStore


SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 720 280"><style>text{font-family:system-ui,sans-serif;fill:#17212b}</style><rect width="720" height="280" fill="#f8faf9"/><path d="M220 140H480" stroke="#42786a" stroke-width="3"/><rect x="40" y="80" width="180" height="120" rx="6" fill="#e3f1eb"/><rect x="480" y="80" width="180" height="120" rx="6" fill="#e8edf8"/><text x="130" y="146" text-anchor="middle" font-size="24">提交需求</text><text x="570" y="146" text-anchor="middle" font-size="24">审核发布</text></svg>'


class DshDiagramTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = DshAgentStore(f"sqlite:///{Path(self.tmp.name) / 'diagrams.db'}", dsh_home=Path(self.tmp.name) / "home")
        self.addCleanup(self.store.close)
        self.identity = {"id": "alice", "username": "alice"}
        self.cid = self.store.create_conversation(self.identity)["conversation_id"]
        claimed = self.store.begin_turn(self.cid, "alice", "画一个上新流程图")
        self.events = []
        self.context = ImageTurn(DshImageService(self.store), self.identity, self.cid, claimed["user_message_id"],
                                 normalize_options({}), [], "http://localhost", self.events.append)
        self.service = DiagramService(self.store)

    def test_discovery_is_on_demand_without_manual_selection_or_expert_binding(self):
        self.assertEqual([], resolve_skills(None))
        self.assertIn("diagram-design", skill_policy([]))
        self.assertNotIn("Forty-five visual types", skill_policy([]))
        with self.assertRaises(ValueError):
            read_skill([], {"skill_id": STYLE_SKILL})
        self.assertIn("Forty-five visual types", read_skill([], {"skill_id": DIAGRAM_SKILL})["content"])
        self.assertIn("Flowchart", read_skill([], {"skill_id": DIAGRAM_SKILL, "reference": "type-flowchart"})["content"])
        for skill_id in RESEARCH_SKILLS:
            with self.subTest(skill=skill_id):
                with self.assertRaises(ValueError):
                    read_skill([], {"skill_id": skill_id})
                selected = resolve_skills([skill_id])
                self.assertIn(skill_id, [item["id"] for item in skill_catalog()])
                self.assertIn("不得", skill_policy(selected))
                self.assertIn("系统集成约定", read_skill(selected, {"skill_id": skill_id})["content"])
        all_research = list(RESEARCH_SKILLS)
        self.assertEqual(all_research, resolve_skills(all_research))
        self.assertIn("商品事实", skill_policy(all_research))
        for skill_id, references in {
            "customer-research": ["source-guides", "interviews-and-surveys"],
            "competitor-profiling": ["templates", "tool-reference"],
            "ecommerce-competitor-analyzer": ["analysis-prompt-base", "n8n-workflow-analysis"],
        }.items():
            for reference in references:
                with self.subTest(skill=skill_id, reference=reference):
                    result = read_skill([skill_id], {"skill_id": skill_id, "reference": reference})
                    self.assertIn("系统集成约定", result["content"])
        for value in ("../../.env", "/.env", "type-flowchart.md", "https://example.com", [], None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                read_skill([], {"skill_id": DIAGRAM_SKILL, "reference": value})

    def test_static_svg_roundtrip_and_malicious_or_unbounded_content_is_refused(self):
        svg, width, height = validate_svg(SVG)
        self.assertIn("提交需求", svg)
        self.assertEqual((720, 280), (width, height))
        for snippet in ('<script>alert(1)</script>', '<foreignObject><div>hi</div></foreignObject>',
                        '<image href="https://example.com/x"/>', '<animate attributeName="x"/>',
                        '<g onclick="alert(1)"/>', '<rect fill="url(//evil.example/x)"/>',
                        '<style>@import "https://example.com";</style>', '<style>g{background:image-set("//evil.example/x" 1x)}</style>',
                        '<style>g{fill:u\\72l(//evil.example/x)}</style>',
                        '<defs><g id="loop"><use href="#loop"/></g></defs><use href="#loop"/>'):
            with self.subTest(snippet=snippet), self.assertRaises(ValueError):
                validate_svg(SVG.replace('</svg>', snippet + '</svg>'))
        for source in ('<!DOCTYPE svg [<!ENTITY x "bad">]>' + SVG, '<!ENTITY x SYSTEM "file:///etc/passwd">' + SVG,
                       SVG.replace('720 280', 'NaN 280'), SVG.replace('720 280', '9000 280'),
                       SVG.replace('</svg>', '<g/>' * 1600 + '</svg>'), 'x' * 60001):
            with self.subTest(source=source[:80]), self.assertRaises(ValueError):
                validate_svg(source)

    def test_save_deduplication_refresh_owner_scope_revisions_and_delete(self):
        args = {"title": "上新流程", "svg": SVG}
        first = self.service.render(self.context, "call-one", args)
        self.assertEqual(first, self.service.render(self.context, "call-one", args))
        self.assertEqual(first, self.service.render(self.context, "call-duplicate", args))
        with self.assertRaises(ValueError):
            self.service.render(self.context, "call-one", {**args, "title": "改名"})
        item = self.service.get(self.context, {"reference_id": first["reference_id"]})
        self.assertIn("提交需求", item["svg"])
        with self.assertRaises(ValueError):
            self.service.get(replace(self.context, identity={"id": "bob"}), {"reference_id": first["reference_id"]})
        other_cid = self.store.create_conversation(self.identity)["conversation_id"]
        with self.assertRaises(ValueError):
            self.service.get(replace(self.context, conversation_id=other_cid), {"reference_id": first["reference_id"]})
        self.store.complete_turn(self.cid, "alice", content="已绘制", model="tt-6-sol", usage={})
        history = DshImageService(self.store).attach_messages(self.store.list_messages(self.cid, owner_id="alice"), self.identity)
        self.assertEqual("diagram", history["items"][0]["media"][0]["kind"])
        self.assertEqual([], history["items"][0]["skill_ids"])
        with self.assertRaises(ValueError):
            self.service.render(self.context, "stale", args)
        claimed = self.store.begin_turn(self.cid, "alice", "增加一步")
        with self.assertRaises(ValueError):
            self.service.render(self.context, "stale-again", args)
        revised = replace(self.context, message_id=claimed["user_message_id"])
        result = self.service.render(revised, "call-two", {"title": "新版本", "svg": SVG})
        self.assertNotEqual(first["reference_id"], result["reference_id"])
        self.store.complete_turn(self.cid, "alice", content="已修改", model="tt-6-sol", usage={})
        self.assertTrue(self.store.delete_conversation(self.cid, "alice"))
        with self.assertRaises(ValueError):
            self.service.get(revised, {"reference_id": first["reference_id"]})

    def test_budget_text_only_and_bridge_authorization_expiry(self):
        for index in range(4):
            self.service.render(self.context, f"call-{index}", {"title": str(index), "svg": SVG})
        with self.assertRaises(ValueError):
            self.service.render(self.context, "too-many", {"title": "extra", "svg": SVG})
        with self.assertRaises(ValueError):
            self.service.render(replace(self.context, allow_generation=False), "text", {"title": "文字续写", "svg": SVG})
        bridge = ImageToolBridge()
        self.addCleanup(bridge.close)
        settings = bridge.settings("alice", "diagram-session")
        headers = {"Authorization": "Bearer " + settings["DSH_AGENT_IMAGE_TOKEN"]}
        def post(path, args, auth=headers):
            return requests.post(settings["DSH_AGENT_IMAGE_BASE_URL"] + path, headers=auth,
                                 json={"call_id": "diagram-call", "arguments": args}, timeout=5)
        args = {"title": "0", "svg": SVG}
        self.assertEqual(403, post("/diagram-render", args).status_code)
        with bridge.activate("diagram-session", self.context):
            self.assertEqual(400, post("/diagram-render", args).status_code)
            self.assertEqual(200, post("/skill-reference", {"skill_id": DIAGRAM_SKILL}).status_code)
            self.assertEqual(200, post("/diagram-render", args).status_code)
            self.assertEqual(403, post("/diagram-render", args, {"Authorization": "Bearer " + bridge.token("bob", "diagram-session")}).status_code)
        self.assertEqual(403, post("/diagram-render", args).status_code)

    def test_http_history_is_owner_scoped_and_does_not_query_image_tasks(self):
        self.service.render(self.context, "call", {"title": "上新流程", "svg": SVG})
        self.store.complete_turn(self.cid, "alice", content="图示已保存", model="tt-6-sol", usage={})
        app = FastAPI()
        app.include_router(routes.create_router())
        with TestClient(app) as client, patch.object(routes, "dsh_agent_store", self.store), patch.object(
            routes, "require_identity", side_effect=lambda token: {"id": token}
        ), patch.object(routes.teams, "snapshot", return_value=None), patch("services.dsh_agent.image_service.image_task_service.list_tasks") as images:
            path = f"/api/dsh-agent/conversations/{self.cid}"
            result = client.get(path, headers={"Authorization": "alice"})
            self.assertEqual(200, result.status_code)
            self.assertEqual("上新流程", result.json()["items"][0]["media"][0]["title"])
            self.assertEqual(404, client.get(path, headers={"Authorization": "bob"}).status_code)
            images.assert_not_called()

    def test_http_message_stream_exposes_saved_diagram_without_manual_skill_selection(self):
        self.store.complete_turn(self.cid, "alice", content="准备好了", model="tt-6-sol", usage={})
        app = FastAPI()
        app.include_router(routes.create_router())
        def execute(_prompt, **kwargs):
            settings = image_tool_bridge().settings("alice", kwargs["dsh_session_id"])
            headers = {"Authorization": "Bearer " + settings["DSH_AGENT_IMAGE_TOKEN"]}
            for path, arguments in (("/skill-reference", {"skill_id": DIAGRAM_SKILL}),
                                    ("/diagram-render", {"title": "上新流程", "svg": SVG})):
                result = requests.post(settings["DSH_AGENT_IMAGE_BASE_URL"] + path, headers=headers,
                    json={"call_id": "native-diagram", "arguments": arguments}, timeout=5)
                self.assertEqual(200, result.status_code, result.text)
            self.assertEqual([], kwargs["skill_ids"])
            return {"content": "图示已保存", "model": "tt-6-sol", "usage": {"totalTokens": 42}, "finish_reason": "completed"}
        with TestClient(app) as client, patch.object(routes, "dsh_agent_store", self.store), patch.object(
            routes, "require_identity", return_value=self.identity
        ), patch.object(routes.teams, "snapshot", return_value=None), patch.object(
            routes.dsh_runtime, "public_status", return_value={"enabled": True, "configured": True}
        ), patch.object(routes.dsh_runtime, "run_turn", side_effect=execute):
            result = client.post(f"/api/dsh-agent/conversations/{self.cid}/messages/stream", json={"content": "画一个上新流程"})
            self.assertEqual(200, result.status_code)
            frames = [json.loads(line[6:]) for line in result.text.splitlines() if line.startswith("data: ")]
            self.assertFalse(any(event["type"] == "error" for event in frames), frames)
            artifact = next(event["media"] for event in frames if event["type"] == "media")
            self.assertEqual("diagram", artifact["kind"])
            completed = next(event["message"] for event in frames if event["type"] == "complete")
            self.assertEqual([], completed["skill_ids"])
            self.assertEqual(42, completed["total_tokens"])
            history = client.get(f"/api/dsh-agent/conversations/{self.cid}").json()
            self.assertEqual(artifact["media_id"], history["items"][-2]["media"][0]["media_id"])

    @unittest.skipUnless(os.getenv("DSH_RUNTIME_SMOKE") == "1", "opt-in native DSH against mock only")
    def test_native_runtime_discovers_skill_renders_and_revises_without_paid_requests(self):
        calls = []
        reference = {}
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass
            def do_POST(handler):
                payload = json.loads(handler.rfile.read(int(handler.headers["Content-Length"])))
                calls.append(payload)
                index = len(calls)
                commands = {
                    1: ("read_skill", {"skill_id": DIAGRAM_SKILL}),
                    2: ("read_skill", {"skill_id": DIAGRAM_SKILL, "reference": "type-flowchart"}),
                    3: ("render_diagram", {"title": "上新流程", "svg": SVG}),
                    5: ("read_skill", {"skill_id": DIAGRAM_SKILL}),
                    6: ("get_diagram", {"reference_id": reference.get("id")}),
                    7: ("render_diagram", {"title": "上新流程第二版", "svg": SVG.replace("审核发布", "确认发布")}),
                }
                if index in commands:
                    name, args = commands[index]
                    delta = {"role": "assistant", "tool_calls": [{"index": 0, "id": f"diagram-call-{index}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}
                else:
                    delta = {"role": "assistant", "content": "图示已保存"}
                frames = [{"id": "mock", "object": "chat.completion.chunk", "model": "tt-6-sol", "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                          {"id": "mock", "object": "chat.completion.chunk", "model": "tt-6-sol", "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls" if index in commands else "stop"}]}]
                data = ("".join(f"data: {json.dumps(frame)}\n\n" for frame in frames) + "data: [DONE]\n\n").encode()
                handler.send_response(200)
                handler.send_header("Content-Type", "text/event-stream")
                handler.send_header("Content-Length", str(len(data)))
                handler.end_headers()
                handler.wfile.write(data)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with patch.object(runtime, "DATA_DIR", Path(self.tmp.name)), patch.object(runtime, "runtime_pool", pool := RuntimePool()), patch.object(
            runtime.config, "get_openai_relay_settings", return_value={"base_url": f"http://127.0.0.1:{server.server_port}/v1", "api_key": "mock-only"}
        ), patch.object(runtime, "current_relay_account", return_value=None), patch.object(runtime, "search_available", return_value=False), patch(
            "services.dsh_agent.image_service.image_task_service.submit_generation"
        ) as images:
            try:
                session = "session-" + "a" * 32
                for index, prompt in enumerate(("画流程图", "修改审核步骤")):
                    if index:
                        claimed = self.store.begin_turn(self.cid, "alice", prompt)
                        self.context = replace(self.context, message_id=claimed["user_message_id"], active=True, diagram_ready=False)
                    with image_tool_bridge().activate(session, self.context):
                        result = runtime._execute(self.context.prompt(prompt), model="tt-6-sol", owner_id="alice", skill_ids=[],
                            dsh_session_id=session, has_history=bool(index), history=[], on_notification=lambda _: None)
                    self.assertEqual("图示已保存", result["content"])
                    reference["id"] = self.events[-1]["media_id"]
                    self.store.complete_turn(self.cid, "alice", content=result["content"], model="tt-6-sol", usage={})
                self.assertEqual(8, len(calls))
                self.assertIn("type-flowchart", json.dumps(calls[2]))
                self.assertIn("图示已保存", json.dumps(calls[3], ensure_ascii=False))
                self.assertIn("审核发布", json.dumps(calls[6], ensure_ascii=False))
                self.assertEqual(2, len(self.events))
                images.assert_not_called()
            finally:
                pool.close()
