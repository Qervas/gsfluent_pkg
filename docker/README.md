# Deploying the gsfluent backend in Docker

Reproducible deploy of the workbench's server-side FastAPI gateway.
Each team member SSHes to the server, forwards a local port, and calls
the backend via that tunnel — no public exposure, no auth code to
maintain, SSH provides the access control.

Current image scope: **handshake / API only**. The MPM sim stack
(torch + warp + taichi + canonical sim source) is not yet baked in; a
follow-up layer adds it. The handshake is what proves the SSH+Docker+GPU
chain works before any heavy install.

## Architecture

```
  laptop                                server (sxyin-host)
                                        ┌──────────────────────────┐
                                        │ Docker container         │
                                        │  - gsfluent server       │
  ssh -L 8080:localhost:8080 ──────────►│  - GPU access (--gpus all)│
  sxyin-host                            │  - mounts host paths for │
                                        │    library + cache       │
  curl http://localhost:8080/api/health │                          │
  open http://localhost:8080/           │  listens on :8080        │
                                        └──────────────────────────┘
```

## Build the image (server admin, one-off)

From the repo root (`gsfluent_pkg/`):

```bash
docker build -f docker/Dockerfile.backend -t gsfluent-backend:dev .
```

The image is ~3 GB (`nvidia/cuda:12.4.0-runtime-ubuntu22.04` base
+ python + the gsfluent server pkg). Subsequent rebuilds reuse the
base layers; only changed files invalidate the COPY layers.

## Run

The simplest case — start the container, expose :8080:

```bash
docker run --rm --gpus all -p 8080:8080 gsfluent-backend:dev
```

For configurable ports, library paths, restart policy, use the
compose file:

```bash
docker compose -f docker/compose.yml up -d
```

Override knobs via env vars before invoking compose:

| Env var               | Default                     | What it does |
|-----------------------|-----------------------------|--------------|
| `BACKEND_PORT`        | `8080`                      | Host port the API is published on |
| `CONTAINER_NAME`      | `gsfluent-backend`          | Container name (visible in `docker ps`) |
| `LIBRARY_HOST_DIR`    | `./work/library`            | Host dir holding fused sequences |
| `CACHE_HOST_DIR`      | `./work/cache`              | Host dir for the .npz cache |
| `UPLOADS_HOST_DIR`    | `./work/uploads`            | Host dir for uploaded 3DGS models |
| `IMAGE_TAG`           | `dev`                       | Tag of the image to run |

Example: run on port 18080 with the existing sxyin-host library dir:

```bash
BACKEND_PORT=18080 \
LIBRARY_HOST_DIR=/data/yinshaoxuan/gsfluent_work/library \
CACHE_HOST_DIR=/data/yinshaoxuan/gsfluent_work/cache \
docker compose -f docker/compose.yml up -d
```

## Test from a laptop

Each team member opens an SSH tunnel and hits the API:

```bash
# Terminal A — keep the tunnel alive
ssh -N -L 8080:localhost:8080 sxyin-host

# Terminal B — handshake
curl -s http://localhost:8080/api/health     | python3 -m json.tool
curl -s http://localhost:8080/api/gpu-check  | python3 -m json.tool
curl -s http://localhost:8080/api/system     | python3 -m json.tool
```

What each endpoint validates:

- **`/api/health`** — `{"status":"ok", ...}` means the server is up
  and reachable through the SSH tunnel. Failure → tunnel issue or
  the container isn't running.
- **`/api/gpu-check`** — should return `{"ok": true, "gpus": [...]}`
  with the A100 / H100 line(s) from `nvidia-smi`. Failure with
  "nvidia-smi not on PATH" means the host needs the
  `nvidia-container-toolkit` installed (so `--gpus all` actually wires
  through), OR the container was started without `--gpus all`.
- **`/api/system`** — hostname, Python version, in_container flag.
  Useful as a sanity check that you're hitting the container, not
  some other service that happened to be on the same port.

## What the handshake does NOT prove yet

This image has FastAPI + the gateway only. It does not have:

- `torch`, `warp-lang`, `taichi` — sim deps
- The canonical gsfluent sim source (`gs_simulation_building.py`)
- Compiled CUDA extensions (`diff_gaussian_rasterization`, `simple-knn`)

So `POST /api/runs` will spawn `/app/tools/run_sim.sh`, which will fail
on the missing canonical sim source. That's intentional for this
deployment slice — once handshake is proven, the sim layer is added in
a follow-up image.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `curl: (7) Failed to connect to localhost port 8080` | SSH tunnel not running, or backend container not started |
| `/api/health` returns `502 Bad Gateway` | Reverse proxy in the middle and the container isn't responding — check `docker logs gsfluent-backend` |
| `/api/gpu-check` returns `nvidia-smi not on PATH` | Host missing `nvidia-container-toolkit`, or container started without `--gpus all` |
| `docker build` fails on `npm ci` | Network or cache issue — try `--no-cache` to force fresh pulls |
| Port already in use | Another service holds :8080. Set `BACKEND_PORT=18080` and retry |

## Where this is going

Next slices, in order:

1. **Sim layer image** — Dockerfile.sim (or extend Dockerfile.backend) that adds CUDA dev tools + torch + warp + taichi + builds the `diff_gauss_rast` / `simple-knn` extensions. Mounts the canonical gsfluent source as a read-only volume (so users don't have to vendor it into the image).
2. **End-to-end smoke** — submit a `jelly` recipe through the React UI, the container runs sim, fuse, builds .npz, syncs to a laptop.
3. **CI** — build the image on push, run the handshake against it.
