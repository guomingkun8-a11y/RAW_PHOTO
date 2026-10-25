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
    "seedance-2.0-guanfang": {
        "label": "Seedance 2.0 官方全能参考",
        "provider_model_versions": {
            "Mini": "doubao-seedance-2-0-mini-260615",
            "快速": "doubao-seedance-2-0-fast-260128",
            "标准": "doubao-seedance-2-0-260128",
        },
        "modes": ("text_to_video", "image_to_video", "video_to_video"),
        "min_images": 0,
        "max_images": 9,
        "max_videos": 3,
        "max_audios": 3,
        "min_media_inputs": 0,
        "allow_audio_only": False,
        "image_input_kind": "reference",
        "image_input_param": "image_url",
        "video_input_kind": "reference",
        "video_input_param": "video_url",
        "video_input_shape": "list",
        "audio_input_kind": "reference",
        "audio_input_param": "audio_url",
        "audio_input_shape": "list",
        "request_mode_param": "mode",
        "request_mode_options": ("shouweizhen", "cankaosheng"),
        "request_mode_default": "cankaosheng",
        "request_mode_labels": {"shouweizhen": "首尾帧", "cankaosheng": "参考生"},
        "reference_mode_param": "_quan_neng_mode",
        "reference_mode_options": ("quan_neng", "edit", "extend"),
        "reference_mode_default": "quan_neng",
        "reference_mode_labels": {"quan_neng": "全能参考", "edit": "编辑视频", "extend": "延长视频"},
        "media_by_request_mode": {
            "shouweizhen": {
                "image_input_kind": "first_last_frame",
                "image_input_param": "images",
                "max_images": 2,
                "max_videos": 0,
                "max_audios": 0,
                "video_input_kind": "none",
                "audio_input_kind": "none",
            },
            "cankaosheng": {
                "image_input_kind": "reference",
                "image_input_param": "image_url",
                "max_images": 9,
                "max_videos": 3,
                "max_audios": 3,
                "video_input_kind": "reference",
                "audio_input_kind": "reference",
            },
        },
        "aspect_ratios": ("adaptive", "16:9", "4:3", "1:1", "3:4", "9:16", "21:9"),
        "durations": ("auto", *_duration_range(4, 15)),
        "default_duration": "auto",
        "option_key": "resolution",
        "options": ("480p", "720p", "1080p", "4K"),
        "default_option": "720p",
        "secondary_option_key": "version",
        "secondary_options": ("Mini", "快速", "标准"),
        "secondary_default_option": "标准",
        "options_by_secondary": {
            "Mini": ("480p", "720p"),
            "快速": ("480p", "720p"),
            "标准": ("480p", "720p", "1080p", "4K"),
        },
        "extra_params": {},
    },
    "doubao-seedance-2-5-quannengcankao": {
        "label": "SD 2.5 全能参考",
        "modes": ("text_to_video", "image_to_video", "video_to_video"),
        "min_images": 0,
        "max_images": 30,
        "max_videos": 10,
        "max_audios": 10,
        "min_media_inputs": 1,
        "allow_audio_only": True,
        "image_input_kind": "reference",
        "image_input_param": "image_url",
        "video_input_kind": "reference",
        "video_input_param": "video_url",
        "video_input_shape": "list",
        "audio_input_kind": "reference",
        "audio_input_param": "audio_url",
        "audio_input_shape": "list",
        "aspect_ratios": ("adaptive", "16:9", "4:3", "1:1", "3:4", "9:16", "21:9"),
        "durations": ("auto", *_duration_range(4, 30)),
        "default_duration": "auto",
        "option_key": "resolution",
        "options": ("480p", "720p", "1080p"),
        "default_option": "480p",
        "extra_params": {},
    },
    "wan3.0-video-quannengcankao": {
        "label": "万相 3.0 全能参考",
        "modes": ("text_to_video", "image_to_video", "video_to_video"),
        "min_images": 0,
        "max_images": 10,
        "max_videos": 5,
        "max_audios": 5,
        "min_media_inputs": 0,
        "allow_audio_only": True,
        "image_input_kind": "reference",
        "image_input_param": "image_url",
        "video_input_kind": "reference",
        "video_input_param": "video_url",
        "video_input_shape": "list",
        "audio_input_kind": "reference",
        "audio_input_param": "audio_url",
        "audio_input_shape": "list",
        "aspect_ratio_param": "ratio",
        "aspect_ratios": ("adaptive", "16:9", "9:16", "1:1", "4:3", "3:4"),
        "durations": ("auto", *_duration_range(2, 30)),
        "default_duration": 5,
        "option_key": "resolution",
        "options": ("480P", "720P", "1080P"),
        "default_option": "720P",
        "secondary_option_key": "version",
        "secondary_options": ("standard", "prime"),
        "secondary_default_option": "standard",
        "extra_params": {"audio": "true"},
    },
    "hailuo-h3-quannengcankao": {
        "label": "海螺 H3 全能参考",
        "modes": ("image_to_video", "video_to_video"),
        "min_images": 0,
        "max_images": 9,
        "max_videos": 3,
        "max_audios": 3,
        "min_media_inputs": 1,
        "allow_audio_only": False,
        "image_input_kind": "reference",
        "image_input_param": "image_url",
        "video_input_kind": "reference",
        "video_input_param": "video_url",
        "video_input_shape": "list",
        "audio_input_kind": "reference",
        "audio_input_param": "audio_url",
        "audio_input_shape": "list",
        "aspect_ratios": ("adaptive", "16:9", "9:16", "1:1", "4:3", "3:4", "21:9"),
        "durations": _duration_range(4, 15),
        "default_duration": 5,
        "option_key": "resolution",
        "options": ("768P", "1080P", "2K", "4K"),
        "default_option": "768P",
        "extra_params": {},
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
    "wan2.7-cankaosheng": {
        "label": "万相 2.7 参考生",
        "modes": ("text_to_video", "image_to_video", "video_to_video"),
        "min_images": 0,
        "max_images": 5,
        "max_videos": 1,
        "video_input_kind": "reference",
        "video_input_param": "reference_video",
        "video_input_shape": "scalar",
        "image_input_param": "reference_urls",
        "aspect_ratio_param": "ratio",
        "mode_param": "_mode",
        "mode_value": "wan2.7-r2v",
        "aspect_ratios": ("16:9", "9:16", "1:1", "4:3", "3:4"),
        "durations": (3, 6, 9, 12, 15),
        "default_duration": 3,
        "option_key": "resolution",
        "options": ("720P", "1080P"),
        "default_option": "720P",
        "extra_params": {"prompt_extend": False},
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


def _request_mode(spec: dict[str, Any], params: dict[str, Any] | None, model_label: str) -> str:
    mode_param = _clean(spec.get("request_mode_param"), "", 80)
    options = tuple(str(item) for item in (spec.get("request_mode_options") or ()))
    if not mode_param or not options:
        return ""
    return _pick_allowed(
        params.get(mode_param) if isinstance(params, dict) else None,
        options,
        _clean(spec.get("request_mode_default"), options[0], 80),
        mode_param,
        model_label,
    )


def _media_spec(spec: dict[str, Any], request_mode: str) -> dict[str, Any]:
    resolved = {
        "image_input_kind": str(spec.get("image_input_kind") or ("none" if int(spec.get("max_images") or 0) == 0 else "reference")),
        "image_input_param": _clean(spec.get("image_input_param"), "images", 80),
        "max_images": int(spec.get("max_images") or 0),
        "video_input_kind": _clean(spec.get("video_input_kind"), "none", 40),
        "video_input_param": _clean(spec.get("video_input_param"), "", 80),
        "video_input_shape": _clean(spec.get("video_input_shape"), "list", 20),
        "max_videos": int(spec.get("max_videos") or 0),
        "audio_input_kind": _clean(spec.get("audio_input_kind"), "none", 40),
        "audio_input_param": _clean(spec.get("audio_input_param"), "", 80),
        "audio_input_shape": _clean(spec.get("audio_input_shape"), "list", 20),
        "max_audios": int(spec.get("max_audios") or 0),
    }
    overrides = spec.get("media_by_request_mode")
    if isinstance(overrides, dict) and isinstance(overrides.get(request_mode), dict):
        resolved.update(overrides[request_mode])
    return resolved


def video_generation_media_spec(model: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the input field mapping after model-specific mode selection."""
    spec = VIDEO_GENERATION_MODEL_SPECS.get(_clean(model, limit=191))
    if spec is None:
        return _media_spec({}, "")
    model_label = str(spec["label"])
    return _media_spec(spec, _request_mode(spec, params or {}, model_label))


def video_generation_provider_model(model: str, params: dict[str, Any] | None = None) -> str:
    """Resolve a UI model/version pair to the provider's actual model id."""
    model_id = _clean(model, limit=191)
    spec = VIDEO_GENERATION_MODEL_SPECS.get(model_id)
    versions = spec.get("provider_model_versions") if isinstance(spec, dict) else None
    if not isinstance(versions, dict):
        return model_id
    version = _clean(
        (params or {}).get("version"),
        _clean(spec.get("secondary_default_option"), "标准", 80),
        80,
    )
    return _clean(versions.get(version), model_id, 191)


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
    params_source = params if isinstance(params, dict) else {}
    request_mode = _request_mode(spec, params_source, model_label)
    media_spec = _media_spec(spec, request_mode)
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

    secondary_option_key = _clean(spec.get("secondary_option_key"), "", 80)
    secondary_options = tuple(str(item) for item in (spec.get("secondary_options") or ()))
    secondary_default = _clean(spec.get("secondary_default_option"), "", 80)
    selected_secondary = ""
    if secondary_option_key and secondary_options:
        selected_secondary = _pick_allowed(
            params_source.get(secondary_option_key),
            secondary_options,
            secondary_default or secondary_options[0],
            secondary_option_key,
            model_label,
        )

    options = tuple(spec["options"])
    options_by_secondary = spec.get("options_by_secondary")
    if selected_secondary and isinstance(options_by_secondary, dict):
        configured_options = options_by_secondary.get(selected_secondary)
        if isinstance(configured_options, (list, tuple)) and configured_options:
            options = tuple(str(item) for item in configured_options)
    requested_option = _clean(resolution) or _clean(quality)
    selected_option = _pick_allowed(requested_option, options, str(spec["default_option"]), str(spec["option_key"]), model_label)

    provider_params: dict[str, Any] = {"duration": str(duration)}
    if include_aspect_ratio:
        provider_params[str(spec.get("aspect_ratio_param") or "aspect_ratio")] = aspect
    provider_params[str(spec["option_key"])] = selected_option
    if secondary_option_key and selected_secondary:
        provider_params[secondary_option_key] = selected_secondary
    mode_param = _clean(spec.get("mode_param"))
    mode_value = _clean(spec.get("mode_value"))
    if mode_param and mode_value:
        provider_params[mode_param] = mode_value
    request_mode_param = _clean(spec.get("request_mode_param"), "", 80)
    if request_mode_param and request_mode:
        provider_params[request_mode_param] = request_mode
    reference_mode_param = _clean(spec.get("reference_mode_param"), "", 80)
    reference_mode_options = tuple(str(item) for item in (spec.get("reference_mode_options") or ()))
    if reference_mode_param and reference_mode_options and request_mode == "cankaosheng":
        provider_params[reference_mode_param] = _pick_allowed(
            params_source.get(reference_mode_param),
            reference_mode_options,
            _clean(spec.get("reference_mode_default"), reference_mode_options[0], 80),
            reference_mode_param,
            model_label,
        )
    provider_params.update(dict(spec.get("extra_params") or {}))

    return {
        "aspect_ratio": aspect,
        "duration_secs": duration,
        "quality": selected_option,
        "resolution": selected_option if spec["option_key"] == "resolution" else "",
        "params": provider_params,
        "modes": tuple(spec["modes"]),
        "min_images": int(spec["min_images"]),
        "max_images": int(media_spec["max_images"]),
        "image_input_kind": str(media_spec["image_input_kind"]),
        "image_input_param": str(media_spec["image_input_param"]),
        "video_input_kind": str(media_spec["video_input_kind"]),
        "video_input_param": str(media_spec["video_input_param"]),
        "video_input_shape": str(media_spec["video_input_shape"]),
        "max_videos": int(media_spec["max_videos"]),
        "audio_input_kind": str(media_spec["audio_input_kind"]),
        "audio_input_param": str(media_spec["audio_input_param"]),
        "audio_input_shape": str(media_spec["audio_input_shape"]),
        "max_audios": int(media_spec["max_audios"]),
        "min_media_inputs": int(spec.get("min_media_inputs") or 0),
        "allow_audio_only": bool(spec.get("allow_audio_only")),
        "secondary_option_key": secondary_option_key,
        "secondary_options": list(secondary_options),
        "secondary_default_option": secondary_default,
        "options_by_secondary": {
            str(key): [str(item) for item in value]
            for key, value in (
                options_by_secondary.items()
                if isinstance(options_by_secondary, dict)
                else []
            )
            if isinstance(value, (list, tuple))
        },
        "aspect_ratio_param": _clean(spec.get("aspect_ratio_param"), "aspect_ratio", 80),
        "mode_param": mode_param,
        "mode_value": mode_value,
        "request_mode_param": request_mode_param,
        "request_mode": request_mode,
        "request_mode_options": [str(item) for item in (spec.get("request_mode_options") or ())],
        "request_mode_default": _clean(spec.get("request_mode_default"), "", 80),
        "request_mode_labels": dict(spec.get("request_mode_labels") or {}),
        "reference_mode_param": reference_mode_param,
        "reference_mode": provider_params.get(reference_mode_param, "") if reference_mode_param else "",
        "reference_mode_options": [str(item) for item in (spec.get("reference_mode_options") or ())],
        "reference_mode_default": _clean(spec.get("reference_mode_default"), "", 80),
        "reference_mode_labels": dict(spec.get("reference_mode_labels") or {}),
        "model_label": model_label,
    }


def public_video_generation_models() -> list[dict[str, Any]]:
    def public_media_modes(spec: dict[str, Any]) -> dict[str, dict[str, Any]]:
        raw_modes = spec.get("media_by_request_mode")
        if not isinstance(raw_modes, dict):
            return {}
        result: dict[str, dict[str, Any]] = {}
        for mode, media in raw_modes.items():
            if not isinstance(media, dict):
                continue
            result[str(mode)] = {
                key: value
                for key, value in media.items()
                if key in {
                    "image_input_kind",
                    "image_input_param",
                    "max_images",
                    "video_input_kind",
                    "video_input_param",
                    "video_input_shape",
                    "max_videos",
                    "audio_input_kind",
                    "audio_input_param",
                    "audio_input_shape",
                    "max_audios",
                }
            }
        return result

    return [
        {
            "id": model_id,
            "label": str(spec["label"]),
            "modes": list(spec["modes"]),
            "min_images": int(spec["min_images"]),
            "max_images": int(spec["max_images"]),
            "image_input_kind": str(spec.get("image_input_kind") or ("none" if int(spec["max_images"]) == 0 else "reference")),
            "image_input_param": _clean(spec.get("image_input_param"), "images", 80),
            "video_input_kind": _clean(spec.get("video_input_kind"), "none", 40),
            "video_input_param": _clean(spec.get("video_input_param"), "", 80),
            "video_input_shape": _clean(spec.get("video_input_shape"), "list", 20),
            "max_videos": int(spec.get("max_videos") or 0),
            "audio_input_kind": _clean(spec.get("audio_input_kind"), "none", 40),
            "audio_input_param": _clean(spec.get("audio_input_param"), "", 80),
            "audio_input_shape": _clean(spec.get("audio_input_shape"), "list", 20),
            "max_audios": int(spec.get("max_audios") or 0),
            "min_media_inputs": int(spec.get("min_media_inputs") or 0),
            "allow_audio_only": bool(spec.get("allow_audio_only")),
            "secondary_option_key": _clean(spec.get("secondary_option_key"), "", 80),
            "secondary_options": [str(item) for item in (spec.get("secondary_options") or ())],
            "secondary_default_option": _clean(spec.get("secondary_default_option"), "", 80),
            "options_by_secondary": {
                str(key): [str(item) for item in value]
                for key, value in (
                    spec.get("options_by_secondary", {}).items()
                    if isinstance(spec.get("options_by_secondary"), dict)
                    else []
                )
                if isinstance(value, (list, tuple))
            },
            "aspect_ratio_param": _clean(spec.get("aspect_ratio_param"), "aspect_ratio", 80),
            "mode_param": _clean(spec.get("mode_param"), "", 80),
            "mode_value": _clean(spec.get("mode_value"), "", 80),
            "request_mode_param": _clean(spec.get("request_mode_param"), "", 80),
            "request_mode_options": [str(item) for item in (spec.get("request_mode_options") or ())],
            "request_mode_default": _clean(spec.get("request_mode_default"), "", 80),
            "request_mode_labels": dict(spec.get("request_mode_labels") or {}),
            "media_by_request_mode": public_media_modes(spec),
            "reference_mode_param": _clean(spec.get("reference_mode_param"), "", 80),
            "reference_mode_options": [str(item) for item in (spec.get("reference_mode_options") or ())],
            "reference_mode_default": _clean(spec.get("reference_mode_default"), "", 80),
            "reference_mode_labels": dict(spec.get("reference_mode_labels") or {}),
            "aspect_ratios": list(spec["aspect_ratios"]),
            "durations": list(spec["durations"]),
            "default_duration": spec["default_duration"],
            "option_key": str(spec["option_key"]),
            "options": list(spec["options"]),
            "default_option": str(spec["default_option"]),
        }
        for model_id, spec in VIDEO_GENERATION_MODEL_SPECS.items()
    ]
