"""Redis-backed event store: real-time pub/sub + replay-from-cursor.

Each channel has a monotonic `seq` (Redis INCR). Last MAX_REPLAY events
per channel are retained in a sorted set so a client that disconnects
mid-stream can ask for everything since the last seq it saw and not
miss any events.

Used from both the api process (where /v1/runs publishes run.queued on
submit) and the worker (which publishes run.started, run.progress, etc.).
"""

from __future__ import annotations

import json
from typing import Any

import redis.asyncio as aioredis
from pydantic import BaseModel

from .events import channel_for

CHANNEL_SEQ_PREFIX = "seq:"
CHANNEL_REPLAY_PREFIX = "events:replay:"
MAX_REPLAY = 1000
REPLAY_TTL_SECONDS = 24 * 60 * 60


async def publish_dict(redis: aioredis.Redis, channel: str, payload: dict[str, Any]) -> int:
    """Lower-level publish — used when the caller already has a dict.
    Returns the assigned seq."""
    seq = int(await redis.incr(CHANNEL_SEQ_PREFIX + channel))
    payload = {**payload, "seq": seq}
    body = json.dumps(payload, default=str)

    await redis.publish(channel, body)

    replay_key = CHANNEL_REPLAY_PREFIX + channel
    await redis.zadd(replay_key, {body: seq})
    await redis.zremrangebyrank(replay_key, 0, -(MAX_REPLAY + 1))
    await redis.expire(replay_key, REPLAY_TTL_SECONDS)
    return seq


async def publish(redis: aioredis.Redis, event: BaseModel) -> int:
    """Higher-level publish — routes the event to its channel by type."""
    channel = channel_for(event)  # type: ignore[arg-type]
    return await publish_dict(redis, channel, event.model_dump(mode="json"))


async def replay_since(
    redis: aioredis.Redis,
    channel: str,
    since_seq: int,
    limit: int = MAX_REPLAY,
) -> list[str]:
    """Returns event JSON strings with seq > since_seq, oldest first."""
    replay_key = CHANNEL_REPLAY_PREFIX + channel
    raw = await redis.zrangebyscore(
        replay_key, since_seq + 0.001, "+inf", start=0, num=limit,
    )
    return [r.decode() if isinstance(r, bytes) else r for r in raw]
