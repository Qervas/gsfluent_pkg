# Sim Recipes

Curated config JSONs consumed by the server-side simulation
(`gs_simulation_building.py` on `your-server`). Drop a new `<name>.json`
here and the workbench's recipe dropdown picks it up at next launch.

Recipes are pure configuration — they describe materials, boundary
conditions, gravity, and integration parameters. They never carry
simulation code. The same JSON gets shipped to the server when a run
is submitted.

## Available recipes

### Materials (different physics, same building)

| Name | What it does | Material | Notes |
|---|---|---|---|
| `jelly` | Soft body wobble / gentle bounce | jelly | Default starter; very forgiving |
| `metal` | Stiff metal — dents under load, holds shape | metal | E=50000 (10× jelly), density=3 |
| `sand` | Granular collapse into a pile | sand | No cohesion; building slumps |
| `foam` | Light squishy foam, slow recovery | foam | density=0.3, E=1000 |
| `plasticine` | Plastic clay flow / permanent deformation | plasticine | Slow drape & squash |
| `snow` | Cohesive granular — clumps as it deforms | snow | Like sand but with hardening |

### Scenarios (forces / impactors acting on the building)

| Name | What it does | Material | Notes |
|---|---|---|---|
| `demolition` | Sequential particle release — building collapses top-down | plasticine | Dramatic |
| `earthquake` | Base shaking — 4 cuboid colliders drive the floor laterally | watermelon | Classic seismic test |
| `meteor` | Fast-moving cuboid impacts the building | watermelon | One-shot impact |
| `uplift` | Ground rises into the building from below | watermelon | Slow-motion pop-up |

All recipes ship with `frame_num=150` (≈ 5 sec @ 30 fps target) and use
`bounding_box + surface_collider` for global containment. Production
runs happen on the A100 server stack.

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
