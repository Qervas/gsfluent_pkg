# Phase 1 — Foundations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lay all the abstraction scaffolding (six Protocols, one concrete `EventEmitter`, state persistence, cap-checker, config loader, composition root skeleton) without changing any existing behavior. Foundation for Phases 2-7.

**Architecture:** Pure additions. Six `typing.Protocol` interface files under `server/gsfluent/protocols/`. One concrete `StdlibJSONEmitter` under `server/gsfluent/observability/` (no extra deps — stdlib `logging` + JSON formatter). State persistence module reads/writes `work/_state/runs/<id>.json`. Cap-checker module is pure functions over a recipe dict. Config dataclass loads from env vars. Composition root `build_app(AppConfig)` constructs a FastAPI app with concrete impls wired (stubs where Phase 2+ will fill in). Existing `create_app()` becomes a thin wrapper: `create_app() → build_app(AppConfig.from_env())`.

**Tech Stack:** Python 3.10+, `typing.Protocol`, `pydantic>=2.6`, stdlib `logging`, `pytest>=8`, `pytest-asyncio>=0.23`. **No new dependencies in Phase 1.**

**Spec reference:** `docs/superpowers/specs/2026-05-22-backend-bulletproofing-vertical-slice-design.md`

**Phase 1 is plan 1 of 7.** Phase 2 (extract impls) depends on the Protocols defined here. Phases 3-7 depend on Phase 1's `EventEmitter`, state persistence, and composition root.

---

## File Structure

### New files (Phase 1)

```
server/gsfluent/
├── protocols/
│   ├── __init__.py                  ← re-export all 6 protocols
│   ├── observability.py             ← EventEmitter Protocol
│   ├── sim.py                       ← SimulationEngine Protocol + typed errors
│   ├── fuse.py                      ← Fuser Protocol + typed errors
│   ├── cache.py                     ← CacheCodec Protocol + typed errors
│   ├── storage.py                   ← Storage Protocol + typed errors
│   └── runs.py                      ← RunManager Protocol + state types
├── observability/
│   ├── __init__.py                  ← re-export StdlibJSONEmitter
│   └── jsonlog.py                   ← StdlibJSONEmitter + RunLogAdapter + JsonFormatter
├── core/
│   ├── state.py                     ← run state JSON persistence + scanner
│   └── limits.py                    ← cap config + check_recipe_caps()
├── config.py                        ← AppConfig dataclass + from_env()
└── composition.py                   ← build_app(AppConfig) -> FastAPI

server/tests/
├── protocols/
│   ├── __init__.py
│   ├── test_observability_protocol.py
│   ├── test_sim_protocol.py
│   ├── test_fuse_protocol.py
│   ├── test_cache_protocol.py
│   ├── test_storage_protocol.py
│   └── test_runs_protocol.py
├── observability/
│   ├── __init__.py
│   └── test_jsonlog.py
├── core/
│   ├── (existing __init__.py)
│   ├── test_state.py
│   └── test_limits.py
├── test_config.py
└── test_composition.py
```

### Modified files (Phase 1)

```
server/gsfluent/server.py     ← create_app() delegates to composition.build_app(AppConfig.from_env())
                                Existing create_app() callers continue to work.
```

### Files NOT modified in Phase 1

```
server/gsfluent/core/runner.py     ← Phase 3 (sim orchestration rewrite)
server/gsfluent/api/*.py           ← Phase 3 (api/runs.py), Phase 5 (api/sequences.py)
server/gsfluent/core/library.py    ← Phase 2 (storage extraction)
server/tools/*                     ← Phase 2 (script → CLI wrapper conversion)
server/supervise.sh                ← Phase 4 (replaced by systemd)
frontend/python/viser_headless.py  ← Phase 5 (client-side streaming hardening)
```

---

## Tasks

### Task 1: Branch + baseline test verification

**Files:**
- No file edits in this task. Verification + commit only.

- [ ] **Step 1: Create the phase branch**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git checkout -b phase-1-foundations
```

Expected: `Switched to a new branch 'phase-1-foundations'`

- [ ] **Step 2: Verify baseline test suite passes**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/ -v --tb=short 2>&1 | tail -40
```

Expected: all 12 existing test files pass. Note any tests that fail BEFORE Phase 1 work — those are pre-existing issues, not regressions. Record the baseline pass/fail count in the task notes.

- [ ] **Step 3: Verify Python version**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
.venv/bin/python --version
```

Expected: `Python 3.10` or higher. If lower, halt the plan — `typing.Protocol` runtime checks need 3.10+.

- [ ] **Step 4: Confirm pyproject dependencies**

```bash
grep -E "fastapi|pydantic|pytest" /home/frankyin/Desktop/work/gsfluent_pkg/server/pyproject.toml
```

Expected: see `fastapi>=0.110`, `pydantic>=2.6`, `pytest>=8`, `pytest-asyncio>=0.23`. If any missing, halt and update pyproject.

- [ ] **Step 5: No commit yet — Task 1 is verification only**

---

### Task 2: protocols/observability.py — EventEmitter Protocol

**Files:**
- Create: `server/gsfluent/protocols/__init__.py`
- Create: `server/gsfluent/protocols/observability.py`
- Create: `server/tests/protocols/__init__.py`
- Create: `server/tests/protocols/test_observability_protocol.py`

- [ ] **Step 1: Write the failing test**

Create `server/tests/protocols/__init__.py` as an empty file:

```python
```

Create `server/tests/protocols/test_observability_protocol.py`:

```python
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
```

- [ ] **Step 2: Run test, confirm it fails**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/protocols/test_observability_protocol.py -v
```

Expected: `ModuleNotFoundError: No module named 'gsfluent.protocols'` or similar import error.

- [ ] **Step 3: Implement the Protocol**

Create `server/gsfluent/protocols/__init__.py`:

```python
"""Pure interface contracts for the six gsfluent layers.

No logic lives here — concrete implementations live under core/, storage/,
observability/, etc., and are wired in composition.py.
"""
from gsfluent.protocols.observability import EventEmitter

__all__ = ["EventEmitter"]
```

Create `server/gsfluent/protocols/observability.py`:

```python
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
        """Emit one event. Idempotent semantics not guaranteed —
        callers should not double-emit on retry."""
        ...

    def child(self, **context: Any) -> "EventEmitter":
        """Return a derived emitter that auto-attaches `context` to every
        emit(). Per-event kwargs take precedence over child context."""
        ...
```

- [ ] **Step 4: Run tests, confirm pass**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/protocols/test_observability_protocol.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add server/gsfluent/protocols/__init__.py \
        server/gsfluent/protocols/observability.py \
        server/tests/protocols/__init__.py \
        server/tests/protocols/test_observability_protocol.py
git commit -m "phase-1: protocols/observability.py — EventEmitter Protocol + stub conformance tests"
```

---

### Task 3: observability/jsonlog.py — StdlibJSONEmitter concrete impl

**Files:**
- Create: `server/gsfluent/observability/__init__.py`
- Create: `server/gsfluent/observability/jsonlog.py`
- Create: `server/tests/observability/__init__.py`
- Create: `server/tests/observability/test_jsonlog.py`

- [ ] **Step 1: Write the failing test**

Create `server/tests/observability/__init__.py` as empty file.

Create `server/tests/observability/test_jsonlog.py`:

```python
"""Tests for the stdlib-based JSON EventEmitter implementation."""
import io
import json
import re

import pytest

from gsfluent.observability.jsonlog import StdlibJSONEmitter
from gsfluent.protocols.observability import EventEmitter


def _parse_lines(stream: io.StringIO) -> list[dict]:
    """Parse one JSON object per line from the in-memory stream."""
    stream.seek(0)
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


def test_stdlib_json_emitter_satisfies_event_emitter_protocol() -> None:
    stream = io.StringIO()
    emitter = StdlibJSONEmitter(stream=stream)
    assert isinstance(emitter, EventEmitter)


def test_emit_writes_one_json_line() -> None:
    stream = io.StringIO()
    emitter = StdlibJSONEmitter(stream=stream)
    emitter.emit("run.started", run_id="abc", particle_count=200_000)
    events = _parse_lines(stream)
    assert len(events) == 1
    assert events[0]["event"] == "run.started"
    assert events[0]["run_id"] == "abc"
    assert events[0]["particle_count"] == 200_000


def test_emit_auto_attaches_iso_timestamp() -> None:
    stream = io.StringIO()
    emitter = StdlibJSONEmitter(stream=stream)
    emitter.emit("run.started", run_id="abc")
    events = _parse_lines(stream)
    # ISO 8601 with Z suffix or +00:00 offset
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", events[0]["ts"])


def test_emit_includes_log_level() -> None:
    stream = io.StringIO()
    emitter = StdlibJSONEmitter(stream=stream)
    emitter.emit("run.started", run_id="abc")
    events = _parse_lines(stream)
    assert events[0]["level"] == "INFO"


def test_child_emitter_attaches_context_to_every_event() -> None:
    stream = io.StringIO()
    parent = StdlibJSONEmitter(stream=stream)
    child = parent.child(run_id="abc", sequence_name="demo")
    child.emit("run.started")
    child.emit("run.completed", duration_sec=42.0)
    events = _parse_lines(stream)
    assert len(events) == 2
    for e in events:
        assert e["run_id"] == "abc"
        assert e["sequence_name"] == "demo"
    assert events[1]["duration_sec"] == 42.0


def test_child_context_overridable_per_event() -> None:
    stream = io.StringIO()
    parent = StdlibJSONEmitter(stream=stream)
    child = parent.child(run_id="abc")
    child.emit("run.started", run_id="xyz")
    events = _parse_lines(stream)
    assert events[0]["run_id"] == "xyz"


def test_grandchild_emitter_chains_context() -> None:
    stream = io.StringIO()
    root = StdlibJSONEmitter(stream=stream)
    a = root.child(run_id="abc")
    b = a.child(phase="sim")
    b.emit("sim.started")
    events = _parse_lines(stream)
    assert events[0]["run_id"] == "abc"
    assert events[0]["phase"] == "sim"


def test_non_json_serializable_value_is_coerced_to_string() -> None:
    """Custom objects shouldn't crash emit(); they should str() instead."""
    stream = io.StringIO()
    emitter = StdlibJSONEmitter(stream=stream)

    class CustomObj:
        def __str__(self) -> str:
            return "custom-obj-repr"

    emitter.emit("test.weird", obj=CustomObj())
    events = _parse_lines(stream)
    assert events[0]["obj"] == "custom-obj-repr"
```

- [ ] **Step 2: Run tests, confirm fail**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/observability/test_jsonlog.py -v
```

Expected: `ModuleNotFoundError: No module named 'gsfluent.observability'`.

- [ ] **Step 3: Implement the EventEmitter**

Create `server/gsfluent/observability/__init__.py`:

```python
"""Concrete EventEmitter implementations and the JSON log formatter."""
from gsfluent.observability.jsonlog import StdlibJSONEmitter

__all__ = ["StdlibJSONEmitter"]
```

Create `server/gsfluent/observability/jsonlog.py`:

```python
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


class StdlibJSONEmitter:
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
        _context: dict[str, Any] | None = None,
    ) -> None:
        self._stream = stream if stream is not None else sys.stdout
        self._level = level
        self._context: dict[str, Any] = dict(_context or {})

    def emit(self, event: str, **context: Any) -> None:
        merged: dict[str, Any] = {
            "ts": _now_iso(),
            "level": self._level,
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

    def child(self, **context: Any) -> "StdlibJSONEmitter":
        merged = {**self._context, **context}
        return StdlibJSONEmitter(
            stream=self._stream,
            level=self._level,
            _context=merged,
        )


# Adapter that bridges stdlib `logging` calls to our EventEmitter.
# Phase 6 will use this when auditing the codebase for `print()` and
# stdlib `logging.info()` calls that should become structured events.
class RunLogAdapter(logging.LoggerAdapter):
    """LoggerAdapter that auto-attaches run context to stdlib log records.

    Use when calling into third-party libraries that use stdlib logging:
        run_log = RunLogAdapter(logging.getLogger("gsfluent.runner"),
                                extra={"run_id": run_id})
        run_log.info("sim started")  # JSON output includes run_id
    """

    def process(self, msg: Any, kwargs: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
        # The JsonFormatter (below) reads the extra dict directly.
        if "extra" in kwargs:
            merged = {**self.extra, **kwargs["extra"]}
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
```

- [ ] **Step 4: Run tests, confirm pass**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/observability/test_jsonlog.py -v
```

Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add server/gsfluent/observability/__init__.py \
        server/gsfluent/observability/jsonlog.py \
        server/tests/observability/__init__.py \
        server/tests/observability/test_jsonlog.py
git commit -m "phase-1: observability/jsonlog.py — StdlibJSONEmitter (no extra deps) + RunLogAdapter + JsonFormatter"
```

---

### Task 4: protocols/storage.py — Storage Protocol

**Files:**
- Create: `server/gsfluent/protocols/storage.py`
- Modify: `server/gsfluent/protocols/__init__.py`
- Create: `server/tests/protocols/test_storage_protocol.py`

- [ ] **Step 1: Write the failing test**

Create `server/tests/protocols/test_storage_protocol.py`:

```python
"""Conformance tests for the Storage Protocol.

Any concrete Storage impl must pass these tests. Concrete impls land
in Phase 2 (FilesystemStorage). Phase 1 uses an in-memory stub to
verify the contract shape.
"""
import io
from typing import AsyncIterator

import pytest

from gsfluent.protocols.storage import Storage, StorageStat


class _InMemoryStorage:
    """Stub Storage impl backed by a dict[str, bytes] — for protocol shape verification."""

    def __init__(self) -> None:
        self._data: dict[str, bytes] = {}
        self._mtime: dict[str, float] = {}

    async def put(self, key: str, src, metadata: dict[str, str]) -> dict:
        body = src.read()
        self._data[key] = body
        self._mtime[key] = 0.0  # deterministic for tests
        return {"key": key, "size": len(body)}

    async def get(self, key: str) -> AsyncIterator[bytes]:
        async def _gen():
            yield self._data[key]
        return _gen()

    async def get_range(self, key: str, start: int, end: int | None) -> AsyncIterator[bytes]:
        sl = self._data[key][start:end]
        async def _gen():
            yield sl
        return _gen()

    async def stat(self, key: str) -> StorageStat | None:
        if key not in self._data:
            return None
        return StorageStat(
            size=len(self._data[key]),
            mtime=self._mtime[key],
            etag=f'"{len(self._data[key])}-{int(self._mtime[key])}"',
        )

    async def exists(self, key: str) -> bool:
        return key in self._data


def test_stub_satisfies_storage_protocol() -> None:
    stub: Storage = _InMemoryStorage()
    assert isinstance(stub, Storage)


@pytest.mark.asyncio
async def test_put_then_stat_returns_size_and_etag() -> None:
    s = _InMemoryStorage()
    await s.put("a.gsq", io.BytesIO(b"abc"), {})
    st = await s.stat("a.gsq")
    assert st is not None
    assert st.size == 3
    assert st.etag.startswith('"3-')


@pytest.mark.asyncio
async def test_stat_returns_none_for_missing_key() -> None:
    s = _InMemoryStorage()
    assert (await s.stat("nope.gsq")) is None


@pytest.mark.asyncio
async def test_exists_reflects_put() -> None:
    s = _InMemoryStorage()
    assert (await s.exists("a")) is False
    await s.put("a", io.BytesIO(b"x"), {})
    assert (await s.exists("a")) is True
```

- [ ] **Step 2: Run test, confirm fail**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/protocols/test_storage_protocol.py -v
```

Expected: import error for `gsfluent.protocols.storage`.

- [ ] **Step 3: Implement the Protocol**

Create `server/gsfluent/protocols/storage.py`:

```python
"""Storage Protocol — layer 5.

Persistent key-addressable byte storage. Concrete impls land in Phase 2:
FilesystemStorage (current backend), and later S3Storage/GCSStorage.

Errors are typed and live in this module so callers can catch them
without importing concrete impls.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator, BinaryIO, Protocol, runtime_checkable


class StorageError(Exception):
    """Base for storage-layer errors."""


class StorageNotFoundError(StorageError):
    """Key does not exist."""


class StorageTransientError(StorageError):
    """Transient I/O failure — caller may retry with backoff."""


@dataclass(frozen=True)
class StorageStat:
    """Result of Storage.stat()."""
    size: int
    mtime: float        # POSIX timestamp
    etag: str           # quoted weak ETag, e.g. '"12345-1779266297"'


@dataclass(frozen=True)
class StorageHandle:
    """Result of Storage.put()."""
    key: str
    size: int
    etag: str


@runtime_checkable
class Storage(Protocol):
    """Persistent byte storage keyed by string identifier.

    Keys are filesystem-safe relative paths. Concrete impls validate.
    """

    async def put(
        self, key: str, src: BinaryIO, metadata: dict[str, str]
    ) -> StorageHandle:
        """Write src to key. metadata is impl-defined (e.g. content-type).
        Raises StorageTransientError on transient failure."""
        ...

    async def get(self, key: str) -> AsyncIterator[bytes]:
        """Stream the whole object as chunks.
        Raises StorageNotFoundError if key absent."""
        ...

    async def get_range(
        self, key: str, start: int, end: int | None
    ) -> AsyncIterator[bytes]:
        """Stream a byte range [start, end). end=None means to EOF.
        Raises StorageNotFoundError if key absent."""
        ...

    async def stat(self, key: str) -> StorageStat | None:
        """Return size+mtime+etag, or None if key absent. Never raises."""
        ...

    async def exists(self, key: str) -> bool:
        """Cheap existence check. Never raises."""
        ...
```

Update `server/gsfluent/protocols/__init__.py`:

```python
"""Pure interface contracts for the six gsfluent layers.

No logic lives here — concrete implementations live under core/, storage/,
observability/, etc., and are wired in composition.py.
"""
from gsfluent.protocols.observability import EventEmitter
from gsfluent.protocols.storage import (
    Storage,
    StorageError,
    StorageHandle,
    StorageNotFoundError,
    StorageStat,
    StorageTransientError,
)

__all__ = [
    "EventEmitter",
    "Storage",
    "StorageError",
    "StorageHandle",
    "StorageNotFoundError",
    "StorageStat",
    "StorageTransientError",
]
```

- [ ] **Step 4: Run tests, confirm pass**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/protocols/test_storage_protocol.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add server/gsfluent/protocols/storage.py \
        server/gsfluent/protocols/__init__.py \
        server/tests/protocols/test_storage_protocol.py
git commit -m "phase-1: protocols/storage.py — Storage Protocol + typed errors (StorageStat, StorageHandle)"
```

---

### Task 5: protocols/cache.py — CacheCodec Protocol

**Files:**
- Create: `server/gsfluent/protocols/cache.py`
- Modify: `server/gsfluent/protocols/__init__.py`
- Create: `server/tests/protocols/test_cache_protocol.py`

- [ ] **Step 1: Write the failing test**

Create `server/tests/protocols/test_cache_protocol.py`:

```python
"""Conformance tests for the CacheCodec Protocol.

Phase 2 will implement GSQCodec against this contract. Phase 1 verifies
the Protocol shape with an in-memory stub.
"""
import io
from typing import AsyncIterator, BinaryIO, Iterable, Sequence

import pytest

from gsfluent.protocols.cache import (
    CacheCodec,
    CacheMetadata,
    CodecError,
    DecodedFrame,
    SplatFrame,
)
from gsfluent.protocols.observability import EventEmitter


class _StubEmitter:
    def emit(self, event: str, **context) -> None: pass
    def child(self, **context) -> "_StubEmitter": return self


class _IdentityCodec:
    """Stub codec: emits a single 'frame_count' byte then dummy frame bytes."""

    media_type = "application/x-stub"
    file_extension = ".stub"

    def encode(
        self,
        frames: Iterable[SplatFrame],
        out: BinaryIO,
        on_event: EventEmitter,
    ) -> CacheMetadata:
        count = 0
        for _ in frames:
            count += 1
            out.write(b"f")
        return CacheMetadata(n_splats=0, n_frames=count, bbox=(0, 0, 0, 0, 0, 0))

    async def decode_streaming(
        self, src: AsyncIterator[bytes]
    ) -> AsyncIterator[DecodedFrame]:
        async for chunk in src:
            for _ in chunk:
                yield DecodedFrame(frame_index=0, data={})

    def decode_all(self, src: BinaryIO) -> Sequence[DecodedFrame]:
        body = src.read()
        return [DecodedFrame(frame_index=i, data={}) for i in range(len(body))]


def test_stub_satisfies_cache_codec_protocol() -> None:
    codec: CacheCodec = _IdentityCodec()
    assert isinstance(codec, CacheCodec)


def test_codec_has_media_type_and_extension() -> None:
    codec = _IdentityCodec()
    assert codec.media_type == "application/x-stub"
    assert codec.file_extension == ".stub"


def test_encode_returns_metadata() -> None:
    codec = _IdentityCodec()
    out = io.BytesIO()
    meta = codec.encode([{}, {}, {}], out, _StubEmitter())
    assert meta.n_frames == 3
    assert out.getvalue() == b"fff"


def test_codec_error_is_an_exception() -> None:
    with pytest.raises(CodecError):
        raise CodecError("synthetic")
```

- [ ] **Step 2: Run test, confirm fail**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/protocols/test_cache_protocol.py -v
```

Expected: import error for `gsfluent.protocols.cache`.

- [ ] **Step 3: Implement the Protocol**

Create `server/gsfluent/protocols/cache.py`:

```python
"""CacheCodec Protocol — layer 4.

Encodes/decodes a sequence of splat frames to the codec's wire format.
Concrete: GSQCodec (Phase 2). Swap candidates: SPZ-per-frame, raw-PLY-zstd.

SplatFrame and DecodedFrame use dict[str, Any] for forward-compat: today
the .gsq codec emits xyz/quat/rgb/opacity/scales arrays; tomorrow a
SPZ-style codec might emit SH coefficients. Concrete impls type-check
their own keys.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import (
    Any,
    AsyncIterator,
    BinaryIO,
    Iterable,
    Protocol,
    Sequence,
    runtime_checkable,
)

# Note: TYPE_CHECKING avoids a real import cycle with observability — at
# runtime the parameter is duck-typed.
from gsfluent.protocols.observability import EventEmitter


class CodecError(Exception):
    """Base for cache-codec errors."""


class CodecUnsanitizableError(CodecError):
    """Frame data could not be sanitized to encodable form (e.g. all-NaN xyz)."""


SplatFrame = dict[str, Any]
"""One frame's worth of splat data, as named arrays.

Standard keys (when present):
    xyz       : np.ndarray (N, 3) float32
    quat      : np.ndarray (N, 4) float32  (w, x, y, z)
    rgb       : np.ndarray (N, 3) float32  (frame 0 only for static-attrs codecs)
    opacity   : np.ndarray (N,)   float32  (frame 0 only)
    scales    : np.ndarray (N, 3) float32  (frame 0 only)
"""


@dataclass(frozen=True)
class DecodedFrame:
    """One frame's worth of decoded splat data, indexed for playback."""
    frame_index: int
    data: dict[str, Any]


@dataclass(frozen=True)
class CacheMetadata:
    """Returned by CacheCodec.encode(); summary of the encoded sequence."""
    n_splats: int
    n_frames: int
    bbox: tuple[float, float, float, float, float, float]  # xmin..zmax
    fps_hint: float = 24.0


@runtime_checkable
class CacheCodec(Protocol):
    """Encode/decode a sequence of splat frames.

    Concrete impls declare media_type + file_extension for HTTP serving.
    """

    media_type: str
    file_extension: str

    def encode(
        self,
        frames: Iterable[SplatFrame],
        out: BinaryIO,
        on_event: EventEmitter,
    ) -> CacheMetadata:
        """Encode the sequence to out. Emits structured progress events.
        Raises CodecError on unsanitizable input."""
        ...

    async def decode_streaming(
        self, src: AsyncIterator[bytes]
    ) -> AsyncIterator[DecodedFrame]:
        """Decode-as-bytes-arrive. Yields one DecodedFrame per available frame."""
        ...

    def decode_all(self, src: BinaryIO) -> Sequence[DecodedFrame]:
        """Synchronous load (used by load-from-disk path)."""
        ...
```

Update `server/gsfluent/protocols/__init__.py` — replace contents:

```python
"""Pure interface contracts for the six gsfluent layers."""
from gsfluent.protocols.cache import (
    CacheCodec,
    CacheMetadata,
    CodecError,
    CodecUnsanitizableError,
    DecodedFrame,
    SplatFrame,
)
from gsfluent.protocols.observability import EventEmitter
from gsfluent.protocols.storage import (
    Storage,
    StorageError,
    StorageHandle,
    StorageNotFoundError,
    StorageStat,
    StorageTransientError,
)

__all__ = [
    "CacheCodec",
    "CacheMetadata",
    "CodecError",
    "CodecUnsanitizableError",
    "DecodedFrame",
    "EventEmitter",
    "SplatFrame",
    "Storage",
    "StorageError",
    "StorageHandle",
    "StorageNotFoundError",
    "StorageStat",
    "StorageTransientError",
]
```

- [ ] **Step 4: Run tests, confirm pass**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/protocols/test_cache_protocol.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add server/gsfluent/protocols/cache.py \
        server/gsfluent/protocols/__init__.py \
        server/tests/protocols/test_cache_protocol.py
git commit -m "phase-1: protocols/cache.py — CacheCodec Protocol + SplatFrame/DecodedFrame/CacheMetadata types"
```

---

### Task 6: protocols/fuse.py — Fuser Protocol

**Files:**
- Create: `server/gsfluent/protocols/fuse.py`
- Modify: `server/gsfluent/protocols/__init__.py`
- Create: `server/tests/protocols/test_fuse_protocol.py`

- [ ] **Step 1: Write the failing test**

Create `server/tests/protocols/test_fuse_protocol.py`:

```python
"""Conformance tests for the Fuser Protocol."""
from pathlib import Path

import pytest

from gsfluent.protocols.fuse import (
    Correspondence,
    FuseError,
    Fuser,
    ParticleFrame,
    SplatFrame as FusedSplatFrame,
)


class _StubFuser:
    """Identity fuser: passes particles through as splats."""

    def build_correspondence(
        self, reference_ply_path: Path, first_frame_particles: ParticleFrame
    ) -> Correspondence:
        return Correspondence(
            reference_ply_path=reference_ply_path,
            indices=tuple(range(len(first_frame_particles))),
            extent=1.0,
        )

    def fuse_frame(
        self, correspondence: Correspondence, particle_frame: ParticleFrame
    ) -> FusedSplatFrame:
        return {"xyz": list(particle_frame)}


def test_stub_satisfies_fuser_protocol() -> None:
    fuser: Fuser = _StubFuser()
    assert isinstance(fuser, Fuser)


def test_build_correspondence_returns_correspondence() -> None:
    fuser = _StubFuser()
    corr = fuser.build_correspondence(Path("/tmp/ref.ply"), [(0.0, 0.0, 0.0)])
    assert corr.reference_ply_path == Path("/tmp/ref.ply")
    assert corr.indices == (0,)


def test_fuse_frame_returns_splat_dict() -> None:
    fuser = _StubFuser()
    corr = fuser.build_correspondence(Path("/tmp/ref.ply"), [(0.0, 0.0, 0.0)])
    result = fuser.fuse_frame(corr, [(1.0, 2.0, 3.0)])
    assert result == {"xyz": [(1.0, 2.0, 3.0)]}


def test_fuse_error_is_an_exception() -> None:
    with pytest.raises(FuseError):
        raise FuseError("synthetic")
```

- [ ] **Step 2: Run test, confirm fail**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/protocols/test_fuse_protocol.py -v
```

Expected: import error.

- [ ] **Step 3: Implement the Protocol**

Create `server/gsfluent/protocols/fuse.py`:

```python
"""Fuser Protocol — layer 3.

Combines a reference 3DGS scene with per-frame sim particle positions
to produce per-frame fully-attributed splat frames. Concrete: KNNKabschFuser
(Phase 2; moved from server/tools/fuse_to_full_ply.py).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, Sequence, runtime_checkable


class FuseError(Exception):
    """Base for fuser errors."""


class FuseDegenerateClusterError(FuseError):
    """K-NN cluster degenerate; Kabsch cannot solve."""


class FuseNonFiniteInputError(FuseError):
    """Particle frame contains NaN/Inf positions."""


# ParticleFrame: a sequence of (x, y, z) tuples or an (N, 3) ndarray.
# Kept loose; concrete impls type-narrow as needed.
ParticleFrame = Any
SplatFrame = dict[str, Any]  # same shape as protocols.cache.SplatFrame


@dataclass(frozen=True)
class Correspondence:
    """Reference-to-particle mapping built once per sequence.

    Reused for every subsequent frame's fuse_frame() call.
    """
    reference_ply_path: Path
    indices: tuple[int, ...]
    extent: float


@runtime_checkable
class Fuser(Protocol):
    """Build per-frame splat frames from sim particle positions."""

    def build_correspondence(
        self,
        reference_ply_path: Path,
        first_frame_particles: ParticleFrame,
    ) -> Correspondence:
        """Compute reference→particle mapping. One-shot per sequence.
        Raises FuseError on degenerate input."""
        ...

    def fuse_frame(
        self,
        correspondence: Correspondence,
        particle_frame: ParticleFrame,
    ) -> SplatFrame:
        """Apply correspondence + per-frame rotation.
        Raises FuseError on non-finite input or degenerate K-NN cluster."""
        ...
```

Update `server/gsfluent/protocols/__init__.py` — add fuse imports:

```python
"""Pure interface contracts for the six gsfluent layers."""
from gsfluent.protocols.cache import (
    CacheCodec,
    CacheMetadata,
    CodecError,
    CodecUnsanitizableError,
    DecodedFrame,
    SplatFrame,
)
from gsfluent.protocols.fuse import (
    Correspondence,
    FuseDegenerateClusterError,
    FuseError,
    FuseNonFiniteInputError,
    Fuser,
    ParticleFrame,
)
from gsfluent.protocols.observability import EventEmitter
from gsfluent.protocols.storage import (
    Storage,
    StorageError,
    StorageHandle,
    StorageNotFoundError,
    StorageStat,
    StorageTransientError,
)

__all__ = [
    "CacheCodec",
    "CacheMetadata",
    "CodecError",
    "CodecUnsanitizableError",
    "Correspondence",
    "DecodedFrame",
    "EventEmitter",
    "FuseDegenerateClusterError",
    "FuseError",
    "FuseNonFiniteInputError",
    "Fuser",
    "ParticleFrame",
    "SplatFrame",
    "Storage",
    "StorageError",
    "StorageHandle",
    "StorageNotFoundError",
    "StorageStat",
    "StorageTransientError",
]
```

- [ ] **Step 4: Run tests, confirm pass**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/protocols/test_fuse_protocol.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add server/gsfluent/protocols/fuse.py \
        server/gsfluent/protocols/__init__.py \
        server/tests/protocols/test_fuse_protocol.py
git commit -m "phase-1: protocols/fuse.py — Fuser Protocol + Correspondence + FuseError hierarchy"
```

---

### Task 7: protocols/sim.py — SimulationEngine Protocol

**Files:**
- Create: `server/gsfluent/protocols/sim.py`
- Modify: `server/gsfluent/protocols/__init__.py`
- Create: `server/tests/protocols/test_sim_protocol.py`

- [ ] **Step 1: Write the failing test**

Create `server/tests/protocols/test_sim_protocol.py`:

```python
"""Conformance tests for the SimulationEngine Protocol."""
from pathlib import Path

import pytest

from gsfluent.protocols.observability import EventEmitter
from gsfluent.protocols.sim import (
    GPUUnavailableError,
    ModelRef,
    SimError,
    SimEnvMissingError,
    SimInterpreterMissingError,
    SimResult,
    SimulationEngine,
    SimWallTimeExceededError,
    ValidatedRecipe,
)


class _StubEmitter:
    def emit(self, event: str, **context) -> None: pass
    def child(self, **context) -> "_StubEmitter": return self


class _StubSimEngine:
    """Stub SimEngine that does nothing real."""

    async def preflight(self) -> None:
        return None

    async def run(
        self,
        recipe: ValidatedRecipe,
        model: ModelRef,
        output_dir: Path,
        wall_time_sec: int,
        on_event: EventEmitter,
    ) -> SimResult:
        return SimResult(frames_dir=output_dir / "frames", n_frames=0, duration_sec=0.0)


def test_stub_satisfies_sim_protocol() -> None:
    eng: SimulationEngine = _StubSimEngine()
    assert isinstance(eng, SimulationEngine)


@pytest.mark.asyncio
async def test_stub_preflight_returns_none() -> None:
    eng = _StubSimEngine()
    assert (await eng.preflight()) is None


@pytest.mark.asyncio
async def test_stub_run_returns_sim_result() -> None:
    eng = _StubSimEngine()
    result = await eng.run(
        recipe={"any": "shape"},
        model=ModelRef(name="test", path=Path("/tmp/model")),
        output_dir=Path("/tmp/out"),
        wall_time_sec=60,
        on_event=_StubEmitter(),
    )
    assert isinstance(result, SimResult)
    assert result.n_frames == 0


def test_sim_error_hierarchy() -> None:
    assert issubclass(SimEnvMissingError, SimError)
    assert issubclass(SimInterpreterMissingError, SimError)
    assert issubclass(GPUUnavailableError, SimError)
    assert issubclass(SimWallTimeExceededError, SimError)
```

- [ ] **Step 2: Run test, confirm fail**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/protocols/test_sim_protocol.py -v
```

Expected: import error.

- [ ] **Step 3: Implement the Protocol**

Create `server/gsfluent/protocols/sim.py`:

```python
"""SimulationEngine Protocol — layer 2.

Runs the MPM (or other physics) sim to produce per-frame particle state.
Concrete: MPMSimulationEngine (Phase 3, absorbs run_sim.sh logic) and
MockSimulationEngine (test fixture). Cancellable via SIGTERM to PG.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from gsfluent.protocols.observability import EventEmitter


class SimError(Exception):
    """Base for simulation-layer errors."""


class SimEnvMissingError(SimError):
    """$GSFLUENT_SIM_HOME unset or directory missing."""


class SimInterpreterMissingError(SimError):
    """$GSFLUENT_SIM_PYTHON unset or not on PATH."""


class GPUUnavailableError(SimError):
    """nvidia-smi reports no CUDA-capable device, or GPU is otherwise unreachable."""


class SimWallTimeExceededError(SimError):
    """Sim ran past wall_time_sec; killed by orchestrator timeout."""


class SimGpuOomError(SimError):
    """Sim allocated more GPU memory than available."""


class SimUnstableRecipeError(SimError):
    """Numerical instability detected via stderr classifier."""


class SimCrashedError(SimError):
    """Non-zero exit, classifier did not match a known pattern."""


# ValidatedRecipe: a recipe dict that has already been Pydantic-validated
# and cap-checked at the API boundary. Concrete impls treat it as
# trusted-shape; runtime values still need defensive handling.
ValidatedRecipe = dict[str, Any]


@dataclass(frozen=True)
class ModelRef:
    """Identifier + filesystem location of a 3DGS model."""
    name: str
    path: Path


@dataclass(frozen=True)
class SimResult:
    """Returned by SimulationEngine.run() on success."""
    frames_dir: Path        # directory containing sim_*.ply files
    n_frames: int
    duration_sec: float


@runtime_checkable
class SimulationEngine(Protocol):
    """Run a physics sim from a validated recipe to per-frame particle state."""

    async def preflight(self) -> None:
        """Raise typed error if environment cannot run a sim.
        SimEnvMissingError / SimInterpreterMissingError / GPUUnavailableError."""
        ...

    async def run(
        self,
        recipe: ValidatedRecipe,
        model: ModelRef,
        output_dir: Path,
        wall_time_sec: int,
        on_event: EventEmitter,
    ) -> SimResult:
        """Run sim to completion or raise typed SimError.

        Must be cancellable via cooperative cancellation (asyncio.CancelledError
        on outer task) OR external SIGTERM to the process group.

        Emits events through on_event at sim lifecycle transitions
        (sim.started, sim.completed). Caller (RunManager) translates
        these to run.* events with run_id attached via .child().
        """
        ...
```

Update `server/gsfluent/protocols/__init__.py` to add sim imports — append to existing imports + `__all__`:

```python
# (keep existing imports above this comment)
from gsfluent.protocols.sim import (
    GPUUnavailableError,
    ModelRef,
    SimCrashedError,
    SimError,
    SimEnvMissingError,
    SimGpuOomError,
    SimInterpreterMissingError,
    SimResult,
    SimulationEngine,
    SimUnstableRecipeError,
    SimWallTimeExceededError,
    ValidatedRecipe,
)
```

And append to `__all__` list:

```python
    # ... existing entries ...
    "GPUUnavailableError",
    "ModelRef",
    "SimCrashedError",
    "SimError",
    "SimEnvMissingError",
    "SimGpuOomError",
    "SimInterpreterMissingError",
    "SimResult",
    "SimUnstableRecipeError",
    "SimWallTimeExceededError",
    "SimulationEngine",
    "ValidatedRecipe",
```

- [ ] **Step 4: Run tests, confirm pass**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/protocols/test_sim_protocol.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add server/gsfluent/protocols/sim.py \
        server/gsfluent/protocols/__init__.py \
        server/tests/protocols/test_sim_protocol.py
git commit -m "phase-1: protocols/sim.py — SimulationEngine Protocol + SimError hierarchy + ModelRef/SimResult/ValidatedRecipe types"
```

---

### Task 8: protocols/runs.py — RunManager Protocol

**Files:**
- Create: `server/gsfluent/protocols/runs.py`
- Modify: `server/gsfluent/protocols/__init__.py`
- Create: `server/tests/protocols/test_runs_protocol.py`

- [ ] **Step 1: Write the failing test**

Create `server/tests/protocols/test_runs_protocol.py`:

```python
"""Conformance tests for the RunManager Protocol."""
from typing import AsyncIterator

import pytest

from gsfluent.protocols.runs import (
    CapExceededError,
    RecoveryReport,
    RunEvent,
    RunId,
    RunManager,
    RunState,
    RunStatus,
    ValidationError,
)
from gsfluent.protocols.sim import ModelRef, ValidatedRecipe


class _StubRunManager:
    def __init__(self) -> None:
        self._runs: dict[RunId, RunStatus] = {}

    async def submit(self, recipe: ValidatedRecipe, *, model: ModelRef) -> RunId:
        rid = RunId(f"run-{len(self._runs)}")
        self._runs[rid] = RunStatus(id=rid, state=RunState.QUEUED)
        return rid

    async def cancel(self, run_id: RunId) -> None:
        if run_id in self._runs:
            self._runs[run_id] = RunStatus(id=run_id, state=RunState.CANCELLED)

    async def status(self, run_id: RunId) -> RunStatus:
        return self._runs[run_id]

    async def stream_events(self, run_id: RunId) -> AsyncIterator[RunEvent]:
        async def _gen():
            yield RunEvent(event="run.queued", context={"run_id": run_id})
        return _gen()

    async def recover_on_boot(self) -> RecoveryReport:
        return RecoveryReport(reattached=0, interrupted=0, terminal_already=0)


def test_stub_satisfies_run_manager_protocol() -> None:
    rm: RunManager = _StubRunManager()
    assert isinstance(rm, RunManager)


@pytest.mark.asyncio
async def test_submit_returns_run_id() -> None:
    rm = _StubRunManager()
    rid = await rm.submit({}, model=ModelRef(name="t", path=__import__("pathlib").Path("/")))
    assert isinstance(rid, RunId)


@pytest.mark.asyncio
async def test_cancel_transitions_state() -> None:
    from pathlib import Path
    rm = _StubRunManager()
    rid = await rm.submit({}, model=ModelRef(name="t", path=Path("/")))
    await rm.cancel(rid)
    status = await rm.status(rid)
    assert status.state == RunState.CANCELLED


def test_state_enum_has_required_members() -> None:
    expected = {"QUEUED", "STARTED", "RUNNING", "COMPLETED", "FAILED",
                "CANCELLING", "CANCELLED", "INTERRUPTED"}
    actual = {m.name for m in RunState}
    assert expected <= actual


def test_validation_and_cap_errors() -> None:
    with pytest.raises(ValidationError):
        raise ValidationError("bad recipe")
    with pytest.raises(CapExceededError):
        raise CapExceededError("too many particles")
```

- [ ] **Step 2: Run test, confirm fail**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/protocols/test_runs_protocol.py -v
```

Expected: import error.

- [ ] **Step 3: Implement the Protocol**

Create `server/gsfluent/protocols/runs.py`:

```python
"""RunManager Protocol — layer 1.

Lifecycle controller. Submits runs, cancels them, exposes status, streams
events, recovers in-flight runs on boot. Concrete: AsyncioRunManager
(Phase 2, replaces server/gsfluent/core/runner.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator, NewType, Protocol, runtime_checkable

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
```

Update `server/gsfluent/protocols/__init__.py` — add runs imports:

```python
# (above existing imports)
from gsfluent.protocols.runs import (
    CapExceededError,
    RecoveryReport,
    RunEvent,
    RunId,
    RunManager,
    RunState,
    RunStatus,
    TERMINAL_RUN_STATES,
    ValidationError,
)
```

Append to `__all__`:

```python
    "CapExceededError",
    "RecoveryReport",
    "RunEvent",
    "RunId",
    "RunManager",
    "RunState",
    "RunStatus",
    "TERMINAL_RUN_STATES",
    "ValidationError",
```

- [ ] **Step 4: Run tests, confirm pass**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/protocols/test_runs_protocol.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add server/gsfluent/protocols/runs.py \
        server/gsfluent/protocols/__init__.py \
        server/tests/protocols/test_runs_protocol.py
git commit -m "phase-1: protocols/runs.py — RunManager Protocol + RunState enum + RunStatus/RunEvent/RecoveryReport types"
```

---

### Task 9: core/state.py — run state JSON persistence

**Files:**
- Create: `server/gsfluent/core/state.py`
- Create: `server/tests/core/test_state.py`

- [ ] **Step 1: Write the failing test**

Create `server/tests/core/test_state.py`:

```python
"""Tests for run state JSON persistence + boot scanner."""
import json
import os
import time
from pathlib import Path

import pytest

from gsfluent.core.state import (
    RunStateRecord,
    RunStateStore,
    is_pid_alive_with_starttime,
)
from gsfluent.protocols.runs import RunState


@pytest.fixture
def store(tmp_path: Path) -> RunStateStore:
    return RunStateStore(state_dir=tmp_path)


def test_create_then_read_round_trips(store: RunStateStore) -> None:
    rec = RunStateRecord(
        id="run-abc",
        state=RunState.QUEUED,
        recipe_hash="sha256:deadbeef",
        sequence_name="demo_seq",
    )
    store.write(rec)
    loaded = store.read("run-abc")
    assert loaded is not None
    assert loaded.id == "run-abc"
    assert loaded.state == RunState.QUEUED
    assert loaded.recipe_hash == "sha256:deadbeef"
    assert loaded.sequence_name == "demo_seq"


def test_read_missing_returns_none(store: RunStateStore) -> None:
    assert store.read("does-not-exist") is None


def test_update_state_writes_through(store: RunStateStore) -> None:
    rec = RunStateRecord(id="run-abc", state=RunState.QUEUED)
    store.write(rec)
    rec2 = rec.transition(state=RunState.STARTED, pid=12345, pgid=12345)
    store.write(rec2)
    loaded = store.read("run-abc")
    assert loaded.state == RunState.STARTED
    assert loaded.pid == 12345
    assert loaded.pgid == 12345


def test_atomic_write_via_temp_rename(store: RunStateStore, tmp_path: Path) -> None:
    """Write should not leave a partial file readable mid-flight."""
    rec = RunStateRecord(id="run-x", state=RunState.QUEUED)
    store.write(rec)
    # The on-disk file should be valid JSON the moment it appears.
    target = tmp_path / "run-x.json"
    body = json.loads(target.read_text())
    assert body["id"] == "run-x"


def test_scan_returns_all_records(store: RunStateStore) -> None:
    for i in range(3):
        store.write(RunStateRecord(id=f"r{i}", state=RunState.QUEUED))
    records = list(store.scan())
    assert {r.id for r in records} == {"r0", "r1", "r2"}


def test_scan_skips_non_json_files(store: RunStateStore, tmp_path: Path) -> None:
    store.write(RunStateRecord(id="r0", state=RunState.QUEUED))
    (tmp_path / "README.txt").write_text("not a run")
    records = list(store.scan())
    assert [r.id for r in records] == ["r0"]


def test_terminal_record_is_recognized() -> None:
    completed = RunStateRecord(id="r", state=RunState.COMPLETED)
    queued = RunStateRecord(id="r", state=RunState.QUEUED)
    assert completed.is_terminal()
    assert not queued.is_terminal()


def test_is_pid_alive_with_starttime_handles_dead_pid() -> None:
    # PID 1 typically exists (init) but the starttime won't match a fake value.
    # Use a definitely-impossible PID to confirm dead path.
    assert is_pid_alive_with_starttime(pid=2**31 - 1, expected_starttime=1.0) is False


def test_is_pid_alive_with_starttime_handles_own_process() -> None:
    """Our own PID is alive and starttime is readable."""
    pid = os.getpid()
    # Read our actual starttime from /proc, then pass it back
    with open(f"/proc/{pid}/stat") as f:
        fields = f.read().rsplit(")", 1)[-1].split()
    # /proc/PID/stat field 22 (0-indexed 21 after the comm field): starttime
    actual_starttime = float(fields[19])  # offset because comm was stripped
    assert is_pid_alive_with_starttime(pid=pid, expected_starttime=actual_starttime) is True
```

- [ ] **Step 2: Run test, confirm fail**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/core/test_state.py -v
```

Expected: `ModuleNotFoundError: No module named 'gsfluent.core.state'`.

- [ ] **Step 3: Implement the state module**

Create `server/gsfluent/core/state.py`:

```python
"""Run state persistence — one JSON file per run under work/_state/runs/.

Atomic writes via temp-file + rename. is_pid_alive_with_starttime() defends
against PID reuse during boot recovery by cross-checking /proc/<pid>/stat
field 22 (process start time).
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Iterator

from gsfluent.protocols.runs import TERMINAL_RUN_STATES, RunState


@dataclass(frozen=True)
class RunStateRecord:
    """Persisted snapshot of a run. Lives at work/_state/runs/<id>.json."""

    id: str
    state: RunState
    recipe_hash: str | None = None
    sequence_name: str | None = None
    pid: int | None = None
    pgid: int | None = None
    pid_starttime: float | None = None  # /proc/<pid>/stat field 22
    submitted_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    error: dict | None = None
    paths: dict[str, str] = field(default_factory=dict)

    def transition(self, **changes) -> "RunStateRecord":
        return replace(self, **changes)

    def is_terminal(self) -> bool:
        return self.state in TERMINAL_RUN_STATES

    def to_json(self) -> str:
        d = asdict(self)
        d["state"] = self.state.value  # serialize enum as string
        return json.dumps(d, separators=(",", ":"))

    @classmethod
    def from_json(cls, raw: str) -> "RunStateRecord":
        d = json.loads(raw)
        d["state"] = RunState(d["state"])
        return cls(**d)


class RunStateStore:
    """Filesystem-backed store for RunStateRecord. One JSON file per record."""

    def __init__(self, state_dir: Path) -> None:
        self._dir = state_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, run_id: str) -> Path:
        # Defensive: reject path-traversal attempts. Run IDs should be opaque
        # tokens (ULIDs); anything with a '/' or '..' is suspicious.
        if "/" in run_id or ".." in run_id or run_id.startswith("."):
            raise ValueError(f"unsafe run_id: {run_id!r}")
        return self._dir / f"{run_id}.json"

    def write(self, record: RunStateRecord) -> None:
        """Atomic write: temp file + rename."""
        target = self._path(record.id)
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(record.to_json())
        tmp.replace(target)

    def read(self, run_id: str) -> RunStateRecord | None:
        try:
            raw = self._path(run_id).read_text()
        except FileNotFoundError:
            return None
        return RunStateRecord.from_json(raw)

    def scan(self) -> Iterator[RunStateRecord]:
        """Yield all records in the store. Skips non-JSON files silently."""
        for path in sorted(self._dir.iterdir()):
            if path.suffix != ".json":
                continue
            try:
                yield RunStateRecord.from_json(path.read_text())
            except (json.JSONDecodeError, KeyError, ValueError):
                # Corrupt file. Skip and let the operator find it via
                # observability later. Phase 4 (recover_on_boot) will
                # log this as a structured warning event.
                continue


def is_pid_alive_with_starttime(pid: int, expected_starttime: float) -> bool:
    """True iff PID is alive AND its /proc starttime matches expected.

    Defends against PID reuse during the boot-recovery window. A bare
    os.kill(pid, 0) check is insufficient: kernels reuse PIDs eagerly,
    and a fast restart can race a new process taking the old PID.

    Reads /proc/<pid>/stat field 22 (1-indexed) per `man 5 proc`. The
    `comm` field (parenthesized) is stripped before splitting because
    it may contain spaces or right-parens.

    Returns False on any read failure (process gone, permission denied,
    Linux-only /proc unavailable).
    """
    try:
        with open(f"/proc/{pid}/stat", "r") as f:
            raw = f.read()
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return False

    try:
        # After the last ')' is the rest of the fields, space-separated.
        # Original layout: pid (comm) state ppid pgrp ... starttime ...
        # After stripping comm, field 22 (1-indexed) becomes 20 (0-indexed).
        rest = raw.rsplit(")", 1)[-1].split()
        actual_starttime = float(rest[19])
    except (IndexError, ValueError):
        return False

    # /proc starttime is in clock ticks; expected_starttime is what we
    # persisted at spawn time. Treat exact match as the contract — we
    # always read from the same /proc on the same kernel, so the units
    # agree.
    return actual_starttime == expected_starttime
```

- [ ] **Step 4: Run tests, confirm pass**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/core/test_state.py -v
```

Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add server/gsfluent/core/state.py server/tests/core/test_state.py
git commit -m "phase-1: core/state.py — RunStateStore (atomic JSON) + RunStateRecord + PID-reuse-safe liveness check"
```

---

### Task 10: core/limits.py — recipe cap-checker

**Files:**
- Create: `server/gsfluent/core/limits.py`
- Create: `server/tests/core/test_limits.py`

- [ ] **Step 1: Write the failing test**

Create `server/tests/core/test_limits.py`:

```python
"""Tests for recipe cap-checker."""
import pytest

from gsfluent.core.limits import (
    CapConfig,
    check_recipe_caps,
)
from gsfluent.protocols.runs import CapExceededError


def test_default_caps_accept_modest_recipe() -> None:
    cfg = CapConfig()
    recipe = {"particle_count": 200_000, "wall_time_sec": 600}
    # Should not raise.
    check_recipe_caps(recipe, cfg)


def test_particle_count_cap_rejects_too_many() -> None:
    cfg = CapConfig(max_particle_count=500_000)
    recipe = {"particle_count": 800_000, "wall_time_sec": 600}
    with pytest.raises(CapExceededError) as ei:
        check_recipe_caps(recipe, cfg)
    msg = str(ei.value)
    assert "particle" in msg.lower()
    assert "800000" in msg
    assert "500000" in msg


def test_wall_time_cap_rejects_too_long() -> None:
    cfg = CapConfig(max_wall_time_sec=3600)
    recipe = {"particle_count": 100_000, "wall_time_sec": 7200}
    with pytest.raises(CapExceededError) as ei:
        check_recipe_caps(recipe, cfg)
    assert "wall" in str(ei.value).lower()


def test_recipe_size_cap_rejects_huge() -> None:
    cfg = CapConfig(max_recipe_bytes=1024)
    recipe = {"particle_count": 100, "wall_time_sec": 60, "noise": "x" * 5000}
    with pytest.raises(CapExceededError) as ei:
        check_recipe_caps(recipe, cfg)
    assert "size" in str(ei.value).lower() or "bytes" in str(ei.value).lower()


def test_recipe_without_particle_count_uses_default_zero() -> None:
    """Recipes missing fields should not crash the checker."""
    cfg = CapConfig()
    # No particle_count field — treat as 0, which is under any cap.
    check_recipe_caps({"wall_time_sec": 60}, cfg)


def test_recipe_without_wall_time_uses_cap_as_default() -> None:
    """Missing wall_time_sec means 'use the backend max'."""
    cfg = CapConfig(max_wall_time_sec=3600)
    # Should not raise; treated as 3600.
    check_recipe_caps({"particle_count": 100}, cfg)


def test_cap_config_from_env_uses_defaults_when_unset(monkeypatch) -> None:
    for k in ("GSFLUENT_MAX_PARTICLE_COUNT", "GSFLUENT_MAX_WALL_TIME_SEC",
              "GSFLUENT_MAX_RECIPE_BYTES"):
        monkeypatch.delenv(k, raising=False)
    cfg = CapConfig.from_env()
    assert cfg.max_particle_count > 0
    assert cfg.max_wall_time_sec > 0
    assert cfg.max_recipe_bytes > 0


def test_cap_config_from_env_reads_overrides(monkeypatch) -> None:
    monkeypatch.setenv("GSFLUENT_MAX_PARTICLE_COUNT", "1000000")
    monkeypatch.setenv("GSFLUENT_MAX_WALL_TIME_SEC", "7200")
    monkeypatch.setenv("GSFLUENT_MAX_RECIPE_BYTES", "65536")
    cfg = CapConfig.from_env()
    assert cfg.max_particle_count == 1_000_000
    assert cfg.max_wall_time_sec == 7200
    assert cfg.max_recipe_bytes == 65536
```

- [ ] **Step 2: Run test, confirm fail**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/core/test_limits.py -v
```

Expected: import error for `gsfluent.core.limits`.

- [ ] **Step 3: Implement the limits module**

Create `server/gsfluent/core/limits.py`:

```python
"""Recipe cap-checker. Validates a recipe dict against configured caps.

Configuration lives in CapConfig, loadable from env vars (defaults documented
on the dataclass fields). The check function raises CapExceededError on
violation — the API layer translates to HTTP 422.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

from gsfluent.protocols.runs import CapExceededError


DEFAULT_MAX_PARTICLE_COUNT = 500_000
DEFAULT_MAX_WALL_TIME_SEC = 3600  # 1 hour
DEFAULT_MAX_RECIPE_BYTES = 16 * 1024  # 16 KiB


@dataclass(frozen=True)
class CapConfig:
    """Caps applied to incoming recipes.

    All caps are upper bounds — recipe requests <= these are accepted as-is.
    The wall-time cap also doubles as the orchestrator's enforcement bound
    (sim that exceeds gets PG-killed).
    """

    max_particle_count: int = DEFAULT_MAX_PARTICLE_COUNT
    max_wall_time_sec: int = DEFAULT_MAX_WALL_TIME_SEC
    max_recipe_bytes: int = DEFAULT_MAX_RECIPE_BYTES

    @classmethod
    def from_env(cls) -> "CapConfig":
        return cls(
            max_particle_count=int(
                os.environ.get("GSFLUENT_MAX_PARTICLE_COUNT", DEFAULT_MAX_PARTICLE_COUNT)
            ),
            max_wall_time_sec=int(
                os.environ.get("GSFLUENT_MAX_WALL_TIME_SEC", DEFAULT_MAX_WALL_TIME_SEC)
            ),
            max_recipe_bytes=int(
                os.environ.get("GSFLUENT_MAX_RECIPE_BYTES", DEFAULT_MAX_RECIPE_BYTES)
            ),
        )


def check_recipe_caps(recipe: dict, cfg: CapConfig) -> None:
    """Validate recipe against caps. Raises CapExceededError on first violation."""
    particle_count = int(recipe.get("particle_count", 0))
    if particle_count > cfg.max_particle_count:
        raise CapExceededError(
            f"Particle count {particle_count} exceeds limit {cfg.max_particle_count} "
            f"(set GSFLUENT_MAX_PARTICLE_COUNT to raise)"
        )

    # Missing wall_time_sec means "use the backend max", not "unbounded".
    wall_time_sec = int(recipe.get("wall_time_sec", cfg.max_wall_time_sec))
    if wall_time_sec > cfg.max_wall_time_sec:
        raise CapExceededError(
            f"Wall-time hint {wall_time_sec}s exceeds backend max {cfg.max_wall_time_sec}s "
            f"(set GSFLUENT_MAX_WALL_TIME_SEC to raise)"
        )

    recipe_bytes = len(json.dumps(recipe).encode("utf-8"))
    if recipe_bytes > cfg.max_recipe_bytes:
        raise CapExceededError(
            f"Recipe size {recipe_bytes} bytes exceeds limit {cfg.max_recipe_bytes} bytes "
            f"(set GSFLUENT_MAX_RECIPE_BYTES to raise)"
        )
```

- [ ] **Step 4: Run tests, confirm pass**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/core/test_limits.py -v
```

Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add server/gsfluent/core/limits.py server/tests/core/test_limits.py
git commit -m "phase-1: core/limits.py — CapConfig + check_recipe_caps (particle/wall-time/size)"
```

---

### Task 11: config.py — AppConfig dataclass + from_env()

**Files:**
- Create: `server/gsfluent/config.py`
- Create: `server/tests/test_config.py`

- [ ] **Step 1: Write the failing test**

Create `server/tests/test_config.py`:

```python
"""Tests for AppConfig — single source of truth for backend config."""
from pathlib import Path

import pytest

from gsfluent.config import AppConfig


def test_from_env_with_required_vars_set(monkeypatch, tmp_path: Path) -> None:
    sim_home = tmp_path / "sim_home"
    sim_home.mkdir()
    monkeypatch.setenv("GSFLUENT_SIM_HOME", str(sim_home))
    monkeypatch.setenv("GSFLUENT_SIM_PYTHON", "/usr/bin/python3")
    monkeypatch.setenv("GSFLUENT_WORK_DIR", str(tmp_path / "work"))

    cfg = AppConfig.from_env()
    assert cfg.sim_home == sim_home
    assert cfg.sim_python == "/usr/bin/python3"
    assert cfg.work_dir == tmp_path / "work"
    assert cfg.sim_env is None  # optional


def test_from_env_with_optional_conda_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GSFLUENT_SIM_HOME", str(tmp_path))
    monkeypatch.setenv("GSFLUENT_SIM_PYTHON", "python")
    monkeypatch.setenv("GSFLUENT_SIM_ENV", "physics")
    cfg = AppConfig.from_env()
    assert cfg.sim_env == "physics"


def test_work_dir_defaults_when_unset(monkeypatch) -> None:
    monkeypatch.setenv("GSFLUENT_SIM_HOME", "/tmp")
    monkeypatch.setenv("GSFLUENT_SIM_PYTHON", "python")
    monkeypatch.delenv("GSFLUENT_WORK_DIR", raising=False)
    cfg = AppConfig.from_env()
    # Default points at the repo's work/ directory (PKG_ROOT/work).
    assert cfg.work_dir.name == "work"


def test_cap_config_is_loaded(monkeypatch) -> None:
    monkeypatch.setenv("GSFLUENT_SIM_HOME", "/tmp")
    monkeypatch.setenv("GSFLUENT_SIM_PYTHON", "python")
    monkeypatch.setenv("GSFLUENT_MAX_PARTICLE_COUNT", "750000")
    cfg = AppConfig.from_env()
    assert cfg.caps.max_particle_count == 750_000


def test_app_config_is_immutable(monkeypatch) -> None:
    monkeypatch.setenv("GSFLUENT_SIM_HOME", "/tmp")
    monkeypatch.setenv("GSFLUENT_SIM_PYTHON", "python")
    cfg = AppConfig.from_env()
    with pytest.raises((AttributeError, TypeError)):
        cfg.sim_python = "different"  # type: ignore[misc]
```

- [ ] **Step 2: Run test, confirm fail**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/test_config.py -v
```

Expected: import error.

- [ ] **Step 3: Implement config module**

Create `server/gsfluent/config.py`:

```python
"""AppConfig — single source of truth for backend configuration.

All env-var reads happen here. Subsystems receive a frozen AppConfig
instance (or a sub-dataclass like CapConfig) by constructor injection;
they never read os.environ directly.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from gsfluent._paths import PKG_ROOT
from gsfluent.core.limits import CapConfig


@dataclass(frozen=True)
class AppConfig:
    """Frozen backend configuration. Construct via AppConfig.from_env()."""

    # Sim wiring
    sim_home: Path
    sim_python: str
    sim_env: str | None  # optional conda env name; None = trust calling env

    # Filesystem layout
    work_dir: Path

    # Caps
    caps: CapConfig

    @classmethod
    def from_env(cls) -> "AppConfig":
        sim_home_str = os.environ.get("GSFLUENT_SIM_HOME", "")
        sim_python = os.environ.get("GSFLUENT_SIM_PYTHON", "python")
        sim_env = os.environ.get("GSFLUENT_SIM_ENV") or None
        work_dir_str = os.environ.get("GSFLUENT_WORK_DIR", str(PKG_ROOT / "work"))

        return cls(
            sim_home=Path(sim_home_str),
            sim_python=sim_python,
            sim_env=sim_env,
            work_dir=Path(work_dir_str),
            caps=CapConfig.from_env(),
        )
```

- [ ] **Step 4: Run tests, confirm pass**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/test_config.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add server/gsfluent/config.py server/tests/test_config.py
git commit -m "phase-1: config.py — AppConfig (sim_home, sim_python, work_dir, caps) + from_env loader"
```

---

### Task 12: composition.py — wiring root skeleton

**Files:**
- Create: `server/gsfluent/composition.py`
- Modify: `server/gsfluent/server.py:create_app`
- Create: `server/tests/test_composition.py`

- [ ] **Step 1: Write the failing test**

Create `server/tests/test_composition.py`:

```python
"""Tests for the composition root."""
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gsfluent.composition import build_app
from gsfluent.config import AppConfig
from gsfluent.core.limits import CapConfig


@pytest.fixture
def cfg(tmp_path: Path) -> AppConfig:
    return AppConfig(
        sim_home=tmp_path / "sim_home",
        sim_python="python",
        sim_env=None,
        work_dir=tmp_path / "work",
        caps=CapConfig(),
    )


def test_build_app_returns_fastapi_instance(cfg: AppConfig) -> None:
    app = build_app(cfg)
    assert isinstance(app, FastAPI)


def test_built_app_responds_to_health(cfg: AppConfig) -> None:
    app = build_app(cfg)
    client = TestClient(app)
    resp = client.get("/api/health")
    assert resp.status_code == 200


def test_built_app_creates_work_dirs(cfg: AppConfig) -> None:
    """Composition root should ensure work_dir + _state/runs exists on startup."""
    build_app(cfg)
    assert (cfg.work_dir / "_state" / "runs").is_dir()


def test_create_app_delegates_to_build_app(monkeypatch, tmp_path: Path) -> None:
    """server.create_app() should call composition.build_app(AppConfig.from_env())."""
    monkeypatch.setenv("GSFLUENT_SIM_HOME", str(tmp_path))
    monkeypatch.setenv("GSFLUENT_SIM_PYTHON", "python")
    monkeypatch.setenv("GSFLUENT_WORK_DIR", str(tmp_path / "work"))

    from gsfluent.server import create_app
    app = create_app()
    assert isinstance(app, FastAPI)
    # Sanity check: the same routes the original app exposed are still there.
    client = TestClient(app)
    assert client.get("/api/health").status_code == 200
```

- [ ] **Step 2: Run test, confirm fail**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/test_composition.py -v
```

Expected: import error or test fails because `build_app` doesn't exist.

- [ ] **Step 3: Implement composition root**

Create `server/gsfluent/composition.py`:

```python
"""Composition root — single place where concrete impls get wired into the app.

Phase 1 is a skeleton: it imports the existing FastAPI app factory and
the AppConfig + EventEmitter we just built, and ensures work directories
exist. Phase 2 will replace the stub wiring with real concrete impls
(FilesystemStorage, GSQCodec, KNNKabschFuser, AsyncioRunManager).
"""
from __future__ import annotations

import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from gsfluent.config import AppConfig
from gsfluent.observability.jsonlog import StdlibJSONEmitter
from gsfluent.protocols.observability import EventEmitter


def _ensure_work_dirs(cfg: AppConfig) -> None:
    """Create the on-disk directory layout the backend expects."""
    (cfg.work_dir / "_state" / "runs").mkdir(parents=True, exist_ok=True)
    (cfg.work_dir / "library" / "sequences").mkdir(parents=True, exist_ok=True)
    (cfg.work_dir / "cache" / "viser").mkdir(parents=True, exist_ok=True)
    (cfg.work_dir / "uploads").mkdir(parents=True, exist_ok=True)


def build_app(cfg: AppConfig) -> FastAPI:
    """Construct the FastAPI app with all concrete dependencies wired.

    Phase 1: skeleton wiring — EventEmitter is real, other deps are stubs
    until Phase 2 lands their concrete impls. The app still serves the
    existing routes from api/ as before.
    """
    _ensure_work_dirs(cfg)

    obs: EventEmitter = StdlibJSONEmitter(stream=sys.stdout)
    obs.emit("backend.boot", work_dir=str(cfg.work_dir), sim_home=str(cfg.sim_home))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Phase 4 will plug RunManager.recover_on_boot() in here.
        obs.emit("backend.lifespan.startup")
        yield
        obs.emit("backend.lifespan.shutdown")

    app = FastAPI(title="gsfluent", version="0.1.0", lifespan=lifespan)

    # CORS — match the existing policy.
    import os
    extra = [s.strip() for s in os.environ.get("GSFLUENT_EXTRA_CORS_ORIGINS", "").split(",") if s.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
        allow_origins=extra,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount existing routers (unchanged in Phase 1; Phase 3+ will rewire
    # them through Depends() against the new Protocols).
    from gsfluent.api import recipes, models, runs, sequences, stream
    app.include_router(recipes.router, prefix="/api/recipes", tags=["recipes"])
    app.include_router(models.router, prefix="/api/models", tags=["models"])
    app.include_router(runs.router, prefix="/api/runs", tags=["runs"])
    app.include_router(sequences.router, prefix="/api/sequences", tags=["sequences"])
    app.include_router(stream.router, prefix="/api", tags=["stream"])

    # Health route — preserves the existing /api/health contract.
    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok"}

    return app
```

Now modify `server/gsfluent/server.py` to delegate to `build_app`. Open the file and replace the `create_app()` function and its associated lifespan:

```python
import os
import platform
import shutil
import socket
import subprocess
import sys
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path

from ._paths import PKG_ROOT  # re-exported; legacy `from ..server import PKG_ROOT` still works


def create_app() -> FastAPI:
    """Backward-compatible entry point. Delegates to composition.build_app().

    Existing callers (tests, ASGI servers) keep working unchanged.
    """
    from gsfluent.composition import build_app
    from gsfluent.config import AppConfig
    return build_app(AppConfig.from_env())
```

Make the actual edit to `server/gsfluent/server.py`:

Read current line numbers first to scope the edit; then replace the existing `lifespan` async function + `create_app` function with the simpler delegating version above.

- [ ] **Step 4: Run tests, confirm pass**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/test_composition.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Run the existing test suite to confirm no regression**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/ -v --tb=short 2>&1 | tail -30
```

Expected: same pass/fail count as the baseline recorded in Task 1, plus all the new Phase 1 tests passing.

- [ ] **Step 6: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add server/gsfluent/composition.py \
        server/gsfluent/server.py \
        server/tests/test_composition.py
git commit -m "phase-1: composition.py — build_app(AppConfig) skeleton; server.create_app() delegates"
```

---

### Task 13: Phase 1 verification + branch handoff

**Files:**
- No file edits in this task.

- [ ] **Step 1: Run full test suite end-to-end**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/ -v 2>&1 | tail -50
```

Expected: every test passes. Phase 1 added approximately 45 new tests across:
- `tests/protocols/` — 6 test files, ~25 tests
- `tests/observability/test_jsonlog.py` — 8 tests
- `tests/core/test_state.py` — 9 tests
- `tests/core/test_limits.py` — 8 tests
- `tests/test_config.py` — 5 tests
- `tests/test_composition.py` — 4 tests

Plus all baseline tests still pass.

- [ ] **Step 2: Confirm no production behavior changed**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
.venv/bin/python -c "
from gsfluent.server import create_app
import os
os.environ.setdefault('GSFLUENT_SIM_HOME', '/tmp')
os.environ.setdefault('GSFLUENT_SIM_PYTHON', 'python')
app = create_app()
print('routes:', sorted([r.path for r in app.routes if hasattr(r, 'path')]))
"
```

Expected: routes include `/api/health`, `/api/recipes`, `/api/models`, `/api/runs/*`, `/api/sequences/*`, `/api/stream`. Same as before Phase 1.

- [ ] **Step 3: Confirm Phase 1 git history is clean**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git log --oneline main..HEAD
```

Expected: roughly 11 commits, each prefixed `phase-1:`, one per task that added code.

- [ ] **Step 4: Push the branch (do NOT merge yet)**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git push -u origin phase-1-foundations
```

Expected: branch published on origin. Open a PR titled `phase-1: foundations — protocols + observability + state + limits + config + composition`.

- [ ] **Step 5: Update the spec file's status note (optional)**

Edit `docs/superpowers/specs/2026-05-22-backend-bulletproofing-vertical-slice-design.md`, change `**Status:**` line to add `Phase 1 implemented in branch phase-1-foundations (PR #N)`.

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add docs/superpowers/specs/2026-05-22-backend-bulletproofing-vertical-slice-design.md
git commit -m "docs: mark Phase 1 implemented in branch phase-1-foundations"
git push
```

---

## Definition of Done — Phase 1

Phase 1 ships when ALL of:

- [ ] All 13 tasks above completed
- [ ] All new tests pass (`pytest tests/protocols tests/observability tests/core/test_state.py tests/core/test_limits.py tests/test_config.py tests/test_composition.py -v`)
- [ ] All baseline tests still pass (no regressions; same count as Task 1 baseline)
- [ ] `gsfluent.composition.build_app(cfg)` returns a working FastAPI app
- [ ] `gsfluent.server.create_app()` continues to work (back-compat for ASGI launchers)
- [ ] Branch `phase-1-foundations` pushed; PR open for review
- [ ] Six Protocols importable from `gsfluent.protocols`
- [ ] `StdlibJSONEmitter` writes one JSON line per event to stdout

## Handoff to Phase 2

Phase 2 (`extract impls`) depends on:
- All six Protocols (✓ Phase 1)
- `EventEmitter` (✓ Phase 1)
- `RunStateStore` (✓ Phase 1)
- `composition.build_app` skeleton (✓ Phase 1)

Phase 2 will:
- Move `tools/pack_splats.py` → `core/codecs/gsq.py` (GSQCodec impl conforming to CacheCodec Protocol)
- Move `tools/fuse_to_full_ply.py` → `core/fusers/knn_kabsch.py` (KNNKabschFuser impl)
- Extract `core/library.py`'s filesystem operations → `storage/filesystem.py` (FilesystemStorage impl)
- Refactor `core/runner.py` → `core/run_manager.py` (AsyncioRunManager impl)
- Update `composition.build_app` to wire the concrete impls in

Phase 2 plan will be authored in a follow-up document: `docs/superpowers/plans/2026-05-22-phase-2-extract-impls.md`.

---

**End of Phase 1 plan.**
