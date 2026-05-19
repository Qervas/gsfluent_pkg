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
    """Real engine call via subprocess (Task 3.6).

    Looks up Model + Run, downloads model from MinIO, spawns
    tools/run_sim.sh, streams frame artifacts to MinIO + DB + events,
    streams log lines as events, honors cancel flag.
    """
    from gsfluent_api.event_store import publish as publish_event  # noqa: PLC0415
    from gsfluent_api.events import (  # noqa: PLC0415
        ArtifactCreatedEvent,
        LogLineEvent,
        RunCancelledEvent,
        RunCompletedEvent,
        RunFailedEvent,
        RunStartedEvent,
    )
    from gsfluent_api.models.artifact import Artifact  # noqa: PLC0415
    from gsfluent_api.models.enums import ArtifactKind, RunStatus  # noqa: PLC0415
    from gsfluent_api.models.model import Model  # noqa: PLC0415
    from gsfluent_api.models.run import Run  # noqa: PLC0415
    from gsfluent_api.storage import BUCKET_RUNS, put_object_bytes  # noqa: PLC0415

    from .engine import run_engine  # noqa: PLC0415

    s = get_settings()
    run_id = uuid.UUID(run_id_str)
    redis: aioredis.Redis = ctx["redis"]

    log.info("job.start", run_id=str(run_id))

    # Resolve run + model rows once.
    engine_db = _engine_singleton()
    async with async_sessionmaker(engine_db, expire_on_commit=False)() as session:
        run_row = await session.get(Run, run_id)
        if run_row is None:
            return {"run_id": run_id_str, "status": "not_found"}
        model_row = await session.get(Model, run_row.model_id)
        if model_row is None:
            await _set_run_status(
                run_id, status=RunStatus.failed,
                error="model row missing",
                completed_at=dt.datetime.now(dt.UTC),
            )
            await publish_event(redis, RunFailedEvent(
                run_id=run_id, error="model row missing"))
            return {"run_id": run_id_str, "status": "failed"}
        recipe_snapshot = dict(run_row.recipe_snapshot or {})
        model_minio_path = model_row.minio_path

    await _set_run_status(
        run_id,
        worker_id=s.worker_id,
        started_at=dt.datetime.now(dt.UTC),
        status=RunStatus.running,
    )
    await publish_event(redis, RunStartedEvent(run_id=run_id, worker_id=s.worker_id))
    await _publish_sim_running(redis, True)

    # Buffer log chunks; flush every ~4 KiB or 1 s into a rolling artifact.
    log_buffer = bytearray()
    log_chunks: list[bytes] = []

    async def on_log(level: str, line: str) -> None:
        log_buffer.extend((f"[{level.upper()}] {line}\n").encode())
        await publish_event(redis, LogLineEvent(
            run_id=run_id, level=level, message=line))
        if len(log_buffer) >= 4096:
            log_chunks.append(bytes(log_buffer))
            log_buffer.clear()

    start_frame_count = 0

    async def on_frame(idx: int, kind_str: str, data: bytes) -> None:
        kind = ArtifactKind.cell if kind_str == "cell" else ArtifactKind.preview
        suffix = ".npz" if kind == ArtifactKind.cell else ".ply"
        key = f"runs/{run_id}/frame_{idx:04d}{suffix}"
        await put_object_bytes(BUCKET_RUNS, key, data)
        async with async_sessionmaker(engine_db, expire_on_commit=False)() as s2:
            art = Artifact(
                run_id=run_id,
                kind=kind,
                frame_idx=idx,
                minio_path=f"{BUCKET_RUNS}/{key}",
                size_bytes=len(data),
            )
            s2.add(art)
            await s2.commit()
            await s2.refresh(art)
            await publish_event(redis, ArtifactCreatedEvent(
                run_id=run_id, artifact_id=art.id,
                kind=kind, frame_idx=idx, size_bytes=len(data),
            ))

    async def should_cancel() -> bool:
        return await _is_cancelled(run_id)

    particles = int(recipe_snapshot.get("particles", 200_000))

    success: bool
    err: str | None = None
    try:
        success, err = await run_engine(
            run_id,
            model_minio_path,
            recipe_snapshot,
            particles,
            on_frame=on_frame,
            on_log=on_log,
            should_cancel=should_cancel,
        )
    except Exception as e:  # noqa: BLE001
        success = False
        err = f"engine raised: {e!r}"[:500]
    finally:
        await _publish_sim_running(redis, False)
        # Final log flush into one log artifact.
        if log_buffer:
            log_chunks.append(bytes(log_buffer))
            log_buffer.clear()
        if log_chunks:
            full = b"".join(log_chunks)
            key = f"runs/{run_id}/log.txt"
            await put_object_bytes(BUCKET_RUNS, key, full, "text/plain")
            async with async_sessionmaker(engine_db, expire_on_commit=False)() as s3:
                s3.add(Artifact(
                    run_id=run_id, kind=ArtifactKind.log,
                    minio_path=f"{BUCKET_RUNS}/{key}",
                    size_bytes=len(full),
                ))
                await s3.commit()

    if not success and err and "cancelled" in err.lower():
        await _set_run_status(
            run_id, status=RunStatus.cancelled,
            completed_at=dt.datetime.now(dt.UTC),
        )
        await publish_event(redis, RunCancelledEvent(run_id=run_id))
        log.info("job.cancelled", run_id=str(run_id))
        return {"run_id": run_id_str, "status": "cancelled"}

    if not success:
        await _set_run_status(
            run_id, status=RunStatus.failed,
            error=err or "unknown error",
            completed_at=dt.datetime.now(dt.UTC),
        )
        await publish_event(redis, RunFailedEvent(
            run_id=run_id, error=err or "unknown error"))
        log.error("job.failed", run_id=str(run_id), error=err)
        return {"run_id": run_id_str, "status": "failed"}

    started = run_row.started_at
    elapsed = (dt.datetime.now(dt.UTC) - (started or dt.datetime.now(dt.UTC))).total_seconds()
    await _set_run_status(
        run_id, status=RunStatus.completed,
        completed_at=dt.datetime.now(dt.UTC),
        gpu_seconds=elapsed,
    )
    await publish_event(redis, RunCompletedEvent(
        run_id=run_id, gpu_seconds=elapsed))
    log.info("job.done", run_id=str(run_id), gpu_seconds=elapsed)
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
