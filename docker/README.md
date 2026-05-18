# Deploying the gsfluent backend in Docker

Reproducible deploy of the entire workbench in **one container**:
FastAPI gateway + the React workbench SPA + the sim wrapper scripts.
A single host runs the API and serves the bundled SPA; team members
access it directly by IP.

```bash
git clone <repo> && cd gsfluent_pkg
docker compose -f docker/compose.yml up -d
# open http://<server-ip>:8080/  (or 18080 if BACKEND_PORT is overridden)
```

No Node, no npm, no Python on the host. Docker is the only requirement.
Works on any Linux box, Mac, or Windows-with-WSL.

**Image size**: ~316 MB (slim Python + uv-managed venv + Vite-built SPA,
no CUDA runtime). **Dependencies**: Docker 24+ with BuildKit. Compose v2.

For the full deployment story (split server/client, recipes, API
surface, troubleshooting), see the top-level [README.md](../README.md)
(中文) or [README.en.md](../README.en.md) (English). This file is
Docker-specifics only.

## Quick start

Two paths:

```bash
# A — pull pre-built image from GHCR (no local build, no Node required)
docker pull ghcr.io/qervas/gsfluent-backend:latest
docker run --rm -d -p 8080:8080 \
    -v "$PWD/work:/app/work" \
    --name gsfluent ghcr.io/qervas/gsfluent-backend:latest

# B — clone + compose up (recommended: gets healthcheck, log rotation,
#     restart policy out of the box)
git clone <repo> && cd gsfluent_pkg
docker compose -f docker/compose.yml up -d
```

Then:

```bash
# wait ~10s for the healthcheck grace window
curl http://<server-ip>:8080/api/health             # ok
docker compose -f docker/compose.yml ps             # check Healthy column (B only)
```

To stop:

```bash
# A:
docker stop gsfluent
# B:
docker compose -f docker/compose.yml down
```

Open firewall for the published port (default `:8080`, or
`BACKEND_PORT` if overridden). That's the only port the team needs
to reach.

## Configuration

Every knob is an env var; compose reads `.env` at the repo root
automatically, or set them inline. Sensible defaults — listed for the
cases you'll want to tune.

| Env var               | Default                     | What it does |
|-----------------------|-----------------------------|--------------|
| `BACKEND_PORT`        | `8080`                      | Host port the API is published on. Match the top-level README's `:18080` story by setting `BACKEND_PORT=18080`. |
| `CONTAINER_NAME`      | `gsfluent-backend`          | Container name (shows in `docker ps`). |
| `WORK_HOST_DIR`       | `./work`                    | Host dir for library + cache + uploads. Persistent across container restarts. |
| `SIM_HOST_DIR`        | `/opt/gsfluent-sim`         | Host path to the canonical sim install. Only needed if this host should RUN sims. Mount is commented out in compose.yml by default — uncomment for sim hosts. |
| `GSFLUENT_SIM_PYTHON` | `python`                    | Python interpreter the sim subprocess uses. Override to e.g. `/opt/gsfluent-sim/.venv/bin/python` when the sim env is mounted. The container will preflight-fail with a clear error if this isn't on PATH. |
| `IMAGE_TAG`           | `latest`                    | Tag of the image to run. |

Example — API-only deploy on `:18080` (matches the top-level
deployment guide), persistent volumes in `/var/lib/gsfluent/`:

```bash
BACKEND_PORT=18080 \
WORK_HOST_DIR=/var/lib/gsfluent \
docker compose -f docker/compose.yml up -d
```

Example — sim-capable deploy with the GaussianFluent install mounted:

```bash
BACKEND_PORT=18080 \
SIM_HOST_DIR=$GSFLUENT_SIM_HOME \
GSFLUENT_SIM_PYTHON=/opt/gsfluent-sim/.venv/bin/python \
docker compose -f docker/compose.yml up -d
# then edit docker/compose.yml to UNCOMMENT the SIM_HOST_DIR volume line
```

(Note: sim-capable mode also needs the host to have
`nvidia-container-toolkit` installed AND the GPU `deploy.resources`
block in `compose.yml` uncommented. Coming up in the sim-layer image.)

## Robustness

- **Healthcheck**: `curl /api/health` every 15s. 4 consecutive failures
  → Docker marks the container unhealthy.
- **Auto-restart**: `restart: unless-stopped` brings the container back
  on crash, OOM, or host reboot. Combined with healthcheck this catches
  hangs.
- **Log rotation**: capped at 50 MB across 5 files. Won't fill a
  host disk.
- **Stateless container**: all persistent data is on the host via the
  `WORK_HOST_DIR` mount. Restarting / re-pulling the image doesn't
  lose sequences.

## How it fits the local-rendering deployment

The recommended deployment model (see the top-level README): server
exposes **one HTTP port** (the API + bundled SPA, here in the
container). Team members each run a local client stack
(`run-client.sh` — viser_headless + sync_daemon on their own machines,
bound to 127.0.0.1) for interactive splat playback.

The Docker image is the **server side** of that story. It does not
ship viser_headless or sync_daemon — those run on the team members'
own machines, against `pip install`-ed deps. The image:

- Serves the bundled SPA at `/`
- Exposes `/api/*` for control, model upload, run submission,
  log streaming, frame download
- Spawns the sim subprocess via `tools/run_sim.sh` when
  `POST /api/runs` arrives
- Persists results to `work/` (mounted host volume) so sync_daemon
  on team laptops can mirror them

Team members who only need API access (curl / Python scripting) don't
need the local client stack at all — just hit `http://<server>:8080/api/*`.

## Architecture

```
  team member's machine             server (Docker host)
  ┌────────────────────┐            ┌──────────────────────────┐
  │ browser            │            │ Docker container         │
  │  http://server:8080│ ──────────►│  gsfluent-backend:latest │
  │  (SPA + API proxy) │            │   - FastAPI on :8080     │
  │                    │            │   - bundled SPA at /     │
  │ viser_headless     │            │   - subprocess to sim    │
  │  127.0.0.1:8091    │            │     (mounted from host)  │
  │  (optional, local) │            │   - HEALTHCHECK every 15s│
  │                    │            │   - restart=unless-stopped│
  │ sync_daemon        │            └──────────┬───────────────┘
  │  pulls .npz from   │                       │
  │  server            │                       ▼
  └────────────────────┘            host-mounted volumes:
                                      ./work         ← library + cache
                                      ./gsfluent-sim ← sim env (optional)
```

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `curl http://<server>:8080/api/health` connection refused | Container hasn't bound the port yet. `docker compose ps` → check Health column. Wait 10s after `up -d` for the start-period grace window. |
| Container is `unhealthy` and constantly restarting | App-level error. `docker compose logs backend --tail=50` to see the stack trace. |
| `POST /api/runs` returns 500 / "sim_script not found" | Either `$SIM_HOST_DIR` isn't mounted in compose.yml, or `$GSFLUENT_SIM_PYTHON` doesn't resolve inside the container. |
| `POST /api/runs` log starts with `ERROR: sim interpreter not on PATH` | `$GSFLUENT_SIM_PYTHON=python` (the default) doesn't resolve inside the slim container. Set it to the actual path inside your mounted sim env, e.g. `/opt/gsfluent-sim/.venv/bin/python`. |
| Browser at `http://<server>:8080/` shows nothing | Firewall blocking the published port. Open it (`sudo ufw allow 8080/tcp`). |
| Build fails on `uv sync --frozen` | `server/uv.lock` is out of sync with `pyproject.toml`. Run `cd server && uv lock` locally and rebuild. |
| Image is huge (>1 GB) | You're still on the old CUDA-runtime base. Rebuild from the current Dockerfile.backend. |
| Splat mode shows nothing in the workbench | Splat rendering requires viser_headless running on the team member's machine (loopback). See the top-level README §2.2. The container doesn't ship viser. |

## Rebuilding after pulling new code

After `git pull` brings in code/recipe/SPA changes:

```bash
# A:
docker pull ghcr.io/qervas/gsfluent-backend:latest
docker stop gsfluent && docker rm gsfluent
docker run --rm -d -p 8080:8080 -v "$PWD/work:/app/work" \
    --name gsfluent ghcr.io/qervas/gsfluent-backend:latest

# B:
docker compose -f docker/compose.yml up -d --build
```

To push a new image to GHCR after building:

```bash
docker build -f docker/Dockerfile.backend -t ghcr.io/qervas/gsfluent-backend:latest .
docker push ghcr.io/qervas/gsfluent-backend:latest
```

## Where this is going

1. **Sim-layer image** (`Dockerfile.backend-sim`): same base + CUDA
   runtime + bind-mounted GaussianFluent install. For sim hosts.
2. **Pre-built image** kept current in GHCR — `docker compose up`
   without a build step.
