import unittest
from unittest.mock import patch

from services.ecommerce.ecommerce_profile_service import normalize_product_profile
from services.ecommerce.ecommerce_prompt_router import _explicit_white_background_requested, build_adaptive_image_prompt, build_professional_image_prompt, compose_professional_prompt
from services.ecommerce.ecommerce_scene_template_service import load_scene_templates, resolve_scene_template


class EcommercePromptRouterTests(unittest.TestCase):
    @patch("services.image.general_prompt_service.request_json_completion")
    @patch("services.image.general_prompt_service.is_prompt_analysis_enabled", return_value=True)
    def test_general_planner_builds_final_prompt_from_structured_fields(self, _enabled, request_json):
        request_json.return_value = {
            "subjectSummary": "银色概念轿车",
            "visualDirection": "克制而精确的汽车广告摄影",
            "sceneDescription": "雨后现代建筑入口",
            "background": "有层次的深灰建筑与湿润路面",
            "composition": "低机位三分之四前视角",
            "lighting": "傍晚柔和侧逆光",
            "needsClarification": False,
        }

        result = build_adaptive_image_prompt({
            "prompt": "给银色概念轿车做一张品牌主视觉",
            "size": "1536x1024",
            "images": [],
        })

        self.assertFalse(result["needsClarification"])
        self.assertIn("给银色概念轿车做一张品牌主视觉", result["finalPrompt"])
        self.assertIn("雨后现代建筑入口", result["finalPrompt"])
        self.assertIn("有层次的深灰建筑与湿润路面", result["finalPrompt"])
        self.assertIn("低机位三分之四前视角", result["finalPrompt"])
        self.assertIn("1536x1024", result["finalPrompt"])
        self.assertTrue(any("补齐" in item for item in result["warnings"]))

    @patch("services.image.general_prompt_service.request_json_completion", return_value={})
    @patch("services.image.general_prompt_service.is_prompt_analysis_enabled", return_value=True)
    def test_casual_general_message_requests_clarification_instead_of_generation(self, _enabled, _request_json):
        result = build_adaptive_image_prompt({"prompt": "你好", "images": []})

        self.assertTrue(result["needsClarification"])
        self.assertEqual("", result["finalPrompt"])
        self.assertIn("想生成或修改什么画面", result["clarificationQuestion"])

    def test_white_background_detection_respects_negation(self):
        self.assertTrue(_explicit_white_background_requested("做一张标准白底商品图"))
        self.assertTrue(_explicit_white_background_requested("pure white packshot"))
        self.assertFalse(_explicit_white_background_requested("不要白底，要真实生活场景"))
        self.assertFalse(_explicit_white_background_requested("禁止纯白背景和 packshot"))
        self.assertFalse(_explicit_white_background_requested("no white background"))

    def test_scene_templates_are_complete_and_keyword_routed(self):
        self.assertEqual(8, len(load_scene_templates()))
        self.assertEqual(
            "white_background",
            resolve_scene_template("auto", prompt="生成一张干净的白底商品主图")["id"],
        )
        self.assertNotEqual(
            "white_background",
            resolve_scene_template("auto", prompt="生成一张高级商品主图")["id"],
        )
        self.assertEqual(
            "material_macro",
            resolve_scene_template("auto", prompt="突出面料纹理的微距细节")["id"],
        )
        self.assertEqual(
            "taobao_text_main",
            resolve_scene_template(
                "auto",
                prompt="生成有文字排版的车图，放在淘宝页面上面",
                recommended_scene_type="poster_banner",
            )["id"],
        )

    def test_explicit_scene_overrides_model_recommendation(self):
        scene = resolve_scene_template(
            "magazine_editorial",
            prompt="白底图",
            recommended_scene_type="white_background",
        )

        self.assertEqual("magazine_editorial", scene["id"])

    def test_product_profile_normalizes_skill_style_selling_points(self):
        profile = normalize_product_profile({
            "product_name": "黑色连衣裙",
            "materials": "真丝, 金属扣",
            "selling_points": [
                {"title": "交叉领口", "description": "保持绑带结构"},
                {"title": "荷叶边", "description": "保持下摆层次"},
            ],
            "visible_text_logo": "RAW",
        })

        self.assertEqual("黑色连衣裙", profile["productName"])
        self.assertEqual(["真丝", "金属扣"], profile["materials"])
        self.assertIn("交叉领口：保持绑带结构", profile["sellingPoints"])
        self.assertIn("可见文字与 Logo：RAW", profile["mustPreserve"])

    def test_prompt_composer_keeps_user_goal_and_reference_identity(self):
        scene = resolve_scene_template("luxury_atmosphere")
        final_prompt, negative_prompt = compose_professional_prompt(
            user_prompt="把这瓶香水做成高级广告图",
            product_profile=normalize_product_profile({
                "productName": "玻璃香水瓶",
                "colors": ["琥珀色"],
                "mustPreserve": ["瓶身比例", "正面标签"],
            }),
            scene_template=scene,
            creative_plan={"visualDirection": "安静的夜间香氛氛围"},
            has_reference=True,
            preserve_subject=True,
        )

        self.assertIn("把这瓶香水做成高级广告图", final_prompt)
        self.assertIn("高端氛围主视觉", final_prompt)
        self.assertIn("参考图保真", final_prompt)
        self.assertIn("瓶身比例", final_prompt)
        self.assertIn("商品结构或包装变形", negative_prompt)

    def test_taobao_text_prompt_contains_dynamic_layout_strategy(self):
        final_prompt, negative_prompt = compose_professional_prompt(
            user_prompt="生成有文字排版的车图，放在淘宝页面上面",
            product_profile=normalize_product_profile({
                "productName": "橙香饭店油污净",
                "category": "清洁用品",
                "visibleTextLogo": "橙香饭店油污净",
                "sellingPoints": ["强力去油", "清新橙香"],
            }),
            scene_template=resolve_scene_template(
                "auto",
                prompt="生成有文字排版的车图，放在淘宝页面上面",
                recommended_scene_type="poster_banner",
            ),
            creative_plan={},
            has_reference=True,
            preserve_subject=True,
            output_size="1024x1024",
            needs_typography=True,
        )

        self.assertIn("电商文字排版策略", final_prompt)
        self.assertIn("专业 Prompt 引擎：", final_prompt)
        self.assertIn("主标题：橙香饭店油污净", final_prompt)
        self.assertIn("卖点标签：强力去油、清新橙香", final_prompt)
        self.assertIn("自主选择版式", final_prompt)
        self.assertIn("文字、商品、Logo 和包装关键信息之间保留清晰净空", final_prompt)
        self.assertIn("验收标准", final_prompt)
        self.assertIn("投放环境：淘宝/天猫", final_prompt)
        self.assertIn("严格保持 1:1 方形比例", final_prompt)
        self.assertIn("不得生成其他比例后再裁切", final_prompt)
        self.assertIn("背景策略", final_prompt)
        self.assertIn("背景是创意变量", final_prompt)
        self.assertIn("不要默认使用纯白空背景", final_prompt)
        self.assertIn("文字所在区域要有稳定高对比", final_prompt)
        self.assertIn("审美增强", final_prompt)
        self.assertIn("高点击商业广告摄影", final_prompt)
        self.assertIn("百分比承诺", negative_prompt)
        self.assertIn("纯白空背景、白底商品图", negative_prompt)
        self.assertIn("背景为纯白空底或白底商品棚拍均视为不合格", final_prompt)

    def test_taobao_text_prompt_defaults_to_simplified_chinese_copy(self):
        final_prompt, _ = compose_professional_prompt(
            user_prompt="生成有文字排版的车图，放在淘宝页面上面",
            product_profile=normalize_product_profile({
                "productName": "橙香饭店油污净",
                "category": "清洁用品",
                "visibleTextLogo": "橙香饭店油污净",
                "sellingPoints": ["强力去油", "清新橙香"],
            }),
            scene_template=resolve_scene_template(
                "auto",
                prompt="生成有文字排版的车图，放在淘宝页面上面",
                recommended_scene_type="poster_banner",
            ),
            creative_plan={
                "typography": {
                    "headline": "Orange Kitchen Degreaser",
                    "subheadline": "Safe for Pets",
                    "sellingPointLabels": ["Effective Cleaning"],
                    "badge": "English Copy",
                },
            },
            has_reference=True,
            preserve_subject=True,
            output_size="1024x1024",
            needs_typography=True,
        )

        self.assertIn("排版语言：简体中文", final_prompt)
        self.assertIn("主标题：橙香饭店油污净", final_prompt)
        self.assertIn("副标题：", final_prompt)
        self.assertIn("卖点标签：强力去油、清新橙香", final_prompt)
        self.assertNotIn("Orange Kitchen Degreaser", final_prompt)
        self.assertNotIn("Safe for Pets", final_prompt)
        self.assertNotIn("English Copy", final_prompt)

    def test_taobao_text_prompt_allows_explicit_english_copy(self):
        final_prompt, _ = compose_professional_prompt(
            user_prompt="生成英文排版的车图，标题写 Safe for Pets",
            product_profile=normalize_product_profile({
                "productName": "橙香饭店油污净",
                "category": "清洁用品",
                "visibleTextLogo": "橙香饭店油污净",
            }),
            scene_template=resolve_scene_template(
                "auto",
                prompt="生成英文排版的车图，标题写 Safe for Pets",
                recommended_scene_type="poster_banner",
            ),
            creative_plan={
                "typography": {
                    "headline": "Safe for Pets",
                    "subheadline": "Effective Cleaning",
                    "sellingPointLabels": ["Fast Action"],
                    "badge": "English Copy",
                },
            },
            has_reference=True,
            preserve_subject=True,
            output_size="1024x1024",
            needs_typography=True,
        )

        self.assertIn("排版语言：英文", final_prompt)
        self.assertIn("Safe for Pets", final_prompt)
        self.assertIn("Effective Cleaning", final_prompt)

    def test_explicit_white_background_keeps_white_background_strategy(self):
        final_prompt, _ = compose_professional_prompt(
            user_prompt="生成标准白底商品图",
            product_profile=normalize_product_profile({"productName": "测试商品"}),
            scene_template=resolve_scene_template("white_background"),
            creative_plan={},
            needs_typography=False,
        )

        self.assertIn("用户已明确要求白底/纯白/商品目录式输出", final_prompt)
        self.assertIn("真实接触阴影", final_prompt)

    def test_manual_white_background_scene_is_treated_as_explicit_direction(self):
        final_prompt, negative_prompt = compose_professional_prompt(
            user_prompt="生成标准商品图",
            product_profile=normalize_product_profile({"productName": "测试商品"}),
            scene_template=resolve_scene_template("white_background"),
            creative_plan={},
            needs_typography=False,
        )

        self.assertIn("用户已明确要求白底/纯白/商品目录式输出", final_prompt)
        self.assertNotIn("纯白空背景、白底商品图", negative_prompt)

    def test_analyzer_white_background_plan_does_not_override_car_image_background(self):
        final_prompt, negative_prompt = compose_professional_prompt(
            user_prompt="生成有文字排版的车图，放在淘宝页面上面",
            product_profile=normalize_product_profile({"productName": "宠物航空箱清洁喷雾"}),
            scene_template=resolve_scene_template("taobao_text_main"),
            creative_plan={"sceneDescription": "白色背景上展示商品，左侧放文字"},
            needs_typography=True,
        )

        self.assertNotIn("用户已明确要求白底/纯白/商品目录式输出", final_prompt)
        self.assertIn("不要默认使用纯白空背景", final_prompt)
        self.assertIn("不要沿用分析中的白色背景建议", final_prompt)
        self.assertNotIn("环境与道具：白色背景上展示商品，左侧放文字", final_prompt)
        self.assertIn("纯白空背景、白底商品图", negative_prompt)

    def test_typography_keeps_model_composition_instead_of_forcing_layout(self):
        final_prompt, _ = compose_professional_prompt(
            user_prompt="做淘宝文字主图",
            product_profile=normalize_product_profile({"productName": "测试商品"}),
            scene_template=resolve_scene_template("taobao_text_main"),
            creative_plan={"composition": "商品居中并占满画面"},
            needs_typography=True,
        )

        self.assertIn("构图：商品居中并占满画面", final_prompt)
        self.assertNotIn("强制非居中左右双区构图", final_prompt)

    def test_car_image_term_is_not_treated_as_automotive_category(self):
        final_prompt, _ = compose_professional_prompt(
            user_prompt="给宠物航空箱清洁喷雾做一张电商车图",
            product_profile=normalize_product_profile({
                "productName": "宠物航空箱清洁喷雾",
                "category": "清洁用品",
            }),
            scene_template=resolve_scene_template("taobao_text_main"),
            creative_plan={"conceptTitle": "清爽出行", "visualHook": "柔和弧形光带串联文案与商品"},
            needs_typography=True,
        )

        self.assertIn("创意概念：清爽出行", final_prompt)
        self.assertIn("视觉记忆点：柔和弧形光带串联文案与商品", final_prompt)
        self.assertIn("使用可信清洁场景", final_prompt)
        self.assertNotIn("保持车身比例", final_prompt)

    def test_automotive_product_gets_category_specific_art_direction(self):
        final_prompt, _ = compose_professional_prompt(
            user_prompt="给这辆银色轿车做一张品牌广告图",
            product_profile=normalize_product_profile({
                "productName": "银色轿车",
                "category": "汽车",
                "colors": ["银色"],
            }),
            scene_template=resolve_scene_template("luxury_atmosphere"),
            creative_plan={
                "conceptTitle": "城市流光",
                "visualHook": "建筑折线高光沿车身肩线延伸",
                "audienceMoment": "城市通勤人群第一次看到品牌主视觉",
            },
        )

        self.assertIn("创意概念：城市流光", final_prompt)
        self.assertIn("建筑折线高光沿车身肩线延伸", final_prompt)
        self.assertIn("保持车身比例、漆面颜色、轮毂和灯组结构", final_prompt)
        self.assertIn("轮胎接地、透视和漆面反射真实", final_prompt)

    @patch("services.ecommerce.ecommerce_prompt_router.request_json_completion")
    @patch("services.ecommerce.ecommerce_prompt_router.is_prompt_analysis_enabled", return_value=True)
    def test_professional_engine_builds_deterministic_result(self, _enabled, request_json):
        request_json.return_value = {
            "productProfile": {
                "productName": "银色无线耳机",
                "category": "电子",
                "colors": ["银色"],
                "materials": ["金属", "磨砂塑料"],
                "mustPreserve": ["耳机柄长度", "充电盒轮廓"],
                "confidence": 0.92,
            },
            "recommendedSceneType": "lifestyle",
            "creativePlan": {
                "visualDirection": "清晨桌面上的高端科技生活方式",
                "lighting": "窗边柔和侧光",
            },
            "warnings": [],
        }

        result = build_professional_image_prompt({
            "prompt": "做一张办公桌上的使用场景图",
            "scene_type": "auto",
            "platform": "小红书",
            "images": [],
        })

        self.assertEqual("lifestyle", result["sceneType"])
        self.assertEqual("银色无线耳机", result["productProfile"]["productName"])
        self.assertIn("清晨桌面上的高端科技生活方式", result["finalPrompt"])
        self.assertIn("投放环境：小红书", result["finalPrompt"])
        request_json.assert_called_once()

    @patch("services.ecommerce.ecommerce_prompt_router.request_json_completion")
    @patch("services.ecommerce.ecommerce_prompt_router.is_prompt_analysis_enabled", return_value=True)
    def test_known_product_is_not_blocked_by_missing_platform_or_style(self, _enabled, request_json):
        request_json.return_value = {
            "productProfile": {"productName": "保温杯", "category": "家居", "confidence": 0.9},
            "recommendedSceneType": "lifestyle",
            "creativePlan": {
                "visualDirection": "清晨通勤桌面的生活方式摄影",
                "background": "带窗光层次的办公桌面",
            },
            "needsClarification": True,
            "clarificationQuestion": "目标平台和背景风格是什么？",
        }

        result = build_professional_image_prompt({
            "prompt": "给保温杯做一张商品图，其余你来决定",
            "images": [],
        })

        self.assertFalse(result["needsClarification"])
        self.assertEqual("", result["clarificationQuestion"])
        self.assertIn("保温杯", result["finalPrompt"])
        self.assertIn("清晨通勤桌面", result["finalPrompt"])

    @patch("services.ecommerce.ecommerce_prompt_router.request_json_completion")
    @patch("services.ecommerce.ecommerce_prompt_router.is_prompt_analysis_enabled", return_value=True)
    def test_terse_followup_uses_persisted_product_profile(self, _enabled, request_json):
        request_json.return_value = {
            "productProfile": {},
            "recommendedSceneType": "lifestyle",
            "creativePlan": {"visualDirection": "夜间通勤氛围"},
            "needsClarification": False,
        }

        result = build_professional_image_prompt({
            "prompt": "你自己生成就行",
            "persistent_context": {
                "memory": {
                    "product_profile": {
                        "productName": "保温杯",
                        "category": "家居",
                        "sellingPoints": ["便携"],
                    },
                },
            },
            "images": [],
        })

        self.assertEqual("保温杯", result["productProfile"]["productName"])
        self.assertIn("便携", result["finalPrompt"])

    @patch("services.ecommerce.ecommerce_prompt_router.request_json_completion")
    @patch("services.ecommerce.ecommerce_prompt_router.is_prompt_analysis_enabled", return_value=True)
    def test_explicit_autonomy_can_create_safe_unbranded_concept_without_product(self, _enabled, request_json):
        request_json.return_value = {
            "productProfile": {},
            "recommendedSceneType": "white_background",
            "creativePlan": {},
            "needsClarification": True,
            "clarificationQuestion": "商品是什么？",
        }

        result = build_adaptive_image_prompt({
            "prompt": "你自己生成就行，自由发挥",
            "images": [],
        })

        self.assertFalse(result["needsClarification"])
        self.assertEqual("无品牌生活方式商品", result["productProfile"]["productName"])
        self.assertEqual("agent_concept", result["productProfile"]["profileSource"])
        self.assertEqual("lifestyle", result["sceneType"])
        self.assertIn("现代室内生活方式场景", result["finalPrompt"])
        self.assertIn("不渲染任何未经用户提供的文字", result["finalPrompt"])
        self.assertTrue(any("Agent 自拟" in warning for warning in result["warnings"]))

    @patch("services.ecommerce.ecommerce_prompt_router.request_json_completion")
    @patch("services.ecommerce.ecommerce_prompt_router.is_prompt_analysis_enabled", return_value=True)
    def test_adaptive_router_keeps_terse_followup_in_remembered_ecommerce_context(self, _enabled, request_json):
        request_json.return_value = {
            "productProfile": {},
            "recommendedSceneType": "lifestyle",
            "creativePlan": {"visualDirection": "通勤桌面商业摄影"},
            "needsClarification": False,
        }

        result = build_adaptive_image_prompt({
            "prompt": "背景换成夜景",
            "persistent_context": {
                "memory": {
                    "product_profile": {"productName": "保温杯", "category": "家居"},
                },
            },
            "images": [],
        })

        self.assertEqual("ecommerce", result["domain"])
        self.assertEqual("保温杯", result["productProfile"]["productName"])

    @patch("services.image.general_prompt_service.request_json_completion")
    @patch("services.image.general_prompt_service.is_prompt_analysis_enabled", return_value=True)
    def test_explicit_general_request_overrides_remembered_ecommerce_context(self, _enabled, request_json):
        request_json.return_value = {
            "subjectSummary": "森林里的儿童绘本场景",
            "visualDirection": "轻盈水彩插画",
            "finalPrompt": "森林里的儿童绘本水彩插画",
            "needsClarification": False,
        }

        result = build_adaptive_image_prompt({
            "prompt": "画一幅森林儿童插画",
            "persistent_context": {
                "memory": {
                    "product_profile": {"productName": "保温杯", "category": "家居"},
                },
            },
            "images": [],
        })

        self.assertEqual("general", result["domain"])
        self.assertNotIn("保温杯", result["finalPrompt"])

    @patch("services.ecommerce.ecommerce_prompt_router.request_json_completion")
    @patch("services.ecommerce.ecommerce_prompt_router.is_prompt_analysis_enabled", return_value=True)
    def test_product_attribute_edit_disables_taobao_layout_and_returns_mutation_policy(self, _enabled, request_json):
        request_json.return_value = {
            "productProfile": {
                "productName": "玻璃精华瓶",
                "colors": ["透明"],
                "shapeStructure": "圆柱瓶身",
                "mustPreserve": ["保持圆柱瓶身", "保持正面 Logo"],
                "confidence": 0.95,
            },
            "editIntent": "product_attribute_edit",
            "subjectMutationPolicy": "mutate_requested_attributes",
            "changedAttributes": ["packaging", "shape"],
            "recommendedSceneType": "taobao_text_main",
            "needsTypography": False,
            "creativePlan": {"visualDirection": "简洁棚拍"},
            "warnings": [],
        }

        result = build_professional_image_prompt({
            "prompt": "换个商品的样式，把圆瓶改成方瓶包装",
            "scene_type": "auto",
            "preserve_subject": True,
            "images": [{"name": "product.png", "dataUrl": "data:image/png;base64,AA=="}],
        })

        self.assertEqual("product_attribute_edit", result["editIntent"])
        self.assertEqual("mutate_requested_attributes", result["subjectMutationPolicy"])
        self.assertEqual(["packaging", "shape"], result["changedAttributes"])
        self.assertFalse(result["needsTypography"])
        self.assertEqual({}, result["typography"])
        self.assertNotEqual("taobao_text_main", result["sceneType"])
        self.assertIn("允许修改用户明确指定的商品属性", result["finalPrompt"])
        self.assertIn("不要把任务退化成只更换背景", result["negativePrompt"])
        self.assertNotIn("淘宝文字主图版式", result["finalPrompt"])
        self.assertNotIn("保持圆柱瓶身", result["finalPrompt"])

    @patch("services.ecommerce.ecommerce_prompt_router.prompt_analysis_model", return_value="gpt-4o")
    @patch("services.ecommerce.ecommerce_prompt_router.request_json_completion")
    @patch("services.ecommerce.ecommerce_prompt_router.is_prompt_analysis_enabled", return_value=True)
    def test_agent_request_does_not_use_image_model_for_prompt_analysis(
        self,
        _enabled,
        request_json,
        analysis_model,
    ):
        request_json.return_value = {
            "productProfile": {"productName": "测试商品"},
            "recommendedSceneType": "white_background",
            "creativePlan": {},
            "warnings": [],
        }

        result = build_professional_image_prompt({
            "prompt": "生成商品主图",
            "model": "gpt-image-2",
            "planner_model": "",
            "images": [],
        })

        analysis_model.assert_called_once_with("")
        self.assertEqual("gpt-4o", request_json.call_args.kwargs["model"])
        self.assertEqual("gpt-4o", result["model"])


if __name__ == "__main__":
    unittest.main()
