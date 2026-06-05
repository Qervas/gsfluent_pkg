# Debris death channel — "die at the boundary" (per-frame visibility)

## Problem

Past ~frame 60 the earthquake debris cloud inflates to fill the *entire* sim
box (`bbox → [0.04, 1.96]³`), reading as an unnatural "explosion onto the
walls" instead of pieces flying off-screen. The pieces are genuinely flung
outward (physical), but the finite domain has no way to let them *leave the
scene*.

Root constraint discovered: the playback format (`.gsq`, what the frontend
actually plays — `frames.bin` is legacy/unused) carries **per-frame xyz + quat**
only; opacity/scale/rgb are **static** (frame 0). And the fuser needs a
**fixed splat count** (frozen frame-0 KNN binding), so splats can't be deleted
mid-sequence. There is **no per-frame visibility channel** → debris cannot
vanish.

Already in place (textbook, verified): separating/slip grid BC
(`add_bounding_box`, padding 3), inert-drop of escapers (mass=0), Patch 10 NaN
sanitize, fuser fracture-latch. The *only* missing piece is **visibility**.

## Design — monotonic `death_frame[]`

"Alive" is monotonic: a piece that flies away never returns. So we don't need a
per-frame mask — just **one `death_frame[splat]` array**: the frontend renders a
splat while `current_frame < death_frame[splat]`.

**Kill rule (radial, world space, packer-side):** a splat dies at the first
frame `t` where `‖pos(splat, t) − centroid₀‖ > K · R₀`, where `centroid₀` is the
frame-0 mean and `R₀` is a robust frame-0 radius (95th percentile of
‖pos−centroid₀‖). `K` = "how many building-radii a piece flies before it's
gone." Computed from the fused frame PLYs the packer already loads → **no fuser
change, no solver change, no coordinate ambiguity.**

## Components (2)

### 1. Codec — `server/gsfluent/core/codecs/gsq.py`
- In `encode_sequence_dir`, after `xyz_all` is loaded + sanitized: compute
  `death_frame` (uint16, `0xFFFF` = never). Gated by `K` from env
  `GSFLUENT_GSQ_KILL_RADIUS` (float; **0/unset = disabled** → byte-identical to
  today, no death block written).
- Append a zstd'd `death_frame` block at **EOF** (existing offsets unchanged →
  backward compatible). Write `deathOffset (Q@56)` + `deathSize (I@64)` into the
  currently-reserved header region (was `\x00*24`). `deathSize=0` ⇒ absent.
- `VERSION` stays 2 (old readers ignore reserved bytes; new readers opt in).
- Tests: round-trip — `parse_header_bytes` exposes death ptr; synthetic
  sequence with a known flyaway splat yields the expected `death_frame`.

### 2. Frontend — `frontend/src/lib/gsq/*` + render loop
- `format.ts`: parse `deathOffset/deathSize` (reserved 56/64) into `GsqHeader`.
- `decoder.ts`: decompress the death block → `GsqStatic.deathFrame: Uint16Array
  | null` (null when absent).
- `splat-writer.ts`: `splatArgs(frame, st, i, out, frameIdx)` → `out.opacity =
  (death && frameIdx >= death[i]) ? 0 : st.opacity[i]`. (opacity is already set
  per-splat per-frame in the hot loop — one extra lookup.)
- Render loop (`use-three-scene.ts`/`playback.ts`): thread `frameIdx` into
  `splatArgs`.
- Tests: `format.test.ts` + `decoder.test.ts` cover present/absent death block.

## Validation (offline, end-to-end)
1. Clean Patch-10 sim, 120 frames (done: `gridbc_lab/sim_eq120`). ✔
2. Fuse → fused PLYs (running: `gridbc_lab/fused_eq120`).
3. Pack with several `K` (e.g. 1.5, 2, 3) → test `.gsq`s.
4. Frontend (dev/headless): confirm debris vanishes as it flies out, building
   stays compact; tune `K`.
5. Before/after render to the user. Only then discuss shipping + a production
   default for `K` (likely recipe-carried).

## Non-goals / safety
- **No solver change** → live backend untouched (honors the no-WIP-on-live rule).
- Disabled by default (`K=0`) → zero impact on existing builds until opted in.
