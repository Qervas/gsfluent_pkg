"""/v1/artifacts/{id}/{url,data} — artifact download.

MinIO runs on the api's loopback (127.0.0.1:19000), so a presigned URL
pointing there is unreachable from a browser outside sxyin. The /url
endpoint therefore returns a *same-origin* path that the api itself
proxies — `/url` is just metadata + a relative href, and `/data`
streams the actual bytes through the api process.

Trade-off: each cell flows through the api process (CPU + memory hop)
instead of direct from MinIO. For demo traffic (~75 MB per scene)
this is fine; when MinIO becomes externally reachable (proper public
mapping or S3-compatible front) we can flip /url back to a real
presigned URL with one diff.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import session_scope
from ..models.artifact import Artifact
from ..models.enums import ArtifactKind
from ..schemas import PresignedUrl
from ..storage import stream_object

router = APIRouter(prefix="/v1/artifacts", tags=["artifacts"])


_KIND_MIME = {
    ArtifactKind.cell: "application/octet-stream",
    ArtifactKind.log: "text/plain; charset=utf-8",
    ArtifactKind.video: "video/mp4",
    ArtifactKind.preview: "application/octet-stream",   # .ply
    ArtifactKind.manifest: "application/json",
}


@router.get("/{artifact_id}/url", response_model=PresignedUrl)
async def get_artifact_url(
    artifact_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(session_scope)],
) -> PresignedUrl:
    """Return a same-origin URL the browser can fetch directly.

    No expiry on the proxy path (it's auth'd by being reachable at all
    in this internal-demo deploy), but the schema keeps `expires_at`
    for API-shape stability with the future presign path.
    """
    art = await session.get(Artifact, artifact_id)
    if art is None:
        raise HTTPException(404, "artifact not found")
    return PresignedUrl(
        url=f"/v1/artifacts/{art.id}/data",
        expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(hours=1),
    )


@router.get("/{artifact_id}/data")
async def stream_artifact(
    artifact_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(session_scope)],
) -> StreamingResponse:
    """Stream the artifact bytes through the api. Browser fetches this
    same-origin via the public mapping; api pulls from internal MinIO."""
    art = await session.get(Artifact, artifact_id)
    if art is None:
        raise HTTPException(404, "artifact not found")

    bucket, _, key = art.minio_path.partition("/")
    media_type = _KIND_MIME.get(art.kind, "application/octet-stream")

    return StreamingResponse(
        stream_object(bucket, key),
        media_type=media_type,
        headers={
            # Cells/previews are content-addressed (path includes run_id +
            # frame_idx + extension); safe to cache. Logs are appended to
            # but the artifact row reflects size at flush time.
            "Cache-Control": "public, max-age=3600",
            "Content-Length": str(art.size_bytes),
            "X-Artifact-Kind": art.kind.value,
        },
    )
