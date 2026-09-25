from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.video_agent as video_agent_api
from services.video.video_agent_service import VideoAgentMessageService


OWNER_ONE = {"id": "owner-1", "username": "one", "name": "Owner One", "role": "user"}
OWNER_TWO = {"id": "owner-2", "username": "two", "name": "Owner Two", "role": "user"}


def _save(
    service: VideoAgentMessageService,
    identity: dict[str, object],
    message_id: str,
    conversation_id: str,
    *,
    video_ids: tuple[str, ...] = (),
    generation_task_ids: tuple[str, ...] = (),
) -> None:
    with mock.patch("services.video.video_agent_service.uuid4", return_value=mock.Mock(hex=message_id.removeprefix("video-agent-"))):
        service.save_message(
            identity=identity,
            conversation_id=conversation_id,
            turn_id=f"turn-{message_id}",
            prompt=f"prompt-{message_id}",
            result={"message": f"answer-{message_id}"},
            attachments=[
                *[
                    {
                        "kind": "video",
                        "video_id": video_id,
                        "name": f"{video_id}.mp4",
                        "url": f"https://cdn.example.test/{video_id}.mp4",
                    }
                    for video_id in video_ids
                ],
                *[
                    {
                        "kind": "generation",
                        "task_id": task_id,
                        "name": "海螺 H3 Max 首尾帧",
                    }
                    for task_id in generation_task_ids
                ],
            ],
        )


class VideoAgentMessageDeleteTests(unittest.TestCase):
    def test_delete_conversation_is_hard_deleted_and_scoped_to_owner(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            service = VideoAgentMessageService(f"sqlite:///{Path(temp_dir) / 'video-agent.db'}")
            try:
                _save(service, OWNER_ONE, "video-agent-one-a", "conversation-shared")
                _save(service, OWNER_ONE, "video-agent-one-b", "conversation-shared")
                _save(service, OWNER_TWO, "video-agent-two", "conversation-shared")

                deleted = service.delete_conversation(identity=OWNER_ONE, conversation_id="conversation-shared")

                self.assertEqual(2, deleted)
                self.assertEqual(
                    0,
                    service.list_messages(identity=OWNER_ONE, conversation_id="conversation-shared")["total"],
                )
                self.assertEqual(
                    1,
                    service.list_messages(identity=OWNER_TWO, conversation_id="conversation-shared")["total"],
                )
            finally:
                service.close()

    def test_delete_message_only_deletes_matching_owner_and_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            service = VideoAgentMessageService(f"sqlite:///{Path(temp_dir) / 'video-agent.db'}")
            try:
                _save(service, OWNER_ONE, "video-agent-one", "conversation-one")
                _save(service, OWNER_TWO, "video-agent-two", "conversation-two")

                self.assertEqual(0, service.delete_message(identity=OWNER_TWO, message_id="video-agent-one"))
                self.assertEqual(1, service.delete_message(identity=OWNER_ONE, message_id="video-agent-one"))
                self.assertEqual(0, service.delete_message(identity=OWNER_ONE, message_id="video-agent-one"))
            finally:
                service.close()

    def test_delete_conversation_cleans_unreferenced_video_asset(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            service = VideoAgentMessageService(f"sqlite:///{Path(temp_dir) / 'video-agent.db'}")
            try:
                _save(
                    service,
                    OWNER_ONE,
                    "video-agent-one",
                    "conversation-one",
                    video_ids=("video-asset-1",),
                )
                with mock.patch(
                    "services.video.video_agent_service.professional_video_asset_service.delete_video",
                    return_value=True,
                ) as delete_video:
                    deleted = service.delete_conversation(
                        identity=OWNER_ONE,
                        conversation_id="conversation-one",
                    )

                self.assertEqual(1, deleted)
                delete_video.assert_called_once_with(
                    "video-asset-1",
                    owner_id="owner-1",
                )
            finally:
                service.close()

    def test_delete_message_preserves_video_referenced_by_another_message(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            service = VideoAgentMessageService(f"sqlite:///{Path(temp_dir) / 'video-agent.db'}")
            try:
                _save(
                    service,
                    OWNER_ONE,
                    "video-agent-one",
                    "conversation-one",
                    video_ids=("video-shared",),
                )
                _save(
                    service,
                    OWNER_ONE,
                    "video-agent-two",
                    "conversation-two",
                    video_ids=("video-shared",),
                )
                with mock.patch(
                    "services.video.video_agent_service.professional_video_asset_service.delete_video",
                    return_value=True,
                ) as delete_video:
                    self.assertEqual(
                        1,
                        service.delete_message(identity=OWNER_ONE, message_id="video-agent-one"),
                    )
                    delete_video.assert_not_called()

                    self.assertEqual(
                        1,
                        service.delete_message(identity=OWNER_ONE, message_id="video-agent-two"),
                    )
                    delete_video.assert_called_once_with(
                        "video-shared",
                        owner_id="owner-1",
                    )
            finally:
                service.close()

    def test_delete_last_message_removes_deduplicated_video_from_any_conversation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            service = VideoAgentMessageService(f"sqlite:///{Path(temp_dir) / 'video-agent.db'}")
            try:
                _save(
                    service,
                    OWNER_ONE,
                    "video-agent-first",
                    "conversation-first",
                    video_ids=("video-deduplicated",),
                )
                _save(
                    service,
                    OWNER_ONE,
                    "video-agent-second",
                    "conversation-second",
                    video_ids=("video-deduplicated",),
                )
                with mock.patch(
                    "services.video.video_agent_service.professional_video_asset_service.delete_video",
                    return_value=True,
                ) as delete_video:
                    self.assertEqual(
                        1,
                        service.delete_conversation(
                            identity=OWNER_ONE,
                            conversation_id="conversation-second",
                        ),
                    )
                    delete_video.assert_not_called()

                    self.assertEqual(
                        1,
                        service.delete_conversation(
                            identity=OWNER_ONE,
                            conversation_id="conversation-first",
                        ),
                    )
                    delete_video.assert_called_once_with(
                        "video-deduplicated",
                        owner_id="owner-1",
                    )
            finally:
                service.close()

    def test_delete_conversation_cancels_and_hard_deletes_generation_task(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            service = VideoAgentMessageService(f"sqlite:///{Path(temp_dir) / 'video-agent.db'}")
            try:
                _save(
                    service,
                    OWNER_ONE,
                    "video-agent-generation",
                    "conversation-generation",
                    generation_task_ids=("generation-task-1",),
                )
                with (
                    mock.patch(
                        "services.video.video_agent_service.video_generation_task_service.cancel_task",
                        return_value={"id": "generation-task-1", "status": "canceled"},
                    ) as cancel_task,
                    mock.patch(
                        "services.video.video_agent_service.video_generation_task_service.delete_task",
                        return_value={"ok": True, "deleted": 1},
                    ) as delete_task,
                ):
                    deleted = service.delete_conversation(
                        identity=OWNER_ONE,
                        conversation_id="conversation-generation",
                    )

                self.assertEqual(1, deleted)
                cancel_task.assert_called_once_with({"id": "owner-1"}, "generation-task-1")
                delete_task.assert_called_once_with({"id": "owner-1"}, "generation-task-1")
            finally:
                service.close()

    def test_delete_message_preserves_generation_task_referenced_elsewhere(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            service = VideoAgentMessageService(f"sqlite:///{Path(temp_dir) / 'video-agent.db'}")
            try:
                _save(
                    service,
                    OWNER_ONE,
                    "video-agent-gen-one",
                    "conversation-generation-one",
                    generation_task_ids=("generation-shared",),
                )
                _save(
                    service,
                    OWNER_ONE,
                    "video-agent-gen-two",
                    "conversation-generation-two",
                    generation_task_ids=("generation-shared",),
                )
                with (
                    mock.patch(
                        "services.video.video_agent_service.video_generation_task_service.cancel_task",
                        return_value={"id": "generation-shared", "status": "canceled"},
                    ) as cancel_task,
                    mock.patch(
                        "services.video.video_agent_service.video_generation_task_service.delete_task",
                        return_value={"ok": True, "deleted": 1},
                    ) as delete_task,
                ):
                    self.assertEqual(
                        1,
                        service.delete_message(identity=OWNER_ONE, message_id="video-agent-gen-one"),
                    )
                    cancel_task.assert_not_called()
                    delete_task.assert_not_called()

                    self.assertEqual(
                        1,
                        service.delete_message(identity=OWNER_ONE, message_id="video-agent-gen-two"),
                    )
                    cancel_task.assert_called_once_with({"id": "owner-1"}, "generation-shared")
                    delete_task.assert_called_once_with({"id": "owner-1"}, "generation-shared")
            finally:
                service.close()


class VideoAgentDeleteApiTests(unittest.TestCase):
    def setUp(self):
        self.identity_patcher = mock.patch.object(video_agent_api, "require_identity", return_value=OWNER_ONE)
        self.identity_patcher.start()
        self.addCleanup(self.identity_patcher.stop)
        self.service_patcher = mock.patch.object(video_agent_api, "video_agent_message_service")
        self.service = self.service_patcher.start()
        self.addCleanup(self.service_patcher.stop)
        app = FastAPI()
        app.include_router(video_agent_api.create_router())
        self.client = TestClient(app)

    def test_delete_conversation_returns_deleted_count(self):
        self.service.delete_conversation.return_value = 2

        response = self.client.delete(
            "/api/video-agent/conversations/conversation-1",
            headers={"Authorization": "Bearer test"},
        )

        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual({"ok": True, "deleted": 2}, response.json())
        self.service.delete_conversation.assert_called_once_with(
            identity=OWNER_ONE,
            conversation_id="conversation-1",
        )

    def test_delete_plan_returns_not_found_when_id_is_not_owned(self):
        self.service.delete_message.return_value = 0

        response = self.client.delete(
            "/api/video-agent/plans/video-agent-1",
            headers={"Authorization": "Bearer test"},
        )

        self.assertEqual(404, response.status_code, response.text)
        self.service.delete_message.assert_called_once_with(
            identity=OWNER_ONE,
            message_id="video-agent-1",
        )


if __name__ == "__main__":
    unittest.main()
