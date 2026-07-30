from __future__ import annotations

import io
import statistics
from collections import deque

from PIL import Image, ImageFilter, ImageOps


ImageInput = tuple[bytes, str, str]

ECOMMERCE_PRESERVE_PROMPT = (
    "Product subject preservation mode. Treat the first reference image as the fixed product reference. "
    "Keep the exact product shape, package structure, label layout, logo, brand name, product name, "
    "specifications, icons, and every visible character unchanged. Do not invent, rewrite, translate, "
    "stylize, replace, blur, crop, cover, duplicate, or repaint any text on the product. "
    "Only change the surrounding environment: background, surface, props, lighting, reflections, "
    "shadows, atmosphere, and camera framing. The product must look naturally photographed in the new "
    "scene, with believable contact shadows, reflections, perspective, and color harmony. If preserving "
    "text conflicts with the requested scene, preserving the original product identity and text has "
    "higher priority.\n\n"
    "User request:"
)


def build_preserve_subject_prompt(prompt: str) -> str:
    text = str(prompt or "").strip()
    return f"{ECOMMERCE_PRESERVE_PROMPT}\n{text}" if text else ECOMMERCE_PRESERVE_PROMPT


def build_preserve_subject_mask(source_image: ImageInput) -> ImageInput | None:
    source = _open_rgba(source_image[0])
    subject_alpha = _extract_subject_alpha(source)
    if not subject_alpha.getbbox():
        return None

    protect_alpha = subject_alpha.filter(ImageFilter.MaxFilter(7)).filter(ImageFilter.GaussianBlur(1.0))
    mask = Image.new("RGBA", source.size, (255, 255, 255, 0))
    mask.putalpha(protect_alpha)
    buffer = io.BytesIO()
    mask.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue(), "preserve-subject-mask.png", "image/png"


def _open_rgba(payload: bytes) -> Image.Image:
    with Image.open(io.BytesIO(payload)) as image:
        return ImageOps.exif_transpose(image).convert("RGBA")


def _extract_subject_alpha(image: Image.Image) -> Image.Image:
    alpha = image.getchannel("A")
    if alpha.getextrema()[0] < 245:
        return alpha.filter(ImageFilter.GaussianBlur(0.35))

    background = _estimate_background_alpha(image)
    return background.filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.GaussianBlur(0.8))


def _estimate_background_alpha(image: Image.Image) -> Image.Image:
    max_edge = 1024
    preview = image.convert("RGB")
    if max(preview.size) > max_edge:
        preview.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)

    width, height = preview.size
    pixels = preview.load()
    border_pixels = _border_pixels(preview)
    bg_color = tuple(int(statistics.median(channel)) for channel in zip(*border_pixels))
    spread = statistics.median(_distance(pixel, bg_color) for pixel in border_pixels)
    threshold = int(max(34, min(82, spread * 2.2 + 24)))

    background = bytearray(width * height)
    visited = bytearray(width * height)
    queue: deque[tuple[int, int]] = deque()

    def add_if_background(x: int, y: int) -> None:
        index = y * width + x
        if visited[index]:
            return
        visited[index] = 1
        if _distance(pixels[x, y], bg_color) <= threshold:
            background[index] = 1
            queue.append((x, y))

    for x in range(width):
        add_if_background(x, 0)
        add_if_background(x, height - 1)
    for y in range(height):
        add_if_background(0, y)
        add_if_background(width - 1, y)

    while queue:
        x, y = queue.popleft()
        if x > 0:
            add_if_background(x - 1, y)
        if x + 1 < width:
            add_if_background(x + 1, y)
        if y > 0:
            add_if_background(x, y - 1)
        if y + 1 < height:
            add_if_background(x, y + 1)

    mask = Image.new("L", (width, height), 0)
    mask.putdata([0 if value else 255 for value in background])
    if mask.size != image.size:
        mask = mask.resize(image.size, Image.Resampling.LANCZOS)
    return mask


def _border_pixels(image: Image.Image) -> list[tuple[int, int, int]]:
    width, height = image.size
    pixels = image.load()
    step = max(1, min(width, height) // 80)
    items: list[tuple[int, int, int]] = []
    for x in range(0, width, step):
        items.append(pixels[x, 0])
        items.append(pixels[x, height - 1])
    for y in range(0, height, step):
        items.append(pixels[0, y])
        items.append(pixels[width - 1, y])
    return items or [(255, 255, 255)]


def _distance(left: tuple[int, int, int], right: tuple[int, int, int]) -> float:
    return sum((int(left[index]) - int(right[index])) ** 2 for index in range(3)) ** 0.5

