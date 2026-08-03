from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, Header, HTTPException, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from fastapi.responses import FileResponse, HTMLResponse, Response

from api.support import extract_bearer_token, require_admin, require_identity
from services.captcha_service import captcha_service
from services.business_service import business_service
from services.config import config
from services.image_service import get_image_response, get_thumbnail_response
from services.user_service import AVATAR_DIR, AVATAR_EXTENSIONS, user_service


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=191)
    password: str = Field(..., min_length=6, max_length=128)
    name: str = Field(default="", max_length=191)
    captcha_id: str = Field(..., min_length=1)
    captcha_code: str = Field(..., min_length=1, max_length=12)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=128)
    new_password: str = Field(..., min_length=6, max_length=128)


class UserCreateRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=191)
    password: str = Field(..., min_length=6, max_length=128)
    name: str = Field(default="", max_length=191)
    role: str = "user"
    enabled: bool = True


class UserUpdateRequest(BaseModel):
    name: str | None = Field(default=None, max_length=191)
    password: str | None = Field(default=None, min_length=6, max_length=128)
    role: str | None = None
    enabled: bool | None = None


def _relay_configured() -> bool:
    relay = config.get_openai_relay_settings()
    api_key = str(relay.get("api_key") or "").strip()
    api_keys = relay.get("api_keys")
    has_api_keys = bool(api_keys) if isinstance(api_keys, list) else bool(str(api_keys or "").strip())
    return bool(relay.get("enabled") and relay.get("base_url") and (api_key or has_api_keys))


def _role_label(role: object) -> str:
    return "管理员" if str(role or "").strip().lower() == "admin" else "员工"


def _enabled_label(enabled: object) -> str:
    return "启用" if bool(enabled) else "停用"


def _user_label(user: dict[str, object] | None, fallback_id: str = "") -> str:
    if not user:
        return fallback_id
    username = str(user.get("username") or "").strip()
    name = str(user.get("name") or "").strip()
    user_id = str(user.get("id") or fallback_id).strip()
    label = name or username or user_id
    return f"{label} / {username or user_id}"


def _login_payload(identity: dict[str, object], token: str, app_version: str) -> dict[str, object]:
    return {
        "ok": True,
        "version": app_version,
        "role": identity.get("role"),
        "subject_id": identity.get("id"),
        "username": identity.get("username"),
        "name": identity.get("name"),
        "avatar_url": identity.get("avatar_url"),
        "token": token,
    }


def _record_user_audit(identity: dict[str, object], action: str, target_id: object, detail: str) -> None:
    try:
        business_service.record_audit_log(
            identity=identity,
            action=action,
            target_type="user",
            target_id=target_id,
            detail=detail,
        )
    except Exception:
        pass


def _create_user_detail(user: dict[str, object]) -> str:
    return "；".join(
        [
            f"创建账号：{_user_label(user)}",
            f"角色：{_role_label(user.get('role'))}",
            f"状态：{_enabled_label(user.get('enabled'))}",
        ]
    )


def _update_user_detail(before: dict[str, object] | None, after: dict[str, object], updates: dict[str, object]) -> str:
    changes: list[str] = []
    if before:
        if before.get("name") != after.get("name"):
            changes.append(f"姓名：{before.get('name') or '-'} -> {after.get('name') or '-'}")
        if before.get("role") != after.get("role"):
            changes.append(f"角色：{_role_label(before.get('role'))} -> {_role_label(after.get('role'))}")
        if before.get("enabled") != after.get("enabled"):
            changes.append(f"状态：{_enabled_label(before.get('enabled'))} -> {_enabled_label(after.get('enabled'))}")
    if updates.get("password"):
        changes.append("重置密码")
    if not changes:
        changes.append("无字段变化")
    return f"更新账号：{_user_label(after)}；" + "；".join(changes)


def create_router(app_version: str) -> APIRouter:
    router = APIRouter()

    @router.post("/auth/login")
    async def login(body: LoginRequest):
        result = await run_in_threadpool(user_service.authenticate_password, body.username, body.password)
        if result is None:
            raise HTTPException(status_code=401, detail={"error": "用户名或密码错误"})
        identity, token = result
        return _login_payload(identity, token, app_version)

    @router.get("/auth/captcha")
    async def captcha():
        return {"ok": True, **await run_in_threadpool(captcha_service.create)}

    @router.post("/auth/register")
    async def register(body: RegisterRequest):
        verified = await run_in_threadpool(captcha_service.verify, body.captcha_id, body.captcha_code)
        if not verified:
            raise HTTPException(status_code=400, detail={"error": "验证码错误或已过期"})
        try:
            identity, token = await run_in_threadpool(
                user_service.register_user,
                username=body.username,
                password=body.password,
                name=body.name,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        return _login_payload(identity, token, app_version)

    @router.get("/api/auth/me")
    async def current_user(authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        return {
            "ok": True,
            "role": identity.get("role"),
            "subject_id": identity.get("id"),
            "username": identity.get("username"),
            "name": identity.get("name"),
            "avatar_url": identity.get("avatar_url"),
        }

    @router.post("/api/auth/avatar")
    async def upload_avatar(avatar: UploadFile = File(...), authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        payload = await avatar.read()
        try:
            updated = await run_in_threadpool(
                user_service.set_avatar,
                str(identity.get("id") or ""),
                filename=avatar.filename or "",
                content_type=avatar.content_type or "",
                payload=payload,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        return {
            "ok": True,
            "role": updated.get("role"),
            "subject_id": updated.get("id"),
            "username": updated.get("username"),
            "name": updated.get("name"),
            "avatar_url": updated.get("avatar_url"),
        }

    @router.post("/api/auth/logout")
    async def logout(authorization: str | None = Header(default=None)):
        token = extract_bearer_token(authorization)
        if token:
            await run_in_threadpool(user_service.revoke_token, token)
        return {"ok": True}

    @router.post("/api/auth/change-password")
    async def change_password(body: ChangePasswordRequest, authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        token = extract_bearer_token(authorization)
        try:
            await run_in_threadpool(
                user_service.change_password,
                user_id=str(identity.get("id") or ""),
                current_password=body.current_password,
                new_password=body.new_password,
                revoke_token=token,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        return {"ok": True}

    @router.get("/api/users")
    async def list_users(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return await run_in_threadpool(user_service.list_users)

    @router.post("/api/users")
    async def create_user(body: UserCreateRequest, authorization: str | None = Header(default=None)):
        identity = require_admin(authorization)
        try:
            item = await run_in_threadpool(
                user_service.create_user,
                username=body.username,
                password=body.password,
                name=body.name,
                role=body.role,
                enabled=body.enabled,
            )
            await run_in_threadpool(
                _record_user_audit,
                identity,
                "create_user",
                item.get("id"),
                _create_user_detail(item),
            )
            return item
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.patch("/api/users/{user_id}")
    async def update_user(user_id: str, body: UserUpdateRequest, authorization: str | None = Header(default=None)):
        identity = require_admin(authorization)
        updates = body.model_dump(exclude_unset=True)
        before = await run_in_threadpool(user_service.get_user, user_id)
        try:
            item = await run_in_threadpool(
                user_service.update_user,
                user_id,
                updates,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        if item is None:
            raise HTTPException(status_code=404, detail={"error": "user not found"})
        await run_in_threadpool(
            _record_user_audit,
            identity,
            "update_user",
            item.get("id"),
            _update_user_detail(before, item, updates),
        )
        return item

    @router.delete("/api/users/{user_id}")
    async def disable_user(user_id: str, authorization: str | None = Header(default=None)):
        identity = require_admin(authorization)
        before = await run_in_threadpool(user_service.get_user, user_id)
        try:
            item = await run_in_threadpool(user_service.disable_user, user_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        if item is None:
            raise HTTPException(status_code=404, detail={"error": "user not found"})
        await run_in_threadpool(
            _record_user_audit,
            identity,
            "disable_user",
            item.get("id"),
            _update_user_detail(before, item, {"enabled": False}),
        )
        return item

    @router.get("/version")
    async def get_version():
        return {"version": app_version}

    @router.get("/api/settings")
    async def get_settings(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"config": config.get()}

    @router.get("/images/{image_path:path}", include_in_schema=False)
    async def get_image(image_path: str):
        return get_image_response(image_path)

    @router.get("/image-thumbnails/{image_path:path}", include_in_schema=False)
    async def get_image_thumbnail(image_path: str):
        return get_thumbnail_response(image_path)

    @router.get("/avatars/{avatar_path:path}", include_in_schema=False)
    async def get_avatar(avatar_path: str):
        filename = Path(avatar_path).name
        if filename != avatar_path or Path(filename).suffix.lower() not in AVATAR_EXTENSIONS:
            raise HTTPException(status_code=404, detail={"error": "avatar not found"})
        target = (AVATAR_DIR / filename).resolve()
        base = AVATAR_DIR.resolve()
        try:
            target.relative_to(base)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail={"error": "avatar not found"}) from exc
        if not target.is_file():
            raise HTTPException(status_code=404, detail={"error": "avatar not found"})
        return FileResponse(target)

    @router.get("/health", response_model=None)
    async def health_dashboard(format: str = Query(default="html")):
        payload = {
            "status": "ok",
            "healthy": True,
            "version": app_version,
            "mode": "image-only",
            "relay_enabled": _relay_configured(),
        }
        if format == "json":
            return payload
        return HTMLResponse(
            """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Image API Health</title>
  <style>
    body{margin:0;min-height:100vh;display:grid;place-items:center;background:#111;color:#f5f5f4;font-family:system-ui,-apple-system,sans-serif}
    main{border:1px solid #333;border-radius:16px;padding:28px 32px;background:#18181b}
    h1{margin:0 0 8px;font-size:22px}
    p{margin:0;color:#a8a29e}
    code{color:#86efac}
  </style>
</head>
<body>
  <main>
    <h1>Image-only API is running</h1>
    <p>mode: <code>image-only</code></p>
  </main>
</body>
</html>"""
        )

    return router
