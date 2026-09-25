from __future__ import annotations

import hashlib
import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from services.video import video_generation_storage as storage
from services.video import video_generation_signing as signing


OWNER = "owner-1"
TASK_ID = "task-1"
SOURCE_URL = "https://cdn.example.test/video.mp4?token=private"
OSS_SETTINGS = {
    "enabled": True,
    "oss_endpoint": "https://oss.example.test",
    "oss_bucket": "test-videos",
    "oss_access_key": "unit-access-key",
    "oss_secret_key": "unit-secret-key",
    "oss_region": "us-east-1",
}


def public_dns(*_args, **_kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("8.8.8.8", 443))]


def record(stored, owner=OWNER, task_id=TASK_ID):
    return {"owner_id": owner, "id": task_id, "storage": stored.storage,
            "storage_rel": stored.relative_path, "video_url": stored.url}


class VideoGenerationStorageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.patch(storage, "VIDEO_GENERATION_DIR", self.root)
        self.settings = self.patch(storage, "_settings", return_value={"enabled": False})
        self.generation_settings = self.patch(storage, "_generation_settings", return_value={"result_storage_prefix": "test-results"})
        self.dns = self.patch(storage.socket, "getaddrinfo", side_effect=public_dns)
        self.session_factory = self.patch(storage.requests, "Session")
        self.session = self.session_factory.return_value.__enter__.return_value
        self.minio = self.patch(storage, "Minio")
        self.client = self.minio.return_value
        self.env = mock.patch.dict(os.environ, {"VIDEO_GENERATION_SIGNING_SECRET": "unit-video-secret",
                                                "VIDEO_GENERATION_URL_TTL_SECS": "900"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.response = self.respond()
        self.service = storage.VideoGenerationStorageService()

    def patch(self, target, name, *args, **kwargs):
        patcher = mock.patch.object(target, name, *args, **kwargs)
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def respond(self, *, chunks=(b"fake-", b"video"), status=200, headers=None):
        response = mock.Mock(status_code=status, headers={"content-type": "video/mp4", **(headers or {})})
        # Accessing response.content would reintroduce full in-memory buffering.
        type(response).content = mock.PropertyMock(side_effect=AssertionError("must stream"))

        def get(_url, **kwargs):
            for chunk in chunks:
                if kwargs["content_callback"](chunk) == storage.CURL_WRITEFUNC_ERROR:
                    raise RuntimeError("write aborted")
            return response

        self.session.get.side_effect = get
        return response

    def store(self, **kwargs):
        return self.service.store_remote_video(SOURCE_URL, owner_id=kwargs.get("owner_id", OWNER),
                                               task_id=kwargs.get("task_id", TASK_ID))

    def test_local_storage_is_atomic_and_persists_no_public_url(self):
        stored = self.store()
        self.assertEqual(stored.storage, "local")
        self.assertEqual(stored.size, 10)
        self.assertEqual(stored.url, f"video-storage://local/{stored.relative_path}")
        self.assertEqual((self.root / stored.relative_path).read_bytes(), b"fake-video")
        self.assertEqual(list(self.root.rglob("*.part")), [])
        self.minio.assert_not_called()

    def test_download_is_bounded_streaming_and_pins_dns(self):
        with storage._download(SOURCE_URL) as downloaded:
            self.assertEqual(downloaded.stream.read(), b"fake-video")
            self.assertEqual(downloaded.digest, hashlib.sha256(b"fake-video").hexdigest())
            stream = downloaded.stream
        self.assertTrue(stream.closed)
        self.response.close.assert_called_once()
        self.session_factory.return_value.__exit__.assert_called_once()
        kwargs = self.session.get.call_args.kwargs
        self.assertFalse(kwargs["allow_redirects"])
        self.assertNotIn("stream", kwargs)
        options = self.session_factory.call_args.kwargs
        self.assertFalse(options["trust_env"])
        self.assertEqual(options["curl_options"][storage.CurlOpt.PROXY], "")
        self.assertEqual(options["curl_options"][storage.CurlOpt.RESOLVE], ["cdn.example.test:443:8.8.8.8"])
        self.assertGreater(options["curl_options"][storage.CurlOpt.MAXFILESIZE_LARGE], 0)
        self.dns.assert_called_once()

    def test_rejects_chunked_download_over_limit_and_closes_temp_file(self):
        self.patch(storage, "_max_result_bytes", return_value=7)
        real_temp_file = tempfile.TemporaryFile
        streams = []

        def temp_file(**kwargs):
            result = real_temp_file(**kwargs)
            streams.append(result)
            return result

        self.patch(storage.tempfile, "TemporaryFile", side_effect=temp_file)
        with self.assertRaisesRegex(storage.VideoGenerationStorageError, "limit"):
            self.store()
        self.assertTrue(all(stream.closed for stream in streams))
        self.assertEqual(list(self.root.iterdir()), [])
        self.session_factory.return_value.__exit__.assert_called_once()

    def test_exact_byte_limit_is_allowed(self):
        self.patch(storage, "_max_result_bytes", return_value=10)
        self.assertEqual(self.store().size, 10)

    def test_rejects_empty_truncated_nonvideo_and_partial_responses(self):
        cases = [
            {"chunks": ()},
            {"headers": {"content-length": "20"}},
            {"headers": {"content-length": "NaN"}},
            {"headers": {"content-type": "text/html"}},
            {"status": 206},
            {"status": 500},
        ]
        for case in cases:
            with self.subTest(case=case):
                response = self.respond(**case)
                with self.assertRaises(storage.VideoGenerationStorageError):
                    self.store()
                response.close.assert_called_once()
                self.assertEqual(list(self.root.iterdir()), [])

    def test_content_type_and_redirected_url_select_extension(self):
        self.respond(headers={"content-type": "video/webm; charset=binary"})
        with storage._download("https://cdn.example.test/result") as downloaded:
            self.assertEqual(storage._extension(downloaded.source_url, downloaded.content_type), ".webm")

    def test_rejects_unsafe_source_urls_without_http_requests(self):
        urls = ["", "file:///etc/passwd", "ftp://example.test/video", "https://u:p@example.test/a",
                "https://example.test:8080/a", "https://example.test/a#fragment", "https://localhost/a",
                "https://127.0.0.1/a", "http://169.254.169.254/latest/meta-data/", "https://10.0.0.1/a",
                "https://[::1]/a", "https://[::ffff:127.0.0.1]/a", "https://[fe80::1%25eth0]/a",
                "https://example.test\\@127.0.0.1/a", "https://example.test/\n/a",
                "https://x.internal/a", "http://100.64.0.1/a", "http://224.0.0.1/a",
                "https://[64:ff9b::7f00:1]/a"]
        for url in urls:
            with self.subTest(url=url):
                with self.assertRaises(storage.VideoGenerationStorageError):
                    with storage._download(url):
                        self.fail("unsafe URL accepted")
        self.session.get.assert_not_called()

    def test_rejects_private_or_mixed_dns(self):
        for addresses in (["127.0.0.1"], ["8.8.8.8", "192.168.1.1"], ["::1"], []):
            with self.subTest(addresses=addresses):
                self.dns.side_effect = lambda *_args, **_kwargs: [
                    (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, 443)) for ip in addresses
                ]
                with self.assertRaises(storage.VideoGenerationStorageError):
                    self.store()
        self.session.get.assert_not_called()

    def test_dns_failure_is_sanitized(self):
        self.dns.side_effect = OSError("dns private metadata")
        with self.assertRaisesRegex(storage.VideoGenerationStorageError, "could not be resolved") as error:
            self.store()
        self.assertNotIn("metadata", str(error.exception))
        self.session.get.assert_not_called()

    def test_public_ipv6_is_supported_and_pinned(self):
        self.dns.side_effect = lambda *_args, **_kwargs: [
            (socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("2606:4700:4700::1111", 443, 0, 0))
        ]
        self.store()
        self.assertEqual(self.session_factory.call_args.kwargs["curl_options"][storage.CurlOpt.RESOLVE],
                         ["cdn.example.test:443:[2606:4700:4700::1111]"])

    def test_redirects_are_revalidated_before_the_next_request(self):
        self.respond(status=302, chunks=(), headers={"location": "https://127.0.0.1/private"})
        with self.assertRaises(storage.VideoGenerationStorageError):
            self.store()
        self.session.get.assert_called_once()

    def test_revalidates_dns_on_same_host_redirect(self):
        self.respond(status=302, chunks=(), headers={"location": "/next.mp4"})
        self.dns.side_effect = [public_dns(), [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]]
        with self.assertRaises(storage.VideoGenerationStorageError):
            self.store()
        self.session.get.assert_called_once()

    def test_public_relative_redirect_downloads_only_final_body(self):
        get_video = self.session.get.side_effect
        redirect = mock.Mock(status_code=302, headers={"Location": "/final.webm"})

        def get(url, **kwargs):
            if url == SOURCE_URL:
                kwargs["content_callback"](b"redirect")
                return redirect
            return get_video(url, **kwargs)

        self.session.get.side_effect = get
        stored = self.store()
        self.assertTrue(stored.relative_path.endswith(".webm"))
        self.assertEqual((self.root / stored.relative_path).read_bytes(), b"fake-video")
        self.assertEqual(self.dns.call_count, 2)
        redirect.close.assert_called_once()

    def test_redirect_loop_is_bounded(self):
        self.respond(status=302, chunks=(), headers={"location": "/video.mp4"})
        with self.assertRaisesRegex(storage.VideoGenerationStorageError, "redirect limit"):
            self.store()
        self.assertEqual(self.session.get.call_count, storage.MAX_REDIRECTS + 1)

    def test_rejects_https_downgrade(self):
        self.respond(status=302, chunks=(), headers={"location": "http://cdn.example.test/video.mp4"})
        with self.assertRaisesRegex(storage.VideoGenerationStorageError, "downgrade"):
            self.store()
        self.session.get.assert_called_once()

    def test_http_failure_does_not_leak_upstream_url(self):
        self.session.get.side_effect = RuntimeError(SOURCE_URL)
        with self.assertRaises(storage.VideoGenerationStorageError) as error:
            self.store()
        self.assertNotIn("token", str(error.exception))

    def test_oss_storage_persists_full_key_and_uses_private_presigning(self):
        self.settings.return_value = OSS_SETTINGS
        uploaded = []
        self.client.put_object.side_effect = lambda _bucket, _key, stream, **_kwargs: uploaded.append(stream.read())
        stored = self.store()
        self.assertEqual(uploaded, [b"fake-video"])
        self.assertEqual(stored.storage, "oss")
        self.assertTrue(stored.relative_path.startswith("test-results/generated/"))
        self.assertEqual(stored.url, f"video-storage://oss/test-videos/{stored.relative_path}")
        self.client.presigned_get_object.assert_not_called()
        task = record(stored)
        snapshot = dict(task)
        self.client.presigned_get_object.return_value = "https://private.example.test/fresh?signature=1"
        url = self.service.resolve_result_url(task)
        self.assertEqual(url, "https://private.example.test/fresh?signature=1")
        self.assertEqual(task, snapshot)
        args, kwargs = self.client.presigned_get_object.call_args
        self.assertEqual(args, ("test-videos", stored.relative_path))
        self.assertEqual(kwargs["expires"].total_seconds(), 900)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_oss_upload_failure_raises_without_falling_back(self):
        self.settings.return_value = OSS_SETTINGS
        self.client.put_object.side_effect = RuntimeError("secret storage URL")
        with self.assertLogs(storage.logger, level="ERROR") as logs:
            with self.assertRaisesRegex(storage.VideoGenerationStorageError, "retry storage") as error:
                self.store()
        self.assertNotIn("secret storage", str(error.exception))
        self.assertNotIn("secret storage", "".join(logs.output))
        self.assertEqual(list(self.root.iterdir()), [])

    def test_incomplete_oss_configuration_fails_before_downloading(self):
        for field in ("oss_bucket", "oss_access_key", "oss_secret_key", "oss_endpoint"):
            with self.subTest(field=field):
                self.settings.return_value = {**OSS_SETTINGS, field: ""}
                with self.assertRaises(storage.VideoGenerationStorageError):
                    self.store()
        self.session.get.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_oss_signing_failure_does_not_return_public_or_upstream_url(self):
        self.settings.return_value = {**OSS_SETTINGS, "public_base_url": "https://public.example.test"}
        stored = self.store()
        self.client.presigned_get_object.side_effect = RuntimeError("secret")
        with self.assertRaisesRegex(storage.VideoGenerationStorageError, "signing failed"):
            self.service.resolve_result_url(record(stored))

    def test_legacy_oss_relative_path_gets_signed_and_deleted_by_exact_key(self):
        self.settings.return_value = OSS_SETTINGS
        path = f"generated/{storage._owner_scope(OWNER)}/{TASK_ID}-{'a' * 24}.mp4"
        task = {"owner_id": OWNER, "id": TASK_ID, "storage": "oss", "storage_rel": path,
                "video_url": f"https://public.example.test/test-results/{path}"}
        self.service.resolve_result_url(task)
        self.assertEqual(self.client.presigned_get_object.call_args.args, ("test-videos", f"test-results/{path}"))
        self.assertTrue(self.service.delete_stored_video(task, owner_id=OWNER))
        self.client.remove_object.assert_called_once_with("test-videos", f"test-results/{path}")

    def test_bucket_or_prefix_mismatch_never_signs_or_deletes(self):
        self.settings.return_value = OSS_SETTINGS
        stored = self.store()
        task = record(stored)
        task["video_url"] = stored.url.replace("/test-videos/", "/other-bucket/")
        with self.assertRaises(storage.VideoGenerationStorageError):
            self.service.resolve_result_url(task)
        with self.assertRaises(storage.VideoGenerationStorageError):
            self.service.delete_stored_video(task, owner_id=OWNER)
        task = record(stored)
        task["storage_rel"] = stored.relative_path.replace("test-results/", "another-prefix/")
        with self.assertRaises(storage.VideoGenerationStorageError):
            self.service.delete_stored_video(task, owner_id=OWNER)
        self.client.presigned_get_object.assert_not_called()
        self.client.remove_object.assert_not_called()

    def test_local_signed_capability_plays_without_bearer_and_expires(self):
        stored = self.store()
        task = record(stored)
        self.patch(storage.time, "time", return_value=1000)
        url = self.service.resolve_result_url(task, base_url="http://app.example.test")
        parsed = urlsplit(url)
        query = parse_qs(parsed.query)
        path = parsed.path.removeprefix("/video-assets/")
        self.assertEqual(query["expires"], ["1900"])
        file = self.service.local_file(path, expires=query["expires"][0], signature=query["signature"][0])
        self.assertEqual(file.read_bytes(), b"fake-video")
        self.assertEqual(task["video_url"], stored.url)
        with mock.patch.object(storage.time, "time", return_value=1900):
            with self.assertRaises(storage.HTTPException) as error:
                self.service.local_file(path, expires=1900, signature=query["signature"][0])
            self.assertEqual(error.exception.status_code, 403)
            renewed = self.service.resolve_result_url(task)
            self.assertNotEqual(parse_qs(urlsplit(renewed).query)["signature"], query["signature"])

    def test_unsigned_and_tampered_local_capabilities_are_rejected(self):
        stored = self.store()
        self.patch(storage.time, "time", return_value=1000)
        url = self.service.resolve_result_url(record(stored))
        signature = parse_qs(urlsplit(url).query)["signature"][0]
        for path, expiry, sig in [(stored.relative_path, 0, ""), (stored.relative_path, 2000, signature),
                                  (stored.relative_path, 1900, "0" * 64),
                                  (stored.relative_path.replace(".mp4", ".mov"), 1900, signature)]:
            with self.subTest(path=path, expiry=expiry, signature=sig):
                with self.assertRaises(storage.HTTPException) as error:
                    self.service.local_file(path, expires=expiry, signature=sig)
                self.assertEqual(error.exception.status_code, 403)

    def test_missing_file_returns_404_with_valid_signature(self):
        stored = self.store()
        self.patch(storage.time, "time", return_value=1000)
        signature = signing.sign_video_path(stored.relative_path, 1900)
        self.service.delete_stored_video(record(stored), owner_id=OWNER)
        with self.assertRaises(storage.HTTPException) as error:
            self.service.local_file(stored.relative_path, expires=1900, signature=signature)
        self.assertEqual(error.exception.status_code, 404)

    def test_local_deletion_is_owned_and_idempotent(self):
        stored = self.store()
        self.assertTrue(self.service.delete_stored_video(record(stored), owner_id=OWNER))
        self.assertFalse((self.root / stored.relative_path).exists())
        self.assertTrue(self.service.delete_stored_video(record(stored), owner_id=OWNER))

    def test_cross_owner_and_cross_task_deletion_and_signing_are_rejected(self):
        stored = self.store()
        for task, owner in [(record(stored), "owner-2"), (record(stored, owner="owner-2"), "owner-2"),
                            (record(stored, task_id="task-2"), OWNER)]:
            with self.subTest(task=task, owner=owner):
                with self.assertRaises(storage.VideoGenerationStorageError):
                    self.service.delete_stored_video(task, owner_id=owner)
        for task in [record(stored, owner="owner-2"), record(stored, task_id="task-2")]:
            with self.assertRaises(storage.VideoGenerationStorageError):
                self.service.resolve_result_url(task)
        self.assertTrue((self.root / stored.relative_path).exists())

    def test_rejects_traversal_absolute_paths_and_noncanonical_aliases(self):
        stored = self.store()
        paths = ["../secret.mp4", "/etc/passwd", "C:/Windows/video.mp4", "C:\\Windows\\video.mp4",
                 "generated/../a.mp4", "generated//a.mp4", "generated/./a.mp4", "generated/%2e%2e/a.mp4",
                 "generated/file.mp4:secret", "generated/a./file.mp4", "generated/a /file.mp4"]
        for path in paths:
            with self.subTest(path=path):
                task = {**record(stored), "storage_rel": path}
                with self.assertRaises(storage.VideoGenerationStorageError):
                    self.service.delete_stored_video(task, owner_id=OWNER)
                with self.assertRaises(storage.HTTPException):
                    self.service.local_file(path)

    def test_symbolic_links_and_windows_junctions_are_rejected(self):
        stored = self.store()
        for field in ("is_symlink", "is_junction"):
            with self.subTest(field=field), mock.patch.object(Path, field, return_value=True):
                with self.assertRaisesRegex(storage.VideoGenerationStorageError, "links"):
                    self.service.delete_stored_video(record(stored), owner_id=OWNER)
        self.assertTrue((self.root / stored.relative_path).exists())

    def test_remote_results_are_never_deleted(self):
        task = {"owner_id": OWNER, "id": TASK_ID, "storage": "remote", "video_url": SOURCE_URL}
        self.assertFalse(self.service.delete_stored_video(task, owner_id=OWNER))
        self.assertEqual(self.service.resolve_result_url(task), SOURCE_URL)
        self.dns.assert_not_called()
        self.session.get.assert_not_called()
        self.client.remove_object.assert_not_called()

    def test_cleanup_failure_raises_so_caller_can_keep_metadata_for_retry(self):
        self.settings.return_value = OSS_SETTINGS
        stored = self.store()
        self.client.remove_object.side_effect = RuntimeError("secret upstream failure")
        with self.assertRaisesRegex(storage.VideoGenerationStorageError, "cleanup failed"):
            self.service.delete_stored_video(record(stored), owner_id=OWNER)
        self.client.remove_object.side_effect = None
        self.assertTrue(self.service.delete_stored_video(record(stored), owner_id=OWNER))

    def test_oss_missing_object_cleanup_is_idempotent_but_bucket_errors_are_not(self):
        self.settings.return_value = OSS_SETTINGS
        stored = self.store()
        for code in ("NoSuchKey", "AccessDenied", "NoSuchBucket"):
            self.client.remove_object.side_effect = storage.S3Error(
                None,
                code,
                "unit error",
                "/",
                "req",
                "host",
            )
            if code == "NoSuchKey":
                self.assertTrue(self.service.delete_stored_video(record(stored), owner_id=OWNER))
            else:
                with self.assertRaises(storage.VideoGenerationStorageError):
                    self.service.delete_stored_video(record(stored), owner_id=OWNER)

    def test_failed_atomic_replace_does_not_leave_partial_file(self):
        self.patch(storage.os, "replace", side_effect=OSError("disk full"))
        with self.assertRaisesRegex(storage.VideoGenerationStorageError, "local video"):
            self.store()
        self.assertEqual([item for item in self.root.rglob("*") if item.is_file()], [])

    def test_distinct_ids_do_not_collide_after_filename_sanitization(self):
        first = self.store(task_id="task/a")
        second = self.store(task_id="task?a")
        self.assertNotEqual(first.relative_path, second.relative_path)
        self.service.delete_stored_video(record(first, task_id="task/a"), owner_id=OWNER)
        self.assertTrue((self.root / second.relative_path).exists())

    def test_anonymous_missing_identifiers_are_not_silently_accepted(self):
        for owner, task_id in [("", TASK_ID), (OWNER, ""), (None, TASK_ID)]:
            with self.assertRaises(storage.VideoGenerationStorageError):
                self.store(owner_id=owner, task_id=task_id)
        self.session.get.assert_not_called()


class VideoGenerationSigningTests(unittest.TestCase):
    def test_stable_auth_secret_fallback_and_dedicated_override(self):
        with mock.patch.dict(os.environ, {"VIDEO_GENERATION_SIGNING_SECRET": ""}), mock.patch.object(signing, "config") as config:
            config.auth_key = "unit-auth-secret"
            first = signing.sign_video_path("generated/owner/task.mp4", 1900)
            self.assertEqual(first, signing.sign_video_path("generated/owner/task.mp4", 1900))
            with mock.patch.dict(os.environ, {"VIDEO_GENERATION_SIGNING_SECRET": "unit-dedicated-secret"}):
                self.assertNotEqual(first, signing.sign_video_path("generated/owner/task.mp4", 1900))
            config.auth_key = ""
            with self.assertRaisesRegex(ValueError, "not configured"):
                signing.sign_video_path("generated/owner/task.mp4", 1900)

    def test_malformed_and_excessively_long_lived_tokens_are_rejected(self):
        with mock.patch.object(signing.time, "time", return_value=1000):
            for expiry in ("", -1, None, True, "1e4", "9" * 500, 999, 1000, 4601):
                self.assertFalse(signing.verify_video_signature("path", expiry, "0" * 64))
            self.assertFalse(signing.verify_video_signature("path", 1900, "bad"))

    def test_ttl_is_bounded_and_invalid_setting_falls_back(self):
        for value, expected in [("1", 60), ("900", 900), ("999999", 3600), ("invalid", 900)]:
            with mock.patch.dict(os.environ, {"VIDEO_GENERATION_URL_TTL_SECS": value}):
                self.assertEqual(signing.result_url_ttl(), expected)


if __name__ == "__main__":
    unittest.main()
