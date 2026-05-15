# Deploying the gsfluent backend in Docker

Slim, reproducible deploy of the entire workbench in **one container**:
FastAPI gateway + the React workbench SPA + run-script tools. Bundled
so the leader's-laptop deploy is literally:

```bash
git clone <repo> && cd gsfluent_pkg
docker compose -f docker/compose.yml up -d
open http://localhost:8080/
```

No Node, no npm, no Python on the host. Docker is the only requirement.
Works on any Linux box, Mac, or Windows-with-WSL.

**Image size**: ~316 MB (slim Python + uv-managed venv + Vite-built SPA,
no CUDA runtime). **Dependencies**: Docker 24+ with BuildKit. Compose v2.

## Quick start

From the repo root (`gsfluent_pkg/`):

```bash
docker compose -f docker/compose.yml up -d
# wait ~10s for the start-period grace window
open http://localhost:8080/                        # the workbench
curl http://localhost:8080/api/health              # ok
docker compose -f docker/compose.yml ps            # check Healthy column
```

To stop:

```bash
docker compose -f docker/compose.yml down
```

## Configuration

Every knob is an env var; compose reads `.env` at the repo root
automatically, or set them inline. All defaults work without any
overrides — listed here for the cases you'll want to tune.

| Env var               | Default                     | What it does |
|-----------------------|-----------------------------|--------------|
| `BACKEND_PORT`        | `8080`                      | Host port the API is published on. Change if 8080 is taken. |
| `CONTAINER_NAME`      | `gsfluent-backend`          | Container name (shows in `docker ps`). |
| `WORK_HOST_DIR`       | `./work`                    | Host dir for library + cache + uploads. Persistent across container restarts. |
| `SIM_HOST_DIR`        | `/opt/gsfluent-sim`         | Host path to the canonical sim install. Only needed if this host should RUN sims. Mount is commented out in compose.yml by default — uncomment for sim hosts. |
| `GSFLUENT_SIM_PYTHON` | `python`                    | Python interpreter the sim subprocess will use. Override to e.g. `/opt/gsfluent-sim/.venv/bin/python` when the sim env is mounted. |
| `IMAGE_TAG`           | `latest`                    | Tag of the image to run. |

Example — API-only deploy on `:18080`, persistent volumes in `/var/lib/gsfluent/`:

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
  hangs (the prior backend used to hang silently — now it gets killed
  and restarted automatically).
- **Log rotation**: capped at 50 MB across 5 files. Won't fill a
  laptop disk.
- **Stateless container**: all persistent data is on the host via the
  `WORK_HOST_DIR` mount. Restarting / re-pulling the image doesn't
  lose sequences.

## Two deployment modes

**Mode A — leader-friendly one-machine deploy.** The container hosts
both the API and the SPA. Open `http://localhost:8080/` and the
workbench renders against its own backend. **Points** render mode works
out of the box (uses /api/stream). **Splats** mode and auto-sync of
sequences from a remote sim host require the optional client tools
(`./run-client.sh`) running alongside.

**Mode B — split: laptop client + remote backend.** Backend container
runs on the GPU server; the laptop forwards `:8080` via SSH and uses
`./run-client.sh` for viser + sync_daemon:

```bash
# laptop ~/.ssh/config:
#   Host mygpu
#     HostName <ip>
#     User <you>

SERVER_SSH=mygpu ./run-client.sh
# opens tunnel + viser + sync_daemon + browser to localhost:4173
```

The same container image serves both modes — only the env vars and
mounts differ.

## Architecture

```
  client (laptop)                       server (anywhere with Docker)
                                        ┌──────────────────────────┐
                                        │ Docker container         │
                                        │  gsfluent-backend:latest │
  ssh -L 8080:localhost:8080 ──────────►│   - FastAPI on :8080     │
                                        │   - subprocess to sim    │
  browser → http://localhost:4173/      │     (mounted from host)  │
   (vite preview)                       │   - HEALTHCHECK every 15s│
   /api/* → tunnel → container          │   - restart=unless-stopped│
                                        └──────────────────────────┘
                                                 │
                                                 ▼
                                        host-mounted volumes:
                                          ./work        ← library + cache
                                          ./gsfluent-sim ← sim env (opt)
```

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `curl http://localhost:8080/api/health` connection refused | Container hasn't bound the port yet. `docker compose ps` → check Health column. Wait 10s after `up -d` for the start-period grace window. |
| Container is `unhealthy` and constantly restarting | App-level error. `docker compose logs backend --tail=50` to see the stack trace. |
| `POST /api/runs` returns 500 / "sim_script not found" | Either `$SIM_HOST_DIR` isn't mounted in compose.yml, or `$GSFLUENT_SIM_PYTHON` doesn't resolve inside the container. |
| Build fails on `uv sync --frozen` | `server/uv.lock` is out of sync with `pyproject.toml`. Run `cd server && uv lock` locally and rebuild. |
| Image is huge (>1 GB) | You're still on the old CUDA-runtime base. Rebuild from the current Dockerfile.backend. |

## Where this is going

1. **Sim-layer image** (`Dockerfile.backend-sim`): same base + CUDA
   runtime + bind-mounted GaussianFluent install. For sim hosts.
2. **Bundled SPA** as a separate compose service: serve the React
   workbench from an nginx container so the leader's deploy is "one
   command and open a URL".
3. **Pre-built image** pushed to a public registry — `docker compose
   up` without a build step.
