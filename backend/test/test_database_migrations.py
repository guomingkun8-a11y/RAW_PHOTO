from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine, inspect, text

from services.ecommerce.ecommerce_agent_memory_service import EcommerceAgentMemoryService
from services.platform.database_maintenance import ensure_database_ready
from services.platform.database_migrations import migration_status, run_migrations


class DatabaseMigrationTests(unittest.TestCase):
    def test_migrations_are_versioned_and_idempotent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_url = f"sqlite:///{Path(temp_dir) / 'migrations.db'}"
            first = run_migrations(database_url)
            second = run_migrations(database_url)
            status = migration_status(database_url)

            self.assertEqual(first["applied_now"], [
                "001_base_schema",
                "002_image_task_batch_columns",
                "003_image_task_indexes",
                "004_reference_image_asset_cache",
                "005_generation_stage_timings",
                "006_operational_indexes",
                "007_relational_constraints",
                "008_image_conversation_schema",
                "009_system_announcement_schema",
                "010_professional_agent_memory",
                "011_professional_agent_memory_vectors",
                "012_professional_agent_runs",
                "013_professional_agent_memory_lifecycle",
                "014_professional_agent_folders_and_batches",
                "015_professional_knowledge_mysql",
                "016_professional_memory_review_governance",
                "017_chatgpt_style_memory_scopes",
                "018_professional_agent_operational_indexes",
                "019_generation_cost_monitoring",
                "021_remove_model_token_monitoring",
                "022_professional_agent_videos",
                "023_professional_agent_video_analysis",
                "024_video_generation_tasks",
                "025_video_generation_conversations",
                "026_video_agent_messages",
                "027_video_agent_reasoning",
                "028_video_agent_attachments",
                "029_video_agent_async_processing",
                "031_video_generation_reliability",
                "032_video_generation_history_query",
                "033_video_generation_reconciliation_state",
                "034_image_task_projection_outbox",
                "035_history_query_indexes",
                "036_generation_event_query_path",
                "037_image_asset_database_index",
                "038_upstream_usage_ledger",
                "039_upstream_usage_event_index",
                "040_upstream_usage_model_attribution",
                "041_upstream_usage_chat_attribution",
                "042_audio_generation_tasks",
            ])
            self.assertEqual(second["applied_now"], [])
            self.assertEqual(status["pending"], [])
            maintenance = ensure_database_ready(database_url, cleanup_sessions=False)
            self.assertIn("migrations", maintenance)
            self.assertEqual(maintenance["migrations"]["pending"], [])

            engine = create_engine(database_url)
            try:
                tables = set(inspect(engine).get_table_names())
                self.assertIn("schema_migrations", tables)
                self.assertIn("image_tasks", tables)
                self.assertIn("image_task_batches", tables)
                self.assertIn("image_conversations", tables)
                self.assertIn("reference_image_assets", tables)
                self.assertIn("system_announcements", tables)
                self.assertIn("professional_agent_conversations", tables)
                self.assertIn("professional_agent_messages", tables)
                self.assertIn("professional_agent_state_snapshots", tables)
                self.assertIn("professional_agent_memory_chunks", tables)
                self.assertIn("professional_agent_runs", tables)
                self.assertIn("professional_agent_run_events", tables)
                self.assertIn("professional_agent_folder_assets", tables)
                self.assertIn("professional_agent_folder_items", tables)
                self.assertIn("professional_agent_batch_plans", tables)
                self.assertIn("professional_agent_batch_plan_items", tables)
                self.assertIn("professional_agent_video_assets", tables)
                self.assertIn("video_generation_tasks", tables)
                self.assertIn("video_generation_owner_locks", tables)
                self.assertIn("video_generation_cleanup", tables)
                self.assertIn("video_generation_records", tables)
                self.assertIn("audio_generation_tasks", tables)
                self.assertIn("audio_generation_owner_locks", tables)
                self.assertIn("audio_generation_cleanup", tables)
                self.assertIn("audio_generation_records", tables)
                self.assertIn("upstream_usage_records", tables)
                self.assertIn("upstream_usage_sync_state", tables)
                self.assertIn("video_agent_messages", tables)
                self.assertIn("professional_knowledge_documents", tables)
                self.assertIn("professional_knowledge_chunks", tables)
                self.assertNotIn("model_cost_events", tables)
                image_task_indexes = {
                    item["name"]
                    for item in inspect(engine).get_indexes("image_tasks")
                }
                self.assertIn("idx_image_tasks_owner_status_key", image_task_indexes)
                video_columns = {
                    column["name"]
                    for column in inspect(engine).get_columns("professional_agent_video_assets")
                }
                self.assertIn("analysis_error", video_columns)
                self.assertIn("analysis_started_at", video_columns)
                self.assertIn("analysis_finished_at", video_columns)
                self.assertIn("analysis_version", video_columns)
                video_generation_columns = {
                    column["name"]
                    for column in inspect(engine).get_columns("video_generation_tasks")
                }
                self.assertIn("upstream_task_id", video_generation_columns)
                self.assertIn("task_json", video_generation_columns)
                self.assertIn("conversation_id", video_generation_columns)
                self.assertIn("prompt", video_generation_columns)
                video_record_columns = {
                    column["name"]
                    for column in inspect(engine).get_columns("video_generation_records")
                }
                self.assertIn("reconciliation_required", video_record_columns)
                video_agent_columns = {
                    column["name"]
                    for column in inspect(engine).get_columns("video_agent_messages")
                }
                self.assertIn("reasoning_summary", video_agent_columns)
                self.assertIn("reasoning_enabled", video_agent_columns)
                self.assertIn("attachments_json", video_agent_columns)
                self.assertIn("status", video_agent_columns)
                self.assertIn("analysis_error", video_agent_columns)
            finally:
                engine.dispose()

    def test_video_reconciliation_migration_backfills_existing_task_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_url = f"sqlite:///{Path(temp_dir) / 'video-reconciliation.db'}"
            run_migrations(database_url)
            engine = create_engine(database_url)
            task = {
                "id": "unsettled-video", "owner_id": "legacy-owner", "status": "error",
                "mode": "text_to_video", "model": "legacy-model",
                "reconciliation_required": True,
                "created_at": "2026-09-01 10:00:00", "updated_at": "2026-09-01 10:05:00",
            }
            try:
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            "INSERT INTO video_generation_tasks "
                            "(key, owner_id, task_id, conversation_id, status, mode, model, prompt, upstream_task_id, "
                            "created_at, updated_at, task_json) VALUES "
                            "(:key, :owner_id, :task_id, '', :status, :mode, :model, '', NULL, "
                            ":created_at, :updated_at, :task_json)"
                        ),
                        {
                            "key": "legacy-owner:unsettled-video",
                            "owner_id": "legacy-owner",
                            "task_id": "unsettled-video",
                            "status": "error",
                            "mode": "text_to_video",
                            "model": "legacy-model",
                            "created_at": "2026-09-01 10:00:00",
                            "updated_at": "2026-09-01 10:05:00",
                            "task_json": json.dumps(task),
                        },
                    )
                    connection.execute(
                        text(
                            "INSERT INTO video_generation_records "
                            "(task_key, task_id, owner_id, owner_username, owner_name, status, mode, model, "
                            "upstream_task_id, credential_id, history_deleted, reconciliation_required, "
                            "cost_amount, duration_ms, error, video_url, cover_url, created_at, updated_at, "
                            "completed_at, event_at) VALUES "
                            "(:key, :task_id, :owner_id, '', '', 'error', 'text_to_video', 'legacy-model', "
                            "'', '', 0, 0, NULL, 0, '', '', '', :created_at, :updated_at, :updated_at, :updated_at)"
                        ),
                        {
                            "key": "legacy-owner:unsettled-video",
                            "task_id": "unsettled-video",
                            "owner_id": "legacy-owner",
                            "created_at": "2026-09-01 10:00:00",
                            "updated_at": "2026-09-01 10:05:00",
                        },
                    )
                    connection.execute(text(
                        "DELETE FROM schema_migrations "
                        "WHERE version = '033_video_generation_reconciliation_state'"
                    ))

                result = run_migrations(database_url)
                self.assertEqual(result["applied_now"], ["033_video_generation_reconciliation_state"])
                with engine.connect() as connection:
                    required = connection.execute(text(
                        "SELECT reconciliation_required FROM video_generation_records "
                        "WHERE task_key = 'legacy-owner:unsettled-video'"
                    )).scalar_one()
                self.assertTrue(required)
            finally:
                engine.dispose()

    def test_video_reliability_migration_backfills_existing_accounting(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_url = f"sqlite:///{Path(temp_dir) / 'video-backfill.db'}"
            run_migrations(database_url)
            engine = create_engine(database_url)
            task = {
                "id": "legacy-video", "owner_id": "legacy-owner", "status": "success",
                "mode": "text_to_video", "model": "legacy-model", "cost": 2.75,
                "duration_ms": 1200, "upstream_task_id": "legacy-upstream",
                "created_at": "2026-09-01 10:00:00", "updated_at": "2026-09-01 10:05:00",
            }
            try:
                with engine.begin() as connection:
                    connection.execute(text("DROP INDEX IF EXISTS idx_video_record_reconciliation_event"))
                    connection.execute(text(
                        "ALTER TABLE video_generation_records DROP COLUMN reconciliation_required"
                    ))
                    connection.execute(text("DELETE FROM video_generation_records"))
                    connection.execute(
                        text(
                            "INSERT INTO video_generation_tasks "
                            "(key, owner_id, task_id, conversation_id, status, mode, model, upstream_task_id, "
                            "created_at, updated_at, task_json) VALUES "
                            "(:key, :owner_id, :task_id, '', :status, :mode, :model, :upstream_task_id, "
                            ":created_at, :updated_at, :task_json)"
                        ),
                        {
                            "key": "legacy-owner:legacy-video",
                            "owner_id": "legacy-owner",
                            "task_id": "legacy-video",
                            "status": "success",
                            "mode": "text_to_video",
                            "model": "legacy-model",
                            "upstream_task_id": "legacy-upstream",
                            "created_at": "2026-09-01 10:00:00",
                            "updated_at": "2026-09-01 10:05:00",
                            "task_json": json.dumps(task),
                        },
                    )
                    connection.execute(
                        text(
                            "DELETE FROM schema_migrations WHERE version IN "
                            "('031_video_generation_reliability', "
                            "'032_video_generation_history_query', "
                            "'033_video_generation_reconciliation_state')"
                        )
                    )

                result = run_migrations(database_url)
                self.assertEqual(result["applied_now"], [
                    "031_video_generation_reliability",
                    "032_video_generation_history_query",
                    "033_video_generation_reconciliation_state",
                ])
                self.assertIn(
                    "reconciliation_required",
                    {column["name"] for column in inspect(engine).get_columns("video_generation_records")},
                )
                with engine.connect() as connection:
                    record = connection.execute(text(
                        "SELECT owner_id, status, model, cost_amount, upstream_task_id "
                        "FROM video_generation_records WHERE task_key = 'legacy-owner:legacy-video'"
                    )).mappings().one()
                self.assertEqual(record["owner_id"], "legacy-owner")
                self.assertEqual(record["status"], "success")
                self.assertEqual(record["model"], "legacy-model")
                self.assertEqual(float(record["cost_amount"]), 2.75)
                self.assertEqual(record["upstream_task_id"], "legacy-upstream")
            finally:
                engine.dispose()

    def test_review_governance_migration_demotes_automatic_memory_only(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_url = f"sqlite:///{Path(temp_dir) / 'memory-governance.db'}"
            run_migrations(database_url)
            memory = EcommerceAgentMemoryService(database_url)
            automatic = memory.upsert_long_term_memory(
                owner_id="owner",
                content="模型自动猜测用户喜欢纯白背景",
                memory_key="automatic.preference",
                metadata={"distilled": True},
                confidence=0.8,
                confirmed=True,
            )
            manual = memory.upsert_long_term_memory(
                owner_id="owner",
                content="用户确认喜欢真实场景背景",
                memory_key="manual.preference",
                metadata={"manual": True},
                confidence=0.5,
                confirmed=True,
            )
            memory.close()

            engine = create_engine(database_url)
            try:
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            "UPDATE professional_agent_long_term_memories "
                            "SET embedding_json = '[1.0]', embedding_model = 'legacy' "
                            "WHERE id = :memory_id"
                        ),
                        {"memory_id": int(automatic["memoryId"])},
                    )
                    connection.execute(
                        text(
                            "UPDATE professional_agent_long_term_memories "
                            "SET confidence = 0.5 WHERE id = :memory_id"
                        ),
                        {"memory_id": int(manual["memoryId"])},
                    )
                    connection.execute(
                        text("DELETE FROM schema_migrations WHERE version = :version"),
                        {"version": "016_professional_memory_review_governance"},
                    )

                result = run_migrations(database_url)
                self.assertEqual(["016_professional_memory_review_governance"], result["applied_now"])
                with engine.connect() as connection:
                    rows = {
                        int(row.id): row
                        for row in connection.execute(text(
                            "SELECT id, status, confirmed, confidence, embedding_json, embedding_model "
                            "FROM professional_agent_long_term_memories"
                        )).mappings()
                    }
                auto_row = rows[int(automatic["memoryId"])]
                manual_row = rows[int(manual["memoryId"])]
                self.assertEqual("pending_review", auto_row.status)
                self.assertEqual(0, auto_row.confirmed)
                self.assertIsNone(auto_row.embedding_json)
                self.assertEqual("", auto_row.embedding_model)
                self.assertEqual("active", manual_row.status)
                self.assertEqual(1, manual_row.confirmed)
                self.assertGreaterEqual(float(manual_row.confidence), 0.9)
            finally:
                engine.dispose()

    def test_chatgpt_style_memory_migration_rejects_tool_data_and_scopes_products(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_url = f"sqlite:///{Path(temp_dir) / 'memory-scopes.db'}"
            run_migrations(database_url)
            memory = EcommerceAgentMemoryService(database_url)
            dirty = memory.upsert_long_term_memory(
                owner_id="owner",
                content='{"retrievalMode":"keyword","sections":[],"sources":[{"title":"不要纯白背景"}]}',
                category="preference",
                memory_key="dirty-tool-result",
                metadata={"distilled": True},
                confidence=0.9,
                confirmed=False,
                status="pending_review",
            )
            product = memory.upsert_long_term_memory(
                owner_id="owner",
                content="产品材质为拉丝铝",
                category="product",
                scope="user",
                memory_key="product-material",
                source_conversation_id="project-1",
                confidence=0.95,
                confirmed=True,
            )
            memory.close()

            engine = create_engine(database_url)
            try:
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            "UPDATE professional_agent_long_term_memories SET scope = 'user', scope_id = 'owner' "
                            "WHERE id = :memory_id"
                        ),
                        {"memory_id": int(product["memoryId"])},
                    )
                    connection.execute(
                        text("DELETE FROM schema_migrations WHERE version = :version"),
                        {"version": "017_chatgpt_style_memory_scopes"},
                    )

                result = run_migrations(database_url)
                self.assertEqual(["017_chatgpt_style_memory_scopes"], result["applied_now"])
                with engine.connect() as connection:
                    rows = {
                        int(row.id): row
                        for row in connection.execute(text(
                            "SELECT id, status, confirmed, scope, scope_id, embedding_json "
                            "FROM professional_agent_long_term_memories"
                        )).mappings()
                    }
                dirty_row = rows[int(dirty["memoryId"])]
                product_row = rows[int(product["memoryId"])]
                self.assertEqual("rejected", dirty_row.status)
                self.assertEqual(0, dirty_row.confirmed)
                self.assertIsNone(dirty_row.embedding_json)
                self.assertEqual("project", product_row.scope)
                self.assertEqual("project-1", product_row.scope_id)
            finally:
                engine.dispose()


if __name__ == "__main__":
    unittest.main()
