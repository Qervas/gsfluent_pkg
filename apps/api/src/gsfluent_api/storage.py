"""MinIO client wrapper.

The minio python SDK is sync; we wrap each call with asyncio.to_thread.
Async S3 SDKs exist (aioboto3) but minio-py covers MinIO-specific features
(versioning, lifecycle) better and avoids the boto3 dependency weight.

Path helpers enforce the layout from spec §5:
  models/{model_id}/source.ply
  runs/{run_id}/frame_{N}.npz
  runs/{run_id}/log.txt
"""

from __future__ import annotations

import asyncio
import datetime as dt
import io
import uuid
from collections.abc import AsyncIterator
from functools import lru_cache
from typing import BinaryIO

from minio import Minio
from minio.error import S3Error

from .config import get_settings

# Bucket names (mirror infra/minio/init-buckets.sh).
BUCKET_MODELS = "gsfluent-models"
BUCKET_RUNS = "gsfluent-runs"
BUCKET_MISC = "gsfluent-misc"


@lru_cache(maxsize=1)
def get_minio_client() -> Minio:
    s = get_settings()
    return Minio(
        s.minio_endpoint,
        access_key=s.minio_access_key,
        secret_key=s.minio_secret_key,
        secure=s.minio_secure,
    )


# ---------- path helpers ----------

def model_object_key(model_id: uuid.UUID, filename: str = "source.ply") -> str:
    return f"models/{model_id}/{filename}"


def run_cell_key(run_id: uuid.UUID, frame_idx: int) -> str:
    return f"runs/{run_id}/frame_{frame_idx:04d}.npz"


def run_log_key(run_id: uuid.UUID) -> str:
    return f"runs/{run_id}/log.txt"


# ---------- async wrappers ----------

async def put_object_stream(
    bucket: str,
    key: str,
    data: BinaryIO,
    length: int,
    content_type: str = "application/octet-stream",
) -> None:
    """Upload a stream of known length."""
    def _put() -> None:
        get_minio_client().put_object(bucket, key, data, length, content_type=content_type)
    await asyncio.to_thread(_put)


async def put_object_bytes(
    bucket: str,
    key: str,
    payload: bytes,
    content_type: str = "application/octet-stream",
) -> None:
    """Upload an in-memory payload. Convenience for small objects."""
    def _put() -> None:
        get_minio_client().put_object(
            bucket, key, io.BytesIO(payload), len(payload), content_type=content_type
        )
    await asyncio.to_thread(_put)


async def head_object(bucket: str, key: str) -> dict[str, object] | None:
    def _head() -> dict[str, object] | None:
        try:
            stat = get_minio_client().stat_object(bucket, key)
        except S3Error as e:
            if e.code in ("NoSuchKey", "NoSuchObject", "NoSuchBucket"):
                return None
            raise
        return {
            "size": stat.size,
            "etag": stat.etag,
            "content_type": stat.content_type,
            "last_modified": stat.last_modified,
        }
    return await asyncio.to_thread(_head)


async def delete_object(bucket: str, key: str) -> None:
    def _delete() -> None:
        get_minio_client().remove_object(bucket, key)
    await asyncio.to_thread(_delete)


async def presigned_get_url(
    bucket: str,
    key: str,
    expires: dt.timedelta = dt.timedelta(minutes=5),
) -> str:
    def _presign() -> str:
        return get_minio_client().presigned_get_object(bucket, key, expires=expires)
    return await asyncio.to_thread(_presign)


async def stream_object(
    bucket: str,
    key: str,
    chunk_size: int = 64 * 1024,
) -> AsyncIterator[bytes]:
    """Async iterator over an object's bytes. Used for streaming back to client."""
    def _open() -> object:
        return get_minio_client().get_object(bucket, key)

    response = await asyncio.to_thread(_open)
    try:
        while True:
            chunk = await asyncio.to_thread(response.read, chunk_size)  # type: ignore[union-attr]
            if not chunk:
                break
            yield chunk
    finally:
        await asyncio.to_thread(response.close)  # type: ignore[union-attr]
        await asyncio.to_thread(response.release_conn)  # type: ignore[union-attr]


async def ping_minio() -> dict[str, object]:
    """Health-check used by /v1/system/health."""
    def _ping() -> dict[str, object]:
        try:
            buckets = [b.name for b in get_minio_client().list_buckets()]
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)[:200]}
        return {"ok": True, "buckets": buckets}
    return await asyncio.to_thread(_ping)


async def ensure_buckets() -> None:
    """Idempotent bucket creation. Called from app lifespan."""
    def _ensure() -> None:
        client = get_minio_client()
        for bucket in (BUCKET_MODELS, BUCKET_RUNS, BUCKET_MISC):
            if not client.bucket_exists(bucket):
                client.make_bucket(bucket)
    await asyncio.to_thread(_ensure)
