"""EventEmitter Protocol — structured-event sink, layer 6.

Concrete impls (observability/jsonlog.py: StdlibJSONEmitter) emit events
to a configured sink (stdout, file, journald-via-stdout, etc.). The
RunLogAdapter is built by RunManager via .child(run_id=..., sequence_name=...)
so every event from a run automatically carries that context.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class EventEmitter(Protocol):
    """Sink for structured events.

    Events are dotted noun.verb strings: `run.started`, `error.sim.gpu_oom`,
    `cell.cache.hit`. context kwargs must be JSON-serializable.
    Implementations auto-attach a timestamp; callers don't pass one.
    """

    def emit(self, event: str, **context: Any) -> None:
        """Emit one structured event.

        The optional `level` kwarg (string) is respected by implementations
        that support log levels (INFO, DEBUG, ERROR, WARNING, etc.).
        If omitted, implementations typically default to "INFO".

        Context values should be JSON-serializable. Non-serializable values
        are stringified by the StdlibJSONEmitter.
        """
        ...

    def child(self, **context: Any) -> EventEmitter:
        """Return a derived emitter that auto-attaches `context` to every
        emit(). Per-event kwargs take precedence over child context."""
        ...
