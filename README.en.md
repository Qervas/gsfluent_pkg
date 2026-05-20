# gsfluent

GaussianFluent physics simulation workbench, split between server and laptop.

The backend + GPU simulation runs on your-server; the frontend + viser splat
renderer runs on each teammate's own laptop. The backend is exposed to
the team through a public NAT port; splat traffic stays on the laptop's
loopback and never touches the network.

Chinese version: [README.md](README.md).

---

## Quick start (teammate)

Requires Python 3.10+ and Node 18+ on the laptop. No conda, no sudo.

```bash
git clone <repo> gsfluent_pkg
cd gsfluent_pkg/frontend
npm install      # creates .venv/, installs pip deps, builds dist/
npm start        # launches viser_headless + vite preview
```

The browser opens `http://localhost:5173/` automatically. Ctrl-C tears
the whole stack down.

`npm install` runs `scripts/_install.sh`: it creates `.venv/` at
the repo root, pip-installs `viser`, `fastapi`, `uvicorn`, `httpx`,
`eval_type_backport`, then runs `npm ci` + `vite build`. `npm start`
runs `scripts/_start.sh`, which launches both local services and
proxies `/api/*` to the your-server backend.

Default backend is `http://your-backend:port`. To override:

```bash
GSFLUENT_BACKEND_URL=http://your.host:port npm start
```

---

## Architecture

```
┌─────── Teammate laptop ────────────────────┐
│                                            │
│  Browser  →  http://localhost:5173/        │
│              (vite preview serves dist/)   │
│                                            │
│  vite preview :5173                        │
│   ├─ /api/*  → proxy → your-server :24701        │
│   └─ /       → frontend/dist/              │
│                                            │
│  viser_headless                            │
│   ├─ 127.0.0.1:8091 (splat WS)             │
│   └─ 127.0.0.1:8092 (control API)          │
│                                            │
└────────────────┬───────────────────────────┘
                 │ HTTP /api/*  (public NAT)
                 ▼
┌─────── your-server GPU host ─────────────────────┐
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

The splat WebSocket stays on laptop loopback — no public bandwidth used
for splat playback. Simulation results are stored as PLY frame sequences
on your-server and pulled on demand over the REST API.

---

## Server admin (your-server)

Backend processes are supervised by `tools/supervise.sh`. On your-server:

```bash
bash tools/supervise.sh up      # start viser_headless + v1 backend with auto-restart
bash tools/supervise.sh status  # show current PIDs
bash tools/supervise.sh stop    # take everything down
```

Bindings:

| Process         | Listens on                | Public mapping                |
|-----------------|---------------------------|-------------------------------|
| v1 backend      | `0.0.0.0:7869`            | `your-backend:port` (NAT)     |
| viser_headless  | `127.0.0.1:8091` / `:8092` | not exposed (your-server loopback) |

Logs land in `$GSFLUENT_PKG_ROOT/work/logs/{v1,viser_headless,supervisor}.log`.

The sim Python interpreter is hard-coded near the top of `supervise.sh` —
adjust it there if the path moves.

---

## Repo layout

| Path          | Purpose                                                                |
|---------------|------------------------------------------------------------------------|
| `frontend/`   | React + Vite SPA. `npm install` / `npm start` entry points.            |
| `server/`     | FastAPI v1 backend. Runs on your-server. REST routes + runner.               |
| `tools/`      | Sim wrappers, PLY → npz converters, `viser_headless.py`, `supervise.sh`. |
| `scripts/`    | Laptop launchers `_install.sh` / `_start.sh` (called via npm).         |
| `docs/`       | API reference, architecture doc, patch notes.                          |
| `patches/`    | Upstream viser rendering patches (no-cull, point precision).           |
| `work/`       | Runtime data: `library/sequences/<run>/`, `cache/viser/*.npz`, …       |

---

## API reference

The backend exposes 31 REST endpoints + 1 WebSocket route:

- English: [`docs/API.md`](docs/API.md)
- Chinese: [`docs/API.zh.md`](docs/API.zh.md)

Overview: `/api/health`, `/api/recipes`, `/api/models`, `/api/sequences`,
`/api/runs`, `/api/runs/{name}/log`, `/api/stream` (WS), and more. No
auth — access is gated by IP reachability only.

Architecture details: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

---

## Troubleshooting

| Symptom                                | One-line fix                                                                |
|----------------------------------------|-----------------------------------------------------------------------------|
| SPA won't open / `:5173` error         | Port in use — check `lsof -i :5173`, or `UI_PORT=5174 npm start`            |
| Splat viewport stays blank             | viser not up — `curl http://127.0.0.1:8092/state`; on your-server run `supervise.sh status` |
| All `/api/*` calls 502 / refused       | your-server backend is down — on your-server run `bash tools/supervise.sh status`       |
| Sim errors right after submission      | `curl <backend>/api/runs/<name>/log?offset=0` to see sim stdout             |
| Local `.venv/` is broken        | `rm -rf .venv/ frontend/dist/ && cd frontend && npm install`         |
