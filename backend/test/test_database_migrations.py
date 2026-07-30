from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine, inspect

from services.database_maintenance import ensure_database_ready
from services.database_migrations import migration_status, run_migrations


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
                image_task_indexes = {
                    item["name"]
                    for item in inspect(engine).get_indexes("image_tasks")
                }
                self.assertIn("idx_image_tasks_owner_status_key", image_task_indexes)
            finally:
                engine.dispose()


if __name__ == "__main__":
    unittest.main()
