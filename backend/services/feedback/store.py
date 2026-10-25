from __future__ import annotations

from datetime import UTC, datetime, timedelta
from functools import wraps
import hashlib
import json
import random
import threading
import time
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, and_, create_engine, func, or_
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import declarative_base, sessionmaker

from services.platform.enterprise_schema import resolve_enterprise_database_url


FeedbackBase = declarative_base()


def now():
    return datetime.now(UTC).replace(tzinfo=None)


def stamp(value):
    return value.isoformat(timespec="milliseconds") + "Z" if value else None


def admin(identity):
    return identity.get("role") == "admin"


def uid(identity):
    value = str(identity.get("id") or "").strip()
    if not value:
        raise HTTPException(401, "请使用系统账户登录")
    return value


def display_name(identity):
    return str(identity.get("name") or identity.get("username") or uid(identity))[:120]


def retry_transaction(method):
    @wraps(method)
    def run(*args, **kwargs):
        for attempt in range(3):
            try:
                return method(*args, **kwargs)
            except OperationalError as exc:
                code = getattr(exc.orig, "args", [None])[0]
                if attempt == 2 or (code not in {1205, 1213} and "database is locked" not in str(exc.orig).lower()):
                    raise
                time.sleep((attempt + 1) * 0.025 + random.random() * 0.025)
    return run


class FeedbackThread(FeedbackBase):
    __tablename__ = "feedback_threads"
    __table_args__ = (
        UniqueConstraint("owner_id", "client_id", name="uq_feedback_thread_request"),
        Index("ix_feedback_owner_updated", "owner_id", "updated_at", "id"),
        Index("ix_feedback_status_updated", "status", "updated_at", "id"),
    )
    id = Column(String(36), primary_key=True)
    owner_id = Column(String(191), nullable=False)
    owner_name = Column(String(120), nullable=False)
    client_id = Column(String(64), nullable=False)
    title = Column(String(160), nullable=False)
    category = Column(String(20), nullable=False)
    status = Column(String(20), nullable=False, default="pending")
    assigned_to = Column(String(191), nullable=True)
    assigned_name = Column(String(120), nullable=True)
    source_page = Column(String(512), nullable=False, default="")
    task_id = Column(String(191), nullable=False, default="")
    app_version = Column(String(64), nullable=False, default="")
    last_seq = Column(Integer, nullable=False, default=0)
    public_seq = Column(Integer, nullable=False, default=0)
    preview = Column(String(240), nullable=False, default="")
    created_at = Column(DateTime, nullable=False, default=now)
    updated_at = Column(DateTime, nullable=False, default=now)


class FeedbackMessage(FeedbackBase):
    __tablename__ = "feedback_messages"
    __table_args__ = (
        UniqueConstraint("thread_id", "seq", name="uq_feedback_message_seq"),
        UniqueConstraint("thread_id", "sender_id", "client_id", name="uq_feedback_message_request"),
        Index("ix_feedback_message_cursor", "thread_id", "internal", "seq"),
        Index("ix_feedback_sender_created", "sender_id", "created_at"),
    )
    id = Column(String(36), primary_key=True)
    thread_id = Column(String(36), ForeignKey("feedback_threads.id"), nullable=False)
    seq = Column(Integer, nullable=False)
    sender_id = Column(String(191), nullable=False)
    sender_name = Column(String(120), nullable=False)
    sender_role = Column(String(20), nullable=False)
    client_id = Column(String(64), nullable=False)
    request_hash = Column(String(64), nullable=False)
    content = Column(Text, nullable=False)
    internal = Column(Boolean, nullable=False, default=False)
    kind = Column(String(20), nullable=False, default="message")
    created_at = Column(DateTime, nullable=False, default=now)


class FeedbackAttachment(FeedbackBase):
    __tablename__ = "feedback_attachments"
    __table_args__ = (Index("ix_feedback_attachment_owner_created", "owner_id", "created_at"),)
    id = Column(String(36), primary_key=True)
    owner_id = Column(String(191), nullable=False, index=True)
    message_id = Column(String(36), ForeignKey("feedback_messages.id"), nullable=True, index=True)
    filename = Column(String(180), nullable=False)
    object_key = Column(String(512), nullable=False)
    mime_type = Column(String(40), nullable=False)
    size = Column(Integer, nullable=False)
    created_at = Column(DateTime, nullable=False, default=now)


class FeedbackRead(FeedbackBase):
    __tablename__ = "feedback_reads"
    thread_id = Column(String(36), ForeignKey("feedback_threads.id"), primary_key=True)
    reader_id = Column(String(191), primary_key=True)
    last_seq = Column(Integer, nullable=False, default=0)


class FeedbackOutbox(FeedbackBase):
    __tablename__ = "feedback_outbox"
    __table_args__ = (Index("ix_feedback_outbox_due", "kind", "sent_at", "available_at", "lease_until"),)
    id = Column(String(36), primary_key=True)
    kind = Column(String(20), nullable=False)
    payload = Column(JSON, nullable=False)
    available_at = Column(DateTime, nullable=False, default=now)
    lease_until = Column(DateTime, nullable=True)
    lease_token = Column(String(36), nullable=True)
    attempts = Column(Integer, nullable=False, default=0)
    sent_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=now)


class FeedbackSettings(FeedbackBase):
    __tablename__ = "feedback_settings"
    id = Column(String(20), primary_key=True, default="default")
    notify_enabled = Column(Boolean, nullable=False, default=False)
    site_url = Column(String(512), nullable=False, default="")


class FeedbackStore:
    def __init__(self, database_url=None):
        self.database_url = database_url
        self.engine = None
        self.Session = None
        self._lock = threading.Lock()

    def initialize(self):
        if self.Session:
            return
        with self._lock:
            if self.Session:
                return
            url = self.database_url or resolve_enterprise_database_url()
            options = {"pool_pre_ping": True, "pool_recycle": 1800}
            if url.startswith("sqlite"):
                options["connect_args"] = {"check_same_thread": False, "timeout": 30}
            else:
                options.update(pool_size=2, max_overflow=0, pool_timeout=10)
            engine = create_engine(url, **options)
            try:
                FeedbackBase.metadata.create_all(engine)
            except Exception:
                engine.dispose()
                raise
            self.engine = engine
            self.Session = sessionmaker(engine, expire_on_commit=False)

    def session(self):
        self.initialize()
        return self.Session()

    def close(self):
        if self.engine:
            self.engine.dispose()
        self.engine = self.Session = None

    def _thread(self, session, identity, thread_id, *, lock=False):
        query = session.query(FeedbackThread).filter_by(id=thread_id)
        if not admin(identity):
            query = query.filter_by(owner_id=uid(identity))
        row = (query.with_for_update() if lock else query).first()
        if row is None:
            raise HTTPException(404, "反馈不存在或无权访问")
        return row

    @staticmethod
    def attachment(row):
        return {key: getattr(row, key) for key in ("id", "filename", "mime_type", "size")}

    def message(self, session, row, attachments=None):
        items = attachments if attachments is not None else session.query(FeedbackAttachment).filter_by(message_id=row.id).all()
        return {
            **{key: getattr(row, key) for key in ("id", "thread_id", "seq", "sender_id", "sender_name", "sender_role", "client_id", "content", "internal", "kind")},
            "created_at": stamp(row.created_at), "attachments": [self.attachment(item) for item in items],
        }

    def _unread(self, identity):
        query = or_(FeedbackRead.last_seq.is_(None), FeedbackMessage.seq > FeedbackRead.last_seq)
        return and_(query, FeedbackMessage.sender_id != uid(identity), True if admin(identity) else FeedbackMessage.internal.is_(False))

    def thread(self, session, row, identity, unread=None, receipts=None):
        if unread is None:
            unread = session.query(func.count(FeedbackMessage.id)).outerjoin(
                FeedbackRead, and_(FeedbackRead.thread_id == FeedbackMessage.thread_id, FeedbackRead.reader_id == uid(identity)),
            ).filter(FeedbackMessage.thread_id == row.id, self._unread(identity)).scalar()
        receipts = receipts if receipts is not None else session.query(FeedbackRead).filter_by(thread_id=row.id).all()
        read_seq = next((item.last_seq for item in receipts if item.reader_id == uid(identity)), 0)
        other_read_seq = max((item.last_seq for item in receipts if (item.reader_id != row.owner_id if uid(identity) == row.owner_id else item.reader_id == row.owner_id)), default=0)
        return {
            **{key: getattr(row, key) for key in ("id", "owner_id", "owner_name", "title", "category", "status", "assigned_to", "assigned_name", "source_page", "task_id", "app_version", "preview")},
            "last_seq": row.last_seq if admin(identity) else row.public_seq,
            "read_seq": read_seq,
            "other_read_seq": other_read_seq,
            "unread": unread or 0, "created_at": stamp(row.created_at), "updated_at": stamp(row.updated_at),
        }

    def list_threads(self, identity, *, page=1, limit=25, status="", category="", search="", unread_only=False):
        with self.session() as session:
            unread = session.query(FeedbackMessage.thread_id, func.count(FeedbackMessage.id).label("n")).outerjoin(
                FeedbackRead, and_(FeedbackRead.thread_id == FeedbackMessage.thread_id, FeedbackRead.reader_id == uid(identity)),
            ).filter(self._unread(identity)).group_by(FeedbackMessage.thread_id).subquery()
            query = session.query(FeedbackThread, func.coalesce(unread.c.n, 0)).outerjoin(unread, unread.c.thread_id == FeedbackThread.id)
            if not admin(identity):
                query = query.filter(FeedbackThread.owner_id == uid(identity))
            if status:
                query = query.filter(FeedbackThread.status == status)
            if category:
                query = query.filter(FeedbackThread.category == category)
            if unread_only:
                query = query.filter(unread.c.n > 0)
            if search:
                term = "%" + search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
                query = query.filter(or_(FeedbackThread.title.like(term, escape="\\"), FeedbackThread.owner_name.like(term, escape="\\")))
            total = query.count()
            rows = query.order_by(FeedbackThread.updated_at.desc(), FeedbackThread.id).offset((page - 1) * limit).limit(limit).all()
            ids = [row.id for row, _ in rows]
            grouped = {}
            if ids:
                for receipt in session.query(FeedbackRead).filter(FeedbackRead.thread_id.in_(ids)).all():
                    grouped.setdefault(receipt.thread_id, []).append(receipt)
            return {"items": [self.thread(session, row, identity, n, grouped.get(row.id, [])) for row, n in rows], "total": total, "page": page, "limit": limit}

    def unread_count(self, identity):
        with self.session() as session:
            query = session.query(func.count(FeedbackMessage.id)).join(FeedbackThread).outerjoin(
                FeedbackRead, and_(FeedbackRead.thread_id == FeedbackMessage.thread_id, FeedbackRead.reader_id == uid(identity)),
            ).filter(self._unread(identity))
            if not admin(identity):
                query = query.filter(FeedbackThread.owner_id == uid(identity))
            return {"unread": query.scalar() or 0}

    def get_thread(self, identity, thread_id):
        with self.session() as session:
            return self.thread(session, self._thread(session, identity, thread_id), identity)

    def messages(self, identity, thread_id, *, before=0, after=0, limit=50):
        with self.session() as session:
            thread = self._thread(session, identity, thread_id)
            query = session.query(FeedbackMessage).filter_by(thread_id=thread_id)
            if not admin(identity):
                query = query.filter_by(internal=False)
            if before:
                query = query.filter(FeedbackMessage.seq < before)
            if after:
                query = query.filter(FeedbackMessage.seq > after)
            ordered = query.order_by(FeedbackMessage.seq.asc() if after else FeedbackMessage.seq.desc()).limit(limit + 1).all()
            more = len(ordered) > limit
            rows = ordered[:limit] if after else list(reversed(ordered[:limit]))
            ids = [row.id for row in rows]
            files = session.query(FeedbackAttachment).filter(FeedbackAttachment.message_id.in_(ids)).all() if ids else []
            grouped = {}
            for item in files:
                grouped.setdefault(item.message_id, []).append(item)
            return {"items": [self.message(session, row, grouped.get(row.id, [])) for row in rows], "has_more": more, "thread": self.thread(session, thread, identity)}

    @staticmethod
    def _event(session, thread, *, internal=False, event="feedback.changed", actor_id=""):
        session.add(FeedbackOutbox(id=str(uuid4()), kind="realtime", payload={
            "type": event, "thread_id": thread.id, "owner_id": thread.owner_id,
            "internal": internal, "seq": thread.last_seq, "actor_id": actor_id,
        }))

    def _append(self, session, thread, identity, data, *, kind="message"):
        internal = bool(data.get("internal"))
        if internal and not admin(identity):
            raise HTTPException(403, "仅管理员可以添加内部备注")
        content = str(data.get("content") or "").strip()
        attachment_ids = list(dict.fromkeys(data.get("attachment_ids") or []))
        if not content and not attachment_ids:
            raise HTTPException(422, "请输入消息或上传截图")
        fingerprint = hashlib.sha256(json.dumps([content, sorted(attachment_ids), internal], ensure_ascii=False).encode()).hexdigest()
        existing = session.query(FeedbackMessage).filter_by(thread_id=thread.id, sender_id=uid(identity), client_id=data["client_id"]).first()
        if existing:
            if existing.request_hash != fingerprint:
                raise HTTPException(409, "发送标识已用于另一条消息")
            return existing
        files = session.query(FeedbackAttachment).filter(FeedbackAttachment.id.in_(attachment_ids)).with_for_update().all() if attachment_ids else []
        if len(files) != len(attachment_ids) or any(item.owner_id != uid(identity) or item.message_id for item in files):
            raise HTTPException(403, "截图不存在、已发送或不属于当前用户")
        thread.last_seq += 1
        message = FeedbackMessage(
            id=str(uuid4()), thread_id=thread.id, seq=thread.last_seq, sender_id=uid(identity),
            sender_name=display_name(identity), sender_role="admin" if admin(identity) else "user",
            client_id=data["client_id"], request_hash=fingerprint, content=content, internal=internal, kind=kind,
        )
        session.add(message)
        session.flush()
        for item in files:
            item.message_id = message.id
        if not internal:
            thread.public_seq = thread.last_seq
            thread.preview = content[:240] or "[截图]"
            thread.updated_at = now()
            if kind == "message" and not admin(identity) and thread.status in {"resolved", "waiting_user"}:
                thread.status = "processing"
        self._event(session, thread, internal=internal, actor_id=uid(identity))
        if kind == "message" and not internal:
            settings = session.get(FeedbackSettings, "default")
            if settings and settings.notify_enabled:
                session.add(FeedbackOutbox(id=str(uuid4()), kind="wecom", payload={
                    "thread_id": thread.id, "message_id": message.id, "delivered": [],
                }))
        return message

    @retry_transaction
    def create_thread(self, identity, data):
        with self.session() as session:
            row = session.query(FeedbackThread).filter_by(owner_id=uid(identity), client_id=data["client_id"]).first()
            if row:
                initial = session.query(FeedbackMessage).filter_by(thread_id=row.id, sender_id=uid(identity), client_id=data["client_id"]).first()
                fingerprint = hashlib.sha256(json.dumps([str(data.get("content") or "").strip(), sorted(set(data.get("attachment_ids") or [])), False], ensure_ascii=False).encode()).hexdigest()
                if not initial or initial.request_hash != fingerprint:
                    raise HTTPException(409, "发送标识已用于另一条反馈")
                return self.thread(session, row, identity)
            row = FeedbackThread(
                id=str(uuid4()), owner_id=uid(identity), owner_name=display_name(identity), client_id=data["client_id"],
                title=data.get("title") or str(data.get("content") or "截图反馈")[:80], category=data.get("category", "bug"),
                source_page=data.get("source_page", ""), task_id=data.get("task_id", ""), app_version=data.get("app_version", ""),
                last_seq=0, public_seq=0,
            )
            session.add(row)
            try:
                session.flush()
                self._append(session, row, identity, {**data, "internal": False})
                session.commit()
            except IntegrityError:
                session.rollback()
                existing = session.query(FeedbackThread).filter_by(owner_id=uid(identity), client_id=data["client_id"]).first()
                if not existing:
                    raise
                row = existing
            return self.thread(session, row, identity)

    @retry_transaction
    def send_message(self, identity, thread_id, data):
        with self.session() as session:
            thread = self._thread(session, identity, thread_id, lock=True)
            message = self._append(session, thread, identity, data)
            session.commit()
            return self.message(session, message)

    @retry_transaction
    def mark_read(self, identity, thread_id, seq):
        with self.session() as session:
            thread = self._thread(session, identity, thread_id, lock=True)
            target = min(seq, thread.last_seq if admin(identity) else thread.public_seq)
            row = session.get(FeedbackRead, (thread_id, uid(identity)))
            if row is None:
                row = FeedbackRead(thread_id=thread_id, reader_id=uid(identity), last_seq=0)
                session.add(row)
            if target > row.last_seq:
                row.last_seq = target
                self._event(session, thread, event="feedback.read", actor_id=uid(identity))
            session.commit()
            return {"read_seq": row.last_seq}

    @retry_transaction
    def update_thread(self, identity, thread_id, changes):
        with self.session() as session:
            thread = self._thread(session, identity, thread_id, lock=True)
            status = changes.get("status")
            if not admin(identity) and (set(changes) != {"status"} or thread.status != "resolved" or status != "processing"):
                raise HTTPException(403, "仅管理员可以修改处理信息，用户可以重新打开已解决的问题")
            if "claim" in changes:
                if changes["claim"]:
                    thread.assigned_to, thread.assigned_name = uid(identity), display_name(identity)
                else:
                    thread.assigned_to = thread.assigned_name = None
            if "assigned_to" in changes:
                thread.assigned_to = changes["assigned_to"] or None
                thread.assigned_name = changes.get("assigned_name")
            if status and status != thread.status:
                thread.status = status
                labels = {"pending": "待处理", "processing": "处理中", "waiting_user": "待用户补充", "resolved": "已解决"}
                self._append(session, thread, identity, {"client_id": str(uuid4()), "content": f"将问题标记为{labels[status]}"}, kind="status")
            else:
                self._event(session, thread, actor_id=uid(identity))
            session.commit()
            return self.thread(session, thread, identity)

    def add_attachment(self, identity, values):
        with self.session() as session:
            row = FeedbackAttachment(owner_id=uid(identity), **values)
            session.add(row)
            session.commit()
            return self.attachment(row)

    def get_attachment(self, identity, attachment_id):
        with self.session() as session:
            row = session.get(FeedbackAttachment, attachment_id)
            if row is None:
                raise HTTPException(404, "截图不存在")
            if row.message_id:
                message = session.get(FeedbackMessage, row.message_id)
                self._thread(session, identity, message.thread_id)
                if message.internal and not admin(identity):
                    raise HTTPException(404, "截图不存在")
            elif row.owner_id != uid(identity):
                raise HTTPException(404, "截图不存在")
            return {**self.attachment(row), "object_key": row.object_key}

    def notification_message(self, thread_id, message_id=None):
        with self.session() as session:
            query = session.query(FeedbackMessage).filter_by(thread_id=thread_id, internal=False, kind="message")
            if message_id:
                query = query.filter_by(id=message_id)
            else:
                # Pending notifications from before message-level delivery contain only a thread ID.
                query = query.filter_by(sender_role="user").order_by(FeedbackMessage.seq.desc())
            row = query.first()
            return self.message(session, row) if row else None

    def settings(self, changes=None):
        with self.session() as session:
            row = session.get(FeedbackSettings, "default")
            if row is None:
                row = FeedbackSettings(id="default", notify_enabled=False, site_url="")
                session.add(row)
            if changes:
                for key, value in changes.items():
                    setattr(row, key, value)
            session.commit()
            backlog = session.query(func.count(FeedbackOutbox.id)).filter(FeedbackOutbox.sent_at.is_(None)).scalar()
            return {"notify_enabled": row.notify_enabled, "site_url": row.site_url, "pending_events": backlog or 0}

    def claim_events(self, kind, limit=20):
        claimed = []
        with self.session() as session:
            due = and_(FeedbackOutbox.kind == kind, FeedbackOutbox.sent_at.is_(None), FeedbackOutbox.available_at <= now(), or_(FeedbackOutbox.lease_until.is_(None), FeedbackOutbox.lease_until < now()))
            ids = [item[0] for item in session.query(FeedbackOutbox.id).filter(due).order_by(FeedbackOutbox.available_at).limit(limit).all()]
            for event_id in ids:
                token = str(uuid4())
                changed = session.query(FeedbackOutbox).filter(FeedbackOutbox.id == event_id, due).update({"lease_token": token, "lease_until": now() + timedelta(seconds=120)}, synchronize_session=False)
                if changed:
                    row = session.get(FeedbackOutbox, event_id)
                    claimed.append({"id": row.id, "token": token, "payload": dict(row.payload), "attempts": row.attempts})
            session.commit()
        return claimed

    def finish_event(self, event, *, success=True, payload=None):
        with self.session() as session:
            changes = {"lease_token": None, "lease_until": None}
            if payload is not None:
                changes["payload"] = payload
            if success:
                changes["sent_at"] = now()
            else:
                attempts = event["attempts"] + 1
                changes.update(attempts=attempts, available_at=now() + timedelta(seconds=min(60, 2 ** min(attempts, 6))))
            session.query(FeedbackOutbox).filter_by(id=event["id"], lease_token=event["token"]).update(changes, synchronize_session=False)
            session.commit()

    def prune_events(self):
        with self.session() as session:
            session.query(FeedbackOutbox).filter(FeedbackOutbox.sent_at < now() - timedelta(days=7)).delete(synchronize_session=False)
            session.commit()


feedback_store = FeedbackStore()
