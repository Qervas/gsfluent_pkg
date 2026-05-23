# Design note — GSQ v2: smaller static block via uint8 quantization

**Status:** Deferred. Documented here so a future TTFF profiling pass
can pick this up if static-block bytes turn out to dominate the cold-
cell decode path.

## What's in the current static block

Today's GSQ1 static block (see `server/gsfluent/core/codecs/gsq.py:235-249`,
`viser_headless.py:1378-1386`) holds per-splat attributes that don't
change across frames:

| Attribute | Type | Bytes/splat |
|---|---|---|
| `rgb_f16` | fp16 × 3 channels | 6 |
| `opacity_u8` | uint8 × 1 | 1 |
| `scales_f16` | fp16 × 3 axes | 6 |
| **Total** | | **13** |

For a typical 200k-splat sequence:

- Uncompressed: 200_000 × 13 ≈ 2.6 MB
- Zstd-compressed (level 12 default): ~1.0-1.5 MB on the wire

That sits in the .gsq right after the 80-byte header + 16-byte-per-frame
index, so it's downloaded BEFORE any frame chunk lands. TTFF (time to
first frame) waits on it.

## Where the bytes could go

Two compact-able fields. Opacity is already uint8, so it's untouchable
without psychovisual hacks. The realistic targets:

### rgb: fp16 → uint8 (save 4 bytes/splat = 800 KB for 200k splats)

```
rgb_u8 = clip(round(rgb_f32 * 255), 0, 255).astype(uint8)
# decode: rgb_f32 = rgb_u8.astype(float32) / 255.0
```

- **Saves**: 800 KB raw; ~400 KB on the wire post-zstd.
- **Quality risk**: 256 distinct levels per channel. Visible banding on
  smooth gradients (sky, walls under indirect light). Modern 8-bit
  display pipelines can't show more than this anyway, so the wire
  loss is theoretical IF the splat color is the final pixel. But for
  splats whose contribution sums into a pixel (typical 3DGS
  rendering — many splats per pixel), 8-bit quantization noise
  doesn't sum cleanly to perceptually-equivalent 24-bit output. Worth
  a side-by-side test before committing.
- **Implementation cost**: trivial in the codec (~10 lines). New
  format version flag (`VERSION=2`). Decoder branch in
  `viser_headless._gsq_dequantize_frame` and in
  `core/codecs/gsq.py:load_cell_gsq`. Repack tests + round-trip
  property tests. Estimated **~150 net new lines**.

### scales: fp16 → uint8 per-axis with offset+scale (save 4 bytes/splat = 800 KB for 200k splats)

```
# At pack time, per axis:
lo, hi = scales[:, axis].min(), scales[:, axis].max()
norm = (scales[:, axis] - lo) / (hi - lo)
scales_u8[:, axis] = clip(round(norm * 255), 0, 255).astype(uint8)
# Encode (lo, hi) once in the static block header (24 extra bytes total).

# Decode:
scales[:, axis] = lo + (scales_u8[:, axis].astype(float32) / 255.0) * (hi - lo)
```

- **Saves**: 800 KB raw; ~400 KB on the wire post-zstd.
- **Quality risk**: scales determine splat extent (the σ in the
  Gaussian). 256 levels across a typical scale range means each
  level is ≈ (max_scale - min_scale) / 256. For a scene with scales
  spanning 0.001 to 1.0 (3 orders of magnitude — common for 3DGS),
  the quantization error is dominated by the high end; tiny splats
  collapse to zero or to a few discrete tiers. Visible as either
  needling (scales too small) or visible discs (scales too big).
  Per-axis offset+scale partially mitigates this but doesn't solve
  the log-scale-distribution problem. A log-space quantization
  (encode `log(scale)`) would be safer but adds another decoder branch.
- **Implementation cost**: similar ~150 lines, plus the new per-axis
  (lo, hi) static-header fields.

### Both → estimated total wire savings: ~800 KB / sequence

At a 100 Mbit cellular link, that's ~64 ms saved on first-load TTFF.
At 1 Gbit it's ~6 ms — borderline imperceptible.

## Why we're deferring

The cost/benefit ratio doesn't pencil:

| Dimension | Cost | Benefit |
|---|---|---|
| Wire bytes | – | ~800 KB / sequence |
| TTFF | – | 6-100 ms (depends on link) |
| Codec complexity | new VERSION=2 format, branching decoder in 2 places, repack tests, round-trip property tests, fixture migration | – |
| Quality risk | rgb banding + scale collapse possible; needs eval | – |
| Repack churn | every existing .gsq in `work/cache/viser/` would need re-packing OR the decoder needs to support both v1 and v2 forever | – |

Compared to the four shipped opts (LRU cache, Caddy HTTP/2, CDN docs,
preconnect-on-hover), this is the least bang for the buck. The other
opts each move the user-perceived latency needle by 50-300 ms with
zero quality risk; this one moves it by maybe 50-100 ms with real
quality risk and several hundred lines of decoder churn.

## When to revisit

Pick this back up if **all** of the following hold:

1. TTFF profiling shows the static-block download is on the critical
   path (e.g. the decode of frame 0 stalls for > 50 ms waiting on the
   last static-block bytes). Today the static block is small enough
   that frame-0 bytes typically land within ~1 RTT of it.
2. A side-by-side quality eval (same sequence packed v1 vs v2, same
   viewer, same camera path) shows no visible regression on the
   reference scenes (`cluster_6_15`, etc.).
3. The codec test infrastructure already supports versioned formats
   (right now everything assumes v1; the test fixtures hard-code the
   header bytes).

Otherwise, this stays parked. The 1.5 MB-ish static block is fine.

## Related work

- `server/gsfluent/core/codecs/gsq.py` — current packer
- `frontend/python/viser_headless.py` — current decoder (incremental,
  inside `_sync_cell_gsq_streaming`)
- `pack_splats.py` — CLI entry point that drives `gsq.py`
