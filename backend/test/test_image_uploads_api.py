from __future__ import annotations

import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.image_uploads as image_uploads_module
from services.reference_image_uploader import ReferenceUploadResult


AUTH_HEADERS = {"Authorization": "Bearer chatgpt2api"}


class ImageUploadsApiTests(unittest.TestCase):
    def setUp(self):
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


if __name__ == "__main__":
    unittest.main()
