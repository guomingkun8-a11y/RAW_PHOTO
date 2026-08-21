from __future__ import annotations

import json
import time
from collections.abc import Iterator
from typing import Any, Callable, Mapping

from services.agent import (
    AgentDecision,
    AgentPlannerContext,
    AgentRunner,
    AgentRunStatus,
    AgentRunStore,
    AgentToolContext,
    AgentToolRegistry,
    AgentToolResult,
    FunctionAgentTool,
    InMemoryAgentMemory,
)
from services.ecommerce.ecommerce_prompt_router import build_adaptive_image_prompt


PromptBuilder = Callable[[dict[str, Any]], dict[str, Any]]

ECOMMERCE_AGENT_NAME = "ecommerce-creative-agent"
PROMPT_TOOL_NAME = "ecommerce_prompt_engine"

agent_run_store = AgentRunStore(max_runs=500)
agent_memory = InMemoryAgentMemory(max_items_per_namespace=50)


def _identity_id(identity: Mapping[str, object] | None) -> str:
    if not identity:
        return "anonymous"
    return str(identity.get("id") or identity.get("username") or "anonymous").strip() or "anonymous"


def _memory_namespace(identity: Mapping[str, object] | None) -> str:
    return f"ecommerce-user:{_identity_id(identity)}"


def _prompt_tool(prompt_builder: PromptBuilder) -> FunctionAgentTool:
    def execute(context: AgentToolContext, arguments: dict[str, Any]) -> AgentToolResult:
        result = prompt_builder(arguments)
        if not isinstance(result, dict):
            raise TypeError("professional prompt engine must return an object")
        context.memory.set(
            context.memory_namespace,
            "last_professional_plan",
            {
                "productProfile": result.get("productProfile"),
                "sceneType": result.get("sceneType"),
                "sceneName": result.get("sceneName"),
                "visualDirection": result.get("visualDirection"),
            },
        )
        return AgentToolResult.success(
            result,
            metadata={"sceneType": result.get("sceneType"), "hasTypography": bool(result.get("typography"))},
        )

    return FunctionAgentTool(
        name=PROMPT_TOOL_NAME,
        description="Analyze an ecommerce product and build an executable professional image prompt.",
        handler=execute,
    )


def _professional_prompt_planner(context: AgentPlannerContext) -> AgentDecision:
    if context.last_tool_result is None:
        return AgentDecision.call_tool(
            PROMPT_TOOL_NAME,
            context.request,
            title="Analyze product and plan creative direction",
        )
    if context.last_tool_result.ok:
        return AgentDecision.finish(
            context.last_tool_result.data,
            title="Validate and complete professional prompt",
        )
    return AgentDecision.fail(
        context.last_tool_result.error or "professional prompt tool failed",
        title="Stop after prompt engine failure",
    )


def run_professional_prompt_agent(
    body: dict[str, Any],
    *,
    prompt_builder: PromptBuilder | None = None,
    identity: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    prompt_builder = prompt_builder or build_adaptive_image_prompt
    registry = AgentToolRegistry([_prompt_tool(prompt_builder)])
    runner = AgentRunner(
        registry,
        run_store=agent_run_store,
        memory=agent_memory,
        max_steps=4,
        max_tool_calls=2,
    )
    owner_id = _identity_id(identity)
    run = runner.run(
        body,
        _professional_prompt_planner,
        agent_name=ECOMMERCE_AGENT_NAME,
        memory_namespace=_memory_namespace(identity),
        metadata={"ownerId": owner_id, "workflow": "professional_prompt"},
    )
    if run.status == AgentRunStatus.FAILED:
        if run.failure_exception is not None:
            raise run.failure_exception
        raise RuntimeError(run.error or "professional prompt agent failed")
    if run.status != AgentRunStatus.COMPLETED or not isinstance(run.raw_result, dict):
        raise RuntimeError(f"professional prompt agent stopped with status: {run.status.value}")

    response = dict(run.raw_result)
    response["agentRun"] = run.to_public_dict()
    return response


def get_agent_run(run_id: str, identity: Mapping[str, object] | None = None) -> dict[str, Any] | None:
    return _get_agent_run(run_id, identity)


def cancel_agent_run(run_id: str, identity: Mapping[str, object] | None = None) -> dict[str, Any] | None:
    from services.ecommerce.cow_agent_runtime_service import cancel_cow_agent_run

    cow_canceled = cancel_cow_agent_run(run_id, identity)
    if cow_canceled is not None:
        return cow_canceled.to_public_dict(include_events=True)
    run = agent_run_store.get(run_id)
    if run is None:
        return None
    owner_id = str(run.metadata.get("ownerId") or "anonymous")
    if owner_id != _identity_id(identity):
        raise PermissionError("agent run does not belong to this user")
    canceled = agent_run_store.request_cancel(run_id)
    return canceled.to_public_dict(include_events=True) if canceled is not None else None


def _get_agent_run(run_id: str, identity: Mapping[str, object] | None = None) -> dict[str, Any] | None:
    run = _restore_image_agent_run(run_id, identity) or agent_run_store.get(run_id)
    if run is None:
        return None
    owner_id = str(run.metadata.get("ownerId") or "anonymous")
    if owner_id != _identity_id(identity):
        raise PermissionError("agent run does not belong to this user")
    return run.to_public_dict(include_events=True)


def stream_agent_run_events(
    run_id: str,
    identity: Mapping[str, object] | None = None,
    *,
    after_sequence: int = 0,
    max_seconds: float = 900.0,
) -> Iterator[str]:
    """Yield safe SSE events until an Agent run reaches a terminal state."""
    run = agent_run_store.get(run_id)
    if run is None:
        run = _restore_image_agent_run(run_id, identity)
    if run is None:
        raise KeyError("agent run not found")
    owner_id = str(run.metadata.get("ownerId") or "anonymous")
    if owner_id != _identity_id(identity):
        raise PermissionError("agent run does not belong to this user")

    cursor = max(0, int(after_sequence or 0))
    deadline = time.monotonic() + max(1.0, float(max_seconds))
    is_persistent_cow_run = str(run.metadata.get("workflow") or "") == "cowagent_professional"
    if is_persistent_cow_run:
        from services.ecommerce.agent_queue_service import agent_queue_service
        from services.ecommerce.ecommerce_agent_memory_service import ecommerce_agent_memory_service

        while time.monotonic() < deadline:
            state = ecommerce_agent_memory_service.load_run_state(run_id, owner_id=_identity_id(identity))
            if state is None:
                return
            events = ecommerce_agent_memory_service.load_run_events(run_id, after_sequence=cursor)
            for event in events:
                cursor = int(event.get("sequence") or cursor)
                yield f"id: {cursor}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            if str(state.get("status") or "") in {"waiting_for_input", "completed", "failed", "canceled"}:
                return
            notified = agent_queue_service.wait_for_run_event(
                run_id,
                cursor,
                timeout_secs=min(15.0, max(0.1, deadline - time.monotonic())),
            )
            if not notified:
                yield ": keep-alive\n\n"
        return

    while time.monotonic() < deadline:
        run = agent_run_store.wait_for_update(run_id, cursor, timeout=min(15.0, max(1.0, deadline - time.monotonic())))
        if run is None:
            return
        events = [event for event in run.events if event.sequence > cursor]
        if events:
            for event in events:
                cursor = event.sequence
                yield f"id: {event.sequence}\ndata: {json.dumps(event.to_public_dict(), ensure_ascii=False)}\n\n"
        elif run.status in {AgentRunStatus.COMPLETED, AgentRunStatus.FAILED, AgentRunStatus.WAITING, AgentRunStatus.CANCELED}:
            return
        else:
            yield ": keep-alive\n\n"
        if run.status in {AgentRunStatus.COMPLETED, AgentRunStatus.FAILED, AgentRunStatus.WAITING, AgentRunStatus.CANCELED} and cursor >= len(run.events):
            return


def _restore_image_agent_run(run_id: str, identity: Mapping[str, object] | None):
    from services.ecommerce.cow_agent_runtime_service import restore_cow_agent_run

    return restore_cow_agent_run(run_id, identity)
