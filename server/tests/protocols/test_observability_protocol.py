"""Conformance tests for the EventEmitter Protocol.

Any concrete EventEmitter impl in the codebase must pass these tests.
Phase 1 has no concrete impl yet — the StdlibJSONEmitter from Task 3
will be exercised against this Protocol contract.
"""
from typing import Any

import pytest

from gsfluent.protocols.observability import EventEmitter


class _StubEmitter:
    """Minimal stub that satisfies the Protocol structurally."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []
        self._context: dict[str, Any] = {}

    def emit(self, event: str, **context: Any) -> None:
        merged = {**self._context, **context}
        self.events.append((event, merged))

    def child(self, **context: Any) -> "_StubEmitter":
        new = _StubEmitter()
        new.events = self.events  # share buffer for test inspection
        new._context = {**self._context, **context}
        return new


def test_stub_satisfies_event_emitter_protocol() -> None:
    stub: EventEmitter = _StubEmitter()
    assert isinstance(stub, EventEmitter)


def test_emit_records_event_with_context() -> None:
    stub = _StubEmitter()
    stub.emit("run.started", run_id="abc", particle_count=200_000)
    assert stub.events == [("run.started", {"run_id": "abc", "particle_count": 200_000})]


def test_child_emitter_inherits_and_extends_context() -> None:
    parent = _StubEmitter()
    child = parent.child(run_id="abc")
    child.emit("run.started", phase="sim")
    assert child.events[-1] == (
        "run.started",
        {"run_id": "abc", "phase": "sim"},
    )


def test_child_context_can_be_overridden_per_event() -> None:
    parent = _StubEmitter()
    child = parent.child(run_id="abc")
    child.emit("run.started", run_id="xyz", phase="sim")
    # Per-event kwargs win over child context
    assert child.events[-1][1]["run_id"] == "xyz"
