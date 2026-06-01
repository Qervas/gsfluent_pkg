# gsfluent

GaussianFluent physics simulation workbench, split between server and client.

The backend + GPU simulation runs on your server; the SPA runs on each
teammate's own machine and renders splats in-browser (Spark + three.js,
download-then-play). The backend is exposed to the team through a public
NAT port.

Chinese version: [README.md](README.md).

---

## Quick start (teammate)

Requires Python 3.10+ and Node 18+ locally. No conda, no sudo.

```bash
git clone <repo> gsfluent_pkg
cd gsfluent_pkg/frontend
npm install      # installs JS deps, builds dist/
npm start        # launches vite preview
```

The browser opens `http://localhost:5173/` automatically. Ctrl-C tears
the whole stack down.

`npm install` triggers `frontend/scripts/install.mjs` via postinstall:
it installs JS dependencies and runs `vite build`.
`npm start` runs `frontend/scripts/start.mjs`, which starts vite preview
(proxying `/api/*` to your server) under a shared Ctrl-C.

Default backend comes from `BACKEND_URL` in `.env`. To override per
invocation:

```bash
GSFLUENT_BACKEND_URL=http://your.host:port npm start
```

---

## Authoring a sim: material × scenario × building

Recipes aren't hand-written — they're **composed** from three orthogonal
inputs: **MATERIAL × SCENARIO × BUILDING**. The **Composer** panel at the
top of Properties is the entry point: pick a scenario, a material, and a
building; the backend's `POST /api/compose` generates a ready-to-run flat
recipe. Pick scenario + material, hit **Run** — that's it.

The five curated scenarios (all verified on rendered video to give a
dramatic "the building collapses" moment on the recommended soft material,
`watermelon`):

| Scenario     | What happens                                              |
| ---          | ---                                                      |
| `earthquake` | Base-shake plate → the tower collapses into rubble       |
| `wrecking`   | Mid-height side impact (pinned base) → shears apart      |
| `topple`     | Top third dragged along the thin axis → falls like a domino |
| `burst`      | Four core slabs shove outward → the structure explodes   |
| `demolish`   | Two opposing base-cut impacts → it crashes down + breaks |

Each scenario carries a `recommended_material`. Only the soft `watermelon`
collapses convincingly; stiff materials (jelly/plasticine) stay brittle and
barely deform — so they recommend `watermelon`. The UI snaps material to the
recommendation on scenario change and warns on a mismatch. The old flat
parameter panels (Material / Solver / Forces / …) are still there as
collapsed *advanced overrides* on top of the composed recipe.

**Model orientation:** if an imported scan loads lying down or upside-down,
use the **Y-up→Z-up** / **Flip 180°** buttons (top-right of the viewport) to
rotate it upright in place — repeatable, click until it stands up.

**Boundary mode** (Solver panel `Boundary mode`, default `drop`): in violent
scenarios, debris that flew out of the sim box used to NaN the whole sim
(grid escape). The solver now contains it — `drop` deactivates out-of-box
particles (debris flies out freely), `clamp` pins them at the wall — so the
"building explodes" no longer crashes the run.

> Composed recipes are in-memory only (they carry a `_composed_from` block)
> — they are **not** saved server recipes. Saved recipes are the flat
> material demos + `★` user presets. Full HTTP reference, including the
> composer endpoints: [`docs/API.md`](docs/API.md).

---

## Architecture

```
┌─────── Teammate client ────────────────────┐
│                                            │
│  Browser  →  http://localhost:5173/        │
│              (vite preview serves dist/)   │
│                                            │
│  vite preview :5173                        │
│   ├─ /api/*  → proxy → server :24701       │
│   └─ /       → frontend/dist/              │
│                                            │
│  SPA (SplatScene)                          │
│   downloads + renders splats in-browser    │
│   (Spark + three.js, no extra process)     │
│                                            │
└────────────────┬───────────────────────────┘
                 │ HTTP /api/*  (public NAT)
                 ▼
┌─────── GPU server ─────────────────────────┐
│                                            │
│  Public ingress  your-backend:port         │
│                  │ (NAT)                   │
│                  ▼                         │
│  v1 backend  0.0.0.0:7869                  │
│   ├─ /api/*       (REST, see docs/API.md)  │
│   └─ runner       (spawns MPM sim)         │
│                                            │
│  GaussianFluent sim stack (torch + warp +  │
│   taichi, A100)                            │
│                                            │
│  work/library/sequences/<run>/*.ply        │
│                                            │
└────────────────────────────────────────────┘
```

Splat data is fetched from the server over the same HTTP channel as the
REST API and rendered locally in-browser by `SplatScene`. Simulation
results live as PLY frame sequences on the server; the frontend pulls
them on demand over the REST API.

---

## Server admin

The backend process is supervised by a systemd unit. Files live in
`deploy/` (`gsfluent-backend.service` for production,
`gsfluent-backend.dev.service` for a dev box). `Type=notify` +
`WatchdogSec=30s` detects a wedged backend; every startup runs
`recover_on_boot()` to reconcile in-flight runs (live PG → re-attach,
dead PID → mark `INTERRUPTED`). Install steps live in
[`deploy/README.md`](deploy/README.md).

```bash
# Production
sudo systemctl enable --now gsfluent-backend.service
sudo systemctl status gsfluent-backend.service

# Dev box (per-user systemd)
systemctl --user enable --now gsfluent-backend.service
systemctl --user status gsfluent-backend.service
```

Bindings:

| Process     | Listens on        | Public mapping              |
|-------------|-------------------|-----------------------------|
| v1 backend  | `0.0.0.0:7869`    | `your-backend:port` (NAT)   |

Post-sim utilities (ply → gsq cache, frames.bin packing) live in
`server/tools/` and are run by hand over ssh as needed.

Logs flow through journald: `journalctl -u gsfluent-backend -f -o
json | jq -r '.MESSAGE | fromjson?'` (add `--user` for the dev-box
unit). The backend emits one JSON event per line, so `jq` can filter
by `run_id` / `event`.

The Python interpreters are configured via `.env`
(`GSFLUENT_API_PYTHON`, `GSFLUENT_SIM_PYTHON`).

---

## Cap configuration (runaway-recipe defence)

The backend validates every incoming recipe against caps at the API
boundary. Violations return 422 without spawning any subprocess.
Defaults are in `server/gsfluent/core/limits.py:DEFAULT_*`; override via
env vars:

| Env var                           | Default     | Meaning                                      |
|-----------------------------------|-------------|----------------------------------------------|
| `GSFLUENT_MAX_PARTICLE_COUNT`     | `500000`    | Max particles per submitted recipe           |
| `GSFLUENT_MAX_WALL_TIME_SEC`      | `3600`      | Max sim wall-time (PG-killed on overrun)     |
| `GSFLUENT_MAX_RECIPE_BYTES`       | `16384`     | Max recipe JSON size (DoS guard)             |

A cap-violation response:

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

---

## Component layout

The backend is split into six layers, each a `typing.Protocol` interface
plus a current concrete implementation, wired in
`server/gsfluent/composition.py`:

| Layer | Protocol                                    | Current impl                                  |
|-------|---------------------------------------------|-----------------------------------------------|
| L0    | (HTTP)                                      | `server/gsfluent/api/*.py`                    |
| L1    | `protocols/runs.py:RunManager`              | `core/run_manager.py:AsyncioRunManager`       |
| L2    | `protocols/sim.py:SimulationEngine`         | `core/sim_engines/mpm.py:MPMSimulationEngine` |
| L3    | `protocols/fuse.py:Fuser`                   | `core/fusers/knn_kabsch.py:KNNKabschFuser`    |
| L4    | `protocols/cache.py:CacheCodec`             | `core/codecs/gsq.py:GSQCodec`                 |
| L5    | `protocols/storage.py:Storage`              | `storage/filesystem.py:FilesystemStorage`     |
| L6    | `protocols/observability.py:EventEmitter`   | `observability/jsonlog.py:StdlibJSONEmitter`  |

Every Protocol has a conformance suite
(`server/tests/protocols/test_*_conformance.py`); swapping an
implementation only requires re-running the suite against the new impl.
Architecture details: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

---

## Repo layout

| Path                  | Purpose                                                                 |
|-----------------------|-------------------------------------------------------------------------|
| `frontend/`           | React + Vite SPA. `npm install` / `npm start` entry points.             |
| `frontend/scripts/`   | Node launchers: `install.mjs`, `start.mjs`, `clean.mjs`.                |
| `frontend/python/`    | Client-side Python (legacy): `vkgs_play.py`. (`viser_headless.py` and `sync_daemon.py` removed.) |
| `frontend/patches/`   | Upstream rendering patches (no-cull, point precision).                  |
| `server/`             | FastAPI v1 backend. Six-Protocol layout + composition root under `gsfluent/`. |
| `server/tools/`       | Sim wrapper (`run_sim.sh`, now a ~20-line conda-activate shim) and CLI wrappers around `core/` impls. |
| `server/recipes/`     | Built-in simulation recipe JSONs.                                       |
| `server/patches/`     | Upstream GaussianFluent sim patches.                                    |
| `deploy/`             | systemd units (`gsfluent-backend.service` + `gsfluent-backend.dev.service`) and deploy guide (`README.md`). |
| `docs/`               | API reference, architecture doc.                                        |
| `work/`               | Runtime data (gitignored): `library/sequences/<run>/`, `cache/splats/*.gsq`. |

---

## API reference

The backend exposes 29 REST endpoints + 1 WebSocket route:

- English: [`docs/API.md`](docs/API.md)
- Chinese: [`docs/API.zh.md`](docs/API.zh.md)

Main endpoints: `/api/health`, `/api/recipes`, `/api/models`,
`/api/sequences`, `/api/runs`, `/api/runs/{name}/log`, `/api/stream` (WS),
and more. No auth — access is gated by IP reachability only.

Architecture details: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

---

## Troubleshooting

| Symptom                                | One-line fix                                                                |
|----------------------------------------|-----------------------------------------------------------------------------|
| SPA won't open / `:5173` error         | Port in use — check `lsof -i :5173`, or `UI_PORT=5174 npm start`            |
| Splat viewport stays blank             | Check browser console for errors; verify `/api/sequences/{name}/cache/splats.gsq` is reachable |
| All `/api/*` calls 502 / refused       | Server backend is down — run `systemctl status gsfluent-backend.service` (add `--user` on a dev box) |
| Sim errors right after submission      | `curl <backend>/api/runs/<name>/log?offset=0` to see sim stdout             |
| Local `.venv/` is broken               | `rm -rf .venv/ frontend/dist/ && cd frontend && npm install`                |
