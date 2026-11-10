from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock, patch

from services.dsh_agent import runtime, teams
from services.dsh_agent.sessions import RuntimePool
from services.dsh_agent.team_gateway import GatewayTurn, team_gateway, close_team_gateway
import httpx


class TeamPolicyTests(unittest.TestCase):
    def test_web_budgets_bound_repeats_and_do_not_cross_sessions_or_users(self):
        control = teams.TeamControl("alice", "session-" + "a" * 32)
        for _ in range(2):
            control.check_web("web_fetch", {"url": "https://example.test/page#anchor"}, "child-a")
        control.check_web("web_fetch", {"url": "https://example.test/page"}, "child-b")
        with self.assertRaisesRegex(ValueError, "重复"):
            control.check_web("web_fetch", {"url": "https://example.test/page"}, "child-a")
        other = teams.TeamControl("bob", control.session_id)
        other.check_web("web_fetch", {"url": "https://example.test/page"}, "child-a")
        self.assertFalse(other.failure)

    def test_auth_and_invalid_search_fail_fast_but_temporary_errors_are_bounded(self):
        for error in ("HTTP 401", "HTTP 403", "HTTP 400", "WEB_PROVIDER_CREDENTIAL_MISSING"):
            control = teams.TeamControl("alice", "session-" + "a" * 32)
            control.web_result("child", True, error)
            self.assertTrue(control.failure, error)
        control = teams.TeamControl("alice", "session-" + "a" * 32)
        for error in ("HTTP 429", "HTTP 503"):
            control.web_result("child", True, error)
            self.assertFalse(control.failure)
        control.web_result("child", True, "WEB_FETCH_TIMEOUT")
        self.assertIn("连续失败3次", control.failure)

    def test_search_and_fetch_counts_are_bounded_even_when_every_call_succeeds(self):
        for name, limit in (("web_search", 6), ("web_fetch", 12)):
            control = teams.TeamControl("alice", "session-" + "a" * 32)
            for i in range(limit):
                args = {"queries": [str(i)]} if name == "web_search" else {"url": f"https://example.test/{i}"}
                control.check_web(name, args, "child")
                control.web_result("child", False, "")
            with self.assertRaisesRegex(ValueError, "上限"):
                control.check_web(name, args, "child")

    def test_wrap_up_budget_blocks_new_work_but_allows_reporting(self):
        control = teams.TeamControl("alice", "session-" + "a" * 32, model_calls=teams.MODEL_WRAP_UP_AT)
        with self.assertRaisesRegex(ValueError, "收尾预算"):
            control.check_web("web_search", {"queries": ["new research"]}, "child")
        with self.assertRaisesRegex(ValueError, "收尾预算"):
            control.check("generate_image", {}, captain=True)
        with patch.object(teams, "snapshot", return_value=None):
            control.check("agent_teams_update_task", {"status": "failed"}, captain=False)
            control.check("agent_teams_send_message", {}, captain=False)

    def test_snapshot_projects_transitive_blocked_tasks_without_writing_plugin_state(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(teams, "DATA_DIR", Path(directory)):
            sid = "session-" + "a" * 32
            file = teams.workspace_for("alice", sid) / ".agent-teams" / "example" / "team.json"
            file.parent.mkdir(parents=True)
            state = {"id": "example", "captainSessionId": sid, "phase": "running", "members": [], "tasks": [
                {"id": "t3", "status": "pending", "dependencies": ["t2"]},
                {"id": "t2", "status": "cancelled", "dependencies": ["t1"]},
                {"id": "t1", "status": "failed", "dependencies": []},
            ]}
            file.write_text(json.dumps(state), encoding="utf-8")
            before = file.read_bytes()
            result = teams.snapshot("alice", sid)
            self.assertEqual(["blocked", "blocked", "failed"], [task["status"] for task in result["tasks"]])
            self.assertFalse(teams.executing(result))
            self.assertEqual(before, file.read_bytes())

    def test_user_and_conversation_workspaces_are_separate_and_paths_validated(self):
        sid = "session-" + "a" * 32
        self.assertNotEqual(teams.workspace_for("alice", sid), teams.workspace_for("bob", sid))
        self.assertNotEqual(teams.workspace_for("alice", sid), teams.workspace_for("alice", "session-" + "b" * 32))
        with self.assertRaises(ValueError):
            teams.workspace_for("alice", "../other")

    def test_model_cannot_self_approve_or_run_images_before_confirmation(self):
        state = {"phase": "staged", "revision": "r1", "halted": False, "tasks": []}
        with patch.object(teams, "snapshot", return_value=state):
            control = teams.TeamControl("alice", "session-" + "a" * 32)
            for name, args in [("agent_teams_create", {"approval": "automatic"}), ("agent_teams_approve", {}),
                               ("generate_image", {}), ("agent_teams_resume", {}), ("agent_teams_delete", {}),
                               ("agent_teams_create_task", {"resume": True})]:
                with self.subTest(name=name), self.assertRaises(ValueError):
                    control.check(name, args, captain=True)
            allowed = teams.TeamControl("alice", control.session_id, "approve", "r1")
            with self.assertRaises(ValueError):
                allowed.check("agent_teams_approve", {}, captain=False)
            allowed.check("agent_teams_approve", {}, captain=True)
            with self.assertRaises(ValueError):
                allowed.check("agent_teams_approve", {}, captain=True)
            with self.assertRaises(ValueError):
                teams.TeamControl("alice", control.session_id, "approve", "stale").check("agent_teams_approve", {}, captain=True)


@unittest.skipUnless(os.getenv("DSH_RUNTIME_SMOKE") == "1", "opt-in mock-only full AgentTeams integration")
class TeamIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="raw-photo-team-integration-")
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.sid = "session-" + "c" * 32
        self.requests = []
        self.members_seen = set()
        self.peak = 0
        self.active = 0
        self.lock = threading.Lock()
        self.slow_members = False
        self.search_members = False
        self.search_status = 200
        self.search_requests = []
        self.failed_member = False
        self.dependent_plan = False
        self.release_requests = threading.Event()
        self.addCleanup(self.release_requests.set)
        test = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path == "/search":
                    test.search_requests.append({"body": body, "auth": self.headers.get("Authorization")})
                    data = json.dumps({"results": [{"url": "https://example.test/result", "title": "Mock source", "snippet": "Verified mock evidence", "date": None}]} if test.search_status == 200 else {"error": "Mock search unavailable"}).encode()
                    self.send_response(test.search_status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                with test.lock:
                    test.requests.append(body)
                gemini = self.path.startswith("/v1beta/")
                text = json.dumps(body.get("messages", body), ensure_ascii=False)
                state = teams.snapshot("alice", test.sid)
                member = next((name for name in ("visual", "market") if f"You are {name}, a member" in text), None)
                tool = None
                args = {}
                if member:
                    test.members_seen.add(member)
                    if test.slow_members:
                        test.release_requests.wait(10)
                    task = next(task for task in state["tasks"] if task["assignee"] == member)
                    task_state = json.loads(next(teams.workspace_for("alice", test.sid).glob(".agent-teams/*/team.json")).read_text(encoding="utf-8"))
                    saved = next(row for row in task_state["tasks"] if row["id"] == task["id"])
                    if saved["status"] == "claimed":
                        tool, args = "agent_teams_update_task", {"task_id": saved["id"], "attempt_id": saved["attemptId"], "status": "in_progress"}
                    elif saved["status"] == "in_progress":
                        if test.search_members and member == "visual" and "Verified mock evidence" not in text:
                            tool, args = "web_search", {"queries": ["mock product evidence" + (str(len(test.search_requests)) if test.search_status != 200 else "")]}
                        else:
                            tool, args = "agent_teams_update_task", {"task_id": saved["id"], "attempt_id": saved["attemptId"], "status": "failed" if test.failed_member and member == "visual" else "completed", "output": f"{member} verified result"}
                elif state is None:
                    tool = "agent_teams_create"
                    args = {"name": "product-team", "approval": "required", "description": "Mock-only collaboration", "plan": {
                        "members": [{"name": "visual"}, {"name": "market"}],
                        "tasks": [{"id": "visual-task", "subject": "Describe product", "assignee": "visual"},
                                  {"id": "market-task", "subject": "Check audience", "assignee": "market", "dependencies": ["visual-task"] if test.dependent_plan else []}],
                    }}
                elif state["phase"] == "staged" and "APPROVE-MOCK" in text:
                    tool, args = "agent_teams_approve", {"confirmation": "APPROVE-MOCK"}
                delta = {"role": "assistant", "content": "Member result" if member else "请确认计划" if state and state["phase"] == "staged" else "协作结论"}
                if tool:
                    delta = {"role": "assistant", "tool_calls": [{"index": 0, "id": "call-" + str(len(test.requests)), "type": "function", "function": {"name": tool, "arguments": json.dumps(args)}}]}
                if gemini:
                    frames = [{"candidates": [{"content": {"role": "model", "parts": [{"functionCall": {"name": tool, "args": args}}] if tool else [{"text": delta["content"]}]}, "finishReason": "STOP", "index": 0}],
                               "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 1, "totalTokenCount": 4}, "modelVersion": "gem-3.8-flash"}]
                else:
                    frames = [
                        {"id": "mock", "object": "chat.completion.chunk", "model": body["model"], "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                        {"id": "mock", "object": "chat.completion.chunk", "model": body["model"], "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls" if tool else "stop"}], "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}},
                    ]
                data = ("".join(f"data: {json.dumps(frame)}\n\n" for frame in frames) + ("" if gemini else "data: [DONE]\n\n")).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("X-Task-Id", f"mock-{len(test.requests)}")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 3)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.relay = {"enabled": True, "base_url": f"http://127.0.0.1:{server.server_port}/v1", "api_key": "mock-only"}
        self.search_url = f"http://127.0.0.1:{server.server_port}"
        p = patch.object(runtime, "search_available", return_value=True)
        p.start()
        self.addCleanup(p.stop)
        p = patch.object(runtime, "bridge_settings", return_value={"base_url": self.search_url, "api_key": "mock-search-only"})
        p.start()
        self.addCleanup(p.stop)
        for target, value in [("DATA_DIR", root)]:
            p = patch.object(teams, target, value)
            p.start()
            self.addCleanup(p.stop)
            p = patch.object(runtime, target, value)
            p.start()
            self.addCleanup(p.stop)
        self.pool = RuntimePool()
        p = patch.object(runtime, "runtime_pool", self.pool)
        p.start()
        self.addCleanup(p.stop)
        p = patch.object(runtime.config, "get_openai_relay_settings", return_value=self.relay)
        p.start()
        self.addCleanup(p.stop)
        self.gateway = team_gateway()
        p = patch.object(self.gateway, "scheduler", Mock())
        p.start()
        self.addCleanup(p.stop)
        p = patch("services.dsh_agent.team_gateway.capture_chat_response")
        self.capture = p.start()
        self.addCleanup(p.stop)
        self.addCleanup(close_team_gateway)
        self.addCleanup(self.pool.close)

    def execute(self, prompt, **extra):
        cancel = extra.pop("cancel_event", threading.Event())
        model = extra.pop("model", "tt-6-sol")
        return runtime._execute(prompt, model=model, owner_id="alice", dsh_session_id=self.sid,
                                has_history=bool(self.requests), history=[], on_notification=lambda _notice: None,
                                team_mode=True, cancel_event=cancel, **extra)

    def test_web_runtime_plans_then_approved_members_execute_and_tokens_are_aggregated(self):
        self.check_roundtrip("tt-6-sol")

    def test_default_model_uses_the_relays_chat_protocol_and_attributed_gateway(self):
        self.check_roundtrip("tt-5.6-sol")

    def test_selected_deepseek_flash_model_uses_the_same_attributed_gateway(self):
        self.check_roundtrip("deepseek-v4.1-flash")

    def test_gemini_native_protocol_uses_the_attributed_gateway(self):
        self.check_roundtrip("gem-3.8-flash")

    def test_actual_member_search_uses_the_project_bridge_not_deepseek_defaults(self):
        self.search_members = True
        self.check_roundtrip("tt-6-sol")
        self.assertEqual(1, len(self.search_requests))
        self.assertEqual("Bearer mock-search-only", self.search_requests[0]["auth"])
        self.assertEqual("mock product evidence", self.search_requests[0]["body"]["query"])

    def test_search_401_stops_without_retry_or_starting_the_dependent_member(self):
        self.search_members = self.dependent_plan = True
        self.search_status = 401
        self.execute("Plan only")
        plan = teams.snapshot("alice", self.sid)
        with self.assertRaisesRegex(teams.TeamExecutionError, "鉴权"):
            self.execute("APPROVE-MOCK", team_action="approve", team_revision=plan["revision"])
        self.assertEqual(1, len(self.search_requests))
        self.assertEqual({"visual"}, self.members_seen)
        self.assertTrue(teams.snapshot("alice", self.sid)["halted"])
        self.assertEqual({}, self.gateway.contexts)
        self.assertEqual({}, self.pool._entries)

    def test_temporary_search_failure_stops_after_three_attempts(self):
        self.search_members = self.dependent_plan = True
        self.search_status = 503
        self.execute("Plan only")
        plan = teams.snapshot("alice", self.sid)
        with self.assertRaises(teams.TeamExecutionError):
            self.execute("APPROVE-MOCK", team_action="approve", team_revision=plan["revision"])
        self.assertEqual(3, len(self.search_requests))
        self.assertNotIn("market", self.members_seen)

    def test_failed_dependency_ends_the_run_and_projects_blocked_instead_of_waiting(self):
        self.failed_member = self.dependent_plan = True
        self.execute("Plan only")
        plan = teams.snapshot("alice", self.sid)
        with self.assertRaisesRegex(teams.TeamExecutionError, "前置任务失败"):
            self.execute("APPROVE-MOCK", team_action="approve", team_revision=plan["revision"])
        final = teams.snapshot("alice", self.sid)
        self.assertEqual(["failed", "blocked"], [task["status"] for task in final["tasks"]])
        self.assertEqual({"visual"}, self.members_seen)
        self.assertTrue(final["halted"])

    def test_gateway_reserves_wrapup_calls_and_never_retries_upstream_requests(self):
        control = teams.TeamControl("alice", self.sid)
        turn = GatewayTurn(control, self.relay["base_url"], "mock-only", "tt-6-sol", "mock-task", threading.Event(), calls=teams.MODEL_WRAP_UP_AT - 1)
        with self.gateway.activate(turn):
            settings = self.gateway.settings("alice", self.sid)
            headers = {"Authorization": f"Bearer {settings['DSH_TEAMS_TOKEN']}"}
            body = {"model": "tt-6-sol", "messages": [{"role": "user", "content": "wrap up"}]}
            with httpx.Client(trust_env=False) as client:
                response = client.post(settings["DSH_TEAMS_GATEWAY"] + "/v1/chat/completions", headers=headers, json=body)
                self.assertEqual(200, response.status_code)
                self.assertEqual(teams.WRAP_UP_PROMPT, self.requests[-1]["messages"][-1]["content"])
                self.assertEqual(1, len(self.requests))
                turn.calls = teams.MODEL_REQUEST_LIMIT
                response = client.post(settings["DSH_TEAMS_GATEWAY"] + "/v1/chat/completions", headers=headers, json=body)
                self.assertEqual(400, response.status_code)
                self.assertEqual(1, len(self.requests))
                self.assertTrue(control.failure)

    def check_roundtrip(self, model):
        result = self.execute("Plan a product task, do not execute", model=model)
        self.assertEqual("completed", result["finish_reason"], result.get("failure"))
        self.assertEqual(2, len(self.requests))
        self.assertEqual(set(), self.members_seen)
        plan = teams.snapshot("alice", self.sid)
        self.assertEqual("staged", plan["phase"])
        self.assertEqual(2, len(plan["tasks"]))
        result = self.execute("APPROVE-MOCK", team_action="approve", team_revision=plan["revision"], model=model)
        final = teams.snapshot("alice", self.sid)
        self.assertEqual({"visual", "market"}, self.members_seen)
        self.assertTrue(all(task["status"] == "completed" for task in final["tasks"]), final)
        self.assertGreaterEqual(result["usage"]["totalTokens"], 20)
        self.assertEqual(len(self.requests), self.capture.call_count)
        self.assertEqual({}, self.gateway.contexts)

    def test_stop_halts_the_persisted_team_and_children_and_releases_runtime(self):
        import time

        self.execute("Plan only")
        plan = teams.snapshot("alice", self.sid)
        self.slow_members = True
        cancel = threading.Event()
        failures = []

        def execute():
            try:
                self.execute("APPROVE-MOCK", team_action="approve", team_revision=plan["revision"], cancel_event=cancel)
            except Exception as exc:
                failures.append(exc)

        thread = threading.Thread(target=execute)
        thread.start()
        deadline = time.monotonic() + 12
        while not self.members_seen and time.monotonic() < deadline:
            time.sleep(0.05)
        cancel.set()
        self.release_requests.set()
        thread.join(15)
        self.assertFalse(thread.is_alive(), "stop must drain the runtime")
        self.assertTrue(any(isinstance(exc, runtime.DshAgentStopped) for exc in failures), failures)
        team = teams.snapshot("alice", self.sid)
        self.assertTrue(team["halted"], team)
        self.assertTrue(all(task["status"] in teams.TERMINAL for task in team["tasks"]), team)
        self.assertEqual({}, self.pool._entries)
        self.assertEqual({}, self.gateway.contexts)


if __name__ == "__main__":
    unittest.main()
