from __future__ import annotations

from pathlib import Path
import unittest
from datetime import datetime
import tempfile
from types import SimpleNamespace
from unittest import mock

from PIL import Image
from sqlalchemy import text

import services.image_library_service as image_library_service


class ImageLibraryServiceTests(unittest.TestCase):
    def row(self):
        return SimpleNamespace(
            id=1,
            task_id="task-1",
            mode="generate",
            model="gpt-image-2",
            prompt="prompt",
            revised_prompt="",
            size="1024x1024",
            quality="auto",
            product_id=None,
            template_id=None,
            created_by="local-admin",
            image_rel="2026/07/14/example.png",
            image_url="http://stale.example:4399/images/2026/07/14/example.png",
            thumbnail_url="http://stale.example:4399/image-thumbnails/2026/07/14/example.png",
            width=1024,
            height=1024,
            file_size=123,
            storage="local",
            duration_ms=1000,
            favorite=0,
            deleted_at=None,
            created_at=datetime(2026, 7, 14, 10, 46, 11),
        )

    def png_bytes(self) -> bytes:
        path = Path(tempfile.gettempdir()) / "chatgpt2api-library-test-image.png"
        Image.new("RGB", (2, 2), color=(255, 0, 0)).save(path, format="PNG")
        return path.read_bytes()

    def test_public_item_rebases_local_image_url_to_request_base_url(self):
        with mock.patch.object(
            image_library_service.image_storage_service,
            "settings",
            return_value={"public_base_url": ""},
        ):
            item = image_library_service.ImageLibraryService._public_item(
                self.row(),
                "http://127.0.0.1:8002",
            )

        self.assertEqual(
            item["image_url"],
            "http://127.0.0.1:8002/images/2026/07/14/example.png",
        )
        self.assertEqual(
            item["thumbnail_url"],
            "http://127.0.0.1:8002/image-thumbnails/2026/07/14/example.png",
        )

    def test_store_result_image_uses_remote_bytes_when_local_missing(self):
        payload = self.png_bytes()
        with mock.patch.object(image_library_service.image_storage_service, "exists", return_value=True), mock.patch.object(
            image_library_service.image_storage_service,
            "get_bytes",
            return_value=payload,
        ), mock.patch.object(
            image_library_service.image_storage_service,
            "get_item",
            return_value={"storage": "minio", "remote": True, "minio": True, "size": len(payload)},
        ):
            image_rel, image_url, storage, width, height, file_size = image_library_service._store_result_image(
                {"url": "http://stale.example:4399/images/2026/07/14/example.png"},
                "http://app.test",
            )

        self.assertEqual(image_rel, "2026/07/14/example.png")
        self.assertEqual(image_url, "http://stale.example:4399/images/2026/07/14/example.png")
        self.assertEqual(storage, "minio")
        self.assertEqual((width, height), (2, 2))
        self.assertEqual(file_size, len(payload))

    def test_list_images_caches_total_count(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = image_library_service.ImageLibraryService(f"sqlite:///{Path(tmp_dir) / 'library.db'}")
            try:
                self.insert_image(service, image_id=1, task_id="task-1")
                identity = {"id": "owner-1", "role": "user"}

                first = service.list_images(identity=identity, base_url="http://app.test")
                self.insert_image(service, image_id=2, task_id="task-2")
                second = service.list_images(identity=identity, base_url="http://app.test")
                service._count_cache.clear()
                third = service.list_images(identity=identity, base_url="http://app.test")

                self.assertEqual(first["total"], 1)
                self.assertEqual(second["total"], 1)
                self.assertEqual(third["total"], 2)
            finally:
                service.engine.dispose()

    def test_admin_list_images_is_limited_to_own_history(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = image_library_service.ImageLibraryService(f"sqlite:///{Path(tmp_dir) / 'library.db'}")
            try:
                self.insert_image(service, image_id=1, task_id="admin-task", owner_id="admin-1")
                self.insert_image(service, image_id=2, task_id="user-task", owner_id="user-1")

                result = service.list_images(
                    identity={"id": "admin-1", "role": "admin"},
                    base_url="http://app.test",
                )

                self.assertEqual(result["total"], 1)
                self.assertEqual(result["items"][0]["task_id"], "admin-task")
            finally:
                service.engine.dispose()

    def test_admin_update_image_is_limited_to_own_history(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = image_library_service.ImageLibraryService(f"sqlite:///{Path(tmp_dir) / 'library.db'}")
            try:
                self.insert_image(service, image_id=1, task_id="user-task", owner_id="user-1")

                result = service.update_image(
                    identity={"id": "admin-1", "role": "admin"},
                    image_id=1,
                    deleted=True,
                )

                self.assertIsNone(result)
            finally:
                service.engine.dispose()

    def insert_image(self, service, *, image_id: int, task_id: str, owner_id: str = "owner-1") -> None:
        now = datetime(2026, 7, 14, 10, 46, 11)
        with service.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO generated_images "
                    "(id, task_id, owner_id, image_index, mode, image_rel, image_url, favorite, created_at, updated_at) "
                    "VALUES (:id, :task_id, :owner_id, :image_index, :mode, :image_rel, :image_url, :favorite, :created_at, :updated_at)"
                ),
                {
                    "id": image_id,
                    "task_id": task_id,
                    "owner_id": owner_id,
                    "image_index": 0,
                    "mode": "generate",
                    "image_rel": f"2026/07/14/{task_id}.png",
                    "image_url": f"http://stale.example:4399/images/2026/07/14/{task_id}.png",
                    "favorite": 0,
                    "created_at": now,
                    "updated_at": now,
                },
            )

if __name__ == "__main__":
    unittest.main()
