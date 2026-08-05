from __future__ import annotations

import unittest
from unittest import mock

from services import openai_relay_service


class FakeResponse:
    def __init__(
        self,
        status_code: int = 200,
        payload: dict | None = None,
        lines: list[bytes] | None = None,
        text: str = "",
    ) -> None:
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self._lines = lines or []
        self.text = text
        self.closed = False

    def json(self):
        return self._payload

    def iter_lines(self):
        yield from self._lines

    def close(self) -> None:
        self.closed = True


class FakeCurlMime:
    instances = []

    def __init__(self) -> None:
        self.parts = []
        self.closed = False
        FakeCurlMime.instances.append(self)

    def addpart(self, **kwargs) -> None:
        self.parts.append(kwargs)

    def close(self) -> None:
        self.closed = True


def relay_settings() -> dict[str, object]:
    return {
        "enabled": True,
        "base_url": "https://relay.example/v1",
        "api_key": "test-key",
    }


class OpenAIRelayServiceTests(unittest.TestCase):
    def test_list_models_joins_v1_base_url_once(self):
        with (
            mock.patch.object(openai_relay_service, "settings", side_effect=relay_settings),
            mock.patch.object(
                openai_relay_service.requests,
                "get",
                return_value=FakeResponse(payload={"object": "list", "data": []}),
            ) as get,
        ):
            result = openai_relay_service.list_models()

        self.assertEqual(result, {"object": "list", "data": []})
        self.assertEqual(get.call_args.args[0], "https://relay.example/v1/models")
        self.assertEqual(get.call_args.kwargs["headers"]["Authorization"], "Bearer test-key")

    def test_list_models_rotates_relay_api_keys_after_rate_limit(self):
        def pool_settings() -> dict[str, object]:
            return {
                "enabled": True,
                "base_url": "https://relay.example/v1",
                "api_key": "",
                "api_keys": ["first-key", "second-key"],
                "api_key_concurrency": 1,
                "api_key_pool_max_attempts": 2,
                "api_key_pool_acquire_timeout_secs": 1,
                "api_key_pool_lease_secs": 60,
                "api_key_pool_cooldown_secs": 60,
            }

        with (
            mock.patch.object(openai_relay_service, "settings", side_effect=pool_settings),
            mock.patch.object(
                openai_relay_service.requests,
                "get",
                side_effect=[
                    FakeResponse(status_code=429, payload={"error": {"message": "rate limit"}}),
                    FakeResponse(payload={"object": "list", "data": []}),
                ],
            ) as get,
        ):
            result = openai_relay_service.list_models()

        self.assertEqual(result, {"object": "list", "data": []})
        self.assertEqual(get.call_count, 2)
        self.assertEqual(get.call_args_list[0].kwargs["headers"]["Authorization"], "Bearer first-key")
        self.assertEqual(get.call_args_list[1].kwargs["headers"]["Authorization"], "Bearer second-key")

    def test_image_edits_posts_multipart(self):
        FakeCurlMime.instances = []
        with (
            mock.patch.object(openai_relay_service, "settings", side_effect=relay_settings),
            mock.patch.object(openai_relay_service, "CurlMime", FakeCurlMime),
            mock.patch.object(
                openai_relay_service.requests,
                "post",
                return_value=FakeResponse(payload={"created": 1, "data": [{"url": "https://example.test/image.png"}]}),
            ) as post,
        ):
            result = openai_relay_service.image_edits({
                "model": "gpt-image-2",
                "prompt": "make it brighter",
                "images": [(b"image-bytes", "input.png", "image/png")],
                "mask": [(b"mask-bytes", "mask.png", "image/png")],
                "base_url": "ignored",
                "progress_callback": lambda _step: None,
            })

        self.assertEqual(result["data"][0]["url"], "https://example.test/image.png")
        self.assertEqual(post.call_args.args[0], "https://relay.example/v1/images/edits")
        self.assertNotIn("files", post.call_args.kwargs)
        self.assertEqual(post.call_args.kwargs["data"]["model"], "gpt-image-2")
        self.assertEqual(post.call_args.kwargs["data"]["prompt"], "make it brighter")
        self.assertIs(post.call_args.kwargs["multipart"], FakeCurlMime.instances[0])
        self.assertTrue(FakeCurlMime.instances[0].closed)
        self.assertEqual(
            FakeCurlMime.instances[0].parts,
            [
                {
                    "name": "image",
                    "filename": "input.png",
                    "content_type": "image/png",
                    "data": b"image-bytes",
                },
                {
                    "name": "mask",
                    "filename": "mask.png",
                    "content_type": "image/png",
                    "data": b"mask-bytes",
                },
            ],
        )

    def test_image_edits_falls_back_to_generations_with_reference_images_on_404(self):
        FakeCurlMime.instances = []
        with (
            mock.patch.object(openai_relay_service, "settings", side_effect=relay_settings),
            mock.patch.object(openai_relay_service, "CurlMime", FakeCurlMime),
            mock.patch.object(
                openai_relay_service.requests,
                "post",
                side_effect=[
                    FakeResponse(status_code=404, payload={"error": {"message": "edits unsupported"}}),
                    FakeResponse(payload={"created": 1, "data": [{"url": "https://example.test/fallback.png"}]}),
                ],
            ) as post,
        ):
            result = openai_relay_service.image_edits({
                "model": "gpt-image-2",
                "prompt": "make it brighter",
                "images": [(b"image-bytes", "input.png", "image/png")],
                "response_format": "url",
            })

        self.assertEqual(result["data"][0]["url"], "https://example.test/fallback.png")
        self.assertEqual(post.call_args_list[0].args[0], "https://relay.example/v1/images/edits")
        self.assertEqual(post.call_args_list[1].args[0], "https://relay.example/v1/images/generations")
        fallback_json = post.call_args_list[1].kwargs["json"]
        self.assertEqual(fallback_json["model"], "gpt-image-2")
        self.assertEqual(fallback_json["prompt"], "make it brighter")
        self.assertEqual(fallback_json["response_format"], "url")
        self.assertEqual(fallback_json["images"], ["data:image/png;base64,aW1hZ2UtYnl0ZXM="])

    def test_image_edits_falls_back_when_relay_requires_json_body(self):
        FakeCurlMime.instances = []
        with (
            mock.patch.object(openai_relay_service, "settings", side_effect=relay_settings),
            mock.patch.object(openai_relay_service, "CurlMime", FakeCurlMime),
            mock.patch.object(
                openai_relay_service.requests,
                "post",
                side_effect=[
                    FakeResponse(status_code=400, payload={"error": {"message": "请求体必须是 JSON 对象"}}),
                    FakeResponse(payload={"created": 1, "data": [{"url": "https://example.test/json-fallback.png"}]}),
                ],
            ) as post,
        ):
            result = openai_relay_service.image_edits({
                "model": "gpt-image-2",
                "prompt": "make it brighter",
                "images": [(b"image-bytes", "input.png", "image/png")],
                "image_urls": ["https://cdn.example.test/input.png"],
                "response_format": "url",
            })

        self.assertEqual(result["data"][0]["url"], "https://example.test/json-fallback.png")
        self.assertEqual(post.call_args_list[1].args[0], "https://relay.example/v1/images/generations")

    def test_lingke_image_edits_use_multipart_directly(self):
        def lingke_settings() -> dict[str, object]:
            return {
                "enabled": True,
                "base_url": "https://api.lingkeai.ai/v1",
                "api_key": "test-key",
            }

        FakeCurlMime.instances = []
        with (
            mock.patch.object(openai_relay_service, "settings", side_effect=lingke_settings),
            mock.patch.object(openai_relay_service, "CurlMime", FakeCurlMime),
            mock.patch.object(
                openai_relay_service.requests,
                "post",
                return_value=FakeResponse(payload={"created": 1, "data": [{"url": "https://example.test/lingke.png"}]}),
            ) as post,
            mock.patch.object(
                openai_relay_service.reference_image_uploader,
                "upload_images",
                return_value=["https://cdn.example.test/uploaded.png"],
            ),
        ):
            result = openai_relay_service.image_edits({
                "model": "gpt-image-2",
                "prompt": "make it brighter",
                "images": [(b"image-bytes", "input.png", "image/png")],
                "image_urls": ["https://cdn.example.test/input.png"],
                "response_format": "url",
            })

        self.assertEqual(result["data"][0]["url"], "https://example.test/lingke.png")
        post.assert_called_once()
        self.assertEqual(post.call_args.args[0], "https://api.lingkeai.ai/v1/images/edits")
        self.assertEqual(post.call_args.kwargs["data"]["model"], "gpt-image-2")
        self.assertEqual(post.call_args.kwargs["data"]["prompt"], "make it brighter")
        self.assertIs(post.call_args.kwargs["multipart"], FakeCurlMime.instances[0])

    def test_lingke_image_edits_do_not_upload_references_first(self):
        def lingke_settings() -> dict[str, object]:
            return {
                "enabled": True,
                "base_url": "https://api.lingkeai.ai/v1",
                "api_key": "test-key",
            }

        FakeCurlMime.instances = []
        with (
            mock.patch.object(openai_relay_service, "settings", side_effect=lingke_settings),
            mock.patch.object(openai_relay_service, "CurlMime", FakeCurlMime),
            mock.patch.object(
                openai_relay_service.requests,
                "post",
                return_value=FakeResponse(payload={"created": 1, "data": [{"url": "https://example.test/lingke.png"}]}),
            ) as post,
            mock.patch.object(
                openai_relay_service.reference_image_uploader,
                "upload_images",
                side_effect=AssertionError("reference upload should not be called"),
            ) as upload_images,
        ):
            result = openai_relay_service.image_edits({
                "model": "gpt-image-2",
                "prompt": "make it brighter",
                "images": [(b"image-bytes", "input.png", "image/png")],
                "response_format": "url",
            })

        self.assertEqual(result["data"][0]["url"], "https://example.test/lingke.png")
        post.assert_called_once()
        upload_images.assert_not_called()

    def test_supports_image_edit_masks_reflects_relay_mode_and_model(self):
        with mock.patch.object(openai_relay_service, "settings", side_effect=relay_settings):
            self.assertFalse(openai_relay_service.supports_image_edit_masks("gpt-image-2"))
            self.assertFalse(openai_relay_service.supports_image_edit_masks("gemini-3.1-flash-image-preview"))

        def lingke_settings() -> dict[str, object]:
            return {
                "enabled": True,
                "base_url": "https://api.lingkeai.ai/v1",
                "api_key": "test-key",
            }

        with mock.patch.object(openai_relay_service, "settings", side_effect=lingke_settings):
            self.assertFalse(openai_relay_service.supports_image_edit_masks("gpt-image-2"))

    def test_media_image_model_uses_media_task_api(self):
        progress_steps: list[str] = []
        with (
            mock.patch.object(openai_relay_service, "settings", side_effect=relay_settings),
            mock.patch.object(
                openai_relay_service.requests,
                "post",
                return_value=FakeResponse(payload={"code": 200, "data": {"task_id": 12345}}),
            ) as post,
            mock.patch.object(
                openai_relay_service.requests,
                "get",
                return_value=FakeResponse(payload={"data": {"is_final": True, "status": "success", "result_url": "https://cdn.example.test/nano.png"}}),
            ) as get,
        ):
            result = openai_relay_service.image_generations({
                "model": "gemini-3.1-flash-image-preview",
                "prompt": "cat",
                "size": "1024x1024",
                "progress_callback": progress_steps.append,
            })

        self.assertEqual(result["data"][0]["url"], "https://cdn.example.test/nano.png")
        self.assertEqual(post.call_args.args[0], "https://relay.example/v1/media/generate")
        self.assertEqual(post.call_args.kwargs["json"]["model"], "gemini-3.1-flash-image-preview")
        self.assertEqual(post.call_args.kwargs["json"]["params"]["aspectRatio"], "1:1")
        self.assertEqual(post.call_args.kwargs["json"]["params"]["imageSize"], "1K")
        self.assertEqual(get.call_args.args[0], "https://relay.example/v1/skills/task-status")
        self.assertEqual(get.call_args.kwargs["params"], {"task_id": "12345"})
        self.assertEqual(progress_steps, ["image_stream_resolve_start"])

    def test_seedream_image_model_uses_media_task_api(self):
        with (
            mock.patch.object(openai_relay_service, "settings", side_effect=relay_settings),
            mock.patch.object(
                openai_relay_service.requests,
                "post",
                return_value=FakeResponse(payload={"code": 200, "data": {"task_id": "seedream-task"}}),
            ) as post,
            mock.patch.object(
                openai_relay_service.requests,
                "get",
                return_value=FakeResponse(payload={"data": {"is_final": True, "status": "success", "result_url": "https://cdn.example.test/seedream.png"}}),
            ),
        ):
            result = openai_relay_service.image_generations({
                "model": "doubao-seedream-5-0-pro-260628",
                "prompt": "cat",
                "size": "1024x1536",
            })

        self.assertEqual(result["data"][0]["url"], "https://cdn.example.test/seedream.png")
        self.assertEqual(post.call_args.args[0], "https://relay.example/v1/media/generate")
        self.assertEqual(post.call_args.kwargs["json"]["model"], "doubao-seedream-5-0-pro-260628")
        self.assertEqual(post.call_args.kwargs["json"]["params"]["aspectRatio"], "2:3")

    def test_relay_media_image_models_use_media_task_api(self):
        models = [
            "vidu-image-2",
            "mj_imagine",
            "wan2.7-image",
            "kling-v3-omni",
            "qwen-image",
            "kling-v3",
            "wan2.6-image",
            "kling-image-o1",
        ]
        for model in models:
            with self.subTest(model=model):
                with (
                    mock.patch.object(openai_relay_service, "settings", side_effect=relay_settings),
                    mock.patch.object(
                        openai_relay_service.requests,
                        "post",
                        return_value=FakeResponse(payload={"code": 200, "data": {"task_id": f"{model}-task"}}),
                    ) as post,
                    mock.patch.object(
                        openai_relay_service.requests,
                        "get",
                        return_value=FakeResponse(payload={"data": {"is_final": True, "status": "success", "result_url": f"https://cdn.example.test/{model}.png"}}),
                    ),
                ):
                    result = openai_relay_service.image_generations({
                        "model": model,
                        "prompt": "cat",
                        "size": "1024x1024",
                    })

                self.assertEqual(result["data"][0]["url"], f"https://cdn.example.test/{model}.png")
                self.assertEqual(post.call_args.args[0], "https://relay.example/v1/media/generate")
                self.assertEqual(post.call_args.kwargs["json"]["model"], model)

    def test_media_image_edit_uses_reference_urls(self):
        with (
            mock.patch.object(openai_relay_service, "settings", side_effect=relay_settings),
            mock.patch.object(
                openai_relay_service.requests,
                "post",
                return_value=FakeResponse(payload={"code": 200, "data": {"task_id": "edit-task"}}),
            ) as post,
            mock.patch.object(
                openai_relay_service.requests,
                "get",
                return_value=FakeResponse(payload={"data": {"progress": "100%", "status": "生成完成", "result_url": "https://cdn.example.test/edit.png"}}),
            ),
        ):
            result = openai_relay_service.image_edits({
                "model": "gemini-3.1-flash-image-preview",
                "prompt": "make it brighter",
                "size": "1536x1024",
                "image_urls": ["https://cdn.example.test/input.png"],
            })

        self.assertEqual(result["data"][0]["url"], "https://cdn.example.test/edit.png")
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["params"]["aspectRatio"], "3:2")
        self.assertEqual(payload["params"]["images"], ["https://cdn.example.test/input.png"])


if __name__ == "__main__":
    unittest.main()
