# gsfluent — Deployment & Usage Guide

Workbench for GaussianFluent physics simulations. Provides:

- **3DGS model management** (upload, list, delete)
- **Recipe-driven sim submission** (materials, boundary conditions,
  integration params → remote MPM simulation)
- **Sequence playback** (sim outputs as 3DGS sequences,
  interactive in-browser scrubbing)
- **HTTP REST API** (team members call directly by IP, no SSH)

[中文 README](README.md)

**Deployment model: local rendering (recommended).** Server runs the
API, the bundled SPA, and the sim. The viser splat renderer and
sync_daemon run on each team member's own machine. Splat data is
mirrored to local disk once per sequence (~2.8 GB via sync_daemon),
then playback is loopback — no network bandwidth per frame.

Only **one port** is opened on the server (the API). viser binds to
127.0.0.1 on each team member's machine, so it isn't exposed.

If the whole team is on the same datacenter LAN, you can run viser
on the server too ("server-side rendering"), but that needs 1 Gbps
to support the splat WebSocket. This document covers the recommended
local-rendering deployment.

---

## Architecture

```
┌──────────── Server (GPU box) ────────────────┐
│                                              │
│   gsfluent serve         :18080  [public]    │
│   ├─ REST API            /api/*              │
│   ├─ bundled SPA         /                   │
│   └─ runner (spawns sim) internal            │
│                                              │
│   sim env                GaussianFluent      │
│   (MPM + Warp + Taichi)                      │
│                                              │
│   work/cache/viser/*.npz  [outputs, pulled]  │
│                                              │
└──────────────▲──────────────────────▲────────┘
               │                       │
               │ /api/* (HTTP)         │ /api/sequences/<n>/cache/viser.npz
               │                       │ (sync_daemon downloads)
               │                       │
┌──────────────┴─────────┐  ┌──────────┴───────────────┐
│ Team member's machine  │  │ Team member's machine    │
│                        │  │                          │
│ Browser                │  │ viser_headless           │
│  ├─ http://server:18080│  │  127.0.0.1:8091 (WS)     │
│  │  (SPA + API proxy)  │  │  127.0.0.1:8092 (ctl)    │
│  └─ iframe localhost:  │  │  reads work/cache/viser/ │
│     8091 (splat render)│  │                          │
│                        │  │ sync_daemon              │
│ SPA → :8092 ctl API    │  │  polls server /api/...   │
│                        │  │  downloads new .npz      │
└────────────────────────┘  └──────────────────────────┘
```

Data flow: server runs sim → writes .npz → sync_daemon mirrors to
local → viser_headless reads local .npz → browser connects WebSocket
to viser → WebGL renders.

Only `:18080` (server) needs to be reachable from team members.
viser stays loopback throughout.

---

## 1. Server deployment

### 1.1 Requirements

| Component | Version | Notes |
|---|---|---|
| OS | Linux | Verified on Ubuntu 22.04 |
| Python (API) | 3.11+ | API service runtime |
| Python (sim) | 3.9 | GaussianFluent env (torch + warp + taichi) |
| GPU | NVIDIA, CC ≥ 8.0 | Verified on A100 |
| CUDA Toolkit | 11.5+ | Driver ≥ 525 |
| Disk | 50 GB+ | ~2.8 GB per completed sim sequence (.npz cache) |

### 1.2 Install

The repo is already deployed at `$GSFLUENT_PKG_ROOT_tmp/`
(includes built frontend `frontend/dist/` and Python venv `server/.venv/`).

For a fresh deploy:

```bash
git clone <repo> /opt/gsfluent_pkg
cd /opt/gsfluent_pkg

# 1. Create the API conda env
conda create -n gsfluent-api python=3.11 -y
conda activate gsfluent-api
pip install -e ./server[client]   # includes viser, numpy, ...

# 2. Build the frontend
cd frontend && npm ci && npm run build && cd ..

# 3. Set up the GaussianFluent sim env separately
#    (torch + warp 0.10 + taichi 1.5; needs CUDA build chain)
#    See the GaussianFluent README for that.

# 4. Apply our GaussianFluent upstream patches (REQUIRED)
#    We've made 5 patches to gs_simulation/watermelon/gs_simulation_building.py
#    (particle_F path + stability fixes). See tools/patches/UPSTREAM_PATCHES.md
#    for the full list. One-shot drop-in:
cp tools/patches/gs_simulation_building.patched.py \
   <GaussianFluent-path>/gs_simulation/watermelon/gs_simulation_building.py
```

### 1.3 Start the server

The repo root ships `start-gsfluent-server.sh`:

```bash
cd $GSFLUENT_PKG_ROOT_tmp
./start-gsfluent-server.sh
```

What it does:
1. Exports `GSFLUENT_SIM_HOME` and `GSFLUENT_SIM_PYTHON`
2. Backgrounds `gsfluent serve --host 0.0.0.0 --port 18080 --no-browser`
3. Logs to `/tmp/gsfluent_server.log`

**Don't start viser_headless on the server.** Under the recommended
local-rendering model, viser runs on each team member's machine
(see §2.2).

### 1.4 Ports & firewall

| Port | Purpose | Must open |
|---|---|---|
| 18080 | API + SPA | ✓ team access |

Open with `ufw`:

```bash
sudo ufw allow 18080/tcp
```

viser's 8091 / 8092 are **not exposed** — they bind to 127.0.0.1
on each team member's own machine.

### 1.5 Verify

```bash
curl http://<server-ip>:18080/api/health
# Expected: {"status":"ok","pkg_root":"$GSFLUENT_PKG_ROOT_tmp"}
```

---

## 2. Client usage

### 2.1 API-only (no splat playback needed)

For submitting sims, checking status, downloading frame PLYs: browser
or curl/Python straight to `http://<server-ip>:18080/`. No install
required. See §2.3 for scripted examples.

### 2.2 Full workbench (interactive splat playback, recommended)

To scrub through sim sequences interactively in the browser, each
team member needs two local services on **their own machine**:

- `tools/sync_daemon.py` — pulls new .npz files from the server
- `tools/viser_headless.py` — feeds .npz data to the browser's WebGL splat renderer

`run-client.sh` orchestrates both:

```bash
# One-time: install Python deps + build the frontend
cd gsfluent_pkg && ./setup-client.sh

# Each session: point at the server, start the client stack
GSFLUENT_SERVER=http://<server-ip>:18080 ./run-client.sh
```

What happens:
- Browser auto-opens `http://localhost:4173/` (vite preview serves the built SPA)
- Background: vite preview, viser_headless (binds 127.0.0.1:8091/8092), sync_daemon
- SPA's `/api/*` calls are proxied by vite to `<server-ip>:18080`
- splat iframe connects `localhost:8091` over loopback — no network latency

Ctrl-C tears the whole client stack down.

Requirements:
- Python 3.11+ (for viser / sync_daemon)
- Node.js 18+ (only for the one-time `setup-client.sh` build)
- Disk: ~2.8 GB local cache per completed sim sequence

Browsers: Chrome / Edge / Firefox (latest). Needs WebGL 2.0 + WebSocket.

### 2.3 API (scripted)

Every workbench feature is one HTTP endpoint. Common ones:

#### List recipes

```bash
curl http://<server-ip>:18080/api/recipes
```

Returns:
```json
[
  {"name": "jelly",       "source": "builtin"},
  {"name": "metal",       "source": "builtin"},
  {"name": "sand",        "source": "builtin"},
  {"name": "foam",        "source": "builtin"},
  {"name": "plasticine",  "source": "builtin"},
  {"name": "earthquake",  "source": "builtin"},
  {"name": "demolition",  "source": "builtin"},
  {"name": "wrecking",    "source": "builtin"}
]
```

#### List registered models

```bash
curl http://<server-ip>:18080/api/models
```

#### List completed sim sequences

```bash
curl http://<server-ip>:18080/api/sequences
```

#### Fetch a recipe's full params

```bash
curl http://<server-ip>:18080/api/recipes/jelly
```

#### Submit a sim run

```bash
# 1. Fetch the recipe
curl -s http://<server-ip>:18080/api/recipes/jelly -o /tmp/recipe.json

# 2. Build the request body
python3 -c '
import json
recipe = json.load(open("/tmp/recipe.json"))
print(json.dumps({
    "run_name": "my_test_run_001",
    "model_path": "$GSFLUENT_SIM_HOME/model/cluster_6_15",
    "recipe_data": recipe["data"],
    "recipe_source": "jelly",
    "particles": 200000
}))' > /tmp/req.json

# 3. Submit
curl -X POST http://<server-ip>:18080/api/runs \
     -H "Content-Type: application/json" \
     -d @/tmp/req.json
# Returns: {"run_id":"<id>","run_name":"my_test_run_001"}
```

#### List active runs

```bash
curl http://<server-ip>:18080/api/runs
# Returns each run's state (running / done / error)
```

#### Tail the log

```bash
RUN=my_test_run_001
curl "http://<server-ip>:18080/api/runs/${RUN}/log?offset=0"
# Returns {"content": "...", "offset": N, "size": N}
# Next poll: pass the previous response's `offset` as the new query param
# for incremental tail.
```

#### Cancel a run

```bash
curl -X DELETE http://<server-ip>:18080/api/runs/<run_id>
```

#### Download one frame as PLY

```bash
curl "http://<server-ip>:18080/api/runs/${RUN}/frame/0.ply" -o frame_0000.ply
```

### 2.4 Python client example

```python
import requests, json, time

API = "http://<server-ip>:18080"

# 1. Fetch a recipe
recipe = requests.get(f"{API}/api/recipes/jelly").json()

# 2. Submit a run
resp = requests.post(f"{API}/api/runs", json={
    "run_name": "py_demo_001",
    "model_path": "$GSFLUENT_SIM_HOME/model/cluster_6_15",
    "recipe_data": recipe["data"],
    "recipe_source": "jelly",
    "particles": 200000,
})
print("submitted:", resp.json())

# 3. Tail the log until it finishes
offset = 0
while True:
    runs = requests.get(f"{API}/api/runs").json()
    me = next((r for r in runs if r["name"] == "py_demo_001"), None)
    if me is None:
        break
    log = requests.get(f"{API}/api/runs/py_demo_001/log",
                       params={"offset": offset}).json()
    if log["content"]:
        print(log["content"], end="")
    offset = log["offset"]
    if me["state"] != "running":
        print(f"\n[final state: {me['state']}]")
        break
    time.sleep(1)
```

---

## 3. Available recipes

The sim wrapper `gs_simulation_building.py` does the actual MPM.
The workbench just submits requests and surfaces results.

### Materials (same building, different physics)

| Name | Behavior | Key params |
|---|---|---|
| `jelly` | Soft body, gentle bounce | E=5000, density=1 |
| `metal` | Stiff, dents under load, holds shape | E=50000, density=3 |
| `sand` | Granular pile, no cohesion | Drucker-Prager plasticity |
| `foam` | Soft foam, slow recovery | E=1000, density=0.3 |
| `plasticine` | Plastic clay, permanent deformation | yield_stress=500 |

### Scenarios (external forces / impactors)

| Name | Behavior | Implementation |
|---|---|---|
| `demolition` | Top-down particle release; building collapses in waves | `release_particles_sequentially` |
| `earthquake` | 4 cuboid colliders shake the floor laterally | 4× `cuboid` with alternating velocity |
| `wrecking` | Mid-height lateral impact (wrecking ball) | 1× `cuboid` lateral velocity |

### Removed recipes

`meteor` (vertical impactor) and `uplift` (ground rising) crashed
the MPM solver with `Warp CUDA error 700: illegal memory access`
on the cluster_6_15 model — their cuboid BCs overlap geometry at
t=0, the instantaneous velocity injection produces a stress
concentration that drives numerical blow-up. They've been removed
from the shipped recipe set.

Re-enabling either requires:
- a real solver-side fix (sub-stepping near high-strain regions), or
- redesigned BC schedules so cuboids enter the scene gradually
  rather than spawning inside the model.

---

## 4. Runtime data layout

```
work/
├── library/sequences/<run_name>/
│   ├── frames/frame_NNNN.ply   ← fused per-frame 3DGS (Z-up, normalized)
│   ├── manifest.json           ← sim metadata (timestamps, status, particle count)
│   ├── _meta.json              ← display metadata (frames, bbox, model_ref)
│   ├── _effective_recipe.json  ← exact recipe used (with coord translation)
│   └── run.log                 ← full server-side sim log
└── cache/viser/<run_name>.npz  ← splat-mode playback cache
```

Normal flow doesn't require touching these files:

1. `POST /api/runs` triggers a sim
2. `runner.py` runs sim → fuse → npz cache build serially
3. Client browser auto-picks-up new sequence

---

## 5. Authoring recipes

```bash
# Copy an existing recipe
cp tools/recipes/jelly.json tools/recipes/my_recipe.json

# Edit material params (E, nu, density, yield_stress) or BCs
vim tools/recipes/my_recipe.json
```

The workbench lists it on next launch. You can also POST a
`recipe_data` directly in the request body, skipping persistent files.

Key recipe fields:

- `n_grid` (default 150): MPM grid resolution. Detail scales linearly,
  memory quadratically.
- `substep_dt` (default 1e-4): inner integration step. Smaller is
  more stable but slower. The wrapper clamps to `min(recipe, CFL)`,
  so slightly above-CFL values are auto-corrected.
- `frame_num` (default 150): total frames (~5 s @ 30 fps).
- `g`: gravity, default `[0, 0, -15]`.
- `material`: one of `jelly`, `metal`, `sand`, `foam`, `snow`,
  `plasticine`, `watermelon`.
- `boundary_conditions`: list. `bounding_box` + `surface_collider`
  are always present; scenarios add `cuboid` (moving collider) or
  `release_particles_sequentially`.

---

## 6. Troubleshooting

### "ERROR: sim interpreter not on PATH: $GSFLUENT_SIM_PYTHON=python"

The server started without the sim-env Python set. `start-gsfluent-server.sh`
handles this; if you launched manually, export both:

```bash
export GSFLUENT_SIM_PYTHON=$CONDA_ROOT/envs/GaussianFluent/bin/python
export GSFLUENT_SIM_HOME=$GSFLUENT_SIM_HOME
```

### "no sim environment installed at $GSFLUENT_SIM_HOME"

`GSFLUENT_SIM_HOME` points to a missing dir. Check the path or
make sure GaussianFluent is cloned there.

### Browser shows nothing at `http://<server-ip>:18080/`

1. Service alive? `curl http://<server-ip>:18080/api/health`
2. Firewall open? `sudo ufw status | grep 18080`
3. `gsfluent serve` bound to `0.0.0.0` (not `127.0.0.1`)?

### Splat mode shows no model

`viser_headless` only runs as part of the client stack
(`run-client.sh`) and binds to local 127.0.0.1. Check:

```bash
curl http://localhost:8092/state
# Should return viser state JSON
```

If it fails:
1. Confirm `run-client.sh` started cleanly — check its terminal output
2. Run viser standalone to debug:
   ```bash
   python tools/viser_headless.py \
       --npz_dir work/cache/viser \
       --viser_port 8091 --control_port 8092
   ```
3. Hasn't synced .npz yet? Check sync_daemon status:
   ```bash
   cat /run/user/$(id -u)/gsfluent_sync_status.json | python3 -m json.tool
   ```

### Sim errors immediately after start

Tail the log:

```bash
RUN=<run_name>
curl "http://<server-ip>:18080/api/runs/${RUN}/log?offset=0" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["content"])'
```

Two common ones:

- **`tensor a (X) vs tensor b (Y) shape mismatch`** — the sim
  wrapper's `gaussians._scaling` got out of sync with `init_opacity`.
  Already patched in this version; if it returns, the wrapper was
  overwritten and the patch needs reapplying.
- **`Warp CUDA error 700`** — numerical instability or out-of-bounds
  particle access. Usually too-aggressive recipe (cuboid velocity
  too high, BC origin overlapping geometry, etc.). Lower velocity,
  raise the BC start position, or switch material to `plasticine`
  (has plastic yield).

### .npz cache too large / disk full

Each sim sequence is ~2.8 GB (683k splats × 151 frames × per-frame
covariance). Clean up old runs:

```bash
curl -X DELETE http://<server-ip>:18080/api/runs/history/<run_name>
```

---

## 7. Directory layout

```
gsfluent_pkg/
├── README.md                       ← Chinese version (deployment-oriented)
├── README.en.md                    ← This document (English)
├── start-gsfluent-server.sh        ← server boot script
├── server/                         ← FastAPI backend
│   └── gsfluent/
│       ├── api/                    ← /api/{recipes,runs,models,sequences,schemas}
│       └── core/                   ← runner, library, manifest, recipes
├── frontend/                       ← React + Vite SPA
│   └── dist/                       ← built output (served by gsfluent serve)
├── tools/
│   ├── viser_headless.py           ← splat-render service (port 8091/8092)
│   ├── fuse_to_full_ply.py         ← sim_*.ply + ref 3DGS → per-frame 3DGS
│   ├── sequence_to_viser_npz.py    ← per-frame ply → .npz cache
│   ├── batch_convert_to_npz.py     ← batch converter
│   ├── run_sim.sh                  ← server-side sim wrapper
│   └── recipes/                    ← JSON recipes
└── work/                           ← runtime data
    ├── library/                    ← persisted sequences
    └── cache/                      ← .npz playback cache
```

---

## 8. API surface

Full endpoint list — see docstrings at the top of each file in
`server/gsfluent/api/`. Current endpoints:

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Health check |
| GET | `/api/recipes` | List recipes |
| GET | `/api/recipes/{name}` | Recipe detail |
| GET | `/api/models` | List registered models |
| POST | `/api/models/upload` | Upload new model |
| POST | `/api/models/register` | Register existing model path |
| DELETE | `/api/models/{name}` | Delete model |
| GET | `/api/sequences` | List sim sequences |
| DELETE | `/api/sequences/{name}` | Delete sequence |
| GET | `/api/runs` | List active runs |
| POST | `/api/runs` | Submit a new run |
| DELETE | `/api/runs/{run_id}` | Cancel a run |
| GET | `/api/runs/history` | List historical runs |
| DELETE | `/api/runs/history/{name}` | Delete historical run |
| GET | `/api/runs/{name}/log` | Incremental log tail |
| GET | `/api/runs/{name}/frame/{idx}.ply` | Download one frame |
| GET | `/api/schemas/materials` | Material defaults |
| GET | `/api/schemas/boundaries` | BC type schema |

---

## 9. References

- 3D Gaussian Splatting: Kerbl et al. 2023
- MPM physics: NVIDIA Warp + Taichi (server-side sim stack)
- Splat playback: viser
- Workbench frontend: React + Vite + React Three Fiber
