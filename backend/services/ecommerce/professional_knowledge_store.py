from __future__ import annotations

import hashlib
import os
from datetime import datetime
from typing import Any, Mapping

from sqlalchemy import BigInteger, Column, DateTime, Integer, String, Text, create_engine
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import declarative_base, sessionmaker

from services.platform.enterprise_schema import resolve_enterprise_database_url


Base = declarative_base()
LONG_TEXT = Text().with_variant(LONGTEXT, "mysql")


class ProfessionalKnowledgeDocumentModel(Base):
    __tablename__ = "professional_knowledge_documents"

    document_id = Column(String(191), primary_key=True)
    source_path = Column(String(500), nullable=False)
    title = Column(String(191), nullable=False)
    content = Column(LONG_TEXT, nullable=False)
    content_hash = Column(String(64), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)


class ProfessionalKnowledgeChunkModel(Base):
    __tablename__ = "professional_knowledge_chunks"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    chunk_id = Column(String(191), nullable=False, unique=True)
    document_id = Column(String(191), nullable=False)
    section_id = Column(String(191), nullable=False)
    position = Column(Integer, nullable=False)
    title = Column(String(191), nullable=False)
    content = Column(LONG_TEXT, nullable=False)
    search_text = Column(LONG_TEXT, nullable=False)
    content_hash = Column(String(64), nullable=False)
    vector_model = Column(String(191), nullable=False, default="")
    vector_synced_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)


def _clean(value: object, default: str = "", limit: int = 12000) -> str:
    result = str(value if value is not None else default).strip()
    return (result or default)[:limit]


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class ProfessionalKnowledgeStore:
    """Canonical SQL storage for professional knowledge documents and chunks."""

    def __init__(self, database_url: str | None = None) -> None:
        self.database_url = _clean(database_url)
        self.engine = None
        self.Session = None
        self._init_error = ""
        if self.database_url:
            self._init_engine()

    def _resolved_database_url(self) -> str:
        return self.database_url or _clean(
            os.getenv("GMKRAW_PROFESSIONAL_KNOWLEDGE_DATABASE_URL")
        ) or resolve_enterprise_database_url()

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
            raise RuntimeError(f"professional knowledge database unavailable: {self._init_error}")
        return self.Session()

    def ensure_ready(self) -> None:
        session = self._session()
        session.close()

    def close(self) -> None:
        if self.engine is not None:
            self.engine.dispose()
        self.engine = None
        self.Session = None

    @staticmethod
    def _chunk_dict(row: ProfessionalKnowledgeChunkModel) -> dict[str, Any]:
        return {
            "id": row.section_id,
            "chunkId": row.chunk_id,
            "documentId": row.document_id,
            "position": int(row.position or 0),
            "title": row.title,
            "content": row.content,
            "searchText": row.search_text,
            "contentHash": row.content_hash,
            "vectorModel": row.vector_model,
            "vectorSyncedAt": row.vector_synced_at.isoformat(timespec="seconds") if row.vector_synced_at else "",
        }

    def sync(
        self,
        *,
        documents: list[Mapping[str, Any]],
        chunks: list[Mapping[str, Any]],
    ) -> dict[str, Any]:
        now = datetime.now()
        session = self._session()
        try:
            existing_documents = {
                row.document_id: row
                for row in session.query(ProfessionalKnowledgeDocumentModel).all()
            }
            document_ids: set[str] = set()
            for item in documents:
                document_id = _clean(item.get("documentId"), limit=191)
                if not document_id:
                    continue
                document_ids.add(document_id)
                content = _clean(item.get("content"), limit=1_000_000)
                content_hash = _clean(item.get("contentHash"), limit=64) or _hash(content)
                row = existing_documents.get(document_id)
                if row is None:
                    row = ProfessionalKnowledgeDocumentModel(
                        document_id=document_id,
                        created_at=now,
                    )
                    session.add(row)
                row.source_path = _clean(item.get("sourcePath"), limit=500)
                row.title = _clean(item.get("title"), document_id, 191)
                row.content = content
                row.content_hash = content_hash
                row.updated_at = now
            stale_documents = set(existing_documents) - document_ids
            if stale_documents:
                session.query(ProfessionalKnowledgeDocumentModel).filter(
                    ProfessionalKnowledgeDocumentModel.document_id.in_(stale_documents)
                ).delete(synchronize_session=False)

            existing_chunks = {
                row.chunk_id: row
                for row in session.query(ProfessionalKnowledgeChunkModel).all()
            }
            chunk_ids: set[str] = set()
            changed_chunk_ids: list[str] = []
            for position, item in enumerate(chunks):
                chunk_id = _clean(item.get("chunkId"), limit=191)
                if not chunk_id:
                    continue
                chunk_ids.add(chunk_id)
                search_text = _clean(
                    item.get("searchText") or f"{item.get('title', '')}\n{item.get('content', '')}",
                    limit=50_000,
                )
                content_hash = _clean(item.get("contentHash"), limit=64) or _hash(search_text)
                row = existing_chunks.get(chunk_id)
                content_changed = row is None or row.content_hash != content_hash
                if row is None:
                    row = ProfessionalKnowledgeChunkModel(chunk_id=chunk_id, created_at=now)
                    session.add(row)
                row.document_id = _clean(item.get("documentId"), limit=191)
                row.section_id = _clean(item.get("id"), chunk_id, 191)
                row.position = int(item.get("position", position) or 0)
                row.title = _clean(item.get("title"), limit=191)
                row.content = _clean(item.get("content"), limit=100_000)
                row.search_text = search_text
                row.content_hash = content_hash
                if content_changed:
                    row.vector_model = ""
                    row.vector_synced_at = None
                    changed_chunk_ids.append(chunk_id)
                row.updated_at = now

            stale_chunk_ids = sorted(set(existing_chunks) - chunk_ids)
            if stale_chunk_ids:
                session.query(ProfessionalKnowledgeChunkModel).filter(
                    ProfessionalKnowledgeChunkModel.chunk_id.in_(stale_chunk_ids)
                ).delete(synchronize_session=False)
            session.commit()
            return {
                "documents": len(document_ids),
                "chunks": len(chunk_ids),
                "changedChunkIds": changed_chunk_ids,
                "removedChunkIds": stale_chunk_ids,
            }
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def list_chunks(self) -> list[dict[str, Any]]:
        session = self._session()
        try:
            rows = session.query(ProfessionalKnowledgeChunkModel).order_by(
                ProfessionalKnowledgeChunkModel.position,
                ProfessionalKnowledgeChunkModel.id,
            ).all()
            return [self._chunk_dict(row) for row in rows]
        finally:
            session.close()

    def chunks_requiring_vector(self, *, model: str, limit: int = 5000) -> list[dict[str, Any]]:
        session = self._session()
        try:
            rows = session.query(ProfessionalKnowledgeChunkModel).filter(
                (ProfessionalKnowledgeChunkModel.vector_synced_at.is_(None))
                | (ProfessionalKnowledgeChunkModel.vector_model != _clean(model, limit=191))
            ).order_by(ProfessionalKnowledgeChunkModel.id).limit(max(1, min(20_000, int(limit)))).all()
            return [self._chunk_dict(row) for row in rows]
        finally:
            session.close()

    def mark_vectors_synced(self, chunk_ids: list[str], *, model: str) -> None:
        clean_ids = {_clean(chunk_id, limit=191) for chunk_id in chunk_ids if _clean(chunk_id)}
        if not clean_ids:
            return
        session = self._session()
        try:
            rows = session.query(ProfessionalKnowledgeChunkModel).filter(
                ProfessionalKnowledgeChunkModel.chunk_id.in_(clean_ids)
            ).all()
            now = datetime.now()
            for row in rows:
                row.vector_model = _clean(model, limit=191)
                row.vector_synced_at = now
                row.updated_at = now
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


professional_knowledge_store = ProfessionalKnowledgeStore()
