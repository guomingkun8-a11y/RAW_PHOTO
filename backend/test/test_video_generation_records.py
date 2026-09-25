from __future__ import annotations

import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from threading import Barrier

from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.orm import sessionmaker

from services.video.video_generation_records import (
    RecordsBase, VideoGenerationRecord, aggregate_records, backfill_records,
    list_records, parse_cost, parse_datetime, record_exists, sync_record,
)


def video_task(task_id="request-1", **updates):
    return {
        "id": task_id, "owner_id": "user-1", "status": "success", "cost": "1.25",
        "model": "test-video", "created_at": "2026-09-01 10:00:00",
        "updated_at": "2026-09-01 10:10:00", "duration_ms": 1200,
        "upstream_task_id": "provider-job", "upstream_credential_id": "credential-fingerprint",
        "api_key": "must-not-be-persisted", "raw": {"api_key": "also-secret"},
        "video_url": "https://example.test/video.mp4", "cover_url": "https://example.test/cover.jpg",
        **updates,
    }


class VideoGenerationRecordsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.engine = create_engine(f"sqlite:///{Path(temporary.name) / 'ledger.db'}", connect_args={"timeout": 15})
        self.addCleanup(self.engine.dispose)
        RecordsBase.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def put(self, task, key=None):
        with self.Session.begin() as session:
            sync_record(session, key or f"{task['owner_id']}:{task['id']}", task)

    def test_duplicate_snapshots_are_idempotent_and_keep_only_internal_ids(self):
        self.put(video_task())
        self.put(video_task())
        with self.Session() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(VideoGenerationRecord)), 1)
            row = session.get(VideoGenerationRecord, "user-1:request-1")
            self.assertEqual(row.cost_amount, Decimal("1.25"))
            self.assertEqual(row.credential_id, "credential-fingerprint")
            self.assertEqual(row.upstream_task_id, "provider-job")
            page = list_records(session)
            self.assertEqual(page["cost_total"], Decimal("1.25"))
            self.assertNotIn("credential", repr(page))
            self.assertNotIn("secret", repr(row.__dict__))
        columns = {column["name"] for column in inspect(self.engine).get_columns("video_generation_records")}
        self.assertNotIn("api_key", columns)
        self.assertNotIn("task_json", columns)
        self.assertIn("reconciliation_required", columns)

    def test_reconciliation_state_is_persisted_and_cleared_by_a_newer_snapshot(self):
        self.put(video_task(status="error", reconciliation_required=True))
        with self.Session() as session:
            row = session.get(VideoGenerationRecord, "user-1:request-1")
            self.assertTrue(row.reconciliation_required)
            self.assertTrue(list_records(session)["items"][0]["reconciliation_required"])

        self.put(video_task(
            status="success",
            reconciliation_required=False,
            updated_at="2026-09-01 10:11:00",
        ))
        with self.Session() as session:
            self.assertFalse(session.get(VideoGenerationRecord, "user-1:request-1").reconciliation_required)

    def test_missing_invalid_and_negative_cost_preserve_known_amount(self):
        self.put(video_task())
        for value in (None, "", "NaN", Decimal("sNaN"), float("inf"), "-Infinity", -1, True, {}, [], "1e1000"):
            with self.subTest(value=value):
                self.put(video_task(cost=value))
                with self.Session() as session:
                    self.assertEqual(session.get(VideoGenerationRecord, "user-1:request-1").cost_amount, Decimal("1.25"))
        task = video_task()
        del task["cost"]
        self.put(task)
        with self.Session() as session:
            self.assertEqual(list_records(session)["cost_total"], Decimal("1.25"))

    def test_zero_is_known_and_cost_corrections_replace_not_add(self):
        self.put(video_task(cost="2.50"))
        self.put(video_task(cost="0"))
        with self.Session() as session:
            page = list_records(session)
            self.assertEqual(page["cost_total"], Decimal(0))
            self.assertEqual(page["cost_count"], 1)
        self.put(video_task(cost="0.123456789012"))
        with self.Session() as session:
            self.assertEqual(list_records(session)["cost_total"], Decimal("0.123456789012"))

    def test_stale_snapshot_cannot_revert_cost_status_or_audit(self):
        self.put(video_task())
        self.put(video_task(status="running", cost="99", updated_at="2026-09-01 10:09:00", upstream_task_id="old"))
        self.put(video_task(status="running", cost="99", created_at=None, updated_at=None))
        with self.Session() as session:
            row = session.get(VideoGenerationRecord, "user-1:request-1")
            self.assertEqual((row.status, row.cost_amount, row.upstream_task_id), ("success", Decimal("1.25"), "provider-job"))

    def test_created_and_completed_times_survive_late_fee_and_history_delete(self):
        self.put(video_task(status="canceled", cost=None))
        self.put(video_task(status="canceled", cost="3.5", updated_at="2026-09-02 12:00:00", created_at="2026-09-02 11:00:00"))
        self.put(video_task(status="canceled", cost=None, upstream_task_id="", upstream_credential_id="", history_deleted=True,
                            updated_at="2026-09-03 12:00:00"))
        self.put(video_task(status="canceled", cost=None, history_deleted=False, updated_at="2026-09-04 12:00:00"))
        with self.Session() as session:
            row = session.get(VideoGenerationRecord, "user-1:request-1")
            self.assertTrue(row.history_deleted)
            self.assertEqual(row.created_at, datetime(2026, 9, 1, 10))
            self.assertEqual(row.completed_at, datetime(2026, 9, 1, 10, 10))
            self.assertEqual(row.credential_id, "credential-fingerprint")
            page = list_records(session, start_at=datetime(2026, 9, 1), end_at=datetime(2026, 9, 2))
            self.assertEqual(page["cost_total"], Decimal("3.5"))
            self.assertTrue(record_exists(session, "user-1:request-1"))
            self.assertEqual(page["items"][0]["video_url"], "")

    def test_sync_does_not_commit_and_rolls_back_with_task_transaction(self):
        with self.Session() as session:
            sync_record(session, "user-1:request-1", video_task())
            self.assertTrue(record_exists(session, "user-1:request-1"))
            session.rollback()
        with self.Session() as session:
            self.assertFalse(record_exists(session, "user-1:request-1"))

    def test_owner_qualified_keys_are_distinct_and_identity_is_immutable(self):
        self.put(video_task())
        self.put(video_task(owner_id="user-2"))
        with self.Session() as session:
            self.assertEqual(list_records(session)["record_count"], 2)
            self.assertEqual(list_records(session, owner_id="user-2")["record_count"], 1)
        with self.assertRaisesRegex(ValueError, "cannot be reused"):
            self.put(video_task(owner_id="user-2"), key="user-1:request-1")

    def test_concurrent_identical_snapshots_create_one_record(self):
        barrier = Barrier(4)

        def write(_):
            barrier.wait(timeout=10)
            self.put(video_task())

        with ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(write, range(4)))
        with self.Session() as session:
            page = list_records(session)
            self.assertEqual((page["record_count"], page["cost_count"], page["cost_total"]), (1, 1, Decimal("1.25")))

    def test_pages_use_time_and_task_key_without_duplicates_and_keep_global_totals(self):
        for number in range(5):
            self.put(video_task(str(number)))
        seen, cursor = [], None
        with self.Session() as session:
            for expected_size in (2, 2, 1):
                page = list_records(session, limit=2, cursor=cursor)
                self.assertEqual(len(page["items"]), expected_size)
                self.assertEqual(page["record_count"], 5)
                self.assertEqual(page["cost_total"], Decimal("6.25"))
                seen.extend(item["task_id"] for item in page["items"])
                cursor = page["next_cursor"]
            self.assertIsNone(cursor)
            self.assertEqual(seen, ["4", "3", "2", "1", "0"])
            with self.assertRaisesRegex(ValueError, "cursor"):
                list_records(session, cursor={"event_at": "invalid", "task_key": "x"})

    def test_time_range_is_start_inclusive_end_exclusive(self):
        for task_id, time_value in (("before", "09:59:59"), ("start", "10:00:00"), ("end", "11:00:00")):
            self.put(video_task(task_id, updated_at=f"2026-09-01 {time_value}"))
        with self.Session() as session:
            page = list_records(session, start_at=datetime(2026, 9, 1, 10), end_at=datetime(2026, 9, 1, 11))
            self.assertEqual([row["task_id"] for row in page["items"]], ["start"])

    def test_costs_include_failed_canceled_and_active_known_cost_but_exclude_missing(self):
        for number, status in enumerate(("success", "failed", "cancelled", "running")):
            self.put(video_task(str(number), status=status, cost="0.1"))
        self.put(video_task("missing", cost=None))
        self.put(video_task("queued", status="queued", cost=None))
        with self.Session() as session:
            page = list_records(session)
            self.assertEqual((page["record_count"], page["cost_count"], page["cost_total"]), (5, 4, Decimal("0.4")))
            aggregate = aggregate_records(session)
            owner = aggregate["owners"][0]
            self.assertEqual((owner["success_count"], owner["failed_count"], owner["canceled_count"]), (2, 1, 1))
            self.assertEqual(owner["cost_total"], Decimal("0.4"))

    def test_backfill_is_explicit_bounded_resumable_and_does_not_overwrite_live_rows(self):
        with self.engine.begin() as connection:
            connection.execute(text("CREATE TABLE video_generation_tasks (key TEXT PRIMARY KEY, task_json TEXT NOT NULL)"))
            for number in range(5):
                raw = "invalid json" if number == 1 else json.dumps(video_task(str(number)))
                connection.execute(text("INSERT INTO video_generation_tasks VALUES (:key, :raw)"), {"key": f"user-1:{number}", "raw": raw})
        self.put(video_task("0", cost="7"))
        with self.Session.begin() as session:
            first = backfill_records(session, batch_size=2)
            self.assertEqual(first, {"scanned": 2, "synced": 0, "invalid": 1, "next_key": "user-1:1", "done": False})
        with self.Session.begin() as session:
            second = backfill_records(session, after_key=first["next_key"], batch_size=2)
            self.assertEqual(second["synced"], 2)
        with self.Session.begin() as session:
            final = backfill_records(session, after_key=second["next_key"], batch_size=2)
            self.assertTrue(final["done"])
            self.assertEqual(final["synced"], 1)
        with self.Session.begin() as session:
            self.assertEqual(backfill_records(session, batch_size=100)["synced"], 0)
        with self.Session() as session:
            self.assertEqual(list_records(session)["cost_total"], Decimal("10.75"))

    def test_history_delete_has_no_fk_cascade(self):
        self.assertEqual(inspect(self.engine).get_foreign_keys("video_generation_records"), [])

    def test_datetime_normalization_matches_monitoring_api_local_time(self):
        aware = datetime(2026, 9, 1, tzinfo=timezone.utc)
        self.assertEqual(parse_datetime("2026-09-01T00:00:00Z"), aware.astimezone().replace(tzinfo=None))

    def test_cost_normalizer_rejects_out_of_range_rounding_and_accepts_decimal(self):
        self.assertEqual(parse_cost(Decimal("0.01")), Decimal("0.01"))
        self.assertIsNone(parse_cost("999999999999999999.9999999999999"))


if __name__ == "__main__":
    unittest.main()
