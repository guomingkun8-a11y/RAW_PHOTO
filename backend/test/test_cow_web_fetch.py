from __future__ import annotations

import unittest
from unittest import mock

import services.ecommerce.cow_agent_runtime_service  # noqa: F401
from agent.tools.web_fetch.web_fetch import WebFetch
from agent.tools.utils.url_safety import validate_url_safe


class CowWebFetchTests(unittest.TestCase):
    def test_extract_text_keeps_article_and_removes_navigation_noise(self):
        html = """
        <html><head><title>Example &amp; Test</title><style>.x{}</style></head><body>
          <header>Site header</header><nav>Main menu Navigation</nav>
          <main><article><h1>CSGO history</h1><p>CSGO was released in 2012.</p>
          <ul><li>Competitive mode</li><li>Community maps</li></ul></article></main>
          <footer>Privacy Cookie settings</footer><script>ignore()</script>
        </body></html>
        """

        text = WebFetch._extract_text(html)

        self.assertIn("# CSGO history", text)
        self.assertIn("CSGO was released in 2012.", text)
        self.assertIn("- Competitive mode", text)
        self.assertNotIn("Main menu", text)
        self.assertNotIn("Cookie settings", text)
        self.assertNotIn("ignore()", text)

    def test_dynamic_empty_page_is_reported_as_unreadable(self):
        response = mock.Mock()
        response.headers = {"Content-Type": "text/html; charset=utf-8"}
        response.text = "<html><head><title>App</title></head><body><!-- shell --><div id='app'>--&gt;</div></body></html>"
        response.raise_for_status.return_value = None

        with mock.patch.object(WebFetch, "_safe_get", return_value=response):
            result = WebFetch().execute({"url": "https://example.test/app"})

        self.assertEqual("error", result.status)
        self.assertIn("browser tool", result.result)
        self.assertIn("https://example.test/app", result.result)

    def test_enterprise_ssrf_guard_rejects_private_resolution(self):
        private_info = [(2, 1, 6, "", ("127.0.0.1", 0))]
        with (
            mock.patch.dict("os.environ", {
                "GMKRAW_ENTERPRISE_MODE": "true",
                "WEB_SECURITY_SSRF_PROTECTION": "true",
            }, clear=False),
            mock.patch("socket.getaddrinfo", return_value=private_info),
        ):
            with self.assertRaisesRegex(ValueError, "non-public"):
                validate_url_safe("https://internal.example.test/resource")


if __name__ == "__main__":
    unittest.main()
