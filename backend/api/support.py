from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException, Request

from services.auth_service import auth_service
from services.config import config
from services.user_service import user_service

BASE_DIR = Path(__file__).resolve().parents[2]
WEB_DIST_DIRS = [
    BASE_DIR / "frontend" / "dist",
    BASE_DIR / "web_dist",
]


def extract_bearer_token(authorization: str | None) -> str:
    scheme, _, value = str(authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return ""
    return value.strip()


def _legacy_admin_identity(token: str) -> dict[str, object] | None:
    if not config.legacy_auth_key_admin_enabled:
        return None
    auth_key = str(config.auth_key or "").strip()
    if auth_key and token == auth_key:
        return {"id": "local-admin", "username": "legacy-admin", "name": "管理员", "role": "admin"}
    return None


def require_identity(authorization: str | None) -> dict[str, object]:
    token = extract_bearer_token(authorization)
    if not token:
        raise HTTPException(status_code=401, detail={"error": "login required"})
    identity = user_service.authenticate_token(token) or _legacy_admin_identity(token) or auth_service.authenticate(token)
    if identity is None:
        raise HTTPException(status_code=401, detail={"error": "invalid or expired session"})
    return identity


def require_auth_key(authorization: str | None) -> None:
    require_identity(authorization)


def require_admin(authorization: str | None) -> dict[str, object]:
    identity = require_identity(authorization)
    if identity.get("role") != "admin":
        raise HTTPException(status_code=403, detail={"error": "admin role required"})
    return identity


def resolve_image_base_url(request: Request) -> str:
    return config.base_url or f"{request.url.scheme}://{request.headers.get('host', request.url.netloc)}"


def raise_image_quota_error(exc: Exception) -> None:
    message = str(exc)
    if "no available image quota" in message.lower():
        raise HTTPException(status_code=429, detail={"error": "no available image quota"}) from exc
    raise HTTPException(status_code=502, detail={"error": message}) from exc


def resolve_web_asset(requested_path: str) -> Path | None:
    clean_path = requested_path.strip("/")
    for web_dist_dir in WEB_DIST_DIRS:
        if not web_dist_dir.exists():
            continue
        base_dir = web_dist_dir.resolve()
        candidates = [base_dir / "index.html"] if not clean_path else [
            base_dir / Path(clean_path),
            base_dir / clean_path / "index.html",
            base_dir / f"{clean_path}.html",
        ]
        for candidate in candidates:
            try:
                candidate.resolve().relative_to(base_dir)
            except ValueError:
                continue
            if candidate.is_file():
                return candidate
    return None
