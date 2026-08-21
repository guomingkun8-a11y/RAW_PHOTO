from __future__ import annotations

import re


MEMORY_CATEGORIES = frozenset({
    "identity",
    "preference",
    "visual_style",
    "brand_rule",
    "product",
    "constraint",
    "decision",
    "project",
    "note",
})
USER_MEMORY_CATEGORIES = frozenset({"identity", "preference", "visual_style"})
PROJECT_MEMORY_CATEGORIES = frozenset({"product", "constraint", "decision", "project"})

EXPLICIT_MEMORY_PATTERN = re.compile(
    r"(?:请|麻烦)?(?:帮我)?记(?:住|一下|下来)|"
    r"不要忘(?:记)?|"
    r"保存(?:为|成)?(?:长期)?记忆|"
    r"设为(?:我的)?默认(?:偏好|规则|风格|设置)?|"
    r"(?:以后|今后)(?:都|一律|每次|默认)",
    re.IGNORECASE,
)
EXPLICIT_MEMORY_PREFIX_PATTERN = re.compile(
    r"^\s*(?:(?:请|麻烦)?(?:帮我)?记(?:住|一下|下来)|不要忘(?:记)?|"
    r"保存(?:为|成)?(?:长期)?记忆|设为(?:我的)?默认(?:偏好|规则|风格|设置)?)"
    r"\s*[:：,，。-]*\s*",
    re.IGNORECASE,
)


def explicit_memory_requested(value: object) -> bool:
    return bool(EXPLICIT_MEMORY_PATTERN.search(str(value or "")))


def explicit_memory_content(value: object) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    stripped = EXPLICIT_MEMORY_PREFIX_PATTERN.sub("", text, count=1).strip()
    return (stripped or text)[:6000]


def normalize_memory_category(value: object, content: object = "") -> str:
    category = str(value or "note").strip().lower()[:48]
    aliases = {
        "brand": "brand_rule",
        "style": "visual_style",
        "visual": "visual_style",
        "profile": "identity",
        "user": "identity",
    }
    category = aliases.get(category, category)
    if category in MEMORY_CATEGORIES and category != "note":
        return category

    text = str(content or "")
    if re.search(r"品牌|logo|品牌色|品牌规范|品牌规则", text, re.IGNORECASE):
        return "brand_rule"
    if re.search(r"产品|商品|SKU|型号|规格|材质|包装|容量|尺寸|卖点", text, re.IGNORECASE):
        return "product"
    if re.search(r"我是|我叫|我的身份|我的职业|我的公司", text, re.IGNORECASE):
        return "identity"
    if re.search(r"风格|背景|构图|光线|色调|字体|排版|视觉", text, re.IGNORECASE):
        return "visual_style"
    if re.search(r"不要|禁止|必须|不能|限制", text, re.IGNORECASE):
        return "constraint"
    if re.search(r"偏好|喜欢|习惯|默认", text, re.IGNORECASE):
        return "preference"
    return "note"


def route_memory_scope(
    category: object,
    *,
    project_id: object = "",
    brand_id: object = "",
    conversation_id: object = "",
) -> tuple[str, str]:
    normalized = normalize_memory_category(category)
    project = str(project_id or "").strip()[:191]
    brand = str(brand_id or "").strip()[:191]
    conversation = str(conversation_id or "").strip()[:191]

    if normalized == "brand_rule" and brand:
        return "brand", brand
    if normalized in PROJECT_MEMORY_CATEGORIES:
        scope_id = project or conversation
        return ("project", scope_id) if scope_id else ("user", "")
    if normalized in USER_MEMORY_CATEGORIES or normalized == "brand_rule":
        return "user", ""
    if conversation:
        return "conversation", conversation
    return "user", ""
