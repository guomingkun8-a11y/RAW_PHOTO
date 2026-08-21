from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from services.ecommerce.ecommerce_agent_memory_service import (
    EcommerceAgentMemoryService,
    ProfessionalAgentConversationBusy,
)


class EcommerceAgentMemoryServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        database_url = f"sqlite:///{Path(self.temp_dir.name) / 'agent-memory.db'}"
        self.memory = EcommerceAgentMemoryService(database_url)
        self.addCleanup(self.memory.close)

    def _append(self, owner: str, conversation: str, key: str, role: str, message_type: str, content, *, turn: str):
        return self.memory.append_message(
            owner_id=owner,
            conversation_id=conversation,
            message_key=key,
            role=role,
            message_type=message_type,
            content=content,
            run_id=f"run-{turn}",
            turn_id=turn,
        )

    def test_owner_and_conversation_are_strictly_isolated(self):
        self._append("owner-a", "same-id", "a", "user", "user_message", {"message": "A"}, turn="turn-a")
        self._append("owner-b", "same-id", "b", "user", "user_message", {"message": "B"}, turn="turn-b")
        self._append("owner-a", "other-id", "c", "user", "user_message", {"message": "C"}, turn="turn-c")

        a = self.memory.load_messages(owner_id="owner-a", conversation_id="same-id")
        b = self.memory.load_messages(owner_id="owner-b", conversation_id="same-id")
        other = self.memory.load_messages(owner_id="owner-a", conversation_id="other-id")

        self.assertEqual(["A"], [item["content"]["message"] for item in a])
        self.assertEqual(["B"], [item["content"]["message"] for item in b])
        self.assertEqual(["C"], [item["content"]["message"] for item in other])

    def test_new_service_instance_restores_messages_and_snapshots(self):
        self._append("owner", "conversation", "user-1", "user", "user_message", {"message": "背景换夜景"}, turn="turn-1")
        self.memory.upsert_snapshot(
            owner_id="owner",
            conversation_id="conversation",
            state_key="creative_brief",
            payload={"background": "夜间城市道路", "lighting": "冷暖交错"},
        )

        restored = EcommerceAgentMemoryService(self.memory.database_url)
        self.addCleanup(restored.close)
        context = restored.load_model_context(owner_id="owner", conversation_id="conversation")

        self.assertEqual("背景换夜景", context["messages"][0]["content"]["message"])
        self.assertEqual("夜间城市道路", context["memory"]["creative_brief"]["background"])

    def test_message_key_is_idempotent_and_image_bodies_are_not_saved(self):
        self._append(
            "owner",
            "conversation",
            "same-key",
            "tool",
            "tool_result",
            {"url": "https://example.test/image.png", "b64_json": "large-secret-image-body"},
            turn="turn-1",
        )
        self._append(
            "owner",
            "conversation",
            "same-key",
            "tool",
            "tool_result",
            {"url": "https://example.test/duplicate.png"},
            turn="turn-1",
        )

        messages = self.memory.load_messages(owner_id="owner", conversation_id="conversation")
        self.assertEqual(1, len(messages))
        self.assertEqual("https://example.test/image.png", messages[0]["content"]["url"])
        self.assertNotIn("b64_json", messages[0]["content"])

    def test_context_trimming_keeps_tool_chain_with_its_user_turn(self):
        self._append("owner", "conversation", "u1", "user", "user_message", {"message": "first"}, turn="turn-1")
        self._append("owner", "conversation", "c1", "assistant", "tool_call", {"arguments": {}}, turn="turn-1")
        self._append("owner", "conversation", "r1", "tool", "tool_result", {"ok": True}, turn="turn-1")
        self._append("owner", "conversation", "u2", "user", "user_message", {"message": "second"}, turn="turn-2")
        self._append("owner", "conversation", "c2", "assistant", "tool_call", {"arguments": {}}, turn="turn-2")
        self._append("owner", "conversation", "r2", "tool", "tool_result", {"ok": True}, turn="turn-2")

        messages = self.memory.load_messages(owner_id="owner", conversation_id="conversation", max_turns=1)

        self.assertEqual(["u2", "c2", "r2"], [item["messageKey"] for item in messages])

    def test_delete_is_scoped_to_one_owner_and_conversation(self):
        self._append("owner-a", "conversation", "a", "user", "user_message", {"message": "A"}, turn="turn-a")
        self._append("owner-b", "conversation", "b", "user", "user_message", {"message": "B"}, turn="turn-b")

        self.memory.delete_conversation(owner_id="owner-a", conversation_id="conversation")

        self.assertEqual([], self.memory.load_messages(owner_id="owner-a", conversation_id="conversation"))
        self.assertEqual(1, len(self.memory.load_messages(owner_id="owner-b", conversation_id="conversation")))

    def test_relevant_message_search_is_scoped_and_restored_from_database(self):
        self._append(
            "owner-a",
            "conversation",
            "a",
            "user",
            "user_message",
            {"message": "这个保温杯以后都用夜间通勤背景"},
            turn="turn-a",
        )
        self._append(
            "owner-b",
            "conversation",
            "b",
            "user",
            "user_message",
            {"message": "护肤品使用白色实验室背景"},
            turn="turn-b",
        )

        with (
            mock.patch(
                "services.ecommerce.ecommerce_agent_memory_service.professional_query_embedding",
                return_value=[],
            ),
            mock.patch(
                "services.ecommerce.ecommerce_agent_memory_service.embed_professional_texts",
                return_value=[],
            ),
        ):
            restored = EcommerceAgentMemoryService(self.memory.database_url)
            self.addCleanup(restored.close)
            results = restored.search_relevant_messages(
                owner_id="owner-a",
                conversation_id="conversation",
                query="保温杯通勤",
            )

        self.assertTrue(results)
        self.assertTrue(any("夜间通勤" in item["text"] for item in results))
        self.assertFalse(any("护肤品" in item["text"] for item in results))

    def test_delete_conversation_removes_memory_chunks(self):
        self._append("owner", "conversation", "a", "user", "user_message", {"message": "汽车夜景"}, turn="turn")

        self.memory.delete_conversation(owner_id="owner", conversation_id="conversation")

        with (
            mock.patch(
                "services.ecommerce.ecommerce_agent_memory_service.professional_query_embedding",
                return_value=[],
            ),
            mock.patch(
                "services.ecommerce.ecommerce_agent_memory_service.embed_professional_texts",
                return_value=[],
            ),
        ):
            results = self.memory.search_relevant_messages(
                owner_id="owner",
                conversation_id="conversation",
                query="汽车",
            )
        self.assertEqual([], results)

    def test_run_state_events_cancellation_and_conversation_exclusion_are_persistent(self):
        first = {
            "runId": "run-1",
            "ownerId": "owner",
            "conversationId": "conversation",
            "turnId": "turn-1",
            "agentName": "cowagent-professional",
            "status": "pending",
            "request": {"prompt": "汽车主图"},
            "metadata": {"workflow": "cowagent_professional"},
            "result": {},
            "maxSteps": 8,
        }
        second = {**first, "runId": "run-2", "turnId": "turn-2"}
        self.memory.create_run_state(first)

        with self.assertRaises(ProfessionalAgentConversationBusy):
            self.memory.create_run_state(second)

        first_event = self.memory.append_run_event(
            run_id="run-1",
            event_type="run.queued",
            timestamp="2026-08-15T00:00:00+00:00",
            payload={"position": 1},
        )
        second_event = self.memory.append_run_event(
            run_id="run-1",
            event_type="run.started",
            timestamp="2026-08-15T00:00:01+00:00",
            payload={},
        )
        self.assertEqual([1, 2], [first_event["sequence"], second_event["sequence"]])

        canceled = self.memory.request_run_cancel("run-1", owner_id="owner")
        self.assertTrue(canceled["cancelRequested"])
        self.memory.save_run_state({**first, "status": "running", "cancelRequested": False})

        restored = EcommerceAgentMemoryService(self.memory.database_url)
        self.addCleanup(restored.close)
        state = restored.load_run_state("run-1", owner_id="owner")
        events = restored.load_run_events("run-1")
        self.assertEqual("canceled", state["status"])
        self.assertTrue(state["cancelRequested"])
        self.assertEqual(["run.queued", "run.started"], [event["type"] for event in events])

        restored.save_run_state({**first, "status": "completed"})
        restored.create_run_state(second)
        self.assertEqual("pending", restored.load_run_state("run-2", owner_id="owner")["status"])

        completed = {
            **first,
            "runId": "run-completed",
            "conversationId": "completed-conversation",
            "status": "completed",
        }
        restored.create_run_state(completed)
        unchanged = restored.request_run_cancel("run-completed", owner_id="owner")
        self.assertEqual("completed", unchanged["status"])
        self.assertFalse(unchanged["cancelRequested"])

    def test_long_term_vector_backfill_indexes_existing_memories(self):
        memory = self.memory.upsert_long_term_memory(
            owner_id="owner",
            content="Always use a clean studio background for this brand.",
            category="brand_preference",
            confirmed=True,
        )

        with (
            mock.patch(
                "services.ecommerce.ecommerce_agent_memory_service.professional_embedding_model",
                return_value="embedding-test",
            ),
            mock.patch(
                "services.ecommerce.ecommerce_agent_memory_service.embed_professional_texts",
                return_value=[[1.0, 0.0]],
            ),
            mock.patch(
                "services.ecommerce.ecommerce_agent_memory_service.professional_memory_index.enabled",
                return_value=True,
            ),
            mock.patch(
                "services.ecommerce.ecommerce_agent_memory_service.professional_memory_index.upsert",
                return_value=True,
            ) as upsert,
        ):
            result = self.memory.backfill_long_term_vectors(owner_id="owner")

        self.assertEqual(1, result["embedded"])
        self.assertEqual(1, result["indexed"])
        self.assertEqual(0, result["pending"])
        self.assertEqual(int(memory["memoryId"]), upsert.call_args.args[0])

    def test_confirmed_memory_enqueues_and_syncs_vector_index(self):
        with mock.patch.object(self.memory, "enqueue_memory_job", return_value=True) as enqueue:
            memory = self.memory.upsert_long_term_memory(
                owner_id="owner",
                content="详情页默认使用真实家庭场景",
                category="visual_style",
                confirmed=True,
            )

        self.assertEqual("vector", enqueue.call_args.kwargs["job_type"])
        self.assertEqual(memory["memoryId"], enqueue.call_args.kwargs["run_id"])

        with (
            mock.patch(
                "services.ecommerce.ecommerce_agent_memory_service.professional_embedding_model",
                return_value="embedding-test",
            ),
            mock.patch(
                "services.ecommerce.ecommerce_agent_memory_service.embed_professional_texts",
                return_value=[[0.5, 0.25]],
            ),
            mock.patch(
                "services.ecommerce.ecommerce_agent_memory_service.professional_memory_index.upsert",
                return_value=True,
            ) as upsert,
        ):
            result = self.memory.sync_long_term_memory_vector(
                owner_id="owner",
                memory_id=int(memory["memoryId"]),
            )

        self.assertTrue(result["indexed"])
        self.assertEqual(int(memory["memoryId"]), upsert.call_args.args[0])

    def test_pending_memory_is_visible_for_review_but_not_retrieved(self):
        pending = self.memory.upsert_long_term_memory(
            owner_id="owner",
            content="以后汽车详情页默认使用夜间道路背景",
            category="visual_style",
            confidence=0.82,
            confirmed=False,
            status="pending_review",
            metadata={"distilled": True},
        )

        with mock.patch(
            "services.ecommerce.ecommerce_agent_memory_service.professional_query_embedding",
            return_value=[],
        ):
            recalled_before = self.memory.search_long_term_memories(
                owner_id="owner",
                query="汽车夜间道路",
            )

        self.assertEqual([], recalled_before)
        self.assertEqual([], self.memory.list_long_term_memories(owner_id="owner"))
        review_items = self.memory.list_long_term_memories(owner_id="owner", include_pending=True)
        self.assertEqual("pending_review", review_items[0]["status"])

        approved = self.memory.review_long_term_memory(
            owner_id="owner",
            memory_id=int(pending["memoryId"]),
            decision="approve",
        )
        self.assertEqual("active", approved["status"])
        self.assertTrue(approved["confirmed"])
        self.assertGreaterEqual(approved["confidence"], 0.9)

        with mock.patch(
            "services.ecommerce.ecommerce_agent_memory_service.professional_query_embedding",
            return_value=[],
        ):
            recalled_after = self.memory.search_long_term_memories(
                owner_id="owner",
                query="汽车夜间道路",
            )
        self.assertEqual(1, len(recalled_after))

    def test_auto_candidate_cannot_overwrite_confirmed_memory(self):
        trusted = self.memory.upsert_long_term_memory(
            owner_id="owner",
            content="汽车详情页默认使用真实城市道路",
            category="visual_style",
            memory_key="visual.default.background",
            confidence=1.0,
            confirmed=True,
        )
        candidate = self.memory.upsert_long_term_memory(
            owner_id="owner",
            content="汽车详情页默认使用纯白摄影棚",
            category="visual_style",
            memory_key="visual.default.background",
            confidence=0.8,
            confirmed=False,
            status="pending_review",
            metadata={"distilled": True},
        )

        active = self.memory.list_long_term_memories(owner_id="owner")
        self.assertEqual([trusted["content"]], [item["content"] for item in active])
        self.assertEqual("pending_review", candidate["status"])
        self.assertEqual(int(trusted["memoryId"]), candidate["supersedesId"])

        approved = self.memory.review_long_term_memory(
            owner_id="owner",
            memory_id=int(candidate["memoryId"]),
            decision="approve",
        )
        self.assertEqual("visual.default.background", approved["memoryKey"])
        self.assertEqual(
            ["汽车详情页默认使用纯白摄影棚"],
            [item["content"] for item in self.memory.list_long_term_memories(owner_id="owner")],
        )

    def test_rejected_memory_never_becomes_retrievable(self):
        pending = self.memory.upsert_long_term_memory(
            owner_id="owner",
            content="以后默认使用错误的品牌颜色",
            category="brand_rule",
            confidence=0.88,
            status="pending_review",
        )
        rejected = self.memory.review_long_term_memory(
            owner_id="owner",
            memory_id=int(pending["memoryId"]),
            decision="reject",
        )

        self.assertEqual("rejected", rejected["status"])
        self.assertEqual([], self.memory.list_long_term_memories(owner_id="owner", include_pending=True))

    def test_only_pending_memory_can_be_reviewed(self):
        active = self.memory.upsert_long_term_memory(
            owner_id="owner",
            content="用户已经确认使用真实场景背景",
            category="preference",
            confirmed=True,
        )

        with self.assertRaisesRegex(ValueError, "not pending review"):
            self.memory.review_long_term_memory(
                owner_id="owner",
                memory_id=int(active["memoryId"]),
                decision="reject",
            )

        persisted = self.memory.list_long_term_memories(owner_id="owner")
        self.assertEqual([active["memoryId"]], [item["memoryId"] for item in persisted])

    def test_user_can_update_and_clear_only_their_long_term_memory(self):
        own = self.memory.upsert_long_term_memory(
            owner_id="owner-a",
            content="Prefer white studio backgrounds.",
            category="preference",
        )
        self.memory.upsert_long_term_memory(
            owner_id="owner-b",
            content="Prefer outdoor scenes.",
            category="preference",
            confirmed=True,
        )

        with mock.patch(
            "services.ecommerce.ecommerce_agent_memory_service.professional_memory_index.delete",
            return_value=True,
        ) as delete_vector:
            updated = self.memory.update_long_term_memory(
                owner_id="owner-a",
                memory_id=int(own["memoryId"]),
                content="Prefer realistic home scenes.",
                category="visual_style",
                scope="user",
            )
            deleted = self.memory.delete_all_long_term_memories(owner_id="owner-a")

        self.assertEqual("Prefer realistic home scenes.", updated["content"])
        self.assertEqual("visual_style", updated["category"])
        self.assertTrue(updated["confirmed"])
        self.assertEqual(1, deleted)
        self.assertEqual([], self.memory.list_all_long_term_memories(owner_id="owner-a"))
        self.assertEqual(1, len(self.memory.list_all_long_term_memories(owner_id="owner-b")))
        self.assertGreaterEqual(delete_vector.call_count, 2)

    def test_model_context_can_skip_long_term_retrieval(self):
        with mock.patch.object(self.memory, "search_long_term_memories") as search:
            context = self.memory.load_model_context(
                owner_id="owner",
                conversation_id="conversation",
                query="temporary request",
                include_long_term=False,
            )

        self.assertEqual([], context["longTermMemories"])
        search.assert_not_called()


if __name__ == "__main__":
    unittest.main()
