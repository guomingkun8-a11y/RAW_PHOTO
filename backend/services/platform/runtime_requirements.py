from __future__ import annotations

import os
from typing import Any


TRUE_VALUES = {"1", "true", "yes", "on"}
DATABASE_URL_KEYS = (
    "DATABASE_URL",
    "GMKRAW_DATABASE_URL",
    "MYSQL_DATABASE_URL",
    "IMAGE_TASK_DATABASE_URL",
    "IMAGE_LIBRARY_DATABASE_URL",
    "IMAGE_CONVERSATION_DATABASE_URL",
    "GMKRAW_PROFESSIONAL_KNOWLEDGE_DATABASE_URL",
)


def _enabled(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in TRUE_VALUES


def enterprise_mode_enabled() -> bool:
    return _enabled("GMKRAW_ENTERPRISE_MODE")


def validate_enterprise_runtime() -> dict[str, Any]:
    if not enterprise_mode_enabled():
        return {"enterpriseMode": False, "sqliteRejected": False, "vectorSearchRequired": False}

    errors: list[str] = []
    storage_backend = str(os.getenv("STORAGE_BACKEND") or "").strip().lower()
    if storage_backend in {"sqlite", "json", "git"}:
        errors.append(f"STORAGE_BACKEND={storage_backend} is not allowed in enterprise mode")

    configured_databases: dict[str, str] = {}
    for name in DATABASE_URL_KEYS:
        value = str(os.getenv(name) or "").strip()
        if not value:
            continue
        configured_databases[name] = value
        if value.lower().startswith("sqlite"):
            errors.append(f"{name} must not use SQLite in enterprise mode")
    if not any(name in configured_databases for name in ("GMKRAW_DATABASE_URL", "MYSQL_DATABASE_URL")):
        errors.append("GMKRAW_DATABASE_URL or MYSQL_DATABASE_URL is required in enterprise mode")

    if not _enabled("IMAGE_TASK_QUEUE_ENABLED"):
        errors.append("IMAGE_TASK_QUEUE_ENABLED=true is required in enterprise mode")
    if not str(os.getenv("IMAGE_TASK_REDIS_URL") or "").strip():
        errors.append("IMAGE_TASK_REDIS_URL is required in enterprise mode")
    if not _enabled("AGENT_QUEUE_ENABLED"):
        errors.append("AGENT_QUEUE_ENABLED=true is required in enterprise mode")
    if not str(os.getenv("AGENT_REDIS_URL") or "").strip():
        errors.append("AGENT_REDIS_URL is required in enterprise mode")

    vector_required = _enabled("GMKRAW_REQUIRE_VECTOR_SEARCH", True)
    if vector_required:
        if not str(os.getenv("QDRANT_URL") or os.getenv("GMKRAW_QDRANT_URL") or "").strip():
            errors.append("QDRANT_URL is required when enterprise vector search is enabled")
        if not str(os.getenv("QDRANT_API_KEY") or os.getenv("GMKRAW_QDRANT_API_KEY") or "").strip():
            errors.append("QDRANT_API_KEY is required when enterprise vector search is enabled")
        embedding_base = str(
            os.getenv("GMKRAW_EMBEDDING_BASE_URL")
            or os.getenv("GMKRAW_PROFESSIONAL_KNOWLEDGE_EMBEDDING_BASE_URL")
            or os.getenv("GMKRAW_OPENAI_RELAY_BASE_URL")
            or ""
        ).strip()
        if not embedding_base:
            errors.append("an OpenAI-compatible embedding base URL is required")
        embedding_model = str(
            os.getenv("GMKRAW_EMBEDDING_MODEL")
            or os.getenv("GMKRAW_PROFESSIONAL_KNOWLEDGE_EMBEDDING_MODEL")
            or ""
        ).strip()
        if not embedding_model:
            errors.append("GMKRAW_EMBEDDING_MODEL is required")

    if not _enabled("WEB_SECURITY_SSRF_PROTECTION"):
        errors.append("WEB_SECURITY_SSRF_PROTECTION=true is required in enterprise mode")

    if not _enabled("GMKRAW_IMAGE_REFERENCE_UPLOAD_ENABLED"):
        errors.append("GMKRAW_IMAGE_REFERENCE_UPLOAD_ENABLED=true is required in enterprise mode")
    for name in ("GMKRAW_OSS_ENDPOINT", "GMKRAW_OSS_ACCESS_KEY_ID", "GMKRAW_OSS_ACCESS_KEY_SECRET", "GMKRAW_OSS_BUCKET"):
        if not str(os.getenv(name) or "").strip():
            errors.append(f"{name} is required in enterprise mode")

    if not _enabled("GMKRAW_IMAGE_STORAGE_ENABLED"):
        errors.append("GMKRAW_IMAGE_STORAGE_ENABLED=true is required in enterprise mode")
    image_storage_mode = str(os.getenv("GMKRAW_IMAGE_STORAGE_MODE") or "").strip().lower()
    if image_storage_mode not in {"remote", "both"}:
        errors.append("GMKRAW_IMAGE_STORAGE_MODE must be remote or both in enterprise mode")
    image_storage_provider = str(os.getenv("GMKRAW_IMAGE_STORAGE_PROVIDER") or "").strip().lower()
    if image_storage_provider == "minio":
        for name in ("GMKRAW_MINIO_ENDPOINT", "GMKRAW_MINIO_ACCESS_KEY", "GMKRAW_MINIO_SECRET_KEY", "GMKRAW_MINIO_BUCKET"):
            if not str(os.getenv(name) or "").strip():
                errors.append(f"{name} is required for enterprise MinIO/S3 storage")
    elif image_storage_provider == "webdav":
        if not str(os.getenv("GMKRAW_WEBDAV_URL") or "").strip():
            errors.append("GMKRAW_WEBDAV_URL is required for enterprise WebDAV storage")
    else:
        errors.append("GMKRAW_IMAGE_STORAGE_PROVIDER must be minio or webdav in enterprise mode")

    if errors:
        raise RuntimeError("enterprise runtime validation failed: " + "; ".join(errors))
    return {
        "enterpriseMode": True,
        "sqliteRejected": True,
        "vectorSearchRequired": vector_required,
        "persistentQueuesRequired": True,
        "objectStorageRequired": True,
        "databaseKeys": sorted(configured_databases),
    }
