from __future__ import annotations

import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.image_agent as image_agent_module


IDENTITY = {"id": "api-agent-user", "username": "tester", "name": "Tester", "role": "user"}


class ImageAgentApiTests(unittest.TestCase):
    def setUp(self):
        mock.patch.object(image_agent_module, "require_identity", return_value=IDENTITY).start()
        mock.patch.object(image_agent_module, "resume_cow_agent_run", return_value=None).start()
        self.addCleanup(mock.patch.stopall)
        app = FastAPI()
        app.include_router(image_agent_module.create_router())
        self.client = TestClient(app)

    def test_starts_agent_run_with_normalized_payload(self):
        expected = {"agentRun": {"runId": "run-1", "status": "pending"}}
        with mock.patch.object(image_agent_module, "start_cow_agent_run", return_value=expected) as start:
            response = self.client.post(
                "/api/image-agent/runs",
                headers={"Authorization": "Bearer test"},
                json={
                    "prompt": "生成高级商品主图",
                    "mode": "generate",
                    "sceneType": "taobao_text_main",
                    "count": 1,
                    "conversationId": "conversation-1",
                    "turnId": "turn-1",
                    "useLongTermMemory": False,
                    "conversationContext": [{
                        "userRequest": "上一张做了产品主视觉",
                        "sceneName": "生活方式场景",
                        "proposalSummary": "窗边柔光",
                        "visualDirection": "安静自然",
                        "resultStatus": "success",
                    }],
                },
            )

        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(expected, response.json())
        payload = start.call_args.args[0]
        self.assertEqual("taobao_text_main", payload["scene_type"])
        self.assertEqual("turn-1", payload["turn_id"])
        self.assertEqual("cowagent", payload["agent_engine"])
        self.assertFalse(payload["use_long_term_memory"])
        self.assertEqual("上一张做了产品主视觉", payload["conversation_context"][0]["user_request"])
        self.assertEqual(IDENTITY, start.call_args.kwargs["identity"])

    def test_cowagent_engine_uses_parallel_runtime(self):
        expected = {"agentRun": {"runId": "cow-run-1", "status": "running"}}
        with mock.patch.object(image_agent_module, "start_cow_agent_run", return_value=expected) as start_cow:
            response = self.client.post(
                "/api/image-agent/runs",
                headers={"Authorization": "Bearer test"},
                json={
                    "prompt": "先分析汽车详情页，不要立即生图",
                    "agentEngine": "cowagent",
                    "conversationId": "conversation-cow",
                    "turnId": "turn-cow",
                },
            )

        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(expected, response.json())
        start_cow.assert_called_once()
        self.assertEqual("cowagent", start_cow.call_args.args[0]["agent_engine"])

    def test_rejects_removed_raw_engine(self):
        response = self.client.post(
            "/api/image-agent/runs",
            headers={"Authorization": "Bearer test"},
            json={"prompt": "使用旧 RAW 引擎", "agentEngine": "raw"},
        )

        self.assertEqual(422, response.status_code, response.text)

    def test_start_requires_prompt_or_reference_image(self):
        response = self.client.post(
            "/api/image-agent/runs",
            headers={"Authorization": "Bearer test"},
            json={"prompt": "", "agentEngine": "cowagent"},
        )

        self.assertEqual(400, response.status_code, response.text)

    def test_edit_requires_reference_images(self):
        response = self.client.post(
            "/api/image-agent/runs",
            headers={"Authorization": "Bearer test"},
            json={"prompt": "修改商品图", "mode": "edit", "images": []},
        )

        self.assertEqual(400, response.status_code, response.text)

    def test_edit_rejects_empty_reference_image_payloads(self):
        response = self.client.post(
            "/api/image-agent/runs",
            headers={"Authorization": "Bearer test"},
            json={
                "prompt": "修改商品图",
                "mode": "edit",
                "images": [{"name": "product.png", "type": "image/png", "url": ""}],
            },
        )

        self.assertEqual(400, response.status_code, response.text)

    def test_edit_can_inherit_reference_images_from_conversation(self):
        expected = {"agentRun": {"runId": "run-inherited", "status": "pending"}}
        with mock.patch.object(image_agent_module, "start_cow_agent_run", return_value=expected) as start:
            response = self.client.post(
                "/api/image-agent/runs",
                headers={"Authorization": "Bearer test"},
                json={
                    "prompt": "Change only the background",
                    "mode": "edit",
                    "images": [],
                    "inheritReferenceImages": True,
                    "conversationId": "conversation-1",
                    "turnId": "turn-2",
                },
            )

        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(expected, response.json())
        self.assertTrue(start.call_args.args[0]["inherit_reference_images"])

    def test_resumes_waiting_image_agent_run(self):
        expected = {"agentRun": {"runId": "run-1", "status": "running"}}
        with mock.patch.object(image_agent_module, "resume_cow_agent_run", return_value=expected) as resume:
            response = self.client.post(
                "/api/image-agent/runs/run-1/resume",
                headers={"Authorization": "Bearer test"},
                json={
                    "message": "确认，开始生成",
                    "images": [{
                        "name": "product.png",
                        "type": "image/png",
                        "dataUrl": "data:image/png;base64,ZmFrZQ==",
                    }],
                },
            )

        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(expected, response.json())
        self.assertEqual(("run-1", "确认，开始生成"), resume.call_args.args)
        self.assertEqual(IDENTITY, resume.call_args.kwargs["identity"])
        self.assertEqual("product.png", resume.call_args.kwargs["images"][0]["name"])
        self.assertEqual(
            "data:image/png;base64,ZmFrZQ==",
            resume.call_args.kwargs["images"][0]["data_url"],
        )

    def test_resume_accepts_reference_image_without_message(self):
        expected = {"agentRun": {"runId": "run-image-only", "status": "running"}}
        with mock.patch.object(image_agent_module, "resume_cow_agent_run", return_value=expected) as resume:
            response = self.client.post(
                "/api/image-agent/runs/run-image-only/resume",
                headers={"Authorization": "Bearer test"},
                json={
                    "images": [{
                        "name": "product.png",
                        "type": "image/png",
                        "dataUrl": "data:image/png;base64,ZmFrZQ==",
                    }],
                },
            )

        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(("run-image-only", ""), resume.call_args.args)
        self.assertEqual("product.png", resume.call_args.kwargs["images"][0]["name"])

    def test_resume_rejects_another_users_run(self):
        with mock.patch.object(
            image_agent_module,
            "resume_cow_agent_run",
            side_effect=PermissionError("agent run does not belong to this user"),
        ):
            response = self.client.post(
                "/api/image-agent/runs/run-2/resume",
                headers={"Authorization": "Bearer test"},
                json={"message": "确认"},
            )

        self.assertEqual(403, response.status_code, response.text)

    def test_resume_prefers_cowagent_runtime(self):
        expected = {"agentRun": {"runId": "cow-next", "status": "pending"}}
        with mock.patch.object(image_agent_module, "resume_cow_agent_run", return_value=expected) as resume_cow:
            response = self.client.post(
                "/api/image-agent/runs/cow-old/resume",
                headers={"Authorization": "Bearer test"},
                json={"message": "确认并执行", "folderId": "folder-1"},
            )

        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(expected, response.json())
        self.assertEqual(("cow-old", "确认并执行"), resume_cow.call_args.args)
        self.assertEqual("folder-1", resume_cow.call_args.kwargs["folder_id"])

    def test_memory_management_is_scoped_to_authenticated_owner(self):
        service_path = "services.ecommerce.ecommerce_agent_memory_service.ecommerce_agent_memory_service"
        item = {
            "memoryId": "7",
            "content": "详情页避免纯白背景",
            "category": "constraint",
            "scope": "user",
        }
        with (
            mock.patch(f"{service_path}.list_long_term_memories", return_value=[item]) as list_memory,
            mock.patch(f"{service_path}.upsert_long_term_memory", return_value=item) as create_memory,
            mock.patch(f"{service_path}.update_long_term_memory", return_value={**item, "content": "详情页使用真实场景"}) as update_memory,
            mock.patch(f"{service_path}.review_long_term_memory", return_value={**item, "confirmed": True, "status": "active"}) as review_memory,
            mock.patch(f"{service_path}.delete_long_term_memory", return_value=True) as delete_memory,
            mock.patch(f"{service_path}.delete_all_long_term_memories", return_value=3) as clear_memory,
        ):
            listed = self.client.get(
                "/api/image-agent/memory?conversationId=conversation-1",
                headers={"Authorization": "Bearer test"},
            )
            created = self.client.post(
                "/api/image-agent/memory",
                headers={"Authorization": "Bearer test"},
                json={
                    "content": "详情页避免纯白背景",
                    "category": "constraint",
                    "scope": "conversation",
                    "conversationId": "conversation-1",
                },
            )
            updated = self.client.patch(
                "/api/image-agent/memory/7",
                headers={"Authorization": "Bearer test"},
                json={"content": "详情页使用真实场景", "category": "constraint", "scope": "user"},
            )
            reviewed = self.client.post(
                "/api/image-agent/memory/7/review",
                headers={"Authorization": "Bearer test"},
                json={"decision": "approve"},
            )
            deleted = self.client.delete(
                "/api/image-agent/memory/7",
                headers={"Authorization": "Bearer test"},
            )
            cleared = self.client.delete(
                "/api/image-agent/memory",
                headers={"Authorization": "Bearer test"},
            )

        self.assertEqual(200, listed.status_code, listed.text)
        self.assertEqual(200, created.status_code, created.text)
        self.assertEqual(200, updated.status_code, updated.text)
        self.assertEqual(200, reviewed.status_code, reviewed.text)
        self.assertEqual(200, deleted.status_code, deleted.text)
        self.assertEqual({"ok": True, "deleted": 3}, cleared.json())
        self.assertEqual("api-agent-user", list_memory.call_args.kwargs["owner_id"])
        self.assertFalse(list_memory.call_args.kwargs["include_pending"])
        self.assertEqual("conversation-1", create_memory.call_args.kwargs["scope_id"])
        self.assertEqual("api-agent-user", update_memory.call_args.kwargs["owner_id"])
        self.assertEqual("approve", review_memory.call_args.kwargs["decision"])
        self.assertEqual(7, delete_memory.call_args.kwargs["memory_id"])
        self.assertEqual("api-agent-user", clear_memory.call_args.kwargs["owner_id"])

    def test_folder_upload_preserves_relative_names(self):
        expected = {"folderId": "folder-1", "itemCount": 2}
        with mock.patch.object(
            image_agent_module.professional_folder_asset_service,
            "create_folder",
            return_value=expected,
        ) as create_folder:
            response = self.client.post(
                "/api/image-agent/folders",
                headers={"Authorization": "Bearer test"},
                files=[
                    ("files", ("cover.png", b"cover", "image/png")),
                    ("files", ("scene.png", b"scene", "image/png")),
                    ("relative_names", (None, "商品A/主图/cover.png")),
                    ("relative_names", (None, "商品A/场景/scene.png")),
                ],
                data={"folder_name": "商品A"},
            )

        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(expected, response.json())
        payloads = create_folder.call_args.kwargs["files"]
        self.assertEqual("商品A/主图/cover.png", payloads[0][3])
        self.assertEqual("商品A/场景/scene.png", payloads[1][3])


if __name__ == "__main__":
    unittest.main()
