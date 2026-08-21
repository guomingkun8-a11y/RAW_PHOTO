from __future__ import annotations

import base64
import io
import unittest

from PIL import Image, ImageDraw

from services.image.image_storage_service import StoredImage
from services.image.image_task_assets import (
    ImageAspectRatioMismatchError,
    decode_task_payload,
    normalize_task_result,
    prepare_task_payload,
)


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
    @staticmethod
    def _png(width: int, height: int) -> bytes:
        image = Image.new("RGB", (width, height), "#dfe9df")
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, width // 5, height), fill="#d94f4f")
        draw.rectangle((width - width // 5, 0, width, height), fill="#4169b1")
        output = io.BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()

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

    def test_remote_result_is_moved_to_object_storage_before_task_success(self):
        storage = FakeTaskAssetStorage()
        result = normalize_task_result(
            [{"url": "https://upstream.test/result.png", "revised_prompt": "test"}],
            owner_id="owner-1",
            task_id="task-1",
            base_url="http://api.test",
            storage=storage,
            remote_loader=lambda _url: (b"remote-png", "result.png", "image/png"),
            strict_remote=True,
        )

        self.assertEqual(result[0]["url"], "http://assets.test/task-assets/task_result/result:0.png")
        self.assertEqual(result[0]["storage_rel"], "task-assets/task_result/result:0.png")
        self.assertEqual(storage.items["task-assets/task_result/result:0.png"], b"remote-png")

    def test_wrong_result_ratio_can_be_rejected_for_an_independent_retry(self):
        storage = FakeTaskAssetStorage()
        encoded = base64.b64encode(self._png(1536, 1024)).decode("ascii")

        with self.assertRaises(ImageAspectRatioMismatchError):
            normalize_task_result(
                [{"b64_json": encoded}],
                owner_id="owner-1",
                task_id="task-1",
                storage=storage,
                expected_size="1024x1024",
                aspect_policy="reject",
            )

        self.assertEqual(storage.items, {})

    def test_wrong_result_ratio_is_extended_without_cropping_required_content(self):
        storage = FakeTaskAssetStorage()
        encoded = base64.b64encode(self._png(1536, 1024)).decode("ascii")

        result = normalize_task_result(
            [{"b64_json": encoded}],
            owner_id="owner-1",
            task_id="task-1",
            storage=storage,
            expected_size="1024x1024",
            aspect_policy="conform",
        )

        item = result[0]
        corrected = storage.items[item["storage_rel"]]
        with Image.open(io.BytesIO(corrected)) as image:
            self.assertEqual(image.size, (1024, 1024))
            center_y = image.height // 2
            self.assertGreater(image.getpixel((8, center_y))[0], image.getpixel((8, center_y))[2])
            self.assertGreater(image.getpixel((image.width - 9, center_y))[2], image.getpixel((image.width - 9, center_y))[0])
        self.assertTrue(item["aspect_ratio_corrected"])
        self.assertEqual(item["source_width"], 1536)
        self.assertEqual(item["source_height"], 1024)


if __name__ == "__main__":
    unittest.main()
