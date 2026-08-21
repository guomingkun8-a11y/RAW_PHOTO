from __future__ import annotations

"""Professional knowledge retrieval backed by MySQL and Qdrant.

Markdown files remain the version-controlled source. Documents and chunks are
synced into SQL as canonical runtime data; Qdrant is a rebuildable vector
index. Keyword retrieval remains available when the embedding service is down.
"""

from functools import lru_cache
import hashlib
import math
import os
from pathlib import Path
import re
from threading import RLock
import time
from typing import Any, Mapping

from curl_cffi import requests

from services.platform.config import config
from services.platform.proxy_service import proxy_settings
from services.providers.openai_relay_pool import current_relay_account, run_with_relay_pool
from services.ecommerce import professional_knowledge_index
from services.ecommerce.professional_knowledge_store import professional_knowledge_store


KNOWLEDGE_ROOT = Path(__file__).resolve().parents[2] / "resources" / "professional_knowledge"
WORD_RE = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]", re.IGNORECASE)
MAX_CHUNK_CHARS = 1800
CHUNK_OVERLAP_CHARS = 220
EMBEDDING_TIMEOUT_SECONDS = 90
VECTOR_WEIGHT = 0.7
KEYWORD_WEIGHT = 0.3
_INDEX_LOCK = RLock()
_EMBEDDING_FAILURE_UNTIL = 0.0


def _clean(value: object, limit: int = 6000) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _terms(value: str) -> set[str]:
    normalized = _clean(value).lower()
    terms = set(WORD_RE.findall(normalized))
    chinese = "".join(char for char in normalized if "\u4e00" <= char <= "\u9fff")
    terms.update(chinese[index:index + 2] for index in range(max(0, len(chinese) - 1)))
    return {term for term in terms if term}


def _vector_enabled() -> bool:
    configured = os.getenv("GMKRAW_PROFESSIONAL_KNOWLEDGE_VECTOR_ENABLED")
    if configured is not None:
        return configured.strip().lower() in {"1", "true", "yes", "on"}
    if _dedicated_embedding_base_url():
        return True
    relay = config.get_openai_relay_settings()
    return bool(relay.get("enabled") and relay.get("base_url"))


def _embedding_model() -> str:
    configured = str(
        os.getenv("GMKRAW_PROFESSIONAL_KNOWLEDGE_EMBEDDING_MODEL")
        or os.getenv("GMKRAW_EMBEDDING_MODEL")
        or ""
    ).strip()
    relay = config.get_openai_relay_settings()
    return configured or str(relay.get("embedding_model") or "text-embedding-3-small").strip()


def _dedicated_embedding_base_url() -> str:
    return str(
        os.getenv("GMKRAW_PROFESSIONAL_KNOWLEDGE_EMBEDDING_BASE_URL")
        or os.getenv("GMKRAW_EMBEDDING_BASE_URL")
        or ""
    ).strip().rstrip("/")


def _dedicated_embedding_api_key() -> str:
    return str(
        os.getenv("GMKRAW_PROFESSIONAL_KNOWLEDGE_EMBEDDING_API_KEY")
        or os.getenv("GMKRAW_EMBEDDING_API_KEY")
        or ""
    ).strip()


def _active_relay_settings() -> dict[str, object]:
    relay = dict(config.get_openai_relay_settings())
    account = current_relay_account()
    if account is not None:
        relay["base_url"] = account.base_url
        relay["api_key"] = account.api_key
    return relay


def _embedding_url(path: str) -> str:
    base_url = _dedicated_embedding_base_url()
    if not base_url:
        relay = _active_relay_settings()
        base_url = str(relay.get("base_url") or "").strip().rstrip("/")
    normalized = "/" + path.strip("/")
    if base_url.endswith("/v1") and normalized.startswith("/v1/"):
        normalized = normalized.removeprefix("/v1")
    return f"{base_url}{normalized}"


def _embedding_headers() -> dict[str, str]:
    api_key = _dedicated_embedding_api_key()
    if not _dedicated_embedding_base_url() and not api_key:
        api_key = str(_active_relay_settings().get("api_key") or "").strip()
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _embedding_request(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []

    def call() -> list[list[float]]:
        response = requests.post(
            _embedding_url("/v1/embeddings"),
            headers=_embedding_headers(),
            json={"model": _embedding_model(), "input": texts},
            timeout=EMBEDDING_TIMEOUT_SECONDS,
            **proxy_settings.build_session_kwargs(),
        )
        if response.status_code < 200 or response.status_code >= 300:
            raise RuntimeError(f"embedding endpoint returned HTTP {response.status_code}")
        body = response.json()
        rows = body.get("data") if isinstance(body, Mapping) else None
        if not isinstance(rows, list):
            raise RuntimeError("embedding response has no data")
        ordered: list[tuple[int, list[float]]] = []
        for index, row in enumerate(rows):
            if not isinstance(row, Mapping) or not isinstance(row.get("embedding"), list):
                continue
            ordered.append((int(row.get("index", index)), [float(item) for item in row["embedding"]]))
        ordered.sort(key=lambda item: item[0])
        vectors = [vector for _index, vector in ordered]
        if len(vectors) != len(texts):
            raise RuntimeError("embedding response count does not match input count")
        return vectors

    if _dedicated_embedding_base_url():
        return call()
    return run_with_relay_pool(config.get_openai_relay_settings(), "professional_knowledge_embeddings", call)


@lru_cache(maxsize=128)
def _cached_query_embedding(query: str, model: str) -> tuple[float, ...]:
    return tuple(_embedding_request([query])[0])


def _chunk_content(section: Mapping[str, str]) -> list[dict[str, str]]:
    content = _clean(section.get("content"), 12000)
    if not content:
        return []
    if len(content) <= MAX_CHUNK_CHARS:
        return [{**dict(section), "chunkId": section["id"]}]

    chunks: list[dict[str, str]] = []
    start = 0
    chunk_number = 1
    while start < len(content):
        end = min(len(content), start + MAX_CHUNK_CHARS)
        if end < len(content):
            split_at = max(content.rfind("。", start, end), content.rfind("\n", start, end))
            if split_at > start + MAX_CHUNK_CHARS // 2:
                end = split_at + 1
        chunk = content[start:end].strip()
        if chunk:
            chunks.append({
                "id": f"{section['id']}:{chunk_number}",
                "chunkId": f"{section['id']}:{chunk_number}",
                "documentId": section.get("documentId", ""),
                "title": section["title"],
                "content": chunk,
            })
            chunk_number += 1
        if end >= len(content):
            break
        start = max(start + 1, end - CHUNK_OVERLAP_CHARS)
    return chunks


def _vector_text(chunk: Mapping[str, str]) -> str:
    return f"{chunk.get('title', '')}\n{chunk.get('content', '')}".strip()


def _sync_index(
    documents: list[dict[str, str]],
    chunks: list[dict[str, str]],
) -> list[dict[str, Any]]:
    global _EMBEDDING_FAILURE_UNTIL
    if not chunks:
        return []
    with _INDEX_LOCK:
        prepared_chunks = []
        for position, chunk in enumerate(chunks):
            search_text = _vector_text(chunk)
            prepared_chunks.append({
                **chunk,
                "position": position,
                "searchText": search_text,
                "contentHash": hashlib.sha256(search_text.encode("utf-8")).hexdigest(),
            })
        sync_result = professional_knowledge_store.sync(documents=documents, chunks=prepared_chunks)
        removed_ids = list(sync_result.get("removedChunkIds") or [])
        if removed_ids:
            professional_knowledge_index.delete_chunks(removed_ids)

        can_sync_vectors = (
            _vector_enabled()
            and professional_knowledge_index.enabled()
            and time.monotonic() >= _EMBEDDING_FAILURE_UNTIL
        )
        if can_sync_vectors:
            pending = professional_knowledge_store.chunks_requiring_vector(model=_embedding_model())
            try:
                for offset in range(0, len(pending), 32):
                    batch = pending[offset:offset + 32]
                    vectors = _embedding_request([str(item.get("searchText") or "") for item in batch])
                    synced_ids: list[str] = []
                    for chunk, vector in zip(batch, vectors):
                        chunk_id = str(chunk.get("chunkId") or "")
                        if professional_knowledge_index.upsert(
                            chunk_id=chunk_id,
                            vector=vector,
                            payload={
                                "documentId": chunk.get("documentId"),
                                "sectionId": chunk.get("id"),
                                "title": chunk.get("title"),
                                "contentHash": chunk.get("contentHash"),
                                "embeddingModel": _embedding_model(),
                            },
                        ):
                            synced_ids.append(chunk_id)
                    professional_knowledge_store.mark_vectors_synced(synced_ids, model=_embedding_model())
            except Exception:
                _EMBEDDING_FAILURE_UNTIL = time.monotonic() + 60
        return professional_knowledge_store.list_chunks()


def _keyword_score(query_terms: set[str], query_text: str, title: str, content: str) -> float:
    title_terms = _terms(title)
    content_terms = _terms(content)
    title_overlap = len(query_terms & title_terms)
    content_overlap = len(query_terms & content_terms)
    phrase_bonus = 0.0
    for phrase in ("详情页", "主图", "车图", "背景", "白底", "参考图", "文字", "执行", "生成", "专业"):
        if phrase in query_text and (phrase in title or phrase in content):
            phrase_bonus += 0.12
    raw = title_overlap * 0.55 + content_overlap * 0.08 + phrase_bonus
    return min(1.0, raw)


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return 0.0
    return max(0.0, dot / (left_norm * right_norm))


def professional_retrieval_terms(value: object) -> set[str]:
    """Return the same exact-match terms used by both knowledge and memory."""
    return _terms(_clean(value, 12000))


def professional_embedding_model() -> str:
    return _embedding_model()


def professional_cosine_similarity(left: list[float], right: list[float]) -> float:
    return _cosine(left, right)


def embed_professional_texts(texts: list[str]) -> list[list[float]]:
    """Embed text through RAW's relay, returning an empty list on fallback."""
    global _EMBEDDING_FAILURE_UNTIL
    cleaned = [_clean(text, 12000) for text in texts if _clean(text, 12000)]
    if (
        not cleaned
        or not _vector_enabled()
        or time.monotonic() < _EMBEDDING_FAILURE_UNTIL
    ):
        return []
    try:
        vectors: list[list[float]] = []
        for offset in range(0, len(cleaned), 32):
            vectors.extend(_embedding_request(cleaned[offset:offset + 32]))
        return vectors
    except Exception:
        _EMBEDDING_FAILURE_UNTIL = time.monotonic() + 60
        return []


def professional_query_embedding(query: object) -> list[float]:
    global _EMBEDDING_FAILURE_UNTIL
    text = _clean(query, 3000)
    if not text or not _vector_enabled() or time.monotonic() < _EMBEDDING_FAILURE_UNTIL:
        return []
    try:
        return list(_cached_query_embedding(text, _embedding_model()))
    except Exception:
        _EMBEDDING_FAILURE_UNTIL = time.monotonic() + 60
        return []


@lru_cache(maxsize=1)
def _load_professional_documents() -> tuple[dict[str, str], ...]:
    documents: list[dict[str, str]] = []
    if not KNOWLEDGE_ROOT.exists():
        return ()
    for path in sorted(KNOWLEDGE_ROOT.glob("*.md")):
        content = path.read_text(encoding="utf-8")
        title = path.stem
        for line in content.splitlines():
            if line.startswith("# "):
                title = _clean(line[2:], 120) or title
                break
        documents.append({
            "documentId": path.stem,
            "sourcePath": str(path.relative_to(KNOWLEDGE_ROOT)).replace("\\", "/"),
            "title": title,
            "content": content,
            "contentHash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        })
    return tuple(documents)


@lru_cache(maxsize=1)
def load_professional_knowledge() -> tuple[dict[str, str], ...]:
    sections: list[dict[str, str]] = []
    for document in _load_professional_documents():
        document_title = document["title"]
        current_title = document_title
        lines: list[str] = []

        def flush() -> None:
            content = _clean("\n".join(lines), 4000)
            if not content:
                return
            section_id = f"{document['documentId']}:{len(sections) + 1}"
            sections.append({
                "id": section_id,
                "documentId": document["documentId"],
                "title": current_title,
                "content": content,
            })

        for raw_line in document["content"].splitlines():
            if raw_line.startswith("# "):
                document_title = _clean(raw_line[2:], 120) or document_title
                current_title = document_title
                continue
            if raw_line.startswith("## "):
                flush()
                lines = []
                current_title = f"{document_title} / {_clean(raw_line[3:], 120)}"
                continue
            lines.append(raw_line)
        flush()
    return tuple(sections)


def sync_professional_knowledge_index() -> dict[str, Any]:
    documents = list(_load_professional_documents())
    source_chunks = [
        chunk
        for section in load_professional_knowledge()
        for chunk in _chunk_content(section)
    ]
    stored_chunks = _sync_index(documents, source_chunks) if source_chunks else []
    vector_ready = sum(1 for chunk in stored_chunks if chunk.get("vectorSyncedAt"))
    return {
        "documents": len(documents),
        "chunks": len(stored_chunks),
        "vectorReady": vector_ready,
        "vectorPending": max(0, len(stored_chunks) - vector_ready),
        "embeddingModel": _embedding_model(),
        "qdrantEnabled": professional_knowledge_index.enabled(),
    }


def retrieve_professional_knowledge(query: object, *, limit: int = 5) -> list[dict[str, Any]]:
    global _EMBEDDING_FAILURE_UNTIL
    query_text = _clean(query, 3000)
    query_terms = _terms(query_text)
    source_chunks = [chunk for section in load_professional_knowledge() for chunk in _chunk_content(section)]
    if not source_chunks:
        return []
    chunks = _sync_index(list(_load_professional_documents()), source_chunks)

    vector_scores: dict[str, float] = {}
    vector_used = False
    if (
        _vector_enabled()
        and professional_knowledge_index.enabled()
        and query_text
        and time.monotonic() >= _EMBEDDING_FAILURE_UNTIL
    ):
        try:
            query_vector = list(_cached_query_embedding(query_text, _embedding_model()))
            hits = professional_knowledge_index.search(
                vector=query_vector,
                limit=max(20, int(limit or 5) * 4),
            )
            for item in hits:
                payload = item.get("payload") if isinstance(item.get("payload"), Mapping) else {}
                chunk_id = str(payload.get("chunkId") or "")
                if chunk_id:
                    vector_scores[chunk_id] = float(item.get("score") or 0.0)
            vector_used = bool(vector_scores)
        except Exception:
            _EMBEDDING_FAILURE_UNTIL = time.monotonic() + 60

    scored: list[tuple[float, int, dict[str, Any]]] = []
    for index, chunk in enumerate(chunks):
        keyword_score = _keyword_score(query_terms, query_text, chunk["title"], chunk["content"])
        vector_score = vector_scores.get(chunk["chunkId"], 0.0)
        score = VECTOR_WEIGHT * vector_score + KEYWORD_WEIGHT * keyword_score if vector_used else keyword_score
        if score > 0:
            item = dict(chunk)
            item["score"] = round(score, 4)
            item["retrieval"] = "hybrid" if vector_used else "keyword"
            scored.append((score, -index, item))
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    selected = [item for score, _index, item in scored if score > 0][:max(1, limit)]
    if not selected:
        preferred = [
            chunk
            for chunk in chunks
            if "专业范围" in chunk["title"] or "对话优先" in chunk["title"]
        ]
        fallback_chunks = preferred or chunks
        selected = [
            dict(chunk, score=0.0, retrieval="hybrid" if vector_used else "keyword")
            for chunk in fallback_chunks[:max(1, min(2, limit))]
        ]
    return selected


def knowledge_context_for_model(query: object, *, limit: int = 5) -> dict[str, Any]:
    items = retrieve_professional_knowledge(query, limit=limit)
    retrieval_modes = {str(item.get("retrieval")) for item in items}
    mode = "hybrid" if "hybrid" in retrieval_modes else "keyword"
    return {
        "retrievalMode": mode,
        "sources": [
            {
                "id": item["id"],
                "title": item["title"],
                "score": item.get("score", 0.0),
                "retrieval": item.get("retrieval", mode),
            }
            for item in items
        ],
        "sections": items,
    }
