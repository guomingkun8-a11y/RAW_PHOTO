from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from services.video.video_generation_service import VideoGenerationTaskService
from services.video.video_generation_task_store import DatabaseVideoGenerationTaskStore


OWNER = {"id": "owner-video", "name": "Video Owner", "role": "user"}
OTHER_OWNER = {"id": "owner-other", "name": "Other Owner", "username": "other", "role": "user"}
ADMIN = {"id": "admin-video", "name": "Video Admin", "role": "admin"}


def wait_for_task(service: VideoGenerationTaskService, task_id: str, status: str, timeout: float = 2.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = (service.list_tasks(OWNER, [task_id]).get("items") or [None])[0]
        if last and last.get("status") == status:
            return last
        time.sleep(0.02)
    raise AssertionError(f"task {task_id} did not reach {status}, last={last}")


class VideoGenerationTaskServiceTests(unittest.TestCase):
    def make_service(self, handler):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp_dir.cleanup)
        store = DatabaseVideoGenerationTaskStore(f"sqlite:///{Path(self.tmp_dir.name) / 'video-generation.db'}")
        self.addCleanup(store.close)
        return VideoGenerationTaskService(
            task_store=store,
            task_queue=None,
            run_inline=True,
            generation_handler=handler,
            enabled_getter=lambda: True,
            max_retries_getter=lambda: 0,
            download_results_getter=lambda: False,
        )

    def test_video_generation_result_cost_is_persisted(self):
        service = self.make_service(
            lambda _payload: {
                "video_url": "https://cdn.example.test/result.mp4",
                "cover_url": "https://cdn.example.test/result.jpg",
                "cost": 2.5,
                "upstream_task_id": "upstream-video-1",
            }
        )
        with mock.patch("services.video.video_generation_service.log_service"):
            submitted = service.submit_task(
                OWNER,
                client_task_id="video-task-1",
                prompt="make a product video",
                model="test-video-model",
                aspect_ratio="16:9",
                duration_secs=5,
            )

            self.assertEqual(submitted["status"], "queued")
            task = wait_for_task(service, "video-task-1", "success")

        self.assertEqual(task["video_url"], "https://cdn.example.test/result.mp4")
        self.assertEqual(task["cover_url"], "https://cdn.example.test/result.jpg")
        self.assertEqual(task["cost"], 2.5)
        self.assertEqual(task["upstream_task_id"], "upstream-video-1")
        self.assertEqual(task["data"][0]["type"], "video")

    def test_duplicate_submit_reuses_existing_task(self):
        calls = 0

        def handler(_payload):
            nonlocal calls
            calls += 1
            time.sleep(0.05)
            return {"video_url": "https://cdn.example.test/result.mp4"}

        service = self.make_service(handler)
        first = service.submit_task(OWNER, client_task_id="same-task", prompt="video", model="test-video-model")
        second = service.submit_task(OWNER, client_task_id="same-task", prompt="video", model="test-video-model")
        task = wait_for_task(service, "same-task", "success")

        self.assertEqual(first["id"], "same-task")
        self.assertEqual(second["id"], "same-task")
        self.assertEqual(task["status"], "success")
        self.assertEqual(calls, 1)

    def test_configured_models_reject_image_to_video(self):
        service = self.make_service(lambda _payload: {"video_url": "https://cdn.example.test/result.mp4"})

        with self.assertRaisesRegex(ValueError, "does not support mode"):
            service.submit_task(
                OWNER,
                client_task_id="image-to-video-task",
                prompt="animate the reference",
                model="kling-v3-video",
                mode="image_to_video",
                image_urls=["https://cdn.example.test/reference.png"],
            )

    def test_seedance_image_generation_preserves_auto_duration_and_thirty_images(self):
        captured_payload = {}

        def handler(payload):
            captured_payload.update(payload)
            return {"video_url": "https://cdn.example.test/result.mp4"}

        service = self.make_service(handler)
        image_urls = [f"https://cdn.example.test/reference-{index}.png" for index in range(30)]
        with mock.patch("services.video.video_generation_service.log_service"):
            submitted = service.submit_task(
                OWNER,
                client_task_id="seedance-image-task",
                prompt="animate these product references",
                model="doubao-seedance-2-5-cankaosheng",
                mode="image_to_video",
                aspect_ratio="adaptive",
                duration_secs="auto",
                resolution="720p",
                image_urls=image_urls,
            )
            task = wait_for_task(service, "seedance-image-task", "success")

        self.assertEqual(submitted["duration_secs"], "auto")
        self.assertEqual(task["image_urls"], image_urls)
        self.assertEqual(captured_payload["params"]["duration"], "auto")
        self.assertEqual(captured_payload["image_urls"], image_urls)

    def test_hailuo_h3_max_preserves_first_last_frame_order(self):
        captured_payload = {}

        def handler(payload):
            captured_payload.update(payload)
            return {"video_url": "https://cdn.example.test/result.mp4"}

        service = self.make_service(handler)
        image_urls = [
            "https://cdn.example.test/first.png",
            "https://cdn.example.test/last.png",
        ]
        with mock.patch("services.video.video_generation_service.log_service"):
            service.submit_task(
                OWNER,
                client_task_id="hailuo-first-last-task",
                prompt="transition between both frames",
                model="hailuo-h3-max-shouweizhen",
                mode="image_to_video",
                aspect_ratio="16:9",
                duration_secs=10,
                resolution="768P",
                image_urls=image_urls,
            )
            task = wait_for_task(service, "hailuo-first-last-task", "success")

        self.assertEqual(task["image_urls"], image_urls)
        self.assertEqual(task["aspect_ratio"], "adaptive")
        self.assertEqual(captured_payload["image_urls"], image_urls)
        self.assertEqual(captured_payload["params"], {
            "duration": "10",
            "resolution": "768P",
        })

    def test_image_generation_enforces_model_specific_image_limits(self):
        service = self.make_service(lambda _payload: {"video_url": "https://cdn.example.test/result.mp4"})

        with self.assertRaisesRegex(ValueError, "exactly 1 reference image"):
            service.submit_task(
                OWNER,
                client_task_id="gk-no-image",
                prompt="animate the product",
                model="gk-video-3.5",
                mode="image_to_video",
                image_urls=[],
            )
        with self.assertRaisesRegex(ValueError, "exactly 1 reference image"):
            service.submit_task(
                OWNER,
                client_task_id="gk-two-images",
                prompt="animate the product",
                model="gk-video-3.5",
                mode="image_to_video",
                image_urls=[
                    "https://cdn.example.test/reference-1.png",
                    "https://cdn.example.test/reference-2.png",
                ],
            )
        with self.assertRaisesRegex(ValueError, "at most 9 reference images"):
            service.submit_task(
                OWNER,
                client_task_id="hailuo-ten-images",
                prompt="animate the products",
                model="hailuo-h3-cankaosheng",
                mode="image_to_video",
                image_urls=[f"https://cdn.example.test/reference-{index}.png" for index in range(10)],
            )
        with self.assertRaisesRegex(ValueError, "first-frame image"):
            service.submit_task(
                OWNER,
                client_task_id="hailuo-max-no-first-frame",
                prompt="animate the product",
                model="hailuo-h3-max-shouweizhen",
                mode="image_to_video",
                image_urls=[],
            )
        with self.assertRaisesRegex(ValueError, "first frame and a last frame"):
            service.submit_task(
                OWNER,
                client_task_id="hailuo-max-three-frames",
                prompt="animate the product",
                model="hailuo-h3-max-shouweizhen",
                mode="image_to_video",
                image_urls=[f"https://cdn.example.test/frame-{index}.png" for index in range(3)],
            )

    def test_failed_generation_keeps_upstream_task_id(self):
        def handler(payload):
            payload["submission_callback"]("upstream-failed-1")
            raise RuntimeError("upstream rejected the reference image")

        service = self.make_service(handler)
        with mock.patch("services.video.video_generation_service.log_service"):
            service.submit_task(
                OWNER,
                client_task_id="failed-upstream-task",
                prompt="animate the product",
                model="gk-video-3.5",
                mode="image_to_video",
                image_urls=["https://cdn.example.test/reference.png"],
            )
            task = wait_for_task(service, "failed-upstream-task", "error")

        self.assertEqual(task["upstream_task_id"], "upstream-failed-1")
        self.assertEqual(task["error"], "upstream rejected the reference image")

    def test_video_history_is_owner_scoped_and_admin_can_list_all_owners(self):
        service = self.make_service(lambda payload: {
            "video_url": f"https://cdn.example.test/{payload['prompt']}.mp4",
            "cost": 1.25,
        })
        with mock.patch("services.video.video_generation_service.log_service"):
            service.submit_task(
                OWNER,
                client_task_id="owner-video-task",
                prompt="owner-video",
                model="test-video-model",
            )
            service.submit_task(
                OTHER_OWNER,
                client_task_id="other-video-task",
                prompt="other-video",
                model="test-video-model",
            )

            deadline = time.time() + 2
            while time.time() < deadline:
                owner_items = service.list_tasks(OWNER, [])["items"]
                other_items = service.list_tasks(OTHER_OWNER, [])["items"]
                if owner_items and other_items and all(item["status"] == "success" for item in owner_items + other_items):
                    break
                time.sleep(0.02)

        owner_items = service.list_tasks(OWNER, [])["items"]
        other_items = service.list_tasks(OTHER_OWNER, [])["items"]
        admin_items = service.list_tasks(ADMIN, [], include_all_owners=True)["items"]

        self.assertEqual({"owner-video"}, {item["owner_id"] for item in owner_items})
        self.assertEqual({"owner-other"}, {item["owner_id"] for item in other_items})
        self.assertEqual({"owner-video", "owner-other"}, {item["owner_id"] for item in admin_items})
        self.assertEqual({"Video Owner"}, {item["owner_name"] for item in admin_items if item["owner_id"] == "owner-video"})
        self.assertEqual({"Other Owner"}, {item["owner_name"] for item in admin_items if item["owner_id"] == "owner-other"})
        self.assertTrue(all(item["cost"] == 1.25 for item in admin_items))

    def test_video_history_can_restore_one_conversation(self):
        service = self.make_service(lambda payload: {
            "video_url": f"https://cdn.example.test/{payload['prompt']}.mp4",
        })
        with mock.patch("services.video.video_generation_service.log_service"):
            service.submit_task(
                OWNER,
                client_task_id="conversation-a-1",
                prompt="conversation-a-first",
                model="test-video-model",
                conversation_id="video-conversation-a",
            )
            service.submit_task(
                OWNER,
                client_task_id="conversation-a-2",
                prompt="conversation-a-second",
                model="test-video-model",
                conversation_id="video-conversation-a",
            )
            service.submit_task(
                OWNER,
                client_task_id="conversation-b-1",
                prompt="conversation-b-first",
                model="test-video-model",
                conversation_id="video-conversation-b",
            )
            deadline = time.time() + 2
            while time.time() < deadline:
                all_items = service.list_tasks(OWNER, [])["items"]
                if len(all_items) == 3 and all(item["status"] == "success" for item in all_items):
                    break
                time.sleep(0.02)

        items = service.list_tasks(OWNER, [], conversation_id_filter="video-conversation-a")["items"]

        self.assertEqual({"conversation-a-1", "conversation-a-2"}, {item["id"] for item in items})
        self.assertEqual({"video-conversation-a"}, {item["conversation_id"] for item in items})


if __name__ == "__main__":
    unittest.main()

