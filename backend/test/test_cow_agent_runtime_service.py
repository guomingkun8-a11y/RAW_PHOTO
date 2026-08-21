from __future__ import annotations

import base64
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from services.agent import AgentRun, AgentRunStatus
from services.ecommerce.ecommerce_agent_memory_service import EcommerceAgentMemoryService
import services.ecommerce.cow_agent_runtime_service as cow_runtime
import services.ecommerce.cow_agent_extended_tools as cow_tools


IDENTITY = {"id": "cow-user", "username": "cow-user", "name": "Cow User", "role": "user"}


class _MemoryManager:
    async def search(self, *_args, **_kwargs):
        return []

    async def add_memory(self, *_args, **_kwargs):
        return None

    def close(self):
        return None


class _ToolLoopModel:
    model = "fake-cow-model"
    channel_type = "web"

    def __init__(self):
        self.calls = 0

    def call_stream(self, _request):
        self.calls += 1
        if self.calls == 1:
            yield {
                "choices": [{
                    "delta": {
                        "tool_calls": [{
                            "index": 0,
                            "id": "call-knowledge",
                            "function": {
                                "name": "raw_professional_knowledge",
                                "arguments": '{"query":"汽车商业视觉"}',
                            },
                        }],
                    },
                    "finish_reason": "tool_calls",
                }],
            }
            return
        yield {
            "choices": [{
                "delta": {"content": "我会先从汽车商业视觉角度分析需求，再等待你确认执行。"},
                "finish_reason": "stop",
            }],
        }


class _SkillImageToolLoopModel:
    model = "fake-cow-model"
    channel_type = "web"

    def __init__(self):
        self.calls = 0

    def call_stream(self, _request):
        self.calls += 1
        if self.calls == 1:
            yield {
                "choices": [{
                    "delta": {
                        "tool_calls": [{
                            "index": 0,
                            "id": "call-read-skill",
                            "function": {
                                "name": "read",
                                "arguments": '{"path": "' + (cow_runtime.VENDOR_ROOT / "skills" / "image-generation" / "SKILL.md").as_posix() + '"}',
                            },
                        }],
                    },
                    "finish_reason": "tool_calls",
                }],
            }
            return
        if self.calls == 2:
            yield {
                "choices": [{
                    "delta": {
                        "tool_calls": [{
                            "index": 0,
                            "id": "call-generate-image",
                            "function": {
                                "name": "raw_generate_image",
                                "arguments": '{"prompt":"Premium automotive launch visual with a dimensional studio background","mode":"generate","count":1}',
                            },
                        }],
                    },
                    "finish_reason": "tool_calls",
                }],
            }
            return
        yield {
            "choices": [{
                "delta": {"content": "The requested automotive image has been generated."},
                "finish_reason": "stop",
            }],
        }


class _MarketingStrategyToolLoopModel:
    model = "fake-cow-model"
    channel_type = "web"

    def __init__(self):
        self.calls = 0

    def call_stream(self, _request):
        self.calls += 1
        if self.calls == 1:
            yield {
                "choices": [{
                    "delta": {
                        "tool_calls": [{
                            "index": 0,
                            "id": "call-read-marketing-skill",
                            "function": {
                                "name": "read",
                                "arguments": '{"path": "' + (cow_runtime.VENDOR_ROOT / "skills" / "marketing-strategy" / "SKILL.md").as_posix() + '"}',
                            },
                        }],
                    },
                    "finish_reason": "tool_calls",
                }],
            }
            return
        if self.calls == 2:
            yield {
                "choices": [{
                    "delta": {
                        "tool_calls": [{
                            "index": 0,
                            "id": "call-marketing-strategy",
                            "function": {
                                "name": "raw_marketing_strategy",
                                "arguments": '{"goal":"生成主图带文字排版","product_context":"宠物出行清洁喷雾，适合航空箱场景","platform":"淘宝主图","reference_style":"红色大标题，产品在右侧，背景有宠物航空箱"}',
                            },
                        }],
                    },
                    "finish_reason": "tool_calls",
                }],
            }
            return
        if self.calls == 3:
            yield {
                "choices": [{
                    "delta": {
                        "tool_calls": [{
                            "index": 0,
                            "id": "call-generate-image",
                            "function": {
                                "name": "raw_generate_image",
                                "arguments": '{"prompt":"淘宝宠物出行主图，红色大标题，产品右侧，航空箱背景","mode":"generate","count":1}',
                            },
                        }],
                    },
                    "finish_reason": "tool_calls",
                }],
            }
            return
        yield {
            "choices": [{
                "delta": {"content": "已按营销策略生成主图。"},
                "finish_reason": "stop",
            }],
        }

    def call(self, _request):
        return {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": (
                        '{"shouldUse":true,'
                        '"triggerReason":"用户要求主图和文字排版",'
                        '"productUnderstanding":"宠物出行清洁喷雾，适合航空箱使用场景",'
                        '"targetAudience":"带宠物出行的用户",'
                        '"painPoints":["航空箱异味","外出清洁不方便"],'
                        '"differentiatedSellingPoints":["出行场景适配","喷雾瓶身便携","清洁诉求明确"],'
                        '"headline":"出行也清爽",'
                        '"subheadline":"航空箱随手护理",'
                        '"sellingPointLabels":["便携喷雾","箱内清爽","外出适用"],'
                        '"layoutPlan":"红色粗体主标题置于左上，副标题在其下，三个卖点标签靠左竖排，产品放右侧偏下，文字与背景保持高对比",'
                        '"visualHook":"航空箱空间与喷雾形成真实出行场景",'
                        '"forbiddenClaims":["100%","99%","杀菌","认证","价格"],'
                        '"copyRiskNotes":["不写消杀和百分比承诺"]}'
                    ),
                },
            }],
        }


class CowAgentRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.memory = EcommerceAgentMemoryService(f"sqlite:///{self.root / 'cow-memory.db'}")
        self.addCleanup(self.memory.close)
        self.patchers = [
            mock.patch.object(cow_runtime, "DATA_ROOT", self.root / "users"),
            mock.patch.object(cow_runtime, "COW_RUNTIME_ROOT", self.root / "runtime"),
            mock.patch.object(cow_runtime, "ecommerce_agent_memory_service", self.memory),
            mock.patch.object(cow_tools, "_tool_state_root", return_value=self.root / "tool-runtime"),
            mock.patch(
                "services.ecommerce.ecommerce_agent_memory_service.professional_query_embedding",
                return_value=[],
            ),
            mock.patch(
                "services.ecommerce.ecommerce_agent_memory_service.embed_professional_texts",
                return_value=[],
            ),
        ]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(cow_tools.reset_extended_tool_services)

    @staticmethod
    def _run(*, owner: str = "cow-user", conversation: str = "conversation-1", turn: str = "turn-1", images=None):
        return AgentRun(
            run_id=f"cow-run-{owner}-{turn}",
            agent_name=cow_runtime.COW_AGENT_NAME,
            status=AgentRunStatus.RUNNING,
            request={
                "prompt": "帮我分析汽车详情页",
                "model": "gpt-image-2",
                "quality": "auto",
                "size": "1024x1024",
                "count": 1,
                "mode": "generate",
                "images": list(images or []),
            },
            metadata={
                "ownerId": owner,
                "workflow": "cowagent_professional",
                "identity": {**IDENTITY, "id": owner, "username": owner},
                "conversationId": conversation,
                "turnId": turn,
                "engine": "cowagent",
            },
            max_steps=20,
        )

    def test_owner_workspaces_are_isolated(self):
        first = cow_runtime._workspace_for("owner-a")
        second = cow_runtime._workspace_for("owner-b")

        self.assertNotEqual(first, second)
        self.assertEqual(first.parent, self.root / "users")
        self.assertEqual(second.parent, self.root / "users")

    def test_later_turn_reuses_same_conversation_product_reference(self):
        payload = b"original-product-reference"
        encoded = base64.b64encode(payload).decode("ascii")
        first = self._run(
            conversation="product-conversation",
            turn="turn-1",
            images=[{
                "name": "product.png",
                "type": "image/png",
                "data_url": f"data:image/png;base64,{encoded}",
            }],
        )
        first.request["mode"] = "edit"
        first_runtime = cow_runtime.CowAgentRunRuntime(first)
        first_runtime.save_attachments()

        second = self._run(conversation="product-conversation", turn="turn-2")
        second.request["mode"] = "edit"
        second.request["inherit_reference_images"] = True
        second_runtime = cow_runtime.CowAgentRunRuntime(second)
        second_runtime.save_attachments()

        self.assertTrue(second_runtime.reference_images_inherited)
        self.assertEqual(1, len(second_runtime.attachments))
        self.assertEqual(payload, second_runtime.attachments[0].read_bytes())
        self.assertTrue(second.metadata["referenceImagesInherited"])

        task_service = SimpleNamespace(
            submit_edit=mock.Mock(return_value={
                "id": "task-edit-1",
                "status": "success",
                "data": [{"url": "https://example.test/edited-product.png"}],
            }),
        )
        with mock.patch.object(cow_runtime, "image_task_service", task_service):
            result = second_runtime.generate_images(
                {"prompt": "Change only the background", "mode": "edit", "count": 1},
                progress=lambda _message: None,
                cancelled=lambda: False,
            )
        self.assertEqual(1, result["count"])
        edit_request = task_service.submit_edit.call_args.kwargs
        self.assertEqual(payload, edit_request["images"][0][0])
        self.assertTrue(edit_request["preserve_subject"])
        self.assertEqual("preserve", edit_request["subject_mutation_policy"])

        unrelated = self._run(conversation="another-conversation", turn="turn-2")
        unrelated.request["inherit_reference_images"] = True
        unrelated_runtime = cow_runtime.CowAgentRunRuntime(unrelated)
        with self.assertRaises(FileNotFoundError):
            unrelated_runtime.save_attachments()
        self.assertEqual([], unrelated_runtime.attachments)

    def test_remote_reference_url_is_downloaded_for_edit_task(self):
        payload = b"remote-product-reference"
        run = self._run(
            images=[{
                "name": "product.png",
                "type": "image/png",
                "url": "https://example.test/product.png",
            }],
        )
        run.request["mode"] = "edit"
        task_service = SimpleNamespace(
            submit_edit=mock.Mock(return_value={
                "id": "task-edit-url",
                "status": "success",
                "data": [{"url": "https://example.test/edited.png"}],
            }),
        )
        response = SimpleNamespace(
            status_code=200,
            headers={"content-type": "image/png", "content-length": str(len(payload))},
            content=payload,
        )
        with (
            mock.patch.object(cow_runtime, "image_task_service", task_service),
            mock.patch.object(cow_runtime.requests, "get", return_value=response),
        ):
            runtime = cow_runtime.CowAgentRunRuntime(run)
            runtime.save_attachments()
            result = runtime.generate_images(
                {"prompt": "生成一个主图，带有文字排版的", "mode": "edit", "count": 1},
                progress=lambda _message: None,
                cancelled=lambda: False,
            )

        self.assertEqual(1, result["count"])
        edit_request = task_service.submit_edit.call_args.kwargs
        self.assertEqual(payload, edit_request["images"][0][0])
        self.assertEqual("url-01-product.png", edit_request["images"][0][1])
        self.assertEqual(["https://example.test/product.png"], edit_request["image_urls"])

    def test_raw_vision_accepts_current_reference_url(self):
        payload = b"remote-product-reference"
        run = self._run(
            images=[{
                "name": "product.png",
                "type": "image/png",
                "url": "https://example.test/product.png",
            }],
        )
        response = SimpleNamespace(
            status_code=200,
            headers={"content-type": "image/png", "content-length": str(len(payload))},
            content=payload,
        )
        with (
            mock.patch.object(cow_runtime.requests, "get", return_value=response),
            mock.patch.object(cow_runtime.CowAgentRunRuntime, "_build_memory_manager", return_value=_MemoryManager()),
        ):
            runtime = cow_runtime.CowAgentRunRuntime(run)
            runtime.save_attachments()
            runtime.model.analyze_image = mock.Mock(return_value="visible product on a simple background")
            result = cow_runtime.RawVisionTool(runtime).execute({"question": "analyze the reference"})

        self.assertEqual("success", result.status)
        self.assertEqual("url", result.result["source"])
        self.assertEqual("visible product on a simple background", result.result["analysis"])
        runtime.model.analyze_image.assert_called_once()
        self.assertTrue(runtime.model.analyze_image.call_args.args[0].startswith("data:image/png;base64,"))

    def test_runtime_does_not_create_legacy_sqlite_memory(self):
        runtime = cow_runtime.CowAgentRunRuntime(self._run())

        self.assertIsNone(runtime.memory_manager)
        self.assertFalse((runtime.workspace / "memory" / "long-term" / "index.db").exists())

    def test_temporary_conversation_skips_long_term_recall_and_storage(self):
        self.memory.upsert_long_term_memory(
            owner_id="cow-user",
            content="汽车详情页优先使用夜间道路背景",
            category="visual_style",
            confirmed=True,
        )
        run = self._run()
        run.request["use_long_term_memory"] = False
        with (
            mock.patch.object(cow_runtime.CowAgentRunRuntime, "_build_memory_manager", return_value=_MemoryManager()),
            mock.patch.object(cow_runtime, "knowledge_context_for_model", return_value={"sections": [], "sources": []}),
            mock.patch.object(self.memory, "search_long_term_memories", side_effect=AssertionError("must not retrieve")),
            mock.patch.object(self.memory, "enqueue_memory_job") as enqueue,
        ):
            runtime = cow_runtime.CowAgentRunRuntime(run)
            context = runtime.fresh_context("汽车夜间道路")
            remember_result = cow_runtime.RawRememberTool(runtime).execute({
                "content": "以后都使用赛道背景",
                "category": "visual_style",
            })
            runtime.enqueue_memory_distillation()

        self.assertIn('"longTermMemoryEnabled": false', context)
        self.assertEqual([], runtime.memory_sources)
        self.assertFalse(remember_result.result["stored"])
        enqueue.assert_not_called()
        self.assertEqual(1, len(self.memory.list_all_long_term_memories(owner_id="cow-user")))

    def test_raw_remember_trusts_only_explicit_user_instruction(self):
        explicit_run = self._run(turn="explicit-memory")
        explicit_run.request["prompt"] = "请记住，以后汽车详情页都使用真实道路背景"
        explicit_runtime = cow_runtime.CowAgentRunRuntime(explicit_run)
        explicit_result = cow_runtime.RawRememberTool(explicit_runtime).execute({
            "content": "汽车详情页默认使用真实道路背景",
            "category": "visual_style",
        })

        proposed_run = self._run(turn="proposed-memory")
        proposed_run.request["prompt"] = "分析一下汽车详情页的视觉方向"
        proposed_runtime = cow_runtime.CowAgentRunRuntime(proposed_run)
        proposed_result = cow_runtime.RawRememberTool(proposed_runtime).execute({
            "content": "汽车详情页可以使用蓝色摄影棚背景",
            "category": "visual_style",
        })

        ambiguous_run = self._run(turn="ambiguous-memory")
        ambiguous_run.request["prompt"] = "这个方案以后再说"
        ambiguous_runtime = cow_runtime.CowAgentRunRuntime(ambiguous_run)
        ambiguous_result = cow_runtime.RawRememberTool(ambiguous_runtime).execute({
            "content": "以后使用纯白背景",
            "category": "visual_style",
        })

        self.assertTrue(explicit_result.result["stored"])
        self.assertFalse(proposed_result.result["stored"])
        self.assertFalse(ambiguous_result.result["stored"])
        items = self.memory.list_all_long_term_memories(owner_id="cow-user", include_pending=True)
        by_content = {item["content"]: item for item in items}
        explicit = by_content["汽车详情页默认使用真实道路背景"]
        self.assertEqual("active", explicit["status"])
        self.assertTrue(explicit["confirmed"])
        self.assertGreaterEqual(explicit["confidence"], 0.9)
        self.assertNotIn("汽车详情页可以使用蓝色摄影棚背景", by_content)
        self.assertNotIn("以后使用纯白背景", by_content)

    def test_explicit_product_memory_uses_conversation_as_project_boundary(self):
        run = self._run(conversation="product-project", turn="product-memory")
        run.request["prompt"] = "请记住，这个产品的材质是拉丝铝"
        runtime = cow_runtime.CowAgentRunRuntime(run)

        result = cow_runtime.RawRememberTool(runtime).execute({
            "content": "这个产品的材质是拉丝铝",
            "category": "product",
        })

        self.assertTrue(result.result["stored"])
        item = self.memory.list_all_long_term_memories(owner_id="cow-user")[0]
        self.assertEqual("project", item["scope"])
        self.assertEqual("product-project", item["scopeId"])
        self.assertEqual("product-project", run.request["project_id"])
        self.assertEqual(item["memoryId"], runtime.memory_updates[0]["memoryId"])

    def test_recalled_memory_is_exposed_as_a_public_source(self):
        memory = self.memory.upsert_long_term_memory(
            owner_id="cow-user",
            content="汽车详情页优先使用夜间道路背景",
            category="visual_style",
            confirmed=True,
        )
        with (
            mock.patch.object(cow_runtime.CowAgentRunRuntime, "_build_memory_manager", return_value=_MemoryManager()),
            mock.patch.object(cow_runtime, "knowledge_context_for_model", return_value={"sections": [], "sources": []}),
        ):
            runtime = cow_runtime.CowAgentRunRuntime(self._run())
            context = runtime.fresh_context("汽车夜间道路")

        self.assertIn("汽车详情页优先使用夜间道路背景", context)
        self.assertEqual(str(memory["memoryId"]), runtime.memory_sources[0]["id"])
        self.assertEqual("visual_style", runtime.memory_sources[0]["category"])

    def test_dialogue_sse_is_forwarded_without_buffering_the_response(self):
        response = mock.Mock()
        response.status_code = 200
        response.headers = {"content-type": "text/event-stream"}
        response.iter_lines.return_value = iter([
            b'data: {"choices":[{"delta":{"content":"first"}}]}',
            b'data: {"choices":[{"delta":{"content":"second"}}]}',
            b"data: [DONE]",
        ])
        with mock.patch.object(cow_runtime.requests, "post", return_value=response):
            chunks = cow_runtime._open_sse_chunks({"model": "fake"}, "https://relay.test/v1", "key")
            response.iter_lines.assert_not_called()
            first = next(chunks)
            self.assertEqual("first", first["choices"][0]["delta"]["content"])
            self.assertEqual(1, response.iter_lines.call_count)
            remaining = list(chunks)

        self.assertEqual("second", remaining[0]["choices"][0]["delta"]["content"])
        response.close.assert_called_once()

    def test_relay_payment_errors_explain_account_or_model_access(self):
        response = mock.Mock()
        response.status_code = 402
        error = cow_runtime._response_error(response)

        self.assertEqual(402, error.status_code)
        self.assertEqual("payment_required", error.detail["error"]["type"])
        self.assertIn("账户余额", error.detail["error"]["message"])

    def test_attachment_is_scoped_and_path_escape_is_rejected(self):
        data_url = "data:image/png;base64," + base64.b64encode(b"fake-png").decode("ascii")
        run = self._run(images=[{"name": "car.png", "type": "image/png", "data_url": data_url}])
        with mock.patch.object(cow_runtime.CowAgentRunRuntime, "_build_memory_manager", return_value=_MemoryManager()):
            runtime = cow_runtime.CowAgentRunRuntime(run)
        runtime.save_attachments()

        self.assertEqual(1, len(runtime.attachments))
        self.assertTrue(runtime.attachments[0].is_file())
        with self.assertRaises(PermissionError):
            runtime.resolve_attachment(str(self.root / "outside.png"))

    def test_read_tool_allows_workspace_and_enabled_skill_only(self):
        run = self._run()
        with mock.patch.object(cow_runtime.CowAgentRunRuntime, "_build_memory_manager", return_value=_MemoryManager()):
            runtime = cow_runtime.CowAgentRunRuntime(run)
        runtime._ensure_workspace_files()
        tool = cow_runtime.RawScopedReadTool(runtime)

        workspace_result = tool.execute({"path": "AGENT.md"})
        skill_result = tool.execute({
            "path": str(cow_runtime.VENDOR_ROOT / "skills" / "image-generation" / "SKILL.md"),
        })
        creator_skill_result = tool.execute({
            "path": str(cow_runtime.VENDOR_ROOT / "skills" / "skill-creator" / "SKILL.md"),
        })
        outside_result = tool.execute({"path": str(Path(__file__))})

        self.assertEqual("success", workspace_result.status)
        self.assertIn("RAW Professional Creative Agent", workspace_result.result["content"])
        self.assertEqual("success", skill_result.status)
        self.assertIn("raw_generate_image", skill_result.result["content"])
        self.assertEqual("success", creator_skill_result.status)
        self.assertEqual("error", outside_result.status)

    def test_professional_prompt_exposes_all_vendored_cowagent_skills(self):
        run = self._run()
        with mock.patch.object(cow_runtime.CowAgentRunRuntime, "_build_memory_manager", return_value=_MemoryManager()):
            runtime = cow_runtime.CowAgentRunRuntime(run)
        prompt = runtime._system_prompt([cow_runtime.RawScopedReadTool(runtime)])

        self.assertIn("<name>image-generation</name>", prompt)
        self.assertIn("<name>skill-creator</name>", prompt)
        self.assertIn("<name>knowledge-wiki</name>", prompt)

    def test_extended_tools_are_registered_with_admin_boundaries(self):
        user_run = self._run()
        admin_run = self._run(owner="admin-user")
        admin_run.metadata["identity"]["role"] = "admin"
        with mock.patch.object(cow_runtime.CowAgentRunRuntime, "_build_memory_manager", return_value=_MemoryManager()):
            user_runtime = cow_runtime.CowAgentRunRuntime(user_run)
            admin_runtime = cow_runtime.CowAgentRunRuntime(admin_run)

        user_names = {tool.name for tool in cow_tools.build_extended_cow_tools(user_runtime)}
        admin_names = {tool.name for tool in cow_tools.build_extended_cow_tools(admin_runtime)}
        expected = {
            "ls", "search_files", "write", "edit", "memory_get", "browser",
            "web_search", "web_fetch", "scheduler", "send", "evolution_undo", "mcp",
        }
        self.assertTrue(expected.issubset(user_names))
        self.assertNotIn("bash", user_names)
        self.assertNotIn("env_config", user_names)
        self.assertTrue(expected.union({"bash", "env_config"}).issubset(admin_names))

        runtime_tools = {
            cow_runtime.RawScopedReadTool(user_runtime).name,
            cow_runtime.RawProfessionalKnowledgeTool(user_runtime).name,
            cow_runtime.RawMemorySearchTool(user_runtime).name,
            cow_runtime.CowMemorySearchTool(user_runtime).name,
            cow_runtime.RawRememberTool(user_runtime).name,
            cow_runtime.RawVisionTool(user_runtime).name,
            cow_runtime.CowVisionTool(user_runtime).name,
            cow_runtime.RawGenerateImageTool(user_runtime).name,
        }
        self.assertTrue({"memory_search", "vision"}.issubset(runtime_tools))

    def test_cost_optimized_runtime_exposes_only_relevant_core_tools(self):
        run = self._run()
        with mock.patch.object(cow_runtime.CowAgentRunRuntime, "_build_memory_manager", return_value=_MemoryManager()):
            runtime = cow_runtime.CowAgentRunRuntime(run)

        planning_names = {
            tool.name
            for tool in runtime._runtime_tools(
                allow_generation=False,
                include_extended=False,
                optimized=True,
            )
        }
        execution_names = {
            tool.name
            for tool in runtime._runtime_tools(
                allow_generation=True,
                include_extended=False,
                optimized=True,
            )
        }

        self.assertEqual(
            {"read", "raw_professional_knowledge", "raw_memory_search", "raw_remember", "raw_vision", "raw_marketing_strategy"},
            planning_names,
        )
        self.assertEqual(planning_names | {"raw_generate_image"}, execution_names)

    def test_natural_search_request_enables_web_tools(self):
        self.assertTrue(cow_runtime._requests_extended_tools("你帮我搜索一下家可美"))
        self.assertTrue(cow_runtime._requests_extended_tools("帮我上网查这个品牌"))
        self.assertTrue(cow_runtime._requests_extended_tools("search for this company"))
        self.assertFalse(cow_runtime._requests_extended_tools("分析一下这个产品"))

    def test_web_research_intent_is_distinct_from_other_extended_tools(self):
        self.assertTrue(cow_runtime._requests_web_research("打开官网看看最新消息"))
        self.assertTrue(cow_runtime._requests_web_research("你帮我打开百度，然后搜索我的世界"))
        self.assertTrue(cow_runtime._requests_web_research("https://example.com/product"))
        self.assertTrue(cow_runtime._requests_web_research("搜索家可美"))
        self.assertTrue(cow_runtime._requests_web_research("查一下家可美"))
        self.assertFalse(cow_runtime._requests_web_research("帮我写一个方案文件"))
        self.assertFalse(cow_runtime._requests_web_research("帮我打开产品图片"))

    def test_required_web_tool_distinguishes_search_from_browser_navigation(self):
        self.assertEqual("web_search", cow_runtime._required_web_tool("帮我搜索家可美"))
        self.assertEqual("browser", cow_runtime._required_web_tool("打开链接 https://example.com"))
        self.assertEqual("browser", cow_runtime._required_web_tool("你帮我打开百度，然后搜索我的世界"))
        self.assertEqual("browser", cow_runtime._required_web_tool("打开官网看看"))

    def test_dialogue_model_forces_requested_tool_once(self):
        model = cow_runtime.RawCowLLMModel("gpt-4o", lambda _query: "")
        model.force_next_tool("web_search")
        request = cow_runtime.LLMRequest(
            messages=[{"role": "user", "content": "搜索家可美"}],
            tools=[{
                "name": "web_search",
                "description": "Search the web",
                "input_schema": {"type": "object", "properties": {}},
            }],
        )
        payloads = []

        def open_chunks(payload, _base_url, _api_key):
            payloads.append(payload)
            return iter([{"choices": [{"delta": {}, "finish_reason": "stop"}]}])

        with (
            mock.patch.object(cow_runtime, "_active_relay", return_value=("https://relay.test/v1", "key")),
            mock.patch.object(cow_runtime, "_open_sse_chunks", side_effect=open_chunks),
            mock.patch.object(cow_runtime, "run_with_relay_pool", side_effect=lambda _settings, _operation, action: action()),
        ):
            list(model.call_stream(request))
            list(model.call_stream(request))

        self.assertEqual(
            {"type": "function", "function": {"name": "web_search"}},
            payloads[0]["tool_choice"],
        )
        self.assertEqual("auto", payloads[1]["tool_choice"])

    def test_dialogue_model_enforces_research_tool_minimums_after_old_history(self):
        model = cow_runtime.RawCowLLMModel("gpt-4o", lambda _query: "")
        model.require_additional_tool_calls({"web_search": 1, "web_fetch": 2})
        tool_schemas = [
            {"name": name, "description": name, "input_schema": {"type": "object", "properties": {}}}
            for name in ("web_search", "web_fetch")
        ]
        old_search = {"role": "assistant", "content": [{"type": "tool_use", "name": "web_search", "id": "old"}]}
        messages = [{"role": "user", "content": "旧请求"}, old_search, {"role": "user", "content": "新搜索"}]
        payloads = []

        def call(current_messages):
            request = cow_runtime.LLMRequest(messages=current_messages, tools=tool_schemas)
            with (
                mock.patch.object(cow_runtime, "_active_relay", return_value=("https://relay.test/v1", "key")),
                mock.patch.object(cow_runtime, "_open_sse_chunks", side_effect=lambda payload, _url, _key: payloads.append(payload) or iter([])),
                mock.patch.object(cow_runtime, "run_with_relay_pool", side_effect=lambda _settings, _operation, action: action()),
            ):
                list(model.call_stream(request))

        call(messages)
        messages.append({"role": "assistant", "content": [{"type": "tool_use", "name": "web_search", "id": "new-search"}]})
        call(messages)
        messages.append({"role": "assistant", "content": [{"type": "tool_use", "name": "web_fetch", "id": "fetch-1"}]})
        call(messages)
        messages.append({"role": "assistant", "content": [{"type": "tool_use", "name": "web_fetch", "id": "fetch-2"}]})
        call(messages)

        choices = [payload["tool_choice"] for payload in payloads]
        self.assertEqual("web_search", choices[0]["function"]["name"])
        self.assertEqual("web_fetch", choices[1]["function"]["name"])
        self.assertEqual("web_fetch", choices[2]["function"]["name"])
        self.assertEqual("auto", choices[3])

    def test_raw_web_search_uses_ten_results_unless_user_sets_count(self):
        run = self._run()
        run.request["prompt"] = "帮我搜索家可美"
        with mock.patch.object(cow_runtime.CowAgentRunRuntime, "_build_memory_manager", return_value=_MemoryManager()):
            runtime = cow_runtime.CowAgentRunRuntime(run)
        runtime._web_research_requested = True
        tool = cow_tools.RawWebSearchTool(runtime)

        with mock.patch.object(cow_tools.WebSearch, "execute", return_value=cow_tools.ToolResult.success({})) as execute:
            tool.execute({"query": "家可美", "count": 5})
        self.assertEqual(10, execute.call_args.args[0]["count"])

        runtime.user_message = "帮我搜索3条家可美结果"
        with mock.patch.object(cow_tools.WebSearch, "execute", return_value=cow_tools.ToolResult.success({})) as execute:
            tool.execute({"query": "家可美", "count": 3})
        self.assertEqual(3, execute.call_args.args[0]["count"])

    def test_raw_web_search_prefetches_three_diverse_readable_sources(self):
        run = self._run()
        run.request["prompt"] = "帮我搜索 CSGO"
        with mock.patch.object(cow_runtime.CowAgentRunRuntime, "_build_memory_manager", return_value=_MemoryManager()):
            runtime = cow_runtime.CowAgentRunRuntime(run)
        runtime._web_research_requested = True
        tool = cow_tools.RawWebSearchTool(runtime)
        search_result = cow_tools.ToolResult.success({
            "query": "CSGO",
            "results": [
                {"title": "Official", "url": "https://official.example/game", "snippet": "official"},
                {"title": "Wiki", "url": "https://wiki.example/game", "snippet": "wiki"},
                {"title": "News", "url": "https://news.example/game", "snippet": "news"},
                {"title": "Guide", "url": "https://guide.example/game", "snippet": "guide"},
            ],
        })

        def fetch(_tool, args):
            if "official.example" in args["url"]:
                return cow_tools.ToolResult.fail("dynamic page")
            return cow_tools.ToolResult.success(f"Title: source\nURL: {args['url']}\n\nContent:\nUseful source body")

        with (
            mock.patch.object(cow_tools.WebSearch, "execute", return_value=search_result),
            mock.patch.object(cow_tools.WebFetch, "execute", autospec=True, side_effect=fetch),
        ):
            result = tool.execute({"query": "CSGO"})

        self.assertEqual("success", result.status)
        self.assertEqual(3, result.result["prefetchedSourceCount"])
        self.assertEqual("unreadable", result.result["results"][0]["fetchStatus"])
        self.assertTrue(all(
            item.get("readableContent")
            for item in result.result["results"][1:4]
        ))
        self.assertIn("not just snippets", result.result["researchInstructions"])

    def test_web_research_prompt_requires_source_reading_and_citations(self):
        run = self._run()
        with mock.patch.object(cow_runtime.CowAgentRunRuntime, "_build_memory_manager", return_value=_MemoryManager()):
            runtime = cow_runtime.CowAgentRunRuntime(run)
        runtime._web_research_requested = True

        prompt = runtime._system_prompt(runtime._runtime_tools(
            allow_generation=False,
            include_extended=True,
            optimized=True,
        ))

        self.assertIn("at least two useful, independent sources", prompt)
        self.assertIn("exact entity name or key phrase", prompt)
        self.assertIn("web_fetch", prompt)
        self.assertIn("full URLs", prompt)

    def test_web_research_uses_expanded_agent_budget(self):
        run = self._run()
        run.request["prompt"] = "帮我上网搜索这个品牌并查看官网"
        captured = {}

        class CapturingExecutor:
            def __init__(self, **kwargs):
                captured["executor"] = kwargs
                self.messages = list(kwargs["messages"])

            def run_stream(self, message):
                self.messages.extend([
                    {"role": "user", "content": [{"type": "text", "text": message}]},
                    {"role": "assistant", "content": [{"type": "text", "text": "已完成多来源检索。"}]},
                ])
                return "已完成多来源检索。"

        with (
            mock.patch.object(cow_runtime.CowAgentRunRuntime, "_build_memory_manager", return_value=_MemoryManager()),
            mock.patch.object(cow_runtime, "Agent") as agent_class,
            mock.patch.object(cow_runtime, "AgentStreamExecutor", CapturingExecutor),
        ):
            runtime = cow_runtime.CowAgentRunRuntime(run)
            runtime.execute()

        agent_kwargs = agent_class.call_args.kwargs
        self.assertEqual(cow_runtime.WEB_RESEARCH_AGENT_STEPS, agent_kwargs["max_steps"])
        self.assertEqual(cow_runtime.WEB_RESEARCH_CONTEXT_TOKENS, agent_kwargs["max_context_tokens"])
        self.assertEqual(cow_runtime.WEB_RESEARCH_CONTEXT_TURNS, captured["executor"]["max_context_turns"])
        self.assertEqual("web_research", run.result["agentExecutionProfile"])
        self.assertEqual("web_search", run.metadata["requiredInitialTool"])
        self.assertEqual({"web_search": 1}, runtime.model._required_tool_calls)

    def test_simple_consultation_uses_one_direct_model_call(self):
        run = self._run()
        run.request["prompt"] = "你好"
        with mock.patch.object(cow_runtime.CowAgentRunRuntime, "_build_memory_manager", return_value=_MemoryManager()):
            runtime = cow_runtime.CowAgentRunRuntime(run)
        runtime.add_long_term = mock.Mock()

        captured = {}

        def direct_reply(request):
            captured["max_tokens"] = request.max_tokens
            runtime.model.dialogue_calls += 1
            return {"choices": [{"message": {"role": "assistant", "content": "你好，我可以帮你规划商品主图和详情页。"}}]}

        runtime.model.call = mock.Mock(side_effect=direct_reply)
        runtime.execute()

        self.assertEqual(AgentRunStatus.COMPLETED, run.status, run.error)
        self.assertEqual("direct_consult", run.result["optimizationRoute"])
        self.assertEqual(1, run.result["modelUsage"]["totalCalls"])
        self.assertEqual(0, run.tool_calls)
        self.assertEqual(700, captured["max_tokens"])
        runtime.model.call.assert_called_once()

    def test_first_creation_request_uses_autonomous_generation_tool(self):
        run = self._run()
        run.request["prompt"] = "生成主图带文字排版"
        task_service = SimpleNamespace(
            submit_generation=mock.Mock(return_value={
                "id": "first-generation-task",
                "status": "success",
                "data": [{"url": "https://example.test/main-typography.png", "width": 1024, "height": 1024}],
            }),
        )
        with (
            mock.patch.object(cow_runtime.CowAgentRunRuntime, "_build_memory_manager", return_value=_MemoryManager()),
            mock.patch.object(cow_runtime, "image_task_service", task_service),
        ):
            runtime = cow_runtime.CowAgentRunRuntime(run)
            runtime.add_long_term = mock.Mock()
            runtime.model = _SkillImageToolLoopModel()
            runtime.execute()

        self.assertEqual(AgentRunStatus.COMPLETED, run.status, run.error)
        self.assertEqual("full_agent", run.result["optimizationRoute"])
        self.assertEqual("execute", run.result["turnIntent"]["intent"])
        self.assertEqual("rules", run.result["turnIntent"]["source"])
        self.assertEqual("https://example.test/main-typography.png", run.result["images"][0]["url"])
        self.assertEqual(1, run.result["modelUsage"]["imageGenerationCalls"])
        task_service.submit_generation.assert_called_once()

    def test_optimized_first_turn_generation_routes_directly_without_agent_loop(self):
        run = self._run(turn="optimized-first-generation")
        run.request["prompt"] = "生成一张蓝色未来城市插画"
        task_service = SimpleNamespace(
            submit_generation=mock.Mock(return_value={
                "id": "optimized-first-task",
                "status": "success",
                "data": [{"url": "https://example.test/future-city.png", "width": 1024, "height": 1024}],
            }),
        )
        with (
            mock.patch.object(cow_runtime.CowAgentRunRuntime, "_build_memory_manager", return_value=_MemoryManager()),
            mock.patch.object(cow_runtime, "image_task_service", task_service),
            mock.patch.object(cow_runtime, "AgentStreamExecutor", side_effect=AssertionError("full Agent loop must not run")),
        ):
            runtime = cow_runtime.CowAgentRunRuntime(run)
            runtime.add_long_term = mock.Mock()

            def route_then_plan(request):
                runtime.model.dialogue_calls += 1
                if "intent router" in request.system:
                    return {"choices": [{"message": {"role": "assistant", "content": (
                        '{"intent":"execute","confidence":0.99,"shouldGenerate":true,'
                        '"needClarification":false,"requiredTools":["raw_generate_image"],'
                        '"imageSourcePolicy":"none","hardConstraints":{"textAllowed":false},'
                        '"reason":"用户明确要求生成图片。"}'
                    )}}]}
                return {"choices": [{"message": {"role": "assistant", "content": (
                    '{"finalPrompt":"蓝色未来城市插画，层次丰富的建筑与真实空间光影",'
                    '"negativePrompt":"","needsTypography":false,'
                    '"pages":[{"title":"未来城市","purpose":"视觉创作",'
                    '"prompt":"蓝色未来城市插画，层次丰富的建筑与真实空间光影"}]}'
                )}}]}

            runtime.model.call = mock.Mock(side_effect=route_then_plan)
            runtime.execute()

        self.assertEqual(AgentRunStatus.COMPLETED, run.status, run.error)
        self.assertEqual("confirmed_generation", run.result["optimizationRoute"])
        self.assertEqual("execute", run.result["turnIntent"]["intent"])
        self.assertTrue(run.result["turnIntent"]["shouldGenerate"])
        self.assertEqual(2, run.result["modelUsage"]["dialogueCalls"])
        self.assertEqual(1, run.result["modelUsage"]["imageGenerationCalls"])
        task_service.submit_generation.assert_called_once()

    def test_typography_main_image_uses_marketing_strategy_before_generation(self):
        run = self._run()
        run.request["prompt"] = "生成主图带文字排版，背景用宠物航空箱，红色大字"
        task_service = SimpleNamespace(
            submit_generation=mock.Mock(return_value={
                "id": "marketing-main-image-task",
                "status": "success",
                "data": [{"url": "https://example.test/marketing-main.png", "width": 1024, "height": 1024}],
            }),
        )
        with (
            mock.patch.object(cow_runtime.CowAgentRunRuntime, "_build_memory_manager", return_value=_MemoryManager()),
            mock.patch.object(cow_runtime, "image_task_service", task_service),
        ):
            runtime = cow_runtime.CowAgentRunRuntime(run)
            runtime.add_long_term = mock.Mock()
            runtime.model = _MarketingStrategyToolLoopModel()
            runtime.execute()

        self.assertEqual(AgentRunStatus.COMPLETED, run.status, run.error)
        self.assertEqual("full_agent", run.result["optimizationRoute"])
        self.assertEqual("出行也清爽", run.result["marketingStrategy"]["headline"])
        self.assertEqual(["便携喷雾", "箱内清爽", "外出适用"], run.result["marketingStrategy"]["sellingPointLabels"])
        prompt = task_service.submit_generation.call_args.kwargs["prompt"]
        self.assertIn("营销文案与版式策略：", prompt)
        self.assertIn("主标题：出行也清爽", prompt)
        self.assertIn("短卖点标签：便携喷雾、箱内清爽、外出适用", prompt)
        messages = self.memory.load_messages(owner_id="cow-user", conversation_id="conversation-1")
        tool_names = [
            block.get("name")
            for item in messages
            if item["messageType"] == "cow_message"
            for block in item["content"]["message"].get("content", [])
            if isinstance(block, dict) and block.get("type") == "tool_use"
        ]
        self.assertEqual(["read", "raw_marketing_strategy", "raw_generate_image"], tool_names)

    def test_followup_generate_after_analysis_does_not_loop_on_confirmation(self):
        run = self._run()
        run.request["prompt"] = "生成"
        self.memory.append_message(
            owner_id="cow-user",
            conversation_id="conversation-1",
            message_key="prior-analysis",
            role="assistant",
            message_type="cow_message",
            content={
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "我建议先做一张带文字排版的商品主图，背景用浅灰空间，标题放上方，卖点放右侧。"}],
                }
            },
        )
        task_service = SimpleNamespace(
            submit_generation=mock.Mock(return_value={
                "id": "followup-generation-task",
                "status": "success",
                "data": [{"url": "https://example.test/followup-generated.png", "width": 1024, "height": 1024}],
            }),
        )
        with (
            mock.patch.object(cow_runtime.CowAgentRunRuntime, "_build_memory_manager", return_value=_MemoryManager()),
            mock.patch.object(cow_runtime, "image_task_service", task_service),
        ):
            runtime = cow_runtime.CowAgentRunRuntime(run)
            runtime.add_long_term = mock.Mock()
            runtime.model = _SkillImageToolLoopModel()
            runtime.execute()

        self.assertEqual(AgentRunStatus.COMPLETED, run.status, run.error)
        self.assertEqual("execute", run.result["turnIntent"]["intent"])
        self.assertGreaterEqual(run.tool_calls, 1)
        self.assertEqual("https://example.test/followup-generated.png", run.result["images"][0]["url"])
        self.assertEqual(1, run.result["modelUsage"]["imageGenerationCalls"])
        task_service.submit_generation.assert_called_once()

    def test_confirmed_plan_uses_one_planner_call_then_generates(self):
        run = self._run()
        run.request["prompt"] = "执行，按刚才的方案生成"
        self.memory.append_message(
            owner_id="cow-user",
            conversation_id="conversation-1",
            message_key="prior-plan",
            role="assistant",
            message_type="cow_message",
            content={
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "方案：汽车主图采用深灰摄影棚背景、低机位构图和侧逆光。"}],
                }
            },
        )
        task_service = SimpleNamespace(
            submit_generation=mock.Mock(return_value={
                "id": "fast-task-1",
                "status": "success",
                "data": [{"url": "https://example.test/fast-car.png", "width": 1024, "height": 1024}],
            }),
        )
        with (
            mock.patch.object(cow_runtime.CowAgentRunRuntime, "_build_memory_manager", return_value=_MemoryManager()),
            mock.patch.object(cow_runtime, "image_task_service", task_service),
        ):
            runtime = cow_runtime.CowAgentRunRuntime(run)
            runtime.add_long_term = mock.Mock()

            def direct_plan(_request):
                runtime.model.dialogue_calls += 1
                return {
                    "choices": [{
                        "message": {
                            "role": "assistant",
                            "content": '{"finalPrompt":"Premium car hero image in a dark gray studio with side rim light","negativePrompt":"white empty background","pages":[{"title":"汽车主图","purpose":"首屏展示","prompt":"Premium car hero image in a dark gray studio with side rim light"}]}',
                        }
                    }]
                }

            runtime.model.call = mock.Mock(side_effect=direct_plan)
            runtime.execute()

        self.assertEqual(AgentRunStatus.COMPLETED, run.status, run.error)
        self.assertEqual("confirmed_generation", run.result["optimizationRoute"])
        self.assertEqual(1, run.result["modelUsage"]["totalCalls"])
        self.assertEqual(1, run.result["modelUsage"]["imageGenerationCalls"])
        self.assertEqual(1, run.tool_calls)
        self.assertEqual("https://example.test/fast-car.png", run.result["images"][0]["url"])
        runtime.model.call.assert_called_once()
        task_service.submit_generation.assert_called_once()

    def test_concrete_visual_edit_request_with_reference_executes_directly(self):
        run = self._run()
        run.request["prompt"] = "换一个背景和文字排版"
        run.request["mode"] = "edit"
        run.request["images"] = [{
            "name": "product.png",
            "type": "image/png",
            "data_url": "data:image/png;base64,aW1hZ2U=",
        }]
        task_service = SimpleNamespace(
            submit_generation=mock.Mock(),
            submit_edit=mock.Mock(return_value={
                "id": "visual-edit-task",
                "status": "success",
                "data": [{"url": "https://example.test/visual-edit.png", "width": 1024, "height": 1024}],
            }),
        )
        with (
            mock.patch.object(cow_runtime.CowAgentRunRuntime, "_build_memory_manager", return_value=_MemoryManager()),
            mock.patch.object(cow_runtime, "image_task_service", task_service),
        ):
            runtime = cow_runtime.CowAgentRunRuntime(run)
            runtime.add_long_term = mock.Mock()
            runtime.model.analyze_image = mock.Mock(return_value="可见一件包装完整的商品，适合电商主图展示。")
            runtime.model.call = mock.Mock(return_value={
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": (
                            '{"finalPrompt":"Preserve the product and change only the background and typography",'
                            '"negativePrompt":"",'
                            '"pages":[{"title":"中文排版主图","purpose":"电商展示",'
                            '"prompt":"Preserve the product and change only the background and typography"}]}'
                        ),
                    }
                }]
            })
            runtime.execute()

        self.assertEqual(AgentRunStatus.COMPLETED, run.status, run.error)
        self.assertEqual("execute", run.result["turnIntent"]["intent"])
        self.assertEqual("confirmed_generation", run.result["optimizationRoute"])
        self.assertEqual(1, run.result["modelUsage"]["imageGenerationCalls"])
        task_service.submit_edit.assert_called_once()
        task_service.submit_generation.assert_not_called()

    def test_latest_no_text_instruction_removes_old_typography_before_generation(self):
        run = self._run(turn="no-text-hard-constraint")
        run.request.update({
            "prompt": "用原图生成一个生活场景主图，不要文字",
            "mode": "edit",
            "images": [{
                "name": "product-anchor.png",
                "type": "image/png",
                "role": "product_anchor",
                "data_url": "data:image/png;base64,aW1hZ2U=",
            }],
        })
        task_service = SimpleNamespace(
            submit_generation=mock.Mock(),
            submit_edit=mock.Mock(return_value={
                "id": "no-text-task",
                "status": "success",
                "data": [{"url": "https://example.test/no-text.png", "width": 1024, "height": 1024}],
            }),
        )
        with (
            mock.patch.object(cow_runtime.CowAgentRunRuntime, "_build_memory_manager", return_value=_MemoryManager()),
            mock.patch.object(cow_runtime, "image_task_service", task_service),
        ):
            runtime = cow_runtime.CowAgentRunRuntime(run)
            runtime.add_long_term = mock.Mock()
            runtime.model.analyze_image = mock.Mock(return_value="可见一件蓝色包装商品，瓶身结构完整。")

            def route_then_plan(request):
                runtime.model.dialogue_calls += 1
                if "Agent router" in request.system:
                    return {"choices": [{"message": {"role": "assistant", "content": (
                        '{"intent":"execute","confidence":0.99,"shouldGenerate":true,'
                        '"requiredTools":["raw_vision","raw_marketing_strategy","raw_generate_image"],'
                        '"imageSourcePolicy":"original_upload","hardConstraints":{"textAllowed":true},'
                        '"reason":"用户要求生成生活场景主图。"}'
                    )}}]}
                return {"choices": [{"message": {"role": "assistant", "content": (
                    '{"finalPrompt":"产品置于右侧，红色大标题，添加文案新品上市，生活空间背景",'
                    '"negativePrompt":"","needsTypography":true,'
                    '"pages":[{"title":"文字主图","purpose":"宣传","prompt":"产品置于右侧，红色大标题，添加文案新品上市，生活空间背景"}]}'
                )}}]}

            runtime.model.call = mock.Mock(side_effect=route_then_plan)
            runtime.execute()

        self.assertEqual(AgentRunStatus.COMPLETED, run.status, run.error)
        self.assertFalse(run.result["promptPlan"]["needsTypography"])
        self.assertFalse(run.result["marketingStrategy"]["shouldUse"])
        self.assertEqual("original_upload", run.result["decisionTrace"]["referenceSelection"]["policy"])
        self.assertIn("overlay_text_forbidden", run.result["decisionTrace"]["generationPreflight"]["repairs"])
        prompt = task_service.submit_edit.call_args.kwargs["prompt"]
        self.assertIn("画面不得新增标题、文案、卖点", prompt)
        self.assertNotIn("红色大标题", prompt)
        self.assertNotIn("添加文案新品上市", prompt)
        self.assertNotIn(cow_runtime.MARKETING_STRATEGY_PROMPT_MARKER, prompt)
        task_service.submit_edit.assert_called_once()
        task_service.submit_generation.assert_not_called()

    def test_reference_policy_selects_latest_original_and_new_sources(self):
        images = [
            {"name": "working-canvas.png", "type": "image/png", "role": "working_canvas", "data_url": "data:image/png;base64,d29ya2luZw=="},
            {"name": "product-anchor.png", "type": "image/png", "role": "product_anchor", "data_url": "data:image/png;base64,YW5jaG9y"},
            {"name": "new-style.png", "type": "image/png", "role": "reference", "data_url": "data:image/png;base64,c3R5bGU="},
        ]

        latest_run = self._run(conversation="source-latest", turn="source-latest", images=images)
        latest_run.request["prompt"] = "在上一张基础上继续修改并生成"
        latest_runtime = cow_runtime.CowAgentRunRuntime(latest_run)
        latest_runtime.save_attachments()
        latest_decision = latest_runtime._rule_turn_decision([], source="test")
        latest_runtime._apply_reference_selection(latest_decision)
        self.assertEqual("latest_generated", latest_decision["imageSourcePolicy"])
        self.assertEqual(["working_canvas", "product_anchor", "reference"], latest_runtime.attachment_roles)

        incremental_run = self._run(conversation="source-incremental", turn="source-incremental", images=images)
        incremental_run.request["prompt"] = "再加一个杯子，其他不变"
        incremental_runtime = cow_runtime.CowAgentRunRuntime(incremental_run)
        incremental_runtime.save_attachments()
        incremental_decision = incremental_runtime._rule_turn_decision([], source="test")
        incremental_decision["imageSourcePolicy"] = "original_upload"
        incremental_decision = incremental_runtime._finalize_reference_policy(incremental_decision)
        incremental_runtime._apply_reference_selection(incremental_decision)
        self.assertEqual("latest_generated", incremental_decision["imageSourcePolicy"])
        self.assertEqual("working_canvas", incremental_runtime.attachment_roles[0])

        original_run = self._run(conversation="source-original", turn="source-original", images=images)
        original_run.request["prompt"] = "不要上一版，从原图重新生成"
        original_runtime = cow_runtime.CowAgentRunRuntime(original_run)
        original_runtime.save_attachments()
        original_decision = original_runtime._rule_turn_decision([], source="test")
        original_runtime._apply_reference_selection(original_decision)
        self.assertEqual("original_upload", original_decision["imageSourcePolicy"])
        self.assertEqual(["product_anchor", "reference"], original_runtime.attachment_roles)

        new_run = self._run(conversation="source-new", turn="source-new", images=images)
        new_run.request["prompt"] = "只用这张新产品生成，不要历史图"
        new_runtime = cow_runtime.CowAgentRunRuntime(new_run)
        new_runtime.save_attachments()
        new_decision = new_runtime._rule_turn_decision([], source="test")
        new_runtime._apply_reference_selection(new_decision)
        self.assertEqual("new_upload", new_decision["imageSourcePolicy"])
        self.assertEqual(["reference"], new_runtime.attachment_roles)

    def test_ambiguous_confirmation_without_plan_never_generates(self):
        run = self._run(turn="confirmation-without-plan")
        run.request["prompt"] = "好的"
        task_service = SimpleNamespace(submit_generation=mock.Mock(), submit_edit=mock.Mock())
        with (
            mock.patch.object(cow_runtime.CowAgentRunRuntime, "_build_memory_manager", return_value=_MemoryManager()),
            mock.patch.object(cow_runtime, "image_task_service", task_service),
        ):
            runtime = cow_runtime.CowAgentRunRuntime(run)
            runtime.add_long_term = mock.Mock()

            def unsafe_router_then_reply(request):
                runtime.model.dialogue_calls += 1
                if "Agent router" in request.system:
                    return {"choices": [{"message": {"role": "assistant", "content": (
                        '{"intent":"execute","confidence":0.99,"shouldGenerate":true,'
                        '"requiredTools":["raw_generate_image"],"imageSourcePolicy":"none",'
                        '"reason":"用户说好的。"}'
                    )}}]}
                return {"choices": [{"message": {"role": "assistant", "content": "请先告诉我要生成什么商品图片。"}}]}

            runtime.model.call = mock.Mock(side_effect=unsafe_router_then_reply)
            runtime.execute()

        self.assertEqual(AgentRunStatus.COMPLETED, run.status, run.error)
        self.assertEqual("propose", run.result["turnIntent"]["intent"])
        self.assertFalse(run.result["turnIntent"]["shouldGenerate"])
        self.assertEqual([], run.result["images"])
        task_service.submit_generation.assert_not_called()
        task_service.submit_edit.assert_not_called()

    def test_direct_generation_defaults_page_titles_to_chinese(self):
        run = self._run()
        run.request["prompt"] = "确认执行"
        self.memory.append_message(
            owner_id="cow-user",
            conversation_id="conversation-1",
            message_key="prior-english-display-plan",
            role="assistant",
            message_type="cow_message",
            content={
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "方案：中文淘宝文字主图，左侧标题，右侧商品。确认后执行。"}],
                }
            },
        )
        task_service = SimpleNamespace(
            submit_generation=mock.Mock(return_value={
                "id": "direct-task-1",
                "status": "success",
                "data": [{"url": "https://example.test/direct.png", "width": 1024, "height": 1024}],
            }),
        )

        with (
            mock.patch.object(cow_runtime.CowAgentRunRuntime, "_build_memory_manager", return_value=_MemoryManager()),
            mock.patch.object(cow_runtime, "image_task_service", task_service),
        ):
            runtime = cow_runtime.CowAgentRunRuntime(run)
            runtime.add_long_term = mock.Mock()
            runtime.model.call = mock.Mock(return_value={
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": '{"finalPrompt":"中文排版主图","negativePrompt":"","pages":[{"title":"Safe for Pets","purpose":"English Copy","prompt":"中文排版主图"}]}',
                    }
                }]
            })
            runtime.execute()

        self.assertEqual(AgentRunStatus.COMPLETED, run.status, run.error)
        self.assertEqual("第 1 张方案", run.result["proposal"]["pages"][0]["title"])
        self.assertEqual("按当前视觉方向生成", run.result["proposal"]["pages"][0]["purpose"])
        self.assertEqual("confirmed_generation", run.result["optimizationRoute"])
        task_service.submit_generation.assert_called_once()

    def test_natural_confirmation_phrases_are_execution_requests(self):
        execution_messages = (
            "确认",
            "好的",
            "可以进行",
            "好的，你现在给我生图吧",
            "立即生图别废话了",
            "按这个方案来",
        )
        for message in execution_messages:
            with self.subTest(message=message):
                self.assertTrue(cow_runtime._is_explicit_execution_request(message))

        for message in ("先不要生成", "先分析", "我想看看文字排版方案"):
            with self.subTest(message=message):
                self.assertFalse(cow_runtime._is_explicit_execution_request(message))

    def test_concrete_visual_edit_phrases_are_generation_requests(self):
        execution_messages = (
            "换一个背景和文字排版",
            "把背景改成浅灰并调整文字排版",
            "replace the background and edit the typography",
        )
        for message in execution_messages:
            with self.subTest(message=message):
                self.assertTrue(cow_runtime._is_current_turn_generation_request(message))

    def test_confirmation_only_uses_the_latest_assistant_plan(self):
        plan = {
            "role": "assistant",
            "content": [{"type": "text", "text": "方案：左上角标题，右侧保留商品主体。确认后执行。"}],
        }
        unrelated = {
            "role": "assistant",
            "content": [{"type": "text", "text": "已经取消当前任务，我们继续讨论其他问题。"}],
        }
        canceled_placeholder = {
            "role": "assistant",
            "content": [{"type": "text", "text": "_(Cancelled by user)_"}],
        }

        self.assertTrue(cow_runtime._has_confirmable_plan([plan]))
        self.assertTrue(cow_runtime._has_confirmable_plan([plan, canceled_placeholder]))
        self.assertFalse(cow_runtime._has_confirmable_plan([plan, unrelated]))

    def test_immediate_image_request_with_reference_skips_repeated_analysis(self):
        run = self._run()
        run.request["prompt"] = "立即生图别废话了"
        run.request["images"] = [{
            "name": "product.png",
            "type": "image/png",
            "data_url": "data:image/png;base64,aW1hZ2U=",
        }]
        self.memory.append_message(
            owner_id="cow-user",
            conversation_id="conversation-1",
            message_key="prior-typography-plan",
            role="assistant",
            message_type="cow_message",
            content={
                "message": {
                    "role": "assistant",
                    "content": [{
                        "type": "text",
                        "text": "文字排版方案：标题放左上角，商品主体保留在右侧。确认后立即生成。",
                    }],
                }
            },
        )
        completed_task = {
            "id": "immediate-image-task",
            "status": "success",
            "data": [{"url": "https://example.test/typography.png", "width": 1024, "height": 1024}],
        }
        task_service = SimpleNamespace(
            submit_generation=mock.Mock(return_value=completed_task),
            submit_edit=mock.Mock(return_value={
                "id": "immediate-image-task",
                "status": "success",
                "data": [{"url": "https://example.test/typography.png", "width": 1024, "height": 1024}],
            }),
        )
        with (
            mock.patch.object(cow_runtime.CowAgentRunRuntime, "_build_memory_manager", return_value=_MemoryManager()),
            mock.patch.object(cow_runtime, "image_task_service", task_service),
        ):
            runtime = cow_runtime.CowAgentRunRuntime(run)
            runtime.add_long_term = mock.Mock()

            def direct_plan(_request):
                runtime.model.dialogue_calls += 1
                return {
                    "choices": [{
                        "message": {
                            "role": "assistant",
                            "content": (
                                '{"finalPrompt":"Add the confirmed ecommerce typography to the supplied product image",'
                                '"negativePrompt":"",'
                                '"pages":[{"title":"文字主图","purpose":"电商展示",'
                                '"prompt":"Add the confirmed ecommerce typography to the supplied product image"}]}'
                            ),
                        }
                    }],
                }

            runtime.model.call = mock.Mock(side_effect=direct_plan)
            runtime.execute()

        self.assertEqual(AgentRunStatus.COMPLETED, run.status, run.error)
        self.assertEqual("confirmed_generation", run.result["optimizationRoute"])
        self.assertEqual(1, run.result["modelUsage"]["totalCalls"])
        self.assertEqual(1, run.result["modelUsage"]["imageGenerationCalls"])
        self.assertEqual("https://example.test/typography.png", run.result["images"][0]["url"])
        task_service.submit_edit.assert_called_once()
        task_service.submit_generation.assert_not_called()

    def test_model_intent_router_turns_ambiguous_approval_into_generation(self):
        run = self._run()
        run.request["prompt"] = "就照你刚才建议的方向处理"
        self.memory.append_message(
            owner_id="cow-user",
            conversation_id="conversation-1",
            message_key="model-routed-plan",
            role="assistant",
            message_type="cow_message",
            content={
                "message": {
                    "role": "assistant",
                    "content": [{
                        "type": "text",
                        "text": "视觉方案：采用深灰摄影棚背景、左文右物构图。确认后执行生图。",
                    }],
                }
            },
        )
        task_service = SimpleNamespace(
            submit_generation=mock.Mock(return_value={
                "id": "model-routed-task",
                "status": "success",
                "data": [{"url": "https://example.test/model-routed.png", "width": 1024, "height": 1024}],
            }),
        )
        with (
            mock.patch.object(cow_runtime.CowAgentRunRuntime, "_build_memory_manager", return_value=_MemoryManager()),
            mock.patch.object(cow_runtime, "image_task_service", task_service),
        ):
            runtime = cow_runtime.CowAgentRunRuntime(run)
            runtime.add_long_term = mock.Mock()

            def route_then_plan(request):
                runtime.model.dialogue_calls += 1
                if "intent router" in request.system:
                    return {"choices": [{"message": {"role": "assistant", "content": (
                        '{"intent":"execute","confidence":0.94,'
                        '"reason":"用户要求按最近建议的方向处理。"}'
                    )}}]}
                return {"choices": [{"message": {"role": "assistant", "content": (
                    '{"finalPrompt":"Premium product image in a dark gray studio",'
                    '"negativePrompt":"",'
                    '"pages":[{"title":"商品主图","purpose":"展示",'
                    '"prompt":"Premium product image in a dark gray studio"}]}'
                )}}]}

            runtime.model.call = mock.Mock(side_effect=route_then_plan)
            runtime.execute()

        self.assertEqual(AgentRunStatus.COMPLETED, run.status, run.error)
        self.assertEqual("confirmed_generation", run.result["optimizationRoute"])
        self.assertEqual("execute", run.result["turnIntent"]["intent"])
        self.assertEqual("model", run.result["turnIntent"]["source"])
        self.assertEqual(2, run.result["modelUsage"]["dialogueCalls"])
        self.assertEqual(1, run.result["modelUsage"]["imageGenerationCalls"])
        task_service.submit_generation.assert_called_once()

    def test_model_intent_router_keeps_revision_out_of_generation(self):
        run = self._run()
        run.request["prompt"] = "标题能不能再醒目一点"
        self.memory.append_message(
            owner_id="cow-user",
            conversation_id="conversation-1",
            message_key="revision-plan",
            role="assistant",
            message_type="cow_message",
            content={
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "文字排版方案：标题放左上角，确认后执行。"}],
                }
            },
        )
        task_service = SimpleNamespace(submit_generation=mock.Mock(), submit_edit=mock.Mock())
        with (
            mock.patch.object(cow_runtime.CowAgentRunRuntime, "_build_memory_manager", return_value=_MemoryManager()),
            mock.patch.object(cow_runtime, "image_task_service", task_service),
        ):
            runtime = cow_runtime.CowAgentRunRuntime(run)
            runtime.add_long_term = mock.Mock()

            def route_then_reply(request):
                runtime.model.dialogue_calls += 1
                if "intent router" in request.system:
                    return {"choices": [{"message": {"role": "assistant", "content": (
                        '{"intent":"revise","confidence":0.98,'
                        '"reason":"用户要求修改标题表现，未要求立即生成。"}'
                    )}}]}
                return {"choices": [{"message": {"role": "assistant", "content": "可以，我会提高标题字号和对比度，调整后等你确认执行。"}}]}

            runtime.model.call = mock.Mock(side_effect=route_then_reply)
            runtime.execute()

        self.assertEqual(AgentRunStatus.COMPLETED, run.status, run.error)
        self.assertEqual("direct_consult", run.result["optimizationRoute"])
        self.assertEqual("revise", run.result["turnIntent"]["intent"])
        self.assertEqual([], run.result["images"])
        task_service.submit_generation.assert_not_called()
        task_service.submit_edit.assert_not_called()

    def test_model_intent_router_cancels_pending_plan_without_generation(self):
        run = self._run()
        run.request["prompt"] = "这个先算了吧"
        self.memory.append_message(
            owner_id="cow-user",
            conversation_id="conversation-1",
            message_key="cancel-plan",
            role="assistant",
            message_type="cow_message",
            content={
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "主图方案已完成，确认后执行生图。"}],
                }
            },
        )
        task_service = SimpleNamespace(submit_generation=mock.Mock(), submit_edit=mock.Mock())
        with (
            mock.patch.object(cow_runtime.CowAgentRunRuntime, "_build_memory_manager", return_value=_MemoryManager()),
            mock.patch.object(cow_runtime, "image_task_service", task_service),
        ):
            runtime = cow_runtime.CowAgentRunRuntime(run)
            runtime.add_long_term = mock.Mock()

            def classify_cancel(_request):
                runtime.model.dialogue_calls += 1
                return {"choices": [{"message": {"role": "assistant", "content": (
                    '{"intent":"cancel","confidence":0.99,'
                    '"reason":"用户明确表示暂不继续当前方案。"}'
                )}}]}

            runtime.model.call = mock.Mock(side_effect=classify_cancel)
            runtime.execute()

        self.assertEqual(AgentRunStatus.COMPLETED, run.status, run.error)
        self.assertEqual("intent_cancel", run.result["optimizationRoute"])
        self.assertEqual("cancel", run.result["turnIntent"]["intent"])
        self.assertIn("不会调用生图", run.result["assistantMessage"])
        task_service.submit_generation.assert_not_called()
        task_service.submit_edit.assert_not_called()

    def test_low_confidence_model_execution_is_safely_downgraded(self):
        run = self._run()
        run.request["prompt"] = "这个方向好像还行"
        self.memory.append_message(
            owner_id="cow-user",
            conversation_id="conversation-1",
            message_key="uncertain-plan",
            role="assistant",
            message_type="cow_message",
            content={
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "主图方案：浅色空间背景。确认后执行。"}],
                }
            },
        )
        task_service = SimpleNamespace(submit_generation=mock.Mock(), submit_edit=mock.Mock())
        with (
            mock.patch.object(cow_runtime.CowAgentRunRuntime, "_build_memory_manager", return_value=_MemoryManager()),
            mock.patch.object(cow_runtime, "image_task_service", task_service),
        ):
            runtime = cow_runtime.CowAgentRunRuntime(run)
            runtime.add_long_term = mock.Mock()

            def uncertain_then_reply(request):
                runtime.model.dialogue_calls += 1
                if "intent router" in request.system:
                    return {"choices": [{"message": {"role": "assistant", "content": (
                        '{"intent":"execute","confidence":0.51,'
                        '"reason":"用户表达认可但没有清楚授权生图。"}'
                    )}}]}
                return {"choices": [{"message": {"role": "assistant", "content": "这个方向可以继续细化，目前还没有执行生图。"}}]}

            runtime.model.call = mock.Mock(side_effect=uncertain_then_reply)
            runtime.execute()

        self.assertEqual("revise", run.result["turnIntent"]["intent"])
        self.assertEqual("execute", run.result["turnIntent"]["originalIntent"])
        self.assertEqual([], run.result["images"])
        task_service.submit_generation.assert_not_called()
        task_service.submit_edit.assert_not_called()

    def test_current_text_ratio_overrides_square_ui_size_during_fast_generation(self):
        run = self._run()
        run.request["prompt"] = "我不要1：1的图，给我生成其他比例的"
        run.request["size"] = "1024x1024"
        self.memory.append_message(
            owner_id="cow-user",
            conversation_id="conversation-1",
            message_key="prior-square-plan",
            role="assistant",
            message_type="cow_message",
            content={"message": {"role": "assistant", "content": [{"type": "text", "text": "方案：生成一张商品主图。"}]}},
        )
        task_service = SimpleNamespace(
            submit_generation=mock.Mock(return_value={
                "id": "non-square-task",
                "status": "success",
                "data": [{"url": "https://example.test/non-square.png", "width": 1024, "height": 1536}],
            }),
        )
        with (
            mock.patch.object(cow_runtime.CowAgentRunRuntime, "_build_memory_manager", return_value=_MemoryManager()),
            mock.patch.object(cow_runtime, "image_task_service", task_service),
        ):
            runtime = cow_runtime.CowAgentRunRuntime(run)
            runtime.add_long_term = mock.Mock()
            runtime.model.call = mock.Mock(return_value={
                "choices": [{"message": {"role": "assistant", "content": (
                    '{"finalPrompt":"Premium product image","negativePrompt":"",'
                    '"pages":[{"title":"主图","purpose":"展示","prompt":"Premium product image"}]}'
                )}}],
            })
            runtime.execute()

        self.assertEqual(AgentRunStatus.COMPLETED, run.status, run.error)
        self.assertEqual("1024x1536", task_service.submit_generation.call_args.kwargs["size"])
        self.assertEqual("1024x1536", run.request["size"])
        self.assertEqual("1024x1536", run.result["promptPlan"]["resolvedSize"])
        self.assertEqual("1024x1536", run.result["images"][0]["requestedSize"])
        self.assertIn("覆盖历史方案和界面旧比例", task_service.submit_generation.call_args.kwargs["prompt"])

    def test_ratio_parser_honors_explicit_orientation_and_dimensions(self):
        self.assertEqual("1024x1536", cow_runtime._resolve_user_image_size("不要1:1，换其他比例", "1024x1024"))
        self.assertEqual("1920x1088", cow_runtime._resolve_user_image_size("不要1:1，改成16:9", "1024x1024"))
        self.assertEqual("1536x1024", cow_runtime._resolve_user_image_size("生成横版商品图", "1024x1024"))
        self.assertEqual("1024x1365", cow_runtime._resolve_user_image_size("使用3:4比例", "1024x1024"))
        self.assertEqual("1200x1600", cow_runtime._resolve_user_image_size("输出1200x1600", "1024x1024"))
        self.assertEqual("1024x1024", cow_runtime._resolve_user_image_size("按方案执行", "1024x1024"))

    def test_confirmed_plan_releases_agent_while_image_task_is_pending(self):
        run = self._run()
        run.request["prompt"] = "执行，按刚才的方案生成"
        self.memory.append_message(
            owner_id="cow-user",
            conversation_id="conversation-1",
            message_key="prior-plan-pending",
            role="assistant",
            message_type="cow_message",
            content={"message": {"role": "assistant", "content": [{"type": "text", "text": "方案：深灰摄影棚背景。"}]}},
        )
        task_service = SimpleNamespace(
            submit_generation=mock.Mock(return_value={"id": "pending-task-1", "status": "queued"}),
        )
        with (
            mock.patch.object(cow_runtime.CowAgentRunRuntime, "_build_memory_manager", return_value=_MemoryManager()),
            mock.patch.object(cow_runtime, "image_task_service", task_service),
            mock.patch.object(
                type(cow_runtime.agent_queue_service),
                "settings",
                new_callable=mock.PropertyMock,
                return_value=SimpleNamespace(enabled=True),
            ),
        ):
            runtime = cow_runtime.CowAgentRunRuntime(run)
            runtime.add_long_term = mock.Mock()
            runtime.model.call = mock.Mock(return_value={
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": '{"finalPrompt":"Premium car studio image","negativePrompt":"","pages":[{"title":"主图","purpose":"展示","prompt":"Premium car studio image"}]}',
                    }
                }]
            })
            runtime.execute()

        self.assertEqual(AgentRunStatus.WAITING_FOR_IMAGES, run.status, run.error)
        self.assertEqual("generating", run.result["phase"])
        self.assertEqual(["pending-task-1"], run.result["pendingTaskIds"])
        task_service.submit_generation.assert_called_once()

    def test_pending_image_generation_resumes_without_replanning(self):
        run = self._run()
        run.metadata["pendingImageGeneration"] = {
            "taskIds": ["pending-task-1"],
            "pages": [{"title": "主图", "purpose": "展示", "prompt": "Premium car studio image"}],
            "proposalPages": [{"id": "fast-page-1", "title": "主图", "purpose": "展示", "prompt": "Premium car studio image"}],
            "finalPrompt": "Premium car studio image",
            "negativePrompt": "",
            "needsTypography": False,
            "modelUsage": {"dialogueCalls": 1, "visionCalls": 0, "totalCalls": 1, "imageGenerationCalls": 1},
        }
        task_service = SimpleNamespace(
            list_tasks=mock.Mock(return_value={
                "items": [{
                    "id": "pending-task-1",
                    "status": "success",
                    "data": [{"url": "https://example.test/resumed.png", "width": 1024, "height": 1024}],
                }],
                "missing_ids": [],
            }),
        )
        with (
            mock.patch.object(cow_runtime.CowAgentRunRuntime, "_build_memory_manager", return_value=_MemoryManager()),
            mock.patch.object(cow_runtime, "image_task_service", task_service),
        ):
            runtime = cow_runtime.CowAgentRunRuntime(run)
            runtime.resume_pending_image_generation()

        self.assertEqual(AgentRunStatus.COMPLETED, run.status, run.error)
        self.assertEqual("https://example.test/resumed.png", run.result["images"][0]["url"])
        self.assertNotIn("pendingImageGeneration", run.metadata)
        self.assertEqual(1, run.result["modelUsage"]["totalCalls"])

    def test_folder_batch_releases_agent_while_images_are_pending(self):
        run = self._run()
        run.request.update({"prompt": "确认执行文件夹方案", "folder_id": "folder-1"})
        folder_service = SimpleNamespace(
            create_batch_plan=mock.Mock(return_value={
                "planId": "batch-1",
                "totalItems": 2,
                "items": [],
            }),
            execute_batch_plan=mock.Mock(return_value={
                "planId": "batch-1",
                "totalItems": 2,
                "completedItems": 0,
                "failedItems": 0,
                "items": [
                    {"taskId": "folder-task-1", "status": "queued"},
                    {"taskId": "folder-task-2", "status": "queued"},
                ],
            }),
        )
        with (
            mock.patch.object(cow_runtime.CowAgentRunRuntime, "_build_memory_manager", return_value=_MemoryManager()),
            mock.patch.object(cow_runtime, "professional_folder_asset_service", folder_service),
            mock.patch.object(
                type(cow_runtime.agent_queue_service),
                "settings",
                new_callable=mock.PropertyMock,
                return_value=SimpleNamespace(enabled=True),
            ),
        ):
            runtime = cow_runtime.CowAgentRunRuntime(run)
            runtime._load_folder_context = mock.Mock(return_value={"fileCount": 2, "summary": {"scene": 2}})
            runtime._direct_model_text = mock.Mock(return_value=(
                '{"finalPrompt":"统一商业场景",'
                '"pages":[{"title":"场景图","category":"all","purpose":"展示","prompt":"统一商业场景"}]}'
            ))
            runtime._run_folder_execution([], started=time.perf_counter())

        self.assertEqual(AgentRunStatus.WAITING_FOR_IMAGES, run.status, run.error)
        self.assertEqual(["folder-task-1", "folder-task-2"], run.result["pendingTaskIds"])
        self.assertEqual("batch-1", run.metadata["pendingFolderBatch"]["planId"])
        self.assertEqual(
            run.run_id,
            folder_service.execute_batch_plan.call_args.kwargs["agent_run_id"],
        )

    def test_pending_folder_batch_resumes_without_replanning(self):
        run = self._run()
        run.request["folder_id"] = "folder-1"
        run.metadata["pendingFolderBatch"] = {
            "planId": "batch-1",
            "taskIds": ["folder-task-1", "folder-task-2"],
            "pages": [{"title": "场景图", "category": "all", "purpose": "展示", "prompt": "统一商业场景"}],
            "finalPrompt": "统一商业场景",
            "folderId": "folder-1",
            "folderSummary": {"fileCount": 2},
            "totalItems": 2,
            "modelUsage": {"dialogueCalls": 1, "visionCalls": 0, "totalCalls": 1, "imageGenerationCalls": 2},
        }
        run.metadata["suspendedDurationMs"] = 25
        run.raw_result = {"phase": "generating", "pendingTaskIds": ["folder-task-1", "folder-task-2"]}
        task_service = SimpleNamespace(
            list_tasks=mock.Mock(return_value={
                "items": [
                    {
                        "id": "folder-task-1",
                        "status": "success",
                        "data": [{"url": "https://example.test/folder-1.png", "width": 1024, "height": 1024}],
                    },
                    {
                        "id": "folder-task-2",
                        "status": "success",
                        "data": [{"url": "https://example.test/folder-2.png", "width": 1024, "height": 1024}],
                    },
                ],
                "missing_ids": [],
            }),
        )
        folder_service = SimpleNamespace(
            get_batch_plan=mock.Mock(return_value={
                "planId": "batch-1",
                "totalItems": 2,
                "completedItems": 2,
                "failedItems": 0,
                "items": [
                    {"taskId": "folder-task-1", "folderItemId": 11, "title": "图 1", "purpose": "展示"},
                    {"taskId": "folder-task-2", "folderItemId": 12, "title": "图 2", "purpose": "展示"},
                ],
            }),
        )
        with (
            mock.patch.object(cow_runtime.CowAgentRunRuntime, "_build_memory_manager", return_value=_MemoryManager()),
            mock.patch.object(cow_runtime, "image_task_service", task_service),
            mock.patch.object(cow_runtime, "professional_folder_asset_service", folder_service),
        ):
            runtime = cow_runtime.CowAgentRunRuntime(run)
            runtime._direct_model_text = mock.Mock()
            runtime.resume_pending_image_generation()

        self.assertEqual(AgentRunStatus.COMPLETED, run.status, run.error)
        self.assertEqual(2, len(run.result["images"]))
        self.assertEqual("https://example.test/folder-1.png", run.result["images"][0]["url"])
        self.assertNotIn("pendingFolderBatch", run.metadata)
        self.assertEqual(1, run.result["modelUsage"]["totalCalls"])
        runtime._direct_model_text.assert_not_called()

    def test_workspace_file_tools_write_edit_search_and_block_escape(self):
        run = self._run()
        with mock.patch.object(cow_runtime.CowAgentRunRuntime, "_build_memory_manager", return_value=_MemoryManager()):
            runtime = cow_runtime.CowAgentRunRuntime(run)
        tools = {tool.name: tool for tool in cow_tools.build_extended_cow_tools(runtime)}

        written = tools["write"].execute({"path": "notes/plan.md", "content": "first direction"})
        edited = tools["edit"].execute({
            "path": "notes/plan.md",
            "oldText": "first direction",
            "newText": "premium automotive direction",
        })
        listed = tools["ls"].execute({"path": "notes"})
        searched = tools["search_files"].execute({
            "pattern": "premium automotive",
            "target": "content",
            "path": ".",
            "file_glob": "*.md",
        })
        escaped = tools["write"].execute({"path": str(self.root / "outside.md"), "content": "denied"})

        self.assertEqual("success", written.status)
        self.assertEqual("success", edited.status)
        self.assertEqual("success", listed.status)
        self.assertEqual("success", searched.status)
        self.assertIn("premium automotive direction", (runtime.workspace / "notes" / "plan.md").read_text(encoding="utf-8"))
        self.assertEqual("error", escaped.status)
        self.assertFalse((self.root / "outside.md").exists())

    def test_scheduler_and_mcp_configuration_are_owner_scoped(self):
        first_run = self._run(owner="owner-a", conversation="conversation-a")
        second_run = self._run(owner="owner-b", conversation="conversation-b")
        with mock.patch.object(cow_runtime.CowAgentRunRuntime, "_build_memory_manager", return_value=_MemoryManager()):
            first = cow_runtime.CowAgentRunRuntime(first_run)
            second = cow_runtime.CowAgentRunRuntime(second_run)
        first_tools = {tool.name: tool for tool in cow_tools.build_extended_cow_tools(first)}
        second_tools = {tool.name: tool for tool in cow_tools.build_extended_cow_tools(second)}

        created = first_tools["scheduler"].execute({
            "action": "create",
            "name": "review plan",
            "message": "Review the current image plan",
            "schedule_type": "once",
            "schedule_value": "+1h",
        })
        first_list = first_tools["scheduler"].execute({"action": "list"})
        second_list = second_tools["scheduler"].execute({"action": "list"})
        configured = first_tools["mcp"].execute({
            "action": "configure",
            "server": "design-api",
            "transport": "streamable-http",
            "url": "https://example.test/mcp",
            "headers": {"Authorization": "Bearer test-secret"},
        })
        first_servers = first_tools["mcp"].execute({"action": "list_servers"})
        second_servers = second_tools["mcp"].execute({"action": "list_servers"})
        denied_stdio = first_tools["mcp"].execute({
            "action": "configure",
            "server": "local-command",
            "transport": "stdio",
            "command": "cmd.exe",
        })

        self.assertEqual("success", created.status)
        self.assertEqual("success", first_list.status)
        self.assertEqual("success", second_list.status)
        self.assertIn("review plan", str(first_list.result))
        self.assertNotIn("review plan", str(second_list.result))
        self.assertEqual("success", configured.status)
        self.assertEqual(["design-api"], [item["name"] for item in first_servers.result["servers"]])
        self.assertEqual([], second_servers.result["servers"])
        self.assertEqual("error", denied_stdio.status)

    def test_admin_env_tool_and_persistence_redact_secrets(self):
        run = self._run(owner="admin-user")
        run.metadata["identity"]["role"] = "admin"
        with mock.patch.object(cow_runtime.CowAgentRunRuntime, "_build_memory_manager", return_value=_MemoryManager()):
            runtime = cow_runtime.CowAgentRunRuntime(run)
        env_tool = {tool.name: tool for tool in cow_tools.build_extended_cow_tools(runtime)}["env_config"]
        with mock.patch.dict(os.environ, {}, clear=False):
            configured = env_tool.execute({"action": "set", "key": "BOCHA_API_KEY", "value": "very-secret-search-key"})
            fetched = env_tool.execute({"action": "get", "key": "BOCHA_API_KEY"})

        persistent = cow_runtime.CowAgentRunRuntime._persistent_message({
            "role": "assistant",
            "content": [{
                "type": "tool_use",
                "id": "secret-tool",
                "name": "env_config",
                "input": {"action": "set", "key": "BOCHA_API_KEY", "value": "very-secret-search-key"},
            }],
        })

        self.assertEqual("success", configured.status)
        self.assertEqual("success", fetched.status)
        self.assertNotIn("very-secret-search-key", str(fetched.result))
        self.assertEqual("[REDACTED]", persistent["content"][0]["input"]["value"])

    def test_cow_executor_runs_tool_loop_and_persists_complete_message_chain(self):
        run = self._run()
        with (
            mock.patch.object(cow_runtime.CowAgentRunRuntime, "_build_memory_manager", return_value=_MemoryManager()),
            mock.patch.object(cow_runtime, "knowledge_context_for_model", return_value={
                "sections": [{"title": "汽车视觉", "content": "车身比例和环境光需要统一。"}],
                "sources": [{"id": "cars", "title": "汽车商业视觉"}],
            }),
        ):
            runtime = cow_runtime.CowAgentRunRuntime(run)
            runtime.model = _ToolLoopModel()
            runtime._system_prompt = lambda _tools: "You are a professional commercial visual agent."
            runtime.add_long_term = mock.Mock()
            runtime.execute()

        self.assertEqual(AgentRunStatus.COMPLETED, run.status, run.error)
        self.assertEqual(1, run.tool_calls)
        self.assertIn("等待你确认执行", run.result["assistantMessage"])
        messages = self.memory.load_messages(owner_id="cow-user", conversation_id="conversation-1")
        cow_messages = [item["content"]["message"] for item in messages if item["messageType"] == "cow_message"]
        self.assertEqual(["user", "assistant", "user", "assistant"], [item["role"] for item in cow_messages])
        self.assertEqual("tool_use", cow_messages[1]["content"][0]["type"])
        self.assertEqual("tool_result", cow_messages[2]["content"][0]["type"])

    def test_persistent_message_omits_hidden_thinking(self):
        message = cow_runtime.CowAgentRunRuntime._persistent_message({
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "hidden reasoning"},
                {"type": "text", "text": "visible answer"},
            ],
            "_gemini_raw_parts": "hidden-signature",
        })

        self.assertEqual({"role": "assistant", "content": [{"type": "text", "text": "visible answer"}]}, message)

    def test_generate_image_tool_uses_raw_task_service(self):
        run = self._run()
        with mock.patch.object(cow_runtime.CowAgentRunRuntime, "_build_memory_manager", return_value=_MemoryManager()):
            runtime = cow_runtime.CowAgentRunRuntime(run)
        task_service = SimpleNamespace(
            submit_generation=mock.Mock(return_value={
                "id": "task-1",
                "status": "success",
                "data": [{"url": "https://example.test/car.png", "width": 1024, "height": 1024}],
            }),
        )
        with mock.patch.object(cow_runtime, "image_task_service", task_service):
            result = runtime.generate_images(
                {"prompt": "A premium automotive key visual", "mode": "generate", "count": 1},
                progress=lambda _message: None,
                cancelled=lambda: False,
            )

        self.assertEqual(1, result["count"])
        self.assertEqual("https://example.test/car.png", result["images"][0]["url"])
        self.assertEqual("professional", task_service.submit_generation.call_args.kwargs["prompt_engine_mode"])

    def test_agent_reads_image_skill_before_calling_raw_generation(self):
        run = self._run()
        task_service = SimpleNamespace(
            submit_generation=mock.Mock(return_value={
                "id": "task-from-skill",
                "status": "success",
                "data": [{"url": "https://example.test/skill-car.png", "width": 1024, "height": 1024}],
            }),
        )
        with (
            mock.patch.object(cow_runtime.CowAgentRunRuntime, "_build_memory_manager", return_value=_MemoryManager()),
            mock.patch.object(cow_runtime, "image_task_service", task_service),
            mock.patch.object(cow_runtime, "knowledge_context_for_model", return_value={"sections": [], "sources": []}),
        ):
            runtime = cow_runtime.CowAgentRunRuntime(run)
            runtime.model = _SkillImageToolLoopModel()
            runtime.add_long_term = mock.Mock()
            runtime.execute()

        self.assertEqual(AgentRunStatus.COMPLETED, run.status, run.error)
        self.assertEqual(2, run.tool_calls)
        self.assertEqual("https://example.test/skill-car.png", run.result["images"][0]["url"])
        task_service.submit_generation.assert_called_once()
        messages = self.memory.load_messages(owner_id="cow-user", conversation_id="conversation-1")
        tool_names = [
            block.get("name")
            for item in messages
            if item["messageType"] == "cow_message"
            for block in item["content"]["message"].get("content", [])
            if isinstance(block, dict) and block.get("type") == "tool_use"
        ]
        self.assertEqual(["read", "raw_generate_image"], tool_names)

    def test_run_restore_is_owner_scoped_and_drops_image_bodies(self):
        run = self._run()
        run.status = AgentRunStatus.COMPLETED
        run.raw_result = {"assistantMessage": "done", "images": [{"b64_json": "large-image-body"}]}
        cow_runtime._persist_run_state(run, status="completed")

        restored = cow_runtime.restore_cow_agent_run(run.run_id, {"id": "cow-user"})
        other_owner = cow_runtime.restore_cow_agent_run(run.run_id, {"id": "other-user"})

        self.assertIsNotNone(restored)
        self.assertEqual(AgentRunStatus.COMPLETED, restored.status)
        self.assertNotIn("b64_json", restored.raw_result["images"][0])
        self.assertIsNone(other_owner)

    def test_queued_run_restores_request_from_persistent_state(self):
        run = self._run()
        run.status = AgentRunStatus.PENDING
        run.request.update({
            "prompt": "Generate a professional car product scene",
            "size": "1024x1536",
            "count": 2,
        })
        cow_runtime._persist_run_state(run, status="pending")
        captured = {}

        def fake_execute(runtime_self):
            captured["request"] = dict(runtime_self.run.request)
            runtime_self.run.status = AgentRunStatus.COMPLETED
            runtime_self.run.raw_result = {"assistantMessage": "done", "images": []}
            runtime_self.run.result = runtime_self.run.raw_result
            cow_runtime._persist_run_state(runtime_self.run, status="completed")

        with mock.patch.object(cow_runtime.CowAgentRunRuntime, "execute", new=fake_execute):
            restored = cow_runtime.execute_queued_cow_agent_run(run.run_id, "cow-user")

        self.assertIsNotNone(restored)
        self.assertEqual(AgentRunStatus.COMPLETED, restored.status)
        self.assertEqual("Generate a professional car product scene", captured["request"]["prompt"])
        self.assertEqual("1024x1536", captured["request"]["size"])
        self.assertEqual(2, captured["request"]["count"])
        state = self.memory.load_run_state(run.run_id, owner_id="cow-user")
        self.assertEqual("completed", state["status"])

    def test_queued_run_normalizes_invalid_persisted_size(self):
        run = self._run()
        run.status = AgentRunStatus.PENDING
        run.request.update({
            "prompt": "Generate a professional car product scene",
            "size": "medium",
            "count": 2,
        })
        cow_runtime._persist_run_state(run, status="pending")
        captured = {}

        def fake_execute(runtime_self):
            captured["request"] = dict(runtime_self.run.request)
            runtime_self.run.status = AgentRunStatus.COMPLETED
            runtime_self.run.raw_result = {"assistantMessage": "done", "images": []}
            runtime_self.run.result = runtime_self.run.raw_result
            cow_runtime._persist_run_state(runtime_self.run, status="completed")

        with mock.patch.object(cow_runtime.CowAgentRunRuntime, "execute", new=fake_execute):
            restored = cow_runtime.execute_queued_cow_agent_run(run.run_id, "cow-user")

        self.assertIsNotNone(restored)
        self.assertEqual(AgentRunStatus.COMPLETED, restored.status)
        self.assertEqual("Generate a professional car product scene", captured["request"]["prompt"])
        self.assertEqual("1024x1024", captured["request"]["size"])

    def test_waiting_run_resumes_as_new_run_in_same_conversation(self):
        run = self._run()
        run.status = AgentRunStatus.WAITING
        run.request["folder_id"] = "folder-1"
        run.raw_result = {"phase": "awaiting_confirmation"}
        cow_runtime._persist_run_state(run, status="waiting_for_input")
        expected = {"agentRun": {"runId": "cow-next", "status": "pending"}}

        with mock.patch.object(cow_runtime, "start_cow_agent_run", return_value=expected) as start:
            result = cow_runtime.resume_cow_agent_run(
                run.run_id,
                "确认并执行",
                identity={"id": "cow-user"},
                images=[],
                base_url="https://raw.test",
            )

        self.assertEqual(expected, result)
        body = start.call_args.args[0]
        self.assertEqual("conversation-1", body["conversation_id"])
        self.assertEqual("folder-1", body["folder_id"])
        self.assertEqual("确认并执行", body["prompt"])
        self.assertEqual(run.run_id, body["resumed_from_run_id"])
        self.assertTrue(body["turn_id"].startswith("resume-"))

    def test_cow_resume_is_owner_scoped(self):
        run = self._run()
        run.status = AgentRunStatus.WAITING
        cow_runtime._persist_run_state(run, status="waiting_for_input")

        with self.assertRaises(PermissionError):
            cow_runtime.resume_cow_agent_run(
                run.run_id,
                "确认",
                identity={"id": "other-owner"},
            )

    def test_vendor_snapshot_does_not_contain_banned_302_route(self):
        provider_number = "".join(("3", "0", "2"))
        banned = (
            f"api.{provider_number}.ai",
            "google/v1/" + "models",
            "Gemini" + provider_number + "Provider",
            f"{provider_number}.ai",
        )
        matches = []
        for path in (cow_runtime.PROJECT_ROOT / "backend").rglob("*.py"):
            if path.resolve() == Path(__file__).resolve():
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if any(marker.lower() in text.lower() for marker in banned):
                matches.append(str(path))
        self.assertEqual([], matches)


if __name__ == "__main__":
    unittest.main()
