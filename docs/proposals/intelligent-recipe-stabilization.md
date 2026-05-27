# Intelligent Recipe Stabilization & Validation Layer

**Status:** Proposal (design only)
**Date:** 2026-05-27
**Author:** exploration agent
**Scope:** prevent the class of bug where a recipe silently produces a numerically-unstable (NaN) MPM simulation.

---

## 1. The bug we are designing against

Every shipped recipe carried `grid_v_damping_scale = 1.1`. In the Warp solver
(`mpm_solver_warp/mpm_utils.py:add_damping_via_grid`, gated in
`mpm_solver_warp.py:556` and `:701`), damping is applied as:

```python
if self.mpm_model.grid_v_damping_scale < 1.0:
    grid_v_out *= scale     # multiply grid velocity each substep
```

So `1.1`:
- is a **silent no-op** (the branch never fires — no damping at all), and
- the *name* implies "more damping" when in fact `>1.0` would *amplify*
  velocity if the branch ever fired.

With zero damping, 6 of 8 recipes accumulated grid-velocity energy faster than
the material could dissipate it and diverged to NaN. The fuser silently dropped
the non-finite frames; only the fail-loud guard in
`check_sim_stability()` (mpm.py) turned that into a visible failure — *after*
burning the full GPU run. We fixed it by hand-setting `0.95` in the diverging
recipes.

**Root-cause class:** a recipe is a flat, unconstrained dict of physics
knobs. Nothing on the path from JSON → API → sim relates those knobs to each
other or to known-stable regimes. A single mis-set value (or a value whose
*sign of effect* is counterintuitive, like damping) silently produces garbage.

Two facts about the existing system are the foundation for the fix:

1. **A CFL clamp already exists and only ever tightens.** In
   `gs_simulation_building.py` (~line 594 on the server):

   ```python
   def evaluate_sound_speed_linear_elasticity_analysis(E, nu, rho):
       return np.sqrt(E * (1 - nu) / ((1 + nu) * (1 - 2 * nu) * rho))
   cfl = 0.6
   cfl_dt = cfl * dx / sound_speed          # dx = grid_lim / n_grid
   substep_dt = min(substep_dt, cfl_dt)     # never relaxes the recipe
   ```

   This is the template for the whole proposal: **derive a safe value from
   material properties; only ever make the recipe safer, never less safe;
   print what you did.** We extend the same idea to damping and to a pre-run
   static check.

2. **A fail-loud guard already exists** (`check_sim_stability`, mpm.py:116).
   It catches divergence *after the fact* by counting dropped non-finite
   frames. It is the safety net of last resort and must stay. Everything in
   this proposal is about catching the problem *earlier and cheaper*, so the
   guard fires rarely and only on genuinely novel instabilities.

---

## 2. Design principles

- **Three lines of defense, cheapest first.** Static lint (microseconds, no
  model, no GPU) → optional cheap dynamic pre-check (seconds, CPU/short GPU) →
  the existing post-run fail-loud guard (full run). Each catches what the
  previous can't.
- **Derive, don't just reject.** Where a stable value is computable from
  material properties (damping, dt), auto-supply it instead of erroring. This
  is exactly what the CFL clamp already does for `dt`.
- **Only ever tighten.** Auto-derivation must never make a recipe *less*
  stable than the author asked for (the CFL clamp learned this the hard way —
  see its inline comment about overwriting a deliberate `1e-4` with a looser
  `1.307e-4`). Auto-fixes move toward stability or no-op.
- **Loud, actionable, structured.** Every rejection names the field, the
  offending value, the rule it broke, and the concrete fix — in the same 422
  envelope (`{error: {kind, message, details, trace_id}}`) the API already
  uses.
- **User-overridable, but explicit.** A power user must be able to say "I know,
  let me run it anyway." Overrides are opt-in per-rule and logged, never the
  default.
- **No solver changes required for v1.** The laptop/server Warp builds differ
  (RECIPES.md "Known broken on laptop Warp 1.x"); the package side cannot
  assume it can edit the solver. All v1 logic lives in `server/gsfluent`.

---

## 3. The three layers and where they live

```
                       recipe JSON (server/recipes or user preset)
                                     │
   ┌─────────────────────────────────┼─────────────────────────────────┐
   │ Layer A — RECIPE LINTER (pure, static, no model, no GPU)           │
   │   new module: server/gsfluent/core/recipe_lint.py                  │
   │   • lint(recipe) -> LintReport{errors, warnings, autofixes}        │
   │   • physics knowledge table: per-material stable ranges            │
   │   • rule engine: known-unstable combos -> actionable messages      │
   │   • derive_stable_params(recipe): damping + dt suggestions         │
   │   Called from:                                                      │
   │     - PUT /api/recipes/<name>  (warn on save, never block)         │
   │     - POST /api/runs           (block on error unless overridden)  │
   │     - a CLI linter for the built-in library (CI gate)              │
   └─────────────────────────────────┼─────────────────────────────────┘
                                     │  (errors -> 422; autofixes applied
                                     │   into effective_recipe)
   ┌─────────────────────────────────┼─────────────────────────────────┐
   │ Layer B — CHEAP DYNAMIC PRE-CHECK (optional, opt-in)              │
   │   new: a "trial" mode flag passed to the sim engine               │
   │   • Tier 1 (heuristic, ~0 cost): energy/CFL margin estimate,      │
   │     already mostly computable statically -> folded into Layer A   │
   │   • Tier 2 (short trial run): run N substeps (e.g. 200), abort,   │
   │     check finiteness + max grid velocity growth, report.          │
   │   Lives in: sim_engines/mpm.py (new run(trial_substeps=...) path) │
   └─────────────────────────────────┼─────────────────────────────────┘
                                     │
   ┌─────────────────────────────────┼─────────────────────────────────┐
   │ Layer C — FAIL-LOUD GUARD (exists today, unchanged)               │
   │   check_sim_stability() in sim_engines/mpm.py                     │
   │   • post-run non-finite frame-drop detector -> SimUnstableRecipe  │
   └───────────────────────────────────────────────────────────────────┘
```

### Why a separate `recipe_lint.py` and not just more code in `recipe_validation.py`?

`recipe_validation.py` is about **geometry/scene** validation (sim_area vs
model bbox, coordinate-frame translation) — it needs the model on disk. The
stability rules are about **physics/numerics** and need *nothing but the recipe
dict*. Keeping them separate means:

- the linter is trivially unit-testable (pure function, no fixtures),
- it can run in three contexts (save, run, CI) without dragging in model I/O,
- the existing `validate_sim_area_intersects_model` flow in `runs.py` stays
  focused.

---

## 4. Layer A — the recipe linter (the heart of the proposal)

### 4.1 Data model

```python
# server/gsfluent/core/recipe_lint.py
from dataclasses import dataclass, field

@dataclass(frozen=True)
class LintFinding:
    rule: str                 # stable id, e.g. "damping.disabled"
    severity: str             # "error" | "warning"
    field: str                # offending recipe key, e.g. "grid_v_damping_scale"
    message: str              # human, actionable
    suggested: object | None  # value the autofixer would use, if any

@dataclass(frozen=True)
class LintReport:
    findings: list[LintFinding] = field(default_factory=list)
    autofixes: dict = field(default_factory=dict)  # field -> new value

    @property
    def errors(self):   return [f for f in self.findings if f.severity == "error"]
    @property
    def warnings(self): return [f for f in self.findings if f.severity == "warning"]
    def ok(self) -> bool: return not self.errors
```

### 4.2 Material knowledge table

A single source of truth mapping each `material` to physically-sane parameter
ranges. This is the institutional knowledge that currently lives only in
`_note` strings and people's heads.

```python
# Coarse, deliberately wide ranges — the point is to catch order-of-magnitude
# mistakes and known-bad combos, not to police taste.
MATERIAL_PROFILES = {
    "jelly":      MaterialProfile(E=(1e3, 2e4),  nu=(0.30, 0.45), density=(0.5, 2)),
    "metal":      MaterialProfile(E=(2e4, 2e5),  nu=(0.25, 0.40), density=(2, 8)),
    "sand":       MaterialProfile(E=(1e4, 5e4),  nu=(0.20, 0.40), density=(1.5, 3)),
    "foam":       MaterialProfile(E=(2e2, 2e3),  nu=(0.05, 0.20), density=(0.1, 0.6)),
    "plasticine": MaterialProfile(E=(2e4, 1e5),  nu=(0.15, 0.35), density=(2, 5)),
    "watermelon": MaterialProfile(E=(1e3, 5e3),  nu=(0.30, 0.45), density=(0.5, 2)),
    "snow":       MaterialProfile(...),
}
```

### 4.3 The rule set (v1)

Each rule is a small pure function `(recipe) -> LintFinding | None`. The
flagship rules, in priority order:

**R1 — `damping.disabled` (ERROR + autofix).** *The bug we hit.*
`grid_v_damping_scale >= 1.0` means the solver applies **no** damping (and
`>1.0` is a foot-gun that reads like "more" but is a no-op / would amplify).

> `grid_v_damping_scale=1.1` disables velocity damping entirely (the solver
> only damps when the value is < 1.0). With E=50000 (stiff) this diverges to
> NaN. Suggested: 0.95. Set `grid_v_damping_scale` below 1.0, or pass
> `allow_undamped=true` to override.

Autofix value derived from stiffness (R5).

**R2 — `dt.above_cfl` (ERROR + autofix).** Recompute the *exact* CFL bound the
solver uses and compare to the recipe's `substep_dt`:

```python
dx = grid_lim / n_grid
c  = sqrt(E*(1-nu) / ((1+nu)*(1-2*nu)*rho))   # sound speed
cfl_dt = 0.6 * dx / c
if substep_dt > cfl_dt:  -> error, suggested = cfl_dt
```

The server already clamps this, but surfacing it *pre-run* (a) tells the user
their `step_per_frame` will be larger/slower than they think, and (b) protects
the `--no_cfl_override` fast path (`sim_fast=True` in mpm.py passes
`--no_cfl_override`, which *disables* the server clamp — that path has **no**
dt safety net today, which is a latent re-occurrence of exactly this bug
class).

**R3 — `dt.frame_mismatch` (WARNING).** `frame_dt / substep_dt` should be a
clean-ish integer (the solver floors it into `step_per_frame`). Flag when the
remainder is large (silent frame-timing drift).

**R4 — `material.out_of_range` (WARNING).** `E`/`nu`/`density` outside the
material's profile band. Warn, don't block — exotic values are legitimate, but
they're the usual suspects when a sim misbehaves. Special-case the
`nu -> 0.5` singularity: as `nu` approaches `0.5` the `(1-2nu)` term explodes
the sound speed and collapses `cfl_dt`; flag `nu >= 0.49` as an ERROR.

**R5 — `damping.too_weak_for_stiffness` (WARNING + autofix).** Derived rule:
stiffer materials need stronger damping. Define a monotone mapping (see 4.4).
If `grid_v_damping_scale` is closer to 1.0 than the derived value, warn and
offer the stronger value.

**R6 — `bc.instant_injection` (WARNING).** Encodes the RECIPES.md lesson about
`meteor`/`uplift`: a `cuboid` Dirichlet collider whose volume overlaps the
model bbox at `t=0` injects instantaneous velocity → stress concentration →
`CUDA error 700`. Static geometric check between collider extent and sim_area;
recommend a non-zero `start_time` ramp.

### 4.4 Auto-derivation of stable params (extending the CFL idea)

Two derivations, both "only ever tighten":

**Damping from stiffness.** Pick a target so the per-substep velocity decay
roughly tracks the material's natural settling. A simple, defensible v1:

```python
def derive_damping(E):
    # stiffer -> more damping (smaller scale). Clamp to a sane band.
    # 0.999 (barely damped) for very soft, 0.93 for very stiff.
    return clamp(1.0 - 0.01 * log10(E / 1e3), 0.93, 0.999)
```

This is intentionally crude and documented as a *heuristic floor*, not a
physics result — its only job is to never hand back `>= 1.0` and to bias stiff
materials toward more damping. The real validation that the number is good is
Layer B/C.

**dt from CFL.** Reuse R2's exact formula. The autofix simply sets
`substep_dt = min(recipe_dt, cfl_dt)` — identical to the server, but applied
*before* the run so the cost/quality implication is visible to the user.

### 4.5 Auto vs user-overridable matrix

| Rule | Default action | Override mechanism |
|---|---|---|
| R1 damping disabled | **autofix** (set <1.0) | `allow_undamped: true` keeps recipe value |
| R2 dt above CFL | **autofix** (clamp) | already overridable via `--no_cfl_override`; linter still warns |
| R3 frame mismatch | warn only | n/a |
| R4 material range | warn only | n/a (informational) |
| R4b nu→0.5 | **error** | `allow_incompressible: true` |
| R5 weak damping | warn + offer | accept/ignore |
| R6 instant injection | warn | accept (server may still 700) |

Overrides are top-level recipe keys (e.g. `allow_undamped`), so they're
self-documenting in the saved JSON and travel with the recipe. The linter
records *that* an override was used in the structured response and in the
persisted `recipe.json`, so a later "why did this diverge?" investigation can
see the human chose to bypass a rule.

### 4.6 Wiring into the API

- **`PUT /api/recipes/<name>` (save):** run `lint()`, return the report
  alongside the saved payload (`{name, source, data, lint: {...}}`). **Never
  block a save** — saving an in-progress recipe is legitimate. The workbench
  shows warnings/errors inline so the user fixes them before running.

- **`POST /api/runs` (run):** insert a step between cap-check (step 2) and the
  sim_area geometry check (step 3) in `api/runs.py`:

  ```python
  report = recipe_lint.lint(cap_input)
  effective = recipe_lint.apply_autofixes(cap_input, report,
                                          overrides=cap_input)  # honors allow_*
  if report.errors and not overridden(report, cap_input):
      raise_validation_error(
          kind="validation.recipe_unstable",
          message=report.errors[0].message,
          details={"findings": [asdict(f) for f in report.findings],
                   "trace_id": trace_id})
  ```

  Autofixes flow into `effective_recipe`, which then continues through the
  existing `translate_sim_area_if_local` path — so the *fixed* values are what
  the sim and the persisted `recipe.json` see. `dry_run=true` returns the full
  lint report without spawning, giving the workbench a free "check my recipe"
  button and powering a library compatibility matrix.

- **CLI linter / CI gate:** `python -m gsfluent.core.recipe_lint server/recipes/*.json`
  exits non-zero on any error. **This alone would have caught the original bug
  across all 8 files in one command** and belongs in CI so a bad built-in can
  never ship again.

---

## 5. Layer B — cheap dynamic pre-check (optional)

Static rules can't catch instabilities that depend on the *initial particle
configuration* (e.g. a thin overhang that buckles). Two tiers:

**Tier 1 — heuristic margin (free, fold into Layer A).** Report the CFL margin
`cfl_dt / substep_dt` and a crude energy-injection estimate from gravity +
BCs. Margin `< 1.2×` → warn "little headroom; small param changes may
diverge." Pure arithmetic; no model, no GPU.

**Tier 2 — short trial run (opt-in, seconds).** Add a `trial_substeps: int`
path to `MPMSimulationEngine.run()` that runs the *real* solver for ~100–300
substeps (a fraction of one frame), then aborts and reports:
- any non-finite particle position/velocity (instant fail),
- max grid-velocity growth ratio across the trial (super-linear growth → will
  diverge),
- peak GPU memory (OOM predictor for the cap layer).

This reuses the existing subprocess/stderr-classifier machinery — it's the
same `run()` with an early exit and a finiteness probe. Cost is bounded
(`trial_substeps`), so it can gate an expensive full run cheaply. Make it
opt-in (`POST /api/runs {pretrial: true}`) and surface the result as a new
event (`sim.pretrial_ok` / `error.sim.pretrial_unstable`). **Defer to a later
phase** — it has real GPU cost and the static linter captures the known cases.

---

## 6. How it complements the existing fail-loud guard

`check_sim_stability()` stays exactly as-is. The relationship:

| | Catches | Cost | When |
|---|---|---|---|
| Layer A (lint) | *known* unstable param combos & no-ops (the bug we hit) | ~0 | pre-run, pre-save, CI |
| Layer B (trial) | config-dependent blowups static rules can't see | seconds | pre-run (opt-in) |
| Layer C (guard) | *everything else* — genuinely novel divergence | full run | post-run |

The guard becomes the **backstop for the unknown**, not the first line of
defense. When the guard *does* fire after A+B pass, that's a signal to add a
new linter rule — i.e. Layer C feeds Layer A over time. Concretely: extend the
`SimUnstableRecipeError` message to suggest `gsfluent recipe-lint <name>` so
the operator immediately runs the static check, and log the diverged recipe's
params so a new rule can be written. The `mpm_error_patterns.yaml` classifier
(CFL/NaN/illegal-access → `sim.unstable_recipe`) is unchanged and orthogonal —
it classifies *crashes*; the linter prevents *silent* divergence.

---

## 7. Phased implementation path (smallest valuable first)

**Phase 0 — CLI linter + R1/R2 only (highest value, ~half a day).**
- New `recipe_lint.py` with the data model, the material table, and just
  **R1 (damping disabled)** and **R2 (dt above CFL)** plus their autofixers.
- A `__main__` CLI that lints the built-in library and exits non-zero on error.
- Add it to CI.
- *This alone closes the exact bug we hit, for all current and future
  built-ins, with zero GPU cost and no API change.* Ship it first.

**Phase 1 — wire the linter into `POST /api/runs`.**
- Insert the lint+autofix step in `api/runs.py` between cap-check and sim_area.
- New error kind `validation.recipe_unstable`; autofixes flow into
  `effective_recipe`; `dry_run` returns the report.
- Override keys (`allow_undamped`, `allow_incompressible`).

**Phase 2 — save-time linting + workbench surface.**
- `PUT /api/recipes/<name>` returns the lint report (non-blocking).
- Workbench shows inline warnings/errors and offers "apply suggested fix."

**Phase 3 — broaden the rule set.**
- R3–R6 (frame mismatch, material range, weak-damping derivation, instant
  injection). R5 brings the stiffness→damping auto-derivation.

**Phase 4 — Layer B Tier 2 (opt-in short trial run).**
- `trial_substeps` path in `MPMSimulationEngine.run()`; `pretrial` request
  flag; new pretrial events. Only after the static layers are proven.

**Phase 5 — feedback loop.**
- When Layer C fires, log params + point operators at the linter; codify
  recurring divergences into new R-rules.

---

## 8. Top recommendations (summary)

1. **Build a pure `recipe_lint.py` and ship the CLI + CI gate first (Phase 0)
   with just two rules — `damping.disabled` and `dt.above_cfl`.** That one
   command, run over `server/recipes/*.json`, would have caught the original
   bug in every file at zero GPU cost. Highest value, smallest surface.

2. **Auto-derive, don't merely reject — and only ever tighten.** Extend the
   solver's existing "clamp dt to CFL, never relax" pattern to damping
   (`grid_v_damping_scale` autofixed below 1.0 from stiffness) and surface the
   CFL clamp *before* the run. Auto-fixes must never make a recipe less stable
   than the author asked.

3. **Keep stability rules separate from geometry validation.** Physics/numeric
   rules need only the recipe dict (pure, three contexts: save, run, CI);
   sim_area/model checks need the model on disk. Don't merge them into
   `recipe_validation.py`.

4. **Make the fail-loud guard the backstop, and close its loop.** Layer A/B
   catch the *known*; `check_sim_stability` catches the *unknown*. When the
   guard fires, point the operator at the linter and log params so each
   novel divergence becomes a new static rule.

5. **Close the `--no_cfl_override` (sim_fast) hole.** That fast path disables
   the server's dt clamp and currently has no dt safety net — a latent repeat
   of this exact bug class. The pre-run linter's R2 covers it; make the linter
   non-overridable when `sim_fast` is on, or refuse `--no_cfl_override` unless
   `substep_dt <= cfl_dt`.
