from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from sqlalchemy import inspect, select, text

from services.billing.upstream_usage_models import UpstreamUsageRecord
from services.billing.upstream_usage_service import UpstreamUsageService, UpstreamUsageSettings


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self.payload = payload
        self.status_code = status_code

    def json(self):
        return self.payload


def usage_record(task_id: str, model_type: str, **updates):
    return {
        "task_id": task_id,
        "model": f"test-{model_type}",
        "model_type": model_type,
        "channel_group": "test-channel",
        "state": "success",
        "cost": "1.250000000001",
        "refunded": False,
        "refunded_amount": "0",
        "created_at": "2026-09-16T10:00:00+08:00",
        "completed_at": "2026-09-16T10:01:00+08:00",
        **updates,
    }


class UpstreamUsageServiceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.database_url = f"sqlite:///{Path(temporary.name) / 'usage.db'}"
        self.records = [
            usage_record("upstream-image", "image"),
            usage_record("upstream-chat", "chat", state="failed", cost="0.2"),
            usage_record("upstream-video", "video", cost="2.5"),
        ]
        self.settings = UpstreamUsageSettings(
            enabled=True,
            usage_url="https://usage.example.test/v1/skills/usage",
            scope="user",
            interval_secs=300,
            lookback_days=2,
            full_lookback_days=30,
            timeout_secs=20,
            page_size=2,
            lease_secs=300,
        )

        def get_usage(_url, *, params, **_kwargs):
            offset = int(params["offset"])
            limit = int(params["limit"])
            return FakeResponse(
                {
                    "from": "2026-09-15T00:00:00+08:00",
                    "to": "2026-09-17T00:00:00+08:00",
                    "scope": "user",
                    "unit": "算力",
                    "limit": limit,
                    "offset": offset,
                    "total": len(self.records),
                    "records": self.records[offset : offset + limit],
                }
            )

        self.service = UpstreamUsageService(
            self.database_url,
            settings_factory=lambda: self.settings,
            credential_loader=lambda: ["secret-key-must-not-be-stored"],
            http_get=get_usage,
            initialize_schema=True,
        )
        self.addCleanup(self.service.close)
        with self.service.engine.begin() as connection:
            connection.execute(text(
                "CREATE TABLE generation_task_events ("
                "id INTEGER PRIMARY KEY, upstream_task_id VARCHAR(255), task_id VARCHAR(191), "
                "owner_id VARCHAR(191), model VARCHAR(191), task_updated_at DATETIME)"
            ))
            connection.execute(text(
                "INSERT INTO generation_task_events "
                "(id, upstream_task_id, task_id, owner_id, model, task_updated_at) VALUES "
                "(1, 'upstream-image', 'local-image', 'user-image', 'gpt-image-2.5-flare', '2026-09-16 10:01:00')"
            ))
            connection.execute(text(
                "CREATE TABLE video_generation_records ("
                "task_key VARCHAR(383) PRIMARY KEY, upstream_task_id VARCHAR(255), "
                "task_id VARCHAR(191), owner_id VARCHAR(191), model VARCHAR(191), event_at DATETIME)"
            ))
            connection.execute(text(
                "INSERT INTO video_generation_records "
                "(task_key, upstream_task_id, task_id, owner_id, model, event_at) VALUES "
                "('user-video:local-video', 'upstream-video', 'local-video', 'user-video', 'kling-v3', "
                "'2026-09-16 10:01:00')"
            ))

    def test_sync_is_paginated_idempotent_and_associates_local_tasks(self):
        first = self.service.sync_usage(days=2)
        second = self.service.sync_usage(days=2)

        self.assertEqual(first["records_seen"], 3)
        self.assertEqual(second["records_upserted"], 3)
        with self.service.Session() as session:
            rows = session.scalars(select(UpstreamUsageRecord).order_by(UpstreamUsageRecord.upstream_task_id)).all()
            self.assertEqual(len(rows), 3)
            by_id = {row.upstream_task_id: row for row in rows}
            self.assertEqual((by_id["upstream-image"].owner_id, by_id["upstream-image"].local_task_id), ("user-image", "local-image"))
            self.assertEqual(by_id["upstream-image"].requested_model, "gpt-image-2.5-flare")
            self.assertEqual(by_id["upstream-image"].model_version, "flare")
            self.assertEqual((by_id["upstream-video"].owner_id, by_id["upstream-video"].local_task_id), ("user-video", "local-video"))
            self.assertIsNone(by_id["upstream-chat"].owner_id)
            self.assertEqual(by_id["upstream-image"].unit, "算力")
            self.assertEqual(len(by_id["upstream-image"].sync_key_fingerprint), 64)
            self.assertNotIn("secret-key", repr(by_id["upstream-image"].__dict__))

        columns = {column["name"] for column in inspect(self.service.engine).get_columns("upstream_usage_records")}
        self.assertNotIn("api_key", columns)
        self.assertNotIn("raw_response", columns)

    def test_duplicate_upstream_task_ids_are_counted_once(self):
        self.records.append(usage_record("upstream-image", "image", cost="1.5"))
        result = self.service.sync_usage(days=2)

        self.assertEqual(result["records_seen"], 4)
        self.assertEqual(result["records_upserted"], 3)
        self.assertEqual(self.service.summary()["record_count"], 3)

    def test_refund_correction_replaces_cost_and_summary_exposes_refunds(self):
        self.service.sync_usage(days=2)
        self.records[0] = usage_record(
            "upstream-image",
            "image",
            cost="0",
            refunded=True,
            refunded_amount="1.250000000001",
        )
        self.service.sync_usage(days=2)

        summary = self.service.summary(source="image")
        self.assertEqual(summary["record_count"], 1)
        self.assertEqual(summary["total_cost"], 0)
        self.assertEqual(summary["refunded_count"], 1)
        self.assertAlmostEqual(summary["refunded_amount"], 1.250000000001)
        self.assertEqual(summary["assigned_count"], 1)
        self.assertEqual(summary["unit"], "算力")

    def test_string_false_refund_value_is_not_treated_as_refunded(self):
        self.records[0] = usage_record(
            "upstream-image",
            "image",
            refunded="false",
            refunded_amount="0",
        )
        self.service.sync_usage(days=2)

        summary = self.service.summary(source="image")
        self.assertEqual(summary["refunded_count"], 0)

    def test_records_filter_by_type_and_keep_unassigned_explicit(self):
        self.service.sync_usage(days=2)
        page = self.service.list_records(source="chat", limit=20)

        self.assertEqual(page["record_count"], 1)
        self.assertEqual(page["items"][0]["owner_name"], "未归属")
        self.assertEqual(page["items"][0]["attribution_status"], "unassigned")
        self.assertEqual(page["items"][0]["state"], "failed")

    def test_summary_and_details_keep_requested_model_version(self):
        self.service.sync_usage(days=2)

        summary = self.service.summary(source="image")
        self.assertEqual(summary["models"][0]["model"], "gpt-image-2.5-flare")
        self.assertEqual(summary["models"][0]["model_version"], "flare")
        detail = self.service.list_records(source="image")["items"][0]
        self.assertEqual(detail["model"], "gpt-image-2.5-flare")
        self.assertEqual(detail["upstream_model"], "test-image")
        self.assertEqual(detail["requested_model"], "gpt-image-2.5-flare")
        self.assertEqual(detail["model_version"], "flare")

    def test_unassigned_upstream_version_is_not_merged_into_generic_image_model(self):
        self.records.append(usage_record(
            "upstream-image-unassigned",
            "image",
            model="tt-image-2.5",
            params={"version": "sunburst"},
        ))
        self.service.sync_usage(days=2)

        summary = self.service.summary(source="image")
        self.assertEqual(
            {item["model"] for item in summary["models"]},
            {"gpt-image-2.5-flare", "gpt-image-2.5-sunburst"},
        )
        unassigned = next(
            item for item in self.service.list_records(source="image", limit=20)["items"]
            if item["upstream_task_id"] == "upstream-image-unassigned"
        )
        self.assertEqual(unassigned["model"], "gpt-image-2.5-sunburst")
        self.assertEqual(unassigned["requested_model"], "")
        self.assertEqual(unassigned["model_version"], "sunburst")

    def test_status_reports_configuration_without_revealing_credentials(self):
        status = self.service.status()
        self.assertTrue(status["enabled"])
        self.assertTrue(status["configured"])
        self.assertNotIn("key", " ".join(str(value) for value in status.values()).lower())

        self.settings = replace(self.settings, enabled=False)
        self.assertFalse(self.service.status()["enabled"])


if __name__ == "__main__":
    unittest.main()
