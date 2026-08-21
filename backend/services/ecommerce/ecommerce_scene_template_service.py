from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from services.ecommerce.ecommerce_profile_service import clean_text


SCENE_TEMPLATE_PATH = Path(__file__).resolve().parents[2] / "resources" / "ecommerce_scene_templates.json"
DEFAULT_SCENE_TYPE = "luxury_atmosphere"


@lru_cache(maxsize=1)
def load_scene_templates() -> tuple[dict[str, Any], ...]:
    with SCENE_TEMPLATE_PATH.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, list) or not payload:
        raise RuntimeError("ecommerce scene templates are empty")

    templates: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in payload:
        if not isinstance(item, dict):
            continue
        scene_id = clean_text(item.get("id"), limit=80)
        name = clean_text(item.get("name"), limit=120)
        if not scene_id or not name or scene_id in seen_ids:
            raise RuntimeError("invalid or duplicate ecommerce scene template")
        seen_ids.add(scene_id)
        templates.append(item)
    if DEFAULT_SCENE_TYPE not in seen_ids:
        raise RuntimeError(f"default ecommerce scene template is missing: {DEFAULT_SCENE_TYPE}")
    return tuple(templates)


def public_scene_templates() -> list[dict[str, object]]:
    return [
        {
            "id": item["id"],
            "name": item["name"],
            "description": clean_text(item.get("description"), limit=240),
        }
        for item in load_scene_templates()
    ]


def _scene_index() -> dict[str, dict[str, Any]]:
    return {str(item["id"]): item for item in load_scene_templates()}


def _canonical_scene_id(value: object) -> str:
    text = clean_text(value, limit=100).lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "auto": "auto",
        "hero": "white_background",
        "hero_image": "white_background",
        "white": "white_background",
        "white_background_main": "white_background",
        "detail": "material_macro",
        "macro": "material_macro",
        "poster": "poster_banner",
        "banner": "poster_banner",
        "social": "social_cover",
        "magazine": "magazine_editorial",
        "editorial": "magazine_editorial",
        "luxury": "luxury_atmosphere",
        "luxury_atmospherics": "luxury_atmosphere",
    }
    return aliases.get(text, text)


def resolve_scene_template(
    requested_scene_type: object = "auto",
    *,
    prompt: str = "",
    recommended_scene_type: object = "",
) -> dict[str, Any]:
    index = _scene_index()
    requested = _canonical_scene_id(requested_scene_type or "auto")
    if requested != "auto":
        if requested not in index:
            raise ValueError(f"unsupported ecommerce scene type: {requested}")
        return index[requested]

    recommended = _canonical_scene_id(recommended_scene_type)
    lowered_prompt = clean_text(prompt, limit=4000).lower()
    scored: list[tuple[int, int, dict[str, Any]]] = []
    for order, template in enumerate(load_scene_templates()):
        aliases = [str(item).strip().lower() for item in template.get("aliases", []) if str(item).strip()]
        matched = [alias for alias in aliases if alias in lowered_prompt]
        score = sum(max(1, len(alias)) for alias in matched)
        if score:
            scored.append((score, -order, template))
    if scored:
        return max(scored, key=lambda item: (item[0], item[1]))[2]
    if recommended in index:
        return index[recommended]
    return index[DEFAULT_SCENE_TYPE]


def category_guidance(template: dict[str, Any], category: object) -> str:
    category_text = clean_text(category, limit=120).lower()
    tips = template.get("categoryTips") if isinstance(template.get("categoryTips"), dict) else {}
    for keyword, guidance in tips.items():
        if str(keyword).lower() in category_text or category_text in str(keyword).lower():
            return clean_text(guidance, limit=300)
    return ""
