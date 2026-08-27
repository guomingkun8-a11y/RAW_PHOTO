from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
import json

from services.image.image_conversation_service import ImageConversationModel, ImageConversationService


class ImageConversationServiceTests(unittest.TestCase):
    def test_conversations_are_limited_to_current_owner(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = ImageConversationService(f"sqlite:///{Path(tmp_dir) / 'conversations.db'}")
            try:
                service.upsert_conversation(
                    identity={"id": "owner-1", "role": "user"},
                    conversation_id="conversation-1",
                    payload={"id": "conversation-1", "title": "owner one", "turns": []},
                )
                service.upsert_conversation(
                    identity={"id": "owner-2", "role": "admin"},
                    conversation_id="conversation-2",
                    payload={"id": "conversation-2", "title": "owner two", "turns": []},
                )

                first = service.list_conversations(identity={"id": "owner-1", "role": "user"})
                second = service.list_conversations(identity={"id": "owner-2", "role": "admin"})

                self.assertEqual(first["total"], 1)
                self.assertEqual(first["items"][0]["title"], "owner one")
                self.assertEqual(second["total"], 1)
                self.assertEqual(second["items"][0]["title"], "owner two")
            finally:
                service.engine.dispose()

    def test_delete_only_affects_current_owner(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = ImageConversationService(f"sqlite:///{Path(tmp_dir) / 'conversations.db'}")
            try:
                service.upsert_conversation(
                    identity={"id": "owner-1", "role": "user"},
                    conversation_id="shared-title",
                    payload={"id": "shared-title", "title": "owner one", "turns": []},
                )
                service.upsert_conversation(
                    identity={"id": "owner-2", "role": "user"},
                    conversation_id="shared-title",
                    payload={"id": "shared-title", "title": "owner two", "turns": []},
                )

                deleted = service.delete_conversation(
                    identity={"id": "owner-1", "role": "user"},
                    conversation_id="shared-title",
                )

                self.assertTrue(deleted)
                self.assertEqual(service.list_conversations(identity={"id": "owner-1", "role": "user"})["total"], 0)
                self.assertEqual(service.list_conversations(identity={"id": "owner-2", "role": "user"})["total"], 1)
            finally:
                service.engine.dispose()

    def test_upsert_slims_inline_image_payload_before_persisting(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = ImageConversationService(f"sqlite:///{Path(tmp_dir) / 'conversations.db'}")
            try:
                payload = {
                    "id": "conversation-1",
                    "title": "inline image",
                    "turns": [
                        {
                            "id": "turn-1",
                            "referenceImages": [
                                {
                                    "name": "ref.png",
                                    "type": "image/png",
                                    "dataUrl": "data:image/png;base64," + ("a" * 4096),
                                },
                                {
                                    "name": "uploaded-ref.png",
                                    "type": "image/png",
                                    "url": "https://example.test/uploads/uploaded-ref.png",
                                    "dataUrl": "data:image/png;base64," + ("b" * 4096),
                                }
                            ],
                            "images": [
                                {
                                    "id": "img-1",
                                    "b64_json": "aGVsbG8=",
                                    "url": "http://app.test/images/result.png",
                                }
                            ],
                        }
                    ],
                }

                service.upsert_conversation(
                    identity={"id": "owner-1", "role": "user"},
                    conversation_id="conversation-1",
                    payload=payload,
                )

                session = service._session()
                try:
                    row = session.query(ImageConversationModel).one()
                    stored = json.loads(row.payload_json)
                finally:
                    session.close()

                turn = stored["turns"][0]
                self.assertNotIn("b64_json", json.dumps(stored))
                self.assertNotIn("dataUrl", json.dumps(stored))
                self.assertEqual(turn["images"][0]["url"], "http://app.test/images/result.png")
                self.assertEqual(
                    turn["referenceImages"][1]["url"],
                    "https://example.test/uploads/uploaded-ref.png",
                )
            finally:
                service.engine.dispose()

    def test_stale_upsert_does_not_overwrite_newer_conversation_turns(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = ImageConversationService(f"sqlite:///{Path(tmp_dir) / 'conversations.db'}")
            try:
                identity = {"id": "owner-1", "role": "user"}
                newer = {
                    "id": "conversation-1",
                    "title": "two turns",
                    "updatedAt": "2026-08-16T07:00:02.000Z",
                    "turns": [{"id": "turn-1"}, {"id": "turn-2"}],
                }
                stale = {
                    "id": "conversation-1",
                    "title": "one turn",
                    "updatedAt": "2026-08-16T07:00:01.000Z",
                    "turns": [{"id": "turn-1"}],
                }

                service.upsert_conversation(
                    identity=identity,
                    conversation_id="conversation-1",
                    payload=newer,
                )
                result = service.upsert_conversation(
                    identity=identity,
                    conversation_id="conversation-1",
                    payload=stale,
                )

                self.assertEqual(result["title"], "two turns")
                self.assertEqual([turn["id"] for turn in result["turns"]], ["turn-1", "turn-2"])
                stored = service.list_conversations(identity=identity)["items"][0]
                self.assertEqual(stored["updatedAt"], newer["updatedAt"])
                self.assertEqual(len(stored["turns"]), 2)
            finally:
                service.engine.dispose()


if __name__ == "__main__":
    unittest.main()
