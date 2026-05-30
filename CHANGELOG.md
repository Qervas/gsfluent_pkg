# Changelog

All notable user-visible changes to gsfluent. Follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased] — Structured recipe composer + 5 destruction scenarios

Recipes are now **composed** from three orthogonal inputs —
**MATERIAL × SCENARIO × BUILDING** — instead of being hand-edited. The
frontend's Composer panel is the primary authoring surface; the flat
parameter panels become advanced overrides on top of whatever it produces.
Ships five curated destruction scenarios, each verified on rendered video.

See [docs/API.md](docs/API.md) for the composer endpoints and
`server/gsfluent/authoring/` for the source of truth.

### Added

- **Composer authoring layer** (`server/gsfluent/authoring/`):
  `materials.py`, `scenarios.py`, `buildings.py`, `compose.py`. A scenario
  speaks in building-relative anchors (base/mid/top, ±x/±y, fractions) and
  times in seconds; `compose(material, scenario, building)` resolves those
  against the building's bbox into a flat sim recipe (CFL-derived
  `substep_dt`, auto base-pin, grid-containment + energy-family safety
  ceilings). The flat recipe is a deterministic build artifact, never
  hand-authored.
- **`GET /api/compose/library`** — lists scenarios/materials/buildings for
  the picker (read dynamically from the authoring modules — new scenarios
  appear with no API change).
- **`POST /api/compose`** — `{material, scenario, building}` → `recipe_data`
  (the object you forward to `POST /api/runs`). Over-ceiling / unknown picks
  return a 422 with a human reason; nothing is silently clamped.
- **Five curated scenarios** (recommended material `watermelon`, all
  video-verified): `earthquake` (base shake → rubble), `wrecking`
  (side impact → shear), `topple` (drag the top along the thin axis →
  domino fall), `burst` (4 core slabs blow outward → explode), `demolish`
  (two opposing base-cut impacts → crashes down + breaks apart).
- **Composer UI** (`ComposerPanel.tsx`): three dropdowns (scenario /
  material / building) driving `POST /api/compose`. Self-seeds a verified
  default, snaps material to each scenario's recommendation, and warns on a
  mismatch. `api.compose.{library,run}` + types added.
- **CLI** `server/tools/compose_recipe.py` (`--material --scenario
  --building`) for composing a recipe outside the UI.

### Changed

- **Composer is the primary Properties surface** (open by default). The
  flat panels (Material / Solver / Forces / Sim setup / Boundary) are
  demoted to collapsed *advanced overrides*, shown only once a recipe is
  active. `MaterialPanel` is now fine-tune-only (no material dropdown).
- **`cluster_6_15` bbox corrected.** The cube-frame bbox in `buildings.py`
  was a guess 2–3.6× too wide; re-measured by replaying the sim's own
  `transform2origin` against the 683k-point scan. The building is a tall
  slender slab (z-span 1.0, x 0.60, y 0.36) — which is why `topple` works.
  An over-wide bbox sizes every lateral boundary condition wrong, so this
  matters for *every* composed scenario.
- **Boundary-condition schema** (`schemas/boundary.py`) rewritten to match
  the solver (was stale: `surface_type`/`center`; now `surface`/`point`/
  `reset`, plus particle_impulse + enforce_particle_translation).

### Fixed

- **Composed runs now actually start.** The composer emitted `sim_area` in
  world coords but tagged it `sim_area_frame: "model"`, so the runner
  double-translated it into empty space → 422 ("sim_area does not overlap
  the model bbox") → the run silently halted at 0% with no logs and no
  sequence. The composer now emits a model-local `sim_area`
  (`[-30,30,-30,30,-30,30]` + `frame:"model"`), which the runner translates
  against whatever model you run — portable across original / pruned /
  re-uploaded scans, and orientation-agnostic (the library holds both Z-up
  and Y-up variants of the scan).
- **Composed recipes are runnable from the UI.** Composer recipe names use
  a `·` separator (`earthquake·watermelon`), which is outside the allowed
  run-name charset — the run name failed `CellRef` validation, and the
  pre-dispatch recipe re-fetch hit `GET /api/recipes/earthquake·watermelon`
  (a 422, since composed recipes aren't saved). Fix: `sanitizeCellName()`
  coerces the run name to `[A-Za-z0-9_.-]`, and the re-fetch is skipped for
  composed recipes (detected via `_composed_from`).
- **Failed runs surface their reason.** `RunButton` caught the 422 but
  state had already flipped to "running" (rendered first), so the button
  stuck at 0% with the cause hidden. It now flips to "error" and shows the
  backend message.

### Removed

- **`crush` and `implode` scenarios** — a forced vertical pancake is not
  achievable on this building: a downward imposed-velocity press traps the
  near-incompressible material against the floor and ejects it (CUDA crash),
  and pure gravity self-supports the tower. `demolish` (a lateral base-cut)
  delivers the "collapse + visible breaking" goal instead.
- **`blast` event kind** (composer) — the force-based `particle_impulse`
  primitive crashed 4/5 materials (`dv = force/mass`, mass ~1e-4); superseded
  by `burst`, which gets the explosion read from robust velocity puppets.
- **`CameraPanel` / `OtherPanel`** (frontend) — they edited preview-only
  fields the in-browser playback never reads (the composer fills the camera
  block).

### Notes for the team

- **No new run steps.** Pick scenario + material in the Composer, hit Run.
  The UI is data-driven, so it picks up the five scenarios automatically.
- **Match material to scenario.** Use the recommended material (watermelon)
  for the destruction scenarios; stiff materials eject under them.
- The live backend was deployed on branch `playback-raf-simplify` and the
  branch was fast-forwarded to `main`.

## [Unreleased] — Frontend: in-browser playback + run→play

Sequence playback now runs entirely client-side in one
`requestAnimationFrame` loop (no React-state frame clock), and a
freshly-run sequence builds its `.gsq` cache on demand so it plays
without manual steps.

### Added

- **Single-rAF playback.** Frame advance lives in `SplatScene`'s
  `requestAnimationFrame` loop (wall-clock accumulator, ≤1 frame/tick —
  never skips), decoding `.gsq` frames synchronously and writing them to
  Spark's `setSplat`. Pure step logic in `frontend/src/lib/playback.ts`
  (`tickPlayback`, unit-tested).
- **Run → play orchestration.** When a sequence's `.gsq` isn't packed
  yet, `SplatScene` waits for frames, POSTs `…/cache/build`, polls
  `…/cache/build-status` to done, then downloads + plays — surfacing
  waiting/building/loading states instead of a raw 404. `api.sequences`
  gains `buildCache` / `buildStatus`.
- **Model `.ply` recentering** (`frontend/src/lib/ply-recenter.ts`):
  recenters vertices to the bbox centroid before Spark packs them,
  fixing the "2 layers" collapse of float16 splat centers at large world
  coordinates (our INRIA scans sit at ~29000).

### Changed

- Playback transport simplified to **play/pause · reset · loop**
  (keyboard Space / 0 / L). The frame cursor no longer round-trips
  through React state — that two-clock pipeline was the playback stutter.

### Removed

- The frame **scrubber** and the **speed** control.
- `PlaybackDriver` (the `setTimeout` frame clock), `useGsqPlayer` + the
  decode **Web Worker** (decode is now synchronous in the rAF loop), and
  the store's `currentFrameIdx` / `speedX` / `scrubbing` fields.

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
