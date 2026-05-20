"""Postgres-backed enums used across the data model."""

from __future__ import annotations

import enum


class RunStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class ArtifactKind(str, enum.Enum):
    cell = "cell"
    log = "log"
    video = "video"
    preview = "preview"
    manifest = "manifest"


class RenderSessionStatus(str, enum.Enum):
    signaling = "signaling"
    active = "active"
    closed = "closed"
    failed = "failed"
