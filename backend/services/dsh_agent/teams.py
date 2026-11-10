from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import threading
import time
from types import SimpleNamespace
from typing import Any

from services.platform.config import DATA_DIR
from services.dsh_agent.sessions import user_home, validate_session_id


PROFILE = Path(__file__).parent / "plugins" / "profiles" / "agent-teams"
PACKAGE = "@nanmicoder/dsh-agent-teams"
TERMINAL = {"completed", "failed", "cancelled"}
_install_lock = threading.Lock()
SYSTEM_PROMPT = (
    "当前为多智能体协作模式。必须使用 AgentTeams 工具创建最多两个成员的最小任务计划，"
    "通过独立成员分工与消息协作，不要假装自己已经完成成员工作。"
    "创建时必须 approval=required，计划须包含成员及完整任务依赖，随后向用户说明计划并结束本轮等待确认。"
    "只有用户点击确认执行后才允许批准。用户提出修改时修改原计划并重新等待确认。"
    "成员只可使用系统已授权的搜索和图片工具；禁止文件、终端、部署或创建额外团队。"
    "成员使用当前选择的对话模型。只展示任务、可验证的工作进度和结论，不输出内部思考链。"
    "任务完成后保留团队供用户检查结果，不自动删除；不要循环查询或重复生成图片。"
    "搜索或网页工具报告鉴权失败、连续失败或预算不足时停止尝试，说明缺少的证据并报告失败；"
    "不能把未联网核验的知识冒充调研结果，不得悄悄改成不联网方案。"
)
MODEL_REQUEST_LIMIT = 64
MODEL_WRAP_UP_AT = 56
WRAP_UP_TOOLS = {"agent_teams_update_task", "agent_teams_send_message", "agent_teams_status"}
WRAP_UP_PROMPT = "本轮协作已进入收尾预算。禁止新增任务、搜索、读取网页或生图；请汇总已有结果，报告未完成原因，将当前任务标为完成或失败后结束。不得编造来源或重复提交任务。"


class TeamExecutionError(RuntimeError):
    """Controlled, user-visible failure; never contains raw provider credentials."""


def available() -> bool:
    return (PROFILE / "node_modules" / PACKAGE / "package.json").is_file() and (
        PROFILE / "node_modules" / "@deepseek-ai" / "dsh" / "package.json"
    ).is_file()


def workspace_for(owner_id: str, session_id: str) -> Path:
    validate_session_id(session_id)
    return user_home(DATA_DIR / "dsh-agent" / "teams-workspace", owner_id) / session_id


def prepare_profile(home: Path) -> str:
    if not available():
        raise ValueError("协作插件尚未安装，请先运行安装脚本")
    target = home / "profiles" / "agent-teams"
    with _install_lock:
        target.mkdir(parents=True, exist_ok=True)
        for name in ("package.json", "cordis.patch.yml"):
            source = PROFILE / name
            destination = target / name
            if not destination.exists() or destination.read_bytes() != source.read_bytes():
                shutil.copyfile(source, destination)
        plugin = target / "node_modules" / PACKAGE
        if not (plugin / "package.json").is_file():
            shutil.copytree(PROFILE / "node_modules" / PACKAGE, plugin, dirs_exist_ok=True)
    executable = PROFILE / "node_modules" / ".bin" / ("dsh.CMD" if os.name == "nt" else "dsh")
    return str(executable.resolve())


def snapshot(owner_id: str, session_id: str) -> dict | None:
    root = workspace_for(owner_id, session_id) / ".agent-teams"
    teams = []
    for file in root.glob("*/team.json"):
        if file.is_symlink() or not file.resolve().is_relative_to(root.resolve()):
            continue
        try:
            if file.stat().st_size > 2_000_000:
                continue
            team = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # The plugin can briefly fall back to a direct write on Windows.
            continue
        if not isinstance(team, dict) or team.get("captainSessionId") != session_id:
            continue
        members = [{key: member.get(key) for key in ("name", "role", "status", "model")}
                   for member in team.get("members", [])[:2] if isinstance(member, dict)]
        tasks = [{key: task.get(key) for key in ("id", "subject", "description", "status", "assignee", "dependencies", "output")}
                 for task in team.get("tasks", [])[:32] if isinstance(task, dict)]
        # Derived only: the plugin remains the single writer of its task graph.
        blocked = {task["id"] for task in tasks if task["status"] == "failed" or task["status"] == "cancelled" and not team.get("halted")}
        for _ in tasks:
            changed = False
            for task in tasks:
                dependencies = [key for key in task.get("dependencies") or [] if key in blocked]
                if task["status"] in {"pending", "cancelled"} and dependencies:
                    task.update(status="blocked", blocked_by=dependencies)
                    blocked.add(task["id"])
                    changed = True
            if not changed:
                break
        revision = hashlib.sha256(json.dumps(
            {"members": members, "tasks": tasks, "phase": team.get("phase"), "halted": team.get("halted")},
            sort_keys=True, ensure_ascii=True,
        ).encode()).hexdigest()
        teams.append({"id": team.get("id"), "name": team.get("name"), "phase": team.get("phase"),
                      "halted": bool(team.get("halted")), "members": members, "tasks": tasks, "revision": revision,
                      "updated_at": team.get("updatedAt", 0)})
    return max(teams, key=lambda team: str(team["updated_at"])) if teams else None


def executing(team: dict | None) -> bool:
    return bool(team and team["phase"] != "staged" and not team["halted"]
                and any(task["status"] not in TERMINAL | {"blocked"} for task in team["tasks"]))


@dataclass
class WebBudget:
    counts: dict = field(default_factory=dict)
    attempts: dict = field(default_factory=dict)
    failures: int = 0


@dataclass
class TeamControl:
    owner_id: str
    session_id: str
    action: str = ""
    revision: str = ""
    approved: bool = False
    web: dict[str, WebBudget] = field(default_factory=dict)
    failure: str = ""
    model_calls: int = 0

    def check_web(self, name: str, arguments: dict, session_id: str) -> None:
        if self.failure:
            raise ValueError(self.failure)
        if self.model_calls >= MODEL_WRAP_UP_AT:
            raise ValueError(WRAP_UP_PROMPT)
        budget = self.web.setdefault(session_id, WebBudget())
        limit = 6 if name == "web_search" else 12
        if budget.counts.get(name, 0) >= limit:
            self.failure = "本轮搜索或网页读取次数已达上限，已停止协作。请缩小调研范围后重新规划；已有结果已保留。"
            raise ValueError(self.failure)
        values = arguments.get("queries", []) if name == "web_search" else [arguments.get("url", "")]
        keys = {(name, str(value).strip().casefold() if name == "web_search" else str(value).strip().split("#", 1)[0]) for value in values}
        if any(budget.attempts.get(key, 0) >= 2 for key in keys):
            self.failure = "同一搜索或网址已重复尝试两次，已停止本轮协作以避免循环调用。请检查来源或缩小范围后重新规划。"
            raise ValueError(self.failure)
        budget.counts[name] = budget.counts.get(name, 0) + 1
        for key in keys:
            budget.attempts[key] = budget.attempts.get(key, 0) + 1

    def web_result(self, session_id: str, failed: bool, error: str, name: str = "web_search") -> None:
        budget = self.web.setdefault(session_id, WebBudget())
        if not failed:
            budget.failures = 0
            return
        budget.failures += 1
        if re.search(r"\b(?:400|401|403|422)\b|WEB_(?:INVALID|PROVIDER_(?:CREDENTIAL|(?:CONFIGURED_)?(?:MISSING|UNAVAILABLE)))|Authentication|Unauthorized|Forbidden", error, re.I) or (name == "web_search" and re.search(r"\b404\b", error)):
            self.failure = "搜索或网页服务鉴权、配置或请求异常，已停止本轮协作。请管理员检查搜索配置后重新规划；未切换为未经核验的方案。"
        elif budget.failures >= 3:
            self.failure = "搜索或网页读取连续失败3次，已停止本轮协作。请检查网络或搜索服务后重新规划；未完成的任务不会继续等待。"

    def check(self, name: str, arguments: dict, *, captain: bool) -> None:
        if self.model_calls >= MODEL_WRAP_UP_AT and (name not in WRAP_UP_TOOLS or
                name == "agent_teams_update_task" and arguments.get("status") not in {"completed", "failed"}):
            raise ValueError(WRAP_UP_PROMPT)
        team = snapshot(self.owner_id, self.session_id)
        if name == "agent_teams_create":
            if not captain or arguments.get("approval") != "required":
                raise ValueError("团队只能由主智能体创建，且必须先等待用户确认计划")
            if team and (team["phase"] == "staged" or executing(team)):
                raise ValueError("已有团队计划，请修改现有计划，不要重复创建")
            plan = arguments.get("plan") or {}
            if len(plan.get("tasks", [])) > 16:
                raise ValueError("单次协作最多规划16项任务，请缩小范围")
        elif name == "agent_teams_approve":
            if not captain or self.action != "approve" or self.approved:
                raise ValueError("请用户点击确认执行，不允许智能体自行批准")
            if not team or team["phase"] != "staged" or team["revision"] != self.revision:
                raise ValueError("计划已变化，请重新查看并确认")
            self.approved = True
        elif name == "agent_teams_resume":
            if not captain or self.action != "resume":
                raise ValueError("请用户明确点击继续执行")
        elif name == "agent_teams_delete":
            if not captain or self.action != "discard":
                raise ValueError("请用户点击取消计划，不要自动归档团队")
        elif name in {"agent_teams_add_member", "agent_teams_edit_plan"}:
            if not captain or self.approved:
                raise ValueError("请先完成当前执行，再修改计划并重新确认")
            operations = arguments.get("operations", [])
            if team and len(team["tasks"]) + sum(op.get("action") == "add_task" for op in operations) > 16:
                raise ValueError("单次协作最多规划16项任务")
        if name == "agent_teams_create_task":
            if arguments.get("resume") and (not captain or self.action != "resume"):
                raise ValueError("请用户明确点击继续执行")
            if team and len(team["tasks"]) >= 16:
                raise ValueError("本次协作已达到任务数量上限")
        if name == "generate_image" and (not team or team["phase"] == "staged" or team["halted"]):
            raise ValueError("协作计划尚未确认，不能提交生图任务")


def run_session(harness, session, prompt, *, control: TeamControl, on_notification,
                cancel_event, on_team, timeout: float = 900):
    from deepseek_harness.api import final_response, finish_reason

    events, root_events = [], []
    idle = False
    receipt = False
    last_activity = time.monotonic()
    last_team = None
    deadline = last_activity + timeout
    result = None
    statuses = {}

    def receive(notice):
        nonlocal idle, receipt, last_activity
        last_activity = time.monotonic()
        payload = notice.payload
        sid = payload.get("sessionId")
        if notice.method == "session.status":
            statuses[sid] = payload.get("status")
            if sid == session.id:
                idle = payload.get("status") == "idle"
        if notice.method == "session.event":
            event = payload.get("event") or {}
            if sid == session.id:
                if event.get("type") == "agent/inbox/spliced":
                    inserted = (event.get("data") or {}).get("inserted", [])
                    if any(item.get("id") == message_id for item in inserted):
                        receipt = True
                root_events.append(event)
            if event.get("type") in {"assistant/message", "compaction/summary"} or (sid == session.id and event.get("type") == "turn/end"):
                events.append(event)
        on_notification(notice)

    # A root turn can yield while its members continue. Keep its queue slot and
    # tools alive until both the team and the captain are quiescent.
    with harness.client.subscribe_session_notifications(session.id) as subscription:
        blocks = [{"type": "text", "text": prompt}] if isinstance(prompt, str) else prompt
        message_id = harness.client.session_prompt(session.id, blocks, notification_subscription=subscription)
        while time.monotonic() < deadline:
            if cancel_event is not None and cancel_event.is_set():
                from services.dsh_agent.runtime import DshAgentStopped
                raise DshAgentStopped()
            subscription.drain(receive)
            if control.failure:
                raise TeamExecutionError(control.failure)
            team = snapshot(control.owner_id, control.session_id)
            if team != last_team:
                last_team = team
                on_team(team)
            children_idle = all(state == "idle" for sid, state in statuses.items() if sid != session.id)
            if team and any(task["status"] in {"failed", "blocked"} for task in team["tasks"]) and idle and children_idle and not executing(team) and time.monotonic() - last_activity >= 0.75:
                raise TeamExecutionError("协作任务未完成，前置任务失败的后续任务已阻塞。请查看成员结果，修正问题后重新规划。")
            if receipt and idle and children_idle and not executing(team) and time.monotonic() - last_activity >= 0.75:
                result = SimpleNamespace(final_response=final_response(root_events),
                                         finish_reason=finish_reason(root_events), events=events)
                break
            time.sleep(0.05)
    if result is None:
        raise TimeoutError("协作执行超时，已停止当前运行；已提交的图片任务请查看原任务")
    return result
