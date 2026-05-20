"""Sanity checks for ORM models against a real Postgres."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gsfluent_api.models import (
    Artifact,
    ArtifactKind,
    Model,
    Recipe,
    RecipeVersion,
    RenderSession,
    RenderSessionStatus,
    Run,
    RunStatus,
)


@pytest.mark.asyncio
async def test_insert_model(db_session: AsyncSession) -> None:
    m = Model(
        name="test.ply",
        minio_path="models/test/source.ply",
        size_bytes=12345,
        num_gaussians=683_000,
    )
    db_session.add(m)
    await db_session.flush()
    assert m.id is not None
    assert m.created_at is not None


@pytest.mark.asyncio
async def test_recipe_with_version(db_session: AsyncSession) -> None:
    r = Recipe(name="basic-flood", content={"material": "fluid"})
    db_session.add(r)
    await db_session.flush()

    v1 = RecipeVersion(recipe_id=r.id, version=1, content=r.content)
    db_session.add(v1)
    await db_session.flush()

    fetched = await db_session.scalar(select(Recipe).where(Recipe.id == r.id))
    assert fetched is not None
    assert fetched.version == 1
    assert fetched.starred is False


@pytest.mark.asyncio
async def test_run_with_artifact(db_session: AsyncSession) -> None:
    m = Model(name="m.ply", minio_path="models/m/source.ply", size_bytes=100)
    db_session.add(m)
    await db_session.flush()

    run = Run(
        name="run-1",
        model_id=m.id,
        recipe_snapshot={"material": "fluid"},
    )
    db_session.add(run)
    await db_session.flush()
    assert run.status == RunStatus.queued

    art = Artifact(
        run_id=run.id,
        kind=ArtifactKind.cell,
        frame_idx=0,
        minio_path=f"runs/{run.id}/frame_0.npz",
        size_bytes=2_900_000_000,
    )
    db_session.add(art)
    await db_session.flush()
    assert art.id is not None


@pytest.mark.asyncio
async def test_render_session_xor_check(db_session: AsyncSession) -> None:
    """run_id and model_id must be xor (exactly one non-null)."""
    m = Model(name="m.ply", minio_path="models/m/source.ply", size_bytes=100)
    db_session.add(m)
    await db_session.flush()

    rs = RenderSession(
        model_id=m.id,
        worker_id="w1",
        status=RenderSessionStatus.signaling,
    )
    db_session.add(rs)
    await db_session.flush()
    assert rs.id is not None

    # Both null → violates ck_render_session_target.
    bad = RenderSession(worker_id="w2", status=RenderSessionStatus.signaling)
    db_session.add(bad)
    with pytest.raises(Exception):  # IntegrityError under-the-hood
        await db_session.flush()
