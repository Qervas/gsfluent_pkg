"""Encode a sequence .npz into the visual-lossless streamable .gsq format.

Why a new format
────────────────
The .npz cache is monolithic fp32 — ~2.9 GB for a 151-frame 683k-splat
sequence. The client has to download the whole thing before viser can
mmap it, so first-frame latency is bound by total file size / WAN
bandwidth. Per discussion 2026-05-22: aim for visual-lossless playback
that streams as it downloads, so first frame is on screen in ~1s
regardless of sequence length.

.gsq layout (version 1)
───────────────────────
All multi-byte integers are little-endian.

  +---- HEADER (fixed 80 bytes) ------------------------------+
  | magic         u8[4]   "GSQ1"                              |
  | version       u32     = 1                                 |
  | n_splats      u32                                         |
  | n_frames      u32                                         |
  | fps_hint      f32                                         |
  | bbox_min      f32[3]  per-axis min over all frames        |
  | bbox_max      f32[3]  per-axis max over all frames        |
  | static_offset u64     byte offset of the static block     |
  | static_size   u32     compressed size of the static block |
  | reserved      u8[16]  zero; for forward compat            |
  +-----------------------------------------------------------+
  +---- FRAME INDEX (16 bytes × n_frames) --------------------+
  | for each frame:                                           |
  |   offset      u64     byte offset of this frame's chunk   |
  |   size        u32     compressed size in bytes            |
  |   reserved    u32     zero                                |
  +-----------------------------------------------------------+
  +---- STATIC BLOCK (zstd-compressed) -----------------------+
  | rgb           f16[n_splats, 3]  linear 3DGS color (HDR;   |
  |                                 not 0..1)                 |
  | opacity       u8[n_splats]      0..255 ⇒ 0..1             |
  | scales        f16[n_splats, 3]  linear stddev             |
  +-----------------------------------------------------------+
  +---- FRAME CHUNKS (zstd-compressed each) ------------------+
  | xyz           i16[n_splats, 3]  dequant via bbox          |
  | quat_xyz      i16[n_splats, 3]  axis-angle, w recovered   |
  +-----------------------------------------------------------+

Dequantization
──────────────
  p_f32 = bbox_min + (q_i16 + 32768) / 65535.0 * (bbox_max - bbox_min)

Visual-lossless threshold
─────────────────────────
Per-axis position quantum = (bbox_max - bbox_min) / 65535. For a 60-unit
bbox that's ~0.001 units (≈1 mm in scene scale) — well below pixel-level
discrimination at any reasonable render distance.

Quaternion to axis-angle
────────────────────────
We send (qx, qy, qz) of a normalized (qw,qx,qy,qz). Receiver recovers
qw = sqrt(max(0, 1 - qx² - qy² - qz²)). The qw sign is lost — but viser
uses cov reconstruction R·S·Sᵀ·Rᵀ which is invariant under q → -q, so
the sign is irrelevant for rendering.

Usage
─────
  python server/tools/pack_splats.py             # all sequences w/ npz
  python server/tools/pack_splats.py <seq>       # one sequence
  python server/tools/pack_splats.py --force ... # rebuild if up-to-date
"""
from __future__ import annotations

import argparse
import struct
import sys
import time
from pathlib import Path

import numpy as np
import zstandard as zstd

REPO = Path(__file__).resolve().parents[2]
CACHE = REPO / "work" / "cache" / "viser"

MAGIC = b"GSQ1"
VERSION = 1
HEADER_SIZE = 80
INDEX_ENTRY_SIZE = 16  # u64 offset + u32 size + u32 reserved
ZSTD_LEVEL = 9  # mid-range; level 22 is ~5% smaller but ~10× slower


def _quantize_xyz(xyz: np.ndarray, bmin: np.ndarray, bmax: np.ndarray) -> np.ndarray:
    """xyz (T,N,3) float32 → int16 (T,N,3). Per-axis normalize to [0,65535],
    then subtract 32768 so the result fits int16 with no signed-overflow
    surprises in numpy view-casts."""
    span = (bmax - bmin).astype(np.float64)
    span = np.where(span > 0, span, 1.0)  # zero-span axis: degenerate scene
    q = (xyz.astype(np.float64) - bmin) / span * 65535.0
    q = np.clip(np.round(q), 0, 65535).astype(np.int32) - 32768
    return q.astype(np.int16)


def _quantize_quats(q: np.ndarray) -> np.ndarray:
    """quaternions (T,N,4) (w,x,y,z) → axis-vec int16 (T,N,3).

    We send (qx,qy,qz) as ±1-clipped int16 / 32767. Receiver normalizes
    against qw via sqrt(1 - x² - y² - z²). Quaternions out of viser are
    already unit, so each component is in [-1,1] before quantization."""
    qxyz = q[..., 1:4]  # drop qw
    qxyz = np.clip(qxyz, -1.0, 1.0)
    return np.round(qxyz * 32767.0).astype(np.int16)


def encode_npz_to_gsq(npz_path: Path, out_path: Path) -> None:
    print(f"  loading  {npz_path.name}")
    d = np.load(npz_path)

    frames = np.asarray(d["frames"], dtype=np.float32)        # (T,N,3)
    n_frames, n_splats, _ = frames.shape
    fps_hint = float(d["fps_hint"]) if "fps_hint" in d.files else 24.0

    # quats may not exist on v1 caches; fabricate identity in that case.
    if "quats" in d.files:
        quats = np.asarray(d["quats"], dtype=np.float32)      # (T,N,4)
        if quats.shape != (n_frames, n_splats, 4):
            raise SystemExit(
                f"quats shape mismatch: {quats.shape} vs ({n_frames},{n_splats},4)"
            )
    else:
        print("  WARN: no per-frame quats in source npz; using identity")
        quats = np.zeros((n_frames, n_splats, 4), dtype=np.float32)
        quats[..., 0] = 1.0  # w=1

    rgb = np.asarray(d["rgb"], dtype=np.float32)              # (N,3) or (N,) etc.
    opacity = np.asarray(d["opacity"], dtype=np.float32)
    scales = np.asarray(d["scales"], dtype=np.float32) if "scales" in d.files \
             else None

    if scales is None:
        # v1 npz had a static cov instead of scales+quats. Approximate
        # scales as the diagonal of the cov's eigendecomposition. Cheap.
        if "cov" in d.files:
            cov = np.asarray(d["cov"], dtype=np.float32)      # (N,3,3)
            eig = np.linalg.eigvalsh(cov)                     # (N,3) ascending
            scales = np.sqrt(np.clip(eig, 0, None))[:, ::-1]  # descending
        else:
            raise SystemExit(
                f"npz has neither 'scales' nor 'cov': {list(d.files)}"
            )

    bbox_min = frames.reshape(-1, 3).min(axis=0).astype(np.float32)
    bbox_max = frames.reshape(-1, 3).max(axis=0).astype(np.float32)

    print(f"  quantizing  n_frames={n_frames}  n_splats={n_splats}")
    xyz_q = _quantize_xyz(frames, bbox_min, bbox_max)          # (T,N,3) i16
    quat_q = _quantize_quats(quats)                            # (T,N,3) i16

    # rgb is 3DGS linear-space color, not [0,1] — values can exceed both
    # ends (HDR). uint8 clipping was destroying that range, so store as
    # fp16 instead (~4MB static cost for 683k splats, negligible).
    rgb_f16 = rgb.astype(np.float16)
    opacity_u8 = np.clip(np.round(opacity.reshape(-1) * 255.0), 0, 255).astype(np.uint8)
    scales_f16 = scales.astype(np.float16)

    cctx = zstd.ZstdCompressor(level=ZSTD_LEVEL)

    print(f"  compressing  static + {n_frames} frames @ zstd level {ZSTD_LEVEL}")
    static_uncompressed = rgb_f16.tobytes() + opacity_u8.tobytes() + scales_f16.tobytes()
    static_compressed = cctx.compress(static_uncompressed)

    # Per-frame chunks: xyz (i16 ×3) + quat (i16 ×3).
    frame_chunks: list[bytes] = []
    for t in range(n_frames):
        raw = xyz_q[t].tobytes() + quat_q[t].tobytes()
        frame_chunks.append(cctx.compress(raw))

    # Lay out the file.
    static_offset = HEADER_SIZE + n_frames * INDEX_ENTRY_SIZE
    static_size = len(static_compressed)
    frame0_offset = static_offset + static_size

    index_entries = []
    off = frame0_offset
    for c in frame_chunks:
        index_entries.append((off, len(c)))
        off += len(c)

    print(f"  writing  {out_path}")
    with open(out_path, "wb") as f:
        f.write(MAGIC)
        f.write(struct.pack("<III", VERSION, n_splats, n_frames))
        f.write(struct.pack("<f", fps_hint))
        f.write(bbox_min.tobytes())
        f.write(bbox_max.tobytes())
        f.write(struct.pack("<QI", static_offset, static_size))
        f.write(b"\x00" * 24)  # reserved → fills HEADER_SIZE
        assert f.tell() == HEADER_SIZE, f"header size drift: {f.tell()} != {HEADER_SIZE}"
        for off, sz in index_entries:
            f.write(struct.pack("<QII", off, sz, 0))
        assert f.tell() == static_offset, "static offset drift"
        f.write(static_compressed)
        for c in frame_chunks:
            f.write(c)

    final_size = out_path.stat().st_size
    src_size = npz_path.stat().st_size
    ratio = src_size / max(final_size, 1)
    print(f"  done  {final_size/1e6:.1f} MB  (npz was {src_size/1e6:.1f} MB,  {ratio:.1f}× smaller)")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("sequence", nargs="?", default=None,
                   help="single sequence name; omit for all")
    p.add_argument("--force", action="store_true",
                   help="rebuild even if .gsq is newer than .npz")
    args = p.parse_args()

    if not CACHE.is_dir():
        print(f"no viser cache at {CACHE}", file=sys.stderr)
        return 1

    if args.sequence:
        targets = [CACHE / f"{args.sequence}.npz"]
        if not targets[0].is_file():
            print(f"no .npz for {args.sequence}: {targets[0]}", file=sys.stderr)
            return 1
    else:
        targets = sorted(CACHE.glob("*.npz"))

    n_built = n_skipped = n_failed = 0
    for npz in targets:
        gsq = npz.with_suffix(".gsq")
        if gsq.is_file() and not args.force \
           and gsq.stat().st_mtime >= npz.stat().st_mtime:
            print(f"[pack_splats] {npz.stem}: up-to-date, skip")
            n_skipped += 1
            continue
        print(f"[pack_splats] {npz.stem}: building")
        t0 = time.time()
        try:
            encode_npz_to_gsq(npz, gsq)
            n_built += 1
            print(f"  ({time.time()-t0:.1f}s)\n")
        except Exception as e:
            print(f"  FAILED: {e!r}\n", file=sys.stderr)
            n_failed += 1

    print(f"[pack_splats] built={n_built} skipped={n_skipped} failed={n_failed}")
    return 0 if n_failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
