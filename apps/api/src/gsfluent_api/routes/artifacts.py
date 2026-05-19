"""/v1/artifacts/{id}/url — presigned download URL for run artifacts.

The frontend's ViewerLocal downloads .ply preview frames via this
endpoint; the engine bridge in worker-sim uploads them to MinIO and
the api hands clients short-lived signed URLs (no public bucket).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import session_scope
from ..models.artifact import Artifact
from ..schemas import PresignedUrl
from ..storage import presigned_get_url

router = APIRouter(prefix="/v1/artifacts", tags=["artifacts"])


@router.get("/{artifact_id}/url", response_model=PresignedUrl)
async def get_presigned_url(
    artifact_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(session_scope)],
    expires_seconds: int = 300,
) -> PresignedUrl:
    art = await session.get(Artifact, artifact_id)
    if art is None:
        raise HTTPException(404, "artifact not found")

    bucket, _, key = art.minio_path.partition("/")
    expires = dt.timedelta(seconds=max(1, min(expires_seconds, 3600)))
    url = await presigned_get_url(bucket, key, expires=expires)
    return PresignedUrl(
        url=url,
        expires_at=dt.datetime.now(dt.UTC) + expires,
    )
