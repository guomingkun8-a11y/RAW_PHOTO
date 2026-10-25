from __future__ import annotations

import asyncio
from contextlib import suppress
import json
import time
from urllib.parse import urlsplit

from fastapi import HTTPException, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from pydantic import ValidationError

from api.support import require_identity
from api.feedback.schemas import MessageInput, ReadInput


def attach_websocket(router, service, realtime):
    @router.websocket("/ws")
    async def chat(socket: WebSocket):
        origin = socket.headers.get("origin")
        if origin and urlsplit(origin).netloc != socket.headers.get("host"):
            await socket.close(code=1008)
            return
        await socket.accept()
        connection_id = None
        writer = None
        try:
            # Authenticate in the first frame; never put session tokens in URLs or proxy logs.
            raw = await asyncio.wait_for(socket.receive_text(), timeout=10)
            if len(raw) > 8192:
                await socket.close(code=1009)
                return
            auth = json.loads(raw)
            if not isinstance(auth, dict) or auth.get("type") != "auth" or not isinstance(auth.get("token"), str):
                await socket.close(code=4401)
                return
            token = "Bearer " + auth["token"]
            identity = await run_in_threadpool(require_identity, token)
            connection_id, queue = await realtime.register(identity, socket)

            async def write():
                while True:
                    try:
                        frame = await asyncio.wait_for(queue.get(), timeout=25)
                    except asyncio.TimeoutError:
                        frame = {"type": "ping"}
                    await socket.send_json(frame)

            writer = asyncio.create_task(write())
            queue.put_nowait({"type": "ready"})
            frame_count = 0
            window_started = last_auth_check = time.monotonic()
            while True:
                raw = await asyncio.wait_for(socket.receive_text(), timeout=65)
                if len(raw) > 65536:
                    await socket.close(code=1009)
                    break
                frame = json.loads(raw)
                if not isinstance(frame, dict):
                    await socket.close(code=1008)
                    break
                if time.monotonic() - window_started > 60:
                    window_started, frame_count = time.monotonic(), 0
                frame_count += 1
                if frame_count > 120:
                    await socket.close(code=1008)
                    break
                request_id = str(frame.get("request_id") or "")[:64]
                try:
                    if frame.get("type") in {"ping", "pong"}:
                        if time.monotonic() - last_auth_check > 20:
                            current = await run_in_threadpool(require_identity, token)
                            if current.get("role") != identity.get("role") or current.get("id") != identity.get("id"):
                                await socket.close(code=4401)
                                break
                            last_auth_check = time.monotonic()
                        await realtime.touch(connection_id)
                        if frame["type"] == "ping":
                            queue.put_nowait({"type": "pong"})
                        continue
                    if frame.get("type") == "send":
                        identity = await run_in_threadpool(require_identity, token)
                        await realtime.limit(identity)
                        body = MessageInput.model_validate(frame.get("message") or {})
                        thread_id = str(frame.get("thread_id") or "")[:36]
                        result = await run_in_threadpool(service.store.send_message, identity, thread_id, body.model_dump())
                        queue.put_nowait({"type": "ack", "request_id": request_id, "message": result})
                    elif frame.get("type") == "read":
                        body = ReadInput.model_validate(frame)
                        result = await run_in_threadpool(service.store.mark_read, identity, str(frame.get("thread_id") or "")[:36], body.seq)
                        queue.put_nowait({"type": "ack", "request_id": request_id, **result})
                    else:
                        queue.put_nowait({"type": "error", "request_id": request_id, "status": 422, "message": "不支持的消息类型"})
                except (HTTPException, ValidationError) as exc:
                    status = exc.status_code if isinstance(exc, HTTPException) else 422
                    detail = exc.detail if isinstance(exc, HTTPException) else "消息格式不正确"
                    queue.put_nowait({"type": "error", "request_id": request_id, "status": status, "message": detail})
                except Exception:
                    queue.put_nowait({"type": "error", "request_id": request_id, "status": 503, "message": "消息未确认保存，请重试"})
        except HTTPException:
            with suppress(Exception):
                await socket.close(code=4401)
        except (WebSocketDisconnect, asyncio.TimeoutError, ValueError, TypeError):
            with suppress(Exception):
                await socket.close(code=1008)
        finally:
            if writer:
                writer.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await writer
            if connection_id:
                await realtime.unregister(connection_id)

