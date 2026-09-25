from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.orm import declarative_base


BillingBase = declarative_base()
TIMESTAMP = DateTime().with_variant(DATETIME(fsp=6), "mysql")


class UpstreamUsageRecord(BillingBase):
    __tablename__ = "upstream_usage_records"
    __table_args__ = (
        UniqueConstraint("provider", "upstream_task_id", name="uq_upstream_usage_provider_task"),
        Index("idx_upstream_usage_event", "event_at", "id"),
        Index("idx_upstream_usage_type_event", "model_type", "event_at", "id"),
        Index("idx_upstream_usage_owner_event", "owner_id", "event_at", "id"),
        Index("idx_upstream_usage_model_event", "model", "event_at", "id"),
        Index("idx_upstream_usage_requested_model_event", "requested_model", "event_at", "id"),
        Index("idx_upstream_usage_local_task", "local_source", "local_task_id"),
    )

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    provider = Column(String(64), nullable=False)
    upstream_task_id = Column(String(255), nullable=False)
    model = Column(String(191), nullable=False, default="unknown")
    requested_model = Column(String(191), nullable=False, default="")
    model_version = Column(String(64), nullable=False, default="")
    model_type = Column(String(32), nullable=False, default="unknown")
    channel_group = Column(String(191), nullable=False, default="")
    state = Column(String(32), nullable=False, default="unknown")
    unit = Column(String(32), nullable=False, default="")
    cost = Column(Numeric(30, 12), nullable=False, default=0)
    refunded = Column(Boolean, nullable=False, default=False)
    refunded_amount = Column(Numeric(30, 12), nullable=False, default=0)
    upstream_created_at = Column(TIMESTAMP, nullable=True)
    upstream_completed_at = Column(TIMESTAMP, nullable=True)
    event_at = Column(TIMESTAMP, nullable=False)
    sync_key_fingerprint = Column(String(64), nullable=False, default="")
    local_source = Column(String(32), nullable=False, default="")
    local_task_id = Column(String(191), nullable=False, default="")
    owner_id = Column(String(191), nullable=True)
    first_seen_at = Column(TIMESTAMP, nullable=False, default=datetime.now)
    updated_at = Column(TIMESTAMP, nullable=False, default=datetime.now, onupdate=datetime.now)


class UpstreamUsageAttribution(BillingBase):
    """Local correlation for non-media requests made through the relay."""

    __tablename__ = "upstream_usage_attributions"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "local_source",
            "local_task_id",
            name="uq_upstream_usage_attribution_local_task",
        ),
        Index("idx_upstream_usage_attribution_upstream", "provider", "upstream_task_id"),
        Index("idx_upstream_usage_attribution_owner_event", "owner_id", "created_at", "id"),
    )

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    provider = Column(String(64), nullable=False, default="lk888")
    upstream_task_id = Column(String(255), nullable=False, default="")
    upstream_request_id = Column(String(255), nullable=False, default="")
    model_type = Column(String(32), nullable=False, default="chat")
    requested_model = Column(String(191), nullable=False, default="")
    model_version = Column(String(64), nullable=False, default="")
    local_source = Column(String(32), nullable=False, default="chat")
    local_task_id = Column(String(191), nullable=False)
    owner_id = Column(String(191), nullable=False)
    created_at = Column(TIMESTAMP, nullable=False, default=datetime.now)
    updated_at = Column(TIMESTAMP, nullable=False, default=datetime.now, onupdate=datetime.now)


class UpstreamUsageSyncState(BillingBase):
    __tablename__ = "upstream_usage_sync_state"

    provider = Column(String(64), primary_key=True)
    status = Column(String(32), nullable=False, default="idle")
    last_started_at = Column(TIMESTAMP, nullable=True)
    last_success_at = Column(TIMESTAMP, nullable=True)
    last_error_at = Column(TIMESTAMP, nullable=True)
    last_error = Column(Text, nullable=False, default="")
    last_window_from = Column(TIMESTAMP, nullable=True)
    last_window_to = Column(TIMESTAMP, nullable=True)
    last_full_sync_at = Column(TIMESTAMP, nullable=True)
    records_seen = Column(BigInteger, nullable=False, default=0)
    records_upserted = Column(BigInteger, nullable=False, default=0)
    lease_owner = Column(String(64), nullable=False, default="")
    lease_expires_at = Column(TIMESTAMP, nullable=True)
    updated_at = Column(TIMESTAMP, nullable=False, default=datetime.now, onupdate=datetime.now)
