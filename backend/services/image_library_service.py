from __future__ import annotations

import io
import base64
from datetime import datetime
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from curl_cffi import requests
from PIL import Image
from sqlalchemy import BigInteger, Column, DateTime, Integer, String, Text, UniqueConstraint, and_, create_engine, desc, or_, text
from sqlalchemy.orm import declarative_base, sessionmaker

from services.cache_utils import TTLCache
from services.config import config
from services.image_service import thumbnail_url
from services.image_storage_service import image_storage_service
from services.proxy_service import proxy_settings

Base = declarative_base()

DEFAULT_DATABASE_URL = "mysql+pymysql://root:root@127.0.0.1:3306/raw_photo?charset=utf8mb4"


class GeneratedImageModel(Base):
    __tablename__ = "generated_images"
    __table_args__ = (
        UniqueConstraint("task_id", "image_index", name="uniq_task_image"),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    task_id = Column(String(191), nullable=False)
    owner_id = Column(String(191), nullable=False, default="local-admin")
    conversation_id = Column(String(191), nullable=True)
    turn_id = Column(String(191), nullable=True)
    image_index = Column(Integer, nullable=False, default=0)
    mode = Column(String(32), nullable=False, default="generate")
    model = Column(String(191), nullable=True)
    prompt = Column(Text, nullable=True)
    revised_prompt = Column(Text, nullable=True)
    size = Column(String(64), nullable=True)
    quality = Column(String(64), nullable=True)
    product_id = Column(BigInteger, nullable=True)
    template_id = Column(BigInteger, nullable=True)
    created_by = Column(String(191), nullable=True)
    image_rel = Column(String(512), nullable=False)
    image_url = Column(String(1024), nullable=False)
    thumbnail_url = Column(String(1024), nullable=True)
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    file_size = Column(BigInteger, nullable=True)
    storage = Column(String(64), nullable=True)
    duration_ms = Column(Integer, nullable=True)
    favorite = Column(Integer, nullable=False, default=0)
    deleted_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)


def _clean(value: object, default: str = "") -> str:
    text = str(value if value is not None else default).strip()
    return text or default


def _int_or_none(value: object) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _database_url() -> str:
    return (
        os.getenv("IMAGE_LIBRARY_DATABASE_URL")
        or os.getenv("MYSQL_DATABASE_URL")
        or DEFAULT_DATABASE_URL
    )


def _parse_datetime(value: object) -> datetime | None:
    text_value = _clean(value)
    if not text_value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text_value[:19], fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text_value.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def _parse_image_rel(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path if parsed.scheme else url
    marker = "/images/"
    if marker in path:
        return path.split(marker, 1)[1].lstrip("/")
    return ""


def _image_url(base_url: str, rel: str, existing_url: str = "") -> str:
    safe_rel = _clean(rel)
    if not safe_rel:
        return _clean(existing_url)
    settings = image_storage_service.settings()
    public_base_url = _clean(settings.get("public_base_url")).rstrip("/")
    if public_base_url:
        return _clean(existing_url) or f"{public_base_url}/{safe_rel}"
    return f"{base_url.rstrip('/')}/images/{safe_rel}"


def _thumbnail_url(base_url: str, rel: str, existing_url: str = "") -> str:
    safe_rel = _clean(rel)
    if safe_rel:
        return thumbnail_url(base_url, safe_rel)
    return _clean(existing_url)


def _dimensions_from_path(rel: str) -> tuple[int | None, int | None]:
    if not rel:
        return None, None
    try:
        path = (config.images_dir / rel).resolve()
        path.relative_to(config.images_dir.resolve())
        if path.is_file():
            with Image.open(path) as image:
                return image.size
        if image_storage_service.exists(rel):
            payload = image_storage_service.get_bytes(rel)
            with Image.open(io.BytesIO(payload)) as image:
                return image.size
    except Exception:
        return None, None
    return None, None


def _download_image(url: str) -> bytes:
    response = requests.get(
        url,
        headers={"Accept": "image/*,*/*;q=0.8", "User-Agent": "gmkraw image library"},
        timeout=60,
        allow_redirects=True,
        **proxy_settings.build_session_kwargs(),
    )
    if not 200 <= response.status_code < 300:
        raise RuntimeError(f"image download failed: HTTP {response.status_code}")
    data = bytes(response.content)
    if not data:
        raise RuntimeError("image download returned empty content")
    return data


def _store_result_image(item: dict[str, Any], base_url: str) -> tuple[str, str, str, int | None, int | None, int | None]:
    b64_json = _clean(item.get("b64_json"))
    if b64_json:
        payload = base64.b64decode(b64_json)
        stored = image_storage_service.save(payload, base_url)
        width, height = _dimensions_from_path(stored.rel)
        return stored.rel, stored.url, stored.storage, width, height, stored.size

    url = _clean(item.get("url"))
    if not url:
        raise RuntimeError("image item has no url or b64_json")

    rel = _parse_image_rel(url)
    if rel and image_storage_service.exists(rel):
        width, height = _dimensions_from_path(rel)
        item = image_storage_service.get_item(rel)
        storage = _clean(item.get("storage")) or ("minio" if item.get("minio") else "webdav" if item.get("webdav") else "local")
        file_size = item.get("size")
        try:
            file_size = int(file_size) if file_size is not None else None
        except (TypeError, ValueError):
            file_size = None
        if file_size is None:
            try:
                file_size = (config.images_dir / rel).stat().st_size
            except Exception:
                file_size = None
        return rel, url, storage, width, height, file_size

    try:
        payload = _download_image(url)
        stored = image_storage_service.save(payload, base_url)
        width, height = _dimensions_from_path(stored.rel)
        return stored.rel, stored.url, stored.storage, width, height, stored.size
    except Exception:
        return "", url, "remote", None, None, None


class ImageLibraryService:
    def __init__(
        self,
        database_url: str | None = None,
        *,
        model=GeneratedImageModel,
        table_name: str | None = None,
    ):
        self.database_url = database_url or _database_url()
        self.Model = model
        self.table_name = table_name or str(model.__tablename__)
        if self.table_name != "generated_images":
            raise ValueError("unsupported image library table")
        self.engine = None
        self.Session = None
        self._init_error = ""
        self._count_cache = TTLCache[tuple[Any, ...], int](ttl_seconds=3.0, max_items=256)
        self._init_engine()

    def _init_engine(self) -> None:
        try:
            engine = create_engine(
                self.database_url,
                pool_pre_ping=True,
                pool_recycle=3600,
            )
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
        database = engine.url.database
        if not database:
            return
        table_name = self.table_name
        with engine.begin() as connection:
            self._ensure_business_columns(connection, database)
            existing = {
                str(row[0])
                for row in connection.execute(
                    text(
                        "SELECT INDEX_NAME FROM information_schema.STATISTICS "
                        f"WHERE TABLE_SCHEMA = :schema AND TABLE_NAME = '{table_name}'"
                    ),
                    {"schema": database},
                )
            }
            if "idx_owner_created_id" not in existing:
                connection.execute(text(f"CREATE INDEX idx_owner_created_id ON {table_name} (owner_id, created_at, id)"))
            if "idx_owner_deleted_created_id" not in existing:
                connection.execute(
                    text(f"CREATE INDEX idx_owner_deleted_created_id ON {table_name} (owner_id, deleted_at, created_at, id)")
                )
            if "idx_deleted_created_id" not in existing:
                connection.execute(text(f"CREATE INDEX idx_deleted_created_id ON {table_name} (deleted_at, created_at, id)"))
            if "idx_owner_mode_created" not in existing:
                connection.execute(text(f"CREATE INDEX idx_owner_mode_created ON {table_name} (owner_id, mode, created_at)"))
            if "idx_owner_product_created" not in existing:
                connection.execute(text(f"CREATE INDEX idx_owner_product_created ON {table_name} (owner_id, product_id, created_at)"))
            if "idx_owner_template_created" not in existing:
                connection.execute(text(f"CREATE INDEX idx_owner_template_created ON {table_name} (owner_id, template_id, created_at)"))
            if "idx_owner_favorite_created" not in existing:
                connection.execute(text(f"CREATE INDEX idx_owner_favorite_created ON {table_name} (owner_id, favorite, created_at)"))
            if "ft_prompt" not in existing:
                self._create_prompt_fulltext_index(connection)
            elif not self._prompt_fulltext_uses_ngram(connection):
                connection.execute(text(f"ALTER TABLE {table_name} DROP INDEX ft_prompt"))
                self._create_prompt_fulltext_index(connection)

    def _ensure_business_columns(self, connection, database: str) -> None:
        table_name = self.table_name
        columns = {
            str(row[0])
            for row in connection.execute(
                text(
                    "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                    f"WHERE TABLE_SCHEMA = :schema AND TABLE_NAME = '{table_name}'"
                ),
                {"schema": database},
            )
        }
        additions = {
            "product_id": f"ALTER TABLE {table_name} ADD COLUMN product_id BIGINT NULL",
            "template_id": f"ALTER TABLE {table_name} ADD COLUMN template_id BIGINT NULL",
            "created_by": f"ALTER TABLE {table_name} ADD COLUMN created_by VARCHAR(191) NULL",
            "favorite": f"ALTER TABLE {table_name} ADD COLUMN favorite TINYINT NOT NULL DEFAULT 0",
            "deleted_at": f"ALTER TABLE {table_name} ADD COLUMN deleted_at DATETIME NULL",
        }
        for column, statement in additions.items():
            if column not in columns:
                connection.execute(text(statement))

    def _prompt_fulltext_uses_ngram(self, connection) -> bool:
        row = connection.execute(text(f"SHOW CREATE TABLE {self.table_name}")).first()
        create_sql = str(row[1] if row and len(row) > 1 else "").lower()
        return "fulltext key `ft_prompt`" in create_sql and "with parser `ngram`" in create_sql

    def _create_prompt_fulltext_index(self, connection) -> None:
        try:
            connection.execute(text(f"CREATE FULLTEXT INDEX ft_prompt ON {self.table_name} (prompt, revised_prompt) WITH PARSER ngram"))
        except Exception:
            connection.execute(text(f"CREATE FULLTEXT INDEX ft_prompt ON {self.table_name} (prompt, revised_prompt)"))

    def _session(self):
        if self.Session is None:
            self._init_engine()
        if self.Session is None:
            raise RuntimeError(f"image library database unavailable: {self._init_error}")
        return self.Session()

    def record_task_result(
        self,
        *,
        identity: dict[str, object],
        task: dict[str, Any],
        prompt: str,
        base_url: str,
    ) -> None:
        data = task.get("data")
        if not isinstance(data, list) or not data:
            return

        owner_id = _clean(task.get("owner_id")) or _clean(identity.get("id")) or "local-admin"
        task_id = _clean(task.get("id"))
        product_id = _int_or_none(task.get("product_id"))
        template_id = _int_or_none(task.get("template_id"))
        if not task_id:
            return

        session = self._session()
        Model = self.Model
        try:
            for index, item in enumerate(data):
                if not isinstance(item, dict):
                    continue
                image_rel, image_url, storage, width, height, file_size = _store_result_image(item, base_url)
                row = (
                    session.query(Model)
                    .filter(
                        Model.task_id == task_id,
                        Model.image_index == index,
                    )
                    .one_or_none()
                )
                if row is None:
                    row = Model(task_id=task_id, image_index=index)
                    session.add(row)
                row.owner_id = owner_id
                row.created_by = owner_id
                row.conversation_id = _clean(task.get("conversation_id")) or None
                row.turn_id = _clean(task.get("turn_id")) or None
                row.product_id = product_id
                row.template_id = template_id
                row.mode = _clean(task.get("mode"), "generate")
                row.model = _clean(task.get("model")) or None
                row.prompt = prompt
                row.revised_prompt = _clean(item.get("revised_prompt")) or None
                row.size = _clean(task.get("size")) or None
                row.quality = _clean(task.get("quality")) or None
                row.image_rel = image_rel
                row.image_url = image_url
                row.thumbnail_url = _thumbnail_url(base_url, image_rel, image_url)
                row.width = width
                row.height = height
                row.file_size = file_size
                row.storage = storage
                row.duration_ms = task.get("duration_ms") if isinstance(task.get("duration_ms"), int) else None
                row.updated_at = datetime.now()
            session.commit()
            self._count_cache.clear()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def has_task_result(self, task_id: str) -> bool:
        normalized_task_id = _clean(task_id)
        if not normalized_task_id:
            return False
        session = self._session()
        try:
            return bool(
                session.query(self.Model.id)
                .filter(self.Model.task_id == normalized_task_id)
                .limit(1)
                .first()
            )
        finally:
            session.close()

    def list_images(
        self,
        *,
        identity: dict[str, object],
        base_url: str,
        limit: int = 80,
        offset: int = 0,
        cursor_created_at: str = "",
        cursor_id: int = 0,
        query_text: str = "",
        product_id: int = 0,
        template_id: int = 0,
        favorite_only: bool = False,
        include_deleted: bool = False,
        include_all_owners: bool = False,
        owner_id_filter: str = "",
    ) -> dict[str, Any]:
        owner_id = _clean(identity.get("id")) or "local-admin"
        is_admin = _clean(identity.get("role")) == "admin"
        requested_owner_id = _clean(owner_id_filter)
        all_owners = is_admin and (include_all_owners or bool(requested_owner_id))
        session = self._session()
        Model = self.Model
        try:
            query = session.query(Model)
            if all_owners:
                if requested_owner_id:
                    query = query.filter(Model.owner_id == requested_owner_id)
            else:
                query = query.filter(Model.owner_id == owner_id)
            if not include_deleted:
                query = query.filter(Model.deleted_at.is_(None))
            if product_id > 0:
                query = query.filter(Model.product_id == product_id)
            if template_id > 0:
                query = query.filter(Model.template_id == template_id)
            if favorite_only:
                query = query.filter(Model.favorite == 1)
            keyword = _clean(query_text)
            if keyword:
                query = query.filter(
                    text("MATCH(prompt, revised_prompt) AGAINST (:keyword IN NATURAL LANGUAGE MODE)")
                ).params(keyword=keyword)
            total_key = (
                "all" if all_owners else owner_id,
                requested_owner_id if all_owners else "",
                include_deleted,
                product_id,
                template_id,
                favorite_only,
                keyword,
            )
            total = self._count_cache.get_or_set(total_key, lambda: query.count())
            cursor_time = _parse_datetime(cursor_created_at)
            if cursor_time and cursor_id > 0:
                query = query.filter(
                    or_(
                        Model.created_at < cursor_time,
                        and_(
                            Model.created_at == cursor_time,
                            Model.id < cursor_id,
                        ),
                    )
                )
            page_limit = max(1, min(200, limit))
            rows = (
                query.order_by(desc(Model.created_at), desc(Model.id))
                .offset(0 if cursor_time else max(0, offset))
                .limit(page_limit + 1)
                .all()
            )
            has_more = len(rows) > page_limit
            visible_rows = rows[:page_limit]
            items = [self._public_item(row, base_url) for row in visible_rows]
            next_cursor = None
            if has_more and visible_rows:
                last = visible_rows[-1]
                next_cursor = {
                    "created_at": last.created_at.strftime("%Y-%m-%d %H:%M:%S") if last.created_at else "",
                    "id": last.id,
                }
            return {
                "items": items,
                "total": total,
                "limit": page_limit,
                "offset": offset,
                "has_more": has_more,
                "next_cursor": next_cursor,
            }
        finally:
            session.close()

    def update_image(
        self,
        *,
        identity: dict[str, object],
        image_id: int,
        favorite: bool | None = None,
        deleted: bool | None = None,
    ) -> dict[str, Any] | None:
        owner_id = _clean(identity.get("id")) or "local-admin"
        is_admin = _clean(identity.get("role")) == "admin"
        session = self._session()
        Model = self.Model
        try:
            query = session.query(Model).filter(Model.id == image_id)
            if not is_admin:
                query = query.filter(Model.owner_id == owner_id)
            row = query.one_or_none()
            if row is None:
                return None
            if favorite is not None:
                row.favorite = 1 if favorite else 0
            if deleted is not None:
                row.deleted_at = datetime.now() if deleted else None
            row.updated_at = datetime.now()
            session.commit()
            self._count_cache.clear()
            return self._public_item(row, "")
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def list_images_by_ids(
        self,
        *,
        identity: dict[str, object],
        base_url: str,
        image_ids: list[int],
        include_deleted: bool = False,
    ) -> list[dict[str, Any]]:
        owner_id = _clean(identity.get("id")) or "local-admin"
        is_admin = _clean(identity.get("role")) == "admin"
        ids = list(dict.fromkeys(int(image_id) for image_id in image_ids if int(image_id) > 0))
        if not ids:
            return []

        session = self._session()
        Model = self.Model
        try:
            query = session.query(Model).filter(Model.id.in_(ids))
            if not is_admin:
                query = query.filter(Model.owner_id == owner_id)
            if not include_deleted:
                query = query.filter(Model.deleted_at.is_(None))
            rows = query.all()
            row_map = {int(row.id): row for row in rows}
            return [self._public_item(row_map[image_id], base_url) for image_id in ids if image_id in row_map]
        finally:
            session.close()

    def bulk_delete_images(
        self,
        *,
        identity: dict[str, object],
        image_ids: list[int],
    ) -> dict[str, int]:
        owner_id = _clean(identity.get("id")) or "local-admin"
        is_admin = _clean(identity.get("role")) == "admin"
        ids = list(dict.fromkeys(int(image_id) for image_id in image_ids if int(image_id) > 0))
        if not ids:
            return {"requested": 0, "deleted": 0, "missing": 0}

        session = self._session()
        Model = self.Model
        try:
            query = session.query(Model).filter(Model.id.in_(ids), Model.deleted_at.is_(None))
            if not is_admin:
                query = query.filter(Model.owner_id == owner_id)
            rows = query.all()
            now = datetime.now()
            for row in rows:
                row.deleted_at = now
                row.updated_at = now
            session.commit()
            self._count_cache.clear()
            deleted = len(rows)
            return {"requested": len(ids), "deleted": deleted, "missing": max(0, len(ids) - deleted)}
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def health_check(self) -> dict[str, object]:
        session = self._session()
        try:
            count = session.query(self.Model).count()
            return {"ok": True, "count": count, "table": self.table_name}
        finally:
            session.close()

    @staticmethod
    def _public_item(row: Any, base_url: str) -> dict[str, Any]:
        image_url = _image_url(base_url, row.image_rel, row.image_url)
        thumbnail = _thumbnail_url(base_url, row.image_rel, row.thumbnail_url or image_url)
        return {
            "id": row.id,
            "task_id": row.task_id,
            "owner_id": row.owner_id,
            "mode": row.mode,
            "model": row.model,
            "prompt": row.prompt,
            "revised_prompt": row.revised_prompt,
            "size": row.size,
            "quality": row.quality,
            "product_id": row.product_id,
            "template_id": row.template_id,
            "created_by": row.created_by,
            "image_rel": row.image_rel,
            "image_url": image_url,
            "thumbnail_url": thumbnail,
            "width": row.width,
            "height": row.height,
            "file_size": row.file_size,
            "storage": row.storage,
            "duration_ms": row.duration_ms,
            "favorite": bool(row.favorite),
            "deleted_at": row.deleted_at.strftime("%Y-%m-%d %H:%M:%S") if row.deleted_at else "",
            "created_at": row.created_at.strftime("%Y-%m-%d %H:%M:%S") if row.created_at else "",
        }


image_library_service = ImageLibraryService()
