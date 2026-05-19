"""Pydantic request/response schemas.

Strict by default (extra='forbid') so the API rejects unknown fields.
Keeps the surface honest as it evolves.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .models.enums import ArtifactKind, RenderSessionStatus, RunStatus


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


# ---------- Model (3DGS asset) ----------


class ModelRead(_Strict):
    id: uuid.UUID
    name: str
    minio_path: str
    size_bytes: int
    num_gaussians: int | None
    source_metadata: dict[str, Any]
    created_at: dt.datetime
    updated_at: dt.datetime


# ---------- Recipe ----------


class RecipeCreate(_Strict):
    name: str = Field(min_length=1, max_length=200)
    content: dict[str, Any]


class RecipePatch(_Strict):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    content: dict[str, Any] | None = None
    starred: bool | None = None


class RecipeRead(_Strict):
    id: uuid.UUID
    name: str
    content: dict[str, Any]
    version: int
    starred: bool
    created_at: dt.datetime
    updated_at: dt.datetime


class RecipeVersionRead(_Strict):
    id: uuid.UUID
    recipe_id: uuid.UUID
    version: int
    content: dict[str, Any]
    created_at: dt.datetime


# ---------- Run ----------


class RunCreate(_Strict):
    name: str = Field(min_length=1, max_length=200)
    model_id: uuid.UUID
    recipe_id: uuid.UUID | None = None
    # Either recipe_id or recipe_inline must be supplied.
    recipe_inline: dict[str, Any] | None = None


class RunRead(_Strict):
    id: uuid.UUID
    name: str
    status: RunStatus
    model_id: uuid.UUID
    recipe_id: uuid.UUID | None
    recipe_snapshot: dict[str, Any]
    worker_id: str | None
    queued_at: dt.datetime
    started_at: dt.datetime | None
    completed_at: dt.datetime | None
    gpu_seconds: float
    peak_vram_bytes: int
    error: str | None
    created_at: dt.datetime


# ---------- Artifact ----------


class ArtifactRead(_Strict):
    id: uuid.UUID
    run_id: uuid.UUID
    kind: ArtifactKind
    frame_idx: int | None
    minio_path: str
    size_bytes: int
    created_at: dt.datetime


class PresignedUrl(_Strict):
    url: str
    expires_at: dt.datetime


# ---------- RenderSession ----------


class RenderSessionCreate(_Strict):
    run_id: uuid.UUID | None = None
    model_id: uuid.UUID | None = None


class RenderSessionCreated(_Strict):
    session_id: uuid.UUID
    ice_servers: list[dict[str, Any]]


class SdpOffer(_Strict):
    sdp: str
    type: str


class SdpAnswer(_Strict):
    sdp: str
    type: str


class IceCandidate(_Strict):
    candidate: str
    sdpMid: str | None
    sdpMLineIndex: int | None


class RenderSessionRead(_Strict):
    id: uuid.UUID
    run_id: uuid.UUID | None
    model_id: uuid.UUID | None
    worker_id: str | None
    started_at: dt.datetime
    ended_at: dt.datetime | None
    gpu_seconds: float
    bytes_streamed: int
    status: RenderSessionStatus


# ---------- Pagination ----------


class Page[T](_Strict):
    items: list[T]
    next_cursor: str | None
