"""MinIO client wrapper. Real CRUD lands in Phase 2.

Phase 1: just the connection-test method used by /v1/system/health.
"""

from __future__ import annotations

import asyncio
from functools import lru_cache

from minio import Minio

from .config import get_settings


@lru_cache(maxsize=1)
def get_minio_client() -> Minio:
    s = get_settings()
    return Minio(
        s.minio_endpoint,
        access_key=s.minio_access_key,
        secret_key=s.minio_secret_key,
        secure=s.minio_secure,
    )


async def ping_minio() -> dict[str, object]:
    """Return {ok, buckets|error}. Runs the sync client off the event loop."""
    def _ping() -> dict[str, object]:
        try:
            buckets = [b.name for b in get_minio_client().list_buckets()]
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)[:200]}
        return {"ok": True, "buckets": buckets}

    return await asyncio.to_thread(_ping)
