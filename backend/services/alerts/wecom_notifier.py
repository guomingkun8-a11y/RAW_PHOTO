"""Enterprise WeChat webhook notification transport."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from html import escape
from typing import Any

import requests

from services.alerts.settings import AlertSettings


MAX_MARKDOWN_BYTES = 4096


class WeComNotificationError(RuntimeError):
    """Raised when Enterprise WeChat does not accept an alert message."""


def _clean(value: object, default: str = "-", limit: int = 300) -> str:
    text = " ".join(str(value or "").split()).strip()
    return (text or default)[:limit]


def _safe(value: object, default: str = "-", limit: int = 300) -> str:
    return escape(_clean(value, default, limit), quote=False)


def _truncate_markdown(content: str) -> str:
    encoded = content.encode("utf-8")
    if len(encoded) <= MAX_MARKDOWN_BYTES:
        return content
    suffix = "\n\n> 内容已截断"
    available = MAX_MARKDOWN_BYTES - len(suffix.encode("utf-8"))
    return encoded[:available].decode("utf-8", errors="ignore").rstrip() + suffix


class WeComNotifier:
    def __init__(
        self,
        settings: AlertSettings,
        *,
        http_post: Callable[..., Any] | None = None,
    ) -> None:
        settings.validate()
        self.settings = settings
        self._http_post = http_post or requests.post

    def send_markdown(self, content: str) -> bool:
        if not self.settings.enabled:
            return False
        AlertSettings.validate_webhook_url(self.settings.webhook_url)

        payload = {
            "msgtype": "markdown",
            "markdown": {"content": _truncate_markdown(str(content or "").strip())},
        }
        try:
            response = self._http_post(
                self.settings.webhook_url,
                json=payload,
                timeout=self.settings.request_timeout_secs,
            )
        except requests.RequestException as exc:
            raise WeComNotificationError(
                f"Enterprise WeChat request failed ({type(exc).__name__})"
            ) from exc

        status_code = int(getattr(response, "status_code", 0) or 0)
        if status_code < 200 or status_code >= 300:
            raise WeComNotificationError(
                f"Enterprise WeChat returned HTTP {status_code or 'unknown'}"
            )
        try:
            result = response.json()
        except (TypeError, ValueError) as exc:
            raise WeComNotificationError("Enterprise WeChat returned invalid JSON") from exc
        if not isinstance(result, Mapping):
            raise WeComNotificationError("Enterprise WeChat returned an invalid response")
        try:
            error_code = int(result.get("errcode", -1))
        except (TypeError, ValueError):
            error_code = -1
        if error_code != 0:
            raise WeComNotificationError(
                f"Enterprise WeChat rejected the message (errcode={error_code})"
            )
        return True

    def send_test(self) -> bool:
        environment = _safe(self.settings.environment, "local", 32)
        return self.send_markdown(
            f"### 【{environment}】监控通知测试\n"
            "> 状态：企业微信告警通道连接正常"
        )

    def send_incident(self, incident: Mapping[str, object]) -> bool:
        severity = _clean(incident.get("severity"), "P2", 8).upper()
        if severity not in {"P0", "P1", "P2", "P3"}:
            severity = "P2"
        environment = _safe(self.settings.environment, "local", 32)
        lines = [
            f"### 【{environment} {severity}】{_safe(incident.get('title'), '系统异常', 120)}",
            f"> 模块：{_safe(incident.get('source'), 'unknown', 80)}",
            f"> 摘要：{_safe(incident.get('summary'), '等待进一步诊断', 500)}",
            f"> 首次出现：{_safe(incident.get('first_seen_at'), '-', 64)}",
            f"> 重复次数：{_safe(incident.get('occurrence_count'), '1', 16)}",
            f"> 告警编号：{_safe(incident.get('incident_id'), '-', 80)}",
        ]
        return self.send_markdown("\n".join(lines))

    def send_recovery(self, incident: Mapping[str, object]) -> bool:
        environment = _safe(self.settings.environment, "local", 32)
        lines = [
            f"### 【{environment} 已恢复】{_safe(incident.get('title'), '系统异常', 120)}",
            f"> 模块：{_safe(incident.get('source'), 'unknown', 80)}",
            f"> 恢复时间：{_safe(incident.get('resolved_at'), '-', 64)}",
            f"> 告警编号：{_safe(incident.get('incident_id'), '-', 80)}",
        ]
        return self.send_markdown("\n".join(lines))
