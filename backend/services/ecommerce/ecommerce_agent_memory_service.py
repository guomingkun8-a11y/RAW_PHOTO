from __future__ import annotations

import json
import hashlib
import re
from collections import OrderedDict
from datetime import datetime, timedelta
from typing import Any, Mapping

from sqlalchemy import (
    and_,
    BigInteger,
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    delete,
    desc,
    func,
    or_,
)
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import declarative_base, sessionmaker

from services.platform.config import config as _config
from services.platform.enterprise_schema import resolve_enterprise_database_url
from services.ecommerce.professional_knowledge_service import (
    embed_professional_texts,
    professional_cosine_similarity,
    professional_embedding_model,
    professional_query_embedding,
    professional_retrieval_terms,
)
from services.ecommerce.ecommerce_agent_memory_policy import PROJECT_MEMORY_CATEGORIES
from services.ecommerce import professional_memory_index


Base = declarative_base()
LONG_TEXT = Text().with_variant(LONGTEXT, "mysql")
MEMORY_STATUS_ACTIVE = "active"
MEMORY_STATUS_PENDING_REVIEW = "pending_review"
MEMORY_STATUS_REJECTED = "rejected"
MEMORY_STATUS_SUPERSEDED = "superseded"
MEMORY_RETRIEVAL_MIN_CONFIDENCE = 0.75


class ProfessionalAgentConversationModel(Base):
    __tablename__ = "professional_agent_conversations"

    owner_id = Column(String(191), primary_key=True)
    conversation_id = Column(String(191), primary_key=True)
    active_run_id = Column(String(191), nullable=False, default="")
    latest_turn_id = Column(String(191), nullable=False, default="")
    status = Column(String(40), nullable=False, default="active")
    deleted_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)


class ProfessionalAgentMessageModel(Base):
    __tablename__ = "professional_agent_messages"
    __table_args__ = (
        UniqueConstraint("owner_id", "conversation_id", "message_key", name="uq_prof_agent_message_key"),
    )

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    owner_id = Column(String(191), nullable=False)
    conversation_id = Column(String(191), nullable=False)
    run_id = Column(String(191), nullable=False, default="")
    turn_id = Column(String(191), nullable=False, default="")
    message_key = Column(String(191), nullable=False)
    role = Column(String(24), nullable=False)
    message_type = Column(String(40), nullable=False)
    tool_name = Column(String(120), nullable=False, default="")
    tool_call_id = Column(String(191), nullable=False, default="")
    content_json = Column(LONG_TEXT, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.now)


class ProfessionalAgentStateSnapshotModel(Base):
    __tablename__ = "professional_agent_state_snapshots"

    owner_id = Column(String(191), primary_key=True)
    conversation_id = Column(String(191), primary_key=True)
    state_key = Column(String(120), primary_key=True)
    payload_json = Column(LONG_TEXT, nullable=False)
    updated_at = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)


class ProfessionalAgentMemoryChunkModel(Base):
    __tablename__ = "professional_agent_memory_chunks"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "conversation_id",
            "message_id",
            "chunk_index",
            name="uq_prof_agent_memory_chunk",
        ),
    )

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    owner_id = Column(String(191), nullable=False)
    conversation_id = Column(String(191), nullable=False)
    message_id = Column(BigInteger().with_variant(Integer, "sqlite"), nullable=False)
    chunk_index = Column(Integer, nullable=False, default=0)
    role = Column(String(24), nullable=False)
    message_type = Column(String(40), nullable=False)
    search_text = Column(LONG_TEXT, nullable=False)
    content_hash = Column(String(64), nullable=False)
    embedding_json = Column(LONG_TEXT, nullable=True)
    embedding_model = Column(String(191), nullable=False, default="")
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)


class ProfessionalAgentLongTermMemoryModel(Base):
    """Canonical durable memory. Content and lifecycle live in SQL; vectors are an index."""

    __tablename__ = "professional_agent_long_term_memories"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "scope",
            "scope_id",
            "memory_key",
            name="uq_prof_agent_long_term_memory_key",
        ),
    )

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    owner_id = Column(String(191), nullable=False)
    scope = Column(String(24), nullable=False, default="user")
    scope_id = Column(String(191), nullable=False)
    memory_key = Column(String(191), nullable=False)
    category = Column(String(48), nullable=False, default="note")
    content = Column(LONG_TEXT, nullable=False)
    source_conversation_id = Column(String(191), nullable=False, default="")
    source_run_id = Column(String(191), nullable=False, default="")
    source_message_ids = Column(LONG_TEXT, nullable=False, default="[]")
    metadata_json = Column(LONG_TEXT, nullable=False, default="{}")
    content_hash = Column(String(64), nullable=False)
    embedding_json = Column(LONG_TEXT, nullable=True)
    embedding_model = Column(String(191), nullable=False, default="")
    confidence = Column(Float, nullable=False, default=0.5)
    confirmed = Column(Integer, nullable=False, default=0)
    status = Column(String(24), nullable=False, default="active")
    supersedes_id = Column(Integer, nullable=True)
    retrieval_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)
    last_used_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)


class ProfessionalAgentMemoryDigestModel(Base):
    """Daily summaries and Deep Dream output kept outside retrieval-heavy memory."""

    __tablename__ = "professional_agent_memory_digests"
    __table_args__ = (
        UniqueConstraint("owner_id", "digest_date", "digest_type", name="uq_prof_agent_memory_digest"),
    )

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    owner_id = Column(String(191), nullable=False)
    digest_date = Column(String(32), nullable=False)
    digest_type = Column(String(24), nullable=False, default="daily")
    content = Column(LONG_TEXT, nullable=False)
    source_hash = Column(String(64), nullable=False, default="")
    metadata_json = Column(LONG_TEXT, nullable=False, default="{}")
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)


class ProfessionalAgentMemoryJobModel(Base):
    """Durable memory work queue, claimed by one Agent worker at a time."""

    __tablename__ = "professional_agent_memory_jobs"

    job_id = Column(String(191), primary_key=True)
    owner_id = Column(String(191), nullable=False)
    conversation_id = Column(String(191), nullable=False, default="")
    run_id = Column(String(191), nullable=False, default="")
    job_type = Column(String(24), nullable=False, default="conversation")
    status = Column(String(24), nullable=False, default="pending")
    attempts = Column(Integer, nullable=False, default=0)
    available_at = Column(DateTime, nullable=False, default=datetime.now)
    locked_at = Column(DateTime, nullable=True)
    locked_by = Column(String(191), nullable=False, default="")
    error = Column(LONG_TEXT, nullable=False, default="")
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)


class ProfessionalAgentRunModel(Base):
    __tablename__ = "professional_agent_runs"

    run_id = Column(String(191), primary_key=True)
    owner_id = Column(String(191), nullable=False)
    conversation_id = Column(String(191), nullable=False)
    turn_id = Column(String(191), nullable=False, default="")
    agent_name = Column(String(120), nullable=False)
    status = Column(String(40), nullable=False, default="pending")
    request_json = Column(LONG_TEXT, nullable=False)
    metadata_json = Column(LONG_TEXT, nullable=False)
    result_json = Column(LONG_TEXT, nullable=False)
    error = Column(LONG_TEXT, nullable=False, default="")
    max_steps = Column(Integer, nullable=False, default=8)
    started_at = Column(String(80), nullable=False, default="")
    finished_at = Column(String(80), nullable=False, default="")
    duration_ms = Column(Integer, nullable=True)
    tool_calls = Column(Integer, nullable=False, default=0)
    cancel_requested = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)


class ProfessionalAgentRunEventModel(Base):
    __tablename__ = "professional_agent_run_events"
    __table_args__ = (
        UniqueConstraint("run_id", "sequence", name="uq_prof_agent_run_event_sequence"),
    )

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    run_id = Column(String(191), nullable=False)
    sequence = Column(Integer, nullable=False)
    event_type = Column(String(120), nullable=False)
    timestamp = Column(String(80), nullable=False)
    payload_json = Column(LONG_TEXT, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.now)


class ProfessionalAgentConversationBusy(RuntimeError):
    pass


_SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "access_token",
    "refresh_token",
    "password",
    "secret",
}


def _clean(value: object, default: str = "", limit: int = 12000) -> str:
    result = str(value if value is not None else default).strip()
    return (result or default)[:max(1, int(limit))]


def _slim_for_storage(value: Any, *, depth: int = 0) -> Any:
    if depth >= 12:
        return "[depth limit]"
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_key, child in value.items():
            key = str(raw_key)
            normalized = key.lower().replace("-", "_")
            if normalized in _SENSITIVE_KEYS or normalized.endswith(("_api_key", "_secret", "_password", "_token")):
                continue
            if normalized in {"b64_json", "data_url", "dataurl"}:
                continue
            result[key] = _slim_for_storage(child, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple, set)):
        return [_slim_for_storage(item, depth=depth + 1) for item in value]
    if isinstance(value, bytes):
        return f"[binary omitted: {len(value)} bytes]"
    if isinstance(value, str):
        if value.lower().startswith("data:image/") and ";base64," in value[:128].lower():
            return "[image data omitted]"
        return value[:12000]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:12000]


def _dump(value: Any) -> str:
    return json.dumps(_slim_for_storage(value), ensure_ascii=False, separators=(",", ":"))


def _load(value: str, default: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _message_search_text(value: Any) -> str:
    preferred_keys = (
        "message",
        "assistantMessage",
        "title",
        "summary",
        "decisionSummary",
        "rewrittenInstruction",
        "creativeBrief",
        "productProfile",
        "proposal",
        "result",
    )
    parts: list[str] = []

    def collect(item: Any, depth: int = 0) -> None:
        if depth > 4 or sum(len(part) for part in parts) >= 12000:
            return
        if isinstance(item, str):
            text = re.sub(r"\s+", " ", item).strip()
            if text and not text.startswith("data:image/"):
                parts.append(text[:4000])
        elif isinstance(item, Mapping):
            for key in preferred_keys:
                if key in item:
                    collect(item[key], depth + 1)
        elif isinstance(item, (list, tuple)):
            for child in item[:20]:
                collect(child, depth + 1)

    collect(value)
    return re.sub(r"\s+", " ", " ".join(parts)).strip()[:12000]


def _chunk_search_text(text: str, *, max_chars: int = 1800, overlap: int = 180) -> list[str]:
    if not text:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + max_chars)
        if end < len(text):
            split_at = max(text.rfind("。", start, end), text.rfind("；", start, end))
            if split_at > start + max_chars // 2:
                end = split_at + 1
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = max(start + 1, end - overlap)
    return chunks


class EcommerceAgentMemoryService:
    """Persistent professional-mode memory isolated by owner and conversation."""

    def __init__(self, database_url: str | None = None) -> None:
        self.database_url = _clean(database_url)
        self.engine = None
        self.Session = None
        self._init_error = ""
        if self.database_url:
            self._init_engine()

    def _resolved_database_url(self) -> str:
        return self.database_url or resolve_enterprise_database_url()

    def _init_engine(self) -> None:
        try:
            self.database_url = self._resolved_database_url()
            engine = create_engine(self.database_url, pool_pre_ping=True, pool_recycle=3600)
            Base.metadata.create_all(engine)
            self.engine = engine
            self.Session = sessionmaker(bind=engine)
            self._init_error = ""
        except Exception as exc:
            self.engine = None
            self.Session = None
            self._init_error = str(exc)

    def _session(self):
        if self.Session is None:
            self._init_engine()
        if self.Session is None:
            raise RuntimeError(f"professional agent memory database unavailable: {self._init_error}")
        return self.Session()

    def ensure_ready(self) -> None:
        session = self._session()
        session.close()

    @staticmethod
    def _normalize_memory_scope(scope: object, scope_id: object, *, owner_id: str, conversation_id: str = "") -> tuple[str, str]:
        clean_scope = _clean(scope, "user")[:24].lower()
        if clean_scope not in {"user", "brand", "project", "conversation"}:
            clean_scope = "user"
        fallback = conversation_id if clean_scope == "conversation" else owner_id
        clean_scope_id = _clean(scope_id, fallback)[:191]
        return clean_scope, clean_scope_id

    @staticmethod
    def _memory_key(content: str, category: str, scope: str, scope_id: str, key: object = "") -> str:
        explicit = _clean(key)[:191]
        if explicit:
            return explicit
        digest = hashlib.sha256(f"{scope}:{scope_id}:{category}:{content}".encode("utf-8")).hexdigest()[:32]
        return f"{category}:{digest}"

    def upsert_long_term_memory(
        self,
        *,
        owner_id: str,
        content: str,
        category: str = "note",
        scope: str = "user",
        scope_id: str = "",
        memory_key: str = "",
        source_conversation_id: str = "",
        source_run_id: str = "",
        source_message_ids: list[object] | None = None,
        metadata: Mapping[str, Any] | None = None,
        confidence: float = 0.5,
        confirmed: bool = False,
        status: str = MEMORY_STATUS_ACTIVE,
        expires_at: datetime | None = None,
    ) -> dict[str, Any]:
        owner = _clean(owner_id, "anonymous")[:191]
        text_value = re.sub(r"\s+", " ", _clean(content, limit=12000)).strip()
        if not text_value:
            raise ValueError("memory content is required")
        conversation = _clean(source_conversation_id)[:191]
        clean_scope, clean_scope_id = self._normalize_memory_scope(
            scope,
            scope_id,
            owner_id=owner,
            conversation_id=conversation,
        )
        clean_category = _clean(category, "note")[:48].lower()
        if clean_category in PROJECT_MEMORY_CATEGORIES and conversation:
            clean_scope, clean_scope_id = "project", conversation
        key = self._memory_key(text_value, clean_category, clean_scope, clean_scope_id, memory_key)
        content_hash = hashlib.sha256(text_value.encode("utf-8")).hexdigest()
        confidence_value = max(0.0, min(1.0, float(confidence or 0.5)))
        confirmed_value = bool(confirmed)
        requested_status = _clean(status, MEMORY_STATUS_ACTIVE)[:24].lower()
        if requested_status not in {MEMORY_STATUS_ACTIVE, MEMORY_STATUS_PENDING_REVIEW}:
            requested_status = MEMORY_STATUS_PENDING_REVIEW if not confirmed_value else MEMORY_STATUS_ACTIVE
        if confirmed_value:
            requested_status = MEMORY_STATUS_ACTIVE
            confidence_value = max(0.9, confidence_value)
        elif requested_status == MEMORY_STATUS_ACTIVE:
            requested_status = MEMORY_STATUS_PENDING_REVIEW
        metadata_value = dict(metadata or {})
        session = self._session()
        result: dict[str, Any] | None = None
        should_index = False
        try:
            row = (
                session.query(ProfessionalAgentLongTermMemoryModel)
                .filter(
                    ProfessionalAgentLongTermMemoryModel.owner_id == owner,
                    ProfessionalAgentLongTermMemoryModel.scope == clean_scope,
                    ProfessionalAgentLongTermMemoryModel.scope_id == clean_scope_id,
                    ProfessionalAgentLongTermMemoryModel.memory_key == key,
                )
                .one_or_none()
            )
            supersedes_id: int | None = None
            if (
                row is not None
                and requested_status == MEMORY_STATUS_PENDING_REVIEW
                and bool(row.confirmed)
                and row.status == MEMORY_STATUS_ACTIVE
            ):
                if row.content_hash == content_hash:
                    return self._long_term_dict(row)
                supersedes_id = int(row.id)
                original_key = key
                key = f"{key[:166]}.candidate.{content_hash[:12]}"
                metadata_value.update({
                    "candidateForKey": original_key,
                    "candidateForMemoryId": supersedes_id,
                })
                row = (
                    session.query(ProfessionalAgentLongTermMemoryModel)
                    .filter(
                        ProfessionalAgentLongTermMemoryModel.owner_id == owner,
                        ProfessionalAgentLongTermMemoryModel.scope == clean_scope,
                        ProfessionalAgentLongTermMemoryModel.scope_id == clean_scope_id,
                        ProfessionalAgentLongTermMemoryModel.memory_key == key,
                    )
                    .one_or_none()
                )
            if row is None:
                row = ProfessionalAgentLongTermMemoryModel(
                    owner_id=owner,
                    scope=clean_scope,
                    scope_id=clean_scope_id,
                    memory_key=key,
                    category=clean_category,
                    content=text_value,
                    created_at=datetime.now(),
                )
                session.add(row)
            content_changed = row.content_hash != content_hash
            row.category = clean_category
            row.content = text_value
            row.source_conversation_id = conversation
            row.source_run_id = _clean(source_run_id)[:191]
            row.source_message_ids = _dump(list(source_message_ids or []))
            row.metadata_json = _dump(metadata_value)
            row.content_hash = content_hash
            if content_changed:
                row.embedding_json = None
                row.embedding_model = ""
            row.confidence = max(float(row.confidence or 0.0), confidence_value)
            row.confirmed = 1 if confirmed_value else 0
            row.status = requested_status
            row.supersedes_id = supersedes_id
            row.expires_at = expires_at
            row.updated_at = datetime.now()
            session.commit()
            result = self._long_term_dict(row)
            should_index = bool(row.confirmed) and row.status == MEMORY_STATUS_ACTIVE and (
                content_changed or not row.embedding_json
            )
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
        if result is None:
            raise RuntimeError("long-term memory was not persisted")
        if should_index:
            self.enqueue_long_term_vector_job(
                owner_id=owner,
                memory_id=int(result["id"]),
                content_hash=content_hash,
            )
        return result

    @staticmethod
    def _long_term_dict(row: ProfessionalAgentLongTermMemoryModel) -> dict[str, Any]:
        return {
            "id": int(row.id or 0),
            "memoryId": str(row.id or ""),
            "ownerId": row.owner_id,
            "scope": row.scope,
            "scopeId": row.scope_id,
            "memoryKey": row.memory_key,
            "category": row.category,
            "content": row.content,
            "text": row.content,
            "path": f"memory/{row.scope}/{row.memory_key}.md",
            "sourceConversationId": row.source_conversation_id,
            "sourceRunId": row.source_run_id,
            "sourceMessageIds": _load(row.source_message_ids, []),
            "metadata": _load(row.metadata_json, {}),
            "confidence": round(float(row.confidence or 0.0), 4),
            "confirmed": bool(row.confirmed),
            "status": row.status,
            "supersedesId": int(row.supersedes_id) if row.supersedes_id else None,
            "createdAt": row.created_at.isoformat(timespec="seconds") if row.created_at else "",
            "updatedAt": row.updated_at.isoformat(timespec="seconds") if row.updated_at else "",
            "lastUsedAt": row.last_used_at.isoformat(timespec="seconds") if row.last_used_at else "",
        }

    def list_long_term_memories(
        self,
        *,
        owner_id: str,
        conversation_id: str = "",
        project_id: str = "",
        brand_id: str = "",
        limit: int = 80,
        include_pending: bool = False,
    ) -> list[dict[str, Any]]:
        owner = _clean(owner_id, "anonymous")[:191]
        allowed_scopes = {("user", owner)}
        if _clean(conversation_id):
            allowed_scopes.add(("conversation", _clean(conversation_id)[:191]))
        if _clean(project_id):
            allowed_scopes.add(("project", _clean(project_id)[:191]))
        if _clean(brand_id):
            allowed_scopes.add(("brand", _clean(brand_id)[:191]))
        scope_filters = [
            and_(
                ProfessionalAgentLongTermMemoryModel.scope == scope,
                ProfessionalAgentLongTermMemoryModel.scope_id == scope_id,
            )
            for scope, scope_id in allowed_scopes
        ]
        session = self._session()
        try:
            statuses = [MEMORY_STATUS_ACTIVE]
            if include_pending:
                statuses.append(MEMORY_STATUS_PENDING_REVIEW)
            now = datetime.now()
            query = session.query(ProfessionalAgentLongTermMemoryModel).filter(
                ProfessionalAgentLongTermMemoryModel.owner_id == owner,
                ProfessionalAgentLongTermMemoryModel.status.in_(statuses),
                or_(*scope_filters),
                or_(
                    ProfessionalAgentLongTermMemoryModel.expires_at.is_(None),
                    ProfessionalAgentLongTermMemoryModel.expires_at > now,
                ),
            )
            if not include_pending:
                query = query.filter(
                    ProfessionalAgentLongTermMemoryModel.confirmed == 1,
                    ProfessionalAgentLongTermMemoryModel.confidence >= MEMORY_RETRIEVAL_MIN_CONFIDENCE,
                )
            rows = query.order_by(desc(ProfessionalAgentLongTermMemoryModel.updated_at)).limit(
                max(1, min(200, int(limit or 80)))
            ).all()
            items = [self._long_term_dict(row) for row in rows]
            if include_pending:
                items.sort(key=lambda item: item["updatedAt"], reverse=True)
                items.sort(key=lambda item: item["status"] != MEMORY_STATUS_PENDING_REVIEW)
            return items[:max(1, min(200, int(limit or 80)))]
        finally:
            session.close()

    def search_long_term_memories(
        self,
        *,
        owner_id: str,
        query: object,
        conversation_id: str = "",
        project_id: str = "",
        brand_id: str = "",
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        query_text = re.sub(r"\s+", " ", str(query or "").strip()[:3000]).strip()
        if not query_text:
            return self.list_long_term_memories(
                owner_id=owner_id,
                conversation_id=conversation_id,
                project_id=project_id,
                brand_id=brand_id,
                limit=limit,
            )
        owner = _clean(owner_id, "anonymous")[:191]
        allowed_scopes = {("user", owner)}
        if _clean(conversation_id):
            allowed_scopes.add(("conversation", _clean(conversation_id)[:191]))
        if _clean(project_id):
            allowed_scopes.add(("project", _clean(project_id)[:191]))
        if _clean(brand_id):
            allowed_scopes.add(("brand", _clean(brand_id)[:191]))
        scope_filters = [
            and_(
                ProfessionalAgentLongTermMemoryModel.scope == scope,
                ProfessionalAgentLongTermMemoryModel.scope_id == scope_id,
            )
            for scope, scope_id in allowed_scopes
        ]
        query_terms = professional_retrieval_terms(query_text)
        try:
            query_vector = professional_query_embedding(query_text)
        except Exception:
            query_vector = []
        session = self._session()
        try:
            now = datetime.now()
            rows = (
                session.query(ProfessionalAgentLongTermMemoryModel)
                .filter(
                    ProfessionalAgentLongTermMemoryModel.owner_id == owner,
                    ProfessionalAgentLongTermMemoryModel.status == MEMORY_STATUS_ACTIVE,
                    ProfessionalAgentLongTermMemoryModel.confirmed == 1,
                    ProfessionalAgentLongTermMemoryModel.confidence >= MEMORY_RETRIEVAL_MIN_CONFIDENCE,
                    or_(*scope_filters),
                    or_(
                        ProfessionalAgentLongTermMemoryModel.expires_at.is_(None),
                        ProfessionalAgentLongTermMemoryModel.expires_at > now,
                    ),
                )
                .order_by(desc(ProfessionalAgentLongTermMemoryModel.updated_at))
                .limit(2000)
                .all()
            )
            embedding_model = professional_embedding_model()
            missing = [row for row in rows[:200] if query_vector and (not row.embedding_json or row.embedding_model != embedding_model)]
            if missing:
                try:
                    vectors = embed_professional_texts([row.content for row in missing])
                    if len(vectors) == len(missing):
                        for row, vector in zip(missing, vectors):
                            if vector:
                                row.embedding_json = json.dumps(vector, separators=(",", ":"))
                                row.embedding_model = embedding_model
                        session.commit()
                except Exception:
                    session.rollback()
            qdrant_scores: dict[int, float] = {}
            if professional_memory_index.enabled():
                scope_keys = [f"{scope}:{scope_id}" for scope, scope_id in allowed_scopes]
                for row in rows[:200]:
                    if not row.embedding_json:
                        continue
                    try:
                        professional_memory_index.upsert(
                            int(row.id),
                            json.loads(row.embedding_json),
                            {
                                "ownerId": owner,
                                "scopeKey": f"{row.scope}:{row.scope_id}",
                                "memoryKey": row.memory_key,
                                "category": row.category,
                            },
                        )
                    except Exception:
                        continue
                try:
                    qdrant_hits = professional_memory_index.search(
                        vector=query_vector,
                        owner_id=owner,
                        scope_keys=scope_keys,
                        limit=max(20, int(limit or 8) * 3),
                    )
                    qdrant_scores = {
                        int(item.get("id")): float(item.get("score") or 0.0)
                        for item in qdrant_hits
                        if str(item.get("id") or "").isdigit()
                    }
                except Exception:
                    qdrant_scores = {}
            scored: list[tuple[float, int, ProfessionalAgentLongTermMemoryModel]] = []
            scope_bonus = {"conversation": 0.12, "project": 0.1, "brand": 0.1, "user": 0.0}
            for row in rows:
                terms = professional_retrieval_terms(row.content)
                keyword_score = min(1.0, len(query_terms & terms) / max(1, min(12, len(query_terms))) * 1.4)
                vector_score = 0.0
                if query_vector and row.embedding_json:
                    try:
                        vector_score = professional_cosine_similarity(query_vector, json.loads(row.embedding_json))
                    except (TypeError, ValueError, json.JSONDecodeError):
                        vector_score = 0.0
                if int(row.id or 0) in qdrant_scores:
                    vector_score = qdrant_scores[int(row.id or 0)]
                relevance = 0.7 * vector_score + 0.3 * keyword_score if query_vector else keyword_score
                score = relevance * 0.8 + float(row.confidence or 0.0) * 0.2 + scope_bonus.get(row.scope, 0.0)
                if score > 0:
                    scored.append((score, -int(row.id or 0), row))
            scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
            selected = scored[:max(1, min(20, int(limit or 8)))]
            for score, _index, row in selected:
                row.retrieval_count = int(row.retrieval_count or 0) + 1
                row.last_used_at = now
            if selected:
                session.commit()
            result = []
            for score, _index, row in selected:
                item = self._long_term_dict(row)
                item["score"] = round(score, 4)
                item["retrieval"] = "hybrid" if query_vector else "keyword"
                result.append(item)
            return result
        finally:
            session.close()

    def enqueue_memory_job(
        self,
        *,
        job_id: str,
        owner_id: str,
        conversation_id: str = "",
        run_id: str = "",
        job_type: str = "conversation",
        available_at: datetime | None = None,
    ) -> bool:
        session = self._session()
        try:
            clean_id = _clean(job_id)[:191]
            if not clean_id:
                return False
            if session.get(ProfessionalAgentMemoryJobModel, clean_id) is not None:
                return False
            session.add(ProfessionalAgentMemoryJobModel(
                job_id=clean_id,
                owner_id=_clean(owner_id, "anonymous")[:191],
                conversation_id=_clean(conversation_id)[:191],
                run_id=_clean(run_id)[:191],
                job_type=_clean(job_type, "conversation")[:24],
                status="pending",
                available_at=available_at or datetime.now(),
                created_at=datetime.now(),
            ))
            session.commit()
            return True
        except Exception:
            session.rollback()
            return False
        finally:
            session.close()

    def claim_memory_job(self, *, worker_id: str, stale_after_secs: int = 900) -> dict[str, Any] | None:
        session = self._session()
        try:
            now = datetime.now()
            stale_before = now - timedelta(seconds=max(60, stale_after_secs))
            row = (
                session.query(ProfessionalAgentMemoryJobModel)
                .filter(
                    ((ProfessionalAgentMemoryJobModel.status == "pending") & (ProfessionalAgentMemoryJobModel.available_at <= now))
                    | ((ProfessionalAgentMemoryJobModel.status == "running") & (ProfessionalAgentMemoryJobModel.locked_at < stale_before)),
                )
                .order_by(ProfessionalAgentMemoryJobModel.created_at)
                .with_for_update()
                .first()
            )
            if row is None:
                return None
            row.status = "running"
            row.attempts = int(row.attempts or 0) + 1
            row.locked_at = now
            row.locked_by = _clean(worker_id)[:191]
            row.updated_at = now
            session.commit()
            return {
                "jobId": row.job_id,
                "ownerId": row.owner_id,
                "conversationId": row.conversation_id,
                "runId": row.run_id,
                "jobType": row.job_type,
                "attempts": row.attempts,
            }
        except Exception:
            session.rollback()
            return None
        finally:
            session.close()

    def finish_memory_job(self, job_id: str, *, success: bool, error: str = "") -> None:
        session = self._session()
        try:
            row = session.get(ProfessionalAgentMemoryJobModel, _clean(job_id)[:191])
            if row is not None:
                retryable = not success and int(row.attempts or 0) < 3
                row.status = "completed" if success else ("pending" if retryable else "failed")
                row.error = _clean(error, limit=12000)
                row.locked_at = None
                row.locked_by = ""
                if retryable:
                    row.available_at = datetime.now() + timedelta(seconds=15)
                row.updated_at = datetime.now()
                session.commit()
        finally:
            session.close()

    def memory_job_is_active(self, *, job_id: str, owner_id: str) -> bool:
        clean_job_id = _clean(job_id)[:191]
        if not clean_job_id:
            return True
        session = self._session()
        try:
            return bool(
                session.query(ProfessionalAgentMemoryJobModel.job_id)
                .filter(
                    ProfessionalAgentMemoryJobModel.job_id == clean_job_id,
                    ProfessionalAgentMemoryJobModel.owner_id == _clean(owner_id, "anonymous")[:191],
                    ProfessionalAgentMemoryJobModel.status.in_(("pending", "running")),
                )
                .first()
            )
        finally:
            session.close()

    def save_memory_digest(
        self,
        *,
        owner_id: str,
        digest_date: str,
        digest_type: str,
        content: str,
        source_hash: str = "",
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        session = self._session()
        try:
            owner = _clean(owner_id, "anonymous")[:191]
            date_value = _clean(digest_date)[:32]
            kind = _clean(digest_type, "daily")[:24]
            row = (
                session.query(ProfessionalAgentMemoryDigestModel)
                .filter(
                    ProfessionalAgentMemoryDigestModel.owner_id == owner,
                    ProfessionalAgentMemoryDigestModel.digest_date == date_value,
                    ProfessionalAgentMemoryDigestModel.digest_type == kind,
                )
                .one_or_none()
            )
            if row is None:
                row = ProfessionalAgentMemoryDigestModel(owner_id=owner, digest_date=date_value, digest_type=kind)
                session.add(row)
            row.content = _clean(content, limit=24000)
            row.source_hash = _clean(source_hash)[:64]
            row.metadata_json = _dump(dict(metadata or {}))
            row.updated_at = datetime.now()
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def list_all_long_term_memories(
        self,
        *,
        owner_id: str,
        limit: int = 200,
        include_pending: bool = False,
    ) -> list[dict[str, Any]]:
        owner = _clean(owner_id, "anonymous")[:191]
        session = self._session()
        try:
            statuses = [MEMORY_STATUS_ACTIVE]
            if include_pending:
                statuses.append(MEMORY_STATUS_PENDING_REVIEW)
            rows = (
                session.query(ProfessionalAgentLongTermMemoryModel)
                .filter(
                    ProfessionalAgentLongTermMemoryModel.owner_id == owner,
                    ProfessionalAgentLongTermMemoryModel.status.in_(statuses),
                )
                .order_by(desc(ProfessionalAgentLongTermMemoryModel.updated_at))
                .limit(max(1, min(1000, int(limit or 200))))
                .all()
            )
            return [
                self._long_term_dict(row)
                for row in rows
                if include_pending or (
                    bool(row.confirmed)
                    and float(row.confidence or 0.0) >= MEMORY_RETRIEVAL_MIN_CONFIDENCE
                )
            ]
        finally:
            session.close()

    def backfill_long_term_vectors(self, *, owner_id: str = "", limit: int = 10000) -> dict[str, int]:
        session = self._session()
        try:
            query = session.query(ProfessionalAgentLongTermMemoryModel).filter(
                ProfessionalAgentLongTermMemoryModel.status == MEMORY_STATUS_ACTIVE,
                ProfessionalAgentLongTermMemoryModel.confirmed == 1,
                ProfessionalAgentLongTermMemoryModel.confidence >= MEMORY_RETRIEVAL_MIN_CONFIDENCE,
            )
            clean_owner = _clean(owner_id)
            if clean_owner:
                query = query.filter(ProfessionalAgentLongTermMemoryModel.owner_id == clean_owner[:191])
            rows = query.order_by(ProfessionalAgentLongTermMemoryModel.id).limit(
                max(1, min(100_000, int(limit or 10000)))
            ).all()
            model = professional_embedding_model()
            missing = [row for row in rows if not row.embedding_json or row.embedding_model != model]
            embedded = 0
            for offset in range(0, len(missing), 32):
                batch = missing[offset:offset + 32]
                vectors = embed_professional_texts([row.content for row in batch])
                if len(vectors) != len(batch):
                    break
                for row, vector in zip(batch, vectors):
                    if not vector:
                        continue
                    row.embedding_json = json.dumps(vector, separators=(",", ":"))
                    row.embedding_model = model
                    row.updated_at = datetime.now()
                    embedded += 1
            if embedded:
                session.commit()

            indexed = 0
            failed = 0
            if professional_memory_index.enabled():
                for row in rows:
                    if not row.embedding_json:
                        continue
                    try:
                        vector = json.loads(row.embedding_json)
                    except (TypeError, ValueError, json.JSONDecodeError):
                        failed += 1
                        continue
                    if professional_memory_index.upsert(
                        int(row.id),
                        vector,
                        {
                            "ownerId": row.owner_id,
                            "scopeKey": f"{row.scope}:{row.scope_id}",
                            "memoryKey": row.memory_key,
                            "category": row.category,
                            "contentHash": row.content_hash,
                            "embeddingModel": row.embedding_model,
                        },
                    ):
                        indexed += 1
                    else:
                        failed += 1
            return {
                "rows": len(rows),
                "embedded": embedded,
                "indexed": indexed,
                "pending": sum(1 for row in rows if not row.embedding_json),
                "failed": failed,
            }
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def enqueue_long_term_vector_job(self, *, owner_id: str, memory_id: int, content_hash: str) -> bool:
        digest = _clean(content_hash)[:16] or "current"
        return self.enqueue_memory_job(
            job_id=f"vector:{int(memory_id)}:{digest}",
            owner_id=_clean(owner_id, "anonymous")[:191],
            run_id=str(int(memory_id)),
            job_type="vector",
        )

    def sync_long_term_memory_vector(self, *, owner_id: str, memory_id: int) -> dict[str, Any]:
        owner = _clean(owner_id, "anonymous")[:191]
        row_id = int(memory_id)
        session = self._session()
        try:
            row = (
                session.query(ProfessionalAgentLongTermMemoryModel)
                .filter(
                    ProfessionalAgentLongTermMemoryModel.id == row_id,
                    ProfessionalAgentLongTermMemoryModel.owner_id == owner,
                )
                .one_or_none()
            )
            if row is None or row.status != MEMORY_STATUS_ACTIVE or not bool(row.confirmed):
                try:
                    professional_memory_index.delete(row_id)
                except Exception:
                    pass
                return {"memoryId": row_id, "indexed": False, "skipped": True}

            model = professional_embedding_model()
            vector: list[float] = []
            if row.embedding_json and row.embedding_model == model:
                try:
                    vector = [float(value) for value in json.loads(row.embedding_json)]
                except (TypeError, ValueError, json.JSONDecodeError):
                    vector = []
            if not vector:
                vectors = embed_professional_texts([row.content])
                vector = vectors[0] if vectors and vectors[0] else []
                if not vector:
                    raise RuntimeError("long-term memory embedding is unavailable")
                row.embedding_json = json.dumps(vector, separators=(",", ":"))
                row.embedding_model = model
                row.updated_at = datetime.now()
                session.commit()

            indexed = professional_memory_index.upsert(
                row_id,
                vector,
                {
                    "ownerId": row.owner_id,
                    "scopeKey": f"{row.scope}:{row.scope_id}",
                    "memoryKey": row.memory_key,
                    "category": row.category,
                    "contentHash": row.content_hash,
                    "embeddingModel": row.embedding_model,
                },
            )
            if not indexed:
                raise RuntimeError("long-term memory vector index is unavailable")
            return {"memoryId": row_id, "indexed": True, "skipped": False}
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def list_recent_owner_messages(self, *, owner_id: str, limit: int = 80) -> list[dict[str, Any]]:
        owner = _clean(owner_id, "anonymous")[:191]
        session = self._session()
        try:
            rows = (
                session.query(ProfessionalAgentMessageModel)
                .filter(
                    ProfessionalAgentMessageModel.owner_id == owner,
                    ProfessionalAgentMessageModel.role.in_(("user", "assistant")),
                )
                .order_by(ProfessionalAgentMessageModel.id.desc())
                .limit(max(1, min(500, int(limit or 80))))
                .all()
            )
            return [
                {
                    **self._message_dict(row),
                    "conversationId": row.conversation_id,
                }
                for row in reversed(rows)
            ]
        finally:
            session.close()

    def archive_long_term_keys(
        self,
        *,
        owner_id: str,
        keys: list[str],
        include_confirmed: bool = False,
    ) -> int:
        clean_keys = {_clean(key)[:191] for key in keys if _clean(key)}
        if not clean_keys:
            return 0
        session = self._session()
        try:
            query = session.query(ProfessionalAgentLongTermMemoryModel).filter(
                ProfessionalAgentLongTermMemoryModel.owner_id == _clean(owner_id, "anonymous")[:191],
                ProfessionalAgentLongTermMemoryModel.memory_key.in_(clean_keys),
                ProfessionalAgentLongTermMemoryModel.status.in_([
                    MEMORY_STATUS_ACTIVE,
                    MEMORY_STATUS_PENDING_REVIEW,
                ]),
            )
            if not include_confirmed:
                query = query.filter(ProfessionalAgentLongTermMemoryModel.confirmed == 0)
            rows = query.all()
            for row in rows:
                row.status = MEMORY_STATUS_SUPERSEDED
                row.updated_at = datetime.now()
            session.commit()
            return len(rows)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def review_long_term_memory(
        self,
        *,
        owner_id: str,
        memory_id: int,
        decision: str,
    ) -> dict[str, Any] | None:
        owner = _clean(owner_id, "anonymous")[:191]
        clean_decision = _clean(decision).lower()
        if clean_decision not in {"approve", "reject"}:
            raise ValueError("memory review decision must be approve or reject")
        row_id = int(memory_id)
        session = self._session()
        vector_ids_to_delete: list[int] = [row_id]
        try:
            row = (
                session.query(ProfessionalAgentLongTermMemoryModel)
                .filter(
                    ProfessionalAgentLongTermMemoryModel.id == row_id,
                    ProfessionalAgentLongTermMemoryModel.owner_id == owner,
                )
                .one_or_none()
            )
            if row is None:
                return None
            if row.status != MEMORY_STATUS_PENDING_REVIEW:
                raise ValueError("memory is not pending review")
            if clean_decision == "reject":
                row.status = MEMORY_STATUS_REJECTED
                row.confirmed = 0
                row.embedding_json = None
                row.embedding_model = ""
                row.updated_at = datetime.now()
                session.commit()
                result = self._long_term_dict(row)
            else:
                metadata = _load(row.metadata_json, {})
                target = None
                if row.supersedes_id:
                    target = (
                        session.query(ProfessionalAgentLongTermMemoryModel)
                        .filter(
                            ProfessionalAgentLongTermMemoryModel.id == int(row.supersedes_id),
                            ProfessionalAgentLongTermMemoryModel.owner_id == owner,
                        )
                        .one_or_none()
                    )
                if target is not None:
                    canonical_key = _clean(metadata.get("candidateForKey"), target.memory_key)[:191]
                    target.memory_key = f"{target.memory_key[:164]}.superseded.{int(target.id)}"
                    target.status = MEMORY_STATUS_SUPERSEDED
                    target.updated_at = datetime.now()
                    vector_ids_to_delete.append(int(target.id))
                    session.flush()
                    row.memory_key = canonical_key
                metadata.update({
                    "reviewed": True,
                    "reviewDecision": "approve",
                    "reviewedAt": datetime.now().isoformat(timespec="seconds"),
                })
                row.metadata_json = _dump(metadata)
                row.status = MEMORY_STATUS_ACTIVE
                row.confirmed = 1
                row.confidence = max(0.9, float(row.confidence or 0.0))
                row.supersedes_id = int(target.id) if target is not None else row.supersedes_id
                row.embedding_json = None
                row.embedding_model = ""
                row.updated_at = datetime.now()
                session.commit()
                result = self._long_term_dict(row)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
        for vector_id in vector_ids_to_delete:
            try:
                professional_memory_index.delete(vector_id)
            except Exception:
                pass
        if result and result.get("status") == MEMORY_STATUS_ACTIVE and result.get("confirmed"):
            self.enqueue_long_term_vector_job(
                owner_id=owner,
                memory_id=row_id,
                content_hash=str(result.get("contentHash") or hashlib.sha256(result["content"].encode("utf-8")).hexdigest()),
            )
        return result

    def delete_long_term_memory(self, *, owner_id: str, memory_id: int) -> bool:
        session = self._session()
        try:
            row = (
                session.query(ProfessionalAgentLongTermMemoryModel)
                .filter(
                    ProfessionalAgentLongTermMemoryModel.id == int(memory_id),
                    ProfessionalAgentLongTermMemoryModel.owner_id == _clean(owner_id, "anonymous")[:191],
                )
                .one_or_none()
            )
            if row is None:
                return False
            session.delete(row)
            session.commit()
            try:
                professional_memory_index.delete(int(memory_id))
            except Exception:
                pass
            return True
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def update_long_term_memory(
        self,
        *,
        owner_id: str,
        memory_id: int,
        content: str,
        category: str,
        scope: str,
        scope_id: str = "",
    ) -> dict[str, Any] | None:
        owner = _clean(owner_id, "anonymous")[:191]
        text_value = re.sub(r"\s+", " ", _clean(content, limit=12000)).strip()
        if not text_value:
            raise ValueError("memory content is required")
        clean_category = _clean(category, "note")[:48].lower()
        session = self._session()
        row_id = int(memory_id)
        vector_ids_to_delete: list[int] = [row_id]
        try:
            row = (
                session.query(ProfessionalAgentLongTermMemoryModel)
                .filter(
                    ProfessionalAgentLongTermMemoryModel.id == row_id,
                    ProfessionalAgentLongTermMemoryModel.owner_id == owner,
                )
                .one_or_none()
            )
            if row is None:
                return None
            clean_scope, clean_scope_id = self._normalize_memory_scope(
                scope,
                scope_id,
                owner_id=owner,
                conversation_id=row.source_conversation_id,
            )
            if clean_category in PROJECT_MEMORY_CATEGORIES and row.source_conversation_id:
                clean_scope, clean_scope_id = "project", row.source_conversation_id
            metadata = _load(row.metadata_json, {})
            target = None
            canonical_key = row.memory_key
            if row.status == MEMORY_STATUS_PENDING_REVIEW and row.supersedes_id:
                target = (
                    session.query(ProfessionalAgentLongTermMemoryModel)
                    .filter(
                        ProfessionalAgentLongTermMemoryModel.id == int(row.supersedes_id),
                        ProfessionalAgentLongTermMemoryModel.owner_id == owner,
                    )
                    .one_or_none()
                )
                if target is not None:
                    canonical_key = _clean(metadata.get("candidateForKey"), target.memory_key)[:191]
            conflict = (
                session.query(ProfessionalAgentLongTermMemoryModel.id)
                .filter(
                    ProfessionalAgentLongTermMemoryModel.owner_id == owner,
                    ProfessionalAgentLongTermMemoryModel.scope == clean_scope,
                    ProfessionalAgentLongTermMemoryModel.scope_id == clean_scope_id,
                    ProfessionalAgentLongTermMemoryModel.memory_key == canonical_key,
                    ProfessionalAgentLongTermMemoryModel.id != row_id,
                    *(
                        [ProfessionalAgentLongTermMemoryModel.id != int(target.id)]
                        if target is not None
                        else []
                    ),
                )
                .first()
            )
            if conflict is not None:
                raise ValueError("a memory with the same key already exists in this scope")
            if target is not None:
                target.memory_key = f"{target.memory_key[:164]}.superseded.{int(target.id)}"
                target.status = MEMORY_STATUS_SUPERSEDED
                target.updated_at = datetime.now()
                vector_ids_to_delete.append(int(target.id))
                session.flush()
                row.memory_key = canonical_key
            content_hash = hashlib.sha256(text_value.encode("utf-8")).hexdigest()
            if row.content_hash != content_hash:
                row.embedding_json = None
                row.embedding_model = ""
            row.content = text_value
            row.content_hash = content_hash
            row.category = clean_category
            row.scope = clean_scope
            row.scope_id = clean_scope_id
            row.confirmed = 1
            row.status = MEMORY_STATUS_ACTIVE
            row.confidence = max(0.9, float(row.confidence or 0.0))
            metadata.update({
                "reviewed": True,
                "reviewDecision": "edit_and_approve",
                "reviewedAt": datetime.now().isoformat(timespec="seconds"),
            })
            row.metadata_json = _dump(metadata)
            row.updated_at = datetime.now()
            session.commit()
            result = self._long_term_dict(row)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
        for vector_id in vector_ids_to_delete:
            try:
                professional_memory_index.delete(vector_id)
            except Exception:
                pass
        if result:
            self.enqueue_long_term_vector_job(
                owner_id=owner,
                memory_id=row_id,
                content_hash=hashlib.sha256(result["content"].encode("utf-8")).hexdigest(),
            )
        return result

    def delete_all_long_term_memories(self, *, owner_id: str) -> int:
        owner = _clean(owner_id, "anonymous")[:191]
        session = self._session()
        memory_ids: list[int] = []
        try:
            rows = (
                session.query(ProfessionalAgentLongTermMemoryModel)
                .filter(ProfessionalAgentLongTermMemoryModel.owner_id == owner)
                .all()
            )
            memory_ids = [int(row.id) for row in rows if row.id is not None]
            for row in rows:
                session.delete(row)
            session.query(ProfessionalAgentMemoryDigestModel).filter(
                ProfessionalAgentMemoryDigestModel.owner_id == owner,
            ).delete(synchronize_session=False)
            session.query(ProfessionalAgentMemoryJobModel).filter(
                ProfessionalAgentMemoryJobModel.owner_id == owner,
            ).delete(synchronize_session=False)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
        for memory_id in memory_ids:
            try:
                professional_memory_index.delete(memory_id)
            except Exception:
                pass
        return len(memory_ids)

    def create_run_state(self, state: Mapping[str, Any]) -> None:
        run_id = _clean(state.get("runId"))[:191]
        owner_id = _clean(state.get("ownerId"), "anonymous")[:191]
        conversation_id = _clean(state.get("conversationId"))[:191]
        if not run_id or not conversation_id:
            raise ValueError("run id and conversation id are required")
        session = self._session()
        try:
            conversation = (
                session.query(ProfessionalAgentConversationModel)
                .filter(
                    ProfessionalAgentConversationModel.owner_id == owner_id,
                    ProfessionalAgentConversationModel.conversation_id == conversation_id,
                )
                .with_for_update()
                .one_or_none()
            )
            if conversation is not None:
                active = _clean(conversation.active_run_id)
                if active and active != run_id and conversation.status in {"pending", "running"}:
                    raise ProfessionalAgentConversationBusy(
                        "this conversation already has an active professional agent run"
                    )
            else:
                conversation = ProfessionalAgentConversationModel(
                    owner_id=owner_id,
                    conversation_id=conversation_id,
                    created_at=datetime.now(),
                )
                session.add(conversation)

            existing = session.get(ProfessionalAgentRunModel, run_id)
            if existing is None:
                existing = ProfessionalAgentRunModel(run_id=run_id, owner_id=owner_id, conversation_id=conversation_id)
                session.add(existing)
            self._apply_run_state(existing, state)
            conversation.active_run_id = run_id
            conversation.latest_turn_id = _clean(state.get("turnId"))[:191]
            conversation.status = _clean(state.get("status"), "pending")[:40]
            conversation.deleted_at = None
            conversation.updated_at = datetime.now()
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    @staticmethod
    def _apply_run_state(
        row: ProfessionalAgentRunModel,
        state: Mapping[str, Any],
        *,
        preserve_cancellation: bool = False,
    ) -> None:
        was_cancel_requested = bool(row.cancel_requested)
        row.owner_id = _clean(state.get("ownerId"), "anonymous")[:191]
        row.conversation_id = _clean(state.get("conversationId"))[:191]
        row.turn_id = _clean(state.get("turnId"))[:191]
        row.agent_name = _clean(state.get("agentName"), "ecommerce-cow-agent")[:120]
        row.status = _clean(state.get("status"), "pending")[:40]
        row.request_json = _dump(state.get("request") if isinstance(state.get("request"), Mapping) else {})
        row.metadata_json = _dump(state.get("metadata") if isinstance(state.get("metadata"), Mapping) else {})
        row.result_json = _dump(state.get("result") if isinstance(state.get("result"), Mapping) else {})
        row.error = _clean(state.get("error"))[:12000]
        row.max_steps = max(1, int(state.get("maxSteps") or 8))
        row.started_at = _clean(state.get("startedAt"))[:80]
        row.finished_at = _clean(state.get("finishedAt"))[:80]
        row.duration_ms = state.get("durationMs") if isinstance(state.get("durationMs"), int) else None
        row.tool_calls = max(0, int(state.get("toolCalls") or 0))
        row.cancel_requested = 1 if state.get("cancelRequested") or (preserve_cancellation and was_cancel_requested) else 0
        if preserve_cancellation and was_cancel_requested:
            row.status = "canceled"
            row.error = row.error or "任务已由用户中止"
            row.finished_at = row.finished_at or datetime.now().isoformat(timespec="seconds")
        row.updated_at = datetime.now()

    def save_run_state(self, state: Mapping[str, Any]) -> None:
        run_id = _clean(state.get("runId"))[:191]
        if not run_id:
            raise ValueError("run id is required")
        session = self._session()
        try:
            row = (
                session.query(ProfessionalAgentRunModel)
                .filter(ProfessionalAgentRunModel.run_id == run_id)
                .with_for_update()
                .one_or_none()
            )
            if row is None:
                row = ProfessionalAgentRunModel(
                    run_id=run_id,
                    owner_id=_clean(state.get("ownerId"), "anonymous")[:191],
                    conversation_id=_clean(state.get("conversationId"))[:191],
                )
                session.add(row)
            self._apply_run_state(row, state, preserve_cancellation=True)
            conversation = self._conversation_row(
                session,
                _clean(state.get("ownerId"), "anonymous")[:191],
                _clean(state.get("conversationId"))[:191],
            )
            if conversation is not None and _clean(conversation.active_run_id) in {"", run_id}:
                conversation.active_run_id = run_id
                conversation.latest_turn_id = row.turn_id
                conversation.status = row.status
                conversation.updated_at = datetime.now()
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def append_run_event(
        self,
        *,
        run_id: str,
        sequence: int = 0,
        event_type: str,
        timestamp: str,
        payload: Mapping[str, Any] | None,
    ) -> dict[str, Any] | None:
        normalized_run = _clean(run_id)[:191]
        if not normalized_run:
            return None
        session = self._session()
        try:
            run_row = (
                session.query(ProfessionalAgentRunModel)
                .filter(ProfessionalAgentRunModel.run_id == normalized_run)
                .with_for_update()
                .one_or_none()
            )
            if run_row is None:
                return None
            requested_sequence = max(0, int(sequence or 0))
            if requested_sequence:
                existing = (
                    session.query(ProfessionalAgentRunEventModel)
                    .filter(
                        ProfessionalAgentRunEventModel.run_id == normalized_run,
                        ProfessionalAgentRunEventModel.sequence == requested_sequence,
                    )
                    .one_or_none()
                )
                if existing is not None:
                    return {
                        "sequence": int(existing.sequence),
                        "type": existing.event_type,
                        "timestamp": existing.timestamp,
                        "payload": _load(existing.payload_json, {}),
                    }
            latest_sequence = int(
                session.query(func.max(ProfessionalAgentRunEventModel.sequence))
                .filter(
                    ProfessionalAgentRunEventModel.run_id == normalized_run,
                )
                .scalar()
                or 0
            )
            assigned_sequence = requested_sequence if requested_sequence > latest_sequence else latest_sequence + 1
            normalized_type = _clean(event_type)[:120]
            normalized_timestamp = _clean(timestamp)[:80]
            normalized_payload = dict(payload or {})
            session.add(ProfessionalAgentRunEventModel(
                run_id=normalized_run,
                sequence=assigned_sequence,
                event_type=normalized_type,
                timestamp=normalized_timestamp,
                payload_json=_dump(normalized_payload),
                created_at=datetime.now(),
            ))
            session.commit()
            return {
                "sequence": assigned_sequence,
                "type": normalized_type,
                "timestamp": normalized_timestamp,
                "payload": _slim_for_storage(normalized_payload),
            }
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def load_run_state(self, run_id: str, *, owner_id: str | None = None) -> dict[str, Any] | None:
        normalized_run = _clean(run_id)[:191]
        session = self._session()
        try:
            query = session.query(ProfessionalAgentRunModel).filter(ProfessionalAgentRunModel.run_id == normalized_run)
            if owner_id is not None:
                query = query.filter(ProfessionalAgentRunModel.owner_id == _clean(owner_id, "anonymous")[:191])
            row = query.one_or_none()
            if row is None:
                return None
            return {
                "runId": row.run_id,
                "ownerId": row.owner_id,
                "conversationId": row.conversation_id,
                "turnId": row.turn_id,
                "agentName": row.agent_name,
                "status": row.status,
                "request": _load(row.request_json, {}),
                "metadata": _load(row.metadata_json, {}),
                "result": _load(row.result_json, {}),
                "error": row.error,
                "maxSteps": int(row.max_steps or 8),
                "startedAt": row.started_at,
                "finishedAt": row.finished_at,
                "durationMs": row.duration_ms,
                "toolCalls": int(row.tool_calls or 0),
                "cancelRequested": bool(row.cancel_requested),
            }
        finally:
            session.close()

    def load_run_events(self, run_id: str, *, after_sequence: int = 0, limit: int = 2000) -> list[dict[str, Any]]:
        session = self._session()
        try:
            rows = (
                session.query(ProfessionalAgentRunEventModel)
                .filter(
                    ProfessionalAgentRunEventModel.run_id == _clean(run_id)[:191],
                    ProfessionalAgentRunEventModel.sequence > max(0, int(after_sequence)),
                )
                .order_by(ProfessionalAgentRunEventModel.sequence.asc())
                .limit(max(1, min(5000, int(limit))))
                .all()
            )
            return [
                {
                    "sequence": int(row.sequence),
                    "type": row.event_type,
                    "timestamp": row.timestamp,
                    "payload": _load(row.payload_json, {}),
                }
                for row in rows
            ]
        finally:
            session.close()

    def request_run_cancel(self, run_id: str, *, owner_id: str) -> dict[str, Any] | None:
        session = self._session()
        try:
            row = (
                session.query(ProfessionalAgentRunModel)
                .filter(
                    ProfessionalAgentRunModel.run_id == _clean(run_id)[:191],
                    ProfessionalAgentRunModel.owner_id == _clean(owner_id, "anonymous")[:191],
                )
                .with_for_update()
                .one_or_none()
            )
            if row is None:
                return None
            if row.status in {"completed", "failed", "canceled"}:
                session.commit()
                return self.load_run_state(row.run_id, owner_id=row.owner_id)
            row.cancel_requested = 1
            if row.status in {"pending", "running", "waiting_for_input"}:
                row.status = "canceled"
                row.error = "任务已由用户中止"
                row.finished_at = datetime.now().isoformat(timespec="seconds")
            row.updated_at = datetime.now()
            conversation = self._conversation_row(session, row.owner_id, row.conversation_id)
            if conversation is not None and conversation.active_run_id == row.run_id:
                conversation.status = row.status
                conversation.updated_at = datetime.now()
            session.commit()
            return self.load_run_state(row.run_id, owner_id=row.owner_id)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def is_run_cancel_requested(self, run_id: str) -> bool:
        session = self._session()
        try:
            value = (
                session.query(ProfessionalAgentRunModel.cancel_requested)
                .filter(ProfessionalAgentRunModel.run_id == _clean(run_id)[:191])
                .scalar()
            )
            return bool(value)
        finally:
            session.close()

    def close(self) -> None:
        if self.engine is not None:
            self.engine.dispose()
        self.engine = None
        self.Session = None

    @staticmethod
    def _conversation_row(session, owner_id: str, conversation_id: str):
        return session.get(ProfessionalAgentConversationModel, (owner_id, conversation_id))

    def touch_conversation(
        self,
        *,
        owner_id: str,
        conversation_id: str,
        run_id: str = "",
        turn_id: str = "",
        status: str = "active",
    ) -> None:
        owner = _clean(owner_id, "anonymous")[:191]
        conversation = _clean(conversation_id)[:191]
        if not conversation:
            raise ValueError("conversation id is required")
        session = self._session()
        try:
            row = self._conversation_row(session, owner, conversation)
            if row is None:
                row = ProfessionalAgentConversationModel(owner_id=owner, conversation_id=conversation)
                session.add(row)
            if run_id:
                row.active_run_id = _clean(run_id)[:191]
            if turn_id:
                row.latest_turn_id = _clean(turn_id)[:191]
            row.status = _clean(status, "active")[:40]
            row.deleted_at = None
            row.updated_at = datetime.now()
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def append_message(
        self,
        *,
        owner_id: str,
        conversation_id: str,
        message_key: str,
        role: str,
        message_type: str,
        content: Any,
        run_id: str = "",
        turn_id: str = "",
        tool_name: str = "",
        tool_call_id: str = "",
        status: str = "active",
    ) -> dict[str, Any]:
        owner = _clean(owner_id, "anonymous")[:191]
        conversation = _clean(conversation_id)[:191]
        key = _clean(message_key)[:191]
        if not conversation or not key:
            raise ValueError("conversation id and message key are required")
        session = self._session()
        try:
            existing = (
                session.query(ProfessionalAgentMessageModel)
                .filter(
                    ProfessionalAgentMessageModel.owner_id == owner,
                    ProfessionalAgentMessageModel.conversation_id == conversation,
                    ProfessionalAgentMessageModel.message_key == key,
                )
                .one_or_none()
            )
            if existing is None:
                existing = ProfessionalAgentMessageModel(
                    owner_id=owner,
                    conversation_id=conversation,
                    run_id=_clean(run_id)[:191],
                    turn_id=_clean(turn_id)[:191],
                    message_key=key,
                    role=_clean(role, "assistant")[:24],
                    message_type=_clean(message_type, "message")[:40],
                    tool_name=_clean(tool_name)[:120],
                    tool_call_id=_clean(tool_call_id)[:191],
                    content_json=_dump(content),
                    created_at=datetime.now(),
                )
                session.add(existing)
            row = self._conversation_row(session, owner, conversation)
            if row is None:
                row = ProfessionalAgentConversationModel(owner_id=owner, conversation_id=conversation)
                session.add(row)
            if run_id:
                row.active_run_id = _clean(run_id)[:191]
            if turn_id:
                row.latest_turn_id = _clean(turn_id)[:191]
            row.status = _clean(status, "active")[:40]
            row.deleted_at = None
            row.updated_at = datetime.now()
            session.commit()
            message = self._message_dict(existing)
            self._index_message(
                owner_id=owner,
                conversation_id=conversation,
                message=message,
            )
            return message
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _index_message(
        self,
        *,
        owner_id: str,
        conversation_id: str,
        message: Mapping[str, Any],
    ) -> None:
        message_id = int(message.get("id") or 0)
        if not message_id or message.get("role") == "tool":
            return
        search_text = _message_search_text(message.get("content"))
        if not search_text:
            return
        chunks = _chunk_search_text(search_text)
        if not chunks:
            return
        hashes = [hashlib.sha256(text.encode("utf-8")).hexdigest() for text in chunks]
        session = self._session()
        try:
            existing = {
                row.chunk_index: row
                for row in session.query(ProfessionalAgentMemoryChunkModel)
                .filter(
                    ProfessionalAgentMemoryChunkModel.owner_id == owner_id,
                    ProfessionalAgentMemoryChunkModel.conversation_id == conversation_id,
                    ProfessionalAgentMemoryChunkModel.message_id == message_id,
                )
                .all()
            }
            for index, text_value in enumerate(chunks):
                row = existing.get(index)
                if row is None:
                    row = ProfessionalAgentMemoryChunkModel(
                        owner_id=owner_id,
                        conversation_id=conversation_id,
                        message_id=message_id,
                        chunk_index=index,
                        role=_clean(message.get("role"), "assistant")[:24],
                        message_type=_clean(message.get("messageType"), "message")[:40],
                    )
                    session.add(row)
                row.role = _clean(message.get("role"), "assistant")[:24]
                row.message_type = _clean(message.get("messageType"), "message")[:40]
                row.search_text = text_value
                content_changed = row.content_hash != hashes[index]
                row.content_hash = hashes[index]
                if content_changed:
                    row.embedding_json = None
                    row.embedding_model = ""
                row.updated_at = datetime.now()
            for index, row in existing.items():
                if index >= len(chunks):
                    session.delete(row)
            session.commit()
        except Exception:
            session.rollback()
        finally:
            session.close()

    def search_relevant_messages(
        self,
        *,
        owner_id: str,
        conversation_id: str,
        query: object,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        owner = _clean(owner_id, "anonymous")[:191]
        conversation = _clean(conversation_id)[:191]
        query_text = _message_search_text({"message": query})
        if not conversation or not query_text:
            return []
        query_terms = professional_retrieval_terms(query_text)
        query_vector = professional_query_embedding(query_text)
        self._backfill_message_index(owner_id=owner, conversation_id=conversation)
        session = self._session()
        try:
            rows = (
                session.query(ProfessionalAgentMemoryChunkModel)
                .filter(
                    ProfessionalAgentMemoryChunkModel.owner_id == owner,
                    ProfessionalAgentMemoryChunkModel.conversation_id == conversation,
                )
                .order_by(ProfessionalAgentMemoryChunkModel.id.desc())
                .limit(2000)
                .all()
            )
            embedding_model = professional_embedding_model()
            missing_rows = [
                row
                for row in rows
                if not row.embedding_json or row.embedding_model != embedding_model
            ]
            missing_vectors = embed_professional_texts([row.search_text for row in missing_rows])
            if len(missing_vectors) == len(missing_rows):
                for row, vector in zip(missing_rows, missing_vectors):
                    row.embedding_json = json.dumps(vector, separators=(",", ":"))
                    row.embedding_model = embedding_model
                    row.updated_at = datetime.now()
                session.commit()
            scored: list[tuple[float, int, ProfessionalAgentMemoryChunkModel]] = []
            for row in rows:
                terms = professional_retrieval_terms(row.search_text)
                keyword_score = min(1.0, len(query_terms & terms) / max(1, min(12, len(query_terms))) * 1.4)
                vector_score = 0.0
                if query_vector and row.embedding_json:
                    try:
                        vector_score = professional_cosine_similarity(query_vector, json.loads(row.embedding_json))
                    except (TypeError, ValueError, json.JSONDecodeError):
                        vector_score = 0.0
                score = 0.7 * vector_score + 0.3 * keyword_score if query_vector else keyword_score
                if score > 0:
                    scored.append((score, -int(row.id or 0), row))
            scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
            return [
                {
                    "messageId": int(row.message_id),
                    "role": row.role,
                    "messageType": row.message_type,
                    "text": row.search_text[:1800],
                    "score": round(score, 4),
                    "retrieval": "hybrid" if query_vector else "keyword",
                }
                for score, _index, row in scored[:max(1, min(20, limit))]
            ]
        finally:
            session.close()

    def _backfill_message_index(self, *, owner_id: str, conversation_id: str) -> None:
        session = self._session()
        try:
            indexed_ids = {
                int(value)
                for (value,) in session.query(ProfessionalAgentMemoryChunkModel.message_id)
                .filter(
                    ProfessionalAgentMemoryChunkModel.owner_id == owner_id,
                    ProfessionalAgentMemoryChunkModel.conversation_id == conversation_id,
                )
                .distinct()
                .all()
            }
            messages = (
                session.query(ProfessionalAgentMessageModel)
                .filter(
                    ProfessionalAgentMessageModel.owner_id == owner_id,
                    ProfessionalAgentMessageModel.conversation_id == conversation_id,
                    ProfessionalAgentMessageModel.role != "tool",
                )
                .order_by(ProfessionalAgentMessageModel.id.desc())
                .limit(2000)
                .all()
            )
            pending = [message for message in reversed(messages) if int(message.id or 0) not in indexed_ids]
            for message in pending:
                text_value = _message_search_text(_load(message.content_json, {}))
                for chunk_index, chunk in enumerate(_chunk_search_text(text_value)):
                    session.add(ProfessionalAgentMemoryChunkModel(
                        owner_id=owner_id,
                        conversation_id=conversation_id,
                        message_id=int(message.id),
                        chunk_index=chunk_index,
                        role=message.role,
                        message_type=message.message_type,
                        search_text=chunk,
                        content_hash=hashlib.sha256(chunk.encode("utf-8")).hexdigest(),
                        embedding_json=None,
                        embedding_model="",
                        created_at=message.created_at or datetime.now(),
                        updated_at=datetime.now(),
                    ))
            if pending:
                session.commit()
        except Exception:
            session.rollback()
        finally:
            session.close()

    @staticmethod
    def _message_dict(row: ProfessionalAgentMessageModel) -> dict[str, Any]:
        return {
            "id": int(row.id or 0),
            "runId": row.run_id,
            "turnId": row.turn_id,
            "messageKey": row.message_key,
            "role": row.role,
            "messageType": row.message_type,
            "toolName": row.tool_name,
            "toolCallId": row.tool_call_id,
            "content": _load(row.content_json, {}),
            "createdAt": row.created_at.isoformat(timespec="seconds") if row.created_at else "",
        }

    def upsert_snapshot(self, *, owner_id: str, conversation_id: str, state_key: str, payload: Any) -> None:
        owner = _clean(owner_id, "anonymous")[:191]
        conversation = _clean(conversation_id)[:191]
        key = _clean(state_key)[:120]
        if not conversation or not key:
            raise ValueError("conversation id and state key are required")
        session = self._session()
        try:
            row = session.get(ProfessionalAgentStateSnapshotModel, (owner, conversation, key))
            if row is None:
                row = ProfessionalAgentStateSnapshotModel(owner_id=owner, conversation_id=conversation, state_key=key)
                session.add(row)
            row.payload_json = _dump(payload)
            row.updated_at = datetime.now()
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def sync_snapshots(
        self,
        *,
        owner_id: str,
        conversation_id: str,
        values: Mapping[str, Any],
        managed_keys: set[str] | None = None,
    ) -> None:
        owner = _clean(owner_id, "anonymous")[:191]
        conversation = _clean(conversation_id)[:191]
        if not conversation:
            raise ValueError("conversation id is required")
        clean_values = {_clean(key)[:120]: value for key, value in values.items() if _clean(key)}
        session = self._session()
        try:
            if managed_keys:
                stale_keys = {_clean(key)[:120] for key in managed_keys if _clean(key)} - set(clean_values)
                if stale_keys:
                    session.execute(
                        delete(ProfessionalAgentStateSnapshotModel).where(
                            ProfessionalAgentStateSnapshotModel.owner_id == owner,
                            ProfessionalAgentStateSnapshotModel.conversation_id == conversation,
                            ProfessionalAgentStateSnapshotModel.state_key.in_(stale_keys),
                        )
                    )
            for key, payload in clean_values.items():
                row = session.get(ProfessionalAgentStateSnapshotModel, (owner, conversation, key))
                if row is None:
                    row = ProfessionalAgentStateSnapshotModel(owner_id=owner, conversation_id=conversation, state_key=key)
                    session.add(row)
                row.payload_json = _dump(payload)
                row.updated_at = datetime.now()
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def load_snapshots(self, *, owner_id: str, conversation_id: str) -> dict[str, Any]:
        owner = _clean(owner_id, "anonymous")[:191]
        conversation = _clean(conversation_id)[:191]
        session = self._session()
        try:
            rows = (
                session.query(ProfessionalAgentStateSnapshotModel)
                .filter(
                    ProfessionalAgentStateSnapshotModel.owner_id == owner,
                    ProfessionalAgentStateSnapshotModel.conversation_id == conversation,
                )
                .all()
            )
            return {row.state_key: _load(row.payload_json, {}) for row in rows}
        finally:
            session.close()

    def load_messages(
        self,
        *,
        owner_id: str,
        conversation_id: str,
        max_turns: int = 12,
        max_chars: int = 24000,
    ) -> list[dict[str, Any]]:
        owner = _clean(owner_id, "anonymous")[:191]
        conversation = _clean(conversation_id)[:191]
        session = self._session()
        try:
            rows = (
                session.query(ProfessionalAgentMessageModel)
                .filter(
                    ProfessionalAgentMessageModel.owner_id == owner,
                    ProfessionalAgentMessageModel.conversation_id == conversation,
                )
                .order_by(ProfessionalAgentMessageModel.id.desc())
                .limit(2000)
                .all()
            )
            messages = [self._message_dict(row) for row in reversed(rows)]
        finally:
            session.close()
        if not messages:
            return []

        groups: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
        current_group = "history"
        for message in messages:
            if message.get("role") == "user":
                current_group = f"user-{message['id']}"
            groups.setdefault(current_group, []).append(message)
        selected: list[list[dict[str, Any]]] = []
        used_chars = 0
        for group in reversed(list(groups.values())):
            group_chars = sum(len(json.dumps(item, ensure_ascii=False)) for item in group)
            if selected and (len(selected) >= max(1, max_turns) or used_chars + group_chars > max(1000, max_chars)):
                break
            selected.append(group)
            used_chars += group_chars
        return [item for group in reversed(selected) for item in group]

    def load_model_context(
        self,
        *,
        owner_id: str,
        conversation_id: str,
        max_turns: int = 12,
        max_chars: int = 24000,
        query: object = "",
        project_id: str = "",
        brand_id: str = "",
        include_long_term: bool = True,
    ) -> dict[str, Any]:
        snapshots = self.load_snapshots(owner_id=owner_id, conversation_id=conversation_id)
        selected_snapshot_keys = (
            "creative_context",
            "creative_brief",
            "product_profile",
            "latest_prompt_plan",
            "latest_proposal",
            "quality_result",
            "knowledge_context",
            "dialogue_response",
            "user_preferences",
            "workflow_state",
        )
        context = {
            "conversationId": _clean(conversation_id),
            "messages": self.load_messages(
                owner_id=owner_id,
                conversation_id=conversation_id,
                max_turns=max_turns,
                max_chars=max_chars,
            ),
            "memory": {key: snapshots[key] for key in selected_snapshot_keys if key in snapshots},
            "longTermMemories": self.search_long_term_memories(
                owner_id=owner_id,
                query=query,
                conversation_id=conversation_id,
                project_id=project_id,
                brand_id=brand_id,
                limit=8,
            ) if include_long_term else [],
        }
        if _clean(query):
            context["relevantMessages"] = self.search_relevant_messages(
                owner_id=owner_id,
                conversation_id=conversation_id,
                query=query,
                limit=8,
            )
        return context

    def find_conversation_by_run(self, *, owner_id: str, run_id: str) -> dict[str, Any] | None:
        owner = _clean(owner_id, "anonymous")[:191]
        active_run = _clean(run_id)[:191]
        session = self._session()
        try:
            row = (
                session.query(ProfessionalAgentConversationModel)
                .filter(
                    ProfessionalAgentConversationModel.owner_id == owner,
                    ProfessionalAgentConversationModel.active_run_id == active_run,
                    ProfessionalAgentConversationModel.deleted_at.is_(None),
                )
                .order_by(desc(ProfessionalAgentConversationModel.updated_at))
                .first()
            )
            if row is None:
                return None
            return {
                "ownerId": row.owner_id,
                "conversationId": row.conversation_id,
                "activeRunId": row.active_run_id,
                "latestTurnId": row.latest_turn_id,
                "status": row.status,
            }
        finally:
            session.close()

    def delete_conversation(self, *, owner_id: str, conversation_id: str) -> int:
        owner = _clean(owner_id, "anonymous")[:191]
        conversation = _clean(conversation_id)[:191]
        session = self._session()
        try:
            run_ids = [
                value
                for (value,) in session.query(ProfessionalAgentRunModel.run_id).filter(
                    ProfessionalAgentRunModel.owner_id == owner,
                    ProfessionalAgentRunModel.conversation_id == conversation,
                ).all()
            ]
            if run_ids:
                session.query(ProfessionalAgentRunEventModel).filter(
                    ProfessionalAgentRunEventModel.run_id.in_(run_ids),
                ).delete(synchronize_session=False)
                session.query(ProfessionalAgentRunModel).filter(
                    ProfessionalAgentRunModel.run_id.in_(run_ids),
                ).delete(synchronize_session=False)
            message_count = session.query(ProfessionalAgentMessageModel).filter(
                ProfessionalAgentMessageModel.owner_id == owner,
                ProfessionalAgentMessageModel.conversation_id == conversation,
            ).delete(synchronize_session=False)
            session.query(ProfessionalAgentMemoryChunkModel).filter(
                ProfessionalAgentMemoryChunkModel.owner_id == owner,
                ProfessionalAgentMemoryChunkModel.conversation_id == conversation,
            ).delete(synchronize_session=False)
            session.query(ProfessionalAgentStateSnapshotModel).filter(
                ProfessionalAgentStateSnapshotModel.owner_id == owner,
                ProfessionalAgentStateSnapshotModel.conversation_id == conversation,
            ).delete(synchronize_session=False)
            session.query(ProfessionalAgentLongTermMemoryModel).filter(
                ProfessionalAgentLongTermMemoryModel.owner_id == owner,
                ProfessionalAgentLongTermMemoryModel.scope == "conversation",
                ProfessionalAgentLongTermMemoryModel.scope_id == conversation,
            ).delete(synchronize_session=False)
            session.query(ProfessionalAgentMemoryJobModel).filter(
                ProfessionalAgentMemoryJobModel.owner_id == owner,
                ProfessionalAgentMemoryJobModel.conversation_id == conversation,
            ).delete(synchronize_session=False)
            session.query(ProfessionalAgentConversationModel).filter(
                ProfessionalAgentConversationModel.owner_id == owner,
                ProfessionalAgentConversationModel.conversation_id == conversation,
            ).delete(synchronize_session=False)
            session.commit()
            return int(message_count or 0)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def clear_conversations(self, *, owner_id: str) -> int:
        """Clear conversation history without deleting user/brand/project preferences."""
        owner = _clean(owner_id, "anonymous")
        session = self._session()
        try:
            conversation_ids = [
                value for (value,) in session.query(ProfessionalAgentConversationModel.conversation_id)
                .filter(ProfessionalAgentConversationModel.owner_id == owner)
                .all()
            ]
            if conversation_ids:
                run_ids = [
                    value for (value,) in session.query(ProfessionalAgentRunModel.run_id)
                    .filter(
                        ProfessionalAgentRunModel.owner_id == owner,
                        ProfessionalAgentRunModel.conversation_id.in_(conversation_ids),
                    ).all()
                ]
                if run_ids:
                    session.query(ProfessionalAgentRunEventModel).filter(
                        ProfessionalAgentRunEventModel.run_id.in_(run_ids),
                    ).delete(synchronize_session=False)
                    session.query(ProfessionalAgentRunModel).filter(
                        ProfessionalAgentRunModel.run_id.in_(run_ids),
                    ).delete(synchronize_session=False)
                session.query(ProfessionalAgentMessageModel).filter(
                    ProfessionalAgentMessageModel.owner_id == owner,
                    ProfessionalAgentMessageModel.conversation_id.in_(conversation_ids),
                ).delete(synchronize_session=False)
                session.query(ProfessionalAgentMemoryChunkModel).filter(
                    ProfessionalAgentMemoryChunkModel.owner_id == owner,
                    ProfessionalAgentMemoryChunkModel.conversation_id.in_(conversation_ids),
                ).delete(synchronize_session=False)
                session.query(ProfessionalAgentStateSnapshotModel).filter(
                    ProfessionalAgentStateSnapshotModel.owner_id == owner,
                    ProfessionalAgentStateSnapshotModel.conversation_id.in_(conversation_ids),
                ).delete(synchronize_session=False)
                session.query(ProfessionalAgentMemoryJobModel).filter(
                    ProfessionalAgentMemoryJobModel.owner_id == owner,
                    ProfessionalAgentMemoryJobModel.conversation_id.in_(conversation_ids),
                ).delete(synchronize_session=False)
                session.query(ProfessionalAgentLongTermMemoryModel).filter(
                    ProfessionalAgentLongTermMemoryModel.owner_id == owner,
                    ProfessionalAgentLongTermMemoryModel.scope == "conversation",
                    ProfessionalAgentLongTermMemoryModel.scope_id.in_(conversation_ids),
                ).delete(synchronize_session=False)
                session.query(ProfessionalAgentConversationModel).filter(
                    ProfessionalAgentConversationModel.owner_id == owner,
                ).delete(synchronize_session=False)
            session.commit()
            return len(conversation_ids)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def clear_owner(self, *, owner_id: str) -> int:
        owner = _clean(owner_id, "anonymous")[:191]
        session = self._session()
        try:
            run_ids = [
                value
                for (value,) in session.query(ProfessionalAgentRunModel.run_id).filter(
                    ProfessionalAgentRunModel.owner_id == owner,
                ).all()
            ]
            if run_ids:
                session.query(ProfessionalAgentRunEventModel).filter(
                    ProfessionalAgentRunEventModel.run_id.in_(run_ids),
                ).delete(synchronize_session=False)
                session.query(ProfessionalAgentRunModel).filter(
                    ProfessionalAgentRunModel.run_id.in_(run_ids),
                ).delete(synchronize_session=False)
            message_count = session.query(ProfessionalAgentMessageModel).filter(
                ProfessionalAgentMessageModel.owner_id == owner,
            ).delete(synchronize_session=False)
            session.query(ProfessionalAgentMemoryChunkModel).filter(
                ProfessionalAgentMemoryChunkModel.owner_id == owner,
            ).delete(synchronize_session=False)
            session.query(ProfessionalAgentStateSnapshotModel).filter(
                ProfessionalAgentStateSnapshotModel.owner_id == owner,
            ).delete(synchronize_session=False)
            session.query(ProfessionalAgentLongTermMemoryModel).filter(
                ProfessionalAgentLongTermMemoryModel.owner_id == owner,
            ).delete(synchronize_session=False)
            session.query(ProfessionalAgentMemoryDigestModel).filter(
                ProfessionalAgentMemoryDigestModel.owner_id == owner,
            ).delete(synchronize_session=False)
            session.query(ProfessionalAgentMemoryJobModel).filter(
                ProfessionalAgentMemoryJobModel.owner_id == owner,
            ).delete(synchronize_session=False)
            session.query(ProfessionalAgentConversationModel).filter(
                ProfessionalAgentConversationModel.owner_id == owner,
            ).delete(synchronize_session=False)
            session.commit()
            return int(message_count or 0)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


ecommerce_agent_memory_service = EcommerceAgentMemoryService()
