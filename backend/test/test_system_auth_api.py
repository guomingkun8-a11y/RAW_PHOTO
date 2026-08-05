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
ADMIN_IDENTITY = {
    "id": "admin-1",
    "username": "admin",
    "name": "管理员",
    "role": "admin",
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

    def test_list_announcements_requires_login_and_returns_items(self) -> None:
        result = {"items": [{"id": 1, "title": "更新", "content": "优化图片生成页", "type": "info", "enabled": True}], "total": 1}
        with (
            mock.patch.object(system_module, "require_identity", return_value=TEST_IDENTITY),
            mock.patch.object(system_module.system_announcement_service, "list_announcements", return_value=result) as list_announcements,
        ):
            response = self.client.get("/api/system/announcements", headers=AUTH_HEADERS)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"][0]["title"], "更新")
        self.assertFalse(list_announcements.call_args.kwargs["include_disabled"])

    def test_create_announcement_requires_admin(self) -> None:
        with mock.patch.object(system_module, "require_admin", side_effect=system_module.HTTPException(status_code=403, detail={"error": "admin role required"})):
            response = self.client.post(
                "/api/system/announcements",
                headers=AUTH_HEADERS,
                json={"title": "更新", "content": "新增公告", "type": "success"},
            )

        self.assertEqual(response.status_code, 403)

    def test_admin_can_create_announcement(self) -> None:
        created = {"id": 7, "title": "更新", "content": "新增公告", "type": "success", "enabled": True}
        with (
            mock.patch.object(system_module, "require_admin", return_value=ADMIN_IDENTITY),
            mock.patch.object(system_module.system_announcement_service, "create_announcement", return_value=created) as create_announcement,
            mock.patch.object(system_module, "_record_system_audit"),
        ):
            response = self.client.post(
                "/api/system/announcements",
                headers=AUTH_HEADERS,
                json={"title": "更新", "content": "新增公告", "type": "success"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["id"], 7)
        self.assertEqual(create_announcement.call_args.kwargs["created_by"], "admin-1")


if __name__ == "__main__":
    unittest.main()
