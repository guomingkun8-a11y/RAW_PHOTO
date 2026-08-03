from __future__ import annotations

from datetime import datetime
import os
from typing import Any

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Index,
    Integer,
    JSON,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import declarative_base


NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

EnterpriseBase = declarative_base(metadata=MetaData(naming_convention=NAMING_CONVENTION))


def _now() -> datetime:
    return datetime.now()


class ImageTaskBatchModel(EnterpriseBase):
    __tablename__ = "image_task_batches"
    __table_args__ = (
        UniqueConstraint("owner_id", "client_request_id", name="uq_image_batch_owner_request"),
        Index("ix_image_batch_owner_status_updated", "owner_id", "status", "updated_at"),
    )

    id = Column(String(191), primary_key=True)
    owner_id = Column(String(191), nullable=False)
    created_by = Column(String(191), nullable=False)
    client_request_id = Column(String(191), nullable=True)
    status = Column(String(32), nullable=False, default="queued")
    priority = Column(Integer, nullable=False, default=0)
    mode = Column(String(32), nullable=False, default="generate")
    model = Column(String(191), nullable=True)
    size = Column(String(64), nullable=True)
    quality = Column(String(64), nullable=True)
    prompt = Column(Text, nullable=True)
    requested_count = Column(Integer, nullable=False, default=1)
    completed_count = Column(Integer, nullable=False, default=0)
    failed_count = Column(Integer, nullable=False, default=0)
    cancelled_count = Column(Integer, nullable=False, default=0)
    metadata_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_now)
    updated_at = Column(DateTime, nullable=False, default=_now, onupdate=_now)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)


class ImageTaskItemModel(EnterpriseBase):
    __tablename__ = "image_task_items"
    __table_args__ = (
        UniqueConstraint("batch_id", "position", name="uq_image_task_batch_position"),
        Index("ix_image_task_status_scheduled", "status", "scheduled_at", "priority"),
        Index("ix_image_task_owner_updated", "owner_id", "updated_at"),
        Index("ix_image_task_lease", "status", "lease_expires_at"),
    )

    id = Column(String(191), primary_key=True)
    batch_id = Column(String(191), nullable=False)
    owner_id = Column(String(191), nullable=False)
    position = Column(Integer, nullable=False, default=0)
    status = Column(String(32), nullable=False, default="queued")
    priority = Column(Integer, nullable=False, default=0)
    attempt_count = Column(Integer, nullable=False, default=0)
    max_retries = Column(Integer, nullable=False, default=2)
    version = Column(Integer, nullable=False, default=1)
    worker_id = Column(String(191), nullable=True)
    lease_token = Column(String(191), nullable=True)
    lease_expires_at = Column(DateTime, nullable=True)
    last_heartbeat_at = Column(DateTime, nullable=True)
    scheduled_at = Column(DateTime, nullable=False, default=_now)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    error_code = Column(String(64), nullable=True)
    error_message = Column(Text, nullable=True)
    payload_json = Column(JSON, nullable=False)
    result_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_now)
    updated_at = Column(DateTime, nullable=False, default=_now, onupdate=_now)


class ImageTaskEventModel(EnterpriseBase):
    __tablename__ = "image_task_events"
    __table_args__ = (
        Index("ix_image_task_event_task_created", "task_id", "created_at"),
        Index("ix_image_task_event_owner_created", "owner_id", "created_at"),
    )

    id = Column(String(191), primary_key=True)
    task_id = Column(String(191), nullable=False)
    batch_id = Column(String(191), nullable=False)
    owner_id = Column(String(191), nullable=False)
    event_type = Column(String(64), nullable=False)
    from_status = Column(String(32), nullable=True)
    to_status = Column(String(32), nullable=True)
    error_code = Column(String(64), nullable=True)
    detail_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_now)


class ImageAssetModel(EnterpriseBase):
    __tablename__ = "image_assets"
    __table_args__ = (
        UniqueConstraint("task_id", "image_index", name="uq_image_asset_task_index"),
        Index("ix_image_asset_owner_created", "owner_id", "created_at"),
        Index("ix_image_asset_batch", "batch_id", "image_index"),
    )

    id = Column(String(191), primary_key=True)
    task_id = Column(String(191), nullable=False)
    batch_id = Column(String(191), nullable=False)
    owner_id = Column(String(191), nullable=False)
    image_index = Column(Integer, nullable=False, default=0)
    asset_type = Column(String(32), nullable=False, default="generated")
    storage_provider = Column(String(64), nullable=False)
    object_key = Column(String(1024), nullable=False)
    url = Column(String(2048), nullable=False)
    thumbnail_url = Column(String(2048), nullable=True)
    mime_type = Column(String(128), nullable=True)
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    file_size = Column(BigInteger, nullable=True)
    sha256 = Column(String(64), nullable=True)
    metadata_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_now)
    deleted_at = Column(DateTime, nullable=True)


class ReferenceImageAssetModel(EnterpriseBase):
    __tablename__ = "reference_image_assets"
    __table_args__ = (
        Index("ix_reference_image_asset_sha256", "sha256"),
        Index("ix_reference_image_asset_last_used", "last_used_at"),
    )

    cache_key = Column(String(64), primary_key=True)
    sha256 = Column(String(64), nullable=False)
    storage_provider = Column(String(64), nullable=False, default="oss")
    bucket = Column(String(191), nullable=False)
    object_key = Column(String(1024), nullable=False)
    url = Column(Text, nullable=False)
    mime_type = Column(String(128), nullable=True)
    file_size = Column(BigInteger, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_now)
    updated_at = Column(DateTime, nullable=False, default=_now, onupdate=_now)
    last_used_at = Column(DateTime, nullable=False, default=_now)


class UsageLedgerModel(EnterpriseBase):
    __tablename__ = "usage_ledger"
    __table_args__ = (
        UniqueConstraint("owner_id", "idempotency_key", name="uq_usage_owner_idempotency"),
        Index("ix_usage_owner_created", "owner_id", "created_at"),
        Index("ix_usage_batch", "batch_id", "created_at"),
    )

    id = Column(String(191), primary_key=True)
    owner_id = Column(String(191), nullable=False)
    actor_id = Column(String(191), nullable=True)
    batch_id = Column(String(191), nullable=True)
    task_id = Column(String(191), nullable=True)
    event_type = Column(String(32), nullable=False)
    units = Column(Integer, nullable=False)
    balance_after = Column(Integer, nullable=True)
    idempotency_key = Column(String(191), nullable=False)
    detail_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_now)


class UpstreamAccountModel(EnterpriseBase):
    __tablename__ = "upstream_accounts"
    __table_args__ = (
        UniqueConstraint("provider", "account_ref", name="uq_upstream_provider_account"),
        Index("ix_upstream_status_rate_limit", "status", "rate_limited_until"),
    )

    id = Column(String(191), primary_key=True)
    provider = Column(String(64), nullable=False)
    account_ref = Column(String(191), nullable=False)
    credential_ref = Column(String(512), nullable=True)
    status = Column(String(32), nullable=False, default="active")
    max_concurrency = Column(Integer, nullable=False, default=1)
    inflight_count = Column(Integer, nullable=False, default=0)
    remaining_quota = Column(Integer, nullable=True)
    rate_limited_until = Column(DateTime, nullable=True)
    last_success_at = Column(DateTime, nullable=True)
    last_error_at = Column(DateTime, nullable=True)
    last_error_code = Column(String(64), nullable=True)
    metadata_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_now)
    updated_at = Column(DateTime, nullable=False, default=_now, onupdate=_now)


ENTERPRISE_TABLES = tuple(sorted(EnterpriseBase.metadata.tables))


def resolve_enterprise_database_url(override: str | None = None) -> str:
    database_url = str(
        override
        or os.getenv("GMKRAW_DATABASE_URL")
        or os.getenv("IMAGE_TASK_DATABASE_URL")
        or os.getenv("IMAGE_LIBRARY_DATABASE_URL")
        or os.getenv("MYSQL_DATABASE_URL")
        or ""
    ).strip()
    if not database_url:
        raise RuntimeError(
            "enterprise database URL is required; set GMKRAW_DATABASE_URL or MYSQL_DATABASE_URL"
        )
    return database_url


def create_enterprise_engine(database_url: str) -> Engine:
    return create_engine(database_url, pool_pre_ping=True, pool_recycle=3600)


def create_enterprise_schema(
    database_url: str | None = None,
    *,
    engine: Engine | None = None,
) -> tuple[str, ...]:
    target_engine = engine or create_enterprise_engine(resolve_enterprise_database_url(database_url))
    EnterpriseBase.metadata.create_all(target_engine)
    return ENTERPRISE_TABLES


def schema_summary() -> list[dict[str, Any]]:
    return [
        {
            "table": table.name,
            "columns": [column.name for column in table.columns],
            "indexes": sorted(index.name or "" for index in table.indexes),
        }
        for table in EnterpriseBase.metadata.sorted_tables
    ]
