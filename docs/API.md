# gsfluent API

Canonical HTTP reference for the backend (`gsfluent serve`, port 7869,
exposed publicly via NAT). The frontend talks to everything here through the
vite `/api/*` proxy — see [README](../README.en.md). 中文索引:
[API.zh.md](API.zh.md).

All bodies are JSON. Errors use a structured envelope:

```json
{ "detail": { "error": {
  "kind": "validation.recipe_data",
  "message": "human-readable reason",
  "details": { "...": "..." },
  "trace_id": "abc123"
} } }
```

so a 422 always tells you *why* (surface `error.message` in the UI).

---

## Authoring a sim: the composer (start here)

A recipe is **composed** from three orthogonal inputs —
**MATERIAL × SCENARIO × BUILDING** — instead of being hand-edited. The
composer turns the three picks into the flat sim recipe that
`POST /api/runs` consumes. Source of truth lives in
`server/gsfluent/authoring/` (`materials.py`, `scenarios.py`,
`buildings.py`, `compose.py`); the endpoints below just expose it.

### `GET /api/compose/library`

Lists the three libraries for the picker dropdowns. Read dynamically from
the authoring modules — new scenarios/materials appear here with no API
change.

Response:

```json
{
  "scenarios": [
    { "name": "earthquake", "base": "driven", "frame_num": 150,
      "gravity": -15.0, "recommended_material": "watermelon",
      "damping": 1.1, "num_events": 2, "desc": "Seismic base shake → collapse" }
  ],
  "materials": [
    { "name": "watermelon", "material": "watermelon", "E": 2000.0, "nu": 0.38,
      "density": 1.0, "yield_stress": 0.0, "friction_angle": 45.0,
      "desc": "Soft hyperelastic — the 'building actually breaks' material" }
  ],
  "buildings": [
    { "name": "cluster_6_15", "model_path": "…", "bbox": [...],
      "sim_area": [...], "desc": "Photoreal high-rise tower scan" }
  ]
}
```

**The five curated scenarios** (all verified on rendered video to produce a
dramatic collapse on the recommended soft material, `watermelon`):

| Scenario    | What happens                                              |
|-------------|----------------------------------------------------------|
| `earthquake`| Base-shake plate → the tower collapses into rubble       |
| `wrecking`  | Mid-height side impact (pinned base) → shears apart      |
| `topple`    | Top third dragged along the thin axis → falls like a domino |
| `burst`     | Four core slabs shove outward → the structure explodes   |
| `demolish`  | Two opposing base-cut impacts → it crashes down + breaks |

Each scenario carries a `recommended_material`. The violent ones eject
*stiff* materials (jelly/plasticine) with a grid-escape crash — that's
physics, not a bug — so they recommend the soft `watermelon`. The UI snaps
material to the recommendation on scenario change and warns on a mismatch.

### `POST /api/compose`

Body: `{ "material": "watermelon", "scenario": "demolish", "building": "cluster_6_15" }`

Response: `{ "material", "scenario", "building", "recipe_data": { …flat recipe… } }`

`recipe_data` is the exact object you forward to `POST /api/runs`. Pure +
deterministic — no sim, no GPU. Unknown picks or over-ceiling values
(e.g. an impact speed past the grid-escape limit) come back as a **422**
whose `error.message` says why — surface it, don't swallow it.

The composed recipe carries a `_composed_from` provenance block
(`{material, scenario, building, base_regime}`). It is **in-memory only** —
it is NOT a saved server recipe, so do not `GET /api/recipes/<that name>`.

---

## Models

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/models` | List library models (`{name, path, n_splats, …}`). |
| `GET` | `/api/models/{name}` | One model's metadata. |
| `POST`| `/api/models/upload` | Multipart upload of a `.ply` (+ optional `cameras.json`); client gzips the field (`ply_encoding=gzip`). `convert_y_up=true` to rotate Y-up scans. |
| `POST`| `/api/models/check_hash` | `{sha256, filename?}` → `{exists, name?, path?}` (dedupe before upload). |
| `POST`| `/api/models/register` | `{path, convert_y_up?}` — register an on-disk model without uploading. |
| `DELETE`| `/api/models/{name}` | Remove a library model. |

---

## Recipes (saved presets)

Saved recipes are the **flat material demos** (jelly/metal/sand/foam/
plasticine) + the `demolition` fallback + any `★ user` presets. The five
destruction scenarios are **composed** (above), not saved here.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/recipes` | List saved recipes (`{name, source}`). |
| `GET` | `/api/recipes/{name}` | One recipe's full data. |
| `PUT` | `/api/recipes/{name}` | Save/update `{data, based_on?}` → `work/_user_recipes/<name>.json`. Runs the stability linter (warn-only, never blocks). |
| `POST`| `/api/recipes/{name}` | Create. |
| `DELETE`| `/api/recipes/{name}` | Delete a user preset. |

---

## Runs (submit + track a sim)

### `POST /api/runs`

Strict-validated. Body:

```json
{
  "run_name": "cluster_6_15_earthquake_watermelon_2026-05-30T1715",
  "model_path": "/data/.../models/cluster_6_15",
  "recipe_data": { "...": "the composed (or saved) recipe..." },
  "recipe_source": "earthquake·watermelon",
  "particles": 200000
}
```

- `run_name` MUST match `^[A-Za-z0-9_.\-]+$`. (Composed recipe labels use a
  `·` separator — sanitize the run name before sending; `recipe_source` may
  keep the human label.)
- The runner translates `sim_area` to world coords when
  `recipe_data.sim_area_frame == "model"` (adds the model's bbox center), then
  preflights that it overlaps the model. A mismatch returns a **422**
  ("sim_area does not overlap the model bbox") rather than crashing the sim.
- Caps (env-configurable) are enforced before any subprocess spawns:
  `GSFLUENT_MAX_PARTICLE_COUNT` (500000), `GSFLUENT_MAX_WALL_TIME_SEC`
  (3600), `GSFLUENT_MAX_RECIPE_BYTES` (16384). Over-cap → 422.

Response: `{ "run_id": "ade8fc0ea429", "run_name": "…", "trace_id": "…" }`.
The run then produces frames under `work/library/sequences/<run_name>/`.

`dry_run: true` runs the same validation (model_path, sim_area overlap)
without spawning the sim — handy for a compatibility check.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/runs` | Active runs only (`{id, name, state}`). |
| `DELETE`| `/api/runs/{run_id}` | Cancel: SIGTERM the whole process group, SIGKILL after 30 s. |
| `GET` | `/api/runs/history` | Past runs (`{run_name, status, started_at, finished_at?, particles?, recipe_source?}`); FAILED/CANCELLED/INTERRUPTED outcomes are authoritative (overlaid from the run-state store). |
| `DELETE`| `/api/runs/history/{run_name}` | Forget a history entry. |
| `GET` | `/api/runs/{run_name}/log?offset=N` | Tail the run log (`{content, offset, size}`) — poll with the returned `offset` for streaming. |
| `GET` | `/api/runs/{run_name}/frame/{frame_idx}.ply` | A single fused frame as `.ply`. |

A diverged sim (NaN/Inf positions) fails loudly as
`kind: sim.unstable_recipe` instead of silently producing a truncated
sequence.

---

## Sequences (sim output + playback cache)

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/sequences` | List sequences (`{name, n_frames, fps_hint, …}`). |
| `GET` | `/api/sequences/{name}` | One sequence's metadata. |
| `POST`| `/api/sequences/import` | `{folder_path, name?, convert_y_up?}` — import a frames dir. |
| `DELETE`| `/api/sequences/{name}` | Delete a sequence + its cache. |
| `GET`/`HEAD` | `/api/sequences/{name}/cache/splats.gsq` | The packed `.gsq` (the download-then-play artifact). `Cache-Control: immutable` + `ETag`; `If-None-Match` → 304; supports `Range` resume. |
| `POST`| `/api/sequences/{name}/cache/build` | Kick off on-demand `.gsq` packing (idempotent). |
| `GET` | `/api/sequences/{name}/cache/build-status` | `{name, state: idle\|building\|done\|error, error?}` — poll to `done` then download. |

---

## Health

`GET /api/health` → `{ status, gpu_reachable, sim_home_exists,
disk_free_pct, last_successful_run_at, … }`. `status` is `down` when
`disk_free_pct < 5`.

---

## Typical flow

1. `GET /api/compose/library` → populate the dropdowns.
2. User picks scenario + material (+ building) → `POST /api/compose` → `recipe_data`.
3. `POST /api/runs` with `recipe_data` + the chosen `model_path`.
4. Poll `GET /api/runs/{name}/log` for progress; `GET /api/runs/history` for the outcome.
5. On `done`: `POST …/cache/build` → poll `…/cache/build-status` → `GET …/cache/splats.gsq` → render in-browser.
