"""Arq pool connection — used by the api to enqueue sim jobs."""

from __future__ import annotations

from functools import lru_cache

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from .config import get_settings


@lru_cache(maxsize=1)
def _redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(get_settings().redis_url)


_pool: ArqRedis | None = None


async def get_queue() -> ArqRedis:
    global _pool
    if _pool is None:
        _pool = await create_pool(_redis_settings())
    return _pool


async def close_queue() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
