"""/v1/render-sessions — WebRTC signaling endpoints.

Flow:
  POST /v1/render-sessions          → server creates row, pushes to
                                      Redis render:pending, returns session_id.
                                      Worker pops it and starts a peer.
  POST /v1/render-sessions/{id}/offer
                                    → publish offer on Redis, await answer.
  POST /v1/render-sessions/{id}/candidate
                                    → publish ICE from client to worker.
  DELETE /v1/render-sessions/{id}   → end the session.

The worker's local ICE candidates fan out via `events:render-session:{id}`
through the existing /v1/stream WS, not via these REST endpoints.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import uuid
from typing import Annotated

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import session_scope
from ..models.enums import RenderSessionStatus
from ..models.model import Model
from ..models.render_session import RenderSession
from ..models.run import Run
from ..schemas import (
    IceCandidate,
    RenderSessionCreate,
    RenderSessionCreated,
    SdpAnswer,
    SdpOffer,
)

router = APIRouter(prefix="/v1/render-sessions", tags=["render-sessions"])

PENDING_KEY = "render:pending"
ANSWER_TIMEOUT_SECONDS = 10


@router.post("", response_model=RenderSessionCreated, status_code=201)
async def create_session(
    body: RenderSessionCreate,
    session: Annotated[AsyncSession, Depends(session_scope)],
) -> RenderSessionCreated:
    if (body.run_id is None) == (body.model_id is None):
        raise HTTPException(400, "exactly one of run_id or model_id required")

    if body.run_id is not None:
        run = await session.get(Run, body.run_id)
        if run is None:
            raise HTTPException(404, "run not found")
    if body.model_id is not None:
        model = await session.get(Model, body.model_id)
        if model is None or model.deleted_at is not None:
            raise HTTPException(404, "model not found")

    row = RenderSession(
        run_id=body.run_id,
        model_id=body.model_id,
        status=RenderSessionStatus.signaling,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)

    redis: aioredis.Redis = aioredis.from_url(get_settings().redis_url)
    try:
        await redis.rpush(PENDING_KEY, str(row.id))
    finally:
        await redis.aclose()

    return RenderSessionCreated(
        session_id=row.id,
        # No TURN/STUN at v1 (LAN-only). Add when product goes external.
        ice_servers=[],
    )


@router.post("/{session_id}/offer", response_model=SdpAnswer)
async def post_offer(session_id: uuid.UUID, body: SdpOffer) -> SdpAnswer:
    """Publish the client's SDP offer to the worker; await its answer."""
    redis: aioredis.Redis = aioredis.from_url(get_settings().redis_url)
    pubsub = redis.pubsub()
    answer_ch = f"render:session:{session_id}:answer"

    # Subscribe BEFORE publishing the offer to avoid the race where the
    # worker answers before we're listening.
    await pubsub.subscribe(answer_ch)

    try:
        await redis.publish(
            f"render:session:{session_id}:offer",
            json.dumps(body.model_dump()),
        )
        try:
            async with asyncio.timeout(ANSWER_TIMEOUT_SECONDS):
                async for message in pubsub.listen():
                    if message.get("type") != "message":
                        continue
                    raw = message["data"]
                    text = raw.decode() if isinstance(raw, bytes) else raw
                    answer = json.loads(text)
                    return SdpAnswer(sdp=answer["sdp"], type=answer["type"])
        except asyncio.TimeoutError as e:
            raise HTTPException(504, "worker did not answer within timeout") from e
        raise HTTPException(500, "answer loop exited unexpectedly")
    finally:
        await pubsub.unsubscribe(answer_ch)
        await pubsub.aclose()
        await redis.aclose()


@router.post("/{session_id}/candidate", status_code=204)
async def post_candidate(session_id: uuid.UUID, body: IceCandidate) -> None:
    """Forward client ICE candidate to the worker."""
    redis: aioredis.Redis = aioredis.from_url(get_settings().redis_url)
    try:
        await redis.publish(
            f"render:session:{session_id}:candidate-in",
            json.dumps(body.model_dump()),
        )
    finally:
        await redis.aclose()


@router.delete("/{session_id}", status_code=204)
async def end_session(
    session_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(session_scope)],
) -> None:
    row = await session.get(RenderSession, session_id)
    if row is None:
        raise HTTPException(404, "session not found")
    if row.ended_at is None:
        row.ended_at = dt.datetime.now(dt.UTC)
    row.status = RenderSessionStatus.closed
    await session.flush()

    # Best-effort: notify the worker so it tears down the peer.
    redis: aioredis.Redis = aioredis.from_url(get_settings().redis_url)
    try:
        await redis.publish(
            f"events:render-session:{session_id}",
            json.dumps({
                "type": "render-session.state",
                "session_id": str(session_id),
                "state": "closed",
                "timestamp": dt.datetime.now(dt.UTC).isoformat(),
            }),
        )
    finally:
        await redis.aclose()
