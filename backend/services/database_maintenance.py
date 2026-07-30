from __future__ import annotations

import threading
from datetime import datetime
from typing import Any

from sqlalchemy import create_engine, inspect, text

from services.database_migrations import run_migrations
from services.enterprise_schema import resolve_enterprise_database_url
from services.user_service import user_service
from utils.log import logger

DEFAULT_SESSION_CLEANUP_INTERVAL_SECS = 30 * 60


def _table_set(engine) -> set[str]:
    return set(inspect(engine).get_table_names())


def _count(connection, statement: str, params: dict[str, Any] | None = None) -> int:
    return int(connection.execute(text(statement), params or {}).scalar() or 0)


def collect_integrity_report(database_url: str | None = None) -> dict[str, int]:
    resolved_url = resolve_enterprise_database_url(database_url)
    engine = create_engine(resolved_url, pool_pre_ping=True, pool_recycle=3600)
    try:
        tables = _table_set(engine)
        checks: dict[str, tuple[set[str], str, dict[str, Any]]] = {
            "expired_or_revoked_sessions": (
                {"business_user_sessions"},
                "SELECT COUNT(*) FROM business_user_sessions WHERE expires_at <= :now OR revoked_at IS NOT NULL",
                {"now": datetime.now()},
            ),
            "sessions_without_user": (
                {"business_user_sessions", "business_users"},
                "SELECT COUNT(*) FROM business_user_sessions s LEFT JOIN business_users u ON u.id = s.user_id WHERE u.id IS NULL",
                {},
            ),
            "generated_images_without_user": (
                {"generated_images", "business_users"},
                "SELECT COUNT(*) FROM generated_images g LEFT JOIN business_users u ON u.id = g.owner_id WHERE u.id IS NULL",
                {},
            ),
            "image_tasks_without_user": (
                {"image_tasks", "business_users"},
                "SELECT COUNT(*) FROM image_tasks t LEFT JOIN business_users u ON u.id = t.owner_id WHERE u.id IS NULL",
                {},
            ),
            "generation_events_without_user": (
                {"generation_task_events", "business_users"},
                "SELECT COUNT(*) FROM generation_task_events e LEFT JOIN business_users u ON u.id = e.owner_id WHERE u.id IS NULL",
                {},
            ),
            "product_references_without_product": (
                {"business_product_references", "business_products"},
                "SELECT COUNT(*) FROM business_product_references r LEFT JOIN business_products p ON p.id = r.product_id WHERE p.id IS NULL",
                {},
            ),
        }
        report: dict[str, int] = {}
        with engine.connect() as connection:
            for key, (required_tables, statement, params) in checks.items():
                if not required_tables.issubset(tables):
                    continue
                report[key] = _count(connection, statement, params)
        return report
    finally:
        engine.dispose()


def repair_integrity_issues(
    database_url: str | None = None,
    *,
    purge_legacy_activity: bool = False,
) -> dict[str, int]:
    resolved_url = resolve_enterprise_database_url(database_url)
    engine = create_engine(resolved_url, pool_pre_ping=True, pool_recycle=3600)
    try:
        tables = _table_set(engine)
        statements: dict[str, tuple[set[str], str]] = {
            "sessions_without_user_removed": (
                {"business_user_sessions", "business_users"},
                "DELETE FROM business_user_sessions "
                "WHERE NOT EXISTS (SELECT 1 FROM business_users u WHERE u.id = business_user_sessions.user_id)",
            ),
            "product_references_without_product_removed": (
                {"business_product_references", "business_products"},
                "DELETE FROM business_product_references "
                "WHERE NOT EXISTS (SELECT 1 FROM business_products p WHERE p.id = business_product_references.product_id)",
            ),
            "generated_images_without_user_removed": (
                {"generated_images", "business_users"},
                "DELETE FROM generated_images "
                "WHERE NOT EXISTS (SELECT 1 FROM business_users u WHERE u.id = generated_images.owner_id)",
            ),
        }
        if purge_legacy_activity:
            statements.update(
                {
                    "terminal_image_tasks_without_user_removed": (
                        {"image_tasks", "business_users"},
                        "DELETE FROM image_tasks "
                        "WHERE status IN ('success', 'error', 'canceled') "
                        "AND NOT EXISTS (SELECT 1 FROM business_users u WHERE u.id = image_tasks.owner_id)",
                    ),
                    "generation_events_without_user_removed": (
                        {"generation_task_events", "business_users"},
                        "DELETE FROM generation_task_events "
                        "WHERE NOT EXISTS (SELECT 1 FROM business_users u WHERE u.id = generation_task_events.owner_id)",
                    ),
                }
            )
        removed: dict[str, int] = {}
        with engine.begin() as connection:
            for key, (required_tables, statement) in statements.items():
                if not required_tables.issubset(tables):
                    continue
                result = connection.execute(text(statement))
                removed[key] = int(result.rowcount or 0)
        return removed
    finally:
        engine.dispose()


def ensure_database_ready(
    database_url: str | None = None,
    *,
    strict: bool = False,
    cleanup_sessions: bool = True,
    repair_integrity: bool = False,
    purge_legacy_activity: bool = False,
) -> dict[str, Any]:
    resolved_url = resolve_enterprise_database_url(database_url)
    result: dict[str, Any] = {}
    try:
        result["migrations"] = run_migrations(resolved_url)
    except Exception as exc:
        result["migrations_error"] = str(exc)
        if strict:
            raise

    if cleanup_sessions:
        try:
            result["expired_sessions_removed"] = user_service.cleanup_expired_sessions()
        except Exception as exc:
            result["session_cleanup_error"] = str(exc)
            if strict:
                raise

    if repair_integrity:
        try:
            result["integrity_repair"] = repair_integrity_issues(
                resolved_url,
                purge_legacy_activity=purge_legacy_activity,
            )
        except Exception as exc:
            result["integrity_repair_error"] = str(exc)
            if strict:
                raise

    try:
        result["integrity"] = collect_integrity_report(resolved_url)
    except Exception as exc:
        result["integrity_error"] = str(exc)
        if strict:
            raise
    return result


def _maintenance_worker(stop_event: threading.Event, interval_secs: int) -> None:
    while not stop_event.wait(interval_secs):
        try:
            removed = user_service.cleanup_expired_sessions()
            if removed:
                logger.info({"event": "database_session_cleanup", "expired_sessions_removed": removed})
        except Exception as exc:
            logger.info({"event": "database_session_cleanup_failed", "error": str(exc)})


def start_database_maintenance_scheduler(
    stop_event: threading.Event,
    *,
    interval_secs: int = DEFAULT_SESSION_CLEANUP_INTERVAL_SECS,
) -> threading.Thread:
    interval = max(60, int(interval_secs or DEFAULT_SESSION_CLEANUP_INTERVAL_SECS))
    thread = threading.Thread(
        target=_maintenance_worker,
        args=(stop_event, interval),
        daemon=True,
        name="database-maintenance",
    )
    thread.start()
    return thread
