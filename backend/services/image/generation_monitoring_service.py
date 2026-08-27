from __future__ import annotations

import json
import math
import os
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import BigInteger, Column, DateTime, Float, Integer, String, Text, UniqueConstraint, create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker

from services.image.image_storage_service import image_storage_service
from services.platform.cache_utils import TTLCache

Base = declarative_base()

DEFAULT_DATABASE_URL = "mysql+pymysql://root:root@127.0.0.1:3306/raw_photo?charset=utf8mb4"
ONLINE_WINDOW_MINUTES = 5


def _database_url() -> str:
    return (
        os.getenv("GMKRAW_DATABASE_URL")
        or
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


def _nonnegative_int_or_none(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _float_or_none(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


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


def _reference_preview_item(item: object, *, index: int) -> dict[str, str] | None:
    if isinstance(item, str):
        preview_url = _clean(item)
        if not preview_url:
            return None
        return {
            "preview_url": preview_url,
            "filename": f"reference-{index}",
            "mime_type": "image/png",
            "role": "",
            "kind": "data" if preview_url.startswith("data:") else "url",
            "rel": "",
        }
    if not isinstance(item, dict):
        return None

    role = _clean(item.get("role"))
    filename = _clean(item.get("filename") or item.get("name") or f"reference-{index}")
    mime_type = _clean(item.get("mime_type") or item.get("mimeType"), "image/png")
    rel = _clean(item.get("rel") or item.get("storage_rel"))
    preview_url = _clean(item.get("preview_url") or item.get("url") or item.get("data_url") or item.get("dataUrl"))
    kind = "url"

    if item.get("__image_ref__") == "1" or rel:
        if rel:
            try:
                preview_url = image_storage_service._public_url(rel)
            except Exception:
                pass
        kind = "upload"
    elif item.get("__image_input__") == "1":
        raw_data = _clean(item.get("data"))
        if raw_data:
            preview_url = f"data:{mime_type};base64,{raw_data}"
            kind = "legacy"
    elif preview_url.startswith("data:"):
        kind = "data"

    if not preview_url:
        return None
    return {
        "preview_url": preview_url,
        "filename": filename,
        "mime_type": mime_type,
        "role": role,
        "kind": kind,
        "rel": rel,
    }


def _task_reference_images(task: dict[str, Any]) -> list[dict[str, str]]:
    payload = task.get("payload")
    if not isinstance(payload, dict):
        return []
    candidates: list[dict[str, str]] = []
    raw_images = payload.get("images")
    if isinstance(raw_images, list):
        for index, item in enumerate(raw_images, start=1):
            preview = _reference_preview_item(item, index=index)
            if preview:
                candidates.append(preview)
    raw_urls = payload.get("image_urls")
    if not candidates and isinstance(raw_urls, list):
        offset = len(candidates)
        for index, item in enumerate(raw_urls, start=1):
            preview = _reference_preview_item(item, index=offset + index)
            if preview:
                candidates.append(preview)
    deduped: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for item in candidates:
        preview_url = item.get("preview_url", "")
        if not preview_url or preview_url in seen_urls:
            continue
        seen_urls.add(preview_url)
        deduped.append(item)
    return deduped


def _reference_images_by_task(rows: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, str]]]:
    owner_task_ids: dict[str, set[str]] = {}
    for row in rows:
        owner_id = _clean(row.get("owner_id"))
        task_id = _clean(row.get("task_id"))
        if not owner_id or not task_id:
            continue
        owner_task_ids.setdefault(owner_id, set()).add(task_id)
    if not owner_task_ids:
        return {}

    try:
        from services.image.image_task_service import image_task_service
    except Exception:
        return {}
    store = getattr(image_task_service, "task_store", None)
    if store is None or not hasattr(store, "list_tasks"):
        return {}

    result: dict[tuple[str, str], list[dict[str, str]]] = {}
    for owner_id, task_ids in owner_task_ids.items():
        try:
            tasks = store.list_tasks(owner_id, sorted(task_ids))
        except Exception:
            continue
        for task in tasks:
            task_id = _clean(task.get("id"))
            if not task_id:
                continue
            result[(owner_id, task_id)] = _task_reference_images(task)
    return result


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
    cost = Column(Float, nullable=True)
    upstream_task_id = Column(String(191), nullable=True)
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
            if "cost" not in columns:
                connection.execute(text("ALTER TABLE generation_task_events ADD COLUMN cost DOUBLE NULL"))
            if "upstream_task_id" not in columns:
                connection.execute(text("ALTER TABLE generation_task_events ADD COLUMN upstream_task_id VARCHAR(191) NULL"))
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
                "CREATE INDEX idx_generation_events_upstream_task ON generation_task_events (upstream_task_id)",
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
                row.cost,
                row.upstream_task_id,
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
            if "cost" in task:
                row.cost = _float_or_none(task.get("cost"))
            if "upstream_task_id" in task:
                row.upstream_task_id = _clean(task.get("upstream_task_id")) or None
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
                row.cost,
                row.upstream_task_id,
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
        failure_report_id: str = "",
        error: str = "",
        image_count: int = 1,
        mode: str = "generate",
        model: str = "",
        product_id: int = 0,
        template_id: int = 0,
    ) -> dict[str, Any]:
        owner_id = _clean(identity.get("id")) or "anonymous"
        actual_task_id = _clean(task_id)
        normalized_task_id = _clean(failure_report_id) or actual_task_id
        if not actual_task_id:
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
    def _summary_cache_key(
        queue_snapshot: dict[str, Any] | None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> str:
        try:
            return json.dumps(
                {
                    "queue": queue_snapshot or {},
                    "start_at": _format_datetime(start_at),
                    "end_at": _format_datetime(end_at),
                },
                ensure_ascii=False,
                sort_keys=True,
                default=str,
                separators=(",", ":"),
            )
        except Exception:
            return f"{queue_snapshot or {}}|{start_at}|{end_at}"

    def summary(
        self,
        queue_snapshot: dict[str, Any] | None = None,
        *,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> dict[str, Any]:
        cache_key = self._summary_cache_key(queue_snapshot, start_at, end_at)
        cached = self._summary_cache.get(cache_key)
        if cached is not None:
            return cached

        session = self._session()
        now = datetime.now()
        online_cutoff = now - timedelta(minutes=ONLINE_WINDOW_MINUTES)
        range_params: dict[str, datetime] = {}
        generated_range_sql = ""
        event_range_sql = ""
        event_time_sql = "COALESCE(task_updated_at, updated_at, task_created_at, created_at)"
        if start_at is not None:
            range_params["start_at"] = start_at
            generated_range_sql += " AND created_at >= :start_at"
            event_range_sql += f" AND {event_time_sql} >= :start_at"
        if end_at is not None:
            range_params["end_at"] = end_at
            generated_range_sql += " AND created_at < :end_at"
            event_range_sql += f" AND {event_time_sql} < :end_at"
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
                        "SELECT owner_id, SUM(success_count) AS success_count "
                        "FROM ("
                        "  SELECT owner_id, COUNT(*) AS success_count "
                        "  FROM generated_images WHERE deleted_at IS NULL "
                        f"  {generated_range_sql} GROUP BY owner_id "
                        "  UNION ALL "
                        "  SELECT e.owner_id, SUM(COALESCE(NULLIF(e.image_count, 0), 1)) AS success_count "
                        "  FROM generation_task_events e "
                        "  WHERE e.status = 'success' "
                        f"    {event_range_sql} "
                        "    AND NOT EXISTS ("
                        "      SELECT 1 FROM generated_images g "
                        "      WHERE g.owner_id = e.owner_id AND g.task_id = e.task_id"
                        "    ) "
                        "  GROUP BY e.owner_id"
                        ") AS success_sources "
                        "GROUP BY owner_id"
                    ),
                    range_params,
                ).mappings()
            ]
            failed_rows = [
                dict(row)
                for row in session.execute(
                    text(
                        "SELECT owner_id, SUM(image_count) AS failed_count "
                        "FROM generation_task_events "
                        "WHERE status = 'error' AND failure_reported_at IS NOT NULL "
                        f"{event_range_sql} "
                        "GROUP BY owner_id"
                    ),
                    range_params,
                ).mappings()
            ]
            cost_rows = [
                dict(row)
                for row in session.execute(
                    text(
                        "SELECT owner_id, COUNT(cost) AS cost_count, COALESCE(SUM(cost), 0) AS cost_total "
                        "FROM generation_task_events "
                        "WHERE status = 'success' AND cost IS NOT NULL "
                        f"{event_range_sql} "
                        "GROUP BY owner_id"
                    ),
                    range_params,
                ).mappings()
            ]
            model_rows = [
                dict(row)
                for row in session.execute(
                    text(
                        "SELECT COALESCE(NULLIF(TRIM(model), ''), 'unknown') AS model, "
                        "COUNT(cost) AS cost_count, COALESCE(SUM(cost), 0) AS cost_total "
                        "FROM generation_task_events "
                        "WHERE status = 'success' AND cost IS NOT NULL "
                        f"{event_range_sql} "
                        "GROUP BY COALESCE(NULLIF(TRIM(model), ''), 'unknown')"
                    ),
                    range_params,
                ).mappings()
            ]
            duration_rows = [
                int(row.get("duration_ms") or 0)
                for row in session.execute(
                    text(
                        "SELECT duration_ms FROM generation_task_events "
                        "WHERE status IN ('success', 'error') AND duration_ms IS NOT NULL AND duration_ms > 0 "
                        f"{event_range_sql}"
                    ),
                    range_params,
                ).mappings()
            ]
            stage_rows = [
                dict(row)
                for row in session.execute(
                    text(
                        "SELECT upload_duration_ms, queue_duration_ms, generation_duration_ms, save_duration_ms "
                        "FROM generation_task_events WHERE status IN ('success', 'error') "
                        f"{event_range_sql}"
                    ),
                    range_params,
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
            cost_map: dict[str, dict[str, float | int]] = {}
            for row in cost_rows:
                owner_id = _clean(row.get("owner_id"))
                if not owner_id:
                    continue
                item = cost_map.setdefault(owner_id, {"cost_count": 0, "cost_total": 0.0})
                item["cost_count"] = int(item["cost_count"]) + int(row.get("cost_count") or 0)
                item["cost_total"] = float(item["cost_total"]) + float(row.get("cost_total") or 0)

            model_map: dict[str, dict[str, float | int]] = {}
            for row in model_rows:
                model = _clean(row.get("model"), "unknown")
                item = model_map.setdefault(model, {"cost_count": 0, "cost_total": 0.0})
                item["cost_count"] = int(item["cost_count"]) + int(row.get("cost_count") or 0)
                item["cost_total"] = float(item["cost_total"]) + float(row.get("cost_total") or 0)
            model_items = []
            for model, item in model_map.items():
                cost_count = int(item["cost_count"] or 0)
                model_item = {
                    "model": model,
                    "cost_count": cost_count,
                    "cost_total": round(float(item["cost_total"]), 6),
                    "cost_average": round(
                        float(item["cost_total"]) / int(cost_count or 1),
                        6,
                    ),
                }
                model_items.append(model_item)
            model_items.sort(key=lambda item: (-float(item.get("cost_total") or 0), item["model"]))

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

            owner_ids = set(user_map) | set(online_map) | set(success_map) | set(failed_map) | set(cost_map) | set(owner_activity_map)
            items = []
            for owner_id in owner_ids:
                user = user_map.get(owner_id) or {}
                online = owner_id in online_map
                success_count = success_map.get(owner_id, 0)
                failed_count = failed_map.get(owner_id, 0)
                cost_info = cost_map.get(owner_id, {})
                cost_count = int(cost_info.get("cost_count") or 0)
                cost_total = float(cost_info.get("cost_total") or 0)
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
                        "cost_total": round(cost_total, 6),
                        "cost_count": cost_count,
                        "cost_average": round(cost_total / cost_count, 6) if cost_count else 0,
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
            total_cost = sum(float(item.get("cost_total") or 0) for item in cost_map.values())
            total_cost_count = sum(int(item.get("cost_count") or 0) for item in cost_map.values())
            active_sessions = sum(item["active_sessions"] for item in online_map.values())
            result = {
                "online_users": len(online_map),
                "active_sessions": active_sessions,
                "total_success": total_success,
                "total_failed": total_failed,
                "total_cost": round(total_cost, 6),
                "cost_count": total_cost_count,
                "models": model_items,
                "total_users": len(users),
                "online_window_minutes": ONLINE_WINDOW_MINUTES,
                "range": {
                    "start_at": _format_datetime(start_at),
                    "end_at": _format_datetime(end_at),
                    "end_exclusive": True,
                },
                "task_queue": {
                    "enabled": bool(queue_data.get("enabled")),
                    "executor": str(queue_data.get("executor") or "inline"),
                    "queue_depth": int(queue_data.get("queue_depth") or 0),
                    "queue_depths": dict(queue_data.get("queue_depths") or {}),
                    "queued_tasks": int(queue_data.get("queued_tasks") or 0),
                    "running_tasks": int(queue_data.get("running_tasks") or 0),
                    "stale_running_tasks": int(queue_data.get("stale_running_tasks") or 0),
                    "active_slots": int(queue_data.get("active_slots") or 0),
                    "slot_limit": int(queue_data.get("slot_limit") or 0),
                    "adaptive_concurrency": dict(queue_data.get("adaptive_concurrency") or {}),
                    "active_workers": int(queue_data.get("active_workers") or 0),
                    "worker_concurrency": int(queue_data.get("worker_concurrency") or 0),
                    "local_concurrency_limit": int(queue_data.get("local_concurrency_limit") or 0),
                    "postprocess_concurrency": int(queue_data.get("postprocess_concurrency") or 0),
                    "async_postprocess_enabled": bool(queue_data.get("async_postprocess_enabled")),
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

    def task_details(
        self,
        *,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        owner_id: str = "",
        status: str = "all",
        limit: int = 100,
        include_references: bool = False,
    ) -> dict[str, Any]:
        normalized_status = status if status in {"success", "error"} else "all"
        normalized_owner_id = _clean(owner_id)
        safe_limit = max(1, min(500, int(limit or 100)))
        params: dict[str, Any] = {"limit": safe_limit}

        def filters(alias: str, timestamp_sql: str) -> str:
            clauses = []
            if start_at is not None:
                params["start_at"] = start_at
                clauses.append(f"{timestamp_sql} >= :start_at")
            if end_at is not None:
                params["end_at"] = end_at
                clauses.append(f"{timestamp_sql} < :end_at")
            if normalized_owner_id:
                params["owner_id"] = normalized_owner_id
                clauses.append(f"{alias}.owner_id = :owner_id")
            return "".join(f" AND {clause}" for clause in clauses)

        sources: list[str] = []
        if normalized_status in {"all", "success"}:
            generated_filters = filters("g", "g.created_at")
            sources.append(
                "SELECT g.id AS source_id, 'image' AS source_type, g.task_id, g.owner_id, 'success' AS status, "
                "1 AS image_count, g.mode, g.model, g.duration_ms, e.cost, "
                "e.upstream_task_id, NULL AS error, g.created_at AS completed_at, g.image_url "
                "FROM generated_images g "
                "LEFT JOIN generation_task_events e ON e.owner_id = g.owner_id AND e.task_id = g.task_id "
                "WHERE g.deleted_at IS NULL"
                f"{generated_filters}"
            )
            event_success_filters = filters(
                "e",
                "COALESCE(e.task_updated_at, e.updated_at, e.task_created_at, e.created_at)",
            )
            sources.append(
                "SELECT e.id AS source_id, 'event' AS source_type, e.task_id, e.owner_id, 'success' AS status, "
                "COALESCE(NULLIF(e.image_count, 0), 1) AS image_count, e.mode, e.model, e.duration_ms, "
                "e.cost, e.upstream_task_id, NULL AS error, "
                "COALESCE(e.task_updated_at, e.updated_at, e.task_created_at, e.created_at) AS completed_at, "
                "NULL AS image_url FROM generation_task_events e WHERE e.status = 'success'"
                f"{event_success_filters}"
                " AND NOT EXISTS ("
                "SELECT 1 FROM generated_images g WHERE g.owner_id = e.owner_id AND g.task_id = e.task_id"
                ")"
            )
        if normalized_status in {"all", "error"}:
            event_error_filters = filters(
                "e",
                "COALESCE(e.task_updated_at, e.updated_at, e.task_created_at, e.created_at)",
            )
            sources.append(
                "SELECT e.id AS source_id, 'event' AS source_type, e.task_id, e.owner_id, 'error' AS status, "
                "COALESCE(NULLIF(e.image_count, 0), 1) AS image_count, e.mode, e.model, e.duration_ms, "
                "e.cost, e.upstream_task_id, e.error, "
                "COALESCE(e.task_updated_at, e.updated_at, e.task_created_at, e.created_at) AS completed_at, "
                "NULL AS image_url FROM generation_task_events e "
                "WHERE e.status = 'error' AND e.failure_reported_at IS NOT NULL"
                f"{event_error_filters}"
            )
        union_sql = " UNION ALL ".join(sources)
        cost_filters = filters("e", "COALESCE(e.task_updated_at, e.updated_at, e.task_created_at, e.created_at)")
        if normalized_status == "success":
            cost_status_sql = "AND e.status = 'success' "
        elif normalized_status == "error":
            cost_status_sql = "AND e.status = 'error' AND e.failure_reported_at IS NOT NULL "
        else:
            cost_status_sql = "AND e.status IN ('success', 'error') "
        session = self._session()
        try:
            totals = dict(
                session.execute(
                    text(
                        "SELECT COUNT(*) AS record_count, COALESCE(SUM(image_count), 0) AS image_count "
                        f"FROM ({union_sql}) AS monitoring_details"
                    ),
                    params,
                ).mappings().one()
            )
            image_cost_totals = dict(
                session.execute(
                    text(
                        "SELECT COUNT(e.cost) AS cost_count, COALESCE(SUM(e.cost), 0) AS cost_total "
                        "FROM generation_task_events e "
                        f"WHERE e.cost IS NOT NULL {cost_status_sql}{cost_filters}"
                    ),
                    params,
                ).mappings().one()
            )
            rows = [
                dict(row)
                for row in session.execute(
                    text(
                        "SELECT source_id, source_type, task_id, owner_id, status, image_count, mode, model, duration_ms, "
                        "cost, upstream_task_id, error, completed_at, image_url "
                        f"FROM ({union_sql}) AS monitoring_details "
                        "ORDER BY completed_at DESC LIMIT :limit"
                    ),
                    params,
                ).mappings()
            ]
            reference_images_by_task = _reference_images_by_task(rows) if include_references else {}
            items = [
                {
                    "row_key": f"{_clean(row.get('source_type'))}:{int(row.get('source_id') or 0)}",
                    "source_type": _clean(row.get("source_type")),
                    "task_id": _clean(row.get("task_id")),
                    "owner_id": _clean(row.get("owner_id")),
                    "status": _clean(row.get("status")),
                    "image_count": int(row.get("image_count") or 0),
                    "mode": _clean(row.get("mode")),
                    "model": _clean(row.get("model")),
                    "duration_ms": int(row.get("duration_ms") or 0),
                    "cost": _float_or_none(row.get("cost")),
                    "upstream_task_id": _clean(row.get("upstream_task_id")),
                    "error": _clean(row.get("error")),
                    "completed_at": _format_datetime(row.get("completed_at")),
                    "image_url": _clean(row.get("image_url")),
                    **(
                        {
                            "reference_images": reference_images_by_task.get(
                                (_clean(row.get("owner_id")), _clean(row.get("task_id"))),
                                [],
                            ),
                        }
                        if include_references
                        else {}
                    ),
                }
                for row in rows
            ]
            total_cost_count = int(image_cost_totals.get("cost_count") or 0)
            total_cost_value = float(image_cost_totals.get("cost_total") or 0)
            return {
                "items": items,
                "record_count": int(totals.get("record_count") or 0),
                "image_count": int(totals.get("image_count") or 0),
                "cost_total": round(total_cost_value, 6),
                "cost_count": total_cost_count,
                "limit": safe_limit,
                "truncated": int(totals.get("record_count") or 0) > len(items),
                "range": {
                    "start_at": _format_datetime(start_at),
                    "end_at": _format_datetime(end_at),
                    "end_exclusive": True,
                },
                "owner_id": normalized_owner_id,
                "status": normalized_status,
            }
        finally:
            session.close()


generation_monitoring_service = GenerationMonitoringService()
