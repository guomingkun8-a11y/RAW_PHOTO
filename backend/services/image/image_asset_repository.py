from __future__ import annotations

import random
import time
from datetime import datetime
from hashlib import sha256
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import sessionmaker

from services.platform.enterprise_schema import ImageAssetModel, resolve_enterprise_database_url


MYSQL_RETRYABLE_CODES = {1205, 1213}


def _retryable_mysql_error(exc: BaseException) -> bool:
    current: BaseException | None = exc
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        for value in getattr(current, "args", ()):
            try:
                if int(value) in MYSQL_RETRYABLE_CODES:
                    return True
            except (TypeError, ValueError):
                continue
        nested = getattr(current, "orig", None) or getattr(current, "__cause__", None)
        current = nested if isinstance(nested, BaseException) else None
    return False


class ImageAssetRepository:
    def __init__(self, database_url: str):
        options: dict[str, Any] = {"pool_pre_ping": True, "pool_recycle": 3600}
        if database_url.startswith("sqlite"):
            options["connect_args"] = {"check_same_thread": False, "timeout": 30}
        else:
            options.update(pool_size=5, max_overflow=10)
        self.engine = create_engine(database_url, **options)
        ImageAssetModel.__table__.create(self.engine, checkfirst=True)
        self.Session = sessionmaker(bind=self.engine)

    def _write(self, operation):
        attempts = 3 if self.engine.dialect.name == "mysql" else 1
        for attempt in range(attempts):
            session = self.Session()
            try:
                result = operation(session)
                session.commit()
                return result
            except Exception as exc:
                session.rollback()
                if not _retryable_mysql_error(exc) or attempt + 1 >= attempts:
                    raise
                time.sleep(random.uniform(0.025, 0.075) * (2**attempt))
            finally:
                session.close()
        raise RuntimeError("image asset transaction retry exhausted")

    @staticmethod
    def _identity(owner_id: str, task_id: str, asset_type: str, image_index: str) -> str:
        value = f"{owner_id}\0{task_id}\0{asset_type}\0{image_index}"
        return sha256(value.encode("utf-8")).hexdigest()

    def upsert(
        self,
        *,
        owner_id: str,
        task_id: str,
        asset_type: str,
        image_index: str,
        batch_id: str = "",
        storage_provider: str,
        object_key: str,
        url: str,
        mime_type: str = "",
        width: int | None = None,
        height: int | None = None,
        file_size: int | None = None,
        digest: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        normalized_owner = str(owner_id or "system")[:191]
        normalized_task = str(task_id or object_key)[:191]
        normalized_type = str(asset_type or "generated")[:32]
        normalized_index = str(image_index or "0")[:191]
        values = {
            "id": self._identity(normalized_owner, normalized_task, normalized_type, normalized_index),
            "owner_id": normalized_owner,
            "task_id": normalized_task,
            "batch_id": str(batch_id or "")[:191],
            "image_index": normalized_index,
            "asset_type": normalized_type,
            "storage_provider": str(storage_provider or "local")[:64],
            "object_key": str(object_key)[:1024],
            "url": str(url)[:2048],
            "thumbnail_url": None,
            "mime_type": str(mime_type or "")[:128] or None,
            "width": width,
            "height": height,
            "file_size": file_size,
            "sha256": str(digest or "")[:64] or None,
            "metadata_json": dict(metadata or {}),
            "created_at": datetime.now(),
            "deleted_at": None,
        }
        update_values = {
            key: values[key]
            for key in (
                "batch_id",
                "storage_provider",
                "object_key",
                "url",
                "mime_type",
                "width",
                "height",
                "file_size",
                "sha256",
                "metadata_json",
                "deleted_at",
            )
        }

        def operation(session):
            table = ImageAssetModel.__table__
            if self.engine.dialect.name == "mysql":
                statement = mysql_insert(table).values(**values).on_duplicate_key_update(**update_values)
            elif self.engine.dialect.name == "postgresql":
                statement = postgresql_insert(table).values(**values).on_conflict_do_update(
                    constraint="uq_image_asset_owner_task_type_index",
                    set_=update_values,
                )
            else:
                statement = sqlite_insert(table).values(**values).on_conflict_do_update(
                    index_elements=["owner_id", "task_id", "asset_type", "image_index"],
                    set_=update_values,
                )
            session.execute(statement)

        self._write(operation)

    @staticmethod
    def _row_item(row: ImageAssetModel) -> dict[str, Any]:
        metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
        return {
            **metadata,
            "rel": row.object_key,
            "path": row.object_key,
            "name": metadata.get("name") or row.object_key.rsplit("/", 1)[-1],
            "size": row.file_size,
            "created_at": row.created_at.strftime("%Y-%m-%d %H:%M:%S") if row.created_at else "",
            "storage": row.storage_provider,
            "remote_provider": metadata.get("remote_provider") or row.storage_provider,
            "remote_url": row.url,
            "asset_type": row.asset_type,
            "owner_id": row.owner_id,
            "task_id": row.task_id,
            "image_index": row.image_index,
            "width": row.width,
            "height": row.height,
        }

    def get(self, object_key: str) -> dict[str, Any]:
        session = self.Session()
        try:
            row = (
                session.query(ImageAssetModel)
                .filter(
                    ImageAssetModel.object_key == object_key,
                    ImageAssetModel.deleted_at.is_(None),
                )
                .order_by(ImageAssetModel.created_at.desc())
                .first()
            )
            return self._row_item(row) if row is not None else {}
        finally:
            session.close()

    def list_items(self) -> list[dict[str, Any]]:
        session = self.Session()
        try:
            rows = (
                session.query(ImageAssetModel)
                .filter(ImageAssetModel.deleted_at.is_(None))
                .order_by(ImageAssetModel.created_at.desc())
                .all()
            )
            return [self._row_item(row) for row in rows]
        finally:
            session.close()

    def delete(self, object_key: str) -> int:
        def operation(session):
            return int(
                session.query(ImageAssetModel)
                .filter(ImageAssetModel.object_key == object_key)
                .delete(synchronize_session=False)
                or 0
            )

        return self._write(operation)

    def close(self) -> None:
        self.engine.dispose()


def create_default_image_asset_repository() -> ImageAssetRepository | None:
    try:
        return ImageAssetRepository(resolve_enterprise_database_url())
    except RuntimeError:
        return None
