# Deep dive: how gsfluent couples 3D Gaussian Splatting to MPM physics

*Read-only teardown — 2026-05-27. Sources: the simulation driver
`gs_simulation/watermelon/gs_simulation_building.py`, the MPM solver
(`mpm_solver_warp/`), the particle filler (`particle_filling/filling.py`) on
`sxyin-host`, and the production fuser
`server/gsfluent/core/fusers/knn_kabsch.py` + its wiring in
`server/gsfluent/core/sim_engines/mpm.py`.*

This is the heart of the "physics-driven 3DGS" idea: a static Gaussian cloud
is bound to a deformable particle simulation, and every frame the splats are
advected so the scene *moves* under physics. The surprising finding is that
**two entirely different coupling paths exist in this codebase**, with very
different fidelity, and **the gsfluent production server uses the weaker one.**

---

## 0. The two coupling paths (read this first)

| | **Path A — in-engine (research / PhysGaussian-style)** | **Path B — KNN skinning fuser (gsfluent production)** |
|---|---|---|
| Where | inside `gs_simulation_building.py` under `if args.render_img:` + `mpm_solver_warp` kernels | `server/gsfluent/core/fusers/knn_kabsch.py`, driven by `tools/fuse_to_full_ply.py` |
| Binds | every Gaussian *is* an MPM particle (1:1, plus filler interior particles) | external KNN map: each of 683k ref splats ← 8 nearest sim particles |
| Position | particle advection (MPM g2p) | inverse-distance weighted blend of 8 neighbors' displacement |
| Rotation | polar-decomp R from F, applied to quaternion | **none** — quaternion frozen at rest |
| Covariance/scale | **full** Σ' = F Σ Fᵀ (`compute_cov_from_F`) or rate form (`update_cov`) | **none** — scale frozen at rest |
| Opacity | optional Jp-based damage gating | frozen |
| SH / color | static (no SH rotation either) | static |
| Consumed by | the sim's own `diff_gaussian_rasterization` render → PNG/MP4 | written to `frame_*.ply` → packed to `.gsq` → in-browser Spark renderer |

**Production reality:** `mpm.py:_build_sim_argv` launches the sim with
`--output_ply --async_io --target_particles N` (+ `--graph_capture` in fast
mode). It does **not** pass `--render_img` and does **not** pass
`--output_cov`. So the entire Path-A machinery — `compute_cov_from_F`,
`compute_R_from_F`, the SVD polar decomposition, the Jp opacity gate — runs
either never or only for the solver's internal stress update; none of it
reaches the rendered output. The sim emits **xyz-only** `sim_*.ply`
(200k particles), and `_build_fuse_argv` runs
`fuse_to_full_ply.py --knn 8 --no_zup`, which is **Path B**: pure
position skinning, rotation/scale/opacity/SH all inherited unchanged from the
reference splat.

> The class is named `KNNKabschFuser` and its docstring talks about
> "K-NN skinning + Kabsch," but the **production `fuse_frame` contains no
> Kabsch SVD and no rotation update at all.** Kabsch lives only in the
> legacy `--knn_rotation` path of the original script, which the Phase-2
> Protocol refactor explicitly dropped (see the class docstring: *"knn_rotation
> … remain in the CLI wrapper … out of scope"* — and the wrapper no longer
> exposes it either). The name is now a historical artifact. Treat production
> as **KNN inverse-distance position skinning, rigid-frozen orientation.**

---

## 1. Filling + binding

### 1.1 The reference cloud → MPM particle set

The sim loads the trained Gaussian checkpoint (~683k splats for these scenes),
then runs a sequence of reductions before any physics:

1. **Opacity cull** — `init_opacity > preprocessing_params["opacity_threshold"]`
   throws away near-transparent kernels. `gaussians._*` arrays are masked in
   lockstep.
2. **Rotate / translate** to the sim's working frame
   (`generate_rotation_matrices`, `apply_rotations`), and the covariance is
   rotated to match: `init_cov = apply_cov_rotations(init_cov, R)`.
3. **`sim_area` crop** (optional) — an AABB selects the simulated region;
   everything outside is stashed as `unselected_pos/cov/opacity/shs` and
   re-appended at *render* time only (Path A). The production fuser never sees
   `unselected_*`; it works on the full reference ply.
4. **Normalize to a unit cube**: `transform2origin` (longest axis → 1.0) then
   `shift2center111` (center → (1,1,1)). Covariance is scaled consistently:
   `init_cov = scale_origin² · init_cov`. **This exact normalization is
   re-derived independently by the fuser** — see §1.4.

### 1.2 Particle filling (`particle_filling/filling.py`)

The visible splats only cover the *surface* of an object. MPM needs a
filled *volume* or the body behaves like a hollow shell. `fill_particles`:

- **`densify_grids`** rasterizes each Gaussian onto a voxel grid. For each
  particle it reconstructs the 3×3 covariance from the 6 upper-tri floats,
  eigendecomposes it (`ti.sym_eig`), takes the inverse eigenvalues to form the
  *precision* matrix, and finds the radius `r` (in cells) of the kernel's
  support. It then splats a Gaussian density
  `opacity · exp(-½ dᵀ Σ⁻¹ d)` into all cells within `r`. This is a genuine
  anisotropic density field, not a point count.
- **`fill_dense_grids`** seeds particles into any cell whose accumulated
  density exceeds `density_thres`, up to `max_particles_per_cell`, at random
  sub-cell offsets.
- **`internal_filling`** closes the interior: for each empty cell it casts
  rays in the 6 axis directions (excluding `search_exclude_dir`), and if the
  cell is enclosed on all tested sides *and* a parity ray-cast
  (`collision_times`, odd = inside) says it's interior, it fills the cell too.
  This is a voxel flood/parity test — classic "is this point inside the mesh"
  done on the density grid.
- Optional `mcubes.smooth` on the density field before interior filling.

Result: `mpm_init_pos = [ surface (gaussian-backed) particles ; interior
filler particles ]`. The layout invariant `[:gs_num]` = render-visible
gaussian-backed, `[gs_num:]` = volume-only filler, is preserved everywhere
downstream.

- **`init_filled_particles`** (only if `filling.visualize`) gives each *filler*
  particle attributes by **nearest-neighbor copy** from the surface set
  (`get_attr_from_closest`, brute-force O(N·M) min-distance), and gives them
  the *mean* SH so they render as a neutral interior. Otherwise filler
  particles get **zero covariance** and never render (production case:
  `mpm_init_cov = zeros; mpm_init_cov[:gs_num] = init_cov`).

### 1.3 The resolution gap (683k splats vs ~200k particles)

There are two separate reductions, and people conflate them:

- **Path A** (in-engine) actually *grows* the particle count: 683k surface
  splats → after opacity cull and crop, the remainder + interior filler. There
  is **no resolution gap in Path A** — each surviving splat is its own
  particle, deformed directly.
- **Production gsfluent** then runs **`--target_particles`** importance
  subsampling (`mpm.py` passes a target; the watermelon script's "Phase B.1"
  block). This is where 683k collapses to ~200k *sim* particles:
  - gaussian-backed particles are sampled **weighted by `opacity · sx·sy·sz`**
    (so big, opaque splats survive) without replacement;
  - filler particles are sampled **uniformly**;
  - survivors get `vol_scale = 1/keep_prob` so total mass is conserved
    (inverse-probability weighting).
  - the `[:gs_num] | [gs_num:]` split is preserved (each region subsampled
    independently).

So the production gap is: **683k full-res render splats** vs **~200k sim
particles**, and the fuser's job is to reconstruct the 683k cloud from the
200k sparse motion field. That reconstruction is §2.

### 1.4 The binding the *fuser* builds (`build_correspondence`)

This is the production binding. It is **external and one-shot**:

1. Read the reference ply (full 683k). Normalize it to the same unit cube the
   sim used: `_norm_xyz_to_origin_cube` reproduces `transform2origin` +
   `shift2center111` (longest axis → 1, center → (1,1,1)). *This is a
   re-derivation, not a shared call* — a silent coupling that must stay in sync
   with the sim's `transform2origin`.
2. Build a `cKDTree` over the **frame-0 sim particles** and, for every
   reference splat, query its **K=8 nearest sim particles**.
3. Weights = inverse distance, normalized:
   `w_k = (1/(d_k+1e-6)) / Σ(1/(d_j+1e-6))`. (Degenerate guard: all-zero
   distances → `FuseDegenerateClusterError`.)
4. Bake the full reference attribute array (`full_attrs`): apply the Y-up→Z-up
   quaternion/normal rotation **once** at rest (in production `--no_zup` is
   passed, but the wrapper's flags are no-ops and the Protocol always applies
   the production-default transform — worth verifying against the actual scene
   orientation), and bake rest positions.

The binding stored: `knn_idx (683k×8)`, `knn_weights (683k×8)`,
`sim_xyz_t0 (200k×3)`, and the full rest attribute table. Note it binds to
**sim frame 0**, which is *after* filling and subsampling — so the
correspondence is reference-splat → 8-nearest-*surviving*-sim-particles.

---

## 2. The deformation map (per frame)

### 2.1 Path B — production `fuse_frame` (position only)

```
sim_disp        = particle_frame - sim_xyz_t0          # (200k, 3)  per-particle displacement
neighbors       = sim_disp[knn_idx]                    # (683k, 8, 3)
ref_disp        = Σ_k  knn_weights[...,k] * neighbors   # (683k, 3)   inverse-distance blend
ref_xyz_disp    = ref_xyz_norm + ref_disp              # advect rest position by blended disp
out_xyz_world   = _transform_sim_xyz(ref_xyz_disp)     # un-normalize → center → (zup)
out             = full_attrs.copy(); out.xyz = out_xyz_world
```

That is the **entire** per-frame deformation. Each splat is moved by an
inverse-distance-weighted average of its 8 neighbors' displacements — **linear
blend skinning with translation-only bones.** Crucially:

- **Rotation (quaternion): NOT updated.** Every splat keeps its rest
  orientation forever. A splat sitting on a wall that topples 90° keeps
  pointing its original way — the anisotropic lobe no longer hugs the surface.
- **Covariance / scale: NOT updated.** No Σ' = F Σ Fᵀ, not even a uniform
  J-based volume scale. Gaussians keep rest size/shape under any deformation.
- **Opacity: NOT updated.** No compaction-driven density correction.
- **SH / view-dependent color: NOT rotated.** Even if a splat physically
  rotates, its directional lobes (specular highlights, anisotropic shading)
  stay locked to world axes → wrong under any reorientation.

The displacement blend itself is also **not a rigid transform** — it is an
affine average of neighbor translations. Where neighbors move differently
(shear, rotation, divergence), the blend interpolates *positions* but cannot
represent the local *rotation* of the material, so a rotating chunk of wall
smears its splats along a chord rather than rotating them. This is the
classic LBS "candy-wrapper / collapsing-joint" failure, here with no rotation
component at all to mitigate it.

### 2.2 Path A — what the engine actually computes (and discards in prod)

The MPM solver maintains a per-particle **deformation gradient F** and updates
it every substep in the g2p kernel (`mpm_utils.py`, `p2g_apic_with_stress` /
`g2p_flip`):

```
new_F  = Σ_grid  outer(grid_v, dweight)      # velocity gradient ∇v from grid
F_trial = (I + dt·new_F) · F                  # multiplicative F update
```

(`new_F` here is really the velocity gradient; `(I + dt·∇v)` is the
incremental deformation.) Plasticity return-mapping then maps `F_trial → F`
per material (`compute_stress_from_F_trial`: fixed-corotated, StVK,
Drucker-Prager sand, von-Mises with damage, Cam-Clay). So F carries the **full
elastoplastic deformation history**, and two derived quantities are available:

**Covariance — the anisotropy-correct transform (`compute_cov_from_F`):**
```
cov = F · init_cov · Fᵀ            # exact Σ' = F Σ Fᵀ
```
This is the *right* answer — it stretches, shears, and rotates the Gaussian
ellipsoid exactly as the material deforms. There is also a **rate form**
(`update_cov`, used when `update_cov_with_F=True`):
```
cov_{n+1} = cov_n + dt·(∇v · cov_n + cov_n · ∇vᵀ)
```
which is the time-integrated equivalent (the derivative of F Σ Fᵀ). The solver
defaults to `update_cov_with_F = False`, so production-Path-A would use the
exact `F Σ Fᵀ` form computed on demand in `export_particle_cov_to_torch`.

**Rotation — polar decomposition (`compute_R_from_F`):**
```
svd3(F) → U, σ, V        # with reflection fixes if det(U)<0 or det(V)<0
R = U · Vᵀ               # the rotation factor of F = R·S
particle_R = Rᵀ
```
This is the rigid rotation of the local frame — applied to the splat quaternion
in the render branch so the Gaussian's orientation follows the material.

**Opacity under damage** (render branch): `alpha = Jp; opacity[Jp>0.4]=0` —
when a particle's plastic volume ratio passes a threshold it's hidden, a crude
fracture/erosion cue.

So Path A is **PhysGaussian-grade coupling**: position from advection, rotation
from polar(F), full anisotropic covariance from F Σ Fᵀ, opacity from plastic
damage. **The math is all here and correct — production simply doesn't call
it.** The `--output_cov` flag was added precisely to ferry per-particle cov out
of the solver into the fuse step ("eliminates the K-NN 'ghost' artifact for
cracked regions"), but the production fuser (Path B) has no code path to
consume cov fields — that branch was dropped in the Phase-2 Protocol refactor.

### 2.3 Fidelity lost in production (Path B vs Path A)

| Quantity | Path A (available) | Path B (production) | Visible artifact |
|---|---|---|---|
| Position | advected | LBS blend of 8 neighbors | smearing across shear/rotation boundaries |
| Orientation | polar(F) | **frozen** | rotated surfaces show wrong-facing lobes; "stuck" highlights |
| Anisotropy/scale | F Σ Fᵀ | **frozen** | stretched material keeps rest-shaped blobs → gaps / over-coverage |
| Opacity | Jp damage gate | **frozen** | no thinning where material rarefies; no fade on fracture |
| SH color | (static even in A) | static | specular pinned to world, not surface |

---

## 3. The fuser vs the sim's own update — which runs in production

Settled by reading `mpm.py`:

- **Sim subprocess** (`_build_sim_argv`): emits xyz-only `sim_*.ply`. No
  `--render_img` → none of `compute_cov_from_F` / `compute_R_from_F` / the Jp
  opacity gate runs for output. No `--output_cov` → cov is never even exported.
  (F is still updated internally because the stress model needs it, but it
  dies with the process.)
- **Fuse subprocess** (`_build_fuse_argv`): `fuse_to_full_ply.py --knn 8
  --no_zup` → **Path B**, position-only skinning, into `library/<seq>/frames/`.
- Then `pack_*` encodes to `.gsq` and the browser renders.

**Trade-off the team made:** Path B is *decoupled* from the GPU sim — it's pure
numpy + scipy KDTree, runs on CPU after the sim exits, is deterministic, and
reconstructs the full 683k cloud from any sparse sim. It also degrades
gracefully (a NaN sim frame is just dropped). Path A requires the
`diff_gaussian_rasterization` CUDA renderer in-process and only ever rendered
to 2D PNGs — it never produced the per-frame full-attribute `.ply`/`.gsq` the
gsfluent download-and-play architecture needs. So Path B was the pragmatic
choice to get *a* full-res animated cloud out the door; it traded away all
non-positional fidelity to do it. `pack_sim_splats.py` exists precisely to
A/B this: it renders the raw 200k sim particles as isotropic blobs so you can
*see* what the fuse step adds (color + anisotropy from the reference) and what
it can't (correct deformation of that anisotropy).

---

## 4. Failure / fidelity modes

1. **Large rotation** (toppling wall, tumbling debris in a demolition). Path B
   freezes orientation and scale, so a chunk that rotates 90° renders with its
   Gaussians still axis-aligned to rest — the surface "shreds": lobes point off
   the surface, coverage gaps open between them. This is the single worst mode.
2. **Shear / stretch.** LBS position blend can place centers correctly but the
   ellipsoids never stretch (no F Σ Fᵀ), so stretched material is under-covered
   (gaps) and compressed material is over-covered (mush). No volume/opacity
   compensation either.
3. **Topology change / fracture (demolition's whole point).** The KNN map is
   frozen at frame 0. When a crack opens, a reference splat straddling the crack
   is still bound to particles on *both* sides; its blended displacement
   averages two diverging motions → a splat stretched across the gap = the
   **"ghost" / web artifact** the `--output_cov` note calls out. Path B has no
   remedy; Path A's per-particle F binding would (each splat follows one
   particle's F), which is exactly why `--output_cov` was prototyped.
4. **Thin features** (railings, window frames, foliage). Filling's ray-parity
   interior test misclassifies thin/open geometry; KNN with K=8 over a sparse
   200k set pulls in neighbors from across a thin gap, blending opposite-side
   motion. Subsampling (683k→200k weighted by size·opacity) preferentially
   *drops* small thin-feature splats from the sim set, so thin features have the
   fewest bones and the worst skinning.
5. **Coupling normalization drift.** The fuser re-derives the sim's
   `transform2origin`/`shift2center111` independently (`_norm_xyz_to_origin_cube`).
   If the sim's preprocessing (crop, rotation, scale) ever diverges from this
   reproduction, every splat is mis-registered to the particle field — a silent,
   global failure with no assertion guarding it.
6. **Filler-particle bleed.** Filler interior particles can be among a surface
   splat's 8 nearest neighbors; their motion is physically right but they carry
   no surface constraint, so near boundaries they can drag surface splats
   inward.

---

## 5. Innovation opportunities (grounded in the code)

Ordered by leverage. "Lands in" = the file/seam to touch.

### 5.1 ★ Pipe full F Σ Fᵀ covariance through to the renderer (anisotropy-correct)
**What it unlocks:** correct per-frame stretch/shear/rotation of every Gaussian
— the single biggest visual gap. The math already exists
(`compute_cov_from_F`), and `--output_cov` already exports it; what's missing
is (a) the fuser consuming cov fields and (b) a `.gsq` schema that stores
per-frame covariance instead of static scale + per-frame quat.
**Difficulty:** Medium. Requires a **v3 `.gsq` schema** (per-frame 6-float cov
or per-frame scale+quat) — `pack_sim_splats.py` explicitly notes today's v1
"stores scales static, quat per-frame" and can't carry cov. Also needs the
fuser's particle_F binding path (1-NN bind splat→particle) restored.
**Lands in:** `knn_kabsch.py` (new cov path), `codecs/gsq.py` + `gsq_prune.py`
(v3 schema), the in-browser splat-writer.

### 5.2 ★ Restore polar-R rotation in the fuser (rigid-correct orientation)
**What it unlocks:** splats rotate with the material — fixes the toppling-wall
shredding (failure mode #1) at a fraction of the cost of full cov. `R = U Vᵀ`
is already computed (`compute_R_from_F`, `export_particle_R_to_torch`). Cheapest
path: 1-NN bind each splat to a sim particle, fetch its R, compose with the rest
quaternion. The v1 `.gsq` *already* stores per-frame quaternions, so **no schema
change needed** — this is the lowest-hanging high-value fruit.
**Difficulty:** Low–Medium. Needs `--output_cov`-style export of R (or
on-the-fly KNN-Kabsch in the fuser as a no-export alternative — see 5.4) and a
quaternion-compose in `fuse_frame`.
**Lands in:** `mpm.py` argv (export R), `knn_kabsch.py` `fuse_frame`.

### 5.3 ★ Actually implement KNN-Kabsch (the name's promise) for rotation without sim changes
**What it unlocks:** local rigid rotation **estimated purely from the position
field** — no `--output_cov`, no solver changes, fully decoupled (keeps Path B's
operational advantages). For each splat, solve Kabsch SVD over its 8 neighbors'
(rest → current) positions to get a local R, apply to position + quaternion.
This is literally what the class name claims and the docstring says was dropped.
**Difficulty:** Medium. Kabsch SVD over 683k×8 each frame is heavier than the
current blend but still CPU-numpy-feasible (batched `np.linalg.svd`).
**Lands in:** `knn_kabsch.py` `fuse_frame` (the dropped `knn_rotation` path,
modernized).

### 5.4 Opacity / density correction under volumetric compression
**What it unlocks:** Gaussians thin out where material rarefies and fade on
fracture, removing the "mush on compression / ghost on fracture" look. Cheap
proxy: scale opacity by `J = det(F)` (volume ratio), or reuse the existing
`Jp>0.4` damage gate. Pairs naturally with 5.1.
**Difficulty:** Low (per-frame opacity already storable if schema carries it;
today it's static). **Lands in:** fuser + `.gsq` schema (per-frame opacity).

### 5.5 Fracture-aware re-binding (kill the ghost web)
**What it unlocks:** the demolition use-case directly. Detect when a splat's 8
neighbors diverge beyond a strain threshold (or straddle a detected crack) and
**re-partition the KNN weights** to one side, or hard-bind to a single particle
(1-NN). Eliminates failure mode #3 without needing full F.
**Difficulty:** Medium. Needs a per-frame divergence test on `knn` neighbor
displacements; the binding is currently frozen at frame 0 by design, so this
introduces controlled re-binding. **Lands in:** `knn_kabsch.py`.

### 5.6 Higher splat:particle fidelity via adaptive subsampling
**What it unlocks:** better thin-feature survival (failure mode #4). Today
`--target_particles` weights by `opacity·sx·sy·sz`, which *drops* small thin
splats. Add a curvature/thin-feature term (e.g. boost particles whose local
neighborhood is low-dimensional) so rails/frames keep enough bones.
**Difficulty:** Low–Medium (it's a reweighting of the existing Phase-B.1 block).
**Lands in:** `gs_simulation_building.py` subsample block.

### 5.7 SH rotation under deformation (correct view-dependent shading)
**What it unlocks:** specular/anisotropic highlights track the surface as it
rotates instead of being pinned to world axes. Requires rotating the SH
coefficients by the per-splat R (Wigner-D / fast SH rotation). Only matters
once 5.2/5.3 give a per-splat R, and mostly for shiny scenes.
**Difficulty:** Medium–High (SH rotation is fiddly; needs per-frame SH storage —
big `.gsq` bloat) and arguably low ROI for matte demolition debris. Do last.
**Lands in:** fuser + a much fatter schema; probably keep degree-capped.

### 5.8 Differentiable coupling — fit physics to a target video
**What it unlocks:** the moonshot. Because the whole forward map
(particles → F → Σ' → render) is differentiable (the solver is an MPM in Warp;
`diff_gaussian_rasterization` is differentiable), you could backprop a rendering
loss against a target video to **estimate material parameters** (E, ν, density,
yield) or initial conditions. Turns gsfluent from "simulate a guess" into
"recover the physics that produced this footage."
**Difficulty:** High (memory of differentiating through thousands of substeps;
needs checkpointing). Research-grade, but the codebase already has every
differentiable piece. **Lands in:** a new training harness around the solver,
not the production server.

---

## TL;DR

- **Production coupling = KNN linear-blend skinning, translation only.** Each of
  683k reference splats is moved by an inverse-distance blend of its 8 nearest
  sim particles' displacements; **rotation, covariance/scale, opacity, and SH
  are all frozen at rest.** The `KNNKabschFuser` name is misleading — there is
  no Kabsch and no rotation in the production `fuse_frame`.
- **The anisotropy-correct machinery exists but is unused in production:** the
  MPM solver computes a full deformation gradient F and can emit exact
  `Σ' = F Σ Fᵀ` (`compute_cov_from_F`) and polar-decomposition rotation
  `R = U Vᵀ` (`compute_R_from_F`) — the PhysGaussian-grade path — but the
  gsfluent server runs the sim xyz-only (no `--render_img`, no `--output_cov`)
  and reconstructs frames with the position-only fuser.
- **Biggest fidelity gap:** frozen orientation + frozen anisotropy under
  rotation/shear. A toppling wall's Gaussians never rotate or stretch, so
  surfaces shred and crack regions smear into a "ghost web."
- **Top bets:** (1) restore polar-R rotation in the fuser — *zero schema change*
  since v1 `.gsq` already stores per-frame quaternions, highest value/effort
  ratio; (2) pipe `F Σ Fᵀ` covariance through a v3 `.gsq` schema for full
  anisotropy; (3) fracture-aware re-binding to kill the demolition ghost web.
