# Upstream patches to GaussianFluent

This directory captures hand-applied patches to the upstream
[GaussianFluent](https://github.com/whc1992/GaussianFluent) repo. They live
on the sim host — **not** inside this repo's tree, because GaussianFluent
is a separate codebase we don't fork. The patched files are:

| Sim-host file | Repo snapshot | Patches |
|---|---|---|
| `gs_simulation/watermelon/gs_simulation_building.py` | `gs_simulation_building.patched.py` | 1–6 (driver/IO) |
| `mpm_solver_warp/mpm_solver_warp.py` | `mpm_solver_warp.patched.py` | 7 (surface-collider slip/separate) |
| `mpm_solver_warp/mpm_utils.py` | `mpm_utils.patched.py` | 8 (snow — documented TODO, **no physics change**) |

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
SIM_BUILD="$GSFLUENT_SIM_HOME"/gs_simulation/watermelon/gs_simulation_building.py
grep -c "particle_F\|substep_dt clamp\|output_rot" $SIM_BUILD
# Expected: 6 or more. If 0, none of the patches are applied.
# (output_rot present => Patch 6 / Track-1 GPU rotation export is deployed.)

# Patch 7 (surface-collider slip fix) — in the solver, not the driver:
SOLVER="$GSFLUENT_SIM_HOME"/mpm_solver_warp/mpm_solver_warp.py
grep -c "fix:collider" $SOLVER
# Expected: 1 if Patch 7 is applied; 0 means slip/separate are still full-stick.
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

## Patch 6 — `--output_rot` flag + per-frame GPU polar-rotation export (Track-1)

**Why:** The fuser's Track-1 per-splat rotation should come from the sim's
**already-GPU-computed** per-particle polar rotation `R = polar(F)`
(`compute_R_from_F` / `export_particle_R_to_torch`), NOT a CPU Kabsch SVD
re-derivation in the fuser. This patch wires the OUTPUT of that rotation; the
fuser composes it onto the rest quaternion (no SVD, ~2× faster fuse, exact R).

**Add a new CLI flag** near the other Phase B/C flags:
```python
parser.add_argument("--output_rot", action="store_true",
                    help="[particle_R / Track-1] write each particle's polar "
                         "rotation as a unit quaternion (rot_w..rot_z) per frame.")
```

**Add a module-level `_rotmats_to_quats_wxyz(rmats)` helper** (host-side numpy,
Shepperd matrix->quaternion; see the patched file).

**Extend `_b3_write_ply`** to take an optional `rot_np=(N,4)` and append four
`property float rot_w/rot_x/rot_y/rot_z` rows AFTER xyz (and after cov if both
are on — fixed column order: xyz, cov(6), rot(4)).

**At the per-frame async write site**, snapshot R alongside positions:
```python
_rot_host = None
if args.output_rot:
    _rot_flat = mpm_solver.export_particle_R_to_torch(device=device)
    _rot_mats = _rot_flat.view(-1, 3, 3).detach().cpu().numpy().astype(np.float64, copy=False)
    _rot_host = _rotmats_to_quats_wxyz(_rot_mats).astype(np.float32, copy=False)
# clip pos/cov/rot to the shortest row count, then:
_io_futures.append(_io_executor.submit(_b3_write_ply, _ply_filename, _pos_host, _cov_host, _rot_host))
```

**Extend the frame-0 rewrite** (Patch 5) to also emit rot when `--output_rot`
(frame 0: F=I -> R=I -> quaternion (1,0,0,0), the rest reference the fuser
deltas against). See the patched file's `[particle_F/R]` markers.

**Server-side compute is already present** (no GaussianFluent solver change
needed): `mpm_solver_warp/mpm_utils.py::compute_R_from_F` and
`mpm_solver_warp/mpm_solver_warp.py::export_particle_R_to_torch` already exist.

**Consumed by:** `gsfluent/core/fusers/knn_kabsch.py` — when sim plys carry
`rot_*`, the fuser uses the GPU sim-R path (gather bound particles' R via the
frame-0 KNN map, delta vs frame-0 R, weighted-quaternion blend, compose onto
rest quat). Absent `rot_*` -> CPU-Kabsch fallback. `mpm.py::_build_sim_argv`
now passes `--output_rot`.

**Validated** (2026-05-27, GPU 6, jelly/cluster_6_15, frame_num=30): R=0° at
frame 0, 6-12° median per-particle on deforming frames, all unit + finite.
Fuser GPU sim-R 1.05 s/frame vs CPU Kabsch 2.17 s/frame (~2×); per-splat
output quaternions agree with CPU Kabsch to |dot| 0.98-0.99.

---

## Patch 7 — surface-collider `slip`/`separate` must keep the projected velocity

**File:** `mpm_solver_warp/mpm_solver_warp.py` — the `collide` kernel inside
`add_surface_collider` (~L1007, the `dotproduct < 0.0` else-branch that handles
`surface_type` 1=slip and 2=separate).

**Symptom (when missing):** *every* surface collider behaves as full-stick
regardless of its `surface` setting. A `slip` plane silently freezes particles
on contact instead of letting them slide tangentially; `separate` likewise
cancels all motion instead of only the inward normal component. This affects
production: `server/recipes/demolition.json` declares a `surface_collider` with
`"surface": "slip"` (normal `[0,0,1]`, friction 0) — its slip plane was secretly
sticky.

**Cause:** the branch correctly computes the friction-projected velocity into a
local `v` (slip: `v - (v·n)n`; separate: `v - min(v·n,0)n`; then Coulomb
friction), but the **final line overwrites the grid node with `vec3(0,0,0)`**
unconditionally — discarding the very `v` it just computed. Only `sticky`
(`surface_type == 0`, handled in its own earlier branch) should zero the node.

**Original (the offending tail of the else-branch):**
```python
                        if normal_component < 0.0 and wp.length(v) > 1e-20:
                            v = wp.max(
                                0.0, wp.length(v) + normal_component * param.friction
                            ) * wp.normalize(
                                v
                            )  # apply friction here
                        state.grid_v_out[grid_x, grid_y, grid_z] = wp.vec3(
                            0.0, 0.0, 0.0
                        )
```

**Patched (write the projected velocity instead of zeroing):**
```python
                        if normal_component < 0.0 and wp.length(v) > 1e-20:
                            v = wp.max(
                                0.0, wp.length(v) + normal_component * param.friction
                            ) * wp.normalize(
                                v
                            )  # apply friction here
                        # [fix:collider] write the friction-projected velocity.
                        state.grid_v_out[grid_x, grid_y, grid_z] = v
```

(`sticky` is untouched — it already does `grid_v_out = vec3(0,0,0)` in the
`param.surface_type == 0` branch above. `cut`, type 11, is also untouched.)

**Validated** (2026-05-27, GPU 0): a jelly block with +x tangential velocity and
−z gravity settling onto a `slip` plane (normal +z at z=0.5), 20×50 substeps.
Mean tangential velocity of particles in the contact band:

| Solver | mean vx (near plane) | mean x-displacement |
|---|---|---|
| original (buggy, full-stick) | **0.68** | 0.122 |
| patched (slip preserved) | **1.81** | 0.200 |

The patched plane lets particles keep ~2.7× more tangential velocity and slide
~1.6× farther in x — the expected slip behavior. Normal-direction velocity
(vz ≈ −0.31 vs −0.34) is essentially unchanged, confirming only the tangential
component was being wrongly killed.

**Deploying it requires:** drop `mpm_solver_warp.patched.py` over
`<GaussianFluent>/mpm_solver_warp/mpm_solver_warp.py` on the sim host (back up the
original first), then restart the backend / clear any cached CUDA-graph capture
(the collider is a `grid_postprocess` closure baked into the captured graph, so a
fresh sim process is needed — no source change to this repo's engine wrapper).
No recipe, fuser, or codec change. Solver-physics change → human review before deploy.

---

## Patch 8 — snow (`material == 4`): documented TODO, NOT yet implemented

**File:** `mpm_solver_warp/mpm_utils.py` — `compute_stress_from_F_trial`, the
return-map dispatch (`if model.material[p] == 1 ... elif ... == 7`) and the
stress dispatch below it.

**Finding (confirmed):** there is **no `material == 4` branch** in either
dispatch. `material_2_num` maps `"snow"` → int 4, but with no branch snow falls
through to the elastic `else` (`particle_F = particle_F_trial`) and is rendered
with the FCR jelly stress — i.e. **snow currently behaves exactly like jelly**,
despite distinct `MATERIAL_DEFAULTS` (`xi=10, hardening=5, alpha_0=-0.01`).

**Decision: NOT implemented — left as a precise TODO** (per the "don't guess a
physics model" instruction). The faithful Stomakhin-2013 snow model is
*underdetermined* in this fork:

1. **No `theta_c` / `theta_s`.** Snow's return map is defined by clamping the SVD
   singular values to `[1−theta_c, 1+theta_s]` (critical compression / stretch).
   Neither parameter exists on `MPMModelStruct`, in any recipe, or in
   `material_defaults.py`. Without them the model has no meaning.
2. **`particle_Jp` is taken.** It already stores the Cam-Clay `logJp` hardening
   state (init `alpha_0`); snow needs `Jp` as the accumulated plastic volumetric
   determinant — conflicting semantics.
3. **No base `mu0/lam0`.** `mu[p]/lam[p]` are derived once from `E/nu`; snow
   hardening rescales them every substep by `exp(xi·(1−Jp))`, which needs a stored
   base to rescale *from* (rescaling in place compounds across substeps).
4. **`hardening` is a boolean flag** elsewhere (`if model.hardening == 1`); the
   snow default `hardening=5` has no defined meaning under that convention.

The `mpm_utils.patched.py` snapshot carries a `TODO[snow / material==4]` comment
block at the dispatch site spelling out exactly what to add (theta_c/theta_s
plumbing, base moduli, a dedicated snow Jp, the `elif ==4` return map + matching
FCR stress branch). **This patch is documentation only — it changes no physics**
(the patched file is byte-equivalent to upstream except for the comment). It can
be deployed harmlessly or skipped; it exists so the gap and its requirements are
version-controlled.

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
