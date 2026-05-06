# Sim Recipes

Curated config JSONs for `sim_one.sh`. Each recipe is a complete `gs_simulation_building.py` config — drop in any new one as `<name>.json` and `sim_one.sh --recipe <name>` picks it up.

## Available recipes

| Name | What it does | Particles default | Boundary type | Status |
|---|---|---|---|---|
| `jelly` | Soft body wobble / jelly-cluster oscillation | 500k | particle damping | validated on 5070 laptop |
| `demolition` | Building collapse via sequential particle release | 500k | `release_particles_sequentially` | validated on 5070 laptop |

## Adding your own recipe

1. Copy an existing recipe: `cp jelly.json mything.json`
2. Edit the parameters you care about (Young's modulus, gravity, frame_num, n_grid, BC type)
3. `sim_one.sh model/your_model --recipe mything`
4. If it works well, add a row above and commit it

## Common knobs

- `n_grid` — MPM grid resolution; higher = more detail but quadratically more memory
- `substep_dt` — inner integration step; smaller = more stable but slower
- `frame_num` — total animation frames at `frame_dt` spacing
- `boundary_conditions` — list of BCs; `release_particles_sequentially` (works on Warp 1.x) and `particle_damping` are the validated paths
- `material` — `"jelly"`, `"sand"`, etc. (see `mpm_solver_warp/`)

## Known broken on Warp 1.x (laptop)

These configs work on the A100 server but currently don't on the laptop due to a `@wp.struct` field-pointer propagation issue:

- `cluster_impact.json` — impact BC
- `R4.I_impulse_strong.json` — impulse forcing

Don't ship these as recipes until the impact path is fixed. Memory: `project_phase18_gaussianfluent.md`.
