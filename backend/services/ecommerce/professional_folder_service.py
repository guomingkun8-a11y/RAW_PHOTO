from __future__ import annotations

import hashlib
import json
import mimetypes
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from PIL import Image
from sqlalchemy import BigInteger, Column, DateTime, Float, Integer, String, Text, UniqueConstraint, desc
from sqlalchemy.dialects.mysql import LONGTEXT

from services.ecommerce import professional_folder_index
from services.ecommerce.ecommerce_agent_memory_service import Base
from services.ecommerce.professional_knowledge_service import (
    embed_professional_texts,
    professional_query_embedding,
    professional_retrieval_terms,
)
from services.image.image_storage_service import image_storage_service
from services.platform.enterprise_schema import resolve_enterprise_database_url
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


LONG_TEXT = Text().with_variant(LONGTEXT, "mysql")
FOLDER_MAX_ITEMS = 300
FOLDER_MAX_FILE_BYTES = 50 * 1024 * 1024
FOLDER_MAX_TOTAL_BYTES = 500 * 1024 * 1024
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif", ".heic", ".heif"}


def _clean(value: object, default: str = "", limit: int = 12000) -> str:
    text = str(value if value is not None else default).strip()
    return (text or default)[:limit]


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _load(value: str, default: Any) -> Any:
    try:
        parsed = json.loads(value or "")
        return parsed
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _owner_id(identity: Mapping[str, object] | str | None) -> str:
    if isinstance(identity, str):
        return _clean(identity, "anonymous", 191)
    source = identity or {}
    return _clean(source.get("id") or source.get("username"), "anonymous", 191)


def _safe_name(value: object, default: str) -> str:
    name = Path(_clean(value, default, 240)).name
    name = re.sub(r"[^\w.()\-\u4e00-\u9fff ]+", "_", name).strip(" .")
    return (name or default)[:191]


def _relative_name(value: object, fallback: str) -> str:
    raw = _clean(value, fallback, 500).replace("\\", "/")
    parts = [re.sub(r"[^\w.()\-\u4e00-\u9fff ]+", "_", part).strip(" .") for part in raw.split("/")]
    return "/".join(part for part in parts if part)[:500] or fallback


def _classify(name: str, width: int = 0, height: int = 0) -> str:
    text = name.lower()
    rules = (
        ("main", ("主图", "main", "cover", "首图", "白底")),
        ("detail", ("详情", "detail", "长图", "卖点", "参数")),
        ("scene", ("场景", "scene", "生活", "氛围", "车图", "汽车")),
        ("banner", ("banner", "横幅", "海报", "活动")),
    )
    for category, markers in rules:
        if any(marker in text for marker in markers):
            return category
    if width and height:
        ratio = width / max(1, height)
        if ratio >= 1.65:
            return "banner"
        if ratio <= 0.72:
            return "detail"
    return "other"


class ProfessionalFolderAssetModel(Base):
    __tablename__ = "professional_agent_folder_assets"

    folder_id = Column(String(191), primary_key=True)
    owner_id = Column(String(191), nullable=False)
    conversation_id = Column(String(191), nullable=False, default="")
    name = Column(String(191), nullable=False)
    status = Column(String(24), nullable=False, default="ready")
    item_count = Column(Integer, nullable=False, default=0)
    total_bytes = Column(BigInteger().with_variant(Integer, "sqlite"), nullable=False, default=0)
    summary_json = Column(LONG_TEXT, nullable=False, default="{}")
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(DateTime, nullable=False, default=datetime.now)


class ProfessionalFolderItemModel(Base):
    __tablename__ = "professional_agent_folder_items"
    __table_args__ = (UniqueConstraint("folder_id", "relative_name", name="uq_prof_agent_folder_item_name"),)

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    folder_id = Column(String(191), nullable=False)
    owner_id = Column(String(191), nullable=False)
    relative_name = Column(String(500), nullable=False)
    filename = Column(String(191), nullable=False)
    mime_type = Column(String(120), nullable=False)
    size = Column(BigInteger().with_variant(Integer, "sqlite"), nullable=False, default=0)
    width = Column(Integer, nullable=False, default=0)
    height = Column(Integer, nullable=False, default=0)
    sha256 = Column(String(64), nullable=False)
    category = Column(String(40), nullable=False, default="other")
    storage_rel = Column(String(500), nullable=False)
    url = Column(String(2000), nullable=False)
    analysis_json = Column(LONG_TEXT, nullable=False, default="{}")
    status = Column(String(24), nullable=False, default="ready")
    created_at = Column(DateTime, nullable=False, default=datetime.now)


class ProfessionalBatchPlanModel(Base):
    __tablename__ = "professional_agent_batch_plans"

    plan_id = Column(String(191), primary_key=True)
    owner_id = Column(String(191), nullable=False)
    conversation_id = Column(String(191), nullable=False, default="")
    run_id = Column(String(191), nullable=False, default="")
    folder_id = Column(String(191), nullable=False)
    status = Column(String(32), nullable=False, default="proposed")
    request_json = Column(LONG_TEXT, nullable=False, default="{}")
    summary_json = Column(LONG_TEXT, nullable=False, default="{}")
    total_items = Column(Integer, nullable=False, default=0)
    completed_items = Column(Integer, nullable=False, default=0)
    failed_items = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(DateTime, nullable=False, default=datetime.now)


class ProfessionalBatchPlanItemModel(Base):
    __tablename__ = "professional_agent_batch_plan_items"
    __table_args__ = (UniqueConstraint("plan_id", "folder_item_id", name="uq_prof_agent_batch_plan_item"),)

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    plan_id = Column(String(191), nullable=False)
    folder_item_id = Column(BigInteger().with_variant(Integer, "sqlite"), nullable=False)
    item_index = Column(Integer, nullable=False, default=0)
    title = Column(String(191), nullable=False, default="")
    purpose = Column(String(500), nullable=False, default="")
    prompt = Column(LONG_TEXT, nullable=False)
    task_id = Column(String(191), nullable=False, default="")
    status = Column(String(24), nullable=False, default="pending")
    attempts = Column(Integer, nullable=False, default=0)
    error = Column(LONG_TEXT, nullable=False, default="")
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(DateTime, nullable=False, default=datetime.now)


class ProfessionalFolderAssetService:
    def __init__(self) -> None:
        self.engine = None
        self.Session = None

    def _session(self):
        if self.Session is None:
            self.engine = create_engine(resolve_enterprise_database_url(), pool_pre_ping=True, pool_recycle=3600)
            Base.metadata.create_all(self.engine)
            self.Session = sessionmaker(bind=self.engine)
        return self.Session()

    @staticmethod
    def _item_dict(row: ProfessionalFolderItemModel) -> dict[str, Any]:
        return {
            "id": int(row.id),
            "folderId": row.folder_id,
            "relativeName": row.relative_name,
            "name": row.filename,
            "type": row.mime_type,
            "size": int(row.size or 0),
            "width": int(row.width or 0),
            "height": int(row.height or 0),
            "category": row.category,
            "storageRel": row.storage_rel,
            "url": row.url,
            "analysis": _load(row.analysis_json, {}),
            "status": row.status,
        }

    def _folder_dict(self, row: ProfessionalFolderAssetModel, *, include_items: bool = True) -> dict[str, Any]:
        session = self._session()
        try:
            items = (
                session.query(ProfessionalFolderItemModel)
                .filter(ProfessionalFolderItemModel.folder_id == row.folder_id, ProfessionalFolderItemModel.owner_id == row.owner_id)
                .order_by(ProfessionalFolderItemModel.item_index.asc() if hasattr(ProfessionalFolderItemModel, "item_index") else ProfessionalFolderItemModel.id.asc())
                .all()
            )
        finally:
            session.close()
        result = {
            "folderId": row.folder_id,
            "ownerId": row.owner_id,
            "conversationId": row.conversation_id,
            "name": row.name,
            "status": row.status,
            "itemCount": int(row.item_count or 0),
            "totalBytes": int(row.total_bytes or 0),
            "summary": _load(row.summary_json, {}),
            "createdAt": row.created_at.isoformat() if row.created_at else "",
            "updatedAt": row.updated_at.isoformat() if row.updated_at else "",
        }
        if include_items:
            result["items"] = [self._item_dict(item) for item in items]
        return result

    def create_folder(
        self,
        *,
        owner_id: str,
        conversation_id: str,
        name: str,
        files: list[tuple[bytes, str, str, str]],
        base_url: str = "",
    ) -> dict[str, Any]:
        if not files:
            raise ValueError("folder must contain at least one image")
        if len(files) > FOLDER_MAX_ITEMS:
            raise ValueError(f"folder contains too many images; maximum is {FOLDER_MAX_ITEMS}")
        total_bytes = sum(len(payload) for payload, _filename, _mime, _relative in files)
        if total_bytes > FOLDER_MAX_TOTAL_BYTES:
            raise ValueError("folder exceeds the 500MB total size limit")
        folder_id = f"folder-{uuid4().hex}"
        owner = _owner_id(owner_id)
        folder_name = _safe_name(name, "上传文件夹")
        stored: list[dict[str, Any]] = []
        try:
            for index, (payload, raw_filename, raw_mime, raw_relative) in enumerate(files):
                filename = _safe_name(raw_filename, f"image-{index + 1}.png")
                relative_name = _relative_name(raw_relative, filename)
                suffix = Path(filename).suffix.lower()
                mime = _clean(raw_mime, mimetypes.guess_type(filename)[0] or "image/png", 120)
                if len(payload) > FOLDER_MAX_FILE_BYTES:
                    raise ValueError(f"{filename} exceeds the 50MB file limit")
                if not mime.startswith("image/") and suffix not in IMAGE_EXTENSIONS:
                    continue
                try:
                    with Image.open(__import__("io").BytesIO(payload)) as image:
                        width, height = image.size
                        image.verify()
                except Exception as exc:
                    raise ValueError(f"{filename} is not a valid image") from exc
                digest = hashlib.sha256(payload).hexdigest()
                stored_image = image_storage_service.save_task_asset(
                    payload,
                    owner_id=owner,
                    task_id=folder_id,
                    asset_index=str(index),
                    asset_type="folder-input",
                    filename=filename,
                    mime_type=mime,
                    base_url=base_url or None,
                )
                stored.append({
                    "relative_name": relative_name,
                    "filename": filename,
                    "mime_type": mime,
                    "size": len(payload),
                    "width": int(width),
                    "height": int(height),
                    "sha256": digest,
                    "category": _classify(relative_name, int(width), int(height)),
                    "storage_rel": stored_image.rel,
                    "url": stored_image.url,
                })
        except Exception:
            for item in stored:
                try:
                    image_storage_service.delete(str(item["storage_rel"]))
                except Exception:
                    pass
            raise
        if not stored:
            raise ValueError("folder contains no supported images")
        categories: dict[str, int] = {}
        mime_types: dict[str, int] = {}
        dimensions: dict[str, int] = {}
        for item in stored:
            categories[item["category"]] = categories.get(item["category"], 0) + 1
            mime_types[item["mime_type"]] = mime_types.get(item["mime_type"], 0) + 1
            key = f"{item['width']}x{item['height']}"
            dimensions[key] = dimensions.get(key, 0) + 1
        summary = {
            "fileCount": len(stored),
            "totalBytes": sum(int(item["size"]) for item in stored),
            "categories": categories,
            "mimeTypes": mime_types,
            "dimensions": dimensions,
            "samples": [
                {"id": index, "name": item["relative_name"], "category": item["category"], "width": item["width"], "height": item["height"], "url": item["url"]}
                for index, item in enumerate(stored[:8])
            ],
        }
        session = self._session()
        try:
            row = ProfessionalFolderAssetModel(
                folder_id=folder_id,
                owner_id=owner,
                conversation_id=_clean(conversation_id)[:191],
                name=folder_name,
                item_count=len(stored),
                total_bytes=summary["totalBytes"],
                summary_json=_dump(summary),
            )
            session.add(row)
            for item in stored:
                session.add(ProfessionalFolderItemModel(folder_id=folder_id, owner_id=owner, **item))
            session.commit()
            return self.get_folder(folder_id, owner_id=owner, include_items=True) or {}
        except Exception:
            session.rollback()
            for item in stored:
                try:
                    image_storage_service.delete(str(item["storage_rel"]))
                except Exception:
                    pass
            raise
        finally:
            session.close()

    def get_folder(self, folder_id: str, *, owner_id: str, include_items: bool = True) -> dict[str, Any] | None:
        session = self._session()
        try:
            row = session.query(ProfessionalFolderAssetModel).filter(
                ProfessionalFolderAssetModel.folder_id == _clean(folder_id, limit=191),
                ProfessionalFolderAssetModel.owner_id == _owner_id(owner_id),
                ProfessionalFolderAssetModel.status != "deleted",
            ).one_or_none()
            if row is None:
                return None
            summary = {
                "folderId": row.folder_id,
                "ownerId": row.owner_id,
                "conversationId": row.conversation_id,
                "name": row.name,
                "status": row.status,
                "itemCount": int(row.item_count or 0),
                "totalBytes": int(row.total_bytes or 0),
                "summary": _load(row.summary_json, {}),
                "createdAt": row.created_at.isoformat() if row.created_at else "",
                "updatedAt": row.updated_at.isoformat() if row.updated_at else "",
            }
            if include_items:
                items = session.query(ProfessionalFolderItemModel).filter(
                    ProfessionalFolderItemModel.folder_id == row.folder_id,
                    ProfessionalFolderItemModel.owner_id == row.owner_id,
                ).order_by(ProfessionalFolderItemModel.id.asc()).all()
                summary["items"] = [self._item_dict(item) for item in items]
            return summary
        finally:
            session.close()

    def list_folders(self, *, owner_id: str, conversation_id: str = "", limit: int = 50) -> list[dict[str, Any]]:
        session = self._session()
        try:
            query = session.query(ProfessionalFolderAssetModel).filter(
                ProfessionalFolderAssetModel.owner_id == _owner_id(owner_id),
                ProfessionalFolderAssetModel.status != "deleted",
            )
            if _clean(conversation_id):
                query = query.filter(ProfessionalFolderAssetModel.conversation_id == _clean(conversation_id)[:191])
            rows = query.order_by(desc(ProfessionalFolderAssetModel.updated_at)).limit(max(1, min(200, int(limit or 50)))).all()
            return [self.get_folder(row.folder_id, owner_id=owner_id, include_items=False) or {} for row in rows]
        finally:
            session.close()

    def delete_folder(self, folder_id: str, *, owner_id: str) -> bool:
        owner = _owner_id(owner_id)
        storage_paths: list[str] = []
        deleted_folder_id = ""
        session = self._session()
        try:
            row = session.query(ProfessionalFolderAssetModel).filter(
                ProfessionalFolderAssetModel.folder_id == _clean(folder_id, limit=191),
                ProfessionalFolderAssetModel.owner_id == owner,
            ).one_or_none()
            if row is None:
                return False
            items = session.query(ProfessionalFolderItemModel).filter(ProfessionalFolderItemModel.folder_id == row.folder_id).all()
            storage_paths = [item.storage_rel for item in items if item.storage_rel]
            deleted_folder_id = row.folder_id
            session.query(ProfessionalBatchPlanItemModel).filter(
                ProfessionalBatchPlanItemModel.plan_id.in_(
                    session.query(ProfessionalBatchPlanModel.plan_id).filter(ProfessionalBatchPlanModel.folder_id == row.folder_id)
                )
            ).delete(synchronize_session=False)
            session.query(ProfessionalBatchPlanModel).filter(ProfessionalBatchPlanModel.folder_id == row.folder_id).delete(synchronize_session=False)
            session.query(ProfessionalFolderItemModel).filter(ProfessionalFolderItemModel.folder_id == row.folder_id).delete(synchronize_session=False)
            row.status = "deleted"
            row.updated_at = datetime.now()
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

        for storage_rel in storage_paths:
            try:
                image_storage_service.delete(storage_rel)
            except Exception:
                pass
        if deleted_folder_id:
            try:
                professional_folder_index.delete_folder(owner_id=owner, folder_id=deleted_folder_id)
            except Exception:
                pass
        return True

    @staticmethod
    def _analysis_text(item: Mapping[str, Any]) -> str:
        analysis = item.get("analysis") if isinstance(item.get("analysis"), Mapping) else {}
        return "\n".join(
            value
            for value in (
                _clean(item.get("relativeName") or item.get("name"), limit=500),
                _clean(item.get("category"), limit=80),
                f"{int(item.get('width') or 0)}x{int(item.get('height') or 0)}",
                _clean(analysis.get("text"), limit=4000),
            )
            if value
        )

    @staticmethod
    def _preferred_categories(query: str) -> list[str]:
        text = query.lower()
        rules = (
            ("main", ("主图", "首图", "封面", "main", "cover")),
            ("detail", ("详情", "参数", "卖点", "detail")),
            ("scene", ("场景", "生活", "汽车", "车图", "scene", "lifestyle")),
            ("banner", ("横幅", "海报", "活动", "banner")),
        )
        return [category for category, markers in rules if any(marker in text for marker in markers)]

    def search_folder_items(
        self,
        folder_id: str,
        *,
        owner_id: str,
        query: str = "",
        limit: int = 8,
    ) -> dict[str, Any]:
        owner = _owner_id(owner_id)
        folder = self.get_folder(folder_id, owner_id=owner, include_items=True)
        if folder is None:
            raise KeyError("folder asset not found")
        items = [dict(item) for item in list(folder.get("items") or []) if isinstance(item, Mapping)]
        if not items:
            return {"items": [], "retrievalMode": "metadata"}

        query_text = _clean(query, limit=3000)
        query_terms = professional_retrieval_terms(query_text)
        preferred = self._preferred_categories(query_text)
        vector = professional_query_embedding(query_text) if query_text and professional_folder_index.enabled() else []
        vector_scores: dict[int, float] = {}
        if vector and professional_folder_index.enabled():
            try:
                hits = professional_folder_index.search(
                    vector=vector,
                    owner_id=owner,
                    folder_id=_clean(folder_id, limit=191),
                    limit=max(limit * 3, 12),
                )
                vector_scores = {
                    int((hit.get("payload") or {}).get("itemId")): float(hit.get("score") or 0.0)
                    for hit in hits
                    if isinstance(hit.get("payload"), Mapping) and (hit.get("payload") or {}).get("itemId")
                }
            except Exception:
                vector_scores = {}

        scored: list[tuple[float, int, dict[str, Any]]] = []
        for index, item in enumerate(items):
            item_terms = professional_retrieval_terms(self._analysis_text(item))
            keyword_score = len(query_terms & item_terms) / max(1, min(12, len(query_terms))) if query_terms else 0.0
            category = _clean(item.get("category"), "other", 40)
            category_score = 0.3 if category in preferred else 0.0
            vector_score = vector_scores.get(int(item.get("id") or 0), 0.0)
            score = 0.65 * vector_score + 0.35 * keyword_score + category_score
            scored.append((score, -index, item))
        scored.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)

        selected: list[dict[str, Any]] = []
        selected_ids: set[int] = set()
        seen_categories: set[str] = set()
        seen_shapes: set[str] = set()
        max_items = max(1, min(30, int(limit or 8)))
        for _score, _index, item in scored:
            category = _clean(item.get("category"), "other", 40)
            width = int(item.get("width") or 0)
            height = int(item.get("height") or 0)
            ratio = width / max(1, height)
            shape = "wide" if ratio >= 1.35 else ("tall" if ratio <= 0.8 else "square")
            if category in seen_categories and shape in seen_shapes and len(selected) < min(4, max_items):
                continue
            selected.append(item)
            selected_ids.add(int(item.get("id") or 0))
            seen_categories.add(category)
            seen_shapes.add(shape)
            if len(selected) >= max_items:
                break
        if len(selected) < max_items:
            for _score, _index, item in scored:
                item_id = int(item.get("id") or 0)
                if item_id in selected_ids:
                    continue
                selected.append(item)
                selected_ids.add(item_id)
                if len(selected) >= max_items:
                    break
        retrieval_mode = "hybrid" if vector_scores else ("keyword" if query_terms else "metadata")
        return {"items": selected, "retrievalMode": retrieval_mode}

    def save_item_analysis(
        self,
        folder_id: str,
        item_id: int,
        *,
        owner_id: str,
        analysis: str,
        question: str = "",
    ) -> dict[str, Any]:
        owner = _owner_id(owner_id)
        session = self._session()
        try:
            row = session.query(ProfessionalFolderItemModel).filter(
                ProfessionalFolderItemModel.id == int(item_id),
                ProfessionalFolderItemModel.folder_id == _clean(folder_id, limit=191),
                ProfessionalFolderItemModel.owner_id == owner,
            ).one_or_none()
            if row is None:
                raise KeyError("folder item not found")
            payload = {
                "text": _clean(analysis, limit=6000),
                "question": _clean(question, limit=1000),
                "updatedAt": datetime.now().isoformat(timespec="seconds"),
            }
            row.analysis_json = _dump(payload)
            session.commit()
            item = self._item_dict(row)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

        text = self._analysis_text(item)
        vectors = embed_professional_texts([text]) if text and professional_folder_index.enabled() else []
        if vectors:
            try:
                professional_folder_index.upsert(
                    folder_id=_clean(folder_id, limit=191),
                    item_id=int(item_id),
                    vector=vectors[0],
                    payload={
                        "ownerId": owner,
                        "name": item.get("relativeName") or item.get("name"),
                        "category": item.get("category"),
                        "analysis": payload["text"][:2000],
                    },
                )
            except Exception:
                pass
        return item

    def folder_context(
        self,
        folder_id: str,
        *,
        owner_id: str,
        sample_limit: int = 8,
        query: str = "",
    ) -> dict[str, Any]:
        folder = self.get_folder(folder_id, owner_id=owner_id, include_items=True)
        if folder is None:
            raise KeyError("folder asset not found")
        selected = self.search_folder_items(
            folder_id,
            owner_id=owner_id,
            query=query,
            limit=max(1, min(12, int(sample_limit or 8))),
        )
        return {
            "folderId": folder["folderId"],
            "name": folder["name"],
            "fileCount": folder["itemCount"],
            "totalBytes": folder["totalBytes"],
            "summary": folder.get("summary") or {},
            "sampleItems": selected["items"],
            "retrievalMode": selected["retrievalMode"],
        }

    def create_batch_plan(
        self,
        *,
        owner_id: str,
        conversation_id: str,
        run_id: str,
        folder_id: str,
        request: Mapping[str, Any],
        pages: list[Mapping[str, Any]],
        item_limit: int | None = None,
    ) -> dict[str, Any]:
        owner = _owner_id(owner_id)
        folder = self.get_folder(folder_id, owner_id=owner, include_items=True)
        if folder is None:
            raise KeyError("folder asset not found")
        all_items = list(folder.get("items") or [])
        items = all_items
        if not items:
            raise ValueError("folder has no image items")
        raw_limit = item_limit if item_limit is not None else request.get("count")
        if raw_limit is not None:
            try:
                limit = int(raw_limit)
            except (TypeError, ValueError):
                limit = len(items)
            limit = max(1, min(FOLDER_MAX_ITEMS, len(items), limit))
            items = items[:limit]
        folder_summary = dict(folder.get("summary") or {})
        folder_summary["folderFileCount"] = len(all_items)
        folder_summary["selectedImageCount"] = len(items)
        request_payload = dict(request)
        request_payload["count"] = len(items)
        request_payload["folderFileCount"] = len(all_items)
        request_payload["selectedImageCount"] = len(items)
        page_rows = [item for item in pages if isinstance(item, Mapping)]
        fallback_prompt = _clean(request.get("prompt"), "基于这张参考图生成一张有商业价值、主体完整、背景有设计感的图片。", 12000)
        session = self._session()
        try:
            plan_id = f"batch-{uuid4().hex}"
            plan = ProfessionalBatchPlanModel(
                plan_id=plan_id,
                owner_id=owner,
                conversation_id=_clean(conversation_id)[:191],
                run_id=_clean(run_id)[:191],
                folder_id=_clean(folder_id)[:191],
                request_json=_dump(request_payload),
                summary_json=_dump(folder_summary),
                total_items=len(items),
            )
            session.add(plan)
            for index, item in enumerate(items):
                category = _clean(item.get("category"), "other")
                selected = next(
                    (page for page in page_rows if _clean(page.get("category"), "other", 40) == category),
                    None,
                )
                selected = selected or next(
                    (page for page in page_rows if _clean(page.get("category"), "all", 40) in {"", "all"}),
                    None,
                )
                selected = selected or (page_rows[index % len(page_rows)] if page_rows else {})
                prompt = _clean(selected.get("prompt"), fallback_prompt, 12000)
                session.add(ProfessionalBatchPlanItemModel(
                    plan_id=plan_id,
                    folder_item_id=int(item["id"]),
                    item_index=index,
                    title=_clean(selected.get("title"), f"文件夹图片 {index + 1}", 191),
                    purpose=_clean(selected.get("purpose"), "文件夹批量生成", 500),
                    prompt=prompt,
                ))
            session.commit()
            return self.get_batch_plan(plan_id, owner_id=owner) or {}
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_batch_plan(self, plan_id: str, *, owner_id: str, sync_tasks: bool = True) -> dict[str, Any] | None:
        owner = _owner_id(owner_id)
        session = self._session()
        try:
            plan = session.query(ProfessionalBatchPlanModel).filter(
                ProfessionalBatchPlanModel.plan_id == _clean(plan_id, limit=191),
                ProfessionalBatchPlanModel.owner_id == owner,
            ).one_or_none()
            if plan is None:
                return None
            rows = session.query(ProfessionalBatchPlanItemModel).filter(ProfessionalBatchPlanItemModel.plan_id == plan.plan_id).order_by(ProfessionalBatchPlanItemModel.item_index.asc()).all()
            folder_items = {int(item.id): item for item in session.query(ProfessionalFolderItemModel).filter(ProfessionalFolderItemModel.folder_id == plan.folder_id).all()}
            if sync_tasks:
                self._sync_plan_task_rows(session, plan, rows)
            payload = {
                "planId": plan.plan_id,
                "folderId": plan.folder_id,
                "status": plan.status,
                "totalItems": int(plan.total_items or 0),
                "completedItems": int(plan.completed_items or 0),
                "failedItems": int(plan.failed_items or 0),
                "summary": _load(plan.summary_json, {}),
                "createdAt": plan.created_at.isoformat() if plan.created_at else "",
                "updatedAt": plan.updated_at.isoformat() if plan.updated_at else "",
                "items": [
                    {
                        "id": int(row.id),
                        "folderItemId": int(row.folder_item_id),
                        "index": int(row.item_index or 0),
                        "name": folder_items.get(int(row.folder_item_id)).filename if folder_items.get(int(row.folder_item_id)) else "",
                        "category": folder_items.get(int(row.folder_item_id)).category if folder_items.get(int(row.folder_item_id)) else "other",
                        "title": row.title,
                        "purpose": row.purpose,
                        "taskId": row.task_id,
                        "status": row.status,
                        "attempts": int(row.attempts or 0),
                        "error": row.error,
                    }
                    for row in rows
                ],
            }
            session.commit()
            return payload
        finally:
            session.close()

    def _sync_plan_task_rows(self, session, plan, rows) -> None:
        task_ids = [row.task_id for row in rows if _clean(row.task_id)]
        if not task_ids:
            return
        try:
            from services.image.image_task_service import image_task_service

            tasks = image_task_service.list_tasks({"id": plan.owner_id}, task_ids).get("items") or []
        except Exception:
            return
        by_id = {str(task.get("id")): task for task in tasks if isinstance(task, Mapping)}
        for row in rows:
            task = by_id.get(row.task_id)
            if not task:
                continue
            status = _clean(task.get("status"), row.status)
            if status == "success":
                row.status = "success"
            elif status == "error":
                row.status = "error"
                row.error = _clean(task.get("error"), row.error, 2000)
            elif status == "canceled":
                row.status = "canceled"
            else:
                row.status = "queued" if status == "queued" else "running"
        completed = sum(row.status == "success" for row in rows)
        failed = sum(row.status in {"error", "canceled"} for row in rows)
        plan.completed_items = completed
        plan.failed_items = failed
        if completed + failed >= len(rows) and rows:
            plan.status = "completed" if failed == 0 else "completed_with_errors"
        elif any(row.task_id for row in rows):
            plan.status = "executing"
        plan.updated_at = datetime.now()

    def execute_batch_plan(
        self,
        plan_id: str,
        *,
        owner_id: str,
        identity: Mapping[str, object],
        base_url: str,
        model: str,
        size: str,
        quality: str,
        prompt_engine_mode: str = "professional",
        item_ids: set[int] | None = None,
        agent_run_id: str = "",
    ) -> dict[str, Any]:
        owner = _owner_id(owner_id)
        current = self.get_batch_plan(plan_id, owner_id=owner)
        if current is None:
            raise KeyError("batch plan not found")
        session = self._session()
        try:
            plan = session.query(ProfessionalBatchPlanModel).filter(ProfessionalBatchPlanModel.plan_id == _clean(plan_id, limit=191), ProfessionalBatchPlanModel.owner_id == owner).one_or_none()
            if plan is None:
                raise KeyError("batch plan not found")
            rows = session.query(ProfessionalBatchPlanItemModel).filter(ProfessionalBatchPlanItemModel.plan_id == plan.plan_id).order_by(ProfessionalBatchPlanItemModel.item_index.asc()).all()
            folder_items = {int(item.id): item for item in session.query(ProfessionalFolderItemModel).filter(ProfessionalFolderItemModel.folder_id == plan.folder_id, ProfessionalFolderItemModel.owner_id == owner).all()}
            plan.status = "executing"
            session.commit()
            from services.image.image_task_service import image_task_service
            for row in rows:
                if item_ids is not None and int(row.id) not in item_ids:
                    continue
                if _clean(row.task_id) and row.status not in {"error", "canceled"}:
                    continue
                item = folder_items.get(int(row.folder_item_id))
                if item is None:
                    row.status = "error"
                    row.error = "folder item not found"
                    continue
                try:
                    payload = image_storage_service.get_bytes(item.storage_rel)
                    task = image_task_service.submit_edit(
                        dict(identity),
                        client_task_id=f"{plan.plan_id}-{row.item_index}-{row.attempts + 1}",
                        prompt=row.prompt,
                        model=_clean(model, "gpt-image-2"),
                        size=_clean(size),
                        quality=_clean(quality, "auto"),
                        prompt_engine_mode=prompt_engine_mode,
                        base_url=_clean(base_url),
                        images=[(payload, item.filename, item.mime_type)],
                        image_urls=[],
                        preserve_subject=True,
                        conversation_id=plan.conversation_id,
                        turn_id=plan.plan_id,
                        batch_id=plan.plan_id,
                        batch_index=int(row.item_index or 0),
                        batch_total=len(rows),
                        queue_priority="batch",
                        agent_run_id=_clean(agent_run_id),
                    )
                    row.task_id = _clean(task.get("id"))
                    row.status = _clean(task.get("status"), "queued")
                    row.attempts = int(row.attempts or 0) + 1
                    row.error = ""
                except Exception as exc:
                    row.status = "error"
                    row.attempts = int(row.attempts or 0) + 1
                    row.error = _clean(exc, "batch item submission failed", 2000)
                session.commit()
            self._sync_plan_task_rows(session, plan, rows)
            session.commit()
            return self.get_batch_plan(plan.plan_id, owner_id=owner, sync_tasks=False) or {}
        finally:
            session.close()

    def retry_batch_item(
        self,
        plan_id: str,
        item_id: int,
        *,
        owner_id: str,
        identity: Mapping[str, object],
        base_url: str,
        model: str,
        size: str,
        quality: str,
        agent_run_id: str = "",
    ) -> dict[str, Any]:
        owner = _owner_id(owner_id)
        session = self._session()
        try:
            row = session.query(ProfessionalBatchPlanItemModel).join(ProfessionalBatchPlanModel, ProfessionalBatchPlanModel.plan_id == ProfessionalBatchPlanItemModel.plan_id).filter(
                ProfessionalBatchPlanItemModel.id == int(item_id),
                ProfessionalBatchPlanItemModel.plan_id == _clean(plan_id, limit=191),
                ProfessionalBatchPlanModel.owner_id == owner,
            ).one_or_none()
            if row is None:
                raise KeyError("batch plan item not found")
            row.task_id = ""
            row.status = "pending"
            session.commit()
        finally:
            session.close()
        return self.execute_batch_plan(
            plan_id,
            owner_id=owner,
            identity=identity,
            base_url=base_url,
            model=model,
            size=size,
            quality=quality,
            item_ids={int(item_id)},
            agent_run_id=agent_run_id,
        )


professional_folder_asset_service = ProfessionalFolderAssetService()
