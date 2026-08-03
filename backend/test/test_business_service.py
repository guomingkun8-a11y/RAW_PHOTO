import tempfile
import unittest
from pathlib import Path

from services.business_service import (
    LEGACY_DEFAULT_TEMPLATE_CONTENT,
    BusinessService,
    PromptTemplateModel,
)


class BusinessServiceTemplateTests(unittest.TestCase):
    def _service(self, tmp_dir: str) -> BusinessService:
        return BusinessService(f"sqlite:///{Path(tmp_dir) / 'business.db'}")

    def test_default_templates_are_detailed_ecommerce_prompts(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = self._service(tmp_dir)
            try:
                data = service.list_templates(
                    identity={"id": "admin", "role": "admin"},
                    enabled_only=False,
                )
                templates = {item["name"]: item for item in data["items"]}

                self.assertIn("电商主图", templates)
                self.assertIn("资深电商视觉策略师", templates["电商主图"]["content"])
                self.assertIn("平台图片审核", templates["白底图"]["content"])
                self.assertIn("商业摄影指导", templates["场景图"]["content"])
                self.assertIn("详情页转化专家", templates["详情页卖点图"]["content"])
                self.assertGreater(len(templates["电商主图"]["content"]), 250)
                self.assertIn("小红书种草封面图", templates)
                self.assertIn("小红书生活方式场景图", templates)
                self.assertIn("小红书测评对比图", templates)
                self.assertIn("小红书开箱手持图", templates)
                self.assertIn("小红书卖点信息卡", templates)
                self.assertEqual("小红书", templates["小红书种草封面图"]["category"])
                self.assertIn("小红书电商内容策略专家", templates["小红书种草封面图"]["content"])
                self.assertIn("真实博主分享感", templates["小红书开箱手持图"]["content"])
                self.assertGreater(len(templates["小红书卖点信息卡"]["content"]), 250)
                all_default_content = "\n".join(item["content"] for item in templates.values())
                for phrase in ("使用前后", "夸大功效", "虚假榜单", "虚假测评结论", "绝对化功效"):
                    self.assertNotIn(phrase, all_default_content)
            finally:
                if service.engine is not None:
                    service.engine.dispose()

    def test_legacy_system_templates_are_upgraded(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = self._service(tmp_dir)
            try:
                session = service._session()
                try:
                    row = (
                        session.query(PromptTemplateModel)
                        .filter(
                            PromptTemplateModel.owner_id == "local-admin",
                            PromptTemplateModel.created_by == "system",
                            PromptTemplateModel.name == "电商主图",
                            PromptTemplateModel.category == "main",
                        )
                        .one()
                    )
                    row.content = LEGACY_DEFAULT_TEMPLATE_CONTENT[("电商主图", "main")]
                    session.commit()
                finally:
                    session.close()

                service._ensure_default_templates()

                session = service._session()
                try:
                    row = (
                        session.query(PromptTemplateModel)
                        .filter(
                            PromptTemplateModel.owner_id == "local-admin",
                            PromptTemplateModel.created_by == "system",
                            PromptTemplateModel.name == "电商主图",
                            PromptTemplateModel.category == "main",
                        )
                        .one()
                    )
                    self.assertIn("高转化商品主图", row.content)
                    self.assertNotEqual(LEGACY_DEFAULT_TEMPLATE_CONTENT[("电商主图", "main")], row.content)
                finally:
                    session.close()
            finally:
                if service.engine is not None:
                    service.engine.dispose()

    def test_customized_system_templates_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = self._service(tmp_dir)
            custom_content = "这是我自己手工调整过的电商主图模板。"
            try:
                session = service._session()
                try:
                    row = (
                        session.query(PromptTemplateModel)
                        .filter(
                            PromptTemplateModel.owner_id == "local-admin",
                            PromptTemplateModel.created_by == "system",
                            PromptTemplateModel.name == "电商主图",
                            PromptTemplateModel.category == "main",
                        )
                        .one()
                    )
                    row.content = custom_content
                    session.commit()
                finally:
                    session.close()

                service._ensure_default_templates()

                session = service._session()
                try:
                    row = (
                        session.query(PromptTemplateModel)
                        .filter(
                            PromptTemplateModel.owner_id == "local-admin",
                            PromptTemplateModel.created_by == "system",
                            PromptTemplateModel.name == "电商主图",
                            PromptTemplateModel.category == "main",
                        )
                        .one()
                    )
                    self.assertEqual(custom_content, row.content)
                finally:
                    session.close()
            finally:
                if service.engine is not None:
                    service.engine.dispose()


if __name__ == "__main__":
    unittest.main()
