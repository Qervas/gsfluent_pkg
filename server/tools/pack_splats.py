"""Encode a sequence directly from frame_*.ply into the .gsq streaming format.

Replaces the previous two-step pipeline (frame_*.ply → batch_convert_to_npz
→ .npz → pack_splats → .gsq). This single pass reads the fused frame plys
and writes the .gsq directly, skipping the ~600 MB intermediate.

.gsq layout — see the docstring of the previous version (format unchanged):
  header(80B) + frame_index(16B × N) + static_block(zstd) + frame_chunks(zstd)

Per-frame ply field mapping (matches sequence_to_viser_npz.py):
  - xyz:     v["x"], v["y"], v["z"]                       — per frame
  - quat:    (rot_0, rot_1, rot_2, rot_3) normalized      — per frame (v2 only)
  - scales:  exp(scale_0, scale_1, scale_2)               — static (frame 0)
  - rgb:     clip(0.5 + 0.282 * f_dc_*, 0, 1)             — static (frame 0)
  - opacity: sigmoid(opacity_raw)                         — static (frame 0)

If frame 0 has no rot_0..3 fields, we fall back to identity quats — viewer
falls back to the static-cov rendering path.

Usage:
    python server/tools/pack_splats.py                # all sequences
    python server/tools/pack_splats.py <seq>          # one sequence
    python server/tools/pack_splats.py --force <seq>  # rebuild
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
LIB = REPO / "work" / "library" / "sequences"
CACHE = REPO / "work" / "cache" / "viser"
SH_C0 = 0.28209479177387814

MAGIC = b"GSQ1"
VERSION = 1
HEADER_SIZE = 80
INDEX_ENTRY_SIZE = 16
ZSTD_LEVEL = 9
_FP16_COV_FLOOR_SQRT = np.float32(np.sqrt(6.1e-5))  # ≈ 7.81e-3


def _has_rot_fields(v) -> bool:
    return all(f in v.dtype.names for f in ("rot_0", "rot_1", "rot_2", "rot_3"))


def _norm_quats(qw, qx, qy, qz):
    """Normalize + fix sign so scalar is non-negative (continuous trajectory)."""
    qn = np.sqrt(qw * qw + qx * qx + qy * qy + qz * qz)
    qn[qn == 0] = 1.0
    qw, qx, qy, qz = qw / qn, qx / qn, qy / qn, qz / qn
    flip = qw < 0
    qw[flip] = -qw[flip]; qx[flip] = -qx[flip]
    qy[flip] = -qy[flip]; qz[flip] = -qz[flip]
    return qw, qx, qy, qz


def _read_static_attrs(v0):
    """frame_0 → (scales, rgb, opacity). Applies the fp16 cov-floor clamp
    so viser's WS transport doesn't render needle-splats."""
    sx = np.exp(np.asarray(v0["scale_0"], dtype=np.float32))
    sy = np.exp(np.asarray(v0["scale_1"], dtype=np.float32))
    sz = np.exp(np.asarray(v0["scale_2"], dtype=np.float32))
    scales = np.stack([sx, sy, sz], axis=1)
    n_clamped = int((scales < _FP16_COV_FLOOR_SQRT).any(axis=1).sum())
    if n_clamped:
        print(f"  clamping {n_clamped} splat scales below fp16 normal "
              f"({n_clamped/len(scales)*100:.1f}%)")
        np.maximum(scales, _FP16_COV_FLOOR_SQRT, out=scales)

    rgb = np.stack([
        0.5 + np.asarray(v0["f_dc_0"], dtype=np.float32) * SH_C0,
        0.5 + np.asarray(v0["f_dc_1"], dtype=np.float32) * SH_C0,
        0.5 + np.asarray(v0["f_dc_2"], dtype=np.float32) * SH_C0,
    ], axis=1).astype(np.float32)

    op_logit = np.asarray(v0["opacity"], dtype=np.float32)
    opacity = (1.0 / (1.0 + np.exp(-op_logit))).astype(np.float32)
    return scales, rgb, opacity


def _read_per_frame(v, want_quats: bool):
    xyz = np.stack([
        np.asarray(v["x"], dtype=np.float32),
        np.asarray(v["y"], dtype=np.float32),
        np.asarray(v["z"], dtype=np.float32),
    ], axis=1)
    quat = None
    if want_quats:
        qw = np.asarray(v["rot_0"], dtype=np.float32)
        qx = np.asarray(v["rot_1"], dtype=np.float32)
        qy = np.asarray(v["rot_2"], dtype=np.float32)
        qz = np.asarray(v["rot_3"], dtype=np.float32)
        qw, qx, qy, qz = _norm_quats(qw, qx, qy, qz)
        quat = np.stack([qw, qx, qy, qz], axis=1)
    return xyz, quat


def _quantize_xyz(xyz, bmin, bmax):
    span = (bmax - bmin).astype(np.float64)
    span = np.where(span > 0, span, 1.0)
    q = (xyz.astype(np.float64) - bmin) / span * 65535.0
    q = np.clip(np.round(q), 0, 65535).astype(np.int32) - 32768
    return q.astype(np.int16)


def _quantize_quats(q):
    qxyz = np.clip(q[..., 1:4], -1.0, 1.0)
    return np.round(qxyz * 32767.0).astype(np.int16)


def encode_sequence_to_gsq(seq_name: str, out_path: Path) -> None:
    from plyfile import PlyData

    frames_dir = LIB / seq_name / "frames"
    if not frames_dir.is_dir():
        raise SystemExit(f"no frames/ in {LIB / seq_name}")
    frame_paths = sorted(p for p in frames_dir.iterdir()
                         if p.is_file() and p.name.startswith("frame_") and p.suffix == ".ply")
    if not frame_paths:
        raise SystemExit(f"no frame_*.ply in {frames_dir}")
    n_frames = len(frame_paths)

    print(f"  reading frame 0 for static attrs: {frame_paths[0].name}")
    v0 = PlyData.read(str(frame_paths[0]))["vertex"].data
    n_splats = v0.shape[0]
    has_rot_v0 = _has_rot_fields(v0)
    # Probe frame 1 too — sometimes frame 0 carries quats but later frames
    # don't (sim-emitted plys may only have xyz). v2 needs per-frame rot.
    probe = frame_paths[1] if n_frames > 1 else frame_paths[0]
    v_probe = PlyData.read(str(probe))["vertex"].data
    want_quats = has_rot_v0 and _has_rot_fields(v_probe)
    if not has_rot_v0:
        print(f"  WARN: frame 0 has no rot_* fields; using identity quats (v1 fallback)")

    scales, rgb, opacity = _read_static_attrs(v0)

    xyz_all = np.empty((n_frames, n_splats, 3), dtype=np.float32)
    quat_all = np.empty((n_frames, n_splats, 4), dtype=np.float32)
    if not want_quats:
        # Identity quaternion (w=1) — invariant under cov reconstruction.
        quat_all[..., 0] = 1.0
        quat_all[..., 1:] = 0.0

    print(f"  reading {n_frames} frames…  (n_splats={n_splats}, quats={'yes' if want_quats else 'no'})")
    t0 = time.time()
    for i, p in enumerate(frame_paths):
        v = PlyData.read(str(p))["vertex"].data
        if v.shape[0] != n_splats:
            raise SystemExit(f"{p.name} has {v.shape[0]} splats, expected {n_splats}")
        xyz, quat = _read_per_frame(v, want_quats=want_quats)
        xyz_all[i] = xyz
        if quat is not None:
            quat_all[i] = quat
        if (i + 1) % 25 == 0 or i + 1 == n_frames:
            print(f"    {i+1}/{n_frames}  ({time.time()-t0:.1f}s)", flush=True)

    # Sanitization. The viser WASM splat sorter does an in-place radix
    # sort over float positions; any NaN/Inf causes "memory access out
    # of bounds" because IEEE compare with NaN never returns ordered.
    # Likewise bad quats yield a non-pos-def cov which the sorter then
    # can index into a swap that overflows. Treat both:
    bad_xyz = ~np.isfinite(xyz_all).all(axis=2)   # (T,N), catches NaN AND ±Inf
    if bad_xyz.any():
        n_bad = int(bad_xyz.sum())
        print(f"  sanitizing {n_bad} non-finite positions (forward-fill)")
        if bad_xyz[0].any():
            # Frame 0 has nothing earlier to fall back on; clamp to
            # centroid of finite frame-0 points (or origin if no finite).
            good = ~bad_xyz[0]
            ctr = (xyz_all[0][good].mean(axis=0) if good.any()
                   else np.zeros(3, dtype=np.float32))
            xyz_all[0][bad_xyz[0]] = ctr
        for t in range(1, n_frames):
            b = bad_xyz[t]
            if b.any():
                xyz_all[t][b] = xyz_all[t - 1][b]

    # Quats: replace any non-finite or zero-norm quat with identity
    # (w=1, xyz=0). One-shot rather than forward-fill — bad quats
    # usually mean the fuse Kabsch hit a degenerate K-NN cluster and
    # forward-fill won't make it better.
    qn2 = (quat_all * quat_all).sum(axis=-1)             # (T,N)
    bad_q = (~np.isfinite(qn2)) | (qn2 < 1e-12)
    if bad_q.any():
        n_bad = int(bad_q.sum())
        print(f"  sanitizing {n_bad} bad quats (→ identity)")
        quat_all[bad_q] = np.array([1, 0, 0, 0], dtype=np.float32)

    bbox_min = xyz_all.reshape(-1, 3).min(axis=0).astype(np.float32)
    bbox_max = xyz_all.reshape(-1, 3).max(axis=0).astype(np.float32)
    # Belt-and-suspenders: if some axis still has zero/inf span, sanity-clip.
    if not (np.isfinite(bbox_min).all() and np.isfinite(bbox_max).all()):
        raise SystemExit(
            f"non-finite bbox after sanitization: {bbox_min}..{bbox_max} — "
            f"every frame had bad data on at least one axis"
        )

    print(f"  quantizing  bbox={bbox_min.tolist()}..{bbox_max.tolist()}")
    xyz_q = _quantize_xyz(xyz_all, bbox_min, bbox_max)
    quat_q = _quantize_quats(quat_all)

    rgb_f16 = rgb.astype(np.float16)
    opacity_u8 = np.clip(np.round(opacity * 255.0), 0, 255).astype(np.uint8)
    scales_f16 = scales.astype(np.float16)

    cctx = zstd.ZstdCompressor(level=ZSTD_LEVEL)
    print(f"  compressing  static + {n_frames} frames @ zstd L{ZSTD_LEVEL}")
    static_uncompressed = rgb_f16.tobytes() + opacity_u8.tobytes() + scales_f16.tobytes()
    static_compressed = cctx.compress(static_uncompressed)

    frame_chunks: list[bytes] = []
    for t in range(n_frames):
        raw = xyz_q[t].tobytes() + quat_q[t].tobytes()
        frame_chunks.append(cctx.compress(raw))

    static_offset = HEADER_SIZE + n_frames * INDEX_ENTRY_SIZE
    static_size = len(static_compressed)
    frame0_offset = static_offset + static_size

    index_entries = []
    off = frame0_offset
    for c in frame_chunks:
        index_entries.append((off, len(c)))
        off += len(c)

    print(f"  writing  {out_path}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(MAGIC)
        f.write(struct.pack("<III", VERSION, n_splats, n_frames))
        f.write(struct.pack("<f", 24.0))  # fps_hint default
        f.write(bbox_min.tobytes())
        f.write(bbox_max.tobytes())
        f.write(struct.pack("<QI", static_offset, static_size))
        f.write(b"\x00" * 24)
        assert f.tell() == HEADER_SIZE, f"header drift: {f.tell()}"
        for off, sz in index_entries:
            f.write(struct.pack("<QII", off, sz, 0))
        assert f.tell() == static_offset, "static offset drift"
        f.write(static_compressed)
        for c in frame_chunks:
            f.write(c)

    out_size = out_path.stat().st_size
    print(f"  done  {out_size/1e6:.1f} MB")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("sequence", nargs="?", default=None,
                   help="single sequence name; omit for all")
    p.add_argument("--force", action="store_true",
                   help="rebuild even if .gsq is newer than the source frames")
    args = p.parse_args()

    CACHE.mkdir(parents=True, exist_ok=True)

    if args.sequence:
        seq_names = [args.sequence]
    else:
        seq_names = sorted(p.name for p in LIB.iterdir()
                           if p.is_dir() and (p / "frames").is_dir())

    n_built = n_skipped = n_failed = 0
    for name in seq_names:
        out = CACHE / f"{name}.gsq"
        frames_dir = LIB / name / "frames"
        if not frames_dir.is_dir():
            print(f"[pack_splats] {name}: no frames/ — skip")
            n_skipped += 1
            continue
        # Up-to-date if .gsq exists and is newer than the newest source frame.
        if out.is_file() and not args.force:
            newest_src = max(p.stat().st_mtime for p in frames_dir.iterdir()
                             if p.suffix == ".ply")
            if out.stat().st_mtime >= newest_src:
                print(f"[pack_splats] {name}: up-to-date, skip")
                n_skipped += 1
                continue
        print(f"[pack_splats] {name}: building")
        t0 = time.time()
        try:
            encode_sequence_to_gsq(name, out)
            n_built += 1
            print(f"  ({time.time()-t0:.1f}s)\n")
        except Exception as e:
            print(f"  FAILED: {e!r}\n", file=sys.stderr)
            n_failed += 1

    print(f"[pack_splats] built={n_built} skipped={n_skipped} failed={n_failed}")
    return 0 if n_failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
