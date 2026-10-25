from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import inspect, select, text

from services.billing.upstream_usage_models import UpstreamUsageAttribution, UpstreamUsageRecord
from services.billing.upstream_usage_service import UpstreamUsageService, UpstreamUsageSettings, _scheduler_worker


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

    def test_chat_capture_survives_periodic_sync_without_duplicate_cost(self):
        self.service.capture_chat_call(
            task_id="chat-call-1", model="tt-5.6-sol", owner_id="user-a",
            source="canvas_assistant", local_task_id="run-1", api_key="secret-key-must-not-be-stored",
        )
        self.service.capture_chat_call(
            task_id="chat-call-1", model="tt-5.6-sol", owner_id="user-b",
            source="video_agent", local_task_id="run-2", api_key="secret-key-must-not-be-stored",
        )
        self.service.capture_chat_call(
            task_id="chat-call-1", model="tt-5.6-sol", owner_id="user-a",
            source="canvas_assistant", local_task_id="run-1", api_key="secret-key-must-not-be-stored",
        )
        with self.service.Session.begin() as session:
            session.add(UpstreamUsageAttribution(
                provider="lk888", upstream_task_id="chat-call-1", owner_id="user-a",
                local_source="canvas_assistant", local_task_id="legacy-link-1",
                requested_model="gpt-5.6-sol",
            ))
        self.records.append(usage_record("chat-call-1", "chat", model="tt-5.6-sol", cost="0.345"))
        self.service.sync_usage(days=2)
        self.service.sync_usage(days=2)

        with self.service.Session() as session:
            rows = session.scalars(select(UpstreamUsageRecord).where(
                UpstreamUsageRecord.upstream_task_id == "chat-call-1"
            )).all()
            self.assertEqual(len(rows), 1)
            self.assertEqual((rows[0].owner_id, rows[0].local_source, rows[0].local_task_id),
                             ("user-a", "canvas_assistant", "run-1"))
            self.assertEqual(str(rows[0].cost), "0.345000000000")
            self.assertNotIn("secret-key-must-not-be-stored", repr(rows[0].__dict__))
        self.assertEqual(self.service.summary(source="chat")["record_count"], 2)

    def test_chat_status_settles_only_after_final_cost(self):
        task_id = "chat-call-2"
        self.service.capture_chat_call(
            task_id=task_id, model="tt-5.6-sol", owner_id="user-a",
            source="video_agent", local_task_id="turn-1", api_key="secret-key-must-not-be-stored",
        )
        payload = {"is_final": False, "cost": 0}
        calls = []

        def status_get(url, *, params, headers, **_kwargs):
            calls.append((url, params, headers))
            return FakeResponse(payload.copy())

        self.service._http_get = status_get
        with self.service.Session.begin() as session:
            row = session.scalars(select(UpstreamUsageRecord).where(
                UpstreamUsageRecord.upstream_task_id == task_id
            )).one()
            row.updated_at = datetime.now() - timedelta(seconds=5)
        self.assertEqual(self.service.reconcile_chat_usage(), 0)
        self.assertEqual(self.service.summary(source="chat")["unsettled_count"], 1)

        payload.update({
            "is_final": True, "state": "completed", "cost": "0.125",
            "channel_group": "chat-relay", "refunded": True, "refunded_amount": "0.025",
        })
        with self.service.Session.begin() as session:
            row = session.scalars(select(UpstreamUsageRecord).where(
                UpstreamUsageRecord.upstream_task_id == task_id
            )).one()
            row.updated_at = datetime.now() - timedelta(seconds=5)
        self.assertEqual(self.service.reconcile_chat_usage(), 1)
        self.assertEqual(self.service.reconcile_chat_usage(), 0)
        with self.service.Session() as session:
            row = session.scalars(select(UpstreamUsageRecord).where(
                UpstreamUsageRecord.upstream_task_id == task_id
            )).one()
            self.assertEqual((row.owner_id, row.state, row.unit), ("user-a", "completed", "算力"))
            self.assertEqual(str(row.cost), "0.125000000000")
            self.assertEqual((row.refunded, str(row.refunded_amount)), (True, "0.025000000000"))
        self.assertEqual(self.service.summary(source="chat")["unsettled_count"], 0)
        self.assertEqual(len(calls), 2)

    def test_chat_reconciliation_runs_even_when_periodic_sync_is_disabled(self):
        class StopAfterOnePass:
            stopped = False

            def is_set(self):
                return self.stopped

            def wait(self, _seconds):
                self.stopped = True

        self.settings = replace(self.settings, enabled=False)
        with (
            patch.object(self.service, "reconcile_chat_usage", return_value=0) as reconcile,
            patch.object(self.service, "sync_usage") as sync,
        ):
            _scheduler_worker(StopAfterOnePass(), self.service)
        reconcile.assert_called_once()
        sync.assert_not_called()

    def test_invalid_final_chat_cost_is_not_recorded_as_zero(self):
        self.service.capture_chat_call(
            task_id="bad-cost", model="tt-5.6-sol", owner_id="user-a",
            source="canvas_assistant", api_key="secret-key-must-not-be-stored",
        )
        self.service._http_get = lambda *_args, **_kwargs: FakeResponse({"is_final": True, "cost": None})
        with self.service.Session.begin() as session:
            row = session.scalars(select(UpstreamUsageRecord).where(
                UpstreamUsageRecord.upstream_task_id == "bad-cost"
            )).one()
            row.updated_at = datetime.now() - timedelta(seconds=5)
        self.assertEqual(self.service.reconcile_chat_usage(), 0)
        with self.service.Session() as session:
            row = session.scalars(select(UpstreamUsageRecord).where(
                UpstreamUsageRecord.upstream_task_id == "bad-cost"
            )).one()
            self.assertEqual(row.state, "settling")
            self.assertEqual(row.cost, 0)


if __name__ == "__main__":
    unittest.main()
