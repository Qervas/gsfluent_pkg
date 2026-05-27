# Proposal: A self-customizable recipe system for gsfluent

Status: DRAFT / design-only (no code in this change)
Author: exploration agent
Date: 2026-05-27

## TL;DR

gsfluent is **further along than "8 hardcoded presets"** — there is already a
CRUD recipe API (`GET/PUT/DELETE /api/recipes/<name>`), user recipes on disk
with provenance, a rich Properties panel with per-param sliders/markers, a
material-defaults table, and BC type schemas served over `/api/schemas/*`,
plus pre-spawn validation (`recipe_validation.py`) and caps (`limits.py`).

The real gap is **not "can users save recipes"** (they can) but that the
*knowledge about each parameter* — type, unit, range, default, which group it
belongs to, which material consumes it — is **scattered, duplicated, and
non-authoritative**:

- Per-param min/max/step/hint/markers are hand-coded in **each TSX panel**
  (`MaterialPanel.tsx`, `SolverPanel.tsx`, …). Adding a param = editing React.
- Material defaults live server-side (`material_defaults.py`) but the *list of
  materials* and *which fields each material uses* are re-declared in
  `MaterialPanel.tsx` (`MATERIAL_FIELDS`). Two sources of truth, already drifting
  (server has `beta/xi/hardening/alpha_0/plastic_viscosity`; the UI gates on a
  different 5-field set).
- Safe ranges in the UI sliders are **advisory only** — nothing on the server
  rejects an out-of-range `E` or a negative `substep_dt`. The only server-side
  gate is caps (particle count, wall time, recipe byte size) + sim_area overlap.
- There is no first-class **material library** (named, reusable, decoupled from a
  scene) and no **template/preset grouping** beyond the flat recipe files.

So this proposal recommends consolidating param knowledge into **one
server-authoritative parameter schema**, promoting materials to a **first-class
library decoupled from scenes**, hardening the existing CRUD into a real
fork/edit/save flow with schema-driven validation, and adding lightweight
**templates** (scene scaffolds) on top.

---

## 1. Current state (what exists today)

### Backend
- `server/recipes/*.json` — 8 builtins (demolition, earthquake, foam, jelly,
  metal, plasticine, sand, wrecking). Flat dicts: physics (`E`, `nu`, `density`,
  `substep_dt`, `frame_dt`, `n_grid`, `grid_lim`, `material`,
  `grid_v_damping_scale`, `yield_stress`, `softening`, `g`,
  `boundary_conditions`, plus material-specific `friction_angle/beta/xi/...`),
  particle-filling block, and camera/scene fields (`init_azimuthm`,
  `init_elevation`, `init_radius`, `delta_*`, `sim_area`, `sim_area_frame`,
  `mpm_space_*`, `opacity_threshold`, `show_hint`).
- `core/recipes.py` — read/list/`save_user_recipe`/resolve; builtin (read-only)
  vs user (`work/_user_recipes/`); name regex guard; **provenance stamp**
  (`_provenance.based_on`, `saved_at`). Builtin wins on name clash.
- `api/recipes.py` — full CRUD already: list, get, **PUT (save user, with
  `based_on`)**, **DELETE (403 on builtin, 404 on unknown)**.
- `api/schemas.py` → `/api/schemas/boundaries` (BC field schemas from
  `schemas/boundary.py`), `/api/schemas/materials` (`schemas/material_defaults.py`).
- `core/recipe_validation.py` — `sim_area_frame` translate + sim_area↔model
  bbox overlap preflight (readable 422 instead of torch crash).
- `core/limits.py` — `CapConfig` + `check_recipe_caps` (particle count, wall
  time, recipe bytes), env-overridable.
- `api/runs.py` — strict Pydantic (`extra="forbid"`, `strict=True`) on the
  *envelope*, but `recipe_data: dict` is **opaque** — no per-field schema check.

### Frontend
- `recipes/RecipesModal.tsx` — library manager: list builtin/user, Use-in-Sim,
  Duplicate (fork), Delete (user only), Import/Export `.json`, raw `JsonEditor`
  for user recipes (builtins read-only). Save-on-explicit-Save with dirty flag.
- `lib/store.ts` — `simRecipeBaseline` + sparse `simOverrides` map; effective =
  `{...baseline, ...overrides}`; cleared on recipe switch.
- `lib/use-overrides.ts` — `useOverrides()` / `usePanelData()`.
- `components/properties/*` — folders: Material, Solver, Forces, Sim setup,
  Camera, Particle filling, Other, Boundary conditions, Provenance. **Each panel
  hardcodes its param specs** (label/min/max/step/hint/markers).
- `MaterialPanel.tsx` — material dropdown; on change, snaps server material
  defaults into the recipe and clears overrides; per-material **field-visibility
  gating** (`MATERIAL_FIELDS`).
- Widgets: `ScientificInput` (log/linear slider + reference markers + revert),
  `SliderInput`, `NumberInput`, `Vec3Input`, `SelectInput`, `SwitchInput`,
  `ScientificInput`, `JsonEditor`.

**Takeaway:** the plumbing (CRUD, overrides, widgets, provenance, caps) is
solid. The missing piece is a **single typed schema** that drives the UI,
validation, defaults, and grouping — replacing the duplicated TSX/Python tables.

---

## 2. Goals & non-goals

**Goals**
1. One **authoritative, typed parameter schema** (server-owned, served to UI).
2. A **material library** decoupled from scenes — named materials, reusable,
   forkable, saved like recipes.
3. First-class **fork → edit → save → delete** for both recipes and materials
   (extends today's CRUD; makes "fork from builtin" the primary path).
4. Explicit **param grouping** (material / solver / scene / camera / boundary)
   sourced from the schema, not the panel layout.
5. **Schema-driven validation** with safe ranges tied to stability — *warn* vs
   *hard-reject* tiers, enforced server-side at save and at run.
6. Clean **storage + API + migration** with no break to existing recipes.
7. A **phased path** that ships value at each step and never regresses the
   working CRUD.

**Non-goals**
- Changing the sim engine / MPM solver or the recipe→server submit contract.
- Per-user accounts / multi-tenant recipe ownership (single-workbench model).
- Live local simulation (recipes still run server-side only).

---

## 3. The core idea: a parameter schema as the single source of truth

Introduce a server module `schemas/params.py` exposing `PARAM_SCHEMA`: a typed,
declarative description of **every recipe parameter**. This is the keystone —
everything else (UI rendering, validation, grouping, defaults, material library)
derives from it.

### 3.1 Proposed schema shape (one entry per parameter)

```python
# server/gsfluent/schemas/params.py  (illustrative)
ParamSpec = TypedDict("ParamSpec", {
    "key":      str,                 # recipe JSON key, e.g. "E"
    "label":    str,                 # human label, e.g. "Young's modulus"
    "group":    Literal["material", "solver", "scene", "camera",
                        "boundary", "particle_filling", "advanced"],
    "type":     Literal["float", "int", "bool", "enum", "vec3", "string"],
    "unit":     str | None,          # "sim", "°", "s", "cells", None
    "default":  Any,                 # fallback when absent
    "min":      float | None,        # hard lower bound (reject below)
    "max":      float | None,        # hard upper bound (reject above)
    "soft_min": float | None,        # below → warn (still allowed)
    "soft_max": float | None,        # above → warn
    "step":     float | None,        # UI granularity
    "scale":    Literal["linear", "log"],
    "widget":   Literal["slider", "number", "scientific", "switch",
                        "select", "vec3"] | None,   # UI hint; falls back by type
    "options":  list[str] | None,    # for enum
    "markers":  list[dict] | None,   # [{"value": 50000, "label": "metal"}]
    "hint":     str,                 # the existing tooltip text, moved here
    "stability": str | None,         # WHY the range matters (CFL note etc.)
    "applies_to": list[str] | None,  # materials that consume it; None = always
})
```

Concrete example entries (consolidating today's `FIELD_SPECS`, `SolverPanel`
`FIELDS`, `MATERIAL_FIELDS`, and `material_defaults.py`):

```python
PARAM_SCHEMA: list[ParamSpec] = [
  {"key": "E", "label": "Young's modulus", "group": "material",
   "type": "float", "unit": "sim", "default": 5000.0,
   "min": 1.0, "max": 1e7, "soft_min": 10.0, "soft_max": 1e6,
   "step": 1.0, "scale": "log", "widget": "scientific",
   "markers": [{"value": 50, "label": "soft foam"},
               {"value": 500, "label": "jelly"},
               {"value": 50000, "label": "metal"}],
   "hint": "Material stiffness. Log axis — useful range spans 5+ decades.",
   "stability": "Higher E needs smaller substep_dt for CFL stability.",
   "applies_to": None},

  {"key": "nu", "label": "Poisson ratio", "group": "material",
   "type": "float", "default": 0.38, "min": 0.0, "max": 0.499,
   "soft_max": 0.49, "step": 0.005, "scale": "linear",
   "hint": "0 ≤ ν < 0.5. → 0.5 is incompressible (rubber/jelly).",
   "stability": "ν ≥ 0.5 is singular — the solver divides by (1-2ν).",
   "applies_to": None},

  {"key": "friction_angle", "label": "Friction angle", "group": "material",
   "type": "float", "unit": "°", "default": 45.0, "min": 0.0, "max": 60.0,
   "step": 1.0, "scale": "linear", "hint": "Drucker-Prager internal friction.",
   "applies_to": ["sand", "snow"]},

  {"key": "n_grid", "label": "Grid resolution", "group": "solver",
   "type": "int", "unit": "cells", "default": 150, "min": 32, "max": 400,
   "soft_max": 256, "step": 1, "scale": "linear", "widget": "slider",
   "hint": "MPM grid cells per side. Memory ∝ n_grid³.",
   "stability": "Above ~256 the per-frame cov compute + cloud push dominate."},

  {"key": "substep_dt", "label": "Substep dt", "group": "solver",
   "type": "float", "unit": "s", "default": 1e-4, "min": 1e-6, "max": 5e-3,
   "soft_max": 2e-4, "step": 1e-5, "scale": "log", "widget": "scientific",
   "hint": "Inner integration step. Smaller = stable but slower.",
   "stability": "Must satisfy CFL: dt ≲ Δx / c, c = sqrt(E/ρ). Too large → blow-up."},

  {"key": "g", "label": "Gravity", "group": "scene", "type": "vec3",
   "unit": "u/s²", "default": [0, 0, -15], "hint": "Gravity vector (z-down)."},

  {"key": "init_azimuthm", "label": "Camera azimuth", "group": "camera",
   "type": "float", "unit": "°", "default": 95.0, "min": 0, "max": 360,
   "step": 1, "scale": "linear", "hint": "Initial camera azimuth."},
  # ... frame_dt, frame_num, grid_lim, flip_pic_ratio, rpic_damping,
  #     grid_v_damping_scale, density, yield_stress, opacity_threshold,
  #     show_hint, sim_area, sim_area_frame, particle_filling.*, delta_* ...
]
```

### 3.2 Why this is the right keystone

- **Kills duplication.** `FIELD_SPECS`, `SolverPanel.FIELDS`, `CameraPanel`
  arrays, `OtherPanel` literals, and the hint strings all collapse into one
  table. Panels become a thin `<ParamField spec={...}/>` loop driven by `group`.
- **Stops magic numbers.** Every param carries unit + hint + stability note +
  range, so nothing in the JSON is opaque.
- **Single grouping source.** `group` drives both the Properties folders and any
  future API consumer; layout stops being the source of truth.
- **Validation for free.** The same table powers UI clamping AND server-side
  reject/warn — they can never disagree.

### 3.3 Served to the UI

New endpoint `GET /api/schemas/params` returns the JSON-serialized
`PARAM_SCHEMA` (with `applies_to`, groups, ranges, markers). The frontend
caches it via react-query (like `schemas.materials` today) and:

- Builds a generic `ParamField` that picks the widget by `widget`/`type`,
  feeds min/max/step/scale/markers/unit/hint straight from the spec.
- Builds the Properties folders by grouping specs on `spec.group` (replacing the
  hand-wired panel list — though panels can still add prose/section headers).
- Gates visibility on `applies_to` ∋ current material (replaces `MATERIAL_FIELDS`).

`frontend/src/lib/types.ts` gains a `ParamSpec` type mirroring the server shape,
and `api.schemas.params()` joins `boundaries()` / `materials()`.

---

## 4. Material library (decoupled from scenes)

Today a "material" is a string + a server-side default table snapped into the
recipe. Promote it to a **named, saveable, forkable artifact**, exactly mirroring
the recipe storage model.

### 4.1 Model
A material = `{ name, base_model, params }` where:
- `base_model` ∈ the solver's constitutive models (`jelly`, `metal`, `sand`,
  `foam`, `snow`, `plasticine`, `watermelon`) — the thing the MPM core branches
  on. This stays fixed; users can't invent solver code.
- `params` = the subset of `PARAM_SCHEMA` in group `material` that
  `applies_to` `base_model` (E, nu, density, yield_stress, friction_angle,
  beta, xi, hardening, alpha_0, plastic_viscosity, softening).

Builtin materials = today's `MATERIAL_DEFAULTS` rows, shipped read-only. User
materials live in `work/_user_materials/<name>.json`, with the same provenance
stamp + name guard as recipes.

### 4.2 Decoupling from recipes
- A recipe references a material **by value** at save time (params copied in) so
  recipes remain self-contained for the server submit contract (which expects a
  flat dict). Optionally also record `_material_ref: "<name>"` for provenance /
  "re-sync from material" UX.
- "Apply material" in the Material panel = fetch the material's params and snap
  them in (today's `onMaterialChange`, generalized from builtins to the library).
- "Save current material params as…" lets a user fork a tuned material out of a
  recipe into the library — the inverse direction, new.

### 4.3 API (mirrors recipes)
```
GET    /api/materials                 → [{name, source}]
GET    /api/materials/<name>          → {name, source, base_model, params}
PUT    /api/materials/<name>          → save user material (body: {base_model, params, based_on?})
DELETE /api/materials/<name>          → 403 builtin / 404 unknown / delete user
```
`/api/schemas/materials` stays as the **defaults** endpoint (back-compat) but is
re-derived from the builtin material library so there's one source.

---

## 5. Custom recipes: fork / edit / save / delete

Most of this **already exists** — the work is to make schema-aware editing the
primary path and keep the raw JSON editor as an escape hatch.

- **Fork from builtin** (exists as "Duplicate"): rename the affordance to
  **"Fork"** in `RecipesModal`, prompt for a name, copy data, stamp
  `_provenance.based_on`. Already wired through `api.recipes.save(name, data, based_on)`.
- **Edit**: today user recipes only get a raw `JsonEditor`. Change the detail
  pane to render the **schema-driven Properties panels** for user recipes too
  (read-only structured view for builtins), with the JSON editor behind an
  "Advanced / raw JSON" toggle. This reuses the entire `properties/*` stack.
- **Save / Save-as**: explicit Save (exists) + "Save as new recipe" from the
  Sim Properties panel when the user has accumulated `simOverrides` (turns the
  effective config into a new user recipe — the natural fork-from-overrides flow).
- **Delete**: exists (`onDelete`, 403/404 semantics).
- **Import / Export `.json`**: exists; add a schema validation pass on import so
  a malformed/hand-edited file surfaces field errors instead of failing at run.

UI implication: `RecipeDetail` gains a mode switch (Structured ⇄ Raw JSON);
structured mode is the schema-driven `Properties` tree pointed at the recipe
draft instead of the live sim overrides. The override engine
(`simRecipeBaseline`/`simOverrides`) and the recipe-edit draft are kept distinct
(they already are: store has both `activeRecipeData/Pristine` and the override
pair) — the proposal does not merge them.

---

## 6. Grouping, templates & presets

### 6.1 Grouping
Driven entirely by `spec.group` (§3). The Properties folders map 1:1 to groups:
**Material · Solver · Scene (gravity, sim_area) · Camera · Boundary conditions ·
Particle filling · Advanced**. "Forces" + "Sim setup" + "Other" fold into Scene
/ Advanced. Folder order and default-open state become a small static config
keyed by group name, not a hand-wired component list.

### 6.2 Templates (new, lightweight)
A **template** = a scene scaffold *without* a committed material: gravity, BCs,
camera, sim_area, frame_num, solver params — i.e. "earthquake rig", "wrecking
rig", "free settle". Creating a recipe = **template × material**:

```
new recipe = template(scene + BCs + solver + camera) ⊕ material(physics params)
```

This is the clean decomposition the current 8 builtins blur (e.g. `earthquake`
hardcodes `watermelon`; `demolition`/`wrecking` hardcode `plasticine`). Splitting
them lets a user run "earthquake rig + foam" without hand-editing BCs.

- Storage: `server/recipes/_templates/<name>.json` (builtin) +
  `work/_user_templates/`. API `GET/PUT/DELETE /api/templates/<name>` mirrors recipes.
- The "New recipe" UI = pick a template, pick a material, name it → server
  composes and writes a user recipe (template fields + material params merged,
  material params winning on key conflicts).
- Builtin scenario templates extracted from the 8 builtins:
  `free_settle` (jelly/metal/sand/foam shared scaffold), `earthquake_rig`,
  `wrecking_rig`, `demolition_rig`.

Templates are optional sugar — recipes remain the runnable artifact and the
submit contract is unchanged.

---

## 7. Validation & safe ranges (tie-in to stability)

Two tiers, both **derived from `PARAM_SCHEMA`** so UI and server agree:

1. **Hard bounds** (`min`/`max`): reject. Wired into a new
   `recipe_validation.validate_against_schema(recipe)` that runs:
   - on `PUT /api/recipes/<name>` and `PUT /api/materials/<name>` (save-time), and
   - in `api/runs.py` *before* `check_recipe_caps` (run-time), returning the
     existing 422 envelope (`{"error": {kind, message, details, trace_id}}`).
2. **Soft bounds** (`soft_min`/`soft_max`) + **cross-field stability checks**:
   warn, don't block. Surfaced as non-blocking advisories in the UI and in a
   `warnings: [...]` field on save/dry-run responses.

Cross-field stability rules worth encoding (the part magic numbers hide today):
- **CFL**: `substep_dt ≲ grid_spacing / sqrt(E/density)` where
  `grid_spacing = 2·grid_lim / n_grid`. Warn (or hard-reject at a safety
  multiple) — this is the #1 cause of solver blow-ups for hand-tuned recipes.
- `nu < 0.5` strictly (hard) — singular otherwise.
- `frame_num × frame_dt` vs `max_wall_time_sec` estimate — warn on likely
  timeout.
- BC `start_time < end_time`; collider not spawned inside model geometry at
  `t=0` (the documented `meteor/uplift` crash class — see RECIPES.md).

Server stays authoritative (UI clamping is convenience, never trust). This slots
in beside the existing `validate_sim_area_intersects_model` preflight; `dry_run`
(already in `StartRunRequest`) becomes the "validate my recipe without burning
GPU" path and returns the full warning list.

---

## 8. Storage & API surface (summary)

```
Recipes      server/recipes/*.json (builtin)      work/_user_recipes/*.json
Materials    [builtin from material lib]          work/_user_materials/*.json
Templates    server/recipes/_templates/*.json     work/_user_templates/*.json
Schema       server/gsfluent/schemas/params.py    (code, served read-only)
```

API additions (all mirror the proven `recipes.py` CRUD shape — name regex guard,
builtin-wins resolution, atomic tmp-write, provenance stamp):

| Method | Path | Notes |
|---|---|---|
| GET | `/api/schemas/params` | the new param schema (the keystone) |
| GET/PUT/DELETE | `/api/materials[/<name>]` | material library CRUD |
| GET/PUT/DELETE | `/api/templates[/<name>]` | template CRUD (optional, phase 5) |
| POST | `/api/recipes` (compose) | `{template, material, name}` → user recipe |

Validation hooks added to existing `PUT /api/recipes`, `PUT /api/materials`, and
`POST /api/runs` (schema check before caps).

**Migration (zero-break):**
- The 8 builtins stay valid as-is — `PARAM_SCHEMA` defaults cover every absent
  key, so old recipes load unchanged.
- `material_defaults.py` becomes the seed for the builtin material library
  (no behavior change; `/api/schemas/materials` re-derived from it).
- Optionally split the 4 scenario builtins into template + material for the
  templates feature, but the original flat recipes remain runnable.
- A one-time `scripts/validate_recipe_library.py` (uses `dry_run`) flags any
  builtin that violates the new schema so ranges can be tuned to *include* all
  shipped values before hard bounds go live.

**UI implications:**
- New generic `ParamField` widget; Properties folders generated from `group`.
- `MaterialPanel` reads the material library (builtin + user), not a hardcoded
  list; "Save material as…" added.
- `RecipesModal` detail pane: Structured ⇄ Raw JSON toggle; "Duplicate" → "Fork".
- New "Material library" + (phase 5) "Template" pickers, reusing the modal shell.
- Inline warning chips for soft-range / stability advisories.

---

## 9. Phased implementation path

**Phase 0 — Schema extraction (no behavior change).**
Create `schemas/params.py` consolidating every param spec from the TSX panels +
`material_defaults` + `MATERIAL_FIELDS`. Add `GET /api/schemas/params`. Add the
`ParamSpec` type to `types.ts` and `api.schemas.params()`. Ship a
`scripts/validate_recipe_library.py` that asserts all 8 builtins fall within the
declared (soft) ranges. **Outcome:** one source of truth exists, nothing else
changes yet.

**Phase 1 — UI reads the schema.**
Refactor `MaterialPanel`/`SolverPanel`/`CameraPanel`/etc. to render via a generic
`ParamField` driven by `PARAM_SCHEMA`. Delete the duplicated TSX tables. Generate
Properties folders from `group`. Material field-visibility from `applies_to`.
**Outcome:** adding/retuning a param is a one-line schema edit; UI/Python can't drift.

**Phase 2 — Server-side schema validation.**
`validate_against_schema()` wired into `POST /api/runs` (hard bounds → 422 before
caps) and `PUT /api/recipes`. Add CFL + `nu` + BC-time stability checks as
warnings via `dry_run`. **Outcome:** ranges are enforced, not advisory.

**Phase 3 — Material library.**
`core/materials.py` + `/api/materials` CRUD (clone of recipes). Builtin materials
seeded from `material_defaults`. Material panel reads the library; "Save material
as…" + "Apply material" flows. **Outcome:** materials are reusable, forkable,
decoupled.

**Phase 4 — Recipe editing polish.**
`RecipesModal` "Fork" rename, Structured ⇄ Raw toggle (schema panels for user
recipes), "Save overrides as new recipe" from Sim Properties, import-time schema
validation. **Outcome:** full fork→edit→save→delete on structured fields, not raw JSON.

**Phase 5 — Templates (optional sugar).**
`core/templates.py` + `/api/templates` + `POST /api/recipes` compose endpoint.
Extract `free_settle`/`earthquake_rig`/`wrecking_rig`/`demolition_rig` from the
builtins. "New recipe = template × material" wizard. **Outcome:** scene scaffolds
reusable across materials.

Each phase is independently shippable and never regresses today's working CRUD.

---

## 10. Risks & open questions

- **Range tuning vs shipped values.** Some builtins use aggressive values (e.g.
  `demolition` E=50000 on plasticine, `substep_dt=5e-5`). Hard bounds must be set
  *after* the Phase-0 audit so no builtin is retroactively invalid. Start ranges
  permissive (soft warnings) and tighten over time.
- **CFL check accuracy.** The exact stability bound depends on the MPM core's
  internals; encode it as a *warning* with a tunable safety factor before
  promoting any part to a hard reject.
- **Material-by-value vs by-reference.** Copying material params into recipes
  keeps the submit contract flat (recommended) but means a recipe can drift from
  its source material. `_material_ref` + an explicit "re-sync" action handles this
  without coupling the runtime path to the material library.
- **Recipe byte cap.** `DEFAULT_MAX_RECIPE_BYTES = 16 KiB` — richer recipes
  (templates merged in) stay well under, but worth re-checking after Phase 5.
- **Backward compat of `/api/schemas/materials`.** Keep it; re-derive from the
  library so existing UI code keeps working during the transition.
