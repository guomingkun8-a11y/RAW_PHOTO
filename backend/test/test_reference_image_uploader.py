from concurrent.futures import ThreadPoolExecutor
import threading
import time
import unittest
from unittest.mock import Mock, patch

from minio.error import S3Error

from services import reference_image_uploader


def _s3_error(code: str) -> S3Error:
    return S3Error(
        response=Mock(),
        code=code,
        message=code,
        resource="/bucket/key",
        request_id="request-id",
        host_id="host-id",
        bucket_name="bucket",
        object_name="key",
    )


class FakeOSSClient:
    def __init__(self, *, exists: bool = False):
        self.exists = exists
        self.stat_calls: list[tuple[str, str]] = []
        self.put_calls: list[dict[str, object]] = []

    def stat_object(self, bucket: str, key: str) -> None:
        self.stat_calls.append((bucket, key))
        if not self.exists:
            raise _s3_error("NoSuchKey")

    def put_object(self, bucket: str, key: str, data, **kwargs) -> None:
        self.put_calls.append(
            {
                "bucket": bucket,
                "key": key,
                "payload": data.read(),
                **kwargs,
            }
        )


class OSSReferenceUploadTests(unittest.TestCase):
    def setUp(self):
        reference_image_uploader._upload_url_cache.clear()
        reference_image_uploader._upload_inflight.clear()
        reference_image_uploader._upload_serial_until = 0.0

    @staticmethod
    def oss_settings():
        return {
            "enabled": True,
            "provider": "oss",
            "oss_endpoint": "https://oss-cn-beijing.aliyuncs.com",
            "oss_access_key": "ak",
            "oss_secret_key": "sk",
            "oss_bucket": "bucket",
            "oss_region": "oss-cn-beijing",
            "oss_secure": True,
            "oss_prefix": "reference",
            "public_base_url": "https://cdn.example.test",
            "timeout_sec": 45,
            "persistent_cache_enabled": False,
        }

    def test_upload_images_reuses_cached_identical_image(self):
        with (
            patch.object(reference_image_uploader, "settings", side_effect=self.oss_settings),
            patch.object(reference_image_uploader, "upload_to_oss", return_value="https://cdn.example.test/reference/a.png") as upload,
        ):
            urls = reference_image_uploader.upload_images([
                (b"same-image", "one.png", "image/png"),
                (b"same-image", "two.png", "image/png"),
            ])

        self.assertEqual(urls, ["https://cdn.example.test/reference/a.png"])
        upload.assert_called_once()

    def test_identical_upload_waiter_survives_wait_slice_timeout(self):
        started = threading.Event()
        release = threading.Event()

        def slow_upload(*_args, **_kwargs):
            started.set()
            release.wait(1)
            return "https://cdn.example.test/reference/slow.png"

        with (
            patch.object(reference_image_uploader, "settings", side_effect=self.oss_settings),
            patch.object(reference_image_uploader, "upload_to_oss", side_effect=slow_upload) as upload,
            patch.object(reference_image_uploader, "_UPLOAD_INFLIGHT_WAIT_SLICE_SECONDS", 0.01),
            patch.object(reference_image_uploader, "_UPLOAD_INFLIGHT_MAX_WAIT_SECONDS", 1),
        ):
            with ThreadPoolExecutor(max_workers=2) as executor:
                owner = executor.submit(reference_image_uploader._upload_one_detailed, b"slow-identical", "one.png", "image/png")
                self.assertTrue(started.wait(1))
                waiter = executor.submit(reference_image_uploader._upload_one_detailed, b"slow-identical", "two.png", "image/png")
                time.sleep(0.05)
                self.assertFalse(waiter.done())
                release.set()
                owner_result = owner.result(timeout=2)
                waiter_result = waiter.result(timeout=2)

        self.assertEqual(owner_result.url, waiter_result.url)
        self.assertTrue(waiter_result.cached)
        upload.assert_called_once()

    def test_object_key_is_stable_for_identical_content(self):
        digest = "a" * 64
        with patch.object(reference_image_uploader, "settings", return_value={"oss_prefix": "reference"}):
            first = reference_image_uploader._object_key("one.png", digest, "image/png")
            second = reference_image_uploader._object_key("two.png", digest, "image/png")

        self.assertEqual(first, second)
        self.assertEqual(first, f"reference/sha256/aa/{digest}.png")

    def test_public_base_url_uses_bucket_host_when_no_explicit_url(self):
        item = {
            "oss_endpoint": "https://oss-cn-hangzhou.aliyuncs.com",
            "oss_bucket": "raw-photo",
            "public_base_url": "",
        }

        self.assertEqual(
            reference_image_uploader._public_base_url(item),
            "https://raw-photo.oss-cn-hangzhou.aliyuncs.com",
        )

    def test_upload_to_oss_reuses_existing_object(self):
        client = FakeOSSClient(exists=True)
        with (
            patch.object(reference_image_uploader, "settings", side_effect=self.oss_settings),
            patch.object(reference_image_uploader, "_oss_client", return_value=client),
        ):
            url = reference_image_uploader.upload_to_oss(b"image", "image.png", "image/png")

        self.assertTrue(url.startswith("https://cdn.example.test/reference/sha256/"))
        self.assertEqual(len(client.stat_calls), 1)
        self.assertEqual(client.put_calls, [])

    def test_upload_to_oss_puts_missing_object_sequentially(self):
        client = FakeOSSClient(exists=False)
        with (
            patch.object(reference_image_uploader, "settings", side_effect=self.oss_settings),
            patch.object(reference_image_uploader, "_oss_client", return_value=client),
        ):
            url = reference_image_uploader.upload_to_oss(b"image", "image.png", "image/png")

        self.assertTrue(url.startswith("https://cdn.example.test/reference/sha256/"))
        self.assertEqual(len(client.stat_calls), 1)
        self.assertEqual(len(client.put_calls), 1)
        self.assertEqual(client.put_calls[0]["payload"], b"image")
        self.assertEqual(client.put_calls[0]["content_type"], "image/png")
        self.assertEqual(client.put_calls[0]["num_parallel_uploads"], 1)

    def test_two_small_uploads_can_use_both_upload_slots(self):
        first_entered = threading.Event()
        second_entered = threading.Event()
        release = threading.Event()

        def hold_capacity(entered):
            with reference_image_uploader._upload_capacity(1024):
                entered.set()
                release.wait(1)

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(hold_capacity, first_entered)
            self.assertTrue(first_entered.wait(1))
            second = executor.submit(hold_capacity, second_entered)
            self.assertTrue(second_entered.wait(1))
            release.set()
            first.result(timeout=2)
            second.result(timeout=2)

    def test_large_upload_exclusively_uses_upload_capacity(self):
        large_entered = threading.Event()
        small_entered = threading.Event()
        release_large = threading.Event()

        def hold_large():
            with reference_image_uploader._upload_capacity(reference_image_uploader._UPLOAD_LARGE_FILE_THRESHOLD):
                large_entered.set()
                release_large.wait(1)

        def hold_small():
            with reference_image_uploader._upload_capacity(1024):
                small_entered.set()

        with ThreadPoolExecutor(max_workers=2) as executor:
            large = executor.submit(hold_large)
            self.assertTrue(large_entered.wait(1))
            small = executor.submit(hold_small)
            self.assertFalse(small_entered.wait(0.05))
            release_large.set()
            large.result(timeout=2)
            small.result(timeout=2)

        self.assertTrue(small_entered.is_set())

    def test_retryable_failure_cooldown_serializes_small_uploads(self):
        first_entered = threading.Event()
        second_entered = threading.Event()
        release_first = threading.Event()
        reference_image_uploader._degrade_upload_parallelism()

        def hold_first():
            with reference_image_uploader._upload_capacity(1024):
                first_entered.set()
                release_first.wait(1)

        def hold_second():
            with reference_image_uploader._upload_capacity(1024):
                second_entered.set()

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(hold_first)
            self.assertTrue(first_entered.wait(1))
            second = executor.submit(hold_second)
            self.assertFalse(second_entered.wait(0.05))
            release_first.set()
            first.result(timeout=2)
            second.result(timeout=2)

        self.assertTrue(second_entered.is_set())


if __name__ == "__main__":
    unittest.main()
