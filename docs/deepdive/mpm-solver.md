# Deep Dive: gsfluent's MPM Physics Solver

A line-by-line teardown of the Material Point Method (MPM) solver that drives
gsfluent's `.gsq` sequences. Read-only research; no sim was run for this doc.

## Where the code lives

All paths are on the sim host (`sxyin-host`) unless marked `[repo]`.

| File | Role |
|------|------|
| `/data/yinshaoxuan/GaussianFluent/mpm_solver_warp/mpm_solver_warp.py` | `MPM_Simulator_WARP` class — substep orchestration (`p2g2p`, `p2g2p_capture_safe`), state allocation, BC factory methods. |
| `/data/yinshaoxuan/GaussianFluent/mpm_solver_warp/mpm_utils.py` | **The kernels.** All `@wp.kernel` / `@wp.func` — P2G, G2P, stress, return-mapping, grid update. (The task brief said `warp_utils.py`; the real kernel file is `mpm_utils.py`.) |
| `/data/yinshaoxuan/GaussianFluent/mpm_solver_warp/warp_utils.py` | `@wp.struct` definitions (`MPMModelStruct`, `MPMStateStruct`, collider structs) + torch↔warp aliasing helpers. |
| `/data/yinshaoxuan/GaussianFluent/mpm_solver_warp/engine_utils.py` | I/O (PLY/H5 frame dump). No physics. |
| `/data/yinshaoxuan/GaussianFluent/gs_simulation/watermelon/gs_simulation_building.py` | The driver: per-frame loop, CFL clamp (~L594), substep count, CUDA-graph capture. |
| `/data/yinshaoxuan/GaussianFluent/utils/decode_param.py` (`set_boundary_conditions`, L248) | Maps recipe BC dicts → solver `add_*` calls. |
| `server/gsfluent/core/sim_engines/mpm.py` `[repo]` | Subprocess orchestration + stderr classifier. Passes `--graph_capture` in fast mode; relies on the solver's own CFL clamp. |
| `server/gsfluent/schemas/material_defaults.py` `[repo]` | Per-material `E/nu/density/yield_stress/...` table. |
| `server/gsfluent/core/recipe_lint.py` `[repo]` | Static guards: CFL bound (R2), `grid_v_damping_scale >= 1.0` no-op (R1). |

Lineage note: this is a fork of the **PhysGaussian** MPM solver (the
`update_cov_with_F` covariance machinery and the FCR/StVK/Drucker-Prager/CamClay
constitutive set are the PhysGaussian signature). gsfluent layered on
`mixed_precision` (PhaseB.2 fp16 sidecars), sort-by-cell P2G (PhaseC.2), CUDA-graph
capture (PhaseA), and a CFL clamp.

---

## 1. Transfer scheme: MLS-MPM kernel, with a FLIP/PIC default (not APIC)

### Interpolation
Quadratic B-spline (3×3×3 stencil), the MLS-MPM standard. In every P2G/G2P kernel
(e.g. `p2g_flip_pic_with_stress`, `mpm_utils.py:598`):

```python
grid_pos = particle_x * inv_dx
base_pos  = int(grid_pos - 0.5)          # truncation, valid since coords ∈ [0, grid_lim]
fx = grid_pos - base_pos
wa = 1.5 - fx ; wb = fx - 1.0 ; wc = fx - 0.5
w  = [ 0.5*wa², 0.75 - wb², 0.5*wc² ]    # the three quadratic B-spline weights
dw = [ fx-1.5, -2(fx-1.0), fx-0.5 ]      # weight derivatives (per axis)
weight  = w[0,i]*w[1,j]*w[2,k]
dweight = compute_dweight(...) * inv_dx  # ∇w via product rule (mpm_utils.py:504)
```

`compute_dweight` builds the gradient by the product rule across axes and scales by
`inv_dx`. This is exactly the MLS-MPM quadratic kernel from Hu et al. 2018.

### Two transfer paths (selected by `flip_pic` flag, default **True**)

**(a) FLIP/PIC blend — the production default** (`p2g_flip_pic_with_stress` +
`g2p_flip`). This is the path used for all `.gsq` recipes (the driver always passes
`flip_pic=True`, default `flip_pic_ratio=0.80`; sand recipe uses 0.7).

P2G scatters momentum and internal force to *separate* grid buffers:
```python
v_in_add  = weight * mass * particle_v        # → grid_v_in   (pure momentum)
v_out_add = weight * dt * elastic_force        # → grid_v_out  (impulse from stress)
elastic_force = -vol * stress * dweight        # ∂(internal force) per node
grid_m += weight * mass
```
Note P2G here carries **no affine `C` term** — velocity is transferred PIC-style
(plain `weight*mass*v`), and the affine reconstruction happens entirely on the G2P
side. `grid_normalization_and_gravity` then forms `v_out = (v_in + v_out)/m + dt*g`.

G2P (`g2p_flip`, `mpm_utils.py:786`) does the FLIP/PIC blend:
```python
old_v += grid_v_in[node] * weight / grid_m[node]   # PIC reconstruction of pre-update v
new_v += grid_v_out[node] * weight                  # post-update grid velocity
...
v_new = flip_pic_ratio * v_old                       # FLIP: carry old particle v
      + new_v                                         # + new grid velocity
      - flip_pic_ratio * old_v                        # - interpolated old grid v  (= FLIP increment)
x    += dt * new_v                                    # advect by the PIC velocity (stable)
```
So `flip_pic_ratio=1` is pure FLIP (energetic, noisy), `0` is pure PIC (dissipative,
stable). 0.8 is the usual sweet spot. **Crucially `C` is never reconstructed in
`g2p_flip`** — `particle_C` is left untouched on the FLIP path, so `C` only matters
for the APIC path below.

**(b) APIC path** (`p2g_apic_with_stress` + `g2p`, `flip_pic=False`, *not* the
default). Here P2G adds the affine term `C*dpos` to the transferred velocity:
```python
C = (1 - rpic_damping)*C + (rpic_damping/2)*(C - Cᵀ)   # RPIC blend
if rpic_damping < -0.001: C = 0                          # → pure PIC
v_in_add = weight*mass*(v + C*dpos) + dt*elastic_force
```
and `g2p` reconstructs `C` from the grid via the APIC B-matrix:
```python
new_C += outer(grid_v, dpos) * (weight * inv_dx * 4.0)   # APIC's D⁻¹ = 4*inv_dx² for quadratic
```
The `* 4.0 * inv_dx` is the inverse inertia tensor `D⁻¹` for the quadratic kernel —
the textbook MLS-MPM/APIC affine reconstruction.

### B-matrix / affine / damping levers
- **`C` (particle_C, mat33)**: the APIC affine velocity matrix. Reconstructed only
  on the APIC G2P path.
- **`rpic_damping`** (default 0.0): `0` = standard APIC; in `(0,1)` it damps the
  symmetric part of `C` toward the antisymmetric (rotational) part — **RPIC**
  (rotation-only, sheds the shear modes that cause APIC ringing); `< -0.001` → pure
  PIC (zeroes `C`). Only read on the APIC path.
- **`flip_pic_ratio`** (default 0.80): FLIP/PIC blend on the default path.

**Bottom line:** the kernel math is **MLS-MPM** (quadratic B-spline + force via
stress·∇w). The default *transfer* is a **FLIP/PIC blend**, not APIC. APIC+RPIC
exists as an alternate path but isn't the production default. Stress is integrated
through grid-node forces (`-vol*stress*dweight`), the MLS-MPM weak form.

---

## 2. Constitutive models — elasticity + return-mapping plasticity

Two-stage per substep:
1. **`compute_stress_from_F_trial`** (`mpm_utils.py:836`): apply return mapping to
   `F_trial` → elastic `F`, then compute Kirchhoff stress `τ` from `F`. SVD `F = UΣVᵀ`
   via `wp.svd3` (Warp's analytic 3×3 SVD) is used by nearly every branch.
2. The stress feeds P2G as `elastic_force = -vol * τ * ∇w`.

Material is per-particle (`model.material[p]`, an int array), dispatched by integer:

| `material` int | Name | Elasticity (stress fn) | Plasticity (return map) |
|---|---|---|---|
| 0 | jelly | Fixed-Corotated (`kirchoff_stress_FCR`) | none (elastic) |
| 1 | metal | StVK-Hencky (`kirchoff_stress_StVK`) | **von Mises** (`von_mises_return_mapping`) |
| 2 | sand | log-strain (`kirchoff_stress_drucker_prager`) | **Drucker-Prager** (`sand_return_mapping`) |
| 3 | foam | StVK (placeholder) | **viscoplastic StVK** (`viscoplasticity_return_mapping_with_StVK`) |
| 4 | snow | *(falls to elastic FCR; no dedicated branch present)* | none active |
| 5 | plasticine | FCR | **von Mises + damage/softening** (`von_mises_return_mapping_with_damage`) |
| 7 | watermelon | Neo-Hookean-Borden (`kirchoff_stress_neoHookeanBoarden`) | **Non-Associative Cam-Clay** (`NonAssociativeCamClay_return_mapping`) |

> Caveat: `material 4 (snow)` has *no* `elif` branch in `compute_stress_from_F_trial`,
> so it falls through to the elastic FCR `else`. Snow is effectively elastic jelly in
> this fork despite having distinct E/nu/xi defaults. Worth flagging if snow recipes
> ship.

### Elasticity details
- **FCR** (`kirchoff_stress_FCR`, L10): `τ = 2μ(F−R)Fᵀ + I·λJ(J−1)`, `R = UVᵀ`. The
  standard fixed-corotated Kirchhoff stress.
- **StVK-Hencky** (`kirchoff_stress_StVK`, L43): log-strain `ε = log Σ` (clamped
  `Σ ≥ 0.01` to avoid NaN), `τ = U·diag(2με + λ(Σε)·1)·Vᵀ·Fᵀ`.
- **Neo-Hookean-Borden** (`kirchoff_stress_neoHookeanBoarden`, L81): deviatoric
  `μ·J^(-2/3)·dev(B)` + volumetric `J·(κ/2)(J − 1/J)`, `B = FFᵀ`. κ stored per particle
  (`compute_mu_lam_from_E_nu`: `κ = 2μ/3 + λ`).
- **Drucker-Prager stress** (L62): builds Kirchhoff stress from the log-strain
  derivative `2μ·logσ/σ + λ·(Σlogσ)/σ` per principal axis.

### Plasticity return mappings
- **von Mises** (L127): SVD → log strain `ε`; deviatoric `τ_dev`; if
  `‖τ_dev‖ > yield_stress` project radially: `Δγ = ‖ε̂‖ − yield/(2μ)`,
  `ε ← ε − (Δγ/‖ε̂‖)ε̂`, rebuild `F = U·exp(ε)·Vᵀ`. Optional isotropic **hardening**
  (`yield += 2μ·ξ·Δγ` when `hardening==1`).
- **von Mises + damage** (L173, plasticine): same, but the yield stress is *softened*
  each plastic step (`yield -= softening·‖Δε‖`); once `yield ≤ 0` the particle goes
  fully fluid (`μ = λ = 0`). `yield_stress < 0` = indestructible. This is the
  fracture/melt mechanism.
- **Drucker-Prager** (`sand_return_mapping`, L279): the cohesionless sand cone.
  `Δγ = ‖ε̂‖ + (3λ+2μ)/(2μ)·tr(ε)·α`. Three regimes: `Δγ ≤ 0` elastic; `Δγ>0 & tr>0`
  project to cone tip (`F = UVᵀ`, all volumetric strain dropped); `Δγ>0 & tr≤0` project
  to cone surface. `α` from friction angle: `α = √(2/3)·2sinφ/(3−sinφ)` (set in
  `initialize`, L71, and recomputed on `friction_angle` change).
- **Viscoplastic StVK** (L231, foam/toothpaste): Bingham-like — yields when
  `‖s_trial‖ > √(2/3)·yield`, relaxes the deviator by a rate that depends on
  `plastic_viscosity / (2μ̂ dt)`. dt-dependent (rate-sensitive).
- **Non-Associative Cam-Clay** (L319, watermelon): the most elaborate — pressure-
  dependent cap model. Computes `p_trial`, deviatoric `s_hat`, yield
  `y = c·‖s‖² + M²(p+βp₀)(p−p₀)`. Projects to apex (`p_trial > p₀` or `< −βp₀`) or to the
  yield ellipse; updates the hardening state `logJp` (stored in `particle_Jp`) via a
  quadratic solve. `M = α·dim/√(2/(6−dim))` (from friction angle). This is the
  PhysGaussian "breakable solid" model — `beta` controls brittleness (the driver can
  set `beta` huge to make a region near-unbreakable).

### F update + clamping
- `F_trial = (I + ∇v·dt) · F` is formed in G2P every substep (`g2p`/`g2p_flip` L777/828).
  `∇v` (`new_F`) is `Σ outer(grid_v, ∇w)`.
- Clamping is implicit in the return maps via SVD-singular-value floors
  (`Σ ≥ 0.01`, `≥ 1e-14`) — there's no explicit `Jp` clamp à la snow's `[1−θ_c, 1+θ_s]`.
- Final stress is symmetrized: `τ = (τ + τᵀ)/2` (L899).
- `particle_Jp` (init −0.04, the `alpha_0` recipe field) is the Cam-Clay hardening log.

---

## 3. Time integration — explicit symplectic Euler, CFL-clamped

### Scheme
Explicit (forward/symplectic) Euler MPM. One substep = `p2g2p` (`mpm_solver_warp.py:582`):
```
zero_grid → pre_p2g ops (impulse, velocity modifiers) → compute_stress_from_F_trial
          → P2G (scatter mass+momentum+force) → grid_normalization_and_gravity
          → grid damping (optional) → grid BCs → G2P (gather, advect, update F_trial)
          → time += dt
```
Grid update (`grid_normalization_and_gravity`, L717): for nodes with `m > 1e-15`,
`v_out = (v_in + force_momentum)/m + dt·g`. Velocity is updated *before* advection
(symplectic). Particle advection `x += dt·new_v` uses the PIC velocity (stable choice).

### Substep dt + CFL (driver L587-611)
```python
dx       = grid_lim / n_grid
substep_dt = recipe["substep_dt"]                # e.g. 1e-4
c_sound  = sqrt( E*(1-nu) / ((1+nu)(1-2nu)·ρ) )  # P-wave speed, linear elasticity
cfl_dt   = 0.6 * dx / c_sound                     # CFL number = 0.6
substep_dt = min(substep_dt, cfl_dt)              # CLAMP — only ever tightens
step_per_frame = int(frame_dt / substep_dt)       # e.g. 0.03 / 1e-4 = 300 substeps/frame
```
Key history (encoded in comments + `recipe_lint.py`): the *original* code did
`substep_dt = cfl_dt` unconditionally, which could **relax** a carefully-chosen recipe
dt (e.g. overwrite 1e-4 with 1.31e-4) and *reduce* stability. The fix (PhaseA) clamps
to `min(recipe, cfl)` so the CFL bound only tightens. `recipe_lint.py` R2 statically
rejects recipes whose `substep_dt > cfl_dt` (`CFL_COEFF = 0.6`).

### Stability levers (the hard-won knobs)
- **CFL = 0.6**: explicit MPM needs `dt·c_sound < 0.6·dx`. Stiff materials (high E /
  low density) → tiny dt → huge `step_per_frame` → slow. This is the central tension.
- **`grid_v_damping_scale`** (default **1.1**): applied via `add_damping_via_grid`
  (`v_out *= scale`) — **but only when `< 1.0`** (`mpm_solver_warp.py:556`). The 1.1
  default is therefore a *no-op*; `recipe_lint.py` R1 warns that `>= 1.0` silently
  disables damping. A value like 0.999 bleeds energy per substep to kill FLIP ringing.
- **`flip_pic_ratio` < 1.0**: lower = more PIC dissipation = more stable, less lively.
- **gravity** `g` is a `vec3` (recipes use e.g. `[0,0,-15]`, a scaled-up g for the
  normalized `[0, grid_lim]` domain).

---

## 4. Grid + boundary conditions

### Grid
- Uniform dense Cartesian, `n_grid³` (recipes: 100–150; sand uses 150). Domain is
  `[0, grid_lim]³` (recipes: `grid_lim = 2`). `dx = grid_lim/n_grid`,
  `inv_dx = n_grid/grid_lim`.
- Three dense grid fields: `grid_m` (float), `grid_v_in`, `grid_v_out` (vec3),
  shape `(n_grid,n_grid,n_grid)`. At `n_grid=150` that's ~3.4M nodes × 7 floats ≈ 95 MB
  — fully allocated dense (no sparsity). Zeroed every substep by `zero_grid`.
- Particles must be normalized into the grid domain by the driver before sim
  (the `sim_area` → `[0,grid_lim]` mapping lives in `decode_param`/transform utils).

### Boundary conditions (registered as `grid_postprocess` kernels, applied after grid update)
Each BC appends a closure to `self.grid_postprocess[]` (+ optional `modify_bc` to move
the collider). Types (from `set_boundary_conditions`, `decode_param.py:248`):

- **`bounding_box`** (`add_bounding_box`, L1126): a 3-cell padding wall. For each face,
  if a boundary node's velocity points *out* of the domain, that velocity component is
  zeroed (one-way / non-penetration). Padding = 3 cells (matches the quadratic stencil
  half-width).
- **`surface_collider`** (`add_surface_collider`, L918): half-space `dot(x−point, n) < 0`.
  Surface types: `sticky`(0) → `v=0`; `slip`(1) → project out *all* normal component;
  `separate`(2) → project out only *inward* normal + Coulomb friction
  (`v = max(0, ‖v‖ + v_n·friction)·v̂`); `cut`(11) → a watermelon-specific z-band slicer.
  *(Note: the sticky/slip/separate branch ends by also setting `v_out=0` at L1007 — a
  quirk worth auditing; the friction-projected `v` is computed then overwritten.)*
- **`cuboid`** (`set_velocity_on_cuboid`): set all nodes inside an AABB to a prescribed
  velocity (moving Dirichlet); `modify` advects the box by `dt·velocity`; `reset` zeroes
  for 15 substeps after `end_time`.
- **`release_particles_sequentially`** (L1401): builds 50 nested
  `enforce_particle_velocity_translation` zones along an axis, each releasing at a
  staggered `end_time` — peels a clamped object free layer-by-layer (e.g. a collapsing
  pile). Particle-level (uses `particle_velocity_modifiers`, applied *pre*-P2G), not grid.
- **`particle_impulse`** / **`enforce_particle_translation`** / **`..._rotation`**:
  pre-P2G particle-velocity modifiers (impulse adds `force/m·dt`; rotation sets a
  cylindrical swirl field). Applied before P2G via `pre_p2g_operations` /
  `particle_velocity_modifiers`.

`particle_selection[p] == 0` gates *every* P2G/G2P kernel — non-zero = frozen particle
(excluded from transfer).

---

## 5. Performance — what dominates ~1.5 s/frame

### Kernel structure
Per substep, ~8 kernel launches: `zero_grid` (grid-dim), pre-p2g (particle-dim),
`compute_stress_from_F_trial` (particle-dim, **SVD-heavy**), one P2G (particle-dim,
atomics), `grid_normalization_and_gravity` (grid-dim), optional damping (grid-dim), N
BC kernels (grid-dim), one G2P (particle-dim, gather). At `step_per_frame ≈ 300` and
~969k particles that's ~2,400 launches/frame.

### The cost centers
1. **P2G atomics** — the classic MPM bottleneck. Each particle does
   `27 × wp.atomic_add` into `grid_v_in`, `grid_v_out`, `grid_m`. With ~1M particles
   that's ~27M atomic adds per buffer per substep. Neighboring particles hit the same
   node → contention serializes. **PhaseC.2 sort-by-cell** (`p2g_flip_pic_sorted`,
   `compute_cell_id` + `wp.utils.radix_sort_pairs`) reorders particle processing so
   adjacent threads scatter to adjacent cells → atomics coalesce in L2. Gated behind
   `sort_p2g` (fp32 + flip_pic only); validated to <1e-4 relative drift (atomic order
   changes LSBs).
2. **`compute_stress_from_F_trial`** — every particle does at least one `wp.svd3`
   (return map) + another for the stress, i.e. ~2 analytic 3×3 SVDs/particle/substep.
   SVD is branchy/register-heavy; the time profiler tracks this as a separate bucket.
3. **`zero_grid` + dense grid sweeps** — `n_grid³` work each substep even where empty
   (95 MB dense at n_grid=150). Memory-bandwidth bound, mostly wasted on empty cells.

### CUDA graph capture (`--graph_capture`, PhaseA)
`p2g2p_capture_safe` is the capture-safe substep (no `wp.ScopedTimer`, no host sync).
The driver warms up one substep, then `wp.capture_begin` → loops `step_per_frame`
substeps → `wp.capture_end` into `_graph`; subsequent frames are a single
`wp.capture_launch(_graph)`. This collapses ~2,400 per-launch CPU dispatch overheads
into one graph submission — a major win when each kernel is short. `verify_cuda` is
forced off during capture. The repo's fast path always passes `--graph_capture`.

### Other perf paths present
- **PhaseB.2 fp16 sidecars** (`mixed_precision`): `x/v/C` in fp16 (`vec3h/mat33h`),
  stress/F/grid stay fp32 (compute lifts to fp32 at kernel boundary). Halves the
  particle-state bandwidth; off by default (byte-identical when off).
- **PhaseB.3 async I/O**: PLY/PNG writes deferred to a thread pool, off the timed loop.

---

## Innovation opportunities

Grounded in the code above. Difficulty is rough (S = days, M = weeks, L = months+).

### 1. Implicit / semi-implicit grid-velocity update for stiff materials — **L**, highest payoff
**Today:** explicit symplectic Euler ⇒ `dt < 0.6·dx/c_sound`. Stiff materials (metal
E=50k, watermelon E=50k) force tiny `substep_dt` and ~300 substeps/frame — *this is the
dominant cost*. **What it unlocks:** an implicit (backward-Euler) or
semi-implicit/IMEX grid solve lets `dt` grow 10–100× independent of stiffness → fewer
substeps → directly attacks the 1.5 s/frame. **Where:** replace
`grid_normalization_and_gravity` with a Newton/MINRES solve over `grid_v_out` using the
elasticity Hessian (`∂²Ψ/∂F²`); the stress functions in `mpm_utils.py` already give the
first Piola, so the differential is the main new math. `mpm_implicit_jelly.py` already
exists in the solver dir — a starting point. Hard because the global solve must be
Warp-kernel-friendly and CUDA-graph-capturable.

### 2. Adaptive / per-material substepping — **M**, fast win
**Today:** one global `substep_dt = min(recipe, cfl)` for *all* particles, set by the
single global `E/nu/ρ` CFL. Multi-material scenes (sandwich recipes mix soft+stiff)
run the whole domain at the stiffest material's dt. **What it unlocks:** compute CFL
per material region and substep the stiff region more often (sub-cycling) while the
soft region takes big steps — could halve substeps on mixed scenes. **Where:** driver
L587-611 (CFL is computed once from scalar `E,nu,rho`) + the substep loop; needs a
per-particle dt or a region-tagged sub-cycle. Lower-risk variant: just compute CFL from
the *max* per-particle stiffness already in `model.E[]` instead of the scalar recipe E.

### 3. Differentiable MPM → inverse design / learn material params from video — **L**, strategic
**Today:** the solver is forward-only (PhaseB.2 even *dropped* the decode side). But
Warp supports autodiff (`requires_grad` is threaded through the torch↔warp aliasing in
`warp_utils.py`), and the whole substep is differentiable kernels. **What it unlocks:**
back-prop through the sim to fit `E, nu, yield_stress, friction_angle, beta` so a `.gsq`
*matches a reference video* — turning recipe authoring from manual guesswork into "give
me a clip, I'll infer the material." Also enables shape/initial-condition inverse design.
**Where:** wrap `p2g2p` substeps in a `wp.Tape`, expose particle-x trajectory as the
loss, optimize the `MPMModelStruct` scalar fields. The constitutive funcs are already
pure and SVD-differentiable. This is the single biggest *product* differentiator
(learning physics from observation) versus a perf tweak.

### 4. Sparse grid (hash / block) instead of dense `n_grid³` — **M/L**
**Today:** `grid_m/grid_v_in/grid_v_out` are dense `(n_grid)³` (~95 MB at 150,
zeroed+swept every substep) even though particles occupy a small fraction. **What it
unlocks:** memory + the `zero_grid`/grid-sweep bandwidth scale with *occupied* cells,
not the bounding cube; enables much higher effective resolution (sharper detail) at the
same cost, and bigger domains. **Where:** swap the three dense `wp.array(ndim=3)` for
Warp's `wp.HashGrid` or a block-sparse paged grid; rewrite `zero_grid`,
`grid_normalization_and_gravity`, the BC kernels, and the P2G/G2P node indexing. The
sort-by-cell scaffolding (`compute_cell_id`) already computes the linear cell id needed
for a hash. Touches every grid kernel — hence M/L.

### 5. Fuse stress+P2G and cut SVD count — **M**, surgical perf
**Today:** `compute_stress_from_F_trial` and P2G are separate launches, and the stress
pass does up to **two** `wp.svd3` per particle (one in the return map, one for stress)
— SVD is the per-particle compute hot spot. **What it unlocks:** (a) reuse the single
return-map SVD for the stress computation (the return map already has `U,Σ,V`; pass them
out instead of re-decomposing at L872) → ~halve SVD work; (b) optionally fuse
stress+P2G into one kernel to drop a launch + a `particle_stress` round-trip to VRAM.
**Where:** `mpm_utils.py:836-900` — refactor the return-mapping `@wp.func`s to return
`(F_elastic, U, sig, V)` so the stress branch skips its own `wp.svd3`. Pure
local change, byte-comparable to validate.

### 6. ASFLIP / PolyPIC transfer upgrade — **M**, quality
**Today:** FLIP/PIC blend with a single scalar `flip_pic_ratio`; FLIP energy/ringing is
fought bluntly with damping (which is *off* by default at 1.1!). **What it unlocks:**
**ASFLIP** (affine-separable FLIP, Fei et al. 2021) gives FLIP's liveliness with
position-corrected stability and far less ringing → smoother sequences *without*
energy-killing damping (which the team has been fighting; see the smoothness memory).
PolyPIC reduces dissipation while staying stable. **Where:** new `g2p_asflip` kernel
alongside `g2p_flip` (`mpm_utils.py:786`) — adds the affine `C` term back into the FLIP
update + a position-correction term; small, self-contained, A/B-testable. Directly
targets the open "playback smoothness" root cause.

### 7. Activate snow + add proper Drucker-Prager hardening — **S/M**, correctness
**Today:** `material 4` (snow) has **no branch** in `compute_stress_from_F_trial` → it
silently runs as elastic FCR despite distinct defaults (`xi=10, hardening=5, alpha_0=-0.01`).
**What it unlocks:** real snow (the Stomakhin 2013 model: FCR + von-Mises-like
hardening with `Jp` clamping `[1−θ_c, 1+θ_s]`) — a visibly different, marketable
material. **Where:** add the `elif model.material[p] == 4` branches in both the
return-map dispatch (L843) and stress dispatch (L874); the hardening machinery
(`xi`, `hardening`, `particle_Jp`) is already plumbed. Small, isolated, high
correctness value.

### 8. Two-way / multi-material coupling + rigid colliders — **L**, scope expander
**Today:** colliders are kinematic grid-velocity BCs only (one-way: the fluid feels the
wall, the wall never feels the fluid); materials share one grid but don't exchange
momentum beyond the shared transfer. **What it unlocks:** rigid bodies that *respond* to
the splat material (a ball the goo can push), and proper multi-material contact (fluid
+ sand + elastic in one scene reacting to each other) — a large expansion of expressible
`.gsq` effects. **Where:** accumulate impulse on collider bodies in `add_surface_collider`'s
grid kernel (sum the projected-out momentum) and integrate the rigid body between
substeps via a new `modify_bc`; multi-material already works at the grid (per-particle
`material[]`), so the lift is mostly the rigid-coupling and contact-resolution layer.

---

## Quick-reference: the substep in one screen

```
p2g2p(dt):                                          # mpm_solver_warp.py:582
  zero_grid                                         # grid_m, grid_v_in, grid_v_out = 0
  for op in pre_p2g_operations: op()                # impulse / velocity modifiers (particle)
  compute_stress_from_F_trial(dt)                   # return-map F_trial→F, then τ = stress(F)   [2× SVD]
  p2g_flip_pic_with_stress(dt)                      # scatter: grid_v_in += w·m·v ;
                                                    #          grid_v_out += w·dt·(-vol·τ·∇w) ; grid_m += w·m
  grid_normalization_and_gravity(dt)                # v_out = (v_in+v_out)/m + dt·g
  if grid_v_damping_scale < 1.0: add_damping_via_grid   # v_out *= scale   (DEFAULT 1.1 = no-op)
  for bc in grid_postprocess: bc()                  # bounding_box / surface_collider / cuboid ...
  g2p_flip(dt, flip_pic_ratio=0.80)                 # gather: v = ratio·v_old + new_v - ratio·old_v ;
                                                    #         x += dt·new_v ; F_trial = (I+∇v·dt)·F
  time += dt
```
Default scheme = **MLS-MPM kernel + FLIP/PIC(0.8) transfer + explicit symplectic Euler,
CFL 0.6, per-particle elastoplastic constitutive models (FCR / StVK+vonMises /
Drucker-Prager / Neo-Hookean+CamClay)**.
