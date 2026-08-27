from __future__ import annotations

import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("IMAGE_TASK_QUEUE_ENABLED", "false")


def _load_monitoring_api_module():
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
        spec = importlib.util.spec_from_file_location("monitoring_api_under_test", api_dir / "monitoring.py")
        if spec is None or spec.loader is None:
            raise RuntimeError("failed to load monitoring api module")
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


monitoring_api = _load_monitoring_api_module()


class MonitoringApiTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(monitoring_api.create_router())
        self.client = TestClient(app)

    def test_summary_accepts_time_range(self):
        with (
            mock.patch.object(monitoring_api, "require_admin", return_value={"id": "admin"}),
            mock.patch.object(monitoring_api, "_build_summary", return_value={"ok": True}) as build_summary,
        ):
            response = self.client.get(
                "/api/monitoring/summary",
                params={"startAt": "2026-08-01T10:00", "endAt": "2026-08-01T12:00"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True})
        args = build_summary.call_args.args
        self.assertEqual(args[0].isoformat(), "2026-08-01T10:00:00")
        self.assertEqual(args[1].isoformat(), "2026-08-01T12:00:00")

    def test_summary_rejects_invalid_or_reversed_time_range(self):
        with mock.patch.object(monitoring_api, "require_admin", return_value={"id": "admin"}):
            invalid = self.client.get("/api/monitoring/summary", params={"startAt": "not-a-date"})
            reversed_range = self.client.get(
                "/api/monitoring/summary",
                params={"startAt": "2026-08-01T12:00", "endAt": "2026-08-01T10:00"},
            )

        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(reversed_range.status_code, 400)

    def test_task_details_forwards_filters(self):
        expected = {"items": [], "record_count": 0, "image_count": 0}
        with (
            mock.patch.object(monitoring_api, "require_admin", return_value={"id": "admin"}),
            mock.patch.object(
                monitoring_api.generation_monitoring_service,
                "task_details",
                return_value=expected,
            ) as task_details,
        ):
            response = self.client.get(
                "/api/monitoring/tasks",
                params={
                    "startAt": "2026-08-01T10:00",
                    "endAt": "2026-08-01T12:00",
                    "ownerId": "user-1",
                    "status": "error",
                    "limit": 50,
                    "includeReferences": "1",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), expected)
        task_details.assert_called_once()
        kwargs = task_details.call_args.kwargs
        self.assertEqual(kwargs["start_at"].isoformat(), "2026-08-01T10:00:00")
        self.assertEqual(kwargs["end_at"].isoformat(), "2026-08-01T12:00:00")
        self.assertEqual(kwargs["owner_id"], "user-1")
        self.assertEqual(kwargs["status"], "error")
        self.assertEqual(kwargs["limit"], 50)
        self.assertTrue(kwargs["include_references"])
        self.assertNotIn("include_model_tokens", kwargs)


if __name__ == "__main__":
    unittest.main()
