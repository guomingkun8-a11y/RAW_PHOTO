from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Column, DateTime, Integer, String, Text, create_engine, desc, text
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()

DEFAULT_DATABASE_URL = "mysql+pymysql://root:root@127.0.0.1:3306/raw_photo?charset=utf8mb4"
ANNOUNCEMENT_TYPES = {"info", "success", "warning", "error"}


def _pk_type():
    return BigInteger().with_variant(Integer, "sqlite")


def _database_url() -> str:
    return (
        os.getenv("IMAGE_LIBRARY_DATABASE_URL")
        or os.getenv("MYSQL_DATABASE_URL")
        or DEFAULT_DATABASE_URL
    )


def _now() -> datetime:
    return datetime.now()


def _clean(value: object, default: str = "") -> str:
    text = str(value if value is not None else default).strip()
    return text or default


def _enabled_value(value: object, current: bool = True) -> bool:
    if value is None:
        return current
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}
    return bool(value)


def _announcement_type(value: object) -> str:
    normalized = _clean(value, "info").lower()
    return normalized if normalized in ANNOUNCEMENT_TYPES else "info"


class SystemAnnouncementModel(Base):
    __tablename__ = "system_announcements"

    id = Column(_pk_type(), primary_key=True, autoincrement=True)
    title = Column(String(191), nullable=False)
    content = Column(Text, nullable=False)
    announcement_type = Column(String(32), nullable=False, default="info")
    enabled = Column(Integer, nullable=False, default=1)
    created_by = Column(String(191), nullable=False, default="system")
    created_at = Column(DateTime, nullable=False, default=_now)
    updated_at = Column(DateTime, nullable=False, default=_now, onupdate=_now)


class SystemAnnouncementService:
    def __init__(self, database_url: str | None = None):
        self.database_url = database_url or _database_url()
        self.engine = None
        self.Session = None
        self._init_error = ""
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
        with engine.begin() as connection:
            for statement in (
                "CREATE INDEX idx_system_announcements_enabled_created ON system_announcements (enabled, created_at)",
                "CREATE INDEX idx_system_announcements_created_id ON system_announcements (created_at, id)",
            ):
                try:
                    connection.execute(text(statement))
                except Exception:
                    pass

    def _session(self):
        if self.Session is None:
            self._init_engine()
        if self.Session is None:
            raise RuntimeError(f"announcement database unavailable: {self._init_error}")
        return self.Session()

    def close(self) -> None:
        if self.engine is not None:
            self.engine.dispose()

    def list_announcements(self, *, include_disabled: bool = False, limit: int = 5) -> dict[str, Any]:
        safe_limit = min(50, max(1, int(limit or 5)))
        session = self._session()
        try:
            query = session.query(SystemAnnouncementModel)
            if not include_disabled:
                query = query.filter(SystemAnnouncementModel.enabled == 1)
            rows = query.order_by(desc(SystemAnnouncementModel.created_at), desc(SystemAnnouncementModel.id)).limit(safe_limit).all()
            return {"items": [self._public_item(row) for row in rows], "total": len(rows)}
        finally:
            session.close()

    def create_announcement(self, *, title: str, content: str, announcement_type: str = "info", enabled: bool = True, created_by: str = "system") -> dict[str, object]:
        title = _clean(title)
        content = _clean(content)
        if not title:
            raise ValueError("公告标题不能为空")
        if not content:
            raise ValueError("公告内容不能为空")
        now = _now()
        session = self._session()
        try:
            row = SystemAnnouncementModel(
                title=title,
                content=content,
                announcement_type=_announcement_type(announcement_type),
                enabled=1 if _enabled_value(enabled, True) else 0,
                created_by=_clean(created_by, "system"),
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return self._public_item(row)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def update_announcement(self, announcement_id: int, updates: dict[str, object]) -> dict[str, object] | None:
        session = self._session()
        try:
            row = session.query(SystemAnnouncementModel).filter(SystemAnnouncementModel.id == int(announcement_id)).one_or_none()
            if row is None:
                return None
            if "title" in updates:
                title = _clean(updates.get("title"))
                if not title:
                    raise ValueError("公告标题不能为空")
                row.title = title
            if "content" in updates:
                content = _clean(updates.get("content"))
                if not content:
                    raise ValueError("公告内容不能为空")
                row.content = content
            if "type" in updates:
                row.announcement_type = _announcement_type(updates.get("type"))
            if "enabled" in updates:
                row.enabled = 1 if _enabled_value(updates.get("enabled"), row.enabled == 1) else 0
            row.updated_at = _now()
            session.commit()
            session.refresh(row)
            return self._public_item(row)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def disable_announcement(self, announcement_id: int) -> dict[str, object] | None:
        return self.update_announcement(announcement_id, {"enabled": False})

    @staticmethod
    def _public_item(row: SystemAnnouncementModel) -> dict[str, object]:
        return {
            "id": row.id,
            "title": row.title,
            "content": row.content,
            "type": row.announcement_type,
            "enabled": row.enabled == 1,
            "created_by": row.created_by,
            "created_at": row.created_at.strftime("%Y-%m-%d %H:%M:%S") if row.created_at else "",
            "updated_at": row.updated_at.strftime("%Y-%m-%d %H:%M:%S") if row.updated_at else "",
        }


system_announcement_service = SystemAnnouncementService()
