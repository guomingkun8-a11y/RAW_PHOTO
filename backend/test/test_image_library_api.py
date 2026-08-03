from __future__ import annotations

import io
import unittest
import zipfile
from unittest import mock

from fastapi import HTTPException

from api.image_library import ImageLibraryZipRequest, _download_library_zip_payload


class ImageLibraryApiTests(unittest.TestCase):
    def test_download_library_zip_reads_storage_images(self):
        body = ImageLibraryZipRequest(ids=[1], folder_name="图库测试")
        with mock.patch(
            "api.image_library.image_library_service.list_images_by_ids",
            return_value=[
                {
                    "id": 1,
                    "prompt": "测试商品图",
                    "image_rel": "2026/08/02/example.png",
                    "image_url": "",
                }
            ],
        ), mock.patch(
            "api.image_library.image_storage_service.get_bytes",
            return_value=b"image-bytes",
        ):
            payload, filename = _download_library_zip_payload({"id": "owner-1", "role": "user"}, "http://app.test", body)

        try:
            self.assertEqual(filename, "图库测试.zip")
            data = payload.read()
        finally:
            payload.close()

        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = archive.namelist()
            self.assertEqual(len(names), 1)
            self.assertTrue(names[0].startswith("图库测试/"))
            self.assertEqual(archive.read(names[0]), b"image-bytes")

    def test_download_library_zip_skips_unavailable_images(self):
        body = ImageLibraryZipRequest(ids=[1], folder_name="图库测试")
        with mock.patch(
            "api.image_library.image_library_service.list_images_by_ids",
            return_value=[{"id": 1, "prompt": "测试商品图", "image_rel": "missing.png", "image_url": ""}],
        ), mock.patch(
            "api.image_library.image_storage_service.get_bytes",
            side_effect=HTTPException(status_code=404, detail={"error": "missing"}),
        ):
            with self.assertRaises(ValueError):
                _download_library_zip_payload({"id": "owner-1", "role": "user"}, "http://app.test", body)


if __name__ == "__main__":
    unittest.main()
