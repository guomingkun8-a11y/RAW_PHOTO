import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sqlalchemy import inspect

from services.ecommerce import professional_knowledge_service as knowledge_module
from services.ecommerce.professional_knowledge_service import (
    knowledge_context_for_model,
    load_professional_knowledge,
    retrieve_professional_knowledge,
    sync_professional_knowledge_index,
)
from services.ecommerce.professional_knowledge_store import ProfessionalKnowledgeStore


class ProfessionalKnowledgeServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.store = ProfessionalKnowledgeStore(
            f"sqlite:///{Path(self.temp_dir.name) / 'professional-knowledge.db'}"
        )
        self.addCleanup(self.store.close)
        self.store_patch = mock.patch.object(knowledge_module, "professional_knowledge_store", self.store)
        self.qdrant_patch = mock.patch.object(
            knowledge_module.professional_knowledge_index,
            "enabled",
            return_value=False,
        )
        self.store_patch.start()
        self.qdrant_patch.start()
        self.addCleanup(self.store_patch.stop)
        self.addCleanup(self.qdrant_patch.stop)
        knowledge_module._cached_query_embedding.cache_clear()
        knowledge_module._EMBEDDING_FAILURE_UNTIL = 0.0

    def test_loads_runtime_professional_knowledge(self):
        sections = load_professional_knowledge()

        self.assertGreaterEqual(len(sections), 8)
        self.assertTrue(any("\u8be6\u60c5\u9875" in item["title"] for item in sections))

    def test_retrieval_prefers_relevant_detail_page_knowledge(self):
        results = retrieve_professional_knowledge(
            "\u8be6\u60c5\u9875\u7684\u9996\u5c4f\u5e94\u8be5\u600e\u4e48\u89c4\u5212\uff1f",
            limit=3,
        )

        self.assertTrue(results)
        self.assertIn("\u8be6\u60c5\u9875", " ".join(item["title"] + item["content"] for item in results))

    def test_greeting_still_retrieves_professional_scope(self):
        results = retrieve_professional_knowledge("\u4f60\u597d", limit=2)

        self.assertTrue(results)
        self.assertTrue(any(
            "\u4e13\u4e1a\u8303\u56f4" in item["title"]
            or "\u5bf9\u8bdd\u4f18\u5148" in item["title"]
            for item in results
        ))

    def test_sync_persists_documents_and_chunks_in_sql(self):
        result = sync_professional_knowledge_index()

        self.assertGreaterEqual(result["documents"], 1)
        self.assertGreaterEqual(result["chunks"], 8)
        tables = set(inspect(self.store.engine).get_table_names())
        self.assertIn("professional_knowledge_documents", tables)
        self.assertIn("professional_knowledge_chunks", tables)
        self.assertFalse((Path(self.temp_dir.name) / "professional_knowledge_index.db").exists())

    def test_hybrid_retrieval_uses_qdrant_semantic_match(self):
        target = next(
            chunk
            for section in load_professional_knowledge()
            for chunk in knowledge_module._chunk_content(section)
            if "\u80cc\u666f" in chunk["title"] or "\u6784\u56fe" in chunk["content"]
        )

        knowledge_module._cached_query_embedding.cache_clear()
        with (
            mock.patch.object(knowledge_module, "_vector_enabled", return_value=True),
            mock.patch.object(
                knowledge_module,
                "_embedding_request",
                side_effect=lambda texts: [[1.0, 0.0] for _text in texts],
            ),
            mock.patch.object(knowledge_module.professional_knowledge_index, "enabled", return_value=True),
            mock.patch.object(knowledge_module.professional_knowledge_index, "upsert", return_value=True),
            mock.patch.object(
                knowledge_module.professional_knowledge_index,
                "search",
                return_value=[{"score": 0.98, "payload": {"chunkId": target["chunkId"]}}],
            ),
            mock.patch.object(knowledge_module, "_EMBEDDING_FAILURE_UNTIL", 0.0),
        ):
            context = knowledge_context_for_model(
                "\u8ba9\u753b\u9762\u4e0d\u8981\u50cf\u8bc1\u4ef6\u7167",
                limit=3,
            )

        self.assertEqual("hybrid", context["retrievalMode"])
        self.assertTrue(any(
            "\u80cc\u666f" in item["title"] or "\u6784\u56fe" in item["content"]
            for item in context["sections"]
        ))


if __name__ == "__main__":
    unittest.main()
