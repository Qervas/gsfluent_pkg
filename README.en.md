# gsfluent — workbench for animated 3DGS sequences

Browser workbench for inspecting and playing back physics-simulated 3D
Gaussian Splatting sequences. Pick a sequence, scrub the timeline,
switch between point-cloud and splat rendering, orbit the camera.

Simulation runs on the server (`sxyin-host`); the laptop is a viewer
and a gateway to the run/job API. No CUDA, no PyTorch, no Warp, no
Taichi locally — pure-Python deps.

[中文 README](README.md)

## Architecture: strong frontend/backend split

| | server (GPU box) | client (your machine) |
|---|---|---|
| code | `server/` (FastAPI + sim runner) | `frontend/` (React SPA) + `tools/` (viser, sync, Points WS) |
| install | `./setup-server.sh` | `./setup-client.sh` |
| run | `./run-server.sh` | `./run-client.sh` |
| python env | `server/.venv` (uv) — pure API deps | same lockfile + `[client]` extras (viser, numpy) |
| node | not needed | required (Vite build) |

Python deps are managed by [uv](https://docs.astral.sh/uv/) with
`server/uv.lock` checked in — every install resolves to the exact
same versions. Recipients install uv once
(`curl -LsSf https://astral.sh/uv/install.sh | sh`); the setup
scripts handle the rest.

## Install + run

**Server (one-time):**

```bash
ssh <server-host>
cd gsfluent_pkg && ./setup-server.sh
```

**Server (each session):**

```bash
./run-server.sh                    # API on :8080
```

**Client (one-time):**

```bash
cd gsfluent_pkg && ./setup-client.sh
```

**Client (each session):**

```bash
SERVER_SSH=<server-host> ./run-client.sh
```

`run-client.sh` opens the SSH tunnel for you
(`-L 8080:localhost:8080`), serves the SPA via `vite preview` on
`:4173`, starts viser + sync_daemon + Points WS, and opens the
workbench in your browser. Ctrl-C tears the whole stack down.

Existing tunnel or LAN-reachable server? Skip `SERVER_SSH`:

```bash
GSFLUENT_SERVER=http://server.lan:8080 ./run-client.sh
```

This brings up two cooperating servers:

```
   SERVER (run-server.sh)             CLIENT (run-client.sh)

  ┌──────────────────┐               ┌──────────────────────────┐
  │ gsfluent serve   │   /api  HTTP  │ vite preview  :4173      │
  │ :8080            │ ◀────────────▶│  (serves frontend/dist/) │
  │  - REST + /api   │   over SSH    │                          │
  │  - /api/stream   │    tunnel     │ React workbench in       │
  │    (WS, Points)  │               │  browser ┌─────────────┐ │
  │                  │               │          │ iframe :8091│◀┐
  │ runner.py spawns │               │          │ viser splat │ │
  │ MPM sims         │               │          └─────────────┘ │
  └──────────────────┘               │                          │
         ▲                           │ tools/viser_headless.py  │
         │                           │   :8091 + ctl :8092 ─────┘
         │ sync_daemon polls         │ tools/sync_daemon.py
         │ /api/sequences            │ tools/local_stream.py
         └───────────────────────────┴─────  /set, /camera, /sync-status
```

Open `http://localhost:4173` after `./run-client.sh`. Outliner picks
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

1. **From the server** — `rsync sxyin-host:.../sequences/<name>/`
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
`gs_simulation_building.py` on `sxyin-host` expects.

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
├── setup-client.sh / run-client.sh       # client side
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
