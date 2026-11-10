from __future__ import annotations

import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.dsh_agent import routes
from services.dsh_agent.files import DshFileService, MAX_FILE_BYTES, MAX_TEXT_CHARS
from services.dsh_agent.image_service import DshImageService
from services.dsh_agent.store import DshAgentStore


class DshFileTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = DshAgentStore(f"sqlite:///{Path(tmp.name) / 'files.db'}", dsh_home=Path(tmp.name) / "home")
        self.addCleanup(self.store.close)
        self.cid = self.store.create_conversation({"id": "alice"})["conversation_id"]
        self.service = DshFileService(self.store)

    def upload(self, data=b"product code: 42", name="product.txt", cid=None):
        return self.service.upload(cid or self.cid, "alice", data, name)

    def test_text_types_are_read_and_private_text_is_not_exposed(self):
        for suffix in ("txt", "md", "csv", "json", "log"):
            with self.subTest(suffix=suffix):
                item = self.upload("商品编号42".encode(), f"../product.{suffix}")
                self.assertEqual(f"product.{suffix}", item["filename"])
                self.assertEqual("file", item["kind"])
                self.assertNotIn("_text", item)
        history = DshImageService(self.store).attach_messages(self.store.list_messages(self.cid), {"id": "alice"})
        self.assertEqual(5, len(history["draft_references"]))
        self.assertNotIn("商品编号42", json.dumps(history, ensure_ascii=False))

    def test_docx_paragraphs_tables_and_pdf_text(self):
        from docx import Document
        from pypdf import PdfWriter
        from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

        document = Document()
        document.add_paragraph("Product description")
        document.add_table(rows=1, cols=1).cell(0, 0).text = "code 42"
        data = io.BytesIO()
        document.save(data)
        item = self.upload(data.getvalue(), "product.docx")
        claim = self.store.begin_turn(self.cid, "alice", "read", file_ids=[item["media_id"]])
        text = self.service.prompt("read", self.cid, "alice", claim["user_message_id"])
        self.assertIn("Product description", text)
        self.assertIn("code 42", text)
        self.store.complete_turn(self.cid, "alice", content="done", model="tt-5.6-sol", usage={})

        writer = PdfWriter()
        page = writer.add_blank_page(width=200, height=200)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        stream = DecodedStreamObject()
        stream.set_data(b"BT /F1 12 Tf 20 20 Td (product code 42) Tj ET")
        page[NameObject("/Contents")] = stream
        data = io.BytesIO()
        writer.write(data)
        item = self.upload(data.getvalue(), "product.pdf")
        claim = self.store.begin_turn(self.cid, "alice", "read", file_ids=[item["media_id"]])
        self.assertIn("product code 42", self.service.prompt("read", self.cid, "alice", claim["user_message_id"]))

    def test_invalid_encrypted_scanned_and_oversize_files_are_rejected(self):
        from pypdf import PdfWriter

        for data, name in ((b"", "empty.txt"), (b"x" * (MAX_FILE_BYTES + 1), "big.txt"),
                           (b"not pdf", "bad.pdf"), (b"not zip", "bad.docx"), (b"x\x00x", "binary.txt"),
                           (b"anything", "bad.exe")):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.upload(data, name)
        for encrypted in (False, True):
            writer = PdfWriter()
            writer.add_blank_page(width=100, height=100)
            if encrypted:
                writer.encrypt("secret")
            data = io.BytesIO()
            writer.write(data)
            with self.subTest(encrypted=encrypted), self.assertRaises(ValueError):
                self.upload(data.getvalue(), "scan.pdf")

    def test_binding_is_atomic_owned_and_cannot_reuse_or_become_image_reference(self):
        item = self.upload()
        second = self.store.create_conversation({"id": "alice"})["conversation_id"]
        for cid, owner, ids in ((self.cid, "bob", [item["media_id"]]), (second, "alice", [item["media_id"]]),
                                (self.cid, "alice", [item["media_id"], "forged"]),
                                (self.cid, "alice", [item["media_id"]] * 2),
                                (self.cid, "alice", [item["media_id"]] * 5)):
            with self.subTest(cid=cid, owner=owner):
                if owner == "bob":
                    self.assertEqual("not_found", self.store.begin_turn(cid, owner, "read", file_ids=ids)["state"])
                else:
                    with self.assertRaises(ValueError):
                        self.store.begin_turn(cid, owner, "read", file_ids=ids)
        self.assertEqual([], self.store.list_messages(self.cid)["items"])
        with self.assertRaises(ValueError):
            self.store.begin_turn(self.cid, "alice", "read", reference_ids=[item["media_id"]])
        self.assertFalse(self.service.media.discard_reference(self.cid, "bob", item["media_id"], kind="file"))
        claim = self.store.begin_turn(self.cid, "alice", "read", file_ids=[item["media_id"]])
        self.assertIn("product code: 42", self.service.prompt("read", self.cid, "alice", claim["user_message_id"]))
        self.assertEqual("read", self.service.prompt("read", self.cid, "bob", claim["user_message_id"]))
        self.assertEqual("read", self.service.prompt("read", self.cid, "alice", "unrelated"))
        self.assertFalse(self.service.media.discard_reference(self.cid, "alice", item["media_id"], kind="file"))
        with self.assertRaises(ValueError):
            DshImageService(self.store).reference_inputs([item["media_id"]], [item])
        self.store.complete_turn(self.cid, "alice", content="done", model="tt-5.6-sol", usage={})
        with self.assertRaises(ValueError):
            self.store.begin_turn(self.cid, "alice", "again", file_ids=[item["media_id"]])
        history = DshImageService(self.store).attach_messages(self.store.list_messages(self.cid), {"id": "alice"})
        self.assertEqual(item["media_id"], history["items"][0]["media"][0]["media_id"])
        self.assertNotIn("_text", json.dumps(history))

    def test_truncation_and_draft_deletion(self):
        item = self.upload(b"a" * (MAX_TEXT_CHARS + 10))
        self.assertTrue(item["truncated"])
        self.assertEqual(MAX_TEXT_CHARS, item["text_chars"])
        self.assertIsNotNone(self.service.media.discard_reference(self.cid, "alice", item["media_id"], kind="file"))
        self.assertEqual([], self.service.media.list(self.cid, "alice", message_ids=[""]))

    def test_authenticated_api_upload_send_restore_and_cross_user_protection(self):
        app = FastAPI()
        app.include_router(routes.create_router())
        captured = []

        def run(prompt, **kwargs):
            captured.append(prompt)
            return {"finish_reason": "completed", "content": "Product code is 42", "model": kwargs["model"], "usage": {}}

        with TestClient(app) as client, patch.object(routes, "dsh_agent_store", self.store), patch.object(
            routes, "require_identity", side_effect=lambda token: {"id": "alice" if token == "alice" else "bob"},
        ), patch.object(routes.dsh_runtime, "public_status", return_value={"enabled": True, "configured": True}), patch.object(
            routes.dsh_runtime, "run_turn", side_effect=run,
        ):
            path = f"/api/dsh-agent/conversations/{self.cid}"
            response = client.post(path + "/files", headers={"Authorization": "bob"}, files={"file": ("product.txt", b"secret")})
            self.assertEqual(404, response.status_code)
            response = client.post(path + "/files", headers={"Authorization": "alice"}, files={"file": ("product.txt", b"product code: 42")})
            self.assertEqual(200, response.status_code)
            item = response.json()
            response = client.get(path, headers={"Authorization": "alice"})
            self.assertEqual([item], response.json()["draft_references"])
            response = client.post(path + "/messages/stream", headers={"Authorization": "alice"}, json={"content": "Read attached document", "file_ids": [item["media_id"]]})
            self.assertEqual(200, response.status_code)
            self.assertIn('"type":"complete"', response.text)
            self.assertEqual(1, len(captured))
            self.assertIn("product code: 42", captured[0])
            self.assertIn("不是系统指令", captured[0])
            response = client.get(path, headers={"Authorization": "alice"})
            self.assertNotIn("_text", response.text)
            self.assertEqual("file", response.json()["items"][0]["media"][0]["kind"])
            draft = client.post(path + "/files", headers={"Authorization": "alice"}, files={"file": ("notes.md", b"new draft")}).json()
            client.delete(path + "/files/" + draft["media_id"], headers={"Authorization": "alice"})
            self.assertFalse(client.get(path, headers={"Authorization": "alice"}).json().get("draft_references"))
