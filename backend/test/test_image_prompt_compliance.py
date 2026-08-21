import unittest

from services.image.image_prompt_compliance import (
    IMAGE_PROMPT_DIRECTOR_MARKER,
    IMAGE_PROMPT_GENERAL_MARKER,
    IMAGE_PROMPT_TEXT_LANGUAGE_MARKER,
    IMAGE_PROMPT_STANDARD_MARKER,
    IMAGE_PROMPT_TYPOGRAPHY_MARKER,
    REFERENCE_EDIT_AMBIGUOUS,
    REFERENCE_EDIT_PRODUCT,
    REFERENCE_EDIT_REPLACE,
    REFERENCE_EDIT_SCENE,
    REFERENCE_EDIT_VISUAL_STYLE,
    SUBJECT_POLICY_MUTATE,
    SUBJECT_POLICY_REPLACE,
    classify_reference_edit_intent,
    ensure_image_prompt_engineered,
    has_explicit_english_typography_request,
    has_typography_request,
    is_ecommerce_request,
    sanitize_image_prompt,
)


class ImagePromptComplianceTests(unittest.TestCase):
    def test_general_requests_do_not_receive_ecommerce_director_prompt(self):
        for prompt in (
            "生成我的世界游戏图片",
            "画一张宇宙飞船科幻插画",
            "制作电影感人物海报",
            "生成现代建筑效果图",
            "画一只猫的水彩画",
        ):
            engineered = ensure_image_prompt_engineered(prompt)
            self.assertIn(IMAGE_PROMPT_GENERAL_MARKER, engineered)
            self.assertNotIn(IMAGE_PROMPT_DIRECTOR_MARKER, engineered)

    def test_domain_classifier_requires_commercial_context(self):
        self.assertFalse(is_ecommerce_request("生成一张宇宙飞船插画"))
        self.assertTrue(is_ecommerce_request("生成一张香水产品图"))
        self.assertTrue(is_ecommerce_request("生成淘宝主图"))
        self.assertTrue(is_ecommerce_request("做商品详情页", has_reference=True))

    def test_general_reference_edit_does_not_use_product_preservation_wrapper(self):
        prompt = sanitize_image_prompt(
            "把人物放到雨夜街头，保持人物姿态",
            has_reference=True,
            preserve_subject=True,
        )
        self.assertIn(IMAGE_PROMPT_GENERAL_MARKER, prompt)
        self.assertNotIn("Product subject preservation mode", prompt)

    def test_sanitize_adds_visual_director_layer(self):
        prompt = sanitize_image_prompt("生成一张香水电商主图")

        self.assertIn("生成一张香水电商主图", prompt)
        self.assertIn(IMAGE_PROMPT_DIRECTOR_MARKER, prompt)
        self.assertIn("商品定位", prompt)
        self.assertIn("排版自主", prompt)
        self.assertIn("本张创意方向", prompt)
        self.assertIn("画面结构约束", prompt)
        self.assertIn("合规约束", prompt)
        self.assertIn("负面约束", prompt)

    def test_batch_prompts_use_different_creative_directions(self):
        first = sanitize_image_prompt("生成产品图", image_count=3, image_index=0)
        second = sanitize_image_prompt("生成产品图", image_count=3, image_index=1)

        self.assertIn("第 1/3 张", first)
        self.assertIn("第 2/3 张", second)
        self.assertNotEqual(first, second)

    def test_standard_mode_keeps_basic_guards_without_professional_direction(self):
        prompt = sanitize_image_prompt(
            "把商品放在厨房台面",
            has_reference=True,
            preserve_subject=True,
            prompt_engine_mode="standard",
        )

        self.assertIn(IMAGE_PROMPT_STANDARD_MARKER, prompt)
        self.assertIn("参考图约束", prompt)
        self.assertIn("主体保真", prompt)
        self.assertIn("画面结构约束", prompt)
        self.assertIn("合规约束", prompt)
        self.assertNotIn(IMAGE_PROMPT_DIRECTOR_MARKER, prompt)
        self.assertNotIn("本张创意方向", prompt)
        self.assertNotIn("负面约束", prompt)
        self.assertNotIn(IMAGE_PROMPT_TYPOGRAPHY_MARKER, prompt)

    def test_standard_typography_request_adds_only_lightweight_layout_guard(self):
        prompt = sanitize_image_prompt(
            "参考上传商品，生成一张1:1淘宝车图，添加标题并排版",
            has_reference=True,
            preserve_subject=True,
            prompt_engine_mode="standard",
        )

        self.assertIn(IMAGE_PROMPT_STANDARD_MARKER, prompt)
        self.assertIn(IMAGE_PROMPT_TYPOGRAPHY_MARKER, prompt)
        self.assertIn("按用户当前提示生成文字排版", prompt)
        self.assertIn("排版语言默认使用简体中文", prompt)
        self.assertIn("不强制固定商品占比", prompt)
        self.assertIn("可自主提炼简短中性中文卖点文案", prompt)
        self.assertNotIn("商品占约 55% 到 62%", prompt)
        self.assertNotIn(IMAGE_PROMPT_DIRECTOR_MARKER, prompt)

    def test_absolute_percentage_claims_are_rewritten(self):
        prompt = sanitize_image_prompt("生成清洁喷雾主图，写上99%杀菌率和百分之百安全")
        user_prompt_section = prompt.split(IMAGE_PROMPT_DIRECTOR_MARKER, 1)[0]

        self.assertNotIn("99%", user_prompt_section)
        self.assertNotIn("百分之百", user_prompt_section)
        self.assertIn("高比例", user_prompt_section)
        self.assertIn("不要生成百分百、100%、99%", prompt)

    def test_english_typography_request_is_detected_only_when_explicit(self):
        self.assertTrue(has_explicit_english_typography_request("Please make the headline in English"))
        self.assertTrue(has_explicit_english_typography_request("英文排版，标题写 Safe for Pets"))
        self.assertFalse(has_explicit_english_typography_request("不要英文，保持中文排版"))
        self.assertFalse(has_explicit_english_typography_request("给我中文标题"))
        self.assertFalse(has_explicit_english_typography_request("请用英文包装上的原文保留，不要新增英文卖点"))
        self.assertFalse(has_explicit_english_typography_request("给 Apple 手机做一张带文字排版的主图"))

    def test_general_typography_guard_mentions_default_chinese_language(self):
        prompt = sanitize_image_prompt(
            "生成一张带文字的产品海报",
            has_reference=False,
            preserve_subject=False,
            prompt_engine_mode="general",
        )

        self.assertIn(IMAGE_PROMPT_TEXT_LANGUAGE_MARKER, prompt)
        self.assertIn("默认使用简体中文", prompt)

    def test_standard_typography_detection_respects_negative_requests(self):
        for source in (
            "做成真实生活方式场景，不要文字",
            "不要电商主图，使用自然抓拍构图",
            "不要主图排版，不添加任何额外文字",
            "无文字，产品放在真实厨房里",
        ):
            self.assertFalse(has_typography_request(source), source)
            prompt = sanitize_image_prompt(
                source,
                has_reference=True,
                preserve_subject=True,
                prompt_engine_mode="standard",
            )
            self.assertNotIn(IMAGE_PROMPT_TYPOGRAPHY_MARKER, prompt)

        self.assertTrue(has_typography_request("添加标题：春日清洁，并进行文字排版"))

    def test_existing_standard_prompt_can_add_typography_guard(self):
        legacy_standard_prompt = (
            "参考上传商品，添加标题文字\n\n"
            f"{IMAGE_PROMPT_STANDARD_MARKER}按用户原始提示直接执行。"
        )
        twice = ensure_image_prompt_engineered(
            legacy_standard_prompt,
            prompt_engine_mode="standard",
        )

        self.assertIn(IMAGE_PROMPT_TYPOGRAPHY_MARKER, twice)
        self.assertEqual(1, twice.count(IMAGE_PROMPT_TYPOGRAPHY_MARKER))

    def test_standard_reference_intent_distinguishes_product_scene_style_and_replacement(self):
        self.assertEqual(REFERENCE_EDIT_PRODUCT, classify_reference_edit_intent("换个商品的样式，包装改成磨砂黑"))
        self.assertEqual(REFERENCE_EDIT_PRODUCT, classify_reference_edit_intent("换一个商品的瓶子的样子"))
        self.assertEqual(REFERENCE_EDIT_PRODUCT, classify_reference_edit_intent("帮我换一个产品样式"))
        self.assertEqual(REFERENCE_EDIT_PRODUCT, classify_reference_edit_intent("换一种瓶子"))
        self.assertEqual(REFERENCE_EDIT_SCENE, classify_reference_edit_intent("把商品放到高级浴室背景"))
        self.assertEqual(REFERENCE_EDIT_VISUAL_STYLE, classify_reference_edit_intent("改成日系摄影风格"))
        self.assertEqual(REFERENCE_EDIT_REPLACE, classify_reference_edit_intent("把原商品换成最新上传的商品"))
        self.assertEqual(REFERENCE_EDIT_AMBIGUOUS, classify_reference_edit_intent("整体重新设计一下"))

    def test_standard_product_edit_allows_requested_mutation_without_strict_preserve_guard(self):
        prompt = sanitize_image_prompt(
            "换个商品的样式，把瓶身改成磨砂黑",
            has_reference=True,
            preserve_subject=True,
            prompt_engine_mode="standard",
        )

        self.assertIn("允许严格按照用户原始提示修改商品", prompt)
        self.assertIn("不要把任务退化为只更换背景", prompt)
        self.assertNotIn("只按用户要求改变场景，不更换商品款式", prompt)
        self.assertNotIn("主体保真：", prompt)

    def test_standard_scene_edit_keeps_product_fidelity_guard(self):
        prompt = sanitize_image_prompt(
            "把商品放到浴室台面，使用柔和侧光",
            has_reference=True,
            preserve_subject=True,
            prompt_engine_mode="standard",
        )

        self.assertIn("按照用户要求修改背景、场景、构图、道具、机位或光线", prompt)
        self.assertIn("不得覆盖本轮明确修改要求", prompt)
        self.assertIn("主体保真：", prompt)

    def test_standard_prompt_is_not_upgraded_by_downstream_default(self):
        once = sanitize_image_prompt("普通商品图", prompt_engine_mode="standard")
        twice = ensure_image_prompt_engineered(once)

        self.assertEqual(once, twice)
        self.assertNotIn(IMAGE_PROMPT_DIRECTOR_MARKER, twice)

    def test_reference_prompt_preserves_subject_identity(self):
        prompt = sanitize_image_prompt(
            "把商品放到高级浴室场景",
            has_reference=True,
            preserve_subject=True,
        )

        self.assertIn("参考图约束", prompt)
        self.assertIn("保持商品外形", prompt)
        self.assertIn("主体保真", prompt)

    def test_professional_product_mutation_uses_dynamic_reference_guard(self):
        prompt = sanitize_image_prompt(
            "把圆形瓶身改成方形磨砂包装",
            has_reference=True,
            preserve_subject=False,
            subject_mutation_policy=SUBJECT_POLICY_MUTATE,
        )

        self.assertIn("允许修改用户明确指定的商品属性", prompt)
        self.assertIn("不能把商品修改要求退化为只换背景", prompt)
        self.assertNotIn("只升级场景、光影、构图和质感", prompt)

    def test_professional_product_replacement_does_not_inherit_old_product(self):
        prompt = sanitize_image_prompt(
            "把原商品换成最新上传的商品",
            has_reference=True,
            preserve_subject=False,
            subject_mutation_policy=SUBJECT_POLICY_REPLACE,
        )

        self.assertIn("最新上传的商品作为目标主体", prompt)
        self.assertIn("不继承旧商品", prompt)

    def test_ensure_does_not_duplicate_existing_engineering(self):
        once = ensure_image_prompt_engineered("生成高级商品图")
        twice = ensure_image_prompt_engineered(once)

        self.assertEqual(once, twice)
        self.assertEqual(1, twice.count(IMAGE_PROMPT_DIRECTOR_MARKER))

    def test_ensure_upgrades_existing_prompt_when_reference_is_added(self):
        without_reference = ensure_image_prompt_engineered("生成高级商品图")
        with_reference = ensure_image_prompt_engineered(
            without_reference,
            has_reference=True,
            preserve_subject=True,
        )

        self.assertIn("参考图约束", with_reference)
        self.assertIn("主体保真", with_reference)
        self.assertEqual(1, with_reference.count(IMAGE_PROMPT_DIRECTOR_MARKER))

    def test_empty_prompt_stays_empty_for_direct_api_validation(self):
        self.assertEqual("", ensure_image_prompt_engineered(""))


if __name__ == "__main__":
    unittest.main()
