from __future__ import annotations

import base64
import io
import time
from typing import Any
from pathlib import Path
from urllib.parse import urlparse

from curl_cffi import requests
from PIL import Image, ImageFilter, ImageOps

from services.image.image_storage_service import ImageStorageService, image_storage_service
from services.platform.proxy_service import proxy_settings

IMAGE_REF_MARKER = "__image_ref__"
LEGACY_IMAGE_MARKER = "__image_input__"
RESULT_DOWNLOAD_ATTEMPTS = 5
ASPECT_RATIO_TOLERANCE = 0.015


class ImageAspectRatioMismatchError(RuntimeError):
    def __init__(self, *, expected: tuple[int, int], actual: tuple[int, int]):
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"上游返回图片比例 {actual[0]}:{actual[1]} 与请求画布 {expected[0]}:{expected[1]} 不一致"
        )


def _is_binary_tuple(value: object) -> bool:
    return isinstance(value, tuple) and len(value) == 3 and isinstance(value[0], (bytes, bytearray))


def _decode_legacy_image(value: dict[str, object]) -> tuple[bytes, str, str]:
    return (
        base64.b64decode(str(value.get("data") or "")),
        str(value.get("filename") or "image.png"),
        str(value.get("mime_type") or "image/png"),
    )


def _asset_ref(
    image_data: bytes,
    filename: str,
    mime_type: str,
    *,
    owner_id: str,
    task_id: str,
    asset_index: str,
    asset_type: str,
    storage: ImageStorageService,
) -> dict[str, str]:
    stored = storage.save_task_asset(
        image_data,
        owner_id=owner_id,
        task_id=task_id,
        asset_index=asset_index,
        asset_type=asset_type,
        filename=filename,
        mime_type=mime_type,
    )
    return {
        IMAGE_REF_MARKER: "1",
        "rel": stored.rel,
        "filename": filename or "image.png",
        "mime_type": mime_type or "image/png",
    }


def prepare_task_payload(
    value: Any,
    *,
    owner_id: str,
    task_id: str,
    storage: ImageStorageService | None = None,
) -> Any:
    """Replace binary task inputs and legacy refs with object-storage references."""

    storage_service = storage or image_storage_service
    counter = 0

    def walk(item: Any, path: str) -> Any:
        nonlocal counter
        if _is_binary_tuple(item):
            image_data, filename, mime_type = item
            counter += 1
            asset_type = "task_mask" if ".mask" in path else "task_input"
            return _asset_ref(
                bytes(image_data),
                str(filename or "image.png"),
                str(mime_type or "image/png"),
                owner_id=owner_id,
                task_id=task_id,
                asset_index=f"{counter}:{path}",
                asset_type=asset_type,
                storage=storage_service,
            )
        if isinstance(item, dict):
            if item.get(LEGACY_IMAGE_MARKER) == "1":
                image_data, filename, mime_type = _decode_legacy_image(item)
                counter += 1
                return _asset_ref(
                    image_data,
                    filename,
                    mime_type,
                    owner_id=owner_id,
                    task_id=task_id,
                    asset_index=f"{counter}:{path}",
                    asset_type="task_input",
                    storage=storage_service,
                )
            return {str(key): walk(child, f"{path}.{key}") for key, child in item.items()}
        if isinstance(item, list):
            return [walk(child, f"{path}[{index}]") for index, child in enumerate(item)]
        return item

    return walk(value, "payload")


def decode_task_payload(value: Any, storage: ImageStorageService | None = None) -> Any:
    """Resolve object-storage refs while retaining compatibility with legacy Base64 payloads."""

    storage_service = storage or image_storage_service
    if isinstance(value, dict):
        if value.get(IMAGE_REF_MARKER) == "1":
            return (
                storage_service.get_bytes(str(value.get("rel") or "")),
                str(value.get("filename") or "image.png"),
                str(value.get("mime_type") or "image/png"),
            )
        if value.get(LEGACY_IMAGE_MARKER) == "1":
            return _decode_legacy_image(value)
        return {str(key): decode_task_payload(child, storage_service) for key, child in value.items()}
    if isinstance(value, list):
        return [decode_task_payload(child, storage_service) for child in value]
    return value


def contains_inline_assets(value: Any) -> bool:
    if _is_binary_tuple(value):
        return True
    if isinstance(value, dict):
        if value.get(LEGACY_IMAGE_MARKER) == "1":
            return True
        return any(contains_inline_assets(child) for child in value.values())
    if isinstance(value, list):
        return any(contains_inline_assets(child) for child in value)
    return False


def download_result_image(url: str) -> tuple[bytes, str, str]:
    """Download an upstream result while it may still be becoming available."""

    source_url = str(url or "").strip()
    if not source_url.lower().startswith(("http://", "https://")):
        raise RuntimeError("image result URL must be HTTP or HTTPS")

    last_error: Exception | None = None
    for attempt in range(RESULT_DOWNLOAD_ATTEMPTS):
        try:
            response = requests.get(
                source_url,
                headers={
                    "Accept": "image/*,*/*;q=0.8",
                    "User-Agent": "gmkraw image worker",
                },
                timeout=60,
                allow_redirects=True,
                **proxy_settings.build_session_kwargs(),
            )
            if not 200 <= response.status_code < 300:
                raise RuntimeError(f"image result download failed: HTTP {response.status_code}")
            payload = bytes(response.content)
            if not payload:
                raise RuntimeError("image result download returned empty content")
            with Image.open(io.BytesIO(payload)) as image:
                image.verify()

            mime_type = str(response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
            if not mime_type.startswith("image/"):
                mime_type = "image/png"
            suffix = Path(urlparse(source_url).path).suffix.lower()
            if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif"}:
                suffix = ".jpg" if mime_type in {"image/jpeg", "image/jpg"} else ".png"
            filename = Path(urlparse(source_url).path).name or f"generated-result{suffix}"
            if Path(filename).suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif"}:
                filename = f"{filename}{suffix}"
            return payload, filename, mime_type
        except Exception as exc:
            last_error = exc
            if attempt + 1 < RESULT_DOWNLOAD_ATTEMPTS:
                time.sleep(min(8.0, 0.8 * (2**attempt)))

    raise RuntimeError(str(last_error) or "image result download failed") from last_error


def _target_dimensions(size: object) -> tuple[int, int] | None:
    text = str(size or "").strip().lower()
    if not text or text == "auto" or "x" not in text:
        return None
    width_text, height_text = text.split("x", 1)
    try:
        width = int(width_text.strip())
        height = int(height_text.strip())
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    return width, height


def _aspect_ratio_matches(actual: tuple[int, int], expected: tuple[int, int]) -> bool:
    actual_ratio = actual[0] / actual[1]
    expected_ratio = expected[0] / expected[1]
    return abs(actual_ratio - expected_ratio) / expected_ratio <= ASPECT_RATIO_TOLERANCE


def _extend_image_to_canvas(image_data: bytes, target: tuple[int, int]) -> bytes:
    """Preserve the full image and extend its background to the requested canvas."""

    with Image.open(io.BytesIO(image_data)) as opened:
        source = ImageOps.exif_transpose(opened)
        has_alpha = "A" in source.getbands() or "transparency" in source.info
        source = source.convert("RGBA" if has_alpha else "RGB")

    # The fallback must never create a crop, even for the blurred backdrop.
    # Fit only the sharp foreground; the background uses every source pixel and
    # is allowed to stretch because it is intentionally blurred behind it.
    contained = ImageOps.contain(source, target, method=Image.Resampling.LANCZOS)
    if has_alpha:
        backdrop = Image.new("RGBA", target, (0, 0, 0, 0))
    else:
        backdrop = contained.resize(target, Image.Resampling.LANCZOS)
        blur_radius = max(18, round(max(target) * 0.04))
        backdrop = backdrop.filter(ImageFilter.GaussianBlur(radius=blur_radius))
        average_color = source.resize((1, 1), Image.Resampling.BOX).getpixel((0, 0))
        backdrop = Image.blend(backdrop, Image.new("RGB", target, average_color), 0.24)

    left = (target[0] - contained.width) // 2
    top = (target[1] - contained.height) // 2
    backdrop.paste(contained, (left, top))

    output = io.BytesIO()
    backdrop.save(output, format="PNG", optimize=True)
    return output.getvalue()


def _resize_image_to_canvas(image_data: bytes, target: tuple[int, int], *, extend_background: bool) -> bytes:
    if extend_background:
        return _extend_image_to_canvas(image_data, target)

    with Image.open(io.BytesIO(image_data)) as opened:
        source = ImageOps.exif_transpose(opened)
        has_alpha = "A" in source.getbands() or "transparency" in source.info
        source = source.convert("RGBA" if has_alpha else "RGB")
        resized = source.resize(target, Image.Resampling.LANCZOS)

    output = io.BytesIO()
    resized.save(output, format="PNG", optimize=True)
    return output.getvalue()


def normalize_task_result(
    data: list[Any],
    *,
    owner_id: str,
    task_id: str,
    base_url: str = "",
    storage: ImageStorageService | None = None,
    remote_loader=None,
    strict_remote: bool = False,
    expected_size: object = None,
    aspect_policy: str = "accept",
) -> list[Any]:
    """Move inline or remote results to object storage before persisting task JSON."""

    storage_service = storage or image_storage_service
    normalized: list[Any] = []
    target = _target_dimensions(expected_size)
    policy = str(aspect_policy or "accept").strip().lower()
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            normalized.append(item)
            continue
        if not item.get("b64_json") and not (remote_loader and item.get("url")):
            normalized.append(item)
            continue
        try:
            if item.get("b64_json"):
                image_data = base64.b64decode(str(item.get("b64_json")))
                filename = str(item.get("filename") or f"image-{index + 1}.png")
                mime_type = "image/png"
            else:
                image_data, filename, mime_type = remote_loader(str(item.get("url") or ""))
            actual: tuple[int, int] | None = None
            source_actual: tuple[int, int] | None = None
            aspect_corrected = False
            resolution_corrected = False
            if target is not None:
                with Image.open(io.BytesIO(image_data)) as image:
                    actual = image.size
                source_actual = actual
                aspect_matches = _aspect_ratio_matches(actual, target)
                if not aspect_matches and policy == "reject":
                    raise ImageAspectRatioMismatchError(expected=target, actual=actual)
                should_conform_aspect = not aspect_matches and policy == "conform"
                should_correct_resolution = aspect_matches and actual != target
                if should_conform_aspect or should_correct_resolution:
                    image_data = _resize_image_to_canvas(
                        image_data,
                        target,
                        extend_background=should_conform_aspect,
                    )
                    filename = f"{Path(filename).stem or f'image-{index + 1}'}-canvas.png"
                    mime_type = "image/png"
                    actual = target
                    aspect_corrected = should_conform_aspect
                    resolution_corrected = True
            stored = storage_service.save_task_asset(
                image_data,
                owner_id=owner_id,
                task_id=task_id,
                asset_index=f"result:{index}",
                asset_type="task_result",
                filename=filename,
                mime_type=mime_type,
                base_url=base_url,
            )
            replacement = {key: value for key, value in item.items() if key != "b64_json"}
            replacement["url"] = stored.url
            replacement["storage_rel"] = stored.rel
            if actual is not None:
                replacement["width"] = actual[0]
                replacement["height"] = actual[1]
                replacement["requested_size"] = f"{target[0]}x{target[1]}"
            if aspect_corrected and source_actual is not None:
                replacement["aspect_ratio_corrected"] = True
                replacement["source_width"] = source_actual[0]
                replacement["source_height"] = source_actual[1]
                replacement["aspect_correction"] = "contain_with_extended_background"
            if resolution_corrected and source_actual is not None:
                replacement["resolution_corrected"] = True
                replacement.setdefault("source_width", source_actual[0])
                replacement.setdefault("source_height", source_actual[1])
            normalized.append(replacement)
        except ImageAspectRatioMismatchError:
            raise
        except Exception:
            if strict_remote and item.get("url"):
                raise
            # Keep the original result if storage is temporarily unavailable;
            # the task can still be inspected and retried by the caller.
            normalized.append(item)
    return normalized
