"""Tests for /v1/system/health and /v1/system/config."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_returns_subchecks(client: AsyncClient) -> None:
    r = await client.get("/v1/system/health")
    assert r.status_code == 200
    body = r.json()
    assert "status" in body
    assert body["postgres"]["ok"] is True
    assert body["redis"]["ok"] is True
    # MinIO + GPU not available in the test env; expect ok=False with an error.
    assert "ok" in body["minio"]
    assert "ok" in body["gpu"]


@pytest.mark.asyncio
async def test_health_includes_version(client: AsyncClient) -> None:
    r = await client.get("/v1/system/health")
    assert r.json()["version"]


@pytest.mark.asyncio
async def test_config_defaults(client: AsyncClient) -> None:
    r = await client.get("/v1/system/config")
    assert r.status_code == 200
    body = r.json()
    assert body["max_concurrent_sims"] >= 0
    assert body["max_concurrent_renders"] >= 0


@pytest.mark.asyncio
async def test_config_update_persists(client: AsyncClient) -> None:
    r = await client.post(
        "/v1/system/config",
        json={"max_concurrent_sims": 2, "max_concurrent_renders": 7},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["max_concurrent_sims"] == 2
    assert body["max_concurrent_renders"] == 7

    r2 = await client.get("/v1/system/config")
    assert r2.json()["max_concurrent_sims"] == 2
    assert r2.json()["max_concurrent_renders"] == 7


@pytest.mark.asyncio
async def test_config_rejects_empty_update(client: AsyncClient) -> None:
    r = await client.post("/v1/system/config", json={})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_config_rejects_negative(client: AsyncClient) -> None:
    r = await client.post("/v1/system/config", json={"max_concurrent_sims": -1})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_trace_id_round_trip(client: AsyncClient) -> None:
    """TraceIdMiddleware echoes the inbound X-Trace-Id."""
    r = await client.get("/v1/system/health", headers={"X-Trace-Id": "test-trace-123"})
    assert r.headers["x-trace-id"] == "test-trace-123"

    r2 = await client.get("/v1/system/health")
    assert r2.headers["x-trace-id"]  # auto-generated
    assert len(r2.headers["x-trace-id"]) >= 16
