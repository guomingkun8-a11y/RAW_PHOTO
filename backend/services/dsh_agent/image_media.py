from __future__ import annotations

from datetime import datetime
import hashlib
import json
from typing import Any
from uuid import uuid4

from services.dsh_agent.store import DshAgentConversationModel, DshAgentMediaModel, DshAgentMessageModel, DshAgentStore


def media_dict(row: DshAgentMediaModel) -> dict[str, Any]:
    payload = json.loads(row.payload_json)
    payload.pop("_text", None)
    return {
        "media_id": row.media_id, "conversation_id": row.conversation_id,
        "message_id": row.message_id, "kind": row.kind, "task_id": row.task_id,
        "created_at": row.created_at.isoformat(), **payload,
    }


def bind_references(session, conversation_id: str, owner_id: str, message_id: str, ids: list[str], *, kind: str = "reference") -> None:
    if len(ids) > (4 if kind == "file" else 6) or len(set(ids)) != len(ids):
        raise ValueError("附件数量超限或重复引用")
    rows = session.query(DshAgentMediaModel).filter(
        DshAgentMediaModel.conversation_id == conversation_id,
        DshAgentMediaModel.owner_id == owner_id,
        DshAgentMediaModel.kind == kind,
        DshAgentMediaModel.media_id.in_(ids),
        DshAgentMediaModel.message_id == "",
    ).with_for_update().all()
    if len(rows) != len(ids):
        raise ValueError("附件不存在、已发送或不属于当前会话，请重新上传")
    for row in rows:
        row.message_id = message_id


class ImageMediaStore:
    def __init__(self, store: DshAgentStore):
        self.store = store

    def add_reference(self, conversation_id: str, owner_id: str, payload: dict[str, Any], *, kind: str = "reference") -> dict[str, Any]:
        with self.store._session() as session:
            conversation = session.query(DshAgentConversationModel).filter_by(
                conversation_id=conversation_id, owner_id=owner_id,
            ).with_for_update().one_or_none()
            if conversation is None or conversation.status == "deleting":
                raise KeyError("conversation not found")
            pending = session.query(DshAgentMediaModel).filter_by(
                conversation_id=conversation_id, owner_id=owner_id, message_id="", kind=kind,
            ).count()
            if pending >= 24:
                raise ValueError("待发送附件过多，请先发送或移除附件")
            row = DshAgentMediaModel(
                media_id=f"dshref_{uuid4().hex}", conversation_id=conversation_id, owner_id=owner_id,
                kind=kind, payload_json=json.dumps(payload, ensure_ascii=False),
            )
            session.add(row)
            session.commit()
            return media_dict(row)

    def list(self, conversation_id: str, owner_id: str, *, message_ids: list[str] | None = None) -> list[dict[str, Any]]:
        with self.store._session() as session:
            query = session.query(DshAgentMediaModel).filter_by(conversation_id=conversation_id, owner_id=owner_id)
            if message_ids is not None:
                query = query.filter(DshAgentMediaModel.message_id.in_(message_ids))
            else:
                query = query.filter(DshAgentMediaModel.message_id != "")
            rows = query.order_by(DshAgentMediaModel.created_at.desc(), DshAgentMediaModel.media_id.desc()).limit(500).all()
            return [media_dict(row) for row in reversed(rows)]

    def discard_reference(self, conversation_id: str, owner_id: str, media_id: str, *, kind: str = "reference") -> dict[str, Any] | None:
        with self.store._session() as session:
            row = session.query(DshAgentMediaModel).filter_by(
                conversation_id=conversation_id, owner_id=owner_id, media_id=media_id,
                kind=kind, message_id="",
            ).with_for_update().one_or_none()
            if row is None:
                return None
            item = media_dict(row)
            session.delete(row)
            session.commit()
            return item

    def reserve_tasks(self, conversation_id: str, owner_id: str, message_id: str, call_id: str, payloads: list[dict[str, Any]]) -> list[tuple[dict[str, Any], bool]]:
        with self.store._session() as session:
            # Serialize only this conversation; task dispatch itself happens outside the transaction.
            conversation = session.query(DshAgentConversationModel).filter_by(
                conversation_id=conversation_id, owner_id=owner_id,
            ).with_for_update().one_or_none()
            if conversation is None or conversation.status != "running":
                raise ValueError("当前会话未处于有效运行状态")
            latest = session.query(DshAgentMessageModel.message_id).filter_by(
                conversation_id=conversation_id, owner_id=owner_id, role="user",
            ).order_by(DshAgentMessageModel.created_at.desc(), DshAgentMessageModel.message_id.desc()).first()
            if not latest or latest[0] != message_id:
                raise ValueError("生图工具调用已不属于当前对话轮次")
            tasks = session.query(DshAgentMediaModel).filter_by(
                conversation_id=conversation_id, owner_id=owner_id, message_id=message_id, kind="task",
            ).all()
            count = sum(int(json.loads(row.payload_json).get("n", 1)) for row in tasks)
            reserved = []
            for index, payload in enumerate(payloads):
                child_call_id = call_id if index == 0 else f"{call_id}:{index}"
                digest = hashlib.sha256(f"{owner_id}:{conversation_id}:{message_id}:{child_call_id}".encode()).hexdigest()
                media_id = f"dshimg_{digest}"
                fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                existing = next((row for row in tasks if row.media_id == media_id), None)
                if existing is not None:
                    if json.loads(existing.payload_json).get("request_hash") != fingerprint:
                        raise ValueError("同一次工具调用不能更改生图参数")
                else:
                    existing = next((row for row in tasks if json.loads(row.payload_json).get("request_hash") == fingerprint), None)
                if existing is not None:
                    reserved.append((existing, False))
                    continue
                if any(json.loads(row.payload_json).get("submission_uncertain") for row in tasks):
                    raise ValueError("本轮已有提交状态未确认的图片任务，请先核对，不要自动重新生成")
                count += payload["n"]
                if count > 8:
                    raise ValueError("每轮对话最多提交 8 张图片，请等待任务完成后继续")
                row = DshAgentMediaModel(
                    media_id=media_id, task_id=media_id, conversation_id=conversation_id,
                    owner_id=owner_id, message_id=message_id, kind="task",
                    payload_json=json.dumps({**payload, "request_hash": fingerprint}, ensure_ascii=False),
                    created_at=datetime.now(),
                )
                session.add(row)
                tasks.append(row)
                reserved.append((row, True))
            session.commit()
            return [(media_dict(row), created) for row, created in reserved]

    def mark_uncertain(self, media_id: str) -> None:
        with self.store._session() as session:
            row = session.query(DshAgentMediaModel).filter_by(media_id=media_id).with_for_update().one()
            row.payload_json = json.dumps({**json.loads(row.payload_json), "submission_uncertain": True}, ensure_ascii=False)
            session.commit()
