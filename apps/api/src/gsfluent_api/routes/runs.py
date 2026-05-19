"""/v1/runs — submit, list, get, cancel.

Spec §6.3 + §9. Engine integration lands in the worker (Phase 3.6); this
file only orchestrates DB rows + queue enqueue + cancel signaling.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from typing import Annotated, Any

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import session_scope
from ..event_store import publish as publish_event
from ..events import RunCancelledEvent, RunQueuedEvent
from ..models.artifact import Artifact
from ..models.enums import RunStatus
from ..models.model import Model
from ..models.recipe import Recipe
from ..models.run import Run
from ..queue import get_queue
from ..schemas import ArtifactRead, Page, RunCreate, RunRead

router = APIRouter(prefix="/v1/runs", tags=["runs"])

DEFAULT_LIMIT = 20
MAX_LIMIT = 100

CANCEL_KEY_PREFIX = "cancel:"
IDEMPOTENCY_PREFIX = "idem:"
IDEMPOTENCY_TTL_SECONDS = 24 * 60 * 60
AUTO_DEBOUNCE_TTL_SECONDS = 5 * 60


def _recipe_hash(snapshot: dict[str, Any]) -> str:
    raw = json.dumps(snapshot, sort_keys=True).encode()
    return hashlib.sha256(raw).hexdigest()


@router.post("", response_model=RunRead, status_code=201)
async def submit_run(
    body: RunCreate,
    session: Annotated[AsyncSession, Depends(session_scope)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> RunRead:
    if body.recipe_id is None and body.recipe_inline is None:
        raise HTTPException(400, "recipe_id or recipe_inline required")

    # Resolve the snapshot.
    if body.recipe_id is not None:
        recipe = await session.get(Recipe, body.recipe_id)
        if recipe is None or recipe.deleted_at is not None:
            raise HTTPException(404, "recipe not found")
        snapshot = dict(recipe.content)
    else:
        snapshot = dict(body.recipe_inline or {})

    # Validate model.
    model = await session.get(Model, body.model_id)
    if model is None or model.deleted_at is not None:
        raise HTTPException(404, "model not found")

    redis: aioredis.Redis = aioredis.from_url(get_settings().redis_url)
    try:
        # Explicit Idempotency-Key: short-circuit if seen recently.
        if idempotency_key:
            seen = await redis.get(IDEMPOTENCY_PREFIX + idempotency_key)
            if seen:
                existing = await session.get(Run, uuid.UUID(seen.decode()))
                if existing:
                    return RunRead.model_validate(existing)

        # Auto-debounce by (model_id, recipe_snapshot hash) over 5 min.
        debounce_key = f"debounce:{body.model_id}:{_recipe_hash(snapshot)}"
        existing_id = await redis.get(debounce_key)
        if existing_id:
            existing = await session.get(Run, uuid.UUID(existing_id.decode()))
            if existing and existing.status in (RunStatus.queued, RunStatus.running):
                return RunRead.model_validate(existing)

        row = Run(
            name=body.name,
            model_id=body.model_id,
            recipe_id=body.recipe_id,
            recipe_snapshot=snapshot,
            idempotency_key=idempotency_key,
        )
        session.add(row)
        await session.flush()
        await session.refresh(row)

        if idempotency_key:
            await redis.setex(
                IDEMPOTENCY_PREFIX + idempotency_key,
                IDEMPOTENCY_TTL_SECONDS,
                str(row.id),
            )
        await redis.setex(debounce_key, AUTO_DEBOUNCE_TTL_SECONDS, str(row.id))
    finally:
        await redis.aclose()

    queue = await get_queue()
    await queue.enqueue_job("run_sim_job", str(row.id))

    # Fan out run.queued event for any WS subscribers on this run.
    redis: aioredis.Redis = aioredis.from_url(get_settings().redis_url)
    try:
        await publish_event(redis, RunQueuedEvent(run_id=row.id))
    finally:
        await redis.aclose()

    return RunRead.model_validate(row)


@router.get("", response_model=Page[RunRead])
async def list_runs(
    session: Annotated[AsyncSession, Depends(session_scope)],
    status: RunStatus | None = None,
    model_id: uuid.UUID | None = None,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> Page[RunRead]:
    limit = min(limit, MAX_LIMIT)
    stmt = select(Run).order_by(Run.created_at.desc(), Run.id.desc()).limit(limit + 1)
    if status is not None:
        stmt = stmt.where(Run.status == status)
    if model_id is not None:
        stmt = stmt.where(Run.model_id == model_id)
    if cursor:
        try:
            cursor_id = uuid.UUID(cursor)
        except ValueError as e:
            raise HTTPException(400, f"bad cursor: {e}") from e
        anchor = await session.get(Run, cursor_id)
        if anchor is None:
            raise HTTPException(400, "cursor not found")
        stmt = stmt.where(Run.created_at < anchor.created_at)

    rows = (await session.scalars(stmt)).all()
    next_cursor = str(rows[-1].id) if len(rows) > limit else None
    items = [RunRead.model_validate(r) for r in rows[:limit]]
    return Page[RunRead](items=items, next_cursor=next_cursor)


@router.get("/{run_id}", response_model=RunRead)
async def get_run(
    run_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(session_scope)],
) -> RunRead:
    row = await session.get(Run, run_id)
    if row is None:
        raise HTTPException(404, "run not found")
    return RunRead.model_validate(row)


@router.get("/{run_id}/artifacts", response_model=list[ArtifactRead])
async def list_artifacts(
    run_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(session_scope)],
) -> list[ArtifactRead]:
    run = await session.get(Run, run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    stmt = (
        select(Artifact)
        .where(Artifact.run_id == run_id)
        .order_by(Artifact.kind, Artifact.frame_idx.asc().nullsfirst())
    )
    arts = (await session.scalars(stmt)).all()
    return [ArtifactRead.model_validate(a) for a in arts]


@router.post("/{run_id}/cancel", status_code=204)
async def cancel_run(
    run_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(session_scope)],
) -> None:
    row = await session.get(Run, run_id)
    if row is None:
        raise HTTPException(404, "run not found")
    if row.status not in (RunStatus.queued, RunStatus.running):
        raise HTTPException(409, f"cannot cancel a {row.status.value} run")

    row.status = RunStatus.cancelled
    row.completed_at = dt.datetime.now(dt.UTC)
    await session.flush()

    redis: aioredis.Redis = aioredis.from_url(get_settings().redis_url)
    try:
        # 1h TTL on the cancel flag — the worker either sees it or finishes.
        await redis.setex(CANCEL_KEY_PREFIX + str(run_id), 3600, "1")
        # Fan out cancel event.
        await publish_event(redis, RunCancelledEvent(run_id=run_id))
    finally:
        await redis.aclose()
