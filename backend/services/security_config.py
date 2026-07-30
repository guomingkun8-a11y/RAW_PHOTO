from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import urlsplit


SECRET_PATHS: tuple[tuple[str, ...], ...] = (
    ("auth-key",),
    ("ai_review", "api_key"),
    ("backup", "access_key_id"),
    ("backup", "secret_access_key"),
    ("backup", "passphrase"),
    ("image_reference_upload", "qiniu_access_key"),
    ("image_reference_upload", "qiniu_secret_key"),
    ("image_storage", "webdav_password"),
    ("image_storage", "minio_access_key"),
    ("image_storage", "minio_secret_key"),
    ("openai_relay", "api_key"),
    ("openai_relay", "api_keys"),
    ("proxy_runtime", "clearance", "cf_cookies"),
    ("proxy_runtime", "clearance", "cf_clearance"),
)

URL_PATHS: tuple[tuple[str, ...], ...] = (
    ("proxy",),
    ("proxy_runtime", "proxy_url"),
    ("proxy_runtime", "resource_proxy_url"),
    ("proxy_runtime", "clearance", "flaresolverr_url"),
    ("image_storage", "webdav_url"),
    ("image_task_queue", "redis_url"),
    ("image_task_queue", "database_url"),
)


def _lookup(data: Mapping[str, object], path: tuple[str, ...]) -> object:
    value: object = data
    for part in path:
        if not isinstance(value, Mapping):
            return None
        value = value.get(part)
    return value


def _configured(value: object) -> bool:
    return bool(str(value or "").strip())


def _url_has_password(value: object) -> bool:
    text = str(value or "").strip()
    if not text or "://" not in text:
        return False
    try:
        parsed = urlsplit(text)
    except ValueError:
        return False
    return bool(parsed.password)


def find_embedded_secret_paths(data: Mapping[str, object]) -> list[str]:
    findings = [".".join(path) for path in SECRET_PATHS if _configured(_lookup(data, path))]
    findings.extend(
        f"{'.'.join(path)} (URL password)"
        for path in URL_PATHS
        if _url_has_password(_lookup(data, path))
    )
    return sorted(set(findings))
