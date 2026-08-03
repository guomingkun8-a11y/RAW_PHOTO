from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
from io import BytesIO
from pathlib import PurePosixPath
import threading
import time
from urllib.parse import quote, urlparse

from minio import Minio
from minio.error import S3Error
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from services.config import config
from services.enterprise_schema import ReferenceImageAssetModel


class ReferenceImageUploadError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReferenceUploadResult:
    url: str
    sha256: str
    filename: str
    mime_type: str
    file_size: int
    cached: bool
    upload_ms: int

    def to_public(self) -> dict[str, object]:
        return {
            "url": self.url,
            "sha256": self.sha256,
            "filename": self.filename,
            "mime_type": self.mime_type,
            "file_size": self.file_size,
            "cached": self.cached,
            "upload_ms": self.upload_ms,
        }


_UPLOAD_CACHE_TTL_SECONDS = 6 * 60 * 60
_UPLOAD_CACHE_MAX_ITEMS = 512
_UPLOAD_MAX_CONCURRENCY = 2
_UPLOAD_LARGE_FILE_THRESHOLD = 3 * 1024 * 1024
_UPLOAD_SERIAL_COOLDOWN_SECONDS = 3 * 60
_UPLOAD_INFLIGHT_WAIT_SLICE_SECONDS = 30
_UPLOAD_INFLIGHT_MAX_WAIT_SECONDS = 5 * 60
_upload_cache_lock = threading.RLock()
_upload_semaphore = threading.BoundedSemaphore(_UPLOAD_MAX_CONCURRENCY)
_upload_acquire_lock = threading.Lock()
_upload_url_cache: dict[str, tuple[float, str]] = {}
_upload_inflight: dict[str, threading.Event] = {}
_upload_parallelism_lock = threading.RLock()
_upload_serial_until = 0.0

_asset_cache_lock = threading.RLock()
_asset_cache_database_url = ""
_asset_cache_session_factory: sessionmaker | None = None

_metrics_lock = threading.RLock()
_metrics = {
    "requests": 0,
    "uploaded": 0,
    "cache_hits": 0,
    "failures": 0,
    "durations_ms": [],
}


def _upload_serial_mode_active() -> bool:
    with _upload_parallelism_lock:
        return time.monotonic() < _upload_serial_until


def _degrade_upload_parallelism() -> None:
    global _upload_serial_until
    with _upload_parallelism_lock:
        _upload_serial_until = max(_upload_serial_until, time.monotonic() + _UPLOAD_SERIAL_COOLDOWN_SECONDS)


@contextmanager
def _upload_capacity(file_size: int):
    permits = _UPLOAD_MAX_CONCURRENCY if file_size >= _UPLOAD_LARGE_FILE_THRESHOLD or _upload_serial_mode_active() else 1
    acquired = 0
    try:
        with _upload_acquire_lock:
            for _index in range(permits):
                _upload_semaphore.acquire()
                acquired += 1
        yield
    finally:
        for _index in range(acquired):
            _upload_semaphore.release()


def settings() -> dict[str, object]:
    return config.get_image_reference_upload_settings()


def _clean(value: object) -> str:
    return str(value or "").strip()


def _bool(value: object, default: bool = False) -> bool:
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
        return default
    if value is None:
        return default
    return bool(value)


def is_enabled() -> bool:
    item = settings()
    return bool(
        item.get("enabled")
        and _clean(item.get("oss_endpoint"))
        and _clean(item.get("oss_access_key"))
        and _clean(item.get("oss_secret_key"))
        and _clean(item.get("oss_bucket"))
        and _public_base_url(item)
    )


def _safe_filename(filename: str) -> str:
    value = _clean(filename) or "reference.png"
    return value.replace("\\", "_").replace("/", "_")


def _safe_extension(filename: str, mime_type: str) -> str:
    suffix = PurePosixPath(_safe_filename(filename)).suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif"}:
        return ".jpg" if suffix == ".jpeg" else suffix
    mime = _clean(mime_type).lower()
    if "jpeg" in mime:
        return ".jpg"
    if "webp" in mime:
        return ".webp"
    if "gif" in mime:
        return ".gif"
    if "avif" in mime:
        return ".avif"
    return ".png"


def _upload_scope() -> str:
    item = settings()
    return "|".join(
        [
            "oss",
            _clean(item.get("oss_endpoint")).rstrip("/"),
            _clean(item.get("oss_bucket")),
            _public_base_url(item),
            _clean(item.get("oss_prefix")).strip("/"),
        ]
    )


def _upload_cache_key(image_data: bytes, mime_type: str) -> str:
    digest = hashlib.sha256(image_data).hexdigest()
    return hashlib.sha256(f"{_upload_scope()}|{_clean(mime_type)}|{digest}".encode("utf-8")).hexdigest()


def _persistent_cache_session():
    global _asset_cache_database_url, _asset_cache_session_factory
    if not bool(settings().get("persistent_cache_enabled")):
        return None
    database_url = _clean(config.get_image_task_queue_settings().get("database_url"))
    if not database_url:
        return None
    with _asset_cache_lock:
        if _asset_cache_session_factory is None or database_url != _asset_cache_database_url:
            engine = create_engine(database_url, pool_pre_ping=True, pool_recycle=3600)
            ReferenceImageAssetModel.__table__.create(engine, checkfirst=True)
            _asset_cache_session_factory = sessionmaker(bind=engine)
            _asset_cache_database_url = database_url
        return _asset_cache_session_factory()


def _persistent_cached_upload(cache_key: str) -> str:
    try:
        session = _persistent_cache_session()
    except Exception:
        return ""
    if session is None:
        return ""
    try:
        row = session.get(ReferenceImageAssetModel, cache_key)
        if row is None:
            return ""
        if not row.last_used_at or row.last_used_at < datetime.now() - timedelta(hours=1):
            row.last_used_at = datetime.now()
            session.commit()
        return _clean(row.url)
    except Exception:
        session.rollback()
        return ""
    finally:
        session.close()


def _remember_persistent_upload(
    cache_key: str,
    digest: str,
    url: str,
    object_key: str,
    mime_type: str,
    file_size: int,
) -> None:
    try:
        session = _persistent_cache_session()
    except Exception:
        return
    if session is None:
        return
    try:
        row = session.get(ReferenceImageAssetModel, cache_key)
        if row is None:
            row = ReferenceImageAssetModel(cache_key=cache_key, created_at=datetime.now())
            session.add(row)
        item = settings()
        row.sha256 = digest
        row.storage_provider = "oss"
        row.bucket = _clean(item.get("oss_bucket"))
        row.object_key = object_key
        row.url = url
        row.mime_type = _clean(mime_type) or "image/png"
        row.file_size = max(0, int(file_size))
        row.updated_at = datetime.now()
        row.last_used_at = datetime.now()
        session.commit()
    except IntegrityError:
        session.rollback()
    except Exception:
        session.rollback()
    finally:
        session.close()


def _cached_upload_url(cache_key: str) -> str:
    now = time.time()
    with _upload_cache_lock:
        item = _upload_url_cache.get(cache_key)
        if item:
            cached_at, url = item
            if now - cached_at <= _UPLOAD_CACHE_TTL_SECONDS:
                return url
            _upload_url_cache.pop(cache_key, None)
    persistent = _persistent_cached_upload(cache_key)
    if persistent:
        _remember_upload_url(cache_key, persistent)
    return persistent


def _remember_upload_url(cache_key: str, url: str) -> None:
    if not url:
        return
    with _upload_cache_lock:
        _upload_url_cache[cache_key] = (time.time(), url)
        if len(_upload_url_cache) <= _UPLOAD_CACHE_MAX_ITEMS:
            return
        oldest_keys = sorted(_upload_url_cache, key=lambda key: _upload_url_cache[key][0])
        for key in oldest_keys[: max(1, len(_upload_url_cache) - _UPLOAD_CACHE_MAX_ITEMS)]:
            _upload_url_cache.pop(key, None)


def _record_metric(*, cached: bool = False, failed: bool = False, duration_ms: int = 0) -> None:
    with _metrics_lock:
        _metrics["requests"] += 1
        if failed:
            _metrics["failures"] += 1
        elif cached:
            _metrics["cache_hits"] += 1
        else:
            _metrics["uploaded"] += 1
        durations = _metrics["durations_ms"]
        if isinstance(durations, list):
            durations.append(max(0, int(duration_ms)))
            del durations[:-512]


def metrics_snapshot() -> dict[str, object]:
    with _metrics_lock:
        requests_count = int(_metrics["requests"])
        durations = [int(value) for value in _metrics["durations_ms"]]
        ordered = sorted(durations)
        p95_index = max(0, min(len(ordered) - 1, int(len(ordered) * 0.95) - 1)) if ordered else 0
        return {
            "requests": requests_count,
            "uploaded": int(_metrics["uploaded"]),
            "cache_hits": int(_metrics["cache_hits"]),
            "failures": int(_metrics["failures"]),
            "cache_hit_rate": round(int(_metrics["cache_hits"]) / requests_count, 4) if requests_count else 0,
            "average_ms": round(sum(durations) / len(durations), 1) if durations else 0,
            "p95_ms": ordered[p95_index] if ordered else 0,
            "max_ms": max(durations) if durations else 0,
            "upload_concurrency": 1 if _upload_serial_mode_active() else _UPLOAD_MAX_CONCURRENCY,
            "serial_cooldown_active": _upload_serial_mode_active(),
        }


def _image_bytes_to_data_url(image_data: bytes, mime_type: str = "image/png") -> str:
    return f"data:{_clean(mime_type) or 'image/png'};base64,{base64.b64encode(image_data).decode('ascii')}"


def _object_key(filename: str, digest: str | None = None, mime_type: str = "image/png") -> str:
    item = settings()
    prefix = _clean(item.get("oss_prefix")).strip("/")
    content_digest = digest or hashlib.sha256(_safe_filename(filename).encode("utf-8")).hexdigest()
    suffix = _safe_extension(filename, mime_type)
    key = f"sha256/{content_digest[:2]}/{content_digest}{suffix}"
    return f"{prefix}/{key}" if prefix else key


def _public_base_url(item: dict[str, object] | None = None) -> str:
    settings_item = item or settings()
    explicit = _clean(settings_item.get("public_base_url")).rstrip("/")
    if explicit:
        return explicit
    endpoint = _clean(settings_item.get("oss_endpoint")).rstrip("/")
    bucket = _clean(settings_item.get("oss_bucket"))
    if not endpoint or not bucket:
        return ""
    parsed = urlparse(endpoint)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return f"{parsed.scheme}://{bucket}.{parsed.netloc}".rstrip("/")
    return ""


def _public_url(item: dict[str, object], object_key: str) -> str:
    public_base_url = _public_base_url(item)
    if not public_base_url:
        raise ReferenceImageUploadError("OSS public base URL is empty")
    return f"{public_base_url}/{quote(object_key.lstrip('/'), safe='/')}"


def _oss_client(item: dict[str, object]) -> Minio:
    endpoint = _clean(item.get("oss_endpoint")).rstrip("/")
    access_key = _clean(item.get("oss_access_key"))
    secret_key = _clean(item.get("oss_secret_key"))
    region = _clean(item.get("oss_region")) or None
    if not endpoint or not access_key or not secret_key:
        raise ReferenceImageUploadError("OSS reference image upload is not configured")
    parsed = urlparse(endpoint)
    if parsed.scheme in {"http", "https"}:
        endpoint = parsed.netloc
        secure = parsed.scheme == "https"
    else:
        secure = _bool(item.get("oss_secure"), True)
    if not endpoint:
        raise ReferenceImageUploadError("invalid OSS endpoint")
    return Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure, region=region)


def upload_to_oss(
    image_data: bytes,
    filename: str = "reference.png",
    mime_type: str = "image/png",
) -> str:
    item = settings()
    if not is_enabled():
        raise ReferenceImageUploadError("OSS reference image upload is not configured")
    if not image_data:
        raise ReferenceImageUploadError("reference image is empty")
    bucket = _clean(item.get("oss_bucket"))
    timeout = max(5, int(item.get("timeout_sec") or 20))
    digest = hashlib.sha256(image_data).hexdigest()
    key = _object_key(filename, digest, mime_type)
    client = _oss_client(item)
    try:
        try:
            client.stat_object(bucket, key)
            return _public_url(item, key)
        except S3Error as exc:
            if exc.code not in {"NoSuchKey", "NoSuchBucket", "NoSuchObject", "NotFound"}:
                raise
        client.put_object(
            bucket,
            key,
            BytesIO(image_data),
            length=len(image_data),
            content_type=_clean(mime_type) or "image/png",
            part_size=10 * 1024 * 1024,
            num_parallel_uploads=1,
        )
        return _public_url(item, key)
    except Exception as exc:
        _degrade_upload_parallelism()
        message = str(exc)[:300] or exc.__class__.__name__
        raise ReferenceImageUploadError(f"OSS upload failed within {timeout}s budget: {message}") from exc


def _upload_one_detailed(image_data: bytes, filename: str, mime_type: str) -> ReferenceUploadResult:
    started = time.perf_counter()
    digest = hashlib.sha256(image_data).hexdigest()
    cache_key = _upload_cache_key(image_data, mime_type)
    safe_filename = _safe_filename(filename)
    safe_mime = _clean(mime_type) or "image/png"
    wait_deadline = time.monotonic() + _UPLOAD_INFLIGHT_MAX_WAIT_SECONDS

    while True:
        cached = _cached_upload_url(cache_key)
        if cached:
            duration_ms = int((time.perf_counter() - started) * 1000)
            result = ReferenceUploadResult(cached, digest, safe_filename, safe_mime, len(image_data), True, duration_ms)
            _record_metric(cached=True, duration_ms=duration_ms)
            return result
        with _upload_cache_lock:
            event = _upload_inflight.get(cache_key)
            if event is None:
                event = threading.Event()
                _upload_inflight[cache_key] = event
                is_owner = True
            else:
                is_owner = False
        if is_owner:
            break
        remaining = wait_deadline - time.monotonic()
        if remaining <= 0:
            duration_ms = int((time.perf_counter() - started) * 1000)
            _record_metric(failed=True, duration_ms=duration_ms)
            raise ReferenceImageUploadError("timed out waiting for identical reference image upload")
        event.wait(min(_UPLOAD_INFLIGHT_WAIT_SLICE_SECONDS, remaining))

    try:
        with _upload_capacity(len(image_data)):
            cached = _cached_upload_url(cache_key)
            if cached:
                duration_ms = int((time.perf_counter() - started) * 1000)
                result = ReferenceUploadResult(cached, digest, safe_filename, safe_mime, len(image_data), True, duration_ms)
                _record_metric(cached=True, duration_ms=duration_ms)
                return result
            url = upload_to_oss(image_data, safe_filename, safe_mime)
        object_key = _object_key(safe_filename, digest, safe_mime)
        _remember_upload_url(cache_key, url)
        _remember_persistent_upload(cache_key, digest, url, object_key, safe_mime, len(image_data))
        duration_ms = int((time.perf_counter() - started) * 1000)
        result = ReferenceUploadResult(url, digest, safe_filename, safe_mime, len(image_data), False, duration_ms)
        _record_metric(duration_ms=duration_ms)
        return result
    except Exception:
        _record_metric(failed=True, duration_ms=int((time.perf_counter() - started) * 1000))
        raise
    finally:
        with _upload_cache_lock:
            event = _upload_inflight.pop(cache_key, None)
            if event is not None:
                event.set()


def upload_images_detailed(images: list[tuple[bytes, str, str]]) -> list[ReferenceUploadResult]:
    if not images:
        return []
    with ThreadPoolExecutor(max_workers=min(_UPLOAD_MAX_CONCURRENCY, len(images))) as executor:
        futures = [executor.submit(_upload_one_detailed, image_data, filename, mime_type) for image_data, filename, mime_type in images]
        return [future.result() for future in futures]


def upload_images(
    images: list[tuple[bytes, str, str]],
    *,
    fallback_to_data_url: bool = False,
) -> list[str]:
    urls: list[str] = []
    for image_data, filename, mime_type in images:
        try:
            url = _upload_one_detailed(image_data, filename, mime_type).url
        except Exception:
            if not fallback_to_data_url:
                raise
            url = _image_bytes_to_data_url(image_data, mime_type)
        if url and url not in urls:
            urls.append(url)
    return urls
