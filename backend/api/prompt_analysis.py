from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api.ai import filter_or_log
from api.support import require_identity
from services.ecommerce.ecommerce_agent_service import cancel_agent_run, get_agent_run, run_professional_prompt_agent, stream_agent_run_events
from services.ecommerce.cow_agent_runtime_service import get_cow_agent_result
from services.ecommerce.ecommerce_prompt_router import build_adaptive_image_prompt
from services.ecommerce.ecommerce_scene_template_service import public_scene_templates
from services.platform.log_service import LoggedCall
from services.ecommerce.prompt_analysis_service import analyze_image_prompt


class PromptAnalysisImage(BaseModel):
    name: str = Field(default="", max_length=120)
    data_url: str = Field(default="", alias="dataUrl")

    class Config:
        populate_by_name = True


class PromptAnalysisProduct(BaseModel):
    name: str = ""
    sku: str = ""
    brand: str = ""
    category: str = ""
    selling_points: str = Field(default="", alias="sellingPoints")
    notes: str = ""

    class Config:
        populate_by_name = True


class PromptAnalysisRequest(BaseModel):
    action: str = "optimize"
    mode: str = "single"
    prompt: str = ""
    model: str = ""
    product: PromptAnalysisProduct | None = None
    images: list[PromptAnalysisImage] = Field(default_factory=list, min_length=1, max_length=4)


class ProfessionalPromptRequest(BaseModel):
    prompt: str = Field(default="", max_length=8000)
    model: str = Field(default="", max_length=160)
    scene_type: str = Field(default="auto", alias="sceneType", max_length=100)
    platform: str = Field(default="general", max_length=80)
    preserve_subject: bool = Field(default=True, alias="preserveSubject")
    product: PromptAnalysisProduct | None = None
    images: list[PromptAnalysisImage] = Field(default_factory=list, max_length=4)

    class Config:
        populate_by_name = True


# Keep the old module symbol for integrations that patch or import it. The
# endpoint now routes the request by intent before selecting the prompt engine.
def build_professional_image_prompt(body: dict) -> dict:
    return build_adaptive_image_prompt(body)


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/image-prompt/scenes")
    async def list_professional_scenes(authorization: str | None = Header(default=None)):
        require_identity(authorization)
        return {"items": public_scene_templates()}

    @router.get("/api/agent/runs/{run_id}")
    async def agent_run_status(
        run_id: str,
        include_result: bool = Query(default=False, alias="includeResult"),
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        try:
            run = get_agent_run(run_id, identity)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail={"error": str(exc)}) from exc
        if run is None:
            raise HTTPException(status_code=404, detail={"error": "agent run not found"})
        if include_result:
            try:
                result = get_cow_agent_result(run_id, identity)
            except PermissionError as exc:
                raise HTTPException(status_code=403, detail={"error": str(exc)}) from exc
            if result is not None:
                run["result"] = result
        return {"agentRun": run}

    @router.get("/api/agent/runs/{run_id}/events")
    async def agent_run_events(
        run_id: str,
        after: int = Query(default=0, ge=0),
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        try:
            existing = get_agent_run(run_id, identity)
            if existing is None:
                raise KeyError("agent run not found")
            events = stream_agent_run_events(run_id, identity, after_sequence=after)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail={"error": str(exc)}) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail={"error": str(exc)}) from exc
        return StreamingResponse(
            events,
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.post("/api/agent/runs/{run_id}/cancel")
    async def cancel_agent_run_endpoint(
        run_id: str,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        try:
            run = cancel_agent_run(run_id, identity)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail={"error": str(exc)}) from exc
        if run is None:
            raise HTTPException(status_code=404, detail={"error": "agent run not found"})
        return {"agentRun": run}

    @router.post("/api/image-prompt/analyze")
    async def analyze_prompt(body: PromptAnalysisRequest, authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        call = LoggedCall(
            identity,
            "/api/image-prompt/analyze",
            body.model or "prompt-analysis",
            "image prompt analysis",
            request_text=body.prompt,
        )
        if body.prompt:
            await filter_or_log(call, body.prompt)
        try:
            payload = body.model_dump(mode="python", by_alias=False)
            return await call.run(analyze_image_prompt, payload)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=502, detail={"error": str(exc)}) from exc

    @router.post("/api/image-prompt/professional")
    async def professional_prompt(body: ProfessionalPromptRequest, authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        if not body.prompt.strip() and not body.images:
            raise HTTPException(status_code=400, detail={"error": "prompt or reference images are required"})
        call = LoggedCall(
            identity,
            "/api/image-prompt/professional",
            body.model or "professional-prompt",
            "professional ecommerce prompt",
            request_text=body.prompt,
        )
        if body.prompt:
            await filter_or_log(call, body.prompt)
        try:
            payload = body.model_dump(mode="python", by_alias=False)
            handler = lambda value: run_professional_prompt_agent(
                value,
                prompt_builder=build_professional_image_prompt,
                identity=identity,
            )
            return await call.run(handler, payload)
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail={"error": str(exc)}) from exc

    return router
