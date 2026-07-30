from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import Column, DateTime, Integer, String, Text, create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()


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

    def list_terminal(self) -> list[dict[str, Any]]:
        ...

    def save_task(self, key: str, task: dict[str, Any]) -> None:
        ...

    def create_task(self, key: str, task: dict[str, Any]) -> tuple[dict[str, Any], bool]:
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

    def recover_unfinished(self, *, requeue: bool, message: str) -> int:
        ...

    def cleanup_before(self, cutoff: datetime) -> int:
        ...

    def count_tasks(self, owner_id: str, statuses: set[str]) -> int:
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

    def list_terminal(self) -> list[dict[str, Any]]:
        return [
            task
            for task in self.load_all().values()
            if task.get("status") in {"success", "error", "canceled"}
        ]

    def save_task(self, key: str, task: dict[str, Any]) -> None:
        tasks = self.load_all()
        tasks[key] = task
        self.save_all(tasks)

    def create_task(self, key: str, task: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        tasks = self.load_all()
        existing = tasks.get(key)
        if existing is not None:
            return existing, False
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
    task_json = Column(Text, nullable=False)


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
        }
        with self.engine.begin() as connection:
            for name, definition in definitions.items():
                if name not in existing:
                    connection.execute(text(f"ALTER TABLE image_tasks ADD COLUMN {name} {definition} NULL"))

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
        row.task_json = json.dumps(task, ensure_ascii=False, separators=(",", ":"))

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

    def list_terminal(self) -> list[dict[str, Any]]:
        session = self.Session()
        try:
            rows = (
                session.query(ImageTaskModel)
                .filter(ImageTaskModel.status.in_(["success", "error", "canceled"]))
                .order_by(ImageTaskModel.updated_at.desc())
                .all()
            )
            return [task for row in rows if (task := self._row_to_task(row)) is not None]
        finally:
            session.close()

    def save_task(self, key: str, task: dict[str, Any]) -> None:
        session = self.Session()
        try:
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

    def create_task(self, key: str, task: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        session = self.Session()
        try:
            existing = session.query(ImageTaskModel).filter(ImageTaskModel.key == key).one_or_none()
            if existing is not None:
                stored = self._row_to_task(existing)
                return (stored or {}, False)
            row = ImageTaskModel(key=key)
            self._apply_row(row, key, task)
            session.add(row)
            try:
                session.commit()
                return task, True
            except IntegrityError:
                session.rollback()
                existing = session.query(ImageTaskModel).filter(ImageTaskModel.key == key).one_or_none()
                if existing is None:
                    raise
                stored = self._row_to_task(existing)
                return (stored or {}, False)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def update_task(
        self,
        key: str,
        updates: dict[str, Any],
        *,
        expected_status: str | None = None,
        reject_status: str | None = None,
    ) -> dict[str, Any] | None:
        session = self.Session()
        try:
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
            session.commit()
            return task
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

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

    def claim_task(self, key: str, *, owner_concurrency: int, updates: dict[str, Any]) -> dict[str, Any] | None:
        session = self.Session()
        try:
            row = (
                session.query(ImageTaskModel)
                .filter(ImageTaskModel.key == key)
                .one_or_none()
            )
            if row is None or row.status != "queued":
                return None
            owner_id = row.owner_id
            # Lock this owner's active rows in a deterministic order. Locking
            # the target row first lets two workers claim different queued rows
            # for the same owner and then deadlock when both count active rows.
            active_rows = (
                session.query(ImageTaskModel)
                .filter(
                    ImageTaskModel.owner_id == owner_id,
                    ImageTaskModel.status.in_(["queued", "running"]),
                )
                .order_by(ImageTaskModel.key.asc())
                .with_for_update()
                .all()
            )
            row = next((active_row for active_row in active_rows if active_row.key == key), None)
            if row is None or row.status != "queued":
                return None
            task = self._row_to_task(row)
            if task is None:
                return None
            active_tasks = []
            for active_row in active_rows:
                active_task = self._row_to_task(active_row)
                if active_task is None:
                    active_task = {
                        "id": active_row.task_id,
                        "owner_id": active_row.owner_id,
                        "status": active_row.status,
                        "batch_id": active_row.batch_id,
                    }
                active_tasks.append(active_task)
            if not can_claim_task_fairly(active_tasks, task, owner_concurrency):
                return None
            task.update(updates)
            self._apply_row(row, key, task)
            session.commit()
            return task
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def retry_task(
        self,
        key: str,
        *,
        max_retries: int,
        updates: dict[str, Any],
    ) -> dict[str, Any] | None:
        session = self.Session()
        try:
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
            session.commit()
            return task
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

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
