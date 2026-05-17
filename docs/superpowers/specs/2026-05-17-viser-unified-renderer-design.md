# Viser as the unified renderer — design

**Date:** 2026-05-17
**Status:** approved (verbal)
**Scope:** Collapse the dual-renderer architecture (three.js Points + viser Splat) into a single viser-driven render path. Rip out static→viser-cell conversion, the `_model:` prefix overload, `simKind`, the websocket Points stream, and the multiple state representations of "what's on screen."

## Why

The workbench's job is a linear flow:

1. Load a model (.ply)
2. View it
3. Configure a recipe
4. Run a simulation
5. Watch frames arrive
6. Replay the resulting sequence

Today's code dispatches that flow across two parallel render stacks:

- **Points mode**: three.js `<Canvas>` in React, fed by a websocket positions stream from `tools/local_stream.py`. State lives in `frameXyz: Map<number, Float32Array>` plus `currentFrameIdx`.
- **Splat mode**: viser_headless (Python, runs on laptop), rendered into an iframe. State lives in viser's internal cell store; React forwards `(cell, frame)` via a sidecar control API on port 8092.

Each stack has its own data shape, its own loading semantics, and its own ideas about "what's loaded right now." The dispatch logic that bridges them has accreted:

- `simRunName: string | null` overloaded with a `"_model:<modelName>"` prefix to encode "static model preview" alongside "sim run" and "replay"
- `simKind: "sim" | "replay" | "preview" | null` enum to distinguish those cases
- `tools/static_to_viser.py` (shipped 2026-05-17): converts every uploaded .ply into a duplicate npz cell so viser has something to render
- `_ensure_viser_cell` backfill in `check_hash` and the dedup short-circuit, in case a model is in the library without its viser counterpart
- Prefix-stripping in `ViserSplatScene.tsx` (in two places) to translate `_model:foo` ↔ viser cell name `foo`

This bothers the user and rightly so. The simpler design: **one renderer**.

## Architecture

### Single render surface

Every visible pixel goes through viser_headless → iframe. There is no three.js render path in production. The "Points vs Splat" toggle becomes a primitive choice within viser — same cell data, different scene primitive.

```
React → control API → viser_headless → viser → iframe
        (cell, frame, mode)
```

### Two cell kinds, one resolution path

viser_headless knows two cell-source kinds, lazily resolved on first reference:

| Cell name shape | Source | How viser loads it |
|---|---|---|
| `model:<name>` | A model in `work/library/models/<name>/` (resolved via GET `/api/models`) | Fetch the .ply via HTTP from `/api/models/file?path=…`, parse with plyfile, materialize one frame of attributes |
| `sequence:<name>` | A sequence at `work/cache/viser/<name>.npz` (local mmap) | mmap as today; multi-frame, all v2 attributes |

The `model:` / `sequence:` prefix is part of the cell name on the wire, not a frontend translation step. Cell names are unique across kinds. The prefix is set by the frontend (Phase 3 store refactor) and consumed by viser's `/set` (Phase 1). During the Phase 1 → 3 transition window, viser falls back to "try sequence cache first, then model lookup" when a bare (unprefixed) name arrives.

### State model (frontend)

The store gains one cell-pointer slot and drops three legacy ones:

```ts
// NEW
activeCell:  { kind: "model" | "sequence"; name: string } | null
renderMode:  "points" | "splat"           // (stays — moved meaning)
simState:    "idle" | "running" | "done" | "error" | "cancelled"

// REMOVED
simRunName:  string | null                // gone — replaced by activeCell
simKind:     "sim" | "replay" | "preview" | null   // gone — derivable from activeCell + simState
frameXyz:    Map<number, Float32Array>     // gone — viser owns positions
staticAttrs: StaticAttrs | null            // gone — viser owns static attributes
```

`currentFrameIdx`, `playing`, `speedX`, `loop`, `scrubbing`, `fpsHint`, `sceneScale`, etc. — kept (still used by playback UI controls), but viser is the consumer of the values, not React's render path.

### Live sim playback

When a run is in progress, frames append to `work/cache/viser/<run_name>.npz` server-side. Today the React Points renderer ticks them in via websocket frames. In the new model:

- `sync_daemon` already mirrors server's viser-cache to laptop. It nudges the local viser via `/reload?cell=<name>` when the file mtime changes.
- viser_headless's `mmap_cell` re-reads on `/reload`, the cell's `n_frames` grows.
- React's `currentFrameIdx` advances through the growing range. Buffer-aware (we already shipped this in PlaybackDriver — `frameXyz.has(nextIdx)` becomes `frameIdx < n_frames`).

Latency budget: sync_daemon polls every 10s today, too slow for "real-time" feel. Drop that to 1s during an active run (re-check after, back to 10s) — sufficient for the user to see the sim advancing without burning IO.

### Render mode toggle

Viser exposes two primitives for the same cell data:

- **Splat** (`server.scene.add_gaussian_splats`) — what we use today. Full 3DGS rendering. GPU sort.
- **Points** (`server.scene.add_point_cloud`) — primitive rasterized points. Cheaper, lower fidelity, useful as a quick preview or for low-end clients.

A new control endpoint `POST /set { mode: "points" | "splat" }` re-binds the active cell to the chosen primitive. State machine in viser_headless tracks `(cell, mode)` and rebuilds the scene node when either changes.

## Migration

### What gets deleted

| Path | Why |
|---|---|
| `tools/local_stream.py` | Websocket positions stream; viser owns rendering now |
| `tools/static_to_viser.py` | Shipped 2026-05-17; obsolete — viser parses .ply directly |
| `frontend/src/components/viewport/SplatScene.tsx` | Three.js Points renderer |
| `frontend/src/components/viewport/ViserSplatScene.tsx` | Replaced by a thinner `ViserScene.tsx` that doesn't carry `_model:` prefix logic |
| `frontend/src/lib/use-stream.ts` | Websocket client |
| `frontend/src/components/viewport/PlaybackDriver.tsx` (gutted) | Position-buffer pumping disappears; frame-tick logic keeps a small core for advancing the index |
| `frontend/src/lib/store.ts` slices: `simRunName`, `simKind`, `frameXyz`, `staticAttrs`, `pointsCamera` | Replaced by `activeCell` |
| `_ensure_viser_cell` helper in `api/models.py` | No cell to ensure — viser loads the .ply directly |
| Cell-cleanup in DELETE `/api/models/{name}` | Same |
| `frontend/src/components/viewport/RenderModeToggle.tsx` | Replaced by a viser-aware version that posts `/set?mode=…` |

### What survives, unchanged

- `viser_headless.py` (gains a `load_model` codepath + mode switching; existing sequence path untouched)
- `sync_daemon.py` (still mirrors sequence npzs; gains the 1s polling during active runs)
- `DropZone.tsx` upload pipeline (dedup, gzip, progress) — no change
- Recipes modal, Source card, run flow, override engine — no change
- `/api/models/file?path=…` endpoint — becomes load-bearing for viser
- Playback UI (PlaybackBar, scrubber, frame counter) — no change visually; just consumes from a different source

### What gets added

| Path | Purpose |
|---|---|
| `viser_headless.py` — `load_model_cell(name)` | Fetches `.ply` over HTTP from server, parses, materializes a 1-frame cell |
| `viser_headless.py` — `set_render_mode(cell, mode)` | Rebinds the scene node from splat to points or back |
| `frontend/src/components/viewport/ViserScene.tsx` | Replaces ViserSplatScene; handles both kinds + the mode toggle |
| `frontend/src/lib/use-active-cell.ts` | Hook returning the active cell + setters; replaces `simRunName` usage everywhere |

## Trade-offs

**Viser as single point of failure.** If `viser_headless` crashes, the viewport is blank. Today, Points mode still works without viser (websocket stream is independent). Mitigation:
- `run-client.sh` already restarts viser; we'll keep that.
- StatusPanel's diagnostics already pulse on viser reachability.
- Failure mode is loud and recoverable, not silent corruption.

**Points-mode visual quality on 700k+ point clouds.** Viser's `add_point_cloud` is a primitive vertex renderer. Real-world 3DGS data has spatial density variation that may not look as clean as the current three.js path. **Will be validated in Phase 1 of implementation as a sanity check before tearing out the three.js fallback.** If quality is unacceptable, we can either keep three.js Points around as a niche tool or improve viser's point rendering (sized points, depth-sorting).

**Refactor scope.** Touches ~10 frontend files and 2 server files. Most of the diff is deletes. Plan estimates ~2 days end-to-end via subagent execution.

**Real-time playback lag.** Current websocket frames arrive in tens of ms. The new model is bounded by sync_daemon's polling cadence (1s during active runs). Frame-by-frame display will be slightly chunkier — frames arrive in groups, not one-at-a-time. Acceptable trade for the simplification; if the user wants per-frame updates, sync_daemon can `inotify` watch the cell file (Linux) for instant reload.

## Implementation phases

1. **Phase 1 — Viser learns to load models + switch modes.** No frontend changes yet. Validate point-mode quality on real data. Smoke test: `viser_headless` loads cluster_6_15 directly from .ply in both modes. **(~half day, blocking gate)**

2. **Phase 2 — Rip out static→viser conversion.** Delete `static_to_viser.py`, undo the upload wiring, undo the dedup backfill, undo the prefix-stripping. Models stay as .ply only. Splat mode still works (viser now loads .ply directly per Phase 1). **(~half day)**

3. **Phase 3 — Frontend state model refactor.** Replace `simRunName`/`simKind`/`frameXyz`/`staticAttrs` with `activeCell`. Update every consumer (DropZone, SourceCard, SimulationCard, Properties, StatusPanel, RunButton, PlaybackBar, PlaybackDriver, App.tsx). **(~half day)**

4. **Phase 4 — Drop the three.js Canvas + websocket stream.** Delete `SplatScene.tsx`, `use-stream.ts`, `local_stream.py`, the in-store `frameXyz`. Viewport renders only `ViserScene.tsx`. Render-mode toggle dispatches to viser's `/set`. **(~half day)**

5. **Phase 5 — Live sim cadence + verification.** sync_daemon's poll cadence becomes 1s during active runs, 10s otherwise. End-to-end smoke: load model → pick recipe → Run → see frames arrive in viser → playback works. **(~half day)**

Total: ~2 days. Reviewable per phase via the existing subagent-driven pattern.

## Out of scope

- New 3DGS render features (depth-of-field, environment maps, etc.)
- Camera state sync between React and viser beyond what's already there
- Rendering enhancements beyond points/splat (no triangle, ellipsoid, etc. primitives)
- Mobile / low-end client optimization
- WebGPU migration

## Risks tracked

- **Phase 1 gate**: if viser's point-mode quality is materially worse than three.js's, we revisit before tearing out the fallback. Decision deferred to Phase 1 evidence.
- **inotify availability**: cross-platform considerations for sync_daemon's faster polling. macOS has fsevents, Linux has inotify, both are accessible from Python. Acceptable.
- **The .ply-fetch latency**: first time a model is selected, viser fetches the .ply (could be 150 MB). User sees a "loading" state. Acceptable for first load — subsequent loads cache in viser's process memory.
