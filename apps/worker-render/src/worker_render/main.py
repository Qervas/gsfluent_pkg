"""Render worker entry point.

Pops pending sessions from the Redis `render:pending` list and spawns
a coroutine per peer, bounded by `max_concurrent_sessions`.
"""

from __future__ import annotations

import asyncio
import signal
import uuid

import redis.asyncio as aioredis
import structlog

from .config import get_settings
from .peer import run_peer

log = structlog.get_logger("worker_render")

PENDING_KEY = "render:pending"


async def session_supervisor(session_id: uuid.UUID, redis: aioredis.Redis,
                             sem: asyncio.Semaphore) -> None:
    async with sem:
        try:
            await run_peer(session_id, redis)
        except Exception as e:  # noqa: BLE001
            log.error("peer.error", session=str(session_id), error=str(e)[:200])


async def main() -> None:
    s = get_settings()
    redis = aioredis.from_url(s.redis_url)
    sem = asyncio.Semaphore(s.max_concurrent_sessions)
    log.info("worker.start", worker_id=s.worker_id,
             max_sessions=s.max_concurrent_sessions)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    tasks: set[asyncio.Task[None]] = set()
    try:
        while not stop.is_set():
            # BLPOP with timeout so we can respect stop.set() promptly.
            result = await redis.blpop([PENDING_KEY], timeout=2)
            if result is None:
                continue
            _key, raw = result
            session_id_str = raw.decode() if isinstance(raw, bytes) else raw
            try:
                session_id = uuid.UUID(session_id_str)
            except ValueError:
                log.warning("bad_session_id", value=session_id_str)
                continue

            task = asyncio.create_task(
                session_supervisor(session_id, redis, sem),
                name=f"peer-{session_id}",
            )
            tasks.add(task)
            task.add_done_callback(tasks.discard)
    finally:
        log.info("worker.draining", in_flight=len(tasks))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await redis.aclose()
        log.info("worker.stop")


if __name__ == "__main__":
    asyncio.run(main())
