# Sim Recipes

Curated config JSONs for `sim_one.sh` and the browser workbench. Each recipe is a complete `gs_simulation_building.py` config — drop a new `<name>.json` here and the workbench dropdown picks it up at next launch.

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
| `demolition` | Sequential particle release — building collapses top-down | plasticine | Dramatic; ~2 min on RTX 5070 |
| `earthquake` | Base shaking — 4 cuboid colliders drive the floor laterally | watermelon | Classic seismic test |
| `meteor` | Fast-moving cuboid impacts the building | watermelon | One-shot impact |
| `uplift` | Ground rises into the building from below | watermelon | Slow-motion pop-up |

All recipes ship with `frame_num=150` (≈ 5 sec @ 30 fps target) and use `bounding_box + surface_collider` for global containment. Validated on RTX 5070 Laptop with 100k–200k particles via Warp 1.12.

## Picking from the workbench

Open `./run-workbench.sh` → Sim tab → Recipe → **Preset** dropdown. Built-ins appear first; your own saved presets show with a `★` prefix.

## Adding your own recipe

Two ways:

1. **From the workbench (no JSON editing):** pick a preset, tweak the sliders, type a name in "Save as preset", click Save. Appears in the dropdown as `★ <name>` next session.
2. **By hand:** `cp jelly.json mything.json`, edit, `./run-sim.sh <model> --recipe mything`.

## Key parameters

- `n_grid` — MPM grid resolution; higher = more detail but quadratically more memory
- `substep_dt` — inner integration step; smaller = more stable but slower (5e-5 for stiff materials, 1e-4 for soft)
- `frame_num` — total animation frames at `frame_dt` spacing
- `g` — gravity (x, y, z); default `[0, 0, -15]` (negative-Z is down in sim space)
- `material` — must be one of `jelly`, `metal`, `sand`, `foam`, `snow`, `plasticine`, `watermelon`. Other params (E, ν, yield_stress) should match the chosen material's expected ranges
- `boundary_conditions` — list. `bounding_box` and `surface_collider` are always there; scenarios add `cuboid` (Dirichlet collider) or `release_particles_sequentially` (collapse)

## Known broken on Warp 1.x (laptop)

These BCs hit a `@wp.struct` field-pointer propagation issue on Warp 1.12 and are NOT shipped as recipes:

- `tornado` (`enforce_particle_velocity_rotation`) — would twist the building
- `cluster_impact` / `impulse_strong` — direct force application

They work on the A100 server with the original Warp build. If you need them, copy the JSON in by hand and run on the server.
