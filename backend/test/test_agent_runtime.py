from __future__ import annotations

import json
from threading import Event
import time
import unittest

from services.agent import (
    AgentDecision,
    AgentRunStatus,
    AgentRunner,
    AgentRunStore,
    AgentToolRegistry,
    FunctionAgentTool,
    InMemoryAgentMemory,
)


class AgentRuntimeTests(unittest.TestCase):
    def test_tool_call_then_finish_records_steps_and_events(self):
        calls: list[dict[str, object]] = []

        def tool_handler(context, arguments):
            calls.append(arguments)
            context.memory.set(context.memory_namespace, "seen", True)
            return {"answer": arguments["prompt"]}

        runner = AgentRunner(
            AgentToolRegistry([
                FunctionAgentTool("echo", "Echo input", tool_handler),
            ]),
            run_store=AgentRunStore(),
            memory=InMemoryAgentMemory(),
        )

        def planner(context):
            if context.last_tool_result is None:
                return AgentDecision.call_tool("echo", context.request)
            return AgentDecision.finish(context.last_tool_result.data)

        run = runner.run(
            {"prompt": "hello"},
            planner,
            agent_name="test-agent",
            memory_namespace="user-1",
        )

        self.assertEqual(AgentRunStatus.COMPLETED, run.status)
        self.assertEqual(1, run.tool_calls)
        self.assertEqual(2, len(run.steps))
        self.assertEqual([{"prompt": "hello"}], calls)
        self.assertEqual({"answer": "hello"}, run.raw_result)
        self.assertEqual(True, run.events[0].event_type == "run.started")
        self.assertIn("run.completed", [event.event_type for event in run.events])

    def test_max_steps_prevents_unbounded_agent_loop(self):
        tool = FunctionAgentTool("noop", "No-op", lambda _context, _arguments: {"ok": True})
        runner = AgentRunner(
            AgentToolRegistry([tool]),
            run_store=AgentRunStore(),
            max_steps=2,
            max_tool_calls=10,
        )

        run = runner.run(
            {},
            lambda _context: AgentDecision.call_tool("noop"),
            agent_name="loop-agent",
        )

        self.assertEqual(AgentRunStatus.FAILED, run.status)
        self.assertIn("maximum agent steps exceeded", run.error)
        self.assertEqual(2, run.tool_calls)

    def test_tool_failure_can_be_handled_by_planner(self):
        failure = ValueError("invalid product")

        def broken_tool(_context, _arguments):
            raise failure

        runner = AgentRunner(
            AgentToolRegistry([FunctionAgentTool("broken", "Broken", broken_tool)]),
            run_store=AgentRunStore(),
        )

        def planner(context):
            if context.last_tool_result is None:
                return AgentDecision.call_tool("broken")
            self.assertFalse(context.last_tool_result.ok)
            return AgentDecision.fail(context.last_tool_result.error)

        run = runner.run({}, planner, agent_name="failure-agent")

        self.assertEqual(AgentRunStatus.FAILED, run.status)
        self.assertEqual(failure, run.failure_exception)
        self.assertEqual("invalid product", run.steps[-1].error)
        self.assertIn("tool.failed", [event.event_type for event in run.events])

    def test_agent_can_pause_for_user_input(self):
        runner = AgentRunner(AgentToolRegistry([]), run_store=AgentRunStore())
        run = runner.run(
            {},
            lambda _context: AgentDecision.ask_user("Please provide a product image"),
            agent_name="question-agent",
        )

        self.assertEqual(AgentRunStatus.WAITING, run.status)
        self.assertIsNone(run.finished_at)
        self.assertEqual("Please provide a product image", run.waiting_message)

    def test_waiting_agent_can_resume_without_restarting_steps(self):
        runner = AgentRunner(AgentToolRegistry([]), run_store=AgentRunStore(), max_steps=4)

        def planner(context):
            if context.step_index == 1:
                return AgentDecision.ask_user("Confirm", result={"proposal": {"pages": [1]}})
            return AgentDecision.finish({"confirmed": context.request.get("answer")})

        run = runner.run({}, planner, agent_name="resume-agent")
        self.assertEqual(AgentRunStatus.WAITING, run.status)
        self.assertEqual({"proposal": {"pages": [1]}}, run.raw_result)

        resumed = runner.resume(
            run.run_id,
            {"answer": "yes"},
            planner,
            agent_name="resume-agent",
        )
        deadline = time.time() + 1
        while time.time() < deadline and resumed.status == AgentRunStatus.RUNNING:
            time.sleep(0.01)

        self.assertEqual(AgentRunStatus.COMPLETED, resumed.status)
        self.assertEqual([1, 2], [step.index for step in resumed.steps])
        self.assertEqual({"confirmed": "yes"}, resumed.raw_result)
        self.assertEqual(1, [event.event_type for event in resumed.events].count("run.started"))
        self.assertIn("run.resumed", [event.event_type for event in resumed.events])

    def test_public_state_redacts_credentials_and_image_data(self):
        tool = FunctionAgentTool("inspect", "Inspect", lambda _context, arguments: arguments)
        runner = AgentRunner(AgentToolRegistry([tool]), run_store=AgentRunStore(), max_steps=1)
        request = {
            "authorization": "Bearer top-secret-token",
            "apiKey": "sk-live-value",
            "images": [{"dataUrl": "data:image/png;base64," + ("A" * 50)}],
        }

        run = runner.run(
            request,
            lambda context: AgentDecision.call_tool("inspect", context.request),
            agent_name="redaction-agent",
        )
        public_state = json.dumps(run.to_public_dict(include_events=True), ensure_ascii=False)

        self.assertNotIn("top-secret-token", public_state)
        self.assertNotIn("sk-live-value", public_state)
        self.assertNotIn("AAAA", public_state)
        self.assertIn("[redacted]", public_state)
        self.assertIn("[image data omitted]", public_state)

    def test_run_store_evicts_oldest_runs(self):
        store = AgentRunStore(max_runs=1)
        runner = AgentRunner(AgentToolRegistry([]), run_store=store)
        first = runner.run({}, lambda _context: AgentDecision.finish({}), agent_name="test")
        second = runner.run({}, lambda _context: AgentDecision.finish({}), agent_name="test")

        self.assertIsNone(store.get(first.run_id))
        self.assertIsNotNone(store.get(second.run_id))

    def test_running_agent_can_be_canceled_cooperatively(self):
        started = Event()
        release = Event()
        store = AgentRunStore()

        def blocking_tool(context, _arguments):
            started.set()
            release.wait(1)
            self.assertTrue(context.is_cancel_requested())
            return {"ok": True}

        runner = AgentRunner(
            AgentToolRegistry([FunctionAgentTool("blocking", "Blocking tool", blocking_tool)]),
            run_store=store,
        )
        run = runner.start(
            {},
            lambda context: AgentDecision.call_tool("blocking") if context.last_tool_result is None else AgentDecision.finish({}),
            agent_name="cancel-agent",
        )
        self.assertTrue(started.wait(1))

        canceled = store.request_cancel(run.run_id)
        release.set()
        deadline = time.time() + 1
        while time.time() < deadline and run.steps and run.steps[0].status.value == "running":
            time.sleep(0.01)

        self.assertIsNotNone(canceled)
        self.assertEqual(AgentRunStatus.CANCELED, run.status)
        self.assertEqual("canceled", run.steps[0].status.value)
        self.assertEqual(1, [event.event_type for event in run.events].count("run.canceled"))


if __name__ == "__main__":
    unittest.main()
