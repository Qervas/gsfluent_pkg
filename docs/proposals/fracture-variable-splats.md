# Proposal: fracture / variable splat count — the structural frontier

**Status:** design only (no code, no GPU, no deploy). Read-only research.
**Date:** 2026-05-27.
**Scope:** the single biggest *capability* ceiling in gsfluent — making fracture /
demolition / emission render correctly instead of smearing. This is blocked in
**two layers at once** (the coupling/fuser layer and the `.gsq` codec layer) plus a
**third gating layer** (what the MPM sim can actually expose). This doc designs across
all three and gives a phased, smallest-shippable-first path.

Sources (read for this doc):
`docs/deepdive/splat-physics-coupling.md`, `docs/deepdive/representation-and-frontier.md`,
`docs/deepdive/mpm-solver.md`, `server/gsfluent/core/fusers/knn_kabsch.py`,
`server/gsfluent/core/codecs/gsq.py`, `server/gsfluent/core/codecs/gsq_prune.py`,
`server/recipes/demolition.json`, `server/gsfluent/schemas/boundary.py`,
`server/patches/UPSTREAM_PATCHES.md`, `server/patches/gs_simulation_building.patched.py`,
`frontend/src/lib/gsq/format.ts`.

---

## 0. The problem in one paragraph

Production coupling (Path B in the coupling deep-dive) skins **683k reference splats**
onto **~200k sim particles** with an inverse-distance K-NN map that is **frozen at sim
frame 0** (`knn_kabsch.py::build_correspondence`). Each splat is moved by the
weighted average of its 8 nearest particles' displacements. When a crack opens, a
splat that straddles the crack still has neighbors on **both** sides; its blended
displacement averages two diverging motions and the splat **stretches across the gap**
— the "ghost web." Meanwhile the `.gsq` v2 codec asserts a **fixed splat count and
fixed per-splat identity for the whole sequence** (`gsq.py`: `if v.shape[0] != n_splats:
raise CodecError`; deltas key on array index, `xyz[i,t] - xyz[i,t-1]`). So even if the
fuser *wanted* to split a splat into two children or add splats on a fresh fracture
face, the container literally cannot carry a changing population. Fracture is therefore
forbidden by construction in both layers simultaneously.

The honest framing from the representation deep-dive holds: **`.gsq` is a rigid-skin
deformation codec, not a 4D-radiance codec.** This proposal does not try to make it a
neural 4D codec; it minimally extends the explicit/lossless/GPU-free design point to
admit (a) re-binding across cracks and (b) birth/death of splats.

---

## 1. What "fracture" actually decomposes into

It is critical to separate three sub-problems that get conflated under "fracture,"
because they have wildly different difficulty:

| Sub-problem | What it needs | Difficulty |
|---|---|---|
| **(A) Stop smearing across an existing crack** | re-partition a splat's K-NN binding to one side once its neighbors diverge | **engineering** — fully tractable in the fuser, CPU-only, no sim change |
| **(B) Split one splat into children that follow separate fragments** | duplicate a splat, re-bind each child 1-NN to a different fragment; codec must carry a *growing* count | **engineering** (codec) + **engineering** (fuser); needs `.gsq` v3 |
| **(C) Generate *new* surface on a fresh fracture face** | invent color/normal/opacity/scale for interior material newly exposed when the body splits | **research-open** — there is no ground-truth appearance for never-seen interior |

(A) kills the worst visible artifact and needs **neither** a codec change **nor** a sim
change. (B) needs the v3 codec but the geometry it produces is just *re-bound copies of
existing splats* — no invented appearance, so it is still engineering. (C) is the
genuinely hard, partly-research part and is explicitly **deferred** to the last phase
with a low-fidelity stopgap. Sequencing the phases along A → B → C is the spine of this
proposal.

---

## 2. Layer 1 — Coupling / fuser: fracture-aware re-binding

All of this lands in `server/gsfluent/core/fusers/knn_kabsch.py`. It is pure
numpy + scipy, runs on CPU after the sim exits, and preserves Path B's operational
advantages (decoupled, deterministic, degrades gracefully).

### 2.1 Detecting fracture from the particle field (no sim change)

The fuser already holds everything it needs: `state.knn_idx (n_ref, K)`, the rest sim
positions `sim_xyz_t0_kept`, and each per-frame `particle_frame`. Fracture is a **local
divergence of a splat's bound neighbors** and can be measured purely from positions:

**Per-frame pairwise-stretch test (the core signal).** For reference splat `i` with
neighbor set `N_i = knn_idx[i]` (K particles), compute the rest pairwise distances
`d0_{jk} = |p0_j - p0_k|` (once, at build time) and the current pairwise distances
`dt_{jk} = |p_j - p_k|` (per frame). The splat is *straddling a crack* when the **max
relative stretch** across its neighbor pairs exceeds a threshold:

```
stretch_i = max over (j,k) in N_i of  dt_{jk} / (d0_{jk} + eps)
fractured_i = stretch_i > TAU_STRETCH      # e.g. TAU_STRETCH = 1.5–2.0
```

Intuition: under elastic/plastic deformation neighbors stay roughly equidistant
(stretch ≈ 1); when a crack runs between two neighbors they fly apart (stretch ≫ 1).
This is the cleanest fracture proxy available from positions alone, and it directly
matches the deep-dive's "a splat straddling a crack averages two diverging motions."

**Cost.** K=8 ⇒ 28 unique pairs per splat. 683k × 28 = ~19M distance evaluations per
frame, fully vectorizable in numpy (gather `particle_frame[knn_idx]` → `(n_ref, K, 3)`,
broadcast-subtract to `(n_ref, K, K, 3)`, norm). ~150–300 ms/frame in pure numpy on the
full 683k cloud; after pruning to the retention-0.98 working set (~200k) it is well
under 100 ms/frame. This is **additive to the existing fuse cost**, not a multiplier,
and the fuse step is already off the timed sim loop.

**Optional richer signal (Phase 2+):** if `--output_cov` is enabled (Patch 4 already
exists), the per-particle deformation gradient's volume ratio `J = det(F)` or the
plasticine **damage state** (`von_mises_return_mapping_with_damage` softens `yield`
until the particle goes fully fluid — this is *the* fracture/melt mechanism in the
solver, see `mpm-solver.md` §2) gives a *material* fracture signal rather than a
*kinematic* one. But the kinematic stretch test needs **zero sim changes**, so it is
the Phase-1 default.

### 2.2 Re-binding: which side does the splat follow?

Once `fractured_i` is true, the splat must commit to **one** side of the crack instead
of averaging both. Two re-binding strategies, cheapest first:

**(R1) Hard 1-NN snap (Phase 1).** Replace the splat's K weights with a one-hot on its
*single* nearest neighbor (`argmin dt over N_i`, or simply `knn_idx[i, 0]` if neighbors
stay distance-sorted). The splat now rigidly follows one particle — no averaging, no
ghost web. Cheap, lossless to the codec (still one splat, still position-only), and it
*immediately* kills failure mode #3. Downside: a hard binary flip can pop visibly on
the frame the threshold trips; mitigate with hysteresis (§2.4).

**(R2) Sided soft weights (Phase 1.5).** Instead of one-hot, **zero out the neighbors on
the far side** and renormalize the rest. Partition `N_i` into two clusters by the sign
of each neighbor's displacement projected onto the dominant separation axis (the top
singular vector of the neighbors' current relative positions, or simply k-means k=2 on
`particle_frame[N_i]`). Keep the cluster the splat's *rest* position is closer to; drop
the other. Smoother than R1 (still a weighted blend, just within one fragment) at modest
extra cost (one tiny SVD or 2-means per fractured splat — only the fractured subset, not
all 683k).

Both R1 and R2 keep the splat **count fixed**, so they ship on the **existing v2 codec
with zero format change**. This is the highest-leverage, lowest-risk slice.

### 2.3 Splitting a splat into children (needs v3 codec — Phase 3)

R1/R2 make a straddling splat pick a side, which leaves the *other* side under-covered
(a splat that should have become two surfaces is now one). The full fix is to **split**:
when `fractured_i` first trips and the two neighbor clusters are both substantial,
duplicate splat `i` into `i_a` (bound to cluster A) and `i_b` (bound to cluster B), each
inheriting the parent's static appearance (rgb/opacity/scale) and rest quaternion, each
with its own 1-NN/sided binding. The child appearance is a **copy of the parent** — no
invented appearance, so this is still engineering, not research. The new splat changes
`n_splats` mid-sequence ⇒ requires the v3 codec's append region (Layer 2, §3).

This is where the **`release_particles` emission case** also lands: a BC that *adds*
particles mid-sim (spray/foam/spall) produces particles with no frame-0 reference splat.
Once v3 supports birth, the fuser can author a newborn splat per emitted particle
(appearance copied from the nearest existing splat, or a recipe-default material color).

> **Demolition recipe note (precision).** The shipped `server/recipes/demolition.json`
> uses `release_particles_sequentially` with `num_layers: 40` — this **releases
> pre-existing clamped particles layer-by-layer** (the `particle_selection` gate flips
> from frozen to active), so the particle *count is constant* there. That recipe is the
> ideal **Phase-1 test case for re-binding (A)** because cracks open between released
> and still-clamped layers **without** any count change — exactly what R1/R2 handle on
> the v2 codec. The *count-changing* emission variant (true `release_particles` that
> adds particles) is the Phase-3 birth case. (Also flagged: the recipe's BC fields
> `start_position/end_position/num_layers` do **not** match `boundary.py`'s
> `release_particles_sequentially` schema `axis/start_time/interval` — a pre-existing
> schema drift worth fixing separately; not in scope here.)

### 2.4 When to re-bind: per-frame vs event, and hysteresis

- **Per-frame, monotone-latching (recommended).** Evaluate `stretch_i` every frame, but
  once a splat latches to "fractured + side S," **keep** that decision for the rest of
  the sequence (cracks don't heal in these recipes). This avoids per-frame flip-flop and
  keeps the binding *piecewise-constant in time* — important because the codec's delta
  scheme is happiest when per-splat behavior is temporally coherent.
- **Hysteresis** on the threshold: trip at `TAU_STRETCH = 1.8`, but only re-bind a splat
  whose stretch has exceeded the threshold for `≥2` consecutive frames, to reject a
  single noisy FLIP frame. (The solver default `flip_pic_ratio=0.7` for plasticine is
  already on the dissipative side, so noise is modest.)
- **Latch table** is itself a tiny per-splat artifact: `frac_frame[i]` (first frame the
  splat fractured, or −1) + `side[i]` (which cluster). Computed in one forward pass over
  frames during fuse; O(n_ref) memory.

### 2.5 Cost summary (fuser)

| Step | Cost | Frequency |
|---|---|---|
| build rest pairwise dists `d0` | 683k × 28, once | build-time |
| per-frame `stretch_i` | 683k × 28 numpy, ~100–300 ms | per frame |
| R1 re-bind (one-hot) | O(#fractured), negligible | per frame on latch |
| R2 sided weights (2-means/SVD) | O(#fractured × K), only fractured subset | per frame on latch |
| split (Phase 3) | O(#newly-fractured), append rows | per frame on latch |

Whole thing stays CPU-numpy, deterministic, off the sim's timed loop. No GPU.

---

## 3. Layer 2 — Codec: `.gsq` v3 with variable splat count

Lands in `server/gsfluent/core/codecs/gsq.py` (format + encoder), the pruner
`gsq_prune.py`, and both decoders (`frontend/src/lib/gsq/{format,decoder,dequant}.ts`
and `frontend/python/splat_ring.py`). **Backward-compat is mandatory: v1 and v2 must
still decode unchanged** (the parser already gates on `version`; v3 adds a branch).

### 3.1 What v2 assumes (and v3 must break)

From `representation-and-frontier.md` §2, v2 bakes four assumptions; v3 targets the
first three:

1. **Fixed splat count** — header has one `n_splats`; static block one row/splat. **(break)**
2. **Fixed identity/ordering** — splat `i` is the same physical splat every frame; deltas
   key on index. **(break — key on stable ID, not index)**
3. **No birth/death** — no alive mask, no append, no free-list. **(add all three)**
4. Sequential monotone decode — keep as-is (v3 still optimizes forward playback).

### 3.2 Design principle: stable IDs under the deltas, alive-mask on top

The delta math (`a + (b-a) ≡ b mod 2^16`) is the crown jewel — it gives bit-exact
lossless reconstruction and prune-commutes-with-delta. v3 must **preserve the delta math
byte-for-byte** and add population changes *around* it, not inside it.

The key idea: **deltas key on a stable per-splat ID, not on array position.** A splat
keeps its ID from birth to death. The per-frame payload is laid out in **ID-sorted
order over the currently-alive set**, so that for a splat alive in both `t-1` and `t`,
its row is at a computable position in both frames and `delta = q[t] - q[t-1]` is taken
between the *same ID's* rows. Births append; deaths drop out of the alive set.

### 3.3 Concrete format changes (v3)

**Header (80 B, keep size; repurpose reserved bytes).** `version = 3`. `n_splats`
becomes **`n_splats_max`** = the total number of distinct IDs that ever exist (size of
the static table; see below). Add into the 24 reserved bytes: `id_table_offset (u64)`,
`id_table_size (u32)` pointing at a new ID/lifetime table. Header stays 80 B → v1/v2
parsers that ignore the new version still reject cleanly (they already `raise` on
version ∉ {1,2}).

**Static table becomes a *growing* table keyed by ID.** Today the static block is
`rgb_f16 ++ opacity_u8 ++ scales_f16`, one row per splat, decoded once. In v3 it has one
row per **ID** (`n_splats_max` rows), in ID order. A splat born at frame `t` has its
appearance row written here; born splats just extend the table. Decoded once, exactly
like v2 — births don't change appearance over time, they only change *when* a row
becomes live. (Appearance-change-over-time, e.g. burning/wetting, is explicitly still
out of scope — that's a different bet.)

**New ID/lifetime table** (`id_table_offset`): for each ID, `(birth_frame u32,
death_frame u32)` (death = `n_frames` if it lives to the end). `2 × 4 × n_splats_max`
bytes, zstd-compressed. This is the **alive mask in run-length form** — far more compact
than a per-frame bitmask because births/deaths are sparse and monotone-latched (a splat
born at frame `t` is alive `[t, death)`). The decoder reconstructs "alive at frame `t`"
= `{ID : birth ≤ t < death}`.

**Per-frame chunk layout (v3).** Each frame chunk stores the dynamic payload (xyz int16
+ quat int16, exactly as v2) for the **alive set at that frame, in ID order**, split into
two regions:

```
frame chunk t = zstd(
    [ carried region ]   # IDs alive in BOTH t-1 and t, in ID order
                         #   keyframe: absolute int16 ; delta frame: modular int16 vs t-1
    [ append region  ]   # IDs born exactly at t (not alive at t-1), in ID order
                         #   ALWAYS absolute int16 (no previous frame to delta against)
)
```

Deaths need no payload — a dead ID simply isn't in frame `t`'s alive set, so it's absent
from the carried region. The frame-index entry's `flags` gains bits beyond bit0
(keyframe): `bit1 = has_births`, `bit2 = has_deaths` (lets the decoder skip alive-set
recomputation on frames where the population is unchanged — the common case).

**Why this preserves the delta math.** For a carried splat, its row in the carried
region of frame `t` and frame `t-1` are both at "rank within the alive set, in ID
order." Because the alive set only changes at births/deaths, between two adjacent frames
with no population change the carried region is *identical in layout to v2*, and the
delta is byte-for-byte the v2 delta. On a birth/death frame the decoder recomputes the
alive-set ranks once (from the ID table), then deltas the carried region against the
previous frame's matching IDs. **Bit-exact-lossless is preserved**: deltas are still
modular int16 between the same physical splat's consecutive absolutes.

**Keyframes re-baseline the live set.** As in v2, frame 0 and every K=30 are keyframes
storing absolute int16 for the *entire current alive set* (carried region holds
everything absolute, append region empty unless a birth coincides). A scrub-jump walks
back to the nearest keyframe and replays carried-region deltas + applies births/deaths
from the ID table along the way — same cost class as v2's keyframe walk.

### 3.4 Pruning under v3 (`gsq_prune.py`)

Today pruning slices on **array index** and works because index == identity (significance
is static). Under v3 it must **slice on ID**: `prune_gsq_bytes` keeps a set of IDs, drops
those rows from the static table + ID table, and drops them from every frame's
carried/append regions. Significance is still `opacity × volume` from the static table
(now per-ID), so the *which-to-keep* logic is unchanged; only the slicing index space
moves from position to ID. Prune-commutes-with-delta still holds because, as in v2, we
slice the *stored* int16 payload (keyframe or delta) without dequantizing — a kept ID's
delta is independent of dropped IDs' deltas.

> One subtlety: a pruned ID must be removed from the alive set *consistently*, so the
> carried-region ranks shift. The cleanest implementation re-derives ranks from the
> surviving ID table after pruning, exactly as the decoder does — keeping prune and
> decode using one shared "alive-rank" routine so they never drift (mirror the existing
> discipline where `prune_to_count`/`prune_to_retention` share one entry point).

### 3.5 zstd interaction

Unchanged in spirit: each frame chunk is still one zstd blob (carried ++ append), still
byte-range addressable, still ~3 ms/frame sequential decode for the carried region. The
append region is small (only newborn splats) and absolute, so it compresses like a v2
keyframe slice. The ID/lifetime table compresses extremely well (monotone, sparse). Net
wire cost over v2 for a *non-fracturing* sequence is ~0 (no births ⇒ append regions
empty, ID table is one run). **v2 sequences can even be re-emitted as v3 byte-identical
in the dynamic payload** — v3 is a strict superset.

### 3.6 Backward compatibility

- **Decode:** `parseHeader` adds `version === 3` (today it throws on anything ∉{1,2}); v3
  branch reads the ID table; v1/v2 paths untouched. The Python `splat_ring` and TS
  `decoder.ts` each grow a v3 reconstruction branch alongside the existing v1/v2 ones.
- **Encode:** v3 is opt-in (the fuser signals "this sequence has population changes"); a
  fixed-count sequence still encodes as v2 by default, so nothing regresses. The bit-exact
  test harness (`verify_gsq_v2.py`) gets a v3 sibling.

---

## 4. Layer 3 — MPM side: what the sim must expose (and what's research-open)

From `mpm-solver.md`: the solver **already models material failure today** for the
relevant materials. This is the good news that makes Phase 1 cheap.

### 4.1 What exists today

- **Plasticine (material 5)** uses `von_mises_return_mapping_with_damage`: the yield
  stress is *softened* each plastic step (`yield -= softening·‖Δε‖`) and once `yield ≤ 0`
  the particle goes **fully fluid** (`μ = λ = 0`). This is *the* fracture/melt mechanism,
  and `server/recipes/demolition.json` uses exactly this material (`yield_stress: 500`,
  `softening: 20`). So the demolition scene **already produces a damage field** — the sim
  doesn't need new physics for Phase 1.
- **Deformation gradient F** is maintained per particle every substep and
  `export_particle_cov_to_torch()` exists; **Patch 4 (`--output_cov`)** already ferries
  per-particle 6-float covariance into each `sim_*.ply`. So `J = det(F)` (volume ratio,
  a dilation/void cue) and the full anisotropic deformation are *exportable today* with a
  flag that's already implemented.
- **`particle_Jp`** (Cam-Clay hardening log) and the damage-softened yield are per-particle
  scalars that *could* be exported the same way `--output_cov` exports covariance.

### 4.2 What's tractable vs genuinely research-open

| Need | Status |
|---|---|
| Kinematic crack detection (neighbor divergence) | **tractable, zero sim work** — fuser-side, §2.1. Phase 1 default. |
| Material damage signal (yield→0 / `Jp` / `J=det F`) | **tractable, small sim work** — add a `--output_damage` flag mirroring `--output_cov` (one scalar/particle into the ply). Engineering, ~Patch-4-sized. |
| Particle-level "this particle separated from that one" | **tractable-ish** — derivable from neighbor divergence; MPM has no explicit connectivity, so there's no native crack-face primitive. |
| **Correct new fracture-face geometry** (where do the splats on a freshly-exposed interior surface come from, with correct color/normal/opacity?) | **RESEARCH-OPEN.** MPM is meshless; it has no surface and no notion of "the inside of the wall is grey concrete." There is no ground truth for never-observed interior appearance. |

The last row is the crux and the reason (C) is deferred. MPM produces a **particle cloud**,
not surfaces; "the fracture face" is an emergent gap in a point cloud, not a geometric
primitive the solver hands you. Producing *plausible* new interior surface is a generative
problem (texture/appearance synthesis on exposed interior), and producing *correct* one is
ill-posed (the data was never captured). Be honest in any roadmap: Phases 1–3 make existing
splats stop smearing and let them split/birth with **copied** appearance; **inventing
fresh-face appearance is a separate research bet**, not part of shipping fracture.

### 4.3 Recommended sim asks, by phase

- **Phase 1:** none. Kinematic detection only.
- **Phase 2 (optional richer signal):** `--output_damage` flag (mirror Patch 4) writing
  one float/particle (softened-yield ratio or `J=det F`). Lets the fuser gate fracture on
  *material* failure, not just kinematics → fewer false positives on fast-but-intact motion.
- **Phase 3 (birth):** for the emission `release_particles` case, the sim must expose
  **per-particle birth frame** (which substep a particle was added). For the
  release-clamped case (demolition recipe) no new particles appear, so no sim ask.
- **Phase 4 (faces, research):** out of scope; would need either a meshing pass on the
  particle cloud (e.g. marching cubes on the density field the filler already builds in
  `particle_filling/filling.py`) to find new surfaces, plus appearance synthesis.

---

## 5. Phased path (smallest shippable first)

### Phase 1 — Fracture-aware re-binding on the v2 codec **(ship first)**
**The single highest-leverage slice.** Pure fuser change; **no codec change, no sim
change, no GPU.**
- Add `d0` pairwise-rest-distance precompute in `build_correspondence`.
- Add per-frame `stretch_i` test + monotone latch + hysteresis in `fuse_frame`.
- On latch, **R1 hard 1-NN snap** (Phase 1.0) then **R2 sided weights** (Phase 1.5).
- Validate on `demolition.json` (release-clamped ⇒ constant count ⇒ fits v2 exactly).
**Unlocks:** kills the ghost-web smear (failure mode #3) for the whole demolition class.
**Risk:** threshold tuning; pop on latch frame (mitigated by hysteresis). All lossless to
the codec. **Effort:** days–1 week. **Type:** engineering.

### Phase 2 — Material-gated detection (optional)
- Add `--output_damage` sim flag (mirror existing `--output_cov` Patch 4) → one
  float/particle.
- Fuser gates fracture on damage ∨ stretch (fewer false positives).
**Unlocks:** cleaner re-binding on materials with real damage (plasticine/demolition).
**Risk:** requires re-touching the upstream patched sim file (already a documented patch
surface). **Effort:** ~1 week incl. sim patch. **Type:** engineering (small sim + fuser).

### Phase 3 — `.gsq` v3 variable count: split + birth/death
- Implement v3 format (§3): stable IDs, ID/lifetime table, carried+append regions,
  alive-mask, keyframe re-baseline.
- Encoder, both decoders (TS + Python), prune-on-ID, v3 bit-exact harness.
- Fuser emits splits (copy-appearance children) on fracture latch; emits births for the
  emission `release_particles` case.
**Unlocks:** the full capability — splats split with both fragments covered; emission/spray
representable. The capability ceiling the whole format was missing.
**Risk:** highest of the engineering phases (touches format + both decoders + pruner). De-
risk by keeping v3 a strict superset (v2 sequences re-emit byte-identical in the dynamic
payload) and reusing one shared alive-rank routine across encode/decode/prune. **Effort:**
2–4 weeks. **Type:** engineering.

### Phase 4 — Correct fracture-face appearance **(research, deferred)**
- Generate plausible interior surface on fresh faces (meshing the filler density field +
  appearance synthesis).
**Risk:** ill-posed (no ground-truth interior). **Type:** research-open. Not on the
shipping path; documented as a separate bet.

### Sequencing rationale
Phase 1 delivers the most-visible win (no ghost web) at the least risk and **zero
format/sim churn**, on a scene that already exists (`demolition.json`). Phases 2–3 layer
capability on top without invalidating Phase 1. Phase 4 is honestly walled off as research.

---

## 6. Data-structure / format change summary

| Artifact | v2 today | v3 change |
|---|---|---|
| Header `n_splats` | fixed count | `n_splats_max` (total distinct IDs ever) |
| Header reserved (24 B) | zeros | `id_table_offset u64`, `id_table_size u32` |
| Static block | one row/splat, decoded once | one row/**ID**, grows with births, decoded once |
| ID/lifetime table | — | `(birth_frame u32, death_frame u32) × n_splats_max`, zstd, run-length alive mask |
| Frame chunk | int16 xyz+quat for all splats | **carried region** (IDs alive in t-1∧t, ID-ordered, delta/abs) ++ **append region** (IDs born at t, ID-ordered, absolute) |
| Frame flags | bit0 keyframe | + bit1 has_births, bit2 has_deaths |
| Delta math | `b-a mod 2^16` on index | **unchanged**, keyed on alive-rank of stable ID |
| Pruning | slice on index | slice on **ID** (shared alive-rank routine) |
| Fuser binding | frozen K-NN @ frame 0 | + per-frame stretch test, latch, R1/R2 re-bind, split/birth |

---

## 7. Risks & honesty

- **Phase 1 false positives:** fast-but-intact motion (a whole wall translating quickly)
  inflates absolute displacement but **not** pairwise stretch — the relative-distance test
  is robust to this by design. Genuinely sheared-but-intact material (high stretch, no
  crack) is the real false-positive risk; the material-damage gate (Phase 2) is the
  principled fix.
- **Latch popping:** a binary re-bind on one frame can visibly snap. Hysteresis + sided
  *soft* weights (R2) smooth it; full smoothness would want a short cross-fade of weights
  over a few frames (cheap to add).
- **v3 decoder complexity:** the alive-set bookkeeping is the main new failure surface;
  contain it in one shared routine used by encode, decode, and prune, and gate behind the
  bit-exact harness. v1/v2 paths must remain literally untouched.
- **The hard wall (Phase 4):** correct new-face appearance is research-open and must not
  be promised as part of "fracture support." Phases 1–3 deliver *correct motion of existing
  material across cracks, with split/birth*; they do **not** deliver invented interior
  texture. That distinction should be explicit in any external claim.
- **MPM has no native crack faces:** the solver is meshless; "fracture" is an emergent gap
  in a particle cloud, detected kinematically or via damage, never handed to us as geometry.

---

## 8. The single highest-leverage first phase

**Phase 1 — fracture-aware re-binding in `knn_kabsch.py`, on the existing v2 codec.**
A per-frame pairwise-stretch test over each splat's 8 bound neighbors, a monotone latch
with hysteresis, and a hard 1-NN snap (then sided soft weights) when a splat straddles a
crack. **No codec change, no sim change, no GPU**, validated on the existing
`demolition.json` scene (which already produces damage and, via
`release_particles_sequentially`, opens cracks at constant particle count). It directly
kills the ghost-web smear — the worst and most-visible fracture artifact — and is the
prerequisite signal-generator that Phases 2–3 build on.
