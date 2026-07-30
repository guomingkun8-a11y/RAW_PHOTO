from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import hmac
from io import BytesIO
import json
from pathlib import PurePosixPath
import threading
import time
from typing import Any
from urllib.parse import urlparse

from curl_cffi import CurlMime, requests
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from services.config import config
from services.enterprise_schema import ReferenceImageAssetModel
from services.proxy_service import proxy_settings


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
_QINIU_RESUMABLE_THRESHOLD = 512 * 1024
_QINIU_PART_SIZE = 1 * 1024 * 1024
_QINIU_ENDPOINT_CIRCUIT_SECONDS = 120
_QINIU_PRIMARY_RESUME_ATTEMPTS = 2
_UPLOAD_INFLIGHT_WAIT_SLICE_SECONDS = 30
_UPLOAD_INFLIGHT_MAX_WAIT_SECONDS = 5 * 60
_upload_cache_lock = threading.RLock()
_upload_semaphore = threading.BoundedSemaphore(_UPLOAD_MAX_CONCURRENCY)
_upload_acquire_lock = threading.Lock()
_upload_url_cache: dict[str, tuple[float, str]] = {}
_upload_inflight: dict[str, threading.Event] = {}
_upload_parallelism_lock = threading.RLock()
_upload_serial_until = 0.0

_qiniu_endpoint_circuit_lock = threading.RLock()
_qiniu_endpoint_open_until: dict[str, float] = {}

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
        # Acquire weighted permits as one operation so two large uploads cannot
        # each hold one permit while waiting forever for the other.
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


def is_enabled() -> bool:
    item = settings()
    return bool(
        item.get("enabled")
        and str(item.get("qiniu_access_key") or "").strip()
        and str(item.get("qiniu_secret_key") or "").strip()
        and str(item.get("qiniu_bucket") or "").strip()
        and str(item.get("qiniu_domain") or "").strip()
    )


def _clean(value: object) -> str:
    return str(value or "").strip()


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
            "qiniu",
            _clean(item.get("qiniu_bucket")),
            _clean(item.get("qiniu_domain")),
            _clean(item.get("qiniu_prefix")),
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
        row.storage_provider = "qiniu"
        row.bucket = _clean(item.get("qiniu_bucket"))
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


def _urlsafe_base64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii")


def _qiniu_key(filename: str, digest: str | None = None, mime_type: str = "image/png") -> str:
    item = settings()
    prefix = _clean(item.get("qiniu_prefix")).strip("/")
    content_digest = digest or hashlib.sha256(_safe_filename(filename).encode("utf-8")).hexdigest()
    suffix = _safe_extension(filename, mime_type)
    key = f"sha256/{content_digest[:2]}/{content_digest}{suffix}"
    return f"{prefix}/{key}" if prefix else key


def _qiniu_token(bucket: str, key: str, access_key: str, secret_key: str) -> str:
    put_policy = {
        "scope": f"{bucket}:{key}",
        "deadline": int(time.time()) + 3600,
    }
    encoded_policy = _urlsafe_base64(json.dumps(put_policy, separators=(",", ":")).encode("utf-8"))
    sign = hmac.new(secret_key.encode("utf-8"), encoded_policy.encode("utf-8"), hashlib.sha1).digest()
    encoded_sign = _urlsafe_base64(sign)
    return f"{access_key}:{encoded_sign}:{encoded_policy}"


def _qiniu_public_url(domain: str, key: str) -> str:
    clean_domain = _clean(domain).rstrip("/")
    if not clean_domain:
        raise ReferenceImageUploadError("Qiniu public domain is empty")
    if not clean_domain.startswith(("http://", "https://")):
        clean_domain = f"https://{clean_domain}"
    return f"{clean_domain}/{key.lstrip('/')}"


def _qiniu_response_error(info: object) -> str:
    for name in ("text_body", "error", "exception"):
        value = getattr(info, name, None)
        if value:
            return _clean(value)[:300]
    return f"HTTP {int(getattr(info, 'status_code', 0) or 0)}"


def _configure_qiniu_direct_session() -> None:
    from qiniu.http.default_client import qn_http_client

    qn_http_client.session.trust_env = False


def _qiniu_upload_zone(upload_url: str):
    from qiniu import Region

    parsed = urlparse(_clean(upload_url))
    host = parsed.netloc or parsed.path.strip("/")
    if not host:
        return None
    scheme = parsed.scheme if parsed.scheme in {"http", "https"} else "https"
    backup_host = ""
    if host.startswith("upload-"):
        backup_host = "up-" + host.removeprefix("upload-")
    elif host.startswith("upload."):
        backup_host = "up." + host.removeprefix("upload.")
    return Region(
        up_host=f"{scheme}://{host}",
        up_host_backup=f"{scheme}://{backup_host}" if backup_host else None,
        scheme=scheme,
    )


def _qiniu_single_endpoint_zone(endpoint: str):
    from qiniu import Region

    parsed = urlparse(_clean(endpoint))
    host = parsed.netloc or parsed.path.strip("/")
    if not host:
        return None
    scheme = parsed.scheme if parsed.scheme in {"http", "https"} else "https"
    return Region(up_host=f"{scheme}://{host}", scheme=scheme)


def _qiniu_upload_endpoints(upload_url: str) -> list[str]:
    zone = _qiniu_upload_zone(upload_url)
    if zone is None:
        return []
    return [endpoint for endpoint in (zone.up_host, zone.up_host_backup) if endpoint]


def _resolve_qiniu_upload_endpoints(access_key: str, bucket: str, configured_upload_url: str) -> list[str]:
    try:
        from qiniu import Zone

        discovered = Zone(scheme="https").get_up_host(access_key, bucket, None)
        endpoints = [str(endpoint).strip().rstrip("/") for endpoint in discovered if str(endpoint).strip()]
        if endpoints:
            return list(dict.fromkeys(endpoints))
    except Exception:
        pass
    return _qiniu_upload_endpoints(configured_upload_url)


def _healthy_qiniu_endpoint_candidates(endpoints: list[str]) -> list[str]:
    if not endpoints:
        return []
    now = time.monotonic()
    with _qiniu_endpoint_circuit_lock:
        healthy = [endpoint for endpoint in endpoints if _qiniu_endpoint_open_until.get(endpoint, 0) <= now]
        if healthy:
            return healthy
        return [min(endpoints, key=lambda endpoint: _qiniu_endpoint_open_until.get(endpoint, 0))]
def _open_qiniu_endpoint_circuit(endpoint: str) -> None:
    if not endpoint:
        return
    with _qiniu_endpoint_circuit_lock:
        _qiniu_endpoint_open_until[endpoint] = time.monotonic() + _QINIU_ENDPOINT_CIRCUIT_SECONDS


def _close_qiniu_endpoint_circuit(endpoint: str) -> None:
    with _qiniu_endpoint_circuit_lock:
        _qiniu_endpoint_open_until.pop(endpoint, None)


def _is_retryable_qiniu_failure(info: object) -> bool:
    status_code = int(getattr(info, "status_code", 0) or 0)
    return bool(getattr(info, "exception", None)) or status_code <= 0 or status_code in {408, 429} or status_code >= 500


def _put_qiniu_resumable_data(
    token: str,
    key: str,
    image_data: bytes,
    filename: str,
    mime_type: str,
    bucket: str,
    upload_zone: object,
):
    from qiniu.services.storage.upload_progress_recorder import UploadProgressRecorder
    from qiniu.services.storage.uploaders import ResumeUploaderV2

    recorder = UploadProgressRecorder()
    uploader = ResumeUploaderV2(
        bucket,
        upload_progress_recorder=recorder,
        part_size=_QINIU_PART_SIZE,
        regions=[upload_zone] if upload_zone is not None else None,
        preferred_scheme=getattr(upload_zone, "scheme", "https"),
        concurrent_executor=None,
    )
    return uploader.upload(
        key=key,
        data=BytesIO(image_data),
        data_size=len(image_data),
        file_name=filename,
        mime_type=mime_type,
        up_token=token,
        part_size=_QINIU_PART_SIZE,
    )


def _clear_qiniu_upload_progress(filename: str, key: str) -> None:
    try:
        from qiniu.services.storage.upload_progress_recorder import UploadProgressRecorder

        UploadProgressRecorder().delete_upload_record(filename, key)
    except Exception:
        pass


def _upload_to_qiniu_sdk(
    image_data: bytes,
    filename: str,
    mime_type: str,
    *,
    key: str,
    timeout: int,
    upload_settings: dict[str, object] | None = None,
) -> str:
    from qiniu import Auth, BucketManager, config as qiniu_config, put_data

    _configure_qiniu_direct_session()
    item = upload_settings or settings()
    access_key = _clean(item.get("qiniu_access_key"))
    secret_key = _clean(item.get("qiniu_secret_key"))
    bucket = _clean(item.get("qiniu_bucket"))
    domain = _clean(item.get("qiniu_domain"))
    upload_url = _clean(item.get("qiniu_upload_url"))
    upload_zone = _qiniu_upload_zone(upload_url)
    request_timeout = max(20, min(30, int(timeout or 30)))
    qiniu_config.set_default(
        default_zone=upload_zone,
        connection_timeout=request_timeout,
        connection_retries=1,
        connection_pool=max(3, _UPLOAD_MAX_CONCURRENCY),
        default_rs_host="https://rs.qiniuapi.com",
    )
    auth = Auth(access_key, secret_key)
    try:
        _ret, stat_info = BucketManager(auth).stat(bucket, key)
        if int(getattr(stat_info, "status_code", 0) or 0) == 200:
            return _qiniu_public_url(domain, key)
    except Exception:
        pass

    token = auth.upload_token(bucket, key, 3600)
    safe_filename = _safe_filename(filename)
    safe_mime = _clean(mime_type) or "image/png"
    endpoints = _healthy_qiniu_endpoint_candidates(_resolve_qiniu_upload_endpoints(access_key, bucket, upload_url))
    attempts = endpoints or [""]
    last_error = "no upload endpoint available"
    resumable = len(image_data) >= _QINIU_RESUMABLE_THRESHOLD
    for endpoint_index, endpoint in enumerate(attempts):
        endpoint_zone = _qiniu_single_endpoint_zone(endpoint) if endpoint else upload_zone
        qiniu_config.set_default(default_zone=endpoint_zone)
        endpoint_attempts = _QINIU_PRIMARY_RESUME_ATTEMPTS if resumable and endpoint_index == 0 else 1
        for endpoint_attempt in range(endpoint_attempts):
            try:
                if resumable:
                    ret, info = _put_qiniu_resumable_data(
                        token, key, image_data, safe_filename, safe_mime, bucket, endpoint_zone,
                    )
                else:
                    ret, info = put_data(
                        token,
                        key,
                        image_data,
                        mime_type=safe_mime,
                        check_crc=True,
                        fname=safe_filename,
                        regions=[endpoint_zone] if endpoint_zone is not None else None,
                    )
            except Exception as exc:
                last_error = str(exc)[:300]
                _degrade_upload_parallelism()
                if endpoint_attempt + 1 < endpoint_attempts:
                    time.sleep(1.0)
                    continue
                break
            status_code = int(getattr(info, "status_code", 0) or 0)
            if status_code == 200 and isinstance(ret, dict):
                _close_qiniu_endpoint_circuit(endpoint)
                return _qiniu_public_url(domain, key)
            last_error = _qiniu_response_error(info)
            if not _is_retryable_qiniu_failure(info):
                _clear_qiniu_upload_progress(safe_filename, key)
                raise ReferenceImageUploadError(f"Qiniu SDK upload failed: {last_error}")
            _degrade_upload_parallelism()
            if endpoint_attempt + 1 < endpoint_attempts:
                time.sleep(1.0)
                continue
            break
        _open_qiniu_endpoint_circuit(endpoint)
        if resumable:
            _clear_qiniu_upload_progress(safe_filename, key)

    if not resumable and attempts:
        endpoint = attempts[0]
        endpoint_zone = _qiniu_single_endpoint_zone(endpoint) if endpoint else upload_zone
        qiniu_config.set_default(default_zone=endpoint_zone)
        try:
            ret, info = _put_qiniu_resumable_data(
                token, key, image_data, safe_filename, safe_mime, bucket, endpoint_zone,
            )
        except Exception as exc:
            last_error = str(exc)[:300]
            _degrade_upload_parallelism()
        else:
            status_code = int(getattr(info, "status_code", 0) or 0)
            if status_code == 200 and isinstance(ret, dict):
                _close_qiniu_endpoint_circuit(endpoint)
                return _qiniu_public_url(domain, key)
            last_error = _qiniu_response_error(info)
        _clear_qiniu_upload_progress(safe_filename, key)
    raise ReferenceImageUploadError(f"Qiniu SDK upload failed after endpoint failover: {last_error}")


def _upload_to_qiniu_legacy(
    image_data: bytes,
    filename: str,
    mime_type: str,
    *,
    key: str,
    timeout: int,
) -> str:
    item = settings()
    access_key = _clean(item.get("qiniu_access_key"))
    secret_key = _clean(item.get("qiniu_secret_key"))
    bucket = _clean(item.get("qiniu_bucket"))
    domain = _clean(item.get("qiniu_domain"))
    upload_url = _clean(item.get("qiniu_upload_url")).rstrip("/")
    if not upload_url:
        raise ReferenceImageUploadError("Qiniu upload URL is empty")
    token = _qiniu_token(bucket, key, access_key, secret_key)
    last_error = ""
    for attempt in range(1, 4):
        multipart = CurlMime()
        try:
            multipart.addpart(name="token", data=token)
            multipart.addpart(name="key", data=key)
            multipart.addpart(
                name="file",
                filename=_safe_filename(filename),
                content_type=_clean(mime_type) or "image/png",
                data=image_data,
            )
            response = requests.post(
                upload_url,
                multipart=multipart,
                timeout=timeout,
                **proxy_settings.build_session_kwargs(),
            )
        except Exception as exc:
            last_error = str(exc)
            _degrade_upload_parallelism()
            if attempt >= 3:
                raise ReferenceImageUploadError(f"Qiniu upload failed after {attempt} attempts: {exc}") from exc
            time.sleep(0.8 * attempt)
            continue
        finally:
            multipart.close()
        if 200 <= response.status_code < 300:
            return _qiniu_public_url(domain, key)
        last_error = f"HTTP {response.status_code}: {_clean(response.text)[:300]}"
        if response.status_code in {500, 502, 503, 504} and attempt < 3:
            time.sleep(0.8 * attempt)
            continue
        raise ReferenceImageUploadError(f"Qiniu upload failed: {last_error}")
    raise ReferenceImageUploadError(f"Qiniu upload failed: {last_error or 'no response'}")


def upload_to_qiniu(
    image_data: bytes,
    filename: str = "reference.png",
    mime_type: str = "image/png",
) -> str:
    item = settings()
    if not is_enabled():
        raise ReferenceImageUploadError("Qiniu reference image upload is not configured")
    if not image_data:
        raise ReferenceImageUploadError("reference image is empty")
    timeout = max(5, int(item.get("timeout_sec") or 20))
    digest = hashlib.sha256(image_data).hexdigest()
    key = _qiniu_key(filename, digest, mime_type)
    try:
        return _upload_to_qiniu_sdk(image_data, filename, mime_type, key=key, timeout=timeout)
    except ImportError:
        return _upload_to_qiniu_legacy(image_data, filename, mime_type, key=key, timeout=timeout)


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
            url = upload_to_qiniu(image_data, safe_filename, safe_mime)
        object_key = _qiniu_key(safe_filename, digest, safe_mime)
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
