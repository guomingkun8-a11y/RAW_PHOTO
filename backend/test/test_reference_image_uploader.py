import base64
from concurrent.futures import ThreadPoolExecutor
import json
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from services import reference_image_uploader
from services.reference_image_uploader import _qiniu_token, _urlsafe_base64


class QiniuUploadTokenTests(unittest.TestCase):
    def setUp(self):
        reference_image_uploader._upload_url_cache.clear()
        reference_image_uploader._upload_inflight.clear()
        reference_image_uploader._qiniu_endpoint_open_until.clear()
        reference_image_uploader._upload_serial_until = 0.0
        self.zone_patcher = patch("qiniu.Zone")
        zone_class = self.zone_patcher.start()
        zone_class.return_value.get_up_host.return_value = [
            "https://upload-z0.qiniup.com",
            "https://up-z0.qiniup.com",
        ]
        self.addCleanup(self.zone_patcher.stop)

    @staticmethod
    def qiniu_settings():
        return {
            "enabled": True,
            "provider": "qiniu",
            "qiniu_access_key": "ak",
            "qiniu_secret_key": "sk",
            "qiniu_bucket": "bucket",
            "qiniu_domain": "https://cdn.example.test",
            "qiniu_upload_url": "https://upload-z0.qiniup.com",
            "qiniu_prefix": "reference",
            "timeout_sec": 45,
        }

    @staticmethod
    def qiniu_sdk_patches():
        auth = Mock()
        auth.upload_token.return_value = "upload-token"
        bucket_manager = Mock()
        bucket_manager.stat.return_value = (None, SimpleNamespace(status_code=612))
        return auth, bucket_manager

    def test_urlsafe_base64_keeps_qiniu_padding(self):
        self.assertEqual(_urlsafe_base64(b"{}"), "e30=")

    def test_qiniu_token_policy_segment_remains_decodable_with_padding(self):
        with patch("services.reference_image_uploader.time.time", return_value=1000):
            token = _qiniu_token("bucket", "folder/image.png", "ak", "sk")

        access_key, _signature, encoded_policy = token.split(":")
        expected_policy = json.dumps(
            {"scope": "bucket:folder/image.png", "deadline": 4600},
            separators=(",", ":"),
        ).encode("utf-8")
        policy = json.loads(base64.urlsafe_b64decode(encoded_policy).decode("utf-8"))

        self.assertEqual(access_key, "ak")
        self.assertEqual(encoded_policy, base64.urlsafe_b64encode(expected_policy).decode("ascii"))
        self.assertEqual(policy["scope"], "bucket:folder/image.png")
        self.assertEqual(policy["deadline"], 4600)

    def test_upload_images_reuses_cached_identical_image(self):
        with (
            patch.object(reference_image_uploader, "settings", side_effect=self.qiniu_settings),
            patch.object(reference_image_uploader, "upload_to_qiniu", return_value="https://cdn.example.test/reference/a.png") as upload,
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
            patch.object(reference_image_uploader, "settings", side_effect=self.qiniu_settings),
            patch.object(reference_image_uploader, "upload_to_qiniu", side_effect=slow_upload) as upload,
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

    def test_qiniu_key_is_stable_for_identical_content(self):
        digest = "a" * 64
        with patch.object(reference_image_uploader, "settings", return_value={"qiniu_prefix": "reference"}):
            first = reference_image_uploader._qiniu_key("one.png", digest, "image/png")
            second = reference_image_uploader._qiniu_key("two.png", digest, "image/png")

        self.assertEqual(first, second)
        self.assertEqual(first, f"reference/sha256/aa/{digest}.png")

    def test_qiniu_sdk_session_ignores_windows_system_proxy(self):
        from qiniu.http.default_client import qn_http_client

        qn_http_client.session.trust_env = True
        reference_image_uploader._configure_qiniu_direct_session()

        self.assertFalse(qn_http_client.session.trust_env)

    def test_qiniu_upload_zone_uses_configured_https_primary_and_backup(self):
        zone = reference_image_uploader._qiniu_upload_zone("https://upload-z0.qiniup.com")

        self.assertEqual(zone.scheme, "https")
        self.assertEqual(zone.up_host, "https://upload-z0.qiniup.com")
        self.assertEqual(zone.up_host_backup, "https://up-z0.qiniup.com")

    def test_qiniu_upload_endpoints_prefer_sdk_region_discovery(self):
        import qiniu

        qiniu.Zone.return_value.get_up_host.return_value = ["https://upload.qiniup.com", "https://up.qiniup.com"]
        endpoints = reference_image_uploader._resolve_qiniu_upload_endpoints(
            "ak", "bucket", "https://upload-z0.qiniup.com",
        )

        self.assertEqual(endpoints, ["https://upload.qiniup.com", "https://up.qiniup.com"])

    def test_small_qiniu_payload_uses_form_upload(self):
        import qiniu

        auth, bucket_manager = self.qiniu_sdk_patches()
        put_data = Mock(return_value=({"key": "reference/a.png"}, SimpleNamespace(status_code=200)))
        with (
            patch.object(reference_image_uploader, "settings", side_effect=self.qiniu_settings),
            patch.object(qiniu, "Auth", return_value=auth),
            patch.object(qiniu, "BucketManager", return_value=bucket_manager),
            patch.object(qiniu, "put_data", put_data),
        ):
            url = reference_image_uploader._upload_to_qiniu_sdk(
                b"small-image", "small.png", "image/png", key="reference/a.png", timeout=45,
            )

        self.assertEqual(url, "https://cdn.example.test/reference/a.png")
        put_data.assert_called_once()

    def test_large_qiniu_payload_uses_resumable_upload(self):
        import qiniu

        auth, bucket_manager = self.qiniu_sdk_patches()
        put_data = Mock()
        put_stream = Mock(return_value=({"key": "reference/large.png"}, SimpleNamespace(status_code=200)))
        payload = b"x" * reference_image_uploader._QINIU_RESUMABLE_THRESHOLD
        with (
            patch.object(reference_image_uploader, "settings", side_effect=self.qiniu_settings),
            patch.object(qiniu, "Auth", return_value=auth),
            patch.object(qiniu, "BucketManager", return_value=bucket_manager),
            patch.object(qiniu, "put_data", put_data),
            patch.object(reference_image_uploader, "_put_qiniu_resumable_data", put_stream),
        ):
            reference_image_uploader._upload_to_qiniu_sdk(
                payload, "large.png", "image/png", key="reference/large.png", timeout=45,
            )

        put_data.assert_not_called()
        put_stream.assert_called_once()
        args, _kwargs = put_stream.call_args
        self.assertEqual(args[2], payload)
        self.assertEqual(args[5], "bucket")

    def test_resumable_uploader_forces_sequential_parts(self):
        uploader = Mock()
        uploader.upload.return_value = ({"key": "reference/large.png"}, SimpleNamespace(status_code=200))
        recorder = Mock()
        zone = reference_image_uploader._qiniu_single_endpoint_zone("https://upload.qiniup.com")
        with (
            patch("qiniu.services.storage.upload_progress_recorder.UploadProgressRecorder", return_value=recorder),
            patch("qiniu.services.storage.uploaders.ResumeUploaderV2", return_value=uploader) as uploader_class,
        ):
            reference_image_uploader._put_qiniu_resumable_data(
                "token", "reference/large.png", b"large", "large.png", "image/png", "bucket", zone,
            )

        self.assertIsNone(uploader_class.call_args.kwargs["concurrent_executor"])
        self.assertEqual(uploader_class.call_args.kwargs["part_size"], 1 * 1024 * 1024)
        self.assertEqual(uploader.upload.call_args.kwargs["data_size"], 5)

    def test_resumable_upload_retries_primary_before_backup(self):
        import qiniu

        auth, bucket_manager = self.qiniu_sdk_patches()
        payload = b"x" * reference_image_uploader._QINIU_RESUMABLE_THRESHOLD
        put_stream = Mock(side_effect=[
            (None, SimpleNamespace(status_code=-1, exception=TimeoutError("write timed out"))),
            ({"key": "reference/large.png"}, SimpleNamespace(status_code=200)),
        ])
        with (
            patch.object(reference_image_uploader, "settings", side_effect=self.qiniu_settings),
            patch.object(qiniu, "Auth", return_value=auth),
            patch.object(qiniu, "BucketManager", return_value=bucket_manager),
            patch.object(reference_image_uploader, "_put_qiniu_resumable_data", put_stream),
            patch.object(reference_image_uploader.time, "sleep"),
        ):
            reference_image_uploader._upload_to_qiniu_sdk(
                payload, "large.png", "image/png", key="reference/large.png", timeout=45,
            )

        self.assertEqual(put_stream.call_count, 2)
        self.assertEqual(put_stream.call_args_list[0].args[6].up_host, "https://upload-z0.qiniup.com")
        self.assertEqual(put_stream.call_args_list[1].args[6].up_host, "https://upload-z0.qiniup.com")

    def test_qiniu_primary_failure_falls_back_to_backup_endpoint(self):
        import qiniu

        auth, bucket_manager = self.qiniu_sdk_patches()
        put_data = Mock(side_effect=[
            (None, SimpleNamespace(status_code=-1, exception=TimeoutError("write timed out"))),
            ({"key": "reference/a.png"}, SimpleNamespace(status_code=200)),
        ])
        with (
            patch.object(reference_image_uploader, "settings", side_effect=self.qiniu_settings),
            patch.object(qiniu, "Auth", return_value=auth),
            patch.object(qiniu, "BucketManager", return_value=bucket_manager),
            patch.object(qiniu, "put_data", put_data),
        ):
            reference_image_uploader._upload_to_qiniu_sdk(
                b"small-image", "small.png", "image/png", key="reference/a.png", timeout=45,
            )

        self.assertEqual(put_data.call_count, 2)
        primary_zone = put_data.call_args_list[0].kwargs["regions"][0]
        backup_zone = put_data.call_args_list[1].kwargs["regions"][0]
        self.assertEqual(primary_zone.up_host, "https://upload-z0.qiniup.com")
        self.assertEqual(backup_zone.up_host, "https://up-z0.qiniup.com")

    def test_qiniu_open_circuit_skips_failed_primary_endpoint(self):
        import qiniu

        auth, bucket_manager = self.qiniu_sdk_patches()
        primary = "https://upload-z0.qiniup.com"
        reference_image_uploader._qiniu_endpoint_open_until[primary] = time.monotonic() + 60
        put_data = Mock(return_value=({"key": "reference/a.png"}, SimpleNamespace(status_code=200)))
        with (
            patch.object(reference_image_uploader, "settings", side_effect=self.qiniu_settings),
            patch.object(qiniu, "Auth", return_value=auth),
            patch.object(qiniu, "BucketManager", return_value=bucket_manager),
            patch.object(qiniu, "put_data", put_data),
        ):
            reference_image_uploader._upload_to_qiniu_sdk(
                b"small-image", "small.png", "image/png", key="reference/a.png", timeout=45,
            )

        zone = put_data.call_args.kwargs["regions"][0]
        self.assertEqual(zone.up_host, "https://up-z0.qiniup.com")
        self.assertEqual(reference_image_uploader._UPLOAD_MAX_CONCURRENCY, 2)

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
