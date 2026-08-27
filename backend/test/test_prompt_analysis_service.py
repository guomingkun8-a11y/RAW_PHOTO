from __future__ import annotations

import unittest
from unittest import mock

from services.ecommerce import prompt_analysis_service


class PromptAnalysisServiceTests(unittest.TestCase):
    def relay_settings(self) -> dict[str, object]:
        return {
            "enabled": True,
            "base_url": "https://relay.example/v1",
            "api_key": "single-key",
            "api_keys": [],
            "api_key_concurrency": 1,
            "api_key_pool_distributed": False,
            "api_key_pool_acquire_timeout_secs": 1,
            "api_key_pool_lease_secs": 60,
            "api_key_pool_cooldown_secs": 60,
            "api_key_pool_max_attempts": 1,
        }

    def test_chat_completion_uses_relay_api_key_pool(self):
        relay_settings = {
            "enabled": True,
            "base_url": "https://relay.example/v1",
            "api_key": "",
            "api_keys": ["pool-key"],
            "api_key_concurrency": 1,
            "api_key_pool_distributed": False,
            "api_key_pool_acquire_timeout_secs": 1,
            "api_key_pool_lease_secs": 60,
            "api_key_pool_cooldown_secs": 60,
            "api_key_pool_max_attempts": 1,
        }
        response = mock.Mock(status_code=200)
        response.json.return_value = {
            "choices": [{"message": {"content": '{"ok": true}'}}],
        }

        with (
            mock.patch.object(prompt_analysis_service, "_relay_settings", return_value=relay_settings),
            mock.patch.object(prompt_analysis_service.requests, "post", return_value=response) as post,
        ):
            result = prompt_analysis_service.request_json_completion(
                model="gpt-5.6-sol",
                system_prompt="Return JSON",
                content="test",
            )

        self.assertEqual({"ok": True}, result)
        self.assertEqual("https://relay.example/v1/chat/completions", post.call_args.args[0])
        self.assertEqual("Bearer pool-key", post.call_args.kwargs["headers"]["Authorization"])
        self.assertEqual("tt-5.6-sol", post.call_args.kwargs["json"]["model"])
        self.assertNotIn("temperature", post.call_args.kwargs["json"])

    def test_text_completion_allows_natural_language_response(self):
        relay_settings = {
            "enabled": True,
            "base_url": "https://relay.example/v1",
            "api_key": "single-key",
            "api_keys": [],
            "api_key_concurrency": 1,
            "api_key_pool_distributed": False,
            "api_key_pool_acquire_timeout_secs": 1,
            "api_key_pool_lease_secs": 60,
            "api_key_pool_cooldown_secs": 60,
            "api_key_pool_max_attempts": 1,
        }
        response = mock.Mock(status_code=200)
        response.json.return_value = {
            "choices": [{"message": {"content": "#### 设计方案\n左侧文字，右侧商品。"}}],
        }

        with (
            mock.patch.object(prompt_analysis_service, "_relay_settings", return_value=relay_settings),
            mock.patch.object(prompt_analysis_service.requests, "post", return_value=response) as post,
        ):
            result = prompt_analysis_service.request_text_completion(
                model="gpt-5.6-sol",
                system_prompt="Return a plan",
                content="test",
            )

        self.assertIn("设计方案", result)
        self.assertNotIn("response_format", post.call_args.kwargs["json"])
        self.assertEqual("tt-5.6-sol", post.call_args.kwargs["json"]["model"])
        self.assertNotIn("temperature", post.call_args.kwargs["json"])

    def test_analyze_prompt_marks_multi_image_selling_point_request(self):
        response = mock.Mock(status_code=200)
        response.json.return_value = {
            "choices": [{"message": {"content": '{"analysis":{"subject":"产品"},"suggestions":[],"optimizedPrompt":"1. 场景一：基于包装卖点设计。","negativePrompt":""}'}}],
        }

        with (
            mock.patch.object(prompt_analysis_service, "_relay_settings", return_value=self.relay_settings()),
            mock.patch.object(prompt_analysis_service.requests, "post", return_value=response) as post,
        ):
            result = prompt_analysis_service.analyze_image_prompt({
                "action": "optimize",
                "mode": "single",
                "prompt": "为上图产品设计5款不同卖点图，每张标签排版不要相同",
                "images": [{"name": "product.png", "dataUrl": "data:image/png;base64,AAAA"}],
            })

        payload = post.call_args.kwargs["json"]
        instruction_text = payload["messages"][1]["content"][0]["text"]
        self.assertIn('"multi_image_request": true', instruction_text)
        self.assertIn("Each numbered direction must derive its selling point", instruction_text)
        self.assertIn("Do not use a fixed cross-category default set", instruction_text)
        self.assertEqual("1. 场景一：基于包装卖点设计。", result["optimizedPrompt"])

    def test_analyze_prompt_keeps_single_image_request_unmarked(self):
        response = mock.Mock(status_code=200)
        response.json.return_value = {
            "choices": [{"message": {"content": '{"analysis":{"subject":"产品"},"suggestions":[],"optimizedPrompt":"生成一张产品图","negativePrompt":""}'}}],
        }

        with (
            mock.patch.object(prompt_analysis_service, "_relay_settings", return_value=self.relay_settings()),
            mock.patch.object(prompt_analysis_service.requests, "post", return_value=response) as post,
        ):
            prompt_analysis_service.analyze_image_prompt({
                "action": "optimize",
                "mode": "single",
                "prompt": "为上图产品生成一张相同排版的图",
                "images": [{"name": "product.png", "dataUrl": "data:image/png;base64,AAAA"}],
            })

        payload = post.call_args.kwargs["json"]
        instruction_text = payload["messages"][1]["content"][0]["text"]
        self.assertIn('"multi_image_request": false', instruction_text)


if __name__ == "__main__":
    unittest.main()
