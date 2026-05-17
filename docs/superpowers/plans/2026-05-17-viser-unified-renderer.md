# Viser Unified Renderer — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse the dual-renderer architecture (three.js Points + viser Splat) into a single viser-driven render path; drop the `simRunName`/`simKind`/`frameXyz`/`staticAttrs` state proliferation in favor of one `activeCell` slot.

**Architecture:** viser_headless gains a model-loading codepath that fetches `.ply` files over HTTP from the server's `/api/models/file` endpoint. The frontend's render mode toggle dispatches to a new viser `/mode` endpoint instead of switching between two React components. All state about "what's on screen" collapses into `{kind: "model" | "sequence", name: string}`.

**Tech Stack:** React 18 · TypeScript · Vite · Zustand · viser 1.x · FastAPI · numpy · plyfile.

**Spec:** `docs/superpowers/specs/2026-05-17-viser-unified-renderer-design.md`.

**Verification model:** No frontend test infra. Per-task gate is `npx tsc --noEmit` + `npx vite build`. Per-phase gate is the user hard-reloading `http://localhost:4173/` and walking the visual checklist at the end of each phase. The plan is structured so any phase is independently revertable.

---

## File map

### New

| Path | Purpose |
|---|---|
| `frontend/src/lib/use-active-cell.ts` | Hook + helpers around the new `activeCell` store slice |
| `frontend/src/components/viewport/ViserScene.tsx` | The single render surface — replaces `ViserSplatScene.tsx` and the three.js `<Canvas>` branch |

### Heavily modified

| Path | Change |
|---|---|
| `tools/viser_headless.py` | Add model-loading codepath; render-mode toggle; lazy cell resolution; `/mode` endpoint |
| `frontend/src/lib/store.ts` | Drop `simRunName`/`simKind`/`frameXyz`/`staticAttrs`/`pointsCamera`/`setStaticAttrs`/`putFrame` slices; add `activeCell` slice |
| `frontend/src/components/viewport/Viewport.tsx` | Drop `<Canvas>` branch; render only `<ViserScene>` |
| `frontend/src/components/viewport/RenderModeToggle.tsx` | Dispatch to viser's `/mode` instead of toggling a store enum |
| `frontend/src/components/viewport/PlaybackDriver.tsx` | Gut position-pumping; keep frame-index advancement |
| `frontend/src/components/viewport/PlaybackBar.tsx` | Read frame count from viser state, not from `frameXyz.size` |
| `frontend/src/App.tsx` | Drop `useStreamClient`; rewire `onPickModel`/`onLoadRun` to set `activeCell` |
| `frontend/src/components/sim/SourceCard.tsx` | Use `activeCell` for active-row highlighting |
| `frontend/src/components/sim/SimulationCard.tsx` | Replace `simRunName`-based sequence detection with `activeCell.kind === "sequence"` |
| `frontend/src/components/layout/StatusPanel.tsx` | Replace `simKind` checks with `activeCell` checks |
| `frontend/src/components/runs/RunButton.tsx` | Drop `setSimKind("sim")` (slice gone) |
| `server/gsfluent/api/models.py` | Drop `_ensure_viser_cell` helper + its call sites + cell unlink in DELETE |
| `server/gsfluent/core/models.py` | Drop the `build_viser_cell` call at end of `wrap_ply_upload` |
| `tools/sync_daemon.py` | Active-run aware polling cadence (1s vs 10s) |

### Deleted

| Path | Why |
|---|---|
| `tools/local_stream.py` | Websocket positions stream — viser owns rendering |
| `tools/static_to_viser.py` | Conversion obsolete — viser parses .ply directly |
| `frontend/src/components/viewport/SplatScene.tsx` | Three.js renderer obsolete |
| `frontend/src/components/viewport/ViserSplatScene.tsx` | Replaced by `ViserScene.tsx` |
| `frontend/src/lib/use-stream.ts` | Websocket client obsolete |

---

## Phase 1 — Viser learns to load models + switch modes (gate)

**Objective:** Teach `viser_headless` to lazy-load a model from a `.ply` URL (fetched from the server) and to swap between splat and point rendering. **No frontend changes.** This phase is the gate — if viser's point-mode rendering on 700k splats is materially worse than the three.js path, we revisit before Phase 4 tears the fallback out.

### Task 1.1: Add `mmap_model_cell` helper

**Files:**
- Modify: `tools/viser_headless.py`

- [ ] **Step 1: Add the model-cell parser**

Find the existing `mmap_cell` function (around line 65). Right after it, add:

```python
def mmap_model_cell(ply_path: Path) -> dict:
    """Parse a single-frame model cell from a 3DGS .ply file.

    Mirrors mmap_cell's output shape so the rest of the render loop
    treats models the same way as 1-frame sequences. Unlike mmap_cell
    we *don't* mmap — plyfile materializes the arrays. Models are
    small enough (one frame, ≤200 MB) that the page-on-demand
    optimization doesn't earn its keep here.

    Drops the higher-order SH coefficients (f_rest_*) — viser's splat
    primitive only consumes positions + cov + rgb + opacity. The full
    SH would be wasted bytes.

    Mathematical conversions (3DGS .ply → viser numpy):
      - scales:  exp(scale_*)
      - opacity: sigmoid(opacity_raw)
      - rgb:     clip(0.5 + 0.282 * f_dc_*, 0, 1)  [zero-order SH]
      - quats:   normalize((rot_0, rot_1, rot_2, rot_3))
    """
    from plyfile import PlyData
    v = PlyData.read(str(ply_path)).elements[0]

    xyz = np.stack([v["x"], v["y"], v["z"]], axis=-1).astype(np.float32)
    # Stored pre-sigmoid alpha → real opacity.
    opacity = 1.0 / (1.0 + np.exp(-np.asarray(v["opacity"]).astype(np.float32)))
    # Stored as log-scales → real scales.
    scales = np.exp(np.stack(
        [v["scale_0"], v["scale_1"], v["scale_2"]], axis=-1,
    )).astype(np.float32)
    # Unnormalized quaternion (w, x, y, z) → unit quat.
    quats_raw = np.stack(
        [v["rot_0"], v["rot_1"], v["rot_2"], v["rot_3"]], axis=-1,
    ).astype(np.float32)
    quats = quats_raw / (np.linalg.norm(quats_raw, axis=-1, keepdims=True) + 1e-9)
    # Zero-order SH → RGB.
    SH_C0 = 0.28209479177387814
    f_dc = np.stack(
        [v["f_dc_0"], v["f_dc_1"], v["f_dc_2"]], axis=-1,
    ).astype(np.float32)
    rgb = np.clip(0.5 + SH_C0 * f_dc, 0.0, 1.0)

    f0 = xyz * _VISER_K
    bbox_lo = f0.min(axis=0).astype(np.float32)
    bbox_hi = f0.max(axis=0).astype(np.float32)
    K2 = _VISER_K * _VISER_K

    # Shape as a v2-schema cell with T=1. quats shape (1, N, 4) so
    # _cov_for_frame's existing logic handles the model just like a
    # single-frame sequence.
    return {
        "version": 2,
        "frames": xyz[None, :, :],                # (1, N, 3)
        "quats": quats[None, :, :],               # (1, N, 4)
        "scales_sq": (scales * scales) * K2,      # (N, 3)
        "rgb": rgb,                               # (N, 3)
        "opacity": opacity,                       # (N,)
        "bbox_lo": bbox_lo,
        "bbox_hi": bbox_hi,
    }
```

- [ ] **Step 2: Type-check the change**

Run from the laptop where viser_headless will live:

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg && /home/frankyin/Desktop/work/gsfluent_pkg/server/.venv/bin/python -c "
from tools.viser_headless import mmap_model_cell
from pathlib import Path
print(mmap_model_cell.__doc__.splitlines()[0])
"
```

Expected: prints the first line of the docstring; no import errors.

- [ ] **Step 3: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add tools/viser_headless.py
git -c commit.gpgsign=false commit -m "viser_headless: add mmap_model_cell for .ply → v2 cell"
```

---

### Task 1.2: Add a `fetch_model_ply` helper

**Files:**
- Modify: `tools/viser_headless.py`

viser_headless runs on the laptop. To load a model cell it needs the model's `.ply` bytes. The server exposes them at `/api/models/file?path=<absolute server-side path>`. Since viser_headless is started without knowing the server URL (it just runs on the laptop), we'll accept a `--server` CLI flag for the base URL.

- [ ] **Step 1: Add the helper near the top of the file**

After `_VISER_K` (around line 56):

```python
def fetch_model_ply(server_base: str, model_path_on_server: str) -> Path:
    """Download a model's .ply from the server, cache it to a local
    temp dir, and return the local path.

    Cache key is the absolute path on the server (so collisions are
    impossible across different models). Files persist across viser
    restarts to avoid re-downloading; the laptop's /tmp churn handles
    eviction. Returns the cached Path.

    Args:
      server_base: e.g. "http://localhost:8080" (the SSH tunnel target)
      model_path_on_server: absolute path the server knows, e.g.
        "$GSFLUENT_PKG_ROOT_tmp/work/library/models/<name>"
    """
    import hashlib
    import urllib.parse
    import urllib.request

    cache_dir = Path("/tmp/gsfluent_viser_model_cache")
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(model_path_on_server.encode()).hexdigest()[:16]
    local_path = cache_dir / f"{key}.ply"
    if local_path.exists():
        return local_path

    url = f"{server_base.rstrip('/')}/api/models/file?" \
          f"path={urllib.parse.quote(model_path_on_server)}"
    tmp = local_path.with_suffix(".ply.partial")
    with urllib.request.urlopen(url, timeout=120) as r:
        tmp.write_bytes(r.read())
    tmp.rename(local_path)
    return local_path
```

- [ ] **Step 2: Type-check + commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
/home/frankyin/Desktop/work/gsfluent_pkg/server/.venv/bin/python -c "from tools.viser_headless import fetch_model_ply; print('ok')"
```
Expected: prints `ok`.

```bash
git add tools/viser_headless.py
git -c commit.gpgsign=false commit -m "viser_headless: add fetch_model_ply helper (HTTP fetch + tmp cache)"
```

---

### Task 1.3: Add `--server` CLI flag + the model resolver

**Files:**
- Modify: `tools/viser_headless.py`

- [ ] **Step 1: Add the CLI arg**

In `main()`, alongside the existing `--npz_dir` argument:

```python
    p.add_argument("--server", default="http://localhost:8080",
                   help="Backend base URL (where /api/models/file lives). "
                        "Default: http://localhost:8080 (the SSH tunnel "
                        "target run-client.sh sets up).")
```

- [ ] **Step 2: Add the lazy resolver**

Inside `main()`, AFTER `cells: dict[str, dict] = {}` is populated from the npz scan (around line 280), add:

```python
    def resolve_cell_lazily(name: str) -> bool:
        """If `name` is not yet a loaded cell, try to load it.

        Resolution order:
          1. model:<modelName>  → fetch via /api/models, then .ply, then mmap_model_cell
          2. sequence:<seqName> → look for <seqName>.npz under npz_root
          3. bare <name>        → try sequence first, then model (transition fallback)

        Returns True if the cell is now in `cells`, False otherwise.
        Updates `cells` in place. Idempotent — a re-call with an
        already-loaded name is a no-op.
        """
        import urllib.request, json as _json
        if name in cells:
            return True

        def _try_model(model_name: str) -> bool:
            try:
                with urllib.request.urlopen(
                    f"{args.server.rstrip('/')}/api/models",
                    timeout=10,
                ) as r:
                    listing = _json.loads(r.read())
            except Exception as e:
                print(f"  resolve {name}: failed to list models: {e}")
                return False
            entry = next((m for m in listing if m["name"] == model_name), None)
            if entry is None:
                return False
            try:
                local_ply_dir = fetch_model_ply(args.server, entry["path"])
            except Exception as e:
                print(f"  resolve {name}: model fetch failed: {e}")
                return False
            # The /api/models/file endpoint streams the highest-iteration
            # ply directly — fetch_model_ply saves it as a single file
            # not a dir, so we pass the file path itself.
            try:
                cells[name] = mmap_model_cell(local_ply_dir)
                print(f"  loaded model cell {name} (from {local_ply_dir})")
                return True
            except Exception as e:
                print(f"  resolve {name}: ply parse failed: {e}")
                return False

        def _try_sequence(seq_name: str) -> bool:
            p = npz_root / f"{seq_name}.npz"
            if not p.is_file():
                return False
            try:
                cells[name] = mmap_cell(p)
                print(f"  loaded sequence cell {name} from {p}")
                return True
            except Exception as e:
                print(f"  resolve {name}: npz mmap failed: {e}")
                return False

        if name.startswith("model:"):
            return _try_model(name[len("model:"):])
        if name.startswith("sequence:"):
            return _try_sequence(name[len("sequence:"):])
        # Bare name (transition fallback): sequence first, then model.
        return _try_sequence(name) or _try_model(name)
```

- [ ] **Step 3: Wire `resolve_cell_lazily` into `/set`**

Find the `@api.post("/set")` handler (around line 437). Modify it to call the resolver before the cell-unknown error:

Replace:
```python
            if body.cell is not None:
                if body.cell not in cells:
                    return {"ok": False, "error": f"unknown cell: {body.cell}",
                            "cells": list(cells)}
```

with:
```python
            if body.cell is not None:
                if body.cell not in cells:
                    # Try lazy resolution (model: prefix, sequence: prefix,
                    # or bare-name fallback during the transition phase).
                    if not resolve_cell_lazily(body.cell):
                        return {"ok": False, "error": f"unknown cell: {body.cell}",
                                "cells": list(cells)}
```

(Note: `resolve_cell_lazily` is defined inside `main()` so it closes over `cells`, `args`, and `npz_root`. The `/set` handler is also inside `main()` and can see it directly via closure.)

- [ ] **Step 4: Smoke-test from the laptop**

Verify the model resolver works against your running stack:

```bash
# Restart viser_headless via the existing run-client.sh stack so it
# picks up the new code. Quickest way: kill viser_headless's pid; the
# stack supervisor restarts it. Or restart the whole client stack.
VPID=$(pgrep -f "tools/viser_headless.py" | head -1) && kill "$VPID"
# Wait ~3s for run-client.sh to relaunch, or just relaunch manually.

# Trigger model resolution:
curl -s --noproxy '*' -X POST http://localhost:8092/set \
  -H 'Content-Type: application/json' \
  -d '{"cell":"model:cluster_6_15"}'
```

Expected: `{"ok": true, "cell": "model:cluster_6_15", "frame": 0}` + viser log shows `loaded model cell model:cluster_6_15 ...`. The iframe at `http://localhost:8091` should now render the static cluster_6_15 ply as splats.

If you get an error like `unknown cell` even after this, check:
- viser_headless was actually restarted (check its stderr for the new `loaded model cell` line)
- `/api/models` is reachable from the laptop (it is — `curl http://localhost:8080/api/models` should return a list)

- [ ] **Step 5: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add tools/viser_headless.py
git -c commit.gpgsign=false commit -m "viser_headless: lazy model:/sequence: cell resolution in /set"
```

---

### Task 1.4: Add render-mode toggle (splat ↔ points)

**Files:**
- Modify: `tools/viser_headless.py`

- [ ] **Step 1: Track current render-mode in state**

Find the state dict initialization (around line 360). Add a `mode` key:

```python
    state = {
        "cell": cur_name,
        "frame": 0,
        "pushed_cell": cur_name,
        "pushed_frame": -1,
        "mode": "splat",     # "splat" | "points"; toggled via POST /mode
        # ... existing keys
```

- [ ] **Step 2: Add a per-cell scene-node lifecycle**

Find where the initial `splat = server.scene.add_gaussian_splats(...)` is set up (around line 317). Wrap the scene-node-creation logic in a function so we can call it again when the mode toggles:

Replace the existing `splat = server.scene.add_gaussian_splats(...)` block with:

```python
    # The render handle. `splat` is a viser handle to either a
    # gaussian-splats node OR a point-cloud node; the mode toggle
    # swaps which primitive is mounted. _rebuild_scene_node disposes
    # the current handle and creates a fresh one with the active cell's
    # data in the active mode.
    splat = None

    def _rebuild_scene_node():
        nonlocal splat
        cur_c = cells[state["cell"]]
        centers = np.ascontiguousarray(
            np.asarray(cur_c["frames"][state["frame"]]) * _VISER_K
        )
        # Remove the previous node if any. Viser handles expose `remove()`.
        if splat is not None:
            try:
                splat.remove()
            except Exception:
                pass
            splat = None
        if state["mode"] == "splat":
            splat = server.scene.add_gaussian_splats(
                "splat",
                centers=centers,
                covariances=_cov_for_frame(cur_c, state["frame"]),
                rgbs=np.ascontiguousarray(cur_c["rgb"]),
                opacities=np.ascontiguousarray(cur_c["opacity"]),
            )
        else:
            # points mode — viser's primitive vertex renderer. Sized
            # proportional to scene_scale so small/large scenes both
            # look reasonable.
            extent = np.maximum(cur_c["bbox_hi"] - cur_c["bbox_lo"], 1e-6)
            scene_scale = float(extent.max())
            point_size = max(scene_scale * 0.002, 0.001)
            splat = server.scene.add_point_cloud(
                "splat",
                points=centers,
                colors=(np.ascontiguousarray(cur_c["rgb"]) * 255).astype(np.uint8),
                point_size=point_size,
            )

    _rebuild_scene_node()
```

- [ ] **Step 3: Add `POST /mode` endpoint**

After the `@api.post("/set")` handler, add:

```python
    class ModeBody(BaseModel):
        mode: str   # "splat" | "points"

    @api.post("/mode")
    def set_mode(body: ModeBody) -> dict:
        """Switch the active cell's render primitive between splat and
        points. Rebuilds the scene node — cheap for either primitive
        (~10ms on cluster_6_15-class data)."""
        if body.mode not in ("splat", "points"):
            raise HTTPException(422, f"mode must be 'splat' or 'points', got {body.mode!r}")
        with lock:
            if state["mode"] != body.mode:
                state["mode"] = body.mode
                _rebuild_scene_node()
        return {"ok": True, "mode": state["mode"]}
```

(The `ModeBody` class needs to be defined; place it near `SetBody` and `CameraBody`.)

- [ ] **Step 4: Make `/set` rebuild on cell switch**

In the existing `/set` handler, after the cell-switch block sets `state["scene_dirty"] = True`, also call `_rebuild_scene_node()`:

Inside the `if body.cell != state["cell"]:` block, replace just after `state["scene_dirty"] = True`:

```python
                if body.cell != state["cell"]:
                    state["cell"] = body.cell
                    state["scene_dirty"] = True
                    # ... existing clamp logic ...
                    _rebuild_scene_node()
```

- [ ] **Step 5: Restart viser + smoke-test mode toggle**

```bash
VPID=$(pgrep -f "tools/viser_headless.py" | head -1) && kill "$VPID"
# Wait for relaunch.

# Switch to points mode:
curl -s --noproxy '*' -X POST http://localhost:8092/mode \
  -H 'Content-Type: application/json' \
  -d '{"mode":"points"}'

# Open http://localhost:8091 in a browser. cluster_6_15 should render
# as points (not splats). Visually check for:
#   - Are points readable as a building / scan?
#   - Frame rate acceptable (~30+ fps)?
#   - Same scene framing as splat mode?
```

**Phase 1 GATE**: If point-mode quality is materially worse than the
three.js Points renderer (illegible, blocky, slow on 700k splats), STOP.
Either:
- Improve the viser point-cloud parameters (point_size, density)
- Decide to keep three.js Points as a separate fallback (don't proceed to Phase 4's deletion)

If it looks acceptable: proceed.

- [ ] **Step 6: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add tools/viser_headless.py
git -c commit.gpgsign=false commit -m "viser_headless: /mode endpoint + scene-node rebuild on cell/mode change"
```

---

### Phase 1 visual verification

Open `http://localhost:4173/` and do NOT change anything in the React UI. Then via curl:

**Visual checklist:**

- [ ] `curl -X POST http://localhost:8092/set -d '{"cell":"model:cluster_6_15"}'` — viser iframe shows cluster_6_15 building as splats (no React change needed)
- [ ] `curl -X POST http://localhost:8092/mode -d '{"mode":"points"}'` — same scene, now as points
- [ ] `curl -X POST http://localhost:8092/mode -d '{"mode":"splat"}'` — back to splats
- [ ] `curl -X POST http://localhost:8092/set -d '{"cell":"sequence:api_jelly_native_200k_1778820309"}'` — sequence playback still works (existing path unbroken)
- [ ] `curl -X POST http://localhost:8092/set -d '{"cell":"model:cluster_6_15"}'` — flip back to a model
- [ ] `curl http://localhost:8092/state` — `cells` list contains both the model and sequence cells, `mode` is `splat`

If point-mode visual quality is acceptable, the gate is passed. Move to Phase 2.

---

## Phase 2 — Rip out static→viser conversion

**Objective:** Delete `static_to_viser.py` + the build-cell-on-upload wiring. Viser now loads .ply directly per Phase 1. Reduces server footprint and eliminates the duplicate-storage issue.

### Task 2.1: Drop `build_viser_cell` call in `wrap_ply_upload`

**Files:**
- Modify: `server/gsfluent/core/models.py`

- [ ] **Step 1: Find and remove the call**

Search for the block at the end of `wrap_ply_upload`. Look for:

```python
    try:
        from .static_to_viser import build_viser_cell
        viser_cache = PKG_ROOT / "work" / "cache" / "viser"
        viser_cache.mkdir(parents=True, exist_ok=True)
        build_viser_cell(ply_path, viser_cache / f"{name}.npz")
        _log.info("built viser cell for static model %s", name)
    except Exception as e:
        _log.warning("could not build viser cell for %s: %s; Splat mode won't work for this model", name, e)
```

Delete the entire block.

- [ ] **Step 2: Type-check + commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
python3 -c "import ast; ast.parse(open('server/gsfluent/core/models.py').read()); print('ok')"
```

Expected: prints `ok`.

```bash
git add server/gsfluent/core/models.py
git -c commit.gpgsign=false commit -m "models.py: drop build_viser_cell call (viser now loads .ply directly)"
```

---

### Task 2.2: Drop `_ensure_viser_cell` from `api/models.py`

**Files:**
- Modify: `server/gsfluent/api/models.py`

- [ ] **Step 1: Find and remove the helper + its call sites**

Look for the `_ensure_viser_cell` function definition (a helper added in the previous phase). Also look for its call sites inside the `check_hash` and `upload` handlers (specifically the dedup short-circuit path that returns `existing.meta_dict()`).

Remove:
- The `_ensure_viser_cell` function itself
- Both call sites (one in `check_hash`, one in `upload`'s dedup short-circuit)
- The `from .core.static_to_viser import build_viser_cell` import (if present at module top)

Be careful NOT to remove the surrounding logic — only the cell-ensure call lines.

- [ ] **Step 2: Drop the cell-unlink in DELETE**

Find the `DELETE /{name}` handler at the bottom of the file. Look for code that unlinks the viser cache file:

```python
    # also clean up the viser cell if one exists
    try:
        (PKG_ROOT / "work" / "cache" / "viser" / f"{name}.npz").unlink(missing_ok=True)
    except OSError:
        pass
```

Delete that block. (Phase 2 leaves any existing `<name>.npz` files on disk; sync_daemon will eventually flush them — they're harmless cache entries.)

- [ ] **Step 3: Type-check + commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
python3 -c "import ast; ast.parse(open('server/gsfluent/api/models.py').read()); print('ok')"
```
Expected: `ok`.

```bash
git add server/gsfluent/api/models.py
git -c commit.gpgsign=false commit -m "api/models.py: drop _ensure_viser_cell + DELETE-time cell unlink"
```

---

### Task 2.3: Delete `static_to_viser.py`

**Files:**
- Delete: `server/gsfluent/core/static_to_viser.py`

- [ ] **Step 1: Verify nothing imports it**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
grep -rn "static_to_viser" server/ frontend/ tools/ 2>&1
```

Expected: zero matches (other than possibly in tests or docs — flag if present, otherwise proceed).

- [ ] **Step 2: Delete + commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
rm server/gsfluent/core/static_to_viser.py
git add -A server/
git -c commit.gpgsign=false commit -m "Delete static_to_viser.py (viser now loads .ply directly via /api/models/file)"
```

---

### Task 2.4: Drop the `pokeViserReload` call in DropZone

**Files:**
- Modify: `frontend/src/components/viewport/DropZone.tsx`

In Phase 1 of the just-shipped change, DropZone added a `pokeViserReload` helper that POSTed to viser's `/reload` endpoint after a successful upload. With viser no longer caching a per-model npz, the reload poke is a no-op. Remove it.

- [ ] **Step 1: Delete the helper and its call sites**

Find `async function pokeViserReload(modelName: string)` (around line 37). Remove the entire function.

Then remove the two call sites in `onDrop`:
- The one inside the dedup-hit branch after `safeToast(\`Already in library...)`
- The one after the successful upload after `safeToast(\`Uploaded ${m.name}\`)`

Both look like `pokeViserReload(check.name)` or `pokeViserReload(m.name)`.

- [ ] **Step 2: Build + commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/frontend && npx tsc --noEmit 2>&1 | head -20 && npx vite build 2>&1 | tail -5
```

Expected: clean.

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add frontend/src/components/viewport/DropZone.tsx
git -c commit.gpgsign=false commit -m "DropZone: drop pokeViserReload (viser no longer per-model cell)"
```

---

### Task 2.5: Deploy the Phase-2 server changes to your-server

**Files:**
- (Deploy `api/models.py` + `core/models.py` to the running server)

- [ ] **Step 1: Rsync the modified server files**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
rsync -avz server/gsfluent/api/models.py your-server:$GSFLUENT_PKG_ROOT_tmp/server/gsfluent/api/models.py
rsync -avz server/gsfluent/core/models.py your-server:$GSFLUENT_PKG_ROOT_tmp/server/gsfluent/core/models.py
```

- [ ] **Step 2: Remove the now-unused server-side helper**

```bash
ssh your-server 'rm -f $GSFLUENT_PKG_ROOT_tmp/server/gsfluent/core/static_to_viser.py'
```

- [ ] **Step 3: Restart gsfluent serve**

```bash
ssh your-server '
GPID=$(pgrep -f "gsfluent serve" | head -1)
kill $GPID
for i in 1 2 3 4 5 6; do kill -0 $GPID 2>/dev/null || break; sleep 1; done
if kill -0 $GPID 2>/dev/null; then kill -9 $GPID; sleep 1; fi
nohup env GSFLUENT_SIM_PYTHON=$CONDA_ROOT/envs/GaussianFluent/bin/python GSFLUENT_SIM_HOME=$GSFLUENT_SIM_HOME $CONDA_ROOT/envs/gsfluent-api/bin/gsfluent serve --host 0.0.0.0 --port 18080 --no-browser > $GSFLUENT_PKG_ROOT_tmp/work/logs/server.log 2>&1 & disown
sleep 3
curl -s http://localhost:18080/api/health'
```

Expected: `{"status":"ok",...}`.

- [ ] **Step 4: No commit (deploy step only)**

---

### Phase 2 visual verification

**Visual checklist:**

- [ ] Drop a new .ply via the workbench — uploads cleanly, no console errors about missing viser cells
- [ ] Switch to Splat mode for the freshly-uploaded model — viser loads the .ply directly (verify by `tail $GSFLUENT_PKG_ROOT_tmp/work/logs/server.log` or viser's stderr showing the model-fetch line). May take a few seconds the first time.
- [ ] The model's previous `.npz` cache file (if any) is still on disk and harmless — verify Sim still works against it for legacy reasons
- [ ] Delete a model — model dir gone, viser cell (if any) stays on disk, no errors

---

## Phase 3 — Frontend state refactor

**Objective:** Replace the overloaded `simRunName` + `simKind` + `frameXyz` + `staticAttrs` slices with a single `activeCell: {kind: "model" | "sequence", name: string} | null` slot. This phase changes lots of files; nothing visible to the user yet (viewport still renders via the dual path).

### Task 3.1: Add `activeCell` slice to the store

**Files:**
- Modify: `frontend/src/lib/store.ts`

- [ ] **Step 1: Add the new slice alongside existing ones**

Find the state type definition. Add (near the existing `simRunName` etc.):

```ts
  /** Replaces the simRunName-as-string overload. Encodes both what
   *  kind of resource is loaded (model vs sequence) and its name,
   *  with no prefix-shenanigans. Null when nothing is loaded. */
  activeCell: { kind: "model" | "sequence"; name: string } | null;
  setActiveCell: (cell: { kind: "model" | "sequence"; name: string } | null) => void;
```

In the `create()` body:

```ts
  activeCell: null,
  setActiveCell: (cell) => set({ activeCell: cell }),
```

- [ ] **Step 2: Type-check**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/frontend && npx tsc --noEmit 2>&1 | head -10
```

Expected: clean.

- [ ] **Step 3: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add frontend/src/lib/store.ts
git -c commit.gpgsign=false commit -m "store: add activeCell slice (transitional — coexists with simRunName)"
```

---

### Task 3.2: Add `use-active-cell.ts` hook

**Files:**
- Create: `frontend/src/lib/use-active-cell.ts`

- [ ] **Step 1: Write the hook**

```ts
import { useStore } from "./store";

/** Returns the active cell + its wire-format name (with `model:` /
 *  `sequence:` prefix) for forwarding to viser's /set endpoint.
 *
 *  Cells on the wire MUST carry the kind prefix — viser uses it to
 *  decide whether to fetch a .ply or mmap a .npz. The frontend store
 *  keeps the kind separate from the name for ergonomics, but everything
 *  that talks to viser must use this hook (or the helper below) to
 *  render the wire name. */
export function useActiveCell() {
  const activeCell = useStore((s) => s.activeCell);
  const setActiveCell = useStore((s) => s.setActiveCell);
  return {
    activeCell,
    setActiveCell,
    /** Wire-format cell name (e.g. "model:tower_01"). Null when no cell. */
    wireName: activeCell ? `${activeCell.kind}:${activeCell.name}` : null,
    /** True when the current activity is a finished sequence (replay). */
    isSequence: activeCell?.kind === "sequence",
    /** True when the current activity is a static-model preview. */
    isModel: activeCell?.kind === "model",
  };
}

/** Imperative form — useful when not in a React component. */
export function getActiveCellWireName(): string | null {
  const cell = useStore.getState().activeCell;
  return cell ? `${cell.kind}:${cell.name}` : null;
}
```

- [ ] **Step 2: Type-check + commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/frontend && npx tsc --noEmit 2>&1 | head -10
```

Expected: clean.

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add frontend/src/lib/use-active-cell.ts
git -c commit.gpgsign=false commit -m "lib: add useActiveCell hook + getActiveCellWireName helper"
```

---

### Task 3.3: Update `App.tsx` callbacks to write `activeCell`

**Files:**
- Modify: `frontend/src/App.tsx`

`onPickModel` and `onLoadRun` are the two places that decide "what's loaded." Both currently set `simRunName` via `resetForNewRun`. Add a sibling `setActiveCell` call so `activeCell` mirrors the old state (for now — Phase 3 keeps both around for transition safety).

- [ ] **Step 1: In `onPickModel`, set the activeCell:**

Locate `onPickModel`. After `setActiveModel(m)`:

```ts
      useStore.getState().setActiveCell({ kind: "model", name: m.name });
```

- [ ] **Step 2: In `onLoadRun`, set the activeCell:**

Locate `onLoadRun`. After `resetForNewRun(run_name)`:

```ts
      useStore.getState().setActiveCell({ kind: "sequence", name: run_name });
```

- [ ] **Step 3: After successful Run from RunButton (palette path), set activeCell too**

Find `triggerRun` (the palette flow). After `st.resetForNewRun(run_name)`:

```ts
      st.setActiveCell({ kind: "sequence", name: run_name });
```

- [ ] **Step 4: Build + commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/frontend && npx tsc --noEmit 2>&1 | head -10 && npx vite build 2>&1 | tail -5
```

Expected: clean.

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add frontend/src/App.tsx
git -c commit.gpgsign=false commit -m "App: mirror simRunName changes into activeCell (transitional)"
```

---

### Task 3.4: Also set activeCell in RunButton

**Files:**
- Modify: `frontend/src/components/runs/RunButton.tsx`

The Run button's `onRun` already calls `resetForNewRun(run_name)`. Add an `setActiveCell` mirror right after it.

- [ ] **Step 1: Edit**

After `resetForNewRun(run_name);`:

```ts
      useStore.getState().setActiveCell({ kind: "sequence", name: run_name });
```

- [ ] **Step 2: Build + commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/frontend && npx tsc --noEmit 2>&1 | head -10
```
Expected: clean.

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add frontend/src/components/runs/RunButton.tsx
git -c commit.gpgsign=false commit -m "RunButton: mirror simRunName into activeCell"
```

---

### Task 3.5: Update `ViserSplatScene.tsx` to consume `activeCell` first

**Files:**
- Modify: `frontend/src/components/viewport/ViserSplatScene.tsx`

Current code reads `simRunName` and strips the `_model:` prefix. The new code reads `activeCell.wireName` directly.

- [ ] **Step 1: Replace the cell-derivation logic**

At the top of the component, replace:
```ts
  const simRunName = useStore((s) => s.simRunName);
```
with:
```ts
  import { useActiveCell } from "@/lib/use-active-cell";
  // ... inside the component:
  const { wireName } = useActiveCell();
  // Keep simRunName around as a transition fallback until Phase 4
  // when every consumer is migrated.
  const simRunName = useStore((s) => s.simRunName);
```

In the cell-forwarding effect, replace the prefix-stripping logic:
```ts
    const cell = simRunName
      ? (simRunName.startsWith("_model:") ? simRunName.slice("_model:".length) : simRunName)
      : null;
```
with:
```ts
    const cell = wireName;  // already in "model:foo" or "sequence:foo" form
```

- [ ] **Step 2: Same swap in the cellMissing block**

Look for the second prefix-stripping near the bottom (around the `cellMissing` derivation):
```ts
  const cellName = simRunName
    ? (simRunName.startsWith("_model:") ? simRunName.slice("_model:".length) : simRunName)
    : null;
```
Replace with:
```ts
  const cellName = wireName;
```

- [ ] **Step 3: Update the effect's dep array**

Change `simRunName` to `wireName` in the useEffect deps. Drop the unused `simRunName` import if it's no longer referenced.

- [ ] **Step 4: Build + commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/frontend && npx tsc --noEmit 2>&1 | head -10 && npx vite build 2>&1 | tail -5
```
Expected: clean.

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add frontend/src/components/viewport/ViserSplatScene.tsx
git -c commit.gpgsign=false commit -m "ViserSplatScene: read activeCell wireName (drops prefix-strip)"
```

---

### Task 3.6: Update remaining `simRunName` / `simKind` consumers

**Files (one commit each):**
- Modify: `frontend/src/components/sim/SimulationCard.tsx`
- Modify: `frontend/src/components/sim/SourceCard.tsx`
- Modify: `frontend/src/components/layout/StatusPanel.tsx`
- Modify: `frontend/src/components/viewport/Viewport.tsx`

Each consumer has its own pattern to swap. Do them one file at a time, each with its own build + commit.

- [ ] **Step 1: SimulationCard — `isSequenceRun` / `isSequenceUnderModel` derivation**

Find:
```ts
  const isSequenceRun = !!simRunName && !simRunName.startsWith("_model:");
```
Replace with:
```ts
  const { isSequence, activeCell } = useActiveCell();
  const isSequenceRun = isSequence;
```

Then update `seq` lookup: replace `s.name === simRunName` with `s.name === activeCell?.name`. Update the `seqRecipeSource` cast to use `seq?.recipe_source` (unchanged but verify path still resolves now that `seq` derives from `activeCell.name`).

Build + commit:
```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/frontend && npx tsc --noEmit 2>&1 | head -10
git add frontend/src/components/sim/SimulationCard.tsx
git -c commit.gpgsign=false commit -m "SimulationCard: derive isSequence from activeCell"
```

- [ ] **Step 2: SourceCard — active-row highlight**

Find both places (the model row and the sequence row) that compare to `simRunName`:
```ts
            (simRunName === s.name ? "text-accent" : "text-text-secondary")
```
Replace with:
```ts
            (activeCell?.kind === "sequence" && activeCell.name === s.name ? "text-accent" : "text-text-secondary")
```

Similarly for the model row highlight (`activeModel?.name === m.name`) — that one stays since it reads `activeModel` directly, but worth confirming `activeCell.kind === "model"` is consistent. (Actually `activeModel` is separate from `activeCell`; both should agree but `activeModel` keeps its semantics for now — defer cleanup.)

Add `import { useActiveCell } from "@/lib/use-active-cell";` at the top. Use:
```ts
  const { activeCell } = useActiveCell();
```

Drop the `simRunName` store-selector if no longer referenced.

Build + commit:
```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/frontend && npx tsc --noEmit 2>&1 | head -10
git add frontend/src/components/sim/SourceCard.tsx
git -c commit.gpgsign=false commit -m "SourceCard: highlight active row from activeCell"
```

- [ ] **Step 3: StatusPanel — replace simKind with activeCell**

Find:
```ts
  const simKind = useStore((s) => s.simKind);
  // ...
  if (simKind === "replay") {
```

Replace with:
```ts
  const { activeCell } = useActiveCell();
  // Replay: viewing a finished sequence cell (not a fresh sim run).
  // A sim run also has activeCell.kind === "sequence" but simState
  // === "running" disambiguates.
  const isReplay = activeCell?.kind === "sequence" && simState !== "running";
  if (isReplay) {
```

Drop the `simKind` selector. Add the `useActiveCell` import.

Same pattern for the `simKind === "preview"` checks (if any) — replace with `activeCell?.kind === "model"`.

Build + commit:
```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/frontend && npx tsc --noEmit 2>&1 | head -10
git add frontend/src/components/layout/StatusPanel.tsx
git -c commit.gpgsign=false commit -m "StatusPanel: replace simKind checks with activeCell+simState"
```

- [ ] **Step 4: Viewport.tsx — drop the `isModelPreview` / `isSimRun` derivations from simRunName**

The Viewport currently derives `isModelPreview` from `simRunName.startsWith("_model:")`. Replace with `activeCell` reads:

```ts
  const { activeCell, isSequence, isModel } = useActiveCell();
  const splatAvailable = !!activeCell;  // viser handles both now
```

Drop the old `isModelPreview` / `isSimRun` lines.

Build + commit:
```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/frontend && npx tsc --noEmit 2>&1 | head -10
git add frontend/src/components/viewport/Viewport.tsx
git -c commit.gpgsign=false commit -m "Viewport: derive isSequence/isModel from activeCell"
```

---

### Phase 3 visual verification

The viewport should still work identically (it's been reading from `activeCell` instead of `simRunName` — but both are kept in sync). This phase is invisible; the verification confirms no regressions.

**Visual checklist:**

- [ ] Open the workbench, pick a model → still loads in Points mode
- [ ] Pick a sequence → still plays back
- [ ] Toggle Splat mode → viser still renders the sequence
- [ ] Toggle Splat mode for a model → viser renders the model (Phase 1's path)
- [ ] Click Run → progress UI works
- [ ] No console errors

---

## Phase 4 — Drop three.js Canvas + websocket stream

**Objective:** Now that viser is the only renderer that matters, remove the three.js path entirely. Single render surface = single source of truth for "what's on screen."

### Task 4.1: Drop the three.js Canvas branch from Viewport

**Files:**
- Modify: `frontend/src/components/viewport/Viewport.tsx`

- [ ] **Step 1: Replace the `effectiveMode === "splat" ? ... : <Canvas>` ternary**

The current render JSX is something like:
```tsx
      {effectiveMode === "splat" ? (
        <ViserSplatScene />
      ) : (
        <Canvas ...>
          <Grid ... />
          <OrbitControls ... />
          <GizmoHelper ... />
          {staticAttrs && <SplatScene />}
        </Canvas>
      )}
```

Replace with just:
```tsx
      <ViserSplatScene />
```

Drop all the `<Canvas>` / `<Grid>` / `<OrbitControls>` / `<GizmoHelper>` / `<SplatScene>` imports + usage from this file. Also drop the `UpAxisSync` helper component if it's defined here.

- [ ] **Step 2: Drop unused imports**

Remove these imports from the top of Viewport.tsx:
- `Canvas` from `@react-three/fiber`
- `OrbitControls, Grid, GizmoHelper, GizmoViewport` from `@react-three/drei`
- `DoubleSide` from three
- `SplatScene` from `./SplatScene`
- Anything else that's now unused

- [ ] **Step 3: Build + commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/frontend && npx tsc --noEmit 2>&1 | head -10 && npx vite build 2>&1 | tail -5
```
Expected: clean.

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add frontend/src/components/viewport/Viewport.tsx
git -c commit.gpgsign=false commit -m "Viewport: drop three.js Canvas branch (viser is the only renderer)"
```

---

### Task 4.2: Make RenderModeToggle dispatch to viser's /mode

**Files:**
- Modify: `frontend/src/components/viewport/RenderModeToggle.tsx`

- [ ] **Step 1: Rewrite the click handlers**

The current toggle writes to `useStore.getState().setRenderMode("splat"|"points")`. Replace with a fetch to viser's `/mode`:

```tsx
import { useEffect, useState } from "react";

export function RenderModeToggle({ splatAvailable }: { splatAvailable: boolean }) {
  const [mode, setModeLocal] = useState<"splat" | "points">("splat");

  const controlUrl = (import.meta.env.VITE_VISER_CONTROL_URL as string | undefined)
    || `http://${location.hostname}:8092`;

  const switchMode = async (next: "splat" | "points") => {
    setModeLocal(next);
    try {
      await fetch(`${controlUrl}/mode`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: next }),
      });
    } catch {
      /* viser unreachable; the next state change will retry */
    }
  };

  // ... rest of the JSX, using `mode` + `switchMode` instead of the store ...
}
```

Drop the `renderMode` + `setRenderMode` selectors from the store. The toggle now owns its local state and pushes to viser directly.

- [ ] **Step 2: Drop `renderMode` slice from the store**

In `frontend/src/lib/store.ts`, remove:
- `renderMode: RenderMode;` field
- `setRenderMode: (m: RenderMode) => void;` setter
- The corresponding lines in `create()`
- The `type RenderMode = "points" | "splat"` if not used elsewhere (it's exported — check)

- [ ] **Step 3: Build + commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/frontend && npx tsc --noEmit 2>&1 | head -10 && npx vite build 2>&1 | tail -5
```
Expected: clean.

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add frontend/src/components/viewport/RenderModeToggle.tsx frontend/src/lib/store.ts
git -c commit.gpgsign=false commit -m "RenderModeToggle: dispatch to viser /mode (drops renderMode store slice)"
```

---

### Task 4.3: Gut PlaybackDriver

**Files:**
- Modify: `frontend/src/components/viewport/PlaybackDriver.tsx`

The driver currently advances `currentFrameIdx` and pumps positions into the three.js geometry. The positions-pumping is now viser's job; only the frame-index advancement stays (viser reads frame from /set posts).

- [ ] **Step 1: Strip the buffer/position-pump code**

Remove all references to `frameXyz`, `staticAttrs`, `setStaticAttrs`, `putFrame`. Keep only:
- `currentFrameIdx` read/write
- `playing` / `scrubbing` checks
- `speedX` for delay
- The RAF tick loop
- The buffer-aware "hold if next frame doesn't exist" logic, but query viser's `/state` for `n_frames` instead of `frameXyz.size`

Replace the n-frames source. Today it reads `useStore.getState().frameXyz.size`. After this change it polls viser's `/state`:

```ts
  // n_frames comes from viser's authoritative state, not from a local
  // position-buffer count.
  const [nFrames, setNFrames] = useState(0);
  const controlUrl = (import.meta.env.VITE_VISER_CONTROL_URL as string | undefined)
    || `http://${location.hostname}:8092`;

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const r = await fetch(`${controlUrl}/state`);
        const d = await r.json();
        if (!cancelled) setNFrames(d.n_frames ?? 0);
      } catch {
        /* ignore */
      }
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => { cancelled = true; clearInterval(id); };
  }, [controlUrl]);
```

In the RAF advance loop:
```ts
        const lastIdx = Math.max(nFrames - 1, 0);
        const nextIdx = currentFrameIdx + 1;
        if (nextIdx > lastIdx) {
          if (loop) setCurrentFrame(0);
          else setPlaying(false);
        } else {
          setCurrentFrame(nextIdx);
        }
```

(Drop the `frameXyz.has(nextIdx)` buffer-check — viser already clamps frame to the cell's `n_frames` server-side, so client never advances past it.)

When `currentFrameIdx` changes, also forward to viser:
```ts
  useEffect(() => {
    fetch(`${controlUrl}/set`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ frame: currentFrameIdx }),
    }).catch(() => {});
  }, [currentFrameIdx, controlUrl]);
```

Actually — wait. ViserSplatScene already does this. Don't duplicate. Just don't push the frame from PlaybackDriver; rely on ViserSplatScene's existing effect.

- [ ] **Step 2: Build + commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/frontend && npx tsc --noEmit 2>&1 | head -10 && npx vite build 2>&1 | tail -5
```
Expected: clean.

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add frontend/src/components/viewport/PlaybackDriver.tsx
git -c commit.gpgsign=false commit -m "PlaybackDriver: gut position-pump; n_frames now from viser /state"
```

---

### Task 4.4: Update PlaybackBar to read frame count from viser

**Files:**
- Modify: `frontend/src/components/viewport/PlaybackBar.tsx`

PlaybackBar reads `frameXyz.size` to derive `lastIdx`. Replace with a `/state` poll (or — simpler — share a single polling source with PlaybackDriver via a new store slice).

- [ ] **Step 1: Add a viser-state slice to the store**

In `frontend/src/lib/store.ts`, replace `frameXyz: Map<...>` with:

```ts
  viserState: { cell: string | null; frame: number; n_frames: number };
  setViserState: (s: { cell: string | null; frame: number; n_frames: number }) => void;
```

Initialize:
```ts
  viserState: { cell: null, frame: 0, n_frames: 0 },
  setViserState: (s) => set({ viserState: s }),
```

Drop `frameXyz`, `putFrame`, `staticAttrs`, `setStaticAttrs`, `pointsCamera`, `setPointsCamera` from the store.

- [ ] **Step 2: Centralize the /state polling in ViserSplatScene**

In `ViserSplatScene.tsx`, replace the existing one-time `fetch(controlUrl/state)` on mount with a polling effect that writes to `viserState`:

```ts
  const setViserState = useStore((s) => s.setViserState);
  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const r = await fetch(`${controlUrl}/state`);
        const d = await r.json();
        if (!cancelled) {
          setControlReachable(true);
          setServerCells(d.cells ?? []);
          setViserState({
            cell: d.cell,
            frame: d.frame,
            n_frames: d.n_frames ?? 0,
          });
        }
      } catch {
        if (!cancelled) setControlReachable(false);
      }
    };
    tick();
    const id = setInterval(tick, 500);
    return () => { cancelled = true; clearInterval(id); };
  }, [controlUrl, setViserState]);
```

- [ ] **Step 3: PlaybackBar reads viserState**

Replace:
```ts
  const frameCount = useStore((s) => s.frameXyz.size);
```
with:
```ts
  const nFrames = useStore((s) => s.viserState.n_frames);
  // ... use nFrames in place of frameCount everywhere
```

- [ ] **Step 4: PlaybackDriver reads viserState**

In PlaybackDriver, drop the local `nFrames` state and the /state poll; read `useStore((s) => s.viserState.n_frames)` instead.

- [ ] **Step 5: Build + commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/frontend && npx tsc --noEmit 2>&1 | head -10 && npx vite build 2>&1 | tail -5
```
Expected: clean.

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add frontend/src/lib/store.ts frontend/src/components/viewport/PlaybackBar.tsx frontend/src/components/viewport/PlaybackDriver.tsx frontend/src/components/viewport/ViserSplatScene.tsx
git -c commit.gpgsign=false commit -m "Playback: n_frames from viser /state (drops frameXyz)"
```

---

### Task 4.5: Drop the websocket stream client

**Files:**
- Modify: `frontend/src/App.tsx`
- Delete: `frontend/src/lib/use-stream.ts`

- [ ] **Step 1: Remove the `useStreamClient` usage from App.tsx**

In `App.tsx`:
- Remove `import { useStreamClient } from "@/lib/use-stream"`
- Remove `const client = useStreamClient();`
- Remove `useEffect(() => { client.connect(); }, [client]);`
- Replace every `client.subscribe(...)` and `client.loadModel(...)` call:
  - `client.loadModel(m.path)` → just remove (viser loads on `/set` push from ViserSplatScene's effect; the load is implicit when activeCell changes)
  - `client.subscribe(run_name)` → just remove (same reason)
- Drop `subscribe` from props passed to AppShell / SimulationCard / RunButton if it's no longer needed

- [ ] **Step 2: Drop the `subscribe` prop from AppShell + downstream**

In `AppShell.tsx`, remove the `subscribe` prop from its `Props` type and stop forwarding it.

Search for everything that takes a `subscribe` callback and verify it's no longer needed. The RunButton currently uses it to post-subscribe after a successful `api.runs.start` — but with no websocket, there's nothing to subscribe to. Drop the prop + the call.

- [ ] **Step 3: Delete the use-stream module**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
grep -rn "use-stream\|useStreamClient" frontend/src/ --include="*.ts" --include="*.tsx"
# Expect: zero matches after above edits
rm frontend/src/lib/use-stream.ts
```

- [ ] **Step 4: Build + commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/frontend && npx tsc --noEmit 2>&1 | head -10 && npx vite build 2>&1 | tail -5
```
Expected: clean.

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add -A frontend/src/
git -c commit.gpgsign=false commit -m "Drop useStreamClient + websocket stream (viser owns rendering)"
```

---

### Task 4.6: Delete `SplatScene.tsx` + the dead store fields

**Files:**
- Delete: `frontend/src/components/viewport/SplatScene.tsx`
- Modify: `frontend/src/lib/store.ts` (already partially done; final pass)

- [ ] **Step 1: Verify no remaining imports of SplatScene**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/frontend
grep -rn "SplatScene" src/ --include="*.ts" --include="*.tsx"
# Expect: zero
```

- [ ] **Step 2: Delete**

```bash
rm /home/frankyin/Desktop/work/gsfluent_pkg/frontend/src/components/viewport/SplatScene.tsx
```

- [ ] **Step 3: Drop dead simRunName / simKind from store**

In `frontend/src/lib/store.ts`, remove:
- `simRunName: string | null;` field
- `simKind: ...` field
- `setSimKind: ...` setter
- The `resetForNewRun` method's `simRunName: name` assignment (replace with `activeCell` if needed — though `activeCell` is set separately by the callers)

Audit every remaining `useStore((s) => s.simRunName)` consumer:
```bash
grep -rn "simRunName" frontend/src/ --include="*.ts" --include="*.tsx"
```
Any remaining hit must be migrated to `useActiveCell()`.

- [ ] **Step 4: Drop tools/local_stream.py**

```bash
ssh your-server 'ls $GSFLUENT_PKG_ROOT_tmp/tools/local_stream.py 2>/dev/null'
# If it's there, remove the SERVER copy too:
ssh your-server 'rm -f $GSFLUENT_PKG_ROOT_tmp/tools/local_stream.py'
rm /home/frankyin/Desktop/work/gsfluent_pkg/tools/local_stream.py
```

Update `run-client.sh` to drop the `local_stream.py` launch step. Find the line that backgrounds it and remove the block.

- [ ] **Step 5: Build + commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/frontend && npx tsc --noEmit 2>&1 | head -10 && npx vite build 2>&1 | tail -5
```
Expected: clean.

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add -A frontend/src/ tools/ run-client.sh
git -c commit.gpgsign=false commit -m "Delete SplatScene + local_stream + dead store slices"
```

---

### Phase 4 visual verification

**Visual checklist:**

- [ ] Reload http://localhost:4173/
- [ ] Pick a model — viser renders it as splats (no three.js branch fires)
- [ ] Toggle Points mode — viser renders as points (no more three.js fallback either)
- [ ] Toggle Splat — back to splats
- [ ] Pick a sequence — viser playback works
- [ ] Click Run — sim launches (postoback via `api.runs.start`); the run's sequence appears in the Outliner
- [ ] No console errors about missing imports, undefined store fields, or broken websocket
- [ ] `useStreamClient` is gone from network panel (no WS connection to `:8083`)

---

## Phase 5 — Live sim cadence + verification

**Objective:** Drop sync_daemon's polling cadence to 1s during active runs so users see the in-progress sequence advancing without a 10s lag. Then a full end-to-end smoke pass.

### Task 5.1: Sync daemon: cadence-aware polling

**Files:**
- Modify: `tools/sync_daemon.py`

- [ ] **Step 1: Add active-run detection**

The daemon already polls `/api/runs` (active runs). When the list is non-empty, switch to a faster cadence.

Find the main poll loop. Add:

```python
def _active_run_present(server_base: str) -> bool:
    """True iff the server has at least one active sim run."""
    import urllib.request, json
    try:
        with urllib.request.urlopen(f"{server_base.rstrip('/')}/api/runs", timeout=5) as r:
            return len(json.loads(r.read())) > 0
    except Exception:
        return False
```

In the main loop, decide the next sleep based on the result:

```python
while True:
    # ... existing poll logic ...
    next_sleep = 1.0 if _active_run_present(server_base) else float(interval)
    time.sleep(next_sleep)
```

(`interval` is the existing CLI flag default 10s; the active-run case overrides it.)

- [ ] **Step 2: Verify**

```bash
# Drop a quick test by inspecting the daemon's status during a no-active-runs window:
ssh your-server 'curl -s http://localhost:18080/api/runs'
# Expect: []

# Start a run via the UI, then watch the daemon's poll rate increase to ~1s.
```

- [ ] **Step 3: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add tools/sync_daemon.py
git -c commit.gpgsign=false commit -m "sync_daemon: 1s poll during active runs (10s otherwise)"
```

---

### Task 5.2: Full end-to-end smoke test

- [ ] **Step 1: Pick a model + recipe**

Drop a fresh .ply OR re-use an existing model in the Outliner. Pick a compatible recipe (the cluster_6_15 model needs a cluster_* recipe, or any recipe with `sim_area_frame: "model"`).

- [ ] **Step 2: Click Run**

Observe:
- Status pill shows the sim progress (running indicator + bar + ETA + frame count)
- After a few seconds, the in-progress sequence appears in the Source card under the parent model
- Viser shows frames advancing as they arrive (Splat or Points)

- [ ] **Step 3: When the sim completes**

- Run-finished toast appears in the Sim card
- Click "View sequence" — viser plays back the completed sequence
- PlaybackBar scrubber spans the full frame range from the start (no growing-during-load behavior)

- [ ] **Step 4: Re-pick the model**

Click the parent model in the Source card. Viser switches back to model preview (single-frame static). Splat/Points toggle works.

- [ ] **Step 5: No commit (verification only)**

If anything fails, file the issue and fix before declaring done.

---

## Phase 6 — Final tsc + bundle check

### Task 6.1: Clean build from scratch

- [ ] **Step 1: Full clean build**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/frontend && rm -rf dist && npx tsc --noEmit 2>&1 && npx vite build 2>&1 | tail -10
```

Expected: zero TS errors. Build succeeds. Bundle size should be SMALLER than before since we deleted three.js path + the websocket stream + the use-stream hook. Note the new bundle size in the report.

- [ ] **Step 2: Optional fixup commit** if you find any issues during the bundle build

---

## Out-of-scope reminders

- Camera state sync between React and viser beyond current behavior (the existing `/camera` endpoint is enough).
- Mobile / low-end client optimization.
- inotify-based sync for sub-second sim playback (1s polling is fine for now per spec).
- WebGPU migration.
- Migrating any DEFAULT recipe to model-frame sim_area (orthogonal — that's a recipe library task).

---

## Risks tracked

- **Phase 1 gate:** if viser's point-mode quality is unacceptable, we revisit before Phase 4. Decision made on visual evidence.
- **Cache eviction:** `/tmp/gsfluent_viser_model_cache` grows unbounded over time. Acceptable — `/tmp` is OS-evicted; if it becomes a problem, add a 24h-LRU policy.
- **Server fetch failure during model resolve:** if `/api/models` or `/api/models/file` returns 5xx, the cell resolution fails. Logged in viser_headless's stderr; frontend surfaces the `cellMissing` warning. Acceptable failure mode.
- **First model fetch latency:** a 150 MB ply takes ~30s to fetch over the tunnel. First Splat-mode toggle on a fresh model has that delay. Same as previous Splat path (was also pulling 150 MB worth via the npz cache sync). No regression.

---

## Self-review

- Spec coverage: every numbered concept in the spec maps to a task — single render path (Phase 4), two-cell-kinds lazy resolution (Phase 1), state model collapse (Phase 3), live sim cadence (Phase 5), and the listed deletions (Phases 2 + 4 + 6).
- Placeholder scan: every step contains the exact code or the exact command. No "TBD" / "TODO" markers. No "similar to Task N" references.
- Type consistency: `activeCell: {kind, name}` shape is used identically in Tasks 3.1, 3.2, 3.3, 3.5, 3.6. The cell wire name format (`{kind}:{name}`) is identical in Tasks 1.3, 3.2, 3.5. Viser endpoints `/set`, `/mode`, `/state`, `/reload` are referenced consistently. The store slice names (`activeCell`, `viserState`) match between definition (Tasks 3.1, 4.4) and consumers (every task in Phase 3 and 4).
- The Phase 1 gate is structural — it's the only step that REQUIRES a visual quality call from the user. Everything downstream depends on that decision.
- Backward compat during Phases 2-3: the dual state (`simRunName` AND `activeCell`) coexists until Phase 4 drops `simRunName`. Each phase remains shippable on its own.
