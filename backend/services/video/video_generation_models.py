from __future__ import annotations

from typing import Any


def _duration_range(start: int, end: int) -> tuple[int, ...]:
    return tuple(range(start, end + 1))


VIDEO_GENERATION_MODEL_SPECS: dict[str, dict[str, Any]] = {
    "hailuo-h3": {
        "label": "海螺 H3",
        "modes": ("text_to_video",),
        "min_images": 0,
        "max_images": 0,
        "aspect_ratios": ("16:9", "9:16", "1:1", "4:3", "3:4", "21:9"),
        "durations": _duration_range(4, 15),
        "default_duration": 5,
        "option_key": "resolution",
        "options": ("768P", "1080P", "2K"),
        "default_option": "768P",
        "extra_params": {},
    },
    "doubao-seedance-2-5-260628": {
        "label": "豆包 Seedance 2.5",
        "modes": ("text_to_video",),
        "min_images": 0,
        "max_images": 0,
        "aspect_ratios": ("adaptive", "16:9", "4:3", "1:1", "3:4", "9:16", "21:9"),
        "durations": _duration_range(4, 30),
        "default_duration": 5,
        "option_key": "resolution",
        "options": ("480p", "720p"),
        "default_option": "480p",
        "extra_params": {"web_search": False},
    },
    "kling-v3-video": {
        "label": "可灵 V3 Video",
        "modes": ("text_to_video",),
        "min_images": 0,
        "max_images": 0,
        "aspect_ratios": ("16:9", "9:16", "1:1"),
        "durations": (5, 10, 15),
        "default_duration": 5,
        "option_key": "mode",
        "options": ("std", "pro"),
        "default_option": "std",
        "extra_params": {},
    },
    "hailuo-h3-cankaosheng": {
        "label": "海螺 H3 参考生",
        "modes": ("image_to_video",),
        "min_images": 1,
        "max_images": 9,
        "aspect_ratios": ("adaptive", "16:9", "9:16", "1:1", "4:3", "3:4", "21:9"),
        "durations": _duration_range(4, 15),
        "default_duration": 5,
        "option_key": "resolution",
        "options": ("768P", "1080P", "2K", "4K"),
        "default_option": "768P",
        "extra_params": {},
    },
    "hailuo-h3-max-shouweizhen": {
        "label": "海螺 H3 Max 首尾帧",
        "modes": ("image_to_video",),
        "min_images": 1,
        "max_images": 2,
        "image_input_kind": "first_last_frame",
        "aspect_ratios": ("adaptive",),
        "include_aspect_ratio": False,
        "durations": _duration_range(5, 15),
        "default_duration": 5,
        "option_key": "resolution",
        "options": ("480P", "768P"),
        "default_option": "480P",
        "extra_params": {},
    },
    "gk-video-3.5": {
        "label": "GK-video-3.5",
        "modes": ("image_to_video",),
        "min_images": 1,
        "max_images": 1,
        "aspect_ratios": ("16:9", "9:16", "1:1", "3:2", "2:3"),
        "durations": _duration_range(1, 15),
        "default_duration": 5,
        "option_key": "resolution",
        "options": ("720p", "480p"),
        "default_option": "720p",
        "extra_params": {},
    },
    "doubao-seedance-2-5-cankaosheng": {
        "label": "SD 2.5 参考生",
        "modes": ("image_to_video",),
        "min_images": 1,
        "max_images": 30,
        "aspect_ratios": ("adaptive", "16:9", "4:3", "1:1", "3:4", "9:16", "21:9"),
        "durations": ("auto", *_duration_range(4, 30)),
        "default_duration": "auto",
        "option_key": "resolution",
        "options": ("480p", "720p"),
        "default_option": "480p",
        "extra_params": {"web_search": False},
    },
}

DEFAULT_VIDEO_GENERATION_MODEL = "hailuo-h3"


def _clean(value: object, default: str = "", limit: int = 12000) -> str:
    text = str(value if value is not None else default).strip()
    return (text or default)[:limit]


def _positive_int(value: object, default: int, minimum: int = 1) -> int:
    try:
        normalized = int(value)
    except (OverflowError, TypeError, ValueError):
        normalized = default
    return max(minimum, normalized)


def _pick_allowed(value: object, allowed: tuple[str, ...], default: str, field_name: str, model_label: str) -> str:
    text = _clean(value)
    if not text or text.lower() in {"standard", "high", "auto"}:
        return default
    by_lower = {item.lower(): item for item in allowed}
    if text.lower() in by_lower:
        return by_lower[text.lower()]
    raise ValueError(f"{model_label} does not support {field_name}: {text}")


def normalize_video_generation_options(
    model: str,
    *,
    aspect_ratio: object = "16:9",
    duration_secs: object = 5,
    quality: object = "",
    resolution: object = "",
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    model_id = _clean(model, limit=191)
    spec = VIDEO_GENERATION_MODEL_SPECS.get(model_id)
    if spec is None:
        legacy_params = {
            "aspect_ratio": _clean(aspect_ratio, "16:9", 40),
            "duration": str(_positive_int(duration_secs, 5, 1)),
            "quality": _clean(quality, "standard", 80),
        }
        legacy_resolution = _clean(resolution, limit=80)
        if legacy_resolution:
            legacy_params["resolution"] = legacy_resolution
        if isinstance(params, dict):
            legacy_params.update({key: value for key, value in params.items() if _clean(key)})
        return {
            "aspect_ratio": legacy_params["aspect_ratio"],
            "duration_secs": int(legacy_params["duration"]),
            "quality": legacy_params["quality"],
            "resolution": legacy_params.get("resolution", ""),
            "params": legacy_params,
            "modes": ("text_to_video", "image_to_video"),
            "min_images": 1,
            "max_images": 30,
            "image_input_kind": "reference",
            "model_label": model_id,
        }

    model_label = str(spec["label"])
    aspect_ratios = tuple(spec["aspect_ratios"])
    include_aspect_ratio = bool(spec.get("include_aspect_ratio", True))
    aspect = (
        _pick_allowed(aspect_ratio, aspect_ratios, aspect_ratios[0], "aspect_ratio", model_label)
        if include_aspect_ratio
        else aspect_ratios[0]
    )

    durations = tuple(spec["durations"])
    default_duration = spec["default_duration"]
    duration_text = _clean(duration_secs, str(default_duration), 40)
    if duration_text.lower() == "auto":
        duration: int | str = "auto"
    else:
        try:
            duration = int(duration_text)
        except (TypeError, ValueError):
            duration = default_duration
    if duration not in durations:
        numeric_durations = [int(item) for item in durations if str(item).isdigit()]
        range_text = f"{numeric_durations[0]}-{numeric_durations[-1]} seconds"
        if "auto" in durations:
            range_text += " or auto"
        raise ValueError(f"{model_label} supports duration {range_text}")

    options = tuple(spec["options"])
    requested_option = _clean(resolution) or _clean(quality)
    selected_option = _pick_allowed(requested_option, options, str(spec["default_option"]), str(spec["option_key"]), model_label)

    provider_params: dict[str, Any] = {"duration": str(duration)}
    if include_aspect_ratio:
        provider_params["aspect_ratio"] = aspect
    provider_params[str(spec["option_key"])] = selected_option
    provider_params.update(dict(spec.get("extra_params") or {}))

    return {
        "aspect_ratio": aspect,
        "duration_secs": duration,
        "quality": selected_option,
        "resolution": selected_option if spec["option_key"] == "resolution" else "",
        "params": provider_params,
        "modes": tuple(spec["modes"]),
        "min_images": int(spec["min_images"]),
        "max_images": int(spec["max_images"]),
        "image_input_kind": str(spec.get("image_input_kind") or ("none" if int(spec["max_images"]) == 0 else "reference")),
        "model_label": model_label,
    }


def public_video_generation_models() -> list[dict[str, Any]]:
    return [
        {
            "id": model_id,
            "label": str(spec["label"]),
            "modes": list(spec["modes"]),
            "min_images": int(spec["min_images"]),
            "max_images": int(spec["max_images"]),
            "image_input_kind": str(spec.get("image_input_kind") or ("none" if int(spec["max_images"]) == 0 else "reference")),
            "aspect_ratios": list(spec["aspect_ratios"]),
            "durations": list(spec["durations"]),
            "default_duration": spec["default_duration"],
            "option_key": str(spec["option_key"]),
            "options": list(spec["options"]),
            "default_option": str(spec["default_option"]),
        }
        for model_id, spec in VIDEO_GENERATION_MODEL_SPECS.items()
    ]
