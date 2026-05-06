# gsfluent — drop a building, watch it deform

Self-contained physics simulator for 3D Gaussian Splatting scenes. Drop in a 3DGS-trained building (or any object), pick a recipe (jelly, demolition, ...), and watch it animate live in your browser. Built on MPM (Material Point Method) physics + viser/WebGL rendering.

> **Status:** Linux + NVIDIA GPU validated. Other platforms best-effort. Sim core is Python (PyTorch + NVIDIA Warp + Taichi); viewer is browser-side WebGL via viser, so the playback works on any OS with a browser.

[中文 README](README.md)

## Install (one-time, ~5 min)

```bash
git clone https://github.com/Qervas/gsfluent_pkg
cd gsfluent_pkg
./setup.sh
```

`setup.sh` creates a conda env (`gsfluent`), installs the right PyTorch/Warp/Taichi versions, and builds two CUDA extensions (diff_gaussian_rasterization + simple-knn) from the bundled gaussian-splatting submodule. Re-running it is safe.

Requirements:
- conda (Miniconda / Anaconda / Mambaforge)
- NVIDIA GPU with up-to-date driver (CUDA 12.x runtime — install via conda or system package)
- ~5 GB free disk for the conda env

## Use

### Browser workbench (recommended)

One page does everything — upload model, pick recipe, tweak parameters, run sim, watch results:

```bash
./run-workbench.sh
```

Opens at `http://localhost:8080`:

- **Model** — drop a `.ply` to upload, or paste a path to an existing model directory.
- **Recipe** — dropdown of `jelly` / `demolition` / any you've added. Params below auto-populate with that recipe's defaults.
- **Recipe parameters** — sliders / inputs for grid resolution, substep dt, frame count, camera angles, etc. Edits apply on the next Run.
- **Run** — click and the sim spawns in the background; the building deforms live in the 3D viewport as frames arrive.
- **Playback** — frame slider, pause, speed.

No terminal commands, no JSON editing required.

### CLI (for scripted use)

```bash
./run-sim.sh <building_path> --recipe demolition
```

`<building_path>` can be:

- a single `.ply` file (the script auto-wraps it into the directory layout the simulator expects), or
- a 3DGS model directory (with the standard `point_cloud/iteration_*/point_cloud.ply`).

The script runs the MPM sim (~150 frames at 1 fps on a laptop GPU), fuses each frame back with the original Gaussian attributes, and opens `localhost:8080` showing the building animating live as the sim computes.

Examples:

```bash
./run-sim.sh ~/projects/my_3dgs_model/             --recipe jelly
./run-sim.sh /tmp/quick_scan.ply                   --recipe demolition --particles 100000
./run-sim.sh ~/data/building_a/point_cloud.ply     --recipe jelly --output building_a_test
```

### Available recipes

| Recipe       | What it does                                | Notes                          |
| ------------ | ------------------------------------------- | ------------------------------ |
| `jelly`      | Soft body wobble / gentle bounce            | good for first try             |
| `demolition` | Building collapse via sequential release    | dramatic; ~2 min on RTX 5070   |

### Modify or add recipes

Recipes are JSON files in `tools/recipes/`. Each holds material parameters, boundary conditions, camera angles, and integration settings:

```bash
# Copy an existing recipe to start from
cp tools/recipes/jelly.json tools/recipes/my_recipe.json
# Edit the parameters
vim tools/recipes/my_recipe.json
# Use it
./run-sim.sh <building_path> --recipe my_recipe
```

Key parameters worth tweaking:

- `n_grid` — MPM grid resolution; higher = finer detail, VRAM grows quadratically
- `substep_dt` — inner integration step; smaller = more stable but slower
- `frame_num` — total animation frames at `frame_dt` spacing
- `boundary_conditions` — list of BCs. Validated paths today: `release_particles_sequentially` (collapse) and `particle_damping` (soft body)
- material parameters: density, Young's modulus, Poisson ratio, yield stress, ...

Full parameter reference: [`tools/recipes/RECIPES.md`](tools/recipes/RECIPES.md).

### Replay an old run without re-simulating

```bash
./run-viewer.sh work/fused/<run_name>/
```

Opens the same browser viewer pointed at any directory of `frame_NNNN.ply` files.

## Performance reality check

| Component | Speed              | Real-time?               |
| --------- | ------------------ | ------------------------ |
| Sim       | ~1 frame/sec @ 200k particles, RTX 5070 | No — physics is the bottleneck |
| Browser viewer | 24 fps playback target, 200+ fps render headroom | Yes — playback is real-time |

Live-mode is "live preview as the sim computes," not "real-time physics." The browser updates its slider as new frames arrive (~1/sec at 200k particles).

## CLI flags worth knowing

```
./run-sim.sh <input> [options]
  --recipe NAME         see tools/recipes/
  --particles N         MPM particle count (default 200000; lower = faster)
  --output NAME         output dir name (default auto: <model>_<recipe>_<date>)
  --no-viewer           sim only, no browser
  --port N              viewer port (default 8080)
  --dry-run             preview commands

./run-viewer.sh <dir> [--port N]
```

## Common issues

**`./setup.sh` says CUDA mismatch / extension build fails**
The bundled `env.yml` pins PyTorch with CUDA 12.4. If your driver is older/newer, edit the `--extra-index-url` line in `env.yml` to match (e.g. `cu121`, `cu128`, or `cu132` for Blackwell/sm_120) and re-run `./setup.sh` after deleting the env.

**Sim hangs forever on first launch**
First-run kernel compilation (Warp + Taichi) takes 30–90 seconds. Be patient. After the first run, kernels are cached at `~/.cache/warp/` and re-launches are fast.

**Taichi 1.7.4 hangs on Blackwell (sm_120) GPUs in `densify_grids`**
Sim_one.sh exports `GSFLUENT_TI_ARCH=cpu` by default to force Taichi onto CPU just for the (cheap) particle-fill step. The rest of the pipeline stays on CUDA via Warp.

**Browser shows nothing / blank canvas**
Wait 30 seconds for the first sim frame to land in the fused dir; the viewer is just polling. If it never shows: check `work/output/<run>/fuse.log` and `work/output/<run>/viewer.log`.

## Layout

```
gsfluent_pkg/
├── README.md          # 中文 (default on GitHub)
├── README.en.md       # this file
├── setup.sh           # one-time install (conda env + CUDA exts)
├── run-workbench.sh   # browser workbench (recommended entry)
├── run-sim.sh         # CLI: drop a model, get a browser tab (scripted use)
├── run-viewer.sh      # replay an existing run
├── env.yml            # conda spec
├── core/              # the sim code (gs_simulation, mpm_solver_warp, ...)
├── tools/
│   ├── sim_one.sh         # sim+fuse orchestrator (called by run-sim.sh)
│   ├── fuse_to_full_ply.py
│   ├── view_points.py     # browser viewer (point cloud)
│   ├── viewer_textured.py # browser viewer (gaussian splat — heavier, optional)
│   └── recipes/           # JSON config presets
└── work/              # generated at runtime: per-run sim + fused dirs
```

## Credits

- 3D Gaussian Splatting: Kerbl et al. 2023
- MPM-based sim: built on top of NVIDIA Warp + Taichi
- Browser viewer: viser

