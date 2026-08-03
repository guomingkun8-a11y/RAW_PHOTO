from __future__ import annotations

import base64
import io
import unittest
import zipfile
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.image_tasks as image_tasks_module


AUTH_HEADERS = {"Authorization": "Bearer gmkraw"}
TEST_IDENTITY = {"id": "test-user", "username": "tester", "name": "Tester", "role": "user"}
PNG_BYTES = b"\x89PNG\r\n\x1a\n"
DATA_IMAGE_URL = f"data:image/png;base64,{base64.b64encode(PNG_BYTES).decode('ascii')}"


class FakeImageTaskService:
    def __init__(self):
        self.generation_calls = []
        self.edit_calls = []
        self.cancel_calls = []

    def submit_generation(self, identity, **kwargs):
        self.generation_calls.append((identity, kwargs))
        return {
            "id": kwargs["client_task_id"],
            "status": "success",
            "mode": "generate",
            "created_at": "2026-01-01 00:00:00",
            "updated_at": "2026-01-01 00:00:00",
            "data": [{"url": f"{kwargs['base_url']}/images/fake.png"}],
        }

    def submit_edit(self, identity, **kwargs):
        self.edit_calls.append((identity, kwargs))
        return {
            "id": kwargs["client_task_id"],
            "status": "queued",
            "mode": "edit",
            "created_at": "2026-01-01 00:00:00",
            "updated_at": "2026-01-01 00:00:00",
        }

    def list_tasks(self, _identity, ids):
        return {
            "items": [
                {
                    "id": task_id,
                    "status": "success",
                    "mode": "generate",
                    "created_at": "2026-01-01 00:00:00",
                    "updated_at": "2026-01-01 00:00:00",
                    "data": [{"url": "http://testserver/images/fake.png"}],
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
            "mode": "generate",
            "created_at": "2026-01-01 00:00:00",
            "updated_at": "2026-01-01 00:00:01",
            "error": "任务已中止",
        }


class ImageTasksApiTests(unittest.TestCase):
    def setUp(self):
        self.fake_service = FakeImageTaskService()
        self.identity_patcher = mock.patch.object(image_tasks_module, "require_identity", return_value=TEST_IDENTITY)
        self.identity_patcher.start()
        self.addCleanup(self.identity_patcher.stop)
        self.service_patcher = mock.patch.object(image_tasks_module, "image_task_service", self.fake_service)
        self.service_patcher.start()
        self.addCleanup(self.service_patcher.stop)
        self.storage_patcher = mock.patch.object(
            image_tasks_module.image_storage_service,
            "get_bytes",
            return_value=PNG_BYTES,
        )
        self.storage_patcher.start()
        self.addCleanup(self.storage_patcher.stop)
        self.public_url_patcher = mock.patch.object(
            image_tasks_module.openai_relay_service,
            "requires_public_image_urls",
            return_value=False,
        )
        self.public_url_patcher.start()
        self.addCleanup(self.public_url_patcher.stop)
        app = FastAPI()
        app.include_router(image_tasks_module.create_router())
        self.client = TestClient(app)

    def test_create_generation_task(self):
        response = self.client.post(
            "/api/image-tasks/generations",
            headers=AUTH_HEADERS,
            json={
                "client_task_id": "task-1",
                "prompt": "cat",
                "model": "gpt-image-2",
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["id"], "task-1")
        self.assertEqual(payload["status"], "success")
        self.assertEqual(len(self.fake_service.generation_calls), 1)

    def test_create_generation_task_passes_batch_fields(self):
        response = self.client.post(
            "/api/image-tasks/generations",
            headers=AUTH_HEADERS,
            json={
                "client_task_id": "batch-task-1",
                "prompt": "cat",
                "model": "gpt-image-2",
                "batch_id": "batch-1",
                "batch_index": 1,
                "batch_total": 3,
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        kwargs = self.fake_service.generation_calls[0][1]
        self.assertEqual(kwargs["batch_id"], "batch-1")
        self.assertEqual(kwargs["batch_index"], 1)
        self.assertEqual(kwargs["batch_total"], 3)

    def test_create_edit_task_accepts_multiple_images(self):
        """测试图片编辑任务接口支持多个上传图片。"""
        response = self.client.post(
            "/api/image-tasks/edits",
            headers=AUTH_HEADERS,
            data={
                "client_task_id": "edit-1",
                "prompt": "edit",
                "model": "gpt-image-2",
            },
            files=[
                ("image", ("one.png", b"one", "image/png")),
                ("image", ("two.png", b"two", "image/png")),
            ],
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["id"], "edit-1")
        self.assertEqual(len(self.fake_service.edit_calls), 1)
        images = self.fake_service.edit_calls[0][1]["images"]
        self.assertEqual(len(images), 2)

    def test_create_edit_task_accepts_image_url(self):
        """测试图片编辑任务接口支持表单 image_url 引用。"""
        response = self.client.post(
            "/api/image-tasks/edits",
            headers=AUTH_HEADERS,
            data={
                "client_task_id": "edit-url-1",
                "prompt": "edit",
                "model": "gpt-image-2",
                "image_url": DATA_IMAGE_URL,
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(self.fake_service.edit_calls), 1)
        images = self.fake_service.edit_calls[0][1]["images"]
        self.assertEqual(images, [(PNG_BYTES, "image_url.png", "image/png")])

    def test_create_edit_task_preserves_http_image_url(self):
        read_sources = mock.AsyncMock(return_value=[])
        with (
            mock.patch.object(image_tasks_module, "read_image_sources", read_sources),
            mock.patch.object(image_tasks_module.openai_relay_service, "requires_public_image_urls", return_value=True),
        ):
            response = self.client.post(
                "/api/image-tasks/edits",
                headers=AUTH_HEADERS,
                data={
                    "client_task_id": "edit-url-2",
                    "prompt": "edit",
                    "model": "gpt-image-2",
                    "image_url": "https://cdn.example.test/input.png",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(self.fake_service.edit_calls), 1)
        self.assertEqual(self.fake_service.edit_calls[0][1]["image_urls"], ["https://cdn.example.test/input.png"])
        read_sources.assert_not_awaited()

    def test_create_edit_task_accepts_preserve_subject_flag(self):
        response = self.client.post(
            "/api/image-tasks/edits",
            headers=AUTH_HEADERS,
            data={
                "client_task_id": "edit-preserve-1",
                "prompt": "put the product on a marble table",
                "model": "gpt-image-2",
                "preserve_subject": "true",
            },
            files=[("image", ("product.png", b"product", "image/png"))],
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(self.fake_service.edit_calls), 1)
        self.assertIs(self.fake_service.edit_calls[0][1]["preserve_subject"], True)

    def test_list_tasks_reports_missing_ids(self):
        response = self.client.post(
            "/api/image-tasks/query",
            headers=AUTH_HEADERS,
            json={"ids": ["task-1", "missing"]},
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual([item["id"] for item in payload["items"]], ["task-1"])
        self.assertEqual(payload["missing_ids"], ["missing"])

    def test_download_zip_supports_chinese_filename(self):
        response = self.client.post(
            "/api/image-tasks/download-zip",
            headers=AUTH_HEADERS,
            json={
                "folder_name": "商品图片-任务1",
                "items": [
                    {
                        "task_id": "task-1",
                        "filename": "结果图.png",
                    }
                ],
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["content-type"], "application/zip")
        self.assertIn('filename="AI-Image-Results.zip"', response.headers["content-disposition"])
        self.assertIn("filename*=UTF-8''", response.headers["content-disposition"])
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            self.assertEqual(archive.namelist(), ["商品图片-任务1/结果图.png"])

    def test_download_zip_fetches_external_result_url(self):
        def list_tasks(_identity, ids):
            return {
                "items": [
                    {
                        "id": task_id,
                        "status": "success",
                        "mode": "generate",
                        "created_at": "2026-01-01 00:00:00",
                        "updated_at": "2026-01-01 00:00:00",
                        "data": [{"url": "https://cdn.example.test/result.png"}],
                    }
                    for task_id in ids
                ],
                "missing_ids": [],
            }

        with (
            mock.patch.object(self.fake_service, "list_tasks", side_effect=list_tasks),
            mock.patch.object(image_tasks_module, "_download_image_url", return_value=(PNG_BYTES, "result.png", "image/png")) as download,
        ):
            response = self.client.post(
                "/api/image-tasks/download-zip",
                headers=AUTH_HEADERS,
                json={
                    "folder_name": "results",
                    "items": [{"task_id": "external-1", "filename": "one.png"}],
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        download.assert_called_once_with("https://cdn.example.test/result.png")
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            self.assertEqual(archive.namelist(), ["results/one.png"])

    def test_cancel_image_task(self):
        response = self.client.post("/api/image-tasks/task-1/cancel", headers=AUTH_HEADERS)

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["id"], "task-1")
        self.assertEqual(payload["status"], "canceled")
        self.assertEqual(len(self.fake_service.cancel_calls), 1)


if __name__ == "__main__":
    unittest.main()
