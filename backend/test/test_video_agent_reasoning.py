from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from services.video.video_agent_service import (
    VIDEO_AGENT_SYSTEM_PROMPT,
    VIDEO_EXPERT_SYSTEM_PROMPT,
    VideoAgentMessageService,
    _iter_sse_json,
    _reasoning_events,
    chat,
    open_reasoning_chat_stream,
)


class _FakeSseResponse:
    def __init__(self, lines: list[bytes]):
        self.lines = lines
        self.closed = False

    def iter_lines(self):
        return iter(self.lines)

    def close(self):
        self.closed = True


class VideoAgentReasoningTests(unittest.TestCase):
    def test_expert_prompt_is_the_active_agent_prompt(self):
        self.assertEqual(VIDEO_EXPERT_SYSTEM_PROMPT, VIDEO_AGENT_SYSTEM_PROMPT)
        self.assertIn("家可美的专业视频创作与制作顾问", VIDEO_AGENT_SYSTEM_PROMPT)
        self.assertIn("不输出逐字的隐含思维过程", VIDEO_AGENT_SYSTEM_PROMPT)

    def test_normal_chat_uses_the_expert_prompt(self):
        with (
            patch("services.video.video_agent_service.is_prompt_analysis_enabled", return_value=True),
            patch("services.video.video_agent_service.video_agent_model", return_value="tt-5.6-sol"),
            patch("services.video.video_agent_service.request_text_completion", return_value="回答") as completion,
        ):
            result = chat(prompt="怎么设置快门？")

        self.assertEqual("回答", result["message"])
        self.assertEqual(VIDEO_EXPERT_SYSTEM_PROMPT, completion.call_args.kwargs["system_prompt"])

    def test_reasoning_request_targets_responses_api_shape(self):
        captured: dict[str, object] = {}

        def open_once(payload, *, model, started_at):
            captured.update(payload)
            captured["resolved_model"] = model
            return iter([])

        with (
            patch("services.video.video_agent_service.is_prompt_analysis_enabled", return_value=True),
            patch("services.video.video_agent_service.video_agent_model", return_value="tt-5.6-sol"),
            patch("services.video.video_agent_service._run_web_search", return_value={"status": "error", "error": "test disabled"}),
            patch("services.video.video_agent_service.config.get_openai_relay_settings", return_value={}),
            patch("services.video.video_agent_service.run_with_relay_pool", side_effect=lambda _settings, _operation, action: action()),
            patch("services.video.video_agent_service._open_reasoning_chat_stream_once", side_effect=open_once),
        ):
            list(open_reasoning_chat_stream(prompt="最新问题", history=[{"user": "之前", "assistant": "回答"}]))

        self.assertEqual("tt-5.6-sol", captured["model"])
        self.assertEqual("tt-5.6-sol", captured["resolved_model"])
        self.assertEqual(VIDEO_EXPERT_SYSTEM_PROMPT, captured["instructions"])
        self.assertEqual({"effort": "high", "summary": "auto"}, captured["reasoning"])
        self.assertTrue(captured["stream"])
        self.assertIn("最新问题", str(captured["input"]))
        self.assertIn("之前", str(captured["input"]))

    def test_sse_parser_uses_event_name_when_payload_omits_type(self):
        response = _FakeSseResponse([
            b"event: response.reasoning.delta",
            b'data: {"delta":"analyze"}',
            b"",
        ])

        self.assertEqual(
            [{"type": "response.reasoning.delta", "delta": "analyze"}],
            list(_iter_sse_json(response)),
        )
        self.assertTrue(response.closed)

    def test_reasoning_events_keep_summary_and_answer_separate(self):
        events = list(_reasoning_events(iter([
            {"type": "response.reasoning.delta", "delta": "先分析"},
            {"type": "response.reasoning_summary_text.delta", "delta": "，再比较。"},
            {"type": "response.output_text.delta", "delta": "最终回答"},
            {"type": "response.completed", "response": {}},
        ]), model="tt-5.6-sol", started_at=time.perf_counter()))

        self.assertEqual("reasoning.delta", events[0]["type"])
        self.assertEqual("answer.delta", events[2]["type"])
        result = events[-1]["result"]
        self.assertEqual("先分析，再比较。", result["reasoning_summary"])
        self.assertEqual("最终回答", result["message"])
        self.assertTrue(result["reasoning_enabled"])

    def test_completed_response_is_used_when_upstream_sends_no_deltas(self):
        events = list(_reasoning_events(iter([{
            "type": "response.completed",
            "response": {
                "output": [
                    {"type": "reasoning", "summary": [{"type": "summary_text", "text": "摘要"}]},
                    {"type": "message", "content": [{"type": "output_text", "text": "回答"}]},
                ],
            },
        }]), model="tt-5.6-sol", started_at=time.perf_counter()))

        result = events[-1]["result"]
        self.assertEqual("摘要", result["reasoning_summary"])
        self.assertEqual("回答", result["message"])

    def test_reasoning_message_is_persisted_with_history(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_url = f"sqlite:///{Path(temp_dir) / 'video-agent.db'}"
            service = VideoAgentMessageService(database_url)
            upstream = iter([
                {"type": "reasoning.delta", "delta": "摘要"},
                {"type": "answer.delta", "delta": "回答"},
                {
                    "type": "result",
                    "result": {
                        "message": "回答",
                        "reasoning_summary": "摘要",
                        "reasoning_enabled": True,
                        "chat_model": "tt-5.6-sol",
                        "duration_ms": 25,
                    },
                },
            ])
            identity = {"id": "user-1", "username": "tester", "name": "测试用户"}
            try:
                with patch(
                    "services.video.video_agent_service.open_reasoning_chat_stream",
                    return_value=upstream,
                ):
                    events = list(service.create_reasoning_message_stream(
                        identity=identity,
                        prompt="测试问题",
                        conversation_id="conversation-1",
                        turn_id="turn-1",
                    ))

                saved = events[-1]["message"]
                self.assertEqual("completed", events[-1]["type"])
                self.assertEqual("摘要", saved["reasoning_summary"])
                self.assertTrue(saved["reasoning_enabled"])
                history = service.list_messages(identity=identity, conversation_id="conversation-1")
                self.assertEqual("回答", history["items"][0]["message"])
                self.assertEqual("摘要", history["items"][0]["reasoning_summary"])
            finally:
                service.close()


if __name__ == "__main__":
    unittest.main()
