# gsfluent

GaussianFluent physics simulation workbench, split between server and client.

The backend + GPU simulation runs on your server; the SPA + viser splat
renderer runs on each teammate's own machine. The backend is exposed
to the team through a public NAT port; splat traffic stays on the client's
loopback and never touches the network.

Chinese version: [README.md](README.md).

---

## Quick start (teammate)

Requires Python 3.10+ and Node 18+ locally. No conda, no sudo.

```bash
git clone <repo> gsfluent_pkg
cd gsfluent_pkg/frontend
npm install      # creates .venv/, installs pip deps, builds dist/
npm start        # launches viser_headless + vite preview
```

The browser opens `http://localhost:5173/` automatically. Ctrl-C tears
the whole stack down.

`npm install` triggers `frontend/scripts/install.mjs` via postinstall:
it creates `.venv/` at the repo root, pip-installs `viser`, `fastapi`,
`uvicorn`, `httpx`, `eval_type_backport`, then runs `vite build`.
`npm start` runs `frontend/scripts/start.mjs`, which uses `concurrently`
to launch viser_headless + vite preview under a shared Ctrl-C.

Default backend comes from `BACKEND_URL` in `.env`. To override per
invocation:

```bash
GSFLUENT_BACKEND_URL=http://your.host:port npm start
```

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
│  viser_headless                            │
│   ├─ 127.0.0.1:8091 (splat WS)             │
│   └─ 127.0.0.1:8092 (control API)          │
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

The splat WebSocket stays on client loopback — no public bandwidth
used for splat playback. Simulation results live as PLY frame sequences
on the server and the frontend pulls them on demand over the REST API.

---

## Server admin

The backend process is supervised by `server/supervise.sh`, run on the
GPU host:

```bash
bash server/supervise.sh up      # start v1 backend with auto-restart
bash server/supervise.sh status  # show current PID
bash server/supervise.sh stop    # take it down
```

Bindings:

| Process     | Listens on        | Public mapping              |
|-------------|-------------------|-----------------------------|
| v1 backend  | `0.0.0.0:7869`    | `your-backend:port` (NAT)   |

Post-sim utilities (ply → gsq cache, frames.bin packing) live in
`server/tools/` and are run by hand over ssh as needed.

Logs land in `/path/to/gsfluent_pkg/work/logs/{v1,supervisor}.log`.

The Python interpreters are configured via `.env`
(`GSFLUENT_API_PYTHON`, `GSFLUENT_SIM_PYTHON`).

---

## Repo layout

| Path                  | Purpose                                                                 |
|-----------------------|-------------------------------------------------------------------------|
| `frontend/`           | React + Vite SPA. `npm install` / `npm start` entry points.             |
| `frontend/scripts/`   | Node launchers: `install.mjs`, `start.mjs`, `clean.mjs`.                |
| `frontend/python/`    | Client-side Python: `viser_headless.py`, `sync_daemon.py`, `vkgs_play.py`. |
| `frontend/patches/`   | Upstream viser rendering patches (no-cull, point precision).            |
| `server/`             | FastAPI v1 backend. REST routes + runner live under `gsfluent/`.        |
| `server/tools/`       | Sim wrapper (`run_sim.sh`), PLY → gsq converter (`pack_splats.py`), fuse, migration.      |
| `server/recipes/`     | Built-in simulation recipe JSONs.                                       |
| `server/patches/`     | Upstream GaussianFluent sim patches.                                    |
| `docs/`               | API reference, architecture doc.                                        |
| `work/`               | Runtime data (gitignored): `library/sequences/<run>/`, `cache/viser/*.gsq`. |

---

## API reference

The backend exposes 31 REST endpoints + 1 WebSocket route:

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
| Splat viewport stays blank             | viser not up — `curl http://127.0.0.1:8092/state`                           |
| All `/api/*` calls 502 / refused       | Server backend is down — on the server run `bash server/supervise.sh status` |
| Sim errors right after submission      | `curl <backend>/api/runs/<name>/log?offset=0` to see sim stdout             |
| Local `.venv/` is broken               | `rm -rf .venv/ frontend/dist/ && cd frontend && npm install`                |
