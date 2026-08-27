from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy import Column, DateTime, Integer, String, Text, create_engine
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import declarative_base, sessionmaker


Base = declarative_base()
LONG_TEXT = Text().with_variant(LONGTEXT, "mysql")


def _clean(value: object, default: str = "") -> str:
    text = str(value if value is not None else default).strip()
    return text or default


def _parse_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    text = _clean(value)
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text[:26], fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


class VideoGenerationTaskStore(Protocol):
    row_level: bool

    def get_task(self, key: str) -> dict[str, Any] | None:
        ...

    def list_tasks(self, owner_id: str, task_ids: list[str] | None = None, *, limit: int | None = None) -> list[dict[str, Any]]:
        ...

    def list_unfinished(self) -> list[tuple[str, dict[str, Any]]]:
        ...

    def create_task(self, key: str, task: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        ...

    def save_task(self, key: str, task: dict[str, Any]) -> None:
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

    def claim_task(self, key: str, *, owner_concurrency: int, updates: dict[str, Any]) -> dict[str, Any] | None:
        ...

    def count_tasks(self, owner_id: str, statuses: set[str]) -> int:
        ...

    def recover_unfinished(self, *, requeue: bool, message: str) -> int:
        ...


class VideoGenerationTaskModel(Base):
    __tablename__ = "video_generation_tasks"

    key = Column(String(383), primary_key=True)
    owner_id = Column(String(191), nullable=False, index=True)
    task_id = Column(String(191), nullable=False, index=True)
    status = Column(String(32), nullable=False, index=True)
    mode = Column(String(32), nullable=False, default="text_to_video")
    model = Column(String(191), nullable=True)
    upstream_task_id = Column(String(191), nullable=True, index=True)
    created_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=True, index=True)
    task_json = Column(LONG_TEXT, nullable=False)


class DatabaseVideoGenerationTaskStore:
    row_level = True

    def __init__(self, database_url: str):
        self.database_url = database_url
        engine_options: dict[str, Any] = {"pool_pre_ping": True, "pool_recycle": 3600}
        if database_url.startswith("sqlite"):
            engine_options["connect_args"] = {"check_same_thread": False, "timeout": 30}
        else:
            engine_options["pool_size"] = max(1, int(os.getenv("VIDEO_GENERATION_DB_POOL_SIZE", "5")))
            engine_options["max_overflow"] = max(0, int(os.getenv("VIDEO_GENERATION_DB_MAX_OVERFLOW", "10")))
        self.engine = create_engine(database_url, **engine_options)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    @staticmethod
    def _row_to_task(row: VideoGenerationTaskModel) -> dict[str, Any] | None:
        try:
            data = json.loads(row.task_json)
        except Exception:
            return None
        return data if isinstance(data, dict) else None

    @staticmethod
    def _apply_row(row: VideoGenerationTaskModel, key: str, task: dict[str, Any]) -> None:
        row.key = key
        row.owner_id = _clean(task.get("owner_id"), "anonymous")
        row.task_id = _clean(task.get("id"))
        row.status = _clean(task.get("status"), "error")
        row.mode = _clean(task.get("mode"), "text_to_video")
        row.model = _clean(task.get("model")) or None
        row.upstream_task_id = _clean(task.get("upstream_task_id")) or None
        row.created_at = _parse_datetime(task.get("created_at"))
        row.updated_at = _parse_datetime(task.get("updated_at"))
        row.task_json = json.dumps(task, ensure_ascii=False, separators=(",", ":"))

    def get_task(self, key: str) -> dict[str, Any] | None:
        session = self.Session()
        try:
            row = session.query(VideoGenerationTaskModel).filter(VideoGenerationTaskModel.key == key).one_or_none()
            return self._row_to_task(row) if row is not None else None
        finally:
            session.close()

    def list_tasks(self, owner_id: str, task_ids: list[str] | None = None, *, limit: int | None = None) -> list[dict[str, Any]]:
        session = self.Session()
        try:
            query = session.query(VideoGenerationTaskModel).filter(VideoGenerationTaskModel.owner_id == owner_id)
            if task_ids:
                query = query.filter(VideoGenerationTaskModel.task_id.in_(task_ids))
            query = query.order_by(VideoGenerationTaskModel.updated_at.desc())
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
                session.query(VideoGenerationTaskModel)
                .filter(VideoGenerationTaskModel.status.in_(["queued", "running"]))
                .all()
            )
            return [(row.key, task) for row in rows if (task := self._row_to_task(row)) is not None]
        finally:
            session.close()

    def create_task(self, key: str, task: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        session = self.Session()
        try:
            existing = session.query(VideoGenerationTaskModel).filter(VideoGenerationTaskModel.key == key).one_or_none()
            if existing is not None:
                return (self._row_to_task(existing) or {}, False)
            row = VideoGenerationTaskModel(key=key)
            self._apply_row(row, key, task)
            session.add(row)
            try:
                session.commit()
                return task, True
            except IntegrityError:
                session.rollback()
                existing = session.query(VideoGenerationTaskModel).filter(VideoGenerationTaskModel.key == key).one_or_none()
                if existing is None:
                    raise
                return (self._row_to_task(existing) or {}, False)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def save_task(self, key: str, task: dict[str, Any]) -> None:
        session = self.Session()
        try:
            row = session.query(VideoGenerationTaskModel).filter(VideoGenerationTaskModel.key == key).one_or_none()
            if row is None:
                row = VideoGenerationTaskModel(key=key)
                session.add(row)
            self._apply_row(row, key, task)
            session.commit()
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
                session.query(VideoGenerationTaskModel)
                .filter(VideoGenerationTaskModel.key == key)
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

    def claim_task(self, key: str, *, owner_concurrency: int, updates: dict[str, Any]) -> dict[str, Any] | None:
        session = self.Session()
        try:
            row = session.query(VideoGenerationTaskModel).filter(VideoGenerationTaskModel.key == key).one_or_none()
            if row is None or row.status != "queued":
                return None
            active_count = (
                session.query(VideoGenerationTaskModel)
                .filter(
                    VideoGenerationTaskModel.owner_id == row.owner_id,
                    VideoGenerationTaskModel.status == "running",
                )
                .count()
            )
            if int(active_count or 0) >= max(1, int(owner_concurrency)):
                return None
            task = self._row_to_task(row)
            if task is None:
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

    def count_tasks(self, owner_id: str, statuses: set[str]) -> int:
        session = self.Session()
        try:
            return int(
                session.query(VideoGenerationTaskModel)
                .filter(
                    VideoGenerationTaskModel.owner_id == owner_id,
                    VideoGenerationTaskModel.status.in_(list(statuses)),
                )
                .count()
            )
        finally:
            session.close()

    def recover_unfinished(self, *, requeue: bool, message: str) -> int:
        session = self.Session()
        try:
            rows = (
                session.query(VideoGenerationTaskModel)
                .filter(VideoGenerationTaskModel.status.in_(["queued", "running"]))
                .with_for_update()
                .all()
            )
            status = "queued" if requeue else "error"
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            for row in rows:
                task = self._row_to_task(row)
                if task is None:
                    continue
                task.update({"status": status, "error": message, "updated_at": now, "updated_ts": datetime.now().timestamp()})
                self._apply_row(row, row.key, task)
            session.commit()
            return len(rows)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def close(self) -> None:
        self.engine.dispose()

