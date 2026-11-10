from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import yaml

from services.dsh_agent import runtime
from services.dsh_agent.models import MODELS, context_budget, relay_providers, runtime_provider


PROFILE = Path(runtime.__file__).parent / "plugins" / "profiles" / "agent-teams"
PACKAGE_NAME = "@nanmicoder/dsh-agent-teams"
PACKAGE_VERSION = "0.1.29"


class DshAgentTeamsConfigurationTests(unittest.TestCase):
    def test_package_and_lockfile_pin_plugin_and_compatible_host(self):
        manifest = json.loads((PROFILE / "package.json").read_text(encoding="utf-8"))
        lockfile = yaml.safe_load((PROFILE / "pnpm-lock.yaml").read_text(encoding="utf-8"))
        self.assertEqual({"@deepseek-ai/dsh": "0.2.1-rc.2", PACKAGE_NAME: PACKAGE_VERSION}, manifest["dependencies"])
        self.assertEqual(
            ["@deepseek-ai/dsh-sdk-minimal", PACKAGE_NAME],
            manifest["dsh"]["profile"]["bundles"],
        )
        for name, version in manifest["dependencies"].items():
            self.assertEqual(version, lockfile["importers"]["."]["dependencies"][name]["specifier"])
            self.assertIn(f"{name}@{version}", lockfile["packages"])
        self.assertTrue(lockfile["settings"]["autoInstallPeers"])

    def test_profile_caps_members_and_recursive_delegation(self):
        entries = yaml.safe_load((PROFILE / "cordis.patch.yml").read_text(encoding="utf-8"))
        inserted = {entry["id"]: entry for block in entries for entry in block.get("insert", [])}
        team = next(entry for entry in entries if entry.get("id") == "agent-teams")
        self.assertEqual(2, team["config"]["maxMembers"])
        self.assertEqual(0, team["config"]["memberMaxDepth"])
        self.assertEqual(2, inserted["subagent"]["config"]["maxActiveSubagents"])
        self.assertEqual(1, inserted["subagent"]["config"]["maxDepth"])
        self.assertEqual("@deepseek-ai/dsh-subagent-spawn-in-process", inserted["subagent-spawn"]["name"])


@unittest.skipUnless(os.getenv("DSH_RUNTIME_SMOKE") == "1", "opt-in installed plugin smoke test")
class DshAgentTeamsRuntimeTests(unittest.TestCase):
    def setUp(self):
        package = PROFILE / "node_modules" / PACKAGE_NAME / "package.json"
        self.assertTrue(package.is_file(), "Run backend/scripts/deployment/install_dsh_agent_teams.ps1 first")
        installed = json.loads(package.read_text(encoding="utf-8"))
        self.assertEqual(PACKAGE_VERSION, installed["version"])
        directory = tempfile.TemporaryDirectory(prefix="raw-photo-agent-teams-")
        self.addCleanup(directory.cleanup)
        self.home = Path(directory.name) / "home"
        self.workspace = Path(directory.name) / "workspace"
        self.workspace.mkdir()
        target = self.home / "profiles" / "agent-teams"
        target.mkdir(parents=True)
        for name in ("package.json", "cordis.patch.yml"):
            shutil.copyfile(PROFILE / name, target / name)
        # Only the small third-party plugin is copied; host dependencies resolve from the pinned installation.
        shutil.copytree(PROFILE / "node_modules" / PACKAGE_NAME, target / "node_modules" / PACKAGE_NAME)
        self.requests = []
        self.create_plan = False
        test = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                if self.path != "/v1/chat/completions":
                    self.send_error(400, "Unexpected mock endpoint")
                    return
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                test.requests.append(payload)
                call_tool = test.create_plan and len(test.requests) == 1
                delta = {"role": "assistant", "content": "plugin-ready"}
                if call_tool:
                    arguments = {
                        "name": "smoke-team",
                        "description": "Local mock-only plan validation",
                        "approval": "required",
                        "plan": {
                            "members": [{"name": "visual"}, {"name": "market"}],
                            "tasks": [
                                {"id": "visual-task", "subject": "Describe supplied facts", "assignee": "visual"},
                                {"id": "market-task", "subject": "List unverified assumptions", "assignee": "market", "dependencies": ["visual-task"]},
                            ],
                        },
                    }
                    delta = {"role": "assistant", "tool_calls": [{
                        "index": 0, "id": "call-create-plan", "type": "function",
                        "function": {"name": "agent_teams_create", "arguments": json.dumps(arguments)},
                    }]}
                frames = [
                    {"id": "mock", "object": "chat.completion.chunk", "model": payload["model"], "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                    {"id": "mock", "object": "chat.completion.chunk", "model": payload["model"], "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls" if call_tool else "stop"}], "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}},
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
        self.base_url = f"http://127.0.0.1:{server.server_port}/v1"

    def harness(self):
        from deepseek_harness import DeepSeekHarness

        env = runtime._runtime_environment(self.home, "Validate plugin tools using only the local mock server.")
        env.update({
            "DSH_HOME": str(self.home),
            "DSH_AGENT_CONTEXT_WINDOW": "128000",
            "DSH_AGENT_RELAY_API_KEY": "mock-only",
            "DSH_AGENT_RELAY_PROVIDERS": json.dumps(relay_providers(self.base_url)),
            "DSH_AGENT_CONTEXT_BUDGETS": json.dumps({item["id"]: context_budget(item["id"]) for item in MODELS}),
        })
        harness = DeepSeekHarness(
            _launch_args=(
                shutil.which("node"), str(PROFILE / "node_modules" / "@deepseek-ai" / "dsh" / "lib" / "bin.js"),
                "--profile", "agent-teams", "--patch", str(runtime.PROFILE_PATCH),
            ),
            provider=runtime_provider("tt-6-sol"), model="tt-6-sol", max_tokens=256,
            cwd=str(self.workspace), dsh_home=str(self.home), profile="agent-teams",
            patches=(str(runtime.PROFILE_PATCH),), base_url=self.base_url, api_key="mock-only",
            env=env, initialize_timeout_seconds=60, request_timeout_seconds=20, shutdown_timeout_seconds=3,
        )
        self.addCleanup(harness.close)
        return harness

    def test_sdk_loads_team_tools_without_reenabling_shell_or_files(self):
        with patch.dict(os.environ, {"DSH_RUNTIME_MODE": "node"}):
            harness = self.harness()
            harness.start()
            self.assertEqual([], self.requests, "SDK startup must not call a model")
            result = harness.run("Check the available team tools")
        self.assertEqual("plugin-ready", result.final_response)
        self.assertEqual(1, len(self.requests))
        tools = {tool["function"]["name"] for tool in self.requests[0]["tools"]}
        self.assertEqual(14, len([name for name in tools if name.startswith("agent_teams_")]))
        self.assertIn("agent_teams_approve", tools)
        self.assertIn("agent_teams_send_message", tools)
        self.assertTrue(tools.issubset({"generate_image", "get_image_task", "web_search", "web_fetch",
                                        "read_expert_reference", "read_skill", "render_diagram", "get_diagram"}
                                       | {name for name in tools if name.startswith("agent_teams_")}))

    def test_staged_plan_is_saved_without_starting_any_members(self):
        self.create_plan = True
        with patch.dict(os.environ, {"DSH_RUNTIME_MODE": "node"}):
            result = self.harness().run("/agent-teams Plan only; do not start before approval")
        self.assertEqual("plugin-ready", result.final_response)
        self.assertEqual(2, len(self.requests), "Planning must not start additional member model calls")
        team = json.loads((self.workspace / ".agent-teams" / "smoke-team" / "team.json").read_text(encoding="utf-8"))
        self.assertEqual("staged", team["phase"])
        self.assertEqual(2, len(team["members"]))
        self.assertEqual(2, len(team["tasks"]))
        self.assertTrue(all(not member.get("sessionId") for member in team["members"]))


if __name__ == "__main__":
    unittest.main()
