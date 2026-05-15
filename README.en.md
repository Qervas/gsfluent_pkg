# gsfluent — workbench for animated 3DGS sequences

Browser workbench for inspecting and playing back physics-simulated 3D
Gaussian Splatting sequences. Pick a sequence, scrub the timeline,
switch between point-cloud and splat rendering, orbit the camera.

Simulation runs on the server (`your-server`); the laptop is a viewer
and a gateway to the run/job API. No CUDA, no PyTorch, no Warp, no
Taichi locally — pure-Python deps.

[中文 README](README.md)

## Install

Pure pip, no conda. Pick whichever Python you have on PATH; the seven
deps below get installed there.

```bash
git clone <repo>
cd gsfluent_pkg
./setup-view.sh
```

What `setup-view.sh` installs:

```
fastapi  uvicorn  pydantic  watchfiles  plyfile  numpy  viser
```

Plus `pip install -e ./server` so the `gsfluent` console script lands
on PATH. To target a non-default Python:

```bash
PYTHON=python3.11 ./setup-view.sh
```

To build the SPA for production (HMR-less mode):

```bash
cd frontend && npm install && npm run build
cp -r frontend/dist/* server/gsfluent/static/
```

## Run

Two machines, three commands.

**On the server (one-time):**

```bash
ssh <server-host>
cd gsfluent_pkg && ./setup-server.sh
```

**On the server (each session):**

```bash
./run-server.sh                    # backend on :8080
```

**On the laptop (each session):**

```bash
SERVER_SSH=<server-host> ./run-laptop.sh
```

`SERVER_SSH` is the SSH alias from your `~/.ssh/config`. `run-laptop.sh`
opens the tunnel for you (`-L 8080:localhost:8080`), starts viser +
sync_daemon + Points WS, then opens the workbench in your browser. The
tunnel is torn down with everything else on Ctrl-C.

Got an existing tunnel, or backend on the LAN? Skip `SERVER_SSH` and
set `GSFLUENT_SERVER` directly:

```bash
GSFLUENT_SERVER=http://server.lan:8080 ./run-laptop.sh
```

This brings up two cooperating servers:

```
┌──────────────────┐  HTTP   ┌─────────────────────┐
│ gsfluent serve   │ ←─────→ │  React workbench    │
│ :8080            │         │  (browser)          │
│  - SPA + REST    │         │  ┌───────────────┐  │
│  - /api/stream   │         │  │ iframe :8091  │ ←─┐
│    (WS, Points)  │         │  │  viser splat  │   │  /set, /camera
└──────────────────┘         │  └───────────────┘   │  → :8092
                             └─────────────────────┘
                                                      │
                             ┌────────────────────────┴─┐
                             │  tools/viser_headless.py │
                             │  viser :8091, ctl :8092  │
                             └──────────────────────────┘
```

Open `http://localhost:8080` (or `:5173` in dev mode). Outliner picks
a sequence; the playback bar scrubs frames; the render-mode toggle
switches between **Points** (R3F + int16-quantized xyz over WebSocket)
and **Splats** (viser iframe driven by the control API).

## Where data lives

```
work/
├── library/
│   └── sequences/<name>/
│       ├── frames/frame_NNNN.ply   # fused 3DGS frames (Z-up at rest)
│       ├── frames.bin              # GSSQ-packed int16 xyz (Points mode)
│       ├── manifest.json
│       └── _meta.json
└── cache/
    └── viser/<name>.npz            # Splats-mode playback cache
```

A sequence is the canonical artifact: fused per-frame splat plies plus
optional packed-binary and viser cache files. Two ways to populate it:

1. **From the server** — `rsync your-server:.../sequences/<name>/`
   into `work/library/sequences/`, then
   `python tools/batch_convert_to_npz.py` to build the viser cache.
2. **From a local sim_*.ply set** — `python tools/fuse_to_full_ply.py`
   on rsynced sim outputs (this needs only numpy + plyfile + optional
   torch for `--knn_rotation`).

The server's `runner.py` auto-runs `batch_convert_to_npz.py` after each
sim completes (idempotent — only rebuilds stale .npz). The laptop's
`sync_daemon` then mirrors the new .npz over.

## Render modes

| Mode | Renderer | Transport | What it's good for |
|---|---|---|---|
| **Points** | R3F (three.js) | `/api/stream` WS, int16 xyz via `PackedReader` | Lightweight inspection; works without the viser cache |
| **Splats** | viser iframe | `POST /set`, `POST /camera` to `:8092` | High-quality splat rendering for review and demos |

Both modes share `currentFrameIdx` and `simRunName` from the Zustand
store, so the timeline and outliner drive whichever renderer is
active. Toggling modes does not reset playback state.

## Recipes (sim parameters)

`tools/recipes/*.json` defines material + boundary + integration
parameters consumed by the server-side sim. The schema matches what
`gs_simulation_building.py` on `your-server` expects.

```bash
ls tools/recipes/
# cluster_6_15_smash.json  demolition.json  jelly.json  earthquake.json  ...
cp tools/recipes/jelly.json tools/recipes/my_recipe.json
```

Editing a recipe locally and submitting a run goes through the server —
see `docs/ARCHITECTURE.md` for the sim-submission flow (work in
progress).

## Layout

```
gsfluent_pkg/
├── README.md                README.en.md      # bilingual
├── setup-view.sh / run-laptop.sh         # laptop side
├── setup-server.sh / run-server.sh       # server side
├── docs/ARCHITECTURE.md     # deeper architecture notes
├── server/                  # FastAPI + SPA serving
│   └── gsfluent/
│       ├── api/             # /api/recipes, /api/runs, /api/sequences, /api/stream
│       └── core/            # library scanning, manifest, runner, frame_stream
├── frontend/                # React + Vite + R3F SPA
│   └── src/components/viewport/
│       ├── SplatScene.tsx       # Points mode (R3F)
│       └── ViserSplatScene.tsx  # Splats mode (viser iframe)
├── tools/
│   ├── viser_headless.py        # viser + control API (Splats mode backend)
│   ├── batch_convert_to_npz.py  # builds work/cache/viser/*.npz
│   ├── sequence_to_viser_npz.py # one-sequence converter
│   ├── fuse_to_full_ply.py      # sim_*.ply + ref 3DGS → frame_*.ply
│   ├── pack_sequence.py         # frame_*.ply → frames.bin (GSSQ int16)
│   ├── migrate_to_library.py    # legacy → work/library/ layout
│   ├── vkgs_play.py             # launch the vkgs fork against a sequence
│   └── recipes/                 # JSON recipe presets
└── work/                    # runtime data (library, cache, uploads)
```

## Credits

- 3D Gaussian Splatting: Kerbl et al. 2023
- MPM physics: NVIDIA Warp + Taichi (server-side)
- Splat playback: viser
- Workbench: React + Vite + React Three Fiber
