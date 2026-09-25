from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services.video import video_agent_service as module
from services.video.video_agent_service import VideoAgentMessageService


class VideoAgentMediaContextTests(unittest.TestCase):
    def test_uploaded_image_and_ready_video_are_sent_to_model_and_saved(self) -> None:
        identity = {"id": "owner-1", "username": "tester", "name": "测试用户"}
        video = {
            "videoId": "video-1",
            "name": "demo.mp4",
            "url": "https://cdn.example.test/demo.mp4",
            "mimeType": "video/mp4",
            "size": 123,
            "sha256": "a" * 64,
            "analysisStatus": "ready",
            "analysis": {
                "summary": "画面展示蓝色保温杯，出现桌面使用场景。",
                "media": {"durationSec": 12, "width": 1280, "height": 720},
                "keyFrames": [{"timeSec": 2, "observation": "产品位于画面中央。"}],
                "sellingPoints": ["便携"],
            },
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            service = VideoAgentMessageService(f"sqlite:///{Path(temp_dir) / 'video-agent.db'}")
            try:
                with (
                    patch.object(
                        module.config,
                        "get_image_reference_upload_settings",
                        return_value={"public_base_url": "https://assets.example.test"},
                    ),
                    patch.object(module.professional_video_asset_service, "list_videos", return_value=[video]),
                    patch.object(module, "is_prompt_analysis_enabled", return_value=True),
                    patch.object(module, "video_agent_model", return_value="tt-5.6-sol"),
                    patch.object(module, "request_text_completion", return_value="已分析附件") as completion,
                ):
                    saved = service.create_message(
                        identity=identity,
                        prompt="请分析附件中的产品",
                        conversation_id="conversation-1",
                        turn_id="turn-1",
                        images=[
                            {
                                "name": "product.png",
                                "url": "https://assets.example.test/product.png",
                                "mime_type": "image/png",
                            }
                        ],
                        videos=[{"video_id": "video-1"}],
                    )

                content = completion.call_args.kwargs["content"]
                self.assertIsInstance(content, list)
                self.assertIn("蓝色保温杯", content[0]["text"])
                self.assertIn("产品位于画面中央", content[0]["text"])
                self.assertEqual(
                    {"type": "image_url", "image_url": {"url": "https://assets.example.test/product.png", "detail": "high"}},
                    content[1],
                )
                self.assertEqual(
                    ["image", "video"],
                    [item["kind"] for item in saved["attachments"]],
                )
                self.assertEqual("video-1", saved["attachments"][1]["video_id"])
            finally:
                service.close()


if __name__ == "__main__":
    unittest.main()
