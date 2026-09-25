from __future__ import annotations

import json
import os
import random
import re
import threading
import time
from datetime import datetime
from typing import Any

from sqlalchemy import Column, DateTime, Integer, String, Text, create_engine, desc, text
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import declarative_base, sessionmaker


Base = declarative_base()
LONG_TEXT = Text().with_variant(LONGTEXT, "mysql")
DEFAULT_DATABASE_URL = "mysql+pymysql://root:root@127.0.0.1:3306/raw_photo?charset=utf8mb4"
MAX_WORKFLOW_BYTES = 8 * 1024 * 1024
MAX_WORKFLOW_NODES = 300
MAX_WORKFLOW_CONNECTIONS = 1_000
SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
MYSQL_RETRYABLE_CODES = {1205, 1213}


class CanvasWorkflowModel(Base):
    __tablename__ = "canvas_workflows"

    owner_id = Column(String(191), primary_key=True)
    id = Column(String(191), primary_key=True)
    title = Column(String(191), nullable=False, default="")
    cover_url = Column(Text, nullable=True)
    node_count = Column(Integer, nullable=False, default=0)
    revision = Column(Integer, nullable=False, default=1)
    payload_json = Column(LONG_TEXT, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)


class CanvasWorkflowConflictError(RuntimeError):
    """Raised when a stale browser tab attempts to overwrite a newer workflow."""


def _database_url() -> str:
    return (
        os.getenv("CANVAS_WORKFLOW_DATABASE_URL")
        or os.getenv("GMKRAW_DATABASE_URL")
        or os.getenv("IMAGE_CONVERSATION_DATABASE_URL")
        or os.getenv("IMAGE_LIBRARY_DATABASE_URL")
        or os.getenv("MYSQL_DATABASE_URL")
        or DEFAULT_DATABASE_URL
    )


def _clean(value: object, default: str = "") -> str:
    result = str(value if value is not None else default).strip()
    return result or default


def _owner_id(identity: dict[str, object]) -> str:
    return _clean(identity.get("id")) or "local-admin"


def _workflow_id(value: object) -> str:
    workflow_id = _clean(value)[:191]
    if not workflow_id or not SAFE_ID.fullmatch(workflow_id):
        raise ValueError("workflow id is invalid")
    return workflow_id


def _iso(value: datetime | None) -> str:
    return value.isoformat(timespec="seconds") if value else ""


def _mysql_error_code(exc: BaseException) -> int | None:
    current: BaseException | None = exc
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        for value in getattr(current, "args", ()):
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
        nested = getattr(current, "orig", None) or getattr(current, "__cause__", None)
        current = nested if isinstance(nested, BaseException) else None
    return None


def _is_embedded_media(value: object) -> bool:
    return isinstance(value, str) and value.lower().startswith("data:") and ";base64," in value[:160].lower()


def _slim_payload(value: Any, *, parent: dict[str, Any] | None = None) -> Any:
    if isinstance(value, list):
        return ["" if _is_embedded_media(item) else _slim_payload(item) for item in value]
    if not isinstance(value, dict):
        return value

    remote_url = _clean(value.get("url"))
    result: dict[str, Any] = {}
    for raw_key, child in value.items():
        key = str(raw_key)
        if _is_embedded_media(child):
            if key == "previewUrl" and remote_url.startswith(("http://", "https://", "/")):
                result[key] = remote_url
            else:
                result[key] = ""
            continue
        result[key] = _slim_payload(child, parent=value)
    return result


def _cover_url(nodes: list[Any]) -> str:
    for raw_node in reversed(nodes):
        if not isinstance(raw_node, dict):
            continue
        for key in ("resultUrl", "inputUrl"):
            value = _clean(raw_node.get(key))
            if value and not _is_embedded_media(value):
                return value
        result_urls = raw_node.get("resultUrls")
        if isinstance(result_urls, list):
            for raw_url in reversed(result_urls):
                value = _clean(raw_url)
                if value and not _is_embedded_media(value):
                    return value
        references = raw_node.get("references")
        if isinstance(references, list):
            for reference in reversed(references):
                if not isinstance(reference, dict):
                    continue
                value = _clean(reference.get("url"))
                if value and not _is_embedded_media(value):
                    return value
    return ""


def _normalize_workflow(workflow_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now().isoformat(timespec="seconds")
    normalized = dict(payload)
    nodes = normalized.get("nodes")
    connections = normalized.get("connections")
    if not isinstance(nodes, list):
        raise ValueError("workflow nodes must be a list")
    if not isinstance(connections, list):
        connections = []
    if len(nodes) > MAX_WORKFLOW_NODES:
        raise ValueError(f"workflow supports at most {MAX_WORKFLOW_NODES} nodes")
    if len(connections) > MAX_WORKFLOW_CONNECTIONS:
        raise ValueError(f"workflow supports at most {MAX_WORKFLOW_CONNECTIONS} connections")
    normalized["id"] = workflow_id
    normalized["title"] = _clean(normalized.get("title"))[:191] or "未命名画布"
    normalized["nodes"] = nodes
    normalized["connections"] = connections
    normalized["createdAt"] = _clean(normalized.get("createdAt")) or now
    normalized["updatedAt"] = now
    messages = normalized.get("assistantMessages")
    normalized["assistantMessages"] = messages[-100:] if isinstance(messages, list) else []
    slimmed = _slim_payload(normalized)
    encoded = json.dumps(slimmed, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_WORKFLOW_BYTES:
        raise ValueError("workflow is too large to save")
    return slimmed


def _workflow_from_row(row: CanvasWorkflowModel) -> dict[str, Any]:
    try:
        payload = json.loads(row.payload_json or "{}")
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    payload["id"] = row.id
    payload["title"] = row.title
    payload["coverUrl"] = _clean(payload.get("coverUrl")) or _clean(row.cover_url)
    payload["createdAt"] = _clean(payload.get("createdAt")) or _iso(row.created_at)
    payload["updatedAt"] = _iso(row.updated_at) or _clean(payload.get("updatedAt"))
    payload["revision"] = int(row.revision or 1)
    payload["nodes"] = payload.get("nodes") if isinstance(payload.get("nodes"), list) else []
    payload["connections"] = payload.get("connections") if isinstance(payload.get("connections"), list) else []
    payload["assistantMessages"] = payload.get("assistantMessages") if isinstance(payload.get("assistantMessages"), list) else []
    return payload


def _summary_from_row(row: CanvasWorkflowModel) -> dict[str, Any]:
    return {
        "id": row.id,
        "title": row.title,
        "coverUrl": _clean(row.cover_url),
        "nodeCount": int(row.node_count or 0),
        "createdAt": _iso(row.created_at),
        "updatedAt": _iso(row.updated_at),
    }


class CanvasWorkflowService:
    def __init__(self, database_url: str | None = None):
        self.database_url = database_url or _database_url()
        self.engine = None
        self.Session = None
        self._init_error = ""
        self._init_lock = threading.Lock()

    def _init_engine(self) -> None:
        with self._init_lock:
            if self.engine is not None and self.Session is not None:
                return
            try:
                engine_options: dict[str, Any] = {"pool_pre_ping": True, "pool_recycle": 3600}
                if self.database_url.startswith("sqlite"):
                    engine_options["connect_args"] = {"check_same_thread": False, "timeout": 30}
                else:
                    engine_options["pool_size"] = max(1, int(os.getenv("CANVAS_WORKFLOW_DB_POOL_SIZE", "5")))
                    engine_options["max_overflow"] = max(0, int(os.getenv("CANVAS_WORKFLOW_DB_MAX_OVERFLOW", "10")))
                engine = create_engine(self.database_url, **engine_options)
                Base.metadata.create_all(engine)
                self._ensure_indexes(engine)
                self.engine = engine
                self.Session = sessionmaker(bind=engine)
                self._init_error = ""
            except Exception as exc:
                self.engine = None
                self.Session = None
                self._init_error = str(exc)

    @staticmethod
    def _ensure_indexes(engine) -> None:
        statements = [
            "CREATE INDEX idx_canvas_workflows_owner_updated ON canvas_workflows (owner_id, updated_at)",
            "CREATE INDEX idx_canvas_workflows_owner_title ON canvas_workflows (owner_id, title)",
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
            raise RuntimeError(f"canvas workflow database unavailable: {self._init_error}")
        return self.Session()

    def _write(self, operation):
        if self.Session is None:
            self._init_engine()
        if self.Session is None or self.engine is None:
            raise RuntimeError(f"canvas workflow database unavailable: {self._init_error}")
        attempts = 3 if self.engine.dialect.name == "mysql" else 1
        for attempt in range(attempts):
            session = self.Session()
            try:
                result = operation(session)
                session.commit()
                return result
            except Exception as exc:
                session.rollback()
                if _mysql_error_code(exc) not in MYSQL_RETRYABLE_CODES or attempt + 1 >= attempts:
                    raise
                time.sleep(random.uniform(0.025, 0.075) * (2**attempt))
            finally:
                session.close()
        raise RuntimeError("canvas workflow transaction retry exhausted")

    def close(self) -> None:
        if self.engine is not None:
            self.engine.dispose()
        self.engine = None
        self.Session = None

    def list_workflows(self, *, identity: dict[str, object], limit: int = 100) -> dict[str, Any]:
        owner_id = _owner_id(identity)
        page_limit = max(1, min(500, int(limit or 100)))
        session = self._session()
        try:
            query = session.query(CanvasWorkflowModel).filter(CanvasWorkflowModel.owner_id == owner_id)
            total = query.count()
            rows = (
                query
                .order_by(desc(CanvasWorkflowModel.updated_at), desc(CanvasWorkflowModel.created_at))
                .limit(page_limit)
                .all()
            )
            return {"items": [_summary_from_row(row) for row in rows], "total": total}
        finally:
            session.close()

    def get_workflow(self, *, identity: dict[str, object], workflow_id: str) -> dict[str, Any] | None:
        owner_id = _owner_id(identity)
        session = self._session()
        try:
            row = (
                session.query(CanvasWorkflowModel)
                .filter(
                    CanvasWorkflowModel.owner_id == owner_id,
                    CanvasWorkflowModel.id == _workflow_id(workflow_id),
                )
                .one_or_none()
            )
            return _workflow_from_row(row) if row is not None else None
        finally:
            session.close()

    def upsert_workflow(
        self,
        *,
        identity: dict[str, object],
        workflow_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        owner_id = _owner_id(identity)
        clean_id = _workflow_id(workflow_id)
        raw_revision = payload.get("revision")
        try:
            expected_revision = int(raw_revision) if raw_revision is not None else None
        except (TypeError, ValueError) as exc:
            raise ValueError("workflow revision is invalid") from exc
        if expected_revision is not None and expected_revision < 0:
            raise ValueError("workflow revision is invalid")
        normalized = _normalize_workflow(clean_id, payload)
        nodes = normalized["nodes"]
        cover_url = _cover_url(nodes)
        normalized["coverUrl"] = cover_url
        encoded_payload = lambda: json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))

        if expected_revision in (None, 0):
            def create(session):
                normalized["revision"] = 1
                now = datetime.now()
                row = CanvasWorkflowModel(
                    owner_id=owner_id,
                    id=clean_id,
                    title=normalized["title"],
                    cover_url=cover_url or None,
                    node_count=len(nodes),
                    revision=1,
                    payload_json=encoded_payload(),
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
                try:
                    session.flush()
                except IntegrityError as exc:
                    if _mysql_error_code(exc) == 1062 or self.database_url.startswith("sqlite"):
                        raise CanvasWorkflowConflictError("画布已在其他页面更新，请重新打开后再编辑") from exc
                    raise
                return _workflow_from_row(row)

            return self._write(create)

        def update(session):
            row = (
                session.query(CanvasWorkflowModel)
                .filter(
                    CanvasWorkflowModel.owner_id == owner_id,
                    CanvasWorkflowModel.id == clean_id,
                )
                .with_for_update()
                .one_or_none()
            )
            if row is None:
                raise CanvasWorkflowConflictError("画布已被删除或更新，请重新打开后再编辑")
            current_revision = int(row.revision or 1)
            if expected_revision != current_revision:
                raise CanvasWorkflowConflictError("画布已在其他页面更新，请重新打开后再编辑")
            normalized["revision"] = current_revision + 1
            row.title = normalized["title"]
            row.cover_url = cover_url or None
            row.node_count = len(nodes)
            row.revision = normalized["revision"]
            row.payload_json = encoded_payload()
            row.updated_at = datetime.now()
            session.flush()
            return _workflow_from_row(row)

        return self._write(update)

    def delete_workflow(self, *, identity: dict[str, object], workflow_id: str) -> bool:
        deleted_ids = self.delete_workflows(
            identity=identity,
            workflow_ids=[workflow_id],
        )
        return bool(deleted_ids)

    def delete_workflows(
        self,
        *,
        identity: dict[str, object],
        workflow_ids: list[str],
    ) -> list[str]:
        owner_id = _owner_id(identity)
        clean_ids: list[str] = []
        seen: set[str] = set()
        for value in workflow_ids:
            clean_id = _workflow_id(value)
            if clean_id in seen:
                continue
            seen.add(clean_id)
            clean_ids.append(clean_id)
        if not clean_ids:
            raise ValueError("at least one workflow id is required")
        if len(clean_ids) > 100:
            raise ValueError("at most 100 workflows can be deleted at once")

        session = self._session()
        try:
            owned_rows = (
                session.query(CanvasWorkflowModel.id)
                .filter(
                    CanvasWorkflowModel.owner_id == owner_id,
                    CanvasWorkflowModel.id.in_(clean_ids),
                )
                .all()
            )
            owned_ids = {str(row[0]) for row in owned_rows}
            deleted_ids = [workflow_id for workflow_id in clean_ids if workflow_id in owned_ids]
            if deleted_ids:
                (
                    session.query(CanvasWorkflowModel)
                    .filter(
                        CanvasWorkflowModel.owner_id == owner_id,
                        CanvasWorkflowModel.id.in_(deleted_ids),
                    )
                    .delete(synchronize_session=False)
                )
            session.commit()
            return deleted_ids
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


canvas_workflow_service = CanvasWorkflowService()
