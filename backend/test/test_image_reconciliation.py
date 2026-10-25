from __future__ import annotations

import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from curl_cffi import requests

from services.image.image_task_service import ImageTaskService
from services.image.image_task_store import DatabaseImageTaskStore
from services.providers import openai_relay_service
from services.providers.openai_relay_pool import RelaySubmissionUnknownHTTPException


OWNER = {"id": "owner-1", "name": "Owner", "role": "user"}


class Queue:
    def __init__(self):
        self.items: list[str] = []
        self.max_concurrency = 4

    def enqueue(self, key: str, priority: str = "standard") -> None:
        self.items.append(key)

    def dequeue(self, timeout_secs: int = 0) -> str | None:
        return self.items.pop(0) if self.items else None


def wait_until(predicate, timeout: float = 3.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not reached")


class ImageReconciliationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.monitor = mock.patch("services.image.image_task_service.generation_monitoring_service").start()
        self.library = mock.patch("services.image.image_task_service.image_library_service").start()
        self.addCleanup(mock.patch.stopall)

    def make_service(self, root: Path, queue: Queue) -> ImageTaskService:
        return ImageTaskService(
            root / "tasks.json",
            task_store=DatabaseImageTaskStore(f"sqlite:///{root / 'tasks.db'}"),
            task_queue=queue,
            run_inline=False,
            relay_enabled_getter=lambda: True,
            retention_days_getter=lambda: 30,
            max_retries_getter=lambda: 3,
        )

    @staticmethod
    def submit(service: ImageTaskService, task_id: str = "image-1") -> None:
        service.submit_generation(
            OWNER, client_task_id=task_id, prompt="product photo",
            model="gpt-image-2", size=None, base_url="http://local.test",
        )
        service.work_once(timeout_secs=0)

    @staticmethod
    def make_due(store: DatabaseImageTaskStore, key: str, *, submitted_at: float | None = None,
                 expires_at: float | None = None) -> None:
        task = store.get_task(key)
        details = dict(task["reconciliation"])
        details["next_poll_at"] = 0.0
        details["lease_until"] = 0.0
        if submitted_at is not None:
            details["submitted_at"] = submitted_at
        if expires_at is not None:
            details["expires_at"] = expires_at
        store.update_task(key, {"reconciliation": details})

    def test_late_success_reuses_submission_and_records_cost_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue = Queue()
            service = self.make_service(root, queue)
            statuses = iter([
                {"state": "pending"},
                {"state": "success", "url": "https://example.test/late.png", "cost": 1.25},
            ])
            try:
                with (
                    mock.patch.object(openai_relay_service, "image_generations", return_value={
                        "_media_pending": True, "_media_task_ids": ["upstream-1"], "data": [],
                    }) as submit,
                    mock.patch.object(openai_relay_service, "get_media_image_task_status", side_effect=lambda _id: next(statuses)) as poll,
                ):
                    self.submit(service)
                    key = "owner-1:image-1"
                    wait_until(lambda: service.task_store.get_task(key)["reconciliation"].get("next_poll_at", 0) > time.time())
                    self.assertEqual(service.task_store.get_task(key)["status"], "reconciling")
                    self.assertEqual(service.list_tasks(OWNER, ["image-1"])["items"][0]["status"], "running")
                    wait_until(lambda: not service._reconciliation_threads)
                    self.make_due(service.task_store, key, submitted_at=time.time() - 1900)
                    self.assertEqual(service.start_pending_reconciliations(), 1)
                    wait_until(lambda: service.task_store.get_task(key)["status"] == "queued")
                    self.assertEqual(service.task_store.get_task(key)["provider_result"]["cost"], 1.25)
                    service.work_once(timeout_secs=0)
                    stored = service.task_store.get_task(key)
                    self.assertEqual(stored["status"], "success")
                    self.assertEqual(stored["cost"], 1.25)
                    self.assertEqual(stored["data"][0]["url"], "https://example.test/late.png")
                    self.assertEqual(submit.call_count, 1)
                    self.assertEqual(poll.call_count, 2)
                    self.assertEqual(stored["attempts"], 0)
                    service.process_queued_task(key)
                    self.assertEqual(self.monitor.record_task_event.call_count, 1)
            finally:
                service.close()

    def test_confirmed_cancel_still_reconciles_submitted_upstream_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service = self.make_service(root, Queue())
            statuses = iter([
                {"state": "pending"},
                {"state": "success", "url": "https://example.test/canceled-late.png", "cost": 0.75},
            ])
            try:
                with (
                    mock.patch.object(openai_relay_service, "image_generations", return_value={
                        "_media_pending": True, "_media_task_ids": ["upstream-cancel-1"], "data": [],
                    }) as submit,
                    mock.patch.object(openai_relay_service, "get_media_image_task_status", side_effect=lambda _id: next(statuses)),
                ):
                    self.submit(service)
                    key = "owner-1:image-1"
                    wait_until(lambda: service.task_store.get_task(key)["reconciliation"].get("next_poll_at", 0) > time.time())
                    wait_until(lambda: not service._reconciliation_threads)

                    preview = service.cancel_task(OWNER, "image-1", dry_run=True)
                    self.assertTrue(preview["requires_confirmation"])
                    canceled = service.cancel_task(OWNER, "image-1", confirm_upstream=True)
                    self.assertTrue(canceled["cancellation_pending"])

                    self.make_due(service.task_store, key)
                    self.assertEqual(service.start_pending_reconciliations(), 1)
                    wait_until(lambda: service.task_store.get_task(key)["status"] == "queued")
                    service.work_once(timeout_secs=0)
                    stored = service.task_store.get_task(key)

                    self.assertEqual(stored["status"], "success")
                    self.assertTrue(stored["cancel_requested"])
                    self.assertEqual(stored["cost"], 0.75)
                    self.assertEqual(stored["data"][0]["url"], "https://example.test/canceled-late.png")
                    self.assertEqual(submit.call_count, 1)
            finally:
                service.close()

    def test_restart_keeps_first_image_and_finishes_second(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue = Queue()
            service = self.make_service(root, queue)
            try:
                with (
                    mock.patch.object(openai_relay_service, "image_generations", return_value={
                        "_media_pending": True, "_media_task_ids": ["a", "b"], "data": [],
                    }) as submit,
                    mock.patch.object(openai_relay_service, "get_media_image_task_status", side_effect=lambda task_id: {
                        "state": "success", "url": f"https://example.test/{task_id}.png", "cost": 0.5,
                    }) as poll,
                ):
                    self.submit(service)
                    key = "owner-1:image-1"
                    wait_until(lambda: "a" in service.task_store.get_task(key)["reconciliation"].get("results", {}))
                    wait_until(lambda: not service._reconciliation_threads)
                    service.close()
                    restarted = self.make_service(root, Queue())
                    try:
                        self.make_due(restarted.task_store, key)
                        self.assertEqual(restarted.start_pending_reconciliations(), 1)
                        wait_until(lambda: restarted.task_store.get_task(key)["status"] == "queued")
                        restarted.work_once(timeout_secs=0)
                        stored = restarted.task_store.get_task(key)
                        self.assertEqual(stored["status"], "success")
                        self.assertEqual([item["url"] for item in stored["data"]], [
                            "https://example.test/a.png", "https://example.test/b.png",
                        ])
                        self.assertEqual(stored["cost"], 1.0)
                        self.assertEqual(submit.call_count, 1)
                        self.assertEqual([call.args[0] for call in poll.call_args_list], ["a", "b"])
                    finally:
                        restarted.close()
            finally:
                service.close()

    def test_expiry_is_unknown_and_does_not_resubmit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service = self.make_service(root, Queue())
            try:
                with (
                    mock.patch.object(openai_relay_service, "image_generations", return_value={
                        "_media_pending": True, "_media_task_ids": ["upstream-1"], "data": [],
                    }) as submit,
                    mock.patch.object(openai_relay_service, "get_media_image_task_status", return_value={"state": "pending"}) as poll,
                ):
                    self.submit(service)
                    key = "owner-1:image-1"
                    wait_until(lambda: service.task_store.get_task(key)["reconciliation"].get("next_poll_at", 0) > time.time())
                    wait_until(lambda: not service._reconciliation_threads)
                    self.make_due(service.task_store, key, expires_at=time.time() - 1)
                    self.assertEqual(service.start_pending_reconciliations(), 1)
                    wait_until(lambda: service.task_store.get_task(key)["status"] == "error")
                    stored = service.task_store.get_task(key)
                    self.assertTrue(stored["reconciliation_required"])
                    self.assertIn("仍未确认", stored["error"])
                    self.assertEqual(submit.call_count, 1)
                    self.assertEqual(poll.call_count, 1)
            finally:
                service.close()

    def test_status_timeout_retries_at_lower_frequency_after_thirty_minutes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service = self.make_service(root, Queue())
            responses = iter([TimeoutError("TLS read timed out"), {"state": "pending"}, {
                "state": "success", "url": "https://example.test/recovered.png", "cost": 0.75,
            }])

            def poll(_task_id):
                value = next(responses)
                if isinstance(value, Exception):
                    raise value
                return value

            try:
                with (
                    mock.patch.object(openai_relay_service, "image_generations", return_value={
                        "_media_pending": True, "_media_task_ids": ["upstream-1"], "data": [],
                    }) as submit,
                    mock.patch.object(openai_relay_service, "get_media_image_task_status", side_effect=poll),
                ):
                    self.submit(service)
                    key = "owner-1:image-1"
                    wait_until(lambda: "TLS read timed out" in service.task_store.get_task(key)["reconciliation"].get("last_error", ""))
                    wait_until(lambda: not service._reconciliation_threads)
                    self.make_due(service.task_store, key, submitted_at=time.time() - 1900)
                    service.start_pending_reconciliations()
                    wait_until(lambda: service.task_store.get_task(key)["reconciliation"].get("next_poll_at", 0) > time.time() + 100)
                    wait_until(lambda: not service._reconciliation_threads)
                    self.make_due(service.task_store, key)
                    service.start_pending_reconciliations()
                    wait_until(lambda: service.task_store.get_task(key)["status"] == "queued")
                    service.work_once(timeout_secs=0)
                    self.assertEqual(service.task_store.get_task(key)["status"], "success")
                    self.assertEqual(submit.call_count, 1)
            finally:
                service.close()

    def test_json_inline_restart_resumes_reconciliation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks.json"

            def make_inline():
                return ImageTaskService(
                    path, relay_enabled_getter=lambda: True,
                    retention_days_getter=lambda: 30,
                )

            with (
                mock.patch.object(openai_relay_service, "image_generations", return_value={
                    "_media_pending": True, "_media_task_ids": ["upstream-1"], "data": [],
                }) as submit,
                mock.patch.object(openai_relay_service, "get_media_image_task_status", side_effect=[
                    {"state": "pending"},
                    {"state": "success", "url": "https://example.test/inline.png", "cost": 0.5},
                ]),
            ):
                service = make_inline()
                try:
                    service.submit_generation(
                        OWNER, client_task_id="image-1", prompt="product photo",
                        model="gpt-image-2", size=None,
                    )
                    key = "owner-1:image-1"
                    def current_task():
                        with service._lock:
                            return dict(service._get_task_locked(key) or {})

                    wait_until(lambda: current_task().get("reconciliation", {}).get("next_poll_at", 0) > time.time())
                    wait_until(lambda: not service._reconciliation_threads)
                finally:
                    service.close()
                task = service.task_store.get_task(key)
                details = dict(task["reconciliation"])
                details.update({"next_poll_at": 0.0, "lease_until": 0.0})
                service.task_store.update_task(key, {"reconciliation": details})
                restarted = make_inline()
                try:
                    wait_until(lambda: restarted.list_tasks(OWNER, ["image-1"])["items"][0]["status"] == "success")
                    self.assertEqual(restarted.list_tasks(OWNER, ["image-1"])["items"][0]["cost"], 0.5)
                    self.assertEqual(submit.call_count, 1)
                finally:
                    restarted.close()

    def test_two_workers_claim_only_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = self.make_service(root, Queue())
            second = self.make_service(root, Queue())
            entered = threading.Event()
            release = threading.Event()
            calls = 0

            def poll(_task_id):
                nonlocal calls
                calls += 1
                if calls > 1:
                    entered.set()
                    release.wait(3)
                return {"state": "pending"}

            try:
                with (
                    mock.patch.object(openai_relay_service, "image_generations", return_value={
                        "_media_pending": True, "_media_task_ids": ["upstream-1"], "data": [],
                    }),
                    mock.patch.object(openai_relay_service, "get_media_image_task_status", side_effect=poll),
                ):
                    self.submit(first)
                    key = "owner-1:image-1"
                    wait_until(lambda: first.task_store.get_task(key)["reconciliation"].get("next_poll_at", 0) > time.time())
                    wait_until(lambda: not first._reconciliation_threads)
                    self.make_due(first.task_store, key)
                    with ThreadPoolExecutor(max_workers=2) as executor:
                        jobs = [executor.submit(service.start_pending_reconciliations) for service in (first, second)]
                        self.assertEqual(sum(job.result(timeout=3) for job in jobs), 1)
                    self.assertTrue(entered.wait(3))
                    self.assertEqual(calls, 2)
                    release.set()
                    wait_until(lambda: not first._reconciliation_threads and not second._reconciliation_threads)
            finally:
                release.set()
                first.close()
                second.close()

    def test_submission_transport_error_is_not_retried_without_id(self):
        relay = {"enabled": False, "base_url": "https://relay.example", "api_key": "test"}
        with (
            mock.patch.object(openai_relay_service, "settings", return_value=relay),
            mock.patch.object(openai_relay_service.requests, "post", side_effect=requests.RequestsError("timeout")) as post,
        ):
            with self.assertRaises(RelaySubmissionUnknownHTTPException) as raised:
                openai_relay_service.image_generations({"model": "gpt-image-2", "prompt": "product photo"})
        self.assertEqual(post.call_count, 1)
        self.assertEqual(raised.exception.upstream_task_ids, [])
        self.assertEqual(raised.exception.reconciliation["requested_count"], 1)

    def test_final_status_without_url_remains_unconfirmed(self):
        with mock.patch.object(openai_relay_service, "run_with_relay_pool", return_value={
            "data": {"is_final": True, "state": "success"},
        }):
            status = openai_relay_service.get_media_image_task_status("upstream-1")
        self.assertEqual(status["state"], "pending")

    def test_id_checkpoint_failure_does_not_resubmit(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = self.make_service(Path(tmp), Queue())
            try:
                with (
                    mock.patch.object(openai_relay_service, "image_generations", return_value={
                        "_media_pending": True, "_media_task_ids": ["upstream-1"], "data": [],
                    }) as submit,
                    mock.patch.object(service, "_begin_upstream_reconciliation", side_effect=TimeoutError("database lock timeout")),
                ):
                    self.submit(service)
                    stored = service.task_store.get_task("owner-1:image-1")
                    self.assertEqual(stored["status"], "error")
                    self.assertEqual(stored["upstream_task_ids"], ["upstream-1"])
                    self.assertTrue(stored["reconciliation_required"])
                    self.assertEqual(stored["attempts"], 0)
                    self.assertEqual(submit.call_count, 1)
            finally:
                service.close()


if __name__ == "__main__":
    unittest.main()
