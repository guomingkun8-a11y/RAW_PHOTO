from __future__ import annotations

from contextlib import ExitStack
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
import requests

from api.dsh_agent import routes
from services.dsh_agent import image_service as images, runtime
from services.dsh_agent.image_tools import ImageTurn, ImageToolBridge
from services.dsh_agent.sessions import RuntimePool
from services.dsh_agent.store import DshAgentStore


class DshImageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = DshAgentStore(f"sqlite:///{Path(self.tmp.name) / 'images.db'}", dsh_home=Path(self.tmp.name) / "home")
        self.addCleanup(self.store.close)
        self.identity = {"id": "alice", "username": "alice", "name": "Alice"}
        self.conversation = self.store.create_conversation(self.identity)
        self.cid = self.conversation["conversation_id"]
        self.service = images.DshImageService(self.store)
        self.tasks = {}
        self.files = {}
        stack = self.enterContext(ExitStack())
        self.submitted = stack.enter_context(patch.object(images.image_task_service, "submit_generation", side_effect=self.submit))
        self.edited = stack.enter_context(patch.object(images.image_task_service, "submit_edit", side_effect=self.submit))
        self.listed = stack.enter_context(patch.object(images.image_task_service, "list_tasks", side_effect=self.list_tasks))
        stack.enter_context(patch.object(images.image_storage_service, "save_task_asset", side_effect=self.save))
        stack.enter_context(patch.object(images.image_storage_service, "get_bytes", side_effect=lambda rel: self.files[rel]))
        stack.enter_context(patch.object(images.image_storage_service, "delete"))
        self.events = []

    def save(self, data, **kwargs):
        rel = f"references/{kwargs['asset_index']}.png"
        self.files[rel] = data
        return SimpleNamespace(rel=rel, url=f"https://storage.example/{rel}")

    def list_tasks(self, identity, ids):
        self.assertTrue(ids, "never enumerate unrelated image history")
        self.assertEqual("alice", identity["id"])
        return {"items": [self.tasks[id] for id in ids if id in self.tasks]}

    def submit(self, identity, **kwargs):
        self.assertEqual("alice", identity["id"])
        task = {"id": kwargs["client_task_id"], "status": "queued", "data": []}
        self.tasks[task["id"]] = task
        return task

    def turn(self, references=None):
        claimed = self.store.begin_turn(self.cid, "alice", "生成商品图片", reference_ids=references or [])
        return ImageTurn(self.service, self.identity, self.cid, claimed["user_message_id"],
                         images.normalize_options({}), references or [], "http://127.0.0.1:8003", self.events.append)

    def upload(self):
        data = io.BytesIO()
        Image.new("RGB", (160, 90), "red").save(data, "PNG")
        return self.service.upload(self.cid, self.identity, data.getvalue(), "reference.png", "http://localhost")

    def test_native_tool_call_is_idempotent_and_keeps_owner_source_and_parameters(self):
        context = self.turn()
        first = self.service.submit(context, "native-call-1", {"prompt": "生成商品图片"})
        second = self.service.submit(context, "native-call-1", {"prompt": "生成商品图片"})
        third = self.service.submit(context, "another-call-for-same-request", {"prompt": "生成商品图片"})
        self.assertEqual(first, second)
        self.assertEqual(first, third)
        self.submitted.assert_called_once()
        kwargs = self.submitted.call_args.kwargs
        self.assertEqual("dsh", kwargs["source"])
        self.assertEqual(self.cid, kwargs["conversation_id"])
        self.assertEqual(context.message_id, kwargs["turn_id"])
        params = images.relay._media_image_params(kwargs, upstream_model="tt-image-2.6", requested_size="auto", reference_urls=[])
        self.assertEqual("flare", params["version"])
        with self.assertRaises(ValueError):
            self.service.submit(context, "native-call-1", {"prompt": "different"})

    def test_text_retry_and_continuation_cannot_submit_image_jobs(self):
        context = self.turn()
        context.allow_generation = False
        bridge = ImageToolBridge()
        self.addCleanup(bridge.close)
        settings = bridge.settings("alice", "session-one")
        with bridge.activate("session-one", context):
            response = requests.post(settings["DSH_AGENT_IMAGE_BASE_URL"] + "/generate",
                                     headers={"Authorization": "Bearer " + settings["DSH_AGENT_IMAGE_TOKEN"]},
                                     json={"call_id": "retry-image", "arguments": {"prompt": "generate"}}, timeout=5)
        self.assertEqual(400, response.status_code)
        self.assertIn("不重复提交", response.json()["error"])
        self.submitted.assert_not_called()
        self.edited.assert_not_called()

    def test_distinct_images_have_separate_prompts_tasks_and_idempotent_replay(self):
        context = self.turn()
        prompts = ["A snow leopard on a mountain, wildlife photography", "A red fox in a forest, wildlife photography", "A monkey on a branch, wildlife photography"]
        args = {"prompt": "Three different animals", "prompts": prompts, "n": 3, "aspect_ratio": "3:2", "quality": "high"}
        result = self.service.submit(context, "three-animals", args)
        self.assertEqual(3, len(result["items"]))
        calls = [call.kwargs for call in self.submitted.call_args_list]
        self.assertEqual(prompts, [call["prompt"] for call in calls])
        self.assertEqual([1, 1, 1], [call["n"] for call in calls])
        self.assertEqual(3, len({call["client_task_id"] for call in calls}))
        self.assertTrue(all(call["source"] == "dsh" and call["aspect_ratio"] == "3:2" and call["quality"] == "high" for call in calls))
        self.assertEqual(3, len(self.events))
        replay = self.service.submit(context, "three-animals", args)
        duplicate = self.service.submit(context, "new-call-same-plan", args)
        self.assertEqual(result, replay)
        self.assertEqual(result, duplicate)
        self.assertEqual(3, self.submitted.call_count)
        for changed in ([*prompts[:2], "An elephant"], prompts[:2], [*prompts, "An elephant"]):
            with self.assertRaisesRegex(ValueError, "不能更改"):
                self.service.submit(context, "three-animals", {"prompt": args["prompt"], "prompts": changed})
        self.assertEqual(3, self.submitted.call_count)

    def test_distinct_prompts_validate_all_images_and_reserve_limits_atomically(self):
        context = self.turn()
        for args in (
            {"prompts": []}, {"prompts": "not a list"}, {"prompts": ["good", ""]},
            {"prompts": ["same", " SAME "]}, {"prompts": ["a", "b"], "n": 3},
            {"prompts": ["a", 3]}, {"prompts": ["a", "b", "c", "d", "e"]},
        ):
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.service.submit(context, "invalid", {"prompt": "different images", **args})
        self.assertEqual([], self.service.media.list(self.cid, "alice"))
        self.submitted.assert_not_called()
        self.service.submit(context, "existing-four", {"prompt": "variants", "n": 4})
        self.service.submit(context, "existing-three", {"prompt": "other variants", "n": 3})
        with self.assertRaisesRegex(ValueError, "最多提交 8"):
            self.service.submit(context, "would-exceed", {"prompt": "two subjects", "prompts": ["subject a", "subject b"]})
        self.assertEqual(2, self.submitted.call_count)
        self.assertEqual(2, len(self.service.media.list(self.cid, "alice")))

    def test_distinct_image_edits_share_references_but_keep_individual_instructions(self):
        context = self.turn([self.upload()["media_id"]])
        prompts = ["保留瓶型标签，放在厨房", "保留瓶型标签，放在书房"]
        result = self.service.submit(context, "edit-scenes", {"prompt": "两种场景", "prompts": prompts})
        self.assertEqual(2, len(result["items"]))
        self.assertEqual(prompts, [call.kwargs["prompt"] for call in self.edited.call_args_list])
        self.assertTrue(all(len(call.kwargs["images"]) == 1 and call.kwargs["n"] == 1 for call in self.edited.call_args_list))
        self.submitted.assert_not_called()
        self.assertIn("必须传 prompts 数组", context.prompt("不同的图片")[0]["text"])

    def test_partial_distinct_submission_is_visible_and_never_automatically_resubmitted(self):
        context = self.turn()
        attempts = 0
        def partial(identity, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 2:
                raise TimeoutError("lost response for second image")
            return self.submit(identity, **kwargs)
        self.submitted.side_effect = partial
        args = {"prompt": "different subjects", "prompts": ["a", "b", "c"]}
        with self.assertRaises(TimeoutError):
            self.service.submit(context, "partial", args)
        self.assertEqual(["queued", "unconfirmed", "unconfirmed"], [event["task"]["status"] for event in self.events])
        replay = self.service.submit(context, "partial", args)
        self.assertEqual(["queued", "unconfirmed", "unconfirmed"], [item["task"]["status"] for item in replay["items"]])
        self.assertEqual(2, attempts)
        with self.assertRaisesRegex(ValueError, "未确认"):
            self.service.submit(context, "another", {"prompt": "try a different subject"})

    def test_bridge_exposes_separate_image_tasks_without_multiplying_quantity(self):
        context = self.turn()
        bridge = ImageToolBridge()
        self.addCleanup(bridge.close)
        settings = bridge.settings("alice", "distinct-images")
        with bridge.activate("distinct-images", context):
            response = requests.post(settings["DSH_AGENT_IMAGE_BASE_URL"] + "/generate",
                                     headers={"Authorization": "Bearer " + settings["DSH_AGENT_IMAGE_TOKEN"]},
                                     json={"call_id": "animals", "arguments": {"prompt": "two animals", "prompts": ["A fox", "A monkey"], "n": 2}}, timeout=5)
        self.assertEqual(200, response.status_code)
        items = response.json()["images"]
        self.assertEqual(2, len(items))
        self.assertTrue(all(item["status"] == "queued" for item in items))
        self.assertEqual(2, len({item["task_id"] for item in items}))
        self.assertEqual(2, self.submitted.call_count)

    def test_expert_references_are_allowlisted_scoped_and_never_generate(self):
        from services.dsh_agent.experts import EXPERT_ID, expert_policy, resolve_expert

        policy = expert_policy(EXPERT_ID)
        for rule in ("identity_constraints", "model_capabilities", "Logo", "仅讨论", "reference_ids"):
            self.assertIn(rule, policy)
        for value in ("../../.env", "arbitrary-role"):
            with self.assertRaises(ValueError):
                resolve_expert(value)
        context = self.turn()
        bridge = ImageToolBridge()
        self.addCleanup(bridge.close)
        settings = bridge.settings("alice", "expert-session")
        url = settings["DSH_AGENT_IMAGE_BASE_URL"] + "/expert-reference"
        headers = {"Authorization": "Bearer " + settings["DSH_AGENT_IMAGE_TOKEN"]}
        def read(arguments, auth=headers):
            return requests.post(url, headers=auth, json={"arguments": arguments}, timeout=5)
        with bridge.activate("expert-session", context):
            self.assertEqual(400, read({"reference": "product-rules"}).status_code)
            context.expert_id = EXPERT_ID
            for key in ("product-rules", "style-library"):
                result = read({"reference": key})
                self.assertEqual(200, result.status_code)
                self.assertTrue(result.json()["content"])
            for args in ({"reference": "../../.env"}, {"reference": ["product-rules"]}, {"reference": "product-rules", "path": ".env"}):
                self.assertEqual(400, read(args).status_code)
            foreign = {"Authorization": "Bearer " + bridge.token("bob", "expert-session")}
            self.assertEqual(403, read({"reference": "product-rules"}, foreign).status_code)
        self.assertEqual(403, read({"reference": "product-rules"}).status_code)
        self.submitted.assert_not_called()
        self.edited.assert_not_called()

    def test_skill_reading_is_allowlisted_turn_scoped_and_never_submits_images(self):
        from services.dsh_agent.skills import RESEARCH_SKILLS, STYLE_SKILL, resolve_skills, skill_policy

        self.assertEqual([], resolve_skills(None))
        self.assertEqual([], resolve_skills([]))
        self.assertEqual([STYLE_SKILL], resolve_skills([STYLE_SKILL, STYLE_SKILL]))
        self.assertIn("read_skill", skill_policy([STYLE_SKILL]))
        context = self.turn()
        bridge = ImageToolBridge()
        self.addCleanup(bridge.close)
        settings = bridge.settings("alice", "skill-session")
        url = settings["DSH_AGENT_IMAGE_BASE_URL"] + "/skill-reference"
        headers = {"Authorization": "Bearer " + settings["DSH_AGENT_IMAGE_TOKEN"]}
        def read(args, auth=headers):
            return requests.post(url, headers=auth, json={"arguments": args}, timeout=5)
        with bridge.activate("skill-session", context):
            self.assertEqual(400, read({"skill_id": STYLE_SKILL}).status_code)
            for skill_id in RESEARCH_SKILLS:
                self.assertEqual(400, read({"skill_id": skill_id}).status_code)
                context.skill_ids = [skill_id]
                self.assertIn("系统集成约定", read({"skill_id": skill_id}).json()["content"])
                self.assertEqual(400, read({"skill_id": skill_id, "reference": "../../.env"}).status_code)
                self.assertEqual(403, read({"skill_id": skill_id}, {"Authorization": "Bearer " + bridge.token("bob", "skill-session")}).status_code)
                context.skill_ids = []
            context.skill_ids = [STYLE_SKILL]
            self.assertIn("Workflow", read({"skill_id": STYLE_SKILL}).json()["content"])
            self.assertIn("Product Commerce Visual", read({"skill_id": STYLE_SKILL, "reference": "style-library"}).json()["content"])
            for args in ({"skill_id": "../../.env"}, {"skill_id": STYLE_SKILL, "reference": "../../.env"},
                         {"skill_id": STYLE_SKILL, "url": "http://localhost"}, {"skill_id": STYLE_SKILL, "reference": []}):
                self.assertEqual(400, read(args).status_code)
            self.assertEqual(403, read({"skill_id": STYLE_SKILL}, {"Authorization": "Bearer " + bridge.token("bob", "skill-session")}).status_code)
            context.skill_ids = []
            self.assertEqual(400, read({"skill_id": STYLE_SKILL}).status_code)
        self.assertEqual(403, read({"skill_id": STYLE_SKILL}).status_code)
        self.submitted.assert_not_called()
        self.edited.assert_not_called()

    def test_expert_generation_keeps_existing_edit_idempotency_and_retry_guard(self):
        context = self.turn([self.upload()["media_id"]])
        context.expert_id = "ecommerce-design"
        bridge = ImageToolBridge()
        self.addCleanup(bridge.close)
        settings = bridge.settings("alice", "expert-image")
        url = settings["DSH_AGENT_IMAGE_BASE_URL"] + "/generate"
        headers = {"Authorization": "Bearer " + settings["DSH_AGENT_IMAGE_TOKEN"]}
        payload = {"call_id": "one-edit", "arguments": {"prompt": "保留瓶型、Logo、标签，改白底", "model": "gpt-image-2.5-flare", "aspect_ratio": "16:9"}}
        with bridge.activate("expert-image", context):
            first = requests.post(url, headers=headers, json=payload, timeout=5)
            second = requests.post(url, headers=headers, json=payload, timeout=5)
            self.assertEqual(200, first.status_code)
            self.assertEqual(first.json(), second.json())
            context.allow_generation = False
            self.assertEqual(400, requests.post(url, headers=headers, json=payload, timeout=5).status_code)
        self.edited.assert_called_once()
        self.submitted.assert_not_called()
        self.assertEqual("dsh", self.edited.call_args.kwargs["source"])

    def test_upload_binding_and_conversation_history_restore(self):
        reference = self.upload()
        self.assertEqual((160, 90), (reference["width"], reference["height"]))
        context = self.turn([reference["media_id"]])
        prompt = context.prompt("改成白底")
        self.assertEqual("image", prompt[-1]["type"])
        result = self.service.submit(context, "edit-1", {"prompt": "改成白底"})
        self.edited.assert_called_once()
        self.submitted.assert_not_called()
        self.assertEqual(1, len(self.edited.call_args.kwargs["images"]))
        self.store.complete_turn(self.cid, "alice", content="已提交", model="tt-5.6-sol", usage={})
        history = self.service.attach_messages(self.store.list_messages(self.cid), self.identity)
        media = history["items"][0]["media"]
        self.assertEqual({reference["media_id"], result["media_id"]}, {item["media_id"] for item in media})

    def test_generated_image_can_be_edited_in_followup_turn(self):
        context = self.turn()
        first = self.service.submit(context, "generate", {"prompt": "画商品"})
        self.tasks[first["task_id"]].update(status="success", data=[{"url": "https://storage.example/generated.png"}])
        self.store.complete_turn(self.cid, "alice", content="已提交", model="tt-5.6-sol", usage={})
        result = self.service.submit(self.turn(), "edit", {"prompt": "改为蓝色", "reference_ids": [first["media_id"] + ":0"]})
        self.assertEqual("queued", result["task"]["status"])
        self.assertEqual(["https://storage.example/generated.png"], self.edited.call_args.kwargs["image_urls"])

    def test_invalid_foreign_and_already_sent_references_do_not_claim_turn(self):
        foreign = self.store.create_conversation({"id": "bob"})
        with self.assertRaises(KeyError):
            self.service.upload(foreign["conversation_id"], self.identity, b"not image", "x.png", "")
        for data in (b"", b"not image", b"x" * (images.MAX_IMAGE_BYTES + 1)):
            with self.assertRaises(ValueError):
                self.service.upload(self.cid, self.identity, data, "fake.png", "")
        with self.assertRaises(ValueError):
            self.store.begin_turn(self.cid, "alice", "hello", reference_ids=["forged"])
        self.assertEqual([], self.store.list_messages(self.cid)["items"])
        context = self.turn()
        with self.assertRaises(ValueError):
            self.service.submit(context, "forged", {"prompt": "edit", "reference_ids": ["foreign"]})
        self.edited.assert_not_called()
        self.submitted.assert_not_called()

    def test_uncertain_submit_is_not_resubmitted_and_never_looks_successful(self):
        context = self.turn()
        self.submitted.side_effect = TimeoutError("lost local response")
        with self.assertRaises(TimeoutError):
            self.service.submit(context, "uncertain", {"prompt": "商品图"})
        second = self.service.submit(context, "uncertain", {"prompt": "商品图"})
        self.assertEqual("unconfirmed", second["task"]["status"])
        self.assertEqual(1, self.submitted.call_count)
        self.assertEqual("unconfirmed", self.events[0]["task"]["status"])
        with self.assertRaises(ValueError):
            self.service.submit(context, "another-request-after-timeout", {"prompt": "different wording"})
        self.assertEqual(1, self.submitted.call_count)

    def test_limits_options_and_empty_hydration(self):
        self.assertEqual([], self.service.hydrate(self.identity, []))
        self.listed.assert_not_called()
        for options in ({"model": "foreign"}, {"aspect_ratio": "16:9"}, {"image_size": "4K"}, {"model": "banana-2", "background": "transparent"}):
            with self.assertRaises(ValueError):
                images.normalize_options(options)
        context = self.turn()
        for i in range(2):
            self.service.submit(context, f"count-{i}", {"prompt": f"image {i}", "n": 4})
        with self.assertRaises(ValueError):
            self.service.submit(context, "overflow", {"prompt": "ninth image"})
        self.assertEqual(2, self.submitted.call_count)

    def test_parameter_guidance_and_tool_values_override_backend_defaults(self):
        for model in images.image_model_catalog():
            with self.subTest(model=model["id"]):
                self.assertEqual(model["id"], images.normalize_options({"model": model["id"]})["model"])
                for ratio in model["ratios"]:
                    images.normalize_options({"model": model["id"], "aspect_ratio": ratio})
        banana = images.normalize_options({"model": "banana-2", "aspect_ratio": "auto"})
        params = images.relay._media_image_params(banana, upstream_model="banana-2", requested_size=banana["size"], reference_urls=[])
        self.assertEqual("auto", params["aspectRatio"])
        context = self.turn()
        prompt = context.prompt("请生成3张3:2高质量图片")
        self.assertIn("model_capabilities", prompt)
        self.assertIn('"ratios": ["auto", "1:1", "3:2", "2:3"]', prompt)
        self.assertIn('"resolutions": []', prompt)
        self.assertIn("用户本轮明确要求", prompt)
        self.assertIn("先说明限制并询问用户", prompt)
        for args in ({"aspect_ratio": "16:9"}, {"image_size": "4K"}):
            with self.assertRaises(ValueError):
                self.service.submit(context, "unsupported", {"prompt": "商品图", **args})
        self.submitted.assert_not_called()
        self.service.submit(context, "chosen-params", {
            "prompt": "三张3:2商品图", "n": 3, "aspect_ratio": "3:2", "quality": "high", "background": "opaque",
        })
        kwargs = self.submitted.call_args.kwargs
        self.assertEqual((3, "3:2", "high", "opaque"), (kwargs["n"], kwargs["aspect_ratio"], kwargs["quality"], kwargs["background"]))
        self.assertNotIn("image_size", kwargs)
        context.options = images.normalize_options({"model": "banana-2"})
        self.service.submit(context, "banana-params", {"prompt": "商品图", "aspect_ratio": "16:9", "image_size": "4K"})
        self.assertEqual("16:9", self.submitted.call_args.kwargs["aspect_ratio"])
        self.assertEqual("4K", self.submitted.call_args.kwargs["image_size"])

    def test_api_upload_query_admin_and_cross_user_access(self):
        app = FastAPI()
        app.include_router(routes.create_router())
        with TestClient(app) as client, patch.object(routes, "dsh_agent_store", self.store), patch.object(
            routes, "require_identity", side_effect=lambda token: self.identity if token == "alice" else {"id": "bob"}
        ), patch.object(routes, "require_admin"):
            path = f"/api/dsh-agent/conversations/{self.cid}"
            data = io.BytesIO()
            Image.new("RGB", (2, 2)).save(data, "PNG")
            response = client.post(path + "/images", headers={"Authorization": "bob"}, files={"file": ("a.png", data.getvalue(), "image/png")})
            self.assertEqual(404, response.status_code)
            response = client.post(path + "/images", headers={"Authorization": "alice"}, files={"file": ("a.png", data.getvalue(), "image/png")})
            self.assertEqual(200, response.status_code)
            context = self.turn([response.json()["media_id"]])
            task = self.service.submit(context, "api", {"prompt": "商品"})
            query = client.post(path + "/images/query", headers={"Authorization": "bob"}, json={"ids": [task["media_id"]]})
            self.assertEqual([], query.json()["items"])
            query = client.post(path + "/images/query", headers={"Authorization": "alice"}, json={"ids": [task["media_id"]]})
            self.assertEqual("queued", query.json()["items"][0]["task"]["status"])
            admin = client.get(f"/api/dsh-agent/admin/conversations/{self.cid}")
            self.assertEqual(2, len(admin.json()["items"][0]["media"]))

    def test_loopback_capability_is_session_scoped_and_expires_after_turn(self):
        bridge = ImageToolBridge()
        self.addCleanup(bridge.close)
        settings = bridge.settings("alice", "session-one")
        url = settings["DSH_AGENT_IMAGE_BASE_URL"] + "/generate"
        headers = {"Authorization": "Bearer " + settings["DSH_AGENT_IMAGE_TOKEN"]}
        body = {"call_id": "native-id", "arguments": {"prompt": "test image"}}
        self.assertEqual(403, requests.post(url, headers=headers, json=body, timeout=5).status_code)
        with bridge.activate("session-one", self.turn()):
            wrong = {"Authorization": "Bearer " + bridge.token("bob", "session-one")}
            self.assertEqual(403, requests.post(url, headers=wrong, json=body, timeout=5).status_code)
            result = requests.post(url, headers=headers, json=body, timeout=5)
            self.assertEqual(200, result.status_code)
            self.assertEqual("queued", result.json()["status"])
        self.assertEqual(403, requests.post(url, headers=headers, json=body, timeout=5).status_code)

    @unittest.skipUnless(os.getenv("DSH_RUNTIME_SMOKE") == "1", "opt-in local runtime smoke")
    def test_installed_runtime_calls_native_image_plugin_against_mock_only(self):
        calls = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                calls.append(payload)
                index = len(calls)
                tool = index in (1, 3, 5, 6, 7, 9, 10)
                function = {"name": "read_expert_reference", "arguments": json.dumps({"reference": "product-rules" if index == 5 else "style-library"})} if index in (5, 6) else {"name": "generate_image", "arguments": json.dumps({"prompt": f"mock image {index}", "aspect_ratio": "3:2", "quality": "high", "n": 3})}
                if index in (9, 10):
                    function = {"name": "read_skill", "arguments": json.dumps({"skill_id": "gpt-image-2-style-library", **({"reference": "style-library"} if index == 10 else {})})}
                if index == 1:
                    function["arguments"] = json.dumps({"prompt": "three animals", "prompts": ["A snow leopard", "A red fox", "A monkey"], "aspect_ratio": "3:2", "quality": "high", "n": 3})
                delta = {"role": "assistant", "tool_calls": [{"index": 0, "id": f"native-image-call-{index}", "type": "function", "function": function}]} if tool else {"role": "assistant", "content": "图片任务已排队"}
                frames = [
                    {"id": "mock", "object": "chat.completion.chunk", "model": "tt-6-sol", "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                    {"id": "mock", "object": "chat.completion.chunk", "model": "tt-6-sol", "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls" if tool else "stop"}]},
                ]
                data = ("".join(f"data: {json.dumps(frame)}\n\n" for frame in frames) + "data: [DONE]\n\n").encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        context = self.turn()
        from services.dsh_agent.image_tools import image_tool_bridge
        with patch.object(runtime, "DATA_DIR", Path(self.tmp.name)), patch.object(runtime, "runtime_pool", pool := RuntimePool()), patch.object(
            runtime.config, "get_openai_relay_settings", return_value={"base_url": f"http://127.0.0.1:{server.server_port}/v1", "api_key": "mock-only"}
        ), patch.object(runtime, "current_relay_account", return_value=None), patch.object(runtime, "search_available", return_value=False):
            try:
                with image_tool_bridge().activate("session-" + "f" * 32, context):
                    result = runtime._execute(context.prompt("生成图片"), model="tt-6-sol", owner_id="alice", dsh_session_id="session-" + "f" * 32, has_history=False, history=[], on_notification=lambda notice: None)
                self.assertEqual("图片任务已排队", result["content"])
                self.assertIn("generate_image", json.dumps(calls[0]))
                self.assertIn("queued", json.dumps(calls[1]))
                self.assertEqual(3, self.submitted.call_count)
                self.assertEqual(["A snow leopard", "A red fox", "A monkey"], [call.kwargs["prompt"] for call in self.submitted.call_args_list])
                self.assertEqual(("3:2", "high", 1), tuple(self.submitted.call_args.kwargs[key] for key in ("aspect_ratio", "quality", "n")))
                tool_result = next(message for message in calls[1]["messages"] if message["role"] == "tool")
                self.assertEqual(3, len(json.loads(tool_result["content"])["images"]))
                self.store.complete_turn(self.cid, "alice", content="图片任务已排队", model="tt-6-sol", usage={})
                reference = self.upload()
                second = self.turn([reference["media_id"]])
                with image_tool_bridge().activate("session-" + "f" * 32, second):
                    result = runtime._execute(second.prompt("修改这张图片"), model="tt-6-sol", owner_id="alice", dsh_session_id="session-" + "f" * 32, has_history=True, history=[], on_notification=lambda notice: None)
                self.assertEqual("图片任务已排队", result["content"])
                self.edited.assert_called_once()
                self.assertIn("image_url", json.dumps(calls[2]))
                self.assertEqual(4, len(calls))
                self.store.complete_turn(self.cid, "alice", content="已提交", model="tt-6-sol", usage={})
                expert = self.turn()
                expert.expert_id = "ecommerce-design"
                with image_tool_bridge().activate("session-" + "f" * 32, expert):
                    result = runtime._execute(expert.prompt("生成商品主图"), model="tt-6-sol", expert_id=expert.expert_id, owner_id="alice", dsh_session_id="session-" + "f" * 32, has_history=True, history=[], on_notification=lambda _: None)
                self.assertEqual(8, len(calls))
                system = json.dumps([message for message in calls[4]["messages"] if message["role"] == "system"], ensure_ascii=False)
                self.assertIn("identity_constraints", system)
                self.assertIn("商品一致性与合规", json.dumps(calls[5], ensure_ascii=False))
                self.assertIn("电商构图与风格", json.dumps(calls[6], ensure_ascii=False))
                self.assertEqual(4, self.submitted.call_count)
                self.store.complete_turn(self.cid, "alice", content=result["content"], model="tt-6-sol", usage={})
                skilled = self.turn()
                skilled.skill_ids = ["gpt-image-2-style-library"]
                with image_tool_bridge().activate("session-" + "f" * 32, skilled):
                    runtime._execute(skilled.prompt("只给主图构图建议"), model="tt-6-sol", skill_ids=skilled.skill_ids,
                                     owner_id="alice", dsh_session_id="session-" + "f" * 32, has_history=True, history=[], on_notification=lambda _: None)
                self.assertEqual(11, len(calls))
                self.assertIn("Workflow", json.dumps(calls[9]))
                self.assertIn("Product Commerce Visual", json.dumps(calls[10]))
                self.assertEqual(4, self.submitted.call_count)
                self.store.complete_turn(self.cid, "alice", content="设计建议", model="tt-6-sol", usage={})
                normal = self.turn()
                with image_tool_bridge().activate("session-" + "f" * 32, normal):
                    runtime._execute(normal.prompt("仅给建议"), model="tt-6-sol", owner_id="alice", dsh_session_id="session-" + "f" * 32, has_history=True, history=[], on_notification=lambda _: None)
                system = json.dumps([message for message in calls[11]["messages"] if message["role"] == "system"], ensure_ascii=False)
                self.assertIn("当前未选择专家", system)
                self.assertIn("本轮未授权 GPT Image 设计库", system)
                self.assertNotIn("identity_constraints", system)
                self.assertEqual(4, self.submitted.call_count)
            finally:
                pool.close()


if __name__ == "__main__":
    unittest.main()
