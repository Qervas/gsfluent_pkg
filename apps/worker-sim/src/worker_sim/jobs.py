"""Arq job definitions.

Phase 3 Task 3.2: scaffold with status transitions only. Real engine
integration lands in Task 3.6 (separate commit) once the engine bridge
is wired.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import redis.asyncio as aioredis
import structlog
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from .config import get_settings

log = structlog.get_logger("worker_sim")

CANCEL_KEY_PREFIX = "cancel:"
SIM_RUNNING_CHANNEL = "gpu.sim_running"


_engine: AsyncEngine | None = None


def _engine_singleton() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    return _engine


async def _set_run_status(run_id: uuid.UUID, **fields: Any) -> None:
    # Imported lazily so worker doesn't depend on the api package at import time.
    from gsfluent_api.models.run import Run  # noqa: PLC0415

    engine = _engine_singleton()
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        await session.execute(update(Run).where(Run.id == run_id).values(**fields))
        await session.commit()


async def _is_cancelled(run_id: uuid.UUID) -> bool:
    s = get_settings()
    client = aioredis.from_url(s.redis_url)
    try:
        return bool(await client.exists(CANCEL_KEY_PREFIX + str(run_id)))
    finally:
        await client.aclose()


async def _publish_sim_running(redis: aioredis.Redis, running: bool) -> None:
    """Notify worker-render so it can throttle while sim runs (spec §8.2)."""
    await redis.publish(SIM_RUNNING_CHANNEL, "1" if running else "0")


async def run_sim_job(ctx: dict[str, Any], run_id_str: str) -> dict[str, Any]:
    """Phase 3.2 scaffold with Phase 4 event publishing.
    Replaced by real engine call in Task 3.6."""
    from gsfluent_api.event_store import publish as publish_event  # noqa: PLC0415
    from gsfluent_api.events import (  # noqa: PLC0415
        RunCancelledEvent,
        RunCompletedEvent,
        RunStartedEvent,
    )
    from gsfluent_api.models.enums import RunStatus  # noqa: PLC0415

    s = get_settings()
    run_id = uuid.UUID(run_id_str)
    redis: aioredis.Redis = ctx["redis"]

    log.info("job.start", run_id=str(run_id))

    await _set_run_status(
        run_id,
        worker_id=s.worker_id,
        started_at=dt.datetime.now(dt.UTC),
        status=RunStatus.running,
    )
    await publish_event(redis, RunStartedEvent(run_id=run_id, worker_id=s.worker_id))

    await _publish_sim_running(redis, True)
    try:
        # Placeholder: a real engine call (Task 3.6) will iterate frames,
        # check cancellation, write artifacts. For now: pretend we ran.
        if await _is_cancelled(run_id):
            await _set_run_status(
                run_id,
                status=RunStatus.cancelled,
                completed_at=dt.datetime.now(dt.UTC),
            )
            await publish_event(redis, RunCancelledEvent(run_id=run_id))
            return {"run_id": run_id_str, "status": "cancelled"}

        await _set_run_status(
            run_id,
            status=RunStatus.completed,
            completed_at=dt.datetime.now(dt.UTC),
        )
        await publish_event(
            redis,
            RunCompletedEvent(run_id=run_id, gpu_seconds=0.0),
        )
    finally:
        await _publish_sim_running(redis, False)

    log.info("job.done", run_id=str(run_id))
    return {"run_id": run_id_str, "status": "completed"}


class WorkerSettings:
    """Arq entrypoint. `arq worker_sim.jobs.WorkerSettings`."""

    functions = [run_sim_job]
    max_jobs = 1  # single sim at a time (single A100 — spec §7.5).

    @staticmethod
    async def on_startup(ctx: dict[str, Any]) -> None:
        s = get_settings()
        log.info("worker.startup", worker_id=s.worker_id)

    @staticmethod
    async def on_shutdown(ctx: dict[str, Any]) -> None:
        log.info("worker.shutdown")

    @staticmethod
    def redis_settings() -> "object":
        # Imported lazily to avoid circular config.
        from arq.connections import RedisSettings  # noqa: PLC0415
        s = get_settings()
        # arq parses redis url itself.
        return RedisSettings.from_dsn(s.redis_url)
