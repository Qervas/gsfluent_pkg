# gsfluent_pkg — Architecture

Status: 2026-05-20. Describes the system as deployed today: a single v1
backend on your-server, a laptop-local SPA + viser pair on each teammate's
machine, and a public NAT port linking the two.

---

## What the system is

A pipeline from a trained 3DGS scene + a physics recipe to an animated
3DGS sequence that scrubs interactively in the browser. The MPM solver
is server-side (GPU); the viewer (viser splat renderer + React SPA) is
laptop-side.

```
3DGS reference (.ply, trained)         physics recipe (json)
            │                                  │
            ▼                                  ▼
┌────────────────────────────────────────────────────────────┐
│  SIM     server (your-server GPU host)                           │
│  GaussianFluent / Warp 0.10 / Taichi 1.5 / A100            │
│  MPM solver → sim_*.ply (200k particles, Z-up)             │
└─────────────────────────┬──────────────────────────────────┘
                          ▼
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
│  (laptop-local)     │    │  (sibling repo,    │
│  • Points (R3F+WS)  │    │   not in use today)│
│  • Splats (viser)   │    │                    │
│  • Z-up, raw frames │    │                    │
└─────────────────────┘    └────────────────────┘
```

---

## Components and responsibilities

### `server/gsfluent/` — v1 backend

- FastAPI process on `0.0.0.0:7869`, reached publicly as
  `your-backend:port` via NAT. The wire contract for every route is in
  [`docs/API.md`](API.md) / [`docs/API.zh.md`](API.zh.md).
- Mounts REST routes under `/api/*`: `recipes`, `models`, `runs`,
  `sequences`, `schemas`, plus the per-frame xyz WebSocket at
  `/api/stream`. The SPA static fallback is mounted last so `/api/*`
  always wins on prefix conflict.
- **Owns**: the library API surface, the WS frame pump, the runner that
  spawns sim subprocesses.
- **Does NOT own**: viewer rendering (laptop-side), per-cell viser
  caches (built by `tools/sequence_to_viser_npz.py`, served by
  `tools/viser_headless.py`).
- Process management: `tools/supervise.sh up|stop|status` — a small
  shell supervisor (no systemd, no docker) that respawns the backend
  and viser_headless if they die.

### `tools/` — pipeline glue

- `fuse_to_full_ply.py` — sim_*.ply + reference 3DGS → frame_*.ply.
  K-NN skinning and per-frame Kabsch rotation behind flags.
- `pack_sequence.py` — frame_*.ply → `frames.bin` (GSSQ int16-quantized
  xyz). Read by `core/frame_stream.py:PackedReader` for the Points-mode
  WS stream. ~30× smaller on disk than per-frame plies.
- `sequence_to_viser_npz.py`, `batch_convert_to_npz.py` — build the
  per-sequence `.npz` files in `work/cache/viser/` that
  `viser_headless.py` mmaps for Splats-mode playback.
- `viser_headless.py` — viser splat renderer on `:8091` + FastAPI
  control sidecar on `:8092`. The SPA drives sequence / frame / camera
  via the control API; viser handles WebGL rendering.
- `vkgs_play.py` — viewer-specific adapter for the vkgs native renderer
  (Z-up→Y-up rotation, launch wrapper). Operates on a copy in
  `work/cache/vkgs_yup/`; never mutates library frames.
- `recipes/*.json` — physics recipes consumed by the server-side sim.
- `supervise.sh` — your-server process manager described above.

### `frontend/` — React + Vite + R3F workbench (laptop-local)

- Built once via `vite build` and served by `vite preview` on the
  laptop. Read-only against the backend over `/api/*`.
- **Build-time env** (frontend/.env.production):
  - `VITE_VISER_URL=http://127.0.0.1:8091/` — splat WS endpoint
    (trailing slash matters; viser strips it to build its WS URL)
  - `VITE_VISER_CONTROL_URL=http://127.0.0.1:8092` — viser control
    sidecar
  - `VITE_BACKEND_URL=` — left empty so `/api/*` flows through the
    vite preview proxy; set to a full URL only when shipping the
    bundle to a static host without a preview server.
- **Points mode** (`SplatScene.tsx`): R3F renders `THREE.Points` driven
  by per-frame xyz over `/api/stream`. Static attrs (cov, rgb, opacity)
  ship in frame 0; subsequent frames are int16-quantized xyz only.
- **Splats mode** (`ViserSplatScene.tsx`): iframes `:8091`. On
  `simRunName` or `currentFrameIdx` change, POSTs to `:8092/set`. On
  mode toggles, POSTs to `:8092/camera` to keep the viewpoint in sync
  with Points mode's OrbitControls.

### `vk_gaussian_splatting/` (sibling repo)

- Native Vulkan splat renderer at `~/Desktop/work/vk_gaussian_splatting/`,
  with a `--frames_dir` animation patch (236 fps validated).
- **Not currently in use** — viser is the renderer for the laptop SPA.
  Kept available for native-playback demos.

---

## Invariants — break these and the system breaks

1. **All library sequences are Z-up.** `_meta.json:coord_convention = "z-up"` is the only valid value.
2. **`work/library/sequences/<name>/` is canonical, not a cache.** No suffixes like `_yup_for_vkgs/`, no rotated copies. Caches live in `work/cache/`.
3. **Every sequence has a `_meta.json`.** Fuse writes it; the library API enforces it. Sequences without one are invalid and the API rejects them.
4. **Fuse output never gets mutated after writing.** No `hide_static_splats`-style post-process on frame plys. Want to change the output? Re-fuse.
5. **Viewer caches are derived artifacts.** They live in `work/cache/<viewer>/...` (NOT in `sequences/`). They can be deleted at any time and re-derived from sources.
6. **Sim runs on your-server.** The laptop has no torch / warp / taichi / CUDA. Anything that requires those goes through the backend on your-server.

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
    "host": "<gpu-server>",
    "path": "<sim-output>/simulation_ply",
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

## Runtime topology

```
┌─────── Teammate laptop ─────────────────────────────────────┐
│                                                             │
│  Browser  ───────► http://localhost:5173/                   │
│                                                             │
│  vite preview :5173                                         │
│    proxy /api/*       ─────────────► your-server :24701           │
│    proxy /api/stream (WS) ─────────► your-server :24701           │
│    static /           ─────────────► frontend/dist/         │
│                                                             │
│  viser_headless                                             │
│    127.0.0.1:8091 (splat WS)    ◄── iframe in SPA           │
│    127.0.0.1:8092 (control API) ◄── fetch from SPA          │
│    mmap → work/cache/viser/*.npz                            │
│                                                             │
└────────────────────────────┬────────────────────────────────┘
                             │ HTTP (/api/*, /api/stream WS)
                             ▼  (public NAT  24701 → 7869)
┌─────── your-server GPU host ──────────────────────────────────────┐
│                                                             │
│  v1 backend  0.0.0.0:7869                                   │
│    /api/{recipes,models,runs,sequences,schemas}             │
│    /api/stream (WS, per-frame xyz)                          │
│    runner → spawns gs_simulation_building.py                │
│                                                             │
│  GaussianFluent sim stack (torch + warp + taichi, A100)     │
│  work/library/sequences/<run>/  ← canonical PLY frames      │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

Launch on your-server: `bash tools/supervise.sh up` (starts and supervises
v1 backend on `:7869` + viser_headless on loopback `:8091/:8092`).
Launch on a teammate's laptop: `cd frontend && npm start` (runs
`scripts/_start.sh`, which brings up viser_headless on the laptop's
own loopback + vite preview proxying `/api/*` to your-server).

The splat WebSocket stays on the laptop's loopback in both topologies —
there is no high-bandwidth WAN hop for splat playback.

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

Active research in Phase 18 (R_pi, 12–16 week scope) on top of the
current backend:

- **Implicit MPM** — replace the current explicit Warp solver with an
  implicit time integrator for stability under stiff materials.
- **CK-MPM** — compatible-kernel MPM port to GaussianFluent for fracture
  scenarios.
- **CDM damage** — continuum damage mechanics layered on the implicit
  solver to drive crack initiation + propagation.

These are sim-side changes; the API contract above is expected to stay
stable. New recipe fields (damage params, implicit-step config) will
land additively in `tools/recipes/*.json`.

---

## What this is NOT

- A monorepo refactor.
- A new framework / DI / plugin system.
- A web-vs-vkgs unification. The two viewers have different goals:
  - Web = scrub + inspect + share (workbench-grade).
  - vkgs = 236 fps native playback (demo-grade, currently shelved).
