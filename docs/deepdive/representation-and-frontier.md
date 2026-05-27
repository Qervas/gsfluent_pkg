# gsfluent representation deep-dive — the `.gsq` temporal codec + research-frontier positioning

**Scope:** a read-only teardown of how gsfluent represents a *deforming* 3D-Gaussian-splat
sequence on the wire and on disk (the `.gsq` v2 codec), what that representation assumes,
where those assumptions break, where the whole approach sits versus the physics-animated-3DGS
literature, and a ranked set of concrete innovation bets.

Source of truth for everything below:

- `server/gsfluent/core/codecs/gsq.py` — the v2 encoder + header/raw-frame readers.
- `server/gsfluent/core/codecs/gsq_prune.py` — significance pruning (lossless int16 slicing).
- `frontend/python/splat_ring.py` — the canonical Python decoder (sliding-window ring).
- `frontend/src/lib/gsq/{format,decoder,dequant,splat-writer}.ts` — the in-browser TS decoder.
- `server/tools/pack_splats.py` — the frame_*.ply → `.gsq` CLI (drives the codec + prune).
- `server/gsfluent/core/fusers/knn_kabsch.py` — the fuse stage that *produces* the frames.
- `docs/ARCHITECTURE.md`, `docs/superpowers/specs/2026-05-25-gsq-v2-delta-keyframe-design.md`,
  `docs/notes/gsq-v2-static-quant.md`.

The end-to-end product pipeline is:

```
MPM sim (server, taichi/warp/torch)
   → per-frame sim particles (sim_*.ply)
   → KNN-Kabsch fuse: skin a static reference 3DGS asset onto the moving particles
       (per-frame xyz + per-frame Kabsch rotation; color/opacity/scale baked once)
   → frame_*.ply  (fixed splat count + ordering across all frames)
   → GSQCodec.encode_sequence_dir → .gsq v2 (int16 delta+keyframe positions/quats, static rgb/op/scale)
   → significance prune (retention 0.98 default)
   → GET /api/sequences/{name}/cache/splats.gsq
   → in-browser fzstd decode + Spark setSplat in one rAF loop (no iframe, no server renderer)
```

---

## 1. The codec math

### 1.1 File layout

```
header        80 B fixed
frame index   16 B × n_frames        <QII> = (offset u64, size u32, flags u32)
static block  zstd(...)              rgb_f16 ++ opacity_u8 ++ scales_f16  (decoded once)
frame chunks  zstd(...) × n_frames   byte-range addressable, self-contained
```

Header (`gsq.py::parse_header_bytes`, `frontend/src/lib/gsq/format.ts::parseHeader`):
magic `"GSQ1"`, `version` (1 or 2), `n_splats`, `n_frames`, `fps_hint` (f32, written as 24.0),
`bbox_min`/`bbox_max` (3×f32 each — the global AABB over *all* frames), `static_offset`/`static_size`,
24 reserved bytes. The header + frame-index + static block all precede the first frame chunk, so
TTFF (time-to-first-frame) waits on the static block, then on frame-0's chunk.

### 1.2 The static / dynamic split — the key modeling decision

The codec splits a splat's attributes into what changes per frame and what doesn't:

| Attribute | Where | Encoding | Bytes/splat |
|---|---|---|---|
| **xyz** (center) | **per frame** | int16, bbox-relative (delta or keyframe) | 6 |
| **quat** (orientation) | **per frame** | int16 axis-vector (3 comps; w reconstructed) | 6 |
| rgb (DC color) | **static** (frame 0) | fp16 × 3 | 6 |
| opacity | **static** (frame 0) | uint8 (post-sigmoid) | 1 |
| scales (per-axis σ) | **static** (frame 0) | fp16 × 3 | 6 |

So the dynamic payload is **12 B/splat/frame** (xyz + quat), and the static block is **13 B/splat once**.
This is the central physical assumption baked into the format: **only rigid-ish per-splat motion
(translation + rotation) varies; appearance and shape are frozen at frame 0.** That is exactly what
the upstream MPM→KNN-Kabsch fuse produces — each splat is rigidly skinned to a cluster of sim
particles, so its color/opacity/footprint are constant and only its pose moves. The codec is
co-designed with the fuse stage; it is *not* a general dynamic-3DGS container.

Static-block decode math (`gsq.py::_read_static_attrs`, mirrored in `splat_ring._decode_static_block`
and `decoder.ts::decodeStatic`):
- `rgb = clip(0.5 + 0.28209 · f_dc, 0, 1)` (SH band-0 DC term → linear RGB), stored fp16.
- `opacity = sigmoid(opacity_logit)`, stored uint8 (`round(op·255)`).
- `scales = exp(log_scale)` per axis (linear stddev), stored fp16, **floored** at
  `sqrt(6.1e-5) ≈ 7.81e-3` (the `_FP16_COV_FLOOR_SQRT` clamp — see §1.6).

### 1.3 Position quantization — int16, bbox-relative

`gsq.py::_quantize_xyz`:

```
span = bbox_max - bbox_min          (per axis, 0 → 1 to avoid div-by-zero)
q    = round((xyz - bbox_min) / span * 65535)        # → [0, 65535]
q    = clip(q, 0, 65535) - 32768                      # → signed int16 [-32768, 32767]
```

Dequant (`dequant.ts::dequantize`, `splat_ring._dequantize_i16`):
`xyz = bbox_min + (q + 32768) / 65535 · span`.

So positions get **16 bits of precision across the global bounding box of the whole animation**.
Quantum = `span / 65535` per axis — roughly **1 mm at typical scene scale** (per ARCHITECTURE.md).
Because the bbox is the union over all frames, a violent sim (large displacement) widens the bbox
and coarsens the quantum for every frame — a subtle global coupling worth remembering.

### 1.4 Quaternion encoding — store the axis-vector, reconstruct the scalar

`gsq.py::_quantize_quats` stores only the **3 vector components** `(x, y, z)` of a unit quaternion
as int16, dropping `w`:

```
qxyz_i16 = round(clip(q[1:4], -1, 1) · 32767)        # 3 × int16 per splat
```

Decoder reconstructs the scalar from the unit-norm constraint (`dequant.ts`, `splat_ring`):

```
qxyz = qxyz_i16 / 32767
w    = sqrt(clip(1 - (x² + y² + z²), 0, 1))           # always ≥ 0
quat = [w, x, y, z]
```

This is the classic "smallest-three minus the scalar" trick (here always dropping w, not the largest
component), and it only works because the encoder pre-conditions the quaternion in
`gsq.py::_norm_quats`: normalize, then **flip sign so `w ≥ 0`**. Forcing the scalar hemisphere makes
`w = +sqrt(1-|xyz|²)` unambiguous and also keeps the per-splat trajectory continuous (no sign-flip
discontinuities that would wreck the temporal delta). Quantum ≈ `1/32767 ≈ 3e-5` per component — far
below visible. Saves 25% of the dynamic payload (3 comps instead of 4) for free.

Note the **quat reorder at the renderer boundary**: the decoder emits `[w, x, y, z]`; three.js / Spark
want `(x, y, z, w)`, so `splat-writer.ts::splatArgs` reorders when writing to `PackedSplats.setSplat`.

### 1.5 Temporal delta + keyframe scheme (the v2 change)

v1 stored every frame's int16 absolute. The problem (measured on the *foam* scene): absolute int16
positions "look like noise" to zstd — they barely compress. But frame-to-frame *motion* is tiny
(median displacement ≈ 4 quantization units), so the **delta** is near-zero and compresses ~4×.

`gsq.py::_v2_frame_payloads` (`K = GSQ_KEYFRAME_INTERVAL = 30`):

```
for t in range(T):
    keyframe = (t % K == 0)                # frame 0 and every 30th frame
    x = xyz_q[t]              if keyframe else (xyz_q[t] - xyz_q[t-1]).astype(int16)
    q = quat_q[t]            if keyframe else (quat_q[t] - quat_q[t-1]).astype(int16)
    payload[t] = zstd(x.tobytes() + q.tobytes())
    flag[t]    = 1 if keyframe else 0      # frame-index entry bit0
```

This is a video-codec structure: **keyframe = I-frame, delta = P-frame** (no B-frames, no motion
vectors — the "motion vector" is implicit and per-splat). Crucially the deltas are **modular int16**
(`a + (b - a) ≡ b (mod 2^16)`), so reconstruction is **bit-exact lossless** versus v1 — no drift
accumulates even across a full 29-frame keyframe interval. The decoder mirrors numpy's wraparound
with an `Int16Array` add (`decoder.ts::addI16`).

**Reconstruction** (three paths, all decoders agree — `decoder.ts::decodeFrame`,
`splat_ring._decode_one`):
1. **Keyframe / v1** → stored payload is already absolute.
2. **Sequential fast path** → if the previous frame's absolute is cached and the request is `idx-1+1`,
   add a single decompressed delta onto it (~3 ms over ~270k splats). This is the common forward-playback
   case and is lock-free / best-effort (a stale cache only costs a redundant keyframe-walk, never
   correctness).
3. **Cold / scrub jump** → walk back to the nearest keyframe ≤ idx (`while kf>0 && !(flags[kf]&1) kf--`)
   and accumulate up to 29 deltas. Worst-case scrub is tens of ms — acceptable.

### 1.6 The float16 issue we hit — the cov floor

`_FP16_COV_FLOOR_SQRT = sqrt(6.1e-5) ≈ 7.81e-3`. Scales below this are clamped up (with a
`encode.scales_clamped` event emitted). The reason: scales are squared into a covariance, and very
small σ values, once squared, **underflow fp16's smallest normal** (and the GPU covariance compute /
Spark's fp16 splat path collapses them). Clamping the *stddev* at `sqrt(6.1e-5)` guarantees the
*variance* stays representable in fp16. This is the residue of a real bug — tiny needle-like splats
either vanished or NaN'd in the fp16 render path. Related: ARCHITECTURE notes Spark's **fp16
splat-center collapse at large world coordinates** (~29000 for the INRIA scans), handled separately
by recentering static `.ply` models to the origin (`ply-recenter.ts`) — a different fp16 trap on the
*position* side that the `.gsq` bbox-relative int16 encoding sidesteps for sequences.

### 1.7 Compression ratios achieved

- **Static block:** ~13 B/splat raw → ~1.0–1.5 MB on the wire for 200k splats (≈0.6% of the file;
  the static-quant note deliberately *defers* shrinking it as not worth the quality risk).
- **v2 delta+keyframe vs v1 absolute:** prototype on *foam* — pure-delta **4.0×** (488 → 121 MB),
  keyframe/30 **3.7×** (488 → ~134 MB), bit-exact, ~3 ms/frame sequential decode. The keyframe
  interval trades a little size (the I-frames don't delta-compress) for random-access scrubbing.
- **Significance pruning** (`gsq_prune.py`, retention 0.98 default) stacks on top: drops ~3.4× the
  *count* of splats while keeping 98% of total `opacity × volume` contribution. Pruning is done by
  **raw int16 index-slicing** of each frame chunk + the static block — no dequant/requant round-trip,
  so it is lossless for kept splats, and (importantly) **slicing commutes with the delta encoding**
  (`prune_gsq_bytes` slices the *stored* payload, keyframe or delta, preserving flags + version).
- vs the retired fp32 `.npz`: ~3× smaller before any of the above (12 B/splat/frame vs 28 B).

### 1.8 Precision / quality trade-off summary

| Field | Encoding | Quantum | Risk |
|---|---|---|---|
| xyz | int16 / bbox | ~bbox-span/65535 (~1 mm) | coarsens if a violent sim widens the global bbox |
| quat | int16 axis-vec | ~3e-5/comp | none visible; relies on `w≥0` hemisphere conditioning |
| rgb | fp16 | — | below visible threshold; uint8 deferred (banding risk) |
| opacity | uint8 | 1/255 | 256 levels; fine in practice |
| scales | fp16, floored | — | tiny splats clamped to `σ≥7.81e-3` to dodge fp16 cov underflow |

---

## 2. What the representation assumes — and where it breaks

The `.gsq` format and the fuse stage that feeds it bake in four hard assumptions:

1. **Fixed splat count across all frames.** The encoder asserts every frame has exactly `n_splats`
   (`gsq.py`: `if v.shape[0] != n_splats: raise CodecError`). The header stores a single `n_splats`,
   the static block has exactly one row per splat, and the delta scheme requires splat `i` in frame
   `t` to be the *same physical splat* as splat `i` in frame `t-1` (the delta is `xyz[i,t] - xyz[i,t-1]`).
2. **Fixed topology / ordering.** Splat `i` means the same thing in every frame. Guaranteed by the
   fuse stage: `knn_kabsch.py` builds the reference attribute array **once** (`full_attrs`,
   length `len(ref_v)`) and the K-NN correspondence **once** against frame-0 particles, then every
   output frame is that same set re-posed. No re-sort, no re-index.
3. **No birth / death of splats.** A splat exists for the whole sequence. There is no per-frame
   "alive" mask, no append, no free-list.
4. **Sequential, monotone decode.** The codec is **encode-only on the server** now (decode_all was
   dropped); the runtime decoders are tuned for forward playback (the +1 fast path) with scrub as the
   exception. Reverse playback or random access pays the keyframe-walk cost.

These hold *by construction* for the current product (MPM deformation of a fixed body skinned to a
fixed reference asset). They break exactly where the physics gets interesting:

- **Demolition / fracture.** When a body fractures, the *visual* topology changes — a single splat
  cluster wants to split, new surfaces are exposed, and ideally new splats appear on the fracture
  faces. The current pipeline can only *deform* the frame-0 splat set; it cannot add splats for
  newly-exposed interior surfaces, and a splat that should split into two can only smear. KNN-Kabsch
  skinning across a fracture line will stretch splats across the gap (the K nearest particles end up
  on both sides of a crack).
- **`release_particles` boundary condition.** A BC that *adds particles* mid-sim (emission, spray,
  the foam/spall use-case) changes the particle count over time. The fuse correspondence is frozen at
  frame 0, so particles born later have no reference splat to carry, and the codec literally cannot
  represent a growing `n_splats`. (The product caps particle count at `DEFAULT_MAX_PARTICLE_COUNT =
  500_000` — a static cap, not a per-frame budget.)
- **Topology-changing contact / merging.** Two bodies merging, or self-contact that fuses surfaces,
  has the same problem in reverse: the fixed correspondence can't re-skin.
- **Appearance change.** Anything that changes color/opacity/shape over time (burning, wetting,
  glowing, melting that changes the local σ) violates the static-block assumption and would render
  as frozen appearance over moving geometry.

The honest framing: **gsfluent's representation is a rigid-skin deformation codec, not a 4D-radiance
codec.** It is extremely good at the thing it does (lossless, compact, streamable rigid-per-splat
deformation) and structurally cannot do birth/death/appearance-change without a format change.

---

## 3. Frontier positioning

(The local reference repos PhysGaussian / GASP / gs-mpm were deleted; the following reasons from the
published literature.)

The "physics-animated 3D Gaussian splatting" space breaks into a few families. gsfluent overlaps the
**physics-driven-3DGS** family on the simulation side but is genuinely distinct on the
**representation + delivery** side.

**PhysGaussian (Xie et al., 2024)** — embeds a 3DGS scene directly into an MPM continuum and steps the
splats' kinematics with the sim ("what you see is what you simulate"), including a first-order
covariance update so splats stretch/rotate with the deformation gradient F. gsfluent's *simulation*
philosophy is the same lineage (MPM driving splats), but gsfluent **decouples** sim from splats: it
sims *particles*, then KNN-Kabsch-*skins* a reference 3DGS asset onto them as a post-process (the
`particle_F` cov-field mode in the fuser CLI is the optional PhysGaussian-style covariance path).
PhysGaussian has no streaming/storage representation at all — it's a renderer-coupled research method.
**gsfluent's `.gsq` codec + in-browser decoupled playback is the part PhysGaussian doesn't have.**

**GASP (Gaussian-particle physics)** — couples Gaussians to a particle physics substrate; again a
method, not a delivery format. Same gap.

**Spring-Gaus / PhysDreamer** — *learn* physical material parameters (spring-mass, or a learned
material field distilled from video diffusion priors) so a 3DGS scene can be re-simulated. These are
about *inferring* dynamics; gsfluent takes the dynamics as given (authored MPM recipes) and focuses on
*shipping* the result. Orthogonal — a PhysDreamer-style learned-material front end could *feed*
gsfluent's pipeline.

**4D-GS / dynamic-3DGS and their codecs (e.g. 4DGS, deformable-3DGS, HexPlane/quantized 4D-GS, and the
recent dynamic-3DGS compression papers)** — these target *captured* 4D scenes (multi-view video of
real dynamic content) and typically represent motion with a **learned deformation field** (an MLP /
HexPlane / per-Gaussian motion basis) or a canonical-plus-deformation factorization, then compress the
*model*. gsfluent's `.gsq` is the opposite design point: **no learned field, explicit per-frame
per-splat int16 pose, delta+keyframe + zstd** — a hand-rolled, deterministic, bit-exact, GPU-free,
~80-line-decodable video-style codec. It trades the model-compression ratios of neural 4D-GS for
**zero training, exact reconstruction, trivial random access, and a decoder that runs in any browser
with no GPU and no torch.** This is the classic explicit-vs-learned representation trade.

**Neural compression of dynamic scenes (entropy-coded latents, motion-MLPs, etc.)** — strictly better
compression ratios on smooth motion, but needs a trained model per scene (or a big general model),
GPU decode, and gives lossy, non-bit-exact output. gsfluent deliberately sits at the
"dumb-but-bulletproof" end.

### What's genuinely novel here vs commodity

**Commodity / standard technique** (gsfluent does it well but didn't invent it):
- MPM-driven splat animation (PhysGaussian lineage).
- KNN inverse-distance skinning + per-cluster Kabsch rotation (standard graphics skinning).
- int16 bbox-relative position quantization + smallest-components quaternion (standard in mesh/anim
  compression and 3DGS quantization work like LightGaussian/Compact-3DGS).
- keyframe + delta + zstd (video-codec 101).
- significance pruning by opacity×volume (LightGaussian's exact idea).

**Genuinely novel / the actual moat:**
1. **The end-to-end *product*:** authored MPM recipe → fuse → quantized temporal codec →
   download-then-play **in a plain browser with no GPU compute, no server-side renderer, no iframe,
   no torch on the client.** Every academic method above stops at "here's a renderer." gsfluent ships
   the whole path to a teammate's laptop. That integration is the defensible thing.
2. **The `.gsq` representation as a *streaming deforming-splat container*:** bit-exact, byte-range
   addressable per-frame chunks, delta+keyframe for scrub-able random access, a clean static/dynamic
   split co-designed with the fuse stage, and **prune-commutes-with-delta** (you can losslessly
   sub-select splats by raw int16 slicing without touching the temporal structure). There isn't a
   standard interchange format for "rigidly-deforming 3DGS sequence"; `.gsq` is a credible one.
3. **The deliberate explicit/lossless/GPU-free design point** — the literature is racing toward
   learned 4D fields; gsfluent's bet that a video-codec-shaped explicit format is the right substrate
   for *authored physics* (where you control the motion smoothness and want exact playback) is a
   contrarian, coherent position.

The flip side (and the source of the innovation list): everything novel is on the *delivery* axis;
the *representation* is intentionally simple and therefore leaves a lot of compression and capability
on the table.

---

## 4. Innovation opportunities (ranked by bet quality)

Each: what it unlocks · difficulty · where it lands.

### Bet 1 — Birth/death (variable splat count) in the codec
**Unlocks:** fracture, demolition, `release_particles`/emission, melting/spray — i.e. the
*interesting* physics the current format structurally forbids (§2). This is the single biggest
capability ceiling.
**How:** add a per-frame "alive" bitmask + an append region for newly-born splats; the static block
becomes a *growing* table (new splats append their rgb/op/scale when born). Delta encoding keys on
stable splat IDs, not array index, so a free-list / ID-remap layer sits under the delta math. Keyframes
re-baseline the live set. Pruning must slice on IDs, not indices.
**Difficulty:** High. Touches the format (new VERSION=3), the fuse stage (must emit births — needs the
sim to expose particle birth events and a way to author splats on fracture faces, which is its own
research problem), both decoders, and the prune logic. The codec change is tractable; *producing*
correct birth geometry (where do new splats' color/normal come from on a fresh fracture surface?) is
the hard, partly-open part.
**Lands in:** `gsq.py` (format + encoder), `knn_kabsch.py` / a new fracture-aware fuser,
`gsq_prune.py`, `decoder.ts` + `splat_ring.py`.

### Bet 2 — Entropy-code the deltas (range/ANS coder on top of/instead of zstd)
**Unlocks:** another ~1.5–3× on the dynamic payload essentially for free, with **zero quality loss and
no GPU** — directly attacks the file size that drove the LOD/streaming work (and that the team
ultimately accepted as "just download"). The deltas are tiny, near-zero, highly skewed integers —
textbook input for a range coder with a per-frame or per-axis adaptive model; zstd's generic entropy
stage leaves a lot on the table for this distribution.
**Difficulty:** Medium. Self-contained, lossless, testable with the existing bit-exact harness
(`verify_gsq_v2.py`). Need a small ANS/range coder on both sides (Python encode + TS decode); the
explicitly-modeled delta distribution (zig-zag + adaptive frequency, possibly separating the
mostly-zero high bytes) is the work. No format-philosophy change.
**Lands in:** `gsq.py` (`_v2_frame_payloads`), `decoder.ts`/`splat_ring.py` (replace the
`zstd → int16` step with `entropy-decode → int16`), new format flag.
**Why high-value:** biggest size win per unit risk; keeps the "explicit, lossless, GPU-free" identity
the team already chose.

### Bet 3 — LOD / progressive streaming layered on the pruner
**Unlocks:** instant first paint + improve-over-time; mobile/weak-link viability; the smoothness goal
the removed streaming work chased. The pruner *already* produces a strict significance ordering and
slices losslessly — a base layer (top-N significant splats) + refinement layers (the next tiers) falls
out almost for free, and because pruning commutes with the delta encoding, each layer is itself a valid
`.gsq`.
**Difficulty:** Medium (codec/transport) but **note the team explicitly REMOVED streaming+LOD**
(2026-05-26, MEMORY.md) as "not promising, didn't fix smoothness." So this is *de-risked technically*
but **politically dead unless paired with a different justification** — namely TTFF / first-paint, not
smoothness. Reframe as "show *something* in 200 ms" rather than "stream to fix stutter."
**Lands in:** `gsq_prune.py` (emit ordered layers), a manifest, `download.ts` + `SplatScene`.

### Bet 4 — On-GPU decode (dequant in a shader / compute pass)
**Unlocks:** removes the ~3 ms/frame CPU dequant + the per-splat JS `setSplat` write loop that is the
real per-frame cost in-browser; enables higher splat counts / framerates; is the prerequisite for
*real-time* (Bet 7). Upload int16 + the delta accumulation and dequant in a WGSL/GLSL compute pass
straight into Spark's packed buffer.
**Difficulty:** Medium-High. Modular-int16 delta accumulation on GPU is fiddly (need either keyframe
upload + GPU prefix-add over deltas, or keep accumulation on CPU and only dequant on GPU). Ties into
Spark internals (`PackedSplats` layout). Browser-only payoff.
**Lands in:** `frontend/src/lib/gsq` + `SplatScene` (the Spark write path), no server change.

### Bet 5 — Learned/neural *residual* codec (keep the explicit core, add an optional lossy layer)
**Unlocks:** the neural-4D-GS compression ratios *without* abandoning bit-exactness — ship a small
learned motion-basis / deformation-MLP that predicts each frame, and store only the **int16 residual**
between the prediction and the true frame. On smooth MPM motion the residual is near-zero → huge
compression; and because you still store an (entropy-coded) residual, you can dial from lossy (drop
residual) to lossless (keep it).
**Difficulty:** High. Per-sequence or general model training, a GPU-or-WASM inference path in the
browser, and it reintroduces the complexity the team deliberately avoided. Best as an *optional*
high-compression profile, not the default.
**Lands in:** new encoder stage + a model artifact alongside `.gsq`; new decoder path. Big, speculative.

### Bet 6 — Differentiable / editable representation
**Unlocks:** in-browser editing (retime, blend two sims, scrub a parameter), and a hook for
inverse/learned-material work (PhysDreamer-style) — make the per-frame poses a *parametric* function
(e.g. low-rank per-cluster motion bases) instead of a raw int16 dump, so the playback is differentiable
and editable rather than baked.
**Difficulty:** High and somewhat research-y; changes the representation from "samples" to "model,"
which fights Bet 2/3's "explicit and dumb" direction. Probably only worth it if editing becomes a
product requirement.
**Lands in:** representation rethink — a new codec family, the fuse stage, the player.

### Bet 7 — Real-time (not offline) physics → splat
**Unlocks:** interactive sims (poke the foam and watch it respond) — the demo that would actually
differentiate the product. Move the MPM step + fuse onto the GPU and skip the `.gsq` round-trip for the
live case (keep `.gsq` for sharing/replay).
**Difficulty:** Very High. Needs GPU MPM (warp/taichi → WebGPU or a server-side real-time loop with
low-latency splat streaming), GPU skinning, and GPU decode (Bet 4) as a prerequisite. This is a
product-direction bet, not a codec tweak — but it's the one that moves gsfluent from "physics-splat
*player*" to "physics-splat *engine*."
**Lands in:** a new real-time path largely parallel to the current offline pipeline.

### Bet 8 — Adaptive keyframe interval + per-cluster bbox (codec polish)
**Unlocks:** smaller files + cheaper scrubs by (a) inserting keyframes on *motion* (scene-cut-style:
when accumulated delta magnitude crosses a threshold) instead of a fixed K=30, and (b) using local
per-cluster or per-octant bboxes so the int16 quantum isn't coarsened by one fast-moving region of the
scene (§1.3's global-bbox coupling).
**Difficulty:** Low-Medium, fully lossless, fully in the existing test harness. The smallest, safest
win; good warm-up before Bet 2.
**Lands in:** `gsq.py` (`_v2_frame_payloads` keyframe decision + a per-region bbox table in the header),
both decoders.

### Recommended sequencing
**Bet 8 → Bet 2** first (safe, lossless, big size wins, keep the explicit identity), then **Bet 4**
(unblocks scale + is the gate to real-time), then **Bet 1** (the capability ceiling — fracture/birth —
which is what makes the *physics* impressive). Bets 3/5/6/7 are product-direction-dependent.

---

## TL;DR

- **The codec:** `.gsq` v2 stores a deforming splat sequence as a once-decoded static block (fp16 rgb,
  uint8 opacity, fp16 scales — appearance is frozen at frame 0) plus per-frame **12 B/splat** of motion:
  int16 bbox-relative positions and int16 axis-vector quaternions (w reconstructed from `‖q‖=1` with a
  forced `w≥0` hemisphere). Most frames are **modular-int16 deltas** from the previous frame with an
  absolute **keyframe every 30**, zstd-compressed per byte-range-addressable chunk — a video-codec
  (I-/P-frame) structure that is **bit-exact lossless** and compresses *foam* 488→~134 MB (~3.7×),
  stacking with significance pruning (retention 0.98, lossless int16 slicing that commutes with the
  deltas). It assumes a **fixed splat count + topology + appearance**, which holds because the upstream
  MPM→KNN-Kabsch fuse rigidly skins one reference asset onto the sim.
- **Genuine novelty:** not the math (MPM-splats, skinning, int16/quat quantization, keyframe+delta,
  opacity×volume pruning are all standard) but the **end-to-end product** — authored MPM → fuse →
  quantized temporal codec → **in-browser, GPU-free, lossless playback** with no renderer/iframe/torch
  on the client — and the **`.gsq` streaming deforming-splat container** itself, which the
  PhysGaussian/GASP/Spring-Gaus/4D-GS literature simply doesn't have (they ship renderers and learned
  fields, not a bit-exact explicit interchange format). gsfluent deliberately sits at the
  explicit/lossless/GPU-free end while the field races toward learned 4D fields.
- **Top innovation bets:** (1) **entropy-code the deltas** — ~2× more size, zero quality loss, fits the
  existing identity; (2) **birth/death in the codec** — the capability ceiling that unlocks
  fracture/emission/demolition; (3) **on-GPU decode** — removes the per-frame CPU cost and gates
  real-time; with adaptive-keyframe/local-bbox codec polish as the safe warm-up and real-time
  physics→splat as the long-horizon product bet.
