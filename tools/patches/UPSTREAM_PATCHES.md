# Upstream patches to GaussianFluent

This directory captures hand-applied patches to the upstream
[GaussianFluent](https://github.com/whc1992/GaussianFluent) repo. They live
at `<GaussianFluent>/gs_simulation/watermelon/gs_simulation_building.py`
on the sim host — **not** inside this repo's tree, because GaussianFluent
is a separate codebase we don't fork.

If you redeploy GaussianFluent (fresh clone, update, etc.), the patches
disappear unless reapplied. This document is the canonical record of
what changed and why.

The patched file (as of `2026-05-18`) is shipped here as
`gs_simulation_building.patched.py` — drop it in if you want the patched
behavior immediately. Or apply the five edits below by hand.

---

## Patch verification (quick check)

```bash
# On the sim host:
SIM_BUILD=$GSFLUENT_SIM_HOME/gs_simulation/watermelon/gs_simulation_building.py
grep -c "particle_F\|substep_dt clamp" $SIM_BUILD
# Expected: 5 or more. If 0, none of the patches are applied.
```

---

## Patch 1 — sim_area mask must also apply to `gaussians._*`

**Symptom (when missing):** `RuntimeError: The size of tensor a (572345)
must match the size of tensor b (683741)` at the importance-weighted
subsampling block.

**Where:** Inside the `if preprocessing_params["sim_area"] is not None:`
block, after the existing `rotated_pos`/`init_cov`/`init_opacity`/`init_shs`
get masked.

**Original lines (before patch):**
```python
        rotated_pos = rotated_pos[mask, :]
        init_cov = init_cov[mask, :]
        init_opacity = init_opacity[mask, :]
        init_shs = init_shs[mask, :]
```

**Patched lines (add after):**
```python
        # [fix] keep gaussians._* aligned with the masked init_* arrays —
        # without this, gaussians._scaling.shape[0] is still the
        # post-opacity-mask count while init_opacity is post-sim_area,
        # and the gauss_w line in Phase B.1 crashes.
        gaussians._xyz = gaussians._xyz[mask, :]
        gaussians._features_dc = gaussians._features_dc[mask, :]
        gaussians._features_rest = gaussians._features_rest[mask, :]
        gaussians._opacity = gaussians._opacity[mask, :]
        gaussians._scaling = gaussians._scaling[mask, :]
        gaussians._rotation = gaussians._rotation[mask, :]
```

---

## Patch 2 — gate `filter_gaussian_points_by_ellipsoid` behind `args.render_img`

**Symptom (when missing):** `Warp CUDA error 700: an illegal memory
access was encountered` immediately after `[PhaseA] graph captured.`,
crashing inside `filter_gaussian_points_by_ellipsoid`.

**Cause:** The call is unconditional but its only consumer is inside
`if args.render_img:` two lines later. It uses a watermelon-shaped
ellipsoid that's meaningless for other scenes, AND it's the next CUDA
sync point after a prior MPM op that left CUDA in an error state — so
it surfaces an unrelated async error.

**Original (around line 870):**
```python
        select_id = filter_gaussian_points_by_ellipsoid(
            tensor=mpm_init_pos,
            ellipsoid_center=torch.tensor([-0.1, 0.0, 0.0]),
            ellipsoid_axes=torch.tensor([0.22, 0.22, 0.22]),
            ellipsoid_greater=False)[1]
        if args.render_img:
            ...
```

**Patched:** move the `select_id = ...` line **inside** the `if args.render_img:`
block. It's only used there anyway.

---

## Patch 3 — clamp `substep_dt` to `min(recipe, CFL)`

**Symptom (when missing):** Recipes with a substep_dt larger than the
CFL bound are silently allowed → numerical blow-up (warp 700 mid-sim).
Recipes with a substep_dt smaller than CFL get **relaxed** to CFL
(making the sim less stable, not more).

**Where:** The block that decides the actual substep_dt.

**Original:**
```python
    cfl_dt = cfl * dx / evaluate_sound_speed_linear_elasticity_analysis(E, nu, rho)
    if args.no_cfl_override:
        print(f"[PhaseA] keeping config substep_dt={substep_dt:.3e} (CFL would suggest {cfl_dt:.3e})")
    else:
        substep_dt = cfl_dt
```

**Patched:**
```python
    cfl_dt = cfl * dx / evaluate_sound_speed_linear_elasticity_analysis(E, nu, rho)
    if args.no_cfl_override:
        print(f"[PhaseA] keeping config substep_dt={substep_dt:.3e} (CFL would suggest {cfl_dt:.3e})")
    else:
        new_dt = min(substep_dt, cfl_dt)
        print(f"[PhaseA] substep_dt clamp: recipe={substep_dt:.3e} cfl={cfl_dt:.3e} chosen={new_dt:.3e}")
        substep_dt = new_dt
```

---

## Patch 4 — `--output_cov` flag + per-frame cov export

**Why:** The fuse step's particle_F path needs per-particle covariance
per frame. Without this patch, sim plys only carry xyz and the fuse
falls back to K-NN (the ghost-prone path).

**Add a new CLI flag** in the argparse block near other Phase B/C flags:
```python
    parser.add_argument("--output_cov", action="store_true",
                        help="[particle_F] Also write 6-float upper-triangular "
                             "covariance per particle into each sim_NNNN.ply.")
```

**Extend `_b3_write_ply` to accept an optional `cov_np` argument** and,
when present, append `property float cov_00..cov_22` to the header and
interleave xyz + cov per row (9 floats × N).

**At the per-frame write site** (the `if args.async_io and args.output_ply ...`
branch), snapshot the cov to host alongside positions:
```python
_cov_host = None
if args.output_cov:
    _cov_flat = mpm_solver.export_particle_cov_to_torch()
    _cov_host = _cov_flat.view(-1, 6).detach().cpu().numpy().astype(np.float32, copy=True)
    # Clip to position row count if shapes differ.
    if _cov_host.shape[0] != _pos_host.shape[0]:
        n = min(_cov_host.shape[0], _pos_host.shape[0])
        _cov_host = _cov_host[:n]; _pos_host = _pos_host[:n]
_io_futures.append(_io_executor.submit(_b3_write_ply, _ply_filename, _pos_host, _cov_host))
```

See the patched file for the exact code (search for `[particle_F]` markers).

---

## Patch 5 — frame-0 cov rewrite

**Why:** `save_data_at_frame` (the upstream lib helper) writes frame 0's
ply without knowing about `--output_cov`. The fuse step detects cov
fields from `sim_plys[0]` — if it can't find them there, it falls back
to K-NN for the whole run.

**Where:** Right after the existing `save_data_at_frame(...)` call for frame 0.

**Add:**
```python
# [particle_F] Frame 0 was written by save_data_at_frame (the lib
# helper) which doesn't know about --output_cov. Rewrite in place so
# all frames have a consistent schema.
if args.output_ply and args.output_cov:
    _f0_path = os.path.join(directory_to_save, "sim_" + "0".zfill(10) + ".ply")
    _f0_pos = mpm_solver.mpm_state.particle_x.numpy().astype(np.float32, copy=True)
    _f0_cov = (mpm_solver.export_particle_cov_to_torch()
               .view(-1, 6).detach().cpu().numpy().astype(np.float32, copy=True))
    n_pf = min(_f0_cov.shape[0], _f0_pos.shape[0])
    if _f0_cov.shape[0] != _f0_pos.shape[0]:
        _f0_cov = _f0_cov[:n_pf]; _f0_pos = _f0_pos[:n_pf]
    try:
        if os.path.exists(_f0_path):
            os.remove(_f0_path)
        with open(_f0_path, "wb") as _fp:
            _fp.write(
                f"ply\nformat binary_little_endian 1.0\nelement vertex {len(_f0_pos)}\n"
                f"property float x\nproperty float y\nproperty float z\n"
                f"property float cov_00\nproperty float cov_01\nproperty float cov_02\n"
                f"property float cov_11\nproperty float cov_12\nproperty float cov_22\n"
                f"end_header\n".encode()
            )
            _fp.write(np.concatenate([_f0_pos, _f0_cov], axis=1)
                       .astype(np.float32, copy=False).tobytes())
    except Exception as _e:
        print(f"[particle_F] frame-0 cov rewrite failed: {_e}")
```

---

## Quick-apply recipe (drop-in replace)

For a sim host that runs the patched build:

```bash
SIM_BUILD_DIR=<your-GaussianFluent>/gs_simulation/watermelon
cp tools/patches/gs_simulation_building.patched.py \
   "$SIM_BUILD_DIR/gs_simulation_building.py"
```

This brings in all 5 patches at once. The patched file is a snapshot of
GaussianFluent at the commit it was forked from + our edits.

If GaussianFluent's upstream advances, the patched file will need a
merge — re-derive against the new upstream and update this directory.
