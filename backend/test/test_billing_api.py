from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _load_billing_api_module():
    api_dir = Path(__file__).resolve().parents[1] / "api"
    support_module = types.ModuleType("api.support")
    support_module.require_admin = lambda authorization=None: {"id": "admin"}
    api_module = types.ModuleType("api")
    api_module.__path__ = [str(api_dir)]
    previous_api = sys.modules.get("api")
    previous_support = sys.modules.get("api.support")
    sys.modules["api"] = api_module
    sys.modules["api.support"] = support_module
    try:
        spec = importlib.util.spec_from_file_location("billing_api_under_test", api_dir / "billing.py")
        if spec is None or spec.loader is None:
            raise RuntimeError("failed to load billing api module")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if previous_api is None:
            sys.modules.pop("api", None)
        else:
            sys.modules["api"] = previous_api
        if previous_support is None:
            sys.modules.pop("api.support", None)
        else:
            sys.modules["api.support"] = previous_support


billing_api = _load_billing_api_module()


class BillingApiTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(billing_api.create_router())
        self.client = TestClient(app)

    def test_summary_forwards_type_and_time_range(self):
        expected = {"source": "chat", "record_count": 1}
        with (
            mock.patch.object(billing_api, "require_admin", return_value={"id": "admin"}),
            mock.patch.object(billing_api.upstream_usage_service, "summary", return_value=expected) as summary,
        ):
            response = self.client.get(
                "/api/billing/summary",
                params={"source": "chat", "startAt": "2026-09-01T10:00", "endAt": "2026-09-02T10:00"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), expected)
        self.assertEqual(summary.call_args.kwargs["source"], "chat")
        self.assertEqual(summary.call_args.kwargs["start_at"].isoformat(), "2026-09-01T10:00:00")

    def test_records_forward_server_side_pagination_and_search(self):
        expected = {"items": [], "record_count": 0}
        with (
            mock.patch.object(billing_api, "require_admin", return_value={"id": "admin"}),
            mock.patch.object(billing_api.upstream_usage_service, "list_records", return_value=expected) as records,
        ):
            response = self.client.get(
                "/api/billing/records",
                params={"source": "audio", "limit": 20, "offset": 40, "q": "doubao"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(records.call_args.kwargs["offset"], 40)
        self.assertEqual(records.call_args.kwargs["query_text"], "doubao")

    def test_manual_sync_is_admin_only_and_returns_status(self):
        with (
            mock.patch.object(billing_api, "require_admin", return_value={"id": "admin"}) as require_admin,
            mock.patch.object(billing_api.upstream_usage_service, "sync_usage", return_value={"ok": True}) as sync,
            mock.patch.object(billing_api.upstream_usage_service, "status", return_value={"status": "success"}),
        ):
            response = self.client.post("/api/billing/sync", params={"days": 30})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["sync"]["status"], "success")
        self.assertTrue(sync.call_args.kwargs["full_sync"])
        require_admin.assert_called_once()

    def test_invalid_range_is_rejected_before_query(self):
        with mock.patch.object(billing_api, "require_admin", return_value={"id": "admin"}):
            response = self.client.get(
                "/api/billing/summary",
                params={"startAt": "2026-09-02", "endAt": "2026-09-01"},
            )
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()

