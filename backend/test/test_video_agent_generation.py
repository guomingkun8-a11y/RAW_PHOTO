from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services.video import video_agent_service as module
from services.video.video_agent_service import VideoAgentMessageService


IDENTITY = {"id": "owner-1", "username": "tester", "name": "测试用户"}
IMAGE = {
    "name": "opening.png",
    "url": "https://assets.example.test/opening.png",
    "mime_type": "image/png",
}


def _decision(**overrides: object) -> dict[str, object]:
    return {
        "action": "generate_video",
        "generation_prompt": "商品保持一致，镜头平滑推进，最后停在品牌标志。",
        "duration_secs": 10,
        "resolution": "768P",
        **overrides,
    }


def _video(status: str) -> dict[str, object]:
    item: dict[str, object] = {
        "videoId": "video-1",
        "name": "reference.mp4",
        "url": "https://assets.example.test/reference.mp4",
        "mimeType": "video/mp4",
        "analysisStatus": status,
    }
    if status == "ready":
        item["analysis"] = {
            "summary": "商品从正面缓慢转到侧面。",
            "keyFrames": [{"timeSec": 1, "observation": "商品位于画面中央。"}],
        }
    return item


class VideoAgentGenerationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.service = VideoAgentMessageService(
            f"sqlite:///{Path(self.temp_dir.name) / 'video-agent.db'}"
        )
        self.addCleanup(self.service.close)
        self.image_settings = patch.object(
            module.config,
            "get_image_reference_upload_settings",
            return_value={"public_base_url": "https://assets.example.test"},
        )
        self.image_settings.start()
        self.addCleanup(self.image_settings.stop)

    def test_explicit_request_submits_hailuo_first_last_frame_task(self) -> None:
        with (
            patch.object(module.professional_video_asset_service, "list_videos", return_value=[]),
            patch.object(module, "video_agent_model", return_value="tt-5.6-sol"),
            patch.object(module, "upstream_chat_model", side_effect=lambda model: model),
            patch.object(module, "request_json_completion", return_value=_decision()) as router,
            patch.object(
                module.video_generation_task_service,
                "submit_task",
                return_value={
                    "id": "video-agent-generation-task-1",
                    "status": "queued",
                    "prompt": "数据库中实际执行的提示词。",
                    "duration_secs": 9,
                    "resolution": "480P",
                },
            ) as submit,
            patch.object(module, "chat") as chat,
        ):
            saved = self.service.create_message(
                identity=IDENTITY,
                prompt="直接生成这段商品视频",
                conversation_id="conversation-1",
                turn_id="turn-1",
                images=[IMAGE, {**IMAGE, "name": "closing.png", "url": "https://assets.example.test/closing.png"}],
            )

        chat.assert_not_called()
        router_content = router.call_args.kwargs["content"]
        self.assertIsInstance(router_content, list)
        self.assertEqual(3, len(router_content))
        submit.assert_called_once()
        kwargs = submit.call_args.kwargs
        self.assertEqual("hailuo-h3-max-shouweizhen", kwargs["model"])
        self.assertEqual("image_to_video", kwargs["mode"])
        self.assertEqual("adaptive", kwargs["aspect_ratio"])
        self.assertEqual(10, kwargs["duration_secs"])
        self.assertEqual("768P", kwargs["quality"])
        self.assertEqual("768P", kwargs["resolution"])
        self.assertEqual(
            ["https://assets.example.test/opening.png", "https://assets.example.test/closing.png"],
            kwargs["image_urls"],
        )
        self.assertEqual("conversation-1", kwargs["conversation_id"])
        self.assertEqual("turn-1", kwargs["turn_id"])
        self.assertIn("首帧", saved["message"])
        self.assertEqual("generation", saved["attachments"][-1]["kind"])
        self.assertEqual("video-agent-generation-task-1", saved["attachments"][-1]["task_id"])
        self.assertEqual("数据库中实际执行的提示词。", saved["attachments"][-1]["prompt"])
        self.assertEqual(9, saved["attachments"][-1]["duration_secs"])
        self.assertEqual("480P", saved["attachments"][-1]["resolution"])
        self.assertIn("时长 9 秒，清晰度 480P", saved["message"])

    def test_generation_request_without_image_asks_for_first_frame(self) -> None:
        with (
            patch.object(module.professional_video_asset_service, "list_videos", return_value=[]),
            patch.object(module, "video_agent_model", return_value="tt-5.6-sol"),
            patch.object(module, "upstream_chat_model", side_effect=lambda model: model),
            patch.object(module, "request_json_completion", return_value=_decision()),
            patch.object(module.video_generation_task_service, "submit_task") as submit,
            patch.object(module, "chat") as chat,
        ):
            saved = self.service.create_message(
                identity=IDENTITY,
                prompt="现在直接生成视频",
                conversation_id="conversation-1",
                turn_id="turn-1",
            )

        submit.assert_not_called()
        chat.assert_not_called()
        self.assertEqual("completed", saved["status"])
        self.assertIn("上传 1 张首帧图", saved["message"])

    def test_generation_discussion_remains_normal_chat_when_router_declines(self) -> None:
        with (
            patch.object(module.professional_video_asset_service, "list_videos", return_value=[]),
            patch.object(module, "video_agent_model", return_value="tt-5.6-sol"),
            patch.object(module, "request_json_completion", return_value={"action": "chat"}) as router,
            patch.object(module.video_generation_task_service, "submit_task") as submit,
            patch.object(module, "chat", return_value={"message": "可以先整理镜头提示词。"}) as chat,
        ):
            saved = self.service.create_message(
                identity=IDENTITY,
                prompt="怎么写视频生成提示词？",
                conversation_id="conversation-1",
                turn_id="turn-1",
            )

        router.assert_called_once()
        submit.assert_not_called()
        chat.assert_called_once()
        self.assertEqual("可以先整理镜头提示词。", saved["message"])

    def test_unrelated_conversation_skips_generation_router(self) -> None:
        with (
            patch.object(module.professional_video_asset_service, "list_videos", return_value=[]),
            patch.object(module, "request_json_completion") as router,
            patch.object(module.video_generation_task_service, "submit_task") as submit,
            patch.object(module, "chat", return_value={"message": "快门可从帧率的两倍倒数开始。"}) as chat,
        ):
            saved = self.service.create_message(
                identity=IDENTITY,
                prompt="拍摄 25 帧视频时快门怎么设置？",
                conversation_id="conversation-1",
                turn_id="turn-1",
            )

        router.assert_not_called()
        submit.assert_not_called()
        chat.assert_called_once()
        self.assertEqual("快门可从帧率的两倍倒数开始。", saved["message"])

    def test_router_failure_falls_back_to_chat(self) -> None:
        with (
            patch.object(module.professional_video_asset_service, "list_videos", return_value=[]),
            patch.object(module, "video_agent_model", return_value="tt-5.6-sol"),
            patch.object(module, "request_json_completion", side_effect=RuntimeError("router unavailable")),
            patch.object(module.video_generation_task_service, "submit_task") as submit,
            patch.object(module, "chat", return_value={"message": "继续按普通对话回答。"}) as chat,
            patch.object(module.logger, "warning"),
        ):
            saved = self.service.create_message(
                identity=IDENTITY,
                prompt="帮我生成视频之前先解释一下",
                conversation_id="conversation-1",
                turn_id="turn-1",
            )

        submit.assert_not_called()
        chat.assert_called_once()
        self.assertEqual("继续按普通对话回答。", saved["message"])

    def test_reasoning_mode_returns_generation_task_in_completed_event(self) -> None:
        with (
            patch.object(module.professional_video_asset_service, "list_videos", return_value=[]),
            patch.object(module, "video_agent_model", return_value="tt-5.6-sol"),
            patch.object(module, "upstream_chat_model", side_effect=lambda model: model),
            patch.object(module, "request_json_completion", return_value=_decision()),
            patch.object(
                module.video_generation_task_service,
                "submit_task",
                return_value={"id": "reasoning-generation-1", "status": "queued"},
            ),
            patch.object(module, "open_reasoning_chat_stream") as reasoning_chat,
        ):
            events = list(self.service.create_reasoning_message_stream(
                identity=IDENTITY,
                prompt="就按这个直接生成",
                conversation_id="conversation-1",
                turn_id="turn-1",
                images=[IMAGE],
            ))

        reasoning_chat.assert_not_called()
        self.assertEqual("completed", events[-1]["type"])
        message = events[-1]["message"]
        self.assertTrue(message["reasoning_enabled"])
        self.assertEqual("reasoning-generation-1", message["attachments"][-1]["task_id"])

    def test_pending_video_is_parsed_before_generation_routing_and_submission(self) -> None:
        with (
            patch.object(module.professional_video_asset_service, "list_videos", return_value=[_video("queued")]),
            patch.object(module, "request_json_completion") as router,
            patch.object(module.video_generation_task_service, "submit_task") as submit,
        ):
            pending = self.service.create_message(
                identity=IDENTITY,
                prompt="参考这个视频，直接生成新的商品镜头",
                conversation_id="conversation-1",
                turn_id="turn-1",
                images=[IMAGE],
                videos=[{"video_id": "video-1"}],
            )

        self.assertEqual("analyzing", pending["status"])
        router.assert_not_called()
        submit.assert_not_called()

        with (
            patch.object(module.professional_video_asset_service, "list_videos", return_value=[_video("ready")]),
            patch.object(module, "video_agent_model", return_value="tt-5.6-sol"),
            patch.object(module, "upstream_chat_model", side_effect=lambda model: model),
            patch.object(module, "request_json_completion", return_value=_decision()) as router,
            patch.object(
                module.video_generation_task_service,
                "submit_task",
                return_value={"id": "post-analysis-generation-1", "status": "queued"},
            ) as submit,
            patch.object(module, "chat") as chat,
        ):
            processed = self.service.process_pending_messages_for_video("video-1", owner_id="owner-1")

        self.assertEqual(1, processed)
        router.assert_called_once()
        self.assertIn("商品从正面缓慢转到侧面", str(router.call_args.kwargs["content"]))
        submit.assert_called_once()
        chat.assert_not_called()
        saved = self.service.list_messages(identity=IDENTITY, conversation_id="conversation-1")["items"][0]
        self.assertEqual("completed", saved["status"])
        self.assertEqual("post-analysis-generation-1", saved["attachments"][-1]["task_id"])


if __name__ == "__main__":
    unittest.main()
