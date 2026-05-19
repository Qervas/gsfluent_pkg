"""/v1/models — upload, list, get, soft-delete."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import session_scope
from ..models.model import Model
from ..schemas import ModelRead, Page
from ..storage import BUCKET_MODELS, model_object_key, put_object_stream

router = APIRouter(prefix="/v1/models", tags=["models"])

DEFAULT_LIMIT = 20
MAX_LIMIT = 100
PLY_MAGIC = b"ply\n"


def _parse_num_gaussians(header: bytes) -> int | None:
    """Best-effort: read the PLY header to extract element vertex count."""
    if not header.startswith(PLY_MAGIC):
        return None
    text = header.decode("ascii", errors="ignore")
    for line in text.splitlines():
        if line.startswith("element vertex "):
            try:
                return int(line.split()[-1])
            except ValueError:
                return None
        if line.strip() == "end_header":
            break
    return None


@router.post("", response_model=ModelRead, status_code=201)
async def upload_model(
    session: Annotated[AsyncSession, Depends(session_scope)],
    file: Annotated[UploadFile, File()],
    name: Annotated[str | None, Form()] = None,
) -> ModelRead:
    """Streaming multipart upload to MinIO, then create the DB row."""
    if not file.filename:
        raise HTTPException(400, "file has no filename")

    model_id = uuid.uuid4()
    key = model_object_key(model_id, filename="source.ply")

    # Read a small header chunk to parse PLY metadata before streaming.
    head = await file.read(8192)
    num_gaussians = _parse_num_gaussians(head)
    await file.seek(0)

    if file.size is None:
        # Without content-length we can't stream to MinIO efficiently.
        raise HTTPException(411, "Content-Length required")

    await put_object_stream(
        BUCKET_MODELS,
        key,
        file.file,
        length=file.size,
        content_type=file.content_type or "application/octet-stream",
    )

    row = Model(
        id=model_id,
        name=name or file.filename,
        minio_path=f"{BUCKET_MODELS}/{key}",
        size_bytes=file.size,
        num_gaussians=num_gaussians,
        source_metadata={},
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return ModelRead.model_validate(row)


@router.get("", response_model=Page[ModelRead])
async def list_models(
    session: Annotated[AsyncSession, Depends(session_scope)],
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> Page[ModelRead]:
    limit = min(limit, MAX_LIMIT)
    stmt = (
        select(Model)
        .where(Model.deleted_at.is_(None))
        .order_by(Model.created_at.desc(), Model.id.desc())
        .limit(limit + 1)
    )
    if cursor:
        try:
            cursor_id = uuid.UUID(cursor)
        except ValueError as e:
            raise HTTPException(400, f"bad cursor: {e}") from e
        anchor = await session.get(Model, cursor_id)
        if anchor is None:
            raise HTTPException(400, "cursor not found")
        stmt = stmt.where(Model.created_at < anchor.created_at)

    rows = (await session.scalars(stmt)).all()
    next_cursor = str(rows[-1].id) if len(rows) > limit else None
    items = [ModelRead.model_validate(r) for r in rows[:limit]]
    return Page[ModelRead](items=items, next_cursor=next_cursor)


@router.get("/{model_id}", response_model=ModelRead)
async def get_model(
    model_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(session_scope)],
) -> ModelRead:
    row = await session.get(Model, model_id)
    if row is None or row.deleted_at is not None:
        raise HTTPException(404, "model not found")
    return ModelRead.model_validate(row)


@router.delete("/{model_id}", status_code=204)
async def delete_model(
    model_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(session_scope)],
) -> None:
    import datetime as dt

    row = await session.get(Model, model_id)
    if row is None or row.deleted_at is not None:
        raise HTTPException(404, "model not found")
    row.deleted_at = dt.datetime.now(dt.UTC)
    await session.flush()
