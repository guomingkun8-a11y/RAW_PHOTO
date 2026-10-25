from __future__ import annotations

import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from services.billing.chat_usage import capture_chat_response, chat_usage_scope
from services.billing.upstream_usage_service import upstream_usage_service
from services.ecommerce import cow_agent_runtime_service, prompt_analysis_service
from services.video import video_agent_service


class ChatUsageCaptureTests(unittest.TestCase):
    def test_scope_captures_header_and_resets(self):
        response = SimpleNamespace(status_code=200, headers={"X-Task-Id": "task-1"})
        with patch.object(upstream_usage_service, "capture_chat_call") as capture:
            with chat_usage_scope("user-1", "image_agent", "run-1"):
                capture_chat_response(response, model="tt-5.6-sol", api_key="secret")
            capture_chat_response(response, model="tt-5.6-sol", api_key="secret")
        capture.assert_called_once_with(
            task_id="task-1", model="tt-5.6-sol", owner_id="user-1",
            source="image_agent", local_task_id="run-1", api_key="secret",
        )

    def test_failed_request_and_empty_scope_do_not_capture(self):
        response = SimpleNamespace(status_code=500, headers={"X-Task-Id": "task-2"})
        with patch.object(upstream_usage_service, "capture_chat_call") as capture:
            with chat_usage_scope("user-1", "image_agent"):
                capture_chat_response(response, model="tt-5.6-sol", api_key="secret")
                with chat_usage_scope("", "image_agent"):
                    capture_chat_response(
                        SimpleNamespace(status_code=200, headers=response.headers),
                        model="tt-5.6-sol", api_key="secret",
                    )
        capture.assert_not_called()

    def test_prompt_completion_uses_response_header_and_authenticated_user(self):
        response = SimpleNamespace(
            status_code=200,
            headers={"X-Task-Id": "task-3"},
            json=lambda: {"choices": [{"message": {"content": "ok"}}]},
        )
        with (
            patch.object(upstream_usage_service, "capture_chat_call") as capture,
            patch.object(prompt_analysis_service.requests, "post", return_value=response),
            patch.object(prompt_analysis_service, "_relay_url", return_value="https://relay.test/v1/chat/completions"),
            patch.object(prompt_analysis_service, "_relay_headers", return_value={"Authorization": "Bearer secret"}),
            patch.object(prompt_analysis_service, "run_with_relay_pool", side_effect=lambda _settings, _operation, action: action()),
            patch.object(prompt_analysis_service.proxy_settings, "build_session_kwargs", return_value={}),
        ):
            prompt_analysis_service._chat_completion(
                {"model": "tt-5.6-sol", "user": "user-2"}, operation="canvas_assistant"
            )
        capture.assert_called_once_with(
            task_id="task-3", model="tt-5.6-sol", owner_id="user-2",
            source="canvas_assistant", local_task_id="", api_key="secret",
        )

    def test_cow_agent_stream_captures_before_iteration(self):
        class Response:
            status_code = 200
            headers = {"X-Task-Id": "cow-1", "content-type": "text/event-stream"}

            def iter_lines(self):
                return iter(['data: {"choices": [{"delta": {"content": "ok"}}]}'])

            def close(self):
                pass

        with (
            patch.object(upstream_usage_service, "capture_chat_call") as capture,
            patch.object(cow_agent_runtime_service.requests, "post", return_value=Response()),
            patch.object(cow_agent_runtime_service.proxy_settings, "build_session_kwargs", return_value={}),
        ):
            with chat_usage_scope("user-1", "cowagent_dialogue"):
                chunks = cow_agent_runtime_service._open_sse_chunks(
                    {"model": "tt-5.6-sol"}, "https://relay.test", "pool-key"
                )
            self.assertEqual(len(list(chunks)), 1)
        capture.assert_called_once_with(
            task_id="cow-1", model="tt-5.6-sol", owner_id="user-1",
            source="cowagent_dialogue", local_task_id="", api_key="pool-key",
        )

    def test_video_reasoning_stream_captures_before_iteration(self):
        class Response:
            status_code = 200
            headers = {"X-Task-Id": "video-1", "content-type": "text/event-stream"}

            def iter_lines(self):
                return iter([
                    'data: {"type": "response.output_text.delta", "delta": "ok"}',
                    "",
                    'data: {"type": "response.completed", "response": {}}',
                    "",
                ])

            def close(self):
                pass

        with (
            patch.object(upstream_usage_service, "capture_chat_call") as capture,
            patch.object(video_agent_service.requests, "post", return_value=Response()),
            patch.object(video_agent_service, "_relay_responses_url", return_value="https://relay.test/v1/responses"),
            patch.object(video_agent_service, "_relay_responses_headers", return_value={"Authorization": "Bearer pool-key"}),
            patch.object(video_agent_service.proxy_settings, "build_session_kwargs", return_value={}),
        ):
            with chat_usage_scope("user-2", "video_agent_reasoning", "turn-1"):
                events = video_agent_service._open_reasoning_chat_stream_once(
                    {"model": "tt-5.6-sol"}, model="tt-5.6-sol", started_at=time.perf_counter()
                )
            self.assertEqual(list(events)[-1]["type"], "result")
        capture.assert_called_once_with(
            task_id="video-1", model="tt-5.6-sol", owner_id="user-2",
            source="video_agent_reasoning", local_task_id="turn-1", api_key="pool-key",
        )


if __name__ == "__main__":
    unittest.main()
