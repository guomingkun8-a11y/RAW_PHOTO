from __future__ import annotations

import unittest
from unittest import mock

from services.ecommerce import prompt_analysis_service


class PromptAnalysisServiceTests(unittest.TestCase):
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
                model="gpt-4o",
                system_prompt="Return JSON",
                content="test",
            )

        self.assertEqual({"ok": True}, result)
        self.assertEqual("https://relay.example/v1/chat/completions", post.call_args.args[0])
        self.assertEqual("Bearer pool-key", post.call_args.kwargs["headers"]["Authorization"])

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
                model="gpt-4o",
                system_prompt="Return a plan",
                content="test",
            )

        self.assertIn("设计方案", result)
        self.assertNotIn("response_format", post.call_args.kwargs["json"])


if __name__ == "__main__":
    unittest.main()
