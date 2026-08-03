from __future__ import annotations

import copy
from dataclasses import dataclass
import json
import os
import sys
from pathlib import Path
import time

from dotenv import load_dotenv

from services.security_config import find_embedded_secret_paths
from services.storage.base import StorageBackend

BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / "data"
CONFIG_FILE = BASE_DIR / "config.json"
VERSION_FILE = BASE_DIR / "VERSION"
BACKUP_STATE_FILE = DATA_DIR / "backup_state.json"

load_dotenv(BASE_DIR / ".env.local", override=False)
load_dotenv(BASE_DIR / ".env", override=False)

DEFAULT_BACKUP_INCLUDE = {
    "config": True,
    "cpa": True,
    "sub2api": True,
    "logs": True,
    "image_tasks": True,
    "accounts_snapshot": True,
    "auth_keys_snapshot": True,
    "images": False,
}

DEFAULT_IMAGE_STORAGE = {
    "enabled": False,
    "mode": "local",
    "provider": "webdav",
    "webdav_url": "",
    "webdav_username": "",
    "webdav_password": "",
    "webdav_root_path": "gmkraw/images",
    "minio_endpoint": "",
    "minio_access_key": "",
    "minio_secret_key": "",
    "minio_bucket": "",
    "minio_region": "us-east-1",
    "minio_secure": True,
    "minio_root_path": "gmkraw/images",
    "public_base_url": "",
}

DEFAULT_IMAGE_REFERENCE_UPLOAD = {
    "enabled": False,
    "provider": "oss",
    "oss_endpoint": "",
    "oss_access_key": "",
    "oss_secret_key": "",
    "oss_bucket": "",
    "oss_region": "oss-cn-beijing",
    "oss_secure": True,
    "oss_prefix": "raw-photo/reference",
    "public_base_url": "",
    "timeout_sec": 20,
    "persistent_cache_enabled": True,
}

DEFAULT_PROXY_RUNTIME_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/145.0.0.0 Safari/537.36"
)

DEFAULT_PROXY_RUNTIME = {
    "enabled": False,
    "egress_mode": "direct",
    "proxy_url": "",
    "resource_proxy_url": "",
    "skip_ssl_verify": False,
    "reset_session_status_codes": [403],
    "clearance": {
        "enabled": False,
        "mode": "none",
        "cf_cookies": "",
        "cf_clearance": "",
        "user_agent": DEFAULT_PROXY_RUNTIME_USER_AGENT,
        "browser": "chrome",
        "flaresolverr_url": "",
        "timeout_sec": 60,
        "refresh_interval": 3600,
        "warm_up_on_start": False,
    },
}

DEFAULT_THIRD_PARTY_APPS = {
    "infinite_canvas": {
        "enabled": False,
        "url": "https://canvas.best",
    },
}

DEFAULT_OPENAI_RELAY = {
    "enabled": False,
    "base_url": "",
    "api_key": "",
    "api_keys": [],
    "api_key_concurrency": 1,
    "api_key_pool_distributed": False,
    "api_key_pool_acquire_timeout_secs": 5,
    "api_key_pool_lease_secs": 600,
    "api_key_pool_cooldown_secs": 60,
    "api_key_pool_max_attempts": 3,
    "prompt_analysis_model": "gpt-4o",
}

DEFAULT_IMAGE_TASK_QUEUE = {
    "enabled": False,
    "executor": "redis",
    "redis_url": "redis://127.0.0.1:6379/0",
    "queue_name": "ai_image_tasks",
    "database_url": "",
    "max_retries": 2,
    "worker_poll_timeout_secs": 5,
    "stale_running_timeout_secs": 1800,
    "total_concurrency": 0,
    "worker_concurrency": 3,
    "owner_concurrency": 2,
    "dynamic_owner_concurrency_enabled": False,
    "dynamic_owner_concurrency_threshold": 10,
    "dynamic_owner_concurrency_max": 20,
    "owner_pending_limit": 50,
    "slot_lease_secs": 7200,
}


def _normalize_bool(value: object, default: bool = False) -> bool:
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


def _normalize_positive_int(value: object, default: int, minimum: int = 0) -> int:
    try:
        normalized = int(value)
    except (OverflowError, TypeError, ValueError):
        normalized = default
    return max(minimum, normalized)


def _normalize_string_list(value: object) -> list[str]:
    if isinstance(value, str):
        items = value.replace("\r", "\n").replace(";", "\n").replace(",", "\n").split("\n")
    elif isinstance(value, (list, tuple, set)):
        items = list(value)
    else:
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item if item is not None else "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized


def _clear_nested_value(data: dict[str, object], path: tuple[str, ...], replacement: object = "") -> None:
    current: dict[str, object] = data
    for part in path[:-1]:
        nested = current.get(part)
        if not isinstance(nested, dict):
            return
        current = nested
    current[path[-1]] = replacement


def _strip_environment_managed_secrets(data: dict[str, object]) -> dict[str, object]:
    mappings: tuple[tuple[tuple[str, ...], tuple[str, ...], object], ...] = (
        (("GMKRAW_AUTH_KEY",), ("auth-key",), ""),
        (("GMKRAW_AI_REVIEW_API_KEY",), ("ai_review", "api_key"), ""),
        (("GMKRAW_BACKUP_ACCESS_KEY_ID",), ("backup", "access_key_id"), ""),
        (("GMKRAW_BACKUP_SECRET_ACCESS_KEY",), ("backup", "secret_access_key"), ""),
        (("GMKRAW_BACKUP_PASSPHRASE",), ("backup", "passphrase"), ""),
        (
            ("GMKRAW_OSS_ACCESS_KEY_ID", "GMKRAW_OSS_ACCESS_KEY", "GMKRAW_MINIO_ACCESS_KEY", "MINIO_ACCESS_KEY"),
            ("image_reference_upload", "oss_access_key"),
            "",
        ),
        (
            ("GMKRAW_OSS_ACCESS_KEY_SECRET", "GMKRAW_OSS_SECRET_KEY", "GMKRAW_MINIO_SECRET_KEY", "MINIO_SECRET_KEY"),
            ("image_reference_upload", "oss_secret_key"),
            "",
        ),
        (("GMKRAW_WEBDAV_PASSWORD", "WEBDAV_PASSWORD"), ("image_storage", "webdav_password"), ""),
        (("GMKRAW_MINIO_ACCESS_KEY", "MINIO_ACCESS_KEY"), ("image_storage", "minio_access_key"), ""),
        (("GMKRAW_MINIO_SECRET_KEY", "MINIO_SECRET_KEY"), ("image_storage", "minio_secret_key"), ""),
        (("GMKRAW_OPENAI_RELAY_API_KEY",), ("openai_relay", "api_key"), ""),
        (("GMKRAW_OPENAI_RELAY_API_KEYS",), ("openai_relay", "api_keys"), []),
        (("GMKRAW_CF_COOKIES",), ("proxy_runtime", "clearance", "cf_cookies"), ""),
        (("GMKRAW_CF_CLEARANCE",), ("proxy_runtime", "clearance", "cf_clearance"), ""),
        (("IMAGE_TASK_REDIS_URL", "REDIS_URL"), ("image_task_queue", "redis_url"), DEFAULT_IMAGE_TASK_QUEUE["redis_url"]),
        (("IMAGE_TASK_DATABASE_URL", "MYSQL_DATABASE_URL"), ("image_task_queue", "database_url"), ""),
    )
    for env_names, path, replacement in mappings:
        if any(os.getenv(name) is not None for name in env_names):
            _clear_nested_value(data, path, replacement)
    return data


def _normalize_backup_include(value: object) -> dict[str, bool]:
    source = value if isinstance(value, dict) else {}
    normalized = dict(DEFAULT_BACKUP_INCLUDE)
    for key in normalized:
        normalized[key] = _normalize_bool(source.get(key), normalized[key])
    return normalized


def _normalize_backup_settings(value: object) -> dict[str, object]:
    source = value if isinstance(value, dict) else {}
    enabled_env = os.getenv("GMKRAW_BACKUP_ENABLED")
    encrypt_env = os.getenv("GMKRAW_BACKUP_ENCRYPT")
    return {
        "enabled": _normalize_bool(enabled_env if enabled_env is not None else source.get("enabled"), False),
        "provider": "cloudflare_r2",
        "account_id": str(os.getenv("GMKRAW_BACKUP_ACCOUNT_ID") or source.get("account_id") or "").strip(),
        "access_key_id": str(
            os.getenv("GMKRAW_BACKUP_ACCESS_KEY_ID") or source.get("access_key_id") or ""
        ).strip(),
        "secret_access_key": str(
            os.getenv("GMKRAW_BACKUP_SECRET_ACCESS_KEY") or source.get("secret_access_key") or ""
        ).strip(),
        "bucket": str(os.getenv("GMKRAW_BACKUP_BUCKET") or source.get("bucket") or "").strip(),
        "prefix": str(
            os.getenv("GMKRAW_BACKUP_PREFIX") or source.get("prefix") or "backups"
        ).strip().strip("/") or "backups",
        "interval_minutes": _normalize_positive_int(
            os.getenv("GMKRAW_BACKUP_INTERVAL_MINUTES") or source.get("interval_minutes"),
            360,
            1,
        ),
        "rotation_keep": _normalize_positive_int(
            os.getenv("GMKRAW_BACKUP_ROTATION_KEEP") or source.get("rotation_keep"),
            10,
            0,
        ),
        "encrypt": _normalize_bool(encrypt_env if encrypt_env is not None else source.get("encrypt"), False),
        "passphrase": str(
            os.getenv("GMKRAW_BACKUP_PASSPHRASE") or source.get("passphrase") or ""
        ).strip(),
        "include": _normalize_backup_include(source.get("include")),
    }


def _normalize_backup_state(value: object) -> dict[str, object]:
    source = value if isinstance(value, dict) else {}
    return {
        "last_started_at": str(source.get("last_started_at") or "").strip() or None,
        "last_finished_at": str(source.get("last_finished_at") or "").strip() or None,
        "last_status": str(source.get("last_status") or "idle").strip() or "idle",
        "last_error": str(source.get("last_error") or "").strip() or None,
        "last_object_key": str(source.get("last_object_key") or "").strip() or None,
    }


def _normalize_image_storage_settings(value: object) -> dict[str, object]:
    source = value if isinstance(value, dict) else {}
    enabled_env = os.getenv("GMKRAW_IMAGE_STORAGE_ENABLED")
    mode = str(os.getenv("GMKRAW_IMAGE_STORAGE_MODE") or source.get("mode") or "local").strip().lower()
    provider = str(
        os.getenv("GMKRAW_IMAGE_STORAGE_PROVIDER")
        or source.get("provider")
        or source.get("remote_provider")
        or ""
    ).strip().lower()
    if mode == "remote":
        mode = provider if provider in {"webdav", "minio"} else "minio"
    if mode not in {"local", "webdav", "minio", "both"}:
        mode = "local"
    enabled = _normalize_bool(
        enabled_env if enabled_env is not None else source.get("enabled"),
        False,
    )
    if not enabled:
        mode = "local"
    if not provider:
        has_minio_config = any(
            str(item or "").strip()
            for item in (
                os.getenv("GMKRAW_MINIO_ENDPOINT"),
                os.getenv("MINIO_ENDPOINT"),
                source.get("minio_endpoint"),
                source.get("minio_bucket"),
            )
        )
        if mode == "minio":
            provider = "minio"
        elif mode == "webdav":
            provider = "webdav"
        elif mode == "both" and has_minio_config:
            provider = "minio"
        else:
            provider = str(DEFAULT_IMAGE_STORAGE["provider"])
    if provider not in {"webdav", "minio"}:
        provider = str(DEFAULT_IMAGE_STORAGE["provider"])
    if mode == "webdav":
        provider = "webdav"
    elif mode == "minio":
        provider = "minio"
    root_path = str(source.get("webdav_root_path") or DEFAULT_IMAGE_STORAGE["webdav_root_path"]).strip().strip("/")
    minio_root_path = str(
        os.getenv("GMKRAW_MINIO_ROOT_PATH")
        or os.getenv("MINIO_ROOT_PATH")
        or source.get("minio_root_path")
        or DEFAULT_IMAGE_STORAGE["minio_root_path"]
    ).strip().strip("/")
    minio_secure_env = os.getenv("GMKRAW_MINIO_SECURE") or os.getenv("MINIO_SECURE")
    public_base_url = str(
        os.getenv("GMKRAW_IMAGE_STORAGE_PUBLIC_BASE_URL")
        or os.getenv("IMAGE_PUBLIC_BASE_URL")
        or source.get("public_base_url")
        or ""
    ).strip().rstrip("/")
    return {
        "enabled": enabled,
        "mode": mode,
        "provider": provider,
        "webdav_url": str(
            os.getenv("GMKRAW_WEBDAV_URL") or os.getenv("WEBDAV_URL") or source.get("webdav_url") or ""
        ).strip().rstrip("/"),
        "webdav_username": str(
            os.getenv("GMKRAW_WEBDAV_USERNAME")
            or os.getenv("WEBDAV_USERNAME")
            or source.get("webdav_username")
            or ""
        ).strip(),
        "webdav_password": str(
            os.getenv("GMKRAW_WEBDAV_PASSWORD")
            or os.getenv("WEBDAV_PASSWORD")
            or source.get("webdav_password")
            or ""
        ).strip(),
        "webdav_root_path": str(
            os.getenv("GMKRAW_WEBDAV_ROOT_PATH")
            or os.getenv("WEBDAV_ROOT_PATH")
            or root_path
            or DEFAULT_IMAGE_STORAGE["webdav_root_path"]
        ).strip().strip("/"),
        "minio_endpoint": str(
            os.getenv("GMKRAW_MINIO_ENDPOINT")
            or os.getenv("MINIO_ENDPOINT")
            or source.get("minio_endpoint")
            or ""
        ).strip().rstrip("/"),
        "minio_access_key": str(
            os.getenv("GMKRAW_MINIO_ACCESS_KEY")
            or os.getenv("MINIO_ACCESS_KEY")
            or source.get("minio_access_key")
            or ""
        ).strip(),
        "minio_secret_key": str(
            os.getenv("GMKRAW_MINIO_SECRET_KEY")
            or os.getenv("MINIO_SECRET_KEY")
            or source.get("minio_secret_key")
            or ""
        ).strip(),
        "minio_bucket": str(
            os.getenv("GMKRAW_MINIO_BUCKET")
            or os.getenv("MINIO_BUCKET")
            or source.get("minio_bucket")
            or ""
        ).strip(),
        "minio_region": str(
            os.getenv("GMKRAW_MINIO_REGION")
            or os.getenv("MINIO_REGION")
            or source.get("minio_region")
            or DEFAULT_IMAGE_STORAGE["minio_region"]
        ).strip(),
        "minio_secure": _normalize_bool(
            minio_secure_env if minio_secure_env is not None else source.get("minio_secure"),
            bool(DEFAULT_IMAGE_STORAGE["minio_secure"]),
        ),
        "minio_root_path": minio_root_path or str(DEFAULT_IMAGE_STORAGE["minio_root_path"]),
        "public_base_url": public_base_url,
    }


def _normalize_image_reference_upload_settings(value: object) -> dict[str, object]:
    source = value if isinstance(value, dict) else {}
    enabled_env = os.getenv("GMKRAW_IMAGE_REFERENCE_UPLOAD_ENABLED") or os.getenv("GMKRAW_OSS_REFERENCE_UPLOAD_ENABLED")
    oss_endpoint_env = (
        os.getenv("GMKRAW_OSS_ENDPOINT")
        or os.getenv("GMKRAW_REFERENCE_OSS_ENDPOINT")
        or os.getenv("GMKRAW_MINIO_ENDPOINT")
        or os.getenv("MINIO_ENDPOINT")
    )
    oss_access_key_env = (
        os.getenv("GMKRAW_OSS_ACCESS_KEY_ID")
        or os.getenv("GMKRAW_OSS_ACCESS_KEY")
        or os.getenv("GMKRAW_REFERENCE_OSS_ACCESS_KEY_ID")
        or os.getenv("GMKRAW_MINIO_ACCESS_KEY")
        or os.getenv("MINIO_ACCESS_KEY")
    )
    oss_secret_key_env = (
        os.getenv("GMKRAW_OSS_ACCESS_KEY_SECRET")
        or os.getenv("GMKRAW_OSS_SECRET_KEY")
        or os.getenv("GMKRAW_REFERENCE_OSS_ACCESS_KEY_SECRET")
        or os.getenv("GMKRAW_MINIO_SECRET_KEY")
        or os.getenv("MINIO_SECRET_KEY")
    )
    oss_bucket_env = (
        os.getenv("GMKRAW_OSS_BUCKET")
        or os.getenv("GMKRAW_REFERENCE_OSS_BUCKET")
        or os.getenv("GMKRAW_MINIO_BUCKET")
        or os.getenv("MINIO_BUCKET")
    )
    oss_region_env = (
        os.getenv("GMKRAW_OSS_REGION")
        or os.getenv("GMKRAW_REFERENCE_OSS_REGION")
        or os.getenv("GMKRAW_MINIO_REGION")
        or os.getenv("MINIO_REGION")
    )
    oss_secure_env = (
        os.getenv("GMKRAW_OSS_SECURE")
        or os.getenv("GMKRAW_REFERENCE_OSS_SECURE")
        or os.getenv("GMKRAW_MINIO_SECURE")
        or os.getenv("MINIO_SECURE")
    )
    oss_prefix_env = (
        os.getenv("GMKRAW_OSS_REFERENCE_PREFIX")
        or os.getenv("GMKRAW_REFERENCE_OSS_PREFIX")
        or os.getenv("GMKRAW_IMAGE_REFERENCE_OSS_PREFIX")
        or os.getenv("GMKRAW_OSS_PREFIX")
    )
    public_base_url_env = (
        os.getenv("GMKRAW_OSS_REFERENCE_PUBLIC_BASE_URL")
        or os.getenv("GMKRAW_REFERENCE_OSS_PUBLIC_BASE_URL")
        or os.getenv("GMKRAW_IMAGE_REFERENCE_PUBLIC_BASE_URL")
        or os.getenv("GMKRAW_OSS_PUBLIC_BASE_URL")
    )
    timeout_sec_env = os.getenv("GMKRAW_OSS_TIMEOUT_SEC") or os.getenv("GMKRAW_IMAGE_REFERENCE_UPLOAD_TIMEOUT_SEC")
    persistent_cache_env = os.getenv("GMKRAW_OSS_PERSISTENT_CACHE_ENABLED") or os.getenv("GMKRAW_IMAGE_REFERENCE_PERSISTENT_CACHE_ENABLED")
    timeout_sec = _normalize_positive_int(
        timeout_sec_env or source.get("timeout_sec"),
        int(DEFAULT_IMAGE_REFERENCE_UPLOAD["timeout_sec"]),
        5,
    )
    return {
        "enabled": _normalize_bool(
            enabled_env if enabled_env is not None else source.get("enabled"),
            bool(DEFAULT_IMAGE_REFERENCE_UPLOAD["enabled"]),
        ),
        "provider": "oss",
        "oss_endpoint": str(
            oss_endpoint_env
            or source.get("oss_endpoint")
            or source.get("minio_endpoint")
            or ""
        ).strip().rstrip("/"),
        "oss_access_key": str(
            oss_access_key_env
            or source.get("oss_access_key")
            or source.get("oss_access_key_id")
            or source.get("minio_access_key")
            or ""
        ).strip(),
        "oss_secret_key": str(
            oss_secret_key_env
            or source.get("oss_secret_key")
            or source.get("oss_access_key_secret")
            or source.get("minio_secret_key")
            or ""
        ).strip(),
        "oss_bucket": str(
            oss_bucket_env
            or source.get("oss_bucket")
            or source.get("minio_bucket")
            or ""
        ).strip(),
        "oss_region": str(
            oss_region_env
            or source.get("oss_region")
            or source.get("minio_region")
            or DEFAULT_IMAGE_REFERENCE_UPLOAD["oss_region"]
        ).strip(),
        "oss_secure": _normalize_bool(
            oss_secure_env if oss_secure_env is not None else source.get("oss_secure", source.get("minio_secure")),
            bool(DEFAULT_IMAGE_REFERENCE_UPLOAD["oss_secure"]),
        ),
        "oss_prefix": str(
            oss_prefix_env
            or source.get("oss_prefix")
            or source.get("minio_prefix")
            or DEFAULT_IMAGE_REFERENCE_UPLOAD["oss_prefix"]
        ).strip().strip("/"),
        "public_base_url": str(
            public_base_url_env
            or source.get("public_base_url")
            or source.get("oss_public_base_url")
            or ""
        ).strip().rstrip("/"),
        "timeout_sec": timeout_sec,
        "persistent_cache_enabled": _normalize_bool(
            persistent_cache_env if persistent_cache_env is not None else source.get("persistent_cache_enabled"),
            bool(DEFAULT_IMAGE_REFERENCE_UPLOAD["persistent_cache_enabled"]),
        ),
    }


def _normalize_status_codes(value: object) -> list[int]:
    items = value if isinstance(value, list) else DEFAULT_PROXY_RUNTIME["reset_session_status_codes"]
    normalized: list[int] = []
    for item in items:
        if isinstance(item, bool):
            continue
        try:
            status = int(item)
        except (OverflowError, TypeError, ValueError):
            continue
        if 100 <= status <= 599 and status not in normalized:
            normalized.append(status)
    if not normalized:
        return list(DEFAULT_PROXY_RUNTIME["reset_session_status_codes"])
    return normalized


def _normalize_proxy_runtime_settings(value: object) -> dict[str, object]:
    source = value if isinstance(value, dict) else {}
    default_clearance = DEFAULT_PROXY_RUNTIME["clearance"]
    clearance_source = source.get("clearance") if isinstance(source.get("clearance"), dict) else {}

    enabled_env = os.getenv("GMKRAW_PROXY_RUNTIME_ENABLED")
    egress_mode = str(
        os.getenv("GMKRAW_PROXY_EGRESS_MODE")
        or source.get("egress_mode")
        or DEFAULT_PROXY_RUNTIME["egress_mode"]
    ).strip().lower()
    if egress_mode not in {"direct", "single_proxy"}:
        egress_mode = str(DEFAULT_PROXY_RUNTIME["egress_mode"])

    clearance_mode = str(clearance_source.get("mode") or default_clearance["mode"]).strip().lower()
    if clearance_mode not in {"none", "manual", "flaresolverr"}:
        clearance_mode = str(default_clearance["mode"])

    user_agent = str(clearance_source.get("user_agent") or default_clearance["user_agent"]).strip()
    browser = str(clearance_source.get("browser") or default_clearance["browser"]).strip()

    existing_clearance_cookies = str(source.get("_existing_cf_cookies") or "").strip()
    existing_cf_clearance = str(source.get("_existing_cf_clearance") or "").strip()
    cf_cookies = str(clearance_source.get("cf_cookies") or "").strip()
    cf_clearance = str(clearance_source.get("cf_clearance") or "").strip()
    if not cf_cookies and _normalize_bool(clearance_source.get("has_cf_cookies"), False):
        cf_cookies = existing_clearance_cookies
    if not cf_clearance and _normalize_bool(clearance_source.get("has_cf_clearance"), False):
        cf_clearance = existing_cf_clearance

    return {
        "enabled": _normalize_bool(
            enabled_env if enabled_env is not None else source.get("enabled"),
            bool(DEFAULT_PROXY_RUNTIME["enabled"]),
        ),
        "egress_mode": egress_mode,
        "proxy_url": str(
            os.getenv("GMKRAW_PROXY_URL") or source.get("proxy_url") or ""
        ).strip(),
        "resource_proxy_url": str(
            os.getenv("GMKRAW_RESOURCE_PROXY_URL") or source.get("resource_proxy_url") or ""
        ).strip(),
        "skip_ssl_verify": _normalize_bool(
            source.get("skip_ssl_verify"),
            bool(DEFAULT_PROXY_RUNTIME["skip_ssl_verify"]),
        ),
        "reset_session_status_codes": _normalize_status_codes(source.get("reset_session_status_codes")),
        "clearance": {
            "enabled": _normalize_bool(clearance_source.get("enabled"), bool(default_clearance["enabled"])),
            "mode": clearance_mode,
            "cf_cookies": str(os.getenv("GMKRAW_CF_COOKIES") or cf_cookies).strip(),
            "cf_clearance": str(os.getenv("GMKRAW_CF_CLEARANCE") or cf_clearance).strip(),
            "user_agent": user_agent or str(default_clearance["user_agent"]),
            "browser": browser or str(default_clearance["browser"]),
            "flaresolverr_url": str(
                os.getenv("GMKRAW_FLARESOLVERR_URL") or clearance_source.get("flaresolverr_url") or ""
            ).strip(),
            "timeout_sec": _normalize_positive_int(
                clearance_source.get("timeout_sec"),
                int(default_clearance["timeout_sec"]),
                1,
            ),
            "refresh_interval": _normalize_positive_int(
                clearance_source.get("refresh_interval"),
                int(default_clearance["refresh_interval"]),
                60,
            ),
            "warm_up_on_start": _normalize_bool(
                clearance_source.get("warm_up_on_start"),
                bool(default_clearance["warm_up_on_start"]),
            ),
        },
    }


def _normalize_third_party_apps_settings(value: object) -> dict[str, object]:
    source = value if isinstance(value, dict) else {}
    canvas_source = source.get("infinite_canvas") if isinstance(source.get("infinite_canvas"), dict) else {}
    return {
        "infinite_canvas": {
            "enabled": _normalize_bool(canvas_source.get("enabled"), False),
            "url": str(canvas_source.get("url") or DEFAULT_THIRD_PARTY_APPS["infinite_canvas"]["url"]).strip(),
        },
    }


def _normalize_ai_review_settings(value: object) -> dict[str, object]:
    source = dict(value) if isinstance(value, dict) else {}
    enabled_env = os.getenv("GMKRAW_AI_REVIEW_ENABLED")
    fail_open_env = os.getenv("GMKRAW_AI_REVIEW_FAIL_OPEN")
    source["enabled"] = _normalize_bool(
        enabled_env if enabled_env is not None else source.get("enabled"),
        False,
    )
    source["fail_open"] = _normalize_bool(
        fail_open_env if fail_open_env is not None else source.get("fail_open"),
        True,
    )
    source["base_url"] = str(
        os.getenv("GMKRAW_AI_REVIEW_BASE_URL") or source.get("base_url") or ""
    ).strip().rstrip("/")
    source["api_key"] = str(
        os.getenv("GMKRAW_AI_REVIEW_API_KEY") or source.get("api_key") or ""
    ).strip()
    source["model"] = str(
        os.getenv("GMKRAW_AI_REVIEW_MODEL") or source.get("model") or ""
    ).strip()
    return source


def _normalize_openai_relay_settings(value: object) -> dict[str, object]:
    source = value if isinstance(value, dict) else {}
    enabled_env = os.getenv("GMKRAW_OPENAI_RELAY_ENABLED")
    base_url_env = os.getenv("GMKRAW_OPENAI_RELAY_BASE_URL")
    api_key_env = os.getenv("GMKRAW_OPENAI_RELAY_API_KEY")
    api_keys_env = os.getenv("GMKRAW_OPENAI_RELAY_API_KEYS")
    api_key_concurrency_env = os.getenv("GMKRAW_OPENAI_RELAY_API_KEY_CONCURRENCY")
    pool_distributed_env = os.getenv("GMKRAW_OPENAI_RELAY_POOL_DISTRIBUTED")
    pool_acquire_timeout_env = os.getenv("GMKRAW_OPENAI_RELAY_POOL_ACQUIRE_TIMEOUT_SECS")
    pool_lease_env = os.getenv("GMKRAW_OPENAI_RELAY_POOL_LEASE_SECS")
    pool_cooldown_env = os.getenv("GMKRAW_OPENAI_RELAY_POOL_COOLDOWN_SECS")
    pool_max_attempts_env = os.getenv("GMKRAW_OPENAI_RELAY_POOL_MAX_ATTEMPTS")
    prompt_analysis_model_env = os.getenv("GMKRAW_PROMPT_ANALYSIS_MODEL")
    return {
        "enabled": _normalize_bool(
            enabled_env if enabled_env is not None else source.get("enabled"),
            bool(DEFAULT_OPENAI_RELAY["enabled"]),
        ),
        "base_url": str(base_url_env or source.get("base_url") or "").strip().rstrip("/"),
        "api_key": str(api_key_env or source.get("api_key") or "").strip(),
        "api_keys": _normalize_string_list(
            api_keys_env if api_keys_env is not None else source.get("api_keys")
        ),
        "api_key_concurrency": _normalize_positive_int(
            api_key_concurrency_env if api_key_concurrency_env is not None else source.get("api_key_concurrency"),
            int(DEFAULT_OPENAI_RELAY["api_key_concurrency"]),
            1,
        ),
        "api_key_pool_distributed": _normalize_bool(
            pool_distributed_env if pool_distributed_env is not None else source.get("api_key_pool_distributed"),
            bool(DEFAULT_OPENAI_RELAY["api_key_pool_distributed"]),
        ),
        "api_key_pool_acquire_timeout_secs": _normalize_positive_int(
            pool_acquire_timeout_env if pool_acquire_timeout_env is not None else source.get("api_key_pool_acquire_timeout_secs"),
            int(DEFAULT_OPENAI_RELAY["api_key_pool_acquire_timeout_secs"]),
            1,
        ),
        "api_key_pool_lease_secs": _normalize_positive_int(
            pool_lease_env if pool_lease_env is not None else source.get("api_key_pool_lease_secs"),
            int(DEFAULT_OPENAI_RELAY["api_key_pool_lease_secs"]),
            60,
        ),
        "api_key_pool_cooldown_secs": _normalize_positive_int(
            pool_cooldown_env if pool_cooldown_env is not None else source.get("api_key_pool_cooldown_secs"),
            int(DEFAULT_OPENAI_RELAY["api_key_pool_cooldown_secs"]),
            1,
        ),
        "api_key_pool_max_attempts": _normalize_positive_int(
            pool_max_attempts_env if pool_max_attempts_env is not None else source.get("api_key_pool_max_attempts"),
            int(DEFAULT_OPENAI_RELAY["api_key_pool_max_attempts"]),
            1,
        ),
        "prompt_analysis_model": str(
            prompt_analysis_model_env
            or source.get("prompt_analysis_model")
            or DEFAULT_OPENAI_RELAY["prompt_analysis_model"]
        ).strip(),
    }


def _normalize_image_task_queue_settings(value: object) -> dict[str, object]:
    source = value if isinstance(value, dict) else {}
    enabled_env = os.getenv("IMAGE_TASK_QUEUE_ENABLED") or os.getenv("GMKRAW_IMAGE_TASK_QUEUE_ENABLED")
    executor = str(
        os.getenv("IMAGE_TASK_EXECUTOR") or source.get("executor") or DEFAULT_IMAGE_TASK_QUEUE["executor"]
    ).strip().lower()
    if executor not in {"redis", "celery"}:
        executor = str(DEFAULT_IMAGE_TASK_QUEUE["executor"])
    redis_url = str(
        os.getenv("IMAGE_TASK_REDIS_URL")
        or os.getenv("REDIS_URL")
        or source.get("redis_url")
        or DEFAULT_IMAGE_TASK_QUEUE["redis_url"]
    ).strip()
    database_url = str(
        os.getenv("IMAGE_TASK_DATABASE_URL")
        or source.get("database_url")
        or os.getenv("IMAGE_LIBRARY_DATABASE_URL")
        or os.getenv("MYSQL_DATABASE_URL")
        or f"sqlite:///{DATA_DIR / 'image_tasks.db'}"
    ).strip()
    queue_name = str(
        os.getenv("IMAGE_TASK_QUEUE_NAME")
        or source.get("queue_name")
        or DEFAULT_IMAGE_TASK_QUEUE["queue_name"]
    ).strip()
    return {
        "enabled": _normalize_bool(
            enabled_env if enabled_env is not None else source.get("enabled"),
            bool(DEFAULT_IMAGE_TASK_QUEUE["enabled"]),
        ),
        "executor": executor,
        "redis_url": redis_url or str(DEFAULT_IMAGE_TASK_QUEUE["redis_url"]),
        "queue_name": queue_name or str(DEFAULT_IMAGE_TASK_QUEUE["queue_name"]),
        "database_url": database_url,
        "max_retries": _normalize_positive_int(
            os.getenv("IMAGE_TASK_MAX_RETRIES") or source.get("max_retries"),
            int(DEFAULT_IMAGE_TASK_QUEUE["max_retries"]),
            0,
        ),
        "worker_poll_timeout_secs": _normalize_positive_int(
            os.getenv("IMAGE_TASK_WORKER_POLL_TIMEOUT_SECS") or source.get("worker_poll_timeout_secs"),
            int(DEFAULT_IMAGE_TASK_QUEUE["worker_poll_timeout_secs"]),
            1,
        ),
        "stale_running_timeout_secs": _normalize_positive_int(
            os.getenv("IMAGE_TASK_STALE_RUNNING_TIMEOUT_SECS") or source.get("stale_running_timeout_secs"),
            int(DEFAULT_IMAGE_TASK_QUEUE["stale_running_timeout_secs"]),
            60,
        ),
        "total_concurrency": _normalize_positive_int(
            os.getenv("IMAGE_TASK_TOTAL_CONCURRENCY") or source.get("total_concurrency"),
            int(DEFAULT_IMAGE_TASK_QUEUE["total_concurrency"]),
            0,
        ),
        "worker_concurrency": _normalize_positive_int(
            os.getenv("IMAGE_TASK_WORKER_CONCURRENCY") or source.get("worker_concurrency"),
            int(DEFAULT_IMAGE_TASK_QUEUE["worker_concurrency"]),
            1,
        ),
        "owner_concurrency": _normalize_positive_int(
            os.getenv("IMAGE_TASK_OWNER_CONCURRENCY") or source.get("owner_concurrency"),
            int(DEFAULT_IMAGE_TASK_QUEUE["owner_concurrency"]),
            1,
        ),
        "dynamic_owner_concurrency_enabled": _normalize_bool(
            os.getenv("IMAGE_TASK_DYNAMIC_OWNER_CONCURRENCY_ENABLED")
            or source.get("dynamic_owner_concurrency_enabled"),
            bool(DEFAULT_IMAGE_TASK_QUEUE["dynamic_owner_concurrency_enabled"]),
        ),
        "dynamic_owner_concurrency_threshold": _normalize_positive_int(
            os.getenv("IMAGE_TASK_DYNAMIC_OWNER_CONCURRENCY_THRESHOLD")
            or source.get("dynamic_owner_concurrency_threshold"),
            int(DEFAULT_IMAGE_TASK_QUEUE["dynamic_owner_concurrency_threshold"]),
            1,
        ),
        "dynamic_owner_concurrency_max": _normalize_positive_int(
            os.getenv("IMAGE_TASK_DYNAMIC_OWNER_CONCURRENCY_MAX")
            or source.get("dynamic_owner_concurrency_max"),
            int(DEFAULT_IMAGE_TASK_QUEUE["dynamic_owner_concurrency_max"]),
            1,
        ),
        "owner_pending_limit": _normalize_positive_int(
            os.getenv("IMAGE_TASK_OWNER_PENDING_LIMIT") or source.get("owner_pending_limit"),
            int(DEFAULT_IMAGE_TASK_QUEUE["owner_pending_limit"]),
            1,
        ),
        "slot_lease_secs": _normalize_positive_int(
            os.getenv("IMAGE_TASK_SLOT_LEASE_SECS") or source.get("slot_lease_secs"),
            int(DEFAULT_IMAGE_TASK_QUEUE["slot_lease_secs"]),
            60,
        ),
    }


def _mask_url_password(url: str) -> str:
    if "://" not in url or "@" not in url:
        return url
    try:
        protocol, rest = url.split("://", 1)
        credentials, host = rest.split("@", 1)
        if ":" not in credentials:
            return url
        username, _password = credentials.split(":", 1)
        return f"{protocol}://{username}:****@{host}"
    except Exception:
        return url


def _validate_image_storage_settings(settings: dict[str, object]) -> None:
    if not _normalize_bool(settings.get("enabled"), False):
        return
    mode = str(settings.get("mode") or "local").strip().lower()
    if mode == "local":
        return
    provider = str(settings.get("provider") or "webdav").strip().lower()
    if provider == "minio":
        missing = [
            field
            for field in ("minio_endpoint", "minio_access_key", "minio_secret_key", "minio_bucket")
            if not str(settings.get(field) or "").strip()
        ]
        if missing:
            raise ValueError(f"MinIO image storage is missing required settings: {', '.join(missing)}")
        return
    if not str(settings.get("webdav_url") or "").strip():
        raise ValueError("启用 WebDAV 图片存储后必须填写 WebDAV URL")
    if not str(settings.get("webdav_password") or "").strip():
        raise ValueError("启用 WebDAV 图片存储后必须填写 WebDAV 密码")


@dataclass(frozen=True)
class LoadedSettings:
    auth_key: str
    refresh_account_interval_minute: int


def _normalize_auth_key(value: object) -> str:
    return str(value or "").strip()


def _is_invalid_auth_key(value: object) -> bool:
    return _normalize_auth_key(value) == ""


def _read_json_object(path: Path, *, name: str) -> dict[str, object]:
    if not path.exists():
        return {}
    if path.is_dir():
        print(
            f"Warning: {name} at '{path}' is a directory, ignoring it and falling back to other configuration sources.",
            file=sys.stderr,
        )
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        print(
            f"Warning: failed to parse {name} at '{path}': {exc}. Falling back to other configuration sources.",
            file=sys.stderr,
        )
        return {}
    return data if isinstance(data, dict) else {}


def _load_settings() -> LoadedSettings:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    raw_config = _read_json_object(CONFIG_FILE, name="config.json")
    auth_key = _normalize_auth_key(os.getenv("GMKRAW_AUTH_KEY") or raw_config.get("auth-key"))
    if _is_invalid_auth_key(auth_key):
        raise ValueError(
            "❌ auth-key 未设置！\n"
            "请在环境变量 GMKRAW_AUTH_KEY 中设置，或者在 config.json 中填写 auth-key。"
        )

    try:
        refresh_interval = int(raw_config.get("refresh_account_interval_minute", 5))
    except (TypeError, ValueError):
        refresh_interval = 5

    return LoadedSettings(
        auth_key=auth_key,
        refresh_account_interval_minute=refresh_interval,
    )


class ConfigStore:
    def __init__(self, path: Path):
        self.path = path
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.data = self._load()
        self.embedded_secret_paths = find_embedded_secret_paths(self.data)
        if _normalize_bool(os.getenv("GMKRAW_STRICT_SECRET_SOURCES"), False) and self.embedded_secret_paths:
            joined_paths = ", ".join(self.embedded_secret_paths)
            raise ValueError(f"embedded secrets are not allowed in strict mode: {joined_paths}")
        self._storage_backend: StorageBackend | None = None
        if _is_invalid_auth_key(self.auth_key):
            raise ValueError(
                "❌ auth-key 未设置！\n"
                "请按以下任意一种方式解决：\n"
                "1. 在 Render 的 Environment 变量中添加：\n"
                "   GMKRAW_AUTH_KEY = your_real_auth_key\n"
                "2. 或者在 config.json 中填写：\n"
                '   "auth-key": "your_real_auth_key"'
            )

    def _load(self) -> dict[str, object]:
        return _read_json_object(self.path, name="config.json")

    def _save(self) -> None:
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    @property
    def auth_key(self) -> str:
        return _normalize_auth_key(os.getenv("GMKRAW_AUTH_KEY") or self.data.get("auth-key"))

    @property
    def legacy_auth_key_admin_enabled(self) -> bool:
        env_value = os.getenv("GMKRAW_LEGACY_AUTH_KEY_ADMIN_ENABLED")
        if env_value is not None:
            return _normalize_bool(env_value, False)
        security = self.data.get("security")
        if isinstance(security, dict) and "legacy_auth_key_admin_enabled" in security:
            return _normalize_bool(security.get("legacy_auth_key_admin_enabled"), False)
        return _normalize_bool(self.data.get("legacy_auth_key_admin_enabled"), False)

    @property
    def accounts_file(self) -> Path:
        return DATA_DIR / "accounts.json"

    @property
    def refresh_account_interval_minute(self) -> int:
        try:
            return int(self.data.get("refresh_account_interval_minute", 5))
        except (TypeError, ValueError):
            return 5

    @property
    def image_retention_days(self) -> int:
        try:
            return max(1, int(self.data.get("image_retention_days", 30)))
        except (TypeError, ValueError):
            return 30

    @property
    def image_poll_timeout_secs(self) -> int:
        try:
            return max(1, int(self.data.get("image_poll_timeout_secs", 120)))
        except (TypeError, ValueError):
            return 120

    @property
    def image_poll_interval_secs(self) -> float:
        try:
            return max(0.5, float(self.data.get("image_poll_interval_secs", 10.0)))
        except (TypeError, ValueError):
            return 10.0

    @property
    def image_poll_initial_wait_secs(self) -> float:
        """Image generation upstream takes ~30s; polling immediately wastes requests
        and trips a transient 429. Default 10s gives the conversation document time
        to commit before the first poll."""
        try:
            return max(0.0, float(self.data.get("image_poll_initial_wait_secs", 10.0)))
        except (TypeError, ValueError):
            return 10.0

    @property
    def image_account_concurrency(self) -> int:
        try:
            return max(1, int(self.data.get("image_account_concurrency", 3)))
        except (TypeError, ValueError):
            return 3

    @property
    def image_parallel_generation(self) -> bool:
        value = self.data.get("image_parallel_generation", True)
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    @property
    def image_settle_enabled(self) -> bool:
        """图片二次确认机制：找到 file_ids 后等待一段时间再次确认。"""
        value = self.data.get("image_settle_enabled", True)
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    @property
    def image_check_before_hit_enabled(self) -> bool:
        """先check再hit：通过轮询确认 file_ids 存在后再返回，而非仅依赖 SSE 事件。"""
        value = self.data.get("image_check_before_hit_enabled", True)
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    @property
    def image_remove_conversation_after_result(self) -> bool:
        """出图成功后异步隐藏 ChatGPT 本地对话记录。"""
        value = self.data.get("image_remove_conversation_after_result", False)
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    @property
    def image_settle_secs(self) -> float:
        """二次确认等待时间（秒）。"""
        try:
            return max(0.5, float(self.data.get("image_settle_secs", 2.0)))
        except (TypeError, ValueError):
            return 2.0

    @property
    def auto_remove_invalid_accounts(self) -> bool:
        value = self.data.get("auto_remove_invalid_accounts", False)
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    @property
    def auto_remove_rate_limited_accounts(self) -> bool:
        value = self.data.get("auto_remove_rate_limited_accounts", False)
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    @property
    def auto_relogin_after_refresh(self) -> bool:
        value = self.data.get("auto_relogin_after_refresh", False)
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    @property
    def log_levels(self) -> list[str]:
        levels = self.data.get("log_levels")
        if not isinstance(levels, list):
            return []
        allowed = {"debug", "info", "warning", "error"}
        return [level for item in levels if (level := str(item or "").strip().lower()) in allowed]

    @property
    def sensitive_words(self) -> list[str]:
        words = self.data.get("sensitive_words")
        return [word for item in words if (word := str(item or "").strip())] if isinstance(words, list) else []

    @property
    def ai_review(self) -> dict[str, object]:
        return _normalize_ai_review_settings(self.data.get("ai_review"))

    @property
    def global_system_prompt(self) -> str:
        return str(self.data.get("global_system_prompt") or "").strip()

    @property
    def images_dir(self) -> Path:
        path = DATA_DIR / "images"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def image_thumbnails_dir(self) -> Path:
        path = DATA_DIR / "image_thumbnails"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def cleanup_old_images(self) -> int:
        cutoff = time.time() - self.image_retention_days * 86400
        removed = 0
        for path in self.images_dir.rglob("*"):
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        for path in sorted((p for p in self.images_dir.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
            try:
                path.rmdir()
            except OSError:
                pass
        return removed

    @property
    def base_url(self) -> str:
        return str(
            os.getenv("GMKRAW_BASE_URL")
            or self.data.get("base_url")
            or ""
        ).strip().rstrip("/")

    @property
    def app_version(self) -> str:
        try:
            value = VERSION_FILE.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return "0.0.0"
        return value or "0.0.0"

    def get(self) -> dict[str, object]:
        data = dict(self.data)
        data["refresh_account_interval_minute"] = self.refresh_account_interval_minute
        data["image_retention_days"] = self.image_retention_days
        data["image_poll_timeout_secs"] = self.image_poll_timeout_secs
        data["image_poll_interval_secs"] = self.image_poll_interval_secs
        data["image_poll_initial_wait_secs"] = self.image_poll_initial_wait_secs
        data["image_account_concurrency"] = self.image_account_concurrency
        data["image_parallel_generation"] = self.image_parallel_generation
        data["image_remove_conversation_after_result"] = self.image_remove_conversation_after_result
        data["auto_remove_invalid_accounts"] = self.auto_remove_invalid_accounts
        data["auto_remove_rate_limited_accounts"] = self.auto_remove_rate_limited_accounts
        data["auto_relogin_after_refresh"] = self.auto_relogin_after_refresh
        data["log_levels"] = self.log_levels
        data["sensitive_words"] = self.sensitive_words
        data["proxy"] = _mask_url_password(self.get_proxy_settings())
        data["ai_review"] = self.get_public_ai_review_settings()
        data["global_system_prompt"] = self.global_system_prompt
        data["backup"] = self.get_public_backup_settings()
        data["image_storage"] = self.get_public_image_storage_settings()
        data["image_reference_upload"] = self.get_public_image_reference_upload_settings()
        data["proxy_runtime"] = self.get_public_proxy_runtime_settings()
        data["third_party_apps"] = self.get_third_party_apps_settings()
        data["openai_relay"] = self.get_public_openai_relay_settings()
        data["image_task_queue"] = self.get_public_image_task_queue_settings()
        data["legacy_auth_key_admin_enabled"] = self.legacy_auth_key_admin_enabled
        data.pop("auth-key", None)
        return data

    def get_proxy_settings(self) -> str:
        return str(self.data.get("proxy") or "").strip()

    def get_proxy_runtime_settings(self) -> dict[str, object]:
        return _normalize_proxy_runtime_settings(self.data.get("proxy_runtime"))

    def get_public_proxy_runtime_settings(self) -> dict[str, object]:
        runtime = copy.deepcopy(self.get_proxy_runtime_settings())
        runtime["proxy_url"] = _mask_url_password(str(runtime.get("proxy_url") or ""))
        runtime["resource_proxy_url"] = _mask_url_password(str(runtime.get("resource_proxy_url") or ""))
        clearance = runtime.get("clearance") if isinstance(runtime.get("clearance"), dict) else {}
        if isinstance(clearance, dict):
            cf_cookies = str(clearance.get("cf_cookies") or "").strip()
            cf_clearance = str(clearance.get("cf_clearance") or "").strip()
            clearance["cf_cookies"] = ""
            clearance["cf_clearance"] = ""
            clearance["has_cf_cookies"] = bool(cf_cookies)
            clearance["has_cf_clearance"] = bool(cf_clearance)
            clearance["flaresolverr_url"] = _mask_url_password(str(clearance.get("flaresolverr_url") or ""))
        return runtime

    def get_public_ai_review_settings(self) -> dict[str, object]:
        settings = dict(self.ai_review)
        api_key = str(settings.get("api_key") or "").strip()
        settings["api_key"] = ""
        settings["has_api_key"] = bool(api_key)
        return settings

    def get_image_reference_upload_settings(self) -> dict[str, object]:
        return _normalize_image_reference_upload_settings(self.data.get("image_reference_upload"))

    def get_public_image_reference_upload_settings(self) -> dict[str, object]:
        settings = dict(self.get_image_reference_upload_settings())
        oss_access_key = str(settings.get("oss_access_key") or "").strip()
        oss_secret_key = str(settings.get("oss_secret_key") or "").strip()
        settings["oss_access_key"] = ""
        settings["oss_secret_key"] = ""
        settings["has_oss_access_key"] = bool(oss_access_key)
        settings["has_oss_secret_key"] = bool(oss_secret_key)
        return settings

    def get_third_party_apps_settings(self) -> dict[str, object]:
        return _normalize_third_party_apps_settings(self.data.get("third_party_apps"))

    def get_openai_relay_settings(self) -> dict[str, object]:
        return _normalize_openai_relay_settings(self.data.get("openai_relay"))

    def get_public_openai_relay_settings(self) -> dict[str, object]:
        settings = dict(self.get_openai_relay_settings())
        api_key = str(settings.get("api_key") or "").strip()
        api_keys = _normalize_string_list(settings.get("api_keys"))
        settings["api_key"] = ""
        settings["api_keys"] = []
        configured_keys = {key for key in [api_key, *api_keys] if key}
        settings["has_api_key"] = bool(configured_keys)
        settings["api_key_count"] = len(configured_keys)
        return settings

    def get_image_task_queue_settings(self) -> dict[str, object]:
        return _normalize_image_task_queue_settings(self.data.get("image_task_queue"))

    def get_public_image_task_queue_settings(self) -> dict[str, object]:
        settings = dict(self.get_image_task_queue_settings())
        settings["redis_url"] = _mask_url_password(str(settings.get("redis_url") or ""))
        settings["database_url"] = _mask_url_password(str(settings.get("database_url") or ""))
        return settings

    def update(self, data: dict[str, object]) -> dict[str, object]:
        next_data = dict(self.data)
        next_data.update(dict(data or {}))
        if "backup" in next_data:
            next_data["backup"] = _normalize_backup_settings(next_data.get("backup"))
        if "image_storage" in next_data:
            next_data["image_storage"] = _normalize_image_storage_settings(next_data.get("image_storage"))
            _validate_image_storage_settings(next_data["image_storage"])
        if "image_reference_upload" in next_data:
            next_data["image_reference_upload"] = _normalize_image_reference_upload_settings(
                next_data.get("image_reference_upload")
            )
        if "ai_review" in next_data:
            next_data["ai_review"] = _normalize_ai_review_settings(next_data.get("ai_review"))
        next_data.pop("chat_completion_cache", None)
        if "third_party_apps" in next_data:
            next_data["third_party_apps"] = _normalize_third_party_apps_settings(next_data.get("third_party_apps"))
        if "openai_relay" in next_data:
            next_data["openai_relay"] = _normalize_openai_relay_settings(next_data.get("openai_relay"))
        if "image_task_queue" in next_data:
            next_data["image_task_queue"] = _normalize_image_task_queue_settings(next_data.get("image_task_queue"))
        if "proxy_runtime" in next_data:
            incoming_runtime = next_data.get("proxy_runtime")
            if isinstance(incoming_runtime, dict):
                previous_clearance = self.get_proxy_runtime_settings().get("clearance")
                if isinstance(previous_clearance, dict):
                    incoming_runtime = dict(incoming_runtime)
                    incoming_runtime["_existing_cf_cookies"] = previous_clearance.get("cf_cookies")
                    incoming_runtime["_existing_cf_clearance"] = previous_clearance.get("cf_clearance")
            next_data["proxy_runtime"] = _normalize_proxy_runtime_settings(incoming_runtime)
        next_data.pop("backup_state", None)
        next_data = _strip_environment_managed_secrets(next_data)
        embedded_secret_paths = find_embedded_secret_paths(next_data)
        if _normalize_bool(os.getenv("GMKRAW_STRICT_SECRET_SOURCES"), False) and embedded_secret_paths:
            joined_paths = ", ".join(embedded_secret_paths)
            raise ValueError(f"embedded secrets are not allowed in strict mode: {joined_paths}")
        self.data = next_data
        self.embedded_secret_paths = embedded_secret_paths
        self._save()
        return self.get()

    def get_backup_settings(self) -> dict[str, object]:
        return _normalize_backup_settings(self.data.get("backup"))

    def get_public_backup_settings(self) -> dict[str, object]:
        settings = dict(self.get_backup_settings())
        access_key_id = str(settings.get("access_key_id") or "").strip()
        secret_access_key = str(settings.get("secret_access_key") or "").strip()
        passphrase = str(settings.get("passphrase") or "").strip()
        settings["access_key_id"] = ""
        settings["secret_access_key"] = ""
        settings["passphrase"] = ""
        settings["has_access_key_id"] = bool(access_key_id)
        settings["has_secret_access_key"] = bool(secret_access_key)
        settings["has_passphrase"] = bool(passphrase)
        return settings

    def get_image_storage_settings(self) -> dict[str, object]:
        return _normalize_image_storage_settings(self.data.get("image_storage"))

    def get_public_image_storage_settings(self) -> dict[str, object]:
        settings = dict(self.get_image_storage_settings())
        webdav_password = str(settings.get("webdav_password") or "").strip()
        minio_access_key = str(settings.get("minio_access_key") or "").strip()
        minio_secret_key = str(settings.get("minio_secret_key") or "").strip()
        settings["webdav_password"] = ""
        settings["minio_access_key"] = ""
        settings["minio_secret_key"] = ""
        settings["webdav_url"] = _mask_url_password(str(settings.get("webdav_url") or ""))
        settings["has_webdav_password"] = bool(webdav_password)
        settings["has_minio_access_key"] = bool(minio_access_key)
        settings["has_minio_secret_key"] = bool(minio_secret_key)
        return settings

    def get_storage_backend(self) -> StorageBackend:
        """获取存储后端实例（单例）"""
        if self._storage_backend is None:
            from services.storage.factory import create_storage_backend
            self._storage_backend = create_storage_backend(DATA_DIR)
        return self._storage_backend


def load_backup_state() -> dict[str, object]:
    return _normalize_backup_state(_read_json_object(BACKUP_STATE_FILE, name="backup_state.json"))


def save_backup_state(state: dict[str, object]) -> dict[str, object]:
    normalized = _normalize_backup_state(state)
    BACKUP_STATE_FILE.write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return normalized


config = ConfigStore(CONFIG_FILE)
