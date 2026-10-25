"""Threshold evaluation for operational health checks."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from typing import Any, Mapping

from services.alerts.settings import AlertSettings


def _number(value: object) -> int:
    try:
        return max(0, int(float(value or 0)))
    except (TypeError, ValueError):
        return 0


def _fingerprint(kind: str, source: str) -> str:
    value = f"{kind}:{source}".encode("utf-8")
    return hashlib.sha256(value).hexdigest()[:40]


@dataclass(frozen=True, slots=True)
class AlertFinding:
    kind: str
    source: str
    title: str
    summary: str
    severity: str = "P2"
    details: dict[str, object] = field(default_factory=dict)

    @property
    def fingerprint(self) -> str:
        return _fingerprint(self.kind, self.source)

    def as_dict(self) -> dict[str, object]:
        return {
            "fingerprint": self.fingerprint,
            "kind": self.kind,
            "source": self.source,
            "title": self.title,
            "summary": self.summary,
            "severity": self.severity,
            "details": self.details,
        }


def _infrastructure_findings(snapshot: Mapping[str, Any]) -> list[AlertFinding]:
    findings: list[AlertFinding] = []
    database = dict(snapshot.get("database") or {})
    if not database.get("available"):
        findings.append(AlertFinding(
            kind="database_unavailable",
            source="database",
            title="数据库连接异常",
            summary=str(database.get("error") or "数据库健康检查失败")[:300],
            severity="P0",
            details={"latency_ms": _number(database.get("latency_ms"))},
        ))
    redis_result = dict(snapshot.get("redis") or {})
    if not redis_result.get("available"):
        findings.append(AlertFinding(
            kind="redis_unavailable",
            source="redis",
            title="Redis 连接异常",
            summary=str(redis_result.get("error") or "Redis 健康检查失败")[:300],
            severity="P0",
            details={"latency_ms": _number(redis_result.get("latency_ms"))},
        ))
    application = snapshot.get("application")
    if isinstance(application, Mapping) and not application.get("available"):
        findings.append(AlertFinding(
            kind="application_unavailable",
            source="api",
            title="后端服务不可用",
            summary=str(application.get("error") or "后端健康检查失败")[:300],
            severity="P0",
            details={"latency_ms": _number(application.get("latency_ms"))},
        ))
    if snapshot.get("queue_error"):
        findings.append(AlertFinding(
            kind="queue_collection_failed",
            source="queues",
            title="队列状态采集失败",
            summary=str(snapshot.get("queue_error"))[:300],
            severity="P1",
        ))
    return findings


def evaluate_health_snapshot(
    snapshot: Mapping[str, Any],
    settings: AlertSettings,
) -> list[AlertFinding]:
    findings = _infrastructure_findings(snapshot)
    application = snapshot.get("application")
    application_ready = not isinstance(application, Mapping) or bool(application.get("available"))
    infrastructure_ready = (
        bool((snapshot.get("database") or {}).get("available"))
        and bool((snapshot.get("redis") or {}).get("available"))
        and application_ready
    )
    if not infrastructure_ready:
        return findings

    for raw_item in snapshot.get("queues") or []:
        item = dict(raw_item or {})
        if not item.get("enabled", True):
            continue
        queue = str(item.get("queue") or "unknown")[:191]
        label = str(item.get("label") or queue)[:80]
        waiting = _number(item.get("waiting"))
        dead_letters = _number(item.get("dead_letters"))
        stale_tasks = _number(item.get("stale_tasks"))
        active_workers = _number(item.get("active_workers"))

        if not item.get("available", True):
            findings.append(AlertFinding(
                kind="queue_unavailable",
                source=queue,
                title=f"{label}队列不可用",
                summary=str(item.get("error") or "队列连接或状态读取失败")[:300],
                severity="P1",
                details={"queue": queue},
            ))
            continue
        if settings.worker_missing_alert and item.get("worker_required") and active_workers == 0:
            findings.append(AlertFinding(
                kind="worker_missing",
                source=queue,
                title=f"{label} Worker 离线",
                summary=f"当前没有可用 Worker，等待任务 {waiting} 个",
                severity="P1",
                details={"waiting": waiting, "active_workers": active_workers},
            ))
        if waiting >= settings.queue_waiting_threshold:
            findings.append(AlertFinding(
                kind="queue_backlog",
                source=queue,
                title=f"{label}队列积压",
                summary=f"等待任务 {waiting} 个，告警阈值 {settings.queue_waiting_threshold} 个",
                severity="P2",
                details={"waiting": waiting, "threshold": settings.queue_waiting_threshold},
            ))
        if dead_letters >= settings.dead_letter_threshold:
            findings.append(AlertFinding(
                kind="dead_letters",
                source=queue,
                title=f"{label}存在死信任务",
                summary=f"死信任务 {dead_letters} 个，需人工核查",
                severity="P1",
                details={"dead_letters": dead_letters},
            ))
        if stale_tasks >= settings.stale_task_threshold:
            findings.append(AlertFinding(
                kind="stale_tasks",
                source=queue,
                title=f"{label}存在卡住任务",
                summary=f"检测到 {stale_tasks} 个长时间未更新的运行任务",
                severity="P1",
                details={"stale_tasks": stale_tasks},
            ))
    return findings
