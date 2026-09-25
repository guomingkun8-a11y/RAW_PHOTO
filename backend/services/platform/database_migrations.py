from __future__ import annotations

from contextlib import contextmanager
import json
from datetime import datetime
import time
from typing import Callable

from sqlalchemy import BigInteger, Column, DateTime, Float, Integer, MetaData, String, Table, Text, UniqueConstraint, create_engine, inspect, text
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

from services.billing.upstream_usage_models import BillingBase
from services.platform.enterprise_schema import EnterpriseBase
from services.image.image_task_store import Base as ImageTaskBase

MIGRATION_TABLE = "schema_migrations"
MIGRATION_LOCK_NAME = "gmkraw_schema_migrations"
MIGRATION_LOCK_KEY = 0x474D4B524157


def _migration_table(metadata: MetaData) -> Table:
    return Table(
        MIGRATION_TABLE,
        metadata,
        Column("version", String(64), primary_key=True),
        Column("applied_at", DateTime, nullable=False),
    )


def _column_names(connection, table_name: str) -> set[str]:
    inspector = inspect(connection)
    if table_name not in inspector.get_table_names():
        return set()
    return {str(column["name"]) for column in inspector.get_columns(table_name)}


def _quoted_identifier(engine: Engine, name: str) -> str:
    if engine.dialect.name == "mysql":
        return f"`{name}`"
    return f'"{name}"'


def _table_exists(engine: Engine, table_name: str) -> bool:
    return table_name in inspect(engine).get_table_names()


def _ensure_migration_table(engine: Engine, table: Table) -> None:
    try:
        table.create(engine, checkfirst=True)
    except OperationalError:
        # A concurrent status/migration process may create it between the
        # check and CREATE. Re-inspect before deciding this is a real error.
        if MIGRATION_TABLE not in inspect(engine).get_table_names():
            raise


@contextmanager
def _migration_lock(engine: Engine, timeout_secs: int = 60):
    dialect = engine.dialect.name
    if dialect not in {"mysql", "postgresql"}:
        yield
        return

    connection = engine.connect()
    acquired = False
    try:
        if dialect == "mysql":
            acquired = connection.execute(
                text("SELECT GET_LOCK(:name, :timeout)"),
                {"name": MIGRATION_LOCK_NAME, "timeout": max(1, int(timeout_secs))},
            ).scalar() == 1
        else:
            deadline = time.monotonic() + max(1, int(timeout_secs))
            while time.monotonic() < deadline:
                acquired = bool(connection.execute(
                    text("SELECT pg_try_advisory_lock(:key)"),
                    {"key": MIGRATION_LOCK_KEY},
                ).scalar())
                if acquired:
                    break
                time.sleep(0.25)
        if not acquired:
            raise RuntimeError("timed out waiting for the database migration lock")
        yield
    finally:
        if acquired:
            try:
                if dialect == "mysql":
                    connection.execute(
                        text("SELECT RELEASE_LOCK(:name)"),
                        {"name": MIGRATION_LOCK_NAME},
                    )
                else:
                    connection.execute(
                        text("SELECT pg_advisory_unlock(:key)"),
                        {"key": MIGRATION_LOCK_KEY},
                    )
            except Exception:
                pass
        connection.close()


def _apply_base_schema(engine: Engine) -> None:
    EnterpriseBase.metadata.create_all(engine)
    ImageTaskBase.metadata.create_all(engine)


def _apply_image_task_columns(engine: Engine) -> None:
    definitions = {
        "batch_id": "VARCHAR(191)",
        "batch_index": "INTEGER",
        "batch_total": "INTEGER",
    }
    with engine.begin() as connection:
        columns = _column_names(connection, "image_tasks")
        for name, definition in definitions.items():
            if name not in columns:
                connection.execute(text(f"ALTER TABLE image_tasks ADD COLUMN {name} {definition} NULL"))


def _apply_image_task_indexes(engine: Engine) -> None:
    statements = [
        "CREATE INDEX idx_image_tasks_owner_updated ON image_tasks (owner_id, updated_at)",
        "CREATE INDEX idx_image_tasks_status_updated ON image_tasks (status, updated_at)",
        "CREATE INDEX idx_image_tasks_owner_batch ON image_tasks (owner_id, batch_id, updated_at)",
    ]
    with engine.begin() as connection:
        for statement in statements:
            try:
                connection.execute(text(statement))
            except Exception:
                # MySQL, PostgreSQL and SQLite use different IF NOT EXISTS syntax.
                pass


def _apply_reference_image_asset_cache(engine: Engine) -> None:
    EnterpriseBase.metadata.tables["reference_image_assets"].create(engine, checkfirst=True)


def _apply_generation_stage_columns(engine: Engine) -> None:
    if "generation_task_events" not in inspect(engine).get_table_names():
        return
    with engine.begin() as connection:
        columns = _column_names(connection, "generation_task_events")
        for name in (
            "upload_duration_ms",
            "queue_duration_ms",
            "generation_duration_ms",
            "save_duration_ms",
        ):
            if name not in columns:
                connection.execute(text(f"ALTER TABLE generation_task_events ADD COLUMN {name} INTEGER NULL"))


def _apply_generation_cost_columns(engine: Engine) -> None:
    if "generation_task_events" not in inspect(engine).get_table_names():
        return
    with engine.begin() as connection:
        columns = _column_names(connection, "generation_task_events")
        if "cost" not in columns:
            connection.execute(text("ALTER TABLE generation_task_events ADD COLUMN cost FLOAT NULL"))
        if "upstream_task_id" not in columns:
            connection.execute(text("ALTER TABLE generation_task_events ADD COLUMN upstream_task_id VARCHAR(191) NULL"))
        try:
            connection.execute(text("CREATE INDEX idx_generation_events_upstream_task ON generation_task_events (upstream_task_id)"))
        except Exception:
            pass


def _remove_model_token_monitoring_schema(engine: Engine) -> None:
    if "model_cost_events" not in inspect(engine).get_table_names():
        return
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE model_cost_events"))


def _apply_operational_indexes(engine: Engine) -> None:
    key_column = _quoted_identifier(engine, "key")
    statements = {
        "business_user_sessions": [
            "CREATE INDEX idx_sessions_expires_revoked ON business_user_sessions (expires_at, revoked_at)",
            "CREATE INDEX idx_sessions_active_user_seen ON business_user_sessions (revoked_at, expires_at, user_id, last_used_at)",
        ],
        "generated_images": [
            "CREATE INDEX idx_owner_deleted_created_id ON generated_images (owner_id, deleted_at, created_at, id)",
            "CREATE INDEX idx_deleted_created_id ON generated_images (deleted_at, created_at, id)",
        ],
        "business_products": [
            "CREATE INDEX idx_products_owner_status_updated ON business_products (owner_id, status, updated_at)",
        ],
        "business_product_references": [
            "CREATE INDEX idx_references_product_created_id ON business_product_references (product_id, created_at, id)",
        ],
        "business_prompt_templates": [
            "CREATE INDEX idx_templates_owner_enabled_updated ON business_prompt_templates (owner_id, enabled, updated_at)",
        ],
        "business_audit_logs": [
            "CREATE INDEX idx_audit_owner_created_id ON business_audit_logs (owner_id, created_at, id)",
        ],
        "image_tasks": [
            f"CREATE INDEX idx_image_tasks_owner_status_key ON image_tasks (owner_id, status, {key_column})",
        ],
        "generation_task_events": [
            "CREATE INDEX idx_generation_events_owner_updated ON generation_task_events (owner_id, task_updated_at)",
        ],
    }
    with engine.begin() as connection:
        for table_name, table_statements in statements.items():
            if not _table_exists(engine, table_name):
                continue
            for statement in table_statements:
                try:
                    connection.execute(text(statement))
                except Exception:
                    pass


def _apply_image_task_projection_outbox(engine: Engine) -> None:
    if not _table_exists(engine, "image_tasks"):
        return
    with engine.begin() as connection:
        columns = _column_names(connection, "image_tasks")
        for name, definition in {
            "projection_pending": "INTEGER NOT NULL DEFAULT 0",
            "projection_next_attempt_at": "TIMESTAMP NULL",
        }.items():
            if name not in columns:
                connection.execute(text(f"ALTER TABLE image_tasks ADD COLUMN {name} {definition}"))
        try:
            connection.execute(
                text(
                    "CREATE INDEX idx_image_tasks_projection_due "
                    "ON image_tasks (projection_pending, projection_next_attempt_at)"
                )
            )
        except Exception:
            pass


def _apply_history_query_indexes(engine: Engine) -> None:
    statements = {
        "generated_images": [
            "CREATE INDEX idx_generated_images_owner_task ON generated_images (owner_id, task_id)",
        ],
        "generation_task_events": [
            "CREATE INDEX idx_generation_events_status_time_owner "
            "ON generation_task_events (status, task_updated_at, owner_id)",
            "CREATE INDEX idx_generation_events_status_cost_time "
            "ON generation_task_events (status, cost, task_updated_at)",
        ],
    }
    with engine.begin() as connection:
        for table_name, table_statements in statements.items():
            if not _table_exists(engine, table_name):
                continue
            for statement in table_statements:
                try:
                    connection.execute(text(statement))
                except Exception:
                    pass


def _apply_generation_event_query_path(engine: Engine) -> None:
    if not _table_exists(engine, "generation_task_events"):
        return
    statements = (
        "CREATE INDEX idx_generation_events_owner_status_time_id "
        "ON generation_task_events (owner_id, status, task_updated_at, id)",
        "CREATE INDEX idx_generation_events_status_time_owner_id "
        "ON generation_task_events (status, task_updated_at, owner_id, id)",
    )
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE generation_task_events "
                "SET task_created_at = COALESCE(task_created_at, created_at, updated_at, CURRENT_TIMESTAMP), "
                "task_updated_at = COALESCE(task_updated_at, updated_at, task_created_at, created_at, CURRENT_TIMESTAMP) "
                "WHERE task_created_at IS NULL OR task_updated_at IS NULL"
            )
        )
        for statement in statements:
            try:
                connection.execute(text(statement))
            except Exception:
                pass


def _apply_image_asset_database_index(engine: Engine) -> None:
    table_name = "image_assets"
    ImageTaskBase.metadata.tables["image_task_owner_guards"].create(engine, checkfirst=True)
    EnterpriseBase.metadata.tables[table_name].create(engine, checkfirst=True)
    if engine.dialect.name not in {"mysql", "postgresql"}:
        return

    inspector = inspect(engine)
    unique_constraints = {
        str(item.get("name") or "")
        for item in inspector.get_unique_constraints(table_name)
    }
    with engine.begin() as connection:
        if engine.dialect.name == "mysql":
            connection.execute(
                text("ALTER TABLE image_assets MODIFY COLUMN image_index VARCHAR(191) NOT NULL")
            )
            if "uq_image_asset_task_index" in unique_constraints:
                connection.execute(text("ALTER TABLE image_assets DROP INDEX uq_image_asset_task_index"))
            if "uq_image_asset_owner_task_type_index" not in unique_constraints:
                connection.execute(
                    text(
                        "ALTER TABLE image_assets ADD CONSTRAINT "
                        "uq_image_asset_owner_task_type_index UNIQUE "
                        "(owner_id, task_id, asset_type, image_index)"
                    )
                )
        else:
            connection.execute(
                text(
                    "ALTER TABLE image_assets ALTER COLUMN image_index TYPE VARCHAR(191) "
                    "USING image_index::VARCHAR"
                )
            )
            if "uq_image_asset_task_index" in unique_constraints:
                connection.execute(
                    text("ALTER TABLE image_assets DROP CONSTRAINT uq_image_asset_task_index")
                )
            if "uq_image_asset_owner_task_type_index" not in unique_constraints:
                connection.execute(
                    text(
                        "ALTER TABLE image_assets ADD CONSTRAINT "
                        "uq_image_asset_owner_task_type_index UNIQUE "
                        "(owner_id, task_id, asset_type, image_index)"
                    )
                )


def _apply_upstream_usage_ledger(engine: Engine) -> None:
    BillingBase.metadata.create_all(engine)
    with engine.begin() as connection:
        existing = connection.execute(
            text("SELECT provider FROM upstream_usage_sync_state WHERE provider = :provider"),
            {"provider": "lk888"},
        ).scalar()
        if existing is None:
            connection.execute(
                text(
                    "INSERT INTO upstream_usage_sync_state "
                    "(provider, status, last_error, records_seen, records_upserted, lease_owner, updated_at) "
                    "VALUES (:provider, 'idle', '', 0, 0, '', :updated_at)"
                ),
                {"provider": "lk888", "updated_at": datetime.now()},
            )


def _apply_upstream_usage_event_index(engine: Engine) -> None:
    if not _table_exists(engine, "upstream_usage_records"):
        _apply_upstream_usage_ledger(engine)
    with engine.begin() as connection:
        try:
            connection.execute(
                text("CREATE INDEX idx_upstream_usage_event ON upstream_usage_records (event_at, id)")
            )
        except Exception:
            pass


def _apply_upstream_usage_model_attribution(engine: Engine) -> None:
    if not _table_exists(engine, "upstream_usage_records"):
        _apply_upstream_usage_ledger(engine)
    definitions = {
        "requested_model": "VARCHAR(191) NOT NULL DEFAULT ''",
        "model_version": "VARCHAR(64) NOT NULL DEFAULT ''",
    }
    with engine.begin() as connection:
        columns = _column_names(connection, "upstream_usage_records")
        for name, definition in definitions.items():
            if name not in columns:
                connection.execute(text(
                    f"ALTER TABLE upstream_usage_records ADD COLUMN {name} {definition}"
                ))
        try:
            connection.execute(text(
                "CREATE INDEX idx_upstream_usage_requested_model_event "
                "ON upstream_usage_records (requested_model, event_at, id)"
            ))
        except Exception:
            pass


def _apply_upstream_usage_chat_attribution(engine: Engine) -> None:
    BillingBase.metadata.create_all(engine)
    with engine.begin() as connection:
        try:
            connection.execute(
                text(
                    "CREATE INDEX idx_upstream_usage_attribution_source_task "
                    "ON upstream_usage_attributions (local_source, local_task_id)"
                )
            )
        except Exception:
            pass


def _apply_relational_constraints(engine: Engine) -> None:
    if engine.dialect.name not in {"mysql", "postgresql"}:
        return
    inspector = inspect(engine)
    existing_foreign_keys = {
        table_name: {
            tuple(str(column) for column in fk.get("constrained_columns") or ())
            for fk in inspector.get_foreign_keys(table_name)
        }
        for table_name in ("business_user_sessions", "generated_images", "business_product_references")
        if table_name in inspector.get_table_names()
    }
    statements = [
        (
            "business_user_sessions",
            ("user_id",),
            "fk_business_user_sessions_user_id_business_users",
            "ALTER TABLE business_user_sessions ADD CONSTRAINT fk_business_user_sessions_user_id_business_users "
            "FOREIGN KEY (user_id) REFERENCES business_users (id) ON DELETE CASCADE",
        ),
        (
            "generated_images",
            ("owner_id",),
            "fk_generated_images_owner_id_business_users",
            "ALTER TABLE generated_images ADD CONSTRAINT fk_generated_images_owner_id_business_users "
            "FOREIGN KEY (owner_id) REFERENCES business_users (id) ON DELETE RESTRICT",
        ),
        (
            "business_product_references",
            ("product_id",),
            "fk_business_product_references_product_id_business_products",
            "ALTER TABLE business_product_references ADD CONSTRAINT fk_business_product_references_product_id_business_products "
            "FOREIGN KEY (product_id) REFERENCES business_products (id) ON DELETE CASCADE",
        ),
    ]
    with engine.begin() as connection:
        for table_name, columns, _constraint_name, statement in statements:
            if table_name not in existing_foreign_keys:
                continue
            if tuple(columns) in existing_foreign_keys[table_name]:
                continue
            try:
                connection.execute(text(statement))
            except Exception:
                pass


def _apply_image_conversation_schema(engine: Engine) -> None:
    metadata = MetaData()
    Table(
        "image_conversations",
        metadata,
        Column("owner_id", String(191), primary_key=True),
        Column("id", String(191), primary_key=True),
        Column("title", String(191), nullable=False, default=""),
        Column("payload_json", Text().with_variant(LONGTEXT, "mysql"), nullable=False),
        Column("deleted_at", DateTime, nullable=True),
        Column("created_at", DateTime, nullable=False),
        Column("updated_at", DateTime, nullable=False),
    )
    metadata.create_all(engine)
    statements = [
        "CREATE INDEX idx_image_conversations_owner_updated ON image_conversations (owner_id, deleted_at, updated_at)",
        "CREATE INDEX idx_image_conversations_owner_title ON image_conversations (owner_id, title)",
    ]
    with engine.begin() as connection:
        for statement in statements:
            try:
                connection.execute(text(statement))
            except Exception:
                pass


def _apply_system_announcement_schema(engine: Engine) -> None:
    metadata = MetaData()
    Table(
        "system_announcements",
        metadata,
        Column("id", BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True),
        Column("title", String(191), nullable=False),
        Column("content", Text().with_variant(LONGTEXT, "mysql"), nullable=False),
        Column("announcement_type", String(32), nullable=False),
        Column("enabled", Integer, nullable=False),
        Column("created_by", String(191), nullable=False),
        Column("created_at", DateTime, nullable=False),
        Column("updated_at", DateTime, nullable=False),
    )
    metadata.create_all(engine)
    statements = [
        "CREATE INDEX idx_system_announcements_enabled_created ON system_announcements (enabled, created_at)",
        "CREATE INDEX idx_system_announcements_created_id ON system_announcements (created_at, id)",
    ]
    with engine.begin() as connection:
        for statement in statements:
            try:
                connection.execute(text(statement))
            except Exception:
                pass


def _apply_professional_agent_memory_schema(engine: Engine) -> None:
    metadata = MetaData()
    Table(
        "professional_agent_conversations",
        metadata,
        Column("owner_id", String(191), primary_key=True),
        Column("conversation_id", String(191), primary_key=True),
        Column("active_run_id", String(191), nullable=False, default=""),
        Column("latest_turn_id", String(191), nullable=False, default=""),
        Column("status", String(40), nullable=False, default="active"),
        Column("deleted_at", DateTime, nullable=True),
        Column("created_at", DateTime, nullable=False),
        Column("updated_at", DateTime, nullable=False),
    )
    Table(
        "professional_agent_messages",
        metadata,
        Column("id", BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True),
        Column("owner_id", String(191), nullable=False),
        Column("conversation_id", String(191), nullable=False),
        Column("run_id", String(191), nullable=False, default=""),
        Column("turn_id", String(191), nullable=False, default=""),
        Column("message_key", String(191), nullable=False),
        Column("role", String(24), nullable=False),
        Column("message_type", String(40), nullable=False),
        Column("tool_name", String(120), nullable=False, default=""),
        Column("tool_call_id", String(191), nullable=False, default=""),
        Column("content_json", Text().with_variant(LONGTEXT, "mysql"), nullable=False),
        Column("created_at", DateTime, nullable=False),
        UniqueConstraint("owner_id", "conversation_id", "message_key", name="uq_prof_agent_message_key"),
    )
    Table(
        "professional_agent_state_snapshots",
        metadata,
        Column("owner_id", String(191), primary_key=True),
        Column("conversation_id", String(191), primary_key=True),
        Column("state_key", String(120), primary_key=True),
        Column("payload_json", Text().with_variant(LONGTEXT, "mysql"), nullable=False),
        Column("updated_at", DateTime, nullable=False),
    )
    metadata.create_all(engine)
    statements = [
        "CREATE INDEX idx_prof_agent_conversation_run ON professional_agent_conversations (owner_id, active_run_id)",
        "CREATE INDEX idx_prof_agent_conversation_updated ON professional_agent_conversations (owner_id, deleted_at, updated_at)",
        "CREATE INDEX idx_prof_agent_messages_turn ON professional_agent_messages (owner_id, conversation_id, turn_id, id)",
        "CREATE INDEX idx_prof_agent_messages_run ON professional_agent_messages (owner_id, conversation_id, run_id, id)",
    ]
    with engine.begin() as connection:
        for statement in statements:
            try:
                connection.execute(text(statement))
            except Exception:
                pass


def _apply_professional_agent_memory_vector_schema(engine: Engine) -> None:
    metadata = MetaData()
    Table(
        "professional_agent_memory_chunks",
        metadata,
        Column("id", BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True),
        Column("owner_id", String(191), nullable=False),
        Column("conversation_id", String(191), nullable=False),
        Column("message_id", BigInteger().with_variant(Integer, "sqlite"), nullable=False),
        Column("chunk_index", Integer, nullable=False, default=0),
        Column("role", String(24), nullable=False),
        Column("message_type", String(40), nullable=False),
        Column("search_text", Text().with_variant(LONGTEXT, "mysql"), nullable=False),
        Column("content_hash", String(64), nullable=False),
        Column("embedding_json", Text().with_variant(LONGTEXT, "mysql"), nullable=True),
        Column("embedding_model", String(191), nullable=False, default=""),
        Column("created_at", DateTime, nullable=False),
        Column("updated_at", DateTime, nullable=False),
        UniqueConstraint(
            "owner_id",
            "conversation_id",
            "message_id",
            "chunk_index",
            name="uq_prof_agent_memory_chunk",
        ),
    )
    metadata.create_all(engine)
    statements = [
        "CREATE INDEX idx_prof_agent_memory_scope ON professional_agent_memory_chunks (owner_id, conversation_id, id)",
        "CREATE INDEX idx_prof_agent_memory_message ON professional_agent_memory_chunks (owner_id, conversation_id, message_id)",
    ]
    with engine.begin() as connection:
        for statement in statements:
            try:
                connection.execute(text(statement))
            except Exception:
                pass


def _apply_professional_agent_run_schema(engine: Engine) -> None:
    metadata = MetaData()
    long_text = Text().with_variant(LONGTEXT, "mysql")
    Table(
        "professional_agent_runs",
        metadata,
        Column("run_id", String(191), primary_key=True),
        Column("owner_id", String(191), nullable=False),
        Column("conversation_id", String(191), nullable=False),
        Column("turn_id", String(191), nullable=False, default=""),
        Column("agent_name", String(120), nullable=False),
        Column("status", String(40), nullable=False, default="pending"),
        Column("request_json", long_text, nullable=False),
        Column("metadata_json", long_text, nullable=False),
        Column("result_json", long_text, nullable=False),
        Column("error", long_text, nullable=False, default=""),
        Column("max_steps", Integer, nullable=False, default=8),
        Column("started_at", String(80), nullable=False, default=""),
        Column("finished_at", String(80), nullable=False, default=""),
        Column("duration_ms", Integer, nullable=True),
        Column("tool_calls", Integer, nullable=False, default=0),
        Column("cancel_requested", Integer, nullable=False, default=0),
        Column("created_at", DateTime, nullable=False),
        Column("updated_at", DateTime, nullable=False),
    )
    Table(
        "professional_agent_run_events",
        metadata,
        Column("id", BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True),
        Column("run_id", String(191), nullable=False),
        Column("sequence", Integer, nullable=False),
        Column("event_type", String(120), nullable=False),
        Column("timestamp", String(80), nullable=False),
        Column("payload_json", long_text, nullable=False),
        Column("created_at", DateTime, nullable=False),
        UniqueConstraint("run_id", "sequence", name="uq_prof_agent_run_event_sequence"),
    )
    metadata.create_all(engine)
    statements = [
        "CREATE INDEX idx_prof_agent_runs_owner_status ON professional_agent_runs (owner_id, status, updated_at)",
        "CREATE INDEX idx_prof_agent_runs_conversation ON professional_agent_runs (owner_id, conversation_id, updated_at)",
        "CREATE INDEX idx_prof_agent_events_run_sequence ON professional_agent_run_events (run_id, sequence)",
    ]
    with engine.begin() as connection:
        for statement in statements:
            try:
                connection.execute(text(statement))
            except Exception:
                pass


def _apply_professional_agent_memory_lifecycle_schema(engine: Engine) -> None:
    metadata = MetaData()
    Table(
        "professional_agent_long_term_memories",
        metadata,
        Column("id", BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True),
        Column("owner_id", String(191), nullable=False),
        Column("scope", String(24), nullable=False),
        Column("scope_id", String(191), nullable=False),
        Column("memory_key", String(191), nullable=False),
        Column("category", String(48), nullable=False),
        Column("content", Text().with_variant(LONGTEXT, "mysql"), nullable=False),
        Column("source_conversation_id", String(191), nullable=False),
        Column("source_run_id", String(191), nullable=False),
        Column("source_message_ids", Text().with_variant(LONGTEXT, "mysql"), nullable=False),
        Column("metadata_json", Text().with_variant(LONGTEXT, "mysql"), nullable=False),
        Column("content_hash", String(64), nullable=False),
        Column("embedding_json", Text().with_variant(LONGTEXT, "mysql"), nullable=True),
        Column("embedding_model", String(191), nullable=False),
        Column("confidence", Float, nullable=False),
        Column("confirmed", Integer, nullable=False),
        Column("status", String(24), nullable=False),
        Column("supersedes_id", Integer, nullable=True),
        Column("retrieval_count", Integer, nullable=False),
        Column("created_at", DateTime, nullable=False),
        Column("updated_at", DateTime, nullable=False),
        Column("last_used_at", DateTime, nullable=True),
        Column("expires_at", DateTime, nullable=True),
        UniqueConstraint("owner_id", "scope", "scope_id", "memory_key", name="uq_prof_agent_long_term_memory_key"),
    )
    Table(
        "professional_agent_memory_digests",
        metadata,
        Column("id", BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True),
        Column("owner_id", String(191), nullable=False),
        Column("digest_date", String(32), nullable=False),
        Column("digest_type", String(24), nullable=False),
        Column("content", Text().with_variant(LONGTEXT, "mysql"), nullable=False),
        Column("source_hash", String(64), nullable=False),
        Column("metadata_json", Text().with_variant(LONGTEXT, "mysql"), nullable=False),
        Column("created_at", DateTime, nullable=False),
        Column("updated_at", DateTime, nullable=False),
        UniqueConstraint("owner_id", "digest_date", "digest_type", name="uq_prof_agent_memory_digest"),
    )
    Table(
        "professional_agent_memory_jobs",
        metadata,
        Column("job_id", String(191), primary_key=True),
        Column("owner_id", String(191), nullable=False),
        Column("conversation_id", String(191), nullable=False),
        Column("run_id", String(191), nullable=False),
        Column("job_type", String(24), nullable=False),
        Column("status", String(24), nullable=False),
        Column("attempts", Integer, nullable=False),
        Column("available_at", DateTime, nullable=False),
        Column("locked_at", DateTime, nullable=True),
        Column("locked_by", String(191), nullable=False),
        Column("error", Text().with_variant(LONGTEXT, "mysql"), nullable=False),
        Column("created_at", DateTime, nullable=False),
        Column("updated_at", DateTime, nullable=False),
    )
    metadata.create_all(engine)
    statements = [
        "CREATE INDEX idx_prof_agent_long_term_scope ON professional_agent_long_term_memories (owner_id, scope, scope_id, status)",
        "CREATE INDEX idx_prof_agent_long_term_updated ON professional_agent_long_term_memories (owner_id, status, updated_at)",
        "CREATE INDEX idx_prof_agent_memory_jobs_claim ON professional_agent_memory_jobs (status, available_at, created_at)",
        "CREATE INDEX idx_prof_agent_memory_jobs_owner ON professional_agent_memory_jobs (owner_id, job_type, status)",
        "CREATE INDEX idx_prof_agent_memory_digests_owner ON professional_agent_memory_digests (owner_id, digest_date, digest_type)",
    ]
    with engine.begin() as connection:
        for statement in statements:
            try:
                connection.execute(text(statement))
            except Exception:
                pass


def _apply_professional_agent_folder_schema(engine: Engine) -> None:
    metadata = MetaData()
    long_text = Text().with_variant(LONGTEXT, "mysql")
    Table(
        "professional_agent_folder_assets",
        metadata,
        Column("folder_id", String(191), primary_key=True),
        Column("owner_id", String(191), nullable=False),
        Column("conversation_id", String(191), nullable=False),
        Column("name", String(191), nullable=False),
        Column("status", String(24), nullable=False),
        Column("item_count", Integer, nullable=False),
        Column("total_bytes", BigInteger().with_variant(Integer, "sqlite"), nullable=False),
        Column("summary_json", long_text, nullable=False),
        Column("created_at", DateTime, nullable=False),
        Column("updated_at", DateTime, nullable=False),
    )
    Table(
        "professional_agent_folder_items",
        metadata,
        Column("id", BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True),
        Column("folder_id", String(191), nullable=False),
        Column("owner_id", String(191), nullable=False),
        Column("relative_name", String(500), nullable=False),
        Column("filename", String(191), nullable=False),
        Column("mime_type", String(120), nullable=False),
        Column("size", BigInteger().with_variant(Integer, "sqlite"), nullable=False),
        Column("width", Integer, nullable=False),
        Column("height", Integer, nullable=False),
        Column("sha256", String(64), nullable=False),
        Column("category", String(40), nullable=False),
        Column("storage_rel", String(500), nullable=False),
        Column("url", String(2000), nullable=False),
        Column("analysis_json", long_text, nullable=False),
        Column("status", String(24), nullable=False),
        Column("created_at", DateTime, nullable=False),
        UniqueConstraint("folder_id", "relative_name", name="uq_prof_agent_folder_item_name"),
    )
    Table(
        "professional_agent_batch_plans",
        metadata,
        Column("plan_id", String(191), primary_key=True),
        Column("owner_id", String(191), nullable=False),
        Column("conversation_id", String(191), nullable=False),
        Column("run_id", String(191), nullable=False),
        Column("folder_id", String(191), nullable=False),
        Column("status", String(32), nullable=False),
        Column("request_json", long_text, nullable=False),
        Column("summary_json", long_text, nullable=False),
        Column("total_items", Integer, nullable=False),
        Column("completed_items", Integer, nullable=False),
        Column("failed_items", Integer, nullable=False),
        Column("created_at", DateTime, nullable=False),
        Column("updated_at", DateTime, nullable=False),
    )
    Table(
        "professional_agent_batch_plan_items",
        metadata,
        Column("id", BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True),
        Column("plan_id", String(191), nullable=False),
        Column("folder_item_id", BigInteger().with_variant(Integer, "sqlite"), nullable=False),
        Column("item_index", Integer, nullable=False),
        Column("title", String(191), nullable=False),
        Column("purpose", String(500), nullable=False),
        Column("prompt", long_text, nullable=False),
        Column("task_id", String(191), nullable=False),
        Column("status", String(24), nullable=False),
        Column("attempts", Integer, nullable=False),
        Column("error", long_text, nullable=False),
        Column("created_at", DateTime, nullable=False),
        Column("updated_at", DateTime, nullable=False),
        UniqueConstraint("plan_id", "folder_item_id", name="uq_prof_agent_batch_plan_item"),
    )
    metadata.create_all(engine)
    statements = [
        "CREATE INDEX idx_prof_agent_folder_owner_updated ON professional_agent_folder_assets (owner_id, status, updated_at)",
        "CREATE INDEX idx_prof_agent_folder_items_owner_folder ON professional_agent_folder_items (owner_id, folder_id, id)",
        "CREATE INDEX idx_prof_agent_batch_plans_owner_status ON professional_agent_batch_plans (owner_id, status, updated_at)",
        "CREATE INDEX idx_prof_agent_batch_items_plan_status ON professional_agent_batch_plan_items (plan_id, status, item_index)",
    ]
    with engine.begin() as connection:
        for statement in statements:
            try:
                connection.execute(text(statement))
            except Exception:
                pass


def _apply_professional_agent_video_schema(engine: Engine) -> None:
    metadata = MetaData()
    long_text = Text().with_variant(LONGTEXT, "mysql")
    Table(
        "professional_agent_video_assets",
        metadata,
        Column("video_id", String(191), primary_key=True),
        Column("owner_id", String(191), nullable=False),
        Column("conversation_id", String(191), nullable=False),
        Column("filename", String(191), nullable=False),
        Column("mime_type", String(120), nullable=False),
        Column("size", BigInteger().with_variant(Integer, "sqlite"), nullable=False),
        Column("sha256", String(64), nullable=False),
        Column("storage_provider", String(32), nullable=False),
        Column("bucket", String(191), nullable=False),
        Column("object_key", String(1000), nullable=False),
        Column("url", String(2000), nullable=False),
        Column("status", String(32), nullable=False),
        Column("analysis_status", String(32), nullable=False),
        Column("analysis_json", long_text, nullable=False),
        Column("analysis_error", long_text, nullable=False, default=""),
        Column("analysis_started_at", DateTime, nullable=True),
        Column("analysis_finished_at", DateTime, nullable=True),
        Column("analysis_version", Integer, nullable=False, default=1),
        Column("created_at", DateTime, nullable=False),
        Column("updated_at", DateTime, nullable=False),
        UniqueConstraint("owner_id", "sha256", name="uq_prof_agent_video_owner_sha"),
    )
    metadata.create_all(engine)
    statements = [
        "CREATE INDEX idx_prof_agent_video_owner_updated ON professional_agent_video_assets (owner_id, status, updated_at)",
        "CREATE INDEX idx_prof_agent_video_conversation ON professional_agent_video_assets (owner_id, conversation_id, created_at)",
    ]
    with engine.begin() as connection:
        for statement in statements:
            try:
                connection.execute(text(statement))
            except Exception:
                pass


def _apply_professional_agent_video_analysis_schema(engine: Engine) -> None:
    if not _table_exists(engine, "professional_agent_video_assets"):
        _apply_professional_agent_video_schema(engine)
    definitions = {
        "analysis_error": "TEXT NULL",
        "analysis_started_at": "DATETIME NULL",
        "analysis_finished_at": "DATETIME NULL",
        "analysis_version": "INTEGER NOT NULL DEFAULT 1",
    }
    with engine.begin() as connection:
        columns = _column_names(connection, "professional_agent_video_assets")
        for name, definition in definitions.items():
            if name not in columns:
                connection.execute(text(f"ALTER TABLE professional_agent_video_assets ADD COLUMN {name} {definition}"))
        for statement in (
            "CREATE INDEX idx_prof_agent_video_analysis_status ON professional_agent_video_assets (analysis_status, updated_at)",
            "CREATE INDEX idx_prof_agent_video_owner_analysis ON professional_agent_video_assets (owner_id, analysis_status, updated_at)",
        ):
            try:
                connection.execute(text(statement))
            except Exception:
                pass


def _apply_video_generation_task_schema(engine: Engine) -> None:
    metadata = MetaData()
    long_text = Text().with_variant(LONGTEXT, "mysql")
    Table(
        "video_generation_tasks",
        metadata,
        Column("key", String(383), primary_key=True),
        Column("owner_id", String(191), nullable=False),
        Column("task_id", String(191), nullable=False),
        Column("conversation_id", String(191), nullable=False, default=""),
        Column("status", String(32), nullable=False),
        Column("mode", String(32), nullable=False),
        Column("model", String(191), nullable=True),
        Column("upstream_task_id", String(191), nullable=True),
        Column("created_at", DateTime, nullable=True),
        Column("updated_at", DateTime, nullable=True),
        Column("task_json", long_text, nullable=False),
    )
    metadata.create_all(engine)
    statements = [
        "CREATE INDEX idx_video_generation_owner_updated ON video_generation_tasks (owner_id, updated_at)",
        "CREATE INDEX idx_video_generation_owner_conversation_updated ON video_generation_tasks (owner_id, conversation_id, updated_at)",
        "CREATE INDEX idx_video_generation_status_updated ON video_generation_tasks (status, updated_at)",
        "CREATE INDEX idx_video_generation_upstream_task ON video_generation_tasks (upstream_task_id)",
    ]
    with engine.begin() as connection:
        for statement in statements:
            try:
                connection.execute(text(statement))
            except Exception:
                pass


def _apply_canvas_workflow_schema(engine: Engine) -> None:
    metadata = MetaData()
    Table(
        "canvas_workflows",
        metadata,
        Column("owner_id", String(191), primary_key=True),
        Column("id", String(191), primary_key=True),
        Column("title", String(191), nullable=False, default=""),
        Column("cover_url", Text().with_variant(LONGTEXT, "mysql"), nullable=True),
        Column("node_count", Integer, nullable=False, default=0),
        Column("revision", Integer, nullable=False, default=1),
        Column("payload_json", Text().with_variant(LONGTEXT, "mysql"), nullable=False),
        Column("created_at", DateTime, nullable=False),
        Column("updated_at", DateTime, nullable=False),
    )
    metadata.create_all(engine)
    with engine.begin() as connection:
        for statement in (
            "CREATE INDEX idx_canvas_workflows_owner_updated ON canvas_workflows (owner_id, updated_at)",
            "CREATE INDEX idx_canvas_workflows_owner_title ON canvas_workflows (owner_id, title)",
        ):
            try:
                connection.execute(text(statement))
            except Exception:
                pass


def _apply_video_generation_conversation_schema(engine: Engine) -> None:
    if not _table_exists(engine, "video_generation_tasks"):
        _apply_video_generation_task_schema(engine)
    with engine.begin() as connection:
        columns = _column_names(connection, "video_generation_tasks")
        if "conversation_id" not in columns:
            connection.execute(
                text("ALTER TABLE video_generation_tasks ADD COLUMN conversation_id VARCHAR(191) NOT NULL DEFAULT ''")
            )
        try:
            connection.execute(
                text(
                    "CREATE INDEX idx_video_generation_owner_conversation_updated "
                    "ON video_generation_tasks (owner_id, conversation_id, updated_at)"
                )
            )
        except Exception:
            pass


def _apply_video_generation_reliability_schema(engine: Engine) -> None:
    from sqlalchemy.orm import Session

    from services.video.video_generation_records import RecordsBase, backfill_records
    from services.video.video_generation_task_store import Base as VideoGenerationBase

    VideoGenerationBase.metadata.create_all(engine)
    RecordsBase.metadata.create_all(engine)
    _ensure_video_generation_reconciliation_column(engine)
    if not _table_exists(engine, "video_generation_tasks"):
        return

    cursor = ""
    while True:
        with Session(engine) as session, session.begin():
            result = backfill_records(session, after_key=cursor, batch_size=500)
        cursor = str(result["next_key"])
        if bool(result["done"]):
            break


def _apply_video_generation_history_query_schema(engine: Engine) -> None:
    if not _table_exists(engine, "video_generation_tasks"):
        _apply_video_generation_task_schema(engine)
    prompt_type = "LONGTEXT NULL" if engine.dialect.name == "mysql" else "TEXT NULL"
    with engine.begin() as connection:
        if "prompt" not in _column_names(connection, "video_generation_tasks"):
            connection.execute(text(
                f"ALTER TABLE video_generation_tasks ADD COLUMN prompt {prompt_type}"
            ))

    task_key_column = _quoted_identifier(engine, "key")
    cursor = ""
    while True:
        with engine.begin() as connection:
            rows = connection.execute(
                text(
                    f"SELECT {task_key_column} AS task_key, task_json FROM video_generation_tasks "
                    f"WHERE {task_key_column} > :cursor ORDER BY {task_key_column} LIMIT 500"
                ),
                {"cursor": cursor},
            ).mappings().all()
            updates = []
            for row in rows:
                try:
                    task = json.loads(row["task_json"])
                    prompt = str(task.get("prompt") or "")[:12000] if isinstance(task, dict) else ""
                except (TypeError, ValueError):
                    prompt = ""
                updates.append({"key": row["task_key"], "prompt": prompt})
            if updates:
                connection.execute(
                    text(
                        f"UPDATE video_generation_tasks SET prompt = :prompt "
                        f"WHERE {task_key_column} = :key"
                    ),
                    updates,
                )
        if not rows or len(rows) < 500:
            break
        cursor = str(rows[-1]["task_key"])

    statements = (
        "CREATE INDEX idx_video_generation_owner_status_created_key "
        "ON video_generation_tasks (owner_id, status, created_at, key)",
        "CREATE INDEX idx_video_generation_status_created_key "
        "ON video_generation_tasks (status, created_at, key)",
    )
    with engine.begin() as connection:
        for statement in statements:
            try:
                connection.execute(text(statement))
            except Exception:
                pass


def _ensure_video_generation_reconciliation_column(engine: Engine) -> None:
    if not _table_exists(engine, "video_generation_records"):
        return
    with engine.begin() as connection:
        columns = _column_names(connection, "video_generation_records")
        if "reconciliation_required" not in columns:
            default = "FALSE" if engine.dialect.name == "postgresql" else "0"
            connection.execute(text(
                "ALTER TABLE video_generation_records ADD COLUMN "
                f"reconciliation_required BOOLEAN NOT NULL DEFAULT {default}"
            ))


def _apply_video_generation_reconciliation_schema(engine: Engine) -> None:
    from services.video.video_generation_records import RecordsBase

    RecordsBase.metadata.create_all(engine)
    _ensure_video_generation_reconciliation_column(engine)

    if _table_exists(engine, "video_generation_tasks"):
        task_key_column = _quoted_identifier(engine, "key")
        cursor = ""
        while True:
            with engine.begin() as connection:
                rows = connection.execute(
                    text(
                        f"SELECT {task_key_column} AS task_key, task_json FROM video_generation_tasks "
                        f"WHERE {task_key_column} > :cursor ORDER BY {task_key_column} LIMIT 500"
                    ),
                    {"cursor": cursor},
                ).mappings().all()
                updates = []
                for row in rows:
                    try:
                        task = json.loads(row["task_json"])
                        required = bool(task.get("reconciliation_required")) if isinstance(task, dict) else False
                    except (TypeError, ValueError):
                        required = False
                    updates.append({"key": row["task_key"], "required": required})
                if updates:
                    connection.execute(
                        text(
                            "UPDATE video_generation_records SET reconciliation_required = :required "
                            "WHERE task_key = :key"
                        ),
                        updates,
                    )
            if not rows or len(rows) < 500:
                break
            cursor = str(rows[-1]["task_key"])

    with engine.begin() as connection:
        try:
            connection.execute(text(
                "CREATE INDEX idx_video_record_reconciliation_event "
                "ON video_generation_records (reconciliation_required, event_at)"
            ))
        except Exception:
            pass


def _apply_audio_generation_schema(engine: Engine) -> None:
    from services.audio.audio_generation_records import RecordsBase as AudioRecordsBase
    from services.audio.audio_generation_task_store import Base as AudioGenerationBase

    AudioGenerationBase.metadata.create_all(engine)
    AudioRecordsBase.metadata.create_all(engine)


def _apply_video_agent_message_schema(engine: Engine) -> None:
    metadata = MetaData()
    long_text = Text().with_variant(LONGTEXT, "mysql")
    Table(
        "video_agent_messages",
        metadata,
        Column("id", String(191), primary_key=True),
        Column("owner_id", String(191), nullable=False),
        Column("owner_username", String(191), nullable=False, default=""),
        Column("owner_name", String(191), nullable=False, default=""),
        Column("conversation_id", String(191), nullable=False),
        Column("turn_id", String(191), nullable=False, default=""),
        Column("prompt", long_text, nullable=False),
        Column("message", long_text, nullable=False),
        Column("chat_model", String(191), nullable=False, default=""),
        Column("duration_ms", Integer, nullable=True),
        Column("created_at", DateTime, nullable=False),
    )
    metadata.create_all(engine)
    statements = [
        "CREATE INDEX idx_video_agent_owner_conversation_created "
        "ON video_agent_messages (owner_id, conversation_id, created_at)",
        "CREATE INDEX idx_video_agent_owner_created ON video_agent_messages (owner_id, created_at)",
    ]
    with engine.begin() as connection:
        for statement in statements:
            try:
                connection.execute(text(statement))
            except Exception:
                pass


def _apply_video_agent_reasoning_schema(engine: Engine) -> None:
    if not _table_exists(engine, "video_agent_messages"):
        _apply_video_agent_message_schema(engine)
    summary_type = "LONGTEXT NULL" if engine.dialect.name == "mysql" else "TEXT NULL"
    definitions = {
        "reasoning_summary": summary_type,
        "reasoning_enabled": "BOOLEAN NOT NULL DEFAULT FALSE",
    }
    with engine.begin() as connection:
        columns = _column_names(connection, "video_agent_messages")
        for name, definition in definitions.items():
            if name not in columns:
                connection.execute(text(f"ALTER TABLE video_agent_messages ADD COLUMN {name} {definition}"))


def _apply_video_agent_attachment_schema(engine: Engine) -> None:
    if not _table_exists(engine, "video_agent_messages"):
        _apply_video_agent_message_schema(engine)
    attachment_type = "LONGTEXT NULL" if engine.dialect.name == "mysql" else "TEXT NULL"
    with engine.begin() as connection:
        columns = _column_names(connection, "video_agent_messages")
        if "attachments_json" not in columns:
            connection.execute(
                text(
                    "ALTER TABLE video_agent_messages "
                    f"ADD COLUMN attachments_json {attachment_type}"
                )
            )


def _apply_video_agent_async_schema(engine: Engine) -> None:
    if not _table_exists(engine, "video_agent_messages"):
        _apply_video_agent_message_schema(engine)
    status_type = "VARCHAR(32) NOT NULL DEFAULT 'completed'"
    error_type = "LONGTEXT NULL" if engine.dialect.name == "mysql" else "TEXT NULL"
    with engine.begin() as connection:
        columns = _column_names(connection, "video_agent_messages")
        if "status" not in columns:
            connection.execute(
                text(
                    "ALTER TABLE video_agent_messages "
                    f"ADD COLUMN status {status_type}"
                )
            )
        if "analysis_error" not in columns:
            connection.execute(
                text(
                    "ALTER TABLE video_agent_messages "
                    f"ADD COLUMN analysis_error {error_type}"
                )
            )
        try:
            connection.execute(
                text(
                    "CREATE INDEX idx_video_agent_owner_status_created "
                    "ON video_agent_messages (owner_id, status, created_at)"
                )
            )
        except Exception:
            pass


def _apply_professional_knowledge_schema(engine: Engine) -> None:
    metadata = MetaData()
    long_text = Text().with_variant(LONGTEXT, "mysql")
    Table(
        "professional_knowledge_documents",
        metadata,
        Column("document_id", String(191), primary_key=True),
        Column("source_path", String(500), nullable=False),
        Column("title", String(191), nullable=False),
        Column("content", long_text, nullable=False),
        Column("content_hash", String(64), nullable=False),
        Column("created_at", DateTime, nullable=False),
        Column("updated_at", DateTime, nullable=False),
    )
    Table(
        "professional_knowledge_chunks",
        metadata,
        Column("id", BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True),
        Column("chunk_id", String(191), nullable=False, unique=True),
        Column("document_id", String(191), nullable=False),
        Column("section_id", String(191), nullable=False),
        Column("position", Integer, nullable=False),
        Column("title", String(191), nullable=False),
        Column("content", long_text, nullable=False),
        Column("search_text", long_text, nullable=False),
        Column("content_hash", String(64), nullable=False),
        Column("vector_model", String(191), nullable=False),
        Column("vector_synced_at", DateTime, nullable=True),
        Column("created_at", DateTime, nullable=False),
        Column("updated_at", DateTime, nullable=False),
    )
    metadata.create_all(engine)
    statements = [
        "CREATE INDEX idx_prof_knowledge_document_updated ON professional_knowledge_documents (updated_at)",
        "CREATE INDEX idx_prof_knowledge_chunk_document ON professional_knowledge_chunks (document_id, position)",
        "CREATE INDEX idx_prof_knowledge_chunk_vector ON professional_knowledge_chunks (vector_model, vector_synced_at)",
    ]
    with engine.begin() as connection:
        for statement in statements:
            try:
                connection.execute(text(statement))
            except Exception:
                pass


def _apply_professional_memory_review_governance(engine: Engine) -> None:
    table_name = "professional_agent_long_term_memories"
    if not _table_exists(engine, table_name):
        return
    with engine.begin() as connection:
        connection.execute(
            text(
                f"UPDATE {table_name} "
                "SET confidence = :trusted_confidence "
                "WHERE confirmed = 1 AND confidence < :trusted_confidence"
            ),
            {"trusted_confidence": 0.9},
        )
        connection.execute(
            text(
                f"UPDATE {table_name} "
                "SET status = :pending_status, confirmed = 0, embedding_json = NULL, embedding_model = '' "
                "WHERE status = :active_status AND ("
                "metadata_json LIKE :distilled_compact "
                "OR metadata_json LIKE :distilled_spaced "
                "OR metadata_json LIKE :candidate_compact "
                "OR metadata_json LIKE :candidate_spaced "
                "OR metadata_json LIKE :proposed_compact "
                "OR metadata_json LIKE :proposed_spaced"
                ")"
            ),
            {
                "pending_status": "pending_review",
                "active_status": "active",
                "distilled_compact": '%"distilled":true%',
                "distilled_spaced": '%"distilled": true%',
                "candidate_compact": '%"autoCandidate":true%',
                "candidate_spaced": '%"autoCandidate": true%',
                "proposed_compact": '%"agentProposed":true%',
                "proposed_spaced": '%"agentProposed": true%',
            },
        )


def _apply_chatgpt_style_memory_scopes(engine: Engine) -> None:
    table_name = "professional_agent_long_term_memories"
    if not _table_exists(engine, table_name):
        return
    with engine.begin() as connection:
        connection.execute(
            text(
                f"UPDATE {table_name} "
                "SET status = :rejected_status, confirmed = 0, embedding_json = NULL, embedding_model = '' "
                "WHERE status = :pending_status AND ("
                "content LIKE :retrieval_mode "
                "OR content LIKE :tool_result "
                "OR (content LIKE :sections AND content LIKE :sources)"
                ")"
            ),
            {
                "rejected_status": "rejected",
                "pending_status": "pending_review",
                "retrieval_mode": '%"retrievalMode"%',
                "tool_result": '%"tool_use_id"%',
                "sections": '%"sections"%',
                "sources": '%"sources"%',
            },
        )

        rows = connection.execute(
            text(
                f"SELECT id, owner_id, memory_key, source_conversation_id FROM {table_name} "
                "WHERE status = :active_status AND confirmed = 1 AND scope = :user_scope "
                "AND category = :product_category AND source_conversation_id <> ''"
            ),
            {
                "active_status": "active",
                "user_scope": "user",
                "product_category": "product",
            },
        ).mappings().all()
        for row in rows:
            existing_id = connection.execute(
                text(
                    f"SELECT id FROM {table_name} WHERE owner_id = :owner_id AND scope = :project_scope "
                    "AND scope_id = :scope_id AND memory_key = :memory_key AND id <> :memory_id"
                ),
                {
                    "owner_id": row["owner_id"],
                    "project_scope": "project",
                    "scope_id": row["source_conversation_id"],
                    "memory_key": row["memory_key"],
                    "memory_id": row["id"],
                },
            ).scalar()
            if existing_id:
                connection.execute(
                    text(
                        f"UPDATE {table_name} SET status = :superseded_status, embedding_json = NULL, "
                        "embedding_model = '' WHERE id = :memory_id"
                    ),
                    {"superseded_status": "superseded", "memory_id": row["id"]},
                )
                continue
            connection.execute(
                text(
                    f"UPDATE {table_name} SET scope = :project_scope, scope_id = :scope_id, "
                    "embedding_json = NULL, embedding_model = '' WHERE id = :memory_id"
                ),
                {
                    "project_scope": "project",
                    "scope_id": row["source_conversation_id"],
                    "memory_id": row["id"],
                },
            )


def _apply_professional_agent_operational_indexes(engine: Engine) -> None:
    statements = {
        "professional_agent_messages": [
            "CREATE INDEX idx_prof_agent_messages_context ON professional_agent_messages "
            "(owner_id, conversation_id, created_at, id)",
        ],
        "professional_agent_state_snapshots": [
            "CREATE INDEX idx_prof_agent_snapshots_updated ON professional_agent_state_snapshots "
            "(owner_id, conversation_id, updated_at)",
        ],
        "professional_agent_long_term_memories": [
            "CREATE INDEX idx_prof_agent_long_term_retrieval ON professional_agent_long_term_memories "
            "(owner_id, status, confirmed, expires_at, updated_at)",
        ],
    }
    with engine.begin() as connection:
        for table_name, table_statements in statements.items():
            if not _table_exists(engine, table_name):
                continue
            for statement in table_statements:
                try:
                    connection.execute(text(statement))
                except Exception:
                    pass


MIGRATIONS: tuple[tuple[str, Callable[[Engine], None]], ...] = (
    ("001_base_schema", _apply_base_schema),
    ("002_image_task_batch_columns", _apply_image_task_columns),
    ("003_image_task_indexes", _apply_image_task_indexes),
    ("004_reference_image_asset_cache", _apply_reference_image_asset_cache),
    ("005_generation_stage_timings", _apply_generation_stage_columns),
    ("006_operational_indexes", _apply_operational_indexes),
    ("007_relational_constraints", _apply_relational_constraints),
    ("008_image_conversation_schema", _apply_image_conversation_schema),
    ("009_system_announcement_schema", _apply_system_announcement_schema),
    ("010_professional_agent_memory", _apply_professional_agent_memory_schema),
    ("011_professional_agent_memory_vectors", _apply_professional_agent_memory_vector_schema),
    ("012_professional_agent_runs", _apply_professional_agent_run_schema),
    ("013_professional_agent_memory_lifecycle", _apply_professional_agent_memory_lifecycle_schema),
    ("014_professional_agent_folders_and_batches", _apply_professional_agent_folder_schema),
    ("015_professional_knowledge_mysql", _apply_professional_knowledge_schema),
    ("016_professional_memory_review_governance", _apply_professional_memory_review_governance),
    ("017_chatgpt_style_memory_scopes", _apply_chatgpt_style_memory_scopes),
    ("018_professional_agent_operational_indexes", _apply_professional_agent_operational_indexes),
    ("019_generation_cost_monitoring", _apply_generation_cost_columns),
    ("021_remove_model_token_monitoring", _remove_model_token_monitoring_schema),
    ("022_professional_agent_videos", _apply_professional_agent_video_schema),
    ("023_professional_agent_video_analysis", _apply_professional_agent_video_analysis_schema),
    ("024_video_generation_tasks", _apply_video_generation_task_schema),
    ("025_video_generation_conversations", _apply_video_generation_conversation_schema),
    ("026_video_agent_messages", _apply_video_agent_message_schema),
    ("027_video_agent_reasoning", _apply_video_agent_reasoning_schema),
    ("028_video_agent_attachments", _apply_video_agent_attachment_schema),
    ("029_video_agent_async_processing", _apply_video_agent_async_schema),
    ("031_video_generation_reliability", _apply_video_generation_reliability_schema),
    ("032_video_generation_history_query", _apply_video_generation_history_query_schema),
    ("033_video_generation_reconciliation_state", _apply_video_generation_reconciliation_schema),
    ("034_image_task_projection_outbox", _apply_image_task_projection_outbox),
    ("035_history_query_indexes", _apply_history_query_indexes),
    ("036_generation_event_query_path", _apply_generation_event_query_path),
    ("037_image_asset_database_index", _apply_image_asset_database_index),
    ("038_upstream_usage_ledger", _apply_upstream_usage_ledger),
    ("039_upstream_usage_event_index", _apply_upstream_usage_event_index),
    ("040_upstream_usage_model_attribution", _apply_upstream_usage_model_attribution),
    ("041_upstream_usage_chat_attribution", _apply_upstream_usage_chat_attribution),
    ("042_audio_generation_tasks", _apply_audio_generation_schema),
    ("043_canvas_workflows_production", _apply_canvas_workflow_schema),
)


def migration_status(database_url: str) -> dict[str, object]:
    engine = create_engine(database_url, pool_pre_ping=True, pool_recycle=3600)
    try:
        metadata = MetaData()
        table = _migration_table(metadata)
        _ensure_migration_table(engine, table)
        with engine.connect() as connection:
            applied = {
                str(row[0])
                for row in connection.execute(text(f"SELECT version FROM {MIGRATION_TABLE}"))
            }
        versions = [version for version, _ in MIGRATIONS]
        return {
            "database": database_url.split("@")[-1],
            "applied": [version for version in versions if version in applied],
            "pending": [version for version in versions if version not in applied],
        }
    finally:
        engine.dispose()


def run_migrations(database_url: str, *, dry_run: bool = False) -> dict[str, object]:
    engine = create_engine(database_url, pool_pre_ping=True, pool_recycle=3600)
    try:
        metadata = MetaData()
        table = _migration_table(metadata)
        _ensure_migration_table(engine, table)
        with _migration_lock(engine):
            with engine.connect() as connection:
                applied = {
                    str(row[0])
                    for row in connection.execute(text(f"SELECT version FROM {MIGRATION_TABLE}"))
                }
            pending = [version for version, _ in MIGRATIONS if version not in applied]
            if dry_run:
                return {"applied": sorted(applied), "pending": pending, "dry_run": True}

            applied_now: list[str] = []
            for version, handler in MIGRATIONS:
                if version in applied:
                    continue
                handler(engine)
                with engine.begin() as connection:
                    connection.execute(table.insert().values(version=version, applied_at=datetime.now()))
                applied_now.append(version)
            return {
                "applied": sorted(applied | set(applied_now)),
                "applied_now": applied_now,
                "pending": [],
                "dry_run": False,
            }
    finally:
        engine.dispose()
