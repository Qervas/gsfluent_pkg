"""/v1/recipes — CRUD with append-only version history."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import session_scope
from ..models.recipe import Recipe, RecipeVersion
from ..schemas import (
    Page,
    RecipeCreate,
    RecipePatch,
    RecipeRead,
    RecipeVersionRead,
)

router = APIRouter(prefix="/v1/recipes", tags=["recipes"])

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


@router.post("", response_model=RecipeRead, status_code=201)
async def create_recipe(
    body: RecipeCreate,
    session: Annotated[AsyncSession, Depends(session_scope)],
) -> RecipeRead:
    row = Recipe(name=body.name, content=body.content, version=1, starred=False)
    session.add(row)
    await session.flush()

    # Snapshot v1 into recipe_versions.
    session.add(RecipeVersion(recipe_id=row.id, version=1, content=body.content))
    await session.flush()
    await session.refresh(row)
    return RecipeRead.model_validate(row)


@router.get("", response_model=Page[RecipeRead])
async def list_recipes(
    session: Annotated[AsyncSession, Depends(session_scope)],
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> Page[RecipeRead]:
    limit = min(limit, MAX_LIMIT)
    stmt = (
        select(Recipe)
        .where(Recipe.deleted_at.is_(None))
        .order_by(Recipe.starred.desc(), Recipe.updated_at.desc(), Recipe.id.desc())
        .limit(limit + 1)
    )
    if cursor:
        try:
            cursor_id = uuid.UUID(cursor)
        except ValueError as e:
            raise HTTPException(400, f"bad cursor: {e}") from e
        anchor = await session.get(Recipe, cursor_id)
        if anchor is None:
            raise HTTPException(400, "cursor not found")
        stmt = stmt.where(Recipe.updated_at < anchor.updated_at)

    rows = (await session.scalars(stmt)).all()
    next_cursor = str(rows[-1].id) if len(rows) > limit else None
    items = [RecipeRead.model_validate(r) for r in rows[:limit]]
    return Page[RecipeRead](items=items, next_cursor=next_cursor)


@router.get("/{recipe_id}", response_model=RecipeRead)
async def get_recipe(
    recipe_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(session_scope)],
) -> RecipeRead:
    row = await session.get(Recipe, recipe_id)
    if row is None or row.deleted_at is not None:
        raise HTTPException(404, "recipe not found")
    return RecipeRead.model_validate(row)


@router.get("/{recipe_id}/versions", response_model=list[RecipeVersionRead])
async def list_recipe_versions(
    recipe_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(session_scope)],
) -> list[RecipeVersionRead]:
    row = await session.get(Recipe, recipe_id)
    if row is None or row.deleted_at is not None:
        raise HTTPException(404, "recipe not found")
    stmt = (
        select(RecipeVersion)
        .where(RecipeVersion.recipe_id == recipe_id)
        .order_by(RecipeVersion.version.desc())
    )
    versions = (await session.scalars(stmt)).all()
    return [RecipeVersionRead.model_validate(v) for v in versions]


@router.patch("/{recipe_id}", response_model=RecipeRead)
async def patch_recipe(
    recipe_id: uuid.UUID,
    body: RecipePatch,
    session: Annotated[AsyncSession, Depends(session_scope)],
) -> RecipeRead:
    row = await session.get(Recipe, recipe_id)
    if row is None or row.deleted_at is not None:
        raise HTTPException(404, "recipe not found")

    content_changed = body.content is not None and body.content != row.content
    if body.name is not None:
        row.name = body.name
    if body.starred is not None:
        row.starred = body.starred
    if content_changed:
        # Snapshot the *new* version. v1 is the create-time content; subsequent
        # PATCHes bump and snapshot.
        row.content = body.content  # type: ignore[assignment]
        row.version += 1
        session.add(RecipeVersion(recipe_id=row.id, version=row.version, content=row.content))

    await session.flush()
    await session.refresh(row)
    return RecipeRead.model_validate(row)


@router.delete("/{recipe_id}", status_code=204)
async def delete_recipe(
    recipe_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(session_scope)],
) -> None:
    row = await session.get(Recipe, recipe_id)
    if row is None or row.deleted_at is not None:
        raise HTTPException(404, "recipe not found")
    row.deleted_at = dt.datetime.now(dt.UTC)
    await session.flush()
