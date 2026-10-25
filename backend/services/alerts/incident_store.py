"""Persistence for alert settings, incidents, deduplication, and recovery."""

from __future__ import annotations

import base64
from datetime import datetime, timedelta
import hashlib
import secrets
import threading
from typing import Any, Mapping
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from Crypto.Cipher import AES
from sqlalchemy import Column, DateTime, Index, Integer, JSON, String, Text, create_engine, desc
from sqlalchemy.orm import declarative_base, sessionmaker

from services.alerts.settings import AlertSettings
from services.platform.enterprise_schema import resolve_enterprise_database_url


AlertBase = declarative_base()
SETTINGS_ID = "default"


def _now() -> datetime:
    return datetime.now()


class AlertRuntimeSettingsModel(AlertBase):
    __tablename__ = "operational_alert_settings"

    id = Column(String(32), primary_key=True, default=SETTINGS_ID)
    enabled = Column(Integer, nullable=False, default=0)
    check_interval_secs = Column(Integer, nullable=False, default=30)
    cooldown_secs = Column(Integer, nullable=False, default=600)
    repeat_secs = Column(Integer, nullable=False, default=1800)
    queue_waiting_threshold = Column(Integer, nullable=False, default=20)
    dead_letter_threshold = Column(Integer, nullable=False, default=1)
    stale_task_threshold = Column(Integer, nullable=False, default=1)
    worker_missing_alert = Column(Integer, nullable=False, default=1)
    recovery_notifications = Column(Integer, nullable=False, default=1)
    webhooks_initialized = Column(Integer, nullable=False, default=0)
    last_check_at = Column(DateTime, nullable=True)
    last_check_status = Column(String(32), nullable=False, default="never")
    last_check_error = Column(Text, nullable=True)
    last_check_duration_ms = Column(Integer, nullable=False, default=0)
    worker_id = Column(String(191), nullable=True)
    updated_by = Column(String(191), nullable=True)
    created_at = Column(DateTime, nullable=False, default=_now)
    updated_at = Column(DateTime, nullable=False, default=_now, onupdate=_now)


class AlertIncidentModel(AlertBase):
    __tablename__ = "operational_alert_incidents"
    __table_args__ = (
        Index("ix_alert_incident_state_last_seen", "state", "last_seen_at"),
        Index("ix_alert_incident_updated", "updated_at"),
    )

    id = Column(String(36), primary_key=True)
    fingerprint = Column(String(64), nullable=False, unique=True)
    kind = Column(String(64), nullable=False)
    source = Column(String(191), nullable=False)
    title = Column(String(191), nullable=False)
    summary = Column(Text, nullable=False)
    severity = Column(String(8), nullable=False, default="P2")
    state = Column(String(32), nullable=False, default="open")
    occurrence_count = Column(Integer, nullable=False, default=1)
    details_json = Column(JSON, nullable=True)
    first_seen_at = Column(DateTime, nullable=False, default=_now)
    last_seen_at = Column(DateTime, nullable=False, default=_now)
    last_notified_at = Column(DateTime, nullable=True)
    next_notification_at = Column(DateTime, nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_now)
    updated_at = Column(DateTime, nullable=False, default=_now, onupdate=_now)


class AlertWebhookModel(AlertBase):
    __tablename__ = "operational_alert_webhooks"

    id = Column(String(36), primary_key=True)
    name = Column(String(80), nullable=False)
    url_ciphertext = Column(Text, nullable=False)
    url_fingerprint = Column(String(64), nullable=False, unique=True)
    enabled = Column(Integer, nullable=False, default=1)
    receive_alerts = Column(Integer, nullable=False, default=0)
    receive_feedback = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=_now)
    updated_at = Column(DateTime, nullable=False, default=_now, onupdate=_now)


_RUNTIME_FIELDS = {
    "enabled",
    "check_interval_secs",
    "cooldown_secs",
    "repeat_secs",
    "queue_waiting_threshold",
    "dead_letter_threshold",
    "stale_task_threshold",
    "worker_missing_alert",
    "recovery_notifications",
}
_BOOLEAN_FIELDS = {"enabled", "worker_missing_alert", "recovery_notifications"}


def _cipher_key(settings: AlertSettings) -> bytes:
    if not settings.internal_token:
        raise ValueError("WECOM_ALERT_INTERNAL_TOKEN must be configured to store Webhooks")
    return hashlib.sha256(settings.internal_token.encode("utf-8")).digest()


def _encrypt_url(url: str, settings: AlertSettings) -> str:
    cipher = AES.new(_cipher_key(settings), AES.MODE_GCM, nonce=secrets.token_bytes(16))
    encrypted, tag = cipher.encrypt_and_digest(url.encode("utf-8"))
    return "v1:" + base64.urlsafe_b64encode(cipher.nonce + tag + encrypted).decode("ascii")


def _decrypt_url(value: str, settings: AlertSettings) -> str:
    if not value.startswith("v1:"):
        raise ValueError("Unsupported Webhook encryption format")
    payload = base64.urlsafe_b64decode(value[3:])
    cipher = AES.new(_cipher_key(settings), AES.MODE_GCM, nonce=payload[:16])
    return cipher.decrypt_and_verify(payload[32:], payload[16:32]).decode("utf-8")


def _webhook_hint(url: str) -> str:
    key = parse_qs(urlsplit(url).query).get("key", [""])[0]
    return f"企业微信机器人 · 尾号 {key[-4:]}" if len(key) > 8 else "企业微信机器人 · 已配置"


class AlertIncidentStore:
    def __init__(self, database_url: str | None = None) -> None:
        self.database_url = database_url
        self.engine = None
        self.Session = None
        self._init_error = ""
        self._lock = threading.Lock()

    def _init_engine(self) -> None:
        if self.Session is not None:
            return
        with self._lock:
            if self.Session is not None:
                return
            try:
                database_url = self.database_url or resolve_enterprise_database_url()
                options: dict[str, object] = {"pool_pre_ping": True, "pool_recycle": 3600}
                if database_url.startswith("sqlite"):
                    options["connect_args"] = {"check_same_thread": False}
                engine = create_engine(database_url, **options)
                AlertBase.metadata.create_all(engine)
                self.engine = engine
                self.Session = sessionmaker(bind=engine, expire_on_commit=False)
                self._init_error = ""
            except Exception as exc:
                self.engine = None
                self.Session = None
                self._init_error = str(exc)

    def _session(self):
        self._init_engine()
        if self.Session is None:
            raise RuntimeError(f"alert database unavailable: {self._init_error}")
        return self.Session()

    @staticmethod
    def _ensure_settings(session, defaults: AlertSettings) -> AlertRuntimeSettingsModel:
        row = session.get(AlertRuntimeSettingsModel, SETTINGS_ID)
        if row is not None:
            return row
        values = defaults.runtime_values()
        row = AlertRuntimeSettingsModel(
            id=SETTINGS_ID,
            **{
                name: (1 if bool(value) else 0) if name in _BOOLEAN_FIELDS else int(value)
                for name, value in values.items()
            },
        )
        session.add(row)
        session.flush()
        return row

    @staticmethod
    def _runtime_values(row: AlertRuntimeSettingsModel) -> dict[str, object]:
        return {
            name: bool(getattr(row, name)) if name in _BOOLEAN_FIELDS else int(getattr(row, name) or 0)
            for name in _RUNTIME_FIELDS
        }

    @staticmethod
    def _monitor_values(row: AlertRuntimeSettingsModel) -> dict[str, object]:
        return {
            "last_check_at": row.last_check_at.isoformat(timespec="seconds") if row.last_check_at else "",
            "last_check_status": row.last_check_status or "never",
            "last_check_error": row.last_check_error or "",
            "last_check_duration_ms": int(row.last_check_duration_ms or 0),
            "worker_id": row.worker_id or "",
            "updated_at": row.updated_at.isoformat(timespec="seconds") if row.updated_at else "",
        }

    @staticmethod
    def _public_webhook(row: AlertWebhookModel, settings: AlertSettings) -> dict[str, object]:
        return {
            "id": row.id,
            "name": row.name,
            "enabled": bool(row.enabled),
            "receive_alerts": bool(row.receive_alerts),
            "receive_feedback": bool(row.receive_feedback),
            "url_hint": _webhook_hint(_decrypt_url(row.url_ciphertext, settings)),
            "created_at": row.created_at.isoformat(timespec="seconds") if row.created_at else "",
        }

    def _ensure_webhooks(self, session, settings: AlertSettings) -> None:
        row = self._ensure_settings(session, settings)
        session.query(AlertRuntimeSettingsModel).filter_by(id=SETTINGS_ID).with_for_update().one()
        if row.webhooks_initialized:
            return
        if settings.webhook_url:
            url = AlertSettings.validate_webhook_url(settings.webhook_url)
            fingerprint = hashlib.sha256(url.encode("utf-8")).hexdigest()
            if not session.query(AlertWebhookModel).filter_by(url_fingerprint=fingerprint).first():
                session.add(AlertWebhookModel(
                    id=str(uuid4()), name="原有企业微信机器人",
                    url_ciphertext=_encrypt_url(url, settings),
                    url_fingerprint=fingerprint, enabled=1, receive_alerts=1,
                ))
        row.webhooks_initialized = 1
        session.flush()

    def list_webhooks(
        self, settings: AlertSettings, *, active_only: bool = False, include_url: bool = False, purpose: str | None = None
    ) -> list[dict[str, object]]:
        session = self._session()
        try:
            self._ensure_webhooks(session, settings)
            query = session.query(AlertWebhookModel)
            if active_only:
                query = query.filter_by(enabled=1)
            if purpose == "alerts":
                query = query.filter_by(receive_alerts=1)
            elif purpose == "feedback":
                query = query.filter_by(receive_feedback=1)
            elif purpose is not None:
                raise ValueError("Unknown notification purpose")
            rows = query.order_by(AlertWebhookModel.created_at, AlertWebhookModel.id).all()
            result = [self._public_webhook(row, settings) for row in rows]
            if include_url:
                for item, row in zip(result, rows):
                    item["url"] = _decrypt_url(row.url_ciphertext, settings)
            session.commit()
            return result
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def add_webhook(self, settings: AlertSettings, *, name: str, url: str) -> dict[str, object]:
        clean_name = str(name or "").strip()[:80]
        if not clean_name:
            raise ValueError("请输入机器人名称")
        clean_url = AlertSettings.validate_webhook_url(url)
        fingerprint = hashlib.sha256(clean_url.encode("utf-8")).hexdigest()
        session = self._session()
        try:
            self._ensure_webhooks(session, settings)
            if session.query(AlertWebhookModel).filter_by(url_fingerprint=fingerprint).first():
                raise ValueError("这个机器人已经添加")
            row = AlertWebhookModel(
                id=str(uuid4()), name=clean_name,
                url_ciphertext=_encrypt_url(clean_url, settings),
                url_fingerprint=fingerprint, enabled=1,
            )
            session.add(row)
            session.commit()
            return self._public_webhook(row, settings)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def update_webhook(
        self, settings: AlertSettings, webhook_id: str, *, name: str | None, enabled: bool | None,
        receive_alerts: bool | None = None, receive_feedback: bool | None = None,
    ) -> dict[str, object] | None:
        session = self._session()
        try:
            self._ensure_webhooks(session, settings)
            row = session.get(AlertWebhookModel, webhook_id)
            if row is None:
                return None
            if name is not None:
                clean_name = name.strip()[:80]
                if not clean_name:
                    raise ValueError("机器人名称不能为空")
                row.name = clean_name
            if enabled is not None:
                row.enabled = int(enabled)
            if receive_alerts is not None:
                row.receive_alerts = int(receive_alerts)
            if receive_feedback is not None:
                row.receive_feedback = int(receive_feedback)
            row.updated_at = _now()
            session.flush()
            self._pause_if_no_destinations(session, settings)
            session.commit()
            return self._public_webhook(row, settings)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def delete_webhook(self, settings: AlertSettings, webhook_id: str) -> bool:
        session = self._session()
        try:
            self._ensure_webhooks(session, settings)
            row = session.get(AlertWebhookModel, webhook_id)
            if row is None:
                return False
            session.delete(row)
            session.flush()
            self._pause_if_no_destinations(session, settings)
            session.commit()
            return True
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _pause_if_no_destinations(self, session, settings: AlertSettings) -> None:
        if session.query(AlertWebhookModel).filter_by(enabled=1, receive_alerts=1).count() == 0:
            row = self._ensure_settings(session, settings)
            row.enabled = 0
            row.updated_at = _now()

    def load_runtime_settings(self, defaults: AlertSettings) -> dict[str, object]:
        session = self._session()
        try:
            row = self._ensure_settings(session, defaults)
            session.commit()
            return self._runtime_values(row)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def settings_snapshot(self, defaults: AlertSettings) -> dict[str, object]:
        session = self._session()
        try:
            row = self._ensure_settings(session, defaults)
            session.commit()
            return {**self._runtime_values(row), **self._monitor_values(row)}
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def update_runtime_settings(
        self,
        updates: Mapping[str, object],
        *,
        defaults: AlertSettings,
        updated_by: str,
    ) -> dict[str, object]:
        session = self._session()
        try:
            row = self._ensure_settings(session, defaults)
            for name in _RUNTIME_FIELDS:
                if name not in updates:
                    continue
                value = updates[name]
                setattr(row, name, (1 if bool(value) else 0) if name in _BOOLEAN_FIELDS else int(value))
            row.updated_by = str(updated_by or "admin")[:191]
            row.updated_at = _now()
            session.commit()
            return {**self._runtime_values(row), **self._monitor_values(row)}
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def record_check(
        self,
        *,
        status: str,
        duration_ms: int,
        error: str = "",
        worker_id: str = "",
        defaults: AlertSettings,
    ) -> None:
        session = self._session()
        try:
            row = self._ensure_settings(session, defaults)
            row.last_check_at = _now()
            row.last_check_status = str(status or "unknown")[:32]
            row.last_check_error = str(error or "")[:1000] or None
            row.last_check_duration_ms = max(0, int(duration_ms or 0))
            row.worker_id = str(worker_id or "")[:191] or None
            row.updated_at = _now()
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def observe(
        self,
        finding: Mapping[str, object],
        *,
        now: datetime,
        cooldown_secs: int,
        repeat_secs: int,
    ) -> tuple[dict[str, object], bool]:
        fingerprint = str(finding.get("fingerprint") or "")[:64]
        if not fingerprint:
            raise ValueError("alert finding fingerprint is required")
        session = self._session()
        try:
            row = (
                session.query(AlertIncidentModel)
                .filter(AlertIncidentModel.fingerprint == fingerprint)
                .with_for_update()
                .one_or_none()
            )
            if row is None:
                row = AlertIncidentModel(
                    id=str(uuid4()),
                    fingerprint=fingerprint,
                    kind=str(finding.get("kind") or "unknown")[:64],
                    source=str(finding.get("source") or "unknown")[:191],
                    title=str(finding.get("title") or "系统异常")[:191],
                    summary=str(finding.get("summary") or "等待进一步诊断")[:2000],
                    severity=str(finding.get("severity") or "P2")[:8],
                    state="open",
                    occurrence_count=1,
                    details_json=dict(finding.get("details") or {}),
                    first_seen_at=now,
                    last_seen_at=now,
                    next_notification_at=now,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
                session.flush()
            else:
                reopened = row.state != "open"
                previous_resolved_at = row.resolved_at
                if reopened:
                    row.state = "open"
                    row.occurrence_count = 1
                    row.first_seen_at = now
                    row.last_notified_at = None
                    row.next_notification_at = max(
                        now,
                        (previous_resolved_at or now) + timedelta(seconds=max(30, cooldown_secs)),
                    )
                    row.resolved_at = None
                else:
                    row.occurrence_count = int(row.occurrence_count or 0) + 1
                row.kind = str(finding.get("kind") or row.kind)[:64]
                row.source = str(finding.get("source") or row.source)[:191]
                row.title = str(finding.get("title") or row.title)[:191]
                row.summary = str(finding.get("summary") or row.summary)[:2000]
                row.severity = str(finding.get("severity") or row.severity)[:8]
                row.details_json = dict(finding.get("details") or {})
                row.last_seen_at = now
                row.updated_at = now

            notification_due = row.next_notification_at is None or row.next_notification_at <= now
            if notification_due:
                row.last_notified_at = now
                row.next_notification_at = now + timedelta(seconds=max(60, repeat_secs))
            session.commit()
            return self._public_incident(row), notification_due
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def defer_notification(self, fingerprint: str, *, retry_secs: int = 30) -> None:
        session = self._session()
        try:
            row = session.query(AlertIncidentModel).filter(
                AlertIncidentModel.fingerprint == str(fingerprint)
            ).one_or_none()
            if row is not None and row.state == "open":
                row.last_notified_at = None
                row.next_notification_at = _now() + timedelta(seconds=max(10, retry_secs))
                row.updated_at = _now()
                session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def resolve_missing(
        self,
        active_fingerprints: set[str],
        *,
        now: datetime,
    ) -> list[dict[str, object]]:
        session = self._session()
        resolved: list[dict[str, object]] = []
        try:
            rows = session.query(AlertIncidentModel).filter(AlertIncidentModel.state == "open").all()
            for row in rows:
                if row.fingerprint in active_fingerprints:
                    continue
                row.state = "resolved"
                row.resolved_at = now
                row.updated_at = now
                if row.last_notified_at is not None:
                    resolved.append(self._public_incident(row))
            session.commit()
            return resolved
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def list_incidents(self, *, limit: int = 30) -> dict[str, object]:
        session = self._session()
        try:
            rows = (
                session.query(AlertIncidentModel)
                .order_by(desc(AlertIncidentModel.updated_at))
                .limit(min(100, max(1, int(limit or 30))))
                .all()
            )
            open_count = session.query(AlertIncidentModel).filter(
                AlertIncidentModel.state == "open"
            ).count()
            return {
                "items": [self._public_incident(row) for row in rows],
                "total": len(rows),
                "open_count": int(open_count or 0),
            }
        finally:
            session.close()

    @staticmethod
    def _public_incident(row: AlertIncidentModel) -> dict[str, object]:
        def stamp(value: datetime | None) -> str:
            return value.isoformat(timespec="seconds") if value else ""

        return {
            "incident_id": row.id,
            "fingerprint": row.fingerprint,
            "kind": row.kind,
            "source": row.source,
            "title": row.title,
            "summary": row.summary,
            "severity": row.severity,
            "state": row.state,
            "occurrence_count": int(row.occurrence_count or 0),
            "details": dict(row.details_json or {}),
            "first_seen_at": stamp(row.first_seen_at),
            "last_seen_at": stamp(row.last_seen_at),
            "last_notified_at": stamp(row.last_notified_at),
            "resolved_at": stamp(row.resolved_at),
            "updated_at": stamp(row.updated_at),
        }

    def close(self) -> None:
        if self.engine is not None:
            self.engine.dispose()
        self.engine = None
        self.Session = None


alert_incident_store = AlertIncidentStore()
