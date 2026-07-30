from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import text

from services.generation_monitoring_service import GenerationMonitoringService


class GenerationMonitoringServiceTests(unittest.TestCase):
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
                            "deleted_at TEXT NULL)"
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
                        text("INSERT INTO generated_images (owner_id, deleted_at) VALUES (:owner_id, :deleted_at)"),
                        {"owner_id": "user-1", "deleted_at": None},
                    )

                service.record_task_event(
                    {
                        "id": "task-success-1",
                        "owner_id": "user-1",
                        "status": "success",
                        "mode": "generate",
                        "model": "gpt-image-2",
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
                        "model": "gpt-image-2",
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
                self.assertEqual(summary["total_success"], 1)
                self.assertEqual(summary["total_failed"], 2)
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
            finally:
                service.engine.dispose()


if __name__ == "__main__":
    unittest.main()
