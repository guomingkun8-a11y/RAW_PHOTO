from __future__ import annotations

import base64
from typing import Any

from services.image_storage_service import ImageStorageService, image_storage_service

IMAGE_REF_MARKER = "__image_ref__"
LEGACY_IMAGE_MARKER = "__image_input__"


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


def normalize_task_result(
    data: list[Any],
    *,
    owner_id: str,
    task_id: str,
    base_url: str = "",
    storage: ImageStorageService | None = None,
) -> list[Any]:
    """Move inline Base64 results to object storage before persisting task JSON."""

    storage_service = storage or image_storage_service
    normalized: list[Any] = []
    for index, item in enumerate(data):
        if not isinstance(item, dict) or not item.get("b64_json"):
            normalized.append(item)
            continue
        try:
            image_data = base64.b64decode(str(item.get("b64_json")))
            filename = str(item.get("filename") or f"image-{index + 1}.png")
            stored = storage_service.save_task_asset(
                image_data,
                owner_id=owner_id,
                task_id=task_id,
                asset_index=f"result:{index}",
                asset_type="task_result",
                filename=filename,
                mime_type="image/png",
                base_url=base_url,
            )
            replacement = {key: value for key, value in item.items() if key != "b64_json"}
            replacement["url"] = stored.url
            replacement["storage_rel"] = stored.rel
            normalized.append(replacement)
        except Exception:
            # Keep the original result if storage is temporarily unavailable;
            # the task can still be inspected and retried by the caller.
            normalized.append(item)
    return normalized
