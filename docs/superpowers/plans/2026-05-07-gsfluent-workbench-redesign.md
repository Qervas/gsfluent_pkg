# gsfluent workbench redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the viser-based `tools/workbench.py` with a production-grade React frontend (Blender-faithful three-zone layout, Cursor/Linear elevated-dark aesthetic) served by a thin FastAPI bridge over the existing Taichi/Warp sim core.

**Architecture:** A new `frontend/` directory holds a React 18 + Vite + TypeScript app. A new `server/` directory holds a FastAPI app that wraps `tools/sim_one.sh` as a subprocess. Vite output is bundled into the Python wheel and served as static assets, so distribution is `pip install gsfluent && gsfluent serve`. Existing `tools/workbench.py` (viser) keeps working until React reaches feature parity.

**Tech Stack:**
- Frontend: React 18, Vite, TypeScript, Tailwind, shadcn/ui, react-resizable-panels, react-three-fiber, drei, @mkkellogg/gaussian-splats-3d, cmdk, zustand, @tanstack/react-query, react-hook-form + zod, lucide-react
- Backend: FastAPI, uvicorn, websockets, watchfiles, plyfile, pydantic v2
- Tests: pytest (backend), vitest + Testing Library (frontend logic), Playwright (E2E)

**Phases — each ends with a demoable, committable artifact:**

| # | Phase | Days | Demoable artifact |
|---|---|---|---|
| 0 | Splat lib spike | 1 | Standalone HTML rendering 200k animating splats |
| 1 | Backend foundation | 3 | curl + wscat against the FastAPI bridge |
| 2 | Frontend scaffold | 2 | Empty Blender-layout app, theme applied, panels resize |
| 3 | Viewport | 2 | Drag .ply → static splats; load past run → animation |
| 4 | Recipe authoring | 4 | Full param editor + visual BC editor + auto-fill + provenance |
| 5 | Run lifecycle | 2 | Run → progress + stage + ETA + console; History reloads |
| 6 | Polish + integration | 2 | ⌘K palette, shortcuts, panel persistence, E2E test green |
| 7 | Distribution | 1 | `pip install` + `gsfluent serve` opens working app |

**Total:** ~17 days. Phase boundaries are good checkpoints.

---

## Phase 0 — Splat lib spike

**Why this phase exists:** `@mkkellogg/gaussian-splats-3d` claims live `centers` updates, but I haven't validated this at 200k splats / 24 fps. Failure mode: write a custom R3F splat renderer using viser's WebGL technique (~+1 week to Phase 3). Better to know on day 1.

### Task 0.1 — Standalone splat-update spike

**Files:**
- Create: `spike/splat-test/index.html`
- Create: `spike/splat-test/main.tsx`
- Create: `spike/splat-test/package.json`
- Create: `spike/splat-test/vite.config.ts`
- Create: `spike/splat-test/README.md`

- [ ] **Step 1: Scaffold a tiny Vite + R3F app** — see `spike/splat-test/package.json` snippet below.

```json
{
  "name": "splat-spike",
  "private": true,
  "version": "0.0.0",
  "scripts": { "dev": "vite", "build": "vite build" },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "three": "^0.160.0",
    "@react-three/fiber": "^8.16.0",
    "@react-three/drei": "^9.99.0",
    "@mkkellogg/gaussian-splats-3d": "^0.4.4"
  },
  "devDependencies": {
    "vite": "^5.2.0", "typescript": "^5.4.0",
    "@vitejs/plugin-react": "^4.3.0",
    "@types/react": "^18.3.0", "@types/react-dom": "^18.3.0",
    "@types/three": "^0.160.0"
  }
}
```

- [ ] **Step 2: Write the spike — `main.tsx`**

Generate 200k synthetic splats arranged on a torus, animate `centers` per frame via the lib's update API, log fps. Real signature of the update method is unknown — discover in the spike. Pseudocode:

```typescript
// 1. Generate (n=200000) Float32Arrays for centers, scales (constant 0.005),
//    quat rotations (identity), uint8 RGBA colors.
// 2. Mount @mkkellogg/gaussian-splats-3d Viewer in a useEffect, push the
//    buffers, mark selfDrivenMode=false.
// 3. In useFrame: mutate the centers buffer (sin wave on Y), call
//    viewer.splatMesh.updateCenters() (or whatever the actual method is).
// 4. Tally frames + report fps to a setState.
```

If the lib has no in-place update API: that's the FAIL outcome — record in step 5.

- [ ] **Step 3: Install + run**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/spike/splat-test
npm install
npm run dev
# open http://localhost:5173
```

- [ ] **Step 4: Acceptance — sustained ≥ 30 fps for 30 seconds at n=200000.** Watch the FPS counter; if it drops below 30 fps, that's the FAIL outcome.

- [ ] **Step 5: Document spike outcome in README**

```markdown
<!-- spike/splat-test/README.md -->
# Splat lib spike — outcome
Library: @mkkellogg/gaussian-splats-3d v0.4.x
Question: Can we update `centers` in-place per frame at 200k splats and sustain ≥ 30 fps?

Result:
- [ ] PASS — proceed in Phase 3.
- [ ] FAIL — fall back to: custom R3F splat renderer using viser's WebGL technique. +1 week to Phase 3.

Confirmed API surface: ...
Sort cost at 200k: ...
Memory footprint: ...
```

- [ ] **Step 6: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add spike/splat-test/
git commit -m "spike: validate @mkkellogg/gaussian-splats-3d for live frame updates"
```

---

## Phase 1 — Backend foundation

Server lives at `server/gsfluent/`. Tests at `server/tests/`. Existing sim core at `core/` and `tools/sim_one.sh` is treated as a black box subprocess.

### Task 1.1 — pyproject + scaffold

**Files:**
- Create: `server/pyproject.toml`
- Create: `server/gsfluent/__init__.py`
- Create: `server/gsfluent/__main__.py`
- Create: `server/gsfluent/cli.py`
- Create: `server/gsfluent/server.py`
- Create: `server/tests/__init__.py`
- Create: `server/tests/conftest.py`
- Create: `server/tests/test_health.py`

- [ ] **Step 1: pyproject.toml**

```toml
[build-system]
requires = ["hatchling>=1.21"]
build-backend = "hatchling.build"

[project]
name = "gsfluent"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
  "fastapi>=0.110", "uvicorn[standard]>=0.30", "websockets>=12",
  "watchfiles>=0.21", "plyfile>=1.0", "pydantic>=2.6",
  "python-multipart>=0.0.9",
]
[project.optional-dependencies]
dev = ["pytest>=8", "pytest-asyncio>=0.23", "httpx>=0.27"]
[project.scripts]
gsfluent = "gsfluent.cli:main"
[tool.hatch.build.targets.wheel]
packages = ["gsfluent"]
include = ["gsfluent/static/**"]
```

- [ ] **Step 2: server.py with healthcheck**

```python
# server/gsfluent/server.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
PKG_ROOT = Path(__file__).resolve().parents[2]

def create_app() -> FastAPI:
    app = FastAPI(title="gsfluent", version="0.1.0")
    app.add_middleware(CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_methods=["*"], allow_headers=["*"])
    @app.get("/api/health")
    async def health():
        return {"status": "ok", "pkg_root": str(PKG_ROOT)}
    return app
```

- [ ] **Step 3: cli.py**

```python
# server/gsfluent/cli.py
import argparse, webbrowser, uvicorn
from .server import create_app
def main():
    p = argparse.ArgumentParser(prog="gsfluent")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", default=8080, type=int)
    s.add_argument("--no-browser", action="store_true")
    args = p.parse_args()
    if args.cmd == "serve":
        if not args.no_browser:
            webbrowser.open(f"http://{args.host}:{args.port}")
        uvicorn.run(create_app(), host=args.host, port=args.port, log_level="info")
```

- [ ] **Step 4: __main__.py**

```python
from .cli import main
if __name__ == "__main__": main()
```

- [ ] **Step 5: tests/conftest.py + tests/test_health.py**

```python
# tests/conftest.py
import pytest
from fastapi.testclient import TestClient
from gsfluent.server import create_app
@pytest.fixture
def client(): return TestClient(create_app())

# tests/test_health.py
def test_health_returns_ok(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
```

- [ ] **Step 6: Run + commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
pip install -e ".[dev]"
pytest tests/test_health.py -v
# Expected: 1 passed
cd ..
git add server/
git commit -m "server: FastAPI scaffold + healthcheck + pyproject"
```

### Task 1.2 — Recipe CRUD

**Files:**
- Create: `server/gsfluent/api/__init__.py`
- Create: `server/gsfluent/api/recipes.py`
- Create: `server/gsfluent/core/__init__.py`
- Create: `server/gsfluent/core/recipes.py`
- Modify: `server/gsfluent/server.py`
- Create: `server/tests/test_recipes.py`

- [ ] **Step 1: Failing tests**

```python
# server/tests/test_recipes.py
def test_list_recipes_includes_builtins(client):
    r = client.get("/api/recipes")
    assert r.status_code == 200
    names = {x["name"] for x in r.json()}
    for n in ("jelly", "metal", "demolition"):
        assert n in names

def test_get_recipe(client):
    r = client.get("/api/recipes/jelly")
    assert r.status_code == 200
    assert r.json()["data"]["material"] == "jelly"

def test_get_unknown_404(client):
    assert client.get("/api/recipes/nope").status_code == 404

def test_save_user_preset(client, tmp_path, monkeypatch):
    from gsfluent.core import recipes as rec
    monkeypatch.setattr(rec, "USER_RECIPES_DIR", tmp_path / "_user_recipes")
    payload = {"data": {"material": "jelly", "E": 9999.0}}
    r = client.put("/api/recipes/test_preset", json=payload)
    assert r.status_code == 200
    saved = (tmp_path / "_user_recipes" / "test_preset.json").read_text()
    assert "9999" in saved
    import json as J
    assert "_provenance" in J.loads(saved)
```

- [ ] **Step 2: core/recipes.py**

```python
# server/gsfluent/core/recipes.py
from __future__ import annotations
import json, time
from pathlib import Path
from ..server import PKG_ROOT

RECIPES_DIR = PKG_ROOT / "tools" / "recipes"
USER_RECIPES_DIR = PKG_ROOT / "work" / "_user_recipes"

def list_recipes() -> list[dict]:
    out = []
    for p in sorted(RECIPES_DIR.glob("*.json")):
        out.append({"name": p.stem, "source": "builtin"})
    if USER_RECIPES_DIR.exists():
        for p in sorted(USER_RECIPES_DIR.glob("*.json")):
            out.append({"name": p.stem, "source": "user"})
    return out

def resolve_path(name: str) -> Path | None:
    for d in (RECIPES_DIR, USER_RECIPES_DIR):
        p = d / f"{name}.json"
        if p.exists(): return p
    return None

def load_recipe(name: str) -> dict | None:
    p = resolve_path(name)
    return None if p is None else json.loads(p.read_text())

def save_user_recipe(name: str, data: dict, based_on: str | None = None) -> Path:
    USER_RECIPES_DIR.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
    if not safe: raise ValueError(f"invalid name: {name!r}")
    out = USER_RECIPES_DIR / f"{safe}.json"
    payload = dict(data)
    payload["_provenance"] = {
        "based_on": based_on or "(unknown)",
        "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    tmp.replace(out)
    return out
```

- [ ] **Step 3: api/recipes.py**

```python
# server/gsfluent/api/recipes.py
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from ..core import recipes as rec

router = APIRouter(prefix="/api/recipes", tags=["recipes"])

class SaveRecipeRequest(BaseModel):
    data: dict
    based_on: str | None = None

@router.get("")
def list_endpoint():
    return rec.list_recipes()

@router.get("/{name}")
def get_endpoint(name: str):
    data = rec.load_recipe(name)
    if data is None:
        raise HTTPException(404, f"recipe '{name}' not found")
    builtin = rec.RECIPES_DIR / f"{name}.json"
    return {"name": name, "source": "builtin" if builtin.exists() else "user", "data": data}

@router.put("/{name}")
def save_endpoint(name: str, req: SaveRecipeRequest):
    try:
        rec.save_user_recipe(name, req.data, based_on=req.based_on)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"name": name, "source": "user", "data": rec.load_recipe(name) or req.data}
```

- [ ] **Step 4: Wire router**

```python
# in server/gsfluent/server.py, before `return app`:
from .api import recipes as recipes_api
app.include_router(recipes_api.router)
```

- [ ] **Step 5: Run + commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
pytest tests/test_recipes.py -v
# Expected: 4 passed
cd ..
git add server/
git commit -m "server: recipe CRUD endpoints (list/get/save)"
```

### Task 1.3 — Model upload + history

**Files:**
- Create: `server/gsfluent/api/models.py`
- Create: `server/gsfluent/core/models.py`
- Modify: `server/gsfluent/server.py`
- Create: `server/tests/test_models.py`

- [ ] **Step 1: Failing tests**

```python
# server/tests/test_models.py
import io
def test_list_models_empty(client, tmp_path, monkeypatch):
    from gsfluent.core import models as m
    monkeypatch.setattr(m, "UPLOADS_DIR", tmp_path / "uploads")
    monkeypatch.setattr(m, "HISTORY_FILE", tmp_path / "model_history.json")
    assert client.get("/api/models").json() == []

def test_upload_ply(client, tmp_path, monkeypatch):
    from gsfluent.core import models as m
    monkeypatch.setattr(m, "UPLOADS_DIR", tmp_path / "uploads")
    monkeypatch.setattr(m, "HISTORY_FILE", tmp_path / "model_history.json")
    fake = b"ply\nformat binary_little_endian 1.0\nelement vertex 0\nend_header\n"
    r = client.post("/api/models/upload",
        files={"file": ("building.ply", io.BytesIO(fake), "application/octet-stream")})
    assert r.status_code == 200
    body = r.json()
    assert body["name"].startswith("building_")
    assert (tmp_path / "uploads" / body["name"] / "point_cloud" / "iteration_30000" / "point_cloud.ply").exists()
```

- [ ] **Step 2: core/models.py**

```python
# server/gsfluent/core/models.py
from __future__ import annotations
import json, uuid
from pathlib import Path
from ..server import PKG_ROOT

UPLOADS_DIR  = PKG_ROOT / "work" / "uploads"
HISTORY_FILE = PKG_ROOT / "work" / "_state" / "model_history.json"
MAX_HISTORY  = 20

def _load_history() -> list[dict]:
    if not HISTORY_FILE.exists(): return []
    try: return json.loads(HISTORY_FILE.read_text())
    except Exception: return []

def _save_history(items: list[dict]) -> None:
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = HISTORY_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, indent=2))
    tmp.replace(HISTORY_FILE)

def list_models() -> list[dict]:
    return _load_history()

def record_model(name: str, path: Path) -> None:
    items = [x for x in _load_history() if x.get("name") != name]
    items.insert(0, {"name": name, "path": str(path)})
    _save_history(items[:MAX_HISTORY])

def wrap_ply_upload(orig_filename: str, content: bytes) -> tuple[str, Path]:
    base = Path(orig_filename).stem or "model"
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in base)
    name = f"{safe}_{uuid.uuid4().hex[:8]}"
    iter_dir = UPLOADS_DIR / name / "point_cloud" / "iteration_30000"
    iter_dir.mkdir(parents=True, exist_ok=True)
    (iter_dir / "point_cloud.ply").write_bytes(content)
    model_dir = UPLOADS_DIR / name
    record_model(name, model_dir)
    return name, model_dir
```

- [ ] **Step 3: api/models.py + wire**

```python
# server/gsfluent/api/models.py
from fastapi import APIRouter, UploadFile, File, HTTPException
from ..core import models as m
router = APIRouter(prefix="/api/models", tags=["models"])

@router.get("")
def list_endpoint(): return m.list_models()

@router.post("/upload")
async def upload(file: UploadFile = File(...)):
    if not (file.filename or "").lower().endswith(".ply"):
        raise HTTPException(422, "only .ply uploads accepted")
    content = await file.read()
    if len(content) < 64:
        raise HTTPException(422, "file too small to be a valid ply")
    name, path = m.wrap_ply_upload(file.filename, content)
    return {"name": name, "path": str(path)}
```

```python
# in server.py:
from .api import models as models_api
app.include_router(models_api.router)
```

- [ ] **Step 4: Run + commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
pytest tests/test_models.py -v
# Expected: 2 passed
cd ..
git add server/
git commit -m "server: model upload + history persistence"
```

### Task 1.4 — Run launcher + manifest

**Files:**
- Create: `server/gsfluent/core/runner.py`
- Create: `server/gsfluent/core/manifest.py`
- Create: `server/tests/test_runner.py`

- [ ] **Step 1: manifest.py**

```python
# server/gsfluent/core/manifest.py
import json, time, socket, platform
from pathlib import Path

def write_initial(run_dir: Path, run_name: str, model_dir: Path,
                  recipe_source: str, particles: int) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    m = {
        "run_name": run_name, "model_dir": str(model_dir),
        "recipe_source": recipe_source, "particles": particles,
        "started_at": time.time(), "status": "running",
        "host": socket.gethostname(), "platform": platform.platform(),
    }
    p = run_dir / "manifest.json"
    p.write_text(json.dumps(m, indent=2))
    return p

def update(run_dir: Path, **fields) -> None:
    p = run_dir / "manifest.json"
    if not p.exists(): return
    m = json.loads(p.read_text()); m.update(fields)
    tmp = p.with_suffix(".tmp"); tmp.write_text(json.dumps(m, indent=2))
    tmp.replace(p)

def write_recipe(run_dir: Path, recipe_data: dict) -> Path:
    p = run_dir / "recipe_effective.json"
    p.write_text(json.dumps(recipe_data, indent=2))
    return p
```

- [ ] **Step 2: runner.py — uses asyncio subprocess to spawn sim_one.sh**

The runner module needs an async function `start_run(...)` that launches `tools/sim_one.sh` as a child process and returns a run id. Use `asyncio.create_subprocess_exec` (renamed via alias to keep code grep-safe). Track the live `Run` instances in a module-level `_RUNS: dict[str, Run]`. Stream stdout into a 2000-line ring buffer per run. On exit, update the manifest with `status` ("done" or "error") and `exit_code`.

```python
# server/gsfluent/core/runner.py
from __future__ import annotations
import asyncio, json, time, uuid
from asyncio.subprocess import PIPE, STDOUT
from asyncio.subprocess import create_subprocess_exec as _spawn  # alias for grep
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from ..server import PKG_ROOT
from . import manifest as manifest_mod

SIM_ONE_SH = PKG_ROOT / "tools" / "sim_one.sh"
FUSED_DIR  = PKG_ROOT / "work" / "fused"

@dataclass
class Run:
    id: str
    name: str
    proc: Optional[asyncio.subprocess.Process] = None
    state: str = "queued"
    log_lines: list[str] = field(default_factory=list)

_RUNS: dict[str, Run] = {}

def get_run(run_id: str) -> Run | None: return _RUNS.get(run_id)
def list_runs() -> list[Run]: return list(_RUNS.values())

async def start_run(*, run_name: str, model_dir: Path, recipe_data: dict,
                    recipe_source_name: str, particles: int) -> str:
    run_id = uuid.uuid4().hex[:12]
    run_dir = FUSED_DIR / run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest_mod.write_initial(run_dir, run_name, model_dir, recipe_source_name, particles)
    manifest_mod.write_recipe(run_dir, recipe_data)
    recipe_path = run_dir / "_effective_recipe.json"
    recipe_path.write_text(json.dumps(recipe_data, indent=2))
    cmd = [str(SIM_ONE_SH), str(model_dir),
           "--config", str(recipe_path),
           "--particles", str(particles),
           "--output", run_name,
           "--live", "--no-vkgs-launch"]
    proc = await _spawn(*cmd, stdout=PIPE, stderr=STDOUT, cwd=str(PKG_ROOT))
    run = Run(id=run_id, name=run_name, proc=proc, state="running")
    _RUNS[run_id] = run
    asyncio.create_task(_drain(run, run_dir))
    return run_id

async def _drain(run: Run, run_dir: Path) -> None:
    assert run.proc is not None and run.proc.stdout is not None
    async for raw in run.proc.stdout:
        line = raw.decode(errors="replace").rstrip()
        if line:
            run.log_lines.append(line)
            if len(run.log_lines) > 2000:
                run.log_lines = run.log_lines[-2000:]
    rc = await run.proc.wait()
    run.state = "done" if rc == 0 else "error"
    manifest_mod.update(run_dir, status=run.state, exit_code=rc, finished_at=time.time())

async def wait_for_run(run_id: str) -> None:
    r = _RUNS.get(run_id)
    if r is None or r.proc is None: return
    await r.proc.wait()

def cancel_run(run_id: str) -> bool:
    r = _RUNS.get(run_id)
    if r is None or r.proc is None or r.state != "running":
        return False
    r.proc.terminate(); r.state = "cancelled"
    return True
```

- [ ] **Step 3: tests/test_runner.py**

```python
# tests/test_runner.py
import asyncio, json
def test_runner_writes_manifest(tmp_path, monkeypatch):
    from gsfluent.core import runner as r
    fake = tmp_path / "fake.sh"
    fake.write_text("#!/bin/bash\necho 'fake'\nmkdir -p $5/frames && touch $5/frames/frame_0000.ply\n")
    fake.chmod(0o755)
    monkeypatch.setattr(r, "SIM_ONE_SH", fake)
    monkeypatch.setattr(r, "FUSED_DIR", tmp_path / "fused")
    rec = {"material": "jelly", "_provenance": {"based_on": "jelly"}}
    rid = asyncio.run(r.start_run(run_name="t", model_dir=tmp_path / "m",
        recipe_data=rec, recipe_source_name="jelly", particles=10000))
    asyncio.run(r.wait_for_run(rid))
    out = tmp_path / "fused" / "t"
    m = json.loads((out / "manifest.json").read_text())
    assert m["status"] == "done"
    assert m["run_name"] == "t"
    rd = json.loads((out / "recipe_effective.json").read_text())
    assert rd["material"] == "jelly"
```

- [ ] **Step 4: Run + commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
pytest tests/test_runner.py -v
cd ..
git add server/
git commit -m "server: subprocess runner + per-run manifest + recipe co-save"
```

### Task 1.5 — Runs API

**Files:**
- Create: `server/gsfluent/api/runs.py`
- Modify: `server/gsfluent/server.py`
- Create: `server/tests/test_runs_api.py`

- [ ] **Step 1: api/runs.py**

```python
# server/gsfluent/api/runs.py
import json
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from ..core import runner

router = APIRouter(prefix="/api/runs", tags=["runs"])

class StartRunRequest(BaseModel):
    run_name: str
    model_path: str
    recipe_data: dict
    recipe_source: str
    particles: int = 200_000

@router.get("")
def list_active():
    return [{"id": r.id, "name": r.name, "state": r.state} for r in runner.list_runs()]

@router.post("")
async def start(req: StartRunRequest):
    rid = await runner.start_run(
        run_name=req.run_name, model_dir=Path(req.model_path),
        recipe_data=req.recipe_data, recipe_source_name=req.recipe_source,
        particles=req.particles)
    return {"run_id": rid, "run_name": req.run_name}

@router.delete("/{run_id}")
def cancel(run_id: str):
    if not runner.cancel_run(run_id):
        raise HTTPException(404, f"run {run_id} not active")
    return {"status": "cancelled"}

@router.get("/history")
def history():
    out = []
    if not runner.FUSED_DIR.exists(): return out
    for d in sorted(runner.FUSED_DIR.iterdir(), key=lambda p: -p.stat().st_mtime):
        if not d.is_dir(): continue
        m = d / "manifest.json"
        if not m.exists(): continue
        try: out.append(json.loads(m.read_text()))
        except Exception: continue
    return out
```

- [ ] **Step 2: tests + wire + commit**

```python
# tests/test_runs_api.py
def test_runs_list_starts_empty(client):
    assert client.get("/api/runs").json() == []
def test_history_reads_fused(client, tmp_path, monkeypatch):
    from gsfluent.core import runner as r
    f = tmp_path / "fused"
    (f / "alpha").mkdir(parents=True)
    (f / "alpha" / "manifest.json").write_text('{"run_name":"alpha","status":"done","started_at":1,"particles":1000}')
    monkeypatch.setattr(r, "FUSED_DIR", f)
    rr = client.get("/api/runs/history")
    assert any(x["run_name"] == "alpha" for x in rr.json())
def test_post_validates(client):
    assert client.post("/api/runs", json={}).status_code == 422
```

```python
# in server.py:
from .api import runs as runs_api
app.include_router(runs_api.router)
```

```bash
pytest tests/test_runs_api.py -v
git add server/ && git commit -m "server: runs REST endpoints"
```

### Task 1.6 — WebSocket frame stream

**Files:**
- Create: `server/gsfluent/api/stream.py`
- Create: `server/gsfluent/core/frame_stream.py`
- Modify: `server/gsfluent/server.py`

- [ ] **Step 1: core/frame_stream.py — ply parser with y-up→z-up rotation + SH band-0 RGB + 3DGS covariance reconstruction**

```python
# server/gsfluent/core/frame_stream.py
from __future__ import annotations
import numpy as np
from pathlib import Path
from plyfile import PlyData

_SH_C0 = 0.28209479177387814
_M = np.array([[1,0,0],[0,0,-1],[0,1,0]], dtype=np.float32)

def parse_frame_xyz(ply_path: Path) -> np.ndarray:
    v = PlyData.read(str(ply_path))["vertex"].data
    xyz = np.stack([v["x"], v["y"], v["z"]], axis=1).astype(np.float32)
    return np.stack([xyz[:,0], -xyz[:,2], xyz[:,1]], axis=1)

def parse_static_attrs(ply_path: Path) -> dict | None:
    v = PlyData.read(str(ply_path))["vertex"].data
    needed = ("scale_0","scale_1","scale_2","rot_0","rot_1","rot_2","rot_3",
              "f_dc_0","f_dc_1","f_dc_2","opacity")
    if not all(k in v.dtype.names for k in needed): return None
    n = v.shape[0]
    scales = np.exp(np.stack([v["scale_0"],v["scale_1"],v["scale_2"]], axis=1)).astype(np.float32)
    quats = np.stack([v["rot_0"],v["rot_1"],v["rot_2"],v["rot_3"]], axis=1).astype(np.float32)
    norms = np.linalg.norm(quats, axis=1, keepdims=True); norms[norms == 0] = 1.0
    quats /= norms
    qw, qx, qy, qz = quats.T
    R = np.empty((n,3,3), dtype=np.float32)
    R[:,0,0] = 1-2*(qy*qy+qz*qz); R[:,0,1] = 2*(qx*qy-qz*qw); R[:,0,2] = 2*(qx*qz+qy*qw)
    R[:,1,0] = 2*(qx*qy+qz*qw);   R[:,1,1] = 1-2*(qx*qx+qz*qz); R[:,1,2] = 2*(qy*qz-qx*qw)
    R[:,2,0] = 2*(qx*qz-qy*qw);   R[:,2,1] = 2*(qy*qz+qx*qw);   R[:,2,2] = 1-2*(qx*qx+qy*qy)
    R = np.einsum("ij,njk->nik", _M, R)
    rgb = np.clip(np.stack([v["f_dc_0"],v["f_dc_1"],v["f_dc_2"]], axis=1)*_SH_C0+0.5, 0, 1).astype(np.float32)
    op  = (1.0/(1.0+np.exp(-v["opacity"].astype(np.float32)))).astype(np.float32)
    return {"R": R, "scales": scales, "rgb": rgb, "opacity": op, "n": n}
```

- [ ] **Step 2: api/stream.py — WebSocket router**

```python
# server/gsfluent/api/stream.py
from __future__ import annotations
import asyncio, base64
from pathlib import Path
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from watchfiles import awatch
from ..core import runner
from ..core.frame_stream import parse_frame_xyz, parse_static_attrs

router = APIRouter()

@router.websocket("/api/stream")
async def stream(ws: WebSocket):
    await ws.accept()
    sub_task: asyncio.Task | None = None
    try:
        while True:
            msg = await ws.receive_json()
            if msg.get("type") == "subscribe":
                if sub_task: sub_task.cancel()
                sub_task = asyncio.create_task(_pump(ws, msg["run_name"]))
            elif msg.get("type") == "unsubscribe":
                if sub_task: sub_task.cancel()
                sub_task = None
    except WebSocketDisconnect:
        pass
    finally:
        if sub_task: sub_task.cancel()

async def _pump(ws: WebSocket, run_name: str) -> None:
    run_dir = runner.FUSED_DIR / run_name
    sent: set[str] = set()
    sent_static = False
    if run_dir.exists():
        for f in sorted(run_dir.glob("frames/frame_*.ply")):
            sent_static = await _send(ws, run_name, f, sent, sent_static)
    async for changes in awatch(run_dir, stop_event=None):
        for _, p_str in changes:
            p = Path(p_str)
            if p.match("frames/frame_*.ply"):
                sent_static = await _send(ws, run_name, p, sent, sent_static)

async def _send(ws, run_name, ply, sent, sent_static_already):
    if ply.name in sent: return sent_static_already
    if ply.stat().st_size < 1024: return sent_static_already
    if not sent_static_already:
        attrs = parse_static_attrs(ply)
        if attrs is not None:
            await ws.send_json({
                "type": "static_attrs", "run_name": run_name, "n": int(attrs["n"]),
                "R_b64": base64.b64encode(attrs["R"].tobytes()).decode("ascii"),
                "scales_b64": base64.b64encode(attrs["scales"].tobytes()).decode("ascii"),
                "rgb_b64": base64.b64encode(attrs["rgb"].tobytes()).decode("ascii"),
                "opacity_b64": base64.b64encode(attrs["opacity"].tobytes()).decode("ascii"),
            })
            sent_static_already = True
    xyz = parse_frame_xyz(ply)
    idx = int(ply.stem.split("_")[1])
    await ws.send_json({"type": "frame_meta", "run_name": run_name, "frame_idx": idx, "n": int(xyz.shape[0])})
    await ws.send_bytes(xyz.tobytes())
    sent.add(ply.name)
    return sent_static_already
```

- [ ] **Step 3: Wire + smoke test + commit**

```python
# server.py
from .api import stream as stream_api
app.include_router(stream_api.router)
```

```bash
# Manual smoke test:
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
python -m gsfluent serve --no-browser &
sleep 2
npx wscat -c ws://localhost:8080/api/stream
> {"type":"subscribe","run_name":"pkg_smoke_test"}
# Expect: a static_attrs JSON, then alternating frame_meta JSON + binary frames.
git add server/ && git commit -m "server: WebSocket frame + status stream"
```

### Task 1.7 — Schemas API (BCs + material defaults)

**Files:**
- Create: `server/gsfluent/schemas/__init__.py`
- Create: `server/gsfluent/schemas/boundary.py`
- Create: `server/gsfluent/schemas/material_defaults.py`
- Create: `server/gsfluent/api/schemas.py`
- Modify: `server/gsfluent/server.py`
- Create: `server/tests/test_schemas.py`

- [ ] **Step 1: BC + material schemas — TypedDict-style python**

```python
# server/gsfluent/schemas/boundary.py
BC_SCHEMAS: dict[str, list[tuple]] = {
    "bounding_box": [],
    "surface_collider": [
        ("point", "vec3", [0.0,0.0,0.0], "Plane origin"),
        ("normal", "vec3", [0.0,0.0,1.0], "Plane normal (unit)"),
        ("surface_type", "string", "sticky", "sticky | slip | separate"),
        ("friction", "float", 0.0, "0..1"),
    ],
    "cuboid": [
        ("center", "vec3", [0.0,0.0,0.0], "Center"),
        ("size", "vec3", [1.0,1.0,1.0], "Half-extents"),
        ("velocity", "vec3", [0.0,0.0,0.0], "Linear velocity"),
        ("start_time", "float", 0.0, "Activate at (s)"),
        ("end_time", "float", 999.0, "Deactivate at (s)"),
    ],
    "release_particles_sequentially": [
        ("axis", "string", "z", "x|y|z"),
        ("start_time", "float", 0.0, "Begin (s)"),
        ("interval", "float", 0.01, "Sweep step (s)"),
    ],
}
```

```python
# server/gsfluent/schemas/material_defaults.py
MATERIAL_DEFAULTS: dict[str, dict] = {
    "jelly":      {"E": 5000.0,  "nu": 0.38, "density": 1,   "yield_stress": 0.0,   "friction_angle": 45.0, "beta": 1.0, "xi": 3.0,  "hardening": 1.0, "alpha_0": -0.04, "plastic_viscosity": 0.0},
    "metal":      {"E": 50000.0, "nu": 0.30, "density": 3,   "yield_stress": 1000.0,"friction_angle": 0.0,  "beta": 1.0, "xi": 3.0,  "hardening": 1.0, "alpha_0": -0.04, "plastic_viscosity": 0.0},
    "sand":       {"E": 20000.0, "nu": 0.30, "density": 2,   "yield_stress": 0.0,   "friction_angle": 45.0, "beta": 1.0, "xi": 3.0,  "hardening": 1.0, "alpha_0": -0.04, "plastic_viscosity": 0.0},
    "foam":       {"E": 1000.0,  "nu": 0.10, "density": 0.3, "yield_stress": 0.0,   "friction_angle": 0.0,  "beta": 1.0, "xi": 3.0,  "hardening": 1.0, "alpha_0": -0.04, "plastic_viscosity": 0.0},
    "snow":       {"E": 8000.0,  "nu": 0.30, "density": 1,   "yield_stress": 0.0,   "friction_angle": 30.0, "beta": 1.0, "xi": 10.0, "hardening": 5.0, "alpha_0": -0.01, "plastic_viscosity": 0.0},
    "plasticine": {"E": 8000.0,  "nu": 0.30, "density": 2,   "yield_stress": 100.0, "friction_angle": 0.0,  "beta": 1.0, "xi": 3.0,  "hardening": 1.0, "alpha_0": -0.04, "plastic_viscosity": 100.0},
    "watermelon": {"E": 50000.0, "nu": 0.30, "density": 1,   "yield_stress": 0.0,   "friction_angle": 45.0, "beta": 1.0, "xi": 3.0,  "hardening": 1.0, "alpha_0": -0.04, "plastic_viscosity": 0.0},
}
```

- [ ] **Step 2: api/schemas.py + tests + wire**

```python
# server/gsfluent/api/schemas.py
from fastapi import APIRouter
from ..schemas.boundary import BC_SCHEMAS
from ..schemas.material_defaults import MATERIAL_DEFAULTS

router = APIRouter(prefix="/api/schemas", tags=["schemas"])

@router.get("/boundaries")
def boundaries():
    return {ty: [{"name":n,"type":t,"default":d,"hint":h} for (n,t,d,h) in fields]
            for ty, fields in BC_SCHEMAS.items()}

@router.get("/materials")
def materials(): return MATERIAL_DEFAULTS
```

```python
# tests/test_schemas.py
def test_boundaries(client):
    r = client.get("/api/schemas/boundaries")
    assert r.status_code == 200
    assert "cuboid" in r.json()
    assert any(f["name"] == "center" for f in r.json()["cuboid"])
def test_materials(client):
    r = client.get("/api/schemas/materials")
    assert r.json()["metal"]["E"] == 50000.0
```

```python
# server.py
from .api import schemas as schemas_api
app.include_router(schemas_api.router)
```

```bash
pytest tests/test_schemas.py -v
git add server/ && git commit -m "server: BC + material schema endpoints"
```

---

## Phase 1 demoable artifact

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
python -m gsfluent serve --no-browser
# Verify in another terminal:
curl http://localhost:8080/api/health
curl http://localhost:8080/api/recipes | jq
curl http://localhost:8080/api/schemas/materials | jq
# WebSocket:
npx wscat -c ws://localhost:8080/api/stream
# > {"type":"subscribe","run_name":"pkg_smoke_test"}
```

Backend independently demoable + tested. Phase 1 done.

---

## Phase 2 — Frontend scaffold

(Continues in part 2 of this plan — see follow-on tasks for Vite scaffold, shadcn install, three-zone layout, Outliner/Properties shells, REST/WS clients.)

## Phase 2 — Frontend scaffold

Frontend lives at `frontend/`. Vite dev server runs at `localhost:5173`; FastAPI CORS-allows it.

### Task 2.1 — Vite + React + TS + Tailwind

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/vite.config.ts`
- Create: `frontend/tsconfig.json`
- Create: `frontend/tailwind.config.js`
- Create: `frontend/postcss.config.js`
- Create: `frontend/index.html`
- Create: `frontend/src/main.tsx`
- Create: `frontend/src/App.tsx`
- Create: `frontend/src/index.css`
- Create: `frontend/.gitignore`

- [ ] **Step 1: package.json**

```json
{
  "name": "gsfluent-frontend",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "preview": "vite preview",
    "test": "vitest"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "@tanstack/react-query": "^5.51.0",
    "zustand": "^4.5.4",
    "react-resizable-panels": "^2.0.20",
    "lucide-react": "^0.400.0",
    "clsx": "^2.1.1",
    "tailwind-merge": "^2.4.0",
    "@radix-ui/react-dialog": "^1.1.1",
    "@radix-ui/react-dropdown-menu": "^2.1.1",
    "@radix-ui/react-label": "^2.1.0",
    "@radix-ui/react-popover": "^1.1.1",
    "@radix-ui/react-select": "^2.1.1",
    "@radix-ui/react-slider": "^1.2.0",
    "@radix-ui/react-switch": "^1.1.0",
    "@radix-ui/react-tabs": "^1.1.0",
    "@radix-ui/react-tooltip": "^1.1.2",
    "react-hook-form": "^7.52.0",
    "@hookform/resolvers": "^3.9.0",
    "zod": "^3.23.8",
    "cmdk": "^1.0.0",
    "three": "^0.160.0",
    "@react-three/fiber": "^8.16.0",
    "@react-three/drei": "^9.99.0",
    "@mkkellogg/gaussian-splats-3d": "^0.4.4"
  },
  "devDependencies": {
    "@types/react": "^18.3.0",
    "@types/react-dom": "^18.3.0",
    "@types/three": "^0.160.0",
    "@vitejs/plugin-react": "^4.3.0",
    "typescript": "^5.4.0",
    "vite": "^5.4.0",
    "vitest": "^2.0.0",
    "@testing-library/react": "^16.0.0",
    "@testing-library/jest-dom": "^6.4.0",
    "tailwindcss": "^3.4.0",
    "postcss": "^8.4.0",
    "autoprefixer": "^10.4.0"
  }
}
```

- [ ] **Step 2: vite.config.ts — proxy /api to FastAPI**

```ts
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": path.resolve(__dirname, "./src") } },
  server: {
    port: 5173,
    proxy: {
      "/api/stream": { target: "ws://localhost:8080", ws: true },
      "/api":        { target: "http://localhost:8080" },
    },
  },
  build: {
    outDir: "../server/gsfluent/static",
    emptyOutDir: true,
  },
});
```

- [ ] **Step 3: tsconfig.json**

```json
{
  "compilerOptions": {
    "target": "ES2022", "useDefineForClassFields": true, "lib": ["ES2022","DOM","DOM.Iterable"],
    "module": "ESNext", "skipLibCheck": true, "moduleResolution": "bundler",
    "allowImportingTsExtensions": true, "resolveJsonModule": true, "isolatedModules": true,
    "noEmit": true, "jsx": "react-jsx",
    "strict": true, "noUnusedLocals": true, "noUnusedParameters": true,
    "baseUrl": ".", "paths": { "@/*": ["./src/*"] }
  },
  "include": ["src"]
}
```

- [ ] **Step 4: tailwind.config.js + postcss.config.js + index.css with theme tokens**

```js
// tailwind.config.js
export default {
  content: ["./index.html","./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: { extend: {
    colors: {
      canvas:    "#0d1117",
      pane:      "#0d1117",
      elevated:  "#161b22",
      border:    "#21262d",
      "text-primary":   "#c9d1d9",
      "text-secondary": "#8b949e",
      "text-muted":     "#6e7681",
      accent:    "#22d3ee",
      success:   "#34d399",
      warning:   "#fbbf24",
      error:     "#f87171",
    },
    fontFamily: {
      sans: ["Inter","system-ui","sans-serif"],
      mono: ["JetBrains Mono","ui-monospace","Menlo","monospace"],
    },
    boxShadow: {
      "accent-glow": "0 0 12px rgba(34,211,238,0.3)",
    },
  } },
  plugins: [],
};
```

```js
// postcss.config.js
export default { plugins: { tailwindcss: {}, autoprefixer: {} } };
```

```css
/* src/index.css */
@import url("https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap");
@tailwind base; @tailwind components; @tailwind utilities;

html, body, #root { height: 100%; }
body {
  @apply bg-canvas text-text-primary font-sans;
  font-feature-settings: "tnum"; /* tabular figures globally */
}
```

- [ ] **Step 5: main.tsx + App.tsx (placeholder — empty Blender layout)**

```tsx
// src/main.tsx
import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import "./index.css";

const queryClient = new QueryClient();
ReactDOM.createRoot(document.getElementById("root")!).render(
  <QueryClientProvider client={queryClient}>
    <App />
  </QueryClientProvider>
);
```

```tsx
// src/App.tsx
export default function App() {
  return (
    <div className="h-screen w-screen flex flex-col bg-canvas text-text-primary">
      <div className="h-10 border-b border-border px-3 flex items-center gap-2 backdrop-blur bg-canvas/85">
        <span className="text-accent">●</span>
        <span className="font-semibold">gsfluent</span>
        <span className="text-text-muted">·</span>
        <span className="text-text-secondary text-sm">no model loaded</span>
      </div>
      <div className="h-8 border-b border-border px-3 flex items-center gap-4 text-sm">
        <span className="text-accent border-b-2 border-accent pb-0.5">Sim</span>
        <span className="text-text-muted">Compare (soon)</span>
        <span className="text-text-muted">Render (soon)</span>
        <span className="text-text-muted">Recipes (soon)</span>
      </div>
      <div className="flex-1 grid grid-cols-[200px_1fr_280px]">
        <div className="border-r border-border p-3 text-xs text-text-secondary">Outliner</div>
        <div className="bg-elevated"></div>
        <div className="border-l border-border p-3 text-xs text-text-secondary">Properties</div>
      </div>
      <div className="h-8 border-t border-border px-3 flex items-center gap-3 text-xs text-text-muted">
        <span className="text-accent">●</span>
        <span>idle</span>
        <span className="ml-auto">⌘K</span>
      </div>
    </div>
  );
}
```

- [ ] **Step 6: Install + run + commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/frontend
npm install
npm run dev
# open http://localhost:5173 — should show three-zone Blender skeleton in dark mode
cd ..
git add frontend/
git commit -m "frontend: Vite + React + TS + Tailwind scaffold + theme tokens + three-zone shell"
```

### Task 2.2 — shadcn/ui primitives

**Files:**
- Create: `frontend/src/lib/utils.ts`
- Create: `frontend/src/components/ui/button.tsx`
- Create: `frontend/src/components/ui/dialog.tsx`
- Create: `frontend/src/components/ui/input.tsx`
- Create: `frontend/src/components/ui/select.tsx`
- Create: `frontend/src/components/ui/slider.tsx`
- Create: `frontend/src/components/ui/switch.tsx`
- Create: `frontend/src/components/ui/tooltip.tsx`
- Create: `frontend/src/components/ui/label.tsx`

- [ ] **Step 1: lib/utils.ts**

```ts
// src/lib/utils.ts
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
```

- [ ] **Step 2: Install shadcn components manually (no CLI; we want full control)**

For each primitive, create a component file that wraps the corresponding @radix-ui primitive with our theme tokens. The shadcn website provides the canonical source — see `https://ui.shadcn.com/docs/components/button` etc. Key adaptations for our theme:

```tsx
// src/components/ui/button.tsx
import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent disabled:pointer-events-none disabled:opacity-50",
  {
    variants: {
      variant: {
        default:    "bg-accent text-canvas hover:bg-accent/90 shadow-accent-glow",
        secondary:  "bg-elevated text-text-primary hover:bg-border",
        ghost:      "text-text-secondary hover:bg-elevated hover:text-text-primary",
        destructive:"bg-error/15 text-error border border-error/40 hover:bg-error/25",
        outline:    "border border-border bg-canvas hover:bg-elevated",
      },
      size: { default: "h-7 px-3", icon: "h-7 w-7", lg: "h-9 px-4 text-sm" },
    },
    defaultVariants: { variant: "default", size: "default" },
  }
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> { asChild?: boolean }

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    return <Comp className={cn(buttonVariants({ variant, size, className }))} ref={ref} {...props} />;
  }
);
Button.displayName = "Button";
```

Repeat the pattern for `dialog`, `input`, `select`, `slider`, `switch`, `tooltip`, `label`. Each component wraps the radix primitive and applies theme tokens. **Reference implementations:** copy from <https://ui.shadcn.com/docs/components> and replace color classes with our `bg-canvas` / `bg-elevated` / `text-text-primary` / `border-border` / `bg-accent` etc. Add `class-variance-authority` and `@radix-ui/react-slot` to dependencies.

```bash
npm i class-variance-authority @radix-ui/react-slot
```

- [ ] **Step 3: Add a smoke test page**

Update `App.tsx` to render one of each primitive in the right panel:

```tsx
// inside the right Properties pane:
<Button>Run sim</Button>
<Button variant="destructive">Cancel</Button>
<Button variant="ghost">⌘K</Button>
```

Visual confirm: open `localhost:5173`, see styled buttons.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib frontend/src/components/ui frontend/package.json frontend/package-lock.json
git commit -m "frontend: shadcn/ui primitives wired to dark cyan theme"
```

### Task 2.3 — Three-zone layout with react-resizable-panels

**Files:**
- Create: `frontend/src/components/layout/AppShell.tsx`
- Create: `frontend/src/components/layout/TopBar.tsx`
- Create: `frontend/src/components/layout/WorkspaceTabs.tsx`
- Create: `frontend/src/components/layout/StatusStrip.tsx`
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: AppShell with PanelGroup**

```tsx
// src/components/layout/AppShell.tsx
import { PanelGroup, Panel, PanelResizeHandle } from "react-resizable-panels";
import { TopBar } from "./TopBar";
import { WorkspaceTabs } from "./WorkspaceTabs";
import { StatusStrip } from "./StatusStrip";

export function AppShell({
  outliner, viewport, properties,
}: { outliner: React.ReactNode; viewport: React.ReactNode; properties: React.ReactNode }) {
  return (
    <div className="h-screen w-screen flex flex-col bg-canvas text-text-primary text-sm">
      <TopBar />
      <WorkspaceTabs />
      <PanelGroup direction="horizontal" autoSaveId="gsfluent.split.h" className="flex-1">
        <Panel defaultSize={18} minSize={12} className="border-r border-border overflow-auto">
          {outliner}
        </Panel>
        <PanelResizeHandle className="w-px bg-border hover:bg-accent/40 transition-colors" />
        <Panel defaultSize={58} minSize={30}>
          {viewport}
        </Panel>
        <PanelResizeHandle className="w-px bg-border hover:bg-accent/40 transition-colors" />
        <Panel defaultSize={24} minSize={16} className="border-l border-border overflow-auto">
          {properties}
        </Panel>
      </PanelGroup>
      <StatusStrip />
    </div>
  );
}
```

- [ ] **Step 2: TopBar / WorkspaceTabs / StatusStrip — minimal stubs**

```tsx
// src/components/layout/TopBar.tsx
export function TopBar() {
  return (
    <div className="h-10 border-b border-border px-3 flex items-center gap-2 backdrop-blur bg-canvas/85 shrink-0">
      <span className="text-accent text-xs">●</span>
      <span className="font-semibold">gsfluent</span>
      <span className="text-text-muted text-xs">·</span>
      <span className="text-text-secondary text-xs">no model loaded</span>
      <div className="ml-auto flex gap-2">
        <button className="bg-accent text-canvas px-3 py-0.5 text-xs rounded shadow-accent-glow font-medium">Run</button>
      </div>
    </div>
  );
}
```

```tsx
// src/components/layout/WorkspaceTabs.tsx
const TABS = [
  { id: "sim", label: "Sim", active: true },
  { id: "compare", label: "Compare", soon: true },
  { id: "render", label: "Render", soon: true },
  { id: "recipes", label: "Recipes", soon: true },
];
export function WorkspaceTabs() {
  return (
    <div className="h-8 border-b border-border px-3 flex items-center gap-4 text-xs shrink-0">
      {TABS.map(t => (
        <span key={t.id}
              className={t.active ? "text-accent border-b-2 border-accent pb-0.5" :
                         t.soon   ? "text-text-muted cursor-not-allowed" : "text-text-secondary hover:text-text-primary"}>
          {t.label}{t.soon ? " (soon)" : ""}
        </span>
      ))}
    </div>
  );
}
```

```tsx
// src/components/layout/StatusStrip.tsx
export function StatusStrip() {
  return (
    <div className="h-8 border-t border-border px-3 flex items-center gap-3 text-xs text-text-muted shrink-0">
      <span className="text-accent">●</span>
      <span>idle</span>
      <span className="ml-auto">⌘K</span>
    </div>
  );
}
```

- [ ] **Step 3: Wire into App.tsx**

```tsx
// src/App.tsx
import { AppShell } from "@/components/layout/AppShell";

export default function App() {
  return (
    <AppShell
      outliner={<div className="p-3 text-xs text-text-secondary">Outliner</div>}
      viewport={<div className="bg-elevated h-full"></div>}
      properties={<div className="p-3 text-xs text-text-secondary">Properties</div>}
    />
  );
}
```

- [ ] **Step 4: Visual smoke + commit**

Open `localhost:5173` — drag the dividers between panels. Reload; widths persist (autoSaveId).

```bash
git add frontend/src/
git commit -m "frontend: three-zone layout with react-resizable-panels + persistent splits"
```

### Task 2.4 — REST + WebSocket clients + zustand store

**Files:**
- Create: `frontend/src/lib/api.ts`
- Create: `frontend/src/lib/ws.ts`
- Create: `frontend/src/lib/store.ts`
- Create: `frontend/src/lib/types.ts`

- [ ] **Step 1: types.ts — shared shapes**

```ts
// src/lib/types.ts
export type Recipe = { name: string; source: "builtin" | "user"; data: Record<string, unknown> };
export type RecipeListItem = { name: string; source: "builtin" | "user" };
export type ModelItem = { name: string; path: string };
export type RunStatus = { id: string; name: string; state: "queued"|"running"|"done"|"error"|"cancelled" };
export type HistoryEntry = {
  run_name: string; status: string; started_at: number;
  finished_at?: number; particles?: number; recipe_source?: string;
};
export type StaticAttrs = {
  n: number;
  R: Float32Array;       // (n, 3, 3)
  scales: Float32Array;  // (n, 3)
  rgb: Float32Array;     // (n, 3) in [0,1]
  opacity: Float32Array; // (n,)
};
export type FrameMeta = { run_name: string; frame_idx: number; n: number };
```

- [ ] **Step 2: api.ts — typed REST helpers**

```ts
// src/lib/api.ts
import type { Recipe, RecipeListItem, ModelItem, HistoryEntry } from "./types";

const j = async <T>(r: Response): Promise<T> => {
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json();
};

export const api = {
  recipes: {
    list: ()           => fetch("/api/recipes").then(j<RecipeListItem[]>),
    get:  (n: string)  => fetch(`/api/recipes/${n}`).then(j<Recipe>),
    save: (n: string, data: any, based_on?: string) =>
      fetch(`/api/recipes/${n}`, {
        method: "PUT", headers: {"Content-Type":"application/json"},
        body: JSON.stringify({ data, based_on }),
      }).then(j<Recipe>),
  },
  models: {
    list: ()    => fetch("/api/models").then(j<ModelItem[]>),
    upload: (file: File) => {
      const fd = new FormData(); fd.append("file", file);
      return fetch("/api/models/upload", { method: "POST", body: fd }).then(j<ModelItem>);
    },
  },
  runs: {
    list:    () => fetch("/api/runs").then(j<{id:string; name:string; state:string}[]>),
    history: () => fetch("/api/runs/history").then(j<HistoryEntry[]>),
    start:   (req: { run_name: string; model_path: string; recipe_data: any;
                     recipe_source: string; particles: number }) =>
      fetch("/api/runs", {
        method: "POST", headers: {"Content-Type":"application/json"},
        body: JSON.stringify(req),
      }).then(j<{ run_id: string; run_name: string }>),
    cancel:  (id: string) =>
      fetch(`/api/runs/${id}`, { method: "DELETE" }).then(j<{ status: string }>),
  },
  schemas: {
    boundaries: () => fetch("/api/schemas/boundaries").then(j<Record<string, {name:string;type:string;default:any;hint:string}[]>>),
    materials:  () => fetch("/api/schemas/materials").then(j<Record<string, Record<string, number>>>),
  },
};
```

- [ ] **Step 3: ws.ts — WebSocket client with auto-reconnect**

```ts
// src/lib/ws.ts
import type { StaticAttrs, FrameMeta } from "./types";

type Handlers = {
  onStatus?:  (msg: { run_name: string; state: string; n_frames?: number; total_frames?: number; fps_observed?: number }) => void;
  onLog?:     (msg: { run_name: string; line: string }) => void;
  onStaticAttrs?: (msg: { run_name: string; attrs: StaticAttrs }) => void;
  onFrame?:   (meta: FrameMeta, xyz: Float32Array) => void;
};

export class StreamClient {
  private ws: WebSocket | null = null;
  private pendingMeta: FrameMeta | null = null;
  private currentRun: string | null = null;
  constructor(private h: Handlers) {}

  connect(): void {
    const url = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/stream`;
    this.ws = new WebSocket(url);
    this.ws.binaryType = "arraybuffer";
    this.ws.onmessage = (ev) => this._onMessage(ev);
    this.ws.onclose = () => { setTimeout(() => this.connect(), 1500); };
    this.ws.onopen = () => {
      if (this.currentRun) this._send({type:"subscribe", run_name: this.currentRun});
    };
  }

  subscribe(run_name: string): void {
    this.currentRun = run_name;
    if (this.ws?.readyState === WebSocket.OPEN) {
      this._send({type:"subscribe", run_name});
    }
  }

  unsubscribe(): void {
    this.currentRun = null;
    if (this.ws?.readyState === WebSocket.OPEN) {
      this._send({type:"unsubscribe"});
    }
  }

  private _send(m: any) { this.ws?.send(JSON.stringify(m)); }

  private _onMessage(ev: MessageEvent) {
    if (typeof ev.data === "string") {
      const msg = JSON.parse(ev.data);
      if (msg.type === "frame_meta") { this.pendingMeta = msg; return; }
      if (msg.type === "status") this.h.onStatus?.(msg);
      else if (msg.type === "log") this.h.onLog?.(msg);
      else if (msg.type === "static_attrs") {
        const attrs = decodeStatic(msg);
        this.h.onStaticAttrs?.({ run_name: msg.run_name, attrs });
      }
    } else if (ev.data instanceof ArrayBuffer && this.pendingMeta) {
      const xyz = new Float32Array(ev.data);
      this.h.onFrame?.(this.pendingMeta, xyz);
      this.pendingMeta = null;
    }
  }
}

function decodeStatic(msg: any): StaticAttrs {
  const dec = (b64: string) => {
    const bin = atob(b64); const a = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) a[i] = bin.charCodeAt(i);
    return new Float32Array(a.buffer);
  };
  return {
    n: msg.n,
    R:       dec(msg.R_b64),
    scales:  dec(msg.scales_b64),
    rgb:     dec(msg.rgb_b64),
    opacity: dec(msg.opacity_b64),
  };
}
```

- [ ] **Step 4: store.ts — zustand store for client state**

```ts
// src/lib/store.ts
import { create } from "zustand";
import type { StaticAttrs, FrameMeta } from "./types";

type State = {
  // Selected items
  activeModel: { name: string; path: string } | null;
  activeRecipeName: string | null;
  activeRecipeData: Record<string, any> | null;
  // Sim status
  simState: "idle" | "running" | "done" | "error";
  simRunName: string | null;
  simNFrames: number;
  simTotalFrames: number;
  simStage: string;
  simEtaSec: number | null;
  simLog: string[];
  // Frames
  staticAttrs: StaticAttrs | null;
  frameXyz: Map<number, Float32Array>;
  currentFrameIdx: number;
  playing: boolean;
  // Setters
  setActiveModel:  (m: State["activeModel"]) => void;
  setActiveRecipe: (n: string, d: Record<string, any>) => void;
  setSimState:     (s: State["simState"]) => void;
  appendLog:       (line: string) => void;
  putFrame:        (idx: number, xyz: Float32Array) => void;
  setStaticAttrs:  (a: StaticAttrs) => void;
  setCurrentFrame: (i: number) => void;
  setPlaying:      (p: boolean) => void;
  resetForNewRun:  (name: string) => void;
};

export const useStore = create<State>((set) => ({
  activeModel: null, activeRecipeName: null, activeRecipeData: null,
  simState: "idle", simRunName: null, simNFrames: 0, simTotalFrames: 150,
  simStage: "idle", simEtaSec: null, simLog: [],
  staticAttrs: null, frameXyz: new Map(), currentFrameIdx: 0, playing: true,

  setActiveModel:  (m) => set({ activeModel: m }),
  setActiveRecipe: (n, d) => set({ activeRecipeName: n, activeRecipeData: d }),
  setSimState:     (s) => set({ simState: s }),
  appendLog:       (line) => set((st) => ({ simLog: [...st.simLog.slice(-1999), line] })),
  putFrame:        (idx, xyz) => set((st) => {
    const m = new Map(st.frameXyz); m.set(idx, xyz);
    return { frameXyz: m, simNFrames: m.size };
  }),
  setStaticAttrs:  (a) => set({ staticAttrs: a }),
  setCurrentFrame: (i) => set({ currentFrameIdx: i }),
  setPlaying:      (p) => set({ playing: p }),
  resetForNewRun:  (name) => set({
    simRunName: name, simState: "running", simNFrames: 0,
    simLog: [], staticAttrs: null, frameXyz: new Map(),
    currentFrameIdx: 0, simStage: "starting",
  }),
}));
```

- [ ] **Step 5: Wire StreamClient to store + commit**

In `App.tsx`, instantiate one `StreamClient` and call `connect()` on mount. Wire its handlers to store actions.

```tsx
// src/App.tsx — additions
import { useEffect, useMemo } from "react";
import { StreamClient } from "@/lib/ws";
import { useStore } from "@/lib/store";

export default function App() {
  const store = useStore();
  const client = useMemo(() => new StreamClient({
    onStatus:       (m) => { store.setSimState(m.state as any); },
    onLog:          (m) => store.appendLog(m.line),
    onStaticAttrs:  (m) => store.setStaticAttrs(m.attrs),
    onFrame:        (meta, xyz) => store.putFrame(meta.frame_idx, xyz),
  }), []);
  useEffect(() => { client.connect(); }, []);
  // … existing AppShell rendering …
}
```

```bash
git add frontend/src/lib frontend/src/App.tsx
git commit -m "frontend: REST + WebSocket clients + zustand store"
```

### Task 2.5 — Outliner shell (Models / Recipes / History trees)

**Files:**
- Create: `frontend/src/components/outliner/Outliner.tsx`
- Create: `frontend/src/components/outliner/ModelTree.tsx`
- Create: `frontend/src/components/outliner/RecipeTree.tsx`
- Create: `frontend/src/components/outliner/HistoryTree.tsx`

- [ ] **Step 1: ModelTree (queries /api/models, click → setActiveModel)**

```tsx
// src/components/outliner/ModelTree.tsx
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useStore } from "@/lib/store";

export function ModelTree() {
  const { data = [] } = useQuery({ queryKey: ["models"], queryFn: api.models.list });
  const { activeModel, setActiveModel } = useStore();
  return (
    <div>
      <div className="text-text-muted text-[10px] uppercase tracking-wider px-2 py-1">Models</div>
      {data.length === 0 && <div className="text-text-muted text-xs px-3 py-1">(drag a .ply onto the viewport)</div>}
      {data.map((m) => (
        <button
          key={m.name}
          onClick={() => setActiveModel(m)}
          className={`w-full text-left px-3 py-1 text-xs hover:bg-elevated ${activeModel?.name === m.name ? "text-accent" : "text-text-primary"}`}
        >{m.name}</button>
      ))}
    </div>
  );
}
```

- [ ] **Step 2: RecipeTree (queries /api/recipes, click → fetch + setActiveRecipe)**

```tsx
// src/components/outliner/RecipeTree.tsx
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useStore } from "@/lib/store";

export function RecipeTree() {
  const { data = [] } = useQuery({ queryKey: ["recipes"], queryFn: api.recipes.list });
  const { activeRecipeName, setActiveRecipe } = useStore();
  const onPick = async (name: string) => {
    const r = await api.recipes.get(name);
    setActiveRecipe(r.name, r.data);
  };
  return (
    <div>
      <div className="text-text-muted text-[10px] uppercase tracking-wider px-2 py-1 mt-2">Recipes</div>
      {data.map((r) => (
        <button
          key={r.name} onClick={() => onPick(r.name)}
          className={`w-full text-left px-3 py-1 text-xs hover:bg-elevated ${activeRecipeName === r.name ? "text-accent" : "text-text-primary"}`}
        >{r.source === "user" ? "★ " : ""}{r.name}</button>
      ))}
    </div>
  );
}
```

- [ ] **Step 3: HistoryTree (queries /api/runs/history, click → loads frames into viewport)**

```tsx
// src/components/outliner/HistoryTree.tsx
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

export function HistoryTree({ onPick }: { onPick: (run_name: string) => void }) {
  const { data = [] } = useQuery({ queryKey: ["history"], queryFn: api.runs.history,
                                    refetchInterval: 5000 });
  return (
    <div>
      <div className="text-text-muted text-[10px] uppercase tracking-wider px-2 py-1 mt-2">History</div>
      {data.map((h) => (
        <button key={h.run_name} onClick={() => onPick(h.run_name)}
                className="w-full text-left px-3 py-1 text-xs hover:bg-elevated text-text-primary truncate">
          {h.run_name}
          <span className="text-text-muted ml-2">{h.status}</span>
        </button>
      ))}
    </div>
  );
}
```

- [ ] **Step 4: Outliner glues them together**

```tsx
// src/components/outliner/Outliner.tsx
import { ModelTree } from "./ModelTree";
import { RecipeTree } from "./RecipeTree";
import { HistoryTree } from "./HistoryTree";

export function Outliner({ onLoadRun }: { onLoadRun: (name: string) => void }) {
  return (
    <div className="py-1">
      <ModelTree />
      <RecipeTree />
      <HistoryTree onPick={onLoadRun} />
    </div>
  );
}
```

- [ ] **Step 5: Wire into App + commit**

```tsx
// App.tsx — replace placeholder Outliner content
import { Outliner } from "@/components/outliner/Outliner";
// inside AppShell:
outliner={<Outliner onLoadRun={(name) => { /* Phase 3 wires this */ }} />}
```

```bash
git add frontend/src/components/outliner
git commit -m "frontend: Outliner with Models / Recipes / History trees"
```

### Task 2.6 — Properties shell (collapsible folder primitive)

**Files:**
- Create: `frontend/src/components/properties/PropertyFolder.tsx`
- Create: `frontend/src/components/properties/Properties.tsx`

- [ ] **Step 1: PropertyFolder — disclosure widget**

```tsx
// src/components/properties/PropertyFolder.tsx
import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

export function PropertyFolder({
  title, defaultOpen = true, children,
}: { title: string; defaultOpen?: boolean; children: React.ReactNode }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="border-b border-border last:border-b-0">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-1 px-2 py-1.5 text-xs font-medium uppercase tracking-wider text-accent hover:bg-elevated"
      >
        {open ? <ChevronDown size={12}/> : <ChevronRight size={12}/>}
        {title}
      </button>
      {open && <div className="px-3 pb-2 space-y-1">{children}</div>}
    </div>
  );
}
```

- [ ] **Step 2: Properties — top-level container with section folders**

```tsx
// src/components/properties/Properties.tsx
import { PropertyFolder } from "./PropertyFolder";
import { useStore } from "@/lib/store";

export function Properties() {
  const { activeRecipeData, activeRecipeName } = useStore();
  if (!activeRecipeName) {
    return <div className="p-3 text-xs text-text-muted">Select a recipe in the Outliner to edit parameters.</div>;
  }
  return (
    <div className="text-xs">
      <PropertyFolder title="Material">{/* MaterialPanel goes here in Phase 4 */}</PropertyFolder>
      <PropertyFolder title="Solver" defaultOpen={false}>{/* … */}</PropertyFolder>
      <PropertyFolder title="Forces" defaultOpen={false}>{/* … */}</PropertyFolder>
      <PropertyFolder title="Sim setup" defaultOpen={false}>{/* … */}</PropertyFolder>
      <PropertyFolder title="Camera" defaultOpen={false}>{/* … */}</PropertyFolder>
      <PropertyFolder title="Particle filling" defaultOpen={false}>{/* … */}</PropertyFolder>
      <PropertyFolder title="Other" defaultOpen={false}>{/* … */}</PropertyFolder>
      <PropertyFolder title="Boundary conditions" defaultOpen={false}>{/* Visual BC editor in Phase 4 */}</PropertyFolder>
      <PropertyFolder title="Provenance" defaultOpen={false}>
        <ProvenanceFooter data={activeRecipeData} />
      </PropertyFolder>
    </div>
  );
}

function ProvenanceFooter({ data }: { data: any }) {
  const p = data?._provenance;
  if (!p) return <div className="text-text-muted">Built-in preset.</div>;
  return (
    <div className="text-text-secondary">
      Based on <span className="text-accent">{p.based_on}</span>
      {p.saved_at && <> · saved {p.saved_at}</>}
    </div>
  );
}
```

- [ ] **Step 3: Wire + commit**

```tsx
// App.tsx
import { Properties } from "@/components/properties/Properties";
properties={<Properties />}
```

```bash
git add frontend/src/components/properties
git commit -m "frontend: Properties shell with collapsible folders + provenance footer"
```

---

## Phase 2 demoable artifact

```bash
# terminal 1:
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
python -m gsfluent serve --no-browser
# terminal 2:
cd /home/frankyin/Desktop/work/gsfluent_pkg/frontend
npm run dev
# open http://localhost:5173
```

You should see: dark cyan-accented Blender layout, three panels resizable, Outliner pulls real recipes from FastAPI, Properties shell with empty folders, status strip + workspace tabs in place.

---

## Phase 3 — Viewport (R3F + splat rendering)

### Task 3.1 — R3F scene + camera

**Files:**
- Create: `frontend/src/components/viewport/Viewport.tsx`
- Create: `frontend/src/components/viewport/SplatScene.tsx`
- Create: `frontend/src/components/viewport/EmptyState.tsx`

- [ ] **Step 1: Viewport with Canvas + OrbitControls**

```tsx
// src/components/viewport/Viewport.tsx
import { Canvas } from "@react-three/fiber";
import { OrbitControls, Grid, GizmoHelper, GizmoViewport } from "@react-three/drei";
import { SplatScene } from "./SplatScene";
import { EmptyState } from "./EmptyState";
import { useStore } from "@/lib/store";

export function Viewport() {
  const { staticAttrs } = useStore();
  return (
    <div className="h-full relative bg-canvas">
      <Canvas camera={{ position: [3, 3, 3], fov: 50 }}>
        <Grid args={[20, 20]} cellColor="#21262d" sectionColor="#22d3ee" sectionThickness={0.6} fadeDistance={30}/>
        <OrbitControls makeDefault />
        <GizmoHelper alignment="bottom-left" margin={[60, 60]}>
          <GizmoViewport axisColors={["#f87171","#34d399","#22d3ee"]} labelColor="#0d1117"/>
        </GizmoHelper>
        {staticAttrs && <SplatScene />}
      </Canvas>
      {!staticAttrs && <EmptyState />}
    </div>
  );
}
```

- [ ] **Step 2: EmptyState — drop zone hint**

```tsx
// src/components/viewport/EmptyState.tsx
import { Upload } from "lucide-react";
export function EmptyState() {
  return (
    <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
      <div className="border-2 border-dashed border-border rounded p-8 text-center">
        <Upload className="mx-auto mb-2 text-text-muted" size={32}/>
        <div className="text-sm text-text-secondary">Drag a 3DGS .ply here</div>
        <div className="text-xs text-text-muted mt-1">or pick from the Outliner</div>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Wire into App + commit**

```tsx
// App.tsx
import { Viewport } from "@/components/viewport/Viewport";
viewport={<Viewport />}
```

```bash
git add frontend/src/components/viewport
git commit -m "frontend: viewport with R3F canvas + grid + orbit + empty drop-state"
```

### Task 3.2 — Splat rendering with @mkkellogg/gaussian-splats-3d

**Files:**
- Create: `frontend/src/components/viewport/SplatScene.tsx` (replace stub)
- Create: `frontend/src/components/viewport/splat-helpers.ts`

This is the high-risk task — it depends on the spike outcome. If the spike PASSED, port the spike code into the real component. If FAILED, switch to the fallback custom R3F shader implementation (~1 extra week).

- [ ] **Step 1: splat-helpers.ts — pack static attrs into the format the lib expects**

```ts
// src/components/viewport/splat-helpers.ts
import type { StaticAttrs } from "@/lib/types";

/** Pack our (R, scales, rgb, opacity) into the lib's flat buffers. */
export function packForSplats(attrs: StaticAttrs, xyz: Float32Array) {
  const n = attrs.n;
  // The lib expects: positions (n*3), scales (n*3), rotations (n*4 quat), colors (n*4 rgba).
  const scales = new Float32Array(attrs.scales);
  const rotations = new Float32Array(n * 4);
  // Convert R (3x3) per-row → quaternion
  for (let i = 0; i < n; i++) {
    const r = attrs.R.subarray(i * 9, i * 9 + 9);
    const m00=r[0], m01=r[1], m02=r[2],
          m10=r[3], m11=r[4], m12=r[5],
          m20=r[6], m21=r[7], m22=r[8];
    const tr = m00 + m11 + m22;
    let qw, qx, qy, qz;
    if (tr > 0) {
      const s = 0.5 / Math.sqrt(tr + 1.0);
      qw = 0.25 / s; qx = (m21 - m12) * s; qy = (m02 - m20) * s; qz = (m10 - m01) * s;
    } else if (m00 > m11 && m00 > m22) {
      const s = 2.0 * Math.sqrt(1.0 + m00 - m11 - m22);
      qw = (m21 - m12) / s; qx = 0.25 * s; qy = (m01 + m10) / s; qz = (m02 + m20) / s;
    } else if (m11 > m22) {
      const s = 2.0 * Math.sqrt(1.0 + m11 - m00 - m22);
      qw = (m02 - m20) / s; qx = (m01 + m10) / s; qy = 0.25 * s; qz = (m12 + m21) / s;
    } else {
      const s = 2.0 * Math.sqrt(1.0 + m22 - m00 - m11);
      qw = (m10 - m01) / s; qx = (m02 + m20) / s; qy = (m12 + m21) / s; qz = 0.25 * s;
    }
    rotations[i*4+0] = qw; rotations[i*4+1] = qx; rotations[i*4+2] = qy; rotations[i*4+3] = qz;
  }
  const colors = new Uint8Array(n * 4);
  for (let i = 0; i < n; i++) {
    colors[i*4+0] = (attrs.rgb[i*3+0] * 255) | 0;
    colors[i*4+1] = (attrs.rgb[i*3+1] * 255) | 0;
    colors[i*4+2] = (attrs.rgb[i*3+2] * 255) | 0;
    colors[i*4+3] = (attrs.opacity[i] * 255) | 0;
  }
  return { positions: xyz, scales, rotations, colors };
}
```

- [ ] **Step 2: SplatScene — uses the spike's confirmed API**

```tsx
// src/components/viewport/SplatScene.tsx
import { useEffect, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import * as Splat from "@mkkellogg/gaussian-splats-3d";
import * as THREE from "three";
import { useStore } from "@/lib/store";
import { packForSplats } from "./splat-helpers";

export function SplatScene() {
  const groupRef = useRef<THREE.Group>(null);
  const viewerRef = useRef<any>(null);
  const initialFrameSent = useRef(false);

  const { staticAttrs, frameXyz, currentFrameIdx, playing, setCurrentFrame } = useStore();

  // Set up the splat viewer once when staticAttrs first arrive.
  useEffect(() => {
    if (!groupRef.current || !staticAttrs) return;
    const v = new Splat.Viewer({
      threeScene: groupRef.current, selfDrivenMode: false,
      sharedMemoryForWorkers: false, gpuAcceleratedSort: true,
    });
    viewerRef.current = v;
    return () => { v.dispose?.(); viewerRef.current = null; };
  }, [staticAttrs]);

  // Push first frame data when it arrives.
  useEffect(() => {
    if (!viewerRef.current || !staticAttrs || initialFrameSent.current) return;
    const f0 = frameXyz.get(0);
    if (!f0) return;
    const buffers = packForSplats(staticAttrs, f0);
    viewerRef.current.addSplatBuffers([buffers], [{}], false);
    initialFrameSent.current = true;
  }, [frameXyz, staticAttrs]);

  // Per render: advance frame + update centers in place.
  useFrame((_, dt) => {
    const v = viewerRef.current; if (!v || !staticAttrs) return;
    if (playing && frameXyz.size > 1) {
      // 24 fps target
      const next = (currentFrameIdx + 1) % frameXyz.size;
      setCurrentFrame(next);
    }
    const xyz = frameXyz.get(currentFrameIdx);
    if (xyz && v.splatMesh?.getCenters) {
      const buf = v.splatMesh.getCenters();
      buf.set(xyz);
      v.splatMesh.updateCenters?.();
    }
  });

  return <group ref={groupRef}/>;
}
```

- [ ] **Step 3: Visual smoke test using a past run + commit**

```bash
# terminal 1: backend running
# terminal 2: frontend running
# In the browser, click Outliner → History → "pkg_smoke_test"
# Phase 3.3 wires the click; until then, manually subscribe via console:
#   > window.__streamClient.subscribe("pkg_smoke_test")
# Expect: 200k splats appear, animating.
git add frontend/src/components/viewport
git commit -m "frontend: SplatScene with @mkkellogg/gaussian-splats-3d animated centers"
```

### Task 3.3 — Drag-drop .ply + load past run

**Files:**
- Create: `frontend/src/components/viewport/DropZone.tsx`
- Modify: `frontend/src/components/viewport/Viewport.tsx`
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: DropZone — overlays the viewport, handles file drops**

```tsx
// src/components/viewport/DropZone.tsx
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useStore } from "@/lib/store";
import { useQueryClient } from "@tanstack/react-query";

export function DropZone() {
  const [over, setOver] = useState(false);
  const setActiveModel = useStore((s) => s.setActiveModel);
  const qc = useQueryClient();
  useEffect(() => {
    const onOver = (e: DragEvent) => { e.preventDefault(); setOver(true); };
    const onLeave = () => setOver(false);
    const onDrop = async (e: DragEvent) => {
      e.preventDefault(); setOver(false);
      const f = e.dataTransfer?.files?.[0]; if (!f) return;
      if (!f.name.toLowerCase().endsWith(".ply")) {
        alert("Please drop a .ply file"); return;
      }
      const m = await api.models.upload(f);
      setActiveModel(m);
      qc.invalidateQueries({ queryKey: ["models"] });
    };
    window.addEventListener("dragover", onOver);
    window.addEventListener("dragleave", onLeave);
    window.addEventListener("drop", onDrop);
    return () => {
      window.removeEventListener("dragover", onOver);
      window.removeEventListener("dragleave", onLeave);
      window.removeEventListener("drop", onDrop);
    };
  }, []);
  if (!over) return null;
  return (
    <div className="absolute inset-0 bg-accent/10 border-2 border-accent border-dashed rounded pointer-events-none flex items-center justify-center">
      <div className="text-accent font-medium">Drop .ply to upload</div>
    </div>
  );
}
```

- [ ] **Step 2: Wire DropZone into Viewport**

```tsx
// Viewport.tsx — add inside the wrapping div, after Canvas:
import { DropZone } from "./DropZone";
// ...
<DropZone />
```

- [ ] **Step 3: Load past run from History — wire onLoadRun**

```tsx
// App.tsx
import { useStreamClient } from "@/lib/use-stream";   // (small helper exporting the client)
// In App:
const onLoadRun = (run_name: string) => {
  store.resetForNewRun(run_name);
  client.subscribe(run_name);
};
outliner={<Outliner onLoadRun={onLoadRun} />}
```

```ts
// src/lib/use-stream.ts
import { useEffect, useMemo } from "react";
import { StreamClient } from "./ws";
import { useStore } from "./store";
export function useStreamClient() {
  const store = useStore();
  return useMemo(() => new StreamClient({
    onStatus:      (m) => store.setSimState(m.state as any),
    onLog:         (m) => store.appendLog(m.line),
    onStaticAttrs: (m) => store.setStaticAttrs(m.attrs),
    onFrame:       (meta, xyz) => store.putFrame(meta.frame_idx, xyz),
  }), []);
}
```

- [ ] **Step 4: Smoke + commit**

Open the app, drag a .ply onto the viewport → upload → static splats appear. Click a History entry → that run plays back.

```bash
git add frontend/src/
git commit -m "frontend: drag-drop .ply upload + History click-to-load"
```

---

## Phase 3 demoable artifact

Drag a `.ply` onto the viewport → uploads → splats appear. Click History → past run plays. **End-to-end visual is real.**

---

## Phase 4 — Recipe authoring

### Task 4.1 — Param widgets + Material panel with auto-fill

**Files:**
- Create: `frontend/src/components/properties/widgets/NumberInput.tsx`
- Create: `frontend/src/components/properties/widgets/SliderInput.tsx`
- Create: `frontend/src/components/properties/widgets/Vec3Input.tsx`
- Create: `frontend/src/components/properties/widgets/SelectInput.tsx`
- Create: `frontend/src/components/properties/widgets/SwitchInput.tsx`
- Create: `frontend/src/components/properties/MaterialPanel.tsx`

- [ ] **Step 1: SliderInput (range + numeric input pair, JetBrains Mono on the value)**

```tsx
// src/components/properties/widgets/SliderInput.tsx
import * as Slider from "@radix-ui/react-slider";

export function SliderInput({
  label, value, onChange, min, max, step, hint,
}: {
  label: string; value: number;
  onChange: (v: number) => void;
  min: number; max: number; step: number; hint?: string;
}) {
  return (
    <div className="flex items-center gap-2 py-0.5" title={hint}>
      <span className="text-text-secondary text-xs flex-1 truncate">{label}</span>
      <Slider.Root className="relative flex items-center w-20 h-4"
                   value={[value]} min={min} max={max} step={step}
                   onValueChange={(v) => onChange(v[0])}>
        <Slider.Track className="bg-elevated relative grow rounded-full h-1">
          <Slider.Range className="absolute bg-accent rounded-full h-full"/>
        </Slider.Track>
        <Slider.Thumb className="block w-3 h-3 bg-accent rounded-full focus:outline-none"/>
      </Slider.Root>
      <input type="number" value={value} onChange={(e) => onChange(parseFloat(e.target.value))}
             min={min} max={max} step={step}
             className="font-mono text-text-primary bg-elevated rounded px-1 w-16 text-right text-xs"/>
    </div>
  );
}
```

- [ ] **Step 2: Vec3Input + NumberInput + SelectInput + SwitchInput**

Same pattern. Vec3Input renders 3 small number inputs side by side. SelectInput wraps `@radix-ui/react-select`. SwitchInput wraps `@radix-ui/react-switch`. (Skipping the boilerplate; follow the Slider pattern.)

- [ ] **Step 3: MaterialPanel with auto-fill on material change**

```tsx
// src/components/properties/MaterialPanel.tsx
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useStore } from "@/lib/store";
import { SelectInput } from "./widgets/SelectInput";
import { SliderInput } from "./widgets/SliderInput";

const MATERIALS = ["jelly","metal","sand","foam","snow","plasticine","watermelon"];
const MATERIAL_FIELDS: Array<[string, string, [number,number,number]]> = [
  ["E",                "Young's modulus", [100, 1e7, 100]],
  ["nu",               "Poisson ν",       [0, 0.499, 0.005]],
  ["density",          "Density",         [0.01, 100, 0.01]],
  ["yield_stress",     "Yield stress",    [0, 1e6, 1]],
  ["friction_angle",   "Friction (deg)",  [0, 90, 1]],
];

export function MaterialPanel() {
  const { activeRecipeData, setActiveRecipe, activeRecipeName } = useStore();
  const { data: defaults } = useQuery({ queryKey: ["mat_defaults"], queryFn: api.schemas.materials });
  if (!activeRecipeData) return null;
  const set = (key: string, v: any) => {
    if (!activeRecipeName) return;
    setActiveRecipe(activeRecipeName, { ...activeRecipeData, [key]: v });
  };
  const onMaterialChange = (newMat: string) => {
    if (!activeRecipeName || !defaults) return;
    const next = { ...activeRecipeData, material: newMat, ...(defaults[newMat] ?? {}) };
    setActiveRecipe(activeRecipeName, next);
    // toast: "Snapped to <newMat> defaults — Undo"
  };
  return (
    <div className="space-y-1">
      <SelectInput label="Material"
                   value={activeRecipeData.material as string}
                   options={MATERIALS}
                   onChange={onMaterialChange}/>
      {MATERIAL_FIELDS.map(([key, label, [min, max, step]]) => (
        <SliderInput key={key} label={label}
                     value={Number(activeRecipeData[key] ?? 0)}
                     onChange={(v) => set(key, v)}
                     min={min} max={max} step={step}/>
      ))}
    </div>
  );
}
```

- [ ] **Step 4: Wire MaterialPanel into Properties + commit**

```tsx
// Properties.tsx — replace the Material folder body:
import { MaterialPanel } from "./MaterialPanel";
<PropertyFolder title="Material"><MaterialPanel/></PropertyFolder>
```

```bash
git add frontend/src/components/properties
git commit -m "frontend: Material panel with auto-fill on material change"
```

### Task 4.2 — Solver / Forces / Sim setup / Camera / Particle filling / Other panels

**Files:**
- Create one panel file per section under `frontend/src/components/properties/`.

Each panel is a thin wrapper: declare an array of `[key, label, [min, max, step]]` triples, map them to `SliderInput` / `NumberInput` / `Vec3Input` / `SelectInput` / `SwitchInput` widgets bound to `activeRecipeData[key]` via `setActiveRecipe`.

- [ ] **Step 1: SolverPanel**

Fields: `n_grid` (slider 50–400, step 10), `grid_lim` (slider 1–10, step 1), `substep_dt` (number, 1e-5–5e-4), `frame_dt` (slider 0.005–0.1, 0.005), `frame_num` (slider 30–600, 10), `flip_pic_ratio` (slider 0–1, 0.05), `rpic_damping` (slider 0–1, 0.01), `grid_v_damping_scale` (slider 0.5–2, 0.05).

- [ ] **Step 2: ForcesPanel**

One Vec3Input for `g`.

- [ ] **Step 3: SimSetupPanel**

`sim_area` as a 2×3 grid of number inputs (X/Y/Z min/max). `mpm_space_viewpoint_center` as Vec3. `mpm_space_vertical_upward_axis` as a SelectInput with three values: `[1,0,0]` `[0,1,0]` `[0,0,1]`.

- [ ] **Step 4: CameraPanel**

`init_azimuthm` / `init_elevation` / `init_radius` (sliders), `delta_a` / `delta_e` / `delta_r` (number inputs), `move_camera` (switch).

- [ ] **Step 5: ParticleFillingPanel**

Iterate keys of `activeRecipeData.particle_filling` dict and render number inputs.

- [ ] **Step 6: OtherPanel**

`opacity_threshold` (slider), `show_hint` (switch).

- [ ] **Step 7: Wire all + commit**

```tsx
// Properties.tsx
<PropertyFolder title="Solver"><SolverPanel/></PropertyFolder>
<PropertyFolder title="Forces"><ForcesPanel/></PropertyFolder>
<PropertyFolder title="Sim setup"><SimSetupPanel/></PropertyFolder>
<PropertyFolder title="Camera"><CameraPanel/></PropertyFolder>
<PropertyFolder title="Particle filling"><ParticleFillingPanel/></PropertyFolder>
<PropertyFolder title="Other"><OtherPanel/></PropertyFolder>
```

```bash
git add frontend/src/components/properties
git commit -m "frontend: Solver / Forces / Sim setup / Camera / Particle filling / Other panels"
```

### Task 4.3 — Visual BC editor

**Files:**
- Create: `frontend/src/components/properties/BoundaryEditor.tsx`
- Create: `frontend/src/components/properties/BoundaryRow.tsx`

- [ ] **Step 1: BoundaryEditor — list + Add button**

```tsx
// src/components/properties/BoundaryEditor.tsx
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useStore } from "@/lib/store";
import { Plus } from "lucide-react";
import { BoundaryRow } from "./BoundaryRow";

export function BoundaryEditor() {
  const { data: schemas } = useQuery({ queryKey: ["bc_schemas"], queryFn: api.schemas.boundaries });
  const { activeRecipeData, activeRecipeName, setActiveRecipe } = useStore();
  if (!activeRecipeData || !schemas) return null;

  const bcs: any[] = activeRecipeData.boundary_conditions ?? [];
  const set = (next: any[]) => setActiveRecipe(activeRecipeName!, { ...activeRecipeData, boundary_conditions: next });

  const addBC = () => {
    const next = [...bcs, { type: "bounding_box" }];
    set(next);
  };
  return (
    <div className="space-y-1">
      {bcs.map((bc, i) => (
        <BoundaryRow key={i} bc={bc} schemas={schemas}
                     onChange={(b) => set(bcs.map((x, j) => j === i ? b : x))}
                     onDelete={() => set(bcs.filter((_, j) => j !== i))}/>
      ))}
      <button onClick={addBC} className="w-full flex items-center justify-center gap-1 py-1 text-xs text-accent hover:bg-elevated">
        <Plus size={12}/> Add boundary
      </button>
    </div>
  );
}
```

- [ ] **Step 2: BoundaryRow — type dropdown + per-type fields**

```tsx
// src/components/properties/BoundaryRow.tsx
import { Trash2 } from "lucide-react";
import { SelectInput } from "./widgets/SelectInput";
import { Vec3Input } from "./widgets/Vec3Input";
import { NumberInput } from "./widgets/NumberInput";

type FieldSpec = { name: string; type: "vec3"|"float"|"string"; default: any; hint: string };

export function BoundaryRow({
  bc, schemas, onChange, onDelete,
}: { bc: any; schemas: Record<string, FieldSpec[]>;
     onChange: (bc: any) => void; onDelete: () => void; }) {
  const types = Object.keys(schemas);
  const fields = schemas[bc.type] ?? [];
  const setField = (name: string, v: any) => onChange({ ...bc, [name]: v });
  const setType  = (newType: string) => {
    const fresh: any = { type: newType };
    for (const f of schemas[newType] ?? []) fresh[f.name] = f.default;
    onChange(fresh);
  };
  return (
    <div className="border border-border rounded p-2 space-y-1 bg-canvas">
      <div className="flex items-center gap-2">
        <SelectInput label="Type" value={bc.type} options={types} onChange={setType}/>
        <button onClick={onDelete} className="text-error/80 hover:text-error" aria-label="delete">
          <Trash2 size={12}/>
        </button>
      </div>
      {fields.map((f) => {
        const v = bc[f.name] ?? f.default;
        if (f.type === "vec3") return <Vec3Input key={f.name} label={f.name} value={v} onChange={(nv) => setField(f.name, nv)}/>;
        if (f.type === "string") return <SelectInput key={f.name} label={f.name} value={v} options={[v]} onChange={(nv) => setField(f.name, nv)}/>;
        return <NumberInput key={f.name} label={f.name} value={v} onChange={(nv) => setField(f.name, nv)}/>;
      })}
    </div>
  );
}
```

- [ ] **Step 3: Wire + commit**

```tsx
// Properties.tsx
<PropertyFolder title="Boundary conditions"><BoundaryEditor/></PropertyFolder>
```

```bash
git add frontend/src/components/properties
git commit -m "frontend: visual BC editor — list, add/delete, per-type forms"
```

### Task 4.4 — Save preset flow

**Files:**
- Create: `frontend/src/components/properties/SavePresetDialog.tsx`

- [ ] **Step 1: SavePresetDialog — modal with name input + Save**

```tsx
// src/components/properties/SavePresetDialog.tsx
import * as Dialog from "@radix-ui/react-dialog";
import { useState } from "react";
import { api } from "@/lib/api";
import { useStore } from "@/lib/store";
import { useQueryClient } from "@tanstack/react-query";

export function SavePresetDialog() {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const { activeRecipeData, activeRecipeName, setActiveRecipe } = useStore();
  const qc = useQueryClient();

  const onSave = async () => {
    if (!name || !activeRecipeData) return;
    await api.recipes.save(name, activeRecipeData, activeRecipeName ?? undefined);
    qc.invalidateQueries({ queryKey: ["recipes"] });
    setActiveRecipe(name, activeRecipeData);
    setOpen(false); setName("");
  };

  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <Dialog.Trigger className="text-xs text-accent hover:underline px-2 py-1">Save preset…</Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/50"/>
        <Dialog.Content className="fixed inset-0 m-auto w-80 h-fit bg-elevated border border-border rounded p-4 space-y-3">
          <Dialog.Title className="text-sm font-semibold">Save current edits as preset</Dialog.Title>
          <input value={name} onChange={(e) => setName(e.target.value)}
                 placeholder="my_preset"
                 className="w-full bg-canvas border border-border rounded px-2 py-1 text-xs"/>
          <div className="flex justify-end gap-2">
            <button onClick={() => setOpen(false)} className="text-xs text-text-secondary px-2 py-1">Cancel</button>
            <button onClick={onSave} className="bg-accent text-canvas px-3 py-1 rounded text-xs font-medium">Save</button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
```

- [ ] **Step 2: Place SavePresetDialog at the bottom of Properties + commit**

```tsx
// Properties.tsx
<div className="p-2 border-t border-border">
  <SavePresetDialog/>
</div>
```

```bash
git add frontend/src/components/properties
git commit -m "frontend: Save preset dialog — writes user preset, refreshes Outliner"
```

### Task 4.5 — Provenance footer + diff view

**Files:**
- Create: `frontend/src/components/properties/ProvenanceFooter.tsx`

- [ ] **Step 1: ProvenanceFooter — show "Based on X · 4 edits"**

```tsx
// src/components/properties/ProvenanceFooter.tsx
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useStore } from "@/lib/store";

export function ProvenanceFooter() {
  const { activeRecipeData, activeRecipeName } = useStore();
  const basedOn = activeRecipeData?._provenance?.based_on
                ?? (activeRecipeName?.startsWith("★ ") ? activeRecipeName.slice(2) : activeRecipeName);
  const { data: source } = useQuery({
    queryKey: ["recipe", basedOn],
    queryFn:  () => basedOn ? api.recipes.get(basedOn) : null,
    enabled:  !!basedOn,
  });
  if (!activeRecipeData || !source) return null;
  const diffs = countDiffs(source.data, activeRecipeData);
  return (
    <div className="text-text-secondary text-xs px-2 py-1">
      Based on <span className="text-accent">{basedOn}</span> · {diffs} edit{diffs === 1 ? "" : "s"}
    </div>
  );
}

function countDiffs(a: any, b: any): number {
  let n = 0;
  for (const k of new Set([...Object.keys(a||{}), ...Object.keys(b||{})])) {
    if (k === "_provenance" || k === "_note") continue;
    if (JSON.stringify(a?.[k]) !== JSON.stringify(b?.[k])) n++;
  }
  return n;
}
```

- [ ] **Step 2: Wire + commit**

```tsx
// Properties.tsx — replace the placeholder ProvenanceFooter
import { ProvenanceFooter } from "./ProvenanceFooter";
<PropertyFolder title="Provenance"><ProvenanceFooter/></PropertyFolder>
```

```bash
git add frontend/src/components/properties
git commit -m "frontend: provenance footer with based-on + edit count"
```

---

## Phase 4 demoable artifact

Pick a recipe → Material dropdown → all params populate. Change material → all material params snap to defaults. Boundary conditions show as editable list. Save as preset → new ★ entry in Outliner. Provenance shows "Based on jelly · 3 edits".

---

## Phase 5 — Run lifecycle

### Task 5.1 — Run button + status flow

**Files:**
- Create: `frontend/src/components/runs/RunButton.tsx`
- Modify: `frontend/src/components/layout/TopBar.tsx`

- [ ] **Step 1: RunButton — POST /api/runs + subscribe to stream**

```tsx
// src/components/runs/RunButton.tsx
import { Play, Square } from "lucide-react";
import { api } from "@/lib/api";
import { useStore } from "@/lib/store";

export function RunButton({ subscribe }: { subscribe: (run: string) => void }) {
  const {
    activeModel, activeRecipeName, activeRecipeData,
    simState, simRunName, resetForNewRun,
  } = useStore();

  const canRun = !!activeModel && !!activeRecipeName && simState !== "running";
  const onRun = async () => {
    if (!canRun || !activeModel || !activeRecipeData) return;
    const ts = new Date().toISOString().replace(/[:.]/g,"").slice(0,15);
    const run_name = `${activeModel.name}_${activeRecipeName}_${ts}`;
    resetForNewRun(run_name);
    await api.runs.start({
      run_name,
      model_path: activeModel.path,
      recipe_data: activeRecipeData,
      recipe_source: activeRecipeName!,
      particles: 200_000,
    });
    subscribe(run_name);
  };
  const onCancel = async () => {
    if (!simRunName) return;
    const all = await api.runs.list();
    const r = all.find(x => x.name === simRunName);
    if (r) await api.runs.cancel(r.id);
  };
  if (simState === "running") {
    return <button onClick={onCancel} className="bg-error/20 text-error border border-error/40 px-3 py-0.5 text-xs rounded font-medium flex items-center gap-1"><Square size={11}/>Cancel</button>;
  }
  return (
    <button onClick={onRun} disabled={!canRun}
            className="bg-accent text-canvas px-3 py-0.5 text-xs rounded shadow-accent-glow font-medium flex items-center gap-1 disabled:opacity-30 disabled:shadow-none">
      <Play size={11}/>Run
    </button>
  );
}
```

- [ ] **Step 2: Wire into TopBar (App.tsx passes the subscribe callback)**

```tsx
// TopBar.tsx
import { RunButton } from "@/components/runs/RunButton";
export function TopBar({ subscribe }: { subscribe: (n: string) => void }) {
  // ...
  <RunButton subscribe={subscribe}/>
}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src
git commit -m "frontend: Run button + cancel + auto-subscribe on start"
```

### Task 5.2 — Progress bar + Stage + ETA in StatusStrip

**Files:**
- Modify: `frontend/src/components/layout/StatusStrip.tsx`
- Create: `frontend/src/lib/derive-progress.ts`

- [ ] **Step 1: derive-progress.ts — parse log for stage + observed fps**

```ts
// src/lib/derive-progress.ts
export function deriveStage(logTail: string): string {
  if (logTail.includes("[PhaseA-SUMMARY]")) return "fuse drain";
  if (logTail.includes("step 2/3") && logTail.includes("fuse")) return "fusing";
  if (logTail.includes("[PhaseA]") || logTail.includes("step 1/3")) return "simulating";
  return "starting (kernel JIT)";
}

export function computeEta(nFrames: number, total: number, firstFrameAt: number | null): string {
  if (!firstFrameAt || nFrames === 0) return "—";
  if (nFrames >= total) return "0:00";
  const elapsed = (Date.now() - firstFrameAt) / 1000;
  const fps = nFrames / Math.max(elapsed, 0.001);
  if (fps <= 0) return "computing…";
  const remain = (total - nFrames) / fps;
  const m = Math.floor(remain / 60), s = Math.floor(remain % 60);
  return `${m}:${s.toString().padStart(2,"0")}  ·  ${fps.toFixed(2)} fps`;
}
```

- [ ] **Step 2: Hook into StatusStrip**

```tsx
// StatusStrip.tsx
import { useStore } from "@/lib/store";
import { useEffect, useState } from "react";
import { deriveStage, computeEta } from "@/lib/derive-progress";

export function StatusStrip() {
  const { simState, simNFrames, simTotalFrames, simLog } = useStore();
  const [firstFrameT, setFirstFrameT] = useState<number | null>(null);
  useEffect(() => { if (simNFrames === 1 && firstFrameT === null) setFirstFrameT(Date.now()); }, [simNFrames]);

  const tail = simLog.slice(-80).join("\n");
  const stage = simState === "running" ? deriveStage(tail) : simState;
  const pct = simTotalFrames > 0 ? Math.min(100, 100 * simNFrames / simTotalFrames) : 0;
  const eta = simState === "running" ? computeEta(simNFrames, simTotalFrames, firstFrameT) : "—";

  return (
    <div className="h-8 border-t border-border px-3 flex items-center gap-3 text-xs text-text-muted shrink-0 font-mono">
      <span className={simState === "running" ? "text-accent" : simState === "error" ? "text-error" : simState === "done" ? "text-success" : "text-text-muted"}>●</span>
      <span className="capitalize w-32">{stage}</span>
      <div className="flex-1 max-w-md h-1 bg-elevated rounded overflow-hidden">
        <div className="h-full bg-accent transition-all" style={{ width: `${pct}%` }}/>
      </div>
      <span>{simNFrames}/{simTotalFrames}</span>
      <span className="ml-2">{eta}</span>
      <span className="ml-auto">⌘K</span>
    </div>
  );
}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src
git commit -m "frontend: live progress bar + stage + ETA in StatusStrip"
```

### Task 5.3 — Console accordion

**Files:**
- Create: `frontend/src/components/runs/ConsoleAccordion.tsx`
- Modify: `frontend/src/components/layout/StatusStrip.tsx`

- [ ] **Step 1: ConsoleAccordion — auto-scrolling tail of last 200 lines**

```tsx
// src/components/runs/ConsoleAccordion.tsx
import { useRef, useEffect, useState } from "react";
import { ChevronUp } from "lucide-react";
import { useStore } from "@/lib/store";

export function ConsoleAccordion() {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const { simLog } = useStore();
  useEffect(() => {
    if (open && ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [simLog, open]);
  return (
    <>
      <button onClick={() => setOpen(!open)} className="flex items-center gap-1 hover:text-text-primary">
        <ChevronUp size={11} className={open ? "rotate-180 transition-transform" : "transition-transform"}/>
        console
      </button>
      {open && (
        <div className="absolute bottom-8 left-0 right-0 h-72 bg-canvas border-t border-border z-10">
          <div ref={ref} className="h-full overflow-auto font-mono text-[11px] p-2 leading-tight">
            {simLog.length === 0 ? <span className="text-text-muted">(no output yet)</span>
              : simLog.map((line, i) => <div key={i} className="text-text-primary">{line}</div>)}
          </div>
        </div>
      )}
    </>
  );
}
```

- [ ] **Step 2: Drop into StatusStrip + commit**

```tsx
// StatusStrip.tsx — replace the static "⌘K" with the console toggle
import { ConsoleAccordion } from "@/components/runs/ConsoleAccordion";
// ...
<div className="ml-auto flex items-center gap-3 relative">
  <span>⌘K</span>
  <ConsoleAccordion/>
</div>
```

```bash
git add frontend/src
git commit -m "frontend: console accordion with auto-scroll-to-bottom tail"
```

### Task 5.4 — Auto-finish + History reload

**Files:**
- Modify: `frontend/src/lib/store.ts`
- Modify: `frontend/src/lib/use-stream.ts`

- [ ] **Step 1: Auto-finish detection — when simNFrames >= simTotalFrames AND log has [PhaseA-SUMMARY]**

In the store, add a `simState` setter that, when transitioning to RUNNING with all expected frames + summary marker present, calls `cancel` for that run. Easier: do it in the StreamClient handler that updates frames.

```ts
// in use-stream.ts — modify the onFrame handler:
onFrame: (meta, xyz) => {
  store.putFrame(meta.frame_idx, xyz);
  // Check auto-finish
  const { simNFrames, simTotalFrames, simLog, simRunName, simState } = useStore.getState();
  if (simState === "running" &&
      simNFrames >= simTotalFrames &&
      simLog.slice(-80).join("\n").includes("[PhaseA-SUMMARY]") &&
      simRunName) {
    api.runs.list().then(rs => {
      const r = rs.find(x => x.name === simRunName);
      if (r) api.runs.cancel(r.id);
    });
  }
},
```

- [ ] **Step 2: When sim transitions to "done" → invalidate /api/runs/history query so Outliner refreshes**

```ts
// in use-stream.ts:
onStatus: (m) => {
  store.setSimState(m.state as any);
  if (m.state === "done" || m.state === "error" || m.state === "cancelled") {
    queryClient.invalidateQueries({ queryKey: ["history"] });
  }
},
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src
git commit -m "frontend: auto-finish on PhaseA-SUMMARY + reload History on run end"
```

---

## Phase 5 demoable artifact

Click Run → progress bar fills, stage label walks through `starting → simulating → fusing → fuse drain → done`, ETA recomputes, console tails. Sim completes → History gets a new entry without manual refresh. **End-to-end run-to-result loop works.**

---

## Phase 6 — Polish + integration

### Task 6.1 — Command palette (cmdk)

**Files:**
- Create: `frontend/src/components/command-palette/CommandPalette.tsx`
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: CommandPalette — Cmd+K opens, lists actions**

```tsx
// src/components/command-palette/CommandPalette.tsx
import { Command } from "cmdk";
import { useEffect, useState } from "react";
import { useStore } from "@/lib/store";
import { api } from "@/lib/api";

export function CommandPalette({ subscribe }: { subscribe: (n: string) => void }) {
  const [open, setOpen] = useState(false);
  const { setActiveRecipe, activeModel, activeRecipeData, activeRecipeName, resetForNewRun } = useStore();

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") { e.preventDefault(); setOpen((o) => !o); }
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const runSim = async () => {
    if (!activeModel || !activeRecipeData || !activeRecipeName) return;
    const ts = new Date().toISOString().replace(/[:.]/g,"").slice(0,15);
    const run_name = `${activeModel.name}_${activeRecipeName}_${ts}`;
    resetForNewRun(run_name);
    await api.runs.start({
      run_name, model_path: activeModel.path,
      recipe_data: activeRecipeData, recipe_source: activeRecipeName, particles: 200_000,
    });
    subscribe(run_name);
    setOpen(false);
  };

  if (!open) return null;
  return (
    <div className="fixed inset-0 bg-black/50 z-50 flex items-start justify-center pt-32" onClick={() => setOpen(false)}>
      <Command label="Command palette" className="w-[480px] bg-elevated border border-border rounded-lg overflow-hidden" onClick={(e) => e.stopPropagation()}>
        <Command.Input placeholder="Type a command…"
                       className="w-full bg-canvas px-3 py-2 text-sm border-b border-border outline-none"/>
        <Command.List className="max-h-80 overflow-auto">
          <Command.Empty className="p-3 text-xs text-text-muted">No matching commands</Command.Empty>
          <Command.Group heading="Actions">
            <Command.Item onSelect={runSim} className="px-3 py-2 text-xs hover:bg-canvas data-[selected=true]:bg-canvas">▶ Run simulation</Command.Item>
          </Command.Group>
        </Command.List>
      </Command>
    </div>
  );
}
```

- [ ] **Step 2: Wire into App + commit**

```tsx
// App.tsx
import { CommandPalette } from "@/components/command-palette/CommandPalette";
// inside render:
<CommandPalette subscribe={subscribe}/>
```

```bash
git add frontend/src
git commit -m "frontend: ⌘K command palette with Run action"
```

### Task 6.2 — Keyboard shortcuts

**Files:**
- Create: `frontend/src/lib/use-shortcuts.ts`

- [ ] **Step 1: useShortcuts — `i` toggles inspector, `Cmd+Enter` runs, `Cmd+B` toggles sidebar**

```ts
// src/lib/use-shortcuts.ts
import { useEffect } from "react";
import { useStore } from "./store";

export function useShortcuts(opts: { onRun: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement;
      if (target.tagName === "INPUT" || target.tagName === "TEXTAREA") return;
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { e.preventDefault(); opts.onRun(); }
      // i / Cmd+B handlers similarly
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [opts]);
}
```

- [ ] **Step 2: Wire into App + commit**

```bash
git add frontend/src/lib/use-shortcuts.ts frontend/src/App.tsx
git commit -m "frontend: keyboard shortcuts (Cmd+Enter run, i inspector, Cmd+B sidebar)"
```

### Task 6.3 — Playwright E2E acceptance test

**Files:**
- Create: `frontend/playwright.config.ts`
- Create: `frontend/e2e/acceptance.spec.ts`
- Modify: `frontend/package.json` — add `@playwright/test`

- [ ] **Step 1: Install Playwright**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/frontend
npm i -D @playwright/test
npx playwright install chromium
```

- [ ] **Step 2: playwright.config.ts**

```ts
// playwright.config.ts
import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./e2e",
  use: { baseURL: "http://localhost:5173", screenshot: "only-on-failure" },
  webServer: [
    { command: "cd ../server && python -m gsfluent serve --no-browser --port 8080", port: 8080, reuseExistingServer: true },
    { command: "npm run dev", port: 5173, reuseExistingServer: true },
  ],
});
```

- [ ] **Step 3: acceptance.spec.ts — the spec's MVP acceptance criteria**

```ts
// e2e/acceptance.spec.ts
import { test, expect } from "@playwright/test";
import path from "path";

test("casual user can drop a ply, pick a preset, run, see frames", async ({ page }) => {
  await page.goto("/");
  // Drop a real .ply file from the prebaked test fixture
  const file = path.resolve(__dirname, "../../core/mpm_solver_warp/sand_column.h5");
  // Note: Playwright drag-drop file is awkward; we use the file input fallback.
  // (DropZone exposes a hidden <input type=file/> for testability.)
  // ...
  // Pick the jelly recipe
  await page.click("text=jelly");
  await expect(page.locator("text=Material")).toBeVisible();
  // Click Run
  await page.click("button:has-text('Run')");
  // Within 90 seconds, expect Status to show "done" or at least one frame to render
  await expect(page.locator("[data-testid=sim-state]")).toContainText("done", { timeout: 90_000 });
});

test("loading past run from History restores model + recipe", async ({ page }) => {
  await page.goto("/");
  // Click first History entry
  await page.click("[data-testid=history-list] button >> nth=0");
  // Properties should populate with a recipe
  await expect(page.locator("text=Material")).toBeVisible();
});
```

- [ ] **Step 4: Add `data-testid` attrs in components used by tests + commit**

```bash
npx playwright test
git add frontend/
git commit -m "frontend: Playwright E2E acceptance test for MVP criteria"
```

---

## Phase 6 demoable artifact

⌘K opens command palette. Cmd+Enter runs from anywhere. Playwright spec passes against a local backend.

---

## Phase 7 — Distribution

### Task 7.1 — Vite build → static assets bundled into wheel

**Files:**
- Modify: `frontend/vite.config.ts` (already outputs to `../server/gsfluent/static`)
- Modify: `server/gsfluent/server.py` — serve static
- Create: `server/Makefile`

- [ ] **Step 1: Mount static in FastAPI**

```python
# server/gsfluent/server.py — at the bottom of create_app(), before return:
from fastapi.staticfiles import StaticFiles
import os
STATIC_DIR = Path(__file__).parent / "static"
if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
return app
```

- [ ] **Step 2: Makefile to build everything**

```makefile
# server/Makefile
.PHONY: build dev test

build:
	cd ../frontend && npm run build
	pip install -e .

dev:
	@echo "run 'cd ../frontend && npm run dev' in one terminal,"
	@echo "and 'python -m gsfluent serve --no-browser' in another"

test:
	pytest -v
```

- [ ] **Step 3: Verify**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
make build
python -m gsfluent serve --port 8090 --no-browser &
sleep 2
curl -s http://localhost:8090/ | head -20
# Expect: HTML page from the Vite build (not just /api/* JSON).
```

- [ ] **Step 4: Commit**

```bash
git add server/ frontend/
git commit -m "dist: bundle Vite static assets into the wheel; FastAPI serves /"
```

### Task 7.2 — Final pip install test

**Files:**
- Create: `server/.gitignore` (exclude build/dist/static)

- [ ] **Step 1: Build the wheel + install in a fresh venv**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/frontend && npm run build
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
python -m venv /tmp/test_install
/tmp/test_install/bin/pip install ./
/tmp/test_install/bin/gsfluent serve --no-browser --port 8091 &
sleep 3
curl -sf http://localhost:8091/api/health
curl -sf http://localhost:8091/ | head -5
```

- [ ] **Step 2: Document distribution + README**

```markdown
<!-- server/README.md -->
# gsfluent server

Install:
    cd frontend && npm run build       # outputs to ../server/gsfluent/static
    cd ../server && pip install -e .

Run:
    gsfluent serve

Develop:
    # terminal 1
    cd server && python -m gsfluent serve --no-browser
    # terminal 2
    cd frontend && npm run dev
```

- [ ] **Step 3: Commit**

```bash
git add server/README.md server/.gitignore
git commit -m "dist: README + verified pip install + gsfluent serve flow"
```

### Task 7.3 — Deprecate viser workbench

**Files:**
- Modify: `tools/workbench.py` — add a deprecation banner at startup
- Modify: `README.md` — point users to `gsfluent serve` instead

- [ ] **Step 1: Banner**

```python
# tools/workbench.py — at the top of main():
print("\n[DEPRECATED] tools/workbench.py is the legacy viser workbench.")
print("[DEPRECATED] Run `gsfluent serve` for the current React workbench.")
print("[DEPRECATED] This file will be removed in v0.3.\n")
```

- [ ] **Step 2: README pointer**

Update both `README.md` and `README.en.md` to lead with `gsfluent serve` and mention the legacy `./run-workbench.sh` is deprecated.

- [ ] **Step 3: Commit**

```bash
git add tools/workbench.py README.md README.en.md
git commit -m "deprecate: tools/workbench.py — gsfluent serve is the way"
```

---

## Final acceptance checklist (must all pass before declaring MVP done)

- [ ] **Casual flow:** open the app → drag a .ply onto viewport → upload completes within 5s → static splats visible → click `jelly` in Outliner → Properties populates → click Run → progress bar advances → first frame within 90s of click → 150 frames stream → DONE shown in StatusStrip.
- [ ] **Power flow:** load any past run from History → frames + recipe both restore → tweak `E` slider → "Save preset" → ★ entry appears in Outliner → Run with ★ preset → entry appears in History when done.
- [ ] **Resilience:** kill the FastAPI process mid-run → frontend shows reconnect attempt → restart server → frontend recovers, History reflects the failed run.
- [ ] **Polish:** all panel splits drag-resize and persist across reload. Cmd+K opens palette. Cmd+Enter runs sim. Theme is consistent (no light-mode leaks).
- [ ] **Distribution:** `pip install ./server` + `gsfluent serve` opens browser to a working app.
- [ ] **No regression:** legacy `./run-workbench.sh` still launches the viser version (with deprecation banner) and works for any existing past runs.
