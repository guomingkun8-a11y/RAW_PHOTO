from __future__ import annotations

import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.video_generation as video_generation_module


AUTH_HEADERS = {"Authorization": "Bearer gmkraw"}
TEST_IDENTITY = {"id": "test-user", "username": "tester", "name": "Tester", "role": "user"}
ADMIN_IDENTITY = {"id": "admin-user", "username": "admin", "name": "Admin", "role": "admin"}


class FakeVideoGenerationTaskService:
    def __init__(self):
        self.submit_calls = []
        self.cancel_calls = []

    def submit_task(self, identity, **kwargs):
        self.submit_calls.append((identity, kwargs))
        return {
            "id": kwargs["client_task_id"],
            "status": "queued",
            "mode": kwargs["mode"],
            "model": kwargs["model"],
            "created_at": "2026-01-01 00:00:00",
            "updated_at": "2026-01-01 00:00:00",
        }

    def list_tasks(self, _identity, ids):
        return {
            "items": [
                {
                    "id": task_id,
                    "status": "success",
                    "mode": "text_to_video",
                    "model": "video-model",
                    "created_at": "2026-01-01 00:00:00",
                    "updated_at": "2026-01-01 00:00:00",
                    "video_url": "https://cdn.example.test/result.mp4",
                }
                for task_id in ids
                if task_id != "missing"
            ],
            "missing_ids": [task_id for task_id in ids if task_id == "missing"],
        }

    def cancel_task(self, identity, task_id):
        self.cancel_calls.append((identity, task_id))
        return {
            "id": task_id,
            "status": "canceled",
            "mode": "text_to_video",
            "created_at": "2026-01-01 00:00:00",
            "updated_at": "2026-01-01 00:00:01",
        }

    def monitoring_snapshot(self):
        return {"enabled": True, "queue_enabled": True, "queue_depth": 0}


class VideoGenerationApiTests(unittest.TestCase):
    def setUp(self):
        self.fake_service = FakeVideoGenerationTaskService()
        self.identity_patcher = mock.patch.object(video_generation_module, "require_identity", return_value=TEST_IDENTITY)
        self.identity_patcher.start()
        self.addCleanup(self.identity_patcher.stop)
        self.admin_patcher = mock.patch.object(video_generation_module, "require_admin", return_value=ADMIN_IDENTITY)
        self.admin_patcher.start()
        self.addCleanup(self.admin_patcher.stop)
        self.filter_patcher = mock.patch.object(video_generation_module, "check_request", return_value=None)
        self.filter_patcher.start()
        self.addCleanup(self.filter_patcher.stop)
        self.service_patcher = mock.patch.object(video_generation_module, "video_generation_task_service", self.fake_service)
        self.service_patcher.start()
        self.addCleanup(self.service_patcher.stop)
        app = FastAPI()
        app.include_router(video_generation_module.create_router())
        self.client = TestClient(app)

    def test_create_video_generation_task(self):
        response = self.client.post(
            "/api/video-generation/tasks",
            headers=AUTH_HEADERS,
            json={
                "client_task_id": "video-1",
                "prompt": "make a product video",
                "model": "video-model",
                "aspect_ratio": "9:16",
                "duration_secs": 6,
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["id"], "video-1")
        kwargs = self.fake_service.submit_calls[0][1]
        self.assertEqual(kwargs["aspect_ratio"], "9:16")
        self.assertEqual(kwargs["duration_secs"], 6)

    def test_query_video_generation_tasks(self):
        response = self.client.post(
            "/api/video-generation/tasks/query",
            headers=AUTH_HEADERS,
            json={"ids": ["video-1", "missing"]},
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["items"][0]["video_url"], "https://cdn.example.test/result.mp4")
        self.assertEqual(response.json()["missing_ids"], ["missing"])

    def test_cancel_video_generation_task(self):
        response = self.client.post("/api/video-generation/tasks/video-1/cancel", headers=AUTH_HEADERS)

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "canceled")
        self.assertEqual(self.fake_service.cancel_calls[0][1], "video-1")

    def test_video_generation_queue_requires_admin(self):
        response = self.client.get("/api/video-generation/queue", headers=AUTH_HEADERS)

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["queue_depth"], 0)


if __name__ == "__main__":
    unittest.main()

