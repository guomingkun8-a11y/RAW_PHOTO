"""Environment-backed settings for scheduled monitoring and notifications."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
import os
from urllib.parse import parse_qs, urlsplit


class AlertSettingsError(ValueError):
    """Raised when alert delivery is enabled with invalid configuration."""


def _env_bool(values: Mapping[str, object], name: str, default: bool) -> bool:
    raw = str(values.get(name, "")).strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def _env_int(
    values: Mapping[str, object],
    name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        value = int(str(values.get(name, default)).strip())
    except (TypeError, ValueError):
        value = default
    return min(maximum, max(minimum, value))


@dataclass(frozen=True, slots=True)
class AlertSettings:
    enabled: bool = False
    webhook_url: str = field(default="", repr=False)
    internal_token: str = field(default="", repr=False)
    environment: str = "local"
    startup_grace_secs: int = 10
    check_interval_secs: int = 30
    cooldown_secs: int = 600
    repeat_secs: int = 1800
    request_timeout_secs: int = 5
    queue_waiting_threshold: int = 20
    dead_letter_threshold: int = 1
    stale_task_threshold: int = 1
    worker_missing_alert: bool = True
    recovery_notifications: bool = True

    @classmethod
    def from_env(cls, values: Mapping[str, object] | None = None) -> "AlertSettings":
        source = os.environ if values is None else values
        settings = cls(
            enabled=_env_bool(source, "WECOM_ALERT_ENABLED", False),
            webhook_url=str(source.get("WECOM_ALERT_WEBHOOK_URL", "")).strip(),
            internal_token=str(source.get("WECOM_ALERT_INTERNAL_TOKEN", "")).strip(),
            environment=str(source.get("WECOM_ALERT_ENVIRONMENT", "local")).strip()[:32] or "local",
            startup_grace_secs=_env_int(
                source,
                "WECOM_ALERT_STARTUP_GRACE_SECS",
                10,
                0,
                300,
            ),
            check_interval_secs=_env_int(
                source,
                "WECOM_ALERT_CHECK_INTERVAL_SECS",
                30,
                10,
                3600,
            ),
            cooldown_secs=_env_int(
                source,
                "WECOM_ALERT_COOLDOWN_SECS",
                600,
                30,
                86400,
            ),
            repeat_secs=_env_int(
                source,
                "WECOM_ALERT_REPEAT_SECS",
                1800,
                60,
                604800,
            ),
            request_timeout_secs=_env_int(
                source,
                "WECOM_ALERT_REQUEST_TIMEOUT_SECS",
                5,
                1,
                30,
            ),
            queue_waiting_threshold=_env_int(
                source,
                "WECOM_ALERT_QUEUE_WAITING_THRESHOLD",
                20,
                1,
                100000,
            ),
            dead_letter_threshold=_env_int(
                source,
                "WECOM_ALERT_DEAD_LETTER_THRESHOLD",
                1,
                1,
                100000,
            ),
            stale_task_threshold=_env_int(
                source,
                "WECOM_ALERT_STALE_TASK_THRESHOLD",
                1,
                1,
                100000,
            ),
            worker_missing_alert=_env_bool(
                source,
                "WECOM_ALERT_WORKER_MISSING_ENABLED",
                True,
            ),
            recovery_notifications=_env_bool(
                source,
                "WECOM_ALERT_RECOVERY_ENABLED",
                True,
            ),
        )
        settings.validate()
        return settings

    def with_runtime(self, values: Mapping[str, object] | None) -> "AlertSettings":
        if not values:
            return self
        settings = replace(
            self,
            enabled=_env_bool(values, "enabled", self.enabled),
            check_interval_secs=_env_int(
                values, "check_interval_secs", self.check_interval_secs, 10, 3600
            ),
            cooldown_secs=_env_int(
                values, "cooldown_secs", self.cooldown_secs, 30, 86400
            ),
            repeat_secs=_env_int(
                values, "repeat_secs", self.repeat_secs, 60, 604800
            ),
            queue_waiting_threshold=_env_int(
                values,
                "queue_waiting_threshold",
                self.queue_waiting_threshold,
                1,
                100000,
            ),
            dead_letter_threshold=_env_int(
                values,
                "dead_letter_threshold",
                self.dead_letter_threshold,
                1,
                100000,
            ),
            stale_task_threshold=_env_int(
                values,
                "stale_task_threshold",
                self.stale_task_threshold,
                1,
                100000,
            ),
            worker_missing_alert=_env_bool(
                values, "worker_missing_alert", self.worker_missing_alert
            ),
            recovery_notifications=_env_bool(
                values, "recovery_notifications", self.recovery_notifications
            ),
        )
        settings.validate()
        return settings

    def runtime_values(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "check_interval_secs": self.check_interval_secs,
            "cooldown_secs": self.cooldown_secs,
            "repeat_secs": self.repeat_secs,
            "queue_waiting_threshold": self.queue_waiting_threshold,
            "dead_letter_threshold": self.dead_letter_threshold,
            "stale_task_threshold": self.stale_task_threshold,
            "worker_missing_alert": self.worker_missing_alert,
            "recovery_notifications": self.recovery_notifications,
        }

    def public_values(self) -> dict[str, object]:
        return {
            **self.runtime_values(),
            "environment": self.environment,
            "webhook_configured": bool(self.webhook_url),
        }

    def validate(self) -> None:
        if self.webhook_url:
            self.validate_webhook_url(self.webhook_url)

    @staticmethod
    def validate_webhook_url(value: str) -> str:
        webhook_url = str(value or "").strip()
        if not webhook_url:
            raise AlertSettingsError("请输入企业微信机器人 Webhook 地址")
        try:
            parsed = urlsplit(webhook_url)
            port = parsed.port
        except ValueError as exc:
            raise AlertSettingsError("Webhook 地址格式不正确") from exc
        query = parse_qs(parsed.query)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "qyapi.weixin.qq.com"
            or parsed.path.rstrip("/") != "/cgi-bin/webhook/send"
            or not query.get("key")
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or port not in (None, 443)
        ):
            raise AlertSettingsError("WECOM_ALERT_WEBHOOK_URL must be an Enterprise WeChat robot webhook")
        return webhook_url
