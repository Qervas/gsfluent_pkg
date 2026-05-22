# Changelog

All notable user-visible changes to gsfluent. Follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased] — Backend bulletproofing slice

Customer-facing hardening sprint. Pipeline shape (recipe → sim → fuse →
pack → cache → stream → render) is unchanged. Six new Protocols + a
composition root sit behind the API. systemd replaces the previous
shell supervisor. Streaming cache becomes ETag-honest.

### Added

- **Six-Protocol component layout.** `RunManager`, `SimulationEngine`,
  `Fuser`, `CacheCodec`, `Storage`, `EventEmitter` Protocols under
  `server/gsfluent/protocols/`. Concrete impls wired in
  `server/gsfluent/composition.py`. Each Protocol has a conformance
  test suite under `server/tests/protocols/test_*_protocol.py`.
- **Recipe caps.** Three env-var-configurable caps applied at the API
  boundary before any subprocess or state file:
  - `GSFLUENT_MAX_PARTICLE_COUNT` (default `500000`)
  - `GSFLUENT_MAX_WALL_TIME_SEC` (default `3600`)
  - `GSFLUENT_MAX_RECIPE_BYTES` (default `16384`)
  Violations return HTTP 422 with a structured error envelope including
  `trace_id`.
- **Structured JSON events.** Every state transition emits one JSON
  event through `EventEmitter` (`StdlibJSONEmitter` writes to stdout →
  journald). Per-run events auto-attach `run_id` and `sequence_name`
  via `EventEmitter.child(...)`. Lifecycle chain:
  `run.queued` → `run.started` → `run.preflight_ok` → `sim.started` →
  `sim.completed` → `run.simmed` → `run.fused` → `run.packed` →
  `run.completed`.
- **Cancellation that actually cancels.** Sim subprocesses spawn in a
  fresh process group; `POST /api/runs/<id>/cancel` sends
  `SIGTERM` to the entire PG and escalates to `SIGKILL` after 30s.
- **Wall-time enforcement.** Sim runs are wrapped in
  `asyncio.wait_for(..., timeout=wall_time_cap)`; overruns trigger the
  same PG-kill escalation.
- **Run-state persistence + boot recovery.** Every run owns
  `work/_state/runs/<id>.json` written atomically (temp file + rename).
  On startup, `RunManager.recover_on_boot()` reconciles with live PIDs
  (cross-checked against `/proc/<pid>/stat` start-time to avoid PID
  reuse). In-flight runs without a live PID are marked `interrupted`
  with `error.kind = internal.backend_restarted`; runs are never
  auto-resumed.
- **systemd supervision.** `deploy/gsfluent-backend.service` (production)
  and `deploy/gsfluent-backend.dev.service` (dev box) + `deploy/README.md`.
  The unit declares `WatchdogSec=30s`; the backend pings via
  `sd_notify("WATCHDOG=1")` from an async heartbeat (suppressed when
  the health probe says the backend is `down`, so a stuck-but-dishonest
  ping never silences systemd).
- **Streaming cache that respects ETags.**
  - Server: `api/sequences.py` returns `.gsq` with
    `Cache-Control: public, immutable, max-age=31536000` and
    `ETag: "<size>-<mtime>"`. `If-None-Match` matching returns 304.
  - Client: `viser_headless._sync_cell_gsq_streaming` HEAD-checks
    before download (cache hit → skip) and resumes with `Range: bytes=<n>-`
    from `.partial` (cache miss with prior interrupted download).
    Emits `cell.cache.hit` and `cell.cache.resuming` events.
- **Real health signals.** `GET /api/health` now returns GPU
  reachability, `sim_home` existence, disk-free percentage, and last
  successful run timestamp (Pydantic `HealthResponse` contract).
- **Typed error taxonomy.** All sim/fuse/codec/storage errors carry a
  dotted `kind` and a `trace_id`. The sim-stderr classifier
  (`core/sim_engines/mpm.py:classify_stderr` driven by
  `mpm_error_patterns.yaml`) maps known patterns (CUDA OOM, CFL
  violation, illegal memory access, NaN/Inf positions) to
  `SimGpuOomError` / `SimUnstableRecipeError` / `SimCrashedError`.
  Patterns are tunable post-launch by editing the YAML.

### Changed

- **`server/tools/run_sim.sh`** slimmed from 197 lines to ~20-line
  conda-activate shim. Sim orchestration (PG-spawn, wall-time
  enforcement, error classification) moved into
  `core/sim_engines/mpm.py`.
- **`server/tools/fuse_to_full_ply.py`** is now a CLI wrapper around
  `core/fusers/knn_kabsch.py:KNNKabschFuser`. Behavior unchanged for
  ssh-driven one-shot runs.
- **`server/tools/pack_splats.py`** is now a CLI wrapper around
  `core/codecs/gsq.py:GSQCodec`. `.gsq` wire format unchanged.
- **`server/gsfluent/core/runner.py`** now wraps
  `core/run_manager.py:AsyncioRunManager` which conforms to
  `protocols.RunManager`. Legacy `core.runner` module retired
  (3 tests skipped with rationale; coverage migrated to
  `tests/runs/test_asyncio_run_manager.py` + the integration suite).
- **`server/gsfluent/core/library.py`** filesystem operations extracted
  to `storage/filesystem.py:FilesystemStorage` conforming to
  `protocols.Storage`. Library business logic stays in `library.py`.
- **`api/runs.py`** moved from permissive validation to strict
  Pydantic + cap-check before persistence.
- **`api/sequences.py`** now uses `Storage.stat()` + `Storage.get_range()`
  instead of direct filesystem calls.
- **Client viser_headless rename:** `npz_root` → `cache_root` and
  `--npz_dir` → `--cache-dir` for clarity. The old name is accepted as
  a deprecated alias for one release.
- **Env-var rename:** `GSFLUENT_NPZ_REBUILD` → `GSFLUENT_CACHE_REBUILD`.
  The old name is accepted as a deprecated alias for one release.

### Removed

- **`server/supervise.sh`** (83-line shell supervisor) — replaced by
  systemd. See [`deploy/README.md`](deploy/README.md) for the install
  steps.

### Fixed

- Cancellation that previously left zombie sim processes now reliably
  kills the entire process group, including child taichi/warp workers.
- Backend restarts that previously left runs stuck in `running` state
  now mark them `interrupted` so the API surface reflects reality.
- `test_library_smoke.py` no longer fails on dev boxes whose library
  contains stale UUID-named sequence directories with only `_meta.json`
  and no frames (skips when no library sequence has both meta + frames).

### Deprecated

- `--npz_dir` / `npz_root` (viser_headless): use `--cache-dir` /
  `cache_root`. One-release transition window.
- `GSFLUENT_NPZ_REBUILD`: use `GSFLUENT_CACHE_REBUILD`. One-release
  transition window.

### Security

- Recipe boundary now rejects oversized payloads (DoS guard via
  `GSFLUENT_MAX_RECIPE_BYTES`). Note: the slice deliberately defers
  full auth + multi-tenancy + container sandboxing per spec
  Non-goals; customers still reach the backend via the existing
  out-of-band whitelist.

### Migration notes

- Stop the old supervisor on the GPU host: `bash server/supervise.sh stop`
  (one last time before the script is gone).
- Install the systemd unit per `deploy/README.md`:
  ```bash
  sudo systemctl link "$(pwd)/deploy/gsfluent-backend.service"
  sudo systemctl enable --now gsfluent-backend.service
  ```
- Set the cap env-vars in the systemd `Environment=` section (or in
  the `EnvironmentFile=` the unit points at). The defaults are safe;
  override only if your workload needs different limits.
- Existing `.gsq` cache files are forward-compatible — the streaming
  cache will start serving ETags from them on the next read.

---
