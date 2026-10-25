"""Shared sidebar configuration for every signed-in user."""

from __future__ import annotations

from datetime import datetime
import threading

from sqlalchemy import Column, DateTime, JSON, String, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from services.platform.enterprise_schema import resolve_enterprise_database_url


NavigationBase = declarative_base()
NAVIGATION_IDS = (
    "image", "video-generation", "audio-generation", "infinite-canvas",
    "prompt-templates", "image-library", "monitoring", "costs", "users",
    "generation-records",
)


class NavigationSettingsModel(NavigationBase):
    __tablename__ = "system_navigation_settings"

    id = Column(String(32), primary_key=True)
    items_json = Column(JSON, nullable=False)
    updated_by = Column(String(191), nullable=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.now)


class NavigationSettingsStore:
    def __init__(self, database_url: str | None = None) -> None:
        self.database_url = database_url
        self.engine = None
        self.Session = None
        self._lock = threading.Lock()

    def _session(self):
        if self.Session is None:
            with self._lock:
                if self.Session is None:
                    url = self.database_url or resolve_enterprise_database_url()
                    options = {"pool_pre_ping": True, "pool_recycle": 3600}
                    if url.startswith("sqlite"):
                        options["connect_args"] = {"check_same_thread": False}
                    self.engine = create_engine(url, **options)
                    NavigationBase.metadata.create_all(self.engine)
                    self.Session = sessionmaker(bind=self.engine)
        return self.Session()

    @staticmethod
    def defaults() -> list[dict[str, object]]:
        return [{"id": item_id, "visible": True} for item_id in NAVIGATION_IDS]

    def get(self) -> dict[str, object]:
        session = self._session()
        try:
            row = session.get(NavigationSettingsModel, "default")
            return {"items": list(row.items_json) if row else self.defaults()}
        finally:
            session.close()

    def update(self, items: list[dict[str, object]], *, updated_by: str) -> dict[str, object]:
        ids = [str(item.get("id") or "") for item in items]
        if len(ids) != len(NAVIGATION_IDS) or set(ids) != set(NAVIGATION_IDS):
            raise ValueError("导航项必须完整且不能重复")
        normalized = [{"id": item_id, "visible": bool(item["visible"])} for item_id, item in zip(ids, items)]
        if not any(item["visible"] for item in normalized if item["id"] not in {"monitoring", "costs", "users"}):
            raise ValueError("至少保留一个对成员可见的导航项")
        session = self._session()
        try:
            row = session.get(NavigationSettingsModel, "default")
            if row is None:
                row = NavigationSettingsModel(id="default", items_json=normalized)
                session.add(row)
            else:
                row.items_json = normalized
            row.updated_by = str(updated_by or "admin")[:191]
            row.updated_at = datetime.now()
            session.commit()
            return {"items": normalized}
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def close(self) -> None:
        if self.engine is not None:
            self.engine.dispose()
        self.engine = None
        self.Session = None


navigation_settings_store = NavigationSettingsStore()
