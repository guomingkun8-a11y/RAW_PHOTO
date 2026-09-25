from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session

from services.video.video_generation_monitoring_service import VideoGenerationMonitoringService
from services.video.video_generation_records import RecordsBase, sync_record


class FakeVideoTaskStore:
    def __init__(self, tasks: list[dict], database_url: str) -> None:
        self.tasks = tasks
        self.database_url = database_url

    def list_tasks(self, _owner_id=None, task_ids=None, *, conversation_id="", limit=None):
        raise AssertionError("monitoring must never scan task JSON")


class FakeVideoTaskService:
    def __init__(self, tasks: list[dict], database_url: str) -> None:
        self.task_store = FakeVideoTaskStore(tasks, database_url)


class VideoGenerationMonitoringServiceTests(unittest.TestCase):
    def make_service(self, tasks: list[dict]):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp_dir.cleanup)
        database_url = f"sqlite:///{Path(self.tmp_dir.name) / 'video-monitoring.db'}"
        engine = create_engine(database_url)
        self.addCleanup(engine.dispose)
        RecordsBase.metadata.create_all(engine)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE business_users ("
                    "id TEXT PRIMARY KEY, username TEXT NOT NULL, name TEXT NOT NULL, "
                    "role TEXT NOT NULL, enabled INTEGER NOT NULL, last_login_at TEXT NULL, "
                    "created_at TEXT NOT NULL)"
                )
            )
            connection.execute(
                text(
                    "CREATE TABLE business_user_sessions ("
                    "user_id TEXT NOT NULL, revoked_at TEXT NULL, expires_at TEXT NOT NULL, "
                    "last_used_at TEXT NULL, created_at TEXT NOT NULL)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO business_users "
                    "(id, username, name, role, enabled, last_login_at, created_at) VALUES "
                    "('video-user', 'video_user', '视频用户', 'user', 1, NULL, '2026-01-01 00:00:00')"
                )
            )
        with Session(engine) as session:
            for task in tasks:
                sync_record(session, f"{task['owner_id']}:{task['id']}", task)
            session.commit()
        service = VideoGenerationMonitoringService(
            task_service=FakeVideoTaskService(tasks, database_url),
            database_url=database_url,
        )
        self.addCleanup(service.engine.dispose)
        return service

    def tearDown(self):
        service = getattr(self, "service", None)
        if service is not None and service.engine is not None:
            service.engine.dispose()

    def test_summary_keeps_video_counts_and_costs_separate(self):
        service = self.make_service(
            [
                {
                    "id": "video-success",
                    "owner_id": "video-user",
                    "status": "success",
                    "mode": "text_to_video",
                    "model": "video-model-a",
                    "cost": 2.5,
                    "duration_ms": 1200,
                    "created_at": "2026-08-01 10:00:00",
                    "updated_at": "2026-08-01 10:01:00",
                    "video_url": "https://cdn.example.test/video.mp4",
                },
                {
                    "id": "video-error",
                    "owner_id": "video-user",
                    "status": "error",
                    "model": "video-model-a",
                    "cost": 99,
                    "duration_ms": 500,
                    "created_at": "2026-08-01 10:02:00",
                    "updated_at": "2026-08-01 10:03:00",
                    "error": "upstream failed",
                },
                {
                    "id": "video-queued",
                    "owner_id": "video-user",
                    "status": "queued",
                    "model": "video-model-b",
                    "created_at": "2026-08-01 10:04:00",
                    "updated_at": "2026-08-01 10:04:00",
                },
            ]
        )

        self.service = service
        summary = service.summary(
            {
                "enabled": True,
                "queue_enabled": True,
                "queue_depth": 1,
                "queued_tasks": 1,
                "running_tasks": 0,
                "active_workers": 1,
                "worker_concurrency": 2,
                "owner_concurrency": 1,
                "owner_pending_limit": 8,
            },
            start_at=datetime(2026, 8, 1, 9, 0),
            end_at=datetime(2026, 8, 1, 11, 0),
        )

        self.assertEqual(summary["source"], "video")
        self.assertEqual(summary["total_success"], 1)
        self.assertEqual(summary["total_failed"], 1)
        self.assertEqual(summary["total_cost"], 101.5)
        self.assertEqual(summary["cost_count"], 2)
        self.assertEqual(summary["models"], [{
            "model": "video-model-a",
            "cost_count": 2,
            "cost_total": 101.5,
            "cost_average": 50.75,
        }])
        self.assertEqual(summary["task_queue"]["queue_depth"], 1)
        self.assertEqual(summary["users"][0]["name"], "视频用户")
        self.assertEqual(summary["users"][0]["success_count"], 1)
        self.assertEqual(summary["users"][0]["failed_count"], 1)
        self.assertEqual(summary["users"][0]["cost_total"], 101.5)

    def test_task_details_filters_video_rows_and_exposes_video_url(self):
        service = self.make_service(
            [
                {
                    "id": "video-inside",
                    "owner_id": "video-user",
                    "status": "success",
                    "mode": "image_to_video",
                    "model": "video-model",
                    "cost": 1.25,
                    "created_at": "2026-08-02 10:00:00",
                    "updated_at": "2026-08-02 10:10:00",
                    "video_url": "https://cdn.example.test/inside.mp4",
                    "cover_url": "https://cdn.example.test/inside.jpg",
                    "upstream_task_id": "upstream-1",
                },
                {
                    "id": "video-outside",
                    "owner_id": "video-user",
                    "status": "success",
                    "model": "video-model",
                    "cost": 3,
                    "created_at": "2026-08-02 12:00:00",
                    "updated_at": "2026-08-02 12:10:00",
                },
            ]
        )

        self.service = service
        details = service.task_details(
            start_at=datetime(2026, 8, 2, 9, 0),
            end_at=datetime(2026, 8, 2, 11, 0),
            owner_id="video-user",
            status="success",
            limit=10,
        )

        self.assertEqual(details["source"], "video")
        self.assertEqual(details["record_count"], 1)
        self.assertEqual(details["media_count"], 1)
        self.assertEqual(details["cost_total"], 1.25)
        self.assertEqual(details["items"][0]["source_type"], "video")
        self.assertEqual(details["items"][0]["video_url"], "https://cdn.example.test/inside.mp4")
        self.assertEqual(details["items"][0]["cover_url"], "https://cdn.example.test/inside.jpg")
        self.assertEqual(details["items"][0]["upstream_task_id"], "upstream-1")

    @staticmethod
    def task(task_id, **updates):
        return {
            "id": task_id, "owner_id": "video-user", "status": "success", "model": "video-model",
            "cost": "0.1", "duration_ms": 100,
            "created_at": "2026-09-01 10:00:00", "updated_at": "2026-09-01 10:10:00",
            "upstream_task_id": "upstream-1", "credential_id": "internal-only",
            "video_url": "https://example.test/video.mp4", **updates,
        }

    def test_known_costs_include_failed_canceled_and_unsettled_running_tasks(self):
        service = self.make_service([
            self.task("success"), self.task("error", status="error", cost="0.2"),
            self.task("canceled", status="canceled", cost="0.3"),
            self.task("running", status="running", cancel_requested=True, cost="0.4"),
            self.task("unknown", status="error", cost=None),
            self.task("zero", status="canceled", cost="0"),
        ])
        summary = service.summary()
        details = service.task_details()
        self.assertEqual(summary["total_cost"], 1.0)
        self.assertEqual(summary["total_cost"], details["cost_total"])
        self.assertEqual(summary["cost_count"], details["cost_count"])
        self.assertEqual(summary["cost_count"], 5)
        self.assertEqual(summary["reconciliation_required_count"], 0)
        self.assertEqual((summary["total_success"], summary["total_failed"], summary["total_canceled"]), (1, 2, 2))
        self.assertEqual(summary["task_queue"]["running_tasks"], 1)
        self.assertEqual(service.task_details(status="canceled")["record_count"], 2)
        self.assertNotIn("internal-only", repr(details))

    def test_monitoring_exposes_reconciliation_required_counts_and_details(self):
        service = self.make_service([
            self.task("unsettled", status="error", reconciliation_required=True),
            self.task("settled", status="success", reconciliation_required=False),
        ])
        summary = service.summary()
        details = service.task_details()
        self.assertEqual(summary["reconciliation_required_count"], 1)
        self.assertEqual(summary["users"][0]["reconciliation_required_count"], 1)
        unsettled = next(item for item in details["items"] if item["task_id"] == "unsettled")
        self.assertTrue(unsettled["reconciliation_required"])

    def test_history_delete_preserves_accounting_and_hides_stale_media(self):
        task = self.task("deleted", cost="2.5")
        service = self.make_service([task])
        before = service.summary()
        with service.engine.begin() as connection:
            connection.execute(text("CREATE TABLE video_generation_tasks (key TEXT PRIMARY KEY)"))
            connection.execute(text("INSERT INTO video_generation_tasks VALUES ('video-user:deleted')"))
        with service.Session.begin() as session:
            sync_record(session, "video-user:deleted", {
                **task, "cost": None, "history_deleted": True, "updated_at": "2026-09-02 10:00:00",
            })
            session.execute(text("DELETE FROM video_generation_tasks WHERE key = 'video-user:deleted'"))
        service._summary_cache.clear()
        after = service.summary()
        self.assertEqual((before["total_cost"], before["cost_count"]), (after["total_cost"], after["cost_count"]))
        item = service.task_details()["items"][0]
        self.assertTrue(item["history_deleted"])
        self.assertEqual(item["video_url"], "")
        self.assertEqual(item["completed_at"], "2026-09-01 10:10:00")
        self.assertEqual(item["cost"], 2.5)

    def test_pagination_filters_in_sql_with_totals_for_the_whole_range(self):
        service = self.make_service([
            self.task(str(i)) for i in range(4)
        ] + [self.task("outside", updated_at="2026-09-02 10:00:00"), self.task("other", owner_id="another-user")])
        filters = {"owner_id": "video-user", "start_at": datetime(2026, 9, 1), "end_at": datetime(2026, 9, 2), "limit": 2}
        statements = []
        event.listen(service.engine, "before_cursor_execute", lambda conn, cur, stmt, params, ctx, many: statements.append(stmt))
        first = service.task_details(**filters)
        second = service.task_details(**filters, cursor=first["next_cursor"])
        self.assertEqual(first["record_count"], 4)
        self.assertEqual(first["cost_total"], 0.4)
        self.assertEqual(second["cost_total"], 0.4)
        self.assertTrue(first["truncated"])
        self.assertFalse(second["truncated"])
        self.assertEqual([row["task_id"] for row in first["items"] + second["items"]], ["3", "2", "1", "0"])
        self.assertTrue(any("LIMIT" in stmt for stmt in statements))
        self.assertTrue(any("sum(" in stmt.lower() for stmt in statements))
        self.assertFalse(any("task_json" in stmt or "credential_id" in stmt for stmt in statements))

        cost_page = service.task_details(owner_id="video-user", status="all", limit=1, offset=1, cost_only=True)
        self.assertEqual(cost_page["record_count"], 5)
        self.assertEqual(cost_page["offset"], 1)
        self.assertEqual(len(cost_page["items"]), 1)
        self.assertIsNotNone(cost_page["items"][0]["cost"])

    def test_summary_uses_sql_aggregation_and_cache_before_queries(self):
        service = self.make_service([self.task("a", duration_ms=100), self.task("b", duration_ms=300)])
        statements = []
        event.listen(service.engine, "before_cursor_execute", lambda conn, cur, stmt, params, ctx, many: statements.append(stmt))
        first = service.summary()
        self.assertEqual(first["task_latency"], {"sample_size": 2, "average_ms": 200.0, "p95_ms": 290, "max_ms": 300})
        self.assertTrue(any("GROUP BY" in stmt for stmt in statements))
        self.assertFalse(any("task_json" in stmt for stmt in statements))
        first["users"][0]["cost_total"] = 1000
        first["task_latency"]["sample_size"] = 0
        with mock.patch.object(service, "_session", side_effect=AssertionError("cache hit must not query")):
            second = service.summary()
        self.assertEqual(second["users"][0]["cost_total"], 0.2)
        self.assertEqual(second["task_latency"]["sample_size"], 2)

    def test_monitoring_survives_missing_accounts_and_uses_identity_snapshot(self):
        service = self.make_service([self.task("a", identity={"username": "former-user", "name": "Former User"})])
        with service.engine.begin() as connection:
            connection.execute(text("DROP TABLE business_user_sessions"))
            connection.execute(text("DROP TABLE business_users"))
        summary = service.summary()
        self.assertEqual(summary["total_users"], 0)
        self.assertEqual(summary["total_cost"], 0.1)
        self.assertEqual(summary["users"][0]["name"], "Former User")

    def test_queue_uses_global_capacity_not_single_worker_concurrency(self):
        service = self.make_service([])
        queue = service.summary({"queue_enabled": True, "worker_concurrency": 2, "total_concurrency": 8, "active_slots": 5})["task_queue"]
        self.assertEqual((queue["worker_concurrency"], queue["total_concurrency"], queue["slot_limit"], queue["active_slots"]), (2, 8, 8, 5))


if __name__ == "__main__":
    unittest.main()
