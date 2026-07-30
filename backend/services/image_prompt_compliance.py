from __future__ import annotations

import re

HIGH_RISK_CLAIM_REPLACEMENTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"杀菌率\s*99(?:\.\d+)?\s*%?", re.IGNORECASE), "清洁表现"),
    (re.compile(r"99(?:\.\d+)?\s*%?\s*杀菌率?", re.IGNORECASE), "清洁表现"),
    (re.compile(r"杀灭细菌|灭活病毒|抗病毒|医用级|医疗级|消毒|杀菌|医用", re.IGNORECASE), "清洁护理"),
)

IMAGE_PROMPT_COMPLIANCE_GUARD = (
    "合规约束：画面文字只保留中性产品信息；不要生成医疗、消杀、抗微生物、"
    "病毒相关或等级背书类功效宣称，不要新增功效徽章、百分比承诺或认证标识。"
)
IMAGE_LAYOUT_GUARD_PREFIX = "画面结构约束："
IMAGE_SINGLE_LAYOUT_GUARD = (
    f"{IMAGE_LAYOUT_GUARD_PREFIX}只生成一张完整独立图片，只展示一个主场景，不要拼图、不要分屏、"
    "不要九宫格、不要多面板，不要把多个场景或多张成品图合在同一张画布里。"
)


def strip_high_risk_claims(text: str) -> str:
    cleaned = str(text or "")
    for pattern, replacement in HIGH_RISK_CLAIM_REPLACEMENTS:
        cleaned = pattern.sub(replacement, cleaned)
    return cleaned.strip()


def _append_guard_once(text: str, guard: str, marker: str) -> str:
    cleaned = text.strip()
    if marker in cleaned:
        return cleaned
    return f"{cleaned}\n\n{guard}".strip()


def image_layout_guard(*, image_count: int = 1, image_index: int = 0) -> str:
    total = max(1, int(image_count or 1))
    if total <= 1:
        return IMAGE_SINGLE_LAYOUT_GUARD
    current = min(total, max(1, int(image_index or 0) + 1))
    return (
        f"{IMAGE_LAYOUT_GUARD_PREFIX}这是第 {current}/{total} 张独立成品图；本次只生成这一张图，"
        "可以选择一个不同场景或卖点表达，但不要拼图、不要分屏、不要九宫格、不要多面板，"
        f"不要把其他编号或其他场景放进同一张画布里，画面中也不要写“第{current}张”。"
    )


def sanitize_image_prompt(text: str, *, image_count: int = 1, image_index: int = 0) -> str:
    cleaned = strip_high_risk_claims(text)
    cleaned = _append_guard_once(
        cleaned,
        image_layout_guard(image_count=image_count, image_index=image_index),
        IMAGE_LAYOUT_GUARD_PREFIX,
    )
    return _append_guard_once(cleaned, IMAGE_PROMPT_COMPLIANCE_GUARD, IMAGE_PROMPT_COMPLIANCE_GUARD)
