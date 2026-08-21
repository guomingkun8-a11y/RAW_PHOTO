from __future__ import annotations

import json
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from services.ecommerce.agent_queue_service import AgentQueueMessage, RedisAgentQueue


def _settings(**overrides):
    values = {
        "enabled": True,
        "redis_url": "redis://example.test/0",
        "queue_name": "test_agent_jobs",
        "worker_concurrency": 1,
        "total_concurrency": 2,
        "owner_concurrency": 1,
        "owner_pending_limit": 10,
        "slot_lease_secs": 60,
        "job_retention_secs": 3600,
        "max_retries": 3,
        "retry_base_delay_secs": 2,
        "retry_max_delay_secs": 10,
        "dead_letter_retention_secs": 604800,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class _TestQueue(RedisAgentQueue):
    def __init__(self, settings):
        super().__init__()
        self._test_settings = settings

    @property
    def settings(self):
        return self._test_settings


class _FakePipeline:
    def __init__(self, client):
        self.client = client
        self.results = []

    def zadd(self, key, mapping):
        self.results.append(self.client.zadd(key, mapping))
        return self

    def expire(self, key, ttl):
        self.results.append(self.client.expire(key, ttl))
        return self

    def xack(self, key, group, message_id):
        self.results.append(self.client.xack(key, group, message_id))
        return self

    def xdel(self, key, message_id):
        self.results.append(self.client.xdel(key, message_id))
        return self

    def xadd(self, key, fields, **kwargs):
        self.results.append(self.client.xadd(key, fields, **kwargs))
        return self

    def execute(self):
        return list(self.results)


class _FakeRedis:
    def __init__(self):
        self.zsets = {}
        self.streams = {}
        self.expirations = []
        self.acks = []
        self.deleted = []
        self.groups = []
        self._sequence = 0

    def pipeline(self, transaction=True):
        return _FakePipeline(self)

    def zadd(self, key, mapping):
        self.zsets.setdefault(key, {}).update(mapping)
        return len(mapping)

    def zrem(self, key, member):
        existed = member in self.zsets.setdefault(key, {})
        self.zsets[key].pop(member, None)
        return 1 if existed else 0

    def zcard(self, key):
        return len(self.zsets.get(key, {}))

    def expire(self, key, ttl):
        self.expirations.append((key, ttl))
        return True

    def xadd(self, key, fields, id="*", **_kwargs):
        self._sequence += 1
        message_id = f"{self._sequence}-0" if id == "*" else id
        self.streams.setdefault(key, []).append((message_id, dict(fields)))
        return message_id

    def xack(self, key, group, message_id):
        self.acks.append((key, group, message_id))
        return 1

    def xdel(self, key, message_id):
        self.deleted.append((key, message_id))
        self.streams[key] = [
            (entry_id, fields)
            for entry_id, fields in self.streams.get(key, [])
            if entry_id != message_id
        ]
        return 1

    def xgroup_create(self, key, group, id="0", mkstream=True):
        self.groups.append((key, group, id, mkstream))
        self.streams.setdefault(key, [])
        return True


def _queue_with_fake_redis(**settings_overrides):
    queue = _TestQueue(_settings(**settings_overrides))
    fake = _FakeRedis()
    queue._client_instance = fake
    return queue, fake


class AgentQueueServiceTests(unittest.TestCase):
    def test_message_parses_attempt_and_conversation_id(self):
        message = RedisAgentQueue._message_from_entry(
            "4-0",
            {
                "run_id": "run-1",
                "owner_id": "owner-1",
                "attempt": "2",
                "conversation_id": "conversation-1",
            },
        )

        self.assertEqual("run-1", message.run_id)
        self.assertEqual("owner-1", message.owner_id)
        self.assertEqual(2, message.attempt)
        self.assertEqual("conversation-1", message.conversation_id)

    def test_retry_delay_uses_exponential_backoff_with_cap(self):
        queue, _fake = _queue_with_fake_redis(retry_base_delay_secs=2, retry_max_delay_secs=10)

        self.assertEqual(2, queue.retry_delay(1))
        self.assertEqual(4, queue.retry_delay(2))
        self.assertEqual(8, queue.retry_delay(3))
        self.assertEqual(10, queue.retry_delay(4))

    def test_enqueue_writes_attempt_and_conversation_id(self):
        queue, fake = _queue_with_fake_redis()

        message_id = queue.enqueue(
            run_id="run-1",
            owner_id="owner-1",
            attempt=2,
            conversation_id="conversation-1",
        )

        self.assertEqual("1-0", message_id)
        _entry_id, fields = fake.streams[queue.settings.queue_name][0]
        self.assertEqual("run-1", fields["run_id"])
        self.assertEqual("owner-1", fields["owner_id"])
        self.assertEqual(2, fields["attempt"])
        self.assertEqual("conversation-1", fields["conversation_id"])

    def test_schedule_retry_moves_message_to_retry_set(self):
        queue, fake = _queue_with_fake_redis()
        message = AgentQueueMessage(
            "1-0",
            "run-1",
            "owner-1",
            attempt=1,
            conversation_id="conversation-1",
        )

        queue.schedule_retry(message, attempt=2, reason="temporary failure", delay_secs=5)

        retry_items = fake.zsets[queue._retry_key()]
        self.assertEqual(1, len(retry_items))
        payload = json.loads(next(iter(retry_items)))
        self.assertEqual("run-1", payload["run_id"])
        self.assertEqual("owner-1", payload["owner_id"])
        self.assertEqual(2, payload["attempt"])
        self.assertEqual("conversation-1", payload["conversation_id"])
        self.assertEqual("temporary failure", payload["reason"])
        self.assertIn((queue.settings.queue_name, queue.GROUP_NAME, "1-0"), fake.acks)
        self.assertIn((queue.settings.queue_name, "1-0"), fake.deleted)

    def test_defer_preserves_current_attempt(self):
        queue, fake = _queue_with_fake_redis()
        message = AgentQueueMessage(
            "1-0",
            "run-1",
            "owner-1",
            attempt=3,
            conversation_id="conversation-1",
        )

        queue.defer(message)

        payload = json.loads(next(iter(fake.zsets[queue._retry_key()])))
        self.assertEqual(3, payload["attempt"])

    def test_promote_due_retries_requeues_attempt_and_conversation_id(self):
        queue, fake = _queue_with_fake_redis()
        retry_payload = json.dumps(
            {
                "run_id": "run-1",
                "owner_id": "owner-1",
                "attempt": 2,
                "conversation_id": "conversation-1",
                "reason": "temporary failure",
            },
            separators=(",", ":"),
        )
        fake.zsets[queue._retry_key()] = {retry_payload: time.time() - 1}

        def fake_script(name, _source):
            self.assertEqual("promote_due_retries", name)

            def promote(keys, args):
                retry_key, queue_key = keys
                now = float(args[0])
                limit = int(args[1])
                due = [
                    encoded
                    for encoded, score in list(fake.zsets.get(retry_key, {}).items())
                    if score <= now
                ][:limit]
                for encoded in due:
                    fake.zrem(retry_key, encoded)
                    payload = json.loads(encoded)
                    fake.xadd(
                        queue_key,
                        {
                            "run_id": payload["run_id"],
                            "owner_id": payload["owner_id"],
                            "attempt": str(payload.get("attempt") or 0),
                            "conversation_id": payload.get("conversation_id") or "",
                            "last_error": payload.get("reason") or "",
                        },
                    )
                return len(due)

            return promote

        with mock.patch.object(queue, "_script", side_effect=fake_script):
            promoted = queue.promote_due_retries(limit=10)

        self.assertEqual(1, promoted)
        self.assertEqual({}, fake.zsets[queue._retry_key()])
        _entry_id, fields = fake.streams[queue.settings.queue_name][0]
        self.assertEqual("run-1", fields["run_id"])
        self.assertEqual("owner-1", fields["owner_id"])
        self.assertEqual("2", fields["attempt"])
        self.assertEqual("conversation-1", fields["conversation_id"])
        self.assertEqual("temporary failure", fields["last_error"])

    def test_dead_letter_acks_message_and_records_failure(self):
        queue, fake = _queue_with_fake_redis()
        message = AgentQueueMessage(
            "1-0",
            "run-1",
            "owner-1",
            attempt=3,
            conversation_id="conversation-1",
        )

        dead_id = queue.dead_letter(message, attempt=4, error="permanent failure")

        self.assertEqual("1-0", dead_id)
        _entry_id, fields = fake.streams[queue._dead_letter_key()][0]
        self.assertEqual("run-1", fields["run_id"])
        self.assertEqual("owner-1", fields["owner_id"])
        self.assertEqual(4, fields["attempt"])
        self.assertEqual("conversation-1", fields["conversation_id"])
        self.assertEqual("permanent failure", fields["error"])
        self.assertIn((queue.settings.queue_name, queue.GROUP_NAME, "1-0"), fake.acks)
        self.assertIn((queue.settings.queue_name, "1-0"), fake.deleted)


if __name__ == "__main__":
    unittest.main()
