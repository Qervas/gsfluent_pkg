# gsfluent server

The FastAPI + WebSocket bridge for the gsfluent workbench. Wraps the
existing Taichi/Warp sim core (`tools/sim_one.sh`) as a subprocess
and serves the React SPA built from `../frontend/`.

## Install

```bash
# Build the frontend bundle and install the server in editable mode.
cd server
make build
```

Or manually:

```bash
cd frontend && npm install && npm run build
cd ../server && pip install -e .
```

## Run

After install, from anywhere:

```bash
gsfluent serve              # opens browser to http://localhost:8080
gsfluent serve --no-browser # don't auto-open
gsfluent serve --reload     # auto-reload on code changes (dev)
```

## Develop (no build step required)

```bash
# terminal 1:
cd server
python -m gsfluent serve --no-browser --reload --port 8080

# terminal 2:
cd frontend
npm run dev   # http://localhost:5173 (vite proxies /api → :8080)
```

## Test

```bash
cd server
make test     # pytest -v
```

## Layout

- `gsfluent/server.py` — FastAPI app factory
- `gsfluent/cli.py` — `gsfluent` console-script entry
- `gsfluent/api/` — REST + WebSocket routers (recipes, models, runs, schemas, stream)
- `gsfluent/core/` — domain logic (recipes, models, manifest, runner, frame_stream)
- `gsfluent/schemas/` — BC + material default schemas
- `gsfluent/static/` — Vite build output, served at `/` (gitignored)
- `tests/` — pytest suite (37 tests, see `pytest -v`)
