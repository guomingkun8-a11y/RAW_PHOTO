from __future__ import annotations

from fastapi import APIRouter, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from api.support import require_identity, resolve_image_base_url
from services.business_service import business_service


class ProductPayload(BaseModel):
    name: str = Field(default="", max_length=191)
    sku: str = Field(default="", max_length=191)
    brand: str = Field(default="", max_length=191)
    category: str = Field(default="", max_length=191)
    selling_points: str = ""
    notes: str = ""
    status: str = "active"


class ProductUpdatePayload(BaseModel):
    name: str | None = Field(default=None, max_length=191)
    sku: str | None = Field(default=None, max_length=191)
    brand: str | None = Field(default=None, max_length=191)
    category: str | None = Field(default=None, max_length=191)
    selling_points: str | None = None
    notes: str | None = None
    status: str | None = None


class PromptTemplatePayload(BaseModel):
    name: str = Field(default="", max_length=191)
    category: str = Field(default="main", max_length=64)
    content: str = ""
    model: str = ""
    size: str = ""
    quality: str = "high"
    preserve_subject: bool = True
    enabled: bool = True


class PromptTemplateUpdatePayload(BaseModel):
    name: str | None = Field(default=None, max_length=191)
    category: str | None = Field(default=None, max_length=64)
    content: str | None = None
    model: str | None = None
    size: str | None = None
    quality: str | None = None
    preserve_subject: bool | None = None
    enabled: bool | None = None


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/products")
    async def list_products(
        q: str = Query(default=""),
        status: str = Query(default="active"),
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        return await run_in_threadpool(business_service.list_products, identity=identity, q=q, status=status)

    @router.post("/api/products")
    async def create_product(body: ProductPayload, authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        try:
            return await run_in_threadpool(
                business_service.create_product,
                identity=identity,
                data=body.model_dump(),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.get("/api/products/{product_id}")
    async def get_product(product_id: int, authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        item = await run_in_threadpool(business_service.get_product, identity=identity, product_id=product_id)
        if item is None:
            raise HTTPException(status_code=404, detail={"error": "product not found"})
        return item

    @router.patch("/api/products/{product_id}")
    async def update_product(product_id: int, body: ProductUpdatePayload, authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        try:
            item = await run_in_threadpool(
                business_service.update_product,
                identity=identity,
                product_id=product_id,
                data=body.model_dump(exclude_unset=True),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        if item is None:
            raise HTTPException(status_code=404, detail={"error": "product not found"})
        return item

    @router.delete("/api/products/{product_id}")
    async def archive_product(product_id: int, authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        item = await run_in_threadpool(
            business_service.update_product,
            identity=identity,
            product_id=product_id,
            data={"status": "archived"},
        )
        if item is None:
            raise HTTPException(status_code=404, detail={"error": "product not found"})
        return item

    @router.post("/api/products/{product_id}/references")
    async def upload_product_reference(
        product_id: int,
        request: Request,
        image: UploadFile = File(...),
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        payload = await image.read()
        await image.close()
        if not payload:
            raise HTTPException(status_code=400, detail={"error": "image file is empty"})
        item = await run_in_threadpool(
            business_service.upload_product_reference,
            identity=identity,
            product_id=product_id,
            payload=payload,
            file_name=image.filename or "reference.png",
            mime_type=image.content_type or "image/png",
            base_url=resolve_image_base_url(request),
        )
        if item is None:
            raise HTTPException(status_code=404, detail={"error": "product not found"})
        return item

    @router.get("/api/prompt-templates")
    async def list_prompt_templates(
        q: str = Query(default=""),
        category: str = Query(default=""),
        include_disabled: bool = Query(default=False),
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        return await run_in_threadpool(
            business_service.list_templates,
            identity=identity,
            q=q,
            category=category,
            enabled_only=not include_disabled,
        )

    @router.post("/api/prompt-templates")
    async def create_prompt_template(body: PromptTemplatePayload, authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        try:
            return await run_in_threadpool(
                business_service.create_template,
                identity=identity,
                data=body.model_dump(),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.patch("/api/prompt-templates/{template_id}")
    async def update_prompt_template(
        template_id: int,
        body: PromptTemplateUpdatePayload,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        try:
            item = await run_in_threadpool(
                business_service.update_template,
                identity=identity,
                template_id=template_id,
                data=body.model_dump(exclude_unset=True),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        if item is None:
            raise HTTPException(status_code=404, detail={"error": "template not found"})
        return item

    @router.delete("/api/prompt-templates/{template_id}")
    async def disable_prompt_template(template_id: int, authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        item = await run_in_threadpool(
            business_service.update_template,
            identity=identity,
            template_id=template_id,
            data={"enabled": False},
        )
        if item is None:
            raise HTTPException(status_code=404, detail={"error": "template not found"})
        return item

    @router.get("/api/audit-logs")
    async def list_audit_logs(limit: int = Query(default=100, ge=1, le=500), authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        return await run_in_threadpool(business_service.list_audit_logs, identity=identity, limit=limit)

    return router
