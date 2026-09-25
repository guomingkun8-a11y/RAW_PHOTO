from __future__ import annotations

import json
import os
import base64
import time
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy import Column, DateTime, Index, Integer, String, Text, and_, func, or_, create_engine, text
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import declarative_base, sessionmaker


Base = declarative_base()
LONG_TEXT = Text().with_variant(LONGTEXT, "mysql")


def _clean(value: object, default: str = "", limit: int | None = None) -> str:
    normalized = str(value if value is not None else default).strip() or default
    return normalized[:limit] if limit is not None else normalized


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

    def list_tasks(
        self,
        owner_id: str | None,
        task_ids: list[str] | None = None,
        *,
        conversation_id: str = "",
        limit: int | None = None,
        cursor: str = "",
        status: str = "",
        query_text: str = "",
    ) -> list[dict[str, Any]]:
        ...

    def count_all_tasks(
        self,
        owner_id: str | None,
        *,
        conversation_id: str = "",
        status: str = "",
        query_text: str = "",
    ) -> int:
        ...

    def list_unfinished(
        self,
        *,
        limit: int | None = None,
        after_key: str = "",
    ) -> list[tuple[str, dict[str, Any]]]:
        ...

    def create_task(
        self,
        key: str,
        task: dict[str, Any],
        *,
        pending_limit: int | None = None,
    ) -> tuple[dict[str, Any], bool]:
        ...

    def save_task(self, key: str, task: dict[str, Any]) -> None:
        ...

    def delete_task(self, key: str) -> bool:
        ...

    def update_task(
        self,
        key: str,
        updates: dict[str, Any],
        *,
        expected_status: str | None = None,
        reject_status: str | None = None,
        expected_token: str | None = None,
    ) -> dict[str, Any] | None:
        ...

    def claim_task(
        self,
        key: str,
        *,
        owner_concurrency: int,
        updates: dict[str, Any],
        owner_id: str = "",
    ) -> dict[str, Any] | None:
        ...

    def count_tasks(self, owner_id: str, statuses: set[str]) -> int:
        ...

    def recover_unfinished(self, *, requeue: bool, message: str) -> int:
        ...

    def request_cancel(self, key: str) -> dict[str, Any] | None:
        ...

    def cleanup_batch(self, handler: Any, limit: int = 20) -> int:
        ...

    def recover_task(self, key: str, cutoff: float) -> dict[str, Any] | None:
        ...


class VideoGenerationTaskModel(Base):
    __tablename__ = "video_generation_tasks"
    __table_args__ = (
        Index("idx_video_generation_owner_conversation_updated", "owner_id", "conversation_id", "updated_at"),
        Index("idx_video_generation_owner_status_created_key", "owner_id", "status", "created_at", "key"),
        Index("idx_video_generation_status_created_key", "status", "created_at", "key"),
    )

    key = Column(String(383), primary_key=True)
    owner_id = Column(String(191), nullable=False, index=True)
    task_id = Column(String(191), nullable=False, index=True)
    conversation_id = Column(String(191), nullable=False, default="")
    status = Column(String(32), nullable=False, index=True)
    mode = Column(String(32), nullable=False, default="text_to_video")
    model = Column(String(191), nullable=True)
    prompt = Column(LONG_TEXT, nullable=True)
    upstream_task_id = Column(String(191), nullable=True, index=True)
    created_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=True, index=True)
    task_json = Column(LONG_TEXT, nullable=False)


class VideoGenerationOwnerLock(Base):
    __tablename__ = "video_generation_owner_locks"
    owner_id = Column(String(191), primary_key=True)


class VideoGenerationCleanup(Base):
    __tablename__ = "video_generation_cleanup"
    key = Column(String(383), primary_key=True)
    task_json = Column(LONG_TEXT, nullable=False)
    attempts = Column(Integer, nullable=False, default=0)
    next_attempt_ts = Column(DateTime, nullable=False, index=True, default=datetime.now)
    error = Column(Text, nullable=False, default="")


def encode_cursor(task: dict[str, Any]) -> str:
    raw = json.dumps([task.get("created_at") or "1970-01-01 00:00:00",
                      f"{task['owner_id']}:{task['id']}"], separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        value = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        if not isinstance(value, list) or len(value) != 2 or not isinstance(value[1], str):
            raise ValueError()
        date = _parse_datetime(value[0])
        if date is None or len(value[1]) > 383:
            raise ValueError()
        return date, value[1]
    except Exception as exc:
        raise ValueError("invalid video history cursor") from exc


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
        from services.video.video_generation_records import RecordsBase
        RecordsBase.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def _begin_write(self, session, owner_id: str = "") -> None:
        # Lock an owner row, not just the task: counts and admission must serialize
        # across different tasks submitted by the same user on different API workers.
        dialect = self.engine.dialect.name
        if dialect == "sqlite":
            session.execute(text("BEGIN IMMEDIATE"))
        if not owner_id:
            return
        if dialect == "mysql":
            from sqlalchemy.dialects.mysql import insert
            statement = insert(VideoGenerationOwnerLock).values(owner_id=owner_id)
            session.execute(statement.on_duplicate_key_update(owner_id=owner_id))
        elif dialect in {"sqlite", "postgresql"}:
            if dialect == "sqlite":
                from sqlalchemy.dialects.sqlite import insert
            else:
                from sqlalchemy.dialects.postgresql import insert
            session.execute(insert(VideoGenerationOwnerLock).values(owner_id=owner_id).on_conflict_do_nothing())
        session.query(VideoGenerationOwnerLock).filter_by(owner_id=owner_id).with_for_update().one()

    @staticmethod
    def _record(session, key: str, task: dict[str, Any]) -> None:
        from services.video.video_generation_records import sync_record
        sync_record(session, key, task)

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
        row.conversation_id = _clean(task.get("conversation_id"))
        row.status = _clean(task.get("status"), "error")
        row.mode = _clean(task.get("mode"), "text_to_video")
        row.model = _clean(task.get("model")) or None
        row.prompt = _clean(task.get("prompt"), limit=12000) or None
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

    def list_tasks(
        self,
        owner_id: str | None,
        task_ids: list[str] | None = None,
        *,
        conversation_id: str = "",
        limit: int | None = None,
        cursor: str = "",
        status: str = "",
        query_text: str = "",
    ) -> list[dict[str, Any]]:
        session = self.Session()
        try:
            query = session.query(VideoGenerationTaskModel)
            if owner_id:
                query = query.filter(VideoGenerationTaskModel.owner_id == owner_id)
            if task_ids:
                query = query.filter(VideoGenerationTaskModel.task_id.in_(task_ids))
            if conversation_id:
                query = query.filter(VideoGenerationTaskModel.conversation_id == conversation_id)
            if status:
                query = query.filter(VideoGenerationTaskModel.status == status)
            if query_text:
                escaped = query_text.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                pattern = f"%{escaped}%"
                query = query.filter(or_(
                    func.lower(VideoGenerationTaskModel.prompt).like(pattern, escape="\\"),
                    func.lower(VideoGenerationTaskModel.model).like(pattern, escape="\\"),
                    func.lower(VideoGenerationTaskModel.task_id).like(pattern, escape="\\"),
                ))
            if cursor:
                date, key = decode_cursor(cursor)
                query = query.filter(or_(VideoGenerationTaskModel.created_at < date,
                    and_(VideoGenerationTaskModel.created_at == date, VideoGenerationTaskModel.key < key)))
            query = query.order_by(VideoGenerationTaskModel.created_at.desc(), VideoGenerationTaskModel.key.desc())
            if not task_ids and limit is not None:
                query = query.limit(max(0, int(limit)))
            rows = query.all()
            return [task for row in rows if (task := self._row_to_task(row)) is not None]
        finally:
            session.close()

    def count_all_tasks(
        self,
        owner_id: str | None,
        *,
        conversation_id: str = "",
        status: str = "",
        query_text: str = "",
    ) -> int:
        session = self.Session()
        try:
            query = session.query(VideoGenerationTaskModel)
            if owner_id:
                query = query.filter(VideoGenerationTaskModel.owner_id == owner_id)
            if conversation_id:
                query = query.filter(VideoGenerationTaskModel.conversation_id == conversation_id)
            if status:
                query = query.filter(VideoGenerationTaskModel.status == status)
            if query_text:
                escaped = query_text.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                pattern = f"%{escaped}%"
                query = query.filter(or_(
                    func.lower(VideoGenerationTaskModel.prompt).like(pattern, escape="\\"),
                    func.lower(VideoGenerationTaskModel.model).like(pattern, escape="\\"),
                    func.lower(VideoGenerationTaskModel.task_id).like(pattern, escape="\\"),
                ))
            return int(query.count())
        finally:
            session.close()

    def list_unfinished(self, *, limit: int | None = None, after_key: str = "") -> list[tuple[str, dict[str, Any]]]:
        session = self.Session()
        try:
            query = (
                session.query(VideoGenerationTaskModel)
                .filter(VideoGenerationTaskModel.status.in_(["queued", "running"]))
            )
            if after_key:
                query = query.filter(VideoGenerationTaskModel.key > after_key)
            query = query.order_by(VideoGenerationTaskModel.key)
            if limit is not None:
                query = query.limit(limit)
            rows = query.all()
            return [(row.key, task) for row in rows if (task := self._row_to_task(row)) is not None]
        finally:
            session.close()

    def create_task(self, key: str, task: dict[str, Any], *, pending_limit: int | None = None) -> tuple[dict[str, Any], bool]:
        session = self.Session()
        try:
            self._begin_write(session, str(task["owner_id"]))
            existing = session.query(VideoGenerationTaskModel).filter(VideoGenerationTaskModel.key == key).one_or_none()
            if existing is not None:
                return (self._row_to_task(existing) or {}, False)
            from services.video.video_generation_records import record_exists
            if record_exists(session, key):
                raise ValueError("client_task_id belongs to a deleted task; use a new request ID")
            if pending_limit is not None:
                count = session.query(VideoGenerationTaskModel).filter(
                    VideoGenerationTaskModel.owner_id == task["owner_id"],
                    VideoGenerationTaskModel.status.in_(["queued", "running"])).count()
                if count >= pending_limit:
                    raise ValueError("user video generation queue is full; wait for existing tasks to finish")
            row = VideoGenerationTaskModel(key=key)
            self._apply_row(row, key, task)
            session.add(row)
            self._record(session, key, task)
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
            self._begin_write(session, str(task["owner_id"]))
            row = session.query(VideoGenerationTaskModel).filter(VideoGenerationTaskModel.key == key).one_or_none()
            if row is None:
                row = VideoGenerationTaskModel(key=key)
                session.add(row)
            self._apply_row(row, key, task)
            self._record(session, key, task)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def delete_task(self, key: str) -> bool:
        session = self.Session()
        try:
            self._begin_write(session)
            row = session.query(VideoGenerationTaskModel).filter(VideoGenerationTaskModel.key == key).with_for_update().one_or_none()
            if row is None:
                return False
            task = self._row_to_task(row) or {}
            if row.status in {"queued", "running"} or task.get("reconciliation_required"):
                raise ValueError("video task is still in progress or requires billing reconciliation")
            task["history_deleted"] = True
            self._record(session, key, task)
            if task.get("storage") in {"local", "oss"} and task.get("storage_rel"):
                session.merge(VideoGenerationCleanup(key=key, task_json=json.dumps(task),
                              attempts=0, next_attempt_ts=datetime.now(), error=""))
            session.delete(row)
            session.commit()
            return True
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
        expected_token: str | None = None,
    ) -> dict[str, Any] | None:
        session = self.Session()
        try:
            self._begin_write(session)
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
            if expected_token is not None and task.get("execution_token") != expected_token:
                return None
            if updates.get("submission_state") == "submitting" and task.get("cancel_requested"):
                return None
            updates = dict(updates)
            if updates.get("cost") is None:
                updates.pop("cost", None)
            if task.get("cancel_requested") and updates.get("status") in {"success", "error"}:
                updates["status"] = "canceled"
                updates["cancellation_pending"] = False
            task.update(updates)
            self._apply_row(row, key, task)
            self._record(session, key, task)
            session.commit()
            return task
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def claim_task(self, key: str, *, owner_concurrency: int, updates: dict[str, Any], owner_id: str = "") -> dict[str, Any] | None:
        if not owner_id:
            task = self.get_task(key)
            if task is None:
                return None
            owner_id = str(task["owner_id"])
        session = self.Session()
        try:
            self._begin_write(session, owner_id)
            row = session.query(VideoGenerationTaskModel).filter(VideoGenerationTaskModel.key == key).with_for_update().one_or_none()
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
            if float(task.get("next_attempt_ts") or 0) > time.time():
                return None
            task.update(updates)
            self._apply_row(row, key, task)
            self._record(session, key, task)
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
            query = session.query(VideoGenerationTaskModel).filter(VideoGenerationTaskModel.status.in_(list(statuses)))
            if owner_id:
                query = query.filter(VideoGenerationTaskModel.owner_id == owner_id)
            return int(query.count())
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

    def request_cancel(self, key: str) -> dict[str, Any] | None:
        session = self.Session()
        try:
            self._begin_write(session)
            row = session.query(VideoGenerationTaskModel).filter_by(key=key).with_for_update().one_or_none()
            if row is None:
                return None
            task = self._row_to_task(row) or {}
            if row.status not in {"queued", "running"}:
                return task
            task["cancel_requested"] = True
            pending = bool(task.get("upstream_task_id") or task.get("submission_state") == "submitting")
            task["cancellation_pending"] = pending
            if not pending:
                task.update(
                    status="canceled",
                    execution_token="",
                    progress="canceled",
                    completed_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                )
            task.update(updated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"), updated_ts=time.time())
            self._apply_row(row, key, task)
            self._record(session, key, task)
            session.commit()
            return task
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def cleanup_batch(self, handler, limit: int = 20) -> int:
        from datetime import timedelta
        completed = 0
        with self.Session() as session:
            keys = [key for (key,) in session.query(VideoGenerationCleanup.key).filter(
                VideoGenerationCleanup.next_attempt_ts <= datetime.now()).order_by(
                VideoGenerationCleanup.next_attempt_ts).limit(limit).all()]
        for key in keys:
            with self.Session() as session:
                self._begin_write(session)
                row = session.query(VideoGenerationCleanup).filter_by(key=key).with_for_update().one_or_none()
                if row is None or row.next_attempt_ts > datetime.now():
                    continue
                task = json.loads(row.task_json)
                row.attempts += 1
                row.next_attempt_ts = datetime.now() + timedelta(seconds=min(3600, 30 * 2 ** min(row.attempts, 7)))
                session.commit()
            try:
                handler(task)
            except Exception as exc:
                with self.Session() as session:
                    session.query(VideoGenerationCleanup).filter_by(key=key).update({"error": str(exc)[:1000]})
                    session.commit()
            else:
                with self.Session() as session:
                    session.query(VideoGenerationCleanup).filter_by(key=key).delete()
                    session.commit()
                completed += 1
        return completed

    def recover_task(self, key: str, cutoff: float) -> dict[str, Any] | None:
        with self.Session() as session:
            self._begin_write(session)
            row = session.query(VideoGenerationTaskModel).filter_by(key=key).with_for_update().one_or_none()
            if row is None or row.status != "running":
                return None
            task = self._row_to_task(row) or {}
            activity = float(task.get("heartbeat_ts") or task.get("updated_ts") or task.get("started_ts") or 0)
            if activity > cutoff:
                return None
            uncertain = task.get("submission_state") == "submitting" and not task.get("upstream_task_id")
            task.update(status="error" if uncertain else "queued", execution_token="",
                        reconciliation_required=bool(uncertain), updated_ts=time.time(),
                        updated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        progress="submission_uncertain" if uncertain else "recovering",
                        error="Submission outcome unknown; reconcile with provider before generating again." if uncertain else "")
            self._apply_row(row, key, task)
            self._record(session, key, task)
            session.commit()
            return task

