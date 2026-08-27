from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from sqlalchemy import text

from services.image import generation_monitoring_service as monitoring_module
from services.image.generation_monitoring_service import GenerationMonitoringService


class GenerationMonitoringServiceTests(unittest.TestCase):
    def test_task_reference_images_returns_all_stored_images(self):
        task = {
            "payload": {
                "images": [
                    {"url": f"https://cdn.example.test/stored-{index}.png"}
                    for index in range(6)
                ],
                "image_urls": [
                    f"https://cdn.example.test/original-{index}.png"
                    for index in range(6)
                ],
            }
        }

        references = monitoring_module._task_reference_images(task)

        self.assertEqual(len(references), 6)
        self.assertEqual(references[0]["preview_url"], "https://cdn.example.test/stored-0.png")
        self.assertEqual(references[-1]["preview_url"], "https://cdn.example.test/stored-5.png")

    def test_task_reference_images_falls_back_to_all_urls(self):
        task = {
            "payload": {
                "images": [],
                "image_urls": [
                    f"https://cdn.example.test/reference-{index}.png"
                    for index in range(7)
                ],
            }
        }

        references = monitoring_module._task_reference_images(task)

        self.assertEqual(len(references), 7)
        self.assertEqual(references[-1]["preview_url"], "https://cdn.example.test/reference-6.png")

    def test_cancellation_reports_are_not_counted_as_failures(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = GenerationMonitoringService(f"sqlite:///{Path(tmp_dir) / 'monitoring.db'}")
            try:
                result = service.report_frontend_failure(
                    identity={"id": "user-1"},
                    task_id="task-canceled-1",
                    error="任务已中止",
                    image_count=3,
                    mode="generate",
                    model="gpt-image-2",
                )
                self.assertTrue(result["ignored"])
                with service.engine.begin() as connection:
                    row = connection.execute(
                        text(
                            "SELECT status, image_count, failure_reported_at "
                            "FROM generation_task_events WHERE task_id = :task_id"
                        ),
                        {"task_id": "task-canceled-1"},
                    ).mappings().one()
                    failed_count = connection.execute(
                        text(
                            "SELECT COUNT(*) FROM generation_task_events "
                            "WHERE status = 'error' AND failure_reported_at IS NOT NULL"
                        )
                    ).scalar_one()
                self.assertEqual(row["status"], "canceled")
                self.assertEqual(row["image_count"], 3)
                self.assertIsNone(row["failure_reported_at"])
                self.assertEqual(failed_count, 0)

                service.report_frontend_failure(
                    identity={"id": "user-1"},
                    task_id="task-canceled-1",
                    error="upstream failed after user cancel",
                    image_count=3,
                    mode="generate",
                    model="gpt-image-2",
                )
                with service.engine.begin() as connection:
                    row = connection.execute(
                        text(
                            "SELECT status, failure_reported_at "
                            "FROM generation_task_events WHERE task_id = :task_id"
                        ),
                        {"task_id": "task-canceled-1"},
                    ).mappings().one()
                self.assertEqual(row["status"], "canceled")
                self.assertIsNone(row["failure_reported_at"])
            finally:
                service.engine.dispose()

    def test_late_cancellation_report_clears_previous_failure(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = GenerationMonitoringService(f"sqlite:///{Path(tmp_dir) / 'monitoring.db'}")
            try:
                service.report_frontend_failure(
                    identity={"id": "user-1"},
                    task_id="task-race-1",
                    error="upstream failed",
                    image_count=1,
                    mode="generate",
                    model="gpt-image-2",
                )
                service.report_frontend_failure(
                    identity={"id": "user-1"},
                    task_id="task-race-1",
                    error="用户取消生成",
                    image_count=1,
                    mode="generate",
                    model="gpt-image-2",
                )
                with service.engine.begin() as connection:
                    row = connection.execute(
                        text(
                            "SELECT status, failure_reported_at "
                            "FROM generation_task_events WHERE task_id = :task_id"
                        ),
                        {"task_id": "task-race-1"},
                    ).mappings().one()
                self.assertEqual(row["status"], "canceled")
                self.assertIsNone(row["failure_reported_at"])
            finally:
                service.engine.dispose()

    def test_retry_failures_share_one_failure_report(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = GenerationMonitoringService(f"sqlite:///{Path(tmp_dir) / 'monitoring.db'}")
            try:
                service.report_frontend_failure(
                    identity={"id": "user-1"},
                    task_id="task-original",
                    failure_report_id="image-slot-1",
                    error="upstream failed",
                    image_count=1,
                    mode="generate",
                    model="gpt-image-2",
                )
                service.report_frontend_failure(
                    identity={"id": "user-1"},
                    task_id="task-retry",
                    failure_report_id="image-slot-1",
                    error="upstream failed again",
                    image_count=1,
                    mode="generate",
                    model="gpt-image-2",
                )

                with service.engine.begin() as connection:
                    failed_count = connection.execute(
                        text(
                            "SELECT SUM(image_count) FROM generation_task_events "
                            "WHERE status = 'error' AND failure_reported_at IS NOT NULL"
                        )
                    ).scalar_one()
                    rows = connection.execute(
                        text("SELECT task_id FROM generation_task_events WHERE owner_id = :owner_id"),
                        {"owner_id": "user-1"},
                    ).mappings().all()

                self.assertEqual(failed_count, 1)
                self.assertEqual([row["task_id"] for row in rows], ["image-slot-1"])
            finally:
                service.engine.dispose()

    def test_record_task_event_stores_cost_metadata(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = GenerationMonitoringService(f"sqlite:///{Path(tmp_dir) / 'monitoring.db'}")
            try:
                service.record_task_event(
                    {
                        "id": "task-cost-1",
                        "owner_id": "user-1",
                        "status": "success",
                        "mode": "generate",
                        "model": "gpt-image-2",
                        "image_count": 1,
                        "cost": "1.375",
                        "upstream_task_id": "media-task-1",
                        "created_at": "2026-08-02 10:00:00",
                        "updated_at": "2026-08-02 10:00:02",
                    }
                )

                with service.engine.begin() as connection:
                    row = connection.execute(
                        text(
                            "SELECT cost, upstream_task_id FROM generation_task_events "
                            "WHERE owner_id = :owner_id AND task_id = :task_id"
                        ),
                        {"owner_id": "user-1", "task_id": "task-cost-1"},
                    ).mappings().one()

                self.assertAlmostEqual(row["cost"], 1.375)
                self.assertEqual(row["upstream_task_id"], "media-task-1")
            finally:
                service.engine.dispose()

    def test_summary_merges_queue_activity_and_failure_stats(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = GenerationMonitoringService(f"sqlite:///{Path(tmp_dir) / 'monitoring.db'}")
            try:
                now = datetime.now()
                active_seen_at = now - timedelta(minutes=1)
                active_login_at = now - timedelta(hours=1)
                created_at = now - timedelta(days=1)
                expires_at = now + timedelta(days=1)
                with service.engine.begin() as connection:
                    connection.execute(
                        text(
                            "CREATE TABLE business_users ("
                            "id TEXT PRIMARY KEY, "
                            "username TEXT NOT NULL, "
                            "name TEXT NOT NULL, "
                            "role TEXT NOT NULL, "
                            "enabled INTEGER NOT NULL, "
                            "last_login_at TEXT NULL, "
                            "created_at TEXT NOT NULL)"
                        )
                    )
                    connection.execute(
                        text(
                            "CREATE TABLE business_user_sessions ("
                            "user_id TEXT NOT NULL, "
                            "revoked_at TEXT NULL, "
                            "expires_at TEXT NOT NULL, "
                            "last_used_at TEXT NULL, "
                            "created_at TEXT NOT NULL)"
                        )
                    )
                    connection.execute(
                        text(
                            "CREATE TABLE generated_images ("
                            "owner_id TEXT NOT NULL, "
                            "task_id TEXT NOT NULL, "
                            "deleted_at TEXT NULL, "
                            "created_at TEXT NOT NULL)"
                        )
                    )
                    connection.execute(
                        text(
                            "INSERT INTO business_users (id, username, name, role, enabled, last_login_at, created_at) "
                            "VALUES (:id, :username, :name, :role, :enabled, :last_login_at, :created_at)"
                        ),
                            {
                                "id": "user-1",
                                "username": "alice",
                                "name": "Alice",
                                "role": "admin",
                                "enabled": 1,
                                "last_login_at": active_login_at.strftime("%Y-%m-%d %H:%M:%S"),
                                "created_at": created_at.strftime("%Y-%m-%d %H:%M:%S"),
                            },
                    )
                    connection.execute(
                        text(
                            "INSERT INTO business_user_sessions (user_id, revoked_at, expires_at, last_used_at, created_at) "
                            "VALUES (:user_id, :revoked_at, :expires_at, :last_used_at, :created_at)"
                        ),
                        {
                            "user_id": "user-1",
                            "revoked_at": None,
                            "expires_at": expires_at.strftime("%Y-%m-%d %H:%M:%S"),
                            "last_used_at": active_seen_at.strftime("%Y-%m-%d %H:%M:%S"),
                            "created_at": created_at.strftime("%Y-%m-%d %H:%M:%S"),
                        },
                    )
                    connection.execute(
                        text(
                            "INSERT INTO generated_images (owner_id, task_id, deleted_at, created_at) "
                            "VALUES (:owner_id, :task_id, :deleted_at, :created_at)"
                        ),
                        {
                            "owner_id": "user-1",
                            "task_id": "task-success-1",
                            "deleted_at": None,
                            "created_at": "2026-07-18 10:00:01",
                        },
                    )

                service.record_task_event(
                    {
                        "id": "task-success-1",
                        "owner_id": "user-1",
                        "status": "success",
                        "mode": "generate",
                        "model": "gpt-image-2",
                        "cost": 1.25,
                        "upstream_task_id": "upstream-success-1",
                        "duration_ms": 100,
                        "stage_timings_ms": {"upload": 20, "queue": 10, "generation": 65, "save": 5},
                        "created_at": "2026-07-18 10:00:00",
                        "updated_at": "2026-07-18 10:00:01",
                    }
                )
                service.record_task_event(
                    {
                        "id": "task-success-2",
                        "owner_id": "user-1",
                        "status": "success",
                        "mode": "generate",
                        "model": "banana-2",
                        "cost": "0.75",
                        "upstream_task_id": "upstream-success-2",
                        "duration_ms": 300,
                        "stage_timings_ms": {"upload": 40, "queue": 30, "generation": 220, "save": 10},
                        "created_at": "2026-07-18 10:01:00",
                        "updated_at": "2026-07-18 10:01:01",
                    }
                )
                service.report_frontend_failure(
                    identity={"id": "user-1"},
                    task_id="task-failed-1",
                    error="boom",
                    image_count=2,
                    mode="generate",
                    model="gpt-image-2",
                )

                summary = service.summary(
                    {
                        "enabled": True,
                        "executor": "redis",
                        "queue_depth": 5,
                        "queued_tasks": 2,
                        "running_tasks": 1,
                        "stale_running_tasks": 0,
                        "active_slots": 1,
                        "slot_limit": 4,
                        "active_workers": 2,
                        "worker_concurrency": 3,
                        "local_concurrency_limit": 2,
                        "configured_total_concurrency": 4,
                        "total_concurrency": 4,
                        "owner_concurrency": 2,
                        "owner_pending_limit": 10,
                        "stale_running_timeout_secs": 1800,
                        "worker_heartbeat_secs": 30,
                        "owner_activity": [
                            {
                                "owner_id": "user-1",
                                "queued_tasks": 2,
                                "running_tasks": 1,
                                "active_tasks": 3,
                            }
                        ],
                    }
                )

                self.assertEqual(summary["online_users"], 1)
                self.assertEqual(summary["active_sessions"], 1)
                self.assertEqual(summary["total_success"], 2)
                self.assertEqual(summary["total_failed"], 2)
                self.assertEqual(summary["total_cost"], 2.0)
                self.assertEqual(summary["cost_count"], 2)
                self.assertEqual(
                    summary["models"],
                    [
                        {
                            "model": "gpt-image-2",
                            "cost_count": 1,
                            "cost_total": 1.25,
                            "cost_average": 1.25,
                        },
                        {
                            "model": "banana-2",
                            "cost_count": 1,
                            "cost_total": 0.75,
                            "cost_average": 0.75,
                        },
                    ],
                )
                self.assertEqual(summary["task_queue"]["queue_depth"], 5)
                self.assertEqual(summary["task_queue"]["total_concurrency"], 4)
                self.assertEqual(summary["task_queue"]["owner_concurrency"], 2)
                self.assertEqual(summary["task_latency"]["sample_size"], 2)
                self.assertEqual(summary["task_latency"]["average_ms"], 200.0)
                self.assertEqual(summary["task_latency"]["p95_ms"], 290)
                self.assertEqual(summary["stage_latency"]["upload"]["average_ms"], 30.0)
                self.assertEqual(summary["stage_latency"]["generation"]["max_ms"], 220)
                self.assertEqual(summary["users"][0]["queued_tasks"], 2)
                self.assertEqual(summary["users"][0]["running_tasks"], 1)
                self.assertEqual(summary["users"][0]["active_tasks"], 3)
                self.assertEqual(summary["users"][0]["failed_count"], 2)
                self.assertEqual(summary["users"][0]["cost_total"], 2.0)
                self.assertEqual(summary["users"][0]["cost_count"], 2)
                self.assertEqual(summary["users"][0]["cost_average"], 1.0)
            finally:
                service.engine.dispose()

    def test_summary_filters_history_by_half_open_range_without_filtering_queue(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = GenerationMonitoringService(f"sqlite:///{Path(tmp_dir) / 'monitoring.db'}")
            try:
                with service.engine.begin() as connection:
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
                            "CREATE TABLE generated_images ("
                            "owner_id TEXT NOT NULL, task_id TEXT NOT NULL, deleted_at TEXT NULL, "
                            "created_at TEXT NOT NULL)"
                        )
                    )
                    connection.execute(
                        text(
                            "INSERT INTO business_users "
                            "(id, username, name, role, enabled, last_login_at, created_at) "
                            "VALUES ('user-1', 'alice', 'Alice', 'user', 1, NULL, '2026-01-01 00:00:00')"
                        )
                    )
                    connection.execute(
                        text(
                            "INSERT INTO generated_images (owner_id, task_id, deleted_at, created_at) VALUES "
                            "('user-1', 'image-before', NULL, '2026-08-01 09:59:59'), "
                            "('user-1', 'image-at-start', NULL, '2026-08-01 10:00:00'), "
                            "('user-1', 'image-at-end', NULL, '2026-08-01 12:00:00')"
                        )
                    )
                    connection.execute(
                        text(
                            "INSERT INTO generation_task_events "
                            "(task_id, owner_id, status, mode, image_count, duration_ms, failure_reported_at, "
                            " task_created_at, task_updated_at, created_at, updated_at) VALUES "
                            "('event-success-inside', 'user-1', 'success', 'generate', 2, 200, NULL, "
                            " '2026-08-01 10:30:00', '2026-08-01 11:00:00', '2026-08-01 10:30:00', '2026-08-01 11:00:00'), "
                            "('event-success-before', 'user-1', 'success', 'generate', 5, 100, NULL, "
                            " '2026-08-01 09:00:00', '2026-08-01 09:59:59', '2026-08-01 09:00:00', '2026-08-01 09:59:59'), "
                            "('event-failed-inside', 'user-1', 'error', 'generate', 3, 300, '2026-08-01 11:30:00', "
                            " '2026-08-01 11:00:00', '2026-08-01 11:30:00', '2026-08-01 11:00:00', '2026-08-01 11:30:00'), "
                            "('event-failed-at-end', 'user-1', 'error', 'generate', 7, 400, '2026-08-01 12:00:00', "
                            " '2026-08-01 11:30:00', '2026-08-01 12:00:00', '2026-08-01 11:30:00', '2026-08-01 12:00:00')"
                        )
                    )

                queue_snapshot = {
                    "enabled": True,
                    "queue_depth": 4,
                    "queued_tasks": 3,
                    "running_tasks": 1,
                    "owner_activity": [
                        {"owner_id": "user-1", "queued_tasks": 3, "running_tasks": 1, "active_tasks": 4}
                    ],
                }
                summary = service.summary(
                    queue_snapshot,
                    start_at=datetime(2026, 8, 1, 10, 0, 0),
                    end_at=datetime(2026, 8, 1, 12, 0, 0),
                )

                self.assertEqual(summary["total_success"], 3)
                self.assertEqual(summary["total_failed"], 3)
                self.assertEqual(summary["task_latency"]["sample_size"], 2)
                self.assertEqual(summary["task_queue"]["queue_depth"], 4)
                self.assertEqual(summary["users"][0]["success_count"], 3)
                self.assertEqual(summary["users"][0]["failed_count"], 3)
                self.assertEqual(summary["users"][0]["active_tasks"], 4)
                self.assertEqual(summary["range"]["start_at"], "2026-08-01 10:00:00")
                self.assertEqual(summary["range"]["end_at"], "2026-08-01 12:00:00")

                all_time = service.summary(queue_snapshot)
                self.assertEqual(all_time["total_success"], 10)
                self.assertEqual(all_time["total_failed"], 10)
                self.assertEqual(all_time["task_queue"]["queue_depth"], 4)
            finally:
                service.engine.dispose()

    def test_task_details_match_summary_range_and_deduplicate_library_successes(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = GenerationMonitoringService(f"sqlite:///{Path(tmp_dir) / 'monitoring.db'}")
            try:
                with service.engine.begin() as connection:
                    connection.execute(
                        text(
                            "CREATE TABLE generated_images ("
                            "id INTEGER PRIMARY KEY, task_id TEXT NOT NULL, owner_id TEXT NOT NULL, "
                            "mode TEXT NULL, model TEXT NULL, duration_ms INTEGER NULL, image_url TEXT NOT NULL, "
                            "deleted_at TEXT NULL, created_at TEXT NOT NULL)"
                        )
                    )
                    connection.execute(
                        text(
                            "INSERT INTO generated_images "
                            "(id, task_id, owner_id, mode, model, duration_ms, image_url, deleted_at, created_at) VALUES "
                            "(1, 'saved-success', 'user-1', 'generate', 'image-model', 120, 'https://example.test/1.png', NULL, '2026-08-01 10:15:00'), "
                            "(2, 'outside-success', 'user-1', 'generate', 'image-model', 140, 'https://example.test/2.png', NULL, '2026-08-01 12:00:00')"
                        )
                    )
                    connection.execute(
                        text(
                            "INSERT INTO generation_task_events "
                            "(task_id, owner_id, status, mode, model, image_count, cost, upstream_task_id, duration_ms, error, failure_reported_at, "
                            " task_created_at, task_updated_at, created_at, updated_at) VALUES "
                            "('saved-success', 'user-1', 'success', 'generate', 'image-model', 1, 1.5, 'media-saved', 120, NULL, NULL, "
                            " '2026-08-01 10:00:00', '2026-08-01 10:15:00', '2026-08-01 10:00:00', '2026-08-01 10:15:00'), "
                            "('event-success', 'user-1', 'success', 'generate', 'image-model', 2, 2.25, 'media-event', 220, NULL, NULL, "
                            " '2026-08-01 10:30:00', '2026-08-01 11:00:00', '2026-08-01 10:30:00', '2026-08-01 11:00:00'), "
                            "('event-error', 'user-1', 'error', 'generate', 'image-model', 3, NULL, NULL, 320, 'boom', '2026-08-01 11:30:00', "
                            " '2026-08-01 11:00:00', '2026-08-01 11:30:00', '2026-08-01 11:00:00', '2026-08-01 11:30:00')"
                        )
                    )

                details = service.task_details(
                    start_at=datetime(2026, 8, 1, 10, 0),
                    end_at=datetime(2026, 8, 1, 12, 0),
                    owner_id="user-1",
                    status="all",
                    limit=10,
                )

                self.assertEqual(details["record_count"], 3)
                self.assertEqual(details["image_count"], 6)
                self.assertEqual(details["cost_total"], 3.75)
                self.assertEqual(details["cost_count"], 2)
                self.assertEqual([item["task_id"] for item in details["items"]], [
                    "event-error",
                    "event-success",
                    "saved-success",
                ])
                self.assertEqual(details["items"][0]["error"], "boom")
                self.assertIsNone(details["items"][0]["cost"])
                self.assertEqual(details["items"][1]["cost"], 2.25)
                self.assertEqual(details["items"][1]["upstream_task_id"], "media-event")
                self.assertEqual(details["items"][2]["cost"], 1.5)
                self.assertEqual(details["items"][2]["upstream_task_id"], "media-saved")
                self.assertEqual(details["items"][2]["image_url"], "https://example.test/1.png")
                self.assertFalse(details["truncated"])

                failures = service.task_details(
                    start_at=datetime(2026, 8, 1, 10, 0),
                    end_at=datetime(2026, 8, 1, 12, 0),
                    owner_id="user-1",
                    status="error",
                )
                self.assertEqual(failures["record_count"], 1)
                self.assertEqual(failures["image_count"], 3)
                self.assertEqual(failures["cost_total"], 0)
                self.assertEqual(failures["cost_count"], 0)
            finally:
                service.engine.dispose()

    def test_task_details_can_include_reference_images(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = GenerationMonitoringService(f"sqlite:///{Path(tmp_dir) / 'monitoring.db'}")
            try:
                with service.engine.begin() as connection:
                    connection.execute(
                        text(
                            "CREATE TABLE generated_images ("
                            "id INTEGER PRIMARY KEY, task_id TEXT NOT NULL, owner_id TEXT NOT NULL, "
                            "mode TEXT NULL, model TEXT NULL, duration_ms INTEGER NULL, image_url TEXT NOT NULL, "
                            "deleted_at TEXT NULL, created_at TEXT NOT NULL)"
                        )
                    )
                    connection.execute(
                        text(
                            "INSERT INTO generation_task_events "
                            "(task_id, owner_id, status, mode, model, image_count, duration_ms, error, failure_reported_at, "
                            " task_created_at, task_updated_at, created_at, updated_at) VALUES "
                            "('event-success', 'user-1', 'success', 'edit', 'image-model', 1, 220, NULL, NULL, "
                            " '2026-08-01 10:30:00', '2026-08-01 11:00:00', '2026-08-01 10:30:00', '2026-08-01 11:00:00')"
                        )
                    )

                reference_images = [
                    {
                        "preview_url": "https://example.test/reference.png",
                        "filename": "reference.png",
                        "mime_type": "image/png",
                        "role": "product_anchor",
                        "kind": "url",
                        "rel": "",
                    }
                ]
                with mock.patch.object(
                    monitoring_module,
                    "_reference_images_by_task",
                    return_value={("user-1", "event-success"): reference_images},
                ) as references_by_task:
                    details = service.task_details(
                        start_at=datetime(2026, 8, 1, 10, 0),
                        end_at=datetime(2026, 8, 1, 12, 0),
                        owner_id="user-1",
                        status="success",
                        include_references=True,
                    )

                references_by_task.assert_called_once()
                self.assertEqual(details["record_count"], 1)
                self.assertEqual(details["items"][0]["reference_images"], reference_images)
            finally:
                service.engine.dispose()

    def test_canceled_event_clears_previous_failure_report(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = GenerationMonitoringService(f"sqlite:///{Path(tmp_dir) / 'monitoring.db'}")
            try:
                service.report_frontend_failure(
                    identity={"id": "user-1"},
                    task_id="task-late-cancel",
                    error="upstream api failed",
                    image_count=2,
                    mode="generate",
                    model="gpt-image-2",
                )

                service.record_task_event(
                    {
                        "id": "task-late-cancel",
                        "owner_id": "user-1",
                        "status": "canceled",
                        "mode": "generate",
                        "model": "gpt-image-2",
                        "image_count": 2,
                        "duration_ms": 1800,
                        "error": "任务已中止",
                        "created_at": "2026-08-02 10:00:00",
                        "updated_at": "2026-08-02 10:00:02",
                    }
                )

                with service.engine.begin() as connection:
                    row = connection.execute(
                        text(
                            "SELECT status, image_count, error, failure_reported_at FROM generation_task_events "
                            "WHERE owner_id = :owner_id AND task_id = :task_id"
                        ),
                        {"owner_id": "user-1", "task_id": "task-late-cancel"},
                    ).mappings().one()
                    failed_count = connection.execute(
                        text(
                            "SELECT COUNT(*) FROM generation_task_events "
                            "WHERE status = 'error' AND failure_reported_at IS NOT NULL"
                        )
                    ).scalar_one()
                self.assertEqual(row["status"], "canceled")
                self.assertEqual(row["image_count"], 2)
                self.assertEqual(row["error"], "任务已中止")
                self.assertIsNone(row["failure_reported_at"])
                self.assertEqual(failed_count, 0)
            finally:
                service.engine.dispose()

    def test_error_event_with_cancellation_text_is_stored_as_canceled(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = GenerationMonitoringService(f"sqlite:///{Path(tmp_dir) / 'monitoring.db'}")
            try:
                service.record_task_event(
                    {
                        "id": "task-error-cancel-text",
                        "owner_id": "user-1",
                        "status": "error",
                        "mode": "generate",
                        "model": "gpt-image-2",
                        "image_count": 1,
                        "duration_ms": 500,
                        "error": "任务已中止",
                        "created_at": "2026-08-02 11:00:00",
                        "updated_at": "2026-08-02 11:00:01",
                    }
                )

                with service.engine.begin() as connection:
                    row = connection.execute(
                        text(
                            "SELECT status, failure_reported_at FROM generation_task_events "
                            "WHERE owner_id = :owner_id AND task_id = :task_id"
                        ),
                        {"owner_id": "user-1", "task_id": "task-error-cancel-text"},
                    ).mappings().one()
                self.assertEqual(row["status"], "canceled")
                self.assertIsNone(row["failure_reported_at"])
            finally:
                service.engine.dispose()


if __name__ == "__main__":
    unittest.main()
