from __future__ import annotations

import hashlib
import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
import hmac
import io
import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Lock
from urllib.parse import quote, urlencode, urlparse

from curl_cffi import CurlMime, requests
from fastapi import HTTPException
from minio import Minio
from minio.error import S3Error
from PIL import Image

from services.config import DATA_DIR, config

IMAGE_INDEX_FILE = DATA_DIR / "image_index.json"
IMAGE_INDEX_LOCK = Lock()
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif"}
REMOTE_MODES = {"webdav", "minio", "qiniu", "both"}
QINIU_REGION_HOSTS = {
    "z0": "https://rs-z0.qiniuapi.com",
    "z1": "https://rs-z1.qiniuapi.com",
    "z2": "https://rs-z2.qiniuapi.com",
    "na0": "https://rs-na0.qiniuapi.com",
    "as0": "https://rs-as0.qiniuapi.com",
}


class ImageStorageError(RuntimeError):
    pass


@dataclass(frozen=True)
class StoredImage:
    rel: str
    url: str
    storage: str
    size: int


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


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _safe_relative_path(path: str) -> str:
    value = str(path or "").strip().replace("\\", "/").lstrip("/")
    if not value:
        raise HTTPException(status_code=404, detail="image not found")
    parts = Path(value).parts
    if any(part in {"", ".", ".."} for part in parts):
        raise HTTPException(status_code=404, detail="image not found")
    return Path(*parts).as_posix()


def _image_dimensions(payload: bytes) -> tuple[int, int] | None:
    try:
        with Image.open(io.BytesIO(payload)) as image:
            return image.size
    except Exception:
        return None


def _is_image_rel(path: str) -> bool:
    try:
        safe_rel = _safe_relative_path(path)
    except HTTPException:
        return False
    return Path(safe_rel).suffix.lower() in IMAGE_EXTENSIONS


def _local_image_path(relative_path: str) -> Path:
    rel = _safe_relative_path(relative_path)
    root = config.images_dir.resolve()
    path = (root / rel).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="image not found") from exc
    return path


def _read_json_object(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _write_json_object(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def _remote_object_path(root_path: str, rel: str) -> str:
    root = _clean(root_path).strip("/")
    safe_rel = _safe_relative_path(rel)
    return "/".join(part for part in (root, safe_rel) if part)


def _remote_enabled(mode: str) -> bool:
    return _clean(mode).lower() in REMOTE_MODES


def _local_enabled(mode: str) -> bool:
    return _clean(mode).lower() in {"local", "both", ""}


def _item_remote_provider(item: dict[str, object], fallback: str = "") -> str:
    provider = _clean(item.get("remote_provider")).lower()
    if provider in {"webdav", "minio", "qiniu"}:
        return provider
    if item.get("minio"):
        return "minio"
    if item.get("qiniu"):
        return "qiniu"
    if item.get("webdav"):
        return "webdav"
    if item.get("remote"):
        fallback = _clean(fallback).lower()
        return fallback if fallback in {"webdav", "minio", "qiniu"} else "webdav"
    return ""


def _has_remote(item: dict[str, object]) -> bool:
    return bool(item.get("remote") or item.get("webdav") or item.get("minio") or item.get("qiniu"))


def _storage_label(local: bool, remote: bool, provider: str) -> str:
    if local and remote:
        return "both"
    if remote:
        return provider if provider in {"webdav", "minio", "qiniu"} else "remote"
    return "local"


def _item_timestamp(rel: str, item: dict[str, object]) -> float:
    created_at = _clean(item.get("created_at"))
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(created_at[:19], fmt).timestamp()
        except ValueError:
            continue
    parts = rel.split("/")
    if len(parts) >= 3:
        try:
            return datetime.strptime("-".join(parts[:3]), "%Y-%m-%d").timestamp()
        except ValueError:
            return 0.0
    return 0.0


def _cleanup_empty_dirs(root: Path) -> None:
    for path in sorted((p for p in root.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        try:
            path.rmdir()
        except OSError:
            pass


class WebDAVClient:
    def __init__(self, settings: dict[str, object]):
        self.url = _clean(settings.get("webdav_url")).rstrip("/")
        self.username = _clean(settings.get("webdav_username"))
        self.password = _clean(settings.get("webdav_password"))
        self.root_path = _clean(settings.get("webdav_root_path")).strip("/")
        self.session = requests.Session()

    def _auth_kwargs(self) -> dict[str, object]:
        return {"auth": (self.username, self.password)} if self.username or self.password else {}

    def _request(self, method: str, url: str, **kwargs):
        response = self.session.request(method, url, timeout=30, **self._auth_kwargs(), **kwargs)
        if response.status_code >= 400 and not (method == "MKCOL" and response.status_code in {405}):
            raise ImageStorageError(f"WebDAV {method} failed: HTTP {response.status_code}")
        return response

    def remote_url(self, rel: str = "") -> str:
        parts = [part for part in [self.root_path, _safe_relative_path(rel) if rel else ""] if part]
        encoded = "/".join(quote(part, safe="") for item in parts for part in item.split("/") if part)
        return f"{self.url}/{encoded}" if encoded else self.url

    def ensure_dirs(self, rel: str) -> None:
        parts = [part for part in [self.root_path, Path(_safe_relative_path(rel)).parent.as_posix()] if part and part != "."]
        current = self.url
        for item in "/".join(parts).split("/"):
            if not item:
                continue
            current = f"{current}/{quote(item, safe='')}"
            response = self.session.request("MKCOL", current, timeout=30, **self._auth_kwargs())
            if response.status_code in {201, 405}:
                continue
            if response.status_code >= 400:
                raise ImageStorageError(f"WebDAV MKCOL failed: HTTP {response.status_code}")

    def put(self, rel: str, payload: bytes, content_type: str = "image/png") -> str:
        self.ensure_dirs(rel)
        url = self.remote_url(rel)
        self._request("PUT", url, data=payload, headers={"Content-Type": content_type})
        return url

    def get(self, rel: str) -> bytes:
        response = self._request("GET", self.remote_url(rel))
        return bytes(response.content)

    def delete(self, rel: str) -> bool:
        response = self.session.request("DELETE", self.remote_url(rel), timeout=30, **self._auth_kwargs())
        if response.status_code in {200, 202, 204, 404}:
            return response.status_code != 404
        raise ImageStorageError(f"WebDAV DELETE failed: HTTP {response.status_code}")

    def test(self) -> dict[str, object]:
        if not self.url:
            return {"ok": False, "status": 0, "error": "WebDAV URL is required"}
        if urlparse(self.url).scheme not in {"http", "https"}:
            return {"ok": False, "status": 0, "error": "invalid WebDAV URL"}
        test_rel = ".gmkraw_webdav_test.txt"
        try:
            self.put(test_rel, b"gmkraw webdav test\n", content_type="text/plain")
            self.delete(test_rel)
            return {"ok": True, "status": 200, "error": None}
        except ImageStorageError as exc:
            return {"ok": False, "status": 0, "error": str(exc)}
        except Exception as exc:
            return {"ok": False, "status": 0, "error": str(exc) or exc.__class__.__name__}
        finally:
            self.session.close()


class MinIOClient:
    def __init__(self, settings: dict[str, object]):
        endpoint = _clean(settings.get("minio_endpoint")).rstrip("/")
        access_key = _clean(settings.get("minio_access_key"))
        secret_key = _clean(settings.get("minio_secret_key"))
        self.bucket = _clean(settings.get("minio_bucket"))
        self.region = _clean(settings.get("minio_region")) or None
        self.root_path = _clean(settings.get("minio_root_path")).strip("/")
        if not endpoint or not access_key or not secret_key or not self.bucket:
            raise ImageStorageError("MinIO settings are incomplete")

        parsed = urlparse(endpoint)
        if parsed.scheme in {"http", "https"}:
            endpoint = parsed.netloc
            secure = parsed.scheme == "https"
        else:
            secure = _bool(settings.get("minio_secure"), True)
        if not endpoint:
            raise ImageStorageError("invalid MinIO endpoint")

        self.client = Minio(
            endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
            region=self.region,
        )

    def object_name(self, rel: str) -> str:
        return _remote_object_path(self.root_path, rel)

    def _ensure_bucket(self) -> None:
        if not self.client.bucket_exists(self.bucket):
            self.client.make_bucket(self.bucket, location=self.region)

    def put(self, rel: str, payload: bytes, content_type: str = "image/png") -> str:
        self._ensure_bucket()
        object_name = self.object_name(rel)
        self.client.put_object(
            self.bucket,
            object_name,
            io.BytesIO(payload),
            length=len(payload),
            content_type=content_type,
        )
        return f"minio://{self.bucket}/{object_name}"

    def get(self, rel: str) -> bytes:
        object_name = self.object_name(rel)
        response = None
        try:
            response = self.client.get_object(self.bucket, object_name)
            return bytes(response.read())
        except S3Error as exc:
            if exc.code in {"NoSuchBucket", "NoSuchKey"}:
                raise HTTPException(status_code=404, detail="image not found") from exc
            raise ImageStorageError(f"MinIO GET failed: {exc.code}") from exc
        finally:
            if response is not None:
                response.close()
                response.release_conn()

    def delete(self, rel: str) -> bool:
        object_name = self.object_name(rel)
        try:
            self.client.remove_object(self.bucket, object_name)
            return True
        except S3Error as exc:
            if exc.code in {"NoSuchBucket", "NoSuchKey"}:
                return False
            raise ImageStorageError(f"MinIO DELETE failed: {exc.code}") from exc

    def test(self) -> dict[str, object]:
        test_rel = ".gmkraw_minio_test.txt"
        try:
            self.put(test_rel, b"gmkraw minio test\n", content_type="text/plain")
            self.delete(test_rel)
            return {"ok": True, "status": 200, "error": None}
        except ImageStorageError as exc:
            return {"ok": False, "status": 0, "error": str(exc)}
        except Exception as exc:
            return {"ok": False, "status": 0, "error": str(exc) or exc.__class__.__name__}


class QiniuClient:
    def __init__(self, settings: dict[str, object]):
        self.access_key = _clean(settings.get("qiniu_access_key"))
        self.secret_key = _clean(settings.get("qiniu_secret_key"))
        self.bucket = _clean(settings.get("qiniu_bucket"))
        self.domain = _clean(settings.get("qiniu_domain")).rstrip("/")
        self.upload_url = _clean(settings.get("qiniu_upload_url")).rstrip("/")
        self.prefix = _clean(settings.get("qiniu_prefix")).strip("/")
        self.region = _clean(settings.get("qiniu_region")).lower() or "z0"
        self.private = _bool(settings.get("qiniu_private"), False)
        if not self.access_key or not self.secret_key or not self.bucket or not self.domain or not self.upload_url:
            raise ImageStorageError("Qiniu settings are incomplete")

    @staticmethod
    def _urlsafe(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).decode("ascii")

    def object_name(self, rel: str) -> str:
        safe_rel = _safe_relative_path(rel)
        return "/".join(part for part in (self.prefix, safe_rel) if part)

    def _public_url(self, object_name: str) -> str:
        domain = self.domain
        if not domain.startswith(("http://", "https://")):
            domain = f"https://{domain}"
        return f"{domain}/{quote(object_name, safe='/')}"

    def _download_url(self, object_name: str) -> str:
        public_url = self._public_url(object_name)
        if not self.private:
            return public_url
        deadline = int(time.time()) + 3600
        parsed = urlparse(public_url)
        signed_path = parsed.path
        sign_data = f"{signed_path}?e={deadline}".encode("utf-8")
        signature = self._urlsafe(hmac.new(self.secret_key.encode("utf-8"), sign_data, hashlib.sha1).digest())
        token = f"{self.access_key}:{signature}"
        return f"{public_url}?{urlencode({'e': deadline, 'token': token})}"

    def _upload_token(self, object_name: str) -> str:
        policy = {
            "scope": f"{self.bucket}:{object_name}",
            "deadline": int(time.time()) + 3600,
        }
        encoded_policy = self._urlsafe(json.dumps(policy, separators=(",", ":")).encode("utf-8"))
        signature = self._urlsafe(
            hmac.new(self.secret_key.encode("utf-8"), encoded_policy.encode("utf-8"), hashlib.sha1).digest()
        )
        return f"{self.access_key}:{signature}:{encoded_policy}"

    def put(self, rel: str, payload: bytes, content_type: str = "image/png") -> str:
        object_name = self.object_name(rel)
        try:
            from services.reference_image_uploader import _upload_to_qiniu_sdk

            return _upload_to_qiniu_sdk(
                payload,
                Path(rel).name,
                content_type,
                key=object_name,
                timeout=30,
                upload_settings={
                    "qiniu_access_key": self.access_key,
                    "qiniu_secret_key": self.secret_key,
                    "qiniu_bucket": self.bucket,
                    "qiniu_domain": self.domain,
                    "qiniu_upload_url": self.upload_url,
                },
            )
        except ImportError:
            pass

        last_error = ""
        for attempt in range(1, 4):
            multipart = CurlMime()
            try:
                multipart.addpart(name="token", data=self._upload_token(object_name))
                multipart.addpart(name="key", data=object_name)
                multipart.addpart(name="file", filename=Path(rel).name, content_type=content_type, data=payload)
                response = requests.post(self.upload_url, multipart=multipart, timeout=30)
                if 200 <= response.status_code < 300:
                    return self._public_url(object_name)
                last_error = f"HTTP {response.status_code}"
                if response.status_code not in {408, 429, 500, 502, 503, 504}:
                    break
            except Exception as exc:
                last_error = str(exc)
            finally:
                multipart.close()
            if attempt < 3:
                time.sleep(0.8 * attempt)
        raise ImageStorageError(f"Qiniu upload failed after 3 attempts: {last_error or 'no response'}")

    def get(self, rel: str) -> bytes:
        object_name = self.object_name(rel)
        try:
            response = requests.get(self._download_url(object_name), timeout=60)
        except Exception as exc:
            raise ImageStorageError(f"Qiniu download failed: {exc}") from exc
        if response.status_code == 404:
            raise HTTPException(status_code=404, detail="image not found")
        if response.status_code >= 400:
            raise ImageStorageError(f"Qiniu download failed: HTTP {response.status_code}")
        return bytes(response.content)

    def delete(self, rel: str) -> bool:
        object_name = self.object_name(rel)
        entry = self._urlsafe(f"{self.bucket}:{object_name}".encode("utf-8"))
        path = f"/delete/{entry}"
        host = QINIU_REGION_HOSTS.get(self.region, QINIU_REGION_HOSTS["z0"])
        signature = self._urlsafe(hmac.new(self.secret_key.encode("utf-8"), f"{path}\n".encode("utf-8"), hashlib.sha1).digest())
        try:
            response = requests.post(
                f"{host}{path}",
                headers={"Authorization": f"QBox {self.access_key}:{signature}"},
                timeout=60,
            )
        except Exception as exc:
            raise ImageStorageError(f"Qiniu delete failed: {exc}") from exc
        if response.status_code in {200, 612}:
            return response.status_code == 200
        raise ImageStorageError(f"Qiniu delete failed: HTTP {response.status_code}")

    def test(self) -> dict[str, object]:
        rel = ".gmkraw_qiniu_test.txt"
        try:
            self.put(rel, b"gmkraw qiniu test\n", "text/plain")
            self.delete(rel)
            return {"ok": True, "status": 200, "error": None}
        except ImageStorageError as exc:
            return {"ok": False, "status": 0, "error": str(exc)}
        except Exception as exc:
            return {"ok": False, "status": 0, "error": str(exc) or exc.__class__.__name__}


class ImageStorageService:
    def __init__(self, index_file: Path = IMAGE_INDEX_FILE):
        self.index_file = index_file
        self._index_lock = IMAGE_INDEX_LOCK

    def settings(self) -> dict[str, object]:
        return config.get_image_storage_settings()

    def mode(self) -> str:
        return _clean(self.settings().get("mode")) or "local"

    def remote_provider(self, settings: dict[str, object] | None = None) -> str:
        item = settings or self.settings()
        mode = _clean(item.get("mode")).lower()
        if mode == "minio":
            return "minio"
        if mode == "webdav":
            return "webdav"
        provider = _clean(item.get("provider")).lower()
        if mode == "qiniu":
            return "qiniu"
        return provider if provider in {"webdav", "minio", "qiniu"} else "webdav"

    def _remote_client(self, provider: str, settings: dict[str, object] | None = None):
        item = settings or self.settings()
        if provider == "minio":
            return MinIOClient(item)
        if provider == "qiniu":
            return QiniuClient(item)
        return WebDAVClient(item)

    def _load_index(self) -> dict[str, dict[str, object]]:
        raw = _read_json_object(self.index_file)
        items = raw.get("items")
        if not isinstance(items, dict):
            return {}
        return {str(key): value for key, value in items.items() if isinstance(value, dict)}

    def _load_clean_index(self) -> dict[str, dict[str, object]]:
        items = self._load_index()
        return {rel: item for rel, item in items.items() if _is_image_rel(rel)}

    def _save_index(self, items: dict[str, dict[str, object]]) -> None:
        _write_json_object(self.index_file, {"items": items})

    def _public_url(self, rel: str, base_url: str | None = None) -> str:
        settings = self.settings()
        public_base_url = _clean(settings.get("public_base_url"))
        if public_base_url:
            return f"{public_base_url.rstrip('/')}/{_safe_relative_path(rel)}"
        return f"{(base_url or config.base_url).rstrip('/')}/images/{_safe_relative_path(rel)}"

    def make_relative_path(self, image_data: bytes) -> str:
        file_hash = hashlib.md5(image_data).hexdigest()
        filename = f"{int(time.time())}_{file_hash}.png"
        relative_dir = Path(time.strftime("%Y"), time.strftime("%m"), time.strftime("%d"))
        return f"{relative_dir.as_posix()}/{filename}"

    def save(
        self,
        image_data: bytes,
        base_url: str | None = None,
        *,
        relative_path: str | None = None,
        asset_type: str = "generated",
        cleanup: bool = True,
    ) -> StoredImage:
        if cleanup:
            self.cleanup_old_images()
        rel = _safe_relative_path(relative_path) if relative_path else self.make_relative_path(image_data)
        if Path(rel).suffix.lower() not in IMAGE_EXTENSIONS:
            raise ImageStorageError("image storage path must use a supported image extension")
        settings = self.settings()
        mode = _clean(settings.get("mode")).lower() or "local"
        if mode not in {"local", "webdav", "minio", "qiniu", "both"}:
            mode = "local"
        remote_provider = self.remote_provider(settings)
        stored_local = False
        stored_remote = False
        remote_url = ""

        if _local_enabled(mode):
            path = _local_image_path(rel)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(image_data)
            stored_local = True

        if _remote_enabled(mode):
            remote_url = self._remote_client(remote_provider, settings).put(rel, image_data)
            stored_remote = True

        dimensions = _image_dimensions(image_data)
        item = {
            "rel": rel,
            "path": rel,
            "name": Path(rel).name,
            "date": "-".join(rel.split("/")[:3]),
            "size": len(image_data),
            "created_at": _now_iso(),
            "storage": _storage_label(stored_local, stored_remote, remote_provider),
            "local": stored_local,
            "remote": stored_remote,
            "remote_provider": remote_provider if stored_remote else "",
            "webdav": stored_remote and remote_provider == "webdav",
            "minio": stored_remote and remote_provider == "minio",
            "qiniu": stored_remote and remote_provider == "qiniu",
            "remote_url": remote_url,
            "asset_type": _clean(asset_type) or "generated",
        }
        if dimensions:
            item["width"], item["height"] = dimensions
        with self._index_lock:
            items = self._load_clean_index()
            items[rel] = item
            self._save_index(items)
        return StoredImage(rel=rel, url=self._public_url(rel, base_url), storage=str(item["storage"]), size=len(image_data))

    def save_task_asset(
        self,
        image_data: bytes,
        *,
        owner_id: str,
        task_id: str,
        asset_index: str,
        asset_type: str,
        filename: str = "image.png",
        mime_type: str = "image/png",
        base_url: str | None = None,
    ) -> StoredImage:
        digest = hashlib.sha256(image_data).hexdigest()
        scope = hashlib.sha256(f"{owner_id}:{task_id}".encode("utf-8")).hexdigest()[:24]
        suffix = Path(str(filename or "")).suffix.lower()
        if suffix not in IMAGE_EXTENSIONS:
            suffix = ".png" if "png" in str(mime_type or "").lower() else ".jpg"
        safe_index = hashlib.sha256(str(asset_index).encode("utf-8")).hexdigest()[:12]
        relative_path = f"task-assets/{_clean(asset_type) or 'input'}/{scope}/{safe_index}-{digest[:32]}{suffix}"
        return self.save(
            image_data,
            base_url,
            relative_path=relative_path,
            asset_type=_clean(asset_type) or "input",
            cleanup=False,
        )

    def get_bytes(self, rel: str) -> bytes:
        safe_rel = _safe_relative_path(rel)
        if not _is_image_rel(safe_rel):
            raise HTTPException(status_code=404, detail="image not found")
        path = _local_image_path(safe_rel)
        if path.is_file():
            return path.read_bytes()
        item = self._load_clean_index().get(safe_rel, {})
        if _has_remote(item):
            settings = self.settings()
            provider = _item_remote_provider(item, self.remote_provider(settings))
            return self._remote_client(provider, settings).get(safe_rel)
        raise HTTPException(status_code=404, detail="image not found")

    def exists(self, rel: str) -> bool:
        safe_rel = _safe_relative_path(rel)
        if not _is_image_rel(safe_rel):
            return False
        if _local_image_path(safe_rel).is_file():
            return True
        item = self._load_clean_index().get(safe_rel, {})
        return _has_remote(item)

    def has_local(self, rel: str) -> bool:
        safe_rel = _safe_relative_path(rel)
        return _is_image_rel(safe_rel) and _local_image_path(safe_rel).is_file()

    def get_item(self, rel: str) -> dict[str, object]:
        safe_rel = _safe_relative_path(rel)
        item = self._load_clean_index().get(safe_rel, {})
        return dict(item) if isinstance(item, dict) else {}

    def cleanup_old_images(self) -> int:
        try:
            retention_days = max(1, int(getattr(config, "image_retention_days", 30)))
        except Exception:
            retention_days = 30
        cutoff = time.time() - retention_days * 86400
        removed = 0
        settings = self.settings()
        remote_clients: dict[str, object] = {}
        with self._index_lock:
            indexed = self._load_clean_index()
            changed = False
            for rel, item in list(indexed.items()):
                path = _local_image_path(rel)
                item_ts = _item_timestamp(rel, item)
                if path.is_file():
                    try:
                        item_ts = max(item_ts, path.stat().st_mtime)
                    except Exception:
                        pass
                if item_ts >= cutoff:
                    continue
                local_exists = path.is_file()
                if local_exists:
                    try:
                        path.unlink()
                        removed += 1
                    except Exception:
                        pass
                remote = _has_remote(item)
                provider = _item_remote_provider(item, self.remote_provider(settings))
                if remote:
                    try:
                        client = remote_clients.get(provider)
                        if client is None:
                            client = self._remote_client(provider, settings)
                            remote_clients[provider] = client
                        if client.delete(rel):
                            removed += 1
                    except Exception:
                        indexed[rel] = {
                            **item,
                            "local": False,
                            "storage": _storage_label(False, True, provider),
                            "remote": True,
                            "remote_provider": provider,
                            "webdav": provider == "webdav",
                            "minio": provider == "minio",
                            "qiniu": provider == "qiniu",
                        }
                        changed = True
                        continue
                indexed.pop(rel, None)
                changed = True
            for path in config.images_dir.rglob("*"):
                if not path.is_file() or not _is_image_rel(path.name):
                    continue
                rel = path.relative_to(config.images_dir).as_posix()
                if rel in indexed:
                    continue
                try:
                    if path.stat().st_mtime >= cutoff:
                        continue
                except Exception:
                    continue
                try:
                    path.unlink()
                    removed += 1
                except Exception:
                    pass
                changed = True
            if changed:
                self._save_index(indexed)
        _cleanup_empty_dirs(config.images_dir)
        return removed

    def list_items(self, base_url: str, start_date: str = "", end_date: str = "") -> list[dict[str, object]]:
        with self._index_lock:
            indexed = self._load_clean_index()
            root = config.images_dir
            changed = False
            for path in root.rglob("*"):
                if not path.is_file() or not _is_image_rel(path.name):
                    continue
                rel = path.relative_to(root).as_posix()
                if rel in indexed:
                    continue
                dimensions = None
                try:
                    dimensions = _image_dimensions(path.read_bytes())
                except Exception:
                    dimensions = None
                indexed[rel] = {
                    "rel": rel,
                    "path": rel,
                    "name": path.name,
                    "date": "-".join(rel.split("/")[:3]) if len(rel.split("/")) >= 4 else datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d"),
                    "size": path.stat().st_size,
                    "created_at": datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                    "storage": "local",
                    "local": True,
                    "remote": False,
                    "remote_provider": "",
                    "webdav": False,
                    "minio": False,
                    "qiniu": False,
                    **({"width": dimensions[0], "height": dimensions[1]} if dimensions else {}),
                }
                changed = True

            items: list[dict[str, object]] = []
            for rel, item in list(indexed.items()):
                if not _is_image_rel(rel):
                    indexed.pop(rel, None)
                    changed = True
                    continue
                local = _local_image_path(rel).is_file()
                provider = _item_remote_provider(item, self.remote_provider())
                remote = _has_remote(item)
                if not local and not remote:
                    indexed.pop(rel, None)
                    changed = True
                    continue
                if str(item.get("asset_type") or "generated") in {"task_input", "task_mask", "task_result"}:
                    continue
                storage = _storage_label(local, remote, provider)
                if (
                    item.get("local") != local
                    or item.get("storage") != storage
                    or item.get("remote") != remote
                    or item.get("remote_provider") != (provider if remote else "")
                ):
                    item = {
                        **item,
                        "local": local,
                        "remote": remote,
                        "remote_provider": provider if remote else "",
                        "storage": storage,
                        "webdav": remote and provider == "webdav",
                        "minio": remote and provider == "minio",
                        "qiniu": remote and provider == "qiniu",
                    }
                    indexed[rel] = item
                    changed = True
                day = str(item.get("date") or "")
                if start_date and day < start_date:
                    continue
                if end_date and day > end_date:
                    continue
                items.append({
                    **item,
                    "rel": rel,
                    "path": rel,
                    "url": self._public_url(rel, base_url),
                })
            if changed:
                self._save_index(indexed)
        items.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return items

    def delete(self, rel: str) -> bool:
        safe_rel = _safe_relative_path(rel)
        removed = False
        path = _local_image_path(safe_rel)
        if path.is_file():
            path.unlink()
            removed = True
        with self._index_lock:
            items = self._load_clean_index()
            item = items.get(safe_rel, {})
            if _has_remote(item):
                try:
                    settings = self.settings()
                    provider = _item_remote_provider(item, self.remote_provider(settings))
                    removed = self._remote_client(provider, settings).delete(safe_rel) or removed
                except ImageStorageError:
                    if not removed:
                        raise
            if safe_rel in items:
                items.pop(safe_rel, None)
                self._save_index(items)
        return removed

    def sync_all(self, workers: int = 1) -> dict[str, int]:
        settings = self.settings()
        mode = self.mode()
        if not _remote_enabled(mode):
            raise ImageStorageError("remote image storage is not enabled")
        provider = self.remote_provider(settings)
        uploaded = 0
        skipped = 0
        failed = 0
        paths = [path for path in sorted(config.images_dir.rglob("*")) if path.is_file() and _is_image_rel(path.name)]
        with self._index_lock:
            items = self._load_clean_index()
        pending: list[Path] = []
        for path in paths:
            rel = path.relative_to(config.images_dir).as_posix()
            item = items.get(rel, {})
            if _has_remote(item) and _item_remote_provider(item, provider) == provider:
                skipped += 1
            else:
                pending.append(path)

        def upload_one(path: Path) -> tuple[str, dict[str, object]]:
            rel = path.relative_to(config.images_dir).as_posix()
            payload = path.read_bytes()
            remote_url = self._remote_client(provider, settings).put(rel, payload)
            dimensions = _image_dimensions(payload)
            stat = path.stat()
            return rel, {
                "rel": rel,
                "path": rel,
                "name": path.name,
                "date": "-".join(rel.split("/")[:3]) if len(rel.split("/")) >= 4 else datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d"),
                "size": len(payload),
                "created_at": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                "storage": "both",
                "local": True,
                "remote": True,
                "remote_provider": provider,
                "webdav": provider == "webdav",
                "minio": provider == "minio",
                "qiniu": provider == "qiniu",
                "remote_url": remote_url,
                **({"width": dimensions[0], "height": dimensions[1]} if dimensions else {}),
            }

        concurrency = max(1, min(2, int(workers or 1)))
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = [executor.submit(upload_one, path) for path in pending]
            for future in as_completed(futures):
                try:
                    rel, uploaded_item = future.result()
                    with self._index_lock:
                        current = self._load_clean_index()
                        existing = current.get(rel, {})
                        uploaded_item["created_at"] = str(existing.get("created_at") or uploaded_item["created_at"])
                        current[rel] = {**existing, **uploaded_item}
                        self._save_index(current)
                    uploaded += 1
                except Exception:
                    failed += 1
        return {"uploaded": uploaded, "skipped": skipped, "failed": failed}

    def test_webdav(self) -> dict[str, object]:
        return WebDAVClient(self.settings()).test()

    def test_minio(self) -> dict[str, object]:
        return MinIOClient(self.settings()).test()

    def test_qiniu(self) -> dict[str, object]:
        return QiniuClient(self.settings()).test()


image_storage_service = ImageStorageService()
