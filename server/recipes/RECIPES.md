# Sim Recipes

> **Destruction scenarios are now COMPOSED, not hand-authored.** The curated
> scenarios (earthquake, wrecking — verified-on-video) live in
> `gsfluent/authoring/scenarios.py` and are generated on demand via
> `POST /api/compose` (material × scenario × building → flat recipe). The flat
> JSONs in *this* directory are the **starter material demos** and verified
> flat fallbacks only. See `gsfluent/authoring/` for the structured system.

The flat config JSONs here are consumed by the server-side simulation
(`gs_simulation_building.py` on the GPU server). They are pure configuration —
materials, boundary conditions, gravity, integration params; never code. The
same JSON (composed or flat) is shipped to the server when a run is submitted.

## Flat builtin recipes (material starter demos + fallback)

| Name | What it does | Material | Notes |
|---|---|---|---|
| `jelly` | Soft body wobble / gentle bounce | jelly | Default starter; very forgiving |
| `metal` | Stiff metal — dents under load, holds shape | metal | E=50000, density=3 |
| `sand` | Granular collapse into a pile | sand | No cohesion; building slumps |
| `foam` | Light squishy foam, slow recovery | foam | density=0.3, E=1000 |
| `plasticine` | Plastic clay flow / permanent deformation | plasticine | Slow drape & squash |
| `demolition` | Sequential particle release — top-down collapse | plasticine | R10-ported flat fallback (no composer scenario yet) |

These are **material-only looks** (+ the demolition fallback): they have no
composer scenario, so they stay as flat recipes reachable via "Browse library".

## Composed scenarios (the curated, verified set)

Generated via the composer (`POST /api/compose`), source of truth in
`gsfluent/authoring/scenarios.py`:

| Scenario | What it does | Recommended material | Verified |
|---|---|---|---|
| `earthquake` | Base-shake plate → tower collapses into rubble | watermelon (soft) | ✅ on video |
| `wrecking` | Mid-height impact, pinned base → shears apart | watermelon (soft) | ✅ on video |
| `topple` | Top third hauled +y (thin axis) → tower falls like a domino | watermelon (soft) | ✅ on video |
| `burst` | 4 core slabs blow the mid-section outward → explodes apart | watermelon (soft) | ✅ on video |
| `demolish` | Two impactors cut the legs → tower crashes down + breaks up | watermelon (soft) | ✅ on video |

All five are **active lateral destabilization** (shake / impact / drag / explode /
leg-cut) — the material yields sideways into open grid, which is what the solver
allows. Two mechanisms were tried and DROPPED as physically unachievable here:
`implode` (down-drag of the core) and a vertical-press `crush` both inject a
DOWNWARD imposed velocity that traps the near-incompressible body against the
floor → pressure spike → grid escape (CUDA 700, frames 15-25). A pure-gravity
pancake also fails: the tower self-supports under gravity (every material,
pinned or free). `demolish` delivers the "building collapses + breaks" goal that
`crush` was reaching for, via the robust lateral cut instead.

**Key finding #1 (material):** buildings collapse with the *soft* `watermelon`
material (E=2000, no yield), not the stiff `plasticine` default — same scenario
+ stiff material just ejects/bends. Material is the lever; the composer makes it
a one-axis swap. The violent scenarios (burst/wrecking/earthquake) crash the
*stiff* materials (jelly/plasticine) with grid-escape — that's physics, not a
bug, so they recommend watermelon.

**Key finding #2 (geometry, 2026-05-29):** the cube-frame `bbox` in
`authoring/buildings.py` MUST be the true normalized extent (measured by
replaying the sim's `transform2origin`), not a guess. `cluster_6_15` is a TALL
slender slab (z-span 1.0, x 0.60, y 0.36 — thin). An earlier guessed bbox was
2–3.6× too wide, which sized every lateral BC wrong (the implode core column
came out wider than the building and ejected it) and produced the false "squat,
can't topple" conclusion. With the real bbox, topple is viable and all lateral
BCs are building-relative.

### Removed (2026-05-29)

`earthquake.json`, `wrecking.json` — superseded by the composer (byte-identical
output, verified). `wrecking_xl.json`, `collapse_fast.json`, `shatter.json` —
unverified spicy experiments that exceeded the composer's safety ceilings
(grid-escape velocity / un-merged fracture dependency); dropped to keep the
curated set trustworthy.

### Removed: `meteor`, `uplift`

The `meteor` (vertical impactor) and `uplift` (ground rising) scenarios
were dropped after headless tests on `cluster_6_15`. Both crash the
upstream MPM solver with `Warp CUDA error 700: illegal memory access`
when their cuboid BC overlaps existing geometry at `t=0` (instantaneous
velocity injection → stress concentration). Affected both watermelon and
R10's plasticine recipe variants. R10's historical run from April 2026
completed, but the current `gs_simulation_building.py` has drifted (Phase
A/B/C optimizations) such that these scenarios no longer run.

Re-enabling either would need either:
  - a real solver-side fix (sub-stepping near high-strain regions), or
  - rewriting the BC schedule so cuboids enter the scene gradually
    rather than spawning inside the model.

## Picking from the workbench

Open the React workbench (`./run-server.sh` on server + `./run-laptop.sh` on laptop) → Sim tab →
Recipe dropdown. Built-ins appear first; user-saved presets show with
a `★` prefix.

## Adding your own recipe

Two ways:

1. **From the workbench:** pick a preset, tweak the sliders, type a
   name in "Save as preset", click Save. The recipe is written to
   `work/_user_recipes/<name>.json` and appears in the dropdown as
   `★ <name>` next session.
2. **By hand:** `cp jelly.json mything.json`, edit, then load from the
   workbench dropdown.

Either way the recipe is sent to the server at submit time — local
recipe edits don't run any local sim.

## Key parameters

- `n_grid` — MPM grid resolution; higher = more detail but quadratically more memory
- `substep_dt` — inner integration step; smaller = more stable but slower (5e-5 for stiff materials, 1e-4 for soft)
- `frame_num` — total animation frames at `frame_dt` spacing
- `g` — gravity (x, y, z); default `[0, 0, -15]` (negative-Z is down in sim space)
- `material` — must be one of `jelly`, `metal`, `sand`, `foam`, `snow`, `plasticine`, `watermelon`. Other params (E, ν, yield_stress) should match the chosen material's expected ranges
- `boundary_conditions` — list. `bounding_box` and `surface_collider` are always there; scenarios add `cuboid` (Dirichlet collider) or `release_particles_sequentially` (collapse)

## Known broken on laptop Warp 1.x

These BCs hit a `@wp.struct` field-pointer propagation issue on Warp
1.12 and are NOT shipped as default recipes. They work on the A100
server with the canonical Warp 0.10 build, so submitting a run with
them is fine — only local-Warp validation breaks.

- `tornado` (`enforce_particle_velocity_rotation`) — would twist the building
- `cluster_impact` / `impulse_strong` — direct force application
