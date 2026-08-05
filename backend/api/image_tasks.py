from __future__ import annotations

import base64
import mimetypes
import re
import tempfile
import zipfile
from urllib.parse import quote, unquote, urlparse

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api.image_inputs import _download_image_url, collect_http_image_urls, parse_image_edit_request, read_image_sources
from api.support import require_identity, resolve_image_base_url
from services.content_filter import check_request
from services.generation_monitoring_service import generation_monitoring_service
from services.image_storage_service import image_storage_service
from services.image_task_service import image_task_service
from services.log_service import LoggedCall
from services import openai_relay_service, reference_image_uploader

ZIP_MAX_ITEM_BYTES = 50 * 1024 * 1024
ZIP_MAX_TOTAL_BYTES = 500 * 1024 * 1024
ZIP_SPOOL_MEMORY_BYTES = 8 * 1024 * 1024
ZIP_STREAM_CHUNK_BYTES = 1024 * 1024


class ImageGenerationTaskRequest(BaseModel):
    client_task_id: str = Field(..., min_length=1)
    prompt: str = Field(..., min_length=1)
    model: str = "gpt-image-2"
    size: str | None = None
    quality: str = "auto"
    conversation_id: str | None = None
    turn_id: str | None = None
    product_id: int | None = None
    template_id: int | None = None
    batch_id: str | None = None
    batch_index: int = Field(default=0, ge=0, le=10000)
    batch_total: int = Field(default=1, ge=1, le=10000)


class ResumePollRequest(BaseModel):
    extra_timeout_secs: float = Field(default=30.0, ge=5.0, le=120.0)


class ImageFailureReportRequest(BaseModel):
    task_id: str = Field(..., min_length=1)
    failure_report_id: str = ""
    error: str = ""
    image_count: int = Field(default=1, ge=1, le=100)
    mode: str = "generate"
    model: str = ""
    product_id: int = 0
    template_id: int = 0


class ImageZipItem(BaseModel):
    task_id: str = Field(..., min_length=1, max_length=191)
    image_index: int = Field(default=0, ge=0, le=100)
    filename: str = "image.png"


class ImageZipDownloadRequest(BaseModel):
    folder_name: str = Field(default="AI-Image-Results", min_length=1, max_length=120)
    items: list[ImageZipItem] = Field(default_factory=list, min_length=1, max_length=100)


class ImageTaskQueryRequest(BaseModel):
    ids: list[str] = Field(default_factory=list, min_length=1, max_length=500)


def _parse_task_ids(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _zip_safe_name(value: str, fallback: str) -> str:
    clean = re.sub(r'[\\/:*?"<>|]+', "-", str(value or "").strip())
    clean = re.sub(r"\s+", " ", clean).strip(" .")
    return (clean or fallback)[:120]


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


def _zip_content_disposition(filename: str) -> str:
    ascii_name = filename.encode("ascii", errors="ignore").decode("ascii")
    if ascii_name != filename:
        ascii_name = "AI-Image-Results.zip"
    fallback = _zip_safe_name(ascii_name, "AI-Image-Results.zip")
    encoded = quote(filename, safe="")
    return f'attachment; filename="{fallback}"; filename*=UTF-8\'\'{encoded}'


def _storage_rel_from_url(url: str) -> str:
    parsed = urlparse(str(url or "").strip())
    path = parsed.path if parsed.scheme or parsed.netloc else str(url or "").split("?", 1)[0]
    marker = "/images/"
    if marker not in path:
        return ""
    return unquote(path.split(marker, 1)[1]).lstrip("/")


def _task_image_payload(task: dict[str, object], image_index: int) -> tuple[bytes, str, str]:
    data = task.get("data")
    if not isinstance(data, list) or image_index >= len(data):
        raise ValueError("task image is unavailable")
    item = data[image_index]
    if not isinstance(item, dict):
        raise ValueError("task image is unavailable")

    storage_rel = str(item.get("storage_rel") or "").strip() or _storage_rel_from_url(str(item.get("url") or ""))
    if storage_rel:
        image_bytes = image_storage_service.get_bytes(storage_rel)
        content_type = mimetypes.guess_type(storage_rel)[0] or ""
        return image_bytes, content_type, storage_rel

    encoded = str(item.get("b64_json") or "").strip()
    if encoded:
        estimated_size = len(encoded) * 3 // 4
        if estimated_size > ZIP_MAX_ITEM_BYTES:
            raise ValueError("task image exceeds download limit")
        try:
            return base64.b64decode(encoded, validate=True), "image/png", "image.png"
        except Exception as exc:
            raise ValueError("task image is invalid") from exc

    url = str(item.get("url") or "").strip()
    if url:
        try:
            image_bytes, filename, content_type = _download_image_url(url)
        except HTTPException as exc:
            raise ValueError("task image URL is unavailable") from exc
        return image_bytes, content_type, filename

    raise ValueError("task image is not stored locally")


def _download_zip_payload(
    identity: dict[str, object],
    body: ImageZipDownloadRequest,
):
    folder_name = _zip_safe_name(body.folder_name, "AI-Image-Results")
    task_ids = list(dict.fromkeys(item.task_id.strip() for item in body.items if item.task_id.strip()))
    task_list = image_task_service.list_tasks(identity, task_ids)
    task_map = {
        str(task.get("id") or ""): task
        for task in task_list.get("items", [])
        if isinstance(task, dict) and task.get("id")
    }
    used_names: set[str] = set()
    archive = tempfile.SpooledTemporaryFile(max_size=ZIP_SPOOL_MEMORY_BYTES, mode="w+b")
    added = 0
    total_bytes = 0
    try:
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for index, item in enumerate(body.items, start=1):
                task = task_map.get(item.task_id.strip())
                if task is None:
                    continue
                try:
                    image_bytes, content_type, source_name = _task_image_payload(task, item.image_index)
                except (HTTPException, ValueError):
                    continue
                if not image_bytes or len(image_bytes) > ZIP_MAX_ITEM_BYTES:
                    continue
                total_bytes += len(image_bytes)
                if total_bytes > ZIP_MAX_TOTAL_BYTES:
                    raise ValueError("download package exceeds size limit")

                raw_name = _zip_safe_name(item.filename, f"image-{index}.png")
                stem = raw_name.rsplit(".", 1)[0] if "." in raw_name else raw_name
                ext = _image_ext_from_content_type(content_type, source_name or raw_name)
                name = f"{stem}.{ext}"
                counter = 2
                while name in used_names:
                    name = f"{stem}-{counter}.{ext}"
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


async def filter_or_log(call: LoggedCall, text: str) -> None:
    try:
        await run_in_threadpool(check_request, text)
    except HTTPException as exc:
        call.log("调用失败", status="failed", error=str(exc.detail))
        raise


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/image-tasks")
    async def list_image_tasks(
        ids: str = Query(default=""),
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        return await run_in_threadpool(image_task_service.list_tasks, identity, _parse_task_ids(ids))

    @router.post("/api/image-tasks/query")
    async def query_image_tasks(
        body: ImageTaskQueryRequest,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        return await run_in_threadpool(image_task_service.list_tasks, identity, body.ids)

    @router.post("/api/image-tasks/download-zip")
    async def download_image_task_zip(body: ImageZipDownloadRequest, authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        try:
            payload, filename = await run_in_threadpool(_download_zip_payload, identity, body)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        headers = {"Content-Disposition": _zip_content_disposition(filename)}
        return StreamingResponse(_stream_zip_payload(payload), media_type="application/zip", headers=headers)

    @router.post("/api/image-tasks/generations")
    async def create_generation_task(
        body: ImageGenerationTaskRequest,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        await filter_or_log(LoggedCall(identity, "/api/image-tasks/generations", body.model, "文生图任务", request_text=body.prompt), body.prompt)
        try:
            return await run_in_threadpool(
                image_task_service.submit_generation,
                identity,
                client_task_id=body.client_task_id,
                prompt=body.prompt,
                model=body.model,
                size=body.size,
                quality=body.quality,
                base_url=resolve_image_base_url(request),
                conversation_id=str(body.conversation_id or ""),
                turn_id=str(body.turn_id or ""),
                product_id=body.product_id or 0,
                template_id=body.template_id or 0,
                batch_id=str(body.batch_id or ""),
                batch_index=body.batch_index,
                batch_total=body.batch_total,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.post("/api/image-tasks/edits")
    async def create_edit_task(
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        payload, image_sources, mask_sources = await parse_image_edit_request(request)
        client_task_id = str(payload.get("client_task_id") or "").strip()
        if not client_task_id:
            raise HTTPException(status_code=400, detail={"error": "client_task_id is required"})
        prompt = str(payload["prompt"])
        model = str(payload["model"])
        await filter_or_log(LoggedCall(identity, "/api/image-tasks/edits", model, "图生图任务", request_text=prompt), prompt)
        image_urls = collect_http_image_urls(image_sources)
        requires_public_urls = openai_relay_service.requires_public_image_urls()
        local_image_sources = (
            [
                source
                for source in image_sources
                if not (isinstance(source, str) and source.strip().lower().startswith(("http://", "https://")))
            ]
            if requires_public_urls
            else image_sources
        )
        images = await read_image_sources(local_image_sources) if local_image_sources else []
        masks = await read_image_sources(mask_sources) if mask_sources else None
        reference_upload_ms = int(payload.get("reference_upload_ms") or 0)
        reference_cache_hits = int(payload.get("reference_cache_hits") or 0)
        if requires_public_urls and images:
            try:
                uploaded = await run_in_threadpool(reference_image_uploader.upload_images_detailed, images)
            except reference_image_uploader.ReferenceImageUploadError as exc:
                raise HTTPException(status_code=502, detail={"error": f"reference image upload failed: {exc}"}) from exc
            image_urls.extend(item.url for item in uploaded if item.url not in image_urls)
            reference_upload_ms += sum(max(0, item.upload_ms) for item in uploaded)
            reference_cache_hits += sum(1 for item in uploaded if item.cached)
            images = []
        try:
            return await run_in_threadpool(
                image_task_service.submit_edit,
                identity,
                client_task_id=client_task_id,
                prompt=prompt,
                model=model,
                size=payload["size"],
                quality=payload["quality"],
                base_url=resolve_image_base_url(request),
                images=images,
                masks=masks,
                image_urls=image_urls,
                preserve_subject=bool(payload.get("preserve_subject")),
                conversation_id=str(payload.get("conversation_id") or ""),
                turn_id=str(payload.get("turn_id") or ""),
                product_id=int(payload.get("product_id") or 0),
                template_id=int(payload.get("template_id") or 0),
                batch_id=str(payload.get("batch_id") or ""),
                batch_index=int(payload.get("batch_index") or 0),
                batch_total=int(payload.get("batch_total") or 1),
                reference_upload_ms=reference_upload_ms,
                reference_cache_hits=reference_cache_hits,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.post("/api/image-tasks/{task_id}/resume-poll")
    async def resume_image_poll(
        task_id: str,
        body: ResumePollRequest,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        try:
            return await run_in_threadpool(
                image_task_service.resume_poll,
                identity,
                task_id,
                body.extra_timeout_secs,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.post("/api/image-tasks/{task_id}/cancel")
    async def cancel_image_task(
        task_id: str,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        try:
            return await run_in_threadpool(image_task_service.cancel_task, identity, task_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.post("/api/image-tasks/failure-reports")
    async def report_image_failure(body: ImageFailureReportRequest, authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        try:
            return await run_in_threadpool(
                generation_monitoring_service.report_frontend_failure,
                identity=identity,
                task_id=body.task_id,
                failure_report_id=body.failure_report_id,
                error=body.error,
                image_count=body.image_count,
                mode=body.mode,
                model=body.model,
                product_id=body.product_id,
                template_id=body.template_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    return router
