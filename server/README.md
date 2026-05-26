# gsfluent server

FastAPI + WebSocket bridge for the gsfluent workbench. Serves the
React SPA built from `../frontend/`, exposes the library/recipes/runs
REST API, and pumps per-frame xyz over `/api/stream` for the Points
render mode.

Pure-Python deps (fastapi, uvicorn, plyfile, pydantic, watchfiles,
numpy). Simulation itself runs on the GPU server, not here
— see `../docs/ARCHITECTURE.md`.

## Install

There is a SINGLE unified `.venv/` at the repo root that holds both
server and client deps (Python 3.12, managed by `uv`). It is normally
created by `npm install` in `../frontend/` (which runs
`frontend/scripts/install.mjs`).

Install the backend + dev/test deps:

```bash
cd server
make install-server        # uv pip install -e .[dev] into ../.venv/
```

That registers the `gsfluent` console script in `../.venv/bin/`. For
the SPA, build it via the frontend:

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

The SPA runs separately on each teammate's machine and renders splats
in-browser — `cd ../frontend && npm install && npm start`.

## Test

```bash
cd server
make test     # PYTHONPATH=. ../.venv/bin/python -m pytest -v
```

## Layout

- `gsfluent/server.py` — FastAPI app factory
- `gsfluent/cli.py` — `gsfluent` console-script entry
- `gsfluent/api/` — REST + WebSocket routers (recipes, models, runs, sequences, schemas, stream)
- `gsfluent/core/` — domain logic (library, manifest, runner, frame_stream, recipes)
- `gsfluent/schemas/` — BC + material default schemas
- `gsfluent/static/` — built SPA, served at `/` (gitignored)
- `tests/` — pytest suite (see `pytest -v`)
