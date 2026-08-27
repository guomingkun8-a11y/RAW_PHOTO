from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import services.ecommerce.video_analysis_service as module
from services.ecommerce.video_analysis_service import ExtractedFrame, ProfessionalVideoAnalysisService


class VideoAnalysisServiceTests(unittest.TestCase):
    def test_analyze_video_extracts_frames_transcribes_and_persists_ready_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            frame_path = Path(temp_dir) / "frame.jpg"
            frame_path.write_bytes(b"jpeg")
            settings = {
                "enabled": True,
                "ffmpeg_path": "ffmpeg",
                "ffprobe_path": "ffprobe",
                "max_duration_secs": 300,
                "max_frames": 2,
                "frame_interval_secs": 8,
                "frame_width": 512,
                "vision_model": "gpt-5.6-sol",
                "asr_enabled": True,
                "asr_model": "whisper-1",
                "audio_max_mb": 25,
            }
            current = {
                "videoId": "video-1",
                "name": "demo.mp4",
                "url": "https://cdn.example.test/demo.mp4",
                "analysisStatus": "pending",
            }
            asset = {**current, "filename": "demo.mp4"}
            frame = ExtractedFrame(index=1, time_sec=0.0, path=frame_path, data_url="data:image/jpeg;base64,anBlZw==")
            vision = {
                "model": "gpt-5.6-sol",
                "summary": "视频展示一款黑色保温杯。",
                "productProfile": {"productName": "保温杯", "category": "杯具"},
                "sceneSummary": "桌面使用场景。",
                "keyFrames": [{"timeSec": 0, "observation": "杯子位于画面中央。"}],
                "transcriptSummary": "口播强调便携。",
                "sellingPoints": ["便携"],
                "visualDirections": ["桌面产品主图"],
                "recommendedImagePrompts": ["生成桌面场景保温杯主图"],
                "risks": [],
            }

            with (
                mock.patch.object(module.config, "get_video_analysis_settings", return_value=settings),
                mock.patch.object(module.professional_video_asset_service, "get_video", return_value=current),
                mock.patch.object(module.professional_video_asset_service, "mark_analysis_processing") as processing,
                mock.patch.object(module.professional_video_asset_service, "download_to_path", return_value=asset) as download,
                mock.patch.object(module, "_probe_video", return_value={"durationSec": 12, "width": 1280, "height": 720, "hasAudio": True}),
                mock.patch.object(module, "_extract_frames", return_value=[frame]),
                mock.patch.object(module, "_extract_audio", return_value=True) as extract_audio,
                mock.patch.object(module, "_transcribe_audio", return_value={"status": "ready", "text": "便携保温杯", "error": ""}),
                mock.patch.object(module, "_analyze_frames", return_value=vision),
                mock.patch.object(module.professional_video_asset_service, "mark_analysis_ready") as ready,
            ):
                analysis = ProfessionalVideoAnalysisService().analyze_video("video-1", owner_id="owner-1")

        processing.assert_called_once_with("video-1", owner_id="owner-1")
        download.assert_called_once()
        extract_audio.assert_called_once()
        ready.assert_called_once()
        self.assertEqual(analysis["status"], "ready")
        self.assertEqual(analysis["summary"], "视频展示一款黑色保温杯。")
        self.assertEqual(analysis["transcript"]["text"], "便携保温杯")
        self.assertEqual(analysis["sampling"]["frameCount"], 1)
        self.assertEqual(analysis["keyFrames"][0]["observation"], "杯子位于画面中央。")

    def test_analyze_video_returns_existing_ready_analysis_without_reprocessing(self) -> None:
        current = {"videoId": "video-1", "analysisStatus": "ready", "analysis": {"summary": "已解析"}}
        with (
            mock.patch.object(module.professional_video_asset_service, "get_video", return_value=current),
            mock.patch.object(module.professional_video_asset_service, "mark_analysis_processing") as processing,
        ):
            analysis = ProfessionalVideoAnalysisService().analyze_video("video-1", owner_id="owner-1")

        self.assertEqual(analysis, {"summary": "已解析"})
        processing.assert_not_called()


if __name__ == "__main__":
    unittest.main()
