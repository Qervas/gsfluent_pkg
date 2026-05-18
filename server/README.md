# gsfluent server

FastAPI + WebSocket bridge for the gsfluent workbench. Serves the
React SPA built from `../frontend/`, exposes the library/recipes/runs
REST API, and pumps per-frame xyz over `/api/stream` for the Points
render mode.

Pure-Python deps (fastapi, uvicorn, plyfile, pydantic, watchfiles,
numpy). Simulation itself runs on the GPU server, not here
— see `../docs/ARCHITECTURE.md`.

## Install

This package is normally installed by the top-level
`../setup-view.sh`. To install standalone:

```bash
cd server
pip install -e .
```

That registers the `gsfluent` console script. For the SPA, build it
first or run vite alongside in dev mode:

```bash
# production: bake the SPA into server/gsfluent/static/
cd ../frontend && npm install && npm run build
cp -r dist/* ../server/gsfluent/static/

# dev: leave static/ empty, run vite separately
cd ../frontend && npm run dev   # http://localhost:5173, proxies /api → :8080
```

## Run

```bash
gsfluent serve              # opens browser to http://localhost:8080
gsfluent serve --no-browser
gsfluent serve --reload     # dev: auto-reload on code changes
```

For the integrated workbench (backend + viser headless together),
use `../run-server.sh` (this box) + `../run-laptop.sh` (laptop) instead.

## Test

```bash
cd server
make test     # pytest -v
```

## Layout

- `gsfluent/server.py` — FastAPI app factory
- `gsfluent/cli.py` — `gsfluent` console-script entry
- `gsfluent/api/` — REST + WebSocket routers (recipes, models, runs, sequences, schemas, stream)
- `gsfluent/core/` — domain logic (library, manifest, runner, frame_stream, recipes)
- `gsfluent/schemas/` — BC + material default schemas
- `gsfluent/static/` — built SPA, served at `/` (gitignored)
- `tests/` — pytest suite (see `pytest -v`)
