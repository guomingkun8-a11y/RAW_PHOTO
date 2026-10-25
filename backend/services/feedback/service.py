from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from io import BytesIO
from contextlib import suppress
import warnings
from uuid import uuid4

from fastapi import HTTPException
from PIL import Image

from services.alerts.incident_store import alert_incident_store
from services.alerts.settings import AlertSettings
from services.alerts.wecom_notifier import WeComNotifier, _safe
from services.feedback.store import FeedbackAttachment, FeedbackStore, feedback_store, now
from services.image.reference_image_uploader import settings as oss_settings, _oss_client


MAX_ATTACHMENT_BYTES = 8 * 1024 * 1024


class FeedbackService:
    def __init__(self, store: FeedbackStore = feedback_store):
        self.store = store

    def upload(self, identity, payload, filename):
        if not payload or len(payload) > MAX_ATTACHMENT_BYTES:
            raise HTTPException(413, "每张截图不得超过 8MB")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(BytesIO(payload)) as image:
                    if image.format not in {"PNG", "JPEG", "WEBP"} or image.width * image.height > 20_000_000:
                        raise ValueError("invalid image")
                    image.load()
                    output = BytesIO()
                    if image.mode not in {"RGB", "RGBA"}:
                        image = image.convert("RGBA" if "transparency" in image.info else "RGB")
                    image.save(output, format="PNG")
                    clean_payload = output.getvalue()
                    if len(clean_payload) > MAX_ATTACHMENT_BYTES:
                        raise HTTPException(413, "截图处理后超过 8MB，请缩小后上传")
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(422, "请上传有效的 PNG、JPG 或 WebP 截图（最多 2000 万像素）") from exc
        item = oss_settings()
        object_id = str(uuid4())
        key = f"{str(item.get('oss_prefix') or 'raw-photo').strip('/')}/feedback/{object_id}.png"
        bucket = str(item.get("oss_bucket") or "")
        if not bucket:
            raise HTTPException(503, "截图对象存储尚未配置")
        # Store private objects; the browser only receives an authenticated content endpoint.
        try:
            client = _oss_client(item)
            client.put_object(bucket, key, BytesIO(clean_payload), len(clean_payload), content_type="image/png", metadata={"x-amz-acl": "private"})
        except Exception as exc:
            raise HTTPException(502, "截图存储连接失败，请稍后重试") from exc
        try:
            safe_name = str(filename or "截图.png").replace("\\", "/").split("/")[-1][:175]
            return self.store.add_attachment(identity, {"id": object_id, "filename": safe_name, "object_key": key, "mime_type": "image/png", "size": len(clean_payload)})
        except Exception:
            with suppress(Exception):
                client.remove_object(bucket, key)
            raise

    def attachment_content(self, identity, attachment_id):
        item = self.store.get_attachment(identity, attachment_id)
        settings = oss_settings()
        try:
            response = _oss_client(settings).get_object(str(settings.get("oss_bucket") or ""), item["object_key"])
        except Exception as exc:
            raise HTTPException(502, "截图暂时无法读取，请稍后重试") from exc
        try:
            payload = response.read(MAX_ATTACHMENT_BYTES + 1)
        finally:
            response.close()
            response.release_conn()
        if len(payload) > MAX_ATTACHMENT_BYTES:
            raise HTTPException(413, "截图过大")
        return payload, item

    def cleanup_unused_attachments(self):
        settings = oss_settings()
        client = None
        with self.store.session() as session:
            query = session.query(FeedbackAttachment).filter(FeedbackAttachment.message_id.is_(None), FeedbackAttachment.created_at < now() - timedelta(days=1))
            rows = query.with_for_update(skip_locked=True).limit(20).all()
            for row in rows:
                if client is None:
                    client = _oss_client(settings)
                client.remove_object(str(settings.get("oss_bucket") or ""), row.object_key)
                session.delete(row)
            session.commit()

    def update_thread(self, identity, thread_id, changes):
        if "assigned_to" in changes:
            if identity.get("role") != "admin":
                raise HTTPException(403, "仅管理员可以分配处理人")
            from services.accounts.user_service import user_service
            target_id = changes["assigned_to"]
            target = user_service.get_user(target_id) if target_id else None
            if target_id and (not target or target.get("role") != "admin" or not target.get("enabled")):
                raise HTTPException(422, "请选择有效的管理员")
            changes = {**changes, "assigned_name": (target.get("name") or target.get("username")) if target else None}
        return self.store.update_thread(identity, thread_id, changes)

    def notification(self, event):
        settings = self.store.settings()
        if not settings["notify_enabled"]:
            return event["payload"]
        thread = self.store.get_thread({"id": "feedback-notifier", "role": "admin"}, event["payload"]["thread_id"])
        message = self.store.notification_message(thread["id"], event["payload"].get("message_id"))
        if not message:
            return event["payload"]
        defaults = AlertSettings.from_env()
        targets = alert_incident_store.list_webhooks(defaults, active_only=True, include_url=True, purpose="feedback")
        if not targets:
            raise RuntimeError("No assigned feedback notification webhook")
        if "target_ids" not in event["payload"]:
            event["payload"]["target_ids"] = [target["id"] for target in targets]
        targets = [target for target in targets if target["id"] in event["payload"]["target_ids"]]
        delivered = set(event["payload"].get("delivered") or [])
        label = "管理员回复" if message["sender_role"] == "admin" else "用户反馈"
        content = f"### 【{_safe(defaults.environment, limit=32)}】{label}\n\n用户：{_safe(thread['owner_name'])}\n问题：{_safe(thread['title'])}\n发送人：{_safe(message['sender_name'])}\n发送时间：{_safe(message['created_at'])}"
        if settings["site_url"]:
            content += f"\n\n[查看并回复]({settings['site_url']}/feedback?thread={thread['id']})"
        body = "\n".join(_safe(line, default="", limit=900) for line in message["content"][:900].splitlines()) or "[截图消息]"
        content += f"\n\n**消息内容**\n{body}"
        if len(message["content"]) > 900:
            content += "\n\n> 内容较长，完整消息请在系统中查看。"
        if message["attachments"]:
            content += f"\n\n截图：{len(message['attachments'])} 张（登录系统查看）"
        failures = []
        for target in targets:
            if target["id"] in delivered:
                continue
            try:
                WeComNotifier(replace(defaults, enabled=True, webhook_url=str(target["url"]))).send_markdown(content)
                delivered.add(target["id"])
            except Exception:
                failures.append(target["id"])
        event["payload"]["delivered"] = sorted(delivered)
        if failures:
            raise RuntimeError("Feedback notification delivery failed")
        return event["payload"]


feedback_service = FeedbackService()
