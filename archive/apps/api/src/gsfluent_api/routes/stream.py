"""WS /v1/stream — per-connection subscribe/unsubscribe + replay.

Protocol (client→server):
  {"subscribe":   ["events:runs:abc", ...]}
  {"unsubscribe": ["events:runs:abc", ...]}
  {"replay_since": {"events:runs:abc": 42, ...}}     # request catch-up

Server→client: raw event JSON (as published by event_store.publish).
"""

from __future__ import annotations

import asyncio
import json

import redis.asyncio as aioredis
import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..config import get_settings
from ..event_store import replay_since

log = structlog.get_logger("stream")

router = APIRouter(tags=["stream"])


@router.websocket("/v1/stream")
async def stream(ws: WebSocket) -> None:
    await ws.accept()
    s = get_settings()
    redis = aioredis.from_url(s.redis_url)
    pubsub = redis.pubsub()
    subscribed: set[str] = set()

    log.info("ws.connect", remote=str(ws.client))

    async def handle_incoming(msg: dict[str, object]) -> None:
        if "subscribe" in msg:
            channels = [c for c in msg["subscribe"] if isinstance(c, str)]  # type: ignore[union-attr]
            new = [c for c in channels if c not in subscribed]
            if new:
                await pubsub.subscribe(*new)
                subscribed.update(new)
        if "unsubscribe" in msg:
            channels = [c for c in msg["unsubscribe"] if isinstance(c, str)]  # type: ignore[union-attr]
            drop = [c for c in channels if c in subscribed]
            if drop:
                await pubsub.unsubscribe(*drop)
                subscribed.difference_update(drop)
        if "replay_since" in msg:
            cursors = msg["replay_since"]
            if isinstance(cursors, dict):
                for ch, seq in cursors.items():
                    try:
                        seq_int = int(seq)  # type: ignore[arg-type]
                    except (TypeError, ValueError):
                        continue
                    for body in await replay_since(redis, ch, seq_int):
                        await ws.send_text(body)

    async def receive_loop() -> None:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send_text(json.dumps({"error": "bad json"}))
                continue
            if not isinstance(msg, dict):
                continue
            await handle_incoming(msg)

    async def send_loop() -> None:
        async for message in pubsub.listen():
            if message.get("type") != "message":
                continue
            payload = message["data"]
            text = payload.decode() if isinstance(payload, bytes) else payload
            await ws.send_text(text)

    rx = asyncio.create_task(receive_loop(), name="ws-rx")
    tx = asyncio.create_task(send_loop(), name="ws-tx")
    try:
        done, pending = await asyncio.wait(
            [rx, tx], return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
    except WebSocketDisconnect:
        pass
    finally:
        if subscribed:
            try:
                await pubsub.unsubscribe(*subscribed)
            except Exception:  # noqa: BLE001
                pass
        await pubsub.aclose()
        await redis.aclose()
        log.info("ws.disconnect")
