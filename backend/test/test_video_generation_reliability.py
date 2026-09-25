from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

from sqlalchemy import text

from services.video.video_generation_provider import VideoGenerationProviderError
from services.video.video_generation_service import VideoGenerationTaskService
from services.video.video_generation_storage import StoredVideo
from services.video.video_generation_task_store import DatabaseVideoGenerationTaskStore


OWNER = {"id": "reliability-owner", "name": "Reliability Owner", "role": "user"}


class FakeQueue:
    def __init__(self, *, fail_enqueue: bool = False) -> None:
        self.fail_enqueue = fail_enqueue
        self.enqueued: list[tuple[str, float]] = []
        self.notifications: list[str] = []

    def enqueue(self, key: str, priority: str = "agent", *, delay_secs: float = 0) -> None:
        if self.fail_enqueue:
            raise RuntimeError("redis unavailable")
        self.enqueued.append((key, delay_secs))

    def notify_task_update(self, key: str) -> None:
        self.notifications.append(key)


class VideoGenerationReliabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.database_url = f"sqlite:///{Path(self.temp_dir.name) / 'video-reliability.db'}"
        self.stores: list[DatabaseVideoGenerationTaskStore] = []

    def store(self) -> DatabaseVideoGenerationTaskStore:
        store = DatabaseVideoGenerationTaskStore(self.database_url)
        self.stores.append(store)
        self.addCleanup(store.close)
        return store

    @staticmethod
    def service(
        store: DatabaseVideoGenerationTaskStore,
        queue: FakeQueue,
        handler,
        *,
        pending_limit: int = 8,
        owner_concurrency: int = 1,
        storage_handler=None,
        download_results: bool = False,
    ) -> VideoGenerationTaskService:
        return VideoGenerationTaskService(
            task_store=store,
            task_queue=queue,
            run_inline=False,
            generation_handler=handler,
            enabled_getter=lambda: True,
            max_retries_getter=lambda: 1,
            owner_pending_limit_getter=lambda: pending_limit,
            owner_concurrency_getter=lambda: owner_concurrency,
            result_storage_handler=storage_handler,
            download_results_getter=lambda: download_results,
        )

    @staticmethod
    def submit(service: VideoGenerationTaskService, task_id: str) -> dict:
        return service.submit_task(
            OWNER,
            client_task_id=task_id,
            prompt="generate a product video",
            model="test-video-model",
        )

    def test_two_services_submit_same_client_id_only_once(self) -> None:
        stores = [self.store(), self.store()]
        queues = [FakeQueue(), FakeQueue()]
        services = [self.service(stores[index], queues[index], lambda _payload: {}) for index in range(2)]
        barrier = threading.Barrier(3)
        results: list[dict] = []
        errors: list[BaseException] = []

        def submit(service: VideoGenerationTaskService) -> None:
            try:
                barrier.wait()
                results.append(self.submit(service, "same-request"))
            except BaseException as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        threads = [threading.Thread(target=submit, args=(service,)) for service in services]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(5)

        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertEqual(stores[0].count_all_tasks(OWNER["id"]), 1)
        self.assertEqual(sum(len(queue.enqueued) for queue in queues), 1)

    def test_pending_limit_is_atomic_across_services(self) -> None:
        services = [self.service(self.store(), FakeQueue(), lambda _payload: {}, pending_limit=1) for _ in range(2)]
        barrier = threading.Barrier(3)
        accepted: list[str] = []
        rejected: list[str] = []

        def submit(index: int) -> None:
            barrier.wait()
            try:
                self.submit(services[index], f"request-{index}")
                accepted.append(str(index))
            except ValueError as exc:
                rejected.append(str(exc))

        threads = [threading.Thread(target=submit, args=(index,)) for index in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(5)

        self.assertEqual(len(accepted), 1)
        self.assertEqual(len(rejected), 1)
        self.assertIn("queue is full", rejected[0])

    def test_claim_and_execution_token_are_fenced_across_stores(self) -> None:
        first, second = self.store(), self.store()
        task = {
            "id": "claim-once", "owner_id": OWNER["id"], "status": "queued",
            "mode": "text_to_video", "model": "test", "prompt": "test",
            "created_at": "2026-09-13 10:00:00", "updated_at": "2026-09-13 10:00:00",
        }
        first.create_task(f"{OWNER['id']}:claim-once", task)
        barrier = threading.Barrier(3)
        claims: list[dict | None] = []

        def claim(store: DatabaseVideoGenerationTaskStore, token: str) -> None:
            barrier.wait()
            claims.append(store.claim_task(
                f"{OWNER['id']}:claim-once",
                owner_id=OWNER["id"],
                owner_concurrency=1,
                updates={"status": "running", "execution_token": token},
            ))

        threads = [
            threading.Thread(target=claim, args=(first, "token-a")),
            threading.Thread(target=claim, args=(second, "token-b")),
        ]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(5)

        winner = next(item for item in claims if item is not None)
        loser_token = "token-b" if winner["execution_token"] == "token-a" else "token-a"
        self.assertEqual(sum(item is not None for item in claims), 1)
        self.assertIsNone(second.update_task(
            f"{OWNER['id']}:claim-once",
            {"status": "success"},
            expected_status="running",
            expected_token=loser_token,
        ))
        self.assertEqual(first.get_task(f"{OWNER['id']}:claim-once")["status"], "running")

    def test_explicit_rejection_retries_but_uncertain_submission_does_not(self) -> None:
        calls: list[dict] = []

        def handler(payload: dict) -> dict:
            calls.append(dict(payload))
            payload["submission_started_callback"]()
            raise VideoGenerationProviderError(
                "rate limited",
                retryable=True,
                submission_uncertain=False,
                credential_id="credential-a",
            )

        queue = FakeQueue()
        store = self.store()
        service = self.service(store, queue, handler)
        self.submit(service, "explicit-rejection")
        queue.enqueued.clear()
        service._run_task(f"{OWNER['id']}:explicit-rejection")
        task = store.get_task(f"{OWNER['id']}:explicit-rejection")
        self.assertEqual(task["status"], "queued")
        self.assertEqual(task["submission_state"], "rejected")
        self.assertEqual(task["attempts"], 1)
        self.assertEqual(len(queue.enqueued), 1)

        uncertain_queue = FakeQueue()

        def uncertain_handler(payload: dict) -> dict:
            payload["submission_started_callback"]()
            raise VideoGenerationProviderError(
                "transport failed",
                submission_uncertain=True,
                credential_id="credential-b",
            )

        uncertain_store = self.store()
        uncertain_service = self.service(uncertain_store, uncertain_queue, uncertain_handler)
        self.submit(uncertain_service, "uncertain-submission")
        uncertain_queue.enqueued.clear()
        uncertain_service._run_task(f"{OWNER['id']}:uncertain-submission")
        uncertain = uncertain_store.get_task(f"{OWNER['id']}:uncertain-submission")
        self.assertEqual(uncertain["status"], "error")
        self.assertTrue(uncertain["reconciliation_required"])
        self.assertEqual(uncertain["credential_id"], "credential-b")
        self.assertEqual(uncertain_queue.enqueued, [])

    def test_exception_upstream_id_is_persisted_and_retry_resumes_it(self) -> None:
        seen_upstream_ids: list[str] = []

        def handler(payload: dict) -> dict:
            seen_upstream_ids.append(str(payload.get("upstream_task_id") or ""))
            if len(seen_upstream_ids) == 1:
                raise VideoGenerationProviderError(
                    "submission persistence interrupted",
                    retryable=True,
                    upstream_task_id="upstream-original",
                    credential_id="credential-original",
                    cost=1.25,
                )
            return {
                "video_url": "https://cdn.example.test/result.mp4",
                "upstream_task_id": "upstream-original",
                "cost": 1.5,
            }

        queue, store = FakeQueue(), self.store()
        service = self.service(store, queue, handler)
        self.submit(service, "resume-original")
        service._run_task(f"{OWNER['id']}:resume-original")
        retry = store.get_task(f"{OWNER['id']}:resume-original")
        self.assertEqual(retry["upstream_task_id"], "upstream-original")
        self.assertEqual(retry["credential_id"], "credential-original")
        self.assertEqual(retry["cost"], 1.25)
        store.update_task(f"{OWNER['id']}:resume-original", {"next_attempt_ts": 0})
        service._run_task(f"{OWNER['id']}:resume-original")
        completed = store.get_task(f"{OWNER['id']}:resume-original")
        self.assertEqual(seen_upstream_ids, ["", "upstream-original"])
        self.assertEqual(completed["status"], "success")
        self.assertEqual(completed["cost"], 1.5)

    def test_manual_reconciliation_only_queries_a_known_upstream_task(self) -> None:
        seen: list[str] = []

        def handler(payload: dict) -> dict:
            seen.append(str(payload.get("upstream_task_id") or ""))
            if len(seen) == 1:
                raise VideoGenerationProviderError(
                    "polling deadline exceeded",
                    upstream_task_id="upstream-to-reconcile",
                    credential_id="credential-a",
                )
            return {
                "video_url": "https://cdn.example.test/reconciled.mp4",
                "upstream_task_id": "upstream-to-reconcile",
                "cost": 2,
            }

        queue, store = FakeQueue(), self.store()
        service = self.service(store, queue, handler)
        self.submit(service, "reconcile-me")
        service._run_task(f"{OWNER['id']}:reconcile-me")
        failed = store.get_task(f"{OWNER['id']}:reconcile-me")
        self.assertEqual(failed["status"], "error")
        self.assertTrue(failed["reconciliation_required"])
        queue.enqueued.clear()

        queued = service.reconcile_task(OWNER, "reconcile-me")
        self.assertEqual(queued["status"], "queued")
        self.assertEqual(len(queue.enqueued), 1)
        service._run_task(f"{OWNER['id']}:reconcile-me")
        self.assertEqual(seen, ["", "upstream-to-reconcile"])
        self.assertEqual(store.get_task(f"{OWNER['id']}:reconcile-me")["status"], "success")

    def test_cancel_after_submission_keeps_polling_and_records_cost(self) -> None:
        submitted = threading.Event()
        release = threading.Event()

        def handler(payload: dict) -> dict:
            payload["submission_started_callback"]()
            payload["submission_callback"]("upstream-canceled", credential_id="credential-a", cost=1)
            submitted.set()
            self.assertTrue(release.wait(5))
            return {
                "video_url": "https://cdn.example.test/canceled.mp4",
                "upstream_task_id": "upstream-canceled",
                "cost": 2.5,
            }

        queue, store = FakeQueue(), self.store()
        service = self.service(store, queue, handler)
        self.submit(service, "cancel-after-submit")
        worker = threading.Thread(target=service._run_task, args=(f"{OWNER['id']}:cancel-after-submit",))
        worker.start()
        self.assertTrue(submitted.wait(5))
        canceled = service.cancel_task(OWNER, "cancel-after-submit")
        self.assertEqual(canceled["status"], "canceled")
        self.assertTrue(canceled["cancellation_pending"])
        release.set()
        worker.join(5)

        task = store.get_task(f"{OWNER['id']}:cancel-after-submit")
        self.assertEqual(task["status"], "canceled")
        self.assertFalse(task["cancellation_pending"])
        self.assertEqual(task["upstream_task_id"], "upstream-canceled")
        self.assertEqual(task["cost"], 2.5)

    def test_database_outbox_recovers_after_enqueue_failure(self) -> None:
        queue, store = FakeQueue(fail_enqueue=True), self.store()
        service = self.service(store, queue, lambda _payload: {})
        self.submit(service, "outbox-recovery")
        self.assertEqual(store.get_task(f"{OWNER['id']}:outbox-recovery")["status"], "queued")
        self.assertEqual(queue.enqueued, [])
        queue.fail_enqueue = False
        service.recover_stale_unfinished()
        self.assertEqual([key for key, _delay in queue.enqueued], [f"{OWNER['id']}:outbox-recovery"])

    def test_history_cursor_is_stable_and_rejects_invalid_values(self) -> None:
        queue, store = FakeQueue(), self.store()
        service = self.service(store, queue, lambda _payload: {})
        for index in range(5):
            task = {
                "id": f"history-{index}", "owner_id": OWNER["id"], "status": "success",
                "mode": "text_to_video", "model": "test", "prompt": f"video {index}",
                "created_at": "2026-09-13 12:00:00", "updated_at": "2026-09-13 12:00:00",
            }
            store.create_task(f"{OWNER['id']}:history-{index}", task)

        seen: list[str] = []
        cursor = ""
        while True:
            page = service.list_tasks(OWNER, [], limit=2, cursor=cursor)
            self.assertEqual(page["total"], 5)
            seen.extend(item["id"] for item in page["items"])
            cursor = str(page.get("next_cursor") or "")
            if not cursor:
                break

        self.assertEqual(seen, ["history-4", "history-3", "history-2", "history-1", "history-0"])
        self.assertEqual(len(set(seen)), 5)
        filtered = service.list_tasks(OWNER, [], limit=2, status_filter="success", query_filter="video 3")
        self.assertEqual(filtered["total"], 1)
        self.assertEqual([item["id"] for item in filtered["items"]], ["history-3"])
        with self.assertRaisesRegex(ValueError, "cursor"):
            service.list_tasks(OWNER, [], limit=2, cursor="not-a-cursor")

    def test_conversation_delete_is_blocked_before_deleting_any_reconciliation_task(self) -> None:
        queue, store = FakeQueue(), self.store()
        service = self.service(store, queue, lambda _payload: {})
        for task_id, required in (("ready", False), ("unsettled", True)):
            task = {
                "id": task_id, "owner_id": OWNER["id"], "status": "error",
                "mode": "text_to_video", "model": "test", "prompt": "video",
                "conversation_id": "reconciliation-conversation",
                "reconciliation_required": required,
                "created_at": "2026-09-13 12:00:00", "updated_at": "2026-09-13 12:00:00",
            }
            store.create_task(f"{OWNER['id']}:{task_id}", task)

        with self.assertRaisesRegex(ValueError, "billing reconciliation"):
            service.delete_conversation(OWNER, "reconciliation-conversation")
        self.assertIsNotNone(store.get_task(f"{OWNER['id']}:ready"))
        self.assertIsNotNone(store.get_task(f"{OWNER['id']}:unsettled"))

    def test_hard_delete_keeps_ledger_and_prevents_request_id_reuse(self) -> None:
        queue, store = FakeQueue(), self.store()
        service = self.service(store, queue, lambda _payload: {})
        task = {
            "id": "delete-once", "owner_id": OWNER["id"], "status": "success",
            "mode": "text_to_video", "model": "test", "prompt": "video",
            "cost": 4.5, "storage": "remote",
            "created_at": "2026-09-13 12:00:00", "updated_at": "2026-09-13 12:01:00",
        }
        store.create_task(f"{OWNER['id']}:delete-once", task)
        self.assertEqual(service.delete_task(OWNER, "delete-once"), {"ok": True, "deleted": 1})
        self.assertIsNone(store.get_task(f"{OWNER['id']}:delete-once"))

        with store.Session() as session:
            row = session.execute(
                text(
                    "SELECT cost_amount, history_deleted FROM video_generation_records WHERE task_key = :key"
                ),
                {"key": f"{OWNER['id']}:delete-once"},
            ).mappings().one()
        self.assertEqual(float(row["cost_amount"]), 4.5)
        self.assertTrue(row["history_deleted"])
        with self.assertRaisesRegex(ValueError, "deleted task"):
            self.submit(service, "delete-once")

    def test_storage_retry_reuses_result_without_regeneration(self) -> None:
        generation_calls = 0
        storage_calls = 0

        def handler(_payload: dict) -> dict:
            nonlocal generation_calls
            generation_calls += 1
            return {
                "video_url": "https://cdn.example.test/generated-once.mp4",
                "upstream_task_id": "upstream-generated-once",
                "cost": 3,
            }

        def storage_handler(_url: str, **_kwargs) -> StoredVideo:
            nonlocal storage_calls
            storage_calls += 1
            if storage_calls == 1:
                raise RuntimeError("OSS unavailable")
            return StoredVideo(
                "generated/owner/result.mp4",
                "video-storage://local/generated/owner/result.mp4",
                "local",
                1024,
            )

        queue, store = FakeQueue(), self.store()
        service = self.service(
            store,
            queue,
            handler,
            storage_handler=storage_handler,
            download_results=True,
        )
        self.submit(service, "storage-only-retry")
        service._run_task(f"{OWNER['id']}:storage-only-retry")
        retry = store.get_task(f"{OWNER['id']}:storage-only-retry")
        self.assertEqual(retry["status"], "queued")
        self.assertTrue(retry["storage_pending"])
        store.update_task(f"{OWNER['id']}:storage-only-retry", {"next_attempt_ts": 0})
        service._run_task(f"{OWNER['id']}:storage-only-retry")
        self.assertEqual(store.get_task(f"{OWNER['id']}:storage-only-retry")["status"], "success")
        self.assertEqual((generation_calls, storage_calls), (1, 2))


if __name__ == "__main__":
    unittest.main()
