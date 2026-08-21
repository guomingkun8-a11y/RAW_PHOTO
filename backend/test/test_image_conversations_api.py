from __future__ import annotations

import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.image_conversations as conversations_api


IDENTITY = {"id": "owner-1", "username": "tester", "role": "user"}


class ImageConversationsApiTests(unittest.TestCase):
    def setUp(self):
        mock.patch.object(conversations_api, "require_identity", return_value=IDENTITY).start()
        self.addCleanup(mock.patch.stopall)
        app = FastAPI()
        app.include_router(conversations_api.create_router())
        self.client = TestClient(app)

    def test_delete_also_deletes_only_matching_agent_memory(self):
        with (
            mock.patch.object(conversations_api.image_conversation_service, "delete_conversation", return_value=True),
            mock.patch.object(conversations_api.ecommerce_agent_memory_service, "delete_conversation", return_value=3) as delete_memory,
        ):
            response = self.client.delete("/api/image-conversations/conversation-1")

        self.assertEqual(200, response.status_code, response.text)
        delete_memory.assert_called_once_with(owner_id="owner-1", conversation_id="conversation-1")

    def test_clear_also_clears_only_current_owners_agent_memory(self):
        with (
            mock.patch.object(conversations_api.image_conversation_service, "clear_conversations", return_value=2),
            mock.patch.object(conversations_api.ecommerce_agent_memory_service, "clear_conversations", return_value=7) as clear_memory,
        ):
            response = self.client.delete("/api/image-conversations")

        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(2, response.json()["deleted"])
        clear_memory.assert_called_once_with(owner_id="owner-1")


if __name__ == "__main__":
    unittest.main()
