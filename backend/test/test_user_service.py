from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from services.user_service import DEFAULT_ADMIN_ID, UserService, UserSessionModel


class UserServiceTests(unittest.TestCase):
    def test_register_user_creates_enabled_user_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            database_url = f"sqlite:///{Path(tmp_dir) / 'users.db'}"
            service = UserService(database_url)

            identity, token = service.register_user(
                username="new-user",
                password="secret123",
                name="New User",
            )

            self.assertEqual(identity["username"], "new-user")
            self.assertEqual(identity["name"], "New User")
            self.assertEqual(identity["role"], "user")
            self.assertTrue(identity["enabled"])
            self.assertTrue(token.startswith("bt-"))
            self.assertEqual(service.authenticate_token(token)["username"], "new-user")
            service.close()

    def test_set_avatar_saves_image_and_replaces_old_extension(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            avatar_dir = Path(tmp_dir) / "avatars"
            database_url = f"sqlite:///{Path(tmp_dir) / 'users.db'}"
            with mock.patch("services.user_service.AVATAR_DIR", avatar_dir):
                service = UserService(database_url)
                identity, _token = service.register_user(
                    username="avatar-user",
                    password="secret123",
                    name="Avatar User",
                )
                user_id = str(identity["id"])

                updated = service.set_avatar(
                    user_id,
                    filename="avatar.png",
                    content_type="image/png",
                    payload=b"\x89PNG\r\n\x1a\navatar",
                )

                self.assertEqual(updated["avatar_url"], f"/avatars/{user_id}.png")
                self.assertTrue((avatar_dir / f"{user_id}.png").is_file())

                updated = service.set_avatar(
                    user_id,
                    filename="avatar.jpg",
                    content_type="image/jpeg",
                    payload=b"\xff\xd8\xffavatar",
                )

                self.assertEqual(updated["avatar_url"], f"/avatars/{user_id}.jpg")
                self.assertFalse((avatar_dir / f"{user_id}.png").exists())
                self.assertTrue((avatar_dir / f"{user_id}.jpg").is_file())
                service.close()

    def test_set_avatar_rejects_non_image_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            avatar_dir = Path(tmp_dir) / "avatars"
            database_url = f"sqlite:///{Path(tmp_dir) / 'users.db'}"
            with mock.patch("services.user_service.AVATAR_DIR", avatar_dir):
                service = UserService(database_url)
                identity, _token = service.register_user(
                    username="bad-avatar-user",
                    password="secret123",
                    name="Bad Avatar User",
                )

                with self.assertRaises(ValueError):
                    service.set_avatar(
                        str(identity["id"]),
                        filename="avatar.txt",
                        content_type="text/plain",
                        payload=b"not an image",
                    )
                service.close()

    def test_cleanup_expired_sessions_removes_expired_and_revoked_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            database_url = f"sqlite:///{Path(tmp_dir) / 'users.db'}"
            service = UserService(database_url)
            identity, token = service.register_user(
                username="cleanup-user",
                password="secret123",
                name="Cleanup User",
            )
            expired_token = "expired-token"
            expired_hash = hashlib.sha256(expired_token.encode("utf-8")).hexdigest()
            session = service._session()
            try:
                session.add(
                    UserSessionModel(
                        token_hash=expired_hash,
                        user_id=str(identity["id"]),
                        expires_at=datetime.now() - timedelta(minutes=1),
                        created_at=datetime.now() - timedelta(days=10),
                    )
                )
                session.commit()
            finally:
                session.close()

            self.assertTrue(service.revoke_token(token))
            removed = service.cleanup_expired_sessions()

            self.assertEqual(removed, 2)
            self.assertIsNone(service.authenticate_token(token))
            self.assertIsNone(service.authenticate_token(expired_token))
            service.close()

    def test_default_admin_cannot_be_demoted_or_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            database_url = f"sqlite:///{Path(tmp_dir) / 'users.db'}"
            service = UserService(database_url)

            with self.assertRaisesRegex(ValueError, "初始管理员"):
                service.update_user(DEFAULT_ADMIN_ID, {"role": "user"})

            with self.assertRaisesRegex(ValueError, "初始管理员"):
                service.update_user(DEFAULT_ADMIN_ID, {"enabled": False})

            admin = service.get_user(DEFAULT_ADMIN_ID)
            self.assertIsNotNone(admin)
            self.assertEqual(admin["role"], "admin")
            self.assertTrue(admin["enabled"])
            self.assertTrue(admin["protected"])
            service.close()

    def test_non_default_admin_can_be_changed_when_recovery_admin_remains(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            database_url = f"sqlite:///{Path(tmp_dir) / 'users.db'}"
            service = UserService(database_url)
            admin = service.create_user(
                username="team-admin",
                password="secret123",
                name="Team Admin",
                role="admin",
                enabled=True,
            )

            updated = service.update_user(str(admin["id"]), {"role": "user"})

            self.assertIsNotNone(updated)
            self.assertEqual(updated["role"], "user")
            self.assertTrue(service.get_user(DEFAULT_ADMIN_ID)["enabled"])
            service.close()

    def test_change_password_requires_current_password_and_revokes_current_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            database_url = f"sqlite:///{Path(tmp_dir) / 'users.db'}"
            service = UserService(database_url)
            identity, token = service.register_user(
                username="password-user",
                password="secret123",
                name="Password User",
            )

            with self.assertRaisesRegex(ValueError, "当前密码不正确"):
                service.change_password(
                    user_id=str(identity["id"]),
                    current_password="wrong123",
                    new_password="secret456",
                    revoke_token=token,
                )

            updated = service.change_password(
                user_id=str(identity["id"]),
                current_password="secret123",
                new_password="secret456",
                revoke_token=token,
            )

            self.assertEqual(updated["username"], "password-user")
            self.assertIsNone(service.authenticate_token(token))
            self.assertIsNone(service.authenticate_password("password-user", "secret123"))
            next_identity, next_token = service.authenticate_password("password-user", "secret456")
            self.assertEqual(next_identity["username"], "password-user")
            self.assertTrue(next_token.startswith("bt-"))
            service.close()


if __name__ == "__main__":
    unittest.main()
