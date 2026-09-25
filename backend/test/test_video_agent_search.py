from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from services.video.video_agent_service import (
    _requests_web_search,
    _run_web_search,
    chat,
    open_reasoning_chat_stream,
)


class VideoAgentSearchTests(unittest.TestCase):
    def test_search_intent_is_explicit(self):
        self.assertTrue(_requests_web_search("\u8bf7\u641c\u7d22\u6700\u65b0\u89c6\u9891\u6a21\u578b\u53c2\u6570"))
        self.assertTrue(_requests_web_search("https://example.com/model"))
        self.assertFalse(_requests_web_search("\u600e\u4e48\u8bbe\u7f6e\u89c6\u9891\u5feb\u95e8"))
        self.assertFalse(_requests_web_search("\u4e0d\u8981\u8054\u7f51\uff0c\u76f4\u63a5\u56de\u7b54"))

    def test_video_search_uses_shared_raw_search_tool(self):
        search_result = SimpleNamespace(
            status="success",
            result={
                "results": [{
                    "title": "Official source",
                    "url": "https://example.com/video-model",
                    "readableContent": "Official details",
                }],
            },
        )
        with (
            patch("services.ecommerce.cow_agent_extended_tools.load_admin_tool_environment"),
            patch("services.ecommerce.cow_agent_extended_tools.RawWebSearchTool") as raw_tool,
        ):
            raw_tool.return_value.execute.return_value = search_result
            result = _run_web_search("\u8bf7\u641c\u7d22\u5b98\u7f51\u7684\u89c6\u9891\u6a21\u578b\u53c2\u6570")

        self.assertEqual("success", result["status"])
        self.assertEqual(search_result.result, result["payload"])
        runtime = raw_tool.call_args.args[0]
        self.assertTrue(runtime._web_research_requested)
        self.assertEqual("\u8bf7\u641c\u7d22\u5b98\u7f51\u7684\u89c6\u9891\u6a21\u578b\u53c2\u6570", runtime.user_message)
        raw_tool.return_value.execute.assert_called_once()
        self.assertEqual(10, raw_tool.return_value.execute.call_args.args[0]["count"])

    def test_normal_chat_passes_search_sources_to_model(self):
        search_payload = {
            "query": "\u6700\u65b0\u89c6\u9891\u751f\u6210\u6a21\u578b\u6587\u6863",
            "backend": "bocha",
            "results": [{
                "title": "Video model documentation",
                "url": "https://example.com/video-model",
                "snippet": "Current duration and aspect-ratio limits.",
            }],
        }
        with (
            patch("services.video.video_agent_service.is_prompt_analysis_enabled", return_value=True),
            patch("services.video.video_agent_service.video_agent_model", return_value="tt-5.6-sol"),
            patch(
                "services.video.video_agent_service._run_web_search",
                return_value={"status": "success", "payload": search_payload},
            ) as search,
            patch("services.video.video_agent_service.request_text_completion", return_value="\u56de\u7b54") as completion,
        ):
            result = chat(prompt="\u8bf7\u641c\u7d22\u6700\u65b0\u89c6\u9891\u751f\u6210\u6a21\u578b\u6587\u6863")

        self.assertEqual("\u56de\u7b54", result["message"])
        search.assert_called_once()
        content = completion.call_args.kwargs["content"]
        self.assertIn("https://example.com/video-model", content)
        self.assertIn("[WEB SEARCH RESULTS]", content)

    def test_normal_chat_does_not_search_regular_questions(self):
        with (
            patch("services.video.video_agent_service.is_prompt_analysis_enabled", return_value=True),
            patch("services.video.video_agent_service.video_agent_model", return_value="tt-5.6-sol"),
            patch("services.video.video_agent_service._run_web_search") as search,
            patch("services.video.video_agent_service.request_text_completion", return_value="\u56de\u7b54"),
        ):
            chat(prompt="\u600e\u4e48\u8bbe\u7f6e\u62cd\u6444\u7684\u5feb\u95e8")

        search.assert_not_called()

    def test_reasoning_request_passes_search_sources_to_responses_api(self):
        captured: dict[str, object] = {}

        def open_once(payload, *, model, started_at):
            captured.update(payload)
            return iter([])

        search_payload = {
            "query": "\u5b98\u7f51\u7684\u89c6\u9891\u6a21\u578b\u53c2\u6570",
            "backend": "serpapi",
            "results": [{
                "title": "Official model parameters",
                "url": "https://example.com/official-params",
                "snippet": "Official parameter reference.",
            }],
        }
        with (
            patch("services.video.video_agent_service.is_prompt_analysis_enabled", return_value=True),
            patch("services.video.video_agent_service.video_agent_model", return_value="tt-5.6-sol"),
            patch(
                "services.video.video_agent_service._run_web_search",
                return_value={"status": "success", "payload": search_payload},
            ) as search,
            patch("services.video.video_agent_service.config.get_openai_relay_settings", return_value={}),
            patch(
                "services.video.video_agent_service.run_with_relay_pool",
                side_effect=lambda _settings, _operation, action: action(),
            ),
            patch("services.video.video_agent_service._open_reasoning_chat_stream_once", side_effect=open_once),
        ):
            list(open_reasoning_chat_stream(prompt="\u8bf7\u67e5\u8be2\u5b98\u7f51\u7684\u89c6\u9891\u6a21\u578b\u53c2\u6570"))

        search.assert_called_once()
        self.assertIn("https://example.com/official-params", str(captured["input"]))
        self.assertIn("[WEB SEARCH RESULTS]", str(captured["input"]))


if __name__ == "__main__":
    unittest.main()
