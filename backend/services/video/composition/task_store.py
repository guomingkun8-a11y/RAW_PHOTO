from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

from sqlalchemy import Column, DateTime, Index, String, Text, create_engine
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import declarative_base, sessionmaker


Base = declarative_base()
LONG_TEXT = Text().with_variant(LONGTEXT, "mysql")


def _clean(value: object, default: str = "") -> str:
    text = str(value if value is not None else default).strip()
    return text or default


def _parse_datetime(value: object) -> datetime | None:
    text = _clean(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


class VideoCompositionTaskModel(Base):
    __tablename__ = "video_composition_tasks"
    __table_args__ = (
        Index("idx_video_composition_owner_updated", "owner_id", "updated_at"),
        Index("idx_video_composition_owner_workflow", "owner_id", "workflow_id"),
    )

    key = Column(String(383), primary_key=True)
    owner_id = Column(String(191), nullable=False, index=True)
    task_id = Column(String(191), nullable=False, index=True)
    workflow_id = Column(String(191), nullable=False, default="")
    status = Column(String(32), nullable=False, index=True)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False, index=True)
    task_json = Column(LONG_TEXT, nullable=False)


class VideoCompositionTaskStore:
    def __init__(self, database_url: str):
        engine_options: dict[str, Any] = {"pool_pre_ping": True, "pool_recycle": 3600}
        if database_url.startswith("sqlite"):
            engine_options["connect_args"] = {"check_same_thread": False, "timeout": 30}
        else:
            engine_options["pool_size"] = max(1, int(os.getenv("VIDEO_COMPOSITION_DB_POOL_SIZE", "5")))
            engine_options["max_overflow"] = max(0, int(os.getenv("VIDEO_COMPOSITION_DB_MAX_OVERFLOW", "10")))
        self.engine = create_engine(database_url, **engine_options)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    @staticmethod
    def _row_to_task(row: VideoCompositionTaskModel) -> dict[str, Any] | None:
        try:
            value = json.loads(row.task_json)
        except Exception:
            return None
        return value if isinstance(value, dict) else None

    @staticmethod
    def _apply(row: VideoCompositionTaskModel, key: str, task: dict[str, Any]) -> None:
        row.key = key
        row.owner_id = _clean(task.get("owner_id"), "anonymous")[:191]
        row.task_id = _clean(task.get("id"))[:191]
        row.workflow_id = _clean(task.get("workflow_id"))[:191]
        row.status = _clean(task.get("status"), "error")[:32]
        row.created_at = _parse_datetime(task.get("created_at")) or datetime.now()
        row.updated_at = _parse_datetime(task.get("updated_at")) or datetime.now()
        row.task_json = json.dumps(task, ensure_ascii=False, separators=(",", ":"))

    def get(self, key: str) -> dict[str, Any] | None:
        session = self.Session()
        try:
            row = session.query(VideoCompositionTaskModel).filter_by(key=key).one_or_none()
            return self._row_to_task(row) if row else None
        finally:
            session.close()

    def list(self, owner_id: str, *, workflow_id: str = "", limit: int = 50) -> list[dict[str, Any]]:
        session = self.Session()
        try:
            query = session.query(VideoCompositionTaskModel).filter_by(owner_id=owner_id)
            if workflow_id:
                query = query.filter_by(workflow_id=workflow_id)
            rows = query.order_by(VideoCompositionTaskModel.updated_at.desc()).limit(max(1, min(200, limit))).all()
            return [task for row in rows if (task := self._row_to_task(row)) is not None]
        finally:
            session.close()

    def count_pending(self, owner_id: str) -> int:
        session = self.Session()
        try:
            return int(session.query(VideoCompositionTaskModel).filter(
                VideoCompositionTaskModel.owner_id == owner_id,
                VideoCompositionTaskModel.status.in_(["queued", "running"]),
            ).count())
        finally:
            session.close()

    def list_by_status(self, statuses: list[str], *, limit: int = 500) -> list[dict[str, Any]]:
        normalized = [str(status).strip() for status in statuses if str(status).strip()]
        if not normalized:
            return []
        session = self.Session()
        try:
            rows = (
                session.query(VideoCompositionTaskModel)
                .filter(VideoCompositionTaskModel.status.in_(normalized))
                .order_by(VideoCompositionTaskModel.updated_at.asc())
                .limit(max(1, min(2000, int(limit))))
                .all()
            )
            return [task for row in rows if (task := self._row_to_task(row)) is not None]
        finally:
            session.close()

    def status_counts(self) -> dict[str, int]:
        session = self.Session()
        try:
            rows = session.query(VideoCompositionTaskModel.status).all()
            result: dict[str, int] = {}
            for (status,) in rows:
                key = _clean(status, "unknown")
                result[key] = result.get(key, 0) + 1
            return result
        finally:
            session.close()

    def duration_samples(self, *, limit: int = 2000) -> list[int]:
        session = self.Session()
        try:
            rows = (
                session.query(VideoCompositionTaskModel.task_json)
                .filter(VideoCompositionTaskModel.status.in_(["success", "error"]))
                .order_by(VideoCompositionTaskModel.updated_at.desc())
                .limit(max(1, min(5000, int(limit))))
                .all()
            )
            samples: list[int] = []
            for (payload,) in rows:
                try:
                    duration = int(json.loads(payload).get("duration_ms") or 0)
                except (TypeError, ValueError, json.JSONDecodeError, AttributeError):
                    duration = 0
                if duration > 0:
                    samples.append(duration)
            return samples
        finally:
            session.close()

    def create(self, key: str, task: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        session = self.Session()
        try:
            existing = session.query(VideoCompositionTaskModel).filter_by(key=key).one_or_none()
            if existing:
                return self._row_to_task(existing) or {}, False
            row = VideoCompositionTaskModel(key=key)
            self._apply(row, key, task)
            session.add(row)
            try:
                session.commit()
                return task, True
            except IntegrityError:
                session.rollback()
                existing = session.query(VideoCompositionTaskModel).filter_by(key=key).one()
                return self._row_to_task(existing) or {}, False
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def update(self, key: str, updates: dict[str, Any], *, expected_status: str | None = None) -> dict[str, Any] | None:
        session = self.Session()
        try:
            row = session.query(VideoCompositionTaskModel).filter_by(key=key).with_for_update().one_or_none()
            if not row:
                return None
            task = self._row_to_task(row)
            if task is None or (expected_status and task.get("status") != expected_status):
                return None
            task.update(updates)
            task["updated_at"] = datetime.now().isoformat(timespec="seconds")
            self._apply(row, key, task)
            session.commit()
            return task
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def delete(self, key: str) -> bool:
        session = self.Session()
        try:
            row = session.query(VideoCompositionTaskModel).filter_by(key=key).one_or_none()
            if row is None:
                return False
            session.delete(row)
            session.commit()
            return True
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def close(self) -> None:
        self.engine.dispose()
