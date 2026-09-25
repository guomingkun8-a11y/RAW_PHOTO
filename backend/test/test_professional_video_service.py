from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import services.ecommerce.professional_video_service as module
from services.ecommerce.professional_video_service import (
    ProfessionalVideoAssetModel,
    ProfessionalVideoAssetService,
)
from services.ecommerce.video_analysis_queue_service import VideoAnalysisQueueUnavailable


def _add_asset(
    service: ProfessionalVideoAssetService,
    *,
    video_id: str,
    owner_id: str,
    conversation_id: str,
    sha256: str,
    object_key: str = "video/shared.mp4",
) -> None:
    session = service._session()
    try:
        session.add(ProfessionalVideoAssetModel(
            video_id=video_id,
            owner_id=owner_id,
            conversation_id=conversation_id,
            filename=f"{video_id}.mp4",
            mime_type="video/mp4",
            size=6,
            sha256=sha256,
            storage_provider="oss",
            bucket="video-bucket",
            object_key=object_key,
            url=f"https://cdn.example.test/{object_key}",
            status="uploaded",
            analysis_status="pending",
        ))
        session.commit()
    finally:
        session.close()


class ProfessionalVideoAssetDeleteTests(unittest.TestCase):
    def test_delete_video_hard_deletes_database_row_and_last_oss_object(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            service = ProfessionalVideoAssetService(f"sqlite:///{Path(temp_dir) / 'video.db'}")
            _add_asset(
                service,
                video_id="video-1",
                owner_id="owner-1",
                conversation_id="conversation-1",
                sha256="a" * 64,
            )
            client = mock.Mock()
            try:
                with mock.patch.object(module, "_oss_client", return_value=client):
                    deleted = service.delete_video(
                        "video-1",
                        owner_id="owner-1",
                        conversation_id="conversation-1",
                    )

                self.assertTrue(deleted)
                self.assertIsNone(service.get_video("video-1", owner_id="owner-1"))
                client.remove_object.assert_called_once_with("video-bucket", "video/shared.mp4")
            finally:
                service.close()

    def test_delete_video_preserves_object_referenced_by_another_asset_row(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            service = ProfessionalVideoAssetService(f"sqlite:///{Path(temp_dir) / 'video.db'}")
            _add_asset(
                service,
                video_id="video-1",
                owner_id="owner-1",
                conversation_id="conversation-1",
                sha256="a" * 64,
            )
            _add_asset(
                service,
                video_id="video-2",
                owner_id="owner-2",
                conversation_id="conversation-2",
                sha256="a" * 64,
            )
            client = mock.Mock()
            try:
                with mock.patch.object(module, "_oss_client", return_value=client):
                    deleted = service.delete_video(
                        "video-1",
                        owner_id="owner-1",
                        conversation_id="conversation-1",
                    )

                self.assertTrue(deleted)
                self.assertIsNotNone(service.get_video("video-2", owner_id="owner-2"))
                client.remove_object.assert_not_called()
            finally:
                service.close()

    def test_delete_video_requires_matching_owner_and_conversation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            service = ProfessionalVideoAssetService(f"sqlite:///{Path(temp_dir) / 'video.db'}")
            _add_asset(
                service,
                video_id="video-1",
                owner_id="owner-1",
                conversation_id="conversation-1",
                sha256="a" * 64,
            )
            try:
                self.assertFalse(service.delete_video(
                    "video-1",
                    owner_id="owner-2",
                    conversation_id="conversation-1",
                ))
                self.assertFalse(service.delete_video(
                    "video-1",
                    owner_id="owner-1",
                    conversation_id="conversation-2",
                ))
                self.assertIsNotNone(service.get_video("video-1", owner_id="owner-1"))
            finally:
                service.close()


class ProfessionalVideoAnalysisQueueTests(unittest.TestCase):
    def test_disabled_queue_marks_asset_failed_and_raises(self):
        service = ProfessionalVideoAssetService()
        current = {
            "videoId": "video-1",
            "conversationId": "conversation-1",
            "analysisStatus": "pending",
        }
        with (
            mock.patch.object(module.config, "get_video_analysis_settings", return_value={
                "enabled": True,
                "queue_enabled": False,
            }),
            mock.patch.object(service, "get_video", return_value=current),
            mock.patch.object(service, "mark_analysis_failed", return_value={
                **current,
                "analysisStatus": "failed",
            }) as mark_failed,
        ):
            with self.assertRaises(VideoAnalysisQueueUnavailable):
                service.request_analysis("video-1", owner_id="owner-1")

        mark_failed.assert_called_once_with(
            "video-1",
            owner_id="owner-1",
            error="video analysis queue is disabled",
        )

    def test_enqueue_failure_marks_asset_failed(self):
        service = ProfessionalVideoAssetService()
        current = {
            "videoId": "video-1",
            "conversationId": "conversation-1",
            "analysisStatus": "pending",
        }
        with (
            mock.patch.object(module.config, "get_video_analysis_settings", return_value={
                "enabled": True,
                "queue_enabled": True,
            }),
            mock.patch.object(service, "get_video", return_value=current),
            mock.patch.object(service, "mark_analysis_queued", return_value={
                **current,
                "analysisStatus": "queued",
            }),
            mock.patch.object(service, "mark_analysis_failed") as mark_failed,
            mock.patch(
                "services.ecommerce.video_analysis_queue_service.video_analysis_queue_service.enqueue",
                side_effect=VideoAnalysisQueueUnavailable("redis unavailable"),
            ),
        ):
            with self.assertRaises(VideoAnalysisQueueUnavailable):
                service.request_analysis("video-1", owner_id="owner-1")

        mark_failed.assert_called_once_with(
            "video-1",
            owner_id="owner-1",
            error="redis unavailable",
        )


if __name__ == "__main__":
    unittest.main()
