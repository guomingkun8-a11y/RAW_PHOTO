from __future__ import annotations

import math
import re

GRID = 16
MIN_TOTAL_PIXELS = 655_360
MAX_TOTAL_PIXELS = 8_294_400
MAX_ASPECT_RATIO = 3.0
MEDIA_MAX_SIDE = 3_840

SIZE_RE = re.compile(r"^\s*(\d+)\s*x\s*(\d+)\s*$", re.IGNORECASE)
BUSINESS_SIZE_ALIASES = {
    "750x3000": "768x2304",
    "750x6000": "1024x3072",
    "800x800": "816x816",
}
CANVAS_IMAGE_SIZE_PIXELS = {
    "0.5k": 512,
    "1k": 1024,
    "2k": 2048,
    "4k": 4096,
}
CANVAS_ASPECT_RE = re.compile(r"^\s*(\d+)\s*:\s*(\d+)\s*$")


def _snap(value: float, mode: str = "nearest") -> int:
    if mode == "ceil":
        snapped = math.ceil(value / GRID) * GRID
    elif mode == "floor":
        snapped = math.floor(value / GRID) * GRID
    else:
        snapped = int(value / GRID + 0.5) * GRID
    return max(GRID, snapped)


def _clamp_aspect(width: int, height: int) -> tuple[int, int]:
    if width > height * MAX_ASPECT_RATIO:
        width = height * int(MAX_ASPECT_RATIO)
    elif height > width * MAX_ASPECT_RATIO:
        height = width * int(MAX_ASPECT_RATIO)
    return width, height


def normalize_image_size(size: object) -> str | None:
    text = str(size or "").strip().lower()
    if not text:
        return None
    if text == "auto":
        return "auto"

    match = SIZE_RE.match(text)
    if not match:
        return None

    original_width = int(match.group(1))
    original_height = int(match.group(2))
    alias = BUSINESS_SIZE_ALIASES.get(f"{original_width}x{original_height}")
    if alias:
        return alias

    width = _snap(original_width)
    height = _snap(original_height)
    width, height = _clamp_aspect(width, height)

    for _ in range(4):
        area = width * height
        if area < MIN_TOTAL_PIXELS:
            scale = math.sqrt(MIN_TOTAL_PIXELS / area)
            width = _snap(width * scale, "ceil")
            height = _snap(height * scale, "ceil")
            width, height = _clamp_aspect(width, height)
            continue
        if area > MAX_TOTAL_PIXELS:
            scale = math.sqrt(MAX_TOTAL_PIXELS / area)
            width = _snap(width * scale, "floor")
            height = _snap(height * scale, "floor")
            width, height = _clamp_aspect(width, height)
            continue
        break

    return f"{width}x{height}"


def canvas_expected_size(aspect_ratio: object, image_size: object) -> str | None:
    """Return the target canvas for the canvas image controls.

    The selected tier is used as the short edge so changing the aspect ratio
    does not silently reduce the requested resolution. The result is kept as
    a separate target so providers can receive the selected ratio and tier in
    the concrete form required by their own API contract.
    """

    ratio_match = CANVAS_ASPECT_RE.match(str(aspect_ratio or "").strip())
    tier = CANVAS_IMAGE_SIZE_PIXELS.get(str(image_size or "").strip().lower())
    if ratio_match is None or tier is None:
        return None

    ratio_width = max(1, int(ratio_match.group(1)))
    ratio_height = max(1, int(ratio_match.group(2)))
    if ratio_width >= ratio_height:
        width = round(tier * ratio_width / ratio_height)
        height = tier
    else:
        width = tier
        height = round(tier * ratio_height / ratio_width)
    return f"{max(1, width)}x{max(1, height)}"


def canvas_media_request_size(aspect_ratio: object, image_size: object) -> str | None:
    """Return a media API size that preserves the canvas ratio and tier."""

    ratio_match = CANVAS_ASPECT_RE.match(str(aspect_ratio or "").strip())
    if ratio_match is None:
        return None
    ratio_width = max(1, int(ratio_match.group(1)))
    ratio_height = max(1, int(ratio_match.group(2)))
    ratio = ratio_width / ratio_height
    if ratio > MAX_ASPECT_RATIO or ratio < 1 / MAX_ASPECT_RATIO:
        return None

    expected = canvas_expected_size(aspect_ratio, image_size)
    normalized = normalize_image_size(expected)
    match = SIZE_RE.match(str(normalized or ""))
    if match is None:
        return None
    width = int(match.group(1))
    height = int(match.group(2))
    longest = max(width, height)
    if longest > MEDIA_MAX_SIDE:
        if ratio_width >= ratio_height:
            width = MEDIA_MAX_SIDE
            height = _snap(MEDIA_MAX_SIDE * ratio_height / ratio_width)
        else:
            width = _snap(MEDIA_MAX_SIDE * ratio_width / ratio_height)
            height = MEDIA_MAX_SIDE
    return f"{width}x{height}"
