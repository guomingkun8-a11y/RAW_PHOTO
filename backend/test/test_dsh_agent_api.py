from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.dsh_agent.routes as dsh_routes
from services.dsh_agent.models import MODELS
from services.dsh_agent.store import DshAgentStore
from services.dsh_agent.sessions import RuntimePool


class DshAgentApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.store = DshAgentStore(
            f"sqlite:///{Path(self.temp_dir.name) / 'dsh-agent-api.db'}",
            dsh_home=Path(self.temp_dir.name) / "dsh-home",
        )
        self.addCleanup(self.store.close)
        app = FastAPI()
        app.include_router(dsh_routes.create_router())
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    @staticmethod
    def _identity(authorization: str | None):
        token = str(authorization or "").replace("Bearer ", "")
        if token == "alice":
            return {"id": "user-alice", "username": "alice", "name": "Alice", "role": "user"}
        return {"id": "user-bob", "username": "bob", "name": "Bob", "role": "user"}

    def test_stop_is_owner_scoped_idempotent_and_cannot_cancel_a_later_turn(self):
        conversation = self.store.create_conversation(self._identity("Bearer alice"))
        cid = conversation["conversation_id"]
        claimed = self.store.begin_turn(cid, "user-alice", "first")
        payload = {"user_message_id": claimed["user_message_id"]}
        path = f"/api/dsh-agent/conversations/{cid}/stop"
        with patch.object(dsh_routes, "dsh_agent_store", self.store), patch.object(dsh_routes, "require_identity", side_effect=self._identity):
            self.assertEqual(404, self.client.post(path, headers={"Authorization": "Bearer bob"}, json=payload).status_code)
            self.assertFalse(self.client.post(path, headers={"Authorization": "Bearer alice"}, json={"user_message_id": "stale"}).json()["accepted"])
            for _ in range(2):
                self.assertTrue(self.client.post(path, headers={"Authorization": "Bearer alice"}, json=payload).json()["accepted"])
            # Completion racing stop still persists a stopped draft, not a success.
            message = self.store.complete_turn(cid, "user-alice", content="partial", model="tt-5.6-sol", usage={"totalTokens": 5})
            self.assertEqual("stopped", message["status"])
            self.assertEqual("idle", self.store.get_conversation(cid)["status"])
            self.store.begin_turn(cid, "user-alice", "second")
            self.assertFalse(self.client.post(path, headers={"Authorization": "Bearer alice"}, json=payload).json()["accepted"])
            self.assertEqual("running", self.store.get_conversation(cid)["status"])

    def test_expert_selection_is_validated_persisted_and_can_be_removed(self):
        cid = self.store.create_conversation(self._identity("Bearer alice"))["conversation_id"]
        path = f"/api/dsh-agent/conversations/{cid}/messages/stream"
        def run(_prompt, **kwargs):
            return {"content": "建议保留商品原有标签。", "model": "tt-5.6-sol", "finish_reason": "completed", "usage": {"totalTokens": 42}}
        with (
            patch.object(dsh_routes, "dsh_agent_store", self.store),
            patch.object(dsh_routes, "require_identity", side_effect=self._identity),
            patch.object(dsh_routes.teams, "snapshot", return_value=None),
            patch.object(dsh_routes.dsh_runtime, "public_status", return_value={"enabled": True, "configured": True}),
            patch.object(dsh_routes.dsh_runtime, "run_turn", side_effect=run) as execute,
        ):
            invalid = self.client.post(path, headers={"Authorization": "Bearer alice"}, json={"content": "建议", "expert_id": "../../.env"})
            self.assertEqual(400, invalid.status_code)
            self.assertEqual([], self.store.list_messages(cid)["items"])
            execute.assert_not_called()
            foreign = self.client.post(path, headers={"Authorization": "Bearer bob"}, json={"content": "建议", "expert_id": "ecommerce-design"})
            self.assertEqual(404, foreign.status_code)
            for expert_id in ("ecommerce-design", ""):
                response = self.client.post(path, headers={"Authorization": "Bearer alice"}, json={"content": "只给商品设计建议，不生成", "expert_id": expert_id})
                self.assertEqual(200, response.status_code)
                frames = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
                self.assertEqual("complete", frames[-1]["type"])
                self.assertEqual(expert_id, execute.call_args.kwargs.get("expert_id", ""))
                history = self.client.get(path.removesuffix("/messages/stream"), headers={"Authorization": "Bearer alice"}).json()["items"]
                for message in history[-2:]:
                    self.assertEqual(expert_id, message["expert_id"])
                    self.assertNotIn("expert_id", message["usage"])

    def test_team_state_is_owner_scoped_and_stale_approval_does_not_start_a_turn(self):
        cid = self.store.create_conversation(self._identity("Bearer alice"))["conversation_id"]
        state = {"id": "team", "phase": "staged", "revision": "current", "halted": False, "tasks": [], "members": []}
        path = f"/api/dsh-agent/conversations/{cid}"
        with (
            patch.object(dsh_routes, "dsh_agent_store", self.store),
            patch.object(dsh_routes, "require_identity", side_effect=self._identity),
            patch.object(dsh_routes.teams, "snapshot", return_value=state),
            patch.object(dsh_routes.dsh_runtime, "public_status", return_value={"enabled": True, "configured": True, "teams_enabled": True}),
            patch.object(dsh_routes.dsh_runtime, "run_turn") as execute,
        ):
            self.assertEqual(404, self.client.get(path + "/team", headers={"Authorization": "Bearer bob"}).status_code)
            self.assertEqual(state, self.client.get(path + "/team", headers={"Authorization": "Bearer alice"}).json()["team"])
            response = self.client.post(path + "/messages/stream", headers={"Authorization": "Bearer alice"},
                                        json={"content": "确认执行", "mode": "teams", "team_action": "approve", "team_revision": "stale"})
            self.assertEqual(409, response.status_code)
            self.assertEqual([], self.store.list_messages(cid)["items"])
            execute.assert_not_called()

    def test_skills_require_explicit_selection_and_are_validated_and_restored(self):
        from services.dsh_agent.skills import RESEARCH_SKILLS, STYLE_SKILL

        cid = self.store.create_conversation(self._identity("Bearer alice"))["conversation_id"]
        path = f"/api/dsh-agent/conversations/{cid}/messages/stream"
        with (
            patch.object(dsh_routes, "dsh_agent_store", self.store),
            patch.object(dsh_routes, "require_identity", side_effect=self._identity),
            patch.object(dsh_routes.teams, "snapshot", return_value=None),
            patch.object(dsh_routes.dsh_runtime, "public_status", return_value={"enabled": True, "configured": True}),
            patch.object(dsh_routes.dsh_runtime, "run_turn", return_value={"content": "建议", "model": "tt-6-sol", "finish_reason": "completed", "usage": {}}) as execute,
        ):
            for ids in (["../../.env"], ["unknown"], [STYLE_SKILL] * 5):
                self.assertIn(self.client.post(path, headers={"Authorization": "Bearer alice"}, json={"content": "建议", "skill_ids": ids}).status_code, (400, 422))
            execute.assert_not_called()
            self.assertEqual([], self.store.list_messages(cid)["items"])
            for payload, expected in (({"expert_id": "ecommerce-design"}, []),
                                      ({"expert_id": "ecommerce-design", "skill_ids": []}, []),
                                      ({"expert_id": "ecommerce-design", "skill_ids": [STYLE_SKILL]}, [STYLE_SKILL]),
                                      ({"skill_ids": [STYLE_SKILL]}, [STYLE_SKILL]),
                                      ({"skill_ids": list(RESEARCH_SKILLS)}, list(RESEARCH_SKILLS)),
                                      ({"skill_ids": []}, [])):
                response = self.client.post(path, headers={"Authorization": "Bearer alice"}, json={"content": "只给建议", **payload})
                self.assertEqual(200, response.status_code)
                self.assertEqual(expected, execute.call_args.kwargs["skill_ids"])
                history = self.client.get(path.removesuffix("/messages/stream"), headers={"Authorization": "Bearer alice"}).json()["items"]
                for message in history[-2:]:
                    self.assertEqual(expected, message["skill_ids"])
                    self.assertNotIn("skill_ids", message["usage"])
            self.assertEqual(404, self.client.get(path.removesuffix("/messages/stream"), headers={"Authorization": "Bearer bob"}).status_code)

    def test_team_snapshot_and_aggregated_usage_survive_refresh(self):
        cid = self.store.create_conversation(self._identity("Bearer alice"))["conversation_id"]
        state = {"id": "team", "phase": "staged", "revision": "current", "halted": False, "tasks": [], "members": []}

        def run(_prompt, **kwargs):
            self.assertTrue(kwargs["team_mode"])
            self.assertEqual("user-alice", kwargs["owner_id"])
            self.assertTrue(kwargs["local_task_id"].startswith("msg_"))
            kwargs["on_team"](state)
            return {"content": "请确认计划", "model": "tt-5.6-sol", "finish_reason": "completed", "usage": {"totalTokens": 42}}

        with (
            patch.object(dsh_routes, "dsh_agent_store", self.store),
            patch.object(dsh_routes, "require_identity", side_effect=self._identity),
            patch.object(dsh_routes.dsh_runtime, "public_status", return_value={"enabled": True, "configured": True, "teams_enabled": True}),
            patch.object(dsh_routes.dsh_runtime, "run_turn", side_effect=run),
        ):
            response = self.client.post(f"/api/dsh-agent/conversations/{cid}/messages/stream", headers={"Authorization": "Bearer alice"}, json={"content": "规划商品", "mode": "teams"})
            frames = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
            self.assertEqual(state, next(item["team"] for item in frames if item["type"] == "team"))
            message = self.store.list_messages(cid)["items"][-1]
            self.assertEqual(state, message["team"])
            self.assertNotIn("team", message["usage"])
            self.assertEqual(42, message["total_tokens"])

    def test_controlled_team_failure_keeps_public_reason_team_and_child_usage_after_refresh(self):
        from types import SimpleNamespace

        cid = self.store.create_conversation(self._identity("Bearer alice"))["conversation_id"]
        reason = "搜索服务鉴权异常，已停止本轮协作。"
        state = {"id": "team", "phase": "running", "revision": "failed", "halted": True, "members": [], "tasks": [{"id": "t1", "status": "failed"}, {"id": "t2", "status": "blocked", "blocked_by": ["t1"]}], "error": reason}

        def run(_prompt, **kwargs):
            kwargs["on_team"](state)
            kwargs["on_notification"](SimpleNamespace(method="session.event", payload={
                "sessionId": kwargs["dsh_session_id"], "event": {"type": "assistant/message", "data": {"usage": {"totalTokens": 42}}},
            }))
            raise dsh_routes.teams.TeamExecutionError(reason)

        with (
            patch.object(dsh_routes, "dsh_agent_store", self.store),
            patch.object(dsh_routes, "require_identity", side_effect=self._identity),
            patch.object(dsh_routes.dsh_runtime, "public_status", return_value={"enabled": True, "configured": True, "teams_enabled": True}),
            patch.object(dsh_routes.dsh_runtime, "run_turn", side_effect=run),
            patch.object(dsh_routes.teams, "snapshot", return_value={key: value for key, value in state.items() if key != "error"}),
        ):
            response = self.client.post(f"/api/dsh-agent/conversations/{cid}/messages/stream", headers={"Authorization": "Bearer alice"}, json={"content": "请调研", "mode": "teams"})
            frames = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
            self.assertEqual(reason, next(item["message"] for item in frames if item["type"] == "error"))
            history = self.client.get(f"/api/dsh-agent/conversations/{cid}", headers={"Authorization": "Bearer alice"}).json()
            self.assertEqual("error", history["conversation"]["status"])
            self.assertEqual(reason, history["items"][-1]["content"])
            self.assertEqual(state, history["items"][-1]["team"])
            self.assertEqual(42, history["items"][-1]["total_tokens"])
            self.assertEqual(reason, history["team"]["error"])
            self.assertEqual(reason, self.client.get(f"/api/dsh-agent/conversations/{cid}/team", headers={"Authorization": "Bearer alice"}).json()["team"]["error"])

    def test_context_stream_snapshot_survives_refresh_without_replacing_history_or_billing_usage(self):
        from types import SimpleNamespace
        cid = self.store.create_conversation(self._identity("Bearer alice"))["conversation_id"]
        snapshot = {"model": "tt-5.6-sol", "used_tokens": 1200, "context_window": 128000,
                    "max_output_tokens": 8192, "compact_at_tokens": 102400, "limit_source": "deployment",
                    "estimated": True, "compacting": False, "compacted": True, "compaction_failed": False}

        def run(_prompt, **kwargs):
            for kind, data in [("compaction/start", {}), ("compaction/end", {}), ("plugin:raw-photo/context", snapshot)]:
                kwargs["on_notification"](SimpleNamespace(method="session.event", payload={
                    "sessionId": kwargs["dsh_session_id"], "event": {"type": kind, "data": data},
                }))
            return {"content": "商品标签保持原样", "model": "tt-5.6-sol", "finish_reason": "completed", "usage": {"inputTokens": 220000, "outputTokens": 30, "totalTokens": 220030}}

        with (
            patch.object(dsh_routes, "dsh_agent_store", self.store),
            patch.object(dsh_routes, "require_identity", side_effect=self._identity),
            patch.object(dsh_routes.dsh_runtime, "public_status", return_value={"enabled": True, "configured": True}),
            patch.object(dsh_routes.dsh_runtime, "run_turn", side_effect=run),
        ):
            path = f"/api/dsh-agent/conversations/{cid}"
            headers = {"Authorization": "Bearer alice"}
            response = self.client.post(path + "/messages/stream", headers=headers, json={"content": "请保留商品标签"})
            frames = [json.loads(frame[6:]) for frame in response.text.strip().split("\n\n") if frame.startswith("data: ")]
            self.assertEqual(snapshot, next(item["context"] for item in frames if item["type"] == "context"))
            history = self.client.get(path, headers=headers).json()["items"]
            self.assertEqual(["请保留商品标签", "商品标签保持原样"], [item["content"] for item in history])
            self.assertEqual(snapshot, history[-1]["context"])
            self.assertNotIn("context", history[-1]["usage"])
            self.assertEqual(220030, history[-1]["total_tokens"])
            self.assertEqual(404, self.client.get(path, headers={"Authorization": "Bearer bob"}).status_code)

    def test_interrupted_compression_clears_spinner_and_persists_failed_snapshot(self):
        from types import SimpleNamespace

        for stopped in (False, True):
            with self.subTest(stopped=stopped):
                cid = self.store.create_conversation(self._identity("Bearer alice"))["conversation_id"]

                def run(_prompt, **kwargs):
                    for kind, data in [
                        ("compaction/start", {}),
                        ("plugin:raw-photo/context", {"model": "tt-5.6-sol", "used_tokens": 110000, "compacting": True}),
                    ]:
                        kwargs["on_notification"](SimpleNamespace(method="session.event", payload={
                            "sessionId": kwargs["dsh_session_id"], "event": {"type": kind, "data": data},
                        }))
                    raise dsh_routes.dsh_runtime.DshAgentStopped() if stopped else RuntimeError("mock compaction failed")

                with (
                    patch.object(dsh_routes, "dsh_agent_store", self.store),
                    patch.object(dsh_routes, "require_identity", side_effect=self._identity),
                    patch.object(dsh_routes.dsh_runtime, "public_status", return_value={"enabled": True, "configured": True}),
                    patch.object(dsh_routes.dsh_runtime, "run_turn", side_effect=run),
                ):
                    response = self.client.post(f"/api/dsh-agent/conversations/{cid}/messages/stream",
                                                headers={"Authorization": "Bearer alice"}, json={"content": "保留原始消息"})
                frames = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
                snapshot = [item["context"] for item in frames if item["type"] == "context"][-1]
                self.assertFalse(snapshot["compacting"])
                self.assertTrue(snapshot["compaction_failed"])
                history = self.store.list_messages(cid)["items"]
                self.assertEqual("保留原始消息", history[0]["content"])
                self.assertEqual(snapshot, history[-1]["context"])
                self.assertEqual("stopped" if stopped else "error", history[-1]["status"])

    def test_ratings_and_discard_are_persisted_and_owner_scoped_without_erasing_usage(self):
        cid = self.store.create_conversation(self._identity("Bearer alice"))["conversation_id"]
        self.store.begin_turn(cid, "user-alice", "question")
        message = self.store.complete_turn(cid, "user-alice", content="partial", model="tt-5.6-sol", usage={"totalTokens": 5}, stopped=True)
        path = f"/api/dsh-agent/conversations/{cid}/messages/{message['message_id']}"
        alice = {"Authorization": "Bearer alice"}
        bob = {"Authorization": "Bearer bob"}
        with patch.object(dsh_routes, "dsh_agent_store", self.store), patch.object(dsh_routes, "require_identity", side_effect=self._identity):
            self.assertEqual(404, self.client.patch(path + "/feedback", headers=bob, json={"feedback": "positive"}).status_code)
            self.assertEqual(422, self.client.patch(path + "/feedback", headers=alice, json={"feedback": "bad"}).status_code)
            for rating in ("positive", "negative", None):
                self.assertEqual(rating, self.client.patch(path + "/feedback", headers=alice, json={"feedback": rating}).json()["feedback"])
                self.assertEqual(rating, self.store.list_messages(cid)["items"][-1]["feedback"])
            self.assertEqual(404, self.client.delete(path + "/draft", headers=bob).status_code)
            self.assertEqual(200, self.client.delete(path + "/draft", headers=alice).status_code)
            self.assertEqual(["user"], [item["role"] for item in self.store.list_messages(cid)["items"]])
            self.assertEqual(5, self.store.admin_usage()["summary"]["total_tokens"])
            self.store.begin_turn(cid, "user-alice", "another")
            completed = self.store.complete_turn(cid, "user-alice", content="done", model="tt-5.6-sol", usage={})
            self.assertEqual(409, self.client.delete(f"/api/dsh-agent/conversations/{cid}/messages/{completed['message_id']}/draft", headers=alice).status_code)

    def test_stopped_stream_keeps_partial_text_and_releases_conversation(self):
        from types import SimpleNamespace
        cid = self.store.create_conversation(self._identity("Bearer alice"))["conversation_id"]

        def run(_prompt, **kwargs):
            kwargs["on_notification"](SimpleNamespace(method="session.event", payload={
                "sessionId": kwargs["dsh_session_id"], "event": {"type": "assistant/live-chunk", "data": {"chunk": {"type": "text-delta", "text": "已收到的部分"}}},
            }))
            raise dsh_routes.dsh_runtime.DshAgentStopped()

        with (
            patch.object(dsh_routes, "dsh_agent_store", self.store),
            patch.object(dsh_routes, "require_identity", side_effect=self._identity),
            patch.object(dsh_routes.dsh_runtime, "public_status", return_value={"enabled": True, "configured": True}),
            patch.object(dsh_routes.dsh_runtime, "run_turn", side_effect=run),
        ):
            response = self.client.post(f"/api/dsh-agent/conversations/{cid}/messages/stream", headers={"Authorization": "Bearer alice"}, json={"content": "question", "text_only": True})
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
        message = events[-1]["message"]
        self.assertEqual("stopped", message["status"])
        self.assertEqual("已收到的部分", message["content"])
        self.assertFalse(any(event["type"] == "error" for event in events))
        self.assertTrue(all(item["status"] != "running" for item in message["activities"]))
        self.assertEqual(message, self.store.list_messages(cid)["items"][-1])
        self.assertEqual("ok", self.store.begin_turn(cid, "user-alice", "continue")["state"])

    def test_users_only_see_and_delete_their_own_conversations(self):
        with patch.object(dsh_routes, "dsh_agent_store", self.store), patch.object(
            dsh_routes, "require_identity", side_effect=self._identity,
        ):
            created = self.client.post("/api/dsh-agent/conversations", headers={"Authorization": "Bearer alice"})
            self.assertEqual(200, created.status_code)
            conversation_id = created.json()["conversation_id"]

            self.assertEqual(0, self.client.get(
                "/api/dsh-agent/conversations", headers={"Authorization": "Bearer bob"},
            ).json()["total"])
            self.assertEqual(404, self.client.get(
                f"/api/dsh-agent/conversations/{conversation_id}",
                headers={"Authorization": "Bearer bob"},
            ).status_code)
            self.assertEqual(404, self.client.delete(
                f"/api/dsh-agent/conversations/{conversation_id}",
                headers={"Authorization": "Bearer bob"},
            ).status_code)
            self.assertEqual(200, self.client.delete(
                f"/api/dsh-agent/conversations/{conversation_id}",
                headers={"Authorization": "Bearer alice"},
            ).status_code)

    def test_admin_monitoring_requires_admin_authorization(self):
        def require_admin(authorization: str | None):
            if authorization != "Bearer admin":
                from fastapi import HTTPException

                raise HTTPException(status_code=403)

        with patch.object(dsh_routes, "dsh_agent_store", self.store), patch.object(
            dsh_routes, "require_admin", side_effect=require_admin,
        ):
            self.assertEqual(403, self.client.get(
                "/api/dsh-agent/admin/usage", headers={"Authorization": "Bearer alice"},
            ).status_code)
            self.assertEqual(200, self.client.get(
                "/api/dsh-agent/admin/usage", headers={"Authorization": "Bearer admin"},
            ).status_code)

    def test_create_accepts_optional_model_and_all_registered_choices(self):
        headers = {"Authorization": "Bearer alice"}
        with (
            patch.object(dsh_routes, "dsh_agent_store", self.store),
            patch.object(dsh_routes, "require_identity", side_effect=self._identity),
            patch.dict(os.environ, {"DSH_AGENT_MODEL": "tt-5.6-sol"}),
        ):
            for kwargs in ({}, {"json": {}}, {"json": {"model": None}}):
                response = self.client.post("/api/dsh-agent/conversations", headers=headers, **kwargs)
                self.assertEqual(200, response.status_code)
                self.assertEqual("tt-5.6-sol", response.json()["model"])
            for model in MODELS:
                with self.subTest(model=model["id"]):
                    response = self.client.post(
                        "/api/dsh-agent/conversations", headers=headers, json={"model": model["id"]},
                    )
                    self.assertEqual(200, response.status_code)
                    self.assertEqual(model["id"], response.json()["model"])
                    conversation_id = response.json()["conversation_id"]
                    self.assertEqual(model["id"], self.client.get(
                        f"/api/dsh-agent/conversations/{conversation_id}", headers=headers,
                    ).json()["conversation"]["model"])

    def test_patch_model_is_persisted_and_other_owners_get_not_found(self):
        headers = {"Authorization": "Bearer alice"}
        with (
            patch.object(dsh_routes, "dsh_agent_store", self.store),
            patch.object(dsh_routes, "require_identity", side_effect=self._identity),
        ):
            conversation = self.client.post(
                "/api/dsh-agent/conversations", headers=headers, json={"model": "gem-3.5-flash"},
            ).json()
            conversation_id = conversation["conversation_id"]
            path = f"/api/dsh-agent/conversations/{conversation_id}/model"
            self.assertEqual(404, self.client.patch(
                path, headers={"Authorization": "Bearer bob"}, json={"model": "tt-6-sol"},
            ).status_code)
            self.assertEqual(404, self.client.patch(
                "/api/dsh-agent/conversations/missing/model", headers=headers, json={"model": "tt-6-sol"},
            ).status_code)
            self.assertEqual(conversation, self.store.get_conversation(conversation_id))

            changed = self.client.patch(path, headers=headers, json={"model": "gem-3.8-flash"})
            self.assertEqual(200, changed.status_code)
            self.assertEqual(conversation_id, changed.json()["conversation_id"])
            self.assertEqual("gem-3.8-flash", changed.json()["model"])
            self.store.close()
            detail = self.client.get(f"/api/dsh-agent/conversations/{conversation_id}", headers=headers).json()
            changed_payload = changed.json()
            notice = changed_payload.pop("model_switch")
            self.assertEqual(changed_payload, detail["conversation"])
            self.assertEqual([notice], detail["items"])
            self.assertEqual({"from_model": "gem-3.5-flash", "to_model": "gem-3.8-flash"}, notice["model_switch"])
            repeated = self.client.patch(path, headers=headers, json={"model": "gem-3.8-flash"}).json()
            self.assertIsNone(repeated["model_switch"])
            self.assertEqual(detail, self.client.get(f"/api/dsh-agent/conversations/{conversation_id}", headers=headers).json())
            self.assertEqual([changed_payload], self.client.get("/api/dsh-agent/conversations", headers=headers).json()["items"])
            self.assertEqual(0, self.client.get(
                "/api/dsh-agent/conversations", headers={"Authorization": "Bearer bob"},
            ).json()["total"])

    def test_invalid_models_are_rejected_before_mutating_rows_or_starting_runtime(self):
        headers = {"Authorization": "Bearer alice"}
        with (
            patch.object(dsh_routes, "dsh_agent_store", self.store),
            patch.object(dsh_routes, "require_identity", side_effect=self._identity),
            patch.object(dsh_routes.dsh_runtime, "public_status", return_value={"enabled": True, "configured": True}),
            patch.object(dsh_routes.dsh_runtime, "run_turn") as run_turn,
        ):
            conversation = self.client.post(
                "/api/dsh-agent/conversations", headers=headers, json={"model": "tt-6.1-sol"},
            ).json()
            conversation_id = conversation["conversation_id"]
            before = self.store.list_messages(conversation_id, owner_id="user-alice")
            for path, payload in (
                ("/api/dsh-agent/conversations", {"model": "unsupported-model"}),
                (f"/api/dsh-agent/conversations/{conversation_id}/model", {"model": "unsupported-model"}),
                (f"/api/dsh-agent/conversations/{conversation_id}/messages/stream", {
                    "content": "must not be saved", "model": "unsupported-model",
                }),
            ):
                with self.subTest(path=path):
                    response = self.client.request("PATCH" if path.endswith("/model") else "POST", path, headers=headers, json=payload)
                    self.assertEqual(400, response.status_code)
                    self.assertEqual(before, self.store.list_messages(conversation_id, owner_id="user-alice"))
                    self.assertEqual(1, self.store.list_conversations("user-alice")["total"])
            run_turn.assert_not_called()

    def test_patch_and_stream_reject_active_or_foreign_conversations_without_changing_model(self):
        headers = {"Authorization": "Bearer alice"}
        with (
            patch.object(dsh_routes, "dsh_agent_store", self.store),
            patch.object(dsh_routes, "require_identity", side_effect=self._identity),
            patch.object(dsh_routes.dsh_runtime, "public_status", return_value={"enabled": True, "configured": True}),
            patch.object(dsh_routes.dsh_runtime, "run_turn") as run_turn,
        ):
            conversation = self.client.post(
                "/api/dsh-agent/conversations", headers=headers, json={"model": "tt-6-sol"},
            ).json()
            conversation_id = conversation["conversation_id"]
            self.assertEqual("ok", self.store.begin_turn(conversation_id, "user-alice", "working")["state"])
            before = self.store.list_messages(conversation_id, owner_id="user-alice")
            for method, suffix, payload in (
                ("PATCH", "model", {"model": "deepseek-v4-flash"}),
                ("POST", "messages/stream", {"content": "duplicate", "model": "gem-3.8-flash"}),
            ):
                for token, expected_status in (("alice", 409), ("bob", 404)):
                    with self.subTest(method=method, owner=token):
                        response = self.client.request(
                            method, f"/api/dsh-agent/conversations/{conversation_id}/{suffix}",
                            headers={"Authorization": f"Bearer {token}"}, json=payload,
                        )
                        self.assertEqual(expected_status, response.status_code)
                        self.assertEqual(before, self.store.list_messages(conversation_id, owner_id="user-alice"))
            run_turn.assert_not_called()

    def test_stream_uses_claimed_model_in_start_runtime_and_success_records(self):
        headers = {"Authorization": "Bearer alice"}
        status = {"enabled": True, "configured": True, "model": "tt-5.6-sol"}
        with (
            patch.object(dsh_routes, "dsh_agent_store", self.store),
            patch.object(dsh_routes, "require_identity", side_effect=self._identity),
            patch.object(dsh_routes.dsh_runtime, "public_status", return_value=status),
            patch.dict(os.environ, {"DSH_AGENT_MODEL": "tt-5.6-sol"}),
        ):
            for model in (item["id"] for item in MODELS):
                for override in (False, True):
                    with self.subTest(model=model, override=override):
                        conversation = self.client.post(
                            "/api/dsh-agent/conversations", headers=headers,
                            json={"model": "tt-5.6-sol" if override else model},
                        ).json()
                        conversation_id = conversation["conversation_id"]
                        payload = {"content": "hello"}
                        if override:
                            payload["model"] = model
                        result = {"content": "mock reply", "model": model, "finish_reason": "completed",
                                  "usage": {"inputTokens": 3, "outputTokens": 4, "totalTokens": 7}}
                        with patch.object(dsh_routes.dsh_runtime, "run_turn", return_value=result) as run_turn:
                            response = self.client.post(
                                f"/api/dsh-agent/conversations/{conversation_id}/messages/stream",
                                headers=headers, json=payload,
                            )
                        self.assertEqual(200, response.status_code)
                        self.assertTrue(response.headers["content-type"].startswith("text/event-stream"))
                        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
                        user_message_id = events[0]["user_message_id"]
                        self.assertEqual({"type": "start", "conversation_id": conversation_id, "model": model, "user_message_id": user_message_id}, events[0])
                        self.assertTrue(user_message_id.startswith("msg_"))
                        self.assertNotIn("error", [event["type"] for event in events])
                        self.assertEqual("complete", events[-1]["type"])
                        self.assertEqual(model, events[-1]["message"]["model"])
                        run_turn.assert_called_once()
                        self.assertTrue(run_turn.call_args.args[0].endswith("用户消息：\nhello"))
                        self.assertIn("generate_image", run_turn.call_args.args[0])
                        self.assertEqual(model, run_turn.call_args.kwargs["model"])
                        self.assertEqual("user-alice", run_turn.call_args.kwargs["owner_id"])
                        history = self.store.list_messages(conversation_id, owner_id="user-alice")
                        self.assertEqual(["user", "assistant"], [message["role"] for message in history["items"]])
                        self.assertEqual([model, model], [message["model"] for message in history["items"]])
                        self.assertEqual(["completed", "completed"], [message["status"] for message in history["items"]])
                        self.assertEqual(7, history["items"][-1]["total_tokens"])
                        self.assertEqual((model, "idle", 1), (
                            history["conversation"]["model"], history["conversation"]["status"], history["conversation"]["turn_count"],
                        ))

    def test_failed_stream_records_selected_model_and_releases_active_turn(self):
        headers = {"Authorization": "Bearer alice"}
        with (
            patch.object(dsh_routes, "dsh_agent_store", self.store),
            patch.object(dsh_routes, "require_identity", side_effect=self._identity),
            patch.object(dsh_routes.dsh_runtime, "public_status", return_value={
                "enabled": True, "configured": True, "model": "tt-5.6-sol",
            }),
        ):
            for override in (False, True):
                with self.subTest(override=override):
                    model = "tt-6.1-sol" if override else "gem-3.8-flash"
                    conversation = self.client.post(
                        "/api/dsh-agent/conversations", headers=headers,
                        json={"model": "tt-5.6-sol" if override else model},
                    ).json()
                    conversation_id = conversation["conversation_id"]
                    payload = {"content": "hello"}
                    if override:
                        payload["model"] = model
                    with patch.object(dsh_routes.dsh_runtime, "run_turn", side_effect=RuntimeError("mock failure")) as run_turn:
                        response = self.client.post(
                            f"/api/dsh-agent/conversations/{conversation_id}/messages/stream",
                            headers=headers, json=payload,
                        )
                    self.assertEqual(200, response.status_code)
                    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
                    self.assertEqual(model, events[0]["model"])
                    self.assertEqual("start", events[0]["type"])
                    self.assertEqual("error", events[-1]["type"])
                    self.assertNotIn("complete", [event["type"] for event in events])
                    run_turn.assert_called_once()
                    self.assertEqual(model, run_turn.call_args.kwargs["model"])
                    history = self.store.list_messages(conversation_id, owner_id="user-alice")
                    self.assertEqual([model, model], [message["model"] for message in history["items"]])
                    self.assertEqual(["completed", "error"], [message["status"] for message in history["items"]])
                    self.assertEqual("RuntimeError", history["items"][-1]["error_code"])
                    self.assertEqual((model, "error", 1), (
                        history["conversation"]["model"], history["conversation"]["status"], history["conversation"]["turn_count"],
                    ))
                    self.assertEqual(200, self.client.patch(
                        f"/api/dsh-agent/conversations/{conversation_id}/model", headers=headers,
                        json={"model": "deepseek-v4.1-flash"},
                    ).status_code)

    def test_five_streamed_turns_preserve_history_across_relay_rotation_and_restart(self):
        requests = []
        request_keys = []

        class MockModel(BaseHTTPRequestHandler):
            def do_POST(self):
                request_keys.append(self.headers.get("x-api-key"))
                requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                frames = [
                    {"type": "message_start", "message": {
                        "id": "local-mock", "model": "tt-5.6-sol",
                        "usage": {"input_tokens": 3, "output_tokens": 0},
                    }},
                    {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
                    {"type": "content_block_delta", "index": 0, "delta": {
                        "type": "text_delta", "text": f"local reply {len(requests)}",
                    }},
                    {"type": "content_block_stop", "index": 0},
                    {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 4}},
                    {"type": "message_stop"},
                ]
                body = "".join(f"event: {frame['type']}\ndata: {json.dumps(frame)}\n\n" for frame in frames).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), MockModel)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        runtime = dsh_routes.dsh_runtime
        runtime_pool = RuntimePool()
        self.addCleanup(runtime_pool.close)
        data_root = Path(self.temp_dir.name) / "runtime-data"
        def short_user_home(root: Path, _owner_id: str) -> Path:
            return data_root / ("home" if root.name == "home" else "workspace")

        settings = {
            "enabled": True, "base_url": f"http://127.0.0.1:{server.server_port}",
            "api_keys": ["local-mock-a", "local-mock-b"], "api_key_pool_distributed": False,
        }
        status = {"enabled": True, "configured": True, "model": "tt-5.6-sol"}

        try:
            with (
                patch.dict(os.environ, {"DSH_AGENT_MODEL": "tt-5.6-sol"}),
                patch.object(dsh_routes, "dsh_agent_store", self.store),
                patch.object(dsh_routes, "require_identity", side_effect=self._identity),
                patch.object(runtime, "public_status", return_value=status),
                patch.object(runtime, "_enabled", return_value=True),
                patch.object(runtime, "_scheduler", Mock(lease_secs=1800)),
                patch.object(runtime, "DATA_DIR", data_root),
                patch.object(runtime, "user_home", side_effect=short_user_home),
                patch.object(runtime, "runtime_pool", runtime_pool),
                patch.object(runtime, "search_available", return_value=False),
                patch.object(runtime.config, "get_openai_relay_settings", return_value=settings),
            ):
                headers = {"Authorization": "Bearer alice"}
                conversation = self.client.post("/api/dsh-agent/conversations", headers=headers).json()
                conversation_id = conversation["conversation_id"]
                prompts = ("remember code polar", "what code?", "repeat the code", "after restart?", "repeat again")
                for turn, prompt in enumerate(prompts, start=1):
                    if turn == 4:
                        runtime_pool.close()
                    response = self.client.post(
                        f"/api/dsh-agent/conversations/{conversation_id}/messages/stream",
                        headers=headers, json={"content": prompt},
                    )
                    self.assertEqual(200, response.status_code)
                    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
                    self.assertNotIn("error", [event["type"] for event in events], events)
                    self.assertEqual("complete", events[-1]["type"], events)
                    self.assertEqual(f"local reply {turn}", events[-1]["message"]["content"])
                    self.assertEqual("idle", self.store.get_conversation(conversation_id)["status"])
                self.assertEqual(5, len(requests))
                self.assertNotEqual(request_keys[0], request_keys[1])
                self.assertEqual(request_keys[0], request_keys[2])
                self.assertIn("remember code polar", json.dumps(requests[1]))
                self.assertIn("local reply 1", json.dumps(requests[1]))
                self.assertIn("what code?", json.dumps(requests[2]))
                self.assertIn("local reply 2", json.dumps(requests[2]))
                self.assertIn("remember code polar", json.dumps(requests[4]))
                self.assertIn("local reply 4", json.dumps(requests[4]))
                self.assertEqual(10, len(self.store.list_messages(conversation_id)["items"]))
                self.assertEqual(5, self.store.admin_usage()["summary"]["assistant_turns"])
        finally:
            server.shutdown()
            server_thread.join(timeout=5)
            server.server_close()


if __name__ == "__main__":
    unittest.main()
