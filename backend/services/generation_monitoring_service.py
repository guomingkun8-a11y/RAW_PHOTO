from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import BigInteger, Column, DateTime, Integer, String, Text, UniqueConstraint, create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker

from services.cache_utils import TTLCache

Base = declarative_base()

DEFAULT_DATABASE_URL = "mysql+pymysql://root:root@127.0.0.1:3306/raw_photo?charset=utf8mb4"
ONLINE_WINDOW_MINUTES = 5


def _database_url() -> str:
    return (
        os.getenv("IMAGE_LIBRARY_DATABASE_URL")
        or os.getenv("MYSQL_DATABASE_URL")
        or DEFAULT_DATABASE_URL
    )


def _clean(value: object, default: str = "") -> str:
    text = str(value if value is not None else default).strip()
    return text or default


def _int_or_none(value: object) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _positive_int(value: object, default: int = 1) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def _parse_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    text_value = _clean(value)
    if not text_value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text_value[:19], fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text_value.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def _format_datetime(value: object) -> str:
    parsed = _parse_datetime(value)
    return parsed.strftime("%Y-%m-%d %H:%M:%S") if parsed else ""


def _is_cancellation_text(value: object) -> bool:
    text_value = _clean(value).lower()
    if not text_value:
        return False
    return any(
        marker in text_value
        for marker in (
            "任务已中止",
            "任务已取消",
            "已中止",
            "已取消",
            "用户中止",
            "用户取消",
            "主动中止",
            "主动取消",
            "canceled",
            "cancelled",
            "aborted",
            "user cancelled",
            "user canceled",
            "user stopped",
            "stopped by user",
        )
    )


def _percentile(values: list[int], percentile: float) -> int:
    ordered = sorted(int(value) for value in values if value is not None)
    if not ordered:
        return 0
    clamped = max(0.0, min(100.0, float(percentile)))
    if len(ordered) == 1:
        return int(ordered[0])
    rank = (len(ordered) - 1) * (clamped / 100.0)
    lower = int(rank)
    upper = min(len(ordered) - 1, lower + 1)
    if lower == upper:
        return int(ordered[lower])
    weight = rank - lower
    return int(round(ordered[lower] * (1 - weight) + ordered[upper] * weight))


def _latency_summary(values: list[int]) -> dict[str, int | float]:
    normalized = [max(0, int(value)) for value in values if value is not None and int(value) >= 0]
    return {
        "sample_size": len(normalized),
        "average_ms": round(sum(normalized) / len(normalized), 1) if normalized else 0,
        "p95_ms": _percentile(normalized, 95),
        "max_ms": max(normalized) if normalized else 0,
    }


class GenerationTaskEventModel(Base):
    __tablename__ = "generation_task_events"
    __table_args__ = (
        UniqueConstraint("owner_id", "task_id", name="uniq_generation_task_owner_task"),
    )

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    task_id = Column(String(191), nullable=False)
    owner_id = Column(String(191), nullable=False, default="local-admin")
    status = Column(String(32), nullable=False)
    mode = Column(String(32), nullable=False, default="generate")
    model = Column(String(191), nullable=True)
    product_id = Column(BigInteger, nullable=True)
    template_id = Column(BigInteger, nullable=True)
    image_count = Column(Integer, nullable=False, default=1)
    duration_ms = Column(Integer, nullable=True)
    upload_duration_ms = Column(Integer, nullable=True)
    queue_duration_ms = Column(Integer, nullable=True)
    generation_duration_ms = Column(Integer, nullable=True)
    save_duration_ms = Column(Integer, nullable=True)
    error = Column(Text, nullable=True)
    failure_reported_at = Column(DateTime, nullable=True)
    task_created_at = Column(DateTime, nullable=True)
    task_updated_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)


class GenerationMonitoringService:
    def __init__(self, database_url: str | None = None):
        self.database_url = database_url or _database_url()
        self.engine = None
        self.Session = None
        self._init_error = ""
        self._summary_cache = TTLCache[str, dict[str, Any]](ttl_seconds=3.0, max_items=16)
        self._init_engine()

    def _init_engine(self) -> None:
        try:
            engine = create_engine(self.database_url, pool_pre_ping=True, pool_recycle=3600)
            Base.metadata.create_all(engine)
            self._ensure_indexes(engine)
            self.engine = engine
            self.Session = sessionmaker(bind=engine)
            self._init_error = ""
        except Exception as exc:
            self.engine = None
            self.Session = None
            self._init_error = str(exc)

    def _ensure_indexes(self, engine) -> None:
        if engine.dialect.name != "mysql":
            return
        database = engine.url.database
        if not database:
            return
        with engine.begin() as connection:
            columns = {
                str(row[0])
                for row in connection.execute(
                    text(
                        "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                        "WHERE TABLE_SCHEMA = :schema AND TABLE_NAME = 'generation_task_events'"
                    ),
                    {"schema": database},
                )
            }
            if "image_count" not in columns:
                connection.execute(text("ALTER TABLE generation_task_events ADD COLUMN image_count INT NOT NULL DEFAULT 1"))
            if "failure_reported_at" not in columns:
                connection.execute(text("ALTER TABLE generation_task_events ADD COLUMN failure_reported_at DATETIME NULL"))
            for name in (
                "upload_duration_ms",
                "queue_duration_ms",
                "generation_duration_ms",
                "save_duration_ms",
            ):
                if name not in columns:
                    connection.execute(text(f"ALTER TABLE generation_task_events ADD COLUMN {name} INT NULL"))
            for statement in (
                "CREATE INDEX idx_generation_events_owner_status ON generation_task_events (owner_id, status, task_updated_at)",
                "CREATE INDEX idx_generation_events_status_updated ON generation_task_events (status, task_updated_at)",
                "CREATE INDEX idx_generation_events_reported_failure ON generation_task_events (status, failure_reported_at, owner_id)",
                "CREATE INDEX idx_generation_events_owner_updated ON generation_task_events (owner_id, task_updated_at)",
            ):
                try:
                    connection.execute(text(statement))
                except Exception:
                    pass

    def _session(self):
        if self.Session is None:
            self._init_engine()
        if self.Session is None:
            raise RuntimeError(f"generation monitoring database unavailable: {self._init_error}")
        return self.Session()

    def record_task_event(self, task: dict[str, Any]) -> None:
        status = _clean(task.get("status"))
        if status == "error" and _is_cancellation_text(task.get("error")):
            status = "canceled"
        if status not in {"success", "error", "canceled"}:
            return
        task_id = _clean(task.get("id"))
        owner_id = _clean(task.get("owner_id")) or "anonymous"
        if not task_id:
            return

        session = self._session()
        try:
            row = (
                session.query(GenerationTaskEventModel)
                .filter(
                    GenerationTaskEventModel.owner_id == owner_id,
                    GenerationTaskEventModel.task_id == task_id,
                )
                .one_or_none()
            )
            previous = None if row is None else (
                row.status,
                row.mode,
                row.model,
                row.product_id,
                row.template_id,
                row.image_count,
                row.duration_ms,
                row.upload_duration_ms,
                row.queue_duration_ms,
                row.generation_duration_ms,
                row.save_duration_ms,
                row.error,
                row.failure_reported_at,
                row.task_created_at,
                row.task_updated_at,
            )
            if row is None:
                row = GenerationTaskEventModel(owner_id=owner_id, task_id=task_id)
                session.add(row)
            row.status = status
            row.mode = "edit" if task.get("mode") == "edit" else "generate"
            row.model = _clean(task.get("model")) or None
            row.product_id = _int_or_none(task.get("product_id"))
            row.template_id = _int_or_none(task.get("template_id"))
            row.image_count = _positive_int(task.get("image_count"), 1)
            row.duration_ms = task.get("duration_ms") if isinstance(task.get("duration_ms"), int) else None
            stage_timings = task.get("stage_timings_ms") if isinstance(task.get("stage_timings_ms"), dict) else {}
            row.upload_duration_ms = _int_or_none(stage_timings.get("upload")) or 0
            row.queue_duration_ms = _int_or_none(stage_timings.get("queue")) or 0
            row.generation_duration_ms = _int_or_none(stage_timings.get("generation")) or 0
            row.save_duration_ms = _int_or_none(stage_timings.get("save")) or 0
            row.error = _clean(task.get("error")) or None
            if status != "error":
                row.failure_reported_at = None
            row.task_created_at = _parse_datetime(task.get("created_at"))
            row.task_updated_at = _parse_datetime(task.get("updated_at"))
            current = (
                row.status,
                row.mode,
                row.model,
                row.product_id,
                row.template_id,
                row.image_count,
                row.duration_ms,
                row.upload_duration_ms,
                row.queue_duration_ms,
                row.generation_duration_ms,
                row.save_duration_ms,
                row.error,
                row.failure_reported_at,
                row.task_created_at,
                row.task_updated_at,
            )
            if previous is not None and previous == current:
                return
            row.updated_at = datetime.now()
            session.commit()
            self._summary_cache.clear()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def report_frontend_failure(
        self,
        *,
        identity: dict[str, object],
        task_id: str,
        error: str = "",
        image_count: int = 1,
        mode: str = "generate",
        model: str = "",
        product_id: int = 0,
        template_id: int = 0,
    ) -> dict[str, Any]:
        owner_id = _clean(identity.get("id")) or "anonymous"
        normalized_task_id = _clean(task_id)
        if not normalized_task_id:
            raise ValueError("task_id is required")
        is_cancellation_report = _is_cancellation_text(error)

        now = datetime.now()
        session = self._session()
        try:
            row = (
                session.query(GenerationTaskEventModel)
                .filter(
                    GenerationTaskEventModel.owner_id == owner_id,
                    GenerationTaskEventModel.task_id == normalized_task_id,
                )
                .one_or_none()
            )
            if row is not None and row.status == "canceled":
                return {"ok": True, "ignored": True, "reason": "canceled"}
            previous = None if row is None else (
                row.status,
                row.mode,
                row.model,
                row.product_id,
                row.template_id,
                row.image_count,
                row.error,
                row.failure_reported_at,
                row.task_created_at,
                row.task_updated_at,
            )
            if row is None:
                row = GenerationTaskEventModel(
                    owner_id=owner_id,
                    task_id=normalized_task_id,
                    task_created_at=now,
                )
                session.add(row)
            if is_cancellation_report:
                row.status = "canceled"
                row.mode = "edit" if mode == "edit" else "generate"
                row.model = _clean(model) or row.model
                row.product_id = _int_or_none(product_id) or row.product_id
                row.template_id = _int_or_none(template_id) or row.template_id
                row.image_count = _positive_int(image_count, 1)
                row.error = _clean(error) or row.error or "任务已中止"
                row.failure_reported_at = None
                row.task_updated_at = row.task_updated_at or now
                row.updated_at = now
                session.commit()
                self._summary_cache.clear()
                return {"ok": True, "ignored": True, "reason": "canceled"}
            row.status = "error"
            row.mode = "edit" if mode == "edit" else "generate"
            row.model = _clean(model) or row.model
            row.product_id = _int_or_none(product_id) or row.product_id
            row.template_id = _int_or_none(template_id) or row.template_id
            row.image_count = _positive_int(image_count, 1)
            row.error = _clean(error) or row.error
            row.failure_reported_at = row.failure_reported_at or now
            row.task_updated_at = row.task_updated_at or now
            current = (
                row.status,
                row.mode,
                row.model,
                row.product_id,
                row.template_id,
                row.image_count,
                row.error,
                row.failure_reported_at,
                row.task_created_at,
                row.task_updated_at,
            )
            if previous is not None and previous == current:
                return
            row.updated_at = now
            session.commit()
            self._summary_cache.clear()
            return {"ok": True}
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def sync_task_events(self, tasks: list[dict[str, Any]]) -> None:
        for task in tasks:
            try:
                self.record_task_event(task)
            except Exception:
                continue

    @staticmethod
    def _summary_cache_key(queue_snapshot: dict[str, Any] | None) -> str:
        try:
            return json.dumps(queue_snapshot or {}, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
        except Exception:
            return str(queue_snapshot or {})

    def summary(self, queue_snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
        cache_key = self._summary_cache_key(queue_snapshot)
        cached = self._summary_cache.get(cache_key)
        if cached is not None:
            return cached

        session = self._session()
        now = datetime.now()
        online_cutoff = now - timedelta(minutes=ONLINE_WINDOW_MINUTES)
        try:
            users = [
                dict(row)
                for row in session.execute(
                    text(
                        "SELECT id, username, name, role, enabled, last_login_at, created_at "
                        "FROM business_users ORDER BY created_at DESC"
                    )
                ).mappings()
            ]
            online_rows = [
                dict(row)
                for row in session.execute(
                    text(
                        "SELECT user_id, COUNT(*) AS active_sessions, MAX(COALESCE(last_used_at, created_at)) AS last_seen_at "
                        "FROM business_user_sessions "
                        "WHERE revoked_at IS NULL AND expires_at > :now "
                        "AND COALESCE(last_used_at, created_at) >= :online_cutoff "
                        "GROUP BY user_id"
                    ),
                    {"now": now, "online_cutoff": online_cutoff},
                ).mappings()
            ]
            success_rows = [
                dict(row)
                for row in session.execute(
                    text(
                        "SELECT owner_id, COUNT(*) AS success_count "
                        "FROM generated_images WHERE deleted_at IS NULL GROUP BY owner_id"
                    )
                ).mappings()
            ]
            failed_rows = [
                dict(row)
                for row in session.execute(
                    text(
                        "SELECT owner_id, SUM(image_count) AS failed_count "
                        "FROM generation_task_events "
                        "WHERE status = 'error' AND failure_reported_at IS NOT NULL "
                        "GROUP BY owner_id"
                    )
                ).mappings()
            ]
            duration_rows = [
                int(row.get("duration_ms") or 0)
                for row in session.execute(
                    text(
                        "SELECT duration_ms FROM generation_task_events "
                        "WHERE status IN ('success', 'error') AND duration_ms IS NOT NULL AND duration_ms > 0"
                    )
                ).mappings()
            ]
            stage_rows = [
                dict(row)
                for row in session.execute(
                    text(
                        "SELECT upload_duration_ms, queue_duration_ms, generation_duration_ms, save_duration_ms "
                        "FROM generation_task_events WHERE status IN ('success', 'error')"
                    )
                ).mappings()
            ]

            user_map: dict[str, dict[str, Any]] = {
                _clean(user.get("id")): user
                for user in users
                if _clean(user.get("id"))
            }
            online_map = {
                _clean(row.get("user_id")): {
                    "active_sessions": int(row.get("active_sessions") or 0),
                    "last_seen_at": row.get("last_seen_at"),
                }
                for row in online_rows
                if _clean(row.get("user_id"))
            }
            success_map = {
                _clean(row.get("owner_id")): int(row.get("success_count") or 0)
                for row in success_rows
                if _clean(row.get("owner_id"))
            }
            failed_map = {
                _clean(row.get("owner_id")): int(row.get("failed_count") or 0)
                for row in failed_rows
                if _clean(row.get("owner_id"))
            }

            queue_data = dict(queue_snapshot or {})
            owner_activity_rows = queue_data.get("owner_activity") if isinstance(queue_data.get("owner_activity"), list) else []
            owner_activity_map: dict[str, dict[str, int]] = {}
            for row in owner_activity_rows:
                if not isinstance(row, dict):
                    continue
                owner_id = _clean(row.get("owner_id"))
                if not owner_id:
                    continue
                queued_tasks = int(row.get("queued_tasks") or 0)
                running_tasks = int(row.get("running_tasks") or 0)
                owner_activity_map[owner_id] = {
                    "queued_tasks": queued_tasks,
                    "running_tasks": running_tasks,
                    "active_tasks": int(row.get("active_tasks") or queued_tasks + running_tasks),
                }

            owner_ids = set(user_map) | set(online_map) | set(success_map) | set(failed_map) | set(owner_activity_map)
            items = []
            for owner_id in owner_ids:
                user = user_map.get(owner_id) or {}
                online = owner_id in online_map
                success_count = success_map.get(owner_id, 0)
                failed_count = failed_map.get(owner_id, 0)
                activity = owner_activity_map.get(owner_id, {"queued_tasks": 0, "running_tasks": 0, "active_tasks": 0})
                items.append(
                    {
                        "user_id": owner_id,
                        "username": _clean(user.get("username")) or owner_id,
                        "name": _clean(user.get("name")) or _clean(user.get("username")) or owner_id,
                        "role": _clean(user.get("role"), "unknown"),
                        "enabled": _clean(user.get("enabled"), "1") == "1",
                        "online": online,
                        "active_sessions": online_map.get(owner_id, {}).get("active_sessions", 0),
                        "success_count": success_count,
                        "failed_count": failed_count,
                        "total_count": success_count + failed_count,
                        "queued_tasks": int(activity.get("queued_tasks") or 0),
                        "running_tasks": int(activity.get("running_tasks") or 0),
                        "active_tasks": int(activity.get("active_tasks") or 0),
                        "last_login_at": _format_datetime(user.get("last_login_at")),
                        "last_seen_at": _format_datetime(online_map.get(owner_id, {}).get("last_seen_at")),
                    }
                )
            items.sort(key=lambda item: (not item["online"], -item["total_count"], item["username"]))

            total_success = sum(success_map.values())
            total_failed = sum(failed_map.values())
            active_sessions = sum(item["active_sessions"] for item in online_map.values())
            result = {
                "online_users": len(online_map),
                "active_sessions": active_sessions,
                "total_success": total_success,
                "total_failed": total_failed,
                "total_users": len(users),
                "online_window_minutes": ONLINE_WINDOW_MINUTES,
                "task_queue": {
                    "enabled": bool(queue_data.get("enabled")),
                    "executor": str(queue_data.get("executor") or "inline"),
                    "queue_depth": int(queue_data.get("queue_depth") or 0),
                    "queued_tasks": int(queue_data.get("queued_tasks") or 0),
                    "running_tasks": int(queue_data.get("running_tasks") or 0),
                    "stale_running_tasks": int(queue_data.get("stale_running_tasks") or 0),
                    "active_slots": int(queue_data.get("active_slots") or 0),
                    "slot_limit": int(queue_data.get("slot_limit") or 0),
                    "active_workers": int(queue_data.get("active_workers") or 0),
                    "worker_concurrency": int(queue_data.get("worker_concurrency") or 0),
                    "local_concurrency_limit": int(queue_data.get("local_concurrency_limit") or 0),
                    "configured_total_concurrency": int(queue_data.get("configured_total_concurrency") or 0),
                    "total_concurrency": int(queue_data.get("total_concurrency") or 0),
                    "owner_concurrency": int(queue_data.get("owner_concurrency") or 0),
                    "effective_owner_concurrency": int(queue_data.get("effective_owner_concurrency") or 0),
                    "dynamic_owner_concurrency_enabled": bool(queue_data.get("dynamic_owner_concurrency_enabled")),
                    "dynamic_owner_concurrency_threshold": int(queue_data.get("dynamic_owner_concurrency_threshold") or 0),
                    "dynamic_owner_concurrency_max": int(queue_data.get("dynamic_owner_concurrency_max") or 0),
                    "active_owner_count": int(queue_data.get("active_owner_count") or 0),
                    "owner_pending_limit": int(queue_data.get("owner_pending_limit") or 0),
                    "stale_running_timeout_secs": int(queue_data.get("stale_running_timeout_secs") or 0),
                    "worker_heartbeat_secs": int(queue_data.get("worker_heartbeat_secs") or 0),
                },
                "task_latency": _latency_summary(duration_rows),
                "stage_latency": {
                    "upload": _latency_summary([int(row["upload_duration_ms"]) for row in stage_rows if row.get("upload_duration_ms") is not None]),
                    "queue": _latency_summary([int(row["queue_duration_ms"]) for row in stage_rows if row.get("queue_duration_ms") is not None]),
                    "generation": _latency_summary([int(row["generation_duration_ms"]) for row in stage_rows if row.get("generation_duration_ms") is not None]),
                    "save": _latency_summary([int(row["save_duration_ms"]) for row in stage_rows if row.get("save_duration_ms") is not None]),
                },
                "users": items,
            }
            return self._summary_cache.set(cache_key, result)
        finally:
            session.close()


generation_monitoring_service = GenerationMonitoringService()
