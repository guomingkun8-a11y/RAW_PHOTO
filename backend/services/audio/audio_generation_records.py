from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Column, DateTime, Index, Numeric, String, Text,
    and_, case, column, func, or_, select, table,
)
from sqlalchemy.dialects.mysql import DATETIME, insert as mysql_insert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, declarative_base, defer


RecordsBase = declarative_base()
TERMINAL_STATUSES = ("success", "error", "canceled")
ACTIVE_STATUSES = ("queued", "running")
TIMESTAMP = DateTime().with_variant(DATETIME(fsp=6), "mysql")
COST_QUANTUM = Decimal("0.000000000001")
COST_LIMIT = Decimal("1000000000000000000")


class AudioGenerationRecord(RecordsBase):
    """Latest accounting snapshot; deliberately has no FK to deletable history."""

    __tablename__ = "audio_generation_records"
    __table_args__ = (
        CheckConstraint("cost_amount IS NULL OR cost_amount >= 0", name="ck_audio_record_cost_nonnegative"),
        Index("idx_audio_record_event_key", "event_at", "task_key"),
        Index("idx_audio_record_owner_event", "owner_id", "event_at"),
        Index("idx_audio_record_status_event", "status", "event_at"),
        Index("idx_audio_record_reconciliation_event", "reconciliation_required", "event_at"),
        Index("idx_audio_record_upstream", "upstream_task_id"),
    )

    task_key = Column(String(383), primary_key=True)
    task_id = Column(String(191), nullable=False)
    owner_id = Column(String(191), nullable=False)
    owner_username = Column(String(191), nullable=False, default="")
    owner_name = Column(String(191), nullable=False, default="")
    status = Column(String(32), nullable=False)
    mode = Column(String(32), nullable=False, default="text_to_speech")
    model = Column(String(191), nullable=False, default="unknown")
    voice_id = Column(String(383), nullable=False, default="")
    output_format = Column(String(32), nullable=False, default="")
    upstream_task_id = Column(String(191), nullable=False, default="")
    credential_id = Column(String(191), nullable=False, default="")
    history_deleted = Column(Boolean, nullable=False, default=False)
    reconciliation_required = Column(Boolean, nullable=False, default=False)
    cost_amount = Column(Numeric(30, 12), nullable=True)
    duration_ms = Column(BigInteger, nullable=False, default=0)
    error = Column(Text, nullable=False, default="")
    audio_url = Column(Text, nullable=False, default="")
    created_at = Column(TIMESTAMP, nullable=False)
    updated_at = Column(TIMESTAMP, nullable=True)
    completed_at = Column(TIMESTAMP, nullable=True)
    event_at = Column(TIMESTAMP, nullable=False)


def _clean(value: object, default: str = "", limit: int = 191) -> str:
    return (str(value if value is not None else "").strip() or default)[:limit]


def parse_datetime(value: object) -> datetime | None:
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone().replace(tzinfo=None) if parsed.tzinfo else parsed
    except (TypeError, ValueError, OverflowError):
        return None


def parse_cost(value: object) -> Decimal | None:
    """Reject missing/invalid money rather than replacing a previously known fee."""
    if value is None or isinstance(value, bool):
        return None
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0 or amount >= COST_LIMIT:
            return None
        with localcontext() as ctx:
            ctx.prec = 42
            amount = amount.quantize(COST_QUANTUM)
        return amount if amount < COST_LIMIT else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def _snapshot_time(task: dict[str, Any]) -> datetime | None:
    try:
        if task.get("updated_ts") is not None and not isinstance(task["updated_ts"], bool):
            return datetime.fromtimestamp(float(task["updated_ts"]))
    except (TypeError, ValueError, OverflowError, OSError):
        pass
    return parse_datetime(task.get("updated_at")) or parse_datetime(task.get("created_at"))


def record_exists(session: Session, key: str) -> bool:
    return bool(session.scalar(select(select(AudioGenerationRecord.task_key).where(
        AudioGenerationRecord.task_key == key,
    ).exists())))


def sync_record(session: Session, key: str, task: dict[str, Any]) -> AudioGenerationRecord:
    """Upsert within the caller's transaction; never commit or delete a record.

    The caller supplies a persisted, locked task snapshot. Upstream credential IDs
    are opaque identifiers only; API keys and provider payloads are never copied.
    """
    if not key or len(key) > 383:
        raise ValueError("invalid audio task key")
    owner_id = _clean(task.get("owner_id"), "anonymous")
    task_id = _clean(task.get("id"))
    if not task_id:
        raise ValueError("audio task id is required")
    source_time = _snapshot_time(task)
    created_at = parse_datetime(task.get("created_at")) or source_time or datetime.now()
    status = _clean(task.get("status"), "queued", 32)
    status = {"failed": "error", "cancelled": "canceled"}.get(status, status)
    initial = {
        "task_key": key, "task_id": task_id, "owner_id": owner_id,
        "status": status, "created_at": created_at, "event_at": created_at,
    }
    # A database upsert arbitrates first-writer races without rolling back the
    # caller's task transaction. The subsequent lock serializes snapshot updates.
    dialect = session.get_bind().dialect.name
    if dialect == "mysql":
        stmt = mysql_insert(AudioGenerationRecord).values(**initial)
        stmt = stmt.on_duplicate_key_update(task_key=stmt.inserted.task_key)
    elif dialect in {"sqlite", "postgresql"}:
        insert = sqlite_insert if dialect == "sqlite" else pg_insert
        stmt = insert(AudioGenerationRecord).values(**initial).on_conflict_do_nothing(index_elements=["task_key"])
    else:
        raise RuntimeError(f"unsupported audio ledger database: {dialect}")
    session.execute(stmt)
    row = session.scalars(select(AudioGenerationRecord).where(
        AudioGenerationRecord.task_key == key,
    ).with_for_update().execution_options(populate_existing=True)).one()
    if row.owner_id != owner_id or row.task_id != task_id:
        raise ValueError("audio task key cannot be reused for another identity")
    if task.get("history_deleted") is True:
        row.history_deleted = True
    if row.updated_at is not None and (source_time is None or source_time < row.updated_at):
        session.flush()
        return row

    identity = task.get("identity") if isinstance(task.get("identity"), dict) else {}
    for field, value in {
        "owner_username": identity.get("username"), "owner_name": identity.get("name"),
        "mode": task.get("mode"), "model": task.get("model"),
        "voice_id": task.get("voice_id"), "output_format": task.get("output_format"),
        "upstream_task_id": task.get("upstream_task_id"),
        "credential_id": task.get("upstream_credential_id") or task.get("credential_id"),
    }.items():
        cleaned = _clean(value, limit=32 if field in {"mode", "output_format"} else 383 if field == "voice_id" else 191)
        if cleaned:
            setattr(row, field, cleaned)
    amount = parse_cost(task.get("cost"))
    if amount is not None:
        row.cost_amount = amount
    for field in ("error", "audio_url"):
        if field in task and task[field] is not None:
            setattr(row, field, _clean(task[field], limit=12000))
    try:
        if task.get("duration_ms") is not None:
            row.duration_ms = max(0, min(2**63 - 1, int(task["duration_ms"])))
    except (ValueError, TypeError, OverflowError):
        pass
    row.status = status
    row.reconciliation_required = bool(task.get("reconciliation_required"))
    row.updated_at = source_time or row.updated_at
    if status in TERMINAL_STATUSES and row.completed_at is None:
        row.completed_at = parse_datetime(task.get("completed_at")) or source_time or created_at
        row.event_at = row.completed_at
    session.flush()
    return row


def _filters(
    *,
    owner_id: str = "",
    status: str = "all",
    start_at=None,
    end_at=None,
    query_text: str = "",
    cost_only: bool = False,
) -> list:
    record = AudioGenerationRecord
    filters = []
    if owner_id:
        filters.append(record.owner_id == owner_id)
    if status != "all":
        filters.append(record.status == status)
    else:
        filters.append(or_(record.status.in_(TERMINAL_STATUSES), record.cost_amount.is_not(None)))
    if start_at is not None:
        filters.append(record.event_at >= parse_datetime(start_at))
    if end_at is not None:
        filters.append(record.event_at < parse_datetime(end_at))
    if query_text:
        pattern = f"%{query_text.lower()}%"
        filters.append(
            or_(
                func.lower(record.task_id).like(pattern),
                func.lower(record.task_key).like(pattern),
                func.lower(record.owner_id).like(pattern),
                func.lower(record.model).like(pattern),
                func.lower(record.mode).like(pattern),
                func.lower(record.upstream_task_id).like(pattern),
            )
        )
    if cost_only:
        filters.append(record.cost_amount.is_not(None))
    return filters


def _public_record(row: AudioGenerationRecord) -> dict[str, Any]:
    return {
        "task_key": row.task_key, "task_id": row.task_id, "owner_id": row.owner_id,
        "status": row.status, "mode": row.mode, "model": row.model,
        "voice_id": row.voice_id, "output_format": row.output_format,
        "cost": row.cost_amount, "duration_ms": row.duration_ms,
        "upstream_task_id": row.upstream_task_id, "error": row.error,
        "completed_at": row.completed_at or row.event_at,
        "audio_url": "" if row.history_deleted else row.audio_url,
        "history_deleted": row.history_deleted,
        "reconciliation_required": row.reconciliation_required,
    }


def list_records(
    session: Session, *, owner_id: str = "", status: str = "all",
    start_at: datetime | None = None, end_at: datetime | None = None,
    limit: int = 100, offset: int = 0, query_text: str = "", cost_only: bool = False,
    cursor: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Lightweight keyset page plus SQL totals for the entire filtered range."""
    record = AudioGenerationRecord
    safe_limit = max(1, min(500, int(limit or 100)))
    filters = _filters(
        owner_id=owner_id,
        status=status,
        start_at=start_at,
        end_at=end_at,
        query_text=query_text,
        cost_only=cost_only,
    )
    totals = session.execute(select(
        func.count(record.task_key), func.count(record.cost_amount), func.sum(record.cost_amount),
    ).where(*filters)).one()
    page_filters = list(filters)
    if cursor is not None:
        at = parse_datetime(cursor.get("event_at"))
        key = cursor.get("task_key")
        if at is None or not isinstance(key, str) or not key or len(key) > 383:
            raise ValueError("invalid audio record cursor")
        page_filters.append(or_(record.event_at < at, and_(record.event_at == at, record.task_key < key)))
    # Do not load the internal credential identifier into monitoring result rows.
    query = select(record).options(defer(record.credential_id)).where(
        *page_filters,
    ).order_by(record.event_at.desc(), record.task_key.desc()).limit(safe_limit + 1)
    if cursor is None:
        query = query.offset(max(0, int(offset or 0)))
    rows = list(session.scalars(query))
    has_more = len(rows) > safe_limit
    rows = rows[:safe_limit]
    last = rows[-1] if rows else None
    return {
        "items": [_public_record(row) for row in rows],
        "record_count": int(totals[0]), "cost_count": int(totals[1]),
        "cost_total": totals[2] or Decimal(0), "limit": safe_limit,
        "offset": max(0, int(offset or 0)),
        "truncated": has_more,
        "next_cursor": {"event_at": last.event_at.isoformat(), "task_key": last.task_key} if has_more else None,
    }


def aggregate_records(session: Session, *, start_at=None, end_at=None) -> dict[str, Any]:
    record = AudioGenerationRecord
    filters = _filters(start_at=start_at, end_at=end_at)
    owners = [dict(row) for row in session.execute(select(
        record.owner_id, func.max(record.owner_username).label("username"),
        func.max(record.owner_name).label("name"),
        func.sum(case((record.status == "success", 1), else_=0)).label("success_count"),
        func.sum(case((record.status == "error", 1), else_=0)).label("failed_count"),
        func.sum(case((record.status == "canceled", 1), else_=0)).label("canceled_count"),
        func.sum(case((record.reconciliation_required.is_(True), 1), else_=0)).label("reconciliation_required_count"),
        func.count(record.cost_amount).label("cost_count"),
        func.sum(record.cost_amount).label("cost_total"),
    ).where(*filters).group_by(record.owner_id)).mappings()]
    models = [dict(row) for row in session.execute(select(
        record.model, func.count(record.cost_amount).label("cost_count"),
        func.sum(record.cost_amount).label("cost_total"),
    ).where(*filters, record.cost_amount.is_not(None)).group_by(record.model)).mappings()]
    latency_filters = [*filters, record.status.in_(TERMINAL_STATUSES), record.duration_ms > 0]
    count, average, maximum = session.execute(select(
        func.count(record.task_key), func.avg(record.duration_ms), func.max(record.duration_ms),
    ).where(*latency_filters)).one()
    p95 = 0
    if count:
        rank = (count - 1) * 0.95
        lower = int(rank)
        samples = list(session.scalars(select(record.duration_ms).where(*latency_filters).order_by(
            record.duration_ms,
        ).offset(lower).limit(2)))
        p95 = int(round(samples[0] + (samples[-1] - samples[0]) * (rank - lower)))
    return {
        "owners": owners, "models": models,
        "latency": {"sample_size": count, "average_ms": round(float(average or 0), 1), "p95_ms": p95, "max_ms": maximum or 0},
    }


def active_record_counts(session: Session) -> list[dict[str, Any]]:
    record = AudioGenerationRecord
    return [dict(row) for row in session.execute(select(
        record.owner_id,
        func.sum(case((record.status == "queued", 1), else_=0)).label("queued_tasks"),
        func.sum(case((record.status == "running", 1), else_=0)).label("running_tasks"),
        func.count(record.task_key).label("active_tasks"),
    ).where(record.status.in_(ACTIVE_STATUSES)).group_by(record.owner_id)).mappings()]


def backfill_records(session: Session, *, after_key: str = "", batch_size: int = 200) -> dict[str, Any]:
    """Explicit, resumable one-batch backfill. Caller commits each batch.

    Existing snapshots are skipped, so a backfill cannot overwrite live accounting.
    No task-store import, DDL, implicit startup or request-time scans are performed.
    """
    tasks = table("audio_generation_tasks", column("key", String(383)), column("task_json", Text))
    size = max(1, min(1000, int(batch_size)))
    rows = session.execute(select(tasks.c.key, tasks.c.task_json).where(
        tasks.c.key > after_key,
    ).order_by(tasks.c.key).limit(size)).all()
    synced = invalid = 0
    for key, raw in rows:
        if record_exists(session, key):
            continue
        try:
            task = json.loads(raw)
        except (TypeError, ValueError):
            invalid += 1
            continue
        if not isinstance(task, dict) or not _clean(task.get("id")):
            invalid += 1
            continue
        sync_record(session, key, task)
        synced += 1
    return {
        "scanned": len(rows), "synced": synced, "invalid": invalid,
        "next_key": rows[-1][0] if rows else after_key, "done": len(rows) < size,
    }
