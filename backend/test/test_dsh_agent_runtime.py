from __future__ import annotations

import tempfile
import unittest
import os
import json
import base64
import io
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

from deepseek_harness.errors import JsonRpcError

from services.dsh_agent import runtime
from services.dsh_agent.sessions import RuntimePool
from services.dsh_agent.models import MODELS, context_budget, relay_providers, resolve_model


class DshAgentRuntimeDiagnosticsTests(unittest.TestCase):
    def test_context_budgets_reserve_output_and_use_per_model_deployment_limits(self):
        with patch.dict(os.environ, {"DSH_AGENT_CONTEXT_WINDOW": "128000", "DSH_AGENT_MODEL_CONTEXT_WINDOWS": '{"gem-3.8-flash":32768}'}):
            default = context_budget("tt-6-sol")
            smaller = context_budget("gem-3.8-flash")
            self.assertEqual(102400, default["compact_at_tokens"])
            self.assertEqual("deployment", default["limit_source"])
            self.assertEqual(16384, smaller["compact_at_tokens"])
            self.assertLess(smaller["compact_at_tokens"] + smaller["max_output_tokens"], smaller["context_window"])
            providers = relay_providers("https://mock.invalid")
            self.assertEqual(32768, next(item for item in providers["google"]["models"] if item["id"] == "gem-3.8-flash")["contextWindow"])
        for overrides in ('[]', '{"unknown":128000}', '{"tt-6-sol":true}', '{"tt-6-sol":1000}'):
            with patch.dict(os.environ, {"DSH_AGENT_MODEL_CONTEXT_WINDOWS": overrides}):
                with self.assertRaises(ValueError):
                    context_budget("tt-6-sol")

    def test_cancel_closes_only_borrowed_runtime_without_repeating_prompt(self):
        with tempfile.TemporaryDirectory() as temp:
            pool = RuntimePool()
            self.addCleanup(pool.close)
            cancelled = threading.Event()
            closed = threading.Event()
            harness = Mock()
            harness.close.side_effect = closed.set

            def run(_prompt, **_kwargs):
                cancelled.set()
                self.assertTrue(closed.wait(2), "SDK runtime was not stopped")
                raise RuntimeError("transport closed after stop")

            harness.start_session.return_value.run.side_effect = run
            with (
                patch.object(runtime, "DATA_DIR", Path(temp)),
                patch.object(runtime.config, "get_openai_relay_settings", return_value={"base_url": "https://mock.invalid", "api_key": "mock"}),
                patch.object(runtime, "current_relay_account", return_value=None),
                patch.object(runtime, "runtime_pool", pool),
                patch.object(runtime, "search_available", return_value=False),
                patch("deepseek_harness.DeepSeekHarness", return_value=harness),
            ):
                with self.assertRaises(runtime.DshAgentStopped):
                    runtime._execute("test", owner_id="alice", dsh_session_id="session-" + "a" * 32, has_history=False,
                                     history=[], on_notification=lambda _: None, cancel_event=cancelled)
            harness.start_session.return_value.run.assert_called_once()
            self.assertEqual({}, pool._entries)

    def test_cancelled_queue_does_not_call_model_or_rotate_keys(self):
        cancelled = threading.Event()
        cancelled.set()
        with (
            patch.object(runtime, "_enabled", return_value=True),
            patch.object(runtime.config, "get_openai_relay_settings", return_value={"enabled": True, "base_url": "https://mock.invalid"}),
            patch.object(runtime._scheduler, "acquire", side_effect=runtime.CanvasAISchedulerError("stopped")),
            patch.object(runtime, "run_with_relay_pool") as relay,
        ):
            with self.assertRaises(runtime.DshAgentStopped):
                runtime.run_turn("test", owner_id="alice", dsh_session_id="session-" + "a" * 32,
                                 has_history=False, history=[], on_notification=lambda _: None, cancel_event=cancelled)
            relay.assert_not_called()

    def test_cancelled_queue_removes_waiting_slot_without_admitting(self):
        cancelled = threading.Event()
        cancelled.set()
        scheduler = type(runtime._scheduler)(namespace="mock-dsh-stop")
        waiting, admit, remove = Mock(return_value=1), Mock(), Mock()
        with patch.object(scheduler, "_script", side_effect=lambda name, _source: {"waiting": waiting, "admit": admit, "remove_waiting": remove}[name]):
            with self.assertRaises(runtime.CanvasAISchedulerError):
                scheduler.acquire("alice", cancel_event=cancelled)
        waiting.assert_called_once()
        admit.assert_not_called()
        remove.assert_called_once()

    def test_model_registry_and_relay_paths_follow_documented_protocols(self):
        self.assertEqual(7, len(MODELS))
        with self.assertRaises(ValueError):
            resolve_model("not-allowed")
        for base in ("https://relay.example", "https://relay.example/v1", "https://relay.example/v1beta/"):
            providers = relay_providers(base)
            self.assertEqual("https://relay.example/v1", providers["lingke-openai"]["baseURL"])
            self.assertEqual("https://relay.example/v1beta", providers["google"]["baseURL"])
            self.assertEqual("openai-completions", providers["lingke-openai"]["api"])
            self.assertEqual(2, len(providers["google"]["models"]))
            for provider in providers.values():
                self.assertNotIn("apiKey", provider)
                self.assertEqual("DSH_AGENT_RELAY_API_KEY", provider["apiKeyEnv"])

    def test_runtime_environment_scrubs_parent_secrets_and_isolates_user_home(self):
        home = Path("isolated-home")
        with patch.dict(os.environ, {"PATH": "runtime-path", "MYSQL_PASSWORD": "secret", "HOME": "shared-home"}, clear=True):
            env = runtime._runtime_environment(home, "test prompt")

        self.assertEqual("", env["MYSQL_PASSWORD"])
        self.assertEqual("runtime-path", env["PATH"])
        self.assertEqual(str(home.resolve()), env["HOME"])
        self.assertEqual(str(home.resolve()), env["USERPROFILE"])
        self.assertEqual("test prompt", env["DSH_SYSTEM_PROMPT"])

    def test_turn_failure_extracts_provider_reason_without_credentials_or_url_query(self):
        failure = runtime._turn_failure([{
            "type": "turn/end",
            "data": {"reason": {"kind": "error", "error": {
                "code": "UPSTREAM_400",
                "message": "request failed for https://relay.example/v1/chat?token=secret Bearer abc123 api_key=key123",
            }}},
        }])

        self.assertEqual("UPSTREAM_400", failure["code"])
        self.assertIn("https://relay.example/v1/chat", failure["message"])
        self.assertNotIn("secret", failure["message"])
        self.assertNotIn("abc123", failure["message"])
        self.assertNotIn("key123", failure["message"])

    def _run_with_failure(self, phase: str) -> runtime.DshAgentExecutionError:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        settings = {"enabled": True, "base_url": "https://relay.example/v1", "api_key": "test-key"}
        pool = RuntimePool()
        self.addCleanup(pool.close)

        with (
            patch.object(runtime, "DATA_DIR", Path(temp_dir.name)),
            patch.object(runtime.config, "get_openai_relay_settings", return_value=settings),
            patch.object(runtime, "current_relay_account", return_value=None),
            patch.object(runtime, "runtime_pool", pool),
            patch.object(runtime, "search_available", return_value=False),
            patch("deepseek_harness.DeepSeekHarness") as harness_factory,
        ):
            harness = harness_factory.return_value
            if phase == "restore_session":
                harness.resume_session.side_effect = JsonRpcError(404, "session storage unavailable")
            else:
                harness.start_session.return_value.run.side_effect = JsonRpcError(429, "provider busy")

            with self.assertRaises(runtime.DshAgentExecutionError) as raised:
                runtime._execute(
                    "test prompt",
                    owner_id="user-1",
                    dsh_session_id=f"session-{'a' * 32}",
                    has_history=phase == "restore_session",
                    history=[],
                    on_notification=lambda _notice: None,
                )
            if phase == "restore_session":
                harness.start_session.assert_not_called()

        return raised.exception

    def test_non_missing_resume_error_does_not_start_a_new_session(self):
        error = self._run_with_failure("restore_session")

        self.assertEqual("restore_session", error.phase)
        self.assertEqual("JsonRpcError", error.cause_type)
        self.assertEqual(404, error.rpc_code)
        self.assertNotIn("storage unavailable", str(error))

    def test_missing_session_falls_back_with_database_history(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        settings = {"enabled": True, "base_url": "https://relay.example/v1", "api_key": "test-key"}
        history = [
            {"role": "user", "content": "我叫小林"},
            {"role": "assistant", "content": "你好，小林。"},
        ]
        pool = RuntimePool()
        self.addCleanup(pool.close)

        with (
            patch.object(runtime, "DATA_DIR", Path(temp_dir.name)),
            patch.object(runtime.config, "get_openai_relay_settings", return_value=settings),
            patch.object(runtime, "current_relay_account", return_value=None),
            patch.object(runtime, "runtime_pool", pool),
            patch.object(runtime, "search_available", return_value=False),
            patch("deepseek_harness.DeepSeekHarness") as harness_factory,
        ):
            harness = harness_factory.return_value
            harness.resume_session.side_effect = JsonRpcError(-32603, f'session "session-{"b" * 32}" not found')
            result = harness.start_session.return_value.run.return_value
            result.final_response = "你刚才说你叫小林。"
            result.finish_reason = "completed"
            result.events = []

            response = runtime._execute(
                "我叫什么？",
                owner_id="user-1",
                dsh_session_id=f"session-{'b' * 32}",
                has_history=True,
                history=history,
                on_notification=lambda _notice: None,
            )

        harness.resume_session.assert_called_once()
        harness.start_session.assert_called_once_with(f"session-{'b' * 32}")
        sent_prompt = harness.start_session.return_value.run.call_args.args[0]
        self.assertIn("我叫小林", sent_prompt)
        self.assertIn("你好，小林。", sent_prompt)
        self.assertIn("当前用户消息：\n我叫什么？", sent_prompt)
        self.assertEqual("你刚才说你叫小林。", response["content"])

    def test_existing_session_sends_only_the_current_prompt(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(runtime, "DATA_DIR", Path(directory)),
            patch.object(runtime.config, "get_openai_relay_settings", return_value={}),
            patch.object(runtime, "current_relay_account", return_value=None),
            patch.object(runtime, "runtime_pool", pool := RuntimePool()),
            patch.object(runtime, "search_available", return_value=False),
            patch("deepseek_harness.DeepSeekHarness") as harness_factory,
        ):
            self.addCleanup(pool.close)
            harness = harness_factory.return_value
            result = harness.resume_session.return_value.run.return_value
            result.final_response = "reply"
            result.finish_reason = "completed"
            result.events = [
                {"type": "assistant/message", "data": {"usage": {"totalTokens": 10}}},
                {"type": "compaction/summary", "data": {"usage": {"totalTokens": 7}}},
                {"type": "plugin:raw-photo/context", "data": {"used_tokens": 999}},
            ]
            response = runtime._execute(
                "current question", owner_id="user-1", dsh_session_id=f"session-{'a' * 32}", has_history=True,
                history=[{"role": "user", "content": "previous question"}],
                on_notification=lambda _notice: None,
            )
            harness.start_session.assert_not_called()
            self.assertEqual("current question", harness.resume_session.return_value.run.call_args.args[0])
            self.assertEqual({"totalTokens": 17}, response["usage"])

    def test_runtime_and_session_are_reused_for_followup_turns(self):
        with tempfile.TemporaryDirectory() as directory:
            pool = RuntimePool()
            self.addCleanup(pool.close)
            with (
                patch.object(runtime, "DATA_DIR", Path(directory)),
                patch.object(runtime.config, "get_openai_relay_settings", return_value={}),
                patch.object(runtime, "current_relay_account", return_value=None),
                patch.object(runtime, "runtime_pool", pool),
                patch.object(runtime, "search_available", return_value=False),
                patch("deepseek_harness.DeepSeekHarness") as harness_factory,
            ):
                harness = harness_factory.return_value
                harness.start_session.return_value.run.return_value.final_response = "reply"
                harness.start_session.return_value.run.return_value.finish_reason = "completed"
                harness.start_session.return_value.run.return_value.events = []
                for prompt in ("first", "follow-up"):
                    runtime._execute(
                        prompt,
                        owner_id="user-1",
                        dsh_session_id=f"session-{'c' * 32}",
                        has_history=prompt == "follow-up",
                        history=[],
                        on_notification=lambda _notice: None,
                    )
                harness_factory.assert_called_once()
                harness.start.assert_called_once()
                harness.start_session.assert_called_once_with(f"session-{'c' * 32}")
                harness.resume_session.assert_not_called()
                self.assertEqual(2, harness.start_session.return_value.run.call_count)

    def test_expert_switch_rebuilds_policy_without_reusing_another_user_runtime(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(runtime, "DATA_DIR", Path(directory)),
            patch.object(runtime.config, "get_openai_relay_settings", return_value={}),
            patch.object(runtime, "current_relay_account", return_value=None),
            patch.object(runtime, "runtime_pool", pool := RuntimePool()),
            patch.object(runtime, "search_available", return_value=False),
            patch("deepseek_harness.DeepSeekHarness") as factory,
        ):
            self.addCleanup(pool.close)
            def create(**_kwargs):
                harness = Mock()
                for session in (harness.start_session.return_value, harness.resume_session.return_value):
                    session.run.return_value.final_response = "reply"
                    session.run.return_value.finish_reason = "completed"
                    session.run.return_value.events = []
                return harness
            factory.side_effect = create
            session_id = "session-" + "d" * 32
            for owner, expert in (("alice", ""), ("alice", "ecommerce-design"), ("alice", "ecommerce-design"), ("bob", ""), ("alice", "")):
                runtime._execute("advice", owner_id=owner, expert_id=expert, dsh_session_id=session_id, has_history=True, history=[], on_notification=lambda _: None)
            self.assertEqual(4, factory.call_count)
            prompts = [call.kwargs["env"]["DSH_SYSTEM_PROMPT"] for call in factory.call_args_list]
            self.assertIn("当前未选择专家", prompts[0])
            self.assertIn("identity_constraints", prompts[1])
            self.assertNotIn("identity_constraints", prompts[2])
            self.assertIn("当前未选择专家", prompts[3])
            self.assertEqual(2, len(pool._entries))

    def test_prompt_error_records_phase_and_rpc_code(self):
        error = self._run_with_failure("send_prompt")

        self.assertEqual("send_prompt", error.phase)
        self.assertEqual(429, error.rpc_code)

    def test_skill_switch_updates_policy_and_never_reuses_another_users_runtime(self):
        from services.dsh_agent.skills import STYLE_SKILL

        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(runtime, "DATA_DIR", Path(directory)),
            patch.object(runtime.config, "get_openai_relay_settings", return_value={}),
            patch.object(runtime, "current_relay_account", return_value=None),
            patch.object(runtime, "runtime_pool", pool := RuntimePool()),
            patch.object(runtime, "search_available", return_value=False),
            patch("deepseek_harness.DeepSeekHarness") as factory,
        ):
            self.addCleanup(pool.close)
            def create(**_kwargs):
                harness = Mock()
                for session in (harness.start_session.return_value, harness.resume_session.return_value):
                    session.run.return_value.final_response = "reply"
                    session.run.return_value.finish_reason = "completed"
                    session.run.return_value.events = []
                return harness
            factory.side_effect = create
            for owner, skills in (("alice", []), ("alice", [STYLE_SKILL]), ("alice", [STYLE_SKILL]), ("bob", []), ("alice", [])):
                runtime._execute("advice", owner_id=owner, skill_ids=skills, dsh_session_id="session-" + "e" * 32,
                                 has_history=True, history=[], on_notification=lambda _: None)
            self.assertEqual(4, factory.call_count)
            prompts = [call.kwargs["env"]["DSH_SYSTEM_PROMPT"] for call in factory.call_args_list]
            self.assertIn("本轮未授权 GPT Image 设计库", prompts[0])
            self.assertIn("本轮已授权技能", prompts[1])
            self.assertIn("本轮未授权 GPT Image 设计库", prompts[2])
            self.assertIn("本轮未授权 GPT Image 设计库", prompts[3])


class DshRuntimePoolTests(unittest.TestCase):
    def setUp(self):
        self.pool = RuntimePool()
        self.addCleanup(self.pool.close)

    def test_rotating_keys_closes_old_runtime_before_resuming_and_never_reuses_it(self):
        first, second, third = Mock(), Mock(), Mock()
        for fingerprint, harness in (("key-a", first), ("key-b", second), ("key-a", third)):
            with self.pool.borrow(("alice", "session", fingerprint), lambda: harness, limit=1, idle_secs=180) as entry:
                self.assertIs(harness, entry.harness)
                if harness is second:
                    first.close.assert_called_once()
                if harness is third:
                    second.close.assert_called_once()

    def test_same_session_is_exclusive_even_when_relay_key_changes(self):
        factory = Mock()
        with self.pool.borrow(("alice", "session", "key-a"), Mock, limit=8, idle_secs=180):
            with self.assertRaisesRegex(RuntimeError, "already processing"):
                with self.pool.borrow(("alice", "session", "key-b"), factory, limit=8, idle_secs=180):
                    self.fail("a second runtime was allowed to own the session")
        factory.assert_not_called()

    def test_other_owners_and_other_sessions_are_not_evicted(self):
        first = Mock()
        with self.pool.borrow(("alice", "session", "key-a"), lambda: first, limit=8, idle_secs=180):
            pass
        for key in (("bob", "session", "key-b"), ("alice", "other-session", "key-b")):
            with self.pool.borrow(key, Mock, limit=8, idle_secs=180):
                first.close.assert_not_called()

    def test_failed_factory_does_not_drop_the_previous_runtime(self):
        first = Mock()
        key = ("alice", "session", "key-a")
        with self.pool.borrow(key, lambda: first, limit=1, idle_secs=180):
            pass
        with self.assertRaisesRegex(RuntimeError, "construction failed"):
            with self.pool.borrow(key[:2] + ("key-b",), Mock(side_effect=RuntimeError("construction failed")), limit=1, idle_secs=180):
                self.fail("factory failure was ignored")
        with self.pool.borrow(key, Mock, limit=1, idle_secs=180) as entry:
            self.assertIs(first, entry.harness)
            first.close.assert_not_called()


@unittest.skipUnless(os.getenv("DSH_RUNTIME_SMOKE") == "1", "opt-in installed DSH runtime smoke test")
class DshInstalledRuntimeTests(unittest.TestCase):
    def test_context_meter_compacts_before_smaller_model_request_and_survives_restart(self):
        requests = []
        summaries = []
        notices = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                messages = json.dumps(payload["messages"])
                summarizing = "You are now acting as a compaction engine" in str(payload["messages"][-1].get("content"))
                (summaries if summarizing else requests).append(payload)
                text = "Confirmed identity: product-A; reference ref-A; pending task task-A; never resubmit task-A." if summarizing else "reply " * 2200
                frames = [
                    {"id": "mock", "object": "chat.completion.chunk", "model": payload["model"], "choices": [{"index": 0, "delta": {"role": "assistant", "content": text}, "finish_reason": None}]},
                    {"id": "mock", "object": "chat.completion.chunk", "model": payload["model"], "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}], "usage": {"prompt_tokens": len(messages) // 4, "completion_tokens": len(text) // 4, "total_tokens": (len(messages) + len(text)) // 4}},
                ]
                body = ("".join(f"data: {json.dumps(frame)}\n\n" for frame in frames) + "data: [DONE]\n\n").encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 3)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(os.environ, {"DSH_AGENT_CONTEXT_WINDOW": "65536", "DSH_AGENT_MODEL_CONTEXT_WINDOWS": '{"tt-6.1-sol":32768}'}),
            patch.object(runtime, "DATA_DIR", Path(directory)),
            patch.object(runtime.config, "get_openai_relay_settings", return_value={"base_url": f"http://127.0.0.1:{server.server_port}", "api_key": "mock-only"}),
            patch.object(runtime, "current_relay_account", return_value=None),
            patch.object(runtime, "runtime_pool", pool := RuntimePool()),
            patch.object(runtime, "search_available", return_value=False),
        ):
            try:
                for index in range(5):
                    if index == 4:
                        pool.close()
                    model = "tt-6-sol" if index < 3 else "tt-6.1-sol"
                    response = runtime._execute(
                        f"Round {index}. product-A ref-A task-A: " + "confirmed-fact " * (800 if index < 3 else 1),
                        owner_id="context-smoke", dsh_session_id="session-" + "c" * 32,
                        model=model, has_history=index > 0, history=[], on_notification=notices.append,
                    )
                    self.assertTrue(response["content"].startswith("reply"), response)
                self.assertEqual(5, len(requests), "compaction must not repeat normal generation requests")
                self.assertTrue(summaries, "smaller-model switch did not compact history")
                self.assertTrue(all(item.get("max_completion_tokens", item.get("max_tokens")) == 4096 for item in summaries))
                self.assertIn("compacted-summary", json.dumps(requests[-1]))
                self.assertIn("product-A", json.dumps(requests[-1]))
                snapshots = [notice.payload["event"]["data"] for notice in notices if notice.method == "session.event" and notice.payload.get("event", {}).get("type") == "plugin:raw-photo/context"]
                self.assertTrue(snapshots, "context plugin did not report native measurements")
                self.assertEqual(32768, snapshots[-1]["context_window"])
                self.assertEqual("tt-6.1-sol", snapshots[-1]["model"])
                self.assertGreater(snapshots[-1]["used_tokens"], 0)
                self.assertTrue(any(item["compacted"] for item in snapshots))
            finally:
                pool.close()

    def test_real_runtime_stop_can_reopen_session_without_resubmitting(self):
        requests = []
        release = threading.Event()
        cancelled = threading.Event()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append(payload)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                text = "partial-before-stop" if len(requests) == 1 else "continued-answer"
                chunks = [{"id": "mock", "object": "chat.completion.chunk", "model": payload["model"], "choices": [{"index": 0, "delta": {"role": "assistant", "content": text}, "finish_reason": None}]}]
                if len(requests) > 1:
                    chunks.append({"id": "mock", "object": "chat.completion.chunk", "model": payload["model"], "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
                self.wfile.write("".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks).encode())
                self.wfile.flush()
                if len(requests) == 1:
                    cancelled.set()
                    release.wait(15)
                else:
                    self.wfile.write(b"data: [DONE]\n\n")

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        session_id = "session-" + "c" * 32
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(runtime, "DATA_DIR", Path(directory)),
            patch.object(runtime.config, "get_openai_relay_settings", return_value={"base_url": f"http://127.0.0.1:{server.server_port}/v1", "api_key": "mock-only"}),
            patch.object(runtime, "current_relay_account", return_value=None),
            patch.object(runtime, "runtime_pool", pool := RuntimePool()),
            patch.object(runtime, "search_available", return_value=False),
        ):
            try:
                with self.assertRaises(runtime.DshAgentStopped):
                    runtime._execute("question", model="tt-6-sol", owner_id="stop-user", dsh_session_id=session_id,
                                     has_history=False, history=[], on_notification=lambda _: None, cancel_event=cancelled)
                release.set()
                self.assertEqual(1, len(requests))
                self.assertEqual({}, pool._entries)
                result = runtime._execute("continue", model="tt-6-sol", owner_id="stop-user", dsh_session_id=session_id,
                                          has_history=True, history=[], on_notification=lambda _: None)
                self.assertEqual("continued-answer", result["content"])
                self.assertEqual(2, len(requests))
            finally:
                release.set()
                pool.close()

    def test_six_models_use_native_protocols_and_preserve_history_when_switching(self):
        requests = []
        searches = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path == "/search":
                    searches.append(payload)
                    body = json.dumps({"results": [{"title": "Mock source", "url": "https://example.com/mock", "snippet": "mock-search-result", "date": None}]}).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if "/models/" not in self.path and self.path != "/v1/chat/completions":
                    self.send_error(400, "Unexpected mock route")
                    return
                requests.append({"path": self.path, "payload": payload, "headers": {key.lower(): value for key, value in self.headers.items()}})
                count = len(requests)
                text = f"reply-{(count + 1) // 2}"
                call_tool = count % 2 == 1
                if "/models/" in self.path:
                    frames = [{
                        "candidates": [{"content": {"parts": [{"functionCall": {"name": "web_search", "args": {"queries": ["test"]}}}] if call_tool else [{"text": text}], "role": "model"}, "finishReason": "STOP", "index": 0}],
                        "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 1, "totalTokenCount": 4},
                        "modelVersion": self.path.split("/models/", 1)[1].split(":", 1)[0],
                    }]
                else:
                    delta = {"role": "assistant", "tool_calls": [{"index": 0, "id": f"call-{count}", "type": "function", "function": {"name": "web_search", "arguments": json.dumps({"queries": ["test"]})}}]} if call_tool else {"role": "assistant", "content": text}
                    frames = [
                        {"id": f"mock-{count}", "object": "chat.completion.chunk", "model": payload["model"], "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                        {"id": f"mock-{count}", "object": "chat.completion.chunk", "model": payload["model"], "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls" if call_tool else "stop"}], "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}},
                    ]
                body = "".join(f"data: {json.dumps(frame)}\n\n" for frame in frames).encode()
                if "/models/" not in self.path:
                    body += b"data: [DONE]\n\n"
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 3)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        settings = {"base_url": f"http://127.0.0.1:{server.server_port}/v1", "api_key": "mock-only"}
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(runtime, "DATA_DIR", Path(directory)),
            patch.object(runtime.config, "get_openai_relay_settings", return_value=settings),
            patch.object(runtime, "current_relay_account", return_value=None),
            patch.object(runtime, "runtime_pool", pool := RuntimePool()),
            patch.object(runtime, "search_available", return_value=True),
            patch.object(runtime, "bridge_settings", return_value={"base_url": f"http://127.0.0.1:{server.server_port}", "api_key": "mock-search-key"}),
        ):
            try:
                models = [item for item in MODELS if item["id"] != "tt-5.6-sol"]
                for index, model in enumerate(models + models[:1], start=1):
                    if index == len(models) + 1:
                        pool.close()
                    response = runtime._execute(
                        f"question-{index}", owner_id="model-user", dsh_session_id=f"session-{'f' * 32}",
                        model=model["id"], has_history=index > 1, history=[], on_notification=lambda _notice: None,
                    )
                    self.assertEqual(f"reply-{index}", response["content"], response)
                    self.assertEqual(model["id"], response["model"])
                    self.assertEqual(8, response["usage"].get("totalTokens"))
                    self.assertEqual(index * 2, len(requests))
                    self.assertEqual(index, len(searches))
                    request = requests[-1]
                    self.assertIn("mock-search-result", json.dumps(request["payload"]))
                    if model["protocol"] == "gemini":
                        self.assertIn(f"/v1beta/models/{model['id']}:streamGenerateContent", request["path"])
                        self.assertIn("contents", request["payload"])
                        self.assertEqual("mock-only", request["headers"].get("x-goog-api-key"))
                    else:
                        self.assertEqual("/v1/chat/completions", request["path"])
                        self.assertEqual(model["id"], request["payload"]["model"])
                        self.assertTrue(request["payload"]["stream"])
                        self.assertEqual("Bearer mock-only", request["headers"].get("authorization"))
                    if index > 1:
                        self.assertIn("question-1", json.dumps(request["payload"]))
                        self.assertIn(f"reply-{index - 1}", json.dumps(request["payload"]))
            finally:
                pool.close()

    def test_real_runtime_continues_and_restores_session_without_upstream_calls(self):
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                if self.path.endswith("/files"):
                    self.rfile.read(int(self.headers["Content-Length"]))
                    self.send_response(404)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"error":"mock relay uses inline images"}')
                    return
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append(payload)
                frames = [
                    {"type": "message_start", "message": {
                        "id": f"mock-{len(requests)}", "model": "tt-5.6-sol",
                        "usage": {"input_tokens": 3, "output_tokens": 0},
                    }},
                    {"type": "content_block_start", "index": 0, "content_block": {
                        "type": "text", "text": f"reply-{len(requests)}",
                    }},
                    {"type": "content_block_stop", "index": 0},
                    {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 1}},
                    {"type": "message_stop"},
                ]
                body = "".join(f"event: {frame['type']}\ndata: {json.dumps(frame)}\n\n" for frame in frames).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 3)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        settings = {"base_url": f"http://127.0.0.1:{server.server_port}", "api_key": "mock-only"}
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(runtime, "DATA_DIR", Path(directory)),
            patch.object(runtime.config, "get_openai_relay_settings", return_value=settings),
            patch.object(runtime, "current_relay_account", return_value=None),
            patch.object(runtime, "runtime_pool", pool := RuntimePool()),
            patch.object(runtime, "search_available", return_value=False),
        ):
            try:
                for index in range(1, 4):
                    if index == 3:
                        pool.close()
                    prompt = f"question-{index}"
                    if index == 2:
                        from PIL import Image
                        output = io.BytesIO()
                        Image.new("RGB", (2, 2), "red").save(output, "PNG")
                        prompt = [{"type": "text", "text": prompt}, {"type": "image", "mimeType": "image/png", "data": base64.b64encode(output.getvalue()).decode()}]
                    response = runtime._execute(
                        prompt, owner_id="smoke-user", dsh_session_id=f"session-{'e' * 32}",
                        model="tt-5.6-sol", has_history=index > 1, history=[], on_notification=lambda _notice: None,
                    )
                    self.assertEqual(f"reply-{index}", response["content"], response)
                    self.assertEqual(index, len(requests))
                    if index == 2:
                        self.assertIn('"type": "image"', json.dumps(requests[-1]))
                self.assertIn("question-1", json.dumps(requests[-1]))
                self.assertIn("reply-2", json.dumps(requests[-1]))
            finally:
                pool.close()


if __name__ == "__main__":
    unittest.main()
