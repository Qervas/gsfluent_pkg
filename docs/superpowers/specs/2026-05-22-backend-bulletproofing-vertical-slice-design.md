# gsfluent backend bulletproofing — vertical slice design

**Date:** 2026-05-22
**Status:** Design, pending user approval → writing-plans transition
**Author:** brainstormed with Claude (gstack / superpowers brainstorming)
**Implementation target:** solo engineer, ~5–6 weeks
**Frontend (`frontend/`):** out of scope; separate later sprint

---

## Context

The gsfluent backend (`server/`) was built for a single-team internal workflow on one A100 GPU box: one engineer, one set of runs at a time, "trust the IP" security model, filesystem-only state, `print()` for logs, an 83-line `supervise.sh`, recipe submission spawns arbitrary subprocesses with no validation or caps.

The next phase requires it to back a customer-facing product. Customers will:

- Submit recipes that the backend cannot trust (today's recipe path can wedge the GPU or run forever)
- Watch runs through to completion and expect cancellation to actually cancel
- Stream `.gsq` sequences interactively; expect cache hits and resumable downloads
- Hit failures and expect to know what failed and why

This spec covers the **vertical slice** that takes the existing pipeline (recipe → sim → fuse → pack → cache → stream → render) from "works for the team" to "won't break the customer." Auth, multi-tenancy, billing, and full container sandboxing are deliberately deferred.

## Goals

A customer-facing backend that:

1. **Won't wedge the GPU.** Cancellation kills the entire sim process group. Recipes are schema-validated and capped (particle count, wall-time) before they reach the GPU. Wall-time enforcement is real (subprocess timeout + PG-SIGKILL escalation), not advisory.
2. **Survives restarts gracefully.** Run state persists to disk. systemd supervises the backend. On restart, in-flight runs are reconciled — running runs re-attached, dead runs marked `interrupted` (never auto-resumed).
3. **Streams interactively without re-downloads.** `.gsq` cache responses carry `Cache-Control: immutable` + `ETag`. The viser_headless client does HEAD-check before download (cache hit → instant) and Range-resume from `.partial` (cache miss with prior interrupted download → no re-download from byte 0).
4. **Is debuggable when it breaks.** Every state transition emits a structured JSON event with `{ts, run_id, sequence_name, event, …context}`. Errors are typed and carry `trace_id` end-to-end. The customer can paste a trace_id into a support ticket; the operator greps the log stream by that ID.
5. **Is built on swappable layers.** Each major concern (simulation engine, fuser, cache codec, storage, run manager, observability) sits behind a Python Protocol. Concrete implementations are pluggable via a single composition root. Conformance tests cover the Protocols; specific tests cover the implementations.

## Non-goals (deferred, deliberate)

- **No auth, no signup, no sessions.** Customers reach the backend through some out-of-band mechanism (whitelist, share link, etc.) for now.
- **No multi-tenancy.** Single shared `work/` directory; no concept of "tenant" or "owner" on a run.
- **No billing / metering / quotas** beyond the wall-time + particle-count caps.
- **No container sandbox per run.** Wall-time + particle caps cover ~80% of the runaway-recipe risk; full cgroup/namespace isolation waits until the box hosts more than one customer's workload concurrently.
- **No DB-backed state.** Disk-persisted JSON in `work/_state/runs/<run_id>.json` is enough at single-tenant scale. Migration to a real DB is a separate sprint.
- **No real job queue.** `asyncio.create_subprocess_*` is sufficient for the concurrency demand of a single tenant. Celery/Arq waits until the demand justifies it.
- **No `.gsq` checksums or intermediate `.ply` retention.** Data integrity is its own sprint after this slice ships.
- **No fuser robustness changes** (K-NN map from frame 0 only — same sprint as above).
- **No `frontend/` (React SPA) changes.** Backend hardening unblocks safe ops independent of frontend state.
- **No Docker/Kubernetes deployment.** systemd on bare-metal Linux is the primary target. `.dockerignore` already exists; containerization is a small future addition that does not invalidate any decision here.

## Architecture

The pipeline shape stays the same:

```
recipe ──► sim ──► fuse ──► pack ──► cache (.gsq) ──► stream ──► render (browser)
```

Six layers, each behind a Protocol; four cross-cutting hardening threads woven through.

### Six layers

```
┌──────────────────────────────────────────────────────────────────┐
│ L0: HTTP API                FastAPI routers (thin shells)         │
│                              api/runs.py, api/sequences.py,        │
│                              api/recipes.py, api/models.py,        │
│                              api/stream.py                         │
├──────────────────────────────────────────────────────────────────┤
│ L1: Run lifecycle           protocols/runs.py    : RunManager      │
│                              core/run_manager.py  : AsyncioRunManager │
│                              swap candidates: Celery/Arq later    │
├──────────────────────────────────────────────────────────────────┤
│ L2: Sim orchestration       protocols/sim.py     : SimulationEngine │
│                              core/sim_engines/mpm.py : MPMSimulationEngine │
│                              swap candidates: mock-for-tests,      │
│                              alternative physics engines           │
├──────────────────────────────────────────────────────────────────┤
│ L3: Splat fusion            protocols/fuse.py    : Fuser           │
│                              core/fusers/knn_kabsch.py : KNNKabschFuser │
│                              swap candidates: ICP, learned warps,  │
│                              no-fuse (if sim emits 3DGS directly)  │
├──────────────────────────────────────────────────────────────────┤
│ L4: Cache codec             protocols/cache.py   : CacheCodec      │
│                              core/codecs/gsq.py   : GSQCodec       │
│                              swap candidates: SPZ-per-frame,       │
│                              4DGS-class formats, raw-PLY-zstd      │
├──────────────────────────────────────────────────────────────────┤
│ L5: Storage                 protocols/storage.py : Storage         │
│                              storage/filesystem.py : FilesystemStorage │
│                              swap candidates: S3, GCS, MinIO       │
├──────────────────────────────────────────────────────────────────┤
│ L6: Observability           protocols/observability.py : EventEmitter │
│                              observability/jsonlog.py : StdlibJSONEmitter │
│                              swap candidates: structlog, OTel      │
└──────────────────────────────────────────────────────────────────┘
```

### Four cross-cutting hardening threads

1. **Process model** — every sim subprocess spawned in a new process group (`start_new_session=True`); cancellation/timeout uses `os.killpg(pgid, SIGTERM)` → wait 30s → `os.killpg(pgid, SIGKILL)`. Run state persists to disk on every transition; backend restart scans `_state/runs/` and reconciles.
2. **Recipe boundary** — `POST /api/runs` strict-Pydantic-validates and `limits.check_recipe_caps()`-validates before enqueueing. Wall-time cap enforced in the orchestrator via `asyncio.wait_for(proc.wait(), timeout=cap)` + PG-kill on timeout.
3. **HTTP cache hygiene** — server adds `Cache-Control: public, immutable, max-age=31536000` + `ETag: "<size>-<mtime>"` to `.gsq` responses; handles `If-None-Match` → 304. Client (viser_headless) does HEAD-first to skip download on hit; `Range: bytes=<n>-` to resume from `.partial`.
4. **Structured observability** — `print()` replaced with stdlib `logging` + JSON formatter (no extra dep). `RunLogAdapter` auto-attaches `{run_id, sequence_name}` context to every event. Run-lifecycle events emitted at every transition: `run.queued`, `run.started`, `run.preflight_ok`, `run.simmed`, `run.fused`, `run.packed`, `run.completed`, `run.failed`, `run.cancelling`, `run.cancelled`.

### Composition root

Single place where concrete impls get wired:

```python
# server/gsfluent/composition.py
def build_app(config: AppConfig) -> FastAPI:
    storage = FilesystemStorage(root=config.work_dir / "cache")
    cache_codec = GSQCodec()
    sim_engine = MPMSimulationEngine(
        sim_home=config.sim_home,
        sim_python=config.sim_python,
        sim_env=config.sim_env,  # optional conda env name
    )
    fuser = KNNKabschFuser(k=8)
    obs = StdlibJSONEmitter(stream=sys.stdout)
    run_mgr = AsyncioRunManager(
        sim_engine=sim_engine,
        fuser=fuser,
        cache_codec=cache_codec,
        storage=storage,
        obs=obs,
        state_dir=config.work_dir / "_state" / "runs",
        wall_time_cap_sec=config.wall_time_cap_sec,
        particle_count_cap=config.particle_count_cap,
    )
    app = FastAPI()
    app.dependency_overrides[get_run_manager] = lambda: run_mgr
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_cache_codec] = lambda: cache_codec
    app.include_router(runs.router, prefix="/api/runs")
    app.include_router(sequences.router, prefix="/api/sequences")
    # ...
    return app
```

## Protocol contracts

All protocols live under `server/gsfluent/protocols/`. Pure interfaces, no logic.

### `protocols/sim.py`

```python
from typing import Protocol
from pathlib import Path

class SimulationEngine(Protocol):
    async def preflight(self) -> None:
        """Raise typed error if environment cannot run a sim.
        Possible: SimEnvMissingError, SimInterpreterMissingError, GPUUnavailableError."""

    async def run(
        self,
        recipe: "ValidatedRecipe",
        model: "ModelRef",
        output_dir: Path,
        wall_time_sec: int,
        on_event: "EventEmitter",
    ) -> "SimResult":
        """Run sim to completion or raise typed SimError.

        Must be cancellable via cooperative cancellation (asyncio.CancelledError
        on outer task) OR external SIGTERM to the process group of the spawned sim.

        Emits structured events through on_event at: sim.started, sim.frame_N,
        sim.completed. Caller (RunManager) translates these to run.* events
        with run_id attached.
        """
```

### `protocols/fuse.py`

```python
class Fuser(Protocol):
    def build_correspondence(
        self,
        reference_ply_path: Path,
        first_frame_particles: "ParticleFrame",
    ) -> "Correspondence":
        """One-shot: build the reference→particle mapping used for every
        subsequent frame in the sequence. Raises FuseError on degenerate input."""

    def fuse_frame(
        self,
        correspondence: "Correspondence",
        particle_frame: "ParticleFrame",
    ) -> "SplatFrame":
        """Apply correspondence + per-frame rotation. Raises FuseError on
        non-finite input or degenerate K-NN cluster."""
```

### `protocols/cache.py`

```python
class CacheCodec(Protocol):
    media_type: str        # e.g. "application/x-gsq"
    file_extension: str    # e.g. ".gsq"

    def encode(
        self,
        frames: "Iterable[SplatFrame]",
        out: "BinaryIO",
        on_event: "EventEmitter",
    ) -> "CacheMetadata":
        """Encode a sequence to the codec's wire format. Returns metadata
        including bbox, n_splats, n_frames, fps_hint. Raises CodecError on
        unsanitizable input (e.g. all-NaN frame)."""

    def decode_streaming(
        self,
        src: "AsyncIterator[bytes]",
    ) -> "AsyncIterator[DecodedFrame]":
        """Decode-as-bytes-arrive. First yields when static block + frame 0
        are available, then yields one DecodedFrame per chunk."""

    def decode_all(self, src: "BinaryIO") -> "Sequence[DecodedFrame]":
        """Synchronous all-at-once loader (used by load-from-disk path)."""
```

### `protocols/storage.py`

```python
class Storage(Protocol):
    async def put(
        self, key: str, src: "BinaryIO", metadata: "dict[str, str]"
    ) -> "StorageHandle": ...

    async def get(self, key: str) -> "AsyncIterator[bytes]":
        """Stream the whole object."""

    async def get_range(
        self, key: str, start: int, end: "int | None"
    ) -> "AsyncIterator[bytes]":
        """Byte-range stream. end=None means to EOF."""

    async def stat(self, key: str) -> "StorageStat | None":
        """Return size, mtime, etag — or None if key doesn't exist."""

    async def exists(self, key: str) -> bool: ...
```

### `protocols/runs.py`

```python
class RunManager(Protocol):
    async def submit(
        self, recipe: "ValidatedRecipe", *, model: "ModelRef"
    ) -> "RunId":
        """Validate, persist initial state, enqueue. Returns immediately
        with a RunId. Raises ValidationError or CapExceededError; both
        translate to HTTP 422 by the API layer."""

    async def cancel(self, run_id: "RunId") -> None:
        """Idempotent. Initiates PG-SIGTERM; background task escalates
        to PG-SIGKILL after 30s if still alive."""

    async def status(self, run_id: "RunId") -> "RunStatus": ...

    async def stream_events(
        self, run_id: "RunId"
    ) -> "AsyncIterator[RunEvent]":
        """SSE/WebSocket feed of structured events for this run."""

    async def recover_on_boot(self) -> "RecoveryReport":
        """Scan state dir, reconcile in-flight runs with live PIDs.
        Returns counts: {reattached, interrupted, terminal_already}."""
```

### `protocols/observability.py`

```python
class EventEmitter(Protocol):
    def emit(self, event: str, **context) -> None:
        """Emit one structured event. `event` is dotted noun.verb
        (run.started, error.sim.gpu_oom, etc.). context is JSON-serializable
        kwargs. Auto-attaches ts."""

    def child(self, **context) -> "EventEmitter":
        """Return an emitter that auto-attaches `context` to every emit().
        Used by RunManager to bind run_id + sequence_name to a per-run logger."""
```

## Components — file-level changes

### Changed files

| File | Today | After | Δ LOC |
|---|---|---|---|
| `server/gsfluent/core/runner.py` | mixed concerns, ~572 lines | renamed → `core/run_manager.py`; implements `AsyncioRunManager`; PG-spawn + signal escalation + state persistence + structured events; calls `SimulationEngine` instead of directly invoking shell | ~+150, -60 net |
| `server/gsfluent/api/runs.py` | permissive validation, ~434 lines | strict-Pydantic + `limits.check_recipe_caps()` + 422 error shape + uses `RunManager` via `Depends()` | ~+80 |
| `server/gsfluent/api/sequences.py` | basic FileResponse, ~429 lines | adds `Cache-Control: immutable`, `ETag`, `If-None-Match` → 304; uses `Storage.stat()` + `Storage.get_range()` instead of direct filesystem | ~+15 |
| `server/gsfluent/core/library.py` | mixed: storage + business logic, ~775 lines | extract filesystem ops to `storage/filesystem.py`; keep sequence/model business logic | ~+0, refactor only |
| `server/tools/run_sim.sh` | 197-line orchestrator | 20-line conda-activate shim that hands control to `python -m gsfluent.core.sim_engines.mpm` | ~-177 |
| `server/tools/fuse_to_full_ply.py` | 819-line script | thin CLI wrapper around `core/fusers/knn_kabsch.py` | refactor only |
| `server/tools/pack_splats.py` | 310-line script | thin CLI wrapper around `core/codecs/gsq.py` | refactor only |
| `server/supervise.sh` | 83-line shell supervisor | **DELETED**; replaced by systemd unit | -83 |
| `frontend/python/viser_headless.py` | always re-downloads, no resume, ~1361 lines | HEAD-check before download; Range-from-`.partial` resume; emits `cell.cache.hit` / `cell.cache.resumed` events | ~+80 |
| `frontend/python/viser_headless.py` (rename) | `npz_root`, `--npz_dir` | `cache_root`, `--cache-dir` (with deprecated alias for one release) | refactor only |
| `server/gsfluent/core/runner.py` (env var) | `GSFLUENT_NPZ_REBUILD` | `GSFLUENT_CACHE_REBUILD` (deprecated alias for one release) | refactor only |

### New files

| File | Purpose | LOC |
|---|---|---|
| `server/gsfluent/protocols/__init__.py` | re-exports | ~10 |
| `server/gsfluent/protocols/sim.py` | `SimulationEngine` Protocol + typed errors | ~60 |
| `server/gsfluent/protocols/fuse.py` | `Fuser` Protocol + typed errors | ~40 |
| `server/gsfluent/protocols/cache.py` | `CacheCodec` Protocol + typed errors | ~50 |
| `server/gsfluent/protocols/storage.py` | `Storage` Protocol + typed errors | ~40 |
| `server/gsfluent/protocols/runs.py` | `RunManager` Protocol + state types | ~60 |
| `server/gsfluent/protocols/observability.py` | `EventEmitter` Protocol | ~30 |
| `server/gsfluent/core/run_manager.py` | `AsyncioRunManager` impl | ~350 |
| `server/gsfluent/core/sim_engines/__init__.py` | re-exports | ~10 |
| `server/gsfluent/core/sim_engines/mpm.py` | `MPMSimulationEngine` (logic from `run_sim.sh`) | ~250 |
| `server/gsfluent/core/sim_engines/mock.py` | `MockSimulationEngine` for tests | ~80 |
| `server/gsfluent/core/fusers/__init__.py` | re-exports | ~10 |
| `server/gsfluent/core/fusers/knn_kabsch.py` | `KNNKabschFuser` impl (logic from `fuse_to_full_ply.py`) | ~600 (moved) |
| `server/gsfluent/core/codecs/__init__.py` | re-exports | ~10 |
| `server/gsfluent/core/codecs/gsq.py` | `GSQCodec` impl (logic from `pack_splats.py`) | ~300 (moved) |
| `server/gsfluent/storage/__init__.py` | re-exports | ~10 |
| `server/gsfluent/storage/filesystem.py` | `FilesystemStorage` impl | ~150 |
| `server/gsfluent/observability/__init__.py` | re-exports | ~10 |
| `server/gsfluent/observability/jsonlog.py` | `StdlibJSONEmitter` + `RunLogAdapter` + JSON formatter | ~120 |
| `server/gsfluent/core/limits.py` | cap config + `check_recipe_caps()` | ~80 |
| `server/gsfluent/core/state.py` | run state JSON persistence + boot scanner | ~120 |
| `server/gsfluent/composition.py` | wiring root | ~80 |
| `server/gsfluent/config.py` | `AppConfig` dataclass; env-var → config loader | ~80 |
| `server/gsfluent/api/health.py` (extend or new) | real signals: GPU/sim-home/disk/last-run | ~60 |
| `deploy/gsfluent-backend.service` | systemd unit | ~30 |
| `deploy/README.md` | how to install the unit, journalctl recipes | ~80 |

### New tests

| File | What it covers | LOC |
|---|---|---|
| `server/tests/protocols/test_simulation_engine_conformance.py` | any `SimEngine` impl: preflight, run-to-completion, cancel | ~150 |
| `server/tests/protocols/test_fuser_conformance.py` | any `Fuser` impl: correspondence build, frame fuse | ~80 |
| `server/tests/protocols/test_cache_codec_conformance.py` | any `Codec` impl: encode/decode round-trip, streaming, edge cases | ~150 |
| `server/tests/protocols/test_storage_conformance.py` | any `Storage` impl: put/get/range/stat/exists | ~120 |
| `server/tests/protocols/test_run_manager_conformance.py` | any `RunManager` impl: submit/cancel/status/recover | ~150 |
| `server/tests/codecs/test_gsq.py` | GSQ-specific: bbox edges, fp16 cov-floor, quantization bounds | ~100 |
| `server/tests/sim_engines/test_mpm.py` | MPM-specific: env-var parsing, preflight error classification | ~80 |
| `server/tests/sim_engines/test_mock.py` | mock-sim correctness (used by integration tests downstream) | ~80 |
| `server/tests/storage/test_filesystem.py` | path traversal defense, atomic rename, range correctness | ~100 |
| `server/tests/fusers/test_knn_kabsch.py` | coord conversion, Kabsch correctness, K-NN degenerate-cluster handling | ~150 |
| `server/tests/runs/test_asyncio_run_manager.py` | state machine, lifecycle, boot recovery | ~200 |
| `server/tests/observability/test_jsonlog.py` | event shape, context propagation, ts injection | ~80 |
| `server/tests/api/test_runs_validation.py` | strict-mode rejection, cap-violation 422 shape, error envelope | ~120 |
| `server/tests/api/test_sequences_cache_headers.py` | ETag / Cache-Control / If-None-Match → 304 / Range → 206 | ~100 |
| `server/tests/integration/test_cancel_kills_pg.py` | submit → cancel → PG dead within grace | ~80 |
| `server/tests/integration/test_sigterm_ignoring_sim_gets_sigkill.py` | escalation works | ~80 |
| `server/tests/integration/test_wall_time_enforced.py` | timeout fires, run.failed.sim.wall_time_exceeded | ~80 |
| `server/tests/integration/test_restart_mid_run_recovers.py` | state persists, boot reconciles | ~100 |
| `server/tests/integration/test_sim_error_classification.py` | parametrized stderr → expected error kind | ~120 |
| `server/tests/integration/test_streaming_cache_hit.py` | second request uses HEAD, no body downloaded | ~80 |
| `server/tests/integration/test_streaming_resume_from_partial.py` | Range request, 206 received, decode completes | ~100 |
| `server/tests/property/test_gsq_round_trip.py` | Hypothesis: encode→decode preserves data within bounds | ~80 |
| `server/tests/property/test_quantization_bounds.py` | int16 xyz quantization error bound | ~60 |
| `server/tests/e2e/test_happy_path_small.py` | submit recipe → completed → fetch .gsq | ~80 |
| `server/tests/e2e/test_recipe_rejected_early.py` | 422 before any subprocess spawn | ~60 |
| `server/tests/fixtures/mock_sim.sh` | configurable fake sim script | ~80 |
| `server/tests/fixtures/__init__.py` (pytest fixtures) | `mock_sim_engine`, `mock_storage`, `tmp_state_dir` fixtures | ~120 |

### Effort totals

```
Source changes:           ~610 lines
Protocols (new):          ~290 lines
Core impls (new + moved): ~1640 lines (most are moves, not net-new)
Storage (new + extracted): ~150 lines
Observability (new):      ~120 lines
viser_headless:           ~80 lines
Configuration:            ~160 lines
systemd unit + README:    ~110 lines
Shell deletion:           -260 lines (supervise.sh + most of run_sim.sh)
Tests:                    ~2700 lines (across protocol conformance, impl-specific,
                          integration, property, e2e, fixtures)
                          ──────
Source net:               ~+2900 lines (most are extracted/moved, not new logic)
Tests net:                ~+2700 lines (all genuinely new)

Total timeline:           5–6 weeks solo at sustainable pace
Critical path:            run_manager + sim_engines/mpm + integration tests
                          for cancellation + recovery
```

## Data flow

### Flow A: Submit a run → completion

```
POST /api/runs {recipe}
        │
        ▼
  api/runs.py
    Pydantic strict-validate      → on fail: 422 error.kind=validation.*
    limits.check_recipe_caps      → on fail: 422 error.kind=cap_exceeded.*
    state.create_run_record()     → write work/_state/runs/<id>.json (queued)
    obs.emit("run.queued", {run_id, recipe_hash, particle_count, wall_time_cap})
    runs.submit(recipe, model)    → returns RunId
        │
        ▼
  core/run_manager.py — AsyncioRunManager
    sim_engine.preflight()        → on fail: state→failed, error.kind=preflight.*
    spawn task: _run_to_completion(run_id, recipe, model)
    return RunId
        │
        ▼  (background task)
  AsyncioRunManager._run_to_completion:
    state→started; obs.emit("run.started")
    proc = sim_engine.run(recipe, model, output_dir, wall_time_cap, on_event=child_obs)
        │
        ▼
  core/sim_engines/mpm.py — MPMSimulationEngine
    asyncio.create_subprocess_*(
        bash, run_sim.sh, model_dir, "--config", recipe_path, ...
        stdout=PIPE, stderr=STDOUT,
        start_new_session=True,    ← NEW PROCESS GROUP
    )
    record pgid in state file
    asyncio.wait_for(proc.wait(), timeout=wall_time_cap)
      └─ on TimeoutError:
           os.killpg(pgid, SIGTERM)
           await asyncio.sleep(30)
           if proc.returncode is None: os.killpg(pgid, SIGKILL)
           raise SimWallTimeExceededError
      └─ on completion:
           parse stderr for known patterns (gpu_oom, cfl, etc.)
           if rc != 0: raise classified SimError
        │
        ▼
  back in _run_to_completion:
    fuser.build_correspondence(reference, frames[0])
    for f in frames: splat = fuser.fuse_frame(corr, f)
    storage.put(f"{run_id}.gsq", codec.encode(splat_iter, ...))
    state→completed; obs.emit("run.completed", {durations: {sim, fuse, pack}})
```

### Flow B: Cancel a running run

```
POST /api/runs/<id>/cancel
        │
        ▼
  api/runs.py → runs.cancel(run_id)
        │
        ▼
  AsyncioRunManager.cancel(run_id):
    state = self._state.read(run_id)
    if state.terminal: return  (idempotent)
    if state.pgid:
        os.killpg(state.pgid, SIGTERM)
    state→cancelling
    obs.emit("run.cancelling")
    asyncio.create_task(self._escalate_kill(run_id, state.pgid, grace=30))
        │
        ▼
  _escalate_kill:
    await asyncio.sleep(grace)
    if proc still alive (read /proc/<pid>/status or os.kill(pid, 0)):
        os.killpg(pgid, SIGKILL)
        obs.emit("run.cancel.escalated")
    state→cancelled
    obs.emit("run.cancelled")
```

### Flow C: Backend crash recovery on restart

```
systemd starts gsfluent-backend.service
        │
        ▼
  uvicorn → FastAPI lifespan startup hook
        │
        ▼
  composition.build_app() → app starts
        │
        ▼
  FastAPI lifespan (async context manager):
      on startup → await run_mgr.recover_on_boot()
        │
        ▼
  AsyncioRunManager.recover_on_boot:
    for f in (state_dir / "runs").glob("*.json"):
        rec = read(f)
        if rec.status in TERMINAL_STATES: continue
        if rec.pid and os.kill(rec.pid, 0) succeeds:
            self._runs[rec.id] = re-attach(rec)
            obs.emit("boot.run.reattached", {run_id})
        else:
            rec.status = "interrupted"
            rec.error = {"kind": "internal.backend_restarted"}
            write(f, rec)
            obs.emit("boot.run.interrupted", {run_id})
    return RecoveryReport(reattached=N, interrupted=M)
        │
        ▼
  sd_notify("READY=1")
  schedule background heartbeat: every 15s while /api/health is ok →
      sd_notify("WATCHDOG=1")
```

### Flow D: Customer hits the streaming cache

```
SPA selects sequence X
        │
        ▼
  viser_headless._sync_cell_gsq_streaming(name, url, dest, partial):
        │
        ▼
    if dest.is_file():
        head = httpx.head(url, timeout=10.0, trust_env=False)
        if head.status_code == 200:
            remote_etag = head.headers.get("etag")
            local_etag = _local_etag(dest)
            if remote_etag and remote_etag == local_etag:
                cells[cell_key] = load_cell_gsq(dest)
                obs.emit("cell.cache.hit", {name, source="etag"})
                return {"ok": True, "cached": True}
            # fallback: size compare for back-compat with no-etag deployments
            if int(head.headers.get("content-length", -1)) == dest.stat().st_size:
                cells[cell_key] = load_cell_gsq(dest)
                obs.emit("cell.cache.hit", {name, source="size"})
                return {"ok": True, "cached": True}
        │
        ▼
    if partial.is_file():
        n = partial.stat().st_size
        headers = {"Range": f"bytes={n}-"}
        obs.emit("cell.cache.resuming", {name, at: n})
        with httpx.stream("GET", url, headers=headers, ...) as r:
            if r.status_code == 206:
                # append to partial; decode-as-arrives, accounting for offset
                ...
            else:
                # server didn't honor Range, restart from byte 0
                partial.unlink()
                # re-enter from-scratch download
        │
        ▼
    else:  # fresh download
        with httpx.stream("GET", url, ...) as r:
            # existing streaming decode (unchanged)
            ...
        │
        ▼
    on completion: partial.replace(dest); write _local_etag(dest, size+mtime)

Backend side, api/sequences.py:
    GET /api/sequences/{name}/cache/splats.gsq:
        if exists check ...
        stat = storage.stat(key)
        etag = f'"{stat.size}-{int(stat.mtime)}"'
        if request.headers.get("If-None-Match") == etag:
            return Response(status_code=304, headers={"etag": etag})
        return FileResponse(
            path,
            media_type=codec.media_type,
            headers={
                "etag": etag,
                "cache-control": "public, immutable, max-age=31536000",
            },
        )
        # Range support: already provided by FileResponse
```

## Error handling

### Error taxonomy

All errors carry a dotted `kind` discriminator and a `message`. Most include `details`. API responses include `trace_id`.

| Kind | Source | HTTP | Customer-visible | Auto-retry? |
|---|---|---|---|---|
| `validation.<field>` | Pydantic strict-mode | 422 | "Field X invalid: <msg>" | No |
| `cap_exceeded.particle_count` | `limits.check_recipe_caps` | 422 | "Particle count N exceeds limit M" | No |
| `cap_exceeded.wall_time` | `limits.check_recipe_caps` | 422 | "Wall-time hint Ns exceeds backend max Ms" | No |
| `cap_exceeded.recipe_size` | `limits.check_recipe_caps` | 422 | "Recipe JSON exceeds Nkb limit" | No |
| `preflight.sim_home_missing` | `MPMSimulationEngine.preflight` | 503 / run→failed | "Backend not ready (operator)" | No |
| `preflight.sim_interpreter_missing` | same | 503 | "Backend not ready (operator)" | No |
| `preflight.gpu_unavailable` | same | 503 | "Backend not ready (operator)" | No |
| `preflight.disk_full` | `FilesystemStorage` | 503 | "Backend not ready (operator)" | No |
| `sim.wall_time_exceeded` | orchestrator timeout fires | run→failed | "Sim exceeded N-second cap" | No |
| `sim.gpu_oom` | stderr classifier | run→failed | "Sim allocated too much GPU memory" | No |
| `sim.unstable_recipe` | stderr classifier (CFL / illegal-memory) | run→failed | "Numerical instability at frame N; try increasing substep_dt" | No |
| `sim.crashed` | non-zero exit, unclassified | run→failed | "Sim crashed (trace_id <id>)" | No |
| `fuse.degenerate_cluster` | `KNNKabschFuser` | run→failed | "Fuse failed at frame N: degenerate K-NN" | No |
| `fuse.non_finite_input` | same | run→failed | "Fuse failed: sim produced NaN positions at frame N" | No |
| `codec.unsanitizable` | `GSQCodec.encode` | run→failed | "Cache build failed: all-NaN frame N" | No |
| `storage.transient.disk_full` | `FilesystemStorage` | 503 / run→failed | "Temporary storage issue, retrying" | **Yes** (3× backoff) |
| `storage.transient.io_error` | same | 503 | "Temporary storage issue, retrying" | **Yes** (3× backoff) |
| `storage.not_found` | `FilesystemStorage.stat → None` | 404 | "Sequence not found" | No |
| `streaming.network.*` | viser_headless client | (cell load failed) | "Reconnecting..." pill | **Yes** (exp backoff) |
| `internal.<class>` | uncaught exception | 500 with trace_id | "Internal error (trace_id <id>)" | No |
| `internal.backend_restarted` | recovery sees in-flight run | (run → interrupted) | "Run was interrupted by a backend restart; please re-submit" | No |

### API error response shape

```json
{
  "error": {
    "kind": "cap_exceeded.particle_count",
    "message": "Particle count 800000 exceeds limit 500000",
    "details": { "requested": 800000, "limit": 500000 },
    "trace_id": "01H8K2P..."
  }
}
```

### Propagation invariant

**Every error has exactly one structured event** emitted at the boundary where it is caught. The per-run `EventEmitter` (built via `obs.child(run_id=..., sequence_name=...)`) auto-attaches context.

```
Codec error    → caught in RunManager._run_to_completion → obs.emit("error.codec.<sub>", ...)
                                                         → state→failed
Fuser error    → caught in RunManager._run_to_completion → obs.emit("error.fuse.<sub>", ...)
                                                         → state→failed
Sim error      → raised by SimulationEngine.run          → caught in RunManager
                                                         → obs.emit("error.sim.<sub>", ...)
                                                         → state→failed
Storage transient → caught and retried in caller         → obs.emit("error.storage.transient", retry=N)
                                                         → on exhaustion: obs.emit("error.storage.exhausted")
Unhandled      → caught in FastAPI exception handler     → obs.emit("error.internal", exc_info=..., trace_id)
                                                         → 500 with trace_id
```

### Recovery patterns

- `validation.*` / `cap_exceeded.*`: never retry, customer fixes input
- `preflight.*`: never auto-retry, operator fixes environment
- `sim.*` / `fuse.*` / `codec.*`: never auto-retry, customer investigates
- `storage.transient.*`: retry with jittered exponential backoff, max 3 attempts
- `streaming.network.*`: retry with exponential backoff on the viser_headless client
- `internal.*`: never auto-retry; 500 + trace_id is the operator alert signal
- `internal.backend_restarted`: stay `interrupted`, never auto-resume

## Testing strategy

### Test pyramid

```
                  e2e (~6 tests, ~30s)
                ──────────────────────
              integration (~10 tests, ~3min)
            ────────────────────────────────────
          property (~4 tests, ~2min)
        ────────────────────────────────────────────
      protocol conformance (~5 suites × N impls, ~10s)
    ─────────────────────────────────────────────────────
   per-impl unit tests (~14 files, ~5s)
  ────────────────────────────────────────────────────────────
```

### `mock_sim.sh` fixture — the unlock

`server/tests/fixtures/mock_sim.sh` is a deterministic, configurable fake of the sim binary. Integration tests parametrize it via env vars:

```bash
# Knobs:
#   MOCK_SIM_FRAMES=10              how many sim_*.ply files to emit
#   MOCK_SIM_DELAY_SEC=0.1          per-frame pause (cancel/timeout tests)
#   MOCK_SIM_IGNORE_SIGTERM=1       trap SIGTERM (SIGKILL escalation tests)
#   MOCK_SIM_EXIT=0                 final exit code
#   MOCK_SIM_STDERR_PATTERN=cfl     inject sim-style stderr (classifier tests)
```

This lets every dangerous-path test be deterministic and CI-able with no real GPU.

### Protocol conformance tests

Each Protocol has a conformance suite that any concrete impl must pass. When a future impl (e.g. `SPZCodec`) is added, point the conformance test at it; no test duplication.

### CI

`.github/workflows/test.yml` (existing — verify, extend if needed):

```yaml
jobs:
  unit:           # protocols + per-impl unit, ~5s
  integration:    # uses mock_sim, ~3min
  lint:           # ruff
  typecheck:      # mypy --strict on gsfluent/
  # property:     # nightly OR `[hypothesis]` label
  # gpu:          # not in CI; manual on the GPU server
```

### Definition of done

The C slice ships when **all** of:

- All Protocol conformance tests pass for current concrete impls
- Every integration test passes locally + in CI
- Existing `test_runner.py`, `test_runs_api.py`, `test_sequences_import.py`, `test_zup_invariant.py`, etc. still pass (no regressions across the refactor)
- Manual: kill -9 the backend mid-sim, restart, run resumes as `interrupted`
- Manual: `journalctl -u gsfluent-backend -o json | jq` shows structured events for a happy-path run
- systemd unit deployed; `systemctl status gsfluent-backend` shows active and recent restart count = 0
- README / deploy docs updated for systemd install

## Migration plan / sequencing

Implementation order minimizes mid-flight breakage. Each phase is independently shippable behind feature flags or via parallel-impl + cutover.

### Phase 1 — foundations (~1 week)

- Add `protocols/` directory with all six Protocols
- Add `observability/jsonlog.py` + `EventEmitter` Protocol implementation
- Add `core/state.py` with run-state JSON persistence
- Add `core/limits.py`
- Add `config.py` + `composition.py` skeletons
- NO behavior changes yet — just scaffolding

**Verify:** existing tests still pass; new scaffolding has its own unit tests.

### Phase 2 — extract impls (~1.5 weeks)

- Move logic from `tools/pack_splats.py` → `core/codecs/gsq.py` (script becomes CLI wrapper)
- Move logic from `tools/fuse_to_full_ply.py` → `core/fusers/knn_kabsch.py` (script becomes CLI wrapper)
- Extract filesystem ops from `core/library.py` → `storage/filesystem.py`
- Refactor `runner.py` → `core/run_manager.py` (preserve all current behavior); wire through `composition.py`
- All Protocol conformance + per-impl unit tests added

**Verify:** existing integration smoke test (submit a recipe with `MockSimulationEngine`) passes end-to-end.

### Phase 3 — sim orchestration rewrite (~1 week)

- Add `core/sim_engines/mpm.py` (absorbs `run_sim.sh` logic) + `core/sim_engines/mock.py`
- Slim `tools/run_sim.sh` to 20-line conda-activate shim
- Add `start_new_session=True` + PG-signal escalation in `run_manager.py`
- Wall-time enforcement via `asyncio.wait_for`
- Recipe strict-validation + `limits.check_recipe_caps` in `api/runs.py`
- 422 error envelope in API

**Verify:** integration tests for cancel-kills-PG, wall-time-enforced, SIGTERM-ignoring-gets-SIGKILL, recipe-rejected-early all pass.

### Phase 4 — crash recovery + supervision (~3 days)

- Implement `RunManager.recover_on_boot()`
- Wire FastAPI `lifespan` async context manager (modern API; replaces deprecated `app.on_event("startup")`) → `await recover_on_boot()`
- Add `sd_notify` heartbeat
- Write `deploy/gsfluent-backend.service`
- Delete `supervise.sh`
- Update deploy docs

**Verify:** restart-mid-run-recovers integration test passes; manual systemd install on dev box works.

### Phase 5 — streaming cache hardening (~3 days)

- Server: `Cache-Control`, `ETag`, `If-None-Match` → 304 in `api/sequences.py`
- Client: HEAD-skip + Range-resume in `viser_headless._sync_cell_gsq_streaming`
- Rename `npz_root` → `cache_root`, `--npz_dir` → `--cache-dir`, `GSFLUENT_NPZ_REBUILD` → `GSFLUENT_CACHE_REBUILD` (with deprecated aliases)

**Verify:** streaming-cache-hit and streaming-resume integration tests pass.

### Phase 6 — observability completion (~3 days)

- Audit `core/run_manager.py` + `core/sim_engines/mpm.py` for remaining `print()` calls → emit structured events instead
- Extend `api/health.py` with real signals (GPU reachable, sim_home exists, disk free, last successful run timestamp)
- Update health probe in `gsfluent-backend.service` to check meaningful signals

**Verify:** `journalctl -u gsfluent-backend -o json | jq '.MESSAGE | fromjson | select(.event | startswith("run."))'` shows the full lifecycle.

### Phase 7 — definition-of-done sweep (~3 days)

- Run all test categories
- Lint + typecheck clean
- Manual verifications (kill -9 backend, journalctl recipes, systemd install)
- Update `README.md` and `docs/ARCHITECTURE.md` for the new component layout
- Decide on `sim.unstable_recipe` classifier (see open question below)

## Open questions

1. **`sim.unstable_recipe` classification** — worth the ~150 lines of stderr pattern matching? Cost/value depends on whether customers write recipes by hand (high value) or via templated UI (low value). Default in spec: include it, parametrize the patterns in a YAML so they can be tuned post-launch.

2. **Wall-time grace period (Section 3 Flow B)** — 30 seconds SIGTERM-to-SIGKILL feels right for typical sim checkpoints, but earthquake / demolition recipes might want longer. Default in spec: 30s with per-recipe override via `recipe.shutdown_grace_sec` (capped at 120s by backend config).

3. **`If-None-Match` ETag format** — currently `"<size>-<mtime>"`. Strong ETag would be a content hash but costs an extra read per response. Default in spec: weak ETag (size+mtime) is sufficient since `.gsq` is immutable per sequence.

4. **`recover_on_boot` interrupt-vs-reattach safety** — `os.kill(pid, 0)` checking for liveness has a PID-reuse race window (microscopic in practice but real). Should we cross-check against process start time from `/proc/<pid>/stat`? Default in spec: yes, comparing the persisted `pgid_started_at` against `/proc/<pid>/stat[21]` (starttime field). Costs ~20 lines, eliminates race.

5. **`MockSimulationEngine` for production use** — useful for customer-facing demos/sandbox? Default in spec: no, keep mock as test-only. A dedicated `DemoSimulationEngine` is a separate spec if needed.

## Out-of-spec follow-ups (future sprints, in order)

Once this slice ships:

1. **Data integrity sprint** — `.gsq` checksums in manifest, intermediate `.ply` retention controls, K-NN map robustness (median over frames 0..3 instead of just frame 0), manifest signing.
2. **Frontend rework sprint** — React SPA hardening (user described as "kinda garbage").
3. **Auth + multi-tenancy sprint** — when first customer requires tenant isolation.
4. **DB-backed state sprint** — when single-tenant filesystem state outgrows its constraints.
5. **Container sandbox per run** — when more than one customer's workload runs concurrently.
6. **Real job queue (Celery/Arq)** — when concurrent run demand exceeds asyncio's reasonable limit (~50 concurrent or persistent-queue requirements).
7. **Object storage migration (S3/GCS)** — when filesystem capacity / multi-region serving becomes the constraint. The `Storage` Protocol is built to make this a single-class swap.

## References

- Repo: `/home/frankyin/Desktop/work/gsfluent_pkg`
- Existing tests: `server/tests/` (12 files, baseline coverage of cells, coord_convert, frame_stream, health, library_smoke, models, recipes, runner, runs_api, schemas, sequences_import, zup_invariant)
- CI: `.github/workflows/` (extend with integration + lint + typecheck)
- systemd reference: `man systemd.service`, `man sd_notify`
- Pydantic strict mode: https://docs.pydantic.dev/latest/concepts/strict_mode/
- FastAPI `Depends` + lifespan: https://fastapi.tiangolo.com/advanced/events/

---

**End of design.** Implementation plan to be generated by the `superpowers:writing-plans` skill after user approval of this document.
