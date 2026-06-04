# Proposal: fix late-frame divergence (the residual 21/31)

**Status: RESOLVED + DEPLOYED (2026-06-04) as Patch 10.** Root cause was a
boundary G2P NaN (not `J→0` — see DIAGNOSTIC RESULTS below). Fix = in-kernel
value-domain sanitize extending Patch 9's `clamp_particle_x_to_grid`. Validated
on the production `graph_capture` path: **0/150 non-finite frames** (was ~130),
collapse renders clean. Live on the sim host (backup saved); documented in
`server/patches/UPSTREAM_PATCHES.md` (Patch 10). The sections below are the
investigation record that led there.

---

## ⚠️ DIAGNOSTIC RESULTS (2026-06-04) — the "J→0" premise is REFUTED

Two instrumented runs on the sim host (`b5036643 · earthquake · watermelon`,
200k particles, faithful `graph_capture` + `drop`, per-frame readback of
`F / v / Jp / x / selection`; traces in `/storage/yinshaoxuan/diag_out/`) show
the late-frame NaN is **not** a value singularity:

- **J does not go to 0.** `minJ` floors at ~0.05–0.14 the whole run; `nJ<1e-3 = 0`.
- **velocity does not blow up.** `maxV ≈ 2–7` throughout — no CFL.
- **the apex `sqrt(neg)` at :390 never fires.** `p_trial = kappa/2·(1−J²) ≤
  kappa/2`, so once `p0 > kappa/2` the `p_trial > p0` branch is *gated out*.
  `p0/k > 0.5` held for ~68k particles for 15 frames with **zero** NaN. The
  static "unguarded sqrt" reading missed the `p_trial ≤ kappa/2` guard one
  level up. (Everything below this banner — the apex/stress/CFL hypotheses —
  is kept for the record but is **refuted**.)

**What it actually is — a boundary-contact NaN.** The bad-F particles are all:
ACTIVE (`selection==0`, `drop=0`, so not boundary-dropped bookkeeping); NOT
escaped (`|x| < grid_lim`); sitting at exactly **`|x| = 1.960 = grid_lim −
3·dx`** (the position-clamp margin); and **un-hardened** (`logJp = −0.04` init)
at onset. So material shaken/pressed against the **domain wall** acquires
non-finite F at the boundary margin — ~0.23% of particles, from frame ~18,
**non-cascading** (35→459 over 16 frames; the sim runs to completion, but the
fuser flags every frame containing a non-finite particle as diverged → ~18/35
usable, the production "21/31").

**Birth-probe verdict (graph_capture OFF, per-substep, exits at first
non-finite F).** The exact write site is now confirmed:

```
[BIRTH] frame=10 step=265 nBadF=305 nBadFtrial=305 nBadV=0 nBadC=0
[BIRTH] CLASSIFY born_in_RETURNMAP=0  born_in_G2P(Ftrial already NaN)=305
[BIRTH] first p=259 sel=0 logJp=-0.0400 x=[1.96 1.96 1.96] |x|max=1.9600
[BIRTH] F_trial[p]=[nan x9]  v[p]=[0 0 0]  C[p]=[0 x9]
```

- **100% born in G2P** (`F_trial` already NaN), **0% in the return map**. The
  constitutive path only inherits the NaN via `F = returnmap(F_trial)`.
- First offender at the **domain corner `[1.96,1.96,1.96] = (grid_lim−3·dx)³`**,
  with **`v = 0`, `C = 0`** — no velocity blow-up. So it is the velocity
  *gradient* `new_F` (`F_trial = (I + new_F·dt)·F`, mpm_utils:777/828),
  gathered from zero-mass grid nodes at the domain edge, that goes non-finite.
- `logJp = −0.04` (un-hardened), `sel = 0` (active, not dropped).

So the late-frame NaN is a **boundary G2P artifact**: material shaken to the
grid-edge margin gathers a non-finite velocity gradient from empty edge nodes,
NaN-ing `F_trial` before the constitutive update; the `drop` boundary clamps
these particles to the margin and keeps them active, so the NaN persists and
the fuser flags those frames. `J→0` / Cam-Clay / `neoHookeanBoarden` are fully
exonerated.

### Fix (targeted, NOT deployed — Patch 10 candidate)

The NaN is born at exactly the particles Patch 9's `clamp_particle_x_to_grid`
already touches. Extend that kernel: when a particle is pinned to the boundary
margin (or its `F`/`v` is non-finite), reset `F → identity` (or last finite `F`)
and `v → 0`. These particles are already `v = C = 0` (dynamically dead against
the wall), so the reset is physically harmless and stops the NaN at its
birthplace. This is the value-domain analogue of Patch 9, now empirically
scoped to the exact site (not a blind catch-all).

Alternatives: (b) min-mass guard in the grid kernel (`grid_v = 0` when
`grid_mass < eps`) — fixes the source but touches the hot grid loop;
(c) grid headroom so material never reaches the edge — avoids, doesn't fix.

### Fix VALIDATED (concept, 2026-06-04)

Tested the sanitize as a per-substep op in the diag harness (`F→I, v=C=0,
stress=0` for any non-finite-F particle, run after g2p before the next p2g —
the same slot the clamp kernel occupies), graph_capture OFF, 50 frames:

| metric | unfixed | with boundary-sanitize |
|---|---|---|
| bad frames | onset frame ~10 → 459 particles NaN | **0 / 50 (every frame clean)** |
| `minJ` | ~0.05 | ~0.10–0.19 (compacts + recovers) |
| `maxV` | ~2–7 | ~1–7 (shake dynamics intact) |
| run | flagged diverged ~frame 18+ | completes 50/50 clean |

So the boundary-sanitize eliminates the divergence without flattening the
collapse. **Remaining before deploy:** (1) port the Python sanitize into a Warp
kernel (extend `clamp_particle_x_to_grid`, or a new `sanitize_nonfinite` kernel
launched right after g2p) so it works on the production `graph_capture` path;
(2) run the full 150-frame production-path validation + fuse + eyeball the
render (confirm the reset boundary splats aren't visible); (3) human review;
then ship as Patch 10 in `UPSTREAM_PATCHES.md` with grep-verification, same as
P9.

---

## Problem (original framing — see DIAGNOSTIC RESULTS above for the correction)

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

## Which material `watermelon` actually is (this was the missing fact)

`watermelon` → `material_2_num` → **int 7** (`mpm_solver_warp.patched.py:379`).
Material 7 is **not** FCR/von-Mises. It is:

- return map: `NonAssociativeCamClay_return_mapping` (`mpm_utils.patched.py:318`)
- stress: `kirchoff_stress_neoHookeanBoarden` (`mpm_utils.patched.py:80`, dispatched at :907)

So the earlier framing in this doc — "both return-maps floor σ at 0.01; FCR is
bounded as J→0" — is **true but irrelevant to watermelon**. The 0.01 floor lives
in the von-Mises maps (materials 1/5). Watermelon goes through Cam-Clay, whose
floor is a **no-op**, into a stress model that **is** singular as J→0. Concretely:

1. **The Cam-Clay "floor" does nothing.** `mpm_utils.patched.py:341-344`:
   ```python
   threshold = 0.0
   sigma[0] = wp.max(sigma[0], threshold)   # max(σ, 0.0) clamps only negatives
   ```
   The comment says "Prevent NaN with minimum threshold" but `0.0` lets a
   singular value reach ~0 ⇒ `J = σ₀σ₁σ₂ → 0`. (von-Mises uses 0.01 here.)

2. **The stress divides by J with no guard.** `neoHookeanBoarden` (:102, :110):
   `mu·J^(−2/3)` and `kappa/2·(J − 1/J)` both → ∞ as J→0. With watermelon's
   `kappa ≈ 2778` (= 2μ/3 + λ from E=2000, ν=0.38, via :494) the 1/J term is
   fully live. Evaluated on the **returned** F's J (:894), no floor.

3. **PRIMARY suspect — an unguarded `sqrt(negative)` in the apex projection.**
   `mpm_utils.patched.py:390`:
   ```python
   if p_trial > p0:                              # compaction apex
       Je_new = wp.sqrt(-2.0 * p0 / kappa + 1.0) # radicand < 0 once p0 > kappa/2
   ```
   `p0 = kappa·(1e-5 + sinh(xi·max(−logJp, 0)))` (:347). The hardening
   accumulator `logJp` (`particle_Jp`, init −0.04) **decreases monotonically**
   under sustained compaction (:397, hardening=1 for watermelon). The moment
   `logJp` crosses `−asinh(0.5)/xi ≈ −0.16` (xi=3) → plastic volume ≈ 0.85 →
   `p0 > kappa/2` → radicand negative → **NaN, right here in the return map**,
   before the stress is even reached. The sibling apex (extension, :401) is
   `+1` (always positive) and :465 uses `wp.abs` — **only :390 is unguarded.**
   This is a *slow-variable threshold crossing*, which is exactly why early
   frames are clean and late frames die.

This is upstream-inherited code (the `NonAssociativeCamClay` port ships in the
official GaussianFluent watermelon demo); not a regression we introduced.

## Open questions (the diagnostic discriminates these)

The static read gives two compaction-driven NaN sites in the material-7 path.
The diagnostic decides which fires *first* on `b5036643`:

1. **(primary)** Apex `sqrt(negative)` at :390 — is the first non-finite
   particle in the `p_trial > p0` branch with `p0/kappa > 0.5` and
   `logJp < −0.16`?
2. **(secondary)** Stress divide — is it instead an elastic / inside-cap
   particle (no apex projection) carrying `J → 0` into `J^(−2/3)` / `1/J`?
3. Or is it **CFL** after all — `substep_dt` fixed at init from initial
   `E,ν,ρ`, material stiffens under compression (`c=√((λ+2μ)/ρ)` rises),
   fixed `dt` goes super-CFL, `|v|`/‖F‖ blow up from the *large* side? (Less
   likely given the clean :390 mechanism, but the diagnostic logs `|v|` too.)

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
    logJp = state.particle_Jp[p]                        # Cam-Clay hardening state
    kappa = model.kappa[p]
    p0    = kappa * (0.00001 + wp.sinh(model.xi * wp.max(-logJp, 0.0)))
    ratio = p0 / kappa                                  # NaN at :390 once > 0.5
    nan_v = (v[0] != v[0]) or (v[1] != v[1]) or (v[2] != v[2])
    nan_J = (J != J)
    if nan_v or nan_J or J < 1.0e-7 or wp.length(v) > 1.0e6 or ratio > 0.5:
        if wp.atomic_add(flagged, 0, 1) == 0:           # first offender only
            wp.printf("[diag] p=%d J=%g |v|=%g logJp=%g p0/kappa=%g mat=%d\n",
                      p, J, wp.length(v), logJp, ratio, model.material[p])
```

The added `logJp` / `p0/kappa` fields are what separate the **primary** (:390
apex `sqrt(negative)`, fires when `p0/kappa > 0.5`) from the **secondary**
(stress divide on `J→0`) and from **CFL** (huge `|v|`). Run one short
`b5036643 · earthquake · watermelon` to the failing frame; the print pins which.

## Step 2 — Candidate fixes (apply the one the diagnostic points to)

**(a) Guard the apex `sqrt` — primary fix, if the diagnostic shows `p0/kappa > 0.5`**
at `mpm_utils.patched.py:390`:
```python
-     Je_new = wp.sqrt(-2.0 * p0 / kappa + 1.0)         # radicand < 0 → NaN
+     Je_new = wp.sqrt(wp.max(-2.0 * p0 / kappa + 1.0, 1.0e-6))
```
Physically this **saturates the compaction cap** (the densest the material can
plastically pack) instead of letting it go imaginary. Mirrors the `wp.abs`
already used at the sibling site :465. Smallest possible diff, no new kernel.

**(b) Give Cam-Clay a real σ floor — if the diagnostic shows an elastic / J→0
stress NaN.** At :341, change the no-op floor to match von-Mises:
```python
-     threshold = 0.0
+     threshold = 0.01      # was a no-op (max(σ,0) clamps only negatives)
```
Bounds `J ≥ 0.01³ = 1e-6`, keeping `J^(−2/3)` / `1/J` in `neoHookeanBoarden`
finite. Caps compression slightly → validate the look.

**(c) Value-sanitize kernel** — backstop, the value-domain analogue of the
position `drop`. Catches whatever (a)/(b) miss before P2G:
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
`clamp_particle_x_to_grid`. Quietly drops the few worst particles.

**(d) Adaptive `substep_dt`** — only if the diagnostic shows CFL (huge `|v|`,
finite `p0/kappa`): recompute the CFL bound from *current* stiffness. Most
invasive; least likely given the clean :390 mechanism.

## Step 3 — Validation gate

- Re-run `b5036643 · earthquake · watermelon`, `frame_num=150`.
- **Pass**: 150/150 (or ≥ the chosen `min_usable` with the destruction intact).
- Diff the rendered collapse against the pre-fix run — confirm the look is not
  degraded (esp. for fix (a), which changes compression).
- Then, and only then: ship as **Patch 10** in `UPSTREAM_PATCHES.md` with its
  own grep-verification, same as P9.

## Why this is the right shape

Patch 9 contained one NaN source by bounding a quantity (position) without
touching the dynamics. The static read has now localized the second source to
the **material-7 Cam-Clay path** — most likely the unguarded apex `sqrt` at
:390, gated on the slow `logJp` accumulator (which is why it's late-onset). Fix
(a) is a one-line radicand clamp that saturates the compaction cap rather than
letting it go imaginary. The diagnostic still runs first — it confirms :390 vs
the stress-divide vs CFL before any line changes — but we are no longer guessing
*which* quantity; we are confirming *which of two named, code-located sites*
fires first.
