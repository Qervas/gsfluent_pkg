# Proposal: fix late-frame divergence (the residual 21/31)

**Status: NOT DEPLOYED.** Solver-physics change → requires human review + a GPU
validation run before any deploy. The disk on the sim host must be freed first.

---

## Problem

After the orientation fix (Z-up model), `earthquake · watermelon` on the
re-uploaded model `b5036643` still produces only **21/31** usable frames — the
opening frames are fine, the late frames go NaN. This is **distinct** from the
grid-escape NaN that Patch 9 (boundary clamp/drop) already fixed.

- **Patch 9 (deployed)** bounds a particle's **position** so the P2G index is
  always valid — it stops "writing to a bad *address*."
- The residual is a particle at a **valid** position scattering a **non-finite
  value** — "writing a bad *value* to a good address." A position clamp is
  structurally blind to it.

So the residual is a constitutive/numerical blow-up at large deformation, not a
grid escape.

## What's already in the solver (so don't re-add it)

Both return-maps **already floor the singular values** (so the naive `log σ`
singularity is NOT the cause):

```python
# mpm_utils.patched.py:131 (von_mises_return_mapping) and :179 (…_with_damage)
wp.svd3(F_trial, U, sig_old, V)
sig = wp.vec3(wp.max(sig_old[0], 0.01),
              wp.max(sig_old[1], 0.01),
              wp.max(sig_old[2], 0.01))   # already prevents log(0)/NaN
```

And `compute_kirchoff_stress` (FCR, line 16) is `2μ(F−R)Fᵀ + λJ(J−1)I` — **bounded
as `J→0`** (no `F⁻ᵀ`/`1/J`). So an FCR material is not obviously singular either.

This is exactly why we must **diagnose, not guess** (the project's own rule —
the damping saga was the cost of skipping it).

## Open questions (the diagnostic answers these)

1. Which quantity goes non-finite **first** at the failing frame — `v`, `F`,
   `J=det(F)`, the stress, or a `svd3` output?
2. Is the failing particle on the **elastic** return path? That path does
   `return F_trial` **un-floored** (the `0.01` floor is applied only to the
   *local* `sig` used for the yield check, not to the returned `F_trial`), so a
   heavily-compressed-but-not-yielding particle can keep `J→0`.
3. Which **stress model** does `watermelon` use? FCR is bounded; the
   neo-Hookean (`J^(−2/3)`, line 31) and Cam-Clay (`1/J`, line 110) paths are
   not.
4. Is it a value NaN at all, or a **CFL** instability — `substep_dt` was fixed
   at init from the *initial* `E,ν,ρ`, but compression stiffens the material
   (`c=√((λ+2μ)/ρ)` rises) so the fixed `dt` can become super-CFL late.

## Step 1 — Diagnostic (cheap, no physics change, ship first)

A kernel run after the constitutive update each substep; logs the **first**
offender's state once, then the run can be inspected:

```python
@wp.kernel
def diag_nonfinite(state: MPMStateStruct, model: MPMModelStruct,
                   flagged: wp.array(dtype=wp.int32)):
    p = wp.tid()
    F = state.particle_F[p]
    v = state.particle_v[p]
    J = wp.determinant(F)
    nan_v = (v[0] != v[0]) or (v[1] != v[1]) or (v[2] != v[2])
    nan_J = (J != J)
    if nan_v or nan_J or J < 1.0e-7 or wp.length(v) > 1.0e6:
        if wp.atomic_add(flagged, 0, 1) == 0:           # first offender only
            wp.printf("[diag] p=%d J=%g |v|=%g mu=%g yield=%g\n",
                      p, J, wp.length(v), model.mu[p], model.yield_stress[p])
```

Run one short `b5036643 · earthquake · watermelon` to the failing frame; the
print pins the cause (small `J`? huge `v`? which material state?). **One run
decides which fix below applies.**

## Step 2 — Candidate fixes (apply the one the diagnostic points to)

**(a) Floor the elastic return path too** — if the diagnostic shows un-yielded
particles with `J→0`:
```python
  else:
-     return F_trial                                   # un-floored, J can → 0
+     return U * wp.diag(sig) * wp.transpose(V)         # sig = max(sig_old, 0.01)
```
*Caveat:* this caps compression → a real (small) physics change; validate the
look doesn't change.

**(b) Value-sanitize kernel** — the direct analogue of the position `drop`, for
*values*. A catch-all that contains one degenerate particle before P2G:
```python
@wp.kernel
def sanitize_nonfinite(state: MPMStateStruct):
    p = wp.tid()
    F, v = state.particle_F[p], state.particle_v[p]
    J = wp.determinant(F)
    if (J != J) or (J < 1.0e-7) or (v[0] != v[0]) or (wp.length(v) > 1.0e6):
        state.particle_mass[p] = 0.0                    # deactivate (like drop)
        state.particle_v[p]    = wp.vec3(0.0, 0.0, 0.0)
        state.particle_F[p]    = wp.identity(n=3, dtype=float)
```
Launch after the constitutive update, before P2G — symmetric to
`clamp_particle_x_to_grid`. Extends "all-finite" to late frames at the cost of
quietly dropping the few worst particles.

**(c) Adaptive `substep_dt`** — if the diagnostic shows CFL (huge `c`/`v`, not a
value NaN): recompute the CFL bound from the *current* stiffness instead of the
init-time one. More invasive; only if (a)/(b) don't hold.

## Step 3 — Validation gate

- Re-run `b5036643 · earthquake · watermelon`, `frame_num=150`.
- **Pass**: 150/150 (or ≥ the chosen `min_usable` with the destruction intact).
- Diff the rendered collapse against the pre-fix run — confirm the look is not
  degraded (esp. for fix (a), which changes compression).
- Then, and only then: ship as **Patch 10** in `UPSTREAM_PATCHES.md` with its
  own grep-verification, same as P9.

## Why this is the right shape

Patch 9 contained one NaN source by bounding a quantity (position) without
touching the dynamics. The natural completion is the same move for the other
source — but **which** quantity to bound (`J`? `v`? `dt`?) is the open question,
and the diagnostic, not a guess, decides it.
