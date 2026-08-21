from __future__ import annotations

import unittest
from unittest import mock

from services.ecommerce.ecommerce_agent_memory_distiller import (
    _user_evidence,
    _worth_distilling,
    _write_memories,
)
from services.ecommerce.ecommerce_agent_memory_service import ecommerce_agent_memory_service


class EcommerceAgentMemoryDistillerTests(unittest.TestCase):
    def test_short_greeting_does_not_trigger_distillation(self):
        self.assertFalse(_worth_distilling([{"messageId": "1", "text": "你好"}]))
        self.assertTrue(_worth_distilling([{"messageId": "2", "text": "以后默认不要使用纯白背景"}]))

    def test_only_real_user_text_becomes_memory_evidence(self):
        messages = [
            {
                "id": 1,
                "role": "user",
                "messageType": "cow_message",
                "content": {
                    "message": {
                        "role": "user",
                        "content": [{"type": "text", "text": "我喜欢真实道路背景"}],
                    },
                },
            },
            {
                "id": 2,
                "role": "user",
                "messageType": "cow_message",
                "content": {
                    "message": {
                        "role": "user",
                        "content": [{
                            "type": "tool_result",
                            "tool_use_id": "knowledge-1",
                            "content": '{"retrievalMode":"keyword","sources":["不要纯白背景"]}',
                        }],
                    },
                },
            },
            {
                "id": 3,
                "role": "user",
                "content": [{
                    "type": "text",
                    "text": "你已经执行了8个决策步骤，达到了单次运行的最大步数限制。请总结一下你目前的执行过程和结果。",
                }],
            },
            {"id": 4, "role": "assistant", "content": "用户应该喜欢摄影棚背景"},
        ]

        evidence = _user_evidence(messages, default_conversation_id="conversation")

        self.assertEqual(1, len(evidence))
        self.assertEqual("1", evidence[0]["messageId"])
        self.assertEqual("我喜欢真实道路背景", evidence[0]["text"])

    def test_explicit_user_instruction_is_saved_immediately(self):
        candidates = [{
            "key": "stable-style",
            "scope": "user",
            "category": "visual_style",
            "content": "汽车详情页默认使用真实道路背景",
            "confidence": 0.8,
            "sourceMessageIds": [12],
        }]
        evidence = [{
            "messageId": "12",
            "conversationId": "conversation",
            "text": "请记住，以后汽车详情页默认使用真实道路背景",
        }]

        with mock.patch.object(
            ecommerce_agent_memory_service,
            "upsert_long_term_memory",
            return_value={"memoryId": "1"},
        ) as upsert:
            stored = _write_memories(
                owner_id="owner",
                conversation_id="conversation",
                run_id="run",
                memories=candidates,
                scope={"projectId": "conversation", "brandId": ""},
                evidence=evidence,
            )

        self.assertEqual(1, stored)
        self.assertEqual("active", upsert.call_args.kwargs["status"])
        self.assertTrue(upsert.call_args.kwargs["confirmed"])
        self.assertTrue(upsert.call_args.kwargs["metadata"]["explicitEvidence"])

    def test_uncertain_single_conversation_preference_is_discarded(self):
        with mock.patch.object(ecommerce_agent_memory_service, "upsert_long_term_memory") as upsert:
            stored = _write_memories(
                owner_id="owner",
                conversation_id="conversation",
                run_id="run",
                memories=[{
                    "category": "visual_style",
                    "content": "汽车详情页可以使用蓝色摄影棚背景",
                    "confidence": 0.97,
                    "sourceMessageIds": [8],
                }],
                scope={"projectId": "conversation", "brandId": ""},
                evidence=[{
                    "messageId": "8",
                    "conversationId": "conversation",
                    "text": "汽车详情页可以使用蓝色摄影棚背景",
                }],
            )

        self.assertEqual(0, stored)
        upsert.assert_not_called()

    def test_repeated_preference_can_grow_across_projects(self):
        evidence = [
            {"messageId": "10", "conversationId": "project-a", "text": "我喜欢汽车详情页使用真实道路背景"},
            {"messageId": "20", "conversationId": "project-b", "text": "汽车详情页我还是喜欢真实道路背景"},
        ]
        with mock.patch.object(
            ecommerce_agent_memory_service,
            "upsert_long_term_memory",
            return_value={"memoryId": "2"},
        ) as upsert:
            stored = _write_memories(
                owner_id="owner",
                conversation_id="project-b",
                run_id="run",
                memories=[{
                    "category": "preference",
                    "content": "用户喜欢汽车详情页使用真实道路背景",
                    "confidence": 0.94,
                    "sourceMessageIds": [20],
                }],
                scope={"projectId": "project-b", "brandId": ""},
                evidence=evidence,
            )

        self.assertEqual(1, stored)
        self.assertEqual("user", upsert.call_args.kwargs["scope"])
        self.assertEqual(2, upsert.call_args.kwargs["metadata"]["evidenceConversationCount"])

    def test_product_fact_stays_in_current_project(self):
        with mock.patch.object(
            ecommerce_agent_memory_service,
            "upsert_long_term_memory",
            return_value={"memoryId": "3"},
        ) as upsert:
            stored = _write_memories(
                owner_id="owner",
                conversation_id="project-a",
                run_id="run",
                memories=[{
                    "category": "product",
                    "content": "这个产品的材质是拉丝铝",
                    "confidence": 0.93,
                    "sourceMessageIds": [30],
                }],
                scope={"projectId": "project-a", "brandId": ""},
                evidence=[{
                    "messageId": "30",
                    "conversationId": "project-a",
                    "text": "这个产品的材质是拉丝铝",
                }],
            )

        self.assertEqual(1, stored)
        self.assertEqual("project", upsert.call_args.kwargs["scope"])
        self.assertEqual("project-a", upsert.call_args.kwargs["scope_id"])


if __name__ == "__main__":
    unittest.main()
