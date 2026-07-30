from __future__ import annotations

import math
import re

GRID = 16
MIN_TOTAL_PIXELS = 655_360
MAX_TOTAL_PIXELS = 8_294_400
MAX_ASPECT_RATIO = 3.0

SIZE_RE = re.compile(r"^\s*(\d+)\s*x\s*(\d+)\s*$", re.IGNORECASE)
BUSINESS_SIZE_ALIASES = {
    "750x3000": "768x2304",
    "750x6000": "1024x3072",
    "800x800": "816x816",
}


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
        return str(size).strip()

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
