from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import re
from threading import Condition, RLock, Thread
from time import perf_counter
from typing import Any, Callable, Mapping, Protocol
from uuid import uuid4

from services.agent.memory import AgentMemory, InMemoryAgentMemory


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


_SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "access_token",
    "refresh_token",
    "password",
    "secret",
    "data_url",
    "dataurl",
}

_BEARER_PATTERN = re.compile(r"(?i)(bearer\s+)[^\s,;]+")
_KEY_VALUE_PATTERN = re.compile(
    r"(?i)((?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret)\s*[:=]\s*)[^\s,;]+"
)


def sanitize_error_message(value: object) -> str:
    message = str(value or "")
    message = _BEARER_PATTERN.sub(r"\1[redacted]", message)
    return _KEY_VALUE_PATTERN.sub(r"\1[redacted]", message)[:1000]


def sanitize_public_data(value: Any, *, depth: int = 0) -> Any:
    """Remove credentials and large image payloads from events and run state."""
    if depth >= 10:
        return "[depth limit]"
    if isinstance(value, Mapping):
        cleaned: dict[str, Any] = {}
        for raw_key, item in value.items():
            key = str(raw_key)
            normalized = key.lower().replace("-", "_")
            if normalized in {"data_url", "dataurl"}:
                cleaned[key] = "[image data omitted]"
            elif (
                normalized in _SENSITIVE_KEYS
                or normalized.endswith(("_api_key", "_secret", "_password", "_token"))
            ):
                cleaned[key] = "[redacted]"
            else:
                cleaned[key] = sanitize_public_data(item, depth=depth + 1)
        return cleaned
    if isinstance(value, (list, tuple, set)):
        return [sanitize_public_data(item, depth=depth + 1) for item in value]
    if isinstance(value, bytes):
        return f"[binary data: {len(value)} bytes]"
    if isinstance(value, str):
        if value.startswith("data:image/") and ";base64," in value[:100]:
            return "[image data omitted]"
        if len(value) > 4000:
            return f"{value[:4000]}...[truncated {len(value) - 4000} chars]"
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


class AgentRunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_FOR_IMAGES = "waiting_for_images"
    WAITING = "waiting_for_input"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


class AgentStepStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    WAITING = "waiting_for_input"
    CANCELED = "canceled"


class AgentAction(str, Enum):
    CALL_TOOL = "call_tool"
    FINISH = "finish"
    ASK_USER = "ask_user"
    FAIL = "fail"


@dataclass(frozen=True)
class AgentDecision:
    action: AgentAction
    tool_name: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    result: Any = None
    message: str = ""
    title: str = ""

    @classmethod
    def call_tool(
        cls,
        tool_name: str,
        arguments: Mapping[str, Any] | None = None,
        *,
        title: str = "",
        message: str = "",
    ) -> AgentDecision:
        return cls(
            action=AgentAction.CALL_TOOL,
            tool_name=str(tool_name).strip(),
            arguments=dict(arguments or {}),
            title=title,
            message=message,
        )

    @classmethod
    def finish(cls, result: Any, *, message: str = "", title: str = "") -> AgentDecision:
        return cls(action=AgentAction.FINISH, result=result, message=message, title=title)

    @classmethod
    def ask_user(cls, message: str, *, title: str = "", result: Any = None) -> AgentDecision:
        return cls(action=AgentAction.ASK_USER, message=message, title=title, result=result)

    @classmethod
    def fail(cls, message: str, *, title: str = "") -> AgentDecision:
        return cls(action=AgentAction.FAIL, message=message, title=title)


@dataclass
class AgentToolResult:
    ok: bool
    data: Any = None
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    exception: Exception | None = field(default=None, repr=False)

    @classmethod
    def success(cls, data: Any, *, metadata: Mapping[str, Any] | None = None) -> AgentToolResult:
        return cls(ok=True, data=data, metadata=dict(metadata or {}))

    @classmethod
    def failure(cls, error: str, *, exception: Exception | None = None) -> AgentToolResult:
        return cls(ok=False, error=sanitize_error_message(error), exception=exception)


@dataclass(frozen=True)
class AgentToolContext:
    run_id: str
    step_id: str
    memory_namespace: str
    memory: AgentMemory
    metadata: dict[str, Any]
    emit: Callable[[str, Mapping[str, Any] | None], None] = field(default=lambda _event_type, _payload=None: None)
    is_cancel_requested: Callable[[], bool] = field(default=lambda: False)


class AgentTool(Protocol):
    name: str
    description: str

    def run(self, context: AgentToolContext, arguments: dict[str, Any]) -> AgentToolResult | Any:
        ...


@dataclass
class FunctionAgentTool:
    name: str
    description: str
    handler: Callable[[AgentToolContext, dict[str, Any]], AgentToolResult | Any]

    def run(self, context: AgentToolContext, arguments: dict[str, Any]) -> AgentToolResult | Any:
        return self.handler(context, arguments)


class AgentToolRegistry:
    def __init__(self, tools: list[AgentTool] | None = None) -> None:
        self._tools: dict[str, AgentTool] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: AgentTool) -> None:
        name = str(tool.name).strip()
        if not name:
            raise ValueError("agent tool name is required")
        if name in self._tools:
            raise ValueError(f"agent tool already registered: {name}")
        self._tools[name] = tool

    def get(self, name: str) -> AgentTool | None:
        return self._tools.get(str(name).strip())

    def names(self) -> list[str]:
        return sorted(self._tools)


@dataclass
class AgentEvent:
    sequence: int
    event_type: str
    timestamp: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "type": self.event_type,
            "timestamp": self.timestamp,
            "payload": sanitize_public_data(self.payload),
        }


@dataclass
class AgentStep:
    step_id: str
    index: int
    action: AgentAction
    status: AgentStepStatus
    title: str
    tool_name: str = ""
    started_at: str = field(default_factory=_utc_now)
    finished_at: str | None = None
    duration_ms: int | None = None
    error: str = ""

    def to_public_dict(self) -> dict[str, Any]:
        response: dict[str, Any] = {
            "stepId": self.step_id,
            "index": self.index,
            "action": self.action.value,
            "status": self.status.value,
            "title": self.title,
            "startedAt": self.started_at,
        }
        if self.tool_name:
            response["toolName"] = self.tool_name
        if self.finished_at:
            response["finishedAt"] = self.finished_at
        if self.duration_ms is not None:
            response["durationMs"] = self.duration_ms
        if self.error:
            response["error"] = self.error
        return response


@dataclass
class AgentRun:
    run_id: str
    agent_name: str
    status: AgentRunStatus
    request: dict[str, Any]
    metadata: dict[str, Any]
    max_steps: int
    started_at: str = field(default_factory=_utc_now)
    finished_at: str | None = None
    duration_ms: int | None = None
    steps: list[AgentStep] = field(default_factory=list)
    events: list[AgentEvent] = field(default_factory=list)
    tool_calls: int = 0
    result: Any = None
    error: str = ""
    waiting_message: str = ""
    raw_result: Any = field(default=None, repr=False)
    failure_exception: Exception | None = field(default=None, repr=False)
    cancel_requested: bool = field(default=False, repr=False)
    tool_results: list[AgentToolResult] = field(default_factory=list, repr=False)

    def to_public_dict(self, *, include_events: bool = False) -> dict[str, Any]:
        response: dict[str, Any] = {
            "runId": self.run_id,
            "agent": self.agent_name,
            "status": self.status.value,
            "startedAt": self.started_at,
            "maxSteps": self.max_steps,
            "stepCount": len(self.steps),
            "toolCalls": self.tool_calls,
            "steps": [step.to_public_dict() for step in self.steps],
        }
        if self.finished_at:
            response["finishedAt"] = self.finished_at
        if self.duration_ms is not None:
            response["durationMs"] = self.duration_ms
        if self.error:
            response["error"] = self.error
        if self.waiting_message:
            response["waitingForInput"] = self.waiting_message
        if include_events:
            response["events"] = [event.to_public_dict() for event in self.events]
        return response


class AgentRunStore:
    def __init__(self, *, max_runs: int = 500) -> None:
        self.max_runs = max(1, int(max_runs))
        self._runs: OrderedDict[str, AgentRun] = OrderedDict()
        self._lock = RLock()
        self._condition = Condition(self._lock)

    def save(self, run: AgentRun) -> None:
        with self._lock:
            self._runs[run.run_id] = run
            self._runs.move_to_end(run.run_id)
            while len(self._runs) > self.max_runs:
                self._runs.popitem(last=False)
            self._condition.notify_all()

    def get(self, run_id: str) -> AgentRun | None:
        with self._lock:
            return self._runs.get(str(run_id).strip())

    def append_event(
        self,
        run: AgentRun,
        event_type: str,
        payload: Mapping[str, Any] | None = None,
    ) -> AgentEvent:
        with self._lock:
            event = AgentEvent(
                sequence=len(run.events) + 1,
                event_type=event_type,
                timestamp=_utc_now(),
                payload=sanitize_public_data(dict(payload or {})),
            )
            run.events.append(event)
            self._runs[run.run_id] = run
            self._runs.move_to_end(run.run_id)
            while len(self._runs) > self.max_runs:
                self._runs.popitem(last=False)
            self._condition.notify_all()
            return event

    def is_cancel_requested(self, run_id: str) -> bool:
        with self._lock:
            run = self._runs.get(str(run_id).strip())
            return bool(run and run.cancel_requested)

    def request_cancel(self, run_id: str) -> AgentRun | None:
        """Cancel a run and publish a terminal event for connected clients."""
        with self._lock:
            run = self._runs.get(str(run_id).strip())
            if run is None:
                return None
            if run.status in {
                AgentRunStatus.COMPLETED,
                AgentRunStatus.FAILED,
                AgentRunStatus.CANCELED,
            }:
                return run
            run.cancel_requested = True
            run.status = AgentRunStatus.CANCELED
            run.waiting_message = ""
            run.finished_at = _utc_now()
            run.error = "任务已由用户中止"
            self.append_event(run, "run.canceled", {"message": run.error})
            return run

    def wait_for_update(self, run_id: str, after_sequence: int, timeout: float = 15.0) -> AgentRun | None:
        normalized_id = str(run_id).strip()
        deadline = datetime.now().timestamp() + max(0.1, float(timeout))
        with self._condition:
            while True:
                run = self._runs.get(normalized_id)
                if run is None:
                    return None
                if len(run.events) > int(after_sequence) or run.status in {
                    AgentRunStatus.COMPLETED,
                    AgentRunStatus.FAILED,
                    AgentRunStatus.WAITING,
                    AgentRunStatus.CANCELED,
                }:
                    return run
                remaining = deadline - datetime.now().timestamp()
                if remaining <= 0:
                    return run
                self._condition.wait(timeout=remaining)


@dataclass(frozen=True)
class AgentPlannerContext:
    run_id: str
    request: dict[str, Any]
    step_index: int
    steps: tuple[AgentStep, ...]
    tool_results: tuple[AgentToolResult, ...]
    last_tool_result: AgentToolResult | None
    memory_namespace: str
    memory: AgentMemory
    available_tools: tuple[str, ...]


AgentPlanner = Callable[[AgentPlannerContext], AgentDecision]
AgentEventSink = Callable[[AgentEvent], None]


class AgentRunner:
    def __init__(
        self,
        registry: AgentToolRegistry,
        *,
        run_store: AgentRunStore | None = None,
        memory: AgentMemory | None = None,
        max_steps: int = 6,
        max_tool_calls: int = 4,
        event_sink: AgentEventSink | None = None,
    ) -> None:
        self.registry = registry
        self.run_store = run_store or AgentRunStore()
        self.memory = memory or InMemoryAgentMemory()
        self.max_steps = max(1, int(max_steps))
        self.max_tool_calls = max(1, int(max_tool_calls))
        self.event_sink = event_sink

    def run(
        self,
        request: Mapping[str, Any],
        planner: AgentPlanner,
        *,
        agent_name: str,
        memory_namespace: str = "default",
        metadata: Mapping[str, Any] | None = None,
        _run: AgentRun | None = None,
    ) -> AgentRun:
        started = perf_counter()
        run = _run or AgentRun(
            run_id=uuid4().hex,
            agent_name=agent_name,
            status=AgentRunStatus.RUNNING,
            request=sanitize_public_data(dict(request)),
            metadata=sanitize_public_data(dict(metadata or {})),
            max_steps=self.max_steps,
        )
        if run.cancel_requested or run.status == AgentRunStatus.CANCELED:
            run.status = AgentRunStatus.CANCELED
            run.finished_at = run.finished_at or _utc_now()
            self.run_store.save(run)
            return run
        is_resume = bool(run.events)
        run.status = AgentRunStatus.RUNNING
        self.run_store.save(run)
        if not is_resume:
            self._emit(run, "run.started", {"agent": agent_name})
        last_tool_result: AgentToolResult | None = run.tool_results[-1] if run.tool_results else None
        tool_results: list[AgentToolResult] = list(run.tool_results)

        first_step_index = len(run.steps) + 1
        remaining_steps = max(0, self.max_steps - len(run.steps))
        for step_index in range(first_step_index, first_step_index + remaining_steps):
            if self.run_store.is_cancel_requested(run.run_id):
                self._cancel_run(run)
                break
            context = AgentPlannerContext(
                run_id=run.run_id,
                request=dict(request),
                step_index=step_index,
                steps=tuple(run.steps),
                tool_results=tuple(tool_results),
                last_tool_result=last_tool_result,
                memory_namespace=memory_namespace,
                memory=self.memory,
                available_tools=tuple(self.registry.names()),
            )
            try:
                decision = planner(context)
            except Exception as exc:
                self._fail_run(run, f"planner failed: {exc}", exception=exc)
                break
            if self.run_store.is_cancel_requested(run.run_id):
                self._cancel_run(run)
                break
            if not isinstance(decision, AgentDecision):
                self._fail_run(run, "planner must return AgentDecision")
                break

            title = decision.title or self._default_step_title(decision)
            self._emit(
                run,
                "decision.made",
                {
                    "action": decision.action.value,
                    "toolName": decision.tool_name,
                    "title": title,
                    "summary": decision.message or title,
                },
            )
            step = AgentStep(
                step_id=uuid4().hex,
                index=step_index,
                action=decision.action,
                status=AgentStepStatus.RUNNING,
                title=title,
                tool_name=decision.tool_name,
            )
            run.steps.append(step)
            self._emit(
                run,
                "step.started",
                {"stepId": step.step_id, "index": step.index, "action": step.action.value, "title": title},
            )

            if decision.action == AgentAction.CALL_TOOL:
                last_tool_result = self._execute_tool(
                    run,
                    step,
                    decision,
                    memory_namespace=memory_namespace,
                )
                tool_results.append(last_tool_result)
                run.tool_results.append(last_tool_result)
                if run.status in {AgentRunStatus.FAILED, AgentRunStatus.CANCELED}:
                    break
                continue

            if decision.action == AgentAction.FINISH:
                run.raw_result = decision.result
                run.result = sanitize_public_data(decision.result)
                self._complete_step(run, step, AgentStepStatus.COMPLETED)
                run.status = AgentRunStatus.COMPLETED
                self._emit(run, "run.completed", {"stepCount": len(run.steps), "toolCalls": run.tool_calls})
                break

            if decision.action == AgentAction.ASK_USER:
                run.waiting_message = str(decision.message).strip()
                if decision.result is not None:
                    run.raw_result = decision.result
                    run.result = sanitize_public_data(decision.result)
                self._complete_step(run, step, AgentStepStatus.WAITING)
                run.status = AgentRunStatus.WAITING
                self._emit(run, "run.waiting_for_input", {"message": run.waiting_message})
                break

            if decision.action == AgentAction.FAIL:
                error = str(decision.message or "agent planner stopped the run")
                self._complete_step(run, step, AgentStepStatus.FAILED, error=error)
                self._fail_run(
                    run,
                    error,
                    exception=last_tool_result.exception if last_tool_result is not None else None,
                )
                break

            self._complete_step(run, step, AgentStepStatus.FAILED, error="unsupported agent action")
            self._fail_run(run, "unsupported agent action")
            break
        else:
            self._fail_run(run, f"maximum agent steps exceeded ({self.max_steps})")

        run.finished_at = _utc_now() if run.status not in {AgentRunStatus.WAITING, AgentRunStatus.CANCELED} else run.finished_at
        run.duration_ms = max(0, round((perf_counter() - started) * 1000))
        self.run_store.save(run)
        return run

    def start(
        self,
        request: Mapping[str, Any],
        planner: AgentPlanner,
        *,
        agent_name: str,
        memory_namespace: str = "default",
        metadata: Mapping[str, Any] | None = None,
        run_id: str = "",
    ) -> AgentRun:
        run = AgentRun(
            run_id=str(run_id).strip() or uuid4().hex,
            agent_name=agent_name,
            status=AgentRunStatus.PENDING,
            request=sanitize_public_data(dict(request)),
            metadata=sanitize_public_data(dict(metadata or {})),
            max_steps=self.max_steps,
        )
        self.run_store.save(run)
        thread = Thread(
            target=self.run,
            args=(request, planner),
            kwargs={
                "agent_name": agent_name,
                "memory_namespace": memory_namespace,
                "metadata": metadata,
                "_run": run,
            },
            name=f"agent-run-{run.run_id[:12]}",
            daemon=True,
        )
        thread.start()
        return run

    def resume(
        self,
        run_id: str,
        request: Mapping[str, Any],
        planner: AgentPlanner,
        *,
        agent_name: str,
        memory_namespace: str = "default",
        metadata: Mapping[str, Any] | None = None,
    ) -> AgentRun:
        """Continue a waiting run on the same run object and event stream."""
        run = self.run_store.get(run_id)
        if run is None:
            raise KeyError("agent run not found")
        if run.status != AgentRunStatus.WAITING:
            raise ValueError("agent run is not waiting for input")
        run.waiting_message = ""
        run.status = AgentRunStatus.RUNNING
        run.request = sanitize_public_data(dict(request))
        run.metadata.update(sanitize_public_data(dict(metadata or {})))
        self._emit(run, "run.resumed", {"agent": agent_name})
        self.run_store.save(run)
        thread = Thread(
            target=self.run,
            args=(request, planner),
            kwargs={
                "agent_name": agent_name,
                "memory_namespace": memory_namespace,
                "metadata": metadata,
                "_run": run,
            },
            name=f"agent-resume-{run.run_id[:12]}",
            daemon=True,
        )
        thread.start()
        return run

    def _execute_tool(
        self,
        run: AgentRun,
        step: AgentStep,
        decision: AgentDecision,
        *,
        memory_namespace: str,
    ) -> AgentToolResult:
        if run.tool_calls >= self.max_tool_calls:
            error = f"maximum tool calls exceeded ({self.max_tool_calls})"
            self._complete_step(run, step, AgentStepStatus.FAILED, error=error)
            self._fail_run(run, error)
            return AgentToolResult.failure(error)

        tool = self.registry.get(decision.tool_name)
        if tool is None:
            error = f"agent tool is not allowed: {decision.tool_name}"
            self._complete_step(run, step, AgentStepStatus.FAILED, error=error)
            self._emit(run, "tool.failed", {"stepId": step.step_id, "toolName": decision.tool_name, "error": error})
            return AgentToolResult.failure(error)

        run.tool_calls += 1
        self._emit(
            run,
            "tool.started",
            {"stepId": step.step_id, "toolName": tool.name, "arguments": decision.arguments},
        )
        context = AgentToolContext(
            run_id=run.run_id,
            step_id=step.step_id,
            memory_namespace=memory_namespace,
            memory=self.memory,
            metadata=dict(run.metadata),
            emit=lambda event_type, payload=None: self._emit(run, event_type, payload),
            is_cancel_requested=lambda: self.run_store.is_cancel_requested(run.run_id),
        )
        try:
            value = tool.run(context, dict(decision.arguments))
            result = value if isinstance(value, AgentToolResult) else AgentToolResult.success(value)
        except Exception as exc:
            result = AgentToolResult.failure(str(exc), exception=exc)
        result.metadata.setdefault("toolName", tool.name)

        if run.status == AgentRunStatus.CANCELED or context.is_cancel_requested():
            step.status = AgentStepStatus.CANCELED
            step.finished_at = _utc_now()
            step.duration_ms = self._duration_between(step.started_at, step.finished_at)
            return result

        if result.ok:
            self._complete_step(run, step, AgentStepStatus.COMPLETED)
            self._emit(
                run,
                "tool.completed",
                {"stepId": step.step_id, "toolName": tool.name, "metadata": result.metadata},
            )
        else:
            error = result.error or "agent tool failed"
            self._complete_step(run, step, AgentStepStatus.FAILED, error=error)
            self._emit(
                run,
                "tool.failed",
                {"stepId": step.step_id, "toolName": tool.name, "error": error},
            )
        return result

    def _complete_step(
        self,
        run: AgentRun,
        step: AgentStep,
        status: AgentStepStatus,
        *,
        error: str = "",
    ) -> None:
        step.status = status
        step.finished_at = _utc_now()
        step.duration_ms = self._duration_between(step.started_at, step.finished_at)
        step.error = sanitize_error_message(error)
        self._emit(
            run,
            "step.completed",
            {"stepId": step.step_id, "status": status.value, "error": step.error},
        )

    def _fail_run(self, run: AgentRun, error: str, *, exception: Exception | None = None) -> None:
        if run.status == AgentRunStatus.CANCELED:
            return
        run.status = AgentRunStatus.FAILED
        run.error = sanitize_error_message(error)
        run.failure_exception = exception
        self._emit(run, "run.failed", {"error": run.error})

    def _cancel_run(self, run: AgentRun) -> None:
        if run.status == AgentRunStatus.CANCELED:
            return
        run.cancel_requested = True
        run.status = AgentRunStatus.CANCELED
        run.finished_at = run.finished_at or _utc_now()
        run.error = "任务已由用户中止"
        self._emit(run, "run.canceled", {"message": run.error})

    def _emit(self, run: AgentRun, event_type: str, payload: Mapping[str, Any] | None = None) -> None:
        event = self.run_store.append_event(run, event_type, payload)
        if self.event_sink is not None:
            try:
                self.event_sink(event)
            except Exception:
                pass

    @staticmethod
    def _default_step_title(decision: AgentDecision) -> str:
        if decision.action == AgentAction.CALL_TOOL:
            return f"Run {decision.tool_name}"
        if decision.action == AgentAction.ASK_USER:
            return "Request more information"
        if decision.action == AgentAction.FAIL:
            return "Stop agent run"
        return "Complete agent run"

    @staticmethod
    def _duration_between(started_at: str, finished_at: str) -> int:
        try:
            start = datetime.fromisoformat(started_at)
            finish = datetime.fromisoformat(finished_at)
            return max(0, round((finish - start).total_seconds() * 1000))
        except (TypeError, ValueError):
            return 0
