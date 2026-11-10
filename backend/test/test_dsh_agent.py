from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import select

from services.dsh_agent.models import MODELS
from services.dsh_agent.store import DshAgentConversationModel, DshAgentStore


class DshAgentStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        root = Path(self.temp_dir.name)
        self.store = DshAgentStore(
            f"sqlite:///{root / 'dsh-agent.db'}",
            dsh_home=root / "dsh-home",
        )
        self.addCleanup(self.store.close)
        self.owner = {"id": "owner-1", "username": "alice", "name": "Alice"}

    def _completed_conversation(self) -> tuple[str, str]:
        conversation = self.store.create_conversation(self.owner)
        conversation_id = conversation["conversation_id"]
        self.assertEqual("ok", self.store.begin_turn(conversation_id, "owner-1", "hello")["state"])
        self.store.complete_turn(
            conversation_id,
            "owner-1",
            content="hello back",
            model="test-model",
            usage={"inputTokens": 7, "outputTokens": 5, "totalTokens": 12},
            activities=[{"id": "search", "kind": "search", "title": "搜索完成", "status": "completed"}],
            timings={"queue_ms": 12, "total_ms": 80},
        )
        runtime = self.store.get_runtime_session(conversation_id, "owner-1")
        self.assertIsNotNone(runtime)
        return conversation_id, runtime[0]

    def test_registered_models_are_persisted_after_reopening_the_store(self):
        expected_models = {
            "tt-5.6-sol": "openai",
            "gem-3.5-flash": "gemini",
            "gem-3.8-flash": "gemini",
            "tt-6-sol": "openai",
            "tt-6.1-sol": "openai",
            "deepseek-v4.1-flash": "openai",
            "deepseek-v4-flash": "openai",
        }
        self.assertEqual(expected_models, {model["id"]: model["protocol"] for model in MODELS})
        conversations = {
            self.store.create_conversation(self.owner, model=model)["conversation_id"]: model
            for model in expected_models
        }
        other = self.store.create_conversation({"id": "owner-2"}, model="tt-6.1-sol")
        self.store.close()
        reopened = DshAgentStore(self.store.database_url, dsh_home=self.store.dsh_home)
        self.addCleanup(reopened.close)

        with patch.dict(os.environ, {"DSH_AGENT_MODEL": "gem-3.8-flash"}):
            self.assertEqual(conversations, {
                row["conversation_id"]: row["model"]
                for row in reopened.list_conversations("owner-1")["items"]
            })
            for conversation_id, model in conversations.items():
                with self.subTest(model=model):
                    self.assertEqual(model, reopened.get_conversation(conversation_id, owner_id="owner-1")["model"])
                    self.assertIsNone(reopened.get_conversation(conversation_id, owner_id="owner-2"))
            self.assertEqual([other], reopened.list_conversations("owner-2")["items"])

    def test_changing_model_is_owner_scoped_and_keeps_existing_history(self):
        conversation_id, _ = self._completed_conversation()
        before = self.store.list_messages(conversation_id, owner_id="owner-1")

        self.assertIsNone(self.store.set_model(conversation_id, "owner-2", "gem-3.5-flash"))
        self.assertIsNone(self.store.set_model("missing-conversation", "owner-1", "gem-3.5-flash"))
        self.assertEqual(before, self.store.list_messages(conversation_id, owner_id="owner-1"))
        updated = self.store.set_model(conversation_id, "owner-1", "tt-6.1-sol")

        self.assertEqual("tt-6.1-sol", updated["model"])
        self.assertEqual(before["conversation"]["turn_count"], updated["turn_count"])
        messages = self.store.list_messages(conversation_id, owner_id="owner-1")["items"]
        self.assertEqual(before["items"], messages[:-1])
        self.assertEqual(updated["model_switch"], messages[-1])
        self.assertEqual("model_switch", messages[-1]["role"])
        self.assertEqual({"from_model": before["conversation"]["model"], "to_model": "tt-6.1-sol"}, messages[-1]["model_switch"])
        self.store.close()
        self.assertEqual("tt-6.1-sol", self.store.get_conversation(conversation_id, owner_id="owner-1")["model"])
        self.assertEqual(messages, self.store.list_messages(conversation_id, owner_id="owner-1")["items"])

    def test_model_switches_are_ordered_idempotent_and_not_part_of_usage_or_model_context(self):
        conversation_id, _ = self._completed_conversation()
        initial_model = self.store.get_conversation(conversation_id)["model"]
        original_usage = self.store.admin_usage()
        self.assertIsNone(self.store.set_model(conversation_id, "owner-1", initial_model)["model_switch"])
        for model in ("gem-3.8-flash", "deepseek-v4-flash", "tt-6-sol"):
            self.store.set_model(conversation_id, "owner-1", model)
            self.assertIsNone(self.store.set_model(conversation_id, "owner-1", model)["model_switch"])
        self.assertEqual(original_usage, self.store.admin_usage())
        claimed = self.store.begin_turn(conversation_id, "owner-1", "next turn")
        messages = self.store.list_messages(conversation_id, owner_id="owner-1")["items"]
        self.assertEqual(["user", "assistant", "model_switch", "model_switch", "model_switch", "user"], [item["role"] for item in messages])
        self.assertEqual([
            {"from_model": initial_model, "to_model": "gem-3.8-flash"},
            {"from_model": "gem-3.8-flash", "to_model": "deepseek-v4-flash"},
            {"from_model": "deepseek-v4-flash", "to_model": "tt-6-sol"},
        ], [item["model_switch"] for item in messages if item["role"] == "model_switch"])
        self.assertEqual([
            {"role": "user", "content": "hello"}, {"role": "assistant", "content": "hello back"},
        ], self.store.get_completed_history(conversation_id, "owner-1", before_message_id=claimed["user_message_id"]))

    def test_active_turn_prevents_model_changes_and_duplicate_claims(self):
        conversation = self.store.create_conversation(self.owner, model="gem-3.5-flash")
        conversation_id = conversation["conversation_id"]
        claimed = self.store.begin_turn(conversation_id, "owner-1", "working", model="tt-6-sol")
        self.assertEqual("ok", claimed["state"])
        before = self.store.list_messages(conversation_id, owner_id="owner-1")

        with self.assertRaises(RuntimeError):
            self.store.set_model(conversation_id, "owner-1", "deepseek-v4-flash")
        self.assertEqual({"state": "busy"}, self.store.begin_turn(
            conversation_id, "owner-1", "duplicate", model="deepseek-v4.1-flash",
        ))
        self.assertEqual({"state": "not_found"}, self.store.begin_turn(
            conversation_id, "owner-2", "foreign", model="gem-3.8-flash",
        ))
        self.assertEqual(before, self.store.list_messages(conversation_id, owner_id="owner-1"))
        self.assertEqual("tt-6-sol", before["conversation"]["model"])
        self.assertEqual(["tt-6-sol"], [message["model"] for message in before["items"]])

    def test_invalid_model_never_creates_or_mutates_a_conversation(self):
        conversation = self.store.create_conversation(self.owner, model="deepseek-v4-flash")
        conversation_id = conversation["conversation_id"]
        before = self.store.list_messages(conversation_id, owner_id="owner-1")
        with self.store._session() as session:
            before_row = dict(session.execute(select(DshAgentConversationModel.__table__)).mappings().one())

        for model in ("unknown-model", "", "tt-6-sol-unregistered"):
            with self.subTest(model=model):
                with self.assertRaisesRegex(ValueError, "unsupported"):
                    self.store.create_conversation(self.owner, model=model)
                with self.assertRaisesRegex(ValueError, "unsupported"):
                    self.store.set_model(conversation_id, "owner-1", model)
                with self.assertRaisesRegex(ValueError, "unsupported"):
                    self.store.begin_turn(conversation_id, "owner-1", "must not be saved", model=model)
                self.assertEqual(1, self.store.list_conversations("owner-1")["total"])
                self.assertEqual(before, self.store.list_messages(conversation_id, owner_id="owner-1"))
                with self.store._session() as session:
                    self.assertEqual(before_row, dict(session.execute(
                        select(DshAgentConversationModel.__table__),
                    ).mappings().one()))

    def test_begin_turn_records_saved_and_overridden_models_on_each_user_message(self):
        conversation = self.store.create_conversation(self.owner, model="gem-3.5-flash")
        conversation_id = conversation["conversation_id"]
        first = self.store.begin_turn(conversation_id, "owner-1", "first")
        self.assertEqual("gem-3.5-flash", first["conversation"]["model"])
        self.store.complete_turn(conversation_id, "owner-1", content="reply", model="gem-3.5-flash", usage={})

        second = self.store.begin_turn(conversation_id, "owner-1", "second", model="deepseek-v4.1-flash")
        self.assertEqual("deepseek-v4.1-flash", second["conversation"]["model"])
        self.store.fail_turn(conversation_id, "owner-1", model="deepseek-v4.1-flash", error_code="RuntimeError")
        self.store.close()
        with patch.dict(os.environ, {"DSH_AGENT_MODEL": "tt-6-sol"}):
            third = self.store.begin_turn(conversation_id, "owner-1", "third")
        self.assertEqual("deepseek-v4.1-flash", third["conversation"]["model"])
        self.store.complete_turn(conversation_id, "owner-1", content="recovered", model="deepseek-v4.1-flash", usage={})

        messages = self.store.list_messages(conversation_id, owner_id="owner-1")["items"]
        self.assertEqual(["user", "assistant"] * 3, [message["role"] for message in messages])
        self.assertEqual(["gem-3.5-flash"] * 2 + ["deepseek-v4.1-flash"] * 4, [message["model"] for message in messages])
        self.assertEqual("error", messages[3]["status"])
        self.assertEqual("RuntimeError", messages[3]["error_code"])
        self.assertEqual("idle", self.store.get_conversation(conversation_id)["status"])

    def test_usage_is_attributed_and_conversation_is_owner_scoped(self):
        conversation_id, _ = self._completed_conversation()

        self.assertEqual(1, self.store.list_conversations("owner-1")["total"])
        self.assertEqual(0, self.store.list_conversations("owner-2")["total"])
        usage = self.store.admin_usage()
        self.assertEqual(1, usage["summary"]["assistant_turns"])
        self.assertEqual(12, usage["summary"]["total_tokens"])
        self.assertEqual("alice", usage["items"][0]["owner_username"])
        stored = self.store.list_messages(conversation_id)["items"]
        self.assertEqual("search", stored[-1]["activities"][0]["kind"])
        self.assertEqual(80, stored[-1]["timings"]["total_ms"])
        self.assertFalse(usage["summary"]["cost_available"])
        self.assertIsNone(self.store.list_messages(conversation_id, owner_id="owner-2"))

    def test_hard_delete_removes_database_history_and_only_that_dsh_session(self):
        conversation_id, runtime_session_id = self._completed_conversation()
        sessions = self.store.dsh_home / "sessions"
        session_dir = sessions / "workspace-one" / runtime_session_id
        other_session_dir = sessions / "workspace-one" / f"session-{'a' * 32}"
        session_dir.mkdir(parents=True)
        other_session_dir.mkdir(parents=True)
        (session_dir / "session.v4.jsonl").write_text("private conversation", encoding="utf-8")
        (other_session_dir / "session.v4.jsonl").write_text("keep", encoding="utf-8")

        self.assertFalse(self.store.delete_conversation(conversation_id, "owner-2"))
        self.assertTrue(self.store.delete_conversation(conversation_id, "owner-1"))

        self.assertFalse(session_dir.exists())
        self.assertTrue(other_session_dir.joinpath("session.v4.jsonl").is_file())
        self.assertEqual(0, self.store.list_conversations("owner-1")["total"])
        self.assertIsNone(self.store.list_messages(conversation_id, owner_id="owner-1"))

    def test_active_conversation_cannot_be_deleted(self):
        conversation = self.store.create_conversation(self.owner)
        conversation_id = conversation["conversation_id"]
        self.assertEqual("ok", self.store.begin_turn(conversation_id, "owner-1", "working")["state"])

        with self.assertRaisesRegex(RuntimeError, "processing"):
            self.store.delete_conversation(conversation_id, "owner-1")

        self.assertIsNotNone(self.store.get_conversation(conversation_id, owner_id="owner-1"))

    def test_completed_history_excludes_failed_and_current_turns(self):
        conversation = self.store.create_conversation(self.owner)
        conversation_id = conversation["conversation_id"]

        first = self.store.begin_turn(conversation_id, "owner-1", "第一轮")
        self.assertEqual("ok", first["state"])
        self.store.complete_turn(
            conversation_id,
            "owner-1",
            content="第一轮回复",
            model="test-model",
            usage={},
        )

        failed = self.store.begin_turn(conversation_id, "owner-1", "失败的一轮")
        self.assertEqual("ok", failed["state"])
        self.store.fail_turn(conversation_id, "owner-1", model="test-model", error_code="RuntimeError")

        current = self.store.begin_turn(conversation_id, "owner-1", "当前问题")
        self.assertEqual("ok", current["state"])
        self.assertTrue(current["user_message_id"])

        history = self.store.get_completed_history(
            conversation_id,
            "owner-1",
            before_message_id=current["user_message_id"],
        )

        self.assertEqual([
            {"role": "user", "content": "第一轮"},
            {"role": "assistant", "content": "第一轮回复"},
        ], history)
        self.assertEqual([], self.store.get_completed_history(
            conversation_id, "owner-2", before_message_id=current["user_message_id"],
        ))
        self.assertEqual([], self.store.get_completed_history(
            conversation_id, "owner-1", before_message_id="missing-message",
        ))
        self.assertEqual([], self.store.get_completed_history(
            conversation_id, "owner-1", before_message_id=current["user_message_id"], max_chars=1,
        ))


if __name__ == "__main__":
    unittest.main()
