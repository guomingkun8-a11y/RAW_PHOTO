from __future__ import annotations

import json
import re
from math import gcd
from typing import Any

from fastapi import HTTPException

from services.ecommerce.ecommerce_profile_service import (
    clean_string_list,
    clean_text,
    normalize_product_profile,
    product_profile_summary,
)
from services.ecommerce.ecommerce_scene_template_service import category_guidance, resolve_scene_template
from services.ecommerce.prompt_analysis_service import (
    is_prompt_analysis_enabled,
    prompt_analysis_model,
    request_json_completion,
    validate_reference_images,
)
from services.image.image_prompt_compliance import (
    REFERENCE_EDIT_AMBIGUOUS,
    REFERENCE_EDIT_PRODUCT,
    REFERENCE_EDIT_REPLACE,
    REFERENCE_EDIT_SCENE,
    REFERENCE_EDIT_VISUAL_STYLE,
    classify_reference_edit_intent,
    has_explicit_english_typography_request,
    is_ecommerce_request,
)
from services.image.general_prompt_service import build_general_image_prompt


PROFESSIONAL_PROMPT_MAX_TOKENS = 3000
PROFESSIONAL_PROMPT_MARKER = "专业 Prompt 引擎："
TAOBAO_TEXT_SCENE_ID = "taobao_text_main"
EDIT_INTENT_GENERATE = "generate_new"
SUBJECT_POLICY_PRESERVE = "preserve"
SUBJECT_POLICY_MUTATE = "mutate_requested_attributes"
SUBJECT_POLICY_REPLACE = "replace"
EDIT_INTENTS = frozenset({
    REFERENCE_EDIT_SCENE,
    REFERENCE_EDIT_VISUAL_STYLE,
    REFERENCE_EDIT_PRODUCT,
    REFERENCE_EDIT_REPLACE,
    EDIT_INTENT_GENERATE,
    REFERENCE_EDIT_AMBIGUOUS,
})
SUBJECT_POLICIES = frozenset({SUBJECT_POLICY_PRESERVE, SUBJECT_POLICY_MUTATE, SUBJECT_POLICY_REPLACE})
ATTRIBUTE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "color": ("颜色", "色彩", "色调", "color"),
    "material": ("材质", "质感", "material"),
    "packaging": ("包装", "瓶型", "瓶身", "瓶盖", "罐体", "盒型", "袋型", "外壳", "packaging"),
    "shape": ("外观", "造型", "形状", "结构", "款式", "样式", "shape", "style"),
    "label": ("图案", "标签", "label", "pattern"),
}
GENERIC_NEGATIVE_PROMPT = (
    "商品结构或包装变形、颜色漂移、Logo 和可见文字被篡改、错误透视、错误比例、重复商品、"
    "乱码、错别字、水印、虚假认证、未经证实的功效文字、百分百、100%、99%、百分比承诺、"
    "医疗/消杀/抗菌/病毒相关宣传、主体被道具或特效遮挡、拼图、分屏、多面板、明显 AI 生成痕迹"
)
AESTHETIC_DIRECTION_PROMPT = (
    "把画面当作可投放的高点击商业广告摄影来完成，而不是合规说明图。"
    "必须给出一个清晰的视觉记忆点：有设计感的背景、可信的空间深度、细腻的材质表面、"
    "柔和但有方向的主光、真实接触阴影、干净的高光边缘和克制的道具层级。"
    "整体应有品牌大片质感，画面高级、鲜活、耐看，避免模板化、平铺、呆板和普通棚拍。"
)
TYPOGRAPHY_AESTHETIC_PROMPT = (
    "文字主图应先服务商品定位和购买决策，再决定信息密度。"
    "模型可以根据品类、包装、卖点、用户痛点和平台气质，自主判断何时用大标题吸引注意、"
    "何时用克制留白建立质感，以及采用左文右图、右文左图、上下结构、环绕标签或杂志式留白。"
)
CATEGORY_ART_DIRECTION = {
    "汽车": "保持车身比例、漆面颜色、轮毂和灯组结构；在可信道路、现代建筑、专业影棚或真实用车场景中选择一种，确保轮胎接地、透视和漆面反射真实。",
    "服装": "优先表达版型、垂坠、面料动态与穿着关系；环境和人物姿态服务服装，不遮挡关键结构与图案。",
    "美妆": "精确表现玻璃、液体、膏体、粉体或皮肤质感；道具克制，瓶身标签和包装结构必须可信。",
    "食品": "让纹理、温度、新鲜度和食用场景可信；配料与道具只能来自已知口味和商品信息。",
    "电子": "保持接口、按键、屏幕和装配关系；用真实使用环境和有方向的光线建立科技感，避免泛滥霓虹。",
    "家居": "保持尺寸、功能和空间透视；使用有生活感但克制的真实空间，不做无功能的样板间陈列。",
    "珠宝": "保持造型、镶嵌和尺度；用精确高光、触感表面与佩戴关系表达质感，避免廉价星芒特效。",
    "清洁": "使用可信清洁场景与中性可视变化，不虚构百分比、认证或夸张前后对比。",
}
OUTPUT_SIZE_RE = re.compile(r"^\s*(\d+)\s*x\s*(\d+)\s*$", re.IGNORECASE)
WHITE_BACKGROUND_NEGATION_RE = re.compile(
    r"(?:不要|不使用|不需要|禁止|避免|拒绝|去掉|取消|不能|别用)\s*(?:做成|使用|采用|生成|要)?\s*"
    r"(?:纯白空?背景|纯白背景|白色背景|白背景|白底(?:图|商品图|棚拍)?|catalog|packshot)"
    r"(?:\s*(?:、|和|或|以及|/)\s*(?:纯白空?背景|纯白背景|白色背景|白背景|白底(?:图|商品图|棚拍)?|catalog|packshot))*|"
    r"(?:no|without|avoid|exclude)\s+(?:a\s+)?(?:pure\s+)?white\s+background",
    re.IGNORECASE,
)
CJK_TEXT_RE = re.compile(r"[\u4e00-\u9fff]")
LATIN_TEXT_RE = re.compile(r"[A-Za-z]")


def _fallback_edit_intent(prompt: str, has_reference: bool) -> str:
    if not has_reference:
        return EDIT_INTENT_GENERATE
    return classify_reference_edit_intent(prompt)


def _normalize_edit_intent(value: object, *, prompt: str, has_reference: bool) -> str:
    if not has_reference:
        return EDIT_INTENT_GENERATE
    text = clean_text(value, limit=80).lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "scene": REFERENCE_EDIT_SCENE,
        "scene_edit": REFERENCE_EDIT_SCENE,
        "visual_style": REFERENCE_EDIT_VISUAL_STYLE,
        "visual_style_edit": REFERENCE_EDIT_VISUAL_STYLE,
        "product": REFERENCE_EDIT_PRODUCT,
        "product_attribute": REFERENCE_EDIT_PRODUCT,
        "product_attribute_edit": REFERENCE_EDIT_PRODUCT,
        "replace": REFERENCE_EDIT_REPLACE,
        "product_replace": REFERENCE_EDIT_REPLACE,
        "generate": EDIT_INTENT_GENERATE,
        "generate_new": EDIT_INTENT_GENERATE,
        "ambiguous": REFERENCE_EDIT_AMBIGUOUS,
    }
    normalized = aliases.get(text, text)
    return normalized if normalized in EDIT_INTENTS else _fallback_edit_intent(prompt, has_reference)


def _normalize_subject_policy(value: object, *, edit_intent: str) -> str:
    if edit_intent == REFERENCE_EDIT_PRODUCT:
        return SUBJECT_POLICY_MUTATE
    if edit_intent == REFERENCE_EDIT_REPLACE:
        return SUBJECT_POLICY_REPLACE
    if edit_intent in {REFERENCE_EDIT_SCENE, REFERENCE_EDIT_VISUAL_STYLE, REFERENCE_EDIT_AMBIGUOUS, EDIT_INTENT_GENERATE}:
        return SUBJECT_POLICY_PRESERVE
    text = clean_text(value, limit=80).lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "keep": SUBJECT_POLICY_PRESERVE,
        "preserve": SUBJECT_POLICY_PRESERVE,
        "mutate": SUBJECT_POLICY_MUTATE,
        "modify": SUBJECT_POLICY_MUTATE,
        "mutate_requested_attributes": SUBJECT_POLICY_MUTATE,
        "replace": SUBJECT_POLICY_REPLACE,
    }
    if aliases.get(text) in SUBJECT_POLICIES:
        return aliases[text]
    return SUBJECT_POLICY_PRESERVE


def _infer_changed_attributes(prompt: str) -> list[str]:
    compact = clean_text(prompt, limit=8000).lower()
    return [
        name
        for name, keywords in ATTRIBUTE_KEYWORDS.items()
        if any(keyword.lower() in compact for keyword in keywords)
    ]


def _normalize_changed_attributes(value: object, *, prompt: str, edit_intent: str) -> list[str]:
    allowed = set(ATTRIBUTE_KEYWORDS)
    aliases = {
        "colour": "color",
        "颜色": "color",
        "材质": "material",
        "包装": "packaging",
        "瓶型": "packaging",
        "外观": "shape",
        "形状": "shape",
        "样式": "shape",
        "图案": "label",
        "标签": "label",
    }
    result: list[str] = []
    for item in clean_string_list(value, limit=8, item_limit=50):
        key = aliases.get(item.lower().strip(), item.lower().strip())
        if key in allowed and key not in result:
            result.append(key)
    if edit_intent == REFERENCE_EDIT_PRODUCT and not result:
        result = _infer_changed_attributes(prompt)
    return result


def _as_bool(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "y", "是", "需要"}:
            return True
        if lowered in {"false", "0", "no", "n", "否", "不需要"}:
            return False
    return default


def _autonomy_requested(value: object) -> bool:
    text = re.sub(r"\s+", "", clean_text(value, limit=8000).lower())
    return any(
        marker in text
        for marker in (
            "你自己生成",
            "你自己做",
            "你来决定",
            "自由发挥",
            "全权交给你",
            "按你的专业判断",
            "授权自主创作",
            "自主创作",
            "不用再问",
            "不用问我",
        )
    )


def _explicit_general_creation_requested(value: object) -> bool:
    text = clean_text(value, limit=8000).lower()
    return any(
        marker in text
        for marker in (
            "儿童插画",
            "绘本插画",
            "水彩插画",
            "人物肖像",
            "角色设计",
            "游戏场景",
            "游戏原画",
            "建筑效果图",
            "室内效果图",
            "风景画",
            "概念艺术",
            "concept art",
            "illustration",
            "portrait",
        )
    )


def _reference_roles(value: object, *, image_count: int, edit_intent: str = "") -> list[dict[str, Any]]:
    roles: list[dict[str, Any]] = []
    for index, raw in enumerate(value if isinstance(value, list) else []):
        if not isinstance(raw, dict):
            continue
        try:
            image_index = max(0, min(image_count - 1, int(raw.get("index", index))))
        except (TypeError, ValueError):
            image_index = index
        role = clean_text(raw.get("role"), limit=60) or "target_product"
        if role not in {"target_product", "scene_reference", "composition_reference"}:
            role = "target_product"
        roles.append({"index": image_index, "role": role})
    if not roles and image_count:
        if edit_intent == REFERENCE_EDIT_REPLACE and image_count > 1:
            roles = [
                {"index": index, "role": "target_product" if index == image_count - 1 else "scene_reference"}
                for index in range(image_count)
            ]
        else:
            roles = [{"index": index, "role": "target_product"} for index in range(image_count)]
    return roles


def _clean_plan(value: object) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    typography_source = source.get("typography") or source.get("textLayout") or source.get("text_layout")
    typography = typography_source if isinstance(typography_source, dict) else {}
    return {
        "conceptTitle": clean_text(source.get("conceptTitle") or source.get("concept_title"), limit=120),
        "visualHook": clean_text(source.get("visualHook") or source.get("visual_hook"), limit=300),
        "audienceMoment": clean_text(source.get("audienceMoment") or source.get("audience_moment"), limit=300),
        "visualDirection": clean_text(source.get("visualDirection") or source.get("visual_direction"), limit=500),
        "sceneDescription": clean_text(source.get("sceneDescription") or source.get("scene_description"), limit=500),
        "composition": clean_text(source.get("composition"), limit=400),
        "lighting": clean_text(source.get("lighting"), limit=400),
        "camera": clean_text(source.get("camera"), limit=300),
        "materialDetail": clean_text(source.get("materialDetail") or source.get("material_detail"), limit=400),
        "colorPalette": clean_text(source.get("colorPalette") or source.get("color_palette"), limit=240),
        "textStrategy": clean_text(source.get("textStrategy") or source.get("text_strategy"), limit=300),
        "typography": {
            "textLanguage": clean_text(
                typography.get("textLanguage")
                or typography.get("text_language")
                or source.get("typographyLanguage")
                or source.get("typography_language"),
                limit=40,
            ),
            "headline": clean_text(typography.get("headline"), limit=120),
            "subheadline": clean_text(typography.get("subheadline"), limit=160),
            "sellingPointLabels": clean_string_list(
                typography.get("sellingPointLabels") or typography.get("selling_point_labels"),
                limit=5,
                item_limit=80,
            ),
            "badge": clean_text(typography.get("badge"), limit=60),
            "placement": clean_text(typography.get("placement"), limit=220),
            "hierarchy": clean_text(typography.get("hierarchy"), limit=240),
            "fontDirection": clean_text(typography.get("fontDirection") or typography.get("font_direction"), limit=180),
            "colorDirection": clean_text(typography.get("colorDirection") or typography.get("color_direction"), limit=180),
            "safeArea": clean_text(typography.get("safeArea") or typography.get("safe_area"), limit=180),
        },
        "negativePrompt": clean_text(source.get("negativePrompt") or source.get("negative_prompt"), limit=700),
    }


def infer_platform(prompt: str, platform: str = "general") -> str:
    explicit = clean_text(platform, limit=80).lower()
    if explicit and explicit not in {"general", "auto", "default"}:
        return clean_text(platform, limit=80)
    text = clean_text(prompt, limit=8000).lower()
    if any(keyword in text for keyword in ("淘宝", "天猫", "taobao", "tmall")):
        return "淘宝/天猫"
    if any(keyword in text for keyword in ("京东", "jd主图")):
        return "京东"
    if any(keyword in text for keyword in ("拼多多", "pdd")):
        return "拼多多"
    if any(keyword in text for keyword in ("小红书", "种草")):
        return "小红书"
    return "通用电商与品牌视觉"


def _is_taobao_text_request(prompt: str, platform: str) -> bool:
    text = clean_text(prompt, limit=8000).lower()
    return (
        "淘宝" in platform
        or "天猫" in platform
        or any(keyword in text for keyword in ("淘宝", "天猫", "淘宝主图", "车图", "文字排版", "卖点排版"))
    ) and any(keyword in text for keyword in ("文字", "排版", "标题", "卖点", "主图", "车图", "淘宝", "天猫"))


def _typography_text_language(prompt: str) -> str:
    return "英文" if has_explicit_english_typography_request(prompt) else "简体中文"


def _looks_latin_only_copy(value: object) -> bool:
    text = clean_text(value, limit=300)
    return bool(text and LATIN_TEXT_RE.search(text) and not CJK_TEXT_RE.search(text))


def _layout_copy(value: object, *, user_prompt: str, text_language: str, limit: int) -> str:
    text = clean_text(value, limit=limit)
    if not text or text_language == "英文" or not _looks_latin_only_copy(text):
        return text
    return text if text.lower() in clean_text(user_prompt, limit=8000).lower() else ""


def _layout_copy_list(value: object, *, user_prompt: str, text_language: str) -> list[str]:
    return [
        item
        for item in clean_string_list(value, limit=5, item_limit=80)
        if _layout_copy(item, user_prompt=user_prompt, text_language=text_language, limit=80)
    ]


def _typography_plan(
    *,
    product_profile: dict[str, Any],
    creative_plan: dict[str, Any],
    scene_template: dict[str, Any],
    platform: str,
    user_prompt: str,
    enabled: bool = True,
) -> dict[str, Any]:
    if not enabled:
        return {}
    typography = dict(creative_plan.get("typography") or {})
    text_language = _typography_text_language(user_prompt)
    product_name = clean_text(product_profile.get("productName"), limit=100)
    visible_text = clean_text(product_profile.get("visibleTextLogo"), limit=120)
    category = clean_text(product_profile.get("subcategory") or product_profile.get("category"), limit=80)
    product_labels = clean_string_list(product_profile.get("sellingPoints"), limit=3, item_limit=80)
    labels = _layout_copy_list(typography.get("sellingPointLabels"), user_prompt=user_prompt, text_language=text_language)[:3]
    if not labels:
        labels = _layout_copy_list(product_labels, user_prompt=user_prompt, text_language=text_language)[:3]
    if not typography.get("headline"):
        typography["headline"] = product_name or visible_text
    if not typography.get("subheadline"):
        typography["subheadline"] = category
    if text_language == "简体中文":
        typography["headline"] = _layout_copy(typography.get("headline"), user_prompt=user_prompt, text_language=text_language, limit=120)
        typography["subheadline"] = _layout_copy(typography.get("subheadline"), user_prompt=user_prompt, text_language=text_language, limit=160)
        typography["badge"] = _layout_copy(typography.get("badge"), user_prompt=user_prompt, text_language=text_language, limit=60)
        if not typography["headline"]:
            for candidate in (product_name, category, visible_text):
                typography["headline"] = _layout_copy(candidate, user_prompt=user_prompt, text_language=text_language, limit=120)
                if typography["headline"]:
                    break
    typography["sellingPointLabels"] = labels
    if not typography.get("placement"):
        typography["placement"] = (
            "由模型根据商品定位和投放目标自主选择版式：可以采用左文右图、右文左图、上下结构、"
            "居中主视觉加边侧标签、杂志式留白或局部角标，但必须保持商品完整、文字清晰且不遮挡包装关键信息"
        )
    if not typography.get("hierarchy"):
        typography["hierarchy"] = (
            "根据卖点强弱决定层级：需要快速抓注意时使用一个短主标题作为最大视觉锚点；"
            "需要高级感时减少文字密度、扩大留白；副标题和卖点保持短句、分组清晰，避免堆满画面"
        )
    if not typography.get("fontDirection"):
        typography["fontDirection"] = "现代无衬线中文字体，字重有层级，字距舒展，避免书法体、卡通体和细到不可读的字体"
    if not typography.get("colorDirection"):
        typography["colorDirection"] = "文字与背景保持高对比；根据产品包装色、背景材质和品牌气质选择深浅搭配，避免复杂渐变填字和影响阅读的花背景"
    if not typography.get("safeArea"):
        typography["safeArea"] = "文字、商品、Logo 和包装关键信息之间保留清晰净空，四周留出安全边距，确保缩略图仍可读"
    typography["textLanguage"] = text_language
    typography["platform"] = platform
    if text_language == "英文":
        typography["renderPolicy"] = "用户已明确要求英文排版；英文逐字准确；不新增价格、折扣、认证、Logo、参数、百分比或未提供的功效承诺"
    else:
        typography["renderPolicy"] = (
            "排版语言默认为简体中文；除用户逐字提供的英文原文、品牌名或必须保留的 Logo/包装文字外，"
            "不得自动生成英文标题、英文卖点、英文角标或英文徽章；"
            "可基于商品画像、包装可见信息、用户提示和中性卖点自主提炼短中文文案；"
            "不新增价格、折扣、认证、Logo、参数、销量、百分比或未提供的功效承诺"
        )
    return typography


def _format_typography(typography: dict[str, Any]) -> str:
    labels = "、".join(clean_string_list(typography.get("sellingPointLabels"), limit=3, item_limit=80)) or "无"
    entries = [
        f"排版语言：{clean_text(typography.get('textLanguage'), limit=40) or '简体中文'}",
        f"主标题：{clean_text(typography.get('headline'), limit=120) or '无'}",
        f"副标题：{clean_text(typography.get('subheadline'), limit=160) or '无'}",
        f"卖点标签：{labels}",
        f"角标：{clean_text(typography.get('badge'), limit=60) or '无'}",
        f"位置：{clean_text(typography.get('placement'), limit=220)}",
        f"层级：{clean_text(typography.get('hierarchy'), limit=240)}",
        f"字体：{clean_text(typography.get('fontDirection'), limit=180)}",
        f"颜色：{clean_text(typography.get('colorDirection'), limit=180)}",
        f"安全区：{clean_text(typography.get('safeArea'), limit=180)}",
        f"渲染规则：{clean_text(typography.get('renderPolicy'), limit=240)}",
    ]
    return "；".join(entry for entry in entries if entry)


def _explicit_white_background_requested(*values: object) -> bool:
    text = " ".join(clean_text(value, limit=4000).lower() for value in values if value)
    if not text:
        return False
    positive_text = WHITE_BACKGROUND_NEGATION_RE.sub(" ", text)
    keywords = (
        "白底",
        "白背景",
        "白色背景",
        "纯白",
        "纯白背景",
        "white background",
        "pure white",
        "catalog",
        "packshot",
    )
    return any(keyword in positive_text for keyword in keywords)


def _background_strategy(
    *,
    user_prompt: str,
    scene_template: dict[str, Any],
    creative_plan: dict[str, Any],
    needs_typography: bool,
) -> str:
    scene_id = clean_text(scene_template.get("id"), limit=100)
    if _explicit_white_background_requested(user_prompt) or scene_id == "white_background":
        return (
            "用户已明确要求白底/纯白/商品目录式输出；背景可使用白色或极浅中性色，"
            "但仍需保留真实接触阴影、商品完整轮廓和必要的文字安全区。"
        )
    if needs_typography:
        return (
            "背景是创意变量：除非用户原话明确要求白底或纯白，否则不要默认使用纯白空背景、白底商品图、catalog/packshot 式棚拍。"
            "必须为本张车图设计一个可见背景方案，根据商品品类、卖点和平台选择产品相关色、轻微材质台面、柔和光影、浅空间层次或克制道具；"
            "文字所在区域要有稳定高对比和足够净空，商品区域要有真实接触阴影和商业质感。"
        )
    return (
        "背景是 Agent 自主设计变量：除非用户明确要求白底或纯白，否则不要默认纯白空背景。"
        "根据商品气质、使用场景和投放目标选择具体背景色、材质、光线层次、空间深度和少量相关道具；"
        "背景必须服务商品而不喧宾夺主。"
    )


def _environment_description(
    *,
    user_prompt: str,
    scene_template: dict[str, Any],
    creative_plan: dict[str, Any],
    needs_typography: bool,
) -> str:
    scene_description = clean_text(creative_plan.get("sceneDescription"), limit=500)
    if (
        scene_description
        and not _explicit_white_background_requested(user_prompt)
        and _explicit_white_background_requested(scene_description)
    ):
        if needs_typography:
            return (
                "不要沿用分析中的白色背景建议；改为产品相关色或品牌色的商业背景，"
                "文字区域使用稳定高对比的细腻材质或色块关系，商品区域使用真实台面、柔和光影和浅空间层次。"
            )
        return (
            "不要沿用分析中的白色背景建议；改为与商品气质相关的有色或材质化商业背景，"
            "通过台面、光影、空间深度或少量克制道具提升商业摄影质感。"
        )
    return scene_description or clean_text(scene_template.get("setting"), limit=500)


def _aesthetic_direction(*, needs_typography: bool) -> str:
    if needs_typography:
        return f"{AESTHETIC_DIRECTION_PROMPT}{TYPOGRAPHY_AESTHETIC_PROMPT}"
    return AESTHETIC_DIRECTION_PROMPT


def _unique_parts(*values: object) -> list[str]:
    result: list[str] = []
    for value in values:
        for item in clean_string_list(value, limit=30, item_limit=700):
            if item and item not in result:
                result.append(item)
    return result


def _canvas_requirement(output_size: object) -> str:
    size = clean_text(output_size, limit=40).lower()
    matched = OUTPUT_SIZE_RE.match(size)
    if not matched:
        return ""
    width = max(1, int(matched.group(1)))
    height = max(1, int(matched.group(2)))
    divisor = gcd(width, height)
    ratio = f"{width // divisor}:{height // divisor}"
    shape = "方形" if width == height else "竖版" if height > width else "横版"
    return (
        f"用户选择的输出画布为 {width}x{height}，严格保持 {ratio} {shape}比例。"
        "标题、副标题、卖点、商品完整轮廓、道具和安全边距必须全部在该画布内完成布局；"
        "不得生成其他比例后再裁切，不得截断左右文案、商品泵头、瓶身、包装边缘或主体。"
    )


def _filtered_must_preserve(profile: dict[str, Any], changed_attributes: list[str], subject_policy: str) -> str:
    values = clean_string_list(profile.get("mustPreserve"), limit=10, item_limit=260)
    if subject_policy != SUBJECT_POLICY_MUTATE or not changed_attributes:
        return "；".join(values)
    keywords = tuple(
        keyword.lower()
        for attribute in changed_attributes
        for keyword in ATTRIBUTE_KEYWORDS.get(attribute, ())
    )
    kept = [value for value in values if not any(keyword in value.lower() for keyword in keywords)]
    return "；".join(kept)


def _subject_negative_guard(subject_policy: str, changed_attributes: list[str]) -> str:
    if subject_policy == SUBJECT_POLICY_REPLACE:
        return "不要把旧参考商品的包装、颜色、Logo、文字或结构错误继承到目标商品中；不要出现多个无关商品。"
    if subject_policy == SUBJECT_POLICY_MUTATE:
        changed = "、".join(changed_attributes) or "用户明确指定的商品属性"
        return f"只允许修改用户明确要求的商品属性（{changed}）；未指定的品牌、Logo、可见文字、品类和其他结构保持可信，不要把任务退化成只更换背景。"
    return "商品身份、外形、包装结构、颜色、材质、比例、Logo 和可见文字保持可信一致；创意只作用于场景、构图、光影和质感。"


def compose_professional_prompt(
    *,
    user_prompt: str,
    product_profile: dict[str, Any],
    scene_template: dict[str, Any],
    creative_plan: dict[str, Any],
    platform: str = "general",
    has_reference: bool = False,
    preserve_subject: bool = True,
    output_size: str = "",
    edit_intent: str = EDIT_INTENT_GENERATE,
    subject_mutation_policy: str = SUBJECT_POLICY_PRESERVE,
    changed_attributes: list[str] | None = None,
    reference_roles: list[dict[str, Any]] | None = None,
    needs_typography: bool = False,
) -> tuple[str, str]:
    subject = product_profile_summary(product_profile) or "严格依据参考图或用户描述中的商品主体"
    selling_points = "；".join(product_profile.get("sellingPoints") or [])
    changed_attributes = list(changed_attributes or [])
    reference_roles = list(reference_roles or [])
    must_preserve = _filtered_must_preserve(product_profile, changed_attributes, subject_mutation_policy)
    category_tip = category_guidance(scene_template, product_profile.get("category"))
    category_text = clean_text(product_profile.get("category"), limit=120)
    category_direction = next(
        (guidance for keyword, guidance in CATEGORY_ART_DIRECTION.items() if keyword in category_text),
        "",
    )
    platform_text = infer_platform(user_prompt, platform)
    typography = _typography_plan(
        product_profile=product_profile,
        creative_plan=creative_plan,
        scene_template=scene_template,
        platform=platform_text,
        user_prompt=user_prompt,
        enabled=needs_typography,
    )
    text_strategy = creative_plan.get("textStrategy") or "除用户明确要求外，不新增营销文字或标识"
    composition = creative_plan.get("composition") or clean_text(scene_template.get("composition"), limit=500)
    visual_direction = creative_plan.get("visualDirection") or "高级、克制、真实，商品为唯一视觉主角"
    background_strategy = _background_strategy(
        user_prompt=user_prompt,
        scene_template=scene_template,
        creative_plan=creative_plan,
        needs_typography=needs_typography,
    )
    environment_description = _environment_description(
        user_prompt=user_prompt,
        scene_template=scene_template,
        creative_plan=creative_plan,
        needs_typography=needs_typography,
    )
    aesthetic_direction = _aesthetic_direction(needs_typography=needs_typography)
    canvas_requirement = _canvas_requirement(output_size)
    if typography:
        visual_direction = (
            "可直接投放的电商文字主图设计稿，需要呈现清晰信息层级、商品卖点和高点击商业广告审美，"
            "不得退化为白底抠图或无文案场景图"
        )
        if not composition:
            composition = (
                "由模型自主选择最适合本商品的图文结构；可以左文右图、右文左图、上下结构、"
                "居中主视觉加侧边标签或杂志式留白。商品必须完整可辨，文字必须清晰可读，"
                "二者保留净空并共享同一套背景、光影和色彩系统。"
            )
        text_strategy = (
            f"电商文字排版策略：{_format_typography(typography)}。"
            "新增排版可根据商品定位自主布局，不覆盖商品、Logo 或包装关键信息；画面必须同时看见主标题层级和完整商品。"
        )

    sections: list[tuple[str, str]] = [
        ("专业 Prompt 引擎", "已完成商品理解与视觉规划；严格执行以下结构化方案，不得简化为普通商品摆拍"),
        ("用户目标", clean_text(user_prompt, limit=4000) or "基于参考商品生成高质量电商视觉"),
        ("本轮执行意图", edit_intent),
        ("商品修改策略", _subject_negative_guard(subject_mutation_policy, changed_attributes)),
        ("画布硬约束", canvas_requirement),
        ("商品主体", subject),
        ("核心卖点", selling_points),
        ("场景类型", f"{scene_template['name']}：{clean_text(scene_template.get('description'), limit=400)}"),
        ("创意概念", creative_plan.get("conceptTitle")),
        ("视觉记忆点", creative_plan.get("visualHook")),
        ("目标观看情境", creative_plan.get("audienceMoment")),
        ("视觉方向", visual_direction),
        ("品类导演规则", category_direction),
        ("审美增强", aesthetic_direction),
        ("背景策略", background_strategy),
        ("环境与道具", environment_description),
        ("构图", composition),
        ("光线", creative_plan.get("lighting") or clean_text(scene_template.get("lighting"), limit=500)),
        ("镜头", creative_plan.get("camera") or clean_text(scene_template.get("camera"), limit=400)),
        ("材质细节", creative_plan.get("materialDetail") or category_tip),
        ("色彩", creative_plan.get("colorPalette")),
        ("画面文字", text_strategy),
        ("成片标准", clean_text(scene_template.get("quality"), limit=500)),
        ("投放环境", platform_text),
    ]

    if has_reference:
        reference_rule = "以参考图中的商品为唯一身份来源，建立可核验的商品主体，不凭空混入其他商品"
        if subject_mutation_policy == SUBJECT_POLICY_MUTATE:
            mutable = "、".join(changed_attributes) or "用户明确指定的属性"
            reference_rule += f"；允许修改用户明确指定的商品属性（{mutable}），未指定属性尽量保持不变"
        elif subject_mutation_policy == SUBJECT_POLICY_REPLACE:
            targets = [str(int(item.get("index", 0)) + 1) for item in reference_roles if item.get("role") == "target_product"]
            target_hint = f"（参考图序号 {', '.join(targets)}）" if targets else ""
            reference_rule += f"；将用户指明或最新上传的商品{target_hint}作为目标主体，不继承旧商品属性"
        elif preserve_subject:
            reference_rule += "；保持商品外形、包装结构、颜色、材质、比例、Logo 与可见文字一致；创意只作用于场景、构图、光影和质感"
        sections.insert(3, ("参考图保真", reference_rule))
    if must_preserve:
        sections.insert(4, ("不可改动", must_preserve))

    negative_parts = _unique_parts(
        creative_plan.get("negativePrompt"),
        scene_template.get("negative"),
        GENERIC_NEGATIVE_PROMPT if subject_mutation_policy == SUBJECT_POLICY_PRESERVE else _subject_negative_guard(subject_mutation_policy, changed_attributes),
    )
    if scene_template.get("id") != "white_background" and not _explicit_white_background_requested(user_prompt):
        negative_parts = _unique_parts(
            negative_parts,
            "纯白空背景、白底商品图、白底棚拍、catalog packshot、没有背景设计、仅抠图贴在白色画布上、平光无层次、僵硬模板排版",
        )
    negative_prompt = "；".join(negative_parts)
    sections.append(("负面约束", negative_prompt))
    if typography:
        sections.append(("验收标准", "必须是带有明确设计背景和清晰文字层级的电商成品图；输出比例错误、内容被裁切、商品不完整、文字缺失、文字压住商品、Logo 或包装关键信息被遮挡、背景为纯白空底或白底商品棚拍均视为不合格并重新构图"))

    final_prompt = "\n".join(f"{label}：{value}" for label, value in sections if clean_text(value, limit=10000))
    return final_prompt.strip(), negative_prompt


def _analysis_request(
    *,
    prompt: str,
    product_context: dict[str, Any],
    requested_scene_type: str,
    platform: str,
    conversation_context: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    typography_language = _typography_text_language(prompt)
    return {
        "task": "理解电商商品、用户生图意图和视觉目标，为确定性 Prompt 引擎返回结构化创意计划。",
        "currentPrompt": prompt,
        "requestedSceneType": requested_scene_type,
        "platform": platform,
        "typographyLanguagePreference": typography_language,
        "productContext": product_context,
        "recentConversationContext": list(conversation_context or [])[-6:],
        "sceneTypeEnum": [
            "taobao_text_main",
            "white_background",
            "lifestyle",
            "material_macro",
            "poster_banner",
            "social_cover",
            "magazine_editorial",
            "luxury_atmosphere",
        ],
        "rules": [
            "只描述图片中真实可见或 productContext/currentPrompt 明确提供的信息。",
            "无法确认的材质、功能、文字或品牌必须留空，不得猜测，不得虚构功效和认证。",
            "多张参考图按同一商品的不同角度综合分析；记录商品身份和必须保持的结构。",
            "先判断 editIntent：用户说换背景/场景时只改场景，用户说改摄影或视觉风格时只改画面表现，用户明确说改包装/颜色/材质/形状时允许修改对应商品属性，用户明确替换商品时使用指定或最新商品；无法判断时返回 ambiguous，不要擅自套用淘宝模板。",
            "subjectMutationPolicy 必须与 editIntent 一致；changedAttributes 只填写用户明确要求修改的属性。",
            "referenceRoles 要区分 target_product、scene_reference 和 composition_reference；最新商品不能被旧商品画像覆盖。",
            "recommendedSceneType 必须来自 sceneTypeEnum；若用户指定了非 auto 场景，优先服从；只有用户明确需要淘宝/天猫主图、车图、标题或文字排版时才令 needsTypography=true。",
            "背景/空间是创意计划中的自主变量：除非 currentPrompt 明确要求白底、纯白、white background、catalog 或 packshot，不要把普通商品主图、淘宝文字主图或高级感图片规划成纯白空背景；应选择与商品、卖点和平台匹配的颜色、材质、光影、空间层次或克制道具。",
            "结合 recentConversationContext 理解短追问和沿用关系；currentPrompt 与历史冲突时以 currentPrompt 为准，不得把旧方案当成本轮硬约束。",
            "对于宽泛需求，像视觉总监一样自主决定默认平台、图片数量、场景、背景、镜头、光线、色彩、构图和视觉记忆点，不要把这些创意选择反问给用户。",
            "用户说‘你自己生成’‘你来决定’‘自由发挥’‘按你的专业判断’时，表示授权你自主补全所有非事实型视觉选择，不得因为缺少平台、风格、背景、镜头、构图或卖点表达而追问。",
            "如果用户明确授权自主创作且所有上下文都没有商品主体，可以提出一个无品牌、无参数、无功效承诺的通用生活方式商品视觉概念；这是 Agent 自拟方案，不得伪装成用户真实商品。",
            "只有以下情况才 needsClarification=true：没有参考图且无法从 currentPrompt、productContext、persistentContext 或 recentConversationContext 确定商品主体；参考图编辑会改变商品本体但修改范围相互冲突；用户明确要求渲染指定文字但所有上下文都没有可用原文。一次只追问最关键的一个问题。",
            "先为本轮建立一个具体创意概念和一个可见视觉记忆点，再扩展场景、构图、光线、镜头、材质与色彩；记忆点必须来自画面设计，不得虚构商品卖点。",
            "中文电商语境中的‘车图’通常指商品轮播图/主图创意，不等于汽车图片；只有商品信息或参考图明确是车辆时才按汽车品类导演。",
            "按商品品类采用真实导演逻辑：汽车重视车身几何、接地和漆面反射；服装重视版型垂坠；美妆重视容器与内容物材质；食品重视纹理温度和食用情境；电子重视结构与真实使用；家居重视尺度功能；珠宝重视精确高光。",
            "创意计划使用简洁中文，避免空泛的高级、完美、震撼等堆词。",
            "当 needsTypography=true 时，不要套固定左右分区模板；根据商品定位、包装视觉重心、卖点强弱和用户痛点自主规划 typography.placement、hierarchy、fontDirection、colorDirection 和 safeArea。",
            "当用户没有逐字提供文案但明确需要文字排版时，可以基于 productContext、referenceImages 中可见包装信息、productProfile.sellingPoints、currentPrompt 和专业电商常识提炼简短中文标题/副标题/卖点；表达必须中性可证，不得新增价格、折扣、销量、排名、认证、参数、医疗/消杀功效或百分比承诺。",
            "画面新增排版文字默认使用简体中文；只有 currentPrompt 明确要求英文/English/英文文案/英文标题/英文排版，或用户逐字给出英文原文时，才使用英文。",
            "如果 typographyLanguagePreference 是简体中文，typography.headline、subheadline、sellingPointLabels、badge 必须用中文表达；不要默认输出英文标题、英文卖点、英文角标或英文徽章。",
            "商品包装和 Logo 上原有英文只作为参考图保真内容，不要扩写成新的英文卖点；如果用户需要画面文字，typography 中的每一条字符串必须来自用户提示词、商品包装可见文字、productContext、productProfile.sellingPoints 或对用户痛点的中性概括；不得凭空编造价格、折扣、认证、参数或绝对化功效。",
            "只返回严格 JSON，不输出 Markdown 或解释。",
        ],
        "jsonSchema": {
            "productProfile": {
                "productName": "string",
                "category": "string",
                "subcategory": "string",
                "brand": "string",
                "colors": ["string"],
                "materials": ["string"],
                "shapeStructure": "string",
                "visibleTextLogo": "string",
                "sellingPoints": ["string"],
                "targetAudience": "string",
                "useScenes": ["string"],
                "mustPreserve": ["string"],
                "confidence": "number 0-1",
            },
            "editIntent": "scene_edit | visual_style_edit | product_attribute_edit | product_replace | generate_new | ambiguous",
            "subjectMutationPolicy": "preserve | mutate_requested_attributes | replace",
            "changedAttributes": ["color | material | packaging | shape | label"],
            "referenceRoles": [{"index": "integer", "role": "target_product | scene_reference | composition_reference"}],
            "recommendedSceneType": "sceneTypeEnum value",
            "creativePlan": {
                "conceptTitle": "string",
                "visualHook": "string",
                "audienceMoment": "string",
                "visualDirection": "string",
                "sceneDescription": "string",
                "composition": "string",
                "lighting": "string",
                "camera": "string",
                "materialDetail": "string",
                "colorPalette": "string",
                "textStrategy": "string",
                "typography": {
                    "textLanguage": "简体中文 | 英文",
                    "headline": "string",
                    "subheadline": "string",
                    "sellingPointLabels": ["string"],
                    "badge": "string",
                    "placement": "string",
                    "hierarchy": "string",
                    "fontDirection": "string",
                    "colorDirection": "string",
                    "safeArea": "string"
                },
                "negativePrompt": "string",
            },
            "needsTypography": "boolean",
            "needsClarification": "boolean",
            "clarificationQuestion": "string",
            "warnings": ["string"],
        },
    }


def _remembered_product_context(value: object) -> dict[str, Any]:
    persistent = value if isinstance(value, dict) else {}
    memory = persistent.get("memory") if isinstance(persistent.get("memory"), dict) else {}
    profile = memory.get("product_profile") if isinstance(memory.get("product_profile"), dict) else {}
    return {
        "name": clean_text(profile.get("productName") or profile.get("product_name"), limit=160),
        "brand": clean_text(profile.get("brand"), limit=100),
        "category": clean_text(profile.get("category") or profile.get("subcategory"), limit=100),
        "selling_points": "；".join(clean_string_list(profile.get("sellingPoints"), limit=6, item_limit=180)),
    }


def _autonomous_product_profile(profile: dict[str, Any]) -> dict[str, Any]:
    result = dict(profile)
    result["productName"] = result.get("productName") or "无品牌生活方式商品"
    result["category"] = result.get("category") or "生活方式商品"
    result["confidence"] = max(0.5, float(result.get("confidence") or 0.0))
    result["profileSource"] = "agent_concept"
    result["mustPreserve"] = list(result.get("mustPreserve") or [])
    if "无品牌、无新增参数、无虚构功效" not in result["mustPreserve"]:
        result["mustPreserve"].append("无品牌、无新增参数、无虚构功效")
    return result


def _autonomous_creative_plan(plan: dict[str, Any]) -> dict[str, Any]:
    defaults = {
        "conceptTitle": "日常材质静物",
        "visualHook": "产品曲面与柔和侧光形成清晰轮廓高光，前景留出轻微空间层次",
        "audienceMoment": "用户浏览电商主视觉时快速感受到简洁、可靠和可使用的生活方式气质",
        "visualDirection": "无品牌生活方式商品的克制商业静物摄影",
        "sceneDescription": "现代室内生活方式场景，浅色石材台面、柔和窗光和低干扰空间背景",
        "composition": "单一主体偏右构图，完整露出主体，前中后景有轻微层次，保留干净留白",
        "lighting": "左前方柔和窗光加弱轮廓光，保留真实接触阴影和材质高光",
        "camera": "50mm 商业静物镜头，略低机位，主体清晰，背景自然虚化",
        "materialDetail": "准确表现可见表面材质，不添加无法确认的功能结构",
        "colorPalette": "雾灰、浅石材、低饱和产品相关色，局部暖光平衡画面",
        "textStrategy": "不渲染任何未经用户提供的文字、Logo、价格、参数或功效",
        "negativePrompt": "品牌 Logo、虚构文字、虚构参数、虚构功效、认证标识、多个无关商品、纯白空背景、廉价模板感",
    }
    return {key: plan.get(key) or value for key, value in defaults.items()} | {
        "typography": dict(plan.get("typography") or {}),
    }


def build_professional_image_prompt(body: dict[str, Any]) -> dict[str, Any]:
    if not is_prompt_analysis_enabled():
        raise HTTPException(status_code=400, detail={"error": "openai_relay is not enabled"})

    prompt = clean_text(body.get("prompt"), limit=8000)
    raw_images = list(body.get("images") or [])
    images = validate_reference_images(raw_images, required=False)
    if not prompt and not images:
        raise HTTPException(status_code=400, detail={"error": "prompt or reference images are required"})

    product = body.get("product") if isinstance(body.get("product"), dict) else {}
    persistent_context = body.get("persistent_context") or body.get("persistentContext")
    remembered_product = _remembered_product_context(persistent_context)
    product_context = {
        "name": clean_text(product.get("name") or remembered_product.get("name"), limit=160),
        "sku": clean_text(product.get("sku"), limit=100),
        "brand": clean_text(product.get("brand") or remembered_product.get("brand"), limit=100),
        "category": clean_text(product.get("category") or remembered_product.get("category"), limit=100),
        "selling_points": clean_text(
            product.get("selling_points") or product.get("sellingPoints"),
            limit=800,
        ) or clean_text(remembered_product.get("selling_points"), limit=800),
        "notes": clean_text(product.get("notes"), limit=800),
    }
    requested_scene_type = clean_text(body.get("scene_type") or body.get("sceneType") or "auto", limit=100) or "auto"
    platform = infer_platform(prompt, clean_text(body.get("platform") or "general", limit=80))
    # Image Agent requests carry the generation model in `model`; keep it out of
    # the chat/vision endpoint by using the dedicated planner override instead.
    model_override = body.get("planner_model") if "planner_model" in body else body.get("model")
    model = prompt_analysis_model(clean_text(model_override, limit=160))
    preserve_subject = bool(body.get("preserve_subject", body.get("preserveSubject", True)))

    request_data = _analysis_request(
        prompt=prompt,
        product_context=product_context,
        requested_scene_type=requested_scene_type,
        platform=platform,
        conversation_context=[
            dict(item)
            for item in list(body.get("conversation_context") or body.get("conversationContext") or [])[-6:]
            if isinstance(item, dict)
        ],
    )
    if isinstance(persistent_context, dict):
        request_data["persistentContext"] = persistent_context
    professional_knowledge = body.get("professional_knowledge") or body.get("professionalKnowledge")
    if isinstance(professional_knowledge, dict):
        request_data["professionalKnowledge"] = professional_knowledge
    request_data["referenceImages"] = [
        {
            "index": index,
            "name": image.get("name", f"reference-{index + 1}.png"),
            "isLatest": index == len(images) - 1,
        }
        for index, image in enumerate(images)
    ]
    content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                "你是 RAW 创意图片智能体，工作方式像一位能主动做决定的资深视觉总监。"
                "先结合当前要求、最近对话和参考图建立真实商品画像，再选择一个具体创意概念、场景和视觉记忆点，最后给出可执行摄影计划。"
                "不要用空泛形容词替代场景、光线、材质、镜头和构图决定。\n\n"
                f"{json.dumps(request_data, ensure_ascii=False)}"
            ),
        }
    ]
    for image in images:
        content.append({"type": "image_url", "image_url": {"url": image["data_url"], "detail": "high"}})

    parsed = request_json_completion(
        model=model,
        system_prompt=(
            "你是模型驱动的电商视觉创意智能体。理解真实意图后主动做专业画面决策，只返回严格 JSON。"
            "除非用户明确要求白底、纯白、catalog 或 packshot，否则必须设计具体可见背景。"
            "可以基于商品画像、包装、卖点和用户痛点提炼中性中文画面文案；"
            "不得虚构未看见的商品事实、参数、功效、认证、价格、销量或百分比承诺，也不要输出隐藏思维过程。"
        ),
        content=content,
        max_tokens=PROFESSIONAL_PROMPT_MAX_TOKENS,
    )
    has_reference = bool(images)
    edit_intent = _normalize_edit_intent(
        parsed.get("editIntent") or parsed.get("edit_intent"),
        prompt=prompt,
        has_reference=has_reference,
    )
    subject_mutation_policy = _normalize_subject_policy(
        parsed.get("subjectMutationPolicy") or parsed.get("subject_mutation_policy"),
        edit_intent=edit_intent,
    )
    changed_attributes = _normalize_changed_attributes(
        parsed.get("changedAttributes") or parsed.get("changed_attributes"),
        prompt=prompt,
        edit_intent=edit_intent,
    )
    reference_roles = _reference_roles(
        parsed.get("referenceRoles") or parsed.get("reference_roles"),
        image_count=len(images),
        edit_intent=edit_intent,
    )
    explicit_typography = (
        requested_scene_type == TAOBAO_TEXT_SCENE_ID
        or _is_taobao_text_request(prompt, platform)
    )
    needs_typography = explicit_typography or (
        _as_bool(parsed.get("needsTypography") or parsed.get("needs_typography"))
        and any(keyword in prompt.lower() for keyword in ("文字", "标题", "卖点", "排版", "淘宝", "天猫", "车图", "taobao", "tmall"))
    )
    needs_clarification = _as_bool(
        parsed.get("needsClarification") or parsed.get("needs_clarification"),
        default=edit_intent == REFERENCE_EDIT_AMBIGUOUS,
    )
    clarification_question = clean_text(
        parsed.get("clarificationQuestion") or parsed.get("clarification_question"),
        limit=300,
    )
    if edit_intent == REFERENCE_EDIT_AMBIGUOUS:
        needs_clarification = True
        clarification_question = clarification_question or "你说的“样式”是指商品本身的包装、颜色、材质和造型，还是图片的摄影风格与场景？"
    profile_value = parsed.get("productProfile") or parsed.get("product_profile")
    product_profile = normalize_product_profile(profile_value, product_context)
    autonomy_requested = _autonomy_requested(prompt)
    has_grounded_subject = bool(
        images
        or product_profile.get("productName")
        or product_profile.get("category")
        or product_profile.get("subcategory")
        or product_context.get("name")
        or product_context.get("category")
    )
    if autonomy_requested and not has_grounded_subject:
        product_profile = _autonomous_product_profile(product_profile)
        has_grounded_subject = True
        needs_clarification = False
        clarification_question = ""
    if needs_clarification and edit_intent != REFERENCE_EDIT_AMBIGUOUS:
        if has_grounded_subject:
            needs_clarification = False
            clarification_question = ""
        else:
            clarification_question = "你要做的是什么商品？可以直接告诉我商品名称，或上传一张商品参考图。"
    recommended_scene_type = clean_text(
        parsed.get("recommendedSceneType") or parsed.get("recommended_scene_type"),
        limit=100,
    )
    if autonomy_requested and requested_scene_type == "auto" and recommended_scene_type in {"", "white_background"}:
        recommended_scene_type = "lifestyle"
    scene_template = resolve_scene_template(
        requested_scene_type,
        prompt=prompt,
        recommended_scene_type=recommended_scene_type,
    )
    if requested_scene_type == "auto" and scene_template.get("id") == "white_background" and not _explicit_white_background_requested(prompt):
        scene_template = resolve_scene_template("auto", prompt=prompt, recommended_scene_type="luxury_atmosphere")
    if requested_scene_type == "auto" and not needs_typography and scene_template.get("id") == TAOBAO_TEXT_SCENE_ID:
        scene_template = resolve_scene_template("auto", prompt=prompt, recommended_scene_type="lifestyle")
    creative_plan = _clean_plan(parsed.get("creativePlan") or parsed.get("creative_plan"))
    if autonomy_requested and product_profile.get("productName") == "无品牌生活方式商品":
        creative_plan = _autonomous_creative_plan(creative_plan)
    warnings = clean_string_list(parsed.get("warnings"), limit=6, item_limit=260)
    if images and product_profile.get("confidence", 0.0) < 0.45:
        warnings.append("商品识别置信度较低，生成前请重点核对商品结构、颜色与文字。")

    final_prompt, negative_prompt = compose_professional_prompt(
        user_prompt=prompt,
        product_profile=product_profile,
        scene_template=scene_template,
        creative_plan=creative_plan,
        platform=platform,
        has_reference=bool(images),
        preserve_subject=preserve_subject,
        output_size=clean_text(body.get("size"), limit=40),
        edit_intent=edit_intent,
        subject_mutation_policy=subject_mutation_policy,
        changed_attributes=changed_attributes,
        reference_roles=reference_roles,
        needs_typography=needs_typography,
    )
    typography = _typography_plan(
        product_profile=product_profile,
        creative_plan=creative_plan,
        scene_template=scene_template,
        platform=platform,
        user_prompt=prompt,
        enabled=needs_typography,
    )
    if needs_clarification:
        warnings.append("商品主体或商品本体修改范围尚不明确，需要先确认一个关键信息。")
    if autonomy_requested and product_profile.get("productName") == "无品牌生活方式商品":
        warnings.append("本方案为 Agent 自拟的无品牌商品概念，不代表用户真实商品、规格或功效。")
    return {
        "domain": "ecommerce",
        "promptEngineMode": "professional",
        "model": model,
        "productProfile": product_profile,
        "editIntent": edit_intent,
        "subjectMutationPolicy": subject_mutation_policy,
        "changedAttributes": changed_attributes,
        "referenceRoles": reference_roles,
        "sceneType": scene_template["id"],
        "sceneName": scene_template["name"],
        "creativeConcept": creative_plan.get("conceptTitle"),
        "visualHook": creative_plan.get("visualHook"),
        "visualDirection": creative_plan.get("visualDirection") or clean_text(scene_template.get("description"), limit=500),
        "finalPrompt": final_prompt,
        "negativePrompt": negative_prompt,
        "typography": typography,
        "needsTypography": needs_typography,
        "needsClarification": needs_clarification,
        "clarificationQuestion": clarification_question,
        "warnings": list(dict.fromkeys(warnings)),
    }


def build_adaptive_image_prompt(body: dict[str, Any]) -> dict[str, Any]:
    """Choose ecommerce intelligence only when the current request is commercial."""
    prompt = clean_text(body.get("prompt"), limit=8000)
    product = body.get("product") if isinstance(body.get("product"), dict) else {}
    platform = clean_text(body.get("platform") or "", limit=80)
    has_reference = bool(body.get("images"))
    explicit_scene = clean_text(body.get("scene_type") or body.get("sceneType") or "auto", limit=100)
    ecommerce_scene_selected = explicit_scene == TAOBAO_TEXT_SCENE_ID
    persistent_context = body.get("persistent_context") or body.get("persistentContext")
    remembered_product = _remembered_product_context(persistent_context)
    has_remembered_ecommerce_context = any(remembered_product.values())
    current_is_ecommerce = is_ecommerce_request(
        prompt,
        product,
        has_reference=has_reference,
        platform=platform,
    )
    if (
        ecommerce_scene_selected
        or current_is_ecommerce
        or _autonomy_requested(prompt)
        or (has_remembered_ecommerce_context and not _explicit_general_creation_requested(prompt))
    ):
        return build_professional_image_prompt(body)
    return build_general_image_prompt(body)
