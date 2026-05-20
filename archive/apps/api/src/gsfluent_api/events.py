"""Typed event models for the WS event stream.

Spec §6.6: discriminated union keyed on `type`. Pydantic v2 validates
inbound (for tests) and serializes outbound payloads. `seq` is filled in
by `event_store.publish` — the producer leaves it None.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

from .models.enums import ArtifactKind, RenderSessionStatus


def _now_utc() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class _Event(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timestamp: dt.datetime = Field(default_factory=_now_utc)
    seq: int | None = None


# ---------- run.* (run-scoped) ----------


class RunQueuedEvent(_Event):
    type: Literal["run.queued"] = "run.queued"
    run_id: uuid.UUID


class RunStartedEvent(_Event):
    type: Literal["run.started"] = "run.started"
    run_id: uuid.UUID
    worker_id: str | None = None


class RunProgressEvent(_Event):
    type: Literal["run.progress"] = "run.progress"
    run_id: uuid.UUID
    frame_idx: int
    fps: float | None = None


class RunCompletedEvent(_Event):
    type: Literal["run.completed"] = "run.completed"
    run_id: uuid.UUID
    gpu_seconds: float
    peak_vram_bytes: int | None = None


class RunFailedEvent(_Event):
    type: Literal["run.failed"] = "run.failed"
    run_id: uuid.UUID
    error: str


class RunCancelledEvent(_Event):
    type: Literal["run.cancelled"] = "run.cancelled"
    run_id: uuid.UUID


# ---------- artifact.* ----------


class ArtifactCreatedEvent(_Event):
    type: Literal["artifact.created"] = "artifact.created"
    run_id: uuid.UUID
    artifact_id: uuid.UUID
    kind: ArtifactKind
    frame_idx: int | None
    size_bytes: int


# ---------- log.* ----------


class LogLineEvent(_Event):
    type: Literal["log.line"] = "log.line"
    run_id: uuid.UUID
    level: str
    message: str


# ---------- render-session.* + webrtc.* ----------


class RenderSessionStateEvent(_Event):
    type: Literal["render-session.state"] = "render-session.state"
    session_id: uuid.UUID
    state: RenderSessionStatus


class WebRtcCandidateEvent(_Event):
    type: Literal["webrtc.candidate"] = "webrtc.candidate"
    session_id: uuid.UUID
    candidate: dict[str, Any]


Event = Annotated[
    Union[
        RunQueuedEvent,
        RunStartedEvent,
        RunProgressEvent,
        RunCompletedEvent,
        RunFailedEvent,
        RunCancelledEvent,
        ArtifactCreatedEvent,
        LogLineEvent,
        RenderSessionStateEvent,
        WebRtcCandidateEvent,
    ],
    Field(discriminator="type"),
]


# ---------- channel naming ----------


def run_channel(run_id: uuid.UUID) -> str:
    return f"events:runs:{run_id}"


def log_channel(run_id: uuid.UUID) -> str:
    return f"events:logs:{run_id}"


def render_session_channel(session_id: uuid.UUID) -> str:
    return f"events:render-session:{session_id}"


def channel_for(event: _Event) -> str:
    """Where this event fans out."""
    if isinstance(event, LogLineEvent):
        return log_channel(event.run_id)
    if isinstance(event, RenderSessionStateEvent | WebRtcCandidateEvent):
        return render_session_channel(event.session_id)
    if hasattr(event, "run_id"):
        return run_channel(event.run_id)  # type: ignore[attr-defined]
    raise ValueError(f"no channel for {type(event).__name__}")
