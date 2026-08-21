from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from services.agent import AgentRun, AgentRunStatus
from services.ecommerce.agent_queue_service import AgentQueueMessage
import backend.agent_worker as agent_worker


class AgentWorkerRetryTests(unittest.TestCase):
    def test_worker_schedules_retry_before_max_attempts(self):
        message = AgentQueueMessage(
            "1-0",
            "run-1",
            "owner-1",
            attempt=1,
            conversation_id="conversation-1",
        )
        queue = SimpleNamespace(
            settings=SimpleNamespace(max_retries=3),
            retry_delay=mock.Mock(return_value=4),
            schedule_retry=mock.Mock(),
            dead_letter=mock.Mock(),
            release_pending=mock.Mock(),
            release_conversation=mock.Mock(),
        )

        with mock.patch.object(agent_worker, "agent_queue_service", queue):
            agent_worker._retry_or_dead_letter(
                message,
                state={"ownerId": "owner-1", "conversationId": "conversation-1"},
                error=RuntimeError("temporary failure"),
            )

        queue.schedule_retry.assert_called_once()
        self.assertEqual(2, queue.schedule_retry.call_args.kwargs["attempt"])
        self.assertEqual(4, queue.schedule_retry.call_args.kwargs["delay_secs"])
        queue.dead_letter.assert_not_called()
        queue.release_pending.assert_not_called()
        queue.release_conversation.assert_not_called()

    def test_worker_dead_letters_after_max_attempts_and_releases_message_conversation(self):
        message = AgentQueueMessage(
            "1-0",
            "run-1",
            "owner-1",
            attempt=3,
            conversation_id="conversation-1",
        )
        queue = SimpleNamespace(
            settings=SimpleNamespace(max_retries=3),
            retry_delay=mock.Mock(),
            schedule_retry=mock.Mock(),
            dead_letter=mock.Mock(return_value="dead-1"),
            release_pending=mock.Mock(),
            release_conversation=mock.Mock(),
        )
        failed_run = AgentRun(
            run_id="run-1",
            agent_name="test-agent",
            status=AgentRunStatus.FAILED,
            request={},
            metadata={"ownerId": "owner-1", "conversationId": "conversation-1"},
            max_steps=1,
        )

        with (
            mock.patch.object(agent_worker, "agent_queue_service", queue),
            mock.patch(
                "services.ecommerce.cow_agent_runtime_service.fail_queued_cow_agent_run",
                return_value=failed_run,
            ) as fail,
            mock.patch.object(
                agent_worker.ecommerce_agent_memory_service,
                "load_run_state",
                return_value={"ownerId": "owner-1", "conversationId": "conversation-1"},
            ),
        ):
            agent_worker._retry_or_dead_letter(
                message,
                state=None,
                error=RuntimeError("permanent failure"),
            )

        fail.assert_called_once()
        queue.schedule_retry.assert_not_called()
        queue.dead_letter.assert_called_once()
        self.assertEqual(4, queue.dead_letter.call_args.kwargs["attempt"])
        queue.release_pending.assert_called_once_with(run_id="run-1", owner_id="owner-1")
        queue.release_conversation.assert_called_once_with(
            run_id="run-1",
            owner_id="owner-1",
            conversation_id="conversation-1",
        )

    def test_worker_releases_message_conversation_when_database_failure_hides_state(self):
        message = AgentQueueMessage(
            "1-0",
            "run-1",
            "owner-1",
            attempt=3,
            conversation_id="conversation-from-message",
        )
        queue = SimpleNamespace(
            settings=SimpleNamespace(max_retries=3),
            retry_delay=mock.Mock(),
            schedule_retry=mock.Mock(),
            dead_letter=mock.Mock(return_value="dead-1"),
            release_pending=mock.Mock(),
            release_conversation=mock.Mock(),
        )

        with (
            mock.patch.object(agent_worker, "agent_queue_service", queue),
            mock.patch(
                "services.ecommerce.cow_agent_runtime_service.fail_queued_cow_agent_run",
                side_effect=RuntimeError("database unavailable"),
            ),
        ):
            agent_worker._retry_or_dead_letter(message, state=None, error="permanent failure")

        queue.dead_letter.assert_called_once()
        queue.release_pending.assert_called_once_with(run_id="run-1", owner_id="owner-1")
        queue.release_conversation.assert_called_once_with(
            run_id="run-1",
            owner_id="owner-1",
            conversation_id="conversation-from-message",
        )


if __name__ == "__main__":
    unittest.main()
