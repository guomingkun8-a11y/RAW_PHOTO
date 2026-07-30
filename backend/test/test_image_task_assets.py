from __future__ import annotations

import unittest

from services.image_storage_service import StoredImage
from services.image_task_assets import decode_task_payload, normalize_task_result, prepare_task_payload


class FakeTaskAssetStorage:
    def __init__(self):
        self.items: dict[str, bytes] = {}

    def save_task_asset(self, image_data: bytes, **kwargs) -> StoredImage:
        rel = f"task-assets/{kwargs['asset_type']}/{kwargs['asset_index']}.png"
        self.items[rel] = image_data
        return StoredImage(rel=rel, url=f"http://assets.test/{rel}", storage="fake", size=len(image_data))

    def get_bytes(self, rel: str) -> bytes:
        return self.items[rel]


class ImageTaskAssetTests(unittest.TestCase):
    def test_binary_payload_is_replaced_by_reference_and_decoded(self):
        storage = FakeTaskAssetStorage()
        original = {
            "images": [(b"input-image", "product.png", "image/png")],
            "mask": [(b"mask-image", "mask.png", "image/png")],
        }

        prepared = prepare_task_payload(
            original,
            owner_id="owner-1",
            task_id="task-1",
            storage=storage,
        )
        decoded = decode_task_payload(prepared, storage)

        self.assertEqual(len(storage.items), 2)
        self.assertEqual(prepared["images"][0]["__image_ref__"], "1")
        self.assertEqual(decoded, original)

    def test_legacy_base64_result_is_moved_to_object_storage(self):
        storage = FakeTaskAssetStorage()
        result = normalize_task_result(
            [{"b64_json": "aGVsbG8=", "revised_prompt": "test"}],
            owner_id="owner-1",
            task_id="task-1",
            base_url="http://api.test",
            storage=storage,
        )

        self.assertNotIn("b64_json", result[0])
        self.assertEqual(result[0]["url"], "http://assets.test/task-assets/task_result/result:0.png")
        self.assertEqual(storage.items["task-assets/task_result/result:0.png"], b"hello")


if __name__ == "__main__":
    unittest.main()
