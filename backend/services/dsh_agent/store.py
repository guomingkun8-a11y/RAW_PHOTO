from __future__ import annotations

from datetime import datetime, timedelta
import json
from pathlib import Path
import re
import shutil
import threading
import time
from typing import Any
from uuid import uuid4

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text, create_engine, func
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

from services.platform.enterprise_schema import resolve_enterprise_database_url
from services.platform.config import DATA_DIR
from services.dsh_agent.sessions import validate_session_id, user_home
from services.dsh_agent.models import resolve_model


DshAgentBase = declarative_base()
_LONG_TEXT = Text().with_variant(LONGTEXT, "mysql")


class DshAgentConversationModel(DshAgentBase):
    __tablename__ = "dsh_agent_conversations"
    __table_args__ = (
        Index("idx_dsh_agent_owner_updated", "owner_id", "updated_at"),
        Index("idx_dsh_agent_active_until", "active_until"),
    )

    conversation_id = Column(String(191), primary_key=True)
    dsh_session_id = Column(String(191), nullable=False, unique=True)
    owner_id = Column(String(191), nullable=False)
    owner_username = Column(String(191), nullable=False, default="")
    owner_name = Column(String(191), nullable=False, default="")
    title = Column(String(191), nullable=False, default="新对话")
    model = Column(String(191), nullable=False, default="", server_default="")
    status = Column(String(24), nullable=False, default="idle")
    active_until = Column(DateTime, nullable=True)
    turn_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(DateTime, nullable=False, default=datetime.now)
    messages = relationship("DshAgentMessageModel", cascade="all, delete-orphan", order_by="DshAgentMessageModel.created_at")


class DshAgentMessageModel(DshAgentBase):
    __tablename__ = "dsh_agent_messages"
    __table_args__ = (
        Index("idx_dsh_agent_messages_owner_created", "owner_id", "created_at"),
        Index("idx_dsh_agent_messages_conversation_created", "conversation_id", "created_at"),
        Index("idx_dsh_agent_messages_role_created", "role", "created_at"),
    )

    message_id = Column(String(191), primary_key=True)
    conversation_id = Column(
        String(191), ForeignKey("dsh_agent_conversations.conversation_id", ondelete="CASCADE"), nullable=False,
    )
    owner_id = Column(String(191), nullable=False)
    role = Column(String(24), nullable=False)
    status = Column(String(24), nullable=False, default="completed")
    content = Column(_LONG_TEXT, nullable=False)
    model = Column(String(191), nullable=False, default="")
    input_tokens = Column(Integer, nullable=True)
    output_tokens = Column(Integer, nullable=True)
    total_tokens = Column(Integer, nullable=True)
    usage_json = Column(Text, nullable=False, default="{}")
    activity_json = Column(Text, nullable=True)
    timings_json = Column(Text, nullable=True)
    error_code = Column(String(80), nullable=False, default="")
    feedback = Column(String(8), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)


class DshAgentMediaModel(DshAgentBase):
    __tablename__ = "dsh_agent_media"
    __table_args__ = (Index("idx_dsh_media_conversation_message", "conversation_id", "message_id", "created_at"),)

    media_id = Column(String(191), primary_key=True)
    conversation_id = Column(String(191), ForeignKey("dsh_agent_conversations.conversation_id", ondelete="CASCADE"), nullable=False)
    owner_id = Column(String(191), nullable=False)
    message_id = Column(String(191), nullable=False, default="")
    kind = Column(String(24), nullable=False)
    task_id = Column(String(191), nullable=False, default="")
    payload_json = Column(_LONG_TEXT, nullable=False, default="{}")
    created_at = Column(DateTime, nullable=False, default=datetime.now)


def _conversation_dict(row: DshAgentConversationModel) -> dict[str, Any]:
    return {
        "conversation_id": row.conversation_id,
        "title": row.title,
        "model": row.model or resolve_model(),
        "status": row.status,
        "turn_count": int(row.turn_count or 0),
        "created_at": row.created_at.isoformat(sep=" ") if row.created_at else "",
        "updated_at": row.updated_at.isoformat(sep=" ") if row.updated_at else "",
        "owner_id": row.owner_id,
        "owner_username": row.owner_username,
        "owner_name": row.owner_name,
    }


def _message_dict(row: DshAgentMessageModel) -> dict[str, Any]:
    try:
        usage = json.loads(row.usage_json or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        usage = {}
    if not isinstance(usage, dict):
        usage = {}
    try:
        activities = json.loads(row.activity_json or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        activities = []
    try:
        timings = json.loads(row.timings_json or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        timings = {}
    return {
        "message_id": row.message_id,
        "conversation_id": row.conversation_id,
        "role": row.role,
        "status": row.status,
        "content": row.content,
        "model": row.model,
        "input_tokens": row.input_tokens,
        "output_tokens": row.output_tokens,
        "total_tokens": row.total_tokens,
        "usage": {key: value for key, value in usage.items() if key not in {"context", "team", "expert_id", "skill_ids"}},
        "expert_id": usage.get("expert_id") if isinstance(usage.get("expert_id"), str) else "",
        "skill_ids": usage.get("skill_ids") if isinstance(usage.get("skill_ids"), list) else None,
        "team": usage.get("team") if isinstance(usage.get("team"), dict) else None,
        "context": usage.get("context") if isinstance(usage.get("context"), dict) else None,
        "activities": activities if isinstance(activities, list) else [],
        "timings": timings if isinstance(timings, dict) else {},
        "model_switch": json.loads(row.content) if row.role == "model_switch" else None,
        "error_code": row.error_code,
        "feedback": row.feedback,
        "created_at": row.created_at.isoformat(sep=" ") if row.created_at else "",
    }


class DshAgentStore:
    def __init__(self, database_url: str | None = None, *, dsh_home: str | Path | None = None) -> None:
        self.database_url = database_url
        self.dsh_home = Path(dsh_home) if dsh_home else DATA_DIR / "dsh-agent" / "home"
        self._lock = threading.Lock()
        self.engine = None
        self.Session = None

    def _session(self):
        if self.Session is None:
            with self._lock:
                if self.Session is None:
                    url = self.database_url or resolve_enterprise_database_url()
                    options: dict[str, Any] = {"pool_pre_ping": True, "pool_recycle": 3600}
                    if url.startswith("sqlite"):
                        options["connect_args"] = {"check_same_thread": False}
                    self.engine = create_engine(url, **options)
                    DshAgentBase.metadata.create_all(self.engine)
                    self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        return self.Session()

    def create_conversation(self, identity: dict[str, object], *, model: str | None = None) -> dict[str, Any]:
        owner_id = str(identity.get("id") or identity.get("username") or "").strip()
        if not owner_id:
            raise ValueError("authenticated user id is required")
        now = datetime.now()
        row = DshAgentConversationModel(
            conversation_id=f"dsh_{uuid4().hex}",
            dsh_session_id=f"session-{uuid4().hex}",
            owner_id=owner_id,
            owner_username=str(identity.get("username") or "")[:191],
            owner_name=str(identity.get("name") or identity.get("username") or "")[:191],
            title="新对话",
            model=resolve_model(model),
            created_at=now,
            updated_at=now,
        )
        session = self._session()
        try:
            session.add(row)
            session.commit()
            return _conversation_dict(row)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def list_conversations(self, owner_id: str, *, limit: int = 50) -> dict[str, Any]:
        session = self._session()
        try:
            query = session.query(DshAgentConversationModel).filter_by(owner_id=owner_id)
            total = query.count()
            rows = query.order_by(DshAgentConversationModel.updated_at.desc()).limit(limit).all()
            return {"items": [_conversation_dict(row) for row in rows], "total": total}
        finally:
            session.close()

    def get_conversation(self, conversation_id: str, *, owner_id: str | None = None) -> dict[str, Any] | None:
        session = self._session()
        try:
            query = session.query(DshAgentConversationModel).filter_by(conversation_id=conversation_id)
            if owner_id is not None:
                query = query.filter_by(owner_id=owner_id)
            row = query.one_or_none()
            return _conversation_dict(row) if row else None
        finally:
            session.close()

    def get_runtime_session(self, conversation_id: str, owner_id: str) -> tuple[str, bool] | None:
        session = self._session()
        try:
            row = session.query(DshAgentConversationModel).filter_by(
                conversation_id=conversation_id, owner_id=owner_id,
            ).one_or_none()
            if row is None:
                return None
            return row.dsh_session_id, int(row.turn_count or 0) > 1
        finally:
            session.close()

    def get_completed_history(
        self,
        conversation_id: str,
        owner_id: str,
        *,
        before_message_id: str,
        max_turns: int = 12,
        max_chars: int = 48000,
    ) -> list[dict[str, str]]:
        session = self._session()
        try:
            rows = session.query(DshAgentMessageModel).filter_by(
                conversation_id=conversation_id, owner_id=owner_id,
            ).filter(DshAgentMessageModel.role.in_(("user", "assistant"))).order_by(
                DshAgentMessageModel.created_at.desc(), DshAgentMessageModel.message_id.desc(),
            ).limit(100).all()
            rows.reverse()
            messages = []
            found_current = False
            for row in rows:
                if row.message_id == before_message_id:
                    found_current = True
                    break
                messages.append(row)
            if not found_current:
                return []

            pairs: list[tuple[DshAgentMessageModel, DshAgentMessageModel]] = []
            pending_user = None
            for row in messages:
                if row.role == "user":
                    pending_user = row
                elif row.role == "assistant" and pending_user is not None:
                    if pending_user.status == "completed" and row.status == "completed":
                        pairs.append((pending_user, row))
                    pending_user = None

            selected = []
            total_chars = 0
            for pair in reversed(pairs[-max(1, max_turns):]):
                pair_chars = sum(len(message.content) for message in pair)
                if total_chars + pair_chars > max_chars:
                    break
                selected.append(pair)
                total_chars += pair_chars
            return [
                {"role": role, "content": message.content}
                for user_message, assistant_message in reversed(selected)
                for role, message in (("user", user_message), ("assistant", assistant_message))
            ]
        finally:
            session.close()

    def list_messages(self, conversation_id: str, *, owner_id: str | None = None, limit: int = 200) -> dict[str, Any] | None:
        session = self._session()
        try:
            conversation_query = session.query(DshAgentConversationModel).filter_by(conversation_id=conversation_id)
            if owner_id is not None:
                conversation_query = conversation_query.filter_by(owner_id=owner_id)
            conversation = conversation_query.one_or_none()
            if conversation is None:
                return None
            rows = session.query(DshAgentMessageModel).filter_by(conversation_id=conversation_id).filter(
                DshAgentMessageModel.status != "discarded",
            ).order_by(
                DshAgentMessageModel.created_at.desc(), DshAgentMessageModel.message_id.desc(),
            ).limit(limit).all()
            rows.reverse()
            return {"conversation": _conversation_dict(conversation), "items": [_message_dict(row) for row in rows]}
        finally:
            session.close()

    def set_model(self, conversation_id: str, owner_id: str, model: str) -> dict[str, Any] | None:
        model = resolve_model(model)
        session = self._session()
        try:
            now = datetime.now()
            # Lock only this conversation so the transition and its notice commit together.
            row = session.query(DshAgentConversationModel).filter_by(
                conversation_id=conversation_id, owner_id=owner_id,
            ).with_for_update().one_or_none()
            if row is None:
                return None
            if row.status == "deleting" or (row.active_until is not None and row.active_until >= now):
                raise RuntimeError("该对话正在生成回复，暂时不能切换模型")
            previous_model = row.model or resolve_model()
            notice = None
            if previous_model != model:
                notice = DshAgentMessageModel(
                    message_id=f"msg_z{time.time_ns():020d}_{uuid4().hex}",
                    conversation_id=conversation_id, owner_id=owner_id,
                    role="model_switch", status="completed", model=model,
                    content=json.dumps({"from_model": previous_model, "to_model": model}),
                    created_at=now,
                )
                session.add(notice)
                row.model = model
                row.updated_at = now
            session.commit()
            return {**_conversation_dict(row), "model_switch": _message_dict(notice) if notice else None}
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def begin_turn(self, conversation_id: str, owner_id: str, prompt: str, *, lease_secs: int = 900, model: str | None = None, reference_ids: list[str] | None = None, file_ids: list[str] | None = None, expert_id: str = "", skill_ids: list[str] | None = None) -> dict[str, Any]:
        from services.dsh_agent.experts import resolve_expert
        from services.dsh_agent.skills import resolve_skills

        expert_id = resolve_expert(expert_id)
        skill_ids = resolve_skills(skill_ids)
        selected_model = resolve_model(model) if model is not None else None
        now = datetime.now()
        session = self._session()
        try:
            updated = session.query(DshAgentConversationModel).filter(
                DshAgentConversationModel.conversation_id == conversation_id,
                DshAgentConversationModel.owner_id == owner_id,
                DshAgentConversationModel.status != "deleting",
                (DshAgentConversationModel.active_until.is_(None)) | (DshAgentConversationModel.active_until < now),
            ).update({
                DshAgentConversationModel.status: "running",
                DshAgentConversationModel.active_until: now + timedelta(seconds=max(60, lease_secs)),
                DshAgentConversationModel.updated_at: now,
            }, synchronize_session=False)
            if updated != 1:
                session.rollback()
                row = session.query(DshAgentConversationModel).filter_by(
                    conversation_id=conversation_id, owner_id=owner_id,
                ).one_or_none()
                return {"state": "not_found" if row is None else "busy"}
            row = session.query(DshAgentConversationModel).filter_by(conversation_id=conversation_id).one()
            row.model = selected_model or resolve_model(row.model or None)
            title = prompt.strip().replace("\r", " ").replace("\n", " ")[:80].strip()
            if row.turn_count == 0 and title:
                row.title = title
            row.turn_count = int(row.turn_count or 0) + 1
            row.updated_at = now
            # MySQL timestamps have second precision; the ID orders messages within a second.
            user_message_id = f"msg_z{time.time_ns():020d}_{uuid4().hex}"
            if reference_ids or file_ids:
                from services.dsh_agent.image_media import bind_references

                if reference_ids:
                    bind_references(session, conversation_id, owner_id, user_message_id, reference_ids)
                if file_ids:
                    bind_references(session, conversation_id, owner_id, user_message_id, file_ids, kind="file")
            session.add(DshAgentMessageModel(
                message_id=user_message_id,
                conversation_id=conversation_id,
                owner_id=owner_id,
                role="user",
                status="completed",
                content=prompt,
                model=row.model,
                usage_json=json.dumps({"expert_id": expert_id, "skill_ids": skill_ids}),
                created_at=now,
            ))
            session.commit()
            return {
                "state": "ok",
                "conversation": _conversation_dict(row),
                "user_message_id": user_message_id,
            }
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def complete_turn(
        self,
        conversation_id: str,
        owner_id: str,
        *,
        content: str,
        model: str,
        usage: dict[str, Any],
        activities: list[dict[str, Any]] | None = None,
        timings: dict[str, int] | None = None,
        stopped: bool = False,
    ) -> dict[str, Any]:
        session = self._session()
        try:
            row = session.query(DshAgentConversationModel).filter_by(
                conversation_id=conversation_id, owner_id=owner_id,
            ).with_for_update().one_or_none()
            if row is None:
                raise KeyError("conversation not found")
            now = datetime.now()
            message = self._assistant_message(
                conversation_id, owner_id, content, model, usage, now,
                activities=activities, timings=timings,
                status="stopped" if stopped or row.status == "stopping" else "completed",
            )
            row.status = "idle"
            row.active_until = None
            row.updated_at = now
            session.add(message)
            session.commit()
            return _message_dict(message)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def heartbeat_turn(self, conversation_id: str, owner_id: str, *, lease_secs: int = 900) -> None:
        session = self._session()
        try:
            now = datetime.now()
            session.query(DshAgentConversationModel).filter_by(
                conversation_id=conversation_id, owner_id=owner_id,
            ).filter(DshAgentConversationModel.status.in_(("running", "stopping"))).update({
                DshAgentConversationModel.active_until: now + timedelta(seconds=max(60, lease_secs)),
                DshAgentConversationModel.updated_at: now,
            }, synchronize_session=False)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def fail_turn(self, conversation_id: str, owner_id: str, *, model: str, error_code: str, usage: dict[str, Any] | None = None, message: str = "本次回复失败，请检查智能体配置后重试。") -> None:
        session = self._session()
        try:
            row = session.query(DshAgentConversationModel).filter_by(
                conversation_id=conversation_id, owner_id=owner_id,
            ).one_or_none()
            if row is None:
                return
            now = datetime.now()
            session.add(self._assistant_message(
                conversation_id, owner_id, message, model, usage or {}, now,
                status="error", error_code=error_code,
            ))
            row.status = "error"
            row.active_until = None
            row.updated_at = now
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def request_stop(self, conversation_id: str, owner_id: str, user_message_id: str) -> bool | None:
        session = self._session()
        try:
            row = session.query(DshAgentConversationModel).filter_by(
                conversation_id=conversation_id, owner_id=owner_id,
            ).with_for_update().one_or_none()
            if row is None:
                return None
            latest = session.query(DshAgentMessageModel.message_id).filter_by(
                conversation_id=conversation_id, owner_id=owner_id, role="user",
            ).order_by(DshAgentMessageModel.created_at.desc(), DshAgentMessageModel.message_id.desc()).first()
            if row.status not in {"running", "stopping"} or not latest or latest[0] != user_message_id:
                return False
            row.status = "stopping"
            session.commit()
            return True
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def update_message(self, conversation_id: str, owner_id: str, message_id: str, *, feedback: str | None = None, discard: bool = False) -> dict[str, Any] | None:
        if feedback not in {None, "positive", "negative"}:
            raise ValueError("invalid feedback")
        session = self._session()
        try:
            row = session.query(DshAgentMessageModel).filter_by(
                conversation_id=conversation_id, owner_id=owner_id, message_id=message_id, role="assistant",
            ).with_for_update().one_or_none()
            if row is None:
                return None
            if discard:
                if row.status not in {"stopped", "discarded"}:
                    raise ValueError("只能丢弃已停止的回复")
                # Retain usage for audit; discarded drafts do not appear in chat history.
                row.status = "discarded"
            else:
                row.feedback = feedback
            session.commit()
            return _message_dict(row)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    @staticmethod
    def _assistant_message(
        conversation_id: str,
        owner_id: str,
        content: str,
        model: str,
        usage: dict[str, Any],
        created_at: datetime,
        *,
        status: str = "completed",
        error_code: str = "",
        activities: list[dict[str, Any]] | None = None,
        timings: dict[str, int] | None = None,
    ) -> DshAgentMessageModel:
        def token(name: str) -> int | None:
            value = usage.get(name)
            return max(0, int(value)) if isinstance(value, (int, float)) and not isinstance(value, bool) else None

        return DshAgentMessageModel(
            message_id=f"msg_z{time.time_ns():020d}_{uuid4().hex}", conversation_id=conversation_id, owner_id=owner_id,
            role="assistant", status=status, content=content, model=model,
            input_tokens=token("inputTokens"), output_tokens=token("outputTokens"),
            total_tokens=token("totalTokens"), usage_json=json.dumps(usage, ensure_ascii=False),
            activity_json=json.dumps(activities or [], ensure_ascii=False),
            timings_json=json.dumps(timings or {}, ensure_ascii=False),
            error_code=error_code[:80], created_at=created_at,
        )

    def delete_conversation(self, conversation_id: str, owner_id: str) -> bool:
        session = self._session()
        try:
            row = session.query(DshAgentConversationModel).filter_by(
                conversation_id=conversation_id, owner_id=owner_id,
            ).one_or_none()
            if row is None:
                return False
            now = datetime.now()
            dsh_session_id = row.dsh_session_id
            claimed = session.query(DshAgentConversationModel).filter(
                DshAgentConversationModel.conversation_id == conversation_id,
                DshAgentConversationModel.owner_id == owner_id,
                (DshAgentConversationModel.active_until.is_(None)) | (DshAgentConversationModel.active_until < now),
            ).update({
                DshAgentConversationModel.status: "deleting",
                DshAgentConversationModel.active_until: now + timedelta(minutes=5),
            }, synchronize_session=False)
            if claimed != 1:
                session.rollback()
                raise RuntimeError("conversation is processing")
            session.flush()
            self._delete_dsh_session_files(dsh_session_id, owner_id)
            session.query(DshAgentMediaModel).filter_by(conversation_id=conversation_id).delete(synchronize_session=False)
            session.query(DshAgentMessageModel).filter_by(conversation_id=conversation_id).delete(synchronize_session=False)
            session.query(DshAgentConversationModel).filter_by(
                conversation_id=conversation_id, owner_id=owner_id, status="deleting",
            ).delete(synchronize_session=False)
            session.commit()
            return True
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _delete_dsh_session_files(self, dsh_session_id: str, owner_id: str) -> None:
        validate_session_id(dsh_session_id)
        sessions_roots = [
            user_home(self.dsh_home, owner_id) / "sessions",
            self.dsh_home / "sessions",
        ]
        for sessions_root_value in sessions_roots:
            sessions_root = sessions_root_value.resolve()
            if not sessions_root.is_dir():
                continue
            for workspace_dir in sessions_root.iterdir():
                candidate = workspace_dir / dsh_session_id
                if workspace_dir.is_symlink() or candidate.is_symlink() or not candidate.is_dir():
                    continue
                resolved_workspace = workspace_dir.resolve()
                resolved_candidate = candidate.resolve()
                if resolved_workspace.parent != sessions_root or resolved_candidate.parent != resolved_workspace:
                    continue
                shutil.rmtree(resolved_candidate)

    def admin_usage(self, *, limit: int = 100) -> dict[str, Any]:
        session = self._session()
        try:
            query = session.query(DshAgentMessageModel, DshAgentConversationModel).join(
                DshAgentConversationModel,
                DshAgentConversationModel.conversation_id == DshAgentMessageModel.conversation_id,
            ).filter(DshAgentMessageModel.role == "assistant")
            totals = query.with_entities(
                func.count(DshAgentMessageModel.message_id),
                func.coalesce(func.sum(DshAgentMessageModel.input_tokens), 0),
                func.coalesce(func.sum(DshAgentMessageModel.output_tokens), 0),
                func.coalesce(func.sum(DshAgentMessageModel.total_tokens), 0),
            ).one()
            rows = query.order_by(DshAgentMessageModel.created_at.desc()).limit(limit).all()
            items = []
            for message, conversation in rows:
                item = _message_dict(message)
                item.update({
                    "owner_id": conversation.owner_id,
                    "owner_username": conversation.owner_username,
                    "owner_name": conversation.owner_name,
                    "conversation_title": conversation.title,
                })
                items.append(item)
            return {
                "summary": {
                    "assistant_turns": int(totals[0] or 0),
                    "input_tokens": int(totals[1] or 0),
                    "output_tokens": int(totals[2] or 0),
                    "total_tokens": int(totals[3] or 0),
                    "cost_available": False,
                },
                "items": items,
            }
        finally:
            session.close()

    def close(self) -> None:
        if self.engine is not None:
            self.engine.dispose()
        self.engine = None
        self.Session = None


dsh_agent_store = DshAgentStore()
