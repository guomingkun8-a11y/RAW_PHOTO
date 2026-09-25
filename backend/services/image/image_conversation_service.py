from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Column, DateTime, String, Text, and_, create_engine, desc, or_, text
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()

DEFAULT_DATABASE_URL = "mysql+pymysql://root:root@127.0.0.1:3306/raw_photo?charset=utf8mb4"
LONG_TEXT = Text().with_variant(LONGTEXT, "mysql")
INLINE_IMAGE_REMOTE_LIMIT = 2048
CONVERSATION_LIST_DEFAULT_LIMIT = 50
CONVERSATION_LIST_MAX_LIMIT = 200


class ImageConversationModel(Base):
    __tablename__ = "image_conversations"

    owner_id = Column(String(191), primary_key=True)
    id = Column(String(191), primary_key=True)
    title = Column(String(191), nullable=False, default="")
    payload_json = Column(LONG_TEXT, nullable=False)
    deleted_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)


def _database_url() -> str:
    return (
        os.getenv("IMAGE_CONVERSATION_DATABASE_URL")
        or os.getenv("IMAGE_LIBRARY_DATABASE_URL")
        or os.getenv("MYSQL_DATABASE_URL")
        or DEFAULT_DATABASE_URL
    )


def _clean(value: object, default: str = "") -> str:
    text_value = str(value if value is not None else default).strip()
    return text_value or default


def _owner_id(identity: dict[str, object]) -> str:
    return _clean(identity.get("id")) or "local-admin"


def _iso(value: datetime | None) -> str:
    return value.isoformat(timespec="seconds") if value else ""


def _parse_cursor_datetime(value: str) -> datetime:
    clean_value = _clean(value)
    if not clean_value:
        raise ValueError("conversation cursor timestamp is required")
    try:
        parsed = datetime.fromisoformat(clean_value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid conversation cursor timestamp") from exc
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _cursor_datetime(value: datetime | None) -> str:
    return value.isoformat(timespec="microseconds") if value else ""


def _payload_timestamp(payload: dict[str, Any]) -> float:
    value = _clean(payload.get("updatedAt"))
    if not value:
        return 0.0
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _normalize_payload(conversation_id: str, payload: dict[str, Any], *, title: str = "") -> dict[str, Any]:
    now = datetime.now().isoformat(timespec="seconds")
    normalized = dict(payload)
    normalized["id"] = conversation_id
    normalized["title"] = _clean(title) or _clean(normalized.get("title"))[:191] or "未命名任务"
    normalized["createdAt"] = _clean(normalized.get("createdAt")) or now
    normalized["updatedAt"] = _clean(normalized.get("updatedAt")) or now
    if not isinstance(normalized.get("turns"), list):
        normalized["turns"] = []
    return _slim_payload_for_storage(normalized)


def _is_image_data_url(value: str) -> bool:
    return value.lower().startswith("data:image/") and ";base64," in value[:128].lower()


def _slim_payload_for_storage(value: Any) -> Any:
    if isinstance(value, list):
        return [_slim_payload_for_storage(item) for item in value]
    if not isinstance(value, dict):
        return value

    has_url = bool(_clean(value.get("url")))
    result: dict[str, Any] = {}
    for key, child in value.items():
        clean_key = str(key)
        if clean_key == "b64_json":
            continue
        if (
            clean_key == "dataUrl"
            and isinstance(child, str)
            and _is_image_data_url(child)
            and (has_url or len(child) > INLINE_IMAGE_REMOTE_LIMIT)
        ):
            continue
        result[clean_key] = _slim_payload_for_storage(child)
    return result


def _payload_from_row(row: ImageConversationModel) -> dict[str, Any]:
    try:
        value = json.loads(row.payload_json or "{}")
    except Exception:
        value = {}
    if not isinstance(value, dict):
        value = {}
    value["id"] = row.id
    value["title"] = row.title
    value["createdAt"] = _clean(value.get("createdAt")) or _iso(row.created_at)
    value["updatedAt"] = _clean(value.get("updatedAt")) or _iso(row.updated_at)
    if not isinstance(value.get("turns"), list):
        value["turns"] = []
    return value


class ImageConversationService:
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
        if engine.dialect.name != "mysql":
            return
        statements = [
            "CREATE INDEX idx_image_conversations_owner_updated ON image_conversations (owner_id, deleted_at, updated_at)",
            "CREATE INDEX idx_image_conversations_owner_updated_id ON image_conversations (owner_id, deleted_at, updated_at, id)",
            "CREATE INDEX idx_image_conversations_owner_title ON image_conversations (owner_id, title)",
        ]
        with engine.begin() as connection:
            for statement in statements:
                try:
                    connection.execute(text(statement))
                except Exception:
                    pass

    def _session(self):
        if self.Session is None:
            self._init_engine()
        if self.Session is None:
            raise RuntimeError(f"image conversation database unavailable: {self._init_error}")
        return self.Session()

    def count_conversations(self, *, identity: dict[str, object]) -> int:
        owner_id = _owner_id(identity)
        session = self._session()
        try:
            return int(
                session.query(ImageConversationModel.id)
                .filter(
                    ImageConversationModel.owner_id == owner_id,
                    ImageConversationModel.deleted_at.is_(None),
                )
                .count()
            )
        finally:
            session.close()

    def list_conversations(
        self,
        *,
        identity: dict[str, object],
        limit: int = CONVERSATION_LIST_DEFAULT_LIMIT,
        cursor_at: str = "",
        cursor_id: str = "",
    ) -> dict[str, Any]:
        owner_id = _owner_id(identity)
        if bool(cursor_at) != bool(cursor_id):
            raise ValueError("cursorAt and cursorId must be provided together")
        cursor_datetime = _parse_cursor_datetime(cursor_at) if cursor_at else None
        clean_cursor_id = _clean(cursor_id)
        if clean_cursor_id and len(clean_cursor_id) > 191:
            raise ValueError("invalid conversation cursor id")
        page_limit = max(1, min(CONVERSATION_LIST_MAX_LIMIT, limit))
        session = self._session()
        try:
            query = session.query(ImageConversationModel).filter(
                ImageConversationModel.owner_id == owner_id,
                ImageConversationModel.deleted_at.is_(None),
            )
            if cursor_datetime is not None:
                query = query.filter(
                    or_(
                        ImageConversationModel.updated_at < cursor_datetime,
                        and_(
                            ImageConversationModel.updated_at == cursor_datetime,
                            ImageConversationModel.id < clean_cursor_id,
                        ),
                    )
                )
            rows = (
                query.order_by(desc(ImageConversationModel.updated_at), desc(ImageConversationModel.id))
                .limit(page_limit + 1)
                .all()
            )
            has_more = len(rows) > page_limit
            visible_rows = rows[:page_limit]
            last = visible_rows[-1] if visible_rows else None
            return {
                "items": [_payload_from_row(row) for row in visible_rows],
                "total": len(visible_rows),
                "limit": page_limit,
                "has_more": has_more,
                "next_cursor": (
                    {"updated_at": _cursor_datetime(last.updated_at), "id": last.id}
                    if has_more and last is not None
                    else None
                ),
            }
        finally:
            session.close()

    def upsert_conversation(
        self,
        *,
        identity: dict[str, object],
        conversation_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        owner_id = _owner_id(identity)
        clean_id = _clean(conversation_id)[:191]
        if not clean_id:
            raise ValueError("conversation id is required")
        normalized = _normalize_payload(clean_id, payload)
        title = _clean(normalized.get("title"))[:191] or "未命名任务"
        session = self._session()
        try:
            row = (
                session.query(ImageConversationModel)
                .filter(
                    ImageConversationModel.owner_id == owner_id,
                    ImageConversationModel.id == clean_id,
                )
                .with_for_update()
                .one_or_none()
            )
            if row is None:
                row = ImageConversationModel(owner_id=owner_id, id=clean_id)
                session.add(row)
            else:
                current = _payload_from_row(row)
                if _payload_timestamp(normalized) < _payload_timestamp(current):
                    return current
            row.title = title
            row.payload_json = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
            row.deleted_at = None
            row.updated_at = datetime.now()
            session.commit()
            return _payload_from_row(row)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def rename_conversation(
        self,
        *,
        identity: dict[str, object],
        conversation_id: str,
        title: str,
    ) -> dict[str, Any] | None:
        owner_id = _owner_id(identity)
        clean_title = _clean(title)[:191]
        if not clean_title:
            raise ValueError("title is required")
        session = self._session()
        try:
            row = (
                session.query(ImageConversationModel)
                .filter(
                    ImageConversationModel.owner_id == owner_id,
                    ImageConversationModel.id == _clean(conversation_id)[:191],
                    ImageConversationModel.deleted_at.is_(None),
                )
                .one_or_none()
            )
            if row is None:
                return None
            payload = _payload_from_row(row)
            payload["title"] = clean_title
            payload["updatedAt"] = datetime.now().isoformat(timespec="seconds")
            row.title = clean_title
            row.payload_json = json.dumps(_slim_payload_for_storage(payload), ensure_ascii=False, separators=(",", ":"))
            row.updated_at = datetime.now()
            session.commit()
            return _payload_from_row(row)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def delete_conversation(self, *, identity: dict[str, object], conversation_id: str) -> bool:
        owner_id = _owner_id(identity)
        session = self._session()
        try:
            row = (
                session.query(ImageConversationModel)
                .filter(
                    ImageConversationModel.owner_id == owner_id,
                    ImageConversationModel.id == _clean(conversation_id)[:191],
                    ImageConversationModel.deleted_at.is_(None),
                )
                .one_or_none()
            )
            if row is None:
                return False
            row.deleted_at = datetime.now()
            row.updated_at = datetime.now()
            session.commit()
            return True
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def clear_conversations(self, *, identity: dict[str, object]) -> int:
        owner_id = _owner_id(identity)
        session = self._session()
        try:
            rows = (
                session.query(ImageConversationModel)
                .filter(
                    ImageConversationModel.owner_id == owner_id,
                    ImageConversationModel.deleted_at.is_(None),
                )
                .all()
            )
            now = datetime.now()
            for row in rows:
                row.deleted_at = now
                row.updated_at = now
            session.commit()
            return len(rows)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


image_conversation_service = ImageConversationService()
