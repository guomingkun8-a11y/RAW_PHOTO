from __future__ import annotations

import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.image_uploads as image_uploads_module
import api.image_agent as image_agent_module
from services.image.reference_image_uploader import ReferenceUploadResult
from services.ecommerce.professional_video_service import ProfessionalVideoUploadResult


AUTH_HEADERS = {"Authorization": "Bearer gmkraw"}
TEST_IDENTITY = {"id": "test-user", "username": "tester", "name": "Tester", "role": "user"}


class ImageUploadsApiTests(unittest.TestCase):
    def setUp(self):
        self.identity_patcher = mock.patch.object(image_uploads_module, "require_identity", return_value=TEST_IDENTITY)
        self.identity_patcher.start()
        self.addCleanup(self.identity_patcher.stop)
        app = FastAPI()
        app.include_router(image_uploads_module.create_router())
        self.client = TestClient(app)

    def test_preupload_returns_urls_in_file_order(self):
        results = [
            ReferenceUploadResult(
                url="https://cdn.example.test/one.png",
                sha256="a" * 64,
                filename="one.png",
                mime_type="image/png",
                file_size=3,
                cached=False,
                upload_ms=12,
            ),
            ReferenceUploadResult(
                url="https://cdn.example.test/two.png",
                sha256="b" * 64,
                filename="two.png",
                mime_type="image/png",
                file_size=3,
                cached=True,
                upload_ms=1,
            ),
        ]
        with (
            mock.patch.object(image_uploads_module.reference_image_uploader, "upload_images_detailed", return_value=results),
            mock.patch.object(image_uploads_module.reference_image_uploader, "metrics_snapshot", return_value={}),
        ):
            response = self.client.post(
                "/api/image-references/preupload",
                headers=AUTH_HEADERS,
                files=[
                    ("images", ("one.png", b"one", "image/png")),
                    ("images", ("two.png", b"two", "image/png")),
                ],
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual([item["url"] for item in payload["items"]], [item.url for item in results])
        self.assertEqual(payload["uploaded"], 1)
        self.assertEqual(payload["cache_hits"], 1)


class AgentVideoUploadsApiTests(unittest.TestCase):
    def setUp(self):
        self.identity_patcher = mock.patch.object(image_agent_module, "require_identity", return_value=TEST_IDENTITY)
        self.identity_patcher.start()
        self.addCleanup(self.identity_patcher.stop)
        app = FastAPI()
        app.include_router(image_agent_module.create_router())
        self.client = TestClient(app)

    def test_upload_agent_video_returns_pending_assets_without_auto_analysis(self):
        results = [
            ProfessionalVideoUploadResult(
                video_id="video-1",
                url="https://cdn.example.test/video.mp4",
                sha256="a" * 64,
                filename="video.mp4",
                mime_type="video/mp4",
                file_size=6,
                cached=False,
                status="uploaded",
                analysis_status="pending",
                conversation_id="conversation-1",
            )
        ]
        with (
            mock.patch.object(image_agent_module.professional_video_asset_service, "upload_many", return_value=results) as upload,
            mock.patch.object(image_agent_module.professional_video_asset_service, "request_analysis") as request_analysis,
            mock.patch.object(image_agent_module.config, "get_video_analysis_settings", return_value={"auto_enqueue_on_upload": False}),
        ):
            response = self.client.post(
                "/api/image-agent/videos",
                headers=AUTH_HEADERS,
                data={"conversation_id": "conversation-1"},
                files=[("videos", ("video.mp4", b"video1", "video/mp4"))],
            )

        self.assertEqual(response.status_code, 200, response.text)
        upload.assert_called_once()
        request_analysis.assert_not_called()
        payload = response.json()
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["items"][0]["videoId"], "video-1")
        self.assertEqual(payload["items"][0]["analysisStatus"], "pending")
        self.assertEqual(payload["items"][0]["conversationId"], "conversation-1")

    def test_upload_agent_video_rejects_empty_video(self):
        response = self.client.post(
            "/api/image-agent/videos",
            headers=AUTH_HEADERS,
            files=[("videos", ("video.mp4", b"", "video/mp4"))],
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("empty", response.text)

    def test_video_status_returns_current_user_assets(self):
        item = {
            "videoId": "video-1",
            "conversationId": "conversation-1",
            "name": "video.mp4",
            "type": "video/mp4",
            "size": 6,
            "url": "https://cdn.example.test/video.mp4",
            "status": "uploaded",
            "analysisStatus": "ready",
            "analysis": {"summary": "已解析的视频摘要"},
        }
        with mock.patch.object(image_agent_module.professional_video_asset_service, "list_videos", return_value=[item]) as list_videos:
            response = self.client.post(
                "/api/image-agent/videos/status",
                headers=AUTH_HEADERS,
                json={"ids": ["video-1", "missing-video"]},
            )

        self.assertEqual(response.status_code, 200, response.text)
        list_videos.assert_called_once_with(["video-1", "missing-video"], owner_id="test-user")
        payload = response.json()
        self.assertEqual(payload["items"][0]["analysisStatus"], "ready")
        self.assertEqual(payload["items"][0]["analysis"]["summary"], "已解析的视频摘要")
        self.assertEqual(payload["missing"], ["missing-video"])


if __name__ == "__main__":
    unittest.main()
