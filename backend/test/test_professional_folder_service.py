from __future__ import annotations

from io import BytesIO
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from services.ecommerce.ecommerce_agent_memory_service import Base
from services.ecommerce.professional_folder_service import (
    ProfessionalBatchPlanItemModel,
    ProfessionalFolderAssetService,
)


def _png(width: int, height: int, color: str) -> bytes:
    output = BytesIO()
    Image.new("RGB", (width, height), color).save(output, format="PNG")
    return output.getvalue()


class ProfessionalFolderAssetServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        database = Path(self.temp_dir.name) / "folder-assets.db"
        engine = create_engine(f"sqlite:///{database}")
        Base.metadata.create_all(engine)
        self.service = ProfessionalFolderAssetService()
        self.service.engine = engine
        self.service.Session = sessionmaker(bind=engine)
        self.addCleanup(engine.dispose)

        storage = mock.patch("services.ecommerce.professional_folder_service.image_storage_service")
        self.storage = storage.start()
        self.addCleanup(storage.stop)
        self.storage.save_task_asset.side_effect = lambda _payload, **kwargs: SimpleNamespace(
            rel=f"folder/{kwargs['task_id']}/{kwargs['asset_index']}.png",
            url=f"https://assets.test/{kwargs['task_id']}/{kwargs['asset_index']}.png",
        )
        qdrant = mock.patch(
            "services.ecommerce.professional_folder_service.professional_folder_index.enabled",
            return_value=False,
        )
        qdrant.start()
        self.addCleanup(qdrant.stop)

    def _create_folder(self):
        return self.service.create_folder(
            owner_id="owner-a",
            conversation_id="conversation-a",
            name="汽车素材",
            files=[
                (_png(1200, 1200, "white"), "cover.png", "image/png", "汽车素材/主图/cover.png"),
                (_png(1600, 900, "gray"), "road.png", "image/png", "汽车素材/场景/road.png"),
                (_png(800, 1400, "blue"), "detail.png", "image/png", "汽车素材/详情/detail.png"),
            ],
        )

    def test_folder_keeps_relative_paths_and_owner_isolation(self):
        folder = self._create_folder()

        self.assertEqual(3, folder["itemCount"])
        self.assertEqual(
            ["main", "scene", "detail"],
            [item["category"] for item in folder["items"]],
        )
        self.assertEqual("汽车素材/主图/cover.png", folder["items"][0]["relativeName"])
        self.assertIsNone(self.service.get_folder(folder["folderId"], owner_id="owner-b"))

    def test_query_selects_representative_item_and_persists_analysis(self):
        folder = self._create_folder()
        scene = next(item for item in folder["items"] if item["category"] == "scene")

        selected = self.service.search_folder_items(
            folder["folderId"],
            owner_id="owner-a",
            query="我要汽车场景车图",
            limit=1,
        )
        saved = self.service.save_item_analysis(
            folder["folderId"],
            scene["id"],
            owner_id="owner-a",
            analysis="夜间道路上的汽车，适合高级商业车图。",
            question="分析商业用途",
        )
        restored = self.service.get_folder(folder["folderId"], owner_id="owner-a")

        self.assertEqual("scene", selected["items"][0]["category"])
        self.assertEqual("keyword", selected["retrievalMode"])
        self.assertIn("夜间道路", saved["analysis"]["text"])
        restored_scene = next(item for item in restored["items"] if item["id"] == scene["id"])
        self.assertEqual("分析商业用途", restored_scene["analysis"]["question"])

    def test_batch_plan_prefers_exact_category_over_other_fallback(self):
        folder = self._create_folder()
        plan = self.service.create_batch_plan(
            owner_id="owner-a",
            conversation_id="conversation-a",
            run_id="run-a",
            folder_id=folder["folderId"],
            request={"prompt": "统一处理"},
            pages=[
                {"category": "other", "title": "其他", "prompt": "OTHER-PROMPT"},
                {"category": "scene", "title": "场景", "prompt": "SCENE-PROMPT"},
                {"category": "all", "title": "统一", "prompt": "ALL-PROMPT"},
            ],
        )
        session = self.service._session()
        try:
            rows = session.query(ProfessionalBatchPlanItemModel).filter(
                ProfessionalBatchPlanItemModel.plan_id == plan["planId"]
            ).order_by(ProfessionalBatchPlanItemModel.item_index).all()
            prompts = [row.prompt for row in rows]
        finally:
            session.close()

        self.assertEqual(["ALL-PROMPT", "SCENE-PROMPT", "ALL-PROMPT"], prompts)

    def test_batch_plan_can_limit_folder_item_count(self):
        folder = self._create_folder()
        plan = self.service.create_batch_plan(
            owner_id="owner-a",
            conversation_id="conversation-a",
            run_id="run-a",
            folder_id=folder["folderId"],
            request={"prompt": "只处理部分图片"},
            pages=[{"category": "all", "title": "统一", "prompt": "LIMITED-PROMPT"}],
            item_limit=2,
        )

        self.assertEqual(2, plan["totalItems"])
        self.assertEqual(2, len(plan["items"]))
        self.assertEqual([0, 1], [item["index"] for item in plan["items"]])
        self.assertEqual(3, plan["summary"]["folderFileCount"])
        self.assertEqual(2, plan["summary"]["selectedImageCount"])

    def test_batch_tasks_keep_low_priority_and_wake_the_agent_run(self):
        folder = self._create_folder()
        plan = self.service.create_batch_plan(
            owner_id="owner-a",
            conversation_id="conversation-a",
            run_id="agent-run-a",
            folder_id=folder["folderId"],
            request={"prompt": "统一处理"},
            pages=[{"category": "all", "title": "统一", "prompt": "ALL-PROMPT"}],
        )
        self.storage.get_bytes.return_value = _png(512, 512, "white")

        with mock.patch("services.image.image_task_service.image_task_service") as task_service:
            task_service.submit_edit.side_effect = [
                {"id": f"task-{index}", "status": "queued"}
                for index in range(3)
            ]
            task_service.list_tasks.return_value = {"items": []}
            self.service.execute_batch_plan(
                plan["planId"],
                owner_id="owner-a",
                identity={"id": "owner-a"},
                base_url="https://relay.test/v1",
                model="gpt-image-2",
                size="1024x1024",
                quality="auto",
                agent_run_id="agent-run-a",
            )

        self.assertEqual(3, task_service.submit_edit.call_count)
        for call in task_service.submit_edit.call_args_list:
            self.assertEqual("batch", call.kwargs["queue_priority"])
            self.assertEqual("agent-run-a", call.kwargs["agent_run_id"])


if __name__ == "__main__":
    unittest.main()
