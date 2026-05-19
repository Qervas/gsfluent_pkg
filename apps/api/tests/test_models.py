"""Tests for /v1/models — multipart upload, list, detail, soft-delete.

Uses real MinIO via the minio_url session fixture. No mocks.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

# Minimal PLY: header + 3 vertices. Lets us assert num_gaussians parse path.
MINIMAL_PLY = (
    b"ply\n"
    b"format ascii 1.0\n"
    b"element vertex 3\n"
    b"property float x\n"
    b"property float y\n"
    b"property float z\n"
    b"end_header\n"
    b"0.0 0.0 0.0\n"
    b"1.0 0.0 0.0\n"
    b"0.0 1.0 0.0\n"
)


@pytest.mark.asyncio
async def test_upload_creates_row_and_minio_object(client: AsyncClient) -> None:
    r = await client.post(
        "/v1/models",
        files={"file": ("triangle.ply", MINIMAL_PLY, "application/octet-stream")},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "triangle.ply"
    assert body["size_bytes"] == len(MINIMAL_PLY)
    assert body["num_gaussians"] == 3
    assert body["minio_path"].startswith("gsfluent-models/models/")
    assert body["minio_path"].endswith("/source.ply")

    # Confirm the object actually lives in MinIO.
    from gsfluent_api.storage import head_object
    bucket, _, key = body["minio_path"].partition("/")
    stat = await head_object(bucket, key)
    assert stat is not None
    assert stat["size"] == len(MINIMAL_PLY)


@pytest.mark.asyncio
async def test_upload_rejects_unparseable_as_gaussians(client: AsyncClient) -> None:
    # Not a PLY: num_gaussians should be NULL (best-effort parse).
    r = await client.post(
        "/v1/models",
        files={"file": ("blob.bin", b"\x00\x01\x02\x03", "application/octet-stream")},
    )
    assert r.status_code == 201, r.text
    assert r.json()["num_gaussians"] is None


@pytest.mark.asyncio
async def test_list_excludes_soft_deleted(client: AsyncClient) -> None:
    up = await client.post(
        "/v1/models",
        files={"file": ("doomed.ply", MINIMAL_PLY, "application/octet-stream")},
    )
    mid = up.json()["id"]

    delr = await client.delete(f"/v1/models/{mid}")
    assert delr.status_code == 204

    detail = await client.get(f"/v1/models/{mid}")
    assert detail.status_code == 404

    lst = await client.get("/v1/models")
    assert all(item["id"] != mid for item in lst.json()["items"])


@pytest.mark.asyncio
async def test_list_pagination_returns_cursor(client: AsyncClient) -> None:
    for i in range(3):
        await client.post(
            "/v1/models",
            files={"file": (f"page{i}.ply", MINIMAL_PLY, "application/octet-stream")},
        )
    r = await client.get("/v1/models?limit=2")
    body = r.json()
    assert len(body["items"]) == 2
    assert body["next_cursor"] is not None


@pytest.mark.asyncio
async def test_get_404_on_missing(client: AsyncClient) -> None:
    r = await client.get("/v1/models/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_idempotent(client: AsyncClient) -> None:
    up = await client.post(
        "/v1/models",
        files={"file": ("tmp.ply", MINIMAL_PLY, "application/octet-stream")},
    )
    mid = up.json()["id"]
    r1 = await client.delete(f"/v1/models/{mid}")
    assert r1.status_code == 204
    r2 = await client.delete(f"/v1/models/{mid}")
    # Second delete on a soft-deleted row should 404 (it's invisible now).
    assert r2.status_code == 404
