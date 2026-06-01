"""Stdlib-logging-based JSON EventEmitter — no extra deps.

Layer 6 concrete impl. Writes one JSON object per line to a configurable
text stream (default: stdout, which systemd routes to journald). The
.child() method returns an emitter that automatically merges a fixed
context into every event — used by RunManager to bind run_id and
sequence_name to a per-run logger.
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import sys
from typing import Any, TextIO


def _coerce(value: Any) -> Any:
    """Make a value JSON-serializable. Falls back to str() for unknown types."""
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# Numeric severity for each level name, mirroring stdlib logging's ordering.
# Used to decide whether an event clears the emitter's min_level threshold.
_LEVEL_SEVERITY = {
    "DEBUG": 10,
    "INFO": 20,
    "WARNING": 30,
    "WARN": 30,
    "ERROR": 40,
    "CRITICAL": 50,
}


class EmitLevelMethods:
    """Mixin adding info()/debug()/error() that delegate to ``emit()``.

    The EventEmitter Protocol only guarantees ``emit()`` + ``child()`` (so
    minimal structural stubs keep satisfying it). These three convenience
    methods are pure sugar over ``emit(event, level=...)`` — any emitter,
    including the per-run wrapper in run_manager and the test doubles, mixes
    this in to get them without reimplementing the level plumbing.
    """

    def emit(self, event: str, **context: Any) -> None:  # pragma: no cover
        raise NotImplementedError

    def info(self, event: str, **context: Any) -> None:
        self.emit(event, level="INFO", **context)

    def debug(self, event: str, **context: Any) -> None:
        self.emit(event, level="DEBUG", **context)

    def error(self, event: str, **context: Any) -> None:
        self.emit(event, level="ERROR", **context)


class StdlibJSONEmitter(EmitLevelMethods):
    """EventEmitter that writes one JSON line per event to a text stream.

    Construction:
        emitter = StdlibJSONEmitter(stream=sys.stdout)         # default
        emitter = StdlibJSONEmitter(stream=open("events.jsonl", "a"))
        emitter = StdlibJSONEmitter(level="DEBUG")             # per-event level

    Output shape (one line):
        {"ts": "2026-05-22T12:34:56.789Z", "level": "INFO",
         "event": "run.started", "run_id": "abc", ...}
    """

    def __init__(
        self,
        stream: TextIO | None = None,
        level: str = "INFO",
        min_level: str = "DEBUG",
        _context: dict[str, Any] | None = None,
    ) -> None:
        self._stream = stream if stream is not None else sys.stdout
        # `level` is the default level stamped on a bare emit() with no level=.
        self._level = level
        # `min_level` is the suppression threshold: events below it are dropped.
        # Default DEBUG == emit everything (the production root raises this via
        # GSFLUENT_LOG_LEVEL so DEBUG noise can be turned off without code).
        self._min_level = min_level
        self._context: dict[str, Any] = dict(_context or {})

    def _should_emit(self, level: str) -> bool:
        """True if `level` clears this emitter's min_level threshold.

        Unknown level names fail open (treated as at-threshold) so a typo in
        a level string never silently swallows a log line.
        """
        threshold = _LEVEL_SEVERITY.get(self._min_level.upper(), 0)
        severity = _LEVEL_SEVERITY.get(level.upper(), threshold)
        return severity >= threshold

    def emit(self, event: str, **context: Any) -> None:
        # Per-call level takes precedence over the emitter's default level.
        level = context.pop("level", self._level)
        if not isinstance(level, str):
            level = str(level).upper()

        if not self._should_emit(level):
            return

        merged: dict[str, Any] = {
            "ts": _now_iso(),
            "level": level,
            "event": event,
            **{k: _coerce(v) for k, v in self._context.items()},
            **{k: _coerce(v) for k, v in context.items()},
        }
        self._stream.write(json.dumps(merged, separators=(",", ":")) + "\n")
        # Best-effort flush so tail/journalctl see events promptly.
        # Acceptable to skip if the stream doesn't expose flush (e.g. some test stubs).
        flush = getattr(self._stream, "flush", None)
        if callable(flush):
            flush()

    def child(self, **context: Any) -> StdlibJSONEmitter:
        merged = {**self._context, **context}
        return StdlibJSONEmitter(
            stream=self._stream,
            level=self._level,
            min_level=self._min_level,
            _context=merged,
        )

    # info()/debug()/error() come from EmitLevelMethods.


# Adapter that bridges stdlib `logging` calls to our EventEmitter.
# Phase 6 will use this when auditing the codebase for `print()` and
# stdlib `logging.info()` calls that should become structured events.
class RunLogAdapter(logging.LoggerAdapter):  # type: ignore[type-arg]
    """LoggerAdapter that auto-attaches run context to stdlib log records.

    Use when calling into third-party libraries that use stdlib logging:
        run_log = RunLogAdapter(logging.getLogger("gsfluent.runner"),
                                extra={"run_id": run_id})
        run_log.info("sim started")  # JSON output includes run_id

    Note: stdlib `LoggerAdapter` was made generic in 3.11; on 3.10 it
    isn't parametrizable, hence the type: ignore on the class line.
    Liskov override on `process` is also intentional — we narrow `kwargs`
    to `dict[str, Any]` because that's what stdlib actually passes in
    practice, and Python's own typeshed has the same divergence.
    """

    def process(  # type: ignore[override]
        self, msg: Any, kwargs: dict[str, Any]
    ) -> tuple[Any, dict[str, Any]]:
        # The JsonFormatter (below) reads the extra dict directly.
        if "extra" in kwargs:
            merged = {**(self.extra or {}), **kwargs["extra"]}
        else:
            merged = dict(self.extra) if self.extra else {}
        kwargs["extra"] = merged
        return msg, kwargs


class JsonFormatter(logging.Formatter):
    """stdlib logging.Formatter that produces our JSON event shape.

    Use when configuring a stdlib logger to emit JSON instead of plain text:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        logging.getLogger().addHandler(handler)
    """

    def format(self, record: logging.LogRecord) -> str:
        obj: dict[str, Any] = {
            "ts": _now_iso(),
            "level": record.levelname,
            "event": record.name,  # logger name = event-ish dotted path
            "message": record.getMessage(),
        }
        # Pull extras (everything not in stdlib's standard LogRecord attrs)
        for key, val in record.__dict__.items():
            if key in _STDLIB_LOGRECORD_ATTRS:
                continue
            obj[key] = _coerce(val)
        return json.dumps(obj, separators=(",", ":"))


_STDLIB_LOGRECORD_ATTRS = frozenset({
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "message", "asctime", "taskName",
})
