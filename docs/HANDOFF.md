# gsfluent - Project Handoff & Situation Report

**Last updated:** 2026-06-08  
**Repo:** `gsfluent_pkg`  
**Current branch:** `main`  
**Latest synced base before this refactor:** `687f747` (`origin/main`)  
**Audience:** teammates running the local frontend against the shared backend.

This document is the operational source of truth for the current repo, shared
server, deployment path, and known risk areas. It replaces the older
`playback-raf-simplify` notes, which are stale now that the branch has been
merged to `main`.

---

## 1. What gsfluent is

gsfluent is a physics-driven Gaussian Splatting workbench. Users upload a 3DGS
scan, choose a destruction scenario plus material, and the backend runs an
MLS-MPM solver to produce a simulated point-motion sequence. The fuser maps that
motion back to the original splats and the codec packs the result as streamable
`.gsq` v2 for browser playback.

Important physics constraint: MLS-MPM is a continuum solver. It deforms, slumps,
shears, sprays, and flows; it does not create true detached fracture chunks.
Dramatic destruction comes from lateral destabilization: earthquake base shake,
side impact, top drag, outward burst, or base-cut demolition. Pure vertical
pressing is intentionally not a curated path because it tends to pressure-spike
the grid.

---

## 2. Architecture

```text
Frontend (React/Vite SPA)
  - local dev server
  - proxies /api to shared backend
  - raw three.js + Spark playback
  - downloads .gsq and plays in browser

Backend (FastAPI, python -m gsfluent serve)
  - compose/material/scenario APIs
  - model upload + explicit reorientation API
  - async run manager
  - MPM sim subprocess
  - fuser subprocess
  - .gsq packing and sequence library
```

Main ownership boundaries:

- API routes live in `server/gsfluent/api/`.
- Run lifecycle lives in `server/gsfluent/core/run_manager.py`.
- MPM orchestration lives in `server/gsfluent/core/sim_engines/mpm.py`.
- MPM support helpers now live beside it:
  - `mpm_errors.py` for stderr classification.
  - `mpm_gpu.py` for GPU probing and per-run CUDA pinning.
  - `mpm_stability.py` for clean/partial/failed frame verdicts.
- Recipe authoring lives in `server/gsfluent/authoring/`.
- Frontend API client/state lives in `frontend/src/lib/`.
- Frontend simulation workflow UI lives in `frontend/src/components/sim/` and
  `frontend/src/components/runs/`.

---

## 3. Server Topology

| Thing | Value |
|---|---|
| SSH | `ssh sxyin-host` |
| Host/user | `jy-r308-f01-7`, user `yinshaoxuan` |
| Server code dir | `/data/yinshaoxuan/gsfluent_pkg` |
| Conda env | `gsfluent-api` |
| Internal backend | `http://127.0.0.1:7869` |
| Public backend | `http://36.170.54.6:24701` |
| Health | `GET /api/health` |
| Compose library | `GET /api/compose/library` |
| Runtime work dir | controlled by `.env` `GSFLUENT_WORK_DIR`; live service has used `/tmp/gsfluent_pkg_work` |

Frontend local dev should point at the public backend:

```bash
cd frontend
GSFLUENT_BACKEND_URL=http://36.170.54.6:24701 npm start
```

The Vite default still targets localhost when `GSFLUENT_BACKEND_URL` is absent,
so set the env var explicitly when using the shared backend.

---

## 4. Supervisor And Deploy

The live backend is managed by the gitignored server script:

```bash
work/supervise.sh up
work/supervise.sh status
work/supervise.sh stop
```

It is not systemd. The stale `deploy/*.service` files are reference material,
not the live supervisor.

SSH gotcha: do not run `ssh host 'work/supervise.sh up'` without redirecting
output. The watcher can inherit the SSH stdout channel and the SSH timeout can
kill the restart. Use:

```bash
ssh sxyin-host 'cd /data/yinshaoxuan/gsfluent_pkg && bash work/supervise.sh up >/tmp/gsfluent_supervise_up.log 2>&1'
```

Then poll:

```bash
curl -sS http://127.0.0.1:7869/api/health
curl -sS http://127.0.0.1:7869/api/compose/library
```

Current practical deploy method is direct sync of changed server files from the
local checkout to `/data/yinshaoxuan/gsfluent_pkg`, with a tarball backup first.
GitHub fetches from the server have been unreliable, so do not assume the server
`.git` pointer proves what code is running. Verify file hashes or behavior.

Most recent known backend sync before this refactor:

- Local `main` pushed to `origin/main` at `687f747`.
- Server backup created at
  `/data/yinshaoxuan/gsfluent_pkg_server_predeploy_20260608_162042.tgz`.
- Server health and `/api/compose/library` verified after restart.

This current refactor is local until committed, pushed, and deployed again.

---

## 5. GPU And Runtime Notes

The box is shared and has multiple GPUs. MPM runs now auto-select a GPU at sim
launch via `mpm_gpu.py`:

- Default: auto-GPU selection is ON.
- Disable with `GSFLUENT_AUTO_GPU=0`, `false`, `no`, or `off`.
- Minimum free memory defaults to 20 GiB.
- Override with `GSFLUENT_GPU_MIN_FREE_MIB`.
- On probe failure, the sim falls back to inherited `CUDA_VISIBLE_DEVICES`.

Heavy sims should still be treated as exclusive workloads. Concurrent
earthquake-like runs can contend, slow down by several times, or provoke CUDA
errors. Auto-pick reduces the stale-pin problem; it does not make the shared box
contention-proof.

Disk notes:

- `/data` is chronically near full and mostly occupied by other users.
- Runtime state should stay under `GSFLUENT_WORK_DIR`, not in the repo.
- Health reports free space for `GSFLUENT_WORK_DIR`.

Do not touch:

- `/data/yinshaoxuan/gsfluent_v2` or its runtime stack.
- Other users' files on the shared host.

---

## 6. Composer And Recipes

Recipes are composed from:

```text
MATERIAL x SCENARIO x BUILDING -> flat sim recipe JSON
```

Endpoints:

- `GET /api/compose/library`
- `POST /api/compose`
- `POST /api/runs`

Curated scenarios:

- `earthquake`
- `wrecking`
- `topple`
- `burst`
- `demolish`

All five were designed around dramatic collapse with the recommended
`watermelon` material. Non-recommended material/scenario combos may get
stabilizing overrides, disabled burst internals, or `_stability_notes`.

`grid_v_damping_scale` semantics remain a footgun: values below `1.0` damp
velocity; `1.1` effectively means damping is off. Some curated scenarios use
that intentionally for energy buildup.

`sim_area_frame` matters:

- `"model"` means bounds are model-local and translated to the model bbox center.
- `"world"` means bounds are already world coordinates.

Wrong framing can filter out every splat before simulation.

---

## 7. Model Orientation

Orientation is now an explicit user decision, not an automatic shape heuristic.

Backend:

- `POST /api/models/{name}/reorient`
- Transform names:
  - `rotate_x_pos_90`, `rotate_x_neg_90`, `rotate_x_180`
  - `rotate_y_pos_90`, `rotate_y_neg_90`, `rotate_y_180`
  - `rotate_z_pos_90`, `rotate_z_neg_90`, `rotate_z_180`
  - compatibility aliases `y_up_to_z_up`, `flip_180`
- Positions, Gaussian quaternions, and normals are rotated in place.
- `_meta.json` is rewritten with a new bbox, splat count, and sha256.

Frontend:

- `ReorientControls` exposes Blender-style axis rotation buttons in the viewport.
- `SplatScene` cache-busts via model sha so rotated models reload.

Pre-run orientation validation is intentionally a no-op now. Building scans and
object scans vary too much for a reliable bbox-axis "up" guess.

---

## 8. NaN And CUDA-700 Status

Historical issue: earthquake + watermelon on some uploaded building scans could
reach late-frame NaN/CUDA-700 and abandon the run.

Current mitigations in code:

- Recipe stability rules and scenario-specific safe caps.
- Boundary/position sanitization patches in the MPM path.
- Fuser drops non-finite frames.
- `check_sim_stability` classifies runs as:
  - `clean`: requested frames survived.
  - `partial`: late divergence, but enough usable frames survived.
  - `failed`: too few usable frames, reported as `sim.unstable_recipe`.
- `.gsq` debris death channel can cull particles that fly away.
- Run history overlays authoritative failed/cancelled outcomes.

Operational rule: do not claim a material/scenario/model combo is stable without
reading the actual run status, usable frame count, and logs. GPU contention can
masquerade as recipe instability.

---

## 9. Current Refactor Notes

Work in progress in this checkout:

- `POST /api/runs` submission validation has been split into
  `server/gsfluent/api/run_submission.py`.
- A wall-time cap error bug was fixed: invalid `wall_time_sec` no longer gets
  re-parsed inside error handling and escapes as a raw `ValueError`.
- MPM support code was split from `mpm.py` into:
  - `mpm_errors.py`
  - `mpm_gpu.py`
  - `mpm_stability.py`
- `mpm.py` still re-exports the helper names that tests/importers expect.

Verification already run during this refactor:

- `python -m pytest -q tests/api/test_runs_validation.py tests/api/test_runs_helpers.py`
- `python -m pytest -q tests/sim_engines/test_mpm.py tests/integration/test_sim_error_classification.py`
- `python -m pytest -q` from `server/` -> 603 passed, 1 skipped.
- `npm test -- --run` from `frontend/` -> 41 passed.
- `npm run build` from `frontend/` -> passed with the existing large chunk warning.

Static `ruff` and `mypy` were attempted in the active Python env but are not
installed there.

---

## 10. Useful Commands

Backend targeted tests:

```bash
cd server
python -m pytest -q tests/api/test_runs_validation.py tests/api/test_runs_helpers.py
python -m pytest -q tests/sim_engines/test_mpm.py tests/integration/test_sim_error_classification.py
python -m pytest -q tests/core tests/runs tests/api tests/observability tests/codecs tests/sim_engines
```

Frontend:

```bash
cd frontend
npm test -- --run
npm run build
```

Static tools are declared in server dev metadata, but the active local env may
not have `ruff` or `mypy` installed. If they are unavailable, report that
explicitly rather than treating the static check as passed.

---

## 11. Open Follow-Ups

- Finish and verify the current refactor before pushing/deploying.
- Decide whether `docs/HANDOFF.md` should remain gitignored. It is currently
  ignored, so updates are local unless `.gitignore` changes.
- Consider splitting `api/runs.py` further by history/log/frame endpoints after
  submit validation settles.
- Consider splitting frontend workflow UI after backend contracts stabilize:
  `SimulationCard.tsx`, `RunButton.tsx`, `store.ts`, and `api.ts`.
- Keep full GPU material x scenario verification as a real run-matrix task, not
  a code-unit-test substitute.

---

## 12. Hard Rules

- Verify recipe behavior with real run output before claiming stability.
- Do not deploy or push without an explicit request.
- Back up server code before direct sync.
- Do not touch `gsfluent_v2` or other users' server files.
- Treat `outputs/` and presentation/node_modules artifacts as generated local
  output, not source.
