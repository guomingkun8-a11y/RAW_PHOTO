from __future__ import annotations

from datetime import datetime
from typing import Callable

from sqlalchemy import Column, DateTime, MetaData, String, Table, Text, create_engine, inspect, text
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

from services.enterprise_schema import EnterpriseBase
from services.image_task_store import Base as ImageTaskBase

MIGRATION_TABLE = "schema_migrations"


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


MIGRATIONS: tuple[tuple[str, Callable[[Engine], None]], ...] = (
    ("001_base_schema", _apply_base_schema),
    ("002_image_task_batch_columns", _apply_image_task_columns),
    ("003_image_task_indexes", _apply_image_task_indexes),
    ("004_reference_image_asset_cache", _apply_reference_image_asset_cache),
    ("005_generation_stage_timings", _apply_generation_stage_columns),
    ("006_operational_indexes", _apply_operational_indexes),
    ("007_relational_constraints", _apply_relational_constraints),
    ("008_image_conversation_schema", _apply_image_conversation_schema),
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
