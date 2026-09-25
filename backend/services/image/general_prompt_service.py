from __future__ import annotations

import json
import re
from math import gcd
from typing import Any

from fastapi import HTTPException

from services.ecommerce.prompt_analysis_service import (
    is_prompt_analysis_enabled,
    prompt_analysis_model,
    request_json_completion,
    validate_reference_images,
)
from services.image.image_prompt_compliance import has_explicit_english_typography_request


GENERAL_PROMPT_MAX_TOKENS = 2600
OUTPUT_SIZE_RE = re.compile(r"^\s*(\d+)\s*x\s*(\d+)\s*$", re.IGNORECASE)
TYPOGRAPHY_KEYWORDS = ("文字", "标题", "文案", "排版", "字幕", "海报字", "写上", "显示文字", "text", "title", "caption")
CASUAL_ONLY_RE = re.compile(
    r"^(?:你|您)?好(?:呀|啊|哇|哦|喔|哈)?|^(?:hi|hello|hey)|^(?:嗨|哈喽|在吗|有人吗|早上好|上午好|中午好|下午好|晚上好|谢谢|感谢)(?:呀|啊|啦|了|哦|喔|哈)?$",
    re.IGNORECASE,
)
PLANNER_FORMAT_ERRORS = frozenset({
    "vision model did not return valid JSON",
    "vision model JSON is not an object",
    "vision model response content is empty",
    "visual planner response missing finalPrompt",
})


def _clean(value: object, limit: int = 8000) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _clean_list(value: object, *, limit: int = 8, item_limit: int = 300) -> list[str]:
    items = value if isinstance(value, list) else [value] if isinstance(value, str) else []
    result: list[str] = []
    for item in items:
        cleaned = _clean(item, item_limit)
        if cleaned and cleaned not in result:
            result.append(cleaned)
        if len(result) >= limit:
            break
    return result


def _as_bool(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "y", "是", "需要"}
    return default


def _canvas_requirement(value: object) -> str:
    matched = OUTPUT_SIZE_RE.match(_clean(value, 40).lower())
    if not matched:
        return ""
    width = max(1, int(matched.group(1)))
    height = max(1, int(matched.group(2)))
    divisor = gcd(width, height)
    ratio = f"{width // divisor}:{height // divisor}"
    return (
        f"输出画布严格保持 {width}x{height}（{ratio}）比例；主体、文字和关键内容必须完整位于画布内，"
        "不得先生成其他比例再裁切，不得截断主体。"
    )


def _normalize_subject_policy(value: object) -> str:
    normalized = _clean(value, 80).lower().replace("-", "_").replace(" ", "_")
    return {
        "mutate": "mutate_requested_attributes",
        "modify": "mutate_requested_attributes",
        "mutate_requested_attributes": "mutate_requested_attributes",
        "replace": "replace",
        "preserve": "preserve",
        "keep": "preserve",
    }.get(normalized, "preserve")


def _normalize_edit_intent(value: object, has_reference: bool) -> str:
    if not has_reference:
        return "generate_new"
    normalized = _clean(value, 80).lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "scene": "scene_edit",
        "scene_edit": "scene_edit",
        "style": "visual_style_edit",
        "visual_style": "visual_style_edit",
        "visual_style_edit": "visual_style_edit",
        "subject": "product_attribute_edit",
        "subject_edit": "product_attribute_edit",
        "product_attribute_edit": "product_attribute_edit",
        "replace": "product_replace",
        "subject_replace": "product_replace",
        "product_replace": "product_replace",
        "ambiguous": "ambiguous",
    }
    return aliases.get(normalized, "visual_style_edit")


def _is_casual_only_prompt(prompt: str) -> bool:
    compact = re.sub(r"[\s，,。.!！?？~～]+", "", prompt).strip()
    return bool(compact and CASUAL_ONLY_RE.fullmatch(compact))


def _planner_format_detail(exc: HTTPException) -> tuple[bool, str]:
    detail = exc.detail if isinstance(exc.detail, dict) else {}
    error = _clean(detail.get("error"), 200)
    question = _clean(detail.get("clarificationQuestion") or detail.get("clarification_question"), 300)
    return error in PLANNER_FORMAT_ERRORS, question


def _structured_prompt_fallback(
    *,
    prompt: str,
    parsed: dict[str, Any],
    has_reference: bool,
) -> str:
    parts: list[str] = []
    if prompt:
        parts.append(f"用户目标：{prompt}")
    field_names = (
        (("subjectSummary", "subject_summary"), "主体"),
        (("visualHook", "visual_hook"), "视觉记忆点"),
        (("visualDirection", "visual_direction"), "视觉方向"),
        (("sceneDescription", "scene_description", "scene"), "场景"),
        (("background", "backgroundDescription", "background_description"), "背景"),
        (("composition",), "构图"),
        (("lighting",), "光线"),
        (("colorPalette", "color_palette"), "色彩"),
        (("camera",), "镜头"),
        (("materialDetail", "material_detail"), "材质细节"),
    )
    for keys, label in field_names:
        value = next((_clean(parsed.get(key), 700) for key in keys if _clean(parsed.get(key), 700)), "")
        if value and value not in prompt:
            parts.append(f"{label}：{value}")
    if has_reference:
        parts.append("参考图约束：只按用户本轮要求使用参考主体、场景、构图或风格，未要求修改的可见特征保持一致。")
    if not parts:
        return ""
    parts.append("输出一张主体完整、构图清晰、光影和空间关系可信的独立完整图片，不要拼图、分屏或多面板。")
    return "\n".join(parts)


def _general_clarification_question(prompt: str, has_reference: bool, parsed: dict[str, Any]) -> str:
    explicit = _clean(
        parsed.get("clarificationQuestion") or parsed.get("clarification_question"),
        300,
    )
    if explicit:
        return explicit
    if has_reference and not prompt:
        return "你希望如何使用这张参考图？请告诉我要保留什么，以及要修改主体、场景、风格还是构图。"
    if _is_casual_only_prompt(prompt):
        return "你好。你想生成或修改什么画面？告诉我主体和用途即可，我会继续帮你规划。"
    return "你想生成或修改什么画面？请补充核心主体、画面用途，或说明参考图需要修改的内容。"


def build_general_image_prompt(body: dict[str, Any]) -> dict[str, Any]:
    if not is_prompt_analysis_enabled():
        raise HTTPException(status_code=400, detail={"error": "openai_relay is not enabled"})

    prompt = _clean(body.get("prompt"))
    images = validate_reference_images(list(body.get("images") or []), required=False)
    if not prompt and not images:
        raise HTTPException(status_code=400, detail={"error": "prompt or reference images are required"})

    model_override = body.get("planner_model") if "planner_model" in body else body.get("model")
    model = prompt_analysis_model(_clean(model_override, 160))
    has_reference = bool(images)
    requested_size = _clean(body.get("size"), 40)
    explicit_typography = any(keyword in prompt.lower() for keyword in TYPOGRAPHY_KEYWORDS)
    typography_language = "英文" if has_explicit_english_typography_request(prompt) else "简体中文"
    request_data = {
        "task": "理解通用图片创作或编辑意图，并生成可直接交给生图模型的结构化视觉方案。",
        "currentPrompt": prompt,
        "hasReferenceImages": has_reference,
        "requestedSize": requested_size,
        "typographyLanguagePreference": typography_language,
        "recentConversationContext": [
            dict(item)
            for item in list(body.get("conversation_context") or body.get("conversationContext") or [])[-6:]
            if isinstance(item, dict)
        ],
        "rules": [
            "结合 recentConversationContext 理解短追问和沿用关系；currentPrompt 与历史冲突时以 currentPrompt 为准。",
            "忠实保留用户明确指定的主题、主体、动作、场景、媒介、风格、构图、文字和比例。",
            "只补充必要的构图、光线、色彩、材质、镜头和画面完整性要求，不改变任务类型。",
            "信息足够时像视觉导演一样主动选择一个具体画面概念和视觉记忆点，不要把场景、镜头、光线、色彩等专业选择反问给用户。",
            "不得把非电商创作改写为商品广告、淘宝主图、品牌大片、固定营销海报或商品套图。",
            "不得自行增加用户未提及的品牌、Logo、作品名、角色名、明星、艺术家、官方风格、认证、功效或营销承诺。",
            "若用户提到受保护的品牌、作品、角色、明星或艺术家，把它转换成原创、不可识别的同类视觉表达；保留题材、媒介和氛围，不复刻具体身份、Logo、官方标识、独有服装或独有造型。",
            "如果有参考图，只按用户要求使用参考主体、构图或风格；无法确认的细节留空，不要臆测。",
            "只有用户明确要求画面文字时 needsTypography 才能为 true，所有文字必须来自用户原文。",
            "画面新增排版文字默认使用简体中文；只有 currentPrompt 明确要求英文/English/英文文案/英文标题/英文排版，或用户逐字给出英文原文时，才使用英文。",
            "finalPrompt 不得包含分析过程、政策说明、作品替换说明或隐藏思维过程，只写最终可执行的视觉描述。",
            "只返回严格 JSON。",
        ],
        "jsonSchema": {
            "editIntent": "scene_edit | visual_style_edit | subject_edit | subject_replace | generate_new | ambiguous",
            "subjectMutationPolicy": "preserve | mutate_requested_attributes | replace",
            "changedAttributes": ["string"],
            "referenceRoles": [{"index": "integer", "role": "subject_reference | scene_reference | composition_reference | style_reference"}],
            "subjectSummary": "string",
            "visualDirection": "string",
            "composition": "string",
            "lighting": "string",
            "colorPalette": "string",
            "camera": "string",
            "finalPrompt": "string",
            "negativePrompt": "string",
            "typography": {
                "language": "简体中文 | 英文",
                "text": ["string"],
                "placement": "string",
                "hierarchy": "string",
                "safeArea": "string",
            },
            "needsTypography": "boolean",
            "needsClarification": "boolean",
            "clarificationQuestion": "string",
            "warnings": ["string"],
        },
    }
    professional_knowledge = body.get("professional_knowledge") or body.get("professionalKnowledge")
    if isinstance(professional_knowledge, dict):
        request_data["professionalKnowledge"] = professional_knowledge
    content: list[dict[str, Any]] = [{"type": "text", "text": json.dumps(request_data, ensure_ascii=False)}]
    for image in images:
        content.append({"type": "image_url", "image_url": {"url": image["data_url"], "detail": "high"}})

    planner_warning = ""
    planner_question = ""
    try:
        parsed = request_json_completion(
            model=model,
            system_prompt=(
                "你是 RAW 创意图片智能体和通用视觉导演。结合当前要求、最近对话与参考图主动做具体画面决策。只返回严格 JSON，不输出隐藏思维过程。"
                "保持用户意图，不把普通创作电商化；对受保护的品牌、作品、角色、明星和艺术家使用原创、不可识别的替代表达。"
            ),
            content=content,
            max_tokens=GENERAL_PROMPT_MAX_TOKENS,
            temperature=0.2,
            billing_owner_id=body.get("billing_owner_id"),
            billing_local_task_id=body.get("billing_local_task_id"),
            billing_local_source=body.get("billing_local_source") or "image_agent_prompt",
        )
    except HTTPException as exc:
        is_format_error, planner_question = _planner_format_detail(exc)
        if not is_format_error:
            raise
        parsed = {}
        planner_warning = "视觉规划模型未返回完整结构，已使用确定性通用规划兜底。"

    final_prompt = _clean(parsed.get("finalPrompt") or parsed.get("final_prompt"), 7000)
    clarification_question = _clean(
        parsed.get("clarificationQuestion") or parsed.get("clarification_question") or planner_question,
        300,
    )
    needs_clarification = (
        _as_bool(parsed.get("needsClarification") or parsed.get("needs_clarification"))
        or bool(clarification_question and not final_prompt)
        or _is_casual_only_prompt(prompt)
        or (not prompt and has_reference and not final_prompt)
    )
    if needs_clarification:
        final_prompt = ""
        clarification_question = _general_clarification_question(prompt, has_reference, {
            **parsed,
            "clarificationQuestion": clarification_question,
        })
    elif not final_prompt:
        final_prompt = _structured_prompt_fallback(
            prompt=prompt,
            parsed=parsed,
            has_reference=has_reference,
        )
        planner_warning = planner_warning or "视觉规划模型缺少 finalPrompt，已根据当前需求和结构化画面字段补齐。"
    if not final_prompt and not needs_clarification:
        needs_clarification = True
        clarification_question = _general_clarification_question(prompt, has_reference, parsed)

    canvas_requirement = _canvas_requirement(requested_size)
    if final_prompt and canvas_requirement:
        final_prompt = f"{final_prompt}\n画布要求：{canvas_requirement}"

    needs_typography = explicit_typography and _as_bool(
        parsed.get("needsTypography") or parsed.get("needs_typography"),
        default=True,
    )
    typography = dict(parsed.get("typography")) if needs_typography and isinstance(parsed.get("typography"), dict) else {}
    if typography:
        typography["language"] = typography_language
    if final_prompt and needs_typography:
        if typography_language == "英文":
            final_prompt = f"{final_prompt}\n画面文字语言：用户已明确要求英文时可使用英文，仍不得新增未提供的虚假卖点或徽章。"
        else:
            final_prompt = f"{final_prompt}\n画面文字语言：新增排版文字默认使用简体中文；不要自动生成英文标题、英文卖点或英文徽章。"
    edit_intent = _normalize_edit_intent(
        parsed.get("editIntent") or parsed.get("edit_intent"),
        has_reference,
    )
    subject_policy = _normalize_subject_policy(
        parsed.get("subjectMutationPolicy") or parsed.get("subject_mutation_policy")
    )
    if edit_intent == "product_attribute_edit":
        subject_policy = "mutate_requested_attributes"
    elif edit_intent == "product_replace":
        subject_policy = "replace"

    reference_roles = parsed.get("referenceRoles") or parsed.get("reference_roles")
    if not isinstance(reference_roles, list):
        reference_roles = []
    raw_warnings = parsed.get("warnings")
    parsed_warnings = raw_warnings if isinstance(raw_warnings, list) else [raw_warnings] if raw_warnings else []
    return {
        "domain": "general",
        "promptEngineMode": "general",
        "model": model,
        "productProfile": {},
        "subjectSummary": _clean(parsed.get("subjectSummary") or parsed.get("subject_summary"), 500),
        "editIntent": edit_intent,
        "subjectMutationPolicy": subject_policy,
        "changedAttributes": _clean_list(parsed.get("changedAttributes") or parsed.get("changed_attributes"), limit=10, item_limit=100),
        "referenceRoles": reference_roles[:4],
        "sceneType": "auto",
        "sceneName": "通用创意视觉",
        "visualDirection": _clean(parsed.get("visualDirection") or parsed.get("visual_direction"), 600) or "忠实执行用户创作意图",
        "finalPrompt": final_prompt,
        "negativePrompt": _clean(parsed.get("negativePrompt") or parsed.get("negative_prompt"), 1400),
        "typography": typography,
        "needsTypography": needs_typography,
        "needsClarification": needs_clarification,
        "clarificationQuestion": clarification_question,
        "warnings": _clean_list([*parsed_warnings, planner_warning], limit=6, item_limit=260),
    }
