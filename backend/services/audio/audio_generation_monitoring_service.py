from __future__ import annotations

import os
from copy import deepcopy
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from services.platform.cache_utils import TTLCache
from services.platform.enterprise_schema import resolve_enterprise_database_url
from services.audio.audio_generation_records import (
    ACTIVE_STATUSES, TERMINAL_STATUSES, active_record_counts, aggregate_records,
    list_records, parse_datetime,
)


ONLINE_WINDOW_MINUTES = 5


def _clean(value: object, default: str = "") -> str:
    return str(value if value is not None else default).strip() or default


def _format_datetime(value: object) -> str:
    parsed = parse_datetime(value)
    return parsed.strftime("%Y-%m-%d %H:%M:%S") if parsed else ""


def _positive_int(value: object) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return 0


def _money(value: Decimal | None) -> float:
    return float(round(value or Decimal(0), 6))


def _cost_summary(count: int, amount: Decimal | None) -> dict[str, Any]:
    return {
        "cost_count": count, "cost_total": _money(amount),
        "cost_average": _money((amount or Decimal(0)) / count) if count else 0,
    }


def _range(start_at, end_at) -> dict[str, Any]:
    return {"start_at": _format_datetime(start_at), "end_at": _format_datetime(end_at), "end_exclusive": True}


class AudioGenerationMonitoringService:
    """Read-only monitoring over the durable ledger, never deletable task JSON."""

    def __init__(self, *, task_service=None, database_url: str | None = None) -> None:
        self.task_service = task_service
        task_store = getattr(task_service, "task_store", None)
        self.database_url = database_url or _clean(getattr(task_store, "database_url", "")) or self._resolve_database_url()
        self.engine = None
        self.Session = None
        self._init_error = ""
        self._summary_cache = TTLCache[str, dict[str, Any]](ttl_seconds=10.0, max_items=16)
        self._init_engine()

    @staticmethod
    def _resolve_database_url() -> str:
        try:
            return resolve_enterprise_database_url()
        except Exception:
            return os.getenv("IMAGE_LIBRARY_DATABASE_URL") or os.getenv("MYSQL_DATABASE_URL") or "sqlite:///data/audio_generation_tasks.db"

    def _init_engine(self) -> None:
        try:
            self.engine = create_engine(self.database_url, pool_pre_ping=True, pool_recycle=3600)
            self.Session = sessionmaker(bind=self.engine)
            self._init_error = ""
        except Exception as exc:
            self.engine = None
            self.Session = None
            self._init_error = str(exc)

    def _session(self):
        if self.Session is None:
            self._init_engine()
        if self.Session is None:
            raise RuntimeError(f"audio monitoring database unavailable: {self._init_error}")
        return self.Session()

    def _users_and_online(self) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
        now = datetime.now()
        online_cutoff = now - timedelta(minutes=ONLINE_WINDOW_MINUTES)
        try:
            session = self._session()
        except Exception:
            return [], {}
        try:
            users = [dict(row) for row in session.execute(text(
                "SELECT id, username, name, role, enabled, last_login_at, created_at "
                "FROM business_users ORDER BY created_at DESC"
            )).mappings()]
            online_rows = [dict(row) for row in session.execute(text(
                "SELECT user_id, COUNT(*) AS active_sessions, "
                "MAX(COALESCE(last_used_at, created_at)) AS last_seen_at "
                "FROM business_user_sessions "
                "WHERE revoked_at IS NULL AND expires_at > :now "
                "AND COALESCE(last_used_at, created_at) >= :online_cutoff GROUP BY user_id"
            ), {"now": now, "online_cutoff": online_cutoff}).mappings()]
        except Exception:
            # Missing account tables must not hide persisted audio accounting.
            users, online_rows = [], []
        finally:
            session.close()
        online = {
            _clean(row.get("user_id")): {
                "active_sessions": _positive_int(row.get("active_sessions")), "last_seen_at": row.get("last_seen_at"),
            } for row in online_rows if _clean(row.get("user_id"))
        }
        return users, online

    @staticmethod
    def _queue_summary(activity: list[dict[str, Any]], queue_snapshot: dict[str, Any] | None) -> dict[str, Any]:
        queue = dict(queue_snapshot or {})
        owner_activity = {row["owner_id"]: {
            name: _positive_int(row.get(name)) for name in ("queued_tasks", "running_tasks", "active_tasks")
        } for row in activity}
        queued_from_records = sum(item["queued_tasks"] for item in owner_activity.values())
        running_from_records = sum(item["running_tasks"] for item in owner_activity.values())
        snapshot_activity = queue.get("owner_activity")
        if isinstance(snapshot_activity, list):
            owner_activity = {
                _clean(row.get("owner_id"), "anonymous"): {
                    name: _positive_int(row.get(name)) for name in ("queued_tasks", "running_tasks", "active_tasks")
                } for row in snapshot_activity if isinstance(row, dict)
            }
        queue_enabled = bool(queue.get("queue_enabled", queue.get("enabled", False)))
        worker_concurrency = _positive_int(queue.get("worker_concurrency"))
        queued_tasks = _positive_int(queue.get("queued_tasks", queued_from_records))
        running_tasks = _positive_int(queue.get("running_tasks", running_from_records))
        owner_concurrency = max(1, _positive_int(queue.get("owner_concurrency")) or 1)
        normalized_activity = [{"owner_id": owner_id, **item} for owner_id, item in owner_activity.items() if item["active_tasks"]]
        normalized_activity.sort(key=lambda item: (-item["active_tasks"], item["owner_id"]))
        total_concurrency = (
            _positive_int(queue.get("total_concurrency")) or _positive_int(queue.get("configured_total_concurrency"))
            or _positive_int(queue.get("slot_limit")) or worker_concurrency or max(running_tasks, 1)
        )
        return {
            "enabled": queue_enabled, "executor": "redis" if queue_enabled else "inline",
            "queue_depth": _positive_int(queue.get("queue_depth", queued_tasks)),
            "queue_depths": dict(queue.get("queue_depths") or {}),
            "queued_tasks": queued_tasks, "running_tasks": running_tasks,
            "stale_running_tasks": _positive_int(queue.get("stale_running_tasks")),
            "active_slots": _positive_int(queue.get("active_slots", running_tasks)),
            "slot_limit": _positive_int(queue.get("slot_limit")) or total_concurrency,
            "adaptive_concurrency": {}, "active_workers": _positive_int(queue.get("active_workers")),
            "worker_concurrency": worker_concurrency, "local_concurrency_limit": worker_concurrency or total_concurrency,
            "postprocess_concurrency": 0, "async_postprocess_enabled": False,
            "configured_total_concurrency": _positive_int(queue.get("configured_total_concurrency")) or total_concurrency,
            "total_concurrency": total_concurrency, "owner_concurrency": owner_concurrency,
            "effective_owner_concurrency": owner_concurrency, "dynamic_owner_concurrency_enabled": False,
            "dynamic_owner_concurrency_threshold": 0, "dynamic_owner_concurrency_max": 0,
            "active_owner_count": len(normalized_activity),
            "owner_pending_limit": max(1, _positive_int(queue.get("owner_pending_limit")) or 1),
            "stale_running_timeout_secs": _positive_int(queue.get("stale_running_timeout_secs")),
            "worker_heartbeat_secs": _positive_int(queue.get("worker_heartbeat_secs")),
            "owner_activity": normalized_activity,
        }

    def _merge_live_snapshot(
        self,
        historical: dict[str, Any],
        queue_snapshot: dict[str, Any] | None,
    ) -> dict[str, Any]:
        result = deepcopy(historical)
        record_activity = result.pop("_record_activity", [])
        queue = self._queue_summary(record_activity, queue_snapshot)
        activity_by_owner = {
            _clean(item.get("owner_id")): item
            for item in queue.get("owner_activity") or []
            if isinstance(item, dict) and _clean(item.get("owner_id"))
        }
        users = [dict(item) for item in result.get("users") or [] if isinstance(item, dict)]
        users_by_id = {_clean(item.get("user_id")): item for item in users if _clean(item.get("user_id"))}
        for owner_id, activity in activity_by_owner.items():
            user = users_by_id.get(owner_id)
            if user is None:
                user = {
                    "user_id": owner_id, "username": owner_id, "name": owner_id,
                    "role": "unknown", "enabled": True, "online": False, "active_sessions": 0,
                    "success_count": 0, "failed_count": 0, "canceled_count": 0,
                    "reconciliation_required_count": 0, "total_count": 0,
                    **_cost_summary(0, None), "last_login_at": "", "last_seen_at": "",
                }
                users.append(user)
                users_by_id[owner_id] = user
            user.update({
                "queued_tasks": _positive_int(activity.get("queued_tasks")),
                "running_tasks": _positive_int(activity.get("running_tasks")),
                "active_tasks": _positive_int(activity.get("active_tasks")),
            })
        for owner_id, user in users_by_id.items():
            if owner_id not in activity_by_owner:
                user.update({"queued_tasks": 0, "running_tasks": 0, "active_tasks": 0})
        users.sort(key=lambda item: (not item.get("online"), -int(item.get("total_count") or 0), _clean(item.get("username"))))
        result["users"] = users
        result["task_queue"] = queue
        result["audio_generation_queue"] = deepcopy(queue_snapshot or {})
        return result

    def summary(self, queue_snapshot: dict[str, Any] | None = None, *, start_at=None, end_at=None) -> dict[str, Any]:
        cache_key = f"{start_at!s}|{end_at!s}"
        cached = self._summary_cache.get(cache_key)
        if cached is not None:
            return self._merge_live_snapshot(cached, queue_snapshot)
        with self._session() as session:
            aggregated = aggregate_records(session, start_at=start_at, end_at=end_at)
            record_activity = active_record_counts(session)
        queue = self._queue_summary(record_activity, None)
        users, online = self._users_and_online()
        owner_totals = {row["owner_id"]: row for row in aggregated["owners"]}
        model_items = [{"model": row["model"], **_cost_summary(row["cost_count"], row["cost_total"])} for row in aggregated["models"]]
        model_items.sort(key=lambda item: (-item["cost_total"], item["model"]))
        user_map = {_clean(user.get("id")): user for user in users if _clean(user.get("id"))}
        queue_activity = {item["owner_id"]: item for item in queue["owner_activity"]}
        owner_ids = set(user_map) | set(online) | set(owner_totals) | set(queue_activity)
        user_items = []
        for owner_id in owner_ids:
            totals = owner_totals.get(owner_id) or {}
            user = user_map.get(owner_id) or totals
            user_activity = queue_activity.get(owner_id) or {}
            success, failed, canceled = (int(totals.get(key) or 0) for key in ("success_count", "failed_count", "canceled_count"))
            user_items.append({
                "user_id": owner_id, "username": _clean(user.get("username"), owner_id),
                "name": _clean(user.get("name"), _clean(user.get("username"), owner_id)),
                "role": _clean(user.get("role"), "unknown"), "enabled": _clean(user.get("enabled"), "1") in {"1", "true", "True"},
                "online": owner_id in online, "active_sessions": int((online.get(owner_id) or {}).get("active_sessions") or 0),
                "success_count": success, "failed_count": failed, "canceled_count": canceled,
                "reconciliation_required_count": int(totals.get("reconciliation_required_count") or 0),
                "total_count": success + failed + canceled,
                **_cost_summary(int(totals.get("cost_count") or 0), totals.get("cost_total")),
                "queued_tasks": _positive_int(user_activity.get("queued_tasks")),
                "running_tasks": _positive_int(user_activity.get("running_tasks")), "active_tasks": _positive_int(user_activity.get("active_tasks")),
                "last_login_at": _format_datetime(user.get("last_login_at")),
                "last_seen_at": _format_datetime((online.get(owner_id) or {}).get("last_seen_at")),
            })
        user_items.sort(key=lambda item: (not item["online"], -item["total_count"], item["username"]))
        total_cost = sum((row["cost_total"] or Decimal(0) for row in aggregated["owners"]), Decimal(0))
        latency = aggregated["latency"]
        empty_latency = {"sample_size": 0, "average_ms": 0, "p95_ms": 0, "max_ms": 0}
        result = {
            "source": "audio", "online_users": len(online),
            "active_sessions": sum(int(item.get("active_sessions") or 0) for item in online.values()),
            "total_success": sum(row["success_count"] for row in aggregated["owners"]),
            "total_failed": sum(row["failed_count"] for row in aggregated["owners"]),
            "total_canceled": sum(row["canceled_count"] for row in aggregated["owners"]),
            "reconciliation_required_count": sum(
                int(row.get("reconciliation_required_count") or 0) for row in aggregated["owners"]
            ),
            "total_cost": _money(total_cost), "cost_count": sum(row["cost_count"] for row in aggregated["owners"]),
            "models": model_items, "total_users": len(users), "online_window_minutes": ONLINE_WINDOW_MINUTES,
            "range": _range(start_at, end_at), "task_queue": queue, "audio_generation_queue": {},
            "task_latency": latency,
            "stage_latency": {"upload": dict(empty_latency), "queue": dict(empty_latency), "generation": dict(latency), "save": dict(empty_latency)},
            "users": user_items,
            "_record_activity": deepcopy(record_activity),
        }
        self._summary_cache.set(cache_key, deepcopy(result))
        return self._merge_live_snapshot(result, queue_snapshot)

    def task_details(
        self, *, start_at=None, end_at=None, owner_id: str = "", status: str = "all",
        limit: int = 100, offset: int = 0, query_text: str = "", cost_only: bool = False,
        include_references: bool = False, cursor: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        normalized_status = status if status in (*TERMINAL_STATUSES, *ACTIVE_STATUSES) else "all"
        normalized_owner_id = _clean(owner_id)
        with self._session() as session:
            page = list_records(session, start_at=start_at, end_at=end_at, owner_id=normalized_owner_id,
                                status=normalized_status, limit=limit, offset=offset,
                                query_text=_clean(query_text).lower(), cost_only=cost_only, cursor=cursor)
        items = [{
            "row_key": f"audio:{row['task_key']}", "source_type": "audio", "task_id": row["task_id"],
            "owner_id": row["owner_id"], "status": row["status"], "image_count": 1, "media_count": 1,
            "mode": row["mode"], "model": row["model"], "duration_ms": row["duration_ms"],
            "voice_id": row["voice_id"], "output_format": row["output_format"],
            "cost": _money(row["cost"]) if row["cost"] is not None else None,
            "upstream_task_id": row["upstream_task_id"], "error": row["error"],
            "completed_at": _format_datetime(row["completed_at"]), "image_url": "",
            "audio_url": row["audio_url"],
            "history_deleted": row["history_deleted"],
            "reconciliation_required": row["reconciliation_required"],
        } for row in page["items"]]
        return {
            **page, "source": "audio", "items": items,
            "image_count": page["record_count"], "media_count": page["record_count"],
            "cost_total": _money(page["cost_total"]), "range": _range(start_at, end_at),
            "owner_id": normalized_owner_id, "status": normalized_status,
            "query": _clean(query_text).lower(), "cost_only": cost_only,
        }


audio_generation_monitoring_service = AudioGenerationMonitoringService()
