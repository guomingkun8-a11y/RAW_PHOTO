from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
