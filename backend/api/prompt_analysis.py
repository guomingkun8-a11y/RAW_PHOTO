from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from api.ai import filter_or_log
from api.support import require_identity
from services.log_service import LoggedCall
from services.prompt_analysis_service import analyze_image_prompt


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


def create_router() -> APIRouter:
    router = APIRouter()

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

    return router
