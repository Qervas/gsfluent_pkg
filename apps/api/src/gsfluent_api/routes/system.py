"""System endpoints: /v1/system/health, /v1/system/config."""

from __future__ import annotations

from typing import Annotated

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .. import __version__
from ..config import get_settings
from ..db import session_scope
from ..gpu import gpu_info
from ..storage import ping_minio

router = APIRouter(prefix="/v1/system", tags=["system"])


# ---------- /v1/system/health ----------

class HealthResponse(BaseModel):
    status: str
    version: str
    postgres: dict[str, object]
    redis: dict[str, object]
    minio: dict[str, object]
    gpu: dict[str, object]


async def _ping_postgres(session: AsyncSession) -> dict[str, object]:
    try:
        result = await session.execute(text("SELECT 1"))
        result.scalar()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:200]}
    return {"ok": True}


async def _ping_redis() -> dict[str, object]:
    client = aioredis.from_url(get_settings().redis_url)
    try:
        await client.ping()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:200]}
    finally:
        await client.aclose()
    return {"ok": True}


@router.get("/health", response_model=HealthResponse)
async def health(
    session: Annotated[AsyncSession, Depends(session_scope)],
) -> HealthResponse:
    """Roll up sub-checks. status='ok' iff every sub-check is ok."""
    pg = await _ping_postgres(session)
    rd = await _ping_redis()
    mn = await ping_minio()
    gp = await gpu_info()

    all_ok = all(d.get("ok") for d in (pg, rd, mn))
    return HealthResponse(
        status="ok" if all_ok else "degraded",
        version=__version__,
        postgres=pg,
        redis=rd,
        minio=mn,
        gpu=gp,
    )


# ---------- /v1/system/config ----------

CONFIG_KEY_PREFIX = "config:"


class SystemConfig(BaseModel):
    max_concurrent_sims: int = Field(ge=0)
    max_concurrent_renders: int = Field(ge=0)
    version: str
    git_sha: str


class ConfigUpdate(BaseModel):
    max_concurrent_sims: int | None = Field(default=None, ge=0)
    max_concurrent_renders: int | None = Field(default=None, ge=0)


async def _get_int(client: aioredis.Redis, key: str, default: int) -> int:
    raw = await client.get(CONFIG_KEY_PREFIX + key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@router.get("/config", response_model=SystemConfig)
async def get_config() -> SystemConfig:
    s = get_settings()
    client = aioredis.from_url(s.redis_url)
    try:
        sims = await _get_int(client, "max_concurrent_sims", s.max_concurrent_sims)
        renders = await _get_int(client, "max_concurrent_renders", s.max_concurrent_renders)
    finally:
        await client.aclose()
    return SystemConfig(
        max_concurrent_sims=sims,
        max_concurrent_renders=renders,
        version=s.version,
        git_sha=s.git_sha,
    )


@router.post("/config", response_model=SystemConfig)
async def set_config(update: ConfigUpdate) -> SystemConfig:
    if update.max_concurrent_sims is None and update.max_concurrent_renders is None:
        raise HTTPException(status_code=400, detail="no config keys provided")

    s = get_settings()
    client = aioredis.from_url(s.redis_url)
    try:
        if update.max_concurrent_sims is not None:
            await client.set(CONFIG_KEY_PREFIX + "max_concurrent_sims",
                             str(update.max_concurrent_sims))
        if update.max_concurrent_renders is not None:
            await client.set(CONFIG_KEY_PREFIX + "max_concurrent_renders",
                             str(update.max_concurrent_renders))
    finally:
        await client.aclose()
    return await get_config()
