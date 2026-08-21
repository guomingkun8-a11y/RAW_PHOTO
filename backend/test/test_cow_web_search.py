from __future__ import annotations

import os
import unittest
from unittest import mock

import services.ecommerce.cow_agent_runtime_service  # noqa: F401
from agent.tools.web_search import web_search


class CowWebSearchTests(unittest.TestCase):
    def test_serpapi_is_selected_when_it_is_the_only_configured_provider(self):
        provider_env = {
            "SERPAPI_API_KEY": "test-serpapi-key",
            "BOCHA_API_KEY": "",
            "ZHIPUAI_API_KEY": "",
            "QIANFAN_API_KEY": "",
            "LINKAI_API_KEY": "",
        }
        with (
            mock.patch.dict(os.environ, provider_env, clear=False),
            mock.patch.object(web_search, "conf", return_value={}),
        ):
            self.assertEqual(["serpapi"], web_search.configured_providers())

    def test_serpapi_results_use_the_common_web_search_contract(self):
        response = mock.Mock(status_code=200)
        response.json.return_value = {
            "search_information": {"total_results": 321},
            "organic_results": [
                {
                    "title": "RAW product imagery",
                    "link": "https://example.test/raw",
                    "snippet": "Commercial product image guidance.",
                    "displayed_link": "example.test",
                    "date": "Aug 17, 2026",
                },
                {
                    "title": "Second result",
                    "link": "https://example.test/second",
                    "snippet": "This result should be truncated.",
                },
            ],
        }
        with (
            mock.patch.object(web_search, "_get_api_key", return_value="test-serpapi-key"),
            mock.patch.object(web_search.requests, "get", return_value=response) as request,
        ):
            result = web_search.WebSearch()._search_serpapi("RAW product image", 1, "oneWeek")

        self.assertEqual("success", result.status)
        self.assertEqual("serpapi", result.result["backend"])
        self.assertEqual(1, result.result["count"])
        self.assertEqual("https://example.test/raw", result.result["results"][0]["url"])
        params = request.call_args.kwargs["params"]
        self.assertEqual("google", params["engine"])
        self.assertEqual("qdr:w", params["tbs"])
        self.assertEqual(1, params["num"])

    def test_serpapi_supports_explicit_date_ranges(self):
        self.assertEqual(
            "cdr:1,cd_min:08/01/2026,cd_max:08/17/2026",
            web_search.WebSearch._serpapi_freshness_tbs("2026-08-01..2026-08-17"),
        )

    def test_serpapi_localizes_chinese_queries_to_google_hong_kong(self):
        response = mock.Mock(status_code=200)
        response.json.return_value = {"organic_results": []}
        with (
            mock.patch.object(web_search, "_get_api_key", return_value="test-serpapi-key"),
            mock.patch.object(web_search.requests, "get", return_value=response) as request,
        ):
            web_search.WebSearch()._search_serpapi("家可美", 5, "noLimit")

        params = request.call_args.kwargs["params"]
        self.assertEqual("google.com.hk", params["google_domain"])
        self.assertEqual("zh-cn", params["hl"])
        self.assertEqual("cn", params["gl"])

    def test_serpapi_merges_organic_and_news_results(self):
        response = mock.Mock(status_code=200)
        response.json.return_value = {
            "organic_results": [{
                "title": "Official product page",
                "link": "https://example.test/product",
                "snippet": "Primary product information.",
            }],
            "news_results": [{
                "title": "Product launch coverage",
                "link": "https://news.example.test/launch",
                "snippet": "Independent launch report.",
                "source": "Example News",
            }],
        }
        with (
            mock.patch.object(web_search, "_get_api_key", return_value="test-serpapi-key"),
            mock.patch.object(web_search.requests, "get", return_value=response),
        ):
            result = web_search.WebSearch()._search_serpapi("product launch", 2, "noLimit")

        self.assertEqual("success", result.status)
        self.assertEqual(["organic", "news"], [item["resultType"] for item in result.result["results"]])
        self.assertEqual("Example News", result.result["results"][1]["siteName"])


if __name__ == "__main__":
    unittest.main()
