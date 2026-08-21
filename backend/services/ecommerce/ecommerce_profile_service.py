from __future__ import annotations

import re
from typing import Any


def clean_text(value: object, *, limit: int = 600) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def clean_string_list(value: object, *, limit: int = 8, item_limit: int = 180) -> list[str]:
    if isinstance(value, str):
        items: list[object] = re.split(r"[\n,，;；]+", value)
    elif isinstance(value, list):
        items = value
    else:
        items = []

    result: list[str] = []
    for item in items:
        if isinstance(item, dict):
            title = clean_text(item.get("title") or item.get("name"), limit=80)
            description = clean_text(item.get("description") or item.get("value"), limit=item_limit)
            text = "：".join(part for part in (title, description) if part)
        else:
            text = clean_text(item, limit=item_limit)
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def normalize_product_profile(value: object, fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    fallback = fallback if isinstance(fallback, dict) else {}

    selling_points = clean_string_list(
        source.get("sellingPoints")
        or source.get("selling_points")
        or fallback.get("selling_points")
        or fallback.get("sellingPoints"),
        limit=6,
    )
    must_preserve = clean_string_list(
        source.get("mustPreserve") or source.get("must_preserve"),
        limit=10,
    )

    visible_text = clean_text(
        source.get("visibleTextLogo")
        or source.get("visible_text_logo")
        or source.get("logoText")
        or source.get("logo_text"),
        limit=240,
    )
    if visible_text and visible_text not in must_preserve:
        must_preserve.append(f"可见文字与 Logo：{visible_text}")

    colors = clean_string_list(source.get("colors") or source.get("color"), limit=6, item_limit=80)
    materials = clean_string_list(source.get("materials") or source.get("material"), limit=6, item_limit=100)

    raw_confidence = source.get("confidence")
    try:
        confidence = max(0.0, min(1.0, float(raw_confidence)))
    except (TypeError, ValueError):
        confidence = 0.0

    return {
        "productName": clean_text(
            source.get("productName")
            or source.get("product_name")
            or fallback.get("name"),
            limit=160,
        ),
        "category": clean_text(
            source.get("category")
            or source.get("productCategory")
            or source.get("product_category")
            or fallback.get("category"),
            limit=100,
        ),
        "subcategory": clean_text(
            source.get("subcategory")
            or source.get("subCategory")
            or source.get("productSubtype")
            or source.get("product_subtype"),
            limit=100,
        ),
        "brand": clean_text(source.get("brand") or fallback.get("brand"), limit=100),
        "colors": colors,
        "materials": materials,
        "shapeStructure": clean_text(
            source.get("shapeStructure")
            or source.get("shape_structure")
            or source.get("structure"),
            limit=260,
        ),
        "visibleTextLogo": visible_text,
        "sellingPoints": selling_points,
        "targetAudience": clean_text(
            source.get("targetAudience") or source.get("target_audience"),
            limit=200,
        ),
        "useScenes": clean_string_list(
            source.get("useScenes") or source.get("use_scenes") or source.get("targetScenes") or source.get("target_scenes"),
            limit=6,
        ),
        "mustPreserve": must_preserve,
        "confidence": confidence,
    }


def product_profile_summary(profile: dict[str, Any]) -> str:
    identity = "，".join(
        part
        for part in (
            clean_text(profile.get("brand"), limit=100),
            clean_text(profile.get("productName"), limit=160),
            clean_text(profile.get("category"), limit=100),
            clean_text(profile.get("subcategory"), limit=100),
        )
        if part
    )
    details: list[str] = []
    if profile.get("colors"):
        details.append(f"颜色：{'、'.join(profile['colors'])}")
    if profile.get("materials"):
        details.append(f"材质：{'、'.join(profile['materials'])}")
    if profile.get("shapeStructure"):
        details.append(f"结构：{profile['shapeStructure']}")
    if profile.get("visibleTextLogo"):
        details.append(f"可见文字/Logo：{profile['visibleTextLogo']}")
    return "；".join(part for part in (identity, *details) if part)
