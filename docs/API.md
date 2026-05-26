# gsfluent v1 Backend — HTTP API Reference

This document is the wire-level contract for the gsfluent backend. It targets
a teammate who needs to write their own client against this surface (curl,
Python, browser, sync daemon). Every endpoint section below was derived by
reading the router source under `server/gsfluent/api/` and the Pydantic
models in `api/schemas.py` (which today only contains pass-through router
wiring; the real request models live next to each router).

If a field shape disagrees with what the server actually returns, the server
is right and this doc is wrong — please file a fix.

## Overview

### Base URL

Public deployment: `${BACKEND_URL}`. All HTTP routes documented
below are mounted under the `/api/` prefix, e.g.
`${BACKEND_URL}/api/health`. The SPA is served from `/` and is
mounted last so `/api/*` always wins on prefix conflict.

### Authentication

**None.** There is no auth header, no API key, no session cookie. Anyone
who can reach the server can call any endpoint. Treat the deployment IP as
internal-only.

### Versioning

No formal version prefix. The FastAPI app reports `"version": "0.1.0"` in
its OpenAPI schema (visible at `/docs` or `/openapi.json`), but routes are
not URL-versioned. Breaking changes happen in place; pin your client to
the commit you tested against.

### Content-Type

- Request bodies: `application/json` unless the endpoint accepts file
  uploads (model `.ply`, `cameras.json`), in which case it is
  `multipart/form-data`.
- Response bodies: `application/json` for everything except file downloads
  (frame plys, `.gsq` cache, `frames.bin`), which use `application/octet-stream`.

### Error response shape

FastAPI's default. Every non-2xx response is:

```json
{ "detail": "human-readable message" }
```

The status code carries the category (400 / 404 / 409 / 413 / 422 / 500).
422 also fires for Pydantic validation failures on request bodies — in that
case `detail` becomes the standard FastAPI list-of-errors structure (one
entry per offending field).

### CORS

Origins matching `^https?://(localhost|127\.0\.0\.1)(:\d+)?$` are allowed
by regex. Additional origins can be added at process start via the
`GSFLUENT_EXTRA_CORS_ORIGINS` env var (comma-separated). Credentials are
not allowed; methods and headers are wildcarded.

### Conventions

- Coordinate convention is **Z-up** end-to-end. Y-up sources are converted
  at import time and the `_meta.json` records `converted_from: "y-up"`.
- Run-name identifiers must match `^[A-Za-z0-9_.\-]+$`. Recipe and model
  names match `^[A-Za-z0-9_\-]+$` / `^[A-Za-z0-9_.\-]+$` respectively.
  Any path-traversal attempt returns 400 or 422.

---

## Health

### GET /api/health

Liveness probe. Always returns 200 while the process is up.

**Response**

```json
{
  "status": "ok",
  "pkg_root": "/path/to/gsfluent_pkg"
}
```

| Field | Type | Description |
| --- | --- | --- |
| `status` | string | Always `"ok"`. |
| `pkg_root` | string | Absolute path of the package root on the server filesystem. Diagnostic only. |

**curl**

```bash
curl ${BACKEND_URL}/api/health
```

---

## Recipes

A recipe is a JSON config that drives one sim run (sim_area, n_grid,
material parameters, boundary conditions, etc.). Built-in recipes ship in
the repo at `server/recipes/*.json` and are read-only. User-saved recipes
live in `work/_user_recipes/`. Names must match `^[A-Za-z0-9_\-]+$`.

### GET /api/recipes

List every recipe known to the server (built-in + user).

**Response**

```json
[
  { "name": "demolition", "source": "builtin" },
  { "name": "earthquake", "source": "builtin" },
  { "name": "my_run",     "source": "user"    }
]
```

| Field | Type | Description |
| --- | --- | --- |
| `name` | string | Recipe identifier (filename stem). |
| `source` | string | `"builtin"` or `"user"`. |

Built-ins are listed first, then user saves; both groups are sorted
alphabetically.

**curl**

```bash
curl ${BACKEND_URL}/api/recipes
```

### GET /api/recipes/{name}

Fetch a single recipe by name. If a name exists in both `builtin` and
`user`, `builtin` wins.

**Path params**

| Name | Type | Description |
| --- | --- | --- |
| `name` | string | Must match `^[A-Za-z0-9_\-]+$`. |

**Response**

```json
{
  "name": "demolition",
  "source": "builtin",
  "data": {
    "sim_area": [-30, 30, -10, 10, -2, 45],
    "n_grid": 150,
    "material": "plasticine",
    "E": 50000.0,
    "nu": 0.2,
    "...": "..."
  }
}
```

| Field | Type | Description |
| --- | --- | --- |
| `name` | string | Echo of the request name. |
| `source` | string | `"builtin"` or `"user"`. |
| `data` | object | The full recipe body. Shape is recipe-specific; see the built-in JSONs for the union of fields. |

**Status codes**

| Code | Cause |
| --- | --- |
| 404 | Recipe not found. |
| 409 | File exists on disk but couldn't be read or parsed (`RecipeReadError`). |
| 422 | Name fails the validation regex. |

**curl**

```bash
curl ${BACKEND_URL}/api/recipes/demolition
```

### PUT /api/recipes/{name}

Create or overwrite a user recipe. Built-ins are immutable — saving under
a builtin's name creates a user copy that shadows the builtin on read
(builtin still wins on `GET`).

**Path params**

| Name | Type | Description |
| --- | --- | --- |
| `name` | string | Target user-recipe name. Validated as `^[A-Za-z0-9_\-]+$`. |

**Request body** (`application/json`)

```json
{
  "data": { "sim_area": [-30, 30, -10, 10, -2, 45], "...": "..." },
  "based_on": "demolition"
}
```

| Field | Type | Description |
| --- | --- | --- |
| `data` | object | **Required.** The recipe body to save. Stored verbatim plus a `_provenance` block. |
| `based_on` | string\|null | Optional. Origin recipe name; written into `_provenance.based_on`. Defaults to `null` (recorded as `"(unknown)"`). |

**Response**

```json
{
  "name": "my_run",
  "source": "user",
  "data": {
    "sim_area": [-30, 30, -10, 10, -2, 45],
    "_provenance": {
      "based_on": "demolition",
      "saved_at": "2026-05-20T10:34:20Z"
    }
  }
}
```

| Field | Type | Description |
| --- | --- | --- |
| `name` | string | Echo. |
| `source` | string | Always `"user"`. |
| `data` | object | The persisted payload, including the injected `_provenance` block. |

**Status codes**

| Code | Cause |
| --- | --- |
| 422 | Name fails the regex, or `data` not present. |

**curl**

```bash
curl -X PUT ${BACKEND_URL}/api/recipes/my_run \
  -H 'Content-Type: application/json' \
  -d '{"data":{"sim_area":[-30,30,-10,10,-2,45]},"based_on":"demolition"}'
```

### DELETE /api/recipes/{name}

Delete a user recipe. Built-ins cannot be deleted.

**Path params**

| Name | Type | Description |
| --- | --- | --- |
| `name` | string | Recipe name. |

**Response**

```json
{ "deleted": "my_run" }
```

**Status codes**

| Code | Cause |
| --- | --- |
| 403 | Name matches a built-in. |
| 404 | User preset not found. |
| 422 | Name fails the regex. |
| 500 | Filesystem unlink failed. |

**curl**

```bash
curl -X DELETE ${BACKEND_URL}/api/recipes/my_run
```

---

## Models

A model is a 3D Gaussian Splatting scan stored in the canonical layout
`<dir>/point_cloud/iteration_<N>/point_cloud.ply`. The library lives under
`work/library/models/`; externally registered paths are tracked in
`work/library/models/_registered.json` and never copied.

### GET /api/models

List every model in the library (internal + registered).

**Response**

```json
[
  {
    "name": "cluster_6_15",
    "kind": "model",
    "source": "register",
    "source_path": "/path/to/GaussianFluent/model/cluster_6_15",
    "n_splats": 683741,
    "bbox": [[3443.6, 29036.1, -19.9], [3474.1, 29054.1, 30.5]],
    "coord_convention": "z-up",
    "imported_at": "2026-05-18T01:03:34Z",
    "converted_from": null,
    "sha256": null,
    "path": "/path/to/GaussianFluent/model/cluster_6_15"
  }
]
```

| Field | Type | Description |
| --- | --- | --- |
| `name` | string | Model identifier (folder name under `library/models/` or registered name). |
| `kind` | string | Always `"model"`. |
| `source` | string | `"upload"`, `"register"`, or `"import"`. |
| `source_path` | string\|null | Original path on disk (registered/imported), or `null` for uploads. |
| `n_splats` | int\|null | Vertex count of the highest-iteration ply. |
| `bbox` | float[2][3]\|null | `[[xmin,ymin,zmin],[xmax,ymax,zmax]]` from the ply. |
| `coord_convention` | string | Always `"z-up"` for valid entries. |
| `imported_at` | string\|null | ISO-8601 UTC timestamp. |
| `converted_from` | string\|null | `"y-up"` iff the source was Y-up. |
| `sha256` | string\|null | SHA-256 of the uploaded ply bytes (uploads only). |
| `path` | string | Absolute on-disk model directory. |

Sorted by `imported_at` descending; entries lacking `imported_at` sort
last, alphabetically.

**curl**

```bash
curl ${BACKEND_URL}/api/models
```

### POST /api/models/check_hash

Look up whether a model with this content hash already exists. The frontend
calls this before uploading so a re-drop of the same `.ply` skips transport
entirely.

**Request body** (`application/json`)

```json
{ "sha256": "abc123...64hex", "filename": "scene.ply" }
```

| Field | Type | Description |
| --- | --- | --- |
| `sha256` | string | **Required.** 64-char lowercase hex SHA-256 of the raw .ply bytes. |
| `filename` | string\|null | Optional; logged but not persisted. |

**Response (hit)**

```json
{
  "exists": true,
  "name": "scene_a1b2c3d4",
  "path": "/data/.../library/models/scene_a1b2c3d4",
  "n_splats": 683741
}
```

**Response (miss)**

```json
{ "exists": false }
```

**Status codes**

| Code | Cause |
| --- | --- |
| 422 | `sha256` missing or not 64 chars. |

**curl**

```bash
curl -X POST ${BACKEND_URL}/api/models/check_hash \
  -H 'Content-Type: application/json' \
  -d '{"sha256":"'$(sha256sum scene.ply | cut -c1-64)'"}'
```

### POST /api/models/upload

Upload a `.ply` file (optionally with `cameras.json`). The server wraps it
into the 3DGS layout, computes bounding box and splat count, and writes
`_meta.json`. Returns the existing model meta if the content hash already
exists.

**Request body** (`multipart/form-data`)

| Field | Type | Description |
| --- | --- | --- |
| `ply` | file | **Required.** `.ply` file (extension is validated). 8 GiB cap. |
| `cameras_json` | file | Optional `.json` file holding the original COLMAP cameras list. |
| `convert_y_up` | bool (form) | If `true`, rewrite positions, quaternions, and normals from Y-up to Z-up at import. Default `false`. |
| `ply_encoding` | string (form) | `"identity"` (default) or `"gzip"`. When `"gzip"`, the body is gunzipped before validation (with a decompressed-size cap of 8 GiB to prevent gzip-bombs). |

**Response (new upload)**

```json
{
  "name": "scene_a1b2c3d4",
  "path": "/data/.../library/models/scene_a1b2c3d4"
}
```

**Response (dedup hit on the decompressed bytes)**

Returns the same shape as a single entry from `GET /api/models` (the
existing model's full meta dict).

**Status codes**

| Code | Cause |
| --- | --- |
| 413 | Body exceeds the 8 GiB cap (raw OR gunzipped). |
| 422 | `ply` field is missing `.ply` extension, file too small (<64 B), missing `ply\n` magic header, `cameras.json` is not a JSON list, `ply_encoding` is unrecognized, gunzip failed, or convert-Y-up parsing failed. |

**curl**

```bash
curl -X POST ${BACKEND_URL}/api/models/upload \
  -F 'ply=@scene.ply' \
  -F 'cameras_json=@cameras.json' \
  -F 'convert_y_up=false' \
  -F 'ply_encoding=identity'
```

### POST /api/models/register

Register an existing on-disk 3DGS directory without copying. The path must
contain `point_cloud/iteration_<N>/point_cloud.ply`. With `convert_y_up=true`
the structure IS copied into the library and rewritten Z-up — the response
`mode` indicates which path executed.

**Request body** (`application/json`)

```json
{ "path": "/data/scans/my_scene", "convert_y_up": false }
```

| Field | Type | Description |
| --- | --- | --- |
| `path` | string | **Required.** Absolute path on the server filesystem. |
| `convert_y_up` | bool | Optional. Default `false`. |

**Response**

```json
{
  "name": "my_scene",
  "path": "/data/scans/my_scene",
  "mode": "registered"
}
```

| Field | Type | Description |
| --- | --- | --- |
| `name` | string | Model name (the directory's basename). |
| `path` | string | Final on-disk path (= source path for `"registered"`, or `library/models/<name>` for `"copied-and-converted"`). |
| `mode` | string | `"registered"` or `"copied-and-converted"`. |

**Status codes**

| Code | Cause |
| --- | --- |
| 409 | `convert_y_up=true` and a model with this name already exists in the library. |
| 422 | Path does not exist, is not a directory, lacks the `point_cloud/iteration_*/point_cloud.ply` layout, or has an unsafe directory name (must match `^[A-Za-z0-9_.\-]+$`). |

**curl**

```bash
curl -X POST ${BACKEND_URL}/api/models/register \
  -H 'Content-Type: application/json' \
  -d '{"path":"/data/scans/my_scene","convert_y_up":false}'
```

### GET /api/models/file?path=&lt;abs-path&gt;

### GET /api/models/file/{filename}?path=&lt;abs-path&gt;

Stream the highest-iteration `point_cloud.ply` for a registered model.
The `{filename}` path segment is cosmetic — it lets the URL end in `.ply`
so browser-side splat libraries (e.g. `@mkkellogg/gaussian-splats-3d`)
dispatch to the right parser; the file actually served is always
`<path>/point_cloud/iteration_<max N>/point_cloud.ply`.

**Query params**

| Name | Type | Description |
| --- | --- | --- |
| `path` | string | **Required.** Absolute path. Must exactly match the `path` of an entry in `GET /api/models` (allowlist check). |

**Path params**

| Name | Type | Description |
| --- | --- | --- |
| `filename` | string | Optional, ignored on the server side. |

**Response**

`application/octet-stream`; the raw .ply bytes. FastAPI's `FileResponse`
supports HTTP `Range` for resumable downloads.

**Status codes**

| Code | Cause |
| --- | --- |
| 404 | Path is not in the registered-models allowlist, is not a directory, or has no `point_cloud/iteration_*/point_cloud.ply`. |

**curl**

```bash
curl -OJ "${BACKEND_URL}/api/models/file/scene.ply?path=/data/scans/my_scene"
```

### DELETE /api/models/{name}

Remove a model from the library. For internally-stored models the directory
is `rmtree`'d. For externally-registered models only the registry entry is
dropped; user files outside the library root are never touched. Sequences
referencing this model by `model_ref` are **not** cascaded — they become
orphans.

**Path params**

| Name | Type | Description |
| --- | --- | --- |
| `name` | string | Model name. |

**Response**

```json
{ "deleted": "scene_a1b2c3d4" }
```

**Status codes**

| Code | Cause |
| --- | --- |
| 404 | Model not found in the library. |
| 500 | Deletion failed (e.g. permission error during rmtree). |

**curl**

```bash
curl -X DELETE ${BACKEND_URL}/api/models/scene_a1b2c3d4
```

---

## Runs

A run is a single sim job. Active runs are tracked in-process by
`core.runner`; archived runs live in the on-disk library at
`work/library/sequences/<run_name>/`. The runs router exposes both surfaces.

### GET /api/runs

List currently-active runs (state `"running"` only). Past runs go through
`GET /api/runs/history`.

**Response**

```json
[
  { "id": "1a2b3c", "name": "cluster_6_15_eq_v1", "state": "running" }
]
```

| Field | Type | Description |
| --- | --- | --- |
| `id` | string | In-process run identifier (opaque). Used as the path param to `DELETE /api/runs/{run_id}`. |
| `name` | string | Run name as supplied at start. Used as the path param everywhere else (`/log`, `/frame`, `/history/...`). |
| `state` | string | Always `"running"` for entries in this list. |

**curl**

```bash
curl ${BACKEND_URL}/api/runs
```

### POST /api/runs

Start a new sim run, or dry-run-validate one without spawning the wrapper.

**Request body** (`application/json`)

```json
{
  "run_name": "cluster_6_15_eq_v1",
  "model_path": "/data/.../library/models/cluster_6_15",
  "recipe_data": { "sim_area": [-30, 30, -10, 10, -2, 45], "...": "..." },
  "recipe_source": "earthquake",
  "particles": 200000,
  "dry_run": false
}
```

| Field | Type | Description |
| --- | --- | --- |
| `run_name` | string | **Required.** Output sequence name. Must be unique. |
| `model_path` | string | **Required.** Absolute path to a model directory (registered or library-local). |
| `recipe_data` | object | **Required.** The full recipe body (same shape as `GET /api/recipes/{name}` `data`). |
| `recipe_source` | string | **Required.** Origin recipe name; recorded in the run manifest. |
| `particles` | int | Optional. Default `200000`. |
| `dry_run` | bool | Optional. If `true`, run pure validators (model_path exists, sim_area overlaps the model bbox, ...) but do not spawn the sim. Default `false`. |

**Response (live run)**

```json
{
  "run_id": "1a2b3c",
  "run_name": "cluster_6_15_eq_v1"
}
```

**Response (dry run)**

```json
{ "dry_run": true, "valid": true, "run_name": "cluster_6_15_eq_v1" }
```

**Status codes**

| Code | Cause |
| --- | --- |
| 422 | `model_path` missing / not a directory; sim_area does not intersect the model bbox; any other `ValueError` raised by `runner.start_run`. |

**curl**

```bash
curl -X POST ${BACKEND_URL}/api/runs \
  -H 'Content-Type: application/json' \
  -d @start.json
```

### DELETE /api/runs/{run_id}

Cancel an active run. Uses the in-process **`run_id`** (not the run name).

**Path params**

| Name | Type | Description |
| --- | --- | --- |
| `run_id` | string | Run id from `GET /api/runs`. |

**Response**

```json
{ "status": "cancelled" }
```

**Status codes**

| Code | Cause |
| --- | --- |
| 404 | `run_id` is not currently active. |

**curl**

```bash
curl -X DELETE ${BACKEND_URL}/api/runs/1a2b3c
```

### GET /api/runs/history

List every past run in the library, newest first. Walks
`library/sequences/` and falls back to the legacy `runner.FUSED_DIR` for
pre-migration data.

**Response**

```json
[
  {
    "run_name": "cluster_6_15_eq_v3",
    "status": "done",
    "started_at": 1779266060.81,
    "finished_at": 1779266270.89,
    "particles": 200000,
    "recipe_source": "earthquake",
    "model_ref": "cluster_6_15",
    "frame_count": 151,
    "sequence_source": "sim"
  }
]
```

| Field | Type | Description |
| --- | --- | --- |
| `run_name` | string | Sequence name. |
| `status` | string | `"done"`, `"error"`, `"cancelled"`, `"running"`, or `"unknown"`. |
| `started_at` | float | Unix epoch seconds. From `manifest.json:started_at`, or `_meta.json:created_at` parsed, or dir mtime. |
| `finished_at` | float | Optional. Present only if `manifest.json` recorded it. |
| `particles` | int | Optional. From `manifest.json`. |
| `recipe_source` | string | Optional. From `manifest.json`. |
| `model_ref` | string | Optional. Parent model name; from `_meta.json` or `manifest.json:model_dir` basename. |
| `frame_count` | int | Optional. From `_meta.json` or live frame count. |
| `sequence_source` | string | Optional. From `_meta.json:source`. |
| `_synthetic` | bool | Only present on legacy-dir fallback entries that lack a manifest. |

**curl**

```bash
curl ${BACKEND_URL}/api/runs/history
```

### DELETE /api/runs/history/{run_name}

Delete a single past run from the library by name. Refuses to delete a
run that is still active.

**Path params**

| Name | Type | Description |
| --- | --- | --- |
| `run_name` | string | Run / sequence name. |

**Response**

```json
{ "deleted": "cluster_6_15_eq_v3" }
```

**Status codes**

| Code | Cause |
| --- | --- |
| 400 | Path-traversal attempt, or legacy entry is not a directory. |
| 404 | Run not found. |
| 409 | Run is still active; cancel it first. |
| 500 | `rmtree` or library delete failed. |

**curl**

```bash
curl -X DELETE ${BACKEND_URL}/api/runs/history/cluster_6_15_eq_v3
```

### GET /api/runs/{run_name}/log

Incremental tail of a run's `run.log`. The frontend polls this every
~500 ms while a sim is active. Works for active **and** archived runs
(searches `runner.FUSED_DIR/<name>/run.log` first, then
`library.SEQUENCES_DIR/<name>/run.log`).

**Path params**

| Name | Type | Description |
| --- | --- | --- |
| `run_name` | string | Run name. Validated against `^[A-Za-z0-9_.\-]+$`. |

**Query params**

| Name | Type | Description |
| --- | --- | --- |
| `offset` | int | Optional, default `0`. Byte offset to start reading from. If `offset > size` or `< 0`, the server resets to `0` (handles log rotation/truncation). |

**Response**

```json
{
  "content": "=== run_sim.sh plan ===\n  model         : /data/...\n  ...",
  "offset": 14460,
  "size": 14460
}
```

| Field | Type | Description |
| --- | --- | --- |
| `content` | string | Bytes between `offset` (request) and `size`, UTF-8 decoded with `errors="replace"`. |
| `offset` | int | Current file size. Pass this back as `offset` in the next poll. |
| `size` | int | Same as `offset`. |

**Status codes**

| Code | Cause |
| --- | --- |
| 400 | Run name fails the validation regex. |
| 404 | No `run.log` found in either the active or library location. |

**curl**

```bash
curl "${BACKEND_URL}/api/runs/cluster_6_15_eq_v3/log?offset=0"
```

### GET /api/runs/{run_name}/frame/{frame_idx}.ply

Serve one frame `.ply` from a sequence. Used by the splat-mode player to
bootstrap the in-browser splat mesh (the WebSocket pump streams xyz only;
this endpoint serves the full attribute set).

**Path params**

| Name | Type | Description |
| --- | --- | --- |
| `run_name` | string | Run / sequence name. |
| `frame_idx` | int | Zero-padded to 4 digits in the on-disk filename, but supplied as an integer here (e.g. `0`, `1`, `42`). |

**Response**

`application/octet-stream`; the raw frame .ply bytes.

**Status codes**

| Code | Cause |
| --- | --- |
| 400 | Path-traversal on the legacy fallback path. |
| 404 | Sequence or frame not found in either canonical or legacy locations. |

**curl**

```bash
curl -OJ "${BACKEND_URL}/api/runs/cluster_6_15_eq_v3/frame/0.ply"
```

---

## Sequences

A sequence is a time-sampled `.ply` collection — either produced by a sim
run (`source: "sim"`) or imported from an external folder (`source:
"import"`). All sequences live in `work/library/sequences/<name>/`.

### GET /api/sequences

List every sequence in the library, newest first by `created_at`.

**Response**

```json
[
  {
    "name": "cluster_6_15_eq_v3",
    "kind": "sequence",
    "source": "sim",
    "source_path": "host:/data/.../sequences/cluster_6_15_eq_v3",
    "model_ref": "cluster_6_15",
    "frame_count": 151,
    "fps_hint": 24,
    "n_splats": 683741,
    "bbox_initial": [[-15.2, -9.0, -25.2], [15.2, 9.0, 25.2]],
    "coord_convention": "z-up",
    "first_frame_full": true,
    "created_at": "2026-05-20T08:38:17Z",
    "converted_from": null,
    "is_broken": false,
    "cache": {
      "splats_gsq_mtime": 1779266297.33,
      "splats_gsq_bytes": 412345678,
      "frames_bin_mtime": null,
      "frames_bin_bytes": null
    }
  }
]
```

| Field | Type | Description |
| --- | --- | --- |
| `name` | string | Sequence name. |
| `kind` | string | Always `"sequence"`. |
| `source` | string | `"sim"` or `"import"`; `"unknown"` if `_meta.json` is missing. |
| `source_path` | string\|null | Original folder path (imports) or `host:/abs/path` (sim). |
| `model_ref` | string\|null | Parent model name. May be filled from `manifest.json:model_dir` while a sim is in-flight. |
| `frame_count` | int | From `_meta.json` if present; live filesystem count when meta is absent or the sim is still running. |
| `fps_hint` | int | Default `24`. |
| `n_splats` | int\|null | Splat count of the first frame. |
| `bbox_initial` | float[2][3]\|null | Initial-frame bounding box. |
| `coord_convention` | string | Always `"z-up"`. |
| `first_frame_full` | bool | True iff frame 0 carries the full 3DGS attribute set. |
| `created_at` | string\|null | ISO-8601 UTC. |
| `converted_from` | string\|null | `"y-up"` iff frames were converted at import. |
| `is_broken` | bool | True iff the imported `frames/` symlink is dangling. |
| `cache.splats_gsq_mtime` | float\|null | mtime of `work/cache/viser/<name>.gsq`, or `null` if absent. |
| `cache.splats_gsq_bytes` | int\|null | Size of the same file. |
| `cache.frames_bin_mtime` | float\|null | mtime of `library/sequences/<name>/frames.bin`. |
| `cache.frames_bin_bytes` | int\|null | Size of `frames.bin`. |

The server filesystem path (`path` key) is stripped before responding so
the client never learns the server's directory layout.

**curl**

```bash
curl ${BACKEND_URL}/api/sequences
```

### POST /api/sequences/import

Register an external folder of `frame_*.ply` as a sequence (symlinked, not
copied).

**Request body** (`application/json`)

```json
{
  "folder_path": "/data/external/my_seq",
  "name": "my_seq",
  "convert_y_up": false
}
```

| Field | Type | Description |
| --- | --- | --- |
| `folder_path` | string | **Required.** Absolute path to a directory of `frame_*.ply` files. |
| `name` | string\|null | Optional. Sequence name; defaults to the folder basename. |
| `convert_y_up` | bool | Optional. Default `false`. |

**Response**

Same shape as a single entry from `GET /api/sequences`.

**Status codes**

| Code | Cause |
| --- | --- |
| 409 | A sequence with that name already exists. |
| 422 | Folder missing / not a directory; plyfile parse error; missing `plyfile` dependency. |
| 500 | Disk error during import. |

**curl**

```bash
curl -X POST ${BACKEND_URL}/api/sequences/import \
  -H 'Content-Type: application/json' \
  -d '{"folder_path":"/data/external/my_seq","name":"my_seq"}'
```

### GET /api/sequences/{name}/frame/{frame_idx}.ply

Alias of `GET /api/runs/{run_name}/frame/{frame_idx}.ply`. Same bytes, same
status codes — exists so the frontend WebSocket bootstrap can hit either
URL shape.

**curl**

```bash
curl -OJ "${BACKEND_URL}/api/sequences/cluster_6_15_eq_v3/frame/0.ply"
```

### POST /api/sequences/{name}/cache/build

Kick off building the `.gsq` cache for a sequence as a background
subprocess (runs `server/tools/pack_splats.py`). Idempotent: if the
`.gsq` already exists on disk it returns `done` immediately without
spawning anything; if a build is already running it returns the existing
job. Job state lives in process memory only (lost on restart), but the
subprocess writes to disk, so a restart simply re-detects the finished
file.

**Path params**

| Name | Type | Description |
| --- | --- | --- |
| `name` | string | Sequence name. Validated against `^[A-Za-z0-9_.\-]+$`. |

**Response** (the job descriptor)

```json
{
  "name": "cluster_6_15_eq_v3",
  "state": "building",
  "started_at": 1779266060.81,
  "finished_at": null,
  "stdout_tail": "",
  "error": null
}
```

When the cache already exists: `{"name": "...", "state": "done", "note": "cache already exists on disk"}`.

| Field | Type | Description |
| --- | --- | --- |
| `name` | string | Echo of the sequence name. |
| `state` | string | `"building"` (new or in-flight) or `"done"` (fast-path). |
| `started_at` | float\|absent | Unix epoch when this build started. |
| `finished_at` | float\|null | Set when the subprocess exits. |
| `stdout_tail` | string | Tail of the packer's stdout (grows as it runs). |
| `error` | string\|null | `repr()` of the exception if the build failed. |
| `note` | string | Only on the `done` fast-path. |

**Status codes**

| Code | Cause |
| --- | --- |
| 404 | Sequence not found. |
| 422 | Name fails the validation regex. |

**curl**

```bash
curl -X POST ${BACKEND_URL}/api/sequences/cluster_6_15_eq_v3/cache/build
```

### GET /api/sequences/{name}/cache/build-status

Poll the build job for a sequence. Use after `POST .../cache/build` to
wait for `state: "done"` before downloading `splats.gsq`.

**Path params**

| Name | Type | Description |
| --- | --- | --- |
| `name` | string | Sequence name. Validated against `^[A-Za-z0-9_.\-]+$`. |

**Response**

```json
{ "name": "cluster_6_15_eq_v3", "state": "done" }
```

| State | Meaning |
| --- | --- |
| `"idle"` | No build requested in this process and no `.gsq` on disk. |
| `"building"` | Subprocess is running. The full job descriptor (see `build`) is returned, including `stdout_tail`. |
| `"done"` | `.gsq` is on disk. (If no job is tracked but the file exists, `note: "cache exists (no job tracked)"` is added.) |
| `"error"` | Subprocess failed; `error` holds the exception repr. |

**Status codes**

| Code | Cause |
| --- | --- |
| 422 | Name fails the validation regex. |

**curl**

```bash
curl ${BACKEND_URL}/api/sequences/cluster_6_15_eq_v3/cache/build-status
```

### GET, HEAD /api/sequences/{name}/cache/splats.gsq

Download the `.gsq` splat-sequence cache (produced by
`server/tools/pack_splats.py`). This is the canonical playback artifact:
the client downloads the whole file once, then plays it back locally — the
server only serves bytes, it never decodes. **`GET`** streams the bytes;
**`HEAD`** returns the same headers with no body, so a client can read the
download size (`Content-Length`) and resumability (`Accept-Ranges`) up
front before committing to the pull.

The file is treated as immutable for a given (size, mtime): every response
carries a weak `ETag` of the form `"<size>-<mtime_int>"` and
`Cache-Control: public, immutable, max-age=31536000`.

Format: 80-byte header + 16-byte index entry per frame + zstd static block
(color/opacity/scale, stored once) + per-frame zstd chunks. As of codec
**v2** the per-frame chunks are temporal deltas against periodic keyframes
(bit-exact). See `docs/ARCHITECTURE.md` for the on-disk layout.

**Path params**

| Name | Type | Description |
| --- | --- | --- |
| `name` | string | Sequence name. |

**Response headers** (GET and HEAD)

| Header | Value |
| --- | --- |
| `Content-Length` | Full file size in bytes — the progress-bar denominator. |
| `Accept-Ranges` | `bytes` — the download is resumable / chunkable. |
| `ETag` | `"<size>-<mtime_int>"`. |
| `Cache-Control` | `public, immutable, max-age=31536000`. |

**Conditional + partial requests**

| Request header | Result |
| --- | --- |
| `If-None-Match: <etag>` matches | `304 Not Modified`, no body (ETag repeated). |
| `Range: bytes=N-` (or `N-M`) | `206 Partial Content` + `Content-Range`. Resume a broken transfer by ranging from the bytes you already have. |
| `HEAD` | `200` with the headers above and an empty body. |

**Response body**

`application/octet-stream`; the raw `.gsq` bytes (GET / 206 only).

**Status codes**

| Code | Cause |
| --- | --- |
| 400 | Resolved path escapes the cache root (defensive). |
| 404 | Sequence not found, or the `.gsq` cache has not been built yet (POST `/api/sequences/{name}/cache/build`, or run `server/tools/pack_splats.py <name>` on the server). |

**curl**

```bash
# size + headers only (no body):
curl -sI "${BACKEND_URL}/api/sequences/cluster_6_15_eq_v3/cache/splats.gsq"
# full download:
curl -OJ "${BACKEND_URL}/api/sequences/cluster_6_15_eq_v3/cache/splats.gsq"
# resume from byte N:
curl -H "Range: bytes=N-" -OJ "${BACKEND_URL}/api/sequences/cluster_6_15_eq_v3/cache/splats.gsq"
```

### GET /api/sequences/{name}/cache/frames.bin

Serve the GSSQ-packed `frames.bin` (int16-quantized xyz per frame, output
of `server/tools/pack_sequence.py`). Used by the client Points-mode WS server for
fast local streaming.

**Path params**

| Name | Type | Description |
| --- | --- | --- |
| `name` | string | Sequence name. |

**Response**

`application/octet-stream`; the raw `frames.bin` bytes.

**Status codes**

| Code | Cause |
| --- | --- |
| 400 | Resolved path escapes the library root. |
| 404 | Sequence not found, or `frames.bin` has not been built yet (run `server/tools/pack_sequence.py <name>` on the server). |

**curl**

```bash
curl -OJ "${BACKEND_URL}/api/sequences/cluster_6_15_eq_v3/cache/frames.bin"
```

### DELETE /api/sequences/{name}

Remove a sequence from the library. For imports (where `frames/` is a
symlink) only the library entry + symlink go; the source folder is never
touched. For sim-produced sequences (real dirs) the whole entry is
`rmtree`'d. Refuses to delete a sequence whose sim is still running.

**Path params**

| Name | Type | Description |
| --- | --- | --- |
| `name` | string | Sequence name. |

**Response**

```json
{ "deleted": "cluster_6_15_eq_v3" }
```

**Status codes**

| Code | Cause |
| --- | --- |
| 400 | Path-traversal. |
| 404 | Sequence not found. |
| 409 | A sim is still writing into this sequence; cancel it first. |
| 500 | Delete failed. |

**curl**

```bash
curl -X DELETE ${BACKEND_URL}/api/sequences/cluster_6_15_eq_v3
```

---

## Schemas

Static schemas used by the React BC and material editors. Both endpoints
read in-memory tables and never touch disk.

### GET /api/schemas/boundaries

Boundary-condition type schemas, keyed by BC type.

**Response**

```json
{
  "bounding_box": [],
  "surface_collider": [
    { "name": "point",        "type": "vec3",   "default": [0.0, 0.0, 0.0], "hint": "Plane origin" },
    { "name": "normal",       "type": "vec3",   "default": [0.0, 0.0, 1.0], "hint": "Plane normal (unit)" },
    { "name": "surface_type", "type": "string", "default": "sticky",        "hint": "sticky | slip | separate" },
    { "name": "friction",     "type": "float",  "default": 0.0,             "hint": "0..1" }
  ],
  "cuboid": [ "..." ],
  "release_particles_sequentially": [ "..." ]
}
```

| Field | Type | Description |
| --- | --- | --- |
| `<bc_type>` | array | List of field schemas. Empty list means the BC takes no parameters. |
| `<bc_type>[].name` | string | Field name. |
| `<bc_type>[].type` | string | `"vec3"`, `"float"`, or `"string"`. |
| `<bc_type>[].default` | any | Default value (typed per `type`). |
| `<bc_type>[].hint` | string | UI hint text. |

**curl**

```bash
curl ${BACKEND_URL}/api/schemas/boundaries
```

### GET /api/schemas/materials

Per-material default parameters.

**Response**

```json
{
  "jelly":      { "E": 5000.0,  "nu": 0.38, "density": 1, "yield_stress": 0.0,    "friction_angle": 45.0, "beta": 1.0, "xi": 3.0,  "hardening": 1.0, "alpha_0": -0.04, "plastic_viscosity": 0.0 },
  "metal":      { "E": 50000.0, "nu": 0.30, "density": 3, "yield_stress": 1000.0, "friction_angle": 0.0,  "beta": 1.0, "xi": 3.0,  "hardening": 1.0, "alpha_0": -0.04, "plastic_viscosity": 0.0 },
  "sand":       { "...": "..." },
  "foam":       { "...": "..." },
  "snow":       { "...": "..." },
  "plasticine": { "...": "..." },
  "watermelon": { "...": "..." }
}
```

| Field | Type | Description |
| --- | --- | --- |
| `<material>` | object | Default MPM parameter set for this material. |
| `<material>.E` | float | Young's modulus. |
| `<material>.nu` | float | Poisson's ratio. |
| `<material>.density` | float | Density (relative). |
| `<material>.yield_stress` | float | Plastic yield stress (`0` for purely elastic materials). |
| `<material>.friction_angle` | float | Drucker-Prager friction angle, degrees. |
| `<material>.beta` | float | Snow-model parameter. |
| `<material>.xi` | float | Snow-model hardening exponent. |
| `<material>.hardening` | float | Hardening multiplier. |
| `<material>.alpha_0` | float | Snow-model initial alpha. |
| `<material>.plastic_viscosity` | float | Plastic viscosity. |

**curl**

```bash
curl ${BACKEND_URL}/api/schemas/materials
```

---

## WebSocket: /api/stream

A streaming channel for live frame data and run logs. Same backend as the
runs router but pushed instead of polled. Documented here in brief so a
client wiring against it knows the message shapes; see
`server/gsfluent/api/stream.py` for the canonical definition.

**Connect**

```
ws://your-backend:port/api/stream
```

**Client → server (JSON)**

| Type | Fields | Effect |
| --- | --- | --- |
| `subscribe` | `run_name: string` | Start pumping frames + log lines for `run_name`. Cancels any prior subscription on the same socket. |
| `unsubscribe` | — | Stop pumping. |
| `load_model` | `path: string` | Render `<path>/point_cloud/iteration_<N>/point_cloud.ply` (highest N) as a one-frame snapshot. Path must be in the registered-models allowlist. |

**Server → client (JSON, except where noted)**

| Type | Fields | Notes |
| --- | --- | --- |
| `static_attrs` | `run_name`, `n`, `R_b64`, `scales_b64`, `rgb_b64`, `opacity_b64` | Sent once per subscription. Currently only `rgb_b64` is populated (others are empty strings — see source comments for the rationale). |
| `frame_meta` | `run_name`, `frame_idx`, `n` | Always followed by a binary message. |
| *(binary)* | `Float32Array` of shape `(n, 3)` xyz | Frame payload. |
| `log` | `run_name`, `line` | Replay + live tail of `run.log`. |
| `status` | `run_name`, `state` | Emitted when a manifest reaches a terminal state (`done` / `error` / `cancelled`). |
| `error` | `code`, `message`, `run_name` *or* `path` | Codes: `run_not_found`, `snapshot_failed`, `watch_failed`, `model_not_found`, `model_parse_failed`. |

---

## Common workflows

### 1. Listing recipes and picking one

```bash
curl ${BACKEND_URL}/api/recipes
curl ${BACKEND_URL}/api/recipes/earthquake
```

The second call returns the full recipe body in `data` — that's what you
pass back as `recipe_data` to `POST /api/runs`.

### 2. Submitting a sim run end-to-end

```bash
# 1. Pick a model
MODEL_PATH=$(curl -s ${BACKEND_URL}/api/models \
  | python3 -c "import json,sys; print(json.load(sys.stdin)[0]['path'])")

# 2. Load a recipe
RECIPE=$(curl -s ${BACKEND_URL}/api/recipes/earthquake)

# 3. Build the start payload (recipe_data === recipe.data)
python3 -c "
import json, sys
r = json.loads('''$RECIPE''')
print(json.dumps({
    'run_name': 'cluster_6_15_eq_demo',
    'model_path': '$MODEL_PATH',
    'recipe_data': r['data'],
    'recipe_source': r['name'],
    'particles': 200000,
    'dry_run': False,
}))
" > start.json

# 4. Submit
curl -X POST ${BACKEND_URL}/api/runs \
  -H 'Content-Type: application/json' -d @start.json
```

Response carries `run_id` (use for cancel) and `run_name` (use for log /
frames / history).

### 3. Polling run status and log

```bash
RUN=cluster_6_15_eq_demo
OFFSET=0
while true; do
  RESP=$(curl -s "${BACKEND_URL}/api/runs/$RUN/log?offset=$OFFSET")
  CONTENT=$(echo "$RESP" | python3 -c "import json,sys; print(json.load(sys.stdin)['content'])")
  OFFSET=$(echo "$RESP" | python3 -c "import json,sys; print(json.load(sys.stdin)['size'])")
  [ -n "$CONTENT" ] && printf '%s' "$CONTENT"
  # Check if the run has dropped out of the active list
  ACTIVE=$(curl -s ${BACKEND_URL}/api/runs)
  echo "$ACTIVE" | grep -q "\"name\":\"$RUN\"" || break
  sleep 1
done
```

For a UI client, prefer the WebSocket: a single `subscribe` message gets
you the log replay, live tail, terminal `status`, and frame stream in one
socket.

### 4. Downloading the resulting .gsq cache

The cache is built automatically at the end of a successful run. To
trigger / inspect it explicitly:

```bash
# kick off (idempotent — fast-paths if the file already exists):
curl -X POST ${BACKEND_URL}/api/sequences/cluster_6_15_eq_demo/cache/build
# poll until "state":"done":
curl ${BACKEND_URL}/api/sequences/cluster_6_15_eq_demo/cache/build-status
```

The model is **download-then-play**: pull the whole file once (HTTP `Range`
makes it resumable, so a broken transfer continues instead of restarting),
then play it back locally.

```bash
# read the size first (HEAD), then download:
curl -sI "${BACKEND_URL}/api/sequences/cluster_6_15_eq_demo/cache/splats.gsq"
curl -OJ "${BACKEND_URL}/api/sequences/cluster_6_15_eq_demo/cache/splats.gsq"
```

For a progress bar, the client reads the total size from `Content-Length`
(via HEAD) or from `cache.splats_gsq_bytes` in `GET /api/sequences`, then
tracks bytes received. The server is stateless — it does not track
per-client download progress.
