from __future__ import annotations

import mimetypes
import tempfile
import zipfile
from urllib.parse import quote

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from api.image_inputs import _download_image_url
from api.support import require_identity, resolve_image_base_url
from services.image_library_service import image_library_service
from services.image_storage_service import image_storage_service

ZIP_MAX_ITEM_BYTES = 50 * 1024 * 1024
ZIP_MAX_TOTAL_BYTES = 500 * 1024 * 1024
ZIP_SPOOL_MEMORY_BYTES = 8 * 1024 * 1024
ZIP_STREAM_CHUNK_BYTES = 1024 * 1024


class ImageLibraryUpdateRequest(BaseModel):
    favorite: bool | None = None
    deleted: bool | None = None


class ImageLibraryBulkRequest(BaseModel):
    ids: list[int]


class ImageLibraryZipRequest(BaseModel):
    ids: list[int]
    folder_name: str = "历史图库"


def _zip_safe_name(value: str, fallback: str) -> str:
    import re

    clean = re.sub(r'[\\/:*?"<>|]+', "-", str(value or "").strip())
    clean = re.sub(r"\s+", " ", clean).strip(" .")
    return (clean or fallback)[:120]


def _zip_content_disposition(filename: str) -> str:
    ascii_name = filename.encode("ascii", errors="ignore").decode("ascii")
    if ascii_name != filename:
        ascii_name = "GMKRAW-Image-Library.zip"
    fallback = _zip_safe_name(ascii_name, "GMKRAW-Image-Library.zip")
    encoded = quote(filename, safe="")
    return f'attachment; filename="{fallback}"; filename*=UTF-8\'\'{encoded}'


def _image_ext_from_content_type(content_type: str, fallback_name: str) -> str:
    content_type = content_type.lower()
    if "jpeg" in content_type:
        return "jpg"
    if "webp" in content_type:
        return "webp"
    if "gif" in content_type:
        return "gif"
    if "avif" in content_type:
        return "avif"
    suffix = fallback_name.rsplit(".", 1)[-1].lower() if "." in fallback_name else ""
    if suffix in {"png", "jpg", "jpeg", "webp", "gif", "avif"}:
        return "jpg" if suffix == "jpeg" else suffix
    return "png"


def _library_image_payload(item: dict[str, object]) -> tuple[bytes, str, str]:
    image_rel = str(item.get("image_rel") or "").strip()
    if image_rel:
        try:
            image_bytes = image_storage_service.get_bytes(image_rel)
        except Exception as exc:
            raise ValueError("library image storage is unavailable") from exc
        content_type = mimetypes.guess_type(image_rel)[0] or "image/png"
        return image_bytes, content_type, image_rel

    image_url = str(item.get("image_url") or "").strip()
    if image_url:
        try:
            image_bytes, filename, content_type = _download_image_url(image_url)
        except HTTPException as exc:
            raise ValueError("library image URL is unavailable") from exc
        return image_bytes, content_type, filename

    raise ValueError("library image is unavailable")


def _download_library_zip_payload(identity: dict[str, object], base_url: str, body: ImageLibraryZipRequest):
    folder_name = _zip_safe_name(body.folder_name, "历史图库")
    items = image_library_service.list_images_by_ids(
        identity=identity,
        base_url=base_url,
        image_ids=body.ids,
    )
    archive = tempfile.SpooledTemporaryFile(max_size=ZIP_SPOOL_MEMORY_BYTES, mode="w+b")
    used_names: set[str] = set()
    added = 0
    total_bytes = 0
    try:
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for index, item in enumerate(items, start=1):
                try:
                    image_bytes, content_type, source_name = _library_image_payload(item)
                except (HTTPException, ValueError):
                    continue
                if not image_bytes or len(image_bytes) > ZIP_MAX_ITEM_BYTES:
                    continue
                total_bytes += len(image_bytes)
                if total_bytes > ZIP_MAX_TOTAL_BYTES:
                    raise ValueError("download package exceeds size limit")

                prompt = str(item.get("prompt") or item.get("revised_prompt") or "").strip()
                raw_name = _zip_safe_name(prompt[:40], f"image-{item.get('id') or index}")
                stem = raw_name.rsplit(".", 1)[0] if "." in raw_name else raw_name
                ext = _image_ext_from_content_type(content_type, source_name)
                name = f"{stem}-{item.get('id') or index}.{ext}"
                counter = 2
                while name in used_names:
                    name = f"{stem}-{item.get('id') or index}-{counter}.{ext}"
                    counter += 1
                used_names.add(name)
                with zf.open(f"{folder_name}/{name}", "w") as target:
                    view = memoryview(image_bytes)
                    for offset in range(0, len(view), ZIP_STREAM_CHUNK_BYTES):
                        target.write(view[offset : offset + ZIP_STREAM_CHUNK_BYTES])
                added += 1
        if added == 0:
            raise ValueError("no downloadable images")
        archive.seek(0)
        return archive, f"{folder_name}.zip"
    except Exception:
        archive.close()
        raise


def _stream_zip_payload(payload):
    try:
        while True:
            chunk = payload.read(ZIP_STREAM_CHUNK_BYTES)
            if not chunk:
                break
            yield chunk
    finally:
        payload.close()


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/image-library")
    async def list_image_library(
        request: Request,
        limit: int = Query(default=80, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
        cursor_created_at: str = Query(default=""),
        cursor_id: int = Query(default=0, ge=0),
        q: str = Query(default=""),
        product_id: int = Query(default=0, ge=0),
        template_id: int = Query(default=0, ge=0),
        favorite: bool = Query(default=False),
        include_deleted: bool = Query(default=False),
        all_owners: bool = Query(default=False),
        owner_id: str = Query(default=""),
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        return await run_in_threadpool(
            image_library_service.list_images,
            identity=identity,
            base_url=resolve_image_base_url(request),
            limit=limit,
            offset=offset,
            cursor_created_at=cursor_created_at,
            cursor_id=cursor_id,
            query_text=q,
            product_id=product_id,
            template_id=template_id,
            favorite_only=favorite,
            include_deleted=include_deleted,
            include_all_owners=all_owners,
            owner_id_filter=owner_id,
        )

    @router.patch("/api/image-library/{image_id}")
    async def update_image_library_item(
        image_id: int,
        body: ImageLibraryUpdateRequest,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        item = await run_in_threadpool(
            image_library_service.update_image,
            identity=identity,
            image_id=image_id,
            favorite=body.favorite,
            deleted=body.deleted,
        )
        if item is None:
            raise HTTPException(status_code=404, detail={"error": "image not found"})
        return item

    @router.post("/api/image-library/bulk-delete")
    async def bulk_delete_image_library_items(
        body: ImageLibraryBulkRequest,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        if not body.ids:
            raise HTTPException(status_code=400, detail={"error": "ids is required"})
        return await run_in_threadpool(
            image_library_service.bulk_delete_images,
            identity=identity,
            image_ids=body.ids,
        )

    @router.post("/api/image-library/download-zip")
    async def download_image_library_zip(
        body: ImageLibraryZipRequest,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        if not body.ids:
            raise HTTPException(status_code=400, detail={"error": "ids is required"})
        try:
            payload, filename = await run_in_threadpool(
                _download_library_zip_payload,
                identity,
                resolve_image_base_url(request),
                body,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        headers = {"Content-Disposition": _zip_content_disposition(filename)}
        return StreamingResponse(_stream_zip_payload(payload), media_type="application/zip", headers=headers)

    @router.get("/api/image-library/health")
    async def image_library_health(authorization: str | None = Header(default=None)):
        require_identity(authorization)
        return await run_in_threadpool(image_library_service.health_check)

    return router
