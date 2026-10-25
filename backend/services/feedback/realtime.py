from __future__ import annotations

import asyncio
from contextlib import suppress
import hashlib
import json
import os
import time
from uuid import uuid4

from fastapi import HTTPException
from redis.asyncio import Redis
from sqlalchemy import func

from services.feedback.store import FeedbackAttachment, FeedbackMessage, FeedbackThread, admin, now, uid
from services.platform.config import config
from services.platform.enterprise_schema import resolve_enterprise_database_url
from utils.log import logger


class FeedbackRealtime:
    def __init__(self, service):
        self.service = service
        self.store = service.store
        self.redis = None
        self.channel = ""
        self.connections = {}
        self.tasks = []
        self.redis_ready = False

    async def start(self):
        await asyncio.to_thread(self.store.initialize)
        database_url = self.store.database_url or resolve_enterprise_database_url()
        scope = hashlib.sha256(database_url.encode()).hexdigest()[:16]
        self.channel = f"gmkraw:feedback:{scope}"
        redis_url = os.getenv("FEEDBACK_REDIS_URL") or config.get_image_task_queue_settings().get("redis_url")
        if redis_url:
            self.redis = Redis.from_url(redis_url, protocol=2, decode_responses=True, socket_connect_timeout=3, socket_timeout=5)
            self.tasks.append(asyncio.create_task(self._listen()))
        self.tasks.extend(asyncio.create_task(self._outbox(kind)) for kind in ("realtime", "wecom"))
        self.tasks.append(asyncio.create_task(self._cleanup()))

    async def stop(self):
        for _, _, _, socket in list(self.connections.values()):
            with suppress(Exception):
                await socket.close(code=1012)
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()
        if self.redis:
            await self.redis.aclose()

    async def fanout(self, envelope):
        payload = envelope.get("data") or {}
        for identity, queue, _, socket in list(self.connections.values()):
            if not admin(identity) and (payload.get("internal") or payload.get("owner_id") != uid(identity)):
                continue
            try:
                queue.put_nowait(envelope)
            except asyncio.QueueFull:
                # Reconnect and read persisted messages instead of silently dropping a slow client's events.
                with suppress(Exception):
                    await socket.close(code=1013, reason="请重新连接以同步消息")

    async def _listen(self):
        while True:
            try:
                async with self.redis.pubsub() as subscriber:
                    await subscriber.subscribe(self.channel)
                    self.redis_ready = True
                    async for message in subscriber.listen():
                        if message["type"] == "message":
                            await self.fanout(json.loads(message["data"]))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.redis_ready = False
                logger.info({"event": "feedback_redis_reconnecting", "error_type": type(exc).__name__})
                await asyncio.sleep(3)
            finally:
                self.redis_ready = False

    async def _outbox(self, kind):
        last_cleanup = 0.0
        while True:
            try:
                events = await asyncio.to_thread(self.store.claim_events, kind, 20 if kind == "realtime" else 1)
                for event in events:
                    try:
                        if kind == "realtime":
                            envelope = {"type": "event", "event_id": event["id"], "data": event["payload"]}
                            if not self.redis_ready:
                                await self.fanout(envelope)
                                raise RuntimeError("Redis unavailable; persisted event will retry")
                            await self.redis.publish(self.channel, json.dumps(envelope))
                        else:
                            await asyncio.to_thread(self.service.notification, event)
                        await asyncio.to_thread(self.store.finish_event, event, payload=event["payload"])
                    except Exception as exc:
                        await asyncio.to_thread(self.store.finish_event, event, success=False, payload=event["payload"])
                        logger.info({"event": "feedback_delivery_retry", "kind": kind, "error_type": type(exc).__name__, "attempt": event["attempts"] + 1})
                if kind == "realtime" and time.monotonic() - last_cleanup > 3600:
                    await asyncio.to_thread(self.store.prune_events)
                    last_cleanup = time.monotonic()
                await asyncio.sleep(0.5 if events else 1)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.info({"event": "feedback_outbox_retry", "error_type": type(exc).__name__})
                await asyncio.sleep(3)

    async def _cleanup(self):
        while True:
            try:
                await asyncio.to_thread(self.service.cleanup_unused_attachments)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.info({"event": "feedback_attachment_cleanup_failed", "error_type": type(exc).__name__})
            await asyncio.sleep(3600)

    async def limit(self, identity, action="message"):
        limit = 40 if action == "message" else 12
        identifier = hashlib.sha256(uid(identity).encode()).hexdigest()[:24]
        if self.redis:
            try:
                key = f"{self.channel}:rate:{action}:{identifier}:{int(time.time()) // 60}"
                count = await self.redis.eval("local n=redis.call('INCR',KEYS[1]); if n==1 then redis.call('EXPIRE',KEYS[1],70) end; return n", 1, key)
                if count > limit:
                    raise HTTPException(429, "操作较频繁，请稍后重试")
                return
            except HTTPException:
                raise
            except Exception:
                pass
        # Database fallback remains effective across API processes when Redis is unavailable.
        def count_recent():
            from datetime import timedelta
            with self.store.session() as session:
                model = FeedbackThread if action == "thread" else FeedbackAttachment if action == "upload" else FeedbackMessage
                owner = model.sender_id if action == "message" else model.owner_id
                return session.query(func.count(model.id)).filter(owner == uid(identity), model.created_at > now() - timedelta(minutes=1)).scalar()
        if await asyncio.to_thread(count_recent) >= limit:
            raise HTTPException(429, "操作较频繁，请稍后重试")

    async def register(self, identity, socket):
        identifier = str(uuid4())
        owner = uid(identity)
        key = f"{self.channel}:connections:{hashlib.sha256(owner.encode()).hexdigest()[:24]}"
        if self.redis:
            try:
                script = "redis.call('ZREMRANGEBYSCORE',KEYS[1],'-inf',ARGV[1]); if redis.call('ZCARD',KEYS[1])>=8 then return 0 end; redis.call('ZADD',KEYS[1],ARGV[2],ARGV[3]); redis.call('EXPIRE',KEYS[1],90); return 1"
                accepted = await self.redis.eval(script, 1, key, time.time(), time.time() + 75, identifier)
                if not accepted:
                    raise HTTPException(429, "打开的聊天连接过多，请关闭部分页面")
            except HTTPException:
                raise
            except Exception:
                if sum(uid(item[0]) == owner for item in self.connections.values()) >= 8:
                    raise HTTPException(429, "打开的聊天连接过多")
        queue = asyncio.Queue(maxsize=128)
        self.connections[identifier] = (identity, queue, key, socket)
        return identifier, queue

    async def touch(self, identifier):
        if self.redis and identifier in self.connections:
            key = self.connections[identifier][2]
            with suppress(Exception):
                await self.redis.zadd(key, {identifier: time.time() + 75})
                await self.redis.expire(key, 90)

    async def unregister(self, identifier):
        record = self.connections.pop(identifier, None)
        if record and self.redis:
            with suppress(Exception):
                await self.redis.zrem(record[2], identifier)
