from __future__ import annotations

import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.system as system_module


AUTH_HEADERS = {"Authorization": "Bearer test-token"}
TEST_IDENTITY = {
    "id": "user-1",
    "username": "avatar-user",
    "name": "Avatar User",
    "role": "user",
    "avatar_url": "/avatars/user-1.png",
}


class SystemAuthApiTests(unittest.TestCase):
    def setUp(self) -> None:
        app = FastAPI()
        app.include_router(system_module.create_router("test"))
        self.client = TestClient(app)

    def test_current_user_includes_avatar_url(self) -> None:
        with mock.patch.object(system_module, "require_identity", return_value=TEST_IDENTITY):
            response = self.client.get("/api/auth/me", headers=AUTH_HEADERS)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["subject_id"], "user-1")
        self.assertEqual(payload["avatar_url"], "/avatars/user-1.png")

    def test_upload_avatar_returns_updated_current_user(self) -> None:
        updated = {
            "id": "user-1",
            "username": "avatar-user",
            "name": "Avatar User",
            "role": "user",
            "avatar_url": "/avatars/user-1.jpg",
        }
        with (
            mock.patch.object(system_module, "require_identity", return_value=TEST_IDENTITY),
            mock.patch.object(system_module.user_service, "set_avatar", return_value=updated) as set_avatar,
        ):
            response = self.client.post(
                "/api/auth/avatar",
                headers=AUTH_HEADERS,
                files={"avatar": ("avatar.jpg", b"\xff\xd8\xffavatar", "image/jpeg")},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["subject_id"], "user-1")
        self.assertEqual(payload["avatar_url"], "/avatars/user-1.jpg")
        self.assertEqual(set_avatar.call_args.args[0], "user-1")
        self.assertEqual(set_avatar.call_args.kwargs["filename"], "avatar.jpg")
        self.assertEqual(set_avatar.call_args.kwargs["content_type"], "image/jpeg")
        self.assertEqual(set_avatar.call_args.kwargs["payload"], b"\xff\xd8\xffavatar")


if __name__ == "__main__":
    unittest.main()
