from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from pathlib import Path
from datetime import datetime, timedelta
from typing import Any, Literal

from sqlalchemy import Column, DateTime, String, Text, create_engine, desc, or_, text
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()

DEFAULT_DATABASE_URL = "mysql+pymysql://root:root@127.0.0.1:3306/raw_photo?charset=utf8mb4"
DEFAULT_ADMIN_ID = "local-admin"
DEFAULT_ADMIN_USERNAME = "admin"
DEFAULT_ADMIN_PASSWORD = "admin123456"
PASSWORD_ITERATIONS = 260_000
SESSION_DAYS = 7
SESSION_TOUCH_INTERVAL_SECONDS = 60
AVATAR_DIR = Path(__file__).resolve().parents[2] / "data" / "avatars"
AVATAR_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".gif")

AuthRole = Literal["admin", "user"]


def _database_url() -> str:
    return (
        os.getenv("IMAGE_LIBRARY_DATABASE_URL")
        or os.getenv("MYSQL_DATABASE_URL")
        or DEFAULT_DATABASE_URL
    )


def _clean(value: object, default: str = "") -> str:
    text = str(value if value is not None else default).strip()
    return text or default


def _enabled_value(value: object, current: bool) -> bool:
    if value is None:
        return current
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}
    return bool(value)


def _now() -> datetime:
    return datetime.now()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS)
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${salt.hex()}${digest.hex()}"


def _avatar_url(user_id: str) -> str:
    clean_id = _clean(user_id)
    if not clean_id:
        return ""
    for extension in AVATAR_EXTENSIONS:
        if (AVATAR_DIR / f"{clean_id}{extension}").is_file():
            return f"/avatars/{clean_id}{extension}"
    return ""


def _detect_avatar_extension(payload: bytes) -> str:
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if payload.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if payload.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if len(payload) >= 12 and payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
        return ".webp"
    return ""


def _verify_password(password: str, password_hash: str) -> bool:
    try:
        algorithm, iterations, salt_hex, digest_hex = password_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt_hex),
            int(iterations),
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except Exception:
        return False


class UserModel(Base):
    __tablename__ = "business_users"

    id = Column(String(191), primary_key=True)
    username = Column(String(191), nullable=False, unique=True)
    name = Column(String(191), nullable=False)
    role = Column(String(32), nullable=False, default="user")
    password_hash = Column(Text, nullable=False)
    enabled = Column(String(8), nullable=False, default="1")
    last_login_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_now)
    updated_at = Column(DateTime, nullable=False, default=_now, onupdate=_now)


class UserSessionModel(Base):
    __tablename__ = "business_user_sessions"

    token_hash = Column(String(64), primary_key=True)
    user_id = Column(String(191), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    revoked_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_now)
    last_used_at = Column(DateTime, nullable=True)


class UserService:
    def __init__(self, database_url: str | None = None):
        self.database_url = database_url or _database_url()
        self.engine = None
        self.Session = None
        self._init_error = ""
        self._init_engine()

    def _init_engine(self) -> None:
        try:
            engine = create_engine(self.database_url, pool_pre_ping=True, pool_recycle=3600)
            Base.metadata.create_all(engine)
            self._ensure_indexes(engine)
            self.engine = engine
            self.Session = sessionmaker(bind=engine)
            self._init_error = ""
            self._ensure_default_admin()
        except Exception as exc:
            self.engine = None
            self.Session = None
            self._init_error = str(exc)

    def _ensure_indexes(self, engine) -> None:
        if engine.dialect.name != "mysql":
            return
        with engine.begin() as connection:
            for statement in (
                "CREATE INDEX idx_users_role_enabled ON business_users (role, enabled)",
                "CREATE INDEX idx_sessions_user_expires ON business_user_sessions (user_id, expires_at)",
                "CREATE INDEX idx_sessions_expires_revoked ON business_user_sessions (expires_at, revoked_at)",
                "CREATE INDEX idx_sessions_active_user_seen ON business_user_sessions (revoked_at, expires_at, user_id, last_used_at)",
            ):
                try:
                    connection.execute(text(statement))
                except Exception:
                    pass

    def _session(self):
        if self.Session is None:
            self._init_engine()
        if self.Session is None:
            raise RuntimeError(f"user database unavailable: {self._init_error}")
        return self.Session()

    def close(self) -> None:
        if self.engine is not None:
            self.engine.dispose()

    def _ensure_default_admin(self) -> None:
        session = self._session()
        try:
            count = session.query(UserModel).count()
            if count > 0:
                return
            session.add(
                UserModel(
                    id=DEFAULT_ADMIN_ID,
                    username=DEFAULT_ADMIN_USERNAME,
                    name="管理员",
                    role="admin",
                    password_hash=_hash_password(DEFAULT_ADMIN_PASSWORD),
                    enabled="1",
                )
            )
            session.commit()
        except Exception:
            session.rollback()
        finally:
            session.close()

    def authenticate_password(self, username: str, password: str) -> tuple[dict[str, object], str] | None:
        normalized_username = _clean(username)
        if not normalized_username or not password:
            return None
        session = self._session()
        try:
            user = session.query(UserModel).filter(UserModel.username == normalized_username).one_or_none()
            if user is None or user.enabled != "1":
                return None
            if not _verify_password(password, user.password_hash):
                return None
            token = f"bt-{secrets.token_urlsafe(36)}"
            now = _now()
            user.last_login_at = now
            user.updated_at = now
            session.add(
                UserSessionModel(
                    token_hash=_hash_token(token),
                    user_id=user.id,
                    expires_at=now + timedelta(days=SESSION_DAYS),
                    created_at=now,
                    last_used_at=now,
                )
            )
            session.commit()
            return self._public_user(user), token
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def authenticate_token(self, token: str) -> dict[str, object] | None:
        normalized = _clean(token)
        if not normalized:
            return None
        session = self._session()
        try:
            row = session.query(UserSessionModel).filter(UserSessionModel.token_hash == _hash_token(normalized)).one_or_none()
            now = _now()
            if row is None or row.revoked_at is not None or row.expires_at <= now:
                return None
            user = session.query(UserModel).filter(UserModel.id == row.user_id).one_or_none()
            if user is None or user.enabled != "1":
                return None
            last_used_at = row.last_used_at or row.created_at
            if last_used_at is None or (now - last_used_at).total_seconds() >= SESSION_TOUCH_INTERVAL_SECONDS:
                row.last_used_at = now
                session.commit()
            return self._public_user(user)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def cleanup_expired_sessions(self, *, now: datetime | None = None) -> int:
        session = self._session()
        cutoff = now or _now()
        try:
            removed = (
                session.query(UserSessionModel)
                .filter(
                    or_(
                        UserSessionModel.expires_at <= cutoff,
                        UserSessionModel.revoked_at.isnot(None),
                    )
                )
                .delete(synchronize_session=False)
            )
            session.commit()
            return int(removed or 0)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def revoke_token(self, token: str) -> bool:
        normalized = _clean(token)
        if not normalized:
            return False
        session = self._session()
        try:
            row = session.query(UserSessionModel).filter(UserSessionModel.token_hash == _hash_token(normalized)).one_or_none()
            if row is None:
                return False
            row.revoked_at = _now()
            session.commit()
            return True
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def change_password(self, *, user_id: str, current_password: str, new_password: str, revoke_token: str = "") -> dict[str, object]:
        normalized_id = _clean(user_id)
        current_password = _clean(current_password)
        new_password = _clean(new_password)
        if not normalized_id:
            raise ValueError("用户不存在")
        if not current_password:
            raise ValueError("请输入当前密码")
        if len(new_password) < 6:
            raise ValueError("新密码至少 6 位")
        if current_password == new_password:
            raise ValueError("新密码不能和当前密码相同")

        session = self._session()
        try:
            user = session.query(UserModel).filter(UserModel.id == normalized_id).one_or_none()
            if user is None or user.enabled != "1":
                raise ValueError("用户不存在或已停用")
            if not _verify_password(current_password, user.password_hash):
                raise ValueError("当前密码不正确")
            user.password_hash = _hash_password(new_password)
            user.updated_at = _now()
            token_hash = _hash_token(revoke_token) if _clean(revoke_token) else ""
            if token_hash:
                row = session.query(UserSessionModel).filter(UserSessionModel.token_hash == token_hash).one_or_none()
                if row is not None:
                    row.revoked_at = _now()
            session.commit()
            return self._public_user(user)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def list_users(self) -> dict[str, Any]:
        session = self._session()
        try:
            rows = session.query(UserModel).order_by(desc(UserModel.created_at)).all()
            return {"items": [self._public_user(row) for row in rows], "total": len(rows)}
        finally:
            session.close()

    def get_user(self, user_id: str) -> dict[str, object] | None:
        normalized_id = _clean(user_id)
        if not normalized_id:
            return None
        session = self._session()
        try:
            row = session.query(UserModel).filter(UserModel.id == normalized_id).one_or_none()
            return self._public_user(row) if row is not None else None
        finally:
            session.close()

    def create_user(self, *, username: str, password: str, name: str, role: str = "user", enabled: bool = True) -> dict[str, object]:
        username = _clean(username)
        password = _clean(password)
        name = _clean(name) or username
        role = "admin" if _clean(role).lower() == "admin" else "user"
        if not username:
            raise ValueError("用户名不能为空")
        if len(password) < 6:
            raise ValueError("密码至少 6 位")
        session = self._session()
        try:
            if session.query(UserModel).filter(UserModel.username == username).one_or_none() is not None:
                raise ValueError("用户名已存在")
            user = UserModel(
                id=secrets.token_hex(8),
                username=username,
                name=name,
                role=role,
                password_hash=_hash_password(password),
                enabled="1" if enabled else "0",
            )
            session.add(user)
            session.commit()
            return self._public_user(user)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def register_user(self, *, username: str, password: str, name: str = "") -> tuple[dict[str, object], str]:
        username = _clean(username)
        password = _clean(password)
        self.create_user(
            username=username,
            password=password,
            name=name,
            role="user",
            enabled=True,
        )
        result = self.authenticate_password(username, password)
        if result is None:
            raise RuntimeError("registered user could not be authenticated")
        return result

    def update_user(self, user_id: str, updates: dict[str, object]) -> dict[str, object] | None:
        normalized_id = _clean(user_id)
        session = self._session()
        try:
            user = session.query(UserModel).filter(UserModel.id == normalized_id).one_or_none()
            if user is None:
                return None
            if user.id == DEFAULT_ADMIN_ID:
                next_role = "admin" if _clean(updates.get("role"), user.role).lower() == "admin" else "user"
                next_enabled = _enabled_value(updates.get("enabled"), user.enabled == "1")
                if next_role != "admin" or not next_enabled:
                    raise ValueError("初始管理员账号不能降级或停用")
            else:
                next_role = "admin" if _clean(updates.get("role"), user.role).lower() == "admin" else "user"
                next_enabled = _enabled_value(updates.get("enabled"), user.enabled == "1")
                if user.role == "admin" and user.enabled == "1" and (next_role != "admin" or not next_enabled):
                    other_enabled_admins = (
                        session.query(UserModel)
                        .filter(
                            UserModel.id != user.id,
                            UserModel.role == "admin",
                            UserModel.enabled == "1",
                        )
                        .count()
                    )
                    if other_enabled_admins < 1:
                        raise ValueError("至少需要保留一个启用的管理员账号")
            if "name" in updates:
                user.name = _clean(updates.get("name")) or user.username
            if "role" in updates and user.id != DEFAULT_ADMIN_ID:
                user.role = "admin" if _clean(updates.get("role")).lower() == "admin" else "user"
            if "enabled" in updates and user.id != DEFAULT_ADMIN_ID:
                user.enabled = "1" if _enabled_value(updates.get("enabled"), user.enabled == "1") else "0"
            if "password" in updates and _clean(updates.get("password")):
                password = _clean(updates.get("password"))
                if len(password) < 6:
                    raise ValueError("密码至少 6 位")
                user.password_hash = _hash_password(password)
            user.updated_at = _now()
            session.commit()
            return self._public_user(user)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def disable_user(self, user_id: str) -> dict[str, object] | None:
        return self.update_user(user_id, {"enabled": False})

    def set_avatar(self, user_id: str, *, filename: str, content_type: str, payload: bytes) -> dict[str, object]:
        normalized_id = _clean(user_id)
        if not normalized_id:
            raise ValueError("用户不存在")
        if not payload:
            raise ValueError("头像文件不能为空")
        if len(payload) > 2 * 1024 * 1024:
            raise ValueError("头像不能超过 2MB")

        extension = _detect_avatar_extension(payload)
        if not extension:
            raise ValueError("只支持 PNG、JPG、WEBP 或 GIF 头像")

        session = self._session()
        try:
            user = session.query(UserModel).filter(UserModel.id == normalized_id).one_or_none()
            if user is None or user.enabled != "1":
                raise ValueError("用户不存在或已停用")
            AVATAR_DIR.mkdir(parents=True, exist_ok=True)
            for old_extension in AVATAR_EXTENSIONS:
                old_path = AVATAR_DIR / f"{normalized_id}{old_extension}"
                if old_path.exists() and old_extension != extension:
                    old_path.unlink()
            target = AVATAR_DIR / f"{normalized_id}{extension}"
            target.write_bytes(payload)
            user.updated_at = _now()
            session.commit()
            return self._public_user(user)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    @staticmethod
    def _public_user(row: UserModel) -> dict[str, object]:
        return {
            "id": row.id,
            "username": row.username,
            "name": row.name,
            "role": row.role,
            "enabled": row.enabled == "1",
            "protected": row.id == DEFAULT_ADMIN_ID,
            "created_at": row.created_at.strftime("%Y-%m-%d %H:%M:%S") if row.created_at else "",
            "updated_at": row.updated_at.strftime("%Y-%m-%d %H:%M:%S") if row.updated_at else "",
            "last_login_at": row.last_login_at.strftime("%Y-%m-%d %H:%M:%S") if row.last_login_at else "",
            "avatar_url": _avatar_url(row.id),
        }


user_service = UserService()
