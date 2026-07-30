from __future__ import annotations

import time

from fastapi import APIRouter, File, Header, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool

from api.support import require_identity
from services import reference_image_uploader


MAX_REFERENCE_FILES = 100
MAX_REFERENCE_FILE_BYTES = 50 * 1024 * 1024
MAX_REFERENCE_BATCH_BYTES = 300 * 1024 * 1024


def create_router() -> APIRouter:
    router = APIRouter()

    @router.post("/api/image-references/preupload")
    async def preupload_reference_images(
        images: list[UploadFile] = File(...),
        authorization: str | None = Header(default=None),
    ):
        require_identity(authorization)
        if not images or len(images) > MAX_REFERENCE_FILES:
            raise HTTPException(
                status_code=400,
                detail={"error": f"reference image count must be between 1 and {MAX_REFERENCE_FILES}"},
            )

        payloads: list[tuple[bytes, str, str]] = []
        total_bytes = 0
        try:
            for index, image in enumerate(images, start=1):
                payload = await image.read()
                mime_type = str(image.content_type or "image/png").strip() or "image/png"
                filename = str(image.filename or f"reference-{index}.png").strip() or f"reference-{index}.png"
                if not payload:
                    raise HTTPException(status_code=400, detail={"error": f"{filename} is empty"})
                if len(payload) > MAX_REFERENCE_FILE_BYTES:
                    raise HTTPException(status_code=400, detail={"error": f"{filename} exceeds 50MB limit"})
                total_bytes += len(payload)
                if total_bytes > MAX_REFERENCE_BATCH_BYTES:
                    raise HTTPException(status_code=400, detail={"error": "reference image batch exceeds 300MB limit"})
                payloads.append((payload, filename, mime_type))
        finally:
            for image in images:
                await image.close()

        started = time.perf_counter()
        try:
            results = await run_in_threadpool(reference_image_uploader.upload_images_detailed, payloads)
        except reference_image_uploader.ReferenceImageUploadError as exc:
            raise HTTPException(status_code=502, detail={"error": f"reference image upload failed: {exc}"}) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail={"error": f"reference image upload failed: {exc}"}) from exc
        duration_ms = int((time.perf_counter() - started) * 1000)
        return {
            "items": [item.to_public() for item in results],
            "total": len(results),
            "uploaded": sum(1 for item in results if not item.cached),
            "cache_hits": sum(1 for item in results if item.cached),
            "duration_ms": duration_ms,
            "metrics": reference_image_uploader.metrics_snapshot(),
        }

    return router
