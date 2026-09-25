from __future__ import annotations

import hashlib
import ipaddress
import logging
import os
import re
import shutil
import socket
import tempfile
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path, PurePosixPath
from typing import BinaryIO
from urllib.parse import quote, urlencode, urljoin, urlsplit, urlunsplit

from curl_cffi import CurlOpt, requests
from curl_cffi.curl import CURL_WRITEFUNC_ERROR
from fastapi import HTTPException
from minio import Minio
from minio.error import S3Error
from services.platform.config import DATA_DIR, config
from services.audio.audio_generation_signing import result_url_ttl, sign_audio_path, verify_audio_signature


AUDIO_GENERATION_DIR = DATA_DIR / "audio_generation"
AUDIO_EXTENSIONS = {".mp3", ".wav", ".pcm", ".ogg", ".opus", ".m4a", ".aac", ".flac"}
DEFAULT_MAX_RESULT_BYTES = 200 * 1024 * 1024
MAX_REDIRECTS = 3
DOWNLOAD_TIMEOUT_SECS = 120
logger = logging.getLogger(__name__)


class AudioGenerationStorageError(RuntimeError):
    pass


@dataclass(frozen=True)
class StoredAudio:
    """Persist these fields, not the temporary URL returned by the resolver.

    relative_path is the full object key for OSS and a root-relative path locally.
    url is an internal audio-storage:// reference, never a playable public URL.
    """

    relative_path: str
    url: str
    storage: str
    size: int


@dataclass(frozen=True)
class _DownloadedAudio:
    stream: BinaryIO
    size: int
    digest: str
    content_type: str
    source_url: str


def _clean(value: object, default: str = "", limit: int = 2000) -> str:
    text = str(value if value is not None else default).strip()
    return (text or default)[:limit]


def _safe_relative_path(value: str) -> str:
    # Reject noncanonical paths, including Windows drive/ADS and encoded traversal.
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise AudioGenerationStorageError("invalid audio storage path")
    parts = value.split("/")
    if any(not re.fullmatch(r"[a-zA-Z0-9_.-]+", part) or part in {".", ".."}
           or part.endswith((".", " ")) for part in parts):
        raise AudioGenerationStorageError("invalid audio storage path")
    return value


def _local_path(relative_path: str) -> Path:
    safe_rel = _safe_relative_path(relative_path)
    root = AUDIO_GENERATION_DIR.resolve()
    target = root
    for part in safe_rel.split("/"):
        target = target / part
        if target.is_symlink() or target.is_junction():
            raise AudioGenerationStorageError("audio storage links are not allowed")
    try:
        target.resolve().relative_to(root)
    except ValueError as exc:
        raise AudioGenerationStorageError("invalid audio storage path") from exc
    return target


def _max_result_bytes() -> int:
    try:
        value = int(os.getenv("AUDIO_GENERATION_MAX_RESULT_MB", "200"))
    except ValueError:
        return DEFAULT_MAX_RESULT_BYTES
    return max(1, value) * 1024 * 1024


def _extension(url: str, content_type: str) -> str:
    suffix = PurePosixPath(urlsplit(url).path).suffix.lower()
    if suffix in AUDIO_EXTENSIONS:
        return suffix
    content = content_type.lower()
    for token, extension in (
        ("mpeg", ".mp3"), ("wav", ".wav"), ("ogg", ".ogg"),
        ("opus", ".opus"), ("mp4", ".m4a"), ("aac", ".aac"), ("flac", ".flac"),
    ):
        if token in content:
            return extension
    return ".mp3"


def _safe_task_id(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "_", _clean(value, "task", 191))[:120] or "task"


def _task_stem(task_id: str) -> str:
    return f"{_safe_task_id(task_id)}-{hashlib.sha256(task_id.encode('utf-8')).hexdigest()[:12]}"


def _owner_scope(value: str) -> str:
    return hashlib.sha256(_clean(value, "anonymous", 191).encode("utf-8")).hexdigest()[:24]


def _required_id(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 191:
        raise AudioGenerationStorageError("audio owner and task identifiers are required")
    return value.strip()


def _settings() -> dict[str, object]:
    return config.get_video_upload_settings()


def _generation_settings() -> dict[str, object]:
    return config.get_audio_generation_settings()


def _result_prefix() -> str:
    value = _generation_settings().get("result_storage_prefix", "raw-photo/audio-results")
    return _safe_relative_path(str(value)) if value else ""


def _object_key(relative_path: str) -> str:
    prefix = _result_prefix()
    return f"{prefix}/{relative_path}" if prefix else relative_path


def _oss_bucket(settings: Mapping[str, object]) -> str:
    bucket = _clean(settings.get("oss_bucket"))
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket):
        raise AudioGenerationStorageError("OSS audio result bucket is not configured")
    return bucket


def _oss_client(settings: dict[str, object]) -> Minio:
    endpoint = _clean(settings.get("oss_endpoint")).rstrip("/")
    access_key = _clean(settings.get("oss_access_key"))
    secret_key = _clean(settings.get("oss_secret_key"))
    _oss_bucket(settings)
    if not endpoint or not access_key or not secret_key:
        raise AudioGenerationStorageError("OSS audio result storage is not configured")
    parsed = urlsplit(endpoint if "://" in endpoint else f"https://{endpoint}")
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username
            or parsed.password or parsed.path or parsed.query or parsed.fragment):
        raise AudioGenerationStorageError("invalid OSS audio result endpoint")
    return Minio(
        parsed.netloc,
        access_key=access_key,
        secret_key=secret_key,
        secure=parsed.scheme == "https" if "://" in endpoint else bool(settings.get("oss_secure", True)),
        region=_clean(settings.get("oss_region")) or None,
    )


def _http_url(value: str) -> tuple[str, str, int]:
    if (not isinstance(value, str) or not value or len(value) > 8192
            or re.search(r"[\s\\\x00-\x1f\x7f]", value)):
        raise AudioGenerationStorageError("invalid audio result URL")
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if (parsed.scheme not in {"http", "https"} or not host or parsed.username is not None
                or parsed.password is not None or parsed.fragment or "%" in host or port not in {80, 443}):
            raise ValueError("unsafe URL")
        host = host.encode("idna").decode("ascii").lower()
        if host.rstrip(".") == "localhost" or host.rstrip(".").endswith((".localhost", ".local", ".internal")):
            raise ValueError("local host")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and not _public_address(address):
            raise ValueError("non-public address")
        authority = f"[{host}]" if ":" in host else host
        if parsed.port is not None:
            authority += f":{port}"
        return urlunsplit((parsed.scheme, authority, parsed.path, parsed.query, "")), host, port
    except (ValueError, UnicodeError) as exc:
        raise AudioGenerationStorageError("invalid or non-public audio result URL") from exc


def _public_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if not address.is_global or address.is_multicast:
        return False
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped:
            return _public_address(address.ipv4_mapped)
        # Avoid transition mechanisms with an embedded, potentially private IPv4 target.
        if address.sixtofour or address.teredo or address in ipaddress.ip_network("64:ff9b::/96"):
            return False
    return True


def _download_target(value: str) -> tuple[str, list[str]]:
    url, host, port = _http_url(value)
    try:
        addresses = {ipaddress.ip_address(info[4][0]) for info in
                     socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)}
    except (OSError, ValueError) as exc:
        raise AudioGenerationStorageError("audio result host could not be resolved") from exc
    if not addresses or any(not _public_address(address) for address in addresses):
        raise AudioGenerationStorageError("audio result host must resolve only to public addresses")
    encoded = ",".join(f"[{address}]" if address.version == 6 else str(address)
                       for address in sorted(addresses, key=str))
    # libcurl does not resolve IP literals, but DNS names must use this vetted answer.
    return url, [] if ":" in host else [f"{host}:{port}:{encoded}"]


@contextmanager
def _download(url: str) -> Iterator[_DownloadedAudio]:
    limit = _max_result_bytes()
    deadline = time.monotonic() + DOWNLOAD_TIMEOUT_SECS
    with tempfile.TemporaryFile(mode="w+b") as payload:
        for redirect in range(MAX_REDIRECTS + 1):
            url, resolution = _download_target(url)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AudioGenerationStorageError("audio result download timed out")
            payload.seek(0)
            payload.truncate()
            size = 0
            digest = hashlib.sha256()
            write_error = ""

            def write_chunk(chunk: bytes) -> int:
                nonlocal size, write_error
                if size + len(chunk) > limit:
                    write_error = "audio result exceeds the configured storage limit"
                    return CURL_WRITEFUNC_ERROR
                try:
                    payload.write(chunk)
                except OSError:
                    write_error = "audio result temporary storage failed"
                    return CURL_WRITEFUNC_ERROR
                size += len(chunk)
                digest.update(chunk)
                return len(chunk)

            try:
                # Synchronous callbacks avoid curl_cffi stream=True's unbounded queue.
                # Disable proxies: remote DNS resolution would bypass the pinned IPs.
                with requests.Session(trust_env=False, curl_options={
                    CurlOpt.RESOLVE: resolution,
                    CurlOpt.PROXY: "",
                    CurlOpt.NOPROXY: "*",
                    CurlOpt.PROTOCOLS_STR: "http,https",
                    CurlOpt.MAXFILESIZE_LARGE: limit,
                }) as session:
                    response = session.get(
                        url,
                        headers={"Accept": "audio/*,application/octet-stream;q=0.8", "User-Agent": "gmkraw audio generation"},
                        timeout=(min(15, remaining), remaining),
                        allow_redirects=False,
                        accept_encoding="identity",
                        content_callback=write_chunk,
                    )
                    try:
                        status = response.status_code
                        headers = dict(response.headers)
                    finally:
                        response.close()
            except Exception as exc:
                # Do not expose signed upstream URLs, credentials or response bodies.
                message = write_error or "audio result download failed"
                logger.warning("Audio result download failed (%s)", type(exc).__name__)
                raise AudioGenerationStorageError(message) from exc
            if write_error:
                raise AudioGenerationStorageError(write_error)
            headers = {key.lower(): value for key, value in headers.items()}
            if status in {301, 302, 303, 307, 308}:
                if redirect >= MAX_REDIRECTS or not headers.get("location"):
                    raise AudioGenerationStorageError("audio result redirect limit exceeded or location missing")
                next_url = urljoin(url, headers["location"])
                if urlsplit(url).scheme == "https" and urlsplit(next_url).scheme != "https":
                    raise AudioGenerationStorageError("audio result redirect must not downgrade HTTPS")
                url = next_url
                continue
            if status != 200:
                raise AudioGenerationStorageError(f"audio result download failed: HTTP {status}")
            if not size:
                raise AudioGenerationStorageError("audio result download returned empty content")
            declared = headers.get("content-length")
            if declared is not None and (not str(declared).isdigit() or int(declared) != size):
                raise AudioGenerationStorageError("audio result download length mismatch")
            content_type = str(headers.get("content-type", "audio/mpeg")).split(";", 1)[0].strip().lower()
            if not content_type.startswith("audio/") and content_type != "application/octet-stream":
                raise AudioGenerationStorageError("audio result download did not return audio content")
            payload.seek(0)
            yield _DownloadedAudio(payload, size, digest.hexdigest(), content_type, url)
            return


def _owned_relative_path(path: str, owner_id: str, task_id: str) -> str:
    _safe_relative_path(path)
    parts = path.split("/")
    if len(parts) != 3 or parts[:2] != ["generated", _owner_scope(owner_id)]:
        raise AudioGenerationStorageError("audio storage path does not belong to this owner")
    suffix = PurePosixPath(parts[2]).suffix
    prefixes = (_task_stem(task_id), _safe_task_id(task_id))  # Also allow existing persisted results.
    if suffix not in AUDIO_EXTENSIONS or not any(
        re.fullmatch(re.escape(prefix) + r"-[0-9a-f]{24}" + re.escape(suffix), parts[2]) for prefix in prefixes
    ):
        raise AudioGenerationStorageError("audio storage path does not belong to this task")
    return path


def _stored_path(task: Mapping[str, object]) -> tuple[str, str]:
    owner_id, task_id = _required_id(task.get("owner_id")), _required_id(task.get("id"))
    storage = str(task.get("storage") or "remote")
    path = str(task.get("storage_rel") or "")
    if storage == "local":
        return storage, _owned_relative_path(path, owner_id, task_id)
    if storage == "oss":
        prefix = _result_prefix()
        # Earlier records stored only generated/<owner>/<file> rather than the object key.
        if prefix and path.startswith(prefix + "/"):
            relative_path = path[len(prefix) + 1:]
        else:
            relative_path = path
        _owned_relative_path(relative_path, owner_id, task_id)
        key = _object_key(relative_path)
        reference = str(task.get("audio_url") or "")
        if reference.startswith("audio-storage:"):
            expected = f"audio-storage://oss/{_oss_bucket(_settings())}/{quote(key, safe='/')}"
            if reference != expected:
                raise AudioGenerationStorageError("audio storage reference does not match configured bucket and path")
        return storage, key
    if storage != "remote":
        raise AudioGenerationStorageError("unknown audio result storage")
    return storage, ""


class AudioGenerationStorageService:
    def store_remote_audio(self, url: str, *, owner_id: str, task_id: str, base_url: str = "") -> StoredAudio:
        owner_id, task_id = _required_id(owner_id), _required_id(task_id)
        storage_settings = _settings()
        use_oss = bool(storage_settings.get("enabled"))
        # Validate the configured destination before downloading an expensive result.
        client = _oss_client(storage_settings) if use_oss else None
        with _download(url) as downloaded:
            suffix = _extension(downloaded.source_url, downloaded.content_type)
            relative_path = f"generated/{_owner_scope(owner_id)}/{_task_stem(task_id)}-{downloaded.digest[:24]}{suffix}"
            if client is not None:
                bucket, key = _oss_bucket(storage_settings), _object_key(relative_path)
                try:
                    client.put_object(
                        bucket, key, downloaded.stream, length=downloaded.size,
                        content_type=downloaded.content_type,
                        part_size=10 * 1024 * 1024, num_parallel_uploads=1,
                    )
                except Exception as exc:
                    logger.error("Audio result OSS upload failed; no local fallback (%s)", type(exc).__name__)
                    raise AudioGenerationStorageError("OSS audio result upload failed; retry storage without regenerating") from exc
                return StoredAudio(key, f"audio-storage://oss/{bucket}/{quote(key, safe='/')}", "oss", downloaded.size)

            target = _local_path(relative_path)
            temporary_path = None
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(mode="wb", dir=target.parent, suffix=".part", delete=False) as output:
                    temporary_path = Path(output.name)
                    shutil.copyfileobj(downloaded.stream, output, length=1024 * 1024)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary_path, target)
            except OSError as exc:
                raise AudioGenerationStorageError("local audio result storage failed") from exc
            finally:
                if temporary_path is not None:
                    temporary_path.unlink(missing_ok=True)
            return StoredAudio(relative_path, f"audio-storage://local/{quote(relative_path, safe='/')}", "local", downloaded.size)

    def resolve_result_url(self, task: Mapping[str, object], *, base_url: str = "") -> str:
        """Call only after task authorization; never persist the returned capability."""
        storage, path = _stored_path(task)
        if storage == "remote":
            url = str(task.get("audio_url") or "")
            return _http_url(url)[0] if url else ""
        if storage == "oss":
            settings = _settings()
            try:
                return _oss_client(settings).presigned_get_object(
                    _oss_bucket(settings), path, expires=timedelta(seconds=result_url_ttl()),
                )
            except Exception as exc:
                raise AudioGenerationStorageError("OSS audio result signing failed") from exc
        expires = int(time.time()) + result_url_ttl()
        try:
            signature = sign_audio_path(path, expires)
        except ValueError as exc:
            raise AudioGenerationStorageError(str(exc)) from exc
        query = urlencode({"expires": expires, "signature": signature})
        return f"{(base_url or config.base_url).rstrip('/')}/audio-assets/{quote(path, safe='/')}?{query}"

    def delete_stored_audio(self, task: Mapping[str, object], *, owner_id: str) -> bool:
        """Delete only this owner's persisted result; missing objects are idempotent.

        The caller must retain metadata and retry cleanup if this method raises.
        Remote upstream URLs are never deleted, fetched or converted to object keys.
        """
        if _required_id(owner_id) != _required_id(task.get("owner_id")):
            raise AudioGenerationStorageError("audio result does not belong to this owner")
        storage, path = _stored_path(task)
        if storage == "remote":
            return False
        try:
            if storage == "local":
                _local_path(path).unlink(missing_ok=True)
            else:
                settings = _settings()
                _oss_client(settings).remove_object(_oss_bucket(settings), path)
        except S3Error as exc:
            if exc.code not in {"NoSuchKey", "NoSuchObject"}:
                logger.error("Audio result OSS cleanup failed (%s)", type(exc).__name__)
                raise AudioGenerationStorageError("OSS audio result cleanup failed") from exc
        except AudioGenerationStorageError:
            raise
        except Exception as exc:
            logger.error("Audio result cleanup failed (%s)", type(exc).__name__)
            raise AudioGenerationStorageError("audio result cleanup failed") from exc
        return True

    def local_file(self, relative_path: str, *, expires: int | str = 0, signature: str = "") -> Path:
        try:
            path = _safe_relative_path(relative_path)
            if not verify_audio_signature(path, expires, signature):
                raise HTTPException(status_code=403, detail="audio link is invalid or expired")
            target = _local_path(path)
        except AudioGenerationStorageError as exc:
            raise HTTPException(status_code=404, detail="audio not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=503, detail="audio signing is not configured") from exc
        if not target.is_file() or target.suffix not in AUDIO_EXTENSIONS or not path.startswith("generated/"):
            raise HTTPException(status_code=404, detail="audio not found")
        return target


audio_generation_storage_service = AudioGenerationStorageService()
