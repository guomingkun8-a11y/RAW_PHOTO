from __future__ import annotations

import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.prompt_analysis as prompt_analysis_module


AUTH_HEADERS = {"Authorization": "Bearer test"}
TEST_IDENTITY = {"id": "test-user", "username": "tester", "name": "Tester", "role": "user"}


class PromptAnalysisApiTests(unittest.TestCase):
    def setUp(self):
        self.identity_patcher = mock.patch.object(
            prompt_analysis_module,
            "require_identity",
            return_value=TEST_IDENTITY,
        )
        self.identity_patcher.start()
        self.addCleanup(self.identity_patcher.stop)
        app = FastAPI()
        app.include_router(prompt_analysis_module.create_router())
        self.client = TestClient(app)

    def test_lists_professional_scenes(self):
        response = self.client.get("/api/image-prompt/scenes", headers=AUTH_HEADERS)

        self.assertEqual(200, response.status_code, response.text)
        payload = response.json()
        self.assertEqual(8, len(payload["items"]))
        self.assertIn("luxury_atmosphere", {item["id"] for item in payload["items"]})

    def test_professional_prompt_endpoint_accepts_prompt_only(self):
        expected = {
            "model": "gpt-5.6-sol",
            "productProfile": {"productName": "香水"},
            "sceneType": "luxury_atmosphere",
            "sceneName": "高端氛围主视觉",
            "visualDirection": "克制",
            "finalPrompt": "专业提示词",
            "negativePrompt": "错误文字",
            "warnings": [],
        }
        with (
            mock.patch.object(prompt_analysis_module, "build_professional_image_prompt", return_value=expected) as build,
            mock.patch.object(prompt_analysis_module, "filter_or_log", new=mock.AsyncMock()),
        ):
            response = self.client.post(
                "/api/image-prompt/professional",
                headers=AUTH_HEADERS,
                json={
                    "prompt": "生成高级香水广告图",
                    "sceneType": "luxury_atmosphere",
                    "preserveSubject": True,
                },
            )

        self.assertEqual(200, response.status_code, response.text)
        response_payload = response.json()
        self.assertEqual(expected, {key: response_payload[key] for key in expected})
        self.assertEqual("completed", response_payload["agentRun"]["status"])
        self.assertEqual(1, response_payload["agentRun"]["toolCalls"])
        run_response = self.client.get(
            f"/api/agent/runs/{response_payload['agentRun']['runId']}",
            headers=AUTH_HEADERS,
        )
        self.assertEqual(200, run_response.status_code, run_response.text)
        self.assertEqual(
            response_payload["agentRun"]["runId"],
            run_response.json()["agentRun"]["runId"],
        )
        payload = build.call_args.args[0]
        self.assertEqual("luxury_atmosphere", payload["scene_type"])
        self.assertTrue(payload["preserve_subject"])

    def test_professional_prompt_requires_prompt_or_image(self):
        response = self.client.post(
            "/api/image-prompt/professional",
            headers=AUTH_HEADERS,
            json={"prompt": "", "images": []},
        )

        self.assertEqual(400, response.status_code, response.text)

    def test_cancel_agent_run_endpoint(self):
        expected = {"runId": "run-1", "status": "canceled"}
        with mock.patch.object(prompt_analysis_module, "cancel_agent_run", return_value=expected) as cancel:
            response = self.client.post("/api/agent/runs/run-1/cancel", headers=AUTH_HEADERS)

        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(expected, response.json()["agentRun"])
        cancel.assert_called_once_with("run-1", TEST_IDENTITY)

    def test_agent_run_result_prefers_cowagent_payload(self):
        run = {"runId": "cow-run-1", "status": "completed"}
        result = {
            "promptPlan": {},
            "images": [],
            "qualityChecks": [],
            "revisionCount": 0,
            "optimizationRoute": "direct_consult",
            "modelUsage": {
                "dialogueCalls": 1,
                "visionCalls": 0,
                "totalCalls": 1,
                "imageGenerationCalls": 0,
            },
        }
        with (
            mock.patch.object(prompt_analysis_module, "get_agent_run", return_value=run),
            mock.patch.object(prompt_analysis_module, "get_cow_agent_result", return_value=result) as get_cow,
        ):
            response = self.client.get(
                "/api/agent/runs/cow-run-1?includeResult=true",
                headers=AUTH_HEADERS,
            )

        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(result, response.json()["agentRun"]["result"])
        get_cow.assert_called_once_with("cow-run-1", TEST_IDENTITY)


if __name__ == "__main__":
    unittest.main()
