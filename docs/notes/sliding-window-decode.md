# Sliding-window decode + strict-sequential frame ordering

## Why

The original viser_headless cell shape decoded every frame of a `.gsq`
into a float32 array at load time:

| Cell type | Resident RAM (1.2 GB `.gsq`, 683k splats × 151 frames) |
|-----------|-------|
| `xyz`     | 151 × 683741 × 3 × 4 = **1182 MB** |
| `quat`    | 151 × 683741 × 4 × 4 = **1575 MB** |
| static    | ~20 MB |
| **total** | **~2.9 GB per cell** |

The LRU caches up to 5 cells. 5 × 2.9 GB = 14.5 GB resident — well past
laptop RAM on the 16 GB target machine. Measured cold-load: ~5 s.

This note documents the replacement: a per-cell ring buffer of
decoded frames around the current playback cursor, with strict
1→2→3 frame ordering preserved across decode hiccups.

## Sliding-window decoder

### SplatRing module

`frontend/python/splat_ring.py` introduces a `SplatRing` class that:

- Parses the `.gsq` header + frame index at construction (~80 B + 16 × n_frames).
- Decodes the static block (rgb, opacity, scales) once (~6 MB for 200 k splats).
- Keeps the source bytes on disk; per-frame decompression happens lazily.
- Maintains an `OrderedDict[int, (xyz, quat)]` ring of decoded frames,
  default capacity `K=32` (configurable via `GSFLUENT_DECODE_WINDOW_FRAMES`).
- Runs one daemon decoder thread per cell, servicing requests from
  `request_frame`, `request_window`, and `advance` hints.

### Memory math

```
window = 32 frames
n_splats = 200,000 (typical sim)
xyz row  = 200000 × 3 × 4 = 2.4 MB
quat row = 200000 × 4 × 4 = 3.2 MB
per-frame = 5.6 MB
ring     = 32 × 5.6 MB = 179 MB
static   = ~6 MB
total per cell ≈ 185 MB
```

Worst case at 5 cached cells: ~925 MB, vs. the prior ~14.5 GB.

### Eviction policy

When a decode lands and the ring is at capacity, the **farthest-from-cursor**
frame is evicted (not pure LRU). Playback locality dominates: the ring
should always track "where we are now", not "where we've been".

The cursor moves on every `advance(idx)` call from the render loop. Scrub
jumps go through `request_window(idx)`, which clears the ring and seeds
new frames in nearest-to-center order.

### File handles

The decoder thread opens the `.gsq` once per frame (not once for the
lifetime of the ring). The OS keeps the inode in page cache; re-open cost
is dwarfed by the zstd decode (~5-50 ms for a 200 k-splat frame). The
one-shot-open model makes `close()` trivial: no file handle to clean up
across thread boundaries.

## Strict-sequential frame ordering

### The invariant

> During continuous playback, frames must render in 1→2→3→4 order.
> NEVER skip. If the next frame isn't decoded in time, HOLD (visible
> stutter) instead of advancing past it.

This is the **opposite** of typical video-player frame-dropping. Splat
review has no audio sync, and a skipped frame can mask a physics bug
(e.g. a single-frame interpenetration). The user explicitly demanded
this contract.

### Exemptions

Two cases are exempt from no-skip:

- **Scrub jump**: user drags the timeline to frame *N* — that IS a
  request to jump. Decode K/2 frames around *N*, render *N* when ready.
  Detected in `/set` when `abs(requested - pushed) > 1`.

- **Initial seek**: when a cell first loads, frame 0 renders when frame
  0 is decoded. After that, advance 0→1→2 from there.

### Render-loop logic

`decide_next_idx_and_push(data, desired, pushed, scrub_pending)` is a
pure function over the SPA's playback state. It returns
`(next_idx, push_now, clear_scrub)`:

```
state["frame"]        = SPA's desired playback cursor (wall-clock driven)
state["pushed_frame"] = the frame the render loop actually pushed last

if scrub_pending:
    return desired if get_frame(desired) else hold
elif pushed < 0:
    return 0 if get_frame(0) else hold      # initial paint
elif desired <= pushed:
    return None                              # paused, hold
elif pushed + 1 >= n_frames:
    return None                              # end of sequence, SPA loops
elif get_frame(pushed + 1):
    return pushed + 1                        # advance by 1
else:
    return None (stutter, hold pushed_frame) # no-skip invariant
```

The render loop:
1. Reads SPA state under the lock.
2. Calls `decide()` to choose the next frame.
3. If `push_now=True`, pushes that frame to viser and bumps `pushed_frame`.
4. If `push_now=False`, holds the current frame. If `next_idx` is non-None,
   the loop posts a decode request and counts a stutter (diagnostic).
5. On every successful push, calls `ring.advance(pushed)` so the
   decoder thread keeps prefetching ahead.

### SPA-side display

The SPA reads BOTH `frame` and `pushed_frame` from `/state`. The scrub
bar displays `pushed_frame` (so it never leads the splats during a
stutter). The SPA's playback driver continues to advance `frame` per
wall-clock — that's the "where we want to be" signal the render loop
walks toward.

When decode keeps up, `pushed_frame == frame` and the bar moves smoothly.
When decode lags, the bar holds at `pushed_frame` while internally
`frame` keeps ticking; once the ring catches up, `pushed_frame` advances
1-at-a-time toward `frame` and the bar resumes.

## Compatibility

### Mixed cell shapes

The render loop reads frames through `_cell_get_xyz_quat(cell, idx)`
which transparently handles:

- **Ring-backed cells** (`load_cell_gsq` output): `cell["ring"].get_frame(idx)`
- **Legacy/streaming/model cells**: `cell["frames"][idx], cell["quats"][idx]`

Models (`mmap_model_cell`) keep the legacy single-frame shape — there's
no benefit to wrapping them in a degenerate ring.

### Streaming download

The streaming `/sync_cell` path keeps its decode-as-arrives behavior
so the first frame is visible before the .gsq finishes downloading
(legacy `frames` / `quats` arrays grow as bytes land). Once the file
is fully on disk, `_swap_to_ring_cell` replaces the cell with a fresh
SplatRing reading the complete file. The 1-2 GB of streaming-decoded
arrays drop out of RAM immediately on the dict reassignment.

### Phase 5 cache hit

`load_cell_gsq` constructs a SplatRing in ~30 ms (parse header +
decode static block + decode frame 0). Down from ~5 s for the
"decode whole file" path. Subsequent frames decode on demand.

## Benchmarks (cluster_6_15_demolition, 1.2 GB, 683k splats × 151 frames)

| Metric | OLD (all-frames) | NEW (sliding window) |
|---|---|---|
| Cell load time | 5.0 s | 27 ms (header + static) + 40 ms (frame 0) |
| RSS after load | +2849 MB | +28 MB (cold) / +637 MB (32-frame warm ring) |
| Cell array bytes | 2757 MB | 0 (frames decode on demand) |
| Scrub 0 → 100 | n/a (always in RAM) | 42 ms |
| Per-frame decode | n/a | ~10 ms |
| 5-cell LRU worst case | 14.5 GB | ~3.2 GB |

## Env vars

- `GSFLUENT_DECODE_WINDOW_FRAMES` (default `32`) — ring capacity per cell.
- `GSFLUENT_MAX_CACHED_CELLS` (default `5`) — LRU cap; still applies.
