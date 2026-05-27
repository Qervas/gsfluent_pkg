# Differentiable MPM → Learn Material Parameters from a Reference Video

**Status:** Proposal (design only — no code, no GPU, no deploy)
**Date:** 2026-05-27
**Author:** exploration agent
**Scope:** Turn recipe authoring from manual knob-guessing into an inverse
problem: back-propagate through the MPM solver to *fit* the material
parameters (`E, nu, yield_stress, friction_angle, beta, ...`) so a `.gsq`
sequence matches an observed reference (first a target trajectory, ultimately a
real video clip). This is the authoring moat that replaces the
recipe-instability/tuning pain documented in
`docs/proposals/intelligent-recipe-stabilization.md`.

This doc is grounded in a read-only teardown of the real solver
(`mpm_solver_warp/mpm_utils.py`, `mpm_solver_warp.py`, `warp_utils.py` on
`sxyin-host`, Warp **1.12.0**) and the repo recipe stack
(`server/recipes/*.json`, `server/gsfluent/schemas/material_defaults.py`,
`server/gsfluent/core/recipe_lint.py`, `server/gsfluent/api/recipes.py`). It
extends innovation opportunity #3 in `docs/deepdive/mpm-solver.md`.

---

## 0. TL;DR

- **Feasible in principle, today.** Warp 1.12 has a full reverse-mode autodiff
  tape (`wp.Tape`), the torch↔warp aliasing in `warp_utils.py` already threads
  `requires_grad`, and every substep is pure differentiable kernels. The
  constitutive math is SVD-based — and `wp.svd3` *has* a registered analytic
  adjoint in Warp, so the elasticity path is differentiable as-is.
- **Three things block a naive `wp.Tape` over the production loop**, all
  fixable: (1) in-place grid scatter + `zero_grid` reuse the same buffers every
  substep (the tape needs distinct state per step, or checkpointing); (2) the
  return maps **write model fields in place** (`model.yield_stress[p]`,
  `model.mu[p]`) — a hard autodiff aliasing hazard; (3) the `if yield`
  branches are piecewise (sub-gradient at the boundary — fine in practice, but
  the hard `min/max`/`wp.abs` clamps and `wp.svd3` near-degenerate `F` need
  smoothing/guarding).
- **Memory is the real constraint.** A naive tape over ~300 substeps/frame ×
  N frames × ~1M particles is multi-GB to tens-of-GB. **Gradient checkpointing
  (recompute substeps in backward) is mandatory** beyond a handful of frames.
- **Tractable Phase 1 = trajectory loss, not video.** The production fuser is
  **position-only KNN skinning** (Path B — see
  `docs/deepdive/splat-physics-coupling.md`): splats are advected by particle
  *positions*; rotation/scale/cov are frozen. So a **particle/splat-position
  loss vs a tracked target** is both the easier loss *and* the
  production-aligned one. Image/video loss (differentiable rendering) is a
  research arc, not Phase 1.
- **Smallest convincing PoC (a weekend):** fit a *single scalar* `E` to a
  short falling-jelly trajectory (jelly = pure elastic FCR, **no return map, no
  in-place model writes, no plasticity branches** — the clean case), 10–20
  particles, ~2–3 frames, full `wp.Tape`, no checkpointing. One number,
  recovered from motion. Detailed in §6.

---

## 1. Differentiability — can `wp.Tape` back-prop through this kernel?

### 1.1 What already supports it

- **Warp 1.12 reverse-mode autodiff.** `wp.Tape()` records kernel launches on
  the forward pass and replays registered adjoints on `tape.backward(loss)`.
  Gradients accumulate into the `.grad` of any array created with
  `requires_grad=True`.
- **The torch↔warp bridge already threads `requires_grad`.** In
  `warp_utils.py` (lines ~249/269/290/311), the `from_torch`-style aliasing
  helpers pass `requires_grad=t.requires_grad` straight through. So a torch
  leaf tensor for `E`/`nu`/`yield_stress` can be aliased into the
  `MPMModelStruct.E` Warp array with gradient tracking, and `tape.backward`
  will populate `E.grad`, readable back in torch for an optimizer step. **This
  is the single most important enabling fact** — the gradient plumbing between
  the optimizer (torch) and the sim (warp) is already half-built for the
  forward direction.
- **`wp.svd3` has an analytic adjoint in Warp.** The SVD that "nearly every
  branch" uses (`compute_stress_from_F_trial`, all return maps) is
  differentiable through Warp's built-in. The classic blocker for
  differentiable-MPM-from-scratch (hand-deriving SVD gradients) is already
  solved upstream. **Caveat:** SVD adjoints are singular when two singular
  values coincide (`σ_i ≈ σ_j`), producing `1/(σ_i²−σ_j²)` blow-ups. Near
  rest (`F ≈ I`, all `σ ≈ 1`) this is exactly the degenerate case. Mitigation
  in §1.3.
- **The constitutive `@wp.func`s are pure** (FCR, StVK, Neo-Hookean-Borden,
  Drucker-Prager stress) — inputs in, `tau` out, no side effects. These
  differentiate cleanly.

### 1.2 What blocks a naive tape over the production `p2g2p` loop

| Blocker | Where | Why it breaks autodiff | Fix |
|---|---|---|---|
| **In-place grid reuse** | `zero_grid` zeros `grid_m/grid_v_in/grid_v_out` at the *top of every substep*; P2G `wp.atomic_add`s into them | The tape needs the grid state *as it was* during forward to compute the backward scatter; overwriting it every substep destroys that. Atomic-add itself *is* adjoint-able (it's a sum), but only if the buffer it wrote isn't clobbered before backward reads it. | Either (a) allocate fresh grid arrays per substep (memory-heavy), or (b) **checkpoint**: don't tape the whole run; recompute each frame's substeps inside backward. (b) is the answer — see §1.4. |
| **Model fields written in place** | `von_mises_return_mapping` does `model.yield_stress[p] = ... + 2μξΔγ` (hardening); `von_mises_return_mapping_with_damage` does `model.yield_stress[p] -= softening·‖Δε‖` and, on full damage, `model.mu[p]=0; model.lam[p]=0` | The *parameter we want to differentiate w.r.t.* is mutated during the forward pass. This aliases the optimization variable with a per-substep state variable — a correctness hazard, not just a perf one. The gradient `∂loss/∂yield_stress(t=0)` is entangled with every in-place overwrite. | **Phase 1: pick materials with no in-place model writes** — jelly (material 0, pure elastic, no return map) and the *non-hardening* paths. For hardening/damage materials (metal w/ hardening, plasticine): split the mutated quantity into a **separate per-particle state array** `yield_state[p]` (initialized from the scalar param `yield_stress`), leave `model.yield_stress` read-only. This is a small, mechanical solver refactor and is required before those materials are learnable. |
| **Piecewise return-map branches** | `if wp.length(cond) > yield_stress`, the three Drucker-Prager `delta_gamma`/`tr` regimes, Cam-Clay apex-vs-ellipse | The map is C⁰ but not C¹ at the yield boundary: a sub-gradient, not a gradient. The backward pass picks one branch's gradient. | In practice fine (the kink is measure-zero); the standard remedy if it bites is a **smoothed yield** (softplus/`tanh` blend over a small `Δ` band instead of a hard `if`). Defer until a material's loss landscape proves jagged. |
| **Hard clamps / abs** | `wp.max(sig, 0.01)`, `wp.max(abs(sig),1e-14)`, `Σ≥0` floors | Zero gradient in the clamped region (a particle stuck against the floor contributes no signal); `abs` non-smooth at 0. | Acceptable — these only clamp pathological deformation. Keep them; they also keep the *forward* sim from NaN-ing, which protects the optimizer from exploring unstable regimes (a feature, see §3). |
| **SVD degeneracy** | every `wp.svd3` when `σ_i≈σ_j` (rest state) | adjoint blow-up | Perturb (`F ← F + εI` analysis), or clamp the `1/(σ_i²−σ_j²)` term; or simply start the optimization from a *deformed* frame (rest frame carries no material signal anyway). |
| **CUDA-graph capture** | `p2g2p_capture_safe`, the `--graph_capture` fast path | A captured graph is a frozen replay; you cannot tape through a `wp.capture_launch`. | The learning loop runs the **un-captured** `p2g2p` (kernels launched individually so the tape can record them). Capture is a forward-only inference optimization; it's simply off during training. No conflict — they're separate code paths already. |
| **fp16 sidecars / sort-by-cell** | PhaseB.2 mixed precision, PhaseC.2 `p2g_flip_pic_sorted` | fp16 gradients are noisy; radix-sort permutation isn't differentiable (but it only reorders a sum, so it's gradient-irrelevant if handled as a stop-gradient index) | Train in **fp32, sort off** (the byte-identical reference path). These are inference perf flags; disable for the tape. |

**Bottom line on differentiability:** the *elastic* path (jelly, and the stress
functions generally) is differentiable through `wp.Tape` essentially as-is. The
*plastic* path is differentiable too, but (a) needs the in-place
`model.yield_stress`/`model.mu` writes refactored into per-particle state to be
*correct* w.r.t. the optimized parameter, and (b) may need smoothed yield if
the landscape is jagged. Memory, not math, is the dominant obstacle.

### 1.3 The smoothing/guarding checklist (constitutive path)

1. **SVD guard:** add `ε` jitter or coincident-singular-value clamp in the
   adjoint of `wp.svd3`; never optimize from the exact rest frame.
2. **Smoothed yield (only if needed):** replace `if ‖cond‖ > yield` with a
   `softplus((‖cond‖−yield)/Δ)·Δ` plastic-flow magnitude; recovers C¹.
3. **Keep the singular-value floors** (`max(σ,0.01)`) — they are stability
   guards, and the lost gradient there is gradient you don't want.
4. **Read-only parameters:** ensure the array you take `.grad` of is never
   written by a kernel (the §1.2 refactor).

### 1.4 Cost of taping ~300 substeps × frames — checkpointing is mandatory

Forward state per substep that the tape must retain (naive, no checkpointing):
particle `x,v,F,F_trial,C,stress` (≈ 6 × mat33/vec3 ≈ ~60 floats/particle) +
the three dense grid buffers (`n_grid³ × 7` floats ≈ **95 MB at n_grid=150**,
*per substep*). At 300 substeps/frame that's ~28 GB of grid tape **per frame** —
infeasible.

**Gradient checkpointing** is the standard, necessary answer and Warp supports
the pattern:

- **Checkpoint granularity = one frame** (store only the per-particle state
  `x,v,F,Jp,cov,yield_state` at each frame boundary — *not* the dense grid, and
  *not* intermediate substeps). Frame state is ~60 floats × N particles ≈
  **~240 MB for 1M particles per checkpoint** — store on host or keep a few on
  device.
- **Backward recomputes** the 300 substeps of a frame from its start
  checkpoint, taping just that frame's substeps (grid buffers live only for the
  duration of one frame's backward), then propagates the gradient to the
  previous frame's checkpoint. Memory is then **O(1 frame of substeps)** of
  tape, not O(all frames). Compute roughly **2× forward** (one recompute).
- **Phase 1 dodges this entirely:** few particles, few frames, ≤ a few hundred
  total substeps → a single flat `wp.Tape` fits in VRAM. Checkpointing is a
  Phase 3 requirement (full-scene, many frames), not a PoC requirement.

**Time:** training cost ≈ (forward + backward ≈ 2–3× forward) × #frames ×
#optimizer-iters. A full 150-frame scene at 1.5 s/frame forward ⇒ ~5–8 s/frame
trained ⇒ ~15–20 min per gradient step on the full sequence — so Phase 3 must
optimize on a **short window (5–15 frames) and a sub-sampled particle set**, not
the whole `.gsq`.

---

## 2. The learning problem — define the loss

### 2.1 Two candidate losses

**(A) Particle/splat-position (trajectory) loss — the tractable Phase 1.**
```
L_pos = Σ_frames Σ_particles  w_p · ‖ x_pred(t) − x_target(t) ‖²
```
where `x_target` is a known/tracked particle (or splat) trajectory. The
gradient path is short and clean: `x_pred` is literally the output of `g2p`
(`particle_x[p] += dt·new_v`), one `wp.to_torch` away. **No renderer in the
loop.** This is also exactly what production consumes — the fuser
(`knn_kabsch.py`, Path B in `docs/deepdive/splat-physics-coupling.md`) advects
splats by **particle position only** (rotation/scale/cov frozen). So a
position loss optimizes precisely the quantity that reaches the screen. This is
the loss for Phases 1–2.

**(B) Differentiable-render / image loss vs video — the research arc.**
```
L_img = Σ_frames  ‖ R(splats(x_pred, Σ_pred, ...)) − I_ref(t) ‖  (+ LPIPS/SSIM)
```
Requires a differentiable rasterizer (`diff_gaussian_rasterization` exists on
the sim side, Path A) **and** the full covariance machinery `Σ' = F Σ Fᵀ`
(`compute_cov_from_F`) so the splats actually deform — which production does
*not* run. Two sub-problems make this Phase 4+:
- **Camera/registration:** the reference video's camera pose vs the splat
  scene must be solved or known.
- **Correspondence-free target:** with no tracked points, the only signal is
  pixels → you must differentiate through the rasterizer *and* the cov update
  *and* the KNN blend (or switch production to Path A). Each is a research
  subproject.

### 2.2 Which is Phase 1

**(A), and specifically against a *synthetic* target first** (a trajectory
produced by the same solver with known parameters). That makes the inverse
problem *identifiable by construction* (the true minimum exists and is the
known param), isolating "does the gradient flow and the optimizer converge"
from "is the model expressive enough to match reality." Only after synthetic
recovery works do we point it at a *real* tracked trajectory, then (Phase 4+)
at pixels.

---

## 3. Optimization — params, init, optimizer, identifiability

### 3.1 What to fit (in difficulty order)

1. **`E`** (Young's modulus) — scalar, monotonic effect on stiffness/oscillation
   frequency. The cleanest single knob. **PoC target.**
2. **`nu`** (Poisson) — couples to `E` via `mu,lam` in `compute_mu_lam_from_E_nu`
   (`mu=E/2(1+nu)`, `lam=Eν/((1+ν)(1−2ν))`); near-incompressible (`ν→0.5`) is
   ill-conditioned (matches the lint's CFL singularity guard). Fit on a bounded
   `nu ∈ [0.1, 0.45]`.
3. **`yield_stress`** — only meaningful for plastic materials; needs the §1.2
   read-only-param refactor first.
4. **`friction_angle` (→ `alpha`)** — sand/Cam-Clay; `alpha` is recomputed from
   the angle (`α=√(2/3)·2sinφ/(3−sinφ)`), differentiable but the
   three-regime Drucker-Prager branch makes the landscape rougher.
5. **`beta`** (Cam-Clay brittleness) — latest, most coupled.

`density` is better treated as **known/measured** (it scales mass and the CFL
dt; fitting it trades off against `E` — see identifiability).

### 3.2 Initialization — from the material prior

Initialize at the **per-material defaults** already in the repo
(`server/gsfluent/schemas/material_defaults.py`, `MATERIAL_DEFAULTS`). These are
known-good, known-stable starting points (the same table the React Material
panel snaps to). The user picks a coarse material ("jelly"), we seed
`E=5000, nu=0.38, …` and let the optimizer refine — so the search starts
*inside* the stable basin, not in the wild.

### 3.3 Optimizer

- **Adam** (torch) on the log of stiffness-like params (`logE`, `log
  yield_stress`) — they span orders of magnitude and are positivity-constrained;
  optimizing in log space gives scale-free steps and free positivity.
- **Bounded params** (`nu`, `friction_angle`) via a `sigmoid` reparam into their
  valid range.
- LR schedule: a few hundred steps is plenty for 1–3 scalars; expect tens of
  steps for the scalar-`E` PoC.

### 3.4 Identifiability & regularization — tie the search to the safe regime

This is where the project closes the loop with the *stability* work:

- **CFL-safe bounds as hard constraints.** The lint's CFL formula
  (`recipe_lint.py:_cfl_dt`, `cfl_dt = 0.6·dx/√(E(1−ν)/((1+ν)(1−2ν)ρ))`) maps
  any `(E,ν,ρ)` to its stability dt. **Constrain the search so the *trained*
  recipe stays `substep_dt ≤ cfl_dt`** — e.g. clamp `E` to the max stiffness the
  recipe's `substep_dt` can stably integrate, or penalize
  `relu(substep_dt − cfl_dt)`. The optimizer then *cannot* wander into the
  divergent regime the linter exists to catch. The forward sim's own clamps
  (`max(σ,0.01)`, the CFL `min`) further fence it in: an unstable trial NaNs the
  loss, which the optimizer avoids.
- **Damping-safe:** keep `grid_v_damping_scale < 1.0` (the lint R1 rule);
  treat it as fixed, not learned, in Phase 1–2.
- **`E`↔`density` degeneracy:** sound speed `c ∝ √(E/ρ)` — many `(E,ρ)` pairs
  give the same wave speed / oscillation. **Fix `ρ`** (measure it, or take the
  material default) so `E` is identifiable from a single trajectory. Fitting
  both needs two excitations (e.g. a drop *and* a squeeze) to disambiguate.
- **Multi-frame, multi-excitation data** improves conditioning: a richer
  deformation history constrains more parameters.
- **Priors as L2-to-default regularizer:** `λ‖θ − θ_default‖²` keeps the fit
  near the known-good material prior unless the data strongly says otherwise —
  exactly the Bayesian "trust the recipe library" stance.

---

## 4. Product integration — output is a tuned RECIPE

### 4.1 Output format

The optimizer's result is **a recipe JSON delta**: the fitted scalar fields
(`E, nu, yield_stress, friction_angle, beta, …`) written into the same flat
recipe schema as `server/recipes/*.json`. It slots in beside the existing
`material`, `n_grid`, `substep_dt`, `boundary_conditions`, etc. **No new schema
is required** — learning produces values for fields that already exist.

### 4.2 Plugs into the existing recipe stack

- **`material_defaults.py`** is the *prior* (init point, §3.2) and the
  *fallback*. The learned recipe can be diffed against the default to show the
  user "we moved E from 5000 → 7300."
- **`recipe_lint.py` is the post-fit gate.** Run `lint_recipe(learned_recipe)`
  before returning it — the same `dt.above_cfl` (R2) and `damping.disabled`
  (R1) rules that guard hand-authored recipes guard learned ones. Because §3.4
  constrains the search to the CFL-safe set, this should pass by construction;
  if it ever fails, that's a bug in the bounds. The lint is wired at
  `server/gsfluent/api/recipes.py:58` (`recipe_lint.lint_recipe(payload)`) — the
  learned recipe flows through the *same* endpoint as any other.
- **`api/schemas.py`** already serves `MATERIAL_DEFAULTS` to the frontend
  (line 24); a learned recipe is just a non-default instance of the same shape.

### 4.3 UX — "upload a clip → get a recipe"

1. User uploads a short clip (or, Phase 1–2, selects a target trajectory /
   reference `.gsq`).
2. Backend tracks points (Phase 4) or ingests the trajectory (Phase 1–2).
3. User picks a coarse material (→ seeds the prior).
4. A short, windowed inverse-fit runs (minutes, not the full sequence).
5. Output: a recipe JSON, lint-clean, with a "fit confidence" + a side-by-side
   "predicted vs target" preview. User accepts → it becomes a normal recipe in
   the library, runnable through the existing pipeline.

The pitch: **"give me a clip of the real material, I'll infer the recipe"** —
replacing the manual `E/nu/yield` guesswork that is the documented source of
the instability/tuning pain.

---

## 5. Phased path & honest feasibility

| Phase | Goal | Loss | Material | Scale | Differentiability work | Feasibility |
|---|---|---|---|---|---|---|
| **0 — PoC** | Recover **one scalar `E`** from a synthetic falling-jelly trajectory | `L_pos` vs synthetic | **jelly only** (elastic FCR, no return map, no in-place writes) | ~10–50 particles, 2–5 frames, flat `wp.Tape` | none beyond SVD guard + start from a deformed frame | **Weekend.** The clean case. |
| **1** | Recover `E, nu` jointly; show the prior+regularizer; lint-gate the output | `L_pos` vs synthetic | jelly | 1k–10k particles, 5–15 frames, flat tape | log/sigmoid reparam; CFL-bound constraint | ~1–2 weeks |
| **2** | Real tracked trajectory; plastic materials | `L_pos` vs tracked points | + metal/plasticine | windowed; **gradient checkpointing**; **read-only-param refactor** (split `yield_state` from `model.yield_stress`) | smoothed yield if jagged | ~1–2 months |
| **3** | Full-scene, many-frame fit; product wiring (upload→recipe UX, lint gate, confidence) | `L_pos`, multi-excitation | all non-degenerate materials | checkpointed full pipeline | per-material state arrays for all hardening/damage writes | ~quarter |
| **4+** | **Video → material** (the frontier) | `L_img` via diff-render | depends on Path A cov machinery | full | differentiable rasterizer + cov update (`Σ'=FΣFᵀ`) + camera reg + correspondence-free | **research arc, open-ended** |

**Honest line in the sand:** Phases 0–1 (synthetic-trajectory inverse for
elastic `E,nu`) are a **prototype**, derisked by existing infrastructure
(`wp.Tape`, `requires_grad` plumbing, analytic `wp.svd3` adjoint). Phase 2 is
**real engineering** (the in-place-write refactor + checkpointing are the gate).
Phase 4 (video→material) is a **research problem** with multiple independent
hard sub-parts (diff-render through the production fuser, camera registration,
correspondence-free pixel loss) and should not be promised as an outcome of
the prototype.

---

## 6. The smallest convincing proof-of-concept (build this first)

**One sentence:** *fit a single scalar `E` to a short falling-jelly trajectory
via `wp.Tape`, recovering a known value from motion alone.*

**Why this exact PoC:**
- **Jelly (material 0) is the only fully-clean path** — `compute_stress_from_F_trial`
  routes it to the `else` (no return map at all), `kirchoff_stress_FCR` is a
  pure func, and **nothing writes `model.*` in place**. So a flat `wp.Tape`
  over a few substeps is correct with *zero* solver refactor.
- **`E` is one scalar with a monotonic, identifiable effect** (given fixed `ρ`).
- **Trajectory loss needs no renderer** — `x_pred` is a direct `g2p` output.
- **It exercises every piece that matters**: torch→warp `requires_grad`
  aliasing of `model.E`, `compute_mu_lam_from_E_nu` (the `E→mu,lam` map, on the
  gradient path), a few `p2g2p` substeps under a tape, `wp.svd3`'s adjoint
  (inside the FCR stress), `tape.backward`, and `E.grad` read back into Adam.

**Concrete setup:**
1. Pick `E_true = 8000`. Init a small jelly block (10–50 particles) at rest,
   drop it under gravity onto a `surface_collider`, run the **un-captured**
   `p2g2p` for ~2–3 frames (a few hundred substeps total — fits one tape).
   Record `x_target(t)`.
2. New run, `E = E_init` (e.g. 3000) aliased from a torch leaf with
   `requires_grad=True`. Forward the same setup under `wp.Tape`.
3. `L = ‖x_pred − x_target‖²`; `tape.backward(L)`; read `E.grad`; Adam step on
   `logE`. Loop ~tens of steps.
4. **Success = `E` converges to ~8000** and `L → 0`. One number, recovered from
   how the jelly moved.

**What it proves to a skeptic:** the whole gradient chain
(optimizer → param → constitutive model → substep dynamics → trajectory → loss
→ back) is real and converges on this codebase's actual solver — i.e. the moat
is *buildable*, not just plausible. Everything after (more params, plastic
materials, checkpointing, real data, video) is scaling a proven loop.

**Start-small guardrails baked into the PoC:** fp32, sort/capture/fp16 off,
start from a slightly-deformed frame (dodge SVD `σ_i≈σ_j` degeneracy), fixed
`ρ`, optimize `logE`, and assert the trial `E` stays under the CFL bound for
the fixed `substep_dt` (reuse `recipe_lint._cfl_dt`).
