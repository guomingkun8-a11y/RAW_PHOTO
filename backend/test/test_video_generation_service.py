from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from services.video.video_generation_service import VideoGenerationTaskService
from services.video.video_generation_task_store import DatabaseVideoGenerationTaskStore


OWNER = {"id": "owner-video", "name": "Video Owner", "role": "user"}


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


if __name__ == "__main__":
    unittest.main()

