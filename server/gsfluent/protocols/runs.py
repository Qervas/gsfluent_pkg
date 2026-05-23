"""RunManager Protocol — layer 1.

Lifecycle controller. Submits runs, cancels them, exposes status, streams
events, recovers in-flight runs on boot. Concrete: AsyncioRunManager
(Phase 2, replaces server/gsfluent/core/runner.py).
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, NewType, Protocol, runtime_checkable

from gsfluent.protocols.sim import ModelRef, ValidatedRecipe

RunId = NewType("RunId", str)
"""Opaque run identifier. Implementation defines format (ULID, UUIDv7, etc.)."""


class RunState(str, Enum):
    """Lifecycle states. Terminal states: COMPLETED, FAILED, CANCELLED, INTERRUPTED."""
    QUEUED = "queued"
    STARTED = "started"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


TERMINAL_RUN_STATES = frozenset({
    RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED, RunState.INTERRUPTED,
})


class ValidationError(Exception):
    """Recipe failed Pydantic strict-mode validation. Translates to HTTP 422."""


class CapExceededError(Exception):
    """Recipe violated a configured cap (particle count, wall-time, recipe size).
    Translates to HTTP 422 with structured detail."""


@dataclass(frozen=True)
class RunStatus:
    """Snapshot of a run's current state."""
    id: RunId
    state: RunState
    error: dict[str, Any] | None = None  # {kind, message, details, trace_id}
    paths: dict[str, str] = field(default_factory=dict)  # frames_dir, gsq_path, manifest_path
    sequence_name: str | None = None  # mirrors RunStateRecord.sequence_name


@dataclass(frozen=True)
class RunEvent:
    """One structured event in a run's lifecycle event stream."""
    event: str
    context: dict[str, Any]


@dataclass(frozen=True)
class RecoveryReport:
    """Returned by RunManager.recover_on_boot()."""
    reattached: int
    interrupted: int
    terminal_already: int


@runtime_checkable
class RunManager(Protocol):
    """Manages run lifecycle: submit, cancel, status, event stream, boot recovery."""

    async def submit(
        self, recipe: ValidatedRecipe, *, model: ModelRef
    ) -> RunId:
        """Validate, persist initial state, schedule background task.
        Returns immediately with RunId. Raises ValidationError or
        CapExceededError (both → 422)."""
        ...

    async def cancel(self, run_id: RunId) -> None:
        """Idempotent. Initiates PG-SIGTERM; background task escalates
        to PG-SIGKILL after grace period if still alive."""
        ...

    async def status(self, run_id: RunId) -> RunStatus:
        """Current snapshot. Raises KeyError if run_id unknown."""
        ...

    async def stream_events(
        self, run_id: RunId
    ) -> AsyncIterator[RunEvent]:
        """SSE-style feed of structured events for this run.
        Yields existing events first, then new ones until run is terminal."""
        ...

    async def recover_on_boot(self) -> RecoveryReport:
        """Scan state dir, reconcile in-flight runs with live PIDs.
        Reattach where PID + start-time match; mark interrupted otherwise.
        Called once from FastAPI lifespan startup."""
        ...
