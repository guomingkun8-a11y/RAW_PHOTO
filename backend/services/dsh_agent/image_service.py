from __future__ import annotations

import io
from pathlib import Path
from typing import Any
from uuid import uuid4

from PIL import Image, UnidentifiedImageError

from services.dsh_agent.image_media import ImageMediaStore
from services.dsh_agent.store import DshAgentStore
from services.image.image_size import canvas_media_request_size
from services.image.image_storage_service import image_storage_service
from services.image.image_task_service import image_task_service
from services.platform.content_filter import check_request
from services.providers import openai_relay_service as relay


IMAGE_MODELS = ("gpt-image-2", *relay.TT_IMAGE_2_5_VERSIONS, *relay.TT_IMAGE_2_6_VERSIONS, "banana-2")
DEFAULT_IMAGE_MODEL = "gpt-image-2.6-flare"
MAX_IMAGE_BYTES = 20 * 1024 * 1024


def image_model_catalog() -> list[dict[str, Any]]:
    result = []
    for model in IMAGE_MODELS:
        native = model in relay.TT_IMAGE_2_6_VERSIONS
        result.append({
            "id": model, "label": model if model != "banana-2" else "Nano Banana 2",
            "ratios": (["auto", "1:1", "3:2", "2:3"] if native else
                       list(relay.TT_IMAGE_2_5_ASPECT_RATIOS) if model in relay.TT_IMAGE_2_5_VERSIONS else
                       ["auto", *relay.MEDIA_IMAGE_ASPECT_RATIOS] if model == "banana-2" else
                       ["auto", "1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "5:4", "4:5", "21:9"]),
            "qualities": (["auto"] if model == "banana-2" else list(relay.TT_IMAGE_2_5_QUALITIES)
                          if native or model in relay.TT_IMAGE_2_5_VERSIONS else list(relay.MEDIA_IMAGE_QUALITY_OPTIONS)),
            "resolutions": ([] if native else list(relay.TT_IMAGE_2_5_RESOLUTIONS)
                            if model in relay.TT_IMAGE_2_5_VERSIONS else list(relay.MEDIA_IMAGE_SIZE_OPTIONS)
                            if model == "banana-2" else ["1K", "2K", "4K"]),
        })
    return result


def normalize_options(options: dict[str, Any]) -> dict[str, Any]:
    model = options.get("model") or DEFAULT_IMAGE_MODEL
    if model not in IMAGE_MODELS:
        raise ValueError("请选择系统已开放的生图模型")
    catalog = next(item for item in image_model_catalog() if item["id"] == model)
    values = {"model": model}
    for field, choices, default in (
        ("aspect_ratio", catalog["ratios"], "auto"),
        ("quality", catalog["qualities"], "auto"),
        ("image_size", catalog["resolutions"], "auto" if "auto" in catalog["resolutions"] else "1K"),
    ):
        value = options.get(field) or default
        if choices and value not in choices:
            raise ValueError(f"{model} 的 {field} 可选值为：{', '.join(choices)}")
        if not choices and field in options and options[field] not in (None, "", "auto"):
            raise ValueError(f"{model} 不支持分辨率档位，请使用模型原生比例")
        if choices:
            values[field] = value
    values["size"] = "auto" if model in relay.TT_IMAGE_2_6_VERSIONS else (
        canvas_media_request_size(values.get("aspect_ratio", "auto"), values.get("image_size", "1K")) or "auto"
    )
    # The existing Banana adapter derives auto from size when the ratio is omitted.
    if model == "banana-2" and values.get("aspect_ratio") == "auto":
        values.pop("aspect_ratio")
    for field in ("background", "complexity", "output_format", "frame_count", "frame_duration_ms", "thinking_level"):
        if options.get(field) is not None:
            supported = (model in relay.TT_IMAGE_2_6_VERSIONS or
                         field == "background" and model in relay.TT_IMAGE_2_5_VERSIONS or
                         field == "thinking_level" and model == "banana-2")
            if not supported:
                raise ValueError(f"{model} 不支持参数 {field}")
            values[field] = options[field]
    relay._media_image_params(values, upstream_model=relay._upstream_media_model(model), requested_size=values["size"], reference_urls=[])
    return values


class DshImageService:
    def __init__(self, store: DshAgentStore):
        self.store = store
        self.media = ImageMediaStore(store)

    def discard_reference(self, conversation_id: str, identity: dict[str, Any], media_id: str) -> None:
        owner = str(identity.get("id") or identity.get("username") or "")
        item = self.media.discard_reference(conversation_id, owner, media_id)
        if item and item.get("storage_rel"):
            image_storage_service.delete(item["storage_rel"])

    def upload(self, conversation_id: str, identity: dict[str, Any], data: bytes, filename: str, base_url: str) -> dict[str, Any]:
        owner = str(identity.get("id") or identity.get("username") or "")
        if not self.store.get_conversation(conversation_id, owner_id=owner):
            raise KeyError("conversation not found")
        if not data or len(data) > MAX_IMAGE_BYTES:
            raise ValueError("参考图片不能为空，且每张不能超过 20MB")
        try:
            with Image.open(io.BytesIO(data)) as image:
                if image.format not in {"PNG", "JPEG", "WEBP", "GIF"} or image.width * image.height > 40_000_000:
                    raise ValueError("仅支持 PNG、JPEG、WebP、GIF，且不能超过 4000 万像素")
                width, height, format = image.width, image.height, image.format
                image.verify()
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
            raise ValueError("无法读取图片，请选择有效的图片文件") from exc
        mime = Image.MIME[format]
        suffix = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp", "GIF": ".gif"}[format]
        saved = image_storage_service.save_task_asset(
            data, owner_id=owner, task_id=conversation_id, asset_index=uuid4().hex,
            asset_type="dsh-reference", filename=f"reference{suffix}", mime_type=mime, base_url=base_url,
        )
        try:
            return self.media.add_reference(conversation_id, owner, {
                "url": saved.url, "storage_rel": saved.rel, "filename": Path(filename.replace("\\", "/")).name[:120],
                "mime_type": mime, "width": width, "height": height, "size_bytes": len(data),
            })
        except Exception:
            image_storage_service.delete(saved.rel)
            raise

    def hydrate(self, identity: dict[str, Any], media: list[dict[str, Any]]) -> list[dict[str, Any]]:
        task_ids = [item["task_id"] for item in media if item["kind"] == "task"]
        tasks = image_task_service.list_tasks(identity, task_ids)["items"] if task_ids else []
        indexed = {task["id"]: task for task in tasks}
        return [{**item, "task": indexed.get(item["task_id"], {"id": item["task_id"], "status": "unconfirmed", "error": "任务提交状态未确认，请勿重复提交"})}
                if item["kind"] == "task" else item for item in media]

    def attach_messages(self, result: dict[str, Any], identity: dict[str, Any]) -> dict[str, Any]:
        owner = str(identity.get("id") or identity.get("username") or "")
        media = self.hydrate(identity, self.media.list(result["conversation"]["conversation_id"], owner, message_ids=[m["message_id"] for m in result["items"]]))
        grouped: dict[str, list] = {}
        for item in media:
            grouped.setdefault(item["message_id"], []).append(item)
        for message in result["items"]:
            if message["message_id"] in grouped:
                message["media"] = grouped[message["message_id"]]
        drafts = self.media.list(result["conversation"]["conversation_id"], owner, message_ids=[""])
        if drafts:
            result["draft_references"] = drafts
        return result

    def catalog(self, conversation_id: str, identity: dict[str, Any]) -> list[dict[str, Any]]:
        owner = str(identity.get("id") or identity.get("username") or "")
        return self.hydrate(identity, self.media.list(conversation_id, owner))

    def reference_inputs(self, references: list[str], catalog: list[dict[str, Any]]) -> tuple[list, list[str]]:
        images, urls = [], []
        if len(references) > 6 or len(set(references)) != len(references):
            raise ValueError("每次最多使用 6 张不同的参考图片")
        indexed = {item["media_id"]: item for item in catalog}
        for reference in references:
            media_id, _, index = reference.partition(":")
            item = indexed.get(media_id)
            if item is None or item["kind"] not in {"reference", "task"}:
                raise ValueError("参考图片不属于当前会话")
            if item["kind"] == "task":
                task = item.get("task") or {}
                outputs = task.get("data") or []
                position = int(index or "0") if (index or "0").isdigit() else -1
                if task.get("status") != "success" or not 0 <= position < len(outputs):
                    raise ValueError("参考图片尚未生成完成或序号无效")
                item = outputs[position]
            if item.get("storage_rel"):
                images.append((image_storage_service.get_bytes(item["storage_rel"]), item.get("filename", "reference.png"), item.get("mime_type", "image/png")))
            elif str(item.get("url", "")).startswith(("https://", "http://")):
                urls.append(item["url"])
            else:
                raise ValueError("参考图片已不可用，请重新上传")
        return images, urls

    def submit(self, context, call_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
        allowed = {"prompt", "prompts", "model", "n", "reference_ids", "aspect_ratio", "quality", "image_size", "background", "complexity", "output_format", "frame_count", "frame_duration_ms", "thinking_level"}
        if set(arguments) - allowed:
            raise ValueError("生图工具包含不支持的参数")
        prompt = arguments.get("prompt")
        if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 12000:
            raise ValueError("请输入 1 至 12000 字的生图需求")
        n = arguments.get("n", 1)
        if type(n) is not int or not 1 <= n <= 4:
            raise ValueError("单次生成数量必须为 1 至 4 张")
        prompts = arguments.get("prompts")
        if prompts is not None:
            if not isinstance(prompts, list) or not 1 <= len(prompts) <= 4 or any(not isinstance(value, str) or not 1 <= len(value.strip()) <= 12000 for value in prompts):
                raise ValueError("prompts 必须包含 1 至 4 条完整的独立图片提示词")
            if "n" in arguments and n != len(prompts):
                raise ValueError("n 必须与 prompts 的图片数量一致，不能相乘生成")
            prompts = [value.strip() for value in prompts]
            if len({" ".join(value.casefold().split()) for value in prompts}) != len(prompts):
                raise ValueError("不同图片的 prompts 不能重复；同一主体的变体请只使用 prompt 和 n")
        # A model change must not inherit incompatible options from the selected model.
        defaults = context.options if arguments.get("model", context.options["model"]) == context.options["model"] else {}
        options = normalize_options({**defaults, **{k: v for k, v in arguments.items() if k not in {"prompt", "prompts", "n", "reference_ids"}}})
        references = arguments.get("reference_ids", context.reference_ids)
        if not isinstance(references, list) or any(not isinstance(item, str) for item in references):
            raise ValueError("reference_ids 必须为当前会话的图片编号列表")
        catalog = self.catalog(context.conversation_id, context.identity)
        images, urls = self.reference_inputs(references, catalog)
        check_request(prompt)
        if prompts is None:
            payloads = [{**options, "prompt": prompt.strip(), "n": n, "reference_ids": references}]
        else:
            for value in prompts:
                check_request(value)
            # Include the whole plan in each fingerprint so a replay cannot change a later image.
            payloads = [{**options, "prompt": value, "n": 1, "reference_ids": references,
                         "batch_prompts": prompts, "batch_index": index} for index, value in enumerate(prompts)]
        reserved = self.media.reserve_tasks(context.conversation_id, context.owner_id, context.message_id, call_id, payloads)
        results = []
        for index, (media, created) in enumerate(reserved):
            try:
                results.append(self._dispatch(context, media, created, options, references, images, urls))
            except Exception:
                for remaining, _created in reserved[index + 1:]:
                    context.on_media(self.hydrate(context.identity, [remaining])[0])
                raise
        return {"items": results} if prompts is not None else results[0]

    def _dispatch(self, context, media: dict, created: bool, options: dict, references: list[str], images: list, urls: list[str]) -> dict[str, Any]:
        if created:
            kwargs = {**options, "prompt": media["prompt"], "n": media["n"], "client_task_id": media["task_id"],
                      "conversation_id": context.conversation_id, "turn_id": context.message_id,
                      "agent_run_id": f"dsh:{context.message_id}", "source": "dsh", "prompt_engine_mode": "general", "base_url": context.base_url}
            try:
                if references:
                    task = image_task_service.submit_edit(context.identity, images=images, image_urls=urls, **kwargs)
                else:
                    task = image_task_service.submit_generation(context.identity, **kwargs)
                public = {**media, "task": task}
            except Exception:
                # A lost response never authorizes resubmitting this tool invocation.
                self.media.mark_uncertain(media["media_id"])
                public = self.hydrate(context.identity, [media])[0]
                context.on_media(public)
                raise
        else:
            public = self.hydrate(context.identity, [media])[0]
        context.on_media(public)
        return public
