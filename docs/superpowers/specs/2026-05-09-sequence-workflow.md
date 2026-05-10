# Sequence Workflow Design

> Goal: replace the patched-everywhere upload + playback paths with three coherent flows — import model, import sequence, play sequence — sharing one data model and one coordinate convention.

**Date:** 2026-05-09
**Status:** spec, awaiting review
**Replaces (in part):** scattered logic in `core/models.py`, `api/runs.py`, `api/stream.py`, `lib/ws.ts`, `viewport/SplatScene.tsx`, `viewport/GaussianSplatScene.tsx`

---

## Why now

The recent fix trail (cameras.json optionality, multi-file upload, splat orientation, Y-up→Z-up rotation removal) is symptomatic: components were written one at a time, each picked its own conventions, and the seams leaked. This spec defines the conventions explicitly so future work composes instead of patches.

## Scope

### In scope
- Importing a 3DGS gaussian model (.ply or directory)
- Importing a sim sequence (folder of `frame_*.ply`)
- Playing a sequence (transport controls, scrubber, speed)
- The data model + on-disk layout these share

### Out of scope (next iteration)
- Sequence editing / trimming / blending
- Compare two sequences side-by-side (workspace feature, not workflow)
- Server-side rendering / video export
- Library-wide search palette (`⌘P`-style)
- Multi-tenant / cross-machine sync

---

## Data model

Two first-class entities. Both are typed dataclasses, not loose dicts.

### `Model`
A static 3DGS scan. Source for sim runs and standalone preview.

```
work/library/models/<name>/
├── point_cloud/iteration_<N>/point_cloud.ply   # required
├── cameras.json                                # optional (only for --render_img)
└── _meta.json                                  # required, written on import
```

`_meta.json`:
```json
{
  "name": "cluster_6_15",
  "kind": "model",
  "source": "upload" | "register" | "import",
  "source_path": "/abs/path/if/registered/or/imported",
  "n_splats": 683741,
  "bbox": [[xmin, ymin, zmin], [xmax, ymax, zmax]],
  "coord_convention": "z-up",
  "imported_at": "2026-05-09T14:30:00Z"
}
```

### `Sequence`
A time-sampled .ply collection. Source: sim run we produced, or external import.

```
work/library/sequences/<name>/
├── frames/frame_0000.ply, frame_0001.ply, ...   # required, frame 0 must be full 3DGS
├── _meta.json                                   # required
└── recipe.json                                  # optional, present iff source=sim
```

`_meta.json`:
```json
{
  "name": "cluster_6_15_smash_20260509T1430",
  "kind": "sequence",
  "source": "sim" | "import",
  "source_path": "/abs/path/to/external/folder",  // if source=import (link, not copy)
  "model_ref": "cluster_6_15",                    // if source=sim
  "frame_count": 30,
  "fps_hint": 24,
  "n_splats": 683741,
  "bbox_initial": [[xmin, ymin, zmin], [xmax, ymax, zmax]],
  "coord_convention": "z-up",
  "first_frame_full": true,                       // false if all frames are xyz-only
  "created_at": "2026-05-09T14:30:00Z"
}
```

**Invariants** (validated at import / sim-completion):
- `frame_0000.ply` is a full 3DGS .ply (positions + rotations + scales + SH + opacity)
- frames 1..N can be xyz-only or full
- `coord_convention` is always `"z-up"` — Y-up data is converted at import time, never at display time
- `n_splats` is constant across frames (sims that change splat count over time aren't supported in v1)

### Coordinate system

**The workbench is Z-up. All stored data is Z-up.** Conversion happens at import time, not display time. There is no per-mode, per-path, or per-source axis swap — that's the bug pattern this spec is killing.

For external Y-up data (PhysGaussian ficus, Inria 3DGS exports), the import flow exposes an explicit "Convert from Y-up" toggle. If on, the importer rotates positions, quaternions, and normals by `Rx(-π/2)` and writes Z-up to disk. The display path never sees Y-up data.

---

## Flow 1 — Import gaussian model

### UX

Three entry points, single backend:

1. **Drag `.ply` (+ optional `cameras.json`) onto viewport.** Validates magic header, wraps into directory layout, copies to `library/models/<name>/`. If only `.ply`, generate synthetic single-camera `cameras.json` from bbox (current behavior, kept).
2. **"Open Model File…" button** in Outliner. Native file picker (single .ply or directory). Same backend as drag-drop.
3. **"Register external path"** in Outliner. Validates `point_cloud/iteration_*/point_cloud.ply` exists, writes `_meta.json` in the external dir (or in a sidecar location if read-only), registers without copy.

### Convert-from-Y-up toggle

Pre-import dialog (or import button dropdown) with one checkbox: **"Source data is Y-up (convert to Z-up)"**. Default off. When on:
- Positions rewritten: `(x, y, z) → (x, z, -y)`
- Per-gaussian rotations rewritten: Hamilton-multiply each quaternion by `Rx(-π/2)`
- Normals rewritten likewise
- Output is a fresh Z-up .ply written to `library/models/<name>/point_cloud/iteration_<N>/`
- Original .ply path stored in `_meta.json:source_path` for audit

This path reuses the math already in `tools/fuse_to_full_ply.py:208-233` — extract into `core/coord_convert.py` and call from both places.

### API (server)

```
POST /api/models                       # multipart: ply (req), cameras_json (opt), convert_y_up (opt bool)
POST /api/models/register              # json: {path, convert_y_up}
GET  /api/models                       # list, returns _meta.json contents
GET  /api/models/{name}/file/<n>.ply   # serve nth .ply (highest iter by default)
DELETE /api/models/{name}              # remove from library (refuses if registered, only unregisters)
```

The `/upload` endpoint we shipped today becomes `POST /api/models` (no semantic change, just a rename for symmetry with `/api/sequences`).

---

## Flow 2 — Import sequence

### UX

Two entry points:

1. **Drag a folder of `frame_*.ply` onto viewport.** Browser limitation: HTML drag of a folder requires `webkitGetAsEntry()` traversal — straightforward but slightly more code than file drag. Worth it for the user.
2. **"Open Sequence Folder…" button** in Outliner. Native folder picker.

### Backend

```python
def import_sequence(folder: Path, name: str | None = None, convert_y_up: bool = False) -> Sequence:
    frames = sorted(folder.glob("frame_*.ply"))
    if not frames:
        raise ImportError("no frame_*.ply files found")
    # Validate frame 0 is full 3DGS
    static_attrs = read_full_ply_attrs(frames[0])  # raises if not full
    # Build sequence dir (link, not copy)
    seq_dir = LIBRARY / "sequences" / (name or folder.name)
    seq_dir.mkdir(parents=True)
    (seq_dir / "frames").symlink_to(folder, target_is_directory=True)
    # Compute meta
    write_meta(seq_dir / "_meta.json", {
        "name": seq_dir.name,
        "kind": "sequence",
        "source": "import",
        "source_path": str(folder),
        "model_ref": None,
        "frame_count": len(frames),
        "fps_hint": 24,
        "n_splats": static_attrs.n,
        "bbox_initial": static_attrs.bbox,
        "coord_convention": "z-up",
        "first_frame_full": True,
        "created_at": now_iso(),
    })
    if convert_y_up:
        # Materialize converted frames into seq_dir/frames/ instead of symlinking
        ...  # uses core/coord_convert.py
    return Sequence.load(seq_dir)
```

**Symlink semantics:** the sequence dir contains a symlink `frames/` → source folder. If the source moves or is deleted, `_meta.json` stays but `frames/` becomes broken. Outliner shows broken sequences with a "missing source" badge and a "Re-link…" action.

If `convert_y_up=True`, we materialize converted frames inside the library (no symlink) since we have to rewrite bytes anyway. Cost: disk space, but explicit.

### API

```
POST /api/sequences                    # multipart or json: {folder_path, name?, convert_y_up?}
GET  /api/sequences                    # list, returns _meta.json contents
GET  /api/sequences/{name}/frame/<i>.ply
DELETE /api/sequences/{name}           # removes the library entry (preserves source if linked)
```

Sim runs land in this same dir via the runner — `_meta.json:source = "sim"`, `recipe.json` written alongside, no symlink (frames are produced into `frames/` directly).

---

## Flow 3 — Play sequence

### Transport UX

A persistent bar at the bottom of the viewport, visible iff a sequence is active:

```
┌──────────────────────────────────────────────────────────────┐
│  [◀◀] [▶/⏸] [▶▶]   ▰▰▰▰▰▱▱▱▱▱   12 / 30   1×▾   ↻         │
│   ←     space    →    scrubber     counter   speed  loop    │
└──────────────────────────────────────────────────────────────┘
```

| Control | Affordance | Keyboard |
|---|---|---|
| Frame step | Previous / next frame | `←` / `→` |
| Play / pause | Toggle | `Space` |
| Big step | ±10 frames | `J` / `K` (vim-style) |
| Scrubber | Drag to scrub, click to jump | (mouse only) |
| Speed | Dropdown: 0.25× / 0.5× / 1× / 2× / 4× | `,` / `.` to step down/up |
| Loop | Cycle vs stop-at-end | `L` |

`1×` = `fps_hint` from `_meta.json` (default 24). Speed multiplier scales the inter-frame delay, not the frame index step (so 4× still hits every frame, just faster).

### Live-sim semantics

When the sequence's source is a still-running sim, the scrubber's max position grows as new frames land. The counter reads `12 / 30 (sim running)` until the sim completes. This is implemented today implicitly — formalize via a `Sequence.is_live` flag pushed via WS that the transport bar reads.

### State (zustand store)

Today the store has `playing: bool` and `currentFrameIdx: number`, advanced by `useFrame` in `SplatScene`. Replace with:

```ts
type Playback = {
  sequenceName: string | null;
  frameCount: number;
  currentFrame: number;
  playing: boolean;
  speedX: 0.25 | 0.5 | 1 | 2 | 4;
  loop: boolean;
  fpsHint: number;       // from sequence meta
  isLive: boolean;       // sim still emitting
};
```

The transport bar mutates `Playback` directly; the renderers (`SplatScene`, `GaussianSplatScene`) only read.

### Display pipelines

Both `SplatScene` (points) and `GaussianSplatScene` (splat) read `Playback.currentFrame` and `frameXyz.get(currentFrame)`. The current per-pipeline frame-advance logic in `SplatScene.tsx:150-164` moves into a single `<PlaybackDriver>` component mounted at the workspace level — single source of truth for playback timing across renderers.

---

## On-disk migration

| Today | Tomorrow |
|---|---|
| `work/uploads/<name>/...` | `work/library/models/<name>/` |
| `work/runs/<run>/frame_*.ply` | `work/library/sequences/<run>/frames/frame_*.ply` |
| `work/runs/<run>/frames/frame_*.ply` (some) | (same — drops the inner `frames/` quirk) |
| `tools/recipes/*.json` | (unchanged in v1; library/recipes/ is next-iteration) |
| `_state/model_history.json` | (deleted — replaced by walking `library/models/` + reading `_meta.json`) |
| `_state/run_history.json` | (deleted — replaced by walking `library/sequences/` + reading `_meta.json`) |

A one-shot migration script (`tools/migrate_to_library.py`) walks the old layout, writes `_meta.json` files inferring fields where possible, and physically moves dirs into `library/`. Idempotent — running twice is a no-op. Run once on each developer machine + the prod server.

---

## What this kills

- The `_model:` prefix hack in run_name (model preview becomes `Sequence.kind === "model"`, no string magic)
- `applyYUpRotation` in `lib/ws.ts` (already disabled today; the spec removes the dead code path)
- The split between `frame_*.ply` at run root vs `frames/frame_*.ply` (one canonical layout)
- Two history files (`model_history.json` + `run_history.json`) — replaced by reading `_meta.json` from disk
- The per-source camera-up assignment in display code (one declared convention, no per-path overrides)
- `core_runtime/` drift vs `core/` — the runner's `GSFLUENT_HOME` defaults to `<pkg>/core_runtime` once the lib is properly packaged (separate spec; not blocked on this one)

---

## Open questions / decisions deferred

1. **Recipes as a library entity.** Treat them like models/sequences with `_meta.json`? Or keep flat JSON? Punted to next spec.
2. **Sequence sharing across users.** Symlinked imports break across machines. Future: a "publish sequence" action that writes a self-contained tarball. Not v1.
3. **Frame-by-frame attribute changes.** v1 assumes static splat count, only positions vary. Sims that add/remove particles per frame need a different sequence kind (`Sequence.kind === "dynamic_topology"`).

---

## Implementation phasing

**Phase 1: data model + library layout** (no UX changes)
- Define `Model` and `Sequence` dataclasses in `server/gsfluent/core/library.py`
- Write `tools/migrate_to_library.py`
- Update existing endpoints to read/write the new layout
- Acceptance: existing UI continues to work, on-disk structure is `library/{models,sequences}`

**Phase 2: sequence import** (new feature)
- New `POST /api/sequences` endpoint with symlink semantics
- Drag-folder-onto-viewport in `DropZone.tsx`
- Outliner "Import Sequence" button + native folder picker
- Acceptance: drop a frame folder, see it in Outliner, click to play

**Phase 3: transport bar** (UX rebuild)
- New `Playback` slice in store
- `<PlaybackBar>` component at workspace level
- `<PlaybackDriver>` replaces inline `useFrame` advance logic
- Acceptance: scrubber, speed, loop, keyboard shortcuts all work in both points and splat modes

**Phase 4: convert-from-Y-up at import**
- Extract coord-conversion math into `core/coord_convert.py`
- Add toggle to model + sequence import dialogs
- Acceptance: drop a PhysGaussian ficus .ply with toggle on, see it correctly oriented in the workbench

Each phase ends shippable. Phases 1–2 are server-heavy; 3 is frontend-heavy; 4 is a small surgical pass.

---

## Approval

Review this. If anything's wrong or missing, say so before we plan. After sign-off:

1. Spec gets a writing-plans pass to break each phase into tasks
2. Implementation proceeds phase-by-phase, each landing shippable

Open questions to resolve before planning: none required — defaults stated above.
