from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services.video import video_agent_service as module
from services.video.video_agent_service import VideoAgentMessageService


IDENTITY = {"id": "owner-1", "username": "tester", "name": "测试用户"}


def _video(status: str, *, error: str = "") -> dict[str, object]:
    item: dict[str, object] = {
        "videoId": "video-1",
        "name": "demo.mp4",
        "url": "https://cdn.example.test/demo.mp4",
        "mimeType": "video/mp4",
        "size": 123,
        "sha256": "a" * 64,
        "analysisStatus": status,
        "analysisError": error,
    }
    if status == "ready":
        item["analysis"] = {
            "summary": "画面展示蓝色保温杯。",
            "keyFrames": [{"timeSec": 2, "observation": "产品位于画面中央。"}],
            "transcript": {"status": "ready", "text": "轻巧便携"},
        }
    return item


class VideoAgentAsyncProcessingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        database_path = Path(self.temp_dir.name) / "video-agent.db"
        self.service = VideoAgentMessageService(f"sqlite:///{database_path}")
        self.addCleanup(self.service.close)

    def _create_pending(self, *, reasoning: bool = False) -> dict[str, object]:
        with (
            patch.object(module.professional_video_asset_service, "list_videos", return_value=[_video("queued")]),
            patch.object(module, "chat") as chat,
        ):
            if reasoning:
                events = list(self.service.create_reasoning_message_stream(
                    identity=IDENTITY,
                    prompt="分析这个视频",
                    conversation_id="conversation-1",
                    turn_id="turn-1",
                    videos=[{"video_id": "video-1"}],
                ))
                pending = events[-1]["message"]
            else:
                pending = self.service.create_message(
                    identity=IDENTITY,
                    prompt="分析这个视频",
                    conversation_id="conversation-1",
                    turn_id="turn-1",
                    videos=[{"video_id": "video-1"}],
                )
            chat.assert_not_called()
        self.assertEqual("analyzing", pending["status"])
        self.assertEqual("", pending["message"])
        return pending

    def test_unparsed_video_is_saved_without_calling_model_then_completed_by_worker(self) -> None:
        pending = self._create_pending()

        with (
            patch.object(module.professional_video_asset_service, "list_videos", return_value=[_video("ready")]),
            patch.object(
                module,
                "chat",
                return_value={"message": "已根据切片完成分析", "chat_model": "tt-5.6-sol"},
            ) as chat,
        ):
            processed = self.service.process_pending_messages_for_video("video-1", owner_id="owner-1")

        self.assertEqual(1, processed)
        chat.assert_called_once()
        self.assertIn("蓝色保温杯", chat.call_args.kwargs["video_context"])
        saved = self.service.list_messages(identity=IDENTITY, conversation_id="conversation-1")["items"][0]
        self.assertEqual(pending["id"], saved["id"])
        self.assertEqual("completed", saved["status"])
        self.assertEqual("已根据切片完成分析", saved["message"])

    def test_model_failure_marks_the_same_pending_message_failed(self) -> None:
        pending = self._create_pending()

        with (
            patch.object(module.professional_video_asset_service, "list_videos", return_value=[_video("ready")]),
            patch.object(module, "chat", side_effect=RuntimeError("model unavailable")),
            patch.object(module.logger, "exception"),
        ):
            processed = self.service.process_pending_messages_for_video("video-1", owner_id="owner-1")

        self.assertEqual(0, processed)
        saved = self.service.list_messages(identity=IDENTITY, conversation_id="conversation-1")["items"][0]
        self.assertEqual(pending["id"], saved["id"])
        self.assertEqual("failed", saved["status"])
        self.assertIn("model unavailable", saved["analysis_error"])

    def test_reasoning_pending_message_is_completed_after_video_analysis(self) -> None:
        pending = self._create_pending(reasoning=True)
        upstream = iter([{
            "type": "result",
            "result": {
                "message": "思考模式回答",
                "reasoning_summary": "先看切片，再总结。",
                "reasoning_enabled": True,
                "chat_model": "tt-5.6-sol",
            },
        }])

        with (
            patch.object(module.professional_video_asset_service, "list_videos", return_value=[_video("ready")]),
            patch.object(module, "open_reasoning_chat_stream", return_value=upstream),
        ):
            processed = self.service.process_pending_messages_for_video("video-1", owner_id="owner-1")

        self.assertEqual(1, processed)
        saved = self.service.list_messages(identity=IDENTITY, conversation_id="conversation-1")["items"][0]
        self.assertEqual(pending["id"], saved["id"])
        self.assertEqual("completed", saved["status"])
        self.assertEqual("思考模式回答", saved["message"])
        self.assertEqual("先看切片，再总结。", saved["reasoning_summary"])

    def test_recent_history_excludes_messages_that_are_still_analyzing(self) -> None:
        self.service.save_message(
            identity=IDENTITY,
            conversation_id="conversation-1",
            turn_id="completed-turn",
            prompt="已完成的问题",
            result={"message": "已完成的回答"},
        )
        self._create_pending()

        history = self.service.recent_history(identity=IDENTITY, conversation_id="conversation-1")

        self.assertEqual(
            [{"user": "已完成的问题", "assistant": "已完成的回答", "attachments": []}],
            history,
        )


if __name__ == "__main__":
    unittest.main()
