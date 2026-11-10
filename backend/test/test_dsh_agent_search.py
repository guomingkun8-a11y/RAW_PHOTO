from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
import threading
from types import SimpleNamespace
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

from services.dsh_agent import search


class MockSearchTool:
    def __init__(self, response=None, barrier=None, release=None):
        self.response = response or SimpleNamespace(
            status="success",
            result={"results": [{
                "url": "https://example.test/result",
                "title": "Example result",
                "snippet": "A local mocked result.",
            }]},
        )
        self.barrier = barrier
        self.release = release
        self.calls = []
        self.entered = threading.Event()
        self._entry_count = 0
        self._entry_lock = threading.Lock()

    def execute(self, args):
        self.calls.append(args)
        if self.barrier is not None:
            with self._entry_lock:
                self._entry_count += 1
                if self._entry_count == 4:
                    self.entered.set()
            self.barrier.wait(timeout=3)
            self.release.wait(timeout=3)
        return self.response


class DshAgentSearchTests(unittest.TestCase):
    def setUp(self):
        self.tool = MockSearchTool()
        self.bridge = search.SearchBridge(self.tool)
        self.addCleanup(self.bridge.close)

    def request(self, payload=None, *, path="/search", method="POST", api_key=None, raw_body=None):
        body = raw_body
        if body is None and payload is not None:
            body = json.dumps(payload).encode("utf-8")
        headers = {}
        if api_key is not None:
            headers["Authorization"] = f"Bearer {api_key}"
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = Request(self.bridge.base_url + path, data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=4) as response:
                return response.status, json.loads(response.read())
        except HTTPError as response:
            return response.code, json.loads(response.read())

    def post(self, payload, *, api_key=None):
        return self.request(payload, api_key=api_key or self.bridge.api_key)

    def test_success_uses_native_search_protocol_and_filters_results(self):
        self.tool.response.result["results"] = [
            {"url": "https://example.test/ok", "title": "T" * 500, "snippet": "S" * 1500},
            {"url": "http://user:pass@example.test/private", "title": "Credentials"},
            {"url": "javascript:alert(1)", "title": "Script"},
            {"url": "https://example.test:99999/invalid", "title": "Port"},
            {"url": "https://exa mple.test/path", "title": "Whitespace"},
        ]

        status, payload = self.post({"query": "latest camera", "count": 3})

        self.assertEqual(200, status)
        self.assertEqual([{"query": "latest camera", "count": 3}], self.tool.calls)
        self.assertEqual({"results": [{
            "url": "https://example.test/ok",
            "title": "T" * 300,
            "snippet": "S" * 1000,
            "date": None,
        }]}, payload)
        self.assertTrue(self.bridge.base_url.startswith("http://127.0.0.1:"))

    def test_authentication_and_routes_are_restricted(self):
        status, payload = self.post({"query": "test", "count": 1}, api_key="incorrect-secret")
        self.assertEqual(401, status)
        self.assertNotIn(self.bridge.api_key, json.dumps(payload))
        self.assertEqual((404, {"error": "Not found."}), self.request(
            {"query": "test", "count": 1}, path="/other", api_key=self.bridge.api_key,
        ))
        self.assertEqual((405, {"error": "Method not allowed."}), self.request(
            path="/search", method="GET", api_key=self.bridge.api_key,
        ))
        self.assertEqual((405, {"error": "Method not allowed."}), self.request(
            {"query": "test", "count": 1}, method="PUT", api_key=self.bridge.api_key,
        ))
        self.assertEqual([], self.tool.calls)

    def test_request_size_and_query_count_limits(self):
        invalid = [
            {"query": "", "count": 1},
            {"query": "q" * 1001, "count": 1},
            {"query": "q", "count": 0},
            {"query": "q", "count": 6},
            {"query": "q", "count": True},
            {"query": "q"},
        ]
        for payload in invalid:
            with self.subTest(payload=payload):
                self.assertEqual(400, self.post(payload)[0])
        self.assertEqual(400, self.request(raw_body=b"not-json", api_key=self.bridge.api_key)[0])
        self.assertEqual(413, self.request(raw_body=b" " * (8 * 1024 + 1), api_key=self.bridge.api_key)[0])
        self.assertEqual([], self.tool.calls)

    def test_upstream_failure_never_returns_its_body(self):
        self.tool.response = SimpleNamespace(status="error", result="upstream leaked secret-value")

        status, payload = self.post({"query": "test", "count": 1})

        self.assertEqual(502, status)
        self.assertEqual({"error": "Search failed."}, payload)
        self.assertNotIn("secret-value", json.dumps(payload))

    def test_fifth_concurrent_search_gets_a_clear_429(self):
        barrier = threading.Barrier(4)
        release = threading.Event()
        tool = MockSearchTool(barrier=barrier, release=release)
        self.bridge._search_tool = tool
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(self.post, {"query": str(i), "count": 1}) for i in range(4)]
            try:
                self.assertTrue(tool.entered.wait(timeout=3))
                status, payload = self.post({"query": "fifth", "count": 1})
                self.assertEqual(429, status)
                self.assertIn("capacity", payload["error"])
            finally:
                release.set()
            self.assertEqual([200] * 4, [future.result(timeout=4)[0] for future in futures])


class DshAgentSearchSettingsTests(unittest.TestCase):
    def test_search_availability_defaults_on_and_requires_a_provider(self):
        module = SimpleNamespace(configured_providers=lambda: ["serpapi"])
        with patch.dict(os.environ, {}, clear=True), patch.object(search, "_load_web_search", return_value=module):
            self.assertTrue(search.search_available())

        module.configured_providers = lambda: []
        with patch.dict(os.environ, {}, clear=True), patch.object(search, "_load_web_search", return_value=module):
            self.assertFalse(search.search_available())

    def test_search_can_be_disabled_without_loading_provider(self):
        with (
            patch.dict(os.environ, {"DSH_AGENT_SEARCH_ENABLED": "false"}, clear=True),
            patch.object(search, "_load_web_search") as load,
        ):
            self.assertFalse(search.search_available())
        load.assert_not_called()

    def test_settings_start_one_loopback_bridge_and_close_releases_it(self):
        module = SimpleNamespace(configured_providers=lambda: ["serpapi"])
        with patch.object(search, "_load_web_search", return_value=module):
            settings = search.bridge_settings()
            bridge = search._bridge
            self.addCleanup(search.close_search_bridge)
            self.assertEqual("127.0.0.1", settings["base_url"].split(":")[1].lstrip("/"))
            self.assertEqual(bridge.api_key, settings["api_key"])
            search.close_search_bridge()
        self.assertIsNone(search._bridge)
        self.assertTrue(bridge._closed)


if __name__ == "__main__":
    unittest.main()
