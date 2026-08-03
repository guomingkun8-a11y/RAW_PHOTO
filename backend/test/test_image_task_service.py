from __future__ import annotations

import io
import json
import threading
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from unittest import mock
from pathlib import Path

from PIL import Image, ImageDraw

from services.image_task_service import ImageTaskService
from services.image_task_store import DatabaseImageTaskStore


OWNER = {"id": "owner-1", "name": "Owner", "role": "admin"}
OTHER_OWNER = {"id": "owner-2", "name": "Other", "role": "user"}


class MemoryTaskQueue:
    def __init__(self):
        self.items: list[str] = []
        self.max_concurrency = 0
        self._lock = threading.Lock()

    def enqueue(self, task_key: str) -> None:
        with self._lock:
            self.items.append(task_key)

    def dequeue(self, timeout_secs: int = 5) -> str | None:
        with self._lock:
            return self.items.pop(0) if self.items else None

    def queue_depth(self) -> int:
        with self._lock:
            return len(self.items)

    def active_slot_count(self) -> int:
        return 0

    def active_worker_count(self) -> int:
        return 0


def wait_for_task(service: ImageTaskService, identity: dict[str, object], task_id: str, status: str, timeout: float = 2.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        result = service.list_tasks(identity, [task_id])
        last = (result.get("items") or [None])[0]
        if last and last.get("status") == status:
            return last
        time.sleep(0.02)
    raise AssertionError(f"task {task_id} did not reach {status}, last={last}")


class ImageTaskServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.monitoring_patcher = mock.patch("services.image_task_service.generation_monitoring_service")
        self.library_patcher = mock.patch("services.image_task_service.image_library_service")
        self.monitoring_patcher.start()
        self.library_patcher.start()

    def tearDown(self) -> None:
        self.library_patcher.stop()
        self.monitoring_patcher.stop()

    def make_service(self, path: Path, handler=None, **kwargs) -> ImageTaskService:
        service = ImageTaskService(
            path,
            generation_handler=handler or (lambda _payload: {"data": [{"url": "http://example.test/image.png"}]}),
            edit_handler=handler or (lambda _payload: {"data": [{"url": "http://example.test/edit.png"}]}),
            relay_enabled_getter=lambda: False,
            retention_days_getter=lambda: 30,
            **kwargs,
        )
        service._dynamic_owner_concurrency_enabled = False
        return service

    def test_duplicate_submit_uses_existing_task(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            calls = 0

            def handler(_payload):
                nonlocal calls
                calls += 1
                time.sleep(0.05)
                return {"data": [{"url": "http://example.test/image.png"}]}

            service = self.make_service(Path(tmp_dir) / "image_tasks.json", handler)
            first = service.submit_generation(
                OWNER,
                client_task_id="task-1",
                prompt="cat",
                model="gpt-image-2",
                size=None,
                base_url="http://local.test",
            )
            second = service.submit_generation(
                OWNER,
                client_task_id="task-1",
                prompt="cat",
                model="gpt-image-2",
                size=None,
                base_url="http://local.test",
            )

            self.assertEqual(first["id"], "task-1")
            self.assertEqual(second["id"], "task-1")
            task = wait_for_task(service, OWNER, "task-1", "success")
            self.assertEqual(task["data"][0]["url"], "http://example.test/image.png")
            self.assertEqual(calls, 1)

    def test_different_owner_cannot_query_task(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = self.make_service(Path(tmp_dir) / "image_tasks.json")
            service.submit_generation(
                OWNER,
                client_task_id="private-task",
                prompt="cat",
                model="gpt-image-2",
                size=None,
                base_url="http://local.test",
            )

            wait_for_task(service, OWNER, "private-task", "success")
            result = service.list_tasks(OTHER_OWNER, ["private-task"])

            self.assertEqual(result["items"], [])
            self.assertEqual(result["missing_ids"], ["private-task"])

    def test_empty_task_list_is_bounded(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = self.make_service(Path(tmp_dir) / "image_tasks.json")
            base_time = datetime.now() - timedelta(seconds=250)
            tasks = {}
            for index in range(250):
                task_id = f"task-{index}"
                updated_at = base_time + timedelta(seconds=index)
                task = {
                    "id": task_id,
                    "owner_id": OWNER["id"],
                    "status": "success",
                    "mode": "generate",
                    "created_at": updated_at.strftime("%Y-%m-%d %H:%M:%S"),
                    "updated_at": updated_at.strftime("%Y-%m-%d %H:%M:%S"),
                }
                tasks[f"{OWNER['id']}:{task_id}"] = task
            with service._lock:
                service._tasks = tasks

            result = service.list_tasks(OWNER, [])

            self.assertEqual(len(result["items"]), 200)
            self.assertTrue(result["has_more"])
            self.assertEqual(result["limit"], 200)
            self.assertEqual(result["items"][0]["id"], "task-249")

    def test_queue_mode_submit_does_not_run_handler_until_worker_processes(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            calls = 0

            def handler(_payload):
                nonlocal calls
                calls += 1
                return {"data": [{"url": "http://example.test/queued.png"}]}

            queue = MemoryTaskQueue()
            service = self.make_service(
                Path(tmp_dir) / "image_tasks.json",
                handler,
                task_queue=queue,
                run_inline=False,
                max_retries_getter=lambda: 0,
            )
            task = service.submit_generation(
                OWNER,
                client_task_id="queued-task",
                prompt="cat",
                model="gpt-image-2",
                size=None,
                base_url="http://local.test",
            )

            self.assertEqual(task["status"], "queued")
            self.assertEqual(calls, 0)
            self.assertEqual(len(queue.items), 1)

            processed = service.work_once()

            self.assertEqual(processed["status"], "success")
            self.assertEqual(processed["data"][0]["url"], "http://example.test/queued.png")
            self.assertEqual(calls, 1)

    def test_queue_mode_retries_failed_task(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            calls = 0

            def handler(_payload):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise RuntimeError("temporary upstream failure")
                return {"data": [{"url": "http://example.test/retry.png"}]}

            queue = MemoryTaskQueue()
            service = self.make_service(
                Path(tmp_dir) / "image_tasks.json",
                handler,
                task_queue=queue,
                run_inline=False,
                max_retries_getter=lambda: 1,
            )
            service.submit_generation(
                OWNER,
                client_task_id="retry-task",
                prompt="cat",
                model="gpt-image-2",
                size=None,
                base_url="http://local.test",
            )

            first = service.work_once()
            self.assertEqual(first["status"], "queued")
            self.assertEqual(first["attempts"], 1)
            self.assertEqual(len(queue.items), 1)

            second = service.work_once()
            self.assertEqual(second["status"], "success")
            self.assertEqual(second["data"][0]["url"], "http://example.test/retry.png")
            self.assertEqual(calls, 2)

    def test_queue_mode_does_not_retry_content_policy_error(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            calls = 0

            def handler(_payload):
                nonlocal calls
                calls += 1
                raise RuntimeError("502: {'error': {'message': '您的提示已被内容安全策略阻止。请调整提示并再试一次。'}}")

            queue = MemoryTaskQueue()
            service = self.make_service(
                Path(tmp_dir) / "image_tasks.json",
                handler,
                task_queue=queue,
                run_inline=False,
                max_retries_getter=lambda: 3,
            )
            service.submit_generation(
                OWNER,
                client_task_id="policy-task",
                prompt="unsafe marketing prompt",
                model="gpt-image-2",
                size=None,
                base_url="http://local.test",
            )

            result = service.work_once()

            self.assertEqual(result["status"], "error")
            self.assertIn("内容安全策略", result["error"])
            self.assertEqual(result.get("attempts"), 0)
            self.assertEqual(calls, 1)
            self.assertEqual(queue.items, [])

    def test_preuploaded_edit_tracks_stage_timings_without_forwarding_internal_fields(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            received = []

            def handler(payload):
                received.append(payload)
                return {"data": [{"url": "http://example.test/preuploaded.png"}]}

            queue = MemoryTaskQueue()
            service = self.make_service(
                Path(tmp_dir) / "image_tasks.json",
                handler,
                task_queue=queue,
                run_inline=False,
                max_retries_getter=lambda: 0,
            )
            service.submit_edit(
                OWNER,
                client_task_id="preuploaded-edit",
                prompt="replace product",
                model="gpt-image-2",
                size="1024x1024",
                image_urls=["https://cdn.example.test/product.png"],
                reference_upload_ms=250,
                reference_cache_hits=1,
            )

            processed = service.work_once()

            self.assertEqual(processed["status"], "success")
            self.assertEqual(processed["stage_timings_ms"]["upload"], 250)
            self.assertIn("queue", processed["stage_timings_ms"])
            self.assertIn("generation", processed["stage_timings_ms"])
            self.assertNotIn("reference_upload_ms", received[0])
            self.assertNotIn("reference_cache_hits", received[0])

    def test_worker_thread_count_respects_queue_and_account_limits(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            queue = MemoryTaskQueue()
            queue.max_concurrency = 5
            service = self.make_service(
                Path(tmp_dir) / "image_tasks.json",
                task_queue=queue,
                run_inline=False,
            )
            self.assertEqual(service._worker_thread_count(), min(service._worker_concurrency, queue.max_concurrency))

    def test_queue_mode_enforces_owner_concurrency_and_reports_owner_activity(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            queue = MemoryTaskQueue()
            queue.max_concurrency = 4
            started = threading.Event()
            release = threading.Event()
            lock = threading.Lock()
            active = 0
            peak_active = 0

            def handler(_payload):
                nonlocal active, peak_active
                with lock:
                    active += 1
                    peak_active = max(peak_active, active)
                started.set()
                release.wait(1)
                with lock:
                    active -= 1
                return {"data": [{"url": "http://example.test/concurrency.png"}]}

            service = self.make_service(
                Path(tmp_dir) / "image_tasks.json",
                handler,
                task_queue=queue,
                run_inline=False,
                max_retries_getter=lambda: 0,
            )
            service._owner_concurrency = 1
            service._owner_pending_limit = 10

            service.submit_generation(
                OWNER,
                client_task_id="concurrency-task-1",
                prompt="cat",
                model="gpt-image-2",
                size=None,
                base_url="http://local.test",
            )
            service.submit_generation(
                OWNER,
                client_task_id="concurrency-task-2",
                prompt="dog",
                model="gpt-image-2",
                size=None,
                base_url="http://local.test",
            )

            with ThreadPoolExecutor(max_workers=2) as executor:
                first_future = executor.submit(service.work_once)
                self.assertTrue(started.wait(1))
                second_future = executor.submit(service.work_once)

                second_result = second_future.result(timeout=2)
                snapshot = service.monitoring_snapshot()
                release.set()
                first_result = first_future.result(timeout=2)

            final_result = service.work_once()

            self.assertEqual(first_result["status"], "success")
            self.assertEqual(second_result["status"], "queued")
            self.assertEqual(final_result["status"], "success")
            self.assertEqual(final_result["id"], "concurrency-task-2")
            self.assertEqual(peak_active, 1)
            self.assertEqual(snapshot["queued_tasks"], 1)
            self.assertEqual(snapshot["running_tasks"], 1)
            self.assertEqual(snapshot["owner_activity"][0]["owner_id"], OWNER["id"])
            self.assertEqual(snapshot["owner_activity"][0]["queued_tasks"], 1)
            self.assertEqual(snapshot["owner_activity"][0]["running_tasks"], 1)

    def test_dynamic_owner_concurrency_borrows_idle_slots_until_cap(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            queue = MemoryTaskQueue()
            queue.max_concurrency = 4
            both_started = threading.Event()
            release = threading.Event()
            lock = threading.Lock()
            active = 0
            peak_active = 0

            def handler(_payload):
                nonlocal active, peak_active
                with lock:
                    active += 1
                    peak_active = max(peak_active, active)
                    if active >= 2:
                        both_started.set()
                release.wait(1)
                with lock:
                    active -= 1
                return {"data": [{"url": "http://example.test/dynamic.png"}]}

            service = self.make_service(
                Path(tmp_dir) / "image_tasks.json",
                handler,
                task_queue=queue,
                run_inline=False,
                max_retries_getter=lambda: 0,
            )
            service._owner_concurrency = 1
            service._dynamic_owner_concurrency_enabled = True
            service._dynamic_owner_concurrency_threshold = 10
            service._dynamic_owner_concurrency_max = 2
            service._owner_pending_limit = 10

            for index in range(2):
                service.submit_generation(
                    OWNER,
                    client_task_id=f"dynamic-task-{index + 1}",
                    prompt="cat",
                    model="gpt-image-2",
                    size=None,
                    base_url="http://local.test",
                )

            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(service.work_once) for _ in range(2)]
                self.assertTrue(both_started.wait(1))
                release.set()
                results = [future.result(timeout=2) for future in futures]

            self.assertEqual([item["status"] for item in results], ["success", "success"])
            self.assertEqual(peak_active, 2)

    def test_dynamic_owner_concurrency_falls_back_after_active_owner_threshold(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = self.make_service(Path(tmp_dir) / "image_tasks.json")
            service._owner_concurrency = 3
            service._total_concurrency = 100
            service._dynamic_owner_concurrency_enabled = True
            service._dynamic_owner_concurrency_threshold = 10
            service._dynamic_owner_concurrency_max = 20

            self.assertEqual(service._effective_owner_concurrency(1), 20)
            self.assertEqual(service._effective_owner_concurrency(2), 20)
            self.assertEqual(service._effective_owner_concurrency(10), 10)
            self.assertEqual(service._effective_owner_concurrency(11), 3)

    def test_queue_mode_worker_can_process_task_from_shared_database(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_url = f"sqlite:///{Path(tmp_dir) / 'tasks.db'}"
            queue = MemoryTaskQueue()
            calls = 0
            api_store = DatabaseImageTaskStore(db_url)
            worker_store = DatabaseImageTaskStore(db_url)

            def handler(_payload):
                nonlocal calls
                calls += 1
                return {"data": [{"url": "http://example.test/shared-db.png"}]}

            try:
                api_service = self.make_service(
                    Path(tmp_dir) / "api-image-tasks.json",
                    task_store=api_store,
                    task_queue=queue,
                    run_inline=False,
                    max_retries_getter=lambda: 0,
                )
                worker_service = self.make_service(
                    Path(tmp_dir) / "worker-image-tasks.json",
                    handler,
                    task_store=worker_store,
                    task_queue=queue,
                    run_inline=False,
                    max_retries_getter=lambda: 0,
                )

                api_service.submit_generation(
                    OWNER,
                    client_task_id="shared-db-task",
                    prompt="cat",
                    model="gpt-image-2",
                    size=None,
                    base_url="http://local.test",
                )
                processed = worker_service.work_once()
                listed = api_service.list_tasks(OWNER, ["shared-db-task"])["items"][0]

                self.assertEqual(processed["status"], "success")
                self.assertEqual(listed["status"], "success")
                self.assertEqual(listed["data"][0]["url"], "http://example.test/shared-db.png")
                self.assertEqual(calls, 1)
            finally:
                api_store.close()
                worker_store.close()

    def test_database_store_reports_batch_progress_and_task_fields(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_url = f"sqlite:///{Path(tmp_dir) / 'batch-tasks.db'}"
            store = DatabaseImageTaskStore(db_url)
            queue = MemoryTaskQueue()
            service = self.make_service(
                Path(tmp_dir) / "image-tasks.json",
                task_store=store,
                task_queue=queue,
                run_inline=False,
            )
            try:
                for index in range(3):
                    task = service.submit_generation(
                        OWNER,
                        client_task_id=f"batch-task-{index}",
                        prompt="cat",
                        model="gpt-image-2",
                        size=None,
                        base_url="http://local.test",
                        batch_id="batch-1",
                        batch_index=index,
                        batch_total=3,
                    )
                    self.assertEqual(task["batch_id"], "batch-1")
                    self.assertEqual(task["batch_index"], index)
                    self.assertEqual(task["batch_total"], 3)

                listed = service.list_tasks(OWNER, ["batch-task-0"])["items"][0]
                self.assertEqual(listed["batch_progress"]["batch_id"], "batch-1")
                self.assertEqual(listed["batch_progress"]["total"], 3)
                self.assertEqual(listed["batch_progress"]["queued"], 3)
                self.assertEqual(listed["batch_progress"]["completed"], 0)
            finally:
                store.close()

    def test_database_store_fairly_shares_owner_slots_across_batches(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = DatabaseImageTaskStore(f"sqlite:///{Path(tmp_dir) / 'fair-tasks.db'}")

            def queued_task(task_id: str, owner_id: str, batch_id: str) -> dict[str, object]:
                return {
                    "id": task_id,
                    "owner_id": owner_id,
                    "status": "queued",
                    "mode": "generate",
                    "model": "gpt-image-2",
                    "batch_id": batch_id,
                    "created_at": "2026-07-20 17:00:00",
                    "updated_at": "2026-07-20 17:00:00",
                }

            try:
                for task_id in ("a-1", "a-2", "a-3"):
                    store.save_task(f"owner-1:{task_id}", queued_task(task_id, "owner-1", "batch-a"))
                store.save_task("owner-1:b-1", queued_task("b-1", "owner-1", "batch-b"))

                running = {"status": "running", "updated_at": "2026-07-20 17:00:01"}
                self.assertIsNotNone(store.claim_task("owner-1:a-1", owner_concurrency=3, updates=running))
                self.assertIsNotNone(store.claim_task("owner-1:a-2", owner_concurrency=3, updates=running))
                self.assertIsNone(store.claim_task("owner-1:a-3", owner_concurrency=3, updates=running))
                self.assertIsNotNone(store.claim_task("owner-1:b-1", owner_concurrency=3, updates=running))

                for task_id in ("c-1", "c-2", "c-3"):
                    store.save_task(f"owner-2:{task_id}", queued_task(task_id, "owner-2", "batch-c"))
                self.assertIsNotNone(store.claim_task("owner-2:c-1", owner_concurrency=3, updates=running))
                self.assertIsNotNone(store.claim_task("owner-2:c-2", owner_concurrency=3, updates=running))
                self.assertIsNotNone(store.claim_task("owner-2:c-3", owner_concurrency=3, updates=running))
            finally:
                store.close()

    def test_database_retry_is_atomic_and_only_queues_running_task_once(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = DatabaseImageTaskStore(f"sqlite:///{Path(tmp_dir) / 'retry-tasks.db'}")
            try:
                store.save_task(
                    "owner-1:retry-task",
                    {
                        "id": "retry-task",
                        "owner_id": "owner-1",
                        "status": "running",
                        "mode": "generate",
                        "model": "gpt-image-2",
                        "attempts": 0,
                        "max_retries": 1,
                        "created_at": "2026-07-18 10:00:00",
                        "updated_at": "2026-07-18 10:00:00",
                    },
                )
                updates = {"error": "temporary", "updated_at": "2026-07-18 10:00:01"}
                first = store.retry_task("owner-1:retry-task", max_retries=1, updates=updates)
                second = store.retry_task("owner-1:retry-task", max_retries=1, updates=updates)

                self.assertEqual(first["status"], "queued")
                self.assertEqual(first["attempts"], 1)
                self.assertIsNone(second)
            finally:
                store.close()

    def test_database_create_task_is_cross_process_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            database_url = f"sqlite:///{Path(tmp_dir) / 'idempotency.db'}"
            stores = [DatabaseImageTaskStore(database_url), DatabaseImageTaskStore(database_url)]
            task = {
                "id": "idempotent-task",
                "owner_id": "owner-1",
                "status": "queued",
                "mode": "generate",
                "model": "gpt-image-2",
                "created_at": "2026-07-18 10:00:00",
                "updated_at": "2026-07-18 10:00:00",
            }
            try:
                barrier = threading.Barrier(2)

                def create(store):
                    barrier.wait()
                    return store.create_task("owner-1:idempotent-task", task)

                with ThreadPoolExecutor(max_workers=2) as executor:
                    results = list(executor.map(create, stores))

                self.assertEqual(sum(1 for _, created in results if created), 1)
                self.assertEqual({item[0]["id"] for item in results}, {"idempotent-task"})
            finally:
                for store in stores:
                    store.close()

    def test_cancel_running_task_ignores_late_success(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            handler_started = threading.Event()
            allow_finish = threading.Event()

            def handler(_payload):
                handler_started.set()
                allow_finish.wait(1)
                return {"data": [{"url": "http://example.test/late.png"}]}

            service = self.make_service(Path(tmp_dir) / "image_tasks.json", handler)
            service.submit_generation(
                OWNER,
                client_task_id="cancel-task",
                prompt="cat",
                model="gpt-image-2",
                size=None,
                base_url="http://local.test",
            )
            self.assertTrue(handler_started.wait(1))
            task = service.cancel_task(OWNER, "cancel-task")

            self.assertEqual(task["status"], "canceled")
            self.assertEqual(task["error"], "任务已中止")

            allow_finish.set()
            time.sleep(0.08)
            result = service.list_tasks(OWNER, ["cancel-task"])
            stored = result["items"][0]

            self.assertEqual(stored["status"], "canceled")
            self.assertEqual(stored.get("data"), [])
            self.assertNotEqual(stored.get("data"), [{"url": "http://example.test/late.png"}])

    def test_other_owner_cannot_cancel_task(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = self.make_service(Path(tmp_dir) / "image_tasks.json")
            service.submit_generation(
                OWNER,
                client_task_id="owned-cancel-task",
                prompt="cat",
                model="gpt-image-2",
                size=None,
                base_url="http://local.test",
            )
            wait_for_task(service, OWNER, "owned-cancel-task", "success")

            with self.assertRaises(ValueError):
                service.cancel_task(OTHER_OWNER, "owned-cancel-task")

    def test_generation_size_is_normalized_before_running_handler(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            seen_payloads: list[dict] = []

            def handler(payload):
                seen_payloads.append(payload)
                return {"data": [{"url": "http://example.test/image.png"}]}

            service = self.make_service(Path(tmp_dir) / "image_tasks.json", handler)
            service.submit_generation(
                OWNER,
                client_task_id="normalized-size-task",
                prompt="square product main image",
                model="gpt-image-2",
                size="800x800",
                base_url="http://local.test",
            )
            task = wait_for_task(service, OWNER, "normalized-size-task", "success")

            self.assertEqual(task["size"], "816x816")
            self.assertEqual(seen_payloads[0]["size"], "816x816")

    def test_relay_enabled_uses_relay_generation_handler(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = self.make_service(
                Path(tmp_dir) / "image_tasks.json",
                handler=lambda _payload: (_ for _ in ()).throw(RuntimeError("local handler should not run")),
            )
            service.relay_enabled_getter = lambda: True
            with (
                mock.patch(
                    "services.image_task_service.openai_relay_service.image_generations",
                    return_value={"data": [{"url": "http://relay.test/image.png"}]},
                ) as relay_handler,
            ):
                service.submit_generation(
                    OWNER,
                    client_task_id="relay-task",
                    prompt="cat",
                    model="gpt-image-2",
                    size=None,
                    base_url="http://local.test",
                )

                task = wait_for_task(service, OWNER, "relay-task", "success")
                self.assertEqual(task["data"][0]["url"], "http://relay.test/image.png")
                relay_handler.assert_called_once()

    def test_resume_poll_rejected_in_relay_mode(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "image_tasks.json"
            path.write_text(
                json.dumps(
                    {
                        "tasks": [
                            {
                                "id": "timeout-task",
                                "owner_id": OWNER["id"],
                                "status": "error",
                                "mode": "generate",
                                "model": "gpt-image-2",
                                "conversation_id": "conv-1",
                                "created_at": "2026-07-14 00:00:00",
                                "updated_at": "2026-07-14 00:00:00",
                                "error": "ChatGPT 生图超时（已等待 300 秒）",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            service = self.make_service(path)
            service.relay_enabled_getter = lambda: True

            with self.assertRaises(ValueError) as ctx:
                service.resume_poll(OWNER, "timeout-task", 30)

            self.assertIn("转发模式", str(ctx.exception))

    def test_preserve_subject_adds_locked_prompt_and_mask_without_compositing(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            product = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            draw = ImageDraw.Draw(product)
            draw.rectangle((22, 18, 42, 46), fill=(220, 20, 30, 255))
            product_buf = io.BytesIO()
            product.save(product_buf, format="PNG")

            seen_payloads: list[dict] = []

            def handler(payload):
                seen_payloads.append(payload)
                self.assertNotIn("preserve_subject", payload)
                self.assertIn("Product subject preservation mode", payload["prompt"])
                self.assertEqual(len(payload["mask"]), 1)
                return {
                    "data": [
                        {
                            "url": "http://example.test/edit.png",
                            "revised_prompt": payload["prompt"],
                        }
                    ]
                }

            service = self.make_service(Path(tmp_dir) / "image_tasks.json", handler)
            service.submit_edit(
                OWNER,
                client_task_id="preserve-task",
                prompt="put it on a marble table",
                model="gpt-image-2",
                size=None,
                base_url="http://local.test",
                images=[(product_buf.getvalue(), "product.png", "image/png")],
                preserve_subject=True,
            )

            task = wait_for_task(service, OWNER, "preserve-task", "success")

            self.assertEqual(task["data"][0]["url"], "http://example.test/edit.png")
            self.assertEqual(len(seen_payloads), 1)
            mask_data = seen_payloads[0]["mask"][0][0]
            with Image.open(io.BytesIO(mask_data)) as mask:
                mask = mask.convert("RGBA")
                self.assertGreater(mask.getpixel((32, 32))[3], 200)
                self.assertLess(mask.getpixel((4, 4))[3], 20)

    def test_preserve_subject_skips_auto_mask_when_relay_cannot_use_masks(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            product = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            draw = ImageDraw.Draw(product)
            draw.rectangle((22, 18, 42, 46), fill=(220, 20, 30, 255))
            product_buf = io.BytesIO()
            product.save(product_buf, format="PNG")

            seen_payloads: list[dict] = []

            def handler(payload):
                seen_payloads.append(payload)
                self.assertIn("Product subject preservation mode", payload["prompt"])
                self.assertEqual(payload["mask"], [])
                return {"data": [{"url": "http://relay.test/edit.png"}]}

            service = self.make_service(Path(tmp_dir) / "image_tasks.json")
            service.relay_enabled_getter = lambda: True
            with (
                mock.patch("services.image_task_service.openai_relay_service.image_edits", side_effect=handler),
                mock.patch("services.image_task_service.openai_relay_service.supports_image_edit_masks", return_value=False),
            ):
                service.submit_edit(
                    OWNER,
                    client_task_id="relay-preserve-task",
                    prompt="put it on a marble table",
                    model="gpt-image-2",
                    size=None,
                    base_url="http://local.test",
                    images=[(product_buf.getvalue(), "product.png", "image/png")],
                    preserve_subject=True,
                )

                task = wait_for_task(service, OWNER, "relay-preserve-task", "success")

            self.assertEqual(task["data"][0]["url"], "http://relay.test/edit.png")
            self.assertEqual(len(seen_payloads), 1)

    def test_success_task_persists_to_new_service_instance(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "image_tasks.json"
            service = self.make_service(path)
            service.submit_generation(
                OWNER,
                client_task_id="persisted-task",
                prompt="cat",
                model="gpt-image-2",
                size=None,
                base_url="http://local.test",
            )
            wait_for_task(service, OWNER, "persisted-task", "success")

            reloaded = self.make_service(path)
            result = reloaded.list_tasks(OWNER, ["persisted-task"])

            self.assertEqual(result["missing_ids"], [])
            self.assertEqual(result["items"][0]["status"], "success")
            self.assertEqual(result["items"][0]["data"][0]["url"], "http://example.test/image.png")

    def test_startup_marks_unfinished_tasks_as_error(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "image_tasks.json"
            path.write_text(
                json.dumps(
                    {
                        "tasks": [
                            {
                                "id": "queued-task",
                                "owner_id": "owner-1",
                                "status": "queued",
                                "mode": "generate",
                                "model": "gpt-image-2",
                                "created_at": "2099-01-01 00:00:00",
                                "updated_at": "2099-01-01 00:00:00",
                            },
                            {
                                "id": "running-task",
                                "owner_id": "owner-1",
                                "status": "running",
                                "mode": "generate",
                                "model": "gpt-image-2",
                                "created_at": "2099-01-01 00:00:00",
                                "updated_at": "2099-01-01 00:00:00",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            service = self.make_service(path)
            result = service.list_tasks(OWNER, ["queued-task", "running-task"])

            self.assertEqual([item["status"] for item in result["items"]], ["error", "error"])
            self.assertTrue(all("已中断" in item.get("error", "") for item in result["items"]))


if __name__ == "__main__":
    unittest.main()
