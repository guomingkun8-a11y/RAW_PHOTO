from __future__ import annotations

import json
import os
import random
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Protocol, TypeVar

from sqlalchemy import Column, DateTime, Integer, String, Text, create_engine, or_, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()
T = TypeVar("T")
MYSQL_RETRYABLE_TRANSACTION_CODES = {1205, 1213}
MYSQL_TRANSACTION_MAX_ATTEMPTS = 3


class OwnerPendingLimitError(ValueError):
    pass


def _mysql_error_code(exc: BaseException) -> int | None:
    current: BaseException | None = exc
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        for value in getattr(current, "args", ()):
            try:
                code = int(value)
            except (TypeError, ValueError):
                continue
            if code in MYSQL_RETRYABLE_TRANSACTION_CODES:
                return code
        nested = getattr(current, "orig", None) or getattr(current, "__cause__", None)
        current = nested if isinstance(nested, BaseException) else None
    return None


def _clean(value: object, default: str = "") -> str:
    text = str(value if value is not None else default).strip()
    return text or default


def _parse_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    text_value = _clean(value)
    if not text_value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text_value[:26], fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text_value.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def _task_workload_key(task: dict[str, Any]) -> str:
    for field in ("batch_id", "turn_id", "conversation_id"):
        value = _clean(task.get(field))
        if value:
            return f"{field}:{value}"
    return f"task:{_clean(task.get('id'))}"


def _merge_projection_updates(task: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    """Merge a delivery attempt without reopening an already delivered projection."""
    merged = dict(task)
    monitoring_pending = bool(task.get("monitoring_pending")) and bool(
        updates.get("monitoring_pending", task.get("monitoring_pending"))
    )
    library_pending = bool(task.get("library_pending")) and bool(
        updates.get("library_pending", task.get("library_pending"))
    )
    merged["monitoring_pending"] = monitoring_pending
    merged["library_pending"] = library_pending

    if not monitoring_pending and not library_pending:
        merged["projection_attempts"] = 0
        merged["projection_next_attempt_at"] = None
        merged["projection_last_error"] = ""
        return merged

    try:
        current_attempts = max(0, int(task.get("projection_attempts") or 0))
    except (TypeError, ValueError):
        current_attempts = 0
    try:
        attempted_count = max(0, int(updates.get("projection_attempts") or 0))
    except (TypeError, ValueError):
        attempted_count = 0
    merged["projection_attempts"] = max(current_attempts, attempted_count)

    current_retry = task.get("projection_next_attempt_at")
    attempted_retry = updates.get("projection_next_attempt_at")
    current_retry_at = _parse_datetime(current_retry)
    attempted_retry_at = _parse_datetime(attempted_retry)
    if current_retry_at is not None and (
        attempted_retry_at is None or current_retry_at >= attempted_retry_at
    ):
        merged["projection_next_attempt_at"] = current_retry
    else:
        merged["projection_next_attempt_at"] = attempted_retry

    attempted_error = _clean(updates.get("projection_last_error"))
    if attempted_error:
        merged["projection_last_error"] = attempted_error
    else:
        merged["projection_last_error"] = _clean(task.get("projection_last_error"))
    return merged


def can_claim_task_fairly(
    active_tasks: list[dict[str, Any]],
    candidate: dict[str, Any],
    owner_concurrency: int,
) -> bool:
    limit = max(1, int(owner_concurrency))
    running_tasks = [task for task in active_tasks if task.get("status") == "running"]
    if len(running_tasks) >= limit:
        return False

    workload_keys = {
        _task_workload_key(task)
        for task in active_tasks
        if task.get("status") in {"queued", "running"}
    }
    if len(workload_keys) <= 1:
        return True

    candidate_key = _task_workload_key(candidate)
    fair_share = max(1, (limit + len(workload_keys) - 1) // len(workload_keys))
    candidate_running = sum(
        1
        for task in running_tasks
        if _task_workload_key(task) == candidate_key
    )
    return candidate_running < fair_share


class ImageTaskStore(Protocol):
    shared: bool
    row_level: bool

    def load_all(self) -> dict[str, dict[str, Any]]:
        ...

    def save_all(self, tasks: dict[str, dict[str, Any]]) -> None:
        ...

    def delete_keys(self, keys: list[str]) -> None:
        ...

    def get_task(self, key: str) -> dict[str, Any] | None:
        ...

    def list_tasks(
        self,
        owner_id: str,
        task_ids: list[str] | None = None,
        *,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        ...

    def list_unfinished(self) -> list[tuple[str, dict[str, Any]]]:
        ...

    def list_projection_pending(self, *, limit: int = 100) -> list[tuple[str, dict[str, Any]]]:
        ...

    def save_task(self, key: str, task: dict[str, Any]) -> None:
        ...

    def create_task(
        self,
        key: str,
        task: dict[str, Any],
        *,
        owner_pending_limit: int | None = None,
    ) -> tuple[dict[str, Any], bool]:
        ...

    def update_task(
        self,
        key: str,
        updates: dict[str, Any],
        *,
        expected_status: str | None = None,
        reject_status: str | None = None,
    ) -> dict[str, Any] | None:
        ...

    def merge_projection_state(self, key: str, updates: dict[str, Any]) -> dict[str, Any] | None:
        ...

    def recover_unfinished(self, *, requeue: bool, message: str) -> int:
        ...

    def cleanup_before(self, cutoff: datetime) -> int:
        ...

    def count_tasks(self, owner_id: str, statuses: set[str]) -> int:
        ...

    def count_active_owners(self) -> int:
        ...

    def claim_task(self, key: str, *, owner_concurrency: int, updates: dict[str, Any]) -> dict[str, Any] | None:
        ...

    def get_batch_progress(self, owner_id: str, batch_id: str) -> dict[str, int | str]:
        ...

    def retry_task(
        self,
        key: str,
        *,
        max_retries: int,
        updates: dict[str, Any],
    ) -> dict[str, Any] | None:
        ...


class JsonImageTaskStore:
    shared = False
    row_level = False

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load_all(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        raw_items = raw.get("tasks") if isinstance(raw, dict) else raw
        if not isinstance(raw_items, list):
            return {}
        return {
            key: item
            for item in raw_items
            if isinstance(item, dict)
            and (task_id := _clean(item.get("id")))
            and (owner_id := _clean(item.get("owner_id")))
            and (key := f"{owner_id}:{task_id}")
        }

    def save_all(self, tasks: dict[str, dict[str, Any]]) -> None:
        items = sorted(tasks.values(), key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp_path.write_text(json.dumps({"tasks": items}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp_path.replace(self.path)

    def delete_keys(self, keys: list[str]) -> None:
        if not keys:
            return
        tasks = self.load_all()
        for key in keys:
            tasks.pop(key, None)
        self.save_all(tasks)

    def get_task(self, key: str) -> dict[str, Any] | None:
        return self.load_all().get(key)

    def list_tasks(
        self,
        owner_id: str,
        task_ids: list[str] | None = None,
        *,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        tasks = self.load_all()
        allowed = set(task_ids or [])
        items = [
            task
            for task in tasks.values()
            if task.get("owner_id") == owner_id and (not allowed or str(task.get("id")) in allowed)
        ]
        if not allowed:
            items.sort(key=lambda task: str(task.get("updated_at") or ""), reverse=True)
            if limit is not None:
                try:
                    page_limit = max(0, int(limit))
                except (TypeError, ValueError):
                    page_limit = 0
                items = items[:page_limit]
        return items

    def list_unfinished(self) -> list[tuple[str, dict[str, Any]]]:
        return [
            (key, task)
            for key, task in self.load_all().items()
            if task.get("status") in {"queued", "running"}
        ]

    def list_projection_pending(self, *, limit: int = 100) -> list[tuple[str, dict[str, Any]]]:
        now = datetime.now()
        items: list[tuple[str, dict[str, Any]]] = []
        for key, task in self.load_all().items():
            if not (task.get("monitoring_pending") or task.get("library_pending")):
                continue
            next_attempt_at = _parse_datetime(task.get("projection_next_attempt_at"))
            if next_attempt_at is not None and next_attempt_at > now:
                continue
            items.append((key, task))
        items.sort(key=lambda item: str(item[1].get("updated_at") or ""))
        return items[: max(1, int(limit or 100))]

    def save_task(self, key: str, task: dict[str, Any]) -> None:
        tasks = self.load_all()
        tasks[key] = task
        self.save_all(tasks)

    def create_task(
        self,
        key: str,
        task: dict[str, Any],
        *,
        owner_pending_limit: int | None = None,
    ) -> tuple[dict[str, Any], bool]:
        tasks = self.load_all()
        existing = tasks.get(key)
        if existing is not None:
            return existing, False
        if owner_pending_limit is not None:
            owner_id = _clean(task.get("owner_id"), "anonymous")
            active_count = sum(
                1
                for item in tasks.values()
                if _clean(item.get("owner_id"), "anonymous") == owner_id
                and item.get("status") in {"queued", "running"}
            )
            if active_count >= max(1, int(owner_pending_limit)):
                raise OwnerPendingLimitError("user task queue is full; wait for existing tasks to finish")
        tasks[key] = task
        self.save_all(tasks)
        return task, True

    def update_task(
        self,
        key: str,
        updates: dict[str, Any],
        *,
        expected_status: str | None = None,
        reject_status: str | None = None,
    ) -> dict[str, Any] | None:
        tasks = self.load_all()
        task = tasks.get(key)
        if task is None:
            return None
        if expected_status is not None and task.get("status") != expected_status:
            return None
        if reject_status is not None and task.get("status") == reject_status:
            return None
        task.update(updates)
        self.save_all(tasks)
        return task

    def merge_projection_state(self, key: str, updates: dict[str, Any]) -> dict[str, Any] | None:
        tasks = self.load_all()
        task = tasks.get(key)
        if task is None:
            return None
        merged = _merge_projection_updates(task, updates)
        tasks[key] = merged
        self.save_all(tasks)
        return merged

    def recover_unfinished(self, *, requeue: bool, message: str) -> int:
        tasks = self.load_all()
        changed = 0
        for task in tasks.values():
            if task.get("status") not in {"queued", "running"}:
                continue
            task["status"] = "queued" if requeue else "error"
            task["error"] = message
            task["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            task["updated_ts"] = datetime.now().timestamp()
            changed += 1
        if changed:
            self.save_all(tasks)
        return changed

    def cleanup_before(self, cutoff: datetime) -> int:
        tasks = self.load_all()
        removed = [
            key
            for key, task in tasks.items()
            if task.get("status") in {"success", "error", "canceled"}
            and not task.get("monitoring_pending")
            and not task.get("library_pending")
            and _parse_datetime(task.get("updated_at")) is not None
            and _parse_datetime(task.get("updated_at")) < cutoff
        ]
        self.delete_keys(removed)
        return len(removed)

    def count_tasks(self, owner_id: str, statuses: set[str]) -> int:
        return sum(
            1
            for task in self.load_all().values()
            if task.get("owner_id") == owner_id and task.get("status") in statuses
        )

    def count_active_owners(self) -> int:
        return len({
            _clean(task.get("owner_id"), "anonymous")
            for task in self.load_all().values()
            if task.get("status") in {"queued", "running"}
        })

    def claim_task(self, key: str, *, owner_concurrency: int, updates: dict[str, Any]) -> dict[str, Any] | None:
        tasks = self.load_all()
        task = tasks.get(key)
        if task is None or task.get("status") != "queued":
            return None
        owner_id = str(task.get("owner_id") or "")
        active_tasks = [
            item
            for item in tasks.values()
            if item.get("owner_id") == owner_id and item.get("status") in {"queued", "running"}
        ]
        if not can_claim_task_fairly(active_tasks, task, owner_concurrency):
            return None
        task.update(updates)
        self.save_all(tasks)
        return task

    def get_batch_progress(self, owner_id: str, batch_id: str) -> dict[str, int | str]:
        items = [
            task
            for task in self.load_all().values()
            if task.get("owner_id") == owner_id and task.get("batch_id") == batch_id
        ]
        total = max([int(task.get("batch_total") or 0) for task in items] + [len(items), 1])
        return {
            "batch_id": batch_id,
            "total": total,
            "completed": sum(1 for task in items if task.get("status") == "success"),
            "failed": sum(1 for task in items if task.get("status") == "error"),
            "canceled": sum(1 for task in items if task.get("status") == "canceled"),
            "running": sum(1 for task in items if task.get("status") == "running"),
            "queued": sum(1 for task in items if task.get("status") == "queued"),
        }

    def retry_task(
        self,
        key: str,
        *,
        max_retries: int,
        updates: dict[str, Any],
    ) -> dict[str, Any] | None:
        tasks = self.load_all()
        task = tasks.get(key)
        if task is None or task.get("status") != "running":
            return None
        attempts = int(task.get("attempts") or 0) + 1
        if attempts > max(0, int(max_retries)):
            return None
        task.update({**updates, "status": "queued", "attempts": attempts})
        self.save_all(tasks)
        return task


class ImageTaskModel(Base):
    __tablename__ = "image_tasks"

    key = Column(String(383), primary_key=True)
    owner_id = Column(String(191), nullable=False, index=True)
    task_id = Column(String(191), nullable=False, index=True)
    status = Column(String(32), nullable=False, index=True)
    mode = Column(String(32), nullable=False, default="generate")
    model = Column(String(191), nullable=True)
    batch_id = Column(String(191), nullable=True, index=True)
    batch_index = Column(Integer, nullable=True)
    batch_total = Column(Integer, nullable=True)
    created_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=True, index=True)
    projection_pending = Column(Integer, nullable=False, default=0, index=True)
    projection_next_attempt_at = Column(DateTime, nullable=True)
    task_json = Column(Text, nullable=False)


class ImageTaskOwnerGuardModel(Base):
    __tablename__ = "image_task_owner_guards"

    owner_id = Column(String(191), primary_key=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.now)


class DatabaseImageTaskStore:
    shared = True
    row_level = True

    def __init__(self, database_url: str):
        self.database_url = database_url
        engine_options: dict[str, Any] = {"pool_pre_ping": True, "pool_recycle": 3600}
        if database_url.startswith("sqlite"):
            engine_options["connect_args"] = {"check_same_thread": False, "timeout": 30}
        else:
            engine_options["pool_size"] = max(1, int(os.getenv("IMAGE_TASK_DB_POOL_SIZE", "10")))
            engine_options["max_overflow"] = max(0, int(os.getenv("IMAGE_TASK_DB_MAX_OVERFLOW", "20")))
        self.engine = create_engine(database_url, **engine_options)
        Base.metadata.create_all(self.engine)
        self._ensure_indexes()
        self.Session = sessionmaker(bind=self.engine)

    def _run_write_transaction(self, operation: Callable[[Any], T]) -> T:
        attempts = MYSQL_TRANSACTION_MAX_ATTEMPTS if self.engine.dialect.name == "mysql" else 1
        for attempt in range(attempts):
            session = self.Session()
            try:
                result = operation(session)
                session.commit()
                return result
            except Exception as exc:
                session.rollback()
                if _mysql_error_code(exc) is None or attempt + 1 >= attempts:
                    raise
                time.sleep(random.uniform(0.025, 0.075) * (2**attempt))
            finally:
                session.close()
        raise RuntimeError("database transaction retry exhausted")

    def _ensure_indexes(self) -> None:
        if self.engine.dialect.name == "sqlite":
            self._ensure_columns()
            return
        if self.engine.dialect.name not in {"mysql", "postgresql"}:
            return
        self._ensure_columns()
        key_column = "`key`" if self.engine.dialect.name == "mysql" else "key"
        statements = [
            "CREATE INDEX idx_image_tasks_owner_updated ON image_tasks (owner_id, updated_at)",
            "CREATE INDEX idx_image_tasks_status_updated ON image_tasks (status, updated_at)",
            "CREATE INDEX idx_image_tasks_owner_batch ON image_tasks (owner_id, batch_id, updated_at)",
            f"CREATE INDEX idx_image_tasks_owner_status_key ON image_tasks (owner_id, status, {key_column})",
            "CREATE INDEX idx_image_tasks_projection_due ON image_tasks (projection_pending, projection_next_attempt_at)",
        ]
        with self.engine.begin() as connection:
            for statement in statements:
                try:
                    connection.execute(text(statement))
                except Exception:
                    pass

    def _ensure_columns(self) -> None:
        database = self.engine.url.database
        if self.engine.dialect.name == "mysql" and database:
            with self.engine.connect() as connection:
                existing = {
                    str(row[0])
                    for row in connection.execute(
                        text(
                            "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                            "WHERE TABLE_SCHEMA = :schema AND TABLE_NAME = 'image_tasks'"
                        ),
                        {"schema": database},
                    )
                }
        elif self.engine.dialect.name == "postgresql":
            with self.engine.connect() as connection:
                existing = {
                    str(row[0])
                    for row in connection.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_name = 'image_tasks'"
                        )
                    )
                }
        elif self.engine.dialect.name == "sqlite":
            with self.engine.connect() as connection:
                existing = {str(row[1]) for row in connection.execute(text("PRAGMA table_info(image_tasks)"))}
        else:
            return
        definitions = {
            "batch_id": "VARCHAR(191)",
            "batch_index": "INTEGER",
            "batch_total": "INTEGER",
            "projection_pending": "INTEGER NOT NULL DEFAULT 0",
            "projection_next_attempt_at": "TIMESTAMP",
        }
        with self.engine.begin() as connection:
            for name, definition in definitions.items():
                if name not in existing:
                    connection.execute(text(f"ALTER TABLE image_tasks ADD COLUMN {name} {definition}"))

    @staticmethod
    def _row_to_task(row: ImageTaskModel) -> dict[str, Any] | None:
        try:
            data = json.loads(row.task_json)
        except Exception:
            return None
        return data if isinstance(data, dict) else None

    @staticmethod
    def _apply_row(row: ImageTaskModel, key: str, task: dict[str, Any]) -> None:
        row.key = key
        row.owner_id = _clean(task.get("owner_id"), "anonymous")
        row.task_id = _clean(task.get("id"))
        row.status = _clean(task.get("status"), "error")
        row.mode = "edit" if task.get("mode") == "edit" else "generate"
        row.model = _clean(task.get("model")) or None
        row.batch_id = _clean(task.get("batch_id")) or None
        row.batch_index = int(task.get("batch_index")) if task.get("batch_index") is not None else None
        row.batch_total = int(task.get("batch_total")) if task.get("batch_total") is not None else None
        row.created_at = _parse_datetime(task.get("created_at"))
        row.updated_at = _parse_datetime(task.get("updated_at"))
        row.projection_pending = 1 if task.get("monitoring_pending") or task.get("library_pending") else 0
        row.projection_next_attempt_at = _parse_datetime(task.get("projection_next_attempt_at"))
        row.task_json = json.dumps(task, ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def _row_values(cls, key: str, task: dict[str, Any]) -> dict[str, Any]:
        row = ImageTaskModel(key=key)
        cls._apply_row(row, key, task)
        return {
            "owner_id": row.owner_id,
            "task_id": row.task_id,
            "status": row.status,
            "mode": row.mode,
            "model": row.model,
            "batch_id": row.batch_id,
            "batch_index": row.batch_index,
            "batch_total": row.batch_total,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "projection_pending": row.projection_pending,
            "projection_next_attempt_at": row.projection_next_attempt_at,
            "task_json": row.task_json,
        }

    def load_all(self) -> dict[str, dict[str, Any]]:
        session = self.Session()
        try:
            tasks: dict[str, dict[str, Any]] = {}
            for row in session.query(ImageTaskModel).all():
                task = self._row_to_task(row)
                if task is not None:
                    tasks[row.key] = task
            return tasks
        finally:
            session.close()

    def save_all(self, tasks: dict[str, dict[str, Any]]) -> None:
        session = self.Session()
        try:
            for key, task in tasks.items():
                if not isinstance(task, dict):
                    continue
                row = session.query(ImageTaskModel).filter(ImageTaskModel.key == key).one_or_none()
                if row is None:
                    row = ImageTaskModel(key=key)
                    session.add(row)
                self._apply_row(row, key, task)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_task(self, key: str) -> dict[str, Any] | None:
        session = self.Session()
        try:
            row = session.query(ImageTaskModel).filter(ImageTaskModel.key == key).one_or_none()
            return self._row_to_task(row) if row is not None else None
        finally:
            session.close()

    def list_tasks(
        self,
        owner_id: str,
        task_ids: list[str] | None = None,
        *,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        session = self.Session()
        try:
            query = session.query(ImageTaskModel).filter(ImageTaskModel.owner_id == owner_id)
            if task_ids:
                query = query.filter(ImageTaskModel.task_id.in_(task_ids))
            query = query.order_by(ImageTaskModel.updated_at.desc())
            if not task_ids and limit is not None:
                query = query.limit(max(0, int(limit)))
            rows = query.all()
            return [task for row in rows if (task := self._row_to_task(row)) is not None]
        finally:
            session.close()

    def list_unfinished(self) -> list[tuple[str, dict[str, Any]]]:
        session = self.Session()
        try:
            rows = (
                session.query(ImageTaskModel)
                .filter(ImageTaskModel.status.in_(["queued", "running"]))
                .all()
            )
            return [
                (row.key, task)
                for row in rows
                if (task := self._row_to_task(row)) is not None
            ]
        finally:
            session.close()

    def list_projection_pending(self, *, limit: int = 100) -> list[tuple[str, dict[str, Any]]]:
        session = self.Session()
        try:
            rows = (
                session.query(ImageTaskModel)
                .filter(
                    ImageTaskModel.projection_pending == 1,
                    or_(
                        ImageTaskModel.projection_next_attempt_at.is_(None),
                        ImageTaskModel.projection_next_attempt_at <= datetime.now(),
                    ),
                )
                .order_by(ImageTaskModel.projection_next_attempt_at.asc(), ImageTaskModel.updated_at.asc())
                .limit(max(1, int(limit or 100)))
                .all()
            )
            return [
                (row.key, task)
                for row in rows
                if (task := self._row_to_task(row)) is not None
            ]
        finally:
            session.close()

    def save_task(self, key: str, task: dict[str, Any]) -> None:
        def operation(session):
            row = session.query(ImageTaskModel).filter(ImageTaskModel.key == key).one_or_none()
            if row is None:
                row = ImageTaskModel(key=key)
                session.add(row)
            self._apply_row(row, key, task)
            return None

        self._run_write_transaction(operation)

    def _ensure_owner_guard(self, session, owner_id: str) -> None:
        if self.engine.dialect.name == "mysql":
            statement = (
                "INSERT IGNORE INTO image_task_owner_guards (owner_id, updated_at) "
                "VALUES (:owner_id, :updated_at)"
            )
        elif self.engine.dialect.name == "postgresql":
            statement = (
                "INSERT INTO image_task_owner_guards (owner_id, updated_at) "
                "VALUES (:owner_id, :updated_at) ON CONFLICT (owner_id) DO NOTHING"
            )
        else:
            statement = (
                "INSERT OR IGNORE INTO image_task_owner_guards (owner_id, updated_at) "
                "VALUES (:owner_id, :updated_at)"
            )
        session.execute(text(statement), {"owner_id": owner_id, "updated_at": datetime.now()})
        session.query(ImageTaskOwnerGuardModel).filter(
            ImageTaskOwnerGuardModel.owner_id == owner_id
        ).with_for_update().one()

    def create_task(
        self,
        key: str,
        task: dict[str, Any],
        *,
        owner_pending_limit: int | None = None,
    ) -> tuple[dict[str, Any], bool]:
        def operation(session):
            owner_id = _clean(task.get("owner_id"), "anonymous")
            self._ensure_owner_guard(session, owner_id)
            existing = session.query(ImageTaskModel).filter(ImageTaskModel.key == key).one_or_none()
            if existing is not None:
                stored = self._row_to_task(existing)
                return (stored or {}, False)
            if owner_pending_limit is not None:
                active_count = int(
                    session.query(ImageTaskModel)
                    .filter(
                        ImageTaskModel.owner_id == owner_id,
                        ImageTaskModel.status.in_(["queued", "running"]),
                    )
                    .count()
                )
                if active_count >= max(1, int(owner_pending_limit)):
                    raise OwnerPendingLimitError("user task queue is full; wait for existing tasks to finish")
            row = ImageTaskModel(key=key)
            self._apply_row(row, key, task)
            session.add(row)
            session.flush()
            return task, True

        try:
            return self._run_write_transaction(operation)
        except IntegrityError:
            existing = self.get_task(key)
            if existing is None:
                raise
            return existing, False

    def update_task(
        self,
        key: str,
        updates: dict[str, Any],
        *,
        expected_status: str | None = None,
        reject_status: str | None = None,
    ) -> dict[str, Any] | None:
        def operation(session):
            row = (
                session.query(ImageTaskModel)
                .filter(ImageTaskModel.key == key)
                .with_for_update()
                .one_or_none()
            )
            if row is None:
                return None
            task = self._row_to_task(row)
            if task is None:
                return None
            if expected_status is not None and task.get("status") != expected_status:
                return None
            if reject_status is not None and task.get("status") == reject_status:
                return None
            task.update(updates)
            self._apply_row(row, key, task)
            return task

        return self._run_write_transaction(operation)

    def merge_projection_state(self, key: str, updates: dict[str, Any]) -> dict[str, Any] | None:
        def operation(session):
            row = (
                session.query(ImageTaskModel)
                .filter(ImageTaskModel.key == key)
                .with_for_update()
                .one_or_none()
            )
            if row is None:
                return None
            task = self._row_to_task(row)
            if task is None:
                return None
            merged = _merge_projection_updates(task, updates)
            self._apply_row(row, key, merged)
            return merged

        return self._run_write_transaction(operation)

    def recover_unfinished(self, *, requeue: bool, message: str) -> int:
        session = self.Session()
        try:
            rows = (
                session.query(ImageTaskModel)
                .filter(ImageTaskModel.status.in_(["queued", "running"]))
                .with_for_update()
                .all()
            )
            status = "queued" if requeue else "error"
            for row in rows:
                task = self._row_to_task(row)
                if task is None:
                    continue
                task.update(
                    {
                        "status": status,
                        "error": message,
                        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "updated_ts": datetime.now().timestamp(),
                    }
                )
                self._apply_row(row, row.key, task)
            session.commit()
            return len(rows)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def cleanup_before(self, cutoff: datetime) -> int:
        session = self.Session()
        try:
            query = session.query(ImageTaskModel).filter(
                ImageTaskModel.status.in_(["success", "error", "canceled"]),
                ImageTaskModel.updated_at < cutoff,
                ImageTaskModel.projection_pending == 0,
            )
            count = query.delete(synchronize_session=False)
            session.commit()
            return int(count or 0)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def count_tasks(self, owner_id: str, statuses: set[str]) -> int:
        session = self.Session()
        try:
            return int(
                session.query(ImageTaskModel)
                .filter(
                    ImageTaskModel.owner_id == owner_id,
                    ImageTaskModel.status.in_(list(statuses)),
                )
                .count()
            )
        finally:
            session.close()

    def count_active_owners(self) -> int:
        session = self.Session()
        try:
            return int(
                session.query(ImageTaskModel.owner_id)
                .filter(ImageTaskModel.status.in_(["queued", "running"]))
                .distinct()
                .count()
            )
        finally:
            session.close()

    def claim_task(self, key: str, *, owner_concurrency: int, updates: dict[str, Any]) -> dict[str, Any] | None:
        def operation(session):
            row = (
                session.query(ImageTaskModel)
                .filter(ImageTaskModel.key == key)
                .with_for_update()
                .one_or_none()
            )
            if row is None or row.status != "queued":
                return None
            task = self._row_to_task(row)
            if task is None:
                return None
            owner_id = _clean(task.get("owner_id"), "anonymous")
            self._ensure_owner_guard(session, owner_id)
            active_rows = (
                session.query(ImageTaskModel)
                .filter(
                    ImageTaskModel.owner_id == owner_id,
                    ImageTaskModel.status.in_(["queued", "running"]),
                )
                .order_by(ImageTaskModel.key.asc())
                .all()
            )
            active_tasks = [
                active_task
                for active_row in active_rows
                if (active_task := self._row_to_task(active_row)) is not None
            ]
            if not can_claim_task_fairly(active_tasks, task, owner_concurrency):
                return None
            task.update(updates)
            affected = (
                session.query(ImageTaskModel)
                .filter(ImageTaskModel.key == key, ImageTaskModel.status == "queued")
                .update(self._row_values(key, task), synchronize_session=False)
            )
            if int(affected or 0) != 1:
                return None
            return task

        return self._run_write_transaction(operation)

    def retry_task(
        self,
        key: str,
        *,
        max_retries: int,
        updates: dict[str, Any],
    ) -> dict[str, Any] | None:
        def operation(session):
            row = (
                session.query(ImageTaskModel)
                .filter(ImageTaskModel.key == key)
                .with_for_update()
                .one_or_none()
            )
            if row is None or row.status != "running":
                return None
            task = self._row_to_task(row)
            if task is None:
                return None
            attempts = int(task.get("attempts") or 0) + 1
            if attempts > max(0, int(max_retries)):
                return None
            task.update({**updates, "status": "queued", "attempts": attempts})
            self._apply_row(row, key, task)
            return task

        return self._run_write_transaction(operation)

    def get_batch_progress(self, owner_id: str, batch_id: str) -> dict[str, int | str]:
        session = self.Session()
        try:
            rows = (
                session.query(ImageTaskModel)
                .filter(
                    ImageTaskModel.owner_id == owner_id,
                    ImageTaskModel.batch_id == batch_id,
                )
                .all()
            )
            tasks = [task for row in rows if (task := self._row_to_task(row)) is not None]
            total = max([int(task.get("batch_total") or 0) for task in tasks] + [len(tasks), 1])
            return {
                "batch_id": batch_id,
                "total": total,
                "completed": sum(1 for task in tasks if task.get("status") == "success"),
                "failed": sum(1 for task in tasks if task.get("status") == "error"),
                "canceled": sum(1 for task in tasks if task.get("status") == "canceled"),
                "running": sum(1 for task in tasks if task.get("status") == "running"),
                "queued": sum(1 for task in tasks if task.get("status") == "queued"),
            }
        finally:
            session.close()

    def delete_keys(self, keys: list[str]) -> None:
        if not keys:
            return
        session = self.Session()
        try:
            session.query(ImageTaskModel).filter(ImageTaskModel.key.in_(keys)).delete(synchronize_session=False)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def is_empty(self) -> bool:
        session = self.Session()
        try:
            return session.query(ImageTaskModel).count() == 0
        finally:
            session.close()

    def close(self) -> None:
        self.engine.dispose()
