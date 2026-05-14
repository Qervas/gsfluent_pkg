# gsfluent_pkg — Architecture

Status: 2026-05-13. Describes the system as it stands today. The
"scattered" state of the May-12 draft has been cleaned up — local sim
code is gone, viewer caches are out of the library tree, K-NN skinning
has landed, and the laptop is pure-Python.

---

## What the system is

A pipeline from a 3DGS scene + a physics recipe to an animated 3DGS
sequence playable in two viewers (browser via the React workbench,
native via the vkgs fork).

```
3DGS reference (.ply, trained)         physics recipe (json)
            │                                  │
            ▼                                  ▼
┌────────────────────────────────────────────────────────────┐
│  SIM     server / sxyin-host                               │
│  /data/yinshaoxuan/GaussianFluent/  (Warp 0.10 + A100)     │
│  MPM solver → sim_*.ply (200k particles, Z-up)             │
└─────────────────────────┬──────────────────────────────────┘
                          ▼  (rsync sim_*.ply down to laptop)
┌────────────────────────────────────────────────────────────┐
│  FUSE    tools/fuse_to_full_ply.py                         │
│  K-NN-weighted skinning, per-frame Kabsch rotation         │
│  sim_*.ply + reference 3DGS → frame_*.ply (Z-up, full SH)  │
└─────────────────────────┬──────────────────────────────────┘
                          ▼
┌────────────────────────────────────────────────────────────┐
│  LIBRARY    work/library/sequences/<name>/                 │
│  Canonical sequence storage. Z-up. _meta.json required.    │
└────────┬──────────────────────────┬────────────────────────┘
         │                          │
         ▼                          ▼
┌─────────────────────┐    ┌────────────────────┐
│  WEB                │    │  VKGS              │
│  React workbench    │    │  Native Vulkan     │
│  • Points (R3F+WS)  │    │  • 236 fps         │
│  • Splats (viser)   │    │  • Y-up adapter    │
│  • Z-up, raw frames │    │    in vkgs_play.py │
└─────────────────────┘    └────────────────────┘
```

---

## Components and responsibilities

### `server/gsfluent/` — backend service
- FastAPI process on `:8080`. Serves the React SPA + REST endpoints
  (`/api/recipes`, `/api/models`, `/api/runs`, `/api/sequences`)
  + WebSocket stream at `/api/stream` for per-frame xyz delivery.
- **Owns**: the library API surface and the WS frame pump.
- **Contract**: every sequence it returns has a valid `_meta.json`.
  No exceptions.
- **Does NOT own**: simulation execution (server-side), viewer
  rendering (client-side), per-cell viser caches (`tools/viser_headless.py`).

### `tools/` — pipeline glue
- `fuse_to_full_ply.py` — sim_*.ply + reference 3DGS → frame_*.ply.
  K-NN skinning and per-frame Kabsch rotation behind flags.
- `pack_sequence.py` — frame_*.ply → `frames.bin` (GSSQ int16-quantized
  xyz). Read by `core/frame_stream.py:PackedReader` for the Points-mode
  WS stream. ~30× smaller on disk than per-frame plies.
- `sequence_to_viser_npz.py`, `batch_convert_to_npz.py` — build the
  per-sequence `.npz` files in `work/cache/viser/` that
  `viser_headless.py` mmaps for Splats-mode playback.
- `viser_headless.py` — viser splat renderer on `:8091` + FastAPI control
  sidecar on `:8092`. React drives sequence/frame/camera via the
  control API; viser handles WebGL rendering.
- `vkgs_play.py` — viewer-specific adapter for the vkgs native renderer:
  Z-up→Y-up rotation, launch wrapper. Operates on a copy in
  `work/cache/vkgs_yup/`, never mutates library frames.
- `migrate_to_library.py` — backfills `_meta.json` on legacy
  sequence directories.
- `recipes/*.json` — physics recipes consumed by the server-side sim.

### `frontend/` — React + Vite + R3F workbench
- Reads the library API; renders Points and Splats modes.
- **Owns**: web-side viewer concerns only. Cannot mutate library data.
- **Points mode** (`SplatScene.tsx`): R3F renders a `THREE.Points`
  cloud driven by per-frame xyz arriving over `/api/stream`. Static
  attrs (cov, rgb, opacity) ship in frame 0; subsequent frames are
  int16-quantized xyz only.
- **Splats mode** (`ViserSplatScene.tsx`): iframes `:8091`. On
  `simRunName` or `currentFrameIdx` change, POSTs to `:8092/set`. On
  mode-toggle (later: Pass B), POSTs to `:8092/camera` to sync the
  viewpoint with Points mode's OrbitControls.

### `vk_gaussian_splatting/` (sibling repo, `~/Desktop/work/vk_gaussian_splatting/`)
- Native Vulkan splat renderer with our `--frames_dir` animation patch.
- 236 fps validated for animated 3DGS playback.
- Y-up internally; `vkgs_play.py` produces the rotated copy at launch.

---

## Invariants — break these and the system breaks

1. **All library sequences are Z-up.** `_meta.json:coord_convention = "z-up"` is the only valid value.
2. **`work/library/sequences/<name>/` is canonical, not a cache.** No suffixes like `_yup_for_vkgs/`, no rotated copies. Caches live in `work/cache/`.
3. **Every sequence has a `_meta.json`.** Fuse writes it; the library API enforces it. Sequences without one are invalid and the API rejects them.
4. **Fuse output never gets mutated after writing.** No `hide_static_splats`-style post-process on frame plys. Want to change the output? Re-fuse.
5. **Viewer caches are derived artifacts.** They live in `work/cache/<viewer>/...` (NOT in `sequences/`). They can be deleted at any time and re-derived from sources.
6. **Sim runs on the server.** The laptop has no torch / warp / taichi / CUDA. Anything that requires those goes through SSH to `sxyin-host`.

---

## Data contracts

### Sequence directory layout

```
work/library/sequences/<name>/
  ├── _meta.json          REQUIRED — see schema below
  ├── _manifest.json      OPTIONAL — sim provenance (for replay)
  ├── frames.bin          OPTIONAL — packed int16 xyz (Points-mode fast path)
  └── frames/
      ├── frame_0000.ply
      ├── frame_0001.ply
      └── ...
```

### `_meta.json` schema (v1)

```json
{
  "name": "<seq-name>",
  "kind": "sequence",
  "source": "sim" | "import",
  "model_ref": "<reference-3dgs-name>",
  "frame_count": 151,
  "fps_hint": 24,
  "n_splats": 683741,
  "coord_convention": "z-up",
  "first_frame_full": true,
  "created_at": "<ISO-8601 UTC>"
}
```

### `_manifest.json` (provenance, optional but recommended)

```json
{
  "sim": {
    "host": "sxyin-host",
    "path": "/data/yinshaoxuan/.../simulation_ply",
    "git_sha": "<commit>",
    "recipe_json": "<inline copy of config>"
  },
  "fuse": {
    "tool_version": "fuse_to_full_ply.py@<git_sha>",
    "args": ["--reference_ply=...", "--knn=8", "--knn_rotation", ...]
  }
}
```

### Cache directory layout

```
work/cache/
  ├── viser/<name>.npz           ← Splats-mode playback
  └── vkgs_yup/<name>/frames/    ← rotated copies for the vkgs native viewer
```

Caches are **never** returned by the library API and **never** indexed
as sequences. The viewer wrappers know how to derive them.

---

## Runtime topology (laptop side)

```
┌─────────────────────┐  REST + WS  ┌────────────────────┐
│ gsfluent serve      │ ←─────────→ │ React SPA (browser)│
│ :8080 (FastAPI)     │             │                    │
│  - SPA static       │             │  Points mode:      │
│  - /api/sequences   │  ws://      │   useStreamClient  │
│  - /api/recipes     │  /api/stream│   → PackedReader   │
│  - /api/runs        │             │   → R3F SplatScene │
│  - /api/stream WS   │             │                    │
└─────────────────────┘             │  Splats mode:      │
                                    │   iframe :8091  ←──┐
┌─────────────────────┐             │   POST :8092/set   │
│ viser_headless.py   │ ←───────────┴────────────────────┘
│  viser :8091        │
│  control :8092      │  mmap → work/cache/viser/*.npz
└─────────────────────┘
```

Server-side service starts via `./run-server.sh`. Laptop-side
(viser + sync daemon + Points WS) starts via `./run-laptop.sh`,
which shares a single cleanup trap on SIGINT.

---

## Where to put new things

| Adding... | Goes in... |
|---|---|
| A new sim recipe | `tools/recipes/<name>.json`; consumed server-side |
| A new fuse strategy (K-NN variant, MLS, ...) | `tools/fuse_to_full_ply.py` as a flag, OR a sibling `tools/fuse_<name>.py` |
| A viewer-specific transform | The viewer's wrapper (`vkgs_play.py` for vkgs; `viser_headless.py` for splat). NEVER mutate library frames. |
| A new backend endpoint | `server/gsfluent/api/<route>.py` |
| A web-side renderer mode | `frontend/src/components/viewport/<NewMode>.tsx` |
| A one-shot migration | `tools/_oneshot/<date>_<purpose>.py`, not flat in `tools/` |

---

## What's next

Two open slices, both planned but not yet built:

### Sim submission protocol (laptop ↔ server)
- `runner.py` (server-side) now spawns `tools/run_sim.sh`, which
  orchestrates `gs_simulation_building.py` + `fuse_to_full_ply.py`
  and triggers `batch_convert_to_npz.py` on completion. The wrapper
  paths are configurable via `GSFLUENT_SIM_HOME`, `GSFLUENT_SIM_PYTHON`,
  `GSFLUENT_SIM_ENV`, `GSFLUENT_SIM_SCRIPT_RUNNER` so each deployment
  adapts without code changes.
- The `POST /api/runs` API contract is unchanged from the pre-split
  shape — the React side submits the same recipe + model + particles
  payload; the server just spawns locally instead of trying SSH.

### Per-frame covariance in Splats mode
- Today `sequence_to_viser_npz.py` stores frame-0 covariance only; per-frame
  rotation is in the fused plies (via `--knn_rotation`) but not in the npz.
- During motion, splat ellipsoids smear because Σ' = F·Σ·F^T isn't applied.
- Plan: extend the npz schema with a per-frame quaternion array; viser_headless's
  push loop reconstructs Σ' on each frame before writing to `splat.covariances`.
- ~1.6× xyz size on disk; cheap CPU on push.

---

## What this is NOT

- A monorepo refactor.
- A new framework / DI / plugin system.
- A web-vs-vkgs unification. The two viewers have different goals:
  - Web = scrub + inspect + share via URL (workbench-grade).
  - vkgs = 236 fps native playback (demo-grade).
