from __future__ import annotations

import unittest

from services.ecommerce.ecommerce_agent_service import get_agent_run, run_professional_prompt_agent


class EcommerceAgentServiceTests(unittest.TestCase):
    def test_professional_prompt_engine_is_executed_as_a_tool(self):
        expected = {
            "model": "test-model",
            "productProfile": {"productName": "Test product"},
            "sceneType": "taobao_text_main",
            "sceneName": "Text main image",
            "visualDirection": "Clean ecommerce layout",
            "finalPrompt": "Product on the right, text on the left",
            "negativePrompt": "distortion",
        }

        def builder(payload):
            self.assertEqual("Create a main image", payload["prompt"])
            return expected

        response = run_professional_prompt_agent(
            {"prompt": "Create a main image"},
            prompt_builder=builder,
            identity={"id": "agent-test-user"},
        )

        self.assertEqual(expected["finalPrompt"], response["finalPrompt"])
        self.assertEqual("completed", response["agentRun"]["status"])
        self.assertEqual(1, response["agentRun"]["toolCalls"])
        self.assertEqual(2, response["agentRun"]["stepCount"])
        run = get_agent_run(response["agentRun"]["runId"], {"id": "agent-test-user"})
        self.assertEqual("completed", run["status"])
        self.assertGreaterEqual(len(run["events"]), 5)

    def test_prompt_engine_failure_is_returned_to_api_layer(self):
        expected_error = RuntimeError("prompt provider unavailable")

        def builder(_payload):
            raise expected_error

        with self.assertRaises(RuntimeError) as raised:
            run_professional_prompt_agent(
                {"prompt": "Create a main image"},
                prompt_builder=builder,
                identity={"id": "agent-failure-user"},
            )
        self.assertIs(expected_error, raised.exception)

    def test_agent_run_is_isolated_by_identity(self):
        response = run_professional_prompt_agent(
            {"prompt": "Create a main image"},
            prompt_builder=lambda _payload: {"finalPrompt": "ok"},
            identity={"id": "owner-user"},
        )

        with self.assertRaises(PermissionError):
            get_agent_run(response["agentRun"]["runId"], {"id": "other-user"})


if __name__ == "__main__":
    unittest.main()
