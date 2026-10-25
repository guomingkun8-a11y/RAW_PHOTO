"""Coordinates checks, alert rules, incident state, and notifications."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import os
import socket
import threading
import time
from typing import Any, Callable, Mapping

from services.alerts.checks import collect_health_snapshot
from services.alerts.incident_store import AlertIncidentStore, alert_incident_store
from services.alerts.rules import AlertFinding, evaluate_health_snapshot
from services.alerts.settings import AlertSettings
from services.alerts.wecom_notifier import WeComNotifier
from utils.log import logger


class AlertService:
    def __init__(
        self,
        *,
        store: AlertIncidentStore = alert_incident_store,
        settings_factory: Callable[[], AlertSettings] = AlertSettings.from_env,
        snapshot_loader: Callable[[], dict[str, Any]] = collect_health_snapshot,
        notifier_factory: Callable[[AlertSettings], WeComNotifier] = WeComNotifier,
        worker_id: str | None = None,
    ) -> None:
        self.store = store
        self.settings_factory = settings_factory
        self.snapshot_loader = snapshot_loader
        self.notifier_factory = notifier_factory
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}"
        self._fallback_open: dict[str, dict[str, object]] = {}
        self._fallback_resolved_at: dict[str, float] = {}
        self._fallback_lock = threading.Lock()
        self._webhook_cache: list[dict[str, object]] = []

    def current_settings(self) -> AlertSettings:
        defaults = self.settings_factory()
        try:
            return defaults.with_runtime(self.store.load_runtime_settings(defaults))
        except Exception as exc:
            logger.info({"event": "alert_settings_fallback", "error": str(exc)[:300]})
            return defaults

    def admin_snapshot(self) -> dict[str, object]:
        defaults = self.settings_factory()
        try:
            stored = self.store.settings_snapshot(defaults)
            settings = defaults.with_runtime(stored)
            webhooks = self.store.list_webhooks(defaults)
            monitor = {
                name: stored.get(name)
                for name in (
                    "last_check_at",
                    "last_check_status",
                    "last_check_error",
                    "last_check_duration_ms",
                    "worker_id",
                    "updated_at",
                )
            }
            public_settings = settings.public_values()
            public_settings["webhook_configured"] = any(item["enabled"] and item["receive_alerts"] for item in webhooks)
            return {"settings": public_settings, "monitor": monitor, "webhooks": webhooks}
        except Exception as exc:
            return {
                "settings": {**defaults.public_values(), "webhook_configured": False},
                "webhooks": [],
                "monitor": {
                    "last_check_at": "",
                    "last_check_status": "unavailable",
                    "last_check_error": str(exc)[:300],
                    "last_check_duration_ms": 0,
                    "worker_id": "",
                    "updated_at": "",
                },
            }

    def update_settings(
        self,
        updates: Mapping[str, object],
        *,
        updated_by: str,
    ) -> dict[str, object]:
        defaults = self.settings_factory()
        current = defaults.with_runtime(self.store.load_runtime_settings(defaults))
        updated = current.with_runtime(updates)
        webhooks = self.store.list_webhooks(defaults)
        if updated.enabled and not any(item["enabled"] and item["receive_alerts"] for item in webhooks):
            raise ValueError("请先指定接收监控告警的机器人")
        stored = self.store.update_runtime_settings(
            updated.runtime_values(),
            defaults=defaults,
            updated_by=updated_by,
        )
        return {
            "settings": {**defaults.with_runtime(stored).public_values(), "webhook_configured": any(item["enabled"] and item["receive_alerts"] for item in webhooks)},
            "webhooks": webhooks,
            "monitor": {
                name: stored.get(name)
                for name in (
                    "last_check_at",
                    "last_check_status",
                    "last_check_error",
                    "last_check_duration_ms",
                    "worker_id",
                    "updated_at",
                )
            },
        }

    def add_webhook(self, *, name: str, url: str) -> dict[str, object]:
        return self.store.add_webhook(self.settings_factory(), name=name, url=url)

    def update_webhook(self, webhook_id: str, *, name: str | None, enabled: bool | None,
                       receive_alerts: bool | None = None, receive_feedback: bool | None = None) -> dict[str, object] | None:
        return self.store.update_webhook(self.settings_factory(), webhook_id, name=name, enabled=enabled,
                                         receive_alerts=receive_alerts, receive_feedback=receive_feedback)

    def delete_webhook(self, webhook_id: str) -> bool:
        return self.store.delete_webhook(self.settings_factory(), webhook_id)

    def send_test(self, webhook_id: str) -> bool:
        settings = self.settings_factory()
        webhook = next(
            (item for item in self.store.list_webhooks(settings, include_url=True) if item["id"] == webhook_id),
            None,
        )
        if webhook is None:
            raise ValueError("机器人不存在")
        return self.notifier_factory(replace(settings, enabled=True, webhook_url=str(webhook["url"]))).send_test()

    def _active_webhooks(self, settings: AlertSettings) -> list[dict[str, object]]:
        try:
            webhooks = self.store.list_webhooks(settings, active_only=True, include_url=True, purpose="alerts")
            self._webhook_cache = webhooks
            return webhooks
        except Exception:
            if self._webhook_cache:
                return self._webhook_cache
            raise

    def list_incidents(self, *, limit: int = 30) -> dict[str, object]:
        return self.store.list_incidents(limit=limit)

    def _send_persisted(
        self,
        findings: list[AlertFinding],
        settings: AlertSettings,
        now: datetime,
        webhooks: list[dict[str, object]],
    ) -> list[str]:
        errors: list[str] = []
        active = {finding.fingerprint for finding in findings}
        for finding in findings:
            incident, should_notify = self.store.observe(
                finding.as_dict(),
                now=now,
                cooldown_secs=settings.cooldown_secs,
                repeat_secs=settings.repeat_secs,
            )
            if not should_notify:
                continue
            delivered = False
            for webhook in webhooks:
                try:
                    notifier = self.notifier_factory(replace(settings, webhook_url=str(webhook["url"])))
                    notifier.send_incident(incident)
                    delivered = True
                except Exception as exc:
                    errors.append(f"{webhook['name']}: {str(exc)[:250]}")
            if not delivered:
                self.store.defer_notification(finding.fingerprint)

        recoveries = self.store.resolve_missing(active, now=now)
        if settings.recovery_notifications:
            for incident in recoveries:
                for webhook in webhooks:
                    try:
                        notifier = self.notifier_factory(replace(settings, webhook_url=str(webhook["url"])))
                        notifier.send_recovery(incident)
                    except Exception as exc:
                        errors.append(f"{webhook['name']}: {str(exc)[:250]}")
        return errors

    def _send_fallback(
        self,
        findings: list[AlertFinding],
        settings: AlertSettings,
        now: datetime,
        webhooks: list[dict[str, object]],
    ) -> list[str]:
        now_ts = now.timestamp()
        errors: list[str] = []
        active = {finding.fingerprint for finding in findings}
        with self._fallback_lock:
            for finding in findings:
                incident = self._fallback_open.get(finding.fingerprint)
                if incident is None:
                    resolved_at = float(self._fallback_resolved_at.get(finding.fingerprint) or 0)
                    next_notification_at = max(now_ts, resolved_at + settings.cooldown_secs)
                    incident = {
                        **finding.as_dict(),
                        "incident_id": f"fallback:{finding.fingerprint}",
                        "first_seen_at": now.isoformat(timespec="seconds"),
                        "last_seen_at": now.isoformat(timespec="seconds"),
                        "occurrence_count": 0,
                        "last_notified_ts": 0.0,
                        "next_notification_ts": next_notification_at,
                    }
                    self._fallback_open[finding.fingerprint] = incident
                incident["summary"] = finding.summary
                incident["last_seen_at"] = now.isoformat(timespec="seconds")
                incident["occurrence_count"] = int(incident.get("occurrence_count") or 0) + 1
                if now_ts < float(incident.get("next_notification_ts") or 0):
                    continue
                delivered = False
                for webhook in webhooks:
                    try:
                        notifier = self.notifier_factory(replace(settings, webhook_url=str(webhook["url"])))
                        notifier.send_incident(incident)
                        delivered = True
                    except Exception as exc:
                        errors.append(f"{webhook['name']}: {str(exc)[:250]}")
                if delivered:
                    incident["last_notified_ts"] = now_ts
                    incident["next_notification_ts"] = now_ts + settings.repeat_secs
                else:
                    incident["next_notification_ts"] = now_ts + 30

            for fingerprint in list(self._fallback_open):
                if fingerprint in active:
                    continue
                incident = self._fallback_open.pop(fingerprint)
                self._fallback_resolved_at[fingerprint] = now_ts
                if settings.recovery_notifications and incident.get("last_notified_ts"):
                    incident["resolved_at"] = now.isoformat(timespec="seconds")
                    for webhook in webhooks:
                        try:
                            notifier = self.notifier_factory(replace(settings, webhook_url=str(webhook["url"])))
                            notifier.send_recovery(incident)
                        except Exception as exc:
                            errors.append(f"{webhook['name']}: {str(exc)[:250]}")
        return errors

    def run_once(self, *, snapshot_loader: Callable[[], dict[str, Any]] | None = None) -> dict[str, object]:
        started = time.perf_counter()
        settings = self.current_settings()
        if not settings.enabled:
            duration_ms = round((time.perf_counter() - started) * 1000)
            try:
                self.store.record_check(
                    status="paused",
                    duration_ms=duration_ms,
                    worker_id=self.worker_id,
                    defaults=self.settings_factory(),
                )
            except Exception:
                pass
            return {"status": "paused", "findings": 0, "duration_ms": duration_ms}

        notification_errors: list[str] = []
        try:
            snapshot = (snapshot_loader or self.snapshot_loader)()
            findings = evaluate_health_snapshot(snapshot, settings)
            now = datetime.now()
            webhooks = self._active_webhooks(settings)
            try:
                notification_errors = self._send_persisted(findings, settings, now, webhooks)
            except Exception as exc:
                logger.info({"event": "alert_store_fallback", "error": str(exc)[:300]})
                notification_errors = self._send_fallback(findings, settings, now, webhooks)
            status = "unhealthy" if findings else "healthy"
            duration_ms = round((time.perf_counter() - started) * 1000)
            check_error = "; ".join(notification_errors)[:1000]
            try:
                self.store.record_check(
                    status=status,
                    duration_ms=duration_ms,
                    error=check_error,
                    worker_id=self.worker_id,
                    defaults=self.settings_factory(),
                )
            except Exception as exc:
                logger.info({"event": "alert_check_state_failed", "error": str(exc)[:300]})
            return {
                "status": status,
                "findings": len(findings),
                "notification_errors": len(notification_errors),
                "duration_ms": duration_ms,
                "checked_at": snapshot.get("checked_at"),
            }
        except Exception as exc:
            duration_ms = round((time.perf_counter() - started) * 1000)
            error = str(exc)[:1000]
            try:
                self.store.record_check(
                    status="error",
                    duration_ms=duration_ms,
                    error=error,
                    worker_id=self.worker_id,
                    defaults=self.settings_factory(),
                )
            except Exception:
                pass
            logger.info({"event": "alert_check_failed", "error": error})
            return {"status": "error", "findings": 0, "duration_ms": duration_ms, "error": error}

    def run_forever(self, stop_event: threading.Event) -> None:
        try:
            startup_grace = self.current_settings().startup_grace_secs
        except Exception:
            startup_grace = 10
        if startup_grace and stop_event.wait(startup_grace):
            return
        while not stop_event.is_set():
            result = self.run_once()
            logger.info({"event": "alert_check_completed", **result})
            try:
                interval = self.current_settings().check_interval_secs
            except Exception:
                interval = 30
            if stop_event.wait(max(10, int(interval))):
                break


alert_service = AlertService()
