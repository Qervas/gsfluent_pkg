"""Tests for /v1/recipes — no MinIO needed; pure DB."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_and_get(client: AsyncClient) -> None:
    r = await client.post(
        "/v1/recipes",
        json={"name": "basic-flood", "content": {"material": "fluid", "yield": 1e4}},
    )
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["version"] == 1
    assert created["starred"] is False

    r2 = await client.get(f"/v1/recipes/{created['id']}")
    assert r2.status_code == 200
    assert r2.json()["name"] == "basic-flood"


@pytest.mark.asyncio
async def test_patch_bumps_version_and_snapshots(client: AsyncClient) -> None:
    r = await client.post(
        "/v1/recipes",
        json={"name": "v1", "content": {"a": 1}},
    )
    rid = r.json()["id"]

    # Three content changes → versions go to 4 (create=1, +3 patches).
    for i in range(2, 5):
        r2 = await client.patch(f"/v1/recipes/{rid}", json={"content": {"a": i}})
        assert r2.status_code == 200, r2.text
        assert r2.json()["version"] == i

    r3 = await client.get(f"/v1/recipes/{rid}/versions")
    assert r3.status_code == 200
    versions = r3.json()
    # Newest first.
    assert [v["version"] for v in versions] == [4, 3, 2, 1]


@pytest.mark.asyncio
async def test_patch_name_only_does_not_bump_version(client: AsyncClient) -> None:
    r = await client.post("/v1/recipes", json={"name": "x", "content": {"a": 1}})
    rid = r.json()["id"]
    r2 = await client.patch(f"/v1/recipes/{rid}", json={"name": "y"})
    assert r2.status_code == 200
    assert r2.json()["version"] == 1
    assert r2.json()["name"] == "y"


@pytest.mark.asyncio
async def test_soft_delete_hides_from_list(client: AsyncClient) -> None:
    r = await client.post("/v1/recipes", json={"name": "doomed", "content": {}})
    rid = r.json()["id"]

    r2 = await client.delete(f"/v1/recipes/{rid}")
    assert r2.status_code == 204

    r3 = await client.get(f"/v1/recipes/{rid}")
    assert r3.status_code == 404

    r4 = await client.get("/v1/recipes")
    assert all(item["id"] != rid for item in r4.json()["items"])


@pytest.mark.asyncio
async def test_strict_schema_rejects_extra(client: AsyncClient) -> None:
    r = await client.post(
        "/v1/recipes",
        json={"name": "x", "content": {}, "bogus_field": 42},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_list_pagination_returns_cursor(client: AsyncClient) -> None:
    # Insert 3, page with limit=2.
    for i in range(3):
        await client.post("/v1/recipes", json={"name": f"p{i}", "content": {"i": i}})
    r = await client.get("/v1/recipes?limit=2")
    body = r.json()
    assert len(body["items"]) == 2
    assert body["next_cursor"] is not None
