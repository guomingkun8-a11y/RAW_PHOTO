from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from api.image_inputs import collect_http_image_urls, parse_image_edit_request, read_image_sources
from api.support import require_identity, resolve_image_base_url
from services import openai_relay_service
from services.content_filter import check_request
from services.log_service import LoggedCall
from services.protocol import (
    openai_v1_image_edit,
    openai_v1_image_generations,
    openai_v1_models,
)


class ImageGenerationRequest(BaseModel):
    prompt: str = Field(..., min_length=1)
    model: str = "gpt-image-2"
    n: int = Field(default=1, ge=1, le=4)
    size: str | None = None
    quality: str = "auto"
    response_format: str = "b64_json"
    history_disabled: bool = True
    stream: bool | None = None


async def filter_or_log(call: LoggedCall, text: str) -> None:
    try:
        await run_in_threadpool(check_request, text)
    except HTTPException as exc:
        call.log("call failed", status="failed", error=str(exc.detail))
        raise


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/v1/models")
    async def list_models(authorization: str | None = Header(default=None)):
        require_identity(authorization)
        try:
            handler = openai_relay_service.list_models if openai_relay_service.is_enabled() else openai_v1_models.list_models
            return await run_in_threadpool(handler)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=502, detail={"error": str(exc)}) from exc

    @router.post("/v1/images/generations")
    async def generate_images(
        body: ImageGenerationRequest,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        payload = body.model_dump(mode="python")
        payload["base_url"] = resolve_image_base_url(request)
        call = LoggedCall(identity, "/v1/images/generations", body.model, "image generation", request_text=body.prompt)
        await filter_or_log(call, body.prompt)
        handler = openai_relay_service.image_generations if openai_relay_service.is_enabled() else openai_v1_image_generations.handle
        return await call.run(handler, payload)

    @router.post("/v1/images/edits")
    async def edit_images(
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        payload, image_sources, mask_sources = await parse_image_edit_request(request)
        prompt = str(payload["prompt"])
        model = str(payload["model"])
        call = LoggedCall(identity, "/v1/images/edits", model, "image edit", request_text=prompt)
        await filter_or_log(call, prompt)
        payload["image_urls"] = collect_http_image_urls(image_sources)
        payload["images"] = await read_image_sources(image_sources)
        if mask_sources:
            payload["mask"] = await read_image_sources(mask_sources)
        payload["base_url"] = resolve_image_base_url(request)
        handler = openai_relay_service.image_edits if openai_relay_service.is_enabled() else openai_v1_image_edit.handle
        return await call.run(handler, payload)

    return router
