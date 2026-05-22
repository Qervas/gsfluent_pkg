# Phase 6 — Observability Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the structured-observability story. (1) Walk `core/run_manager.py` and `core/sim_engines/mpm.py` and convert every remaining `print()` / `logging.info()` / `logging.warning()` call into a structured event through the per-run `EventEmitter`. (2) Replace the trivial `{"status":"ok"}` health endpoint with a contract-stable Pydantic-modeled response carrying real signals (GPU reachability, sim home presence, disk free percent, last successful run timestamp, active run count) plus a top-level `status` discriminator. (3) Update the systemd unit's watchdog heartbeat to gate `WATCHDOG=1` on health status.

**Architecture:** Pure additions + targeted edits. The audit phase is line-by-line: every call site is named, the before/after diff is shown, and the structured event name comes from the spec's taxonomy (`run.*`, `error.sim.*`, `error.fuse.*`, `error.codec.*`, `error.storage.*`, `error.internal`). The health endpoint becomes `api/health.py` (extracted from `server.py`'s inline definition), backed by a `HealthResponse` Pydantic model so the SPA + watchdog can rely on a stable shape. The watchdog heartbeat (introduced by Phase 4) is touched in one place — it now reads `/api/health`, parses the JSON, and only sends `WATCHDOG=1` when `status != "down"`.

**Tech Stack:** Python 3.10+, `pydantic>=2.6`, stdlib `subprocess` + `shutil`, `pytest>=8`, `pytest-asyncio>=0.23`. No new dependencies.

**Spec reference:** `docs/superpowers/specs/2026-05-22-backend-bulletproofing-vertical-slice-design.md` — Section 3 Flow C (boot recovery + watchdog gating), Section 4 (error taxonomy + propagation invariant), Phase 6 in the migration plan.

**Phase 6 is plan 6 of 7.** Depends on Phase 1's `EventEmitter` + `StdlibJSONEmitter`, Phase 2's `AsyncioRunManager` skeleton, Phase 3's `MPMSimulationEngine`, Phase 4's `recover_on_boot` + systemd unit + sd_notify heartbeat, and Phase 5's streaming cache events. Phase 7 (definition-of-done sweep) follows.

---

## File Structure

### Files audited + edited in Phase 6

```
server/gsfluent/core/run_manager.py        ← every print() / _log.* converted to obs.emit(...)
server/gsfluent/core/sim_engines/mpm.py    ← every print() / _log.* converted to obs.emit(...)
deploy/gsfluent-backend.service            ← watchdog probe script gated on health status
deploy/scripts/watchdog_heartbeat.sh       ← reads /api/health, exits non-zero on "down"
server/gsfluent/server.py                  ← removes inline /api/health, imports api.health
```

### New files in Phase 6

```
server/gsfluent/api/
└── health.py                              ← HealthResponse model + real health signals

server/tests/api/
├── __init__.py                            ← (if not already created in Phase 3)
└── test_health.py                         ← exhaustive health-endpoint tests

server/tests/observability/
└── test_event_taxonomy.py                 ← asserts the full run.* lifecycle emits via StdlibJSONEmitter
                                              into an in-memory stream (replaces the manual-journalctl test)
```

### Files NOT modified in Phase 6

```
server/gsfluent/protocols/observability.py     ← stable from Phase 1
server/gsfluent/observability/jsonlog.py        ← stable from Phase 1
server/gsfluent/core/state.py                   ← stable from Phase 1 (scan() consumed by health)
server/gsfluent/core/fusers/knn_kabsch.py       ← Phase 2 already emits via EventEmitter
server/gsfluent/core/codecs/gsq.py              ← Phase 2 already emits via EventEmitter
server/gsfluent/storage/filesystem.py           ← Phase 2 already emits via EventEmitter
server/gsfluent/api/runs.py                     ← Phase 3 already strict + 422 envelope + events
server/gsfluent/api/sequences.py                ← Phase 5 already emits cell.cache.* events on client side
```

---

## Audit Targets — concrete call-site inventory

Phase 6 is a finishing pass, not a rewrite. The two target files contain **named** call sites that get converted in Tasks 2 and 3. Recording them up front so the plan can be followed mechanically.

### `server/gsfluent/core/run_manager.py` (Phase 2 output, ≈400 lines)

The Phase 2 plan refactored `runner.py` → `run_manager.py` while preserving the call sites listed below (Phase 2 left the stdlib `_log` calls in place because Phase 6 owns the conversion). Each row is one replacement Task 2 performs.

| # | Original (today's `runner.py`) | Original line | Event name (Phase 6) |
|---|---|---|---|
| A1 | `_log.warning("recipe has unknown sim_area_frame=%r …")` | 114 | `recipe.unknown_sim_area_frame` |
| A2 | `_log.info("translated sim_area model-local %s -> world %s")` | 129 | `recipe.sim_area_translated` |
| A3 | `_log.warning("failed to read model bbox for %s: %s")` | 167 | `recipe.model_bbox_read_failed` |
| A4 | `_log.error("background task failed: %s", exc, exc_info=exc)` | 227 | `error.internal` |
| A5 | `_log.exception("failed to spawn sim wrapper for run %s")` | 305 | `error.sim.spawn_failed` |
| A6 | `_log.exception("drain loop crashed for run %s")` | 348 | `error.internal` (sub-context: `phase="drain"`) |
| A7 | `_log.exception("drain wrapper crashed for run %s")` | 352 | `error.internal` (sub-context: `phase="drain_wrapper"`) |
| A8 | `_log.warning("post-run .npz rebuild failed for %s")` | 386 | `error.codec.rebuild_failed` |
| A9 | `_log.warning("post-run _meta.json write failed for %s")` | 398 | `error.storage.meta_write_failed` |
| A10 | `_log.warning("post-run cleanup failed for %s")` | 406 | `error.storage.cleanup_failed` |
| A11 | `_log.info(msg.strip())` (cleanup summary) | 443 | `run.cleanup` (context: `bytes_freed`) |
| A12 | `_log.warning("run %s ignored SIGTERM after %.1fs; sending SIGKILL")` | 567 | `run.cancel.escalated` |

In addition, Task 2 inserts the canonical lifecycle events that Phase 2 stubbed out (Phase 1 defined them in the spec; Phase 2 reserved the call sites; Phase 6 fills them in):

| # | Where in `_run_to_completion` (Phase 2 path) | Event name |
|---|---|---|
| L1 | After `state.write(record.transition(state=QUEUED))` | `run.queued` |
| L2 | After successful `sim_engine.preflight()` | `run.preflight_ok` |
| L3 | After `state→started`, before `sim_engine.run(...)` | `run.started` |
| L4 | After `sim_engine.run(...)` returns | `run.simmed` |
| L5 | After the per-frame fuse loop completes | `run.fused` |
| L6 | After `codec.encode(...) + storage.put(...)` | `run.packed` |
| L7 | On terminal success | `run.completed` |
| L8 | On caught `SimError` / `FuseError` / `CodecError` / `StorageError` | `run.failed` (plus the specific `error.<layer>.<sub>`) |
| L9 | At the top of `cancel(run_id)` after PG-SIGTERM | `run.cancelling` |
| L10 | At the end of `_escalate_kill` after process is dead | `run.cancelled` |

### `server/gsfluent/core/sim_engines/mpm.py` (Phase 3 output, ≈250 lines)

The Phase 3 plan absorbs the bash logic from `tools/run_sim.sh` into Python and emits `sim.*` events. Phase 6 verifies the conversions match the taxonomy. The audit table below is taken from the bash `echo` lines in `run_sim.sh` (today's source) that Phase 3 carries forward.

| # | Original `echo` in `run_sim.sh` | Line | Event name (Phase 6) |
|---|---|---|---|
| B1 | `echo "unknown option: $1" >&2; exit 2` | 38 | `error.sim.bad_arg` |
| B2 | `echo "extra positional: $1" >&2` | 43 | `error.sim.bad_arg` |
| B3 | `echo "ERROR: missing required arg: $v" >&2` | 51 | `error.sim.bad_arg` |
| B4 | `echo "ERROR: sim interpreter not on PATH …" >&2` | 106 | (mapped to `SimInterpreterMissingError` in `preflight()` — emitted by `RunManager` as `error.sim.preflight`; no event from sim engine) |
| B5 | `echo "WARN: GSFLUENT_SIM_ENV=… set but conda not on PATH" >&2` | 120 | `sim.preflight.conda_missing` (warn level, non-fatal) |
| B6 | `echo "ERROR: no reference ply under …" >&2` | 134 | `error.sim.reference_ply_missing` |
| B7 | `echo "=== step 1: MPM simulation ==="` | 158 | `sim.started` (context: `phase="mpm"`) |
| B8 | `echo "=== step 2: fuse to frame_*.ply ==="` | 180 | (no event — fuse is no longer the sim engine's job after Phase 2; this line vanishes when Phase 3 slims `run_sim.sh`) |
| B9 | `echo "=== run_sim.sh done: $OUTPUT ==="` | 195 | `sim.completed` (context: `frames_dir`, `n_frames`) |
| B10 | `echo "  frames at: $FUSED_DIR"` | 196 | (merged into B9 context) |
| B11 | `echo "  runner.py will now build the .gsq cache."` | 197 | (deleted — comment, not an event) |

Phase 3 will also have added the following events; Task 3 verifies they exist and use the canonical names from the taxonomy:

| # | Event name | Where in `MPMSimulationEngine` |
|---|---|---|
| M1 | `sim.preflight.checking_env` | top of `preflight()` |
| M2 | `sim.spawning` | just before the subprocess spawn call |
| M3 | `sim.frame_emitted` | per-frame stderr classifier hit on `[sim] frame N done` |
| M4 | `error.sim.wall_time_exceeded` | inside `wait_for(proc.wait(), timeout=...)` `TimeoutError` branch |
| M5 | `error.sim.gpu_oom` | stderr classifier match on `CUDA out of memory` |
| M6 | `error.sim.unstable_recipe` | stderr classifier match on `CFL` or `illegal memory access` |
| M7 | `error.sim.crashed` | unclassified non-zero exit |

---

## Tasks

### Task 1: Branch + baseline verification

**Files:**
- No file edits in this task. Verification + commit only.

- [ ] **Step 1: Create the phase branch**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git checkout -b phase-6-observability
```

Expected: `Switched to a new branch 'phase-6-observability'`

- [ ] **Step 2: Confirm prerequisites from Phases 1-5 are in tree**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
for f in \
  server/gsfluent/observability/jsonlog.py \
  server/gsfluent/protocols/observability.py \
  server/gsfluent/core/state.py \
  server/gsfluent/core/run_manager.py \
  server/gsfluent/core/sim_engines/mpm.py \
  server/gsfluent/config.py \
  server/gsfluent/composition.py \
  deploy/gsfluent-backend.service; do
  test -f "$f" && echo "OK  $f" || echo "MISSING  $f"
done
```

Expected: every line prefixed `OK`. If anything is `MISSING`, halt — the dependent phase has not landed yet and Phase 6 cannot proceed.

- [ ] **Step 3: Run the existing test suite and record baseline**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/ -v --tb=short 2>&1 | tail -40
```

Expected: all tests pass. Record the pass count in the task notes — Phase 6 will add roughly 22 new tests and must not regress the baseline.

- [ ] **Step 4: No commit yet — Task 1 is verification only**

---

### Task 2: Convert `core/run_manager.py` print/log calls to structured events

**Files:**
- Modify: `server/gsfluent/core/run_manager.py`
- Create: `server/tests/observability/test_event_taxonomy.py`

This task implements the conversions in audit table A1-A12 (existing log calls) plus L1-L10 (lifecycle events). Each conversion is a literal Edit.

- [ ] **Step 1: Write the failing taxonomy test first**

The test asserts that a happy-path run emits the full lifecycle event sequence through the `EventEmitter`. We use the `StdlibJSONEmitter` writing to an in-memory `io.StringIO` so the test runs without journald.

Create `server/tests/observability/test_event_taxonomy.py`:

```python
"""End-to-end event taxonomy test.

Drives a happy-path run through AsyncioRunManager with a MockSimulationEngine
(Phase 3) + in-memory storage stub, captures every emitted event into an
io.StringIO via StdlibJSONEmitter, and asserts the full lifecycle event
sequence shows up.

This replaces what would otherwise be a manual `journalctl -o json | jq`
verification — CI has no journald. The manual verification step still
lives in the Definition of Done so the operator confirms the
journald-routing also works in production.
"""
from __future__ import annotations

import io
import json
from pathlib import Path
from typing import AsyncIterator

import pytest

from gsfluent.core.run_manager import AsyncioRunManager
from gsfluent.core.state import RunStateStore
from gsfluent.observability.jsonlog import StdlibJSONEmitter
from gsfluent.protocols.cache import CacheMetadata, DecodedFrame, SplatFrame
from gsfluent.protocols.observability import EventEmitter
from gsfluent.protocols.runs import RunState
from gsfluent.protocols.sim import ModelRef, SimResult


# --- in-memory test doubles ---

class _MockSim:
    async def preflight(self) -> None:
        return None

    async def run(self, recipe, model, output_dir, wall_time_sec, on_event):
        on_event.emit("sim.started", phase="mpm")
        on_event.emit("sim.completed", n_frames=2)
        return SimResult(frames_dir=output_dir, n_frames=2, duration_sec=0.05)


class _MockFuser:
    def build_correspondence(self, reference_ply_path, first_frame_particles):
        from gsfluent.protocols.fuse import Correspondence
        return Correspondence(reference_ply_path=reference_ply_path,
                              indices=(0,), extent=1.0)

    def fuse_frame(self, correspondence, particle_frame):
        return {"xyz": [(0.0, 0.0, 0.0)]}


class _MockCodec:
    media_type = "application/x-gsq"
    file_extension = ".gsq"

    def encode(self, frames, out, on_event):
        n = 0
        for _ in frames:
            n += 1
            out.write(b"\x00")
        return CacheMetadata(n_splats=1, n_frames=n, bbox=(0, 0, 0, 0, 0, 0))

    async def decode_streaming(self, src):  # pragma: no cover (not used here)
        async def _g():
            if False:
                yield None
        return _g()

    def decode_all(self, src):  # pragma: no cover
        return []


class _InMemoryStorage:
    def __init__(self):
        self._data: dict[str, bytes] = {}

    async def put(self, key, src, metadata):
        from gsfluent.protocols.storage import StorageHandle
        body = src.read()
        self._data[key] = body
        return StorageHandle(key=key, size=len(body), etag=f'"{len(body)}-0"')

    async def get(self, key):  # pragma: no cover
        async def _g():
            yield self._data[key]
        return _g()

    async def get_range(self, key, start, end):  # pragma: no cover
        async def _g():
            yield self._data[key][start:end]
        return _g()

    async def stat(self, key):  # pragma: no cover
        from gsfluent.protocols.storage import StorageStat
        if key not in self._data:
            return None
        return StorageStat(size=len(self._data[key]), mtime=0.0,
                           etag=f'"{len(self._data[key])}-0"')

    async def exists(self, key):
        return key in self._data


def _parse_events(stream: io.StringIO) -> list[dict]:
    stream.seek(0)
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


@pytest.fixture
def event_stream() -> io.StringIO:
    return io.StringIO()


@pytest.fixture
def obs(event_stream: io.StringIO) -> EventEmitter:
    return StdlibJSONEmitter(stream=event_stream)


@pytest.fixture
def state_store(tmp_path: Path) -> RunStateStore:
    return RunStateStore(state_dir=tmp_path / "_state" / "runs")


@pytest.mark.asyncio
async def test_happy_path_emits_full_lifecycle(
    obs: EventEmitter,
    event_stream: io.StringIO,
    state_store: RunStateStore,
    tmp_path: Path,
) -> None:
    rm = AsyncioRunManager(
        sim_engine=_MockSim(),
        fuser=_MockFuser(),
        cache_codec=_MockCodec(),
        storage=_InMemoryStorage(),
        obs=obs,
        state_store=state_store,
        work_dir=tmp_path / "work",
        wall_time_cap_sec=60,
    )
    rid = await rm.submit({"particle_count": 100}, model=ModelRef(name="t", path=tmp_path))
    # Wait for the background run task to complete.
    await rm.wait_for(rid)

    events = _parse_events(event_stream)
    seen = [e["event"] for e in events]

    # Required lifecycle sequence (order-preserving subsequence check).
    required = [
        "run.queued",
        "run.preflight_ok",
        "run.started",
        "sim.started",
        "sim.completed",
        "run.simmed",
        "run.fused",
        "run.packed",
        "run.completed",
    ]
    idx = 0
    for ev in seen:
        if idx < len(required) and ev == required[idx]:
            idx += 1
    assert idx == len(required), (
        f"missing events in lifecycle. expected sub-sequence={required!r} "
        f"emitted={seen!r}"
    )


@pytest.mark.asyncio
async def test_every_event_carries_run_id_and_sequence_name(
    obs: EventEmitter,
    event_stream: io.StringIO,
    state_store: RunStateStore,
    tmp_path: Path,
) -> None:
    rm = AsyncioRunManager(
        sim_engine=_MockSim(),
        fuser=_MockFuser(),
        cache_codec=_MockCodec(),
        storage=_InMemoryStorage(),
        obs=obs,
        state_store=state_store,
        work_dir=tmp_path / "work",
        wall_time_cap_sec=60,
    )
    rid = await rm.submit({"particle_count": 100}, model=ModelRef(name="seq-x", path=tmp_path))
    await rm.wait_for(rid)

    events = _parse_events(event_stream)
    # Every run.* / sim.* event in this run must have run_id attached
    # via obs.child(run_id=..., sequence_name=...).
    for e in events:
        if e["event"].startswith(("run.", "sim.", "error.", "cell.")):
            assert e.get("run_id") == rid, f"missing run_id on event {e!r}"


@pytest.mark.asyncio
async def test_sim_error_emits_error_sim_event_and_run_failed(
    event_stream: io.StringIO,
    state_store: RunStateStore,
    tmp_path: Path,
) -> None:
    from gsfluent.protocols.sim import SimGpuOomError

    class _OomSim:
        async def preflight(self) -> None: return None
        async def run(self, *a, **kw):
            raise SimGpuOomError("CUDA out of memory at frame 3")

    obs = StdlibJSONEmitter(stream=event_stream)
    rm = AsyncioRunManager(
        sim_engine=_OomSim(),
        fuser=_MockFuser(),
        cache_codec=_MockCodec(),
        storage=_InMemoryStorage(),
        obs=obs,
        state_store=state_store,
        work_dir=tmp_path / "work",
        wall_time_cap_sec=60,
    )
    rid = await rm.submit({"particle_count": 100}, model=ModelRef(name="t", path=tmp_path))
    await rm.wait_for(rid)

    events = _parse_events(event_stream)
    kinds = [e["event"] for e in events]
    assert "error.sim.gpu_oom" in kinds
    assert "run.failed" in kinds
    # Spec invariant: every error has exactly one structured event at its boundary.
    assert kinds.count("error.sim.gpu_oom") == 1


@pytest.mark.asyncio
async def test_cancel_emits_cancelling_and_cancelled(
    obs: EventEmitter,
    event_stream: io.StringIO,
    state_store: RunStateStore,
    tmp_path: Path,
) -> None:
    import asyncio

    class _SlowSim:
        async def preflight(self) -> None: return None
        async def run(self, recipe, model, output_dir, wall_time_sec, on_event):
            await asyncio.sleep(5.0)  # long enough to cancel
            return SimResult(frames_dir=output_dir, n_frames=0, duration_sec=0.0)

    rm = AsyncioRunManager(
        sim_engine=_SlowSim(),
        fuser=_MockFuser(),
        cache_codec=_MockCodec(),
        storage=_InMemoryStorage(),
        obs=obs,
        state_store=state_store,
        work_dir=tmp_path / "work",
        wall_time_cap_sec=60,
    )
    rid = await rm.submit({"particle_count": 100}, model=ModelRef(name="t", path=tmp_path))
    await asyncio.sleep(0.05)  # let the task actually start
    await rm.cancel(rid)
    await rm.wait_for(rid)

    events = _parse_events(event_stream)
    kinds = [e["event"] for e in events]
    assert "run.cancelling" in kinds
    assert "run.cancelled" in kinds
```

- [ ] **Step 2: Run the test, confirm it fails**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/observability/test_event_taxonomy.py -v
```

Expected: tests fail because `run_manager.py` is still emitting via stdlib `_log` and is missing the explicit `obs.emit(...)` lifecycle calls. Typical failure: `assert idx == len(required)` with `idx` < 9, since `run.queued`/`run.preflight_ok`/etc. are not being emitted yet.

- [ ] **Step 3: Perform the audit conversions on `run_manager.py`**

Open `server/gsfluent/core/run_manager.py`. For each row in audit table A1-A12, locate the original `_log.*(...)` call and replace it with an `obs.emit(...)` (or `run_obs.emit(...)` if the call site already has a per-run emitter in scope — which it does inside `_run_to_completion` and `_drain`).

The Phase 2 plan instantiated `self._obs` on the `AsyncioRunManager` and built a per-run emitter at the top of `_run_to_completion`:

```python
run_obs = self._obs.child(run_id=run_id, sequence_name=run_name)
```

Each conversion below assumes `run_obs` is in scope inside `_run_to_completion` and its helpers; for module-level helpers like `_translate_sim_area_if_local` that have no run context, the call site receives an `EventEmitter` parameter (Phase 2 added it; Phase 6 fills in the body).

Apply these literal edits:

**A1.** Replace
```python
_log.warning(
    "recipe has unknown sim_area_frame=%r (expected 'model'|'world'); "
    "treating as world", frame,
)
```
with
```python
on_event.emit(
    "recipe.unknown_sim_area_frame",
    frame=frame,
    treated_as="world",
    level="WARNING",
)
```

**A2.** Replace
```python
_log.info(
    "translated sim_area model-local %s -> world %s (model center %s)",
    sim_area, out["sim_area"], center,
)
```
with
```python
on_event.emit(
    "recipe.sim_area_translated",
    sim_area_local=list(sim_area),
    sim_area_world=list(out["sim_area"]),
    model_center=list(center),
)
```

**A3.** Replace
```python
_log.warning("failed to read model bbox for %s: %s", model_dir, e)
```
with
```python
on_event.emit(
    "recipe.model_bbox_read_failed",
    model_dir=str(model_dir),
    error=str(e),
    level="WARNING",
)
```

**A4.** Replace
```python
_log.error("background task failed: %s", exc, exc_info=exc)
```
with
```python
self._obs.emit(
    "error.internal",
    where="background_task",
    error_type=type(exc).__name__,
    message=str(exc),
)
```

Note: `_log_task_exception` is a module-level callback registered via `add_done_callback`, so it must reach the emitter via the captured `AsyncioRunManager` instance. The Phase 2 plan refactored it into a method on the run manager — `self._on_background_task_done(task)`.

**A5.** Replace
```python
_log.exception("failed to spawn sim wrapper for run %s", run_name)
```
with
```python
run_obs.emit(
    "error.sim.spawn_failed",
    error=str(e),
)
```
(The surrounding `except` already binds `e`; the spawn path is reached only inside `_run_to_completion`.)

**A6.** Replace
```python
_log.exception("drain loop crashed for run %s", run.name)
```
with
```python
run_obs.emit(
    "error.internal",
    where="drain_loop",
    error=str(e),
)
```

**A7.** Replace
```python
_log.exception("drain wrapper crashed for run %s", run.name)
```
with
```python
run_obs.emit(
    "error.internal",
    where="drain_wrapper",
    error=str(e),
)
```

**A8.** Replace
```python
_log.warning("post-run .npz rebuild failed for %s: %s", run.name, e)
```
with
```python
run_obs.emit(
    "error.codec.rebuild_failed",
    error=str(e),
    level="WARNING",
)
```

**A9.** Replace
```python
_log.warning("post-run _meta.json write failed for %s: %s", run.name, e)
```
with
```python
run_obs.emit(
    "error.storage.meta_write_failed",
    error=str(e),
    level="WARNING",
)
```

**A10.** Replace
```python
_log.warning("post-run cleanup failed for %s: %s", run.name, e)
```
with
```python
run_obs.emit(
    "error.storage.cleanup_failed",
    error=str(e),
    level="WARNING",
)
```

**A11.** Replace
```python
_log.info(msg.strip())
```
(inside `_cleanup_intermediates`)
with
```python
run_obs.emit(
    "run.cleanup",
    bytes_freed=bytes_freed,
    gb_freed=round(bytes_freed / (1024 ** 3), 3),
)
```
(Drop the human-formatted `msg` string; the event is the canonical record.)

**A12.** Replace
```python
_log.warning(
    "run %s ignored SIGTERM after %.1fs; sending SIGKILL",
    run.name,
    grace_sec,
)
```
with
```python
run_obs.emit(
    "run.cancel.escalated",
    grace_sec=grace_sec,
)
```

- [ ] **Step 4: Insert the L1-L10 lifecycle events**

For each row in the L1-L10 table, insert a single `run_obs.emit(...)` call at the documented location inside `_run_to_completion` / `cancel` / `_escalate_kill`. Concrete insertions:

**L1.** Inside `submit()`, immediately after the initial state write:
```python
run_obs.emit(
    "run.queued",
    recipe_hash=recipe_hash,
    particle_count=int(recipe.get("particle_count", 0)),
    wall_time_cap=self._wall_time_cap_sec,
)
```

**L2.** Inside `_run_to_completion()`, after `await self._sim_engine.preflight()` returns without raising:
```python
run_obs.emit("run.preflight_ok")
```

**L3.** Inside `_run_to_completion()`, after the state transition to `STARTED` and just before invoking the sim:
```python
run_obs.emit("run.started", started_at=time.time())
```

**L4.** Inside `_run_to_completion()`, after `sim_result = await self._sim_engine.run(...)`:
```python
run_obs.emit(
    "run.simmed",
    n_frames=sim_result.n_frames,
    duration_sec=sim_result.duration_sec,
)
```

**L5.** Inside `_run_to_completion()`, after the per-frame fuse loop completes:
```python
run_obs.emit(
    "run.fused",
    n_frames=len(splat_frames),
    duration_sec=fuse_duration_sec,
)
```

**L6.** Inside `_run_to_completion()`, after `await self._storage.put(...)` returns:
```python
run_obs.emit(
    "run.packed",
    bytes_written=storage_handle.size,
    etag=storage_handle.etag,
    duration_sec=pack_duration_sec,
)
```

**L7.** Inside `_run_to_completion()`, after the state transition to `COMPLETED`:
```python
run_obs.emit(
    "run.completed",
    durations={
        "sim_sec": sim_result.duration_sec,
        "fuse_sec": fuse_duration_sec,
        "pack_sec": pack_duration_sec,
    },
)
```

**L8.** Inside the unified `except` block at the bottom of `_run_to_completion()`. The error-specific event is already emitted inside the matching `except SimError` / `except FuseError` / etc. branches; the `run.failed` is the lifecycle marker:
```python
run_obs.emit(
    "run.failed",
    error_kind=error_kind,    # e.g. "sim.gpu_oom"
    message=str(exc),
)
```

**L9.** At the top of `cancel(run_id)`, after `os.killpg(record.pgid, signal.SIGTERM)`:
```python
run_obs.emit("run.cancelling")
```

**L10.** At the end of `_escalate_kill(run_id, pgid, grace)`, after the process is confirmed dead:
```python
run_obs.emit("run.cancelled")
```

- [ ] **Step 5: Remove the unused `_log` import + module attribute**

At the top of `server/gsfluent/core/run_manager.py`, remove:
```python
import logging
_log = logging.getLogger(__name__)
```

If any non-event diagnostic logging is still wanted (for example, for the very-first-startup case before an emitter is constructed), keep `import logging` and `_log = logging.getLogger(__name__)` but **only** for that boot-time path. The audit must end with zero `_log.*` calls in lifecycle code.

Verify:
```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
grep -n "_log\.\|logging\." server/gsfluent/core/run_manager.py
```
Expected: no output (or only matches inside boot-time paths that explicitly cannot use the emitter — document each remaining match in a comment if you keep one).

Verify there are no bare `print(` calls:
```bash
grep -n "print(" server/gsfluent/core/run_manager.py
```
Expected: no output.

- [ ] **Step 6: Re-run the taxonomy test, confirm pass**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/observability/test_event_taxonomy.py -v
```

Expected: 4 passed.

- [ ] **Step 7: Re-run the full existing test suite to confirm no regression**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/ -v --tb=short 2>&1 | tail -30
```

Expected: every test that passed in Task 1 still passes; the 4 new tests above also pass.

- [ ] **Step 8: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add server/gsfluent/core/run_manager.py \
        server/tests/observability/test_event_taxonomy.py
git commit -m "phase-6: run_manager event conversion — 12 _log calls + 10 lifecycle emits routed through EventEmitter"
```

---

### Task 3: Convert `core/sim_engines/mpm.py` print/log calls to structured events

**Files:**
- Modify: `server/gsfluent/core/sim_engines/mpm.py`

This task verifies and completes the conversions in audit table B1-B11 and M1-M7. Phase 3 emitted most of the M1-M7 events when absorbing `run_sim.sh`; Phase 6 fills in any gaps and renames where the original `echo`-style strings leaked through.

- [ ] **Step 1: Audit the file for residual print/log calls**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
grep -n "print(\|_log\.\|logging\.\|sys\.stdout\.write\|sys\.stderr\.write" \
  server/gsfluent/core/sim_engines/mpm.py
```

Record each match. For each one, decide:
- If it is a lifecycle/error event: convert per the B-table or M-table.
- If it is a debug aid not in the taxonomy: convert to a `sim.debug` event with a `where` discriminator.
- If it is truly internal-and-noisy (such as raw stderr passthrough): pipe it through `on_event.emit("sim.stderr_line", line=...)` instead so the operator can grep for it.

- [ ] **Step 2: Apply the conversions**

For each `print(...)` / `_log.*(...)` match, replace with the canonical event. Examples — your tree may have slightly different lines, but the pattern is uniform.

If Phase 3 left a `print(f"sim spawning: {cmd}")` for diagnostic purposes:
```python
print(f"sim spawning: {cmd}")
```
becomes:
```python
on_event.emit("sim.spawning", cmd=list(cmd))
```

If Phase 3 left a fallback `_log.error("sim exited with rc=%d", rc)` for an unclassified exit:
```python
_log.error("sim exited with rc=%d", rc)
```
becomes:
```python
on_event.emit(
    "error.sim.crashed",
    exit_code=rc,
    stderr_tail=stderr_tail[-2000:],  # last 2 KiB for triage
)
```

If Phase 3 emitted a frame-progress event with a different name, rename to `sim.frame_emitted` to match the taxonomy:
```python
on_event.emit("sim.frame_done", frame_index=idx)        # before
on_event.emit("sim.frame_emitted", frame_index=idx)     # after
```

- [ ] **Step 3: Verify the GPU OOM / wall-time / unstable / crashed classifier covers M4-M7**

Read the stderr-classifier function (Phase 3 will have named it `_classify_sim_failure(stderr_text: str) -> SimError`). Confirm it returns the right `SimError` subclass for each pattern:

| Pattern | Error subclass | Emitted as event (caller side) |
|---|---|---|
| `CUDA out of memory` | `SimGpuOomError` | `error.sim.gpu_oom` |
| `CFL` or `illegal memory access` | `SimUnstableRecipeError` | `error.sim.unstable_recipe` |
| (`wall_time_sec` exceeded — raised by `wait_for` timeout, not stderr) | `SimWallTimeExceededError` | `error.sim.wall_time_exceeded` |
| Anything else with non-zero exit | `SimCrashedError` | `error.sim.crashed` |

The classifier itself does not emit; it raises. The emission happens in `RunManager._run_to_completion`'s `except SimError` block (already wired in Task 2).

- [ ] **Step 4: Verify no bare print/log calls remain**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
grep -n "print(\|_log\.\|logging\." server/gsfluent/core/sim_engines/mpm.py
```

Expected: zero matches (or only matches behind comments documenting why a particular path cannot use the emitter, with a TODO).

- [ ] **Step 5: Run the existing sim-engine unit tests + the taxonomy test**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/sim_engines/ tests/observability/test_event_taxonomy.py -v
```

Expected: all sim-engine tests pass + all 4 taxonomy tests still pass.

- [ ] **Step 6: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add server/gsfluent/core/sim_engines/mpm.py
git commit -m "phase-6: mpm sim engine — print/_log calls converted to sim.* / error.sim.* events"
```

---

### Task 4: Build `api/health.py` with real signals + Pydantic contract

**Files:**
- Create: `server/gsfluent/api/health.py`
- Modify: `server/gsfluent/server.py`
- Modify: `server/gsfluent/composition.py`
- Modify: `server/tests/test_health.py` (replace the trivial assertion)
- Create: `server/tests/api/test_health.py`

The new endpoint replaces the inline `@app.get("/api/health")` lambda in `server.py` with a router-mounted handler backed by a Pydantic response model. The contract is locked down so the SPA + watchdog + monitoring can rely on a stable shape.

- [ ] **Step 1: Write the failing tests first**

If `server/tests/api/__init__.py` does not exist (Phase 3 may have created it), create it as an empty file.

Create `server/tests/api/test_health.py`:

```python
"""Tests for the real /api/health endpoint.

The endpoint reports five derived signals plus a top-level status discriminator.
Tests cover: (1) Pydantic contract shape, (2) status derivation matrix,
(3) graceful degradation when nvidia-smi is absent, (4) RunStateStore
integration for last_successful_run_at, (5) disk_free_pct math.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gsfluent.api.health import HealthResponse, HealthStatus, build_health_router
from gsfluent.config import AppConfig
from gsfluent.core.limits import CapConfig
from gsfluent.core.state import RunStateRecord, RunStateStore
from gsfluent.protocols.runs import RunState


@pytest.fixture
def cfg(tmp_path: Path) -> AppConfig:
    sim_home = tmp_path / "sim_home"
    sim_home.mkdir()
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    (work_dir / "_state" / "runs").mkdir(parents=True)
    return AppConfig(
        sim_home=sim_home,
        sim_python="python",
        sim_env=None,
        work_dir=work_dir,
        caps=CapConfig(),
    )


@pytest.fixture
def state_store(cfg: AppConfig) -> RunStateStore:
    return RunStateStore(state_dir=cfg.work_dir / "_state" / "runs")


@pytest.fixture
def app(cfg: AppConfig, state_store: RunStateStore) -> FastAPI:
    app = FastAPI()
    app.include_router(build_health_router(cfg=cfg, state_store=state_store))
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


def test_health_response_shape_is_locked(client: TestClient) -> None:
    """Contract test: every documented field is present and correctly typed."""
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    # Top-level keys (exact set — no extras, no omissions)
    assert set(body.keys()) == {
        "status",
        "gpu_reachable",
        "sim_home_exists",
        "disk_free_pct",
        "last_successful_run_at",
        "active_run_count",
        "ts",
    }
    # Type assertions
    assert body["status"] in ("ok", "degraded", "down")
    assert isinstance(body["gpu_reachable"], bool)
    assert isinstance(body["sim_home_exists"], bool)
    assert isinstance(body["disk_free_pct"], (int, float))
    assert body["last_successful_run_at"] is None or isinstance(body["last_successful_run_at"], (int, float))
    assert isinstance(body["active_run_count"], int)
    assert isinstance(body["ts"], (int, float))


def test_status_ok_when_everything_healthy(client: TestClient) -> None:
    """sim_home exists + nvidia-smi mocked as present + plenty of disk = ok."""
    with patch("gsfluent.api.health._gpu_reachable", return_value=True):
        with patch("gsfluent.api.health._disk_free_pct", return_value=87.5):
            r = client.get("/api/health")
            body = r.json()
            assert body["status"] == "ok"
            assert body["gpu_reachable"] is True
            assert body["disk_free_pct"] == 87.5


def test_status_down_when_sim_home_missing(client: TestClient, cfg: AppConfig) -> None:
    """sim_home directory removed -> status=down."""
    import shutil
    shutil.rmtree(cfg.sim_home)
    r = client.get("/api/health")
    body = r.json()
    assert body["status"] == "down"
    assert body["sim_home_exists"] is False


def test_status_down_when_disk_below_5_pct(client: TestClient) -> None:
    """disk_free_pct < 5 -> down (operator alert)."""
    with patch("gsfluent.api.health._disk_free_pct", return_value=2.0):
        r = client.get("/api/health")
        body = r.json()
        assert body["status"] == "down"
        assert body["disk_free_pct"] == 2.0


def test_status_degraded_when_gpu_unreachable(client: TestClient) -> None:
    """nvidia-smi exits non-zero or absent -> degraded."""
    with patch("gsfluent.api.health._gpu_reachable", return_value=False):
        with patch("gsfluent.api.health._disk_free_pct", return_value=50.0):
            r = client.get("/api/health")
            body = r.json()
            assert body["status"] == "degraded"
            assert body["gpu_reachable"] is False


def test_status_degraded_when_last_run_older_than_24h(
    client: TestClient, state_store: RunStateStore,
) -> None:
    """Last successful run > 24h ago -> degraded (sim pipeline may be wedged)."""
    old_finished = time.time() - (25 * 3600)
    state_store.write(RunStateRecord(
        id="old-completed",
        state=RunState.COMPLETED,
        finished_at=old_finished,
    ))
    with patch("gsfluent.api.health._gpu_reachable", return_value=True):
        with patch("gsfluent.api.health._disk_free_pct", return_value=50.0):
            r = client.get("/api/health")
            body = r.json()
            assert body["status"] == "degraded"
            assert body["last_successful_run_at"] == old_finished


def test_last_successful_run_picks_max_completed(
    client: TestClient, state_store: RunStateStore,
) -> None:
    """When multiple completions exist, report the most-recent one."""
    state_store.write(RunStateRecord(id="r1", state=RunState.COMPLETED, finished_at=1000.0))
    state_store.write(RunStateRecord(id="r2", state=RunState.COMPLETED, finished_at=2000.0))
    state_store.write(RunStateRecord(id="r3", state=RunState.FAILED,    finished_at=3000.0))
    r = client.get("/api/health")
    body = r.json()
    assert body["last_successful_run_at"] == 2000.0


def test_last_successful_run_null_when_none_completed(
    client: TestClient, state_store: RunStateStore,
) -> None:
    state_store.write(RunStateRecord(id="r1", state=RunState.QUEUED))
    r = client.get("/api/health")
    body = r.json()
    assert body["last_successful_run_at"] is None


def test_active_run_count_excludes_terminal_states(
    client: TestClient, state_store: RunStateStore,
) -> None:
    state_store.write(RunStateRecord(id="r1", state=RunState.RUNNING))
    state_store.write(RunStateRecord(id="r2", state=RunState.QUEUED))
    state_store.write(RunStateRecord(id="r3", state=RunState.COMPLETED))
    state_store.write(RunStateRecord(id="r4", state=RunState.FAILED))
    r = client.get("/api/health")
    body = r.json()
    assert body["active_run_count"] == 2  # r1 RUNNING + r2 QUEUED


def test_disk_free_pct_uses_work_dir(client: TestClient, cfg: AppConfig) -> None:
    """The disk_free_pct computation must measure cfg.work_dir's filesystem."""
    r = client.get("/api/health")
    body = r.json()
    # Real shutil.disk_usage — just assert it is in plausible bounds.
    assert 0.0 <= body["disk_free_pct"] <= 100.0


def test_gpu_reachable_false_when_nvidia_smi_absent() -> None:
    """Direct test of the helper: missing binary -> False, no exception."""
    from gsfluent.api.health import _gpu_reachable
    with patch("subprocess.run", side_effect=FileNotFoundError):
        assert _gpu_reachable() is False


def test_gpu_reachable_false_on_timeout() -> None:
    import subprocess
    from gsfluent.api.health import _gpu_reachable
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="nvidia-smi", timeout=2)):
        assert _gpu_reachable() is False


def test_health_response_pydantic_model_round_trip() -> None:
    """The HealthResponse model accepts and serializes the contract shape."""
    h = HealthResponse(
        status=HealthStatus.OK,
        gpu_reachable=True,
        sim_home_exists=True,
        disk_free_pct=42.0,
        last_successful_run_at=1700000000.0,
        active_run_count=3,
        ts=1700000123.45,
    )
    d = h.model_dump()
    assert d["status"] == "ok"
    h2 = HealthResponse(**d)
    assert h2 == h
```

Replace the existing trivial assertion in `server/tests/test_health.py`:

```python
"""Compatibility test: legacy /api/health callers continue to receive 200 + a 'status' key.

Detailed contract tests live in tests/api/test_health.py.
"""
def test_health_returns_200_with_status(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert "status" in body
    assert body["status"] in ("ok", "degraded", "down")
```

- [ ] **Step 2: Run tests, confirm fail**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/api/test_health.py tests/test_health.py -v
```

Expected: import error for `gsfluent.api.health` (the module does not exist yet).

- [ ] **Step 3: Implement `api/health.py`**

Create `server/gsfluent/api/health.py`:

```python
"""Health endpoint with real signals + locked-down Pydantic contract.

Replaces the trivial /api/health stub in server.py. The response shape is
contract-stable: SPA, systemd watchdog, and external monitoring all rely
on the keys + types defined by HealthResponse.

Status derivation matrix:
    down     := sim_home missing OR disk_free_pct < 5
    degraded := gpu_reachable False OR last_successful_run > 24h ago
    ok       := otherwise

The watchdog (deploy/scripts/watchdog_heartbeat.sh) reads this endpoint and
sends sd_notify('WATCHDOG=1') only when status != 'down'. See spec
Section 3 Flow C for the full boot + watchdog flow.
"""
from __future__ import annotations

import shutil
import subprocess
import time
from enum import Enum
from pathlib import Path
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from gsfluent.config import AppConfig
from gsfluent.core.state import RunStateStore
from gsfluent.protocols.runs import TERMINAL_RUN_STATES, RunState


# --- contract types ---

class HealthStatus(str, Enum):
    OK = "ok"
    DEGRADED = "degraded"
    DOWN = "down"


class HealthResponse(BaseModel):
    """Locked-down /api/health response shape.

    Any field rename or addition is a breaking change — bump a version
    contract and coordinate with the SPA + watchdog before shipping.
    """
    status: HealthStatus = Field(..., description="Top-level health discriminator")
    gpu_reachable: bool = Field(..., description="nvidia-smi -L succeeded with at least one device")
    sim_home_exists: bool = Field(..., description="cfg.sim_home is a directory")
    disk_free_pct: float = Field(..., ge=0.0, le=100.0, description="Free disk on work_dir's filesystem")
    last_successful_run_at: Optional[float] = Field(
        None, description="POSIX ts of most-recent COMPLETED run, or null if none"
    )
    active_run_count: int = Field(..., ge=0, description="Runs in non-terminal states")
    ts: float = Field(..., description="POSIX ts when this response was generated")

    model_config = {"extra": "forbid"}  # Tightens the contract.


# --- signal helpers (each one is independently mockable for tests) ---

_GPU_PROBE_TIMEOUT_SEC = 2.0
_STALE_RUN_THRESHOLD_SEC = 24 * 3600
_DISK_LOW_THRESHOLD_PCT = 5.0


def _gpu_reachable() -> bool:
    """True iff nvidia-smi -L exits 0 and reports at least one device.

    Returns False (not raises) on any failure: binary absent, timeout,
    permission denied, non-zero exit. Health endpoint must never crash
    just because the GPU is gone.
    """
    try:
        result = subprocess.run(
            ["nvidia-smi", "-L"],
            capture_output=True,
            text=True,
            timeout=_GPU_PROBE_TIMEOUT_SEC,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, PermissionError, OSError):
        return False
    if result.returncode != 0:
        return False
    # nvidia-smi -L emits one line per visible device: "GPU 0: NVIDIA A100 ..."
    return any(line.startswith("GPU") for line in result.stdout.splitlines())


def _disk_free_pct(work_dir: Path) -> float:
    """Free-space percent on work_dir's filesystem. 0..100."""
    try:
        usage = shutil.disk_usage(work_dir)
    except (FileNotFoundError, OSError):
        return 0.0
    if usage.total <= 0:
        return 0.0
    return round(usage.free / usage.total * 100.0, 2)


def _last_successful_run_at(state_store: RunStateStore) -> Optional[float]:
    """POSIX ts of the most-recently-COMPLETED run, or None."""
    best: Optional[float] = None
    for record in state_store.scan():
        if record.state == RunState.COMPLETED and record.finished_at is not None:
            if best is None or record.finished_at > best:
                best = record.finished_at
    return best


def _active_run_count(state_store: RunStateStore) -> int:
    """Number of records currently in non-terminal states."""
    return sum(1 for r in state_store.scan() if r.state not in TERMINAL_RUN_STATES)


def _derive_status(
    *,
    sim_home_exists: bool,
    disk_free_pct: float,
    gpu_reachable: bool,
    last_successful_run_at: Optional[float],
    now: float,
) -> HealthStatus:
    """Spec Section 3 Flow C status derivation."""
    if not sim_home_exists or disk_free_pct < _DISK_LOW_THRESHOLD_PCT:
        return HealthStatus.DOWN
    if not gpu_reachable:
        return HealthStatus.DEGRADED
    if last_successful_run_at is not None and (now - last_successful_run_at) > _STALE_RUN_THRESHOLD_SEC:
        return HealthStatus.DEGRADED
    return HealthStatus.OK


# --- router factory ---

def build_health_router(*, cfg: AppConfig, state_store: RunStateStore) -> APIRouter:
    """Build the /api/health router with its dependencies captured in closure.

    Construct once per app at composition time; the closure binds cfg +
    state_store so the handler does not need to re-read env vars on every
    request.
    """
    router = APIRouter()

    @router.get("/api/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        now = time.time()
        gpu = _gpu_reachable()
        sim_home_ok = cfg.sim_home.is_dir()
        free_pct = _disk_free_pct(cfg.work_dir)
        last_at = _last_successful_run_at(state_store)
        active = _active_run_count(state_store)
        status = _derive_status(
            sim_home_exists=sim_home_ok,
            disk_free_pct=free_pct,
            gpu_reachable=gpu,
            last_successful_run_at=last_at,
            now=now,
        )
        return HealthResponse(
            status=status,
            gpu_reachable=gpu,
            sim_home_exists=sim_home_ok,
            disk_free_pct=free_pct,
            last_successful_run_at=last_at,
            active_run_count=active,
            ts=now,
        )

    return router
```

- [ ] **Step 4: Wire the new router into `composition.py`**

In `server/gsfluent/composition.py`, replace the inline health route with the router-based one. Locate the block (created in Phase 1, possibly modified in Phase 4):

```python
    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok"}
```

Replace it with:

```python
    from gsfluent.api.health import build_health_router
    from gsfluent.core.state import RunStateStore

    health_state_store = RunStateStore(state_dir=cfg.work_dir / "_state" / "runs")
    app.include_router(build_health_router(cfg=cfg, state_store=health_state_store))
```

(If Phase 4 already created a `state_store` instance for `recover_on_boot`, reuse that instead of constructing a new one — share the singleton through whatever wiring Phase 4 chose.)

- [ ] **Step 5: Remove the inline health route from `server.py`**

In `server/gsfluent/server.py` (Phase 1 made it a thin wrapper that delegates to `composition.build_app`, but the original file still has the legacy `@app.get("/api/health")` block from before Phase 1 — if Phase 1's delegation already replaced it, this step is a no-op; otherwise delete the inline block).

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
grep -n "@app.get(\"/api/health\")" server/gsfluent/server.py
```

If anything appears: locate the corresponding `def health()` function (and the legacy `/api/gpu-check`, `/api/system`, `/` routes if Phase 1's delegation has not already removed them) and delete them. The router-based health endpoint is now the single source of truth.

If `server.py` already only contains the thin `create_app() -> build_app(AppConfig.from_env())` wrapper from Phase 1, no edits are needed here.

- [ ] **Step 6: Run the health tests, confirm pass**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/api/test_health.py tests/test_health.py -v
```

Expected: 13 passed (12 from `test_health.py` in `tests/api/` + 1 backward-compat from `tests/test_health.py`).

- [ ] **Step 7: Re-run the full test suite to confirm no regressions**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/ -v --tb=short 2>&1 | tail -30
```

Expected: every test passes; the legacy `test_health.py` shape change is the only intentional behavior delta.

- [ ] **Step 8: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add server/gsfluent/api/health.py \
        server/gsfluent/composition.py \
        server/gsfluent/server.py \
        server/tests/api/__init__.py \
        server/tests/api/test_health.py \
        server/tests/test_health.py
git commit -m "phase-6: api/health.py — real signals (GPU/sim_home/disk/last_run) + Pydantic HealthResponse contract"
```

---

### Task 5: Gate the systemd watchdog heartbeat on health status

**Files:**
- Modify: `deploy/scripts/watchdog_heartbeat.sh` (Phase 4 created the file)
- Modify: `deploy/gsfluent-backend.service`
- Create: `server/tests/deploy/__init__.py`
- Create: `server/tests/deploy/test_watchdog_script.py`

The Phase 4 plan introduced a watchdog heartbeat script that calls `sd_notify('WATCHDOG=1')` every 15s. Phase 6 makes that heartbeat conditional on `/api/health` returning `status != "down"`. Per spec Section 3 Flow C: when the health endpoint says `down`, the watchdog must NOT send `WATCHDOG=1`, which lets systemd detect the stall and restart the unit.

- [ ] **Step 1: Read what Phase 4 produced**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
cat deploy/scripts/watchdog_heartbeat.sh
cat deploy/gsfluent-backend.service
```

Note the current heartbeat invocation (likely an unconditional `systemd-notify --pid=$MAIN_PID WATCHDOG=1`) and the unit's `WatchdogSec=` value. Record both so the Phase 6 edit can preserve everything except the gating condition.

- [ ] **Step 2: Write a failing test for the script's exit code logic**

Create `server/tests/deploy/__init__.py` as an empty file.

Create `server/tests/deploy/test_watchdog_script.py`:

```python
"""Tests for deploy/scripts/watchdog_heartbeat.sh — the WATCHDOG gating.

The script is a small bash file. We test it by invoking it as a subprocess
with a fake /api/health endpoint served on a free port, asserting the
script's exit code reflects the health status:
    status=ok       -> exit 0 (heartbeat sent)
    status=degraded -> exit 0 (heartbeat sent — degraded is not down)
    status=down     -> exit 2 (heartbeat suppressed; systemd watchdog fires)
    HTTP failure    -> exit 2 (treat unreachable backend as down)
"""
from __future__ import annotations

import http.server
import json
import os
import socket
import socketserver
import subprocess
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "deploy" / "scripts" / "watchdog_heartbeat.sh"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def _fake_health_server(payload: dict | None) -> Generator[int, None, None]:
    """Spin up a one-shot HTTP server returning `payload` at /api/health.

    payload=None -> return HTTP 500 to simulate backend down/unreachable.
    """
    port = _free_port()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/api/health" or payload is None:
                self.send_response(500)
                self.end_headers()
                return
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # silence per-request stderr noise
            pass

    server = socketserver.TCPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield port
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture(autouse=True)
def _require_script_exists() -> None:
    if not SCRIPT.is_file():
        pytest.skip(f"deploy script not present: {SCRIPT}")


def _run_script(port: int) -> int:
    """Run the heartbeat script once with HEALTH_URL pointing at our fake."""
    env = os.environ.copy()
    env["HEALTH_URL"] = f"http://127.0.0.1:{port}/api/health"
    # NOTIFY_SOCKET unset -> the script's notify call is a no-op (we
    # only care about its exit code path here).
    env.pop("NOTIFY_SOCKET", None)
    return subprocess.run(["bash", str(SCRIPT)], env=env, timeout=10).returncode


def test_script_exits_zero_when_status_ok() -> None:
    with _fake_health_server({"status": "ok"}) as port:
        assert _run_script(port) == 0


def test_script_exits_zero_when_status_degraded() -> None:
    with _fake_health_server({"status": "degraded"}) as port:
        assert _run_script(port) == 0


def test_script_exits_two_when_status_down() -> None:
    with _fake_health_server({"status": "down"}) as port:
        assert _run_script(port) == 2


def test_script_exits_two_when_endpoint_unreachable() -> None:
    # No server bound to this port.
    bad_port = _free_port()
    env = os.environ.copy()
    env["HEALTH_URL"] = f"http://127.0.0.1:{bad_port}/api/health"
    env.pop("NOTIFY_SOCKET", None)
    rc = subprocess.run(["bash", str(SCRIPT)], env=env, timeout=10).returncode
    assert rc == 2


def test_script_exits_two_when_endpoint_returns_500() -> None:
    with _fake_health_server(payload=None) as port:
        assert _run_script(port) == 2
```

- [ ] **Step 3: Run, confirm fail**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/deploy/test_watchdog_script.py -v
```

Expected: the tests fail because the script today (Phase 4) sends the heartbeat unconditionally and exits 0 regardless of `/api/health`.

- [ ] **Step 4: Edit `deploy/scripts/watchdog_heartbeat.sh` to gate on health**

Replace the script body with:

```bash
#!/usr/bin/env bash
# gsfluent watchdog heartbeat — gated on /api/health status.
#
# Invoked every WatchdogSec/2 by gsfluent-backend.service. Reads
# /api/health, sends WATCHDOG=1 via systemd-notify ONLY when the backend
# reports status != "down". When status=="down" OR the endpoint is
# unreachable, we exit non-zero and skip the notify — that lets
# systemd's watchdog timer fire and restart the unit.
#
# Env vars:
#   HEALTH_URL        Default: http://127.0.0.1:7869/api/health
#   NOTIFY_SOCKET     Set by systemd; if unset we skip the notify
#                     (so the script is testable outside systemd).
#
# Exit codes:
#   0  health=ok or health=degraded; heartbeat sent (if NOTIFY_SOCKET set)
#   2  health=down or unreachable; heartbeat suppressed
set -u

HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:7869/api/health}"

# Read /api/health with a tight timeout. Capture both body + HTTP code.
response=$(curl --silent --show-error --max-time 3 \
                --write-out '\n%{http_code}' "$HEALTH_URL" 2>/dev/null || echo $'\n000')
http_code="${response##*$'\n'}"
body="${response%$'\n'*}"

if [ "$http_code" != "200" ]; then
    # Backend unreachable or returning errors -> treat as down.
    exit 2
fi

# Extract `"status": "<value>"` without jq (which may not be installed
# on minimal deploy targets). Tolerant of whitespace around the colon.
status=$(echo "$body" | grep -oE '"status"[[:space:]]*:[[:space:]]*"[^"]+"' \
                       | head -n1 \
                       | sed -E 's/.*"([^"]+)"$/\1/')

case "$status" in
    ok|degraded)
        if [ -n "${NOTIFY_SOCKET:-}" ]; then
            systemd-notify WATCHDOG=1
        fi
        exit 0
        ;;
    down|*)
        # Either explicit down or unparseable status -> suppress heartbeat.
        exit 2
        ;;
esac
```

Make sure it is executable:

```bash
chmod +x /home/frankyin/Desktop/work/gsfluent_pkg/deploy/scripts/watchdog_heartbeat.sh
```

- [ ] **Step 5: Re-run the script tests, confirm pass**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/deploy/test_watchdog_script.py -v
```

Expected: 5 passed.

- [ ] **Step 6: Confirm `deploy/gsfluent-backend.service` invokes the script correctly**

The Phase 4 unit file should already invoke `watchdog_heartbeat.sh` from a `systemd` timer or `ExecReload`-style hook. Inspect it:

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
grep -n "watchdog_heartbeat\|WatchdogSec\|NotifyAccess" deploy/gsfluent-backend.service
```

If the unit file does not already wire the script in, add a sidecar timer unit `deploy/gsfluent-backend-watchdog.timer` plus service `deploy/gsfluent-backend-watchdog.service`. If Phase 4 instead kept the heartbeat inside the backend process (an asyncio task in `composition.build_app`'s lifespan), Phase 6's change moves it out: the asyncio task should run the bash script via `asyncio.create_subprocess_exec("bash", str(SCRIPT))` and only proceed to `sd_notify` when the exit code is 0.

Pick the integration that matches what Phase 4 produced and document the choice in the deploy README. The unit-test contract from Step 5 stands either way.

- [ ] **Step 7: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add deploy/scripts/watchdog_heartbeat.sh \
        deploy/gsfluent-backend.service \
        server/tests/deploy/__init__.py \
        server/tests/deploy/test_watchdog_script.py
git commit -m "phase-6: systemd watchdog — gate WATCHDOG=1 on /api/health status != down"
```

---

### Task 6: Phase 6 verification + branch handoff

**Files:**
- No file edits in this task. Verification + push only.

- [ ] **Step 1: Run the full test suite end-to-end**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/ -v 2>&1 | tail -50
```

Expected: every test passes. Phase 6 added approximately 22 new tests:
- `tests/observability/test_event_taxonomy.py` — 4 tests
- `tests/api/test_health.py` — 13 tests
- `tests/test_health.py` — 1 (rewritten) test
- `tests/deploy/test_watchdog_script.py` — 5 tests

Plus the baseline tests all still pass.

- [ ] **Step 2: Confirm zero residual `print()` / stdlib-log calls in target files**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
echo "--- run_manager.py ---"
grep -n "print(\|_log\.\|logging\." server/gsfluent/core/run_manager.py || echo "clean"
echo "--- mpm.py ---"
grep -n "print(\|_log\.\|logging\." server/gsfluent/core/sim_engines/mpm.py || echo "clean"
```

Expected: each section prints `clean`. If any matches appear, either convert them per Task 2/Task 3 or document why they cannot (and add a `# noqa: phase-6` comment so future audits can see the explicit decision).

- [ ] **Step 3: Manual smoke test — health endpoint shape**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
PYTHONPATH=server GSFLUENT_SIM_HOME=/tmp GSFLUENT_SIM_PYTHON=python \
  .venv/bin/python -c "
from fastapi.testclient import TestClient
from gsfluent.server import create_app
client = TestClient(create_app())
r = client.get('/api/health')
print('status_code:', r.status_code)
import json
print(json.dumps(r.json(), indent=2))
"
```

Expected: prints JSON with exactly the 7 documented keys plus a `status` of `ok`, `degraded`, or `down` based on the local environment.

- [ ] **Step 4: Manual verification — journalctl shows lifecycle (requires a real systemd host)**

This is the spec's required manual verification. CI cannot run journalctl; on a deploy box where systemd is running gsfluent-backend, after a happy-path run:

```bash
journalctl -u gsfluent-backend -o json --since '10 minutes ago' \
  | jq '.MESSAGE | fromjson | select(.event | startswith("run.")) | .event'
```

Expected output (one per line, in temporal order):
```
"run.queued"
"run.preflight_ok"
"run.started"
"run.simmed"
"run.fused"
"run.packed"
"run.completed"
```

Record this in the Phase 6 PR description as the operator's manual verification step. If a run failed mid-pipeline, the sequence terminates at `run.failed` and a matching `error.<layer>.<sub>` event appears immediately before it.

- [ ] **Step 5: Push the branch**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git push -u origin phase-6-observability
```

Expected: branch published; open a PR titled `phase-6: observability completion — structured events + real health + watchdog gating`.

---

## Definition of Done — Phase 6

Phase 6 ships when ALL of:

- [ ] All 6 tasks above completed
- [ ] All new tests pass (`pytest tests/observability/test_event_taxonomy.py tests/api/test_health.py tests/test_health.py tests/deploy/test_watchdog_script.py -v`)
- [ ] All baseline tests still pass (no regressions)
- [ ] `grep -n "print(\|_log\.\|logging\." server/gsfluent/core/run_manager.py` returns nothing (or only documented exceptions)
- [ ] `grep -n "print(\|_log\.\|logging\." server/gsfluent/core/sim_engines/mpm.py` returns nothing (or only documented exceptions)
- [ ] `/api/health` returns a `HealthResponse`-shaped JSON body in TestClient
- [ ] `deploy/scripts/watchdog_heartbeat.sh` exits 0 on `ok`/`degraded`, exits 2 on `down`/unreachable
- [ ] **Manual verification on the dev box:** `journalctl -u gsfluent-backend -o json | jq '.MESSAGE | fromjson | select(.event | startswith("run."))'` shows the full lifecycle (`run.queued` → `run.preflight_ok` → `run.started` → `run.simmed` → `run.fused` → `run.packed` → `run.completed`) for a successful happy-path run
- [ ] **Manual verification on the dev box:** triggering a forced sim failure (e.g. GPU OOM) produces a single `error.sim.gpu_oom` event followed by `run.failed`
- [ ] **Manual verification on the dev box:** stopping the sim_home directory (`mv $GSFLUENT_SIM_HOME{,.bak}`) makes `/api/health` return `status="down"` and prevents the watchdog from heartbeating (systemd restarts the unit within `WatchdogSec`)
- [ ] PR open for review on `phase-6-observability`

## Handoff to Phase 7

Phase 7 (`definition-of-done sweep`) depends on:
- Every observability event landing in journald with the canonical taxonomy (Phase 6)
- The `/api/health` Pydantic contract being stable (Phase 6)
- The watchdog gating actually triggering restarts on `down` (Phase 6)

Phase 7 will:
- Run every test category (unit, integration, property, e2e) and confirm green
- Run `ruff` + `mypy --strict` and clean up any remaining warnings
- Perform the spec's manual verifications end-to-end (kill -9 mid-sim, journalctl recipes, systemd install)
- Update `README.md` and `docs/ARCHITECTURE.md` for the new component layout
- Decide the `sim.unstable_recipe` classifier open question from the spec

Phase 7 plan will be authored in `docs/superpowers/plans/2026-05-22-phase-7-definition-of-done-sweep.md`.

---

**End of Phase 6 plan.**
