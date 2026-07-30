from __future__ import annotations

import unittest

from services.image_prompt_compliance import sanitize_image_prompt, strip_high_risk_claims


class ImagePromptComplianceTests(unittest.TestCase):
    def test_strip_high_risk_claims_replaces_common_ecommerce_claims(self):
        text = "包装写杀菌率99.9%、消毒、杀灭细菌、抗病毒、灭活病毒、医用级、医疗级"

        cleaned = strip_high_risk_claims(text)

        for forbidden in ("杀菌率99.9", "消毒", "杀灭细菌", "抗病毒", "灭活病毒", "医用级", "医疗级"):
            self.assertNotIn(forbidden, cleaned)
        self.assertIn("清洁", cleaned)

    def test_sanitize_image_prompt_adds_compliance_guard_once(self):
        first = sanitize_image_prompt("生成一张厨房清洁产品图")
        second = sanitize_image_prompt(first)

        self.assertEqual(first, second)
        self.assertIn("合规约束", first)
        self.assertIn("不要拼图", first)

    def test_sanitize_image_prompt_adds_per_image_layout_guard(self):
        cleaned = sanitize_image_prompt("生成 4 张不同场景产品图", image_count=4, image_index=1)

        self.assertIn("第 2/4 张独立成品图", cleaned)
        self.assertIn("本次只生成这一张图", cleaned)
        self.assertIn("不要九宫格", cleaned)


if __name__ == "__main__":
    unittest.main()
