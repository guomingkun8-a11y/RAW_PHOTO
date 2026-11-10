from __future__ import annotations

import asyncio
import json
import threading
from typing import Any
from typing import Literal

from fastapi import APIRouter, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api.support import require_admin, require_identity, resolve_image_base_url
from services.dsh_agent import runtime as dsh_runtime
from services.dsh_agent.events import TurnEvents
from services.dsh_agent.store import dsh_agent_store
from services.dsh_agent.models import resolve_model
from services.dsh_agent.image_service import DshImageService, MAX_IMAGE_BYTES, normalize_options
from services.dsh_agent.image_tools import ImageTurn, image_tool_bridge
from services.dsh_agent.files import DshFileService, MAX_FILE_BYTES
from services.dsh_agent import teams
from services.dsh_agent.experts import resolve_expert
from services.dsh_agent.skills import resolve_skills
from utils.log import logger


class DshAgentMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=12000)
    model: str | None = Field(default=None, max_length=191)
    reference_ids: list[str] = Field(default_factory=list, max_length=6)
    file_ids: list[str] = Field(default_factory=list, max_length=4)
    image_options: dict[str, Any] = Field(default_factory=dict)
    text_only: bool = False
    expert_id: str | None = Field(default=None, max_length=64)
    skill_ids: list[str] | None = Field(default=None, max_length=4)
    mode: Literal["normal", "teams"] = "normal"
    team_action: Literal["approve", "discard", "resume"] | None = None
    team_revision: str = Field(default="", max_length=64)


class DshAgentStopRequest(BaseModel):
    user_message_id: str = Field(min_length=1, max_length=191)


class DshAgentFeedbackRequest(BaseModel):
    feedback: Literal["positive", "negative"] | None = None


class DshImageQuery(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=100)


class DshAgentModelRequest(BaseModel):
    model: str = Field(min_length=1, max_length=191)


class DshAgentCreateRequest(BaseModel):
    model: str | None = Field(default=None, max_length=191)


def _owner_id(identity: dict[str, object]) -> str:
    return str(identity.get("id") or identity.get("username") or "")


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False, separators=(',', ':'))}\n\n"


def _restore_team_failure(team: dict | None, messages: list[dict]) -> dict | None:
    if team and team.get("halted"):
        saved = next((message.get("team") for message in reversed(messages) if message.get("role") == "assistant"), None)
        if saved and saved.get("error") and saved.get("id") == team.get("id") and saved.get("revision") == team.get("revision"):
            return {**team, "error": saved["error"]}
    return team


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/dsh-agent/status")
    async def status(authorization: str | None = Header(default=None)):
        require_identity(authorization)
        return dsh_runtime.public_status()

    @router.post("/api/dsh-agent/conversations")
    async def create_conversation(body: DshAgentCreateRequest | None = None, authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        try:
            return await run_in_threadpool(dsh_agent_store.create_conversation, identity, model=body.model if body else None)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.get("/api/dsh-agent/conversations")
    async def list_conversations(
        limit: int = Query(default=50, ge=1, le=100),
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        return await run_in_threadpool(dsh_agent_store.list_conversations, _owner_id(identity), limit=limit)

    @router.get("/api/dsh-agent/conversations/{conversation_id}")
    async def get_conversation(
        conversation_id: str,
        limit: int = Query(default=200, ge=1, le=500),
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        result = await run_in_threadpool(
            dsh_agent_store.list_messages, conversation_id, owner_id=_owner_id(identity), limit=limit,
        )
        if result is None:
            raise HTTPException(status_code=404, detail={"error": "conversation not found"})
        runtime_session = await run_in_threadpool(dsh_agent_store.get_runtime_session, conversation_id, _owner_id(identity))
        result["team"] = await run_in_threadpool(teams.snapshot, _owner_id(identity), runtime_session[0]) if runtime_session else None
        result["team"] = _restore_team_failure(result["team"], result["items"])
        return await run_in_threadpool(DshImageService(dsh_agent_store).attach_messages, result, identity)

    @router.get("/api/dsh-agent/conversations/{conversation_id}/team")
    async def team_state(conversation_id: str, authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        runtime_session = await run_in_threadpool(dsh_agent_store.get_runtime_session, conversation_id, _owner_id(identity))
        if runtime_session is None:
            raise HTTPException(404, detail={"error": "conversation not found"})
        conversation = await run_in_threadpool(dsh_agent_store.get_conversation, conversation_id, owner_id=_owner_id(identity))
        team = await run_in_threadpool(teams.snapshot, _owner_id(identity), runtime_session[0])
        if team and team.get("halted") and conversation and conversation["status"] == "error":
            history = await run_in_threadpool(dsh_agent_store.list_messages, conversation_id, owner_id=_owner_id(identity), limit=1)
            team = _restore_team_failure(team, history["items"] if history else [])
        return {"team": team, "status": conversation["status"] if conversation else "idle"}

    @router.delete("/api/dsh-agent/conversations/{conversation_id}")
    async def delete_conversation(
        conversation_id: str,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        try:
            runtime_session = await run_in_threadpool(
                dsh_agent_store.get_runtime_session, conversation_id, _owner_id(identity),
            )
            if runtime_session is None:
                raise HTTPException(status_code=404, detail={"error": "conversation not found"})
            dsh_runtime.delete_runtime_session(runtime_session[0])
            deleted = await run_in_threadpool(
                dsh_agent_store.delete_conversation, conversation_id, _owner_id(identity),
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail={"error": str(exc)}) from exc
        if not deleted:
            raise HTTPException(status_code=404, detail={"error": "conversation not found"})
        return {"ok": True}

    @router.patch("/api/dsh-agent/conversations/{conversation_id}/model")
    async def change_model(
        conversation_id: str,
        body: DshAgentModelRequest,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        try:
            result = await run_in_threadpool(dsh_agent_store.set_model, conversation_id, _owner_id(identity), body.model)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail={"error": str(exc)}) from exc
        if result is None:
            raise HTTPException(status_code=404, detail={"error": "conversation not found"})
        return result

    @router.post("/api/dsh-agent/conversations/{conversation_id}/images")
    async def upload_image(conversation_id: str, request: Request, file: UploadFile = File(...), authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        try:
            data = await file.read(MAX_IMAGE_BYTES + 1)
            return await run_in_threadpool(DshImageService(dsh_agent_store).upload, conversation_id, identity,
                                          data, file.filename or "reference.png", resolve_image_base_url(request))
        except KeyError as exc:
            raise HTTPException(404, detail={"error": "conversation not found"}) from exc
        except ValueError as exc:
            raise HTTPException(400, detail={"error": str(exc)}) from exc
        finally:
            await file.close()

    @router.delete("/api/dsh-agent/conversations/{conversation_id}/images/{media_id}")
    async def discard_image(conversation_id: str, media_id: str, authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        await run_in_threadpool(DshImageService(dsh_agent_store).discard_reference, conversation_id, identity, media_id)
        return {"ok": True}

    @router.post("/api/dsh-agent/conversations/{conversation_id}/images/query")
    async def query_images(conversation_id: str, body: DshImageQuery, authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        def query():
            service = DshImageService(dsh_agent_store)
            rows = service.media.list(conversation_id, _owner_id(identity))
            return {"items": service.hydrate(identity, [row for row in rows if row["media_id"] in body.ids and row["kind"] == "task"])}
        return await run_in_threadpool(query)

    @router.post("/api/dsh-agent/conversations/{conversation_id}/files")
    async def upload_file(conversation_id: str, file: UploadFile = File(...), authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        try:
            data = await file.read(MAX_FILE_BYTES + 1)
            return await run_in_threadpool(DshFileService(dsh_agent_store).upload, conversation_id, _owner_id(identity), data, file.filename or "file.txt")
        except KeyError as exc:
            raise HTTPException(404, detail={"error": "conversation not found"}) from exc
        except ValueError as exc:
            raise HTTPException(400, detail={"error": str(exc)}) from exc
        finally:
            await file.close()

    @router.delete("/api/dsh-agent/conversations/{conversation_id}/files/{media_id}")
    async def discard_file(conversation_id: str, media_id: str, authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        service = DshFileService(dsh_agent_store)
        await run_in_threadpool(lambda: service.media.discard_reference(conversation_id, _owner_id(identity), media_id, kind="file"))
        return {"ok": True}

    @router.post("/api/dsh-agent/conversations/{conversation_id}/messages/stream")
    async def send_message(
        conversation_id: str,
        body: DshAgentMessageRequest,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        status = dsh_runtime.public_status()
        if not status["enabled"]:
            raise HTTPException(status_code=503, detail={"error": "AI 智能体尚未启用"})
        if not status["configured"]:
            raise HTTPException(status_code=503, detail={"error": "模型中转配置不完整"})
        prompt = body.content.strip()
        if not prompt:
            raise HTTPException(status_code=400, detail={"error": "message content is required"})
        owner_id = _owner_id(identity)
        try:
            expert_id = resolve_expert(body.expert_id)
            skill_ids = resolve_skills(body.skill_ids)
            if body.mode == "teams" and not status.get("teams_enabled"):
                raise HTTPException(503, detail={"error": "协作插件尚未就绪"})
            if body.team_action and body.mode != "teams":
                raise ValueError("请在协作模式中执行计划操作")
            runtime_session = await run_in_threadpool(dsh_agent_store.get_runtime_session, conversation_id, owner_id)
            current_team = await run_in_threadpool(teams.snapshot, owner_id, runtime_session[0]) if runtime_session else None
            if body.team_action:
                if not current_team or current_team["revision"] != body.team_revision:
                    raise HTTPException(409, detail={"error": "计划已变化，请重新打开对话后确认"})
                if body.team_action == "approve" and current_team["phase"] != "staged":
                    raise HTTPException(409, detail={"error": "当前计划已确认，不能重复提交"})
            if body.mode == "normal" and teams.executing(current_team):
                raise HTTPException(409, detail={"error": "该协作尚未结束，请在协作模式中继续或停止"})
            if body.model is not None:
                resolve_model(body.model)
            image_options = normalize_options(body.image_options)
            claimed = await run_in_threadpool(
                dsh_agent_store.begin_turn,
                conversation_id,
                owner_id,
                prompt,
                lease_secs=900,
                model=body.model,
                reference_ids=body.reference_ids,
                file_ids=body.file_ids,
                **({"expert_id": expert_id} if expert_id else {}),
                skill_ids=skill_ids,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        if claimed["state"] == "not_found":
            raise HTTPException(status_code=404, detail={"error": "conversation not found"})
        if claimed["state"] != "ok":
            raise HTTPException(status_code=409, detail={"error": "该对话正在生成回复"})
        selected_model = claimed["conversation"]["model"]
        cancel_event = threading.Event()

        loop = asyncio.get_running_loop()
        events: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

        def emit(event: dict[str, Any] | None) -> None:
            loop.call_soon_threadsafe(events.put_nowait, event)

        def run() -> None:
            heartbeat_stop = threading.Event()
            phase = "load_context"

            def refresh_database_lease() -> None:
                ticks = 0
                while not heartbeat_stop.wait(1):
                    try:
                        conversation = dsh_agent_store.get_conversation(conversation_id, owner_id=owner_id)
                        if conversation and conversation["status"] == "stopping":
                            cancel_event.set()
                        ticks += 1
                        if ticks % 60 == 0:
                            dsh_agent_store.heartbeat_turn(conversation_id, owner_id, lease_secs=1800)
                    except Exception as exc:
                        logger.warning({"event": "dsh_agent_heartbeat_failed", "error_type": type(exc).__name__})

            heartbeat_thread = threading.Thread(
                target=refresh_database_lease,
                name="dsh-agent-db-heartbeat",
                daemon=True,
            )
            heartbeat_thread.start()
            turn_events: TurnEvents | None = None
            result: dict[str, Any] = {}
            team_snapshot = current_team

            def update_team(team):
                nonlocal team_snapshot
                team_snapshot = team
                emit({"type": "team", "team": team})
            try:
                runtime_session = dsh_agent_store.get_runtime_session(conversation_id, owner_id)
                if runtime_session is None:
                    raise RuntimeError("conversation not found")
                dsh_session_id, has_history = runtime_session
                history = dsh_agent_store.get_completed_history(
                    conversation_id,
                    owner_id,
                    before_message_id=claimed["user_message_id"],
                )
                turn_events = TurnEvents(dsh_session_id, emit)
                turn_events.stage("queue", "正在等待智能体运行资源")
                phase = "execute_agent"
                image_turn = ImageTurn(
                    service=DshImageService(dsh_agent_store), identity=identity,
                    conversation_id=conversation_id, message_id=claimed["user_message_id"],
                    options=image_options, reference_ids=body.reference_ids,
                    base_url=resolve_image_base_url(request), on_media=lambda media: emit({"type": "media", "media": media}),
                    allow_generation=not body.text_only,
                    expert_id=expert_id,
                    skill_ids=skill_ids,
                )
                prompt_with_files = DshFileService(dsh_agent_store).prompt(
                    prompt, conversation_id, owner_id, claimed["user_message_id"],
                )
                with image_tool_bridge().activate(dsh_session_id, image_turn):
                    agent_prompt = image_turn.prompt(prompt_with_files)
                    if body.team_action:
                        instruction = {"approve": "用户已点击确认执行当前计划，请调用 agent_teams_approve 批准此计划并协调执行。",
                                       "discard": "用户已点击取消计划，请调用 agent_teams_delete 归档当前团队，不要重建。",
                                       "resume": "用户已点击继续执行，请调用 agent_teams_resume 恢复原团队，不要重复生成已有图片。"}[body.team_action]
                        block = {"type": "text", "text": instruction}
                        agent_prompt = [block, *agent_prompt] if isinstance(agent_prompt, list) else instruction + "\n" + agent_prompt
                    result = dsh_runtime.run_turn(
                        agent_prompt, model=selected_model, owner_id=owner_id,
                        dsh_session_id=dsh_session_id, has_history=has_history, history=history,
                        on_notification=turn_events.notification, on_activity=turn_events.stage,
                        cancel_event=cancel_event,
                        **({"expert_id": expert_id} if expert_id else {}),
                        skill_ids=skill_ids,
                        **({"team_mode": True, "team_action": body.team_action or "", "team_revision": body.team_revision,
                            "local_task_id": claimed["user_message_id"], "on_team": update_team} if body.mode == "teams" else {}),
                    )
                phase = "validate_response"
                if result["finish_reason"] not in {None, "completed", "max-tokens"} or not result["content"].strip():
                    raise RuntimeError("DSH turn did not complete with an assistant response")
                turn_events.stage("answer", "回答完成", "completed")
                turn_events.finish()
                phase = "persist_response"
                message = dsh_agent_store.complete_turn(
                    conversation_id,
                    owner_id,
                    content=(turn_events.content or result["content"]) if body.mode == "teams" else result["content"],
                    model=result["model"],
                    usage={**result["usage"], "expert_id": expert_id, "skill_ids": skill_ids, **({"context": turn_events.context} if turn_events.context else {}),
                           **({"team": team_snapshot} if body.mode == "teams" else {})},
                    activities=list(turn_events.activities.values()),
                    timings=turn_events.timings,
                )
                emit({"type": "complete", "message": message})
            except dsh_runtime.DshAgentStopped:
                if turn_events:
                    turn_events.finish(stopped=True)
                message = dsh_agent_store.complete_turn(
                    conversation_id, owner_id, content=turn_events.content if turn_events else "",
                    model=selected_model, usage={**(turn_events.usage if turn_events else {}), **result.get("usage", {}), "expert_id": expert_id, "skill_ids": skill_ids, **({"context": turn_events.context} if turn_events and turn_events.context else {}), **({"team": team_snapshot} if body.mode == "teams" else {})}, stopped=True,
                    activities=list(turn_events.activities.values()) if turn_events else [],
                    timings=turn_events.timings if turn_events else {},
                )
                emit({"type": "complete", "message": message})
            except Exception as exc:
                if turn_events:
                    turn_events.finish(failed=True)
                logger.warning({
                    "event": "dsh_agent_turn_failed",
                    "conversation_id": conversation_id,
                    "phase": getattr(exc, "phase", phase),
                    "error_type": getattr(exc, "cause_type", type(exc).__name__),
                    "rpc_code": getattr(exc, "rpc_code", None),
                    "upstream_error_code": (result.get("failure") or {}).get("code"),
                    "upstream_error_message": (result.get("failure") or {}).get("message"),
                    "team_error": str(exc) if isinstance(exc, teams.TeamExecutionError) else None,
                })
                try:
                    dsh_agent_store.fail_turn(
                        conversation_id,
                        owner_id,
                        model=selected_model,
                        error_code=type(exc).__name__,
                        usage={**(turn_events.usage if turn_events else {}), **result.get("usage", {}), "expert_id": expert_id, "skill_ids": skill_ids, **({"context": turn_events.context} if turn_events and turn_events.context else {}), **({"team": team_snapshot} if body.mode == "teams" else {})},
                        **({"message": str(exc)} if isinstance(exc, teams.TeamExecutionError) else {}),
                    )
                except Exception as store_exc:
                    logger.error({"event": "dsh_agent_persist_failure", "error_type": type(store_exc).__name__})
                message = str(exc)[:240] if isinstance(exc, (dsh_runtime.DshAgentCapacityError, teams.TeamExecutionError)) else "智能体暂时无法完成回复，请稍后重试。"
                emit({"type": "error", "message": message})
            finally:
                heartbeat_stop.set()
                heartbeat_thread.join(timeout=1)
                emit(None)

        threading.Thread(target=run, name="dsh-agent-turn", daemon=True).start()

        async def stream():
            yield _sse({"type": "start", "conversation_id": conversation_id, "model": selected_model, "user_message_id": claimed["user_message_id"]})
            yield _sse({
                "type": "activity",
                "activity": {"id": "queue", "kind": "queue", "title": "正在等待智能体运行资源", "status": "running"},
            })
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(events.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                if event is None:
                    break
                yield _sse(event)

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @router.post("/api/dsh-agent/conversations/{conversation_id}/stop")
    async def stop_run(conversation_id: str, body: DshAgentStopRequest, authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        result = await run_in_threadpool(dsh_agent_store.request_stop, conversation_id, _owner_id(identity), body.user_message_id)
        if result is None:
            raise HTTPException(404, detail={"error": "conversation not found"})
        return {"accepted": result}

    @router.patch("/api/dsh-agent/conversations/{conversation_id}/messages/{message_id}/feedback")
    async def feedback(conversation_id: str, message_id: str, body: DshAgentFeedbackRequest, authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        result = await run_in_threadpool(dsh_agent_store.update_message, conversation_id, _owner_id(identity), message_id, feedback=body.feedback)
        if result is None:
            raise HTTPException(404, detail={"error": "message not found"})
        return result

    @router.delete("/api/dsh-agent/conversations/{conversation_id}/messages/{message_id}/draft")
    async def discard_response(conversation_id: str, message_id: str, authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        try:
            result = await run_in_threadpool(dsh_agent_store.update_message, conversation_id, _owner_id(identity), message_id, discard=True)
        except ValueError as exc:
            raise HTTPException(409, detail={"error": str(exc)}) from exc
        if result is None:
            raise HTTPException(404, detail={"error": "message not found"})
        return {"ok": True}

    @router.get("/api/dsh-agent/admin/usage")
    async def admin_usage(
        limit: int = Query(default=100, ge=1, le=500),
        authorization: str | None = Header(default=None),
    ):
        require_admin(authorization)
        return await run_in_threadpool(dsh_agent_store.admin_usage, limit=limit)

    @router.get("/api/dsh-agent/admin/conversations/{conversation_id}")
    async def admin_conversation(
        conversation_id: str,
        limit: int = Query(default=200, ge=1, le=500),
        authorization: str | None = Header(default=None),
    ):
        require_admin(authorization)
        result = await run_in_threadpool(dsh_agent_store.list_messages, conversation_id, limit=limit)
        if result is None:
            raise HTTPException(status_code=404, detail={"error": "conversation not found"})
        owner = result["conversation"]["owner_id"]
        return await run_in_threadpool(DshImageService(dsh_agent_store).attach_messages, result, {"id": owner})

    return router
