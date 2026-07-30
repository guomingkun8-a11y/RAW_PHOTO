from __future__ import annotations

import io
import os
from datetime import datetime
from typing import Any

from PIL import Image
from sqlalchemy import BigInteger, Column, DateTime, Integer, String, Text, and_, create_engine, desc, or_, text
from sqlalchemy.orm import declarative_base, sessionmaker

from services.cache_utils import TTLCache
from services.config import config
from services.image_service import thumbnail_url
from services.image_storage_service import image_storage_service

Base = declarative_base()

DEFAULT_DATABASE_URL = "mysql+pymysql://root:root@127.0.0.1:3306/raw_photo?charset=utf8mb4"


def _clean(value: object, default: str = "") -> str:
    text = str(value if value is not None else default).strip()
    return text or default


def _database_url() -> str:
    return (
        os.getenv("IMAGE_LIBRARY_DATABASE_URL")
        or os.getenv("MYSQL_DATABASE_URL")
        or DEFAULT_DATABASE_URL
    )


def _now() -> datetime:
    return datetime.now()


def _image_dimensions(payload: bytes) -> tuple[int | None, int | None]:
    try:
        with Image.open(io.BytesIO(payload)) as image:
            return image.size
    except Exception:
        return None, None


class ProductModel(Base):
    __tablename__ = "business_products"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    owner_id = Column(String(191), nullable=False, default="local-admin")
    created_by = Column(String(191), nullable=False, default="local-admin")
    name = Column(String(191), nullable=False)
    sku = Column(String(191), nullable=True)
    brand = Column(String(191), nullable=True)
    category = Column(String(191), nullable=True)
    selling_points = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
    status = Column(String(32), nullable=False, default="active")
    created_at = Column(DateTime, nullable=False, default=_now)
    updated_at = Column(DateTime, nullable=False, default=_now, onupdate=_now)


class ProductReferenceModel(Base):
    __tablename__ = "business_product_references"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    product_id = Column(BigInteger, nullable=False)
    owner_id = Column(String(191), nullable=False, default="local-admin")
    file_name = Column(String(255), nullable=True)
    mime_type = Column(String(128), nullable=True)
    image_rel = Column(String(512), nullable=False)
    image_url = Column(String(1024), nullable=False)
    thumbnail_url = Column(String(1024), nullable=True)
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    file_size = Column(BigInteger, nullable=True)
    storage = Column(String(64), nullable=True)
    created_at = Column(DateTime, nullable=False, default=_now)


class PromptTemplateModel(Base):
    __tablename__ = "business_prompt_templates"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    owner_id = Column(String(191), nullable=False, default="local-admin")
    created_by = Column(String(191), nullable=False, default="local-admin")
    name = Column(String(191), nullable=False)
    category = Column(String(64), nullable=False, default="main")
    content = Column(Text, nullable=False)
    model = Column(String(191), nullable=True)
    size = Column(String(64), nullable=True)
    quality = Column(String(64), nullable=True)
    preserve_subject = Column(Integer, nullable=False, default=0)
    enabled = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, nullable=False, default=_now)
    updated_at = Column(DateTime, nullable=False, default=_now, onupdate=_now)


class AuditLogModel(Base):
    __tablename__ = "business_audit_logs"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    owner_id = Column(String(191), nullable=False, default="local-admin")
    actor_id = Column(String(191), nullable=False, default="local-admin")
    action = Column(String(64), nullable=False)
    target_type = Column(String(64), nullable=False)
    target_id = Column(String(191), nullable=True)
    detail = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_now)


DEFAULT_TEMPLATES = [
    {
        "name": "电商主图",
        "category": "main",
        "content": "生成一张电商商品主图，突出商品主体，画面干净高级，保留商品 Logo、文字、结构和材质细节，背景适合线上平台展示。",
        "model": "gpt-image-2",
        "size": "1024x1024",
        "quality": "high",
        "preserve_subject": 1,
    },
    {
        "name": "白底图",
        "category": "white",
        "content": "生成标准电商白底图，商品居中完整展示，边缘自然清晰，保留原商品造型、Logo、文字和颜色，不添加多余装饰。",
        "model": "gpt-image-2",
        "size": "1024x1024",
        "quality": "high",
        "preserve_subject": 1,
    },
    {
        "name": "场景图",
        "category": "scene",
        "content": "生成真实商业场景图，让商品自然融入使用环境，光影真实，质感高级，主体保持与参考商品一致。",
        "model": "gpt-image-2",
        "size": "1024x1024",
        "quality": "high",
        "preserve_subject": 1,
    },
    {
        "name": "详情页卖点图",
        "category": "detail",
        "content": "生成商品详情页配图，突出核心卖点和材质细节，构图清晰，适合电商详情页使用，商品主体保持一致。",
        "model": "gpt-image-2",
        "size": "1024x1024",
        "quality": "high",
        "preserve_subject": 1,
    },
]


class BusinessService:
    def __init__(self, database_url: str | None = None):
        self.database_url = database_url or _database_url()
        self.engine = None
        self.Session = None
        self._init_error = ""
        self._list_cache = TTLCache[tuple[Any, ...], dict[str, Any]](ttl_seconds=3.0, max_items=128)
        self._init_engine()

    def _init_engine(self) -> None:
        try:
            engine = create_engine(self.database_url, pool_pre_ping=True, pool_recycle=3600)
            Base.metadata.create_all(engine)
            self._ensure_indexes(engine)
            self.engine = engine
            self.Session = sessionmaker(bind=engine)
            self._init_error = ""
            self._ensure_default_templates()
        except Exception as exc:
            self.engine = None
            self.Session = None
            self._init_error = str(exc)

    def _session(self):
        if self.Session is None:
            self._init_engine()
        if self.Session is None:
            raise RuntimeError(f"business database unavailable: {self._init_error}")
        return self.Session()

    def _invalidate_list_cache(self) -> None:
        self._list_cache.clear()

    def _ensure_indexes(self, engine) -> None:
        if engine.dialect.name != "mysql":
            return
        with engine.begin() as connection:
            statements = [
                "CREATE INDEX idx_products_owner_updated ON business_products (owner_id, updated_at)",
                "CREATE INDEX idx_products_owner_sku ON business_products (owner_id, sku)",
                "CREATE INDEX idx_products_owner_status_updated ON business_products (owner_id, status, updated_at)",
                "CREATE INDEX idx_references_product ON business_product_references (product_id, created_at)",
                "CREATE INDEX idx_references_product_created_id ON business_product_references (product_id, created_at, id)",
                "CREATE INDEX idx_templates_owner_category ON business_prompt_templates (owner_id, category, enabled)",
                "CREATE INDEX idx_templates_owner_enabled_updated ON business_prompt_templates (owner_id, enabled, updated_at)",
                "CREATE INDEX idx_audit_owner_created ON business_audit_logs (owner_id, created_at)",
                "CREATE INDEX idx_audit_owner_created_id ON business_audit_logs (owner_id, created_at, id)",
            ]
            for statement in statements:
                try:
                    connection.execute(text(statement))
                except Exception:
                    pass

    def _ensure_default_templates(self) -> None:
        session = self._session()
        try:
            existing = session.query(PromptTemplateModel).filter(PromptTemplateModel.owner_id == "local-admin").count()
            if existing > 0:
                return
            for item in DEFAULT_TEMPLATES:
                session.add(PromptTemplateModel(owner_id="local-admin", created_by="system", **item))
            session.commit()
        except Exception:
            session.rollback()
        finally:
            session.close()

    def _log(self, session, identity: dict[str, object], action: str, target_type: str, target_id: object, detail: str = "") -> None:
        owner_id = _clean(identity.get("id")) or "local-admin"
        session.add(
            AuditLogModel(
                owner_id=owner_id,
                actor_id=owner_id,
                action=action,
                target_type=target_type,
                target_id=_clean(target_id),
                detail=detail,
            )
        )

    def record_audit_log(self, *, identity: dict[str, object], action: str, target_type: str, target_id: object, detail: str = "") -> None:
        session = self._session()
        try:
            self._log(session, identity, action, target_type, target_id, detail)
            session.commit()
            self._invalidate_list_cache()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def list_products(self, *, identity: dict[str, object], q: str = "", status: str = "active") -> dict[str, Any]:
        owner_id = _clean(identity.get("id")) or "local-admin"
        is_admin = _clean(identity.get("role")) == "admin"
        keyword = _clean(q)
        cache_key = ("products", owner_id, is_admin, keyword, status)

        def _build() -> dict[str, Any]:
            session = self._session()
            try:
                query = session.query(ProductModel)
                if not is_admin:
                    query = query.filter(ProductModel.owner_id == owner_id)
                if status:
                    query = query.filter(ProductModel.status == status)
                if keyword:
                    like = f"%{keyword}%"
                    query = query.filter(
                        or_(
                            ProductModel.name.like(like),
                            ProductModel.sku.like(like),
                            ProductModel.brand.like(like),
                            ProductModel.category.like(like),
                        )
                    )
                rows = query.order_by(desc(ProductModel.updated_at), desc(ProductModel.id)).limit(500).all()
                product_ids = [row.id for row in rows]
                references = {}
                if product_ids:
                    ref_rows = (
                        session.query(ProductReferenceModel)
                        .filter(ProductReferenceModel.product_id.in_(product_ids))
                        .order_by(desc(ProductReferenceModel.created_at), desc(ProductReferenceModel.id))
                        .all()
                    )
                    for ref in ref_rows:
                        references.setdefault(ref.product_id, []).append(self._public_reference(ref))
                return {
                    "items": [self._public_product(row, references.get(row.id, [])) for row in rows],
                    "total": len(rows),
                }
            finally:
                session.close()

        return self._list_cache.get_or_set(cache_key, _build)

    def get_product(self, *, identity: dict[str, object], product_id: int) -> dict[str, Any] | None:
        owner_id = _clean(identity.get("id")) or "local-admin"
        is_admin = _clean(identity.get("role")) == "admin"
        session = self._session()
        try:
            query = session.query(ProductModel).filter(ProductModel.id == product_id)
            if not is_admin:
                query = query.filter(ProductModel.owner_id == owner_id)
            row = query.one_or_none()
            if row is None:
                return None
            refs = (
                session.query(ProductReferenceModel)
                .filter(ProductReferenceModel.product_id == product_id)
                .order_by(desc(ProductReferenceModel.created_at), desc(ProductReferenceModel.id))
                .all()
            )
            return self._public_product(row, [self._public_reference(ref) for ref in refs])
        finally:
            session.close()

    def create_product(self, *, identity: dict[str, object], data: dict[str, object]) -> dict[str, Any]:
        owner_id = _clean(identity.get("id")) or "local-admin"
        name = _clean(data.get("name"))
        if not name:
            raise ValueError("商品名称不能为空")
        session = self._session()
        try:
            row = ProductModel(
                owner_id=owner_id,
                created_by=owner_id,
                name=name,
                sku=_clean(data.get("sku")) or None,
                brand=_clean(data.get("brand")) or None,
                category=_clean(data.get("category")) or None,
                selling_points=_clean(data.get("selling_points")) or None,
                notes=_clean(data.get("notes")) or None,
                status=_clean(data.get("status"), "active"),
            )
            session.add(row)
            session.flush()
            self._log(session, identity, "create", "product", row.id, row.name)
            session.commit()
            self._invalidate_list_cache()
            return self._public_product(row, [])
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def update_product(self, *, identity: dict[str, object], product_id: int, data: dict[str, object]) -> dict[str, Any] | None:
        owner_id = _clean(identity.get("id")) or "local-admin"
        is_admin = _clean(identity.get("role")) == "admin"
        session = self._session()
        try:
            query = session.query(ProductModel).filter(ProductModel.id == product_id)
            if not is_admin:
                query = query.filter(ProductModel.owner_id == owner_id)
            row = query.one_or_none()
            if row is None:
                return None
            for field in ("name", "sku", "brand", "category", "selling_points", "notes", "status"):
                if field in data:
                    value = _clean(data.get(field))
                    if field == "name" and not value:
                        raise ValueError("商品名称不能为空")
                    if field == "status":
                        setattr(row, field, value or "active")
                    else:
                        setattr(row, field, value or None)
            row.updated_at = _now()
            self._log(session, identity, "update", "product", row.id, row.name)
            session.commit()
            self._invalidate_list_cache()
            return self.get_product(identity=identity, product_id=product_id)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def upload_product_reference(
        self,
        *,
        identity: dict[str, object],
        product_id: int,
        payload: bytes,
        file_name: str,
        mime_type: str,
        base_url: str,
    ) -> dict[str, Any] | None:
        owner_id = _clean(identity.get("id")) or "local-admin"
        is_admin = _clean(identity.get("role")) == "admin"
        session = self._session()
        try:
            query = session.query(ProductModel).filter(ProductModel.id == product_id)
            if not is_admin:
                query = query.filter(ProductModel.owner_id == owner_id)
            product = query.one_or_none()
            if product is None:
                return None
            stored = image_storage_service.save(payload, base_url)
            width, height = _image_dimensions(payload)
            row = ProductReferenceModel(
                product_id=product_id,
                owner_id=owner_id,
                file_name=_clean(file_name) or "reference.png",
                mime_type=_clean(mime_type) or "image/png",
                image_rel=stored.rel,
                image_url=stored.url,
                thumbnail_url=thumbnail_url(base_url, stored.rel),
                width=width,
                height=height,
                file_size=stored.size,
                storage=stored.storage,
            )
            product.updated_at = _now()
            session.add(row)
            session.flush()
            self._log(session, identity, "upload_reference", "product", product_id, row.image_rel)
            session.commit()
            self._invalidate_list_cache()
            return self._public_reference(row)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def list_templates(self, *, identity: dict[str, object], q: str = "", category: str = "", enabled_only: bool = True) -> dict[str, Any]:
        owner_id = _clean(identity.get("id")) or "local-admin"
        is_admin = _clean(identity.get("role")) == "admin"
        keyword = _clean(q)
        cache_key = ("templates", owner_id, is_admin, keyword, category, enabled_only)

        def _build() -> dict[str, Any]:
            session = self._session()
            try:
                query = session.query(PromptTemplateModel)
                if not is_admin:
                    query = query.filter(or_(PromptTemplateModel.owner_id == owner_id, PromptTemplateModel.owner_id == "local-admin"))
                if enabled_only:
                    query = query.filter(PromptTemplateModel.enabled == 1)
                if category:
                    query = query.filter(PromptTemplateModel.category == category)
                if keyword:
                    like = f"%{keyword}%"
                    query = query.filter(or_(PromptTemplateModel.name.like(like), PromptTemplateModel.content.like(like)))
                rows = query.order_by(desc(PromptTemplateModel.updated_at), desc(PromptTemplateModel.id)).limit(500).all()
                return {"items": [self._public_template(row) for row in rows], "total": len(rows)}
            finally:
                session.close()

        return self._list_cache.get_or_set(cache_key, _build)

    def create_template(self, *, identity: dict[str, object], data: dict[str, object]) -> dict[str, Any]:
        owner_id = _clean(identity.get("id")) or "local-admin"
        name = _clean(data.get("name"))
        content = _clean(data.get("content"))
        if not name:
            raise ValueError("模板名称不能为空")
        if not content:
            raise ValueError("模板内容不能为空")
        session = self._session()
        try:
            row = PromptTemplateModel(
                owner_id=owner_id,
                created_by=owner_id,
                name=name,
                category=_clean(data.get("category"), "main"),
                content=content,
                model=_clean(data.get("model")) or None,
                size=_clean(data.get("size")) or None,
                quality=_clean(data.get("quality")) or None,
                preserve_subject=1 if data.get("preserve_subject") else 0,
                enabled=1 if data.get("enabled", True) else 0,
            )
            session.add(row)
            session.flush()
            self._log(session, identity, "create", "template", row.id, row.name)
            session.commit()
            self._invalidate_list_cache()
            return self._public_template(row)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def update_template(self, *, identity: dict[str, object], template_id: int, data: dict[str, object]) -> dict[str, Any] | None:
        owner_id = _clean(identity.get("id")) or "local-admin"
        is_admin = _clean(identity.get("role")) == "admin"
        session = self._session()
        try:
            query = session.query(PromptTemplateModel).filter(PromptTemplateModel.id == template_id)
            if not is_admin:
                query = query.filter(PromptTemplateModel.owner_id == owner_id)
            row = query.one_or_none()
            if row is None:
                return None
            for field in ("name", "category", "content", "model", "size", "quality"):
                if field in data:
                    value = _clean(data.get(field))
                    if field in {"name", "content"} and not value:
                        raise ValueError("模板名称和内容不能为空")
                    if field == "category":
                        setattr(row, field, value or "main")
                    else:
                        setattr(row, field, value or None)
            if "preserve_subject" in data:
                row.preserve_subject = 1 if data.get("preserve_subject") else 0
            if "enabled" in data:
                row.enabled = 1 if data.get("enabled") else 0
            row.updated_at = _now()
            self._log(session, identity, "update", "template", row.id, row.name)
            session.commit()
            self._invalidate_list_cache()
            return self._public_template(row)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def list_audit_logs(self, *, identity: dict[str, object], limit: int = 100) -> dict[str, Any]:
        owner_id = _clean(identity.get("id")) or "local-admin"
        is_admin = _clean(identity.get("role")) == "admin"
        page_limit = max(1, min(500, limit))
        cache_key = ("audit_logs", owner_id, is_admin, page_limit)

        def _build() -> dict[str, Any]:
            session = self._session()
            try:
                query = session.query(AuditLogModel)
                if not is_admin:
                    query = query.filter(AuditLogModel.owner_id == owner_id)
                rows = query.order_by(desc(AuditLogModel.created_at), desc(AuditLogModel.id)).limit(page_limit).all()
                return {"items": [self._public_audit_log(row) for row in rows], "total": len(rows)}
            finally:
                session.close()

        return self._list_cache.get_or_set(cache_key, _build)

    @staticmethod
    def _public_product(row: ProductModel, references: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "id": row.id,
            "name": row.name,
            "sku": row.sku,
            "brand": row.brand,
            "category": row.category,
            "selling_points": row.selling_points,
            "notes": row.notes,
            "status": row.status,
            "references": references,
            "cover_image_url": references[0]["thumbnail_url"] if references else "",
            "created_at": row.created_at.strftime("%Y-%m-%d %H:%M:%S") if row.created_at else "",
            "updated_at": row.updated_at.strftime("%Y-%m-%d %H:%M:%S") if row.updated_at else "",
        }

    @staticmethod
    def _public_reference(row: ProductReferenceModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "product_id": row.product_id,
            "file_name": row.file_name,
            "mime_type": row.mime_type,
            "image_rel": row.image_rel,
            "image_url": row.image_url,
            "thumbnail_url": row.thumbnail_url,
            "width": row.width,
            "height": row.height,
            "file_size": row.file_size,
            "storage": row.storage,
            "created_at": row.created_at.strftime("%Y-%m-%d %H:%M:%S") if row.created_at else "",
        }

    @staticmethod
    def _public_template(row: PromptTemplateModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "name": row.name,
            "category": row.category,
            "content": row.content,
            "model": row.model,
            "size": row.size,
            "quality": row.quality,
            "preserve_subject": bool(row.preserve_subject),
            "enabled": bool(row.enabled),
            "created_at": row.created_at.strftime("%Y-%m-%d %H:%M:%S") if row.created_at else "",
            "updated_at": row.updated_at.strftime("%Y-%m-%d %H:%M:%S") if row.updated_at else "",
        }

    @staticmethod
    def _public_audit_log(row: AuditLogModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "actor_id": row.actor_id,
            "action": row.action,
            "target_type": row.target_type,
            "target_id": row.target_id,
            "detail": row.detail,
            "created_at": row.created_at.strftime("%Y-%m-%d %H:%M:%S") if row.created_at else "",
        }


business_service = BusinessService()
