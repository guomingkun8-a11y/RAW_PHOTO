from __future__ import annotations

import os
import time
import unittest
from uuid import uuid4

from services.video.video_generation_queue import RedisVideoGenerationQueue, VideoDelivery


class RedisVideoGenerationQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        redis_url = os.getenv("VIDEO_GENERATION_QUEUE_TEST_REDIS_URL", "").strip()
        if not redis_url:
            self.skipTest("VIDEO_GENERATION_QUEUE_TEST_REDIS_URL is not configured")
        self.queue_name = f"test:video-generation:{uuid4().hex}"
        self.queue = RedisVideoGenerationQueue(
            redis_url=redis_url,
            queue_name=self.queue_name,
            max_concurrency=1,
            slot_lease_secs=60,
            adaptive_concurrency_enabled=False,
        )
        try:
            self.queue._client.ping()
        except Exception as exc:
            self.skipTest(f"test Redis is unavailable: {type(exc).__name__}")
        self.addCleanup(self._cleanup_keys)

    def _cleanup_keys(self) -> None:
        keys = list(self.queue._client.scan_iter(match=f"{self.queue_name}*", count=100))
        if keys:
            self.queue._client.delete(*keys)

    def test_duplicate_enqueue_reserves_once_and_ack_is_token_fenced(self) -> None:
        self.queue.enqueue("owner:task")
        self.queue.enqueue("owner:task")
        self.assertEqual(self.queue.queue_depths(), {"ready": 1, "delayed": 0})

        delivery = self.queue.reserve(timeout_secs=0)
        self.assertIsNotNone(delivery)
        self.assertEqual(delivery.key, "owner:task")
        self.assertEqual(self.queue.processing_count(), 1)
        self.assertFalse(self.queue.ack(VideoDelivery(delivery.key, "stale-token")))
        self.assertEqual(self.queue.processing_count(), 1)
        self.assertTrue(self.queue.ack(delivery))
        self.assertEqual(self.queue.processing_count(), 0)
        self.assertIsNone(self.queue.reserve(timeout_secs=0))

    def test_delayed_retry_is_not_visible_until_due(self) -> None:
        self.queue.enqueue("owner:retry")
        delivery = self.queue.reserve(timeout_secs=0)
        self.assertTrue(self.queue.retry(delivery, delay_secs=0.25))
        self.assertEqual(self.queue.queue_depths(), {"ready": 0, "delayed": 1})
        self.assertIsNone(self.queue.reserve(timeout_secs=0))
        time.sleep(0.3)
        retried = self.queue.reserve(timeout_secs=0)
        self.assertIsNotNone(retried)
        self.assertEqual(retried.key, "owner:retry")
        self.assertNotEqual(retried.token, delivery.token)
        self.assertTrue(self.queue.ack(retried))

    def test_expired_worker_delivery_is_recovered(self) -> None:
        self.queue.enqueue("owner:crashed-worker")
        abandoned = self.queue.reserve(timeout_secs=0, lease_secs=1)
        self.assertIsNotNone(abandoned)
        time.sleep(1.1)
        self.assertEqual(self.queue.recover_deliveries(), 1)
        recovered = self.queue.reserve(timeout_secs=0)
        self.assertIsNotNone(recovered)
        self.assertEqual(recovered.key, abandoned.key)
        self.assertNotEqual(recovered.token, abandoned.token)
        self.assertFalse(self.queue.ack(abandoned))
        self.assertTrue(self.queue.ack(recovered))

    def test_delivery_heartbeat_extends_lease(self) -> None:
        self.queue.enqueue("owner:heartbeat")
        delivery = self.queue.reserve(timeout_secs=0, lease_secs=1)
        time.sleep(0.6)
        self.assertTrue(self.queue.renew_delivery(delivery, lease_secs=1))
        time.sleep(0.6)
        self.assertEqual(self.queue.recover_deliveries(), 0)
        self.assertTrue(self.queue.ack(delivery))

    def test_global_slot_is_renewable_and_exclusive(self) -> None:
        self.assertTrue(self.queue.acquire_slot("slot-a", timeout_secs=0.1))
        before = float(self.queue._client.zscore(self.queue._slot_key, "slot-a"))
        self.assertFalse(self.queue.acquire_slot("slot-b", timeout_secs=0.1))
        time.sleep(1.05)
        self.assertTrue(self.queue.renew_slot("slot-a"))
        after = float(self.queue._client.zscore(self.queue._slot_key, "slot-a"))
        self.assertGreater(after, before)
        self.queue.release_slot("slot-a")
        self.assertTrue(self.queue.acquire_slot("slot-b", timeout_secs=0.1))
        self.queue.release_slot("slot-b")


if __name__ == "__main__":
    unittest.main()
