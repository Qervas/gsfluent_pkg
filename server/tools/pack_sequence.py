"""Pack a frames-dir sequence into a single `frames.bin` with int16 xyz.

On-disk layout (little-endian):

    offset  size       field           notes
    ------  ---------  --------------  -------------------------------------
    0       4          magic           ASCII "GSSQ"
    4       4          version         u32, currently 1
    8       4          n_splats        u32, splats per frame (== ref count)
    12      4          n_frames        u32, total frames including frame 0
    16      24         bbox            6 × fp32: (xmin, ymin, zmin, xmax, ymax, zmax)
    40      ...        xyz_int16       n_frames × n_splats × 3 × 2 bytes
                                       row-major: [frame][splat][axis]

The static gaussian attrs (scale, rot, SH, opacity) are NOT in this file.
They stay in `frame_0000.ply` (the bootstrap inputFile that the viewer
loads once at start). frames.bin only carries the time-varying xyz.

Dequantization (reader-side):

    norm  = (q + 32768) / 65535            # [0, 1] per axis
    xyz   = norm * (bbox.hi - bbox.lo) + bbox.lo

Precision: on a 1 m extent, int16 → 15 μm. Sub-mm for everything we sim.

Usage:
    python server/tools/pack_sequence.py <sequence_name>
    python server/tools/pack_sequence.py --check <sequence_name>   # round-trip diff

Output:
    work/library/sequences/<name>/frames.bin
    (frame_0000.ply stays in place as the bootstrap; frames 1+ become
    redundant once packed but are not deleted by this tool.)
"""
from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

import numpy as np
from plyfile import PlyData

MAGIC = b"GSSQ"
VERSION = 1
HEADER_SIZE = 4 + 4 + 4 + 4 + 6 * 4  # = 40 bytes

REPO = Path(__file__).resolve().parents[1]
LIB = REPO / "work" / "library" / "sequences"


def _read_xyz(ply_path: Path) -> np.ndarray:
    v = PlyData.read(str(ply_path))["vertex"].data
    return np.stack([v["x"], v["y"], v["z"]], axis=1).astype(np.float32)


def _global_bbox(frames: list[Path]) -> tuple[np.ndarray, np.ndarray]:
    lo = np.array([np.inf, np.inf, np.inf], dtype=np.float64)
    hi = np.array([-np.inf, -np.inf, -np.inf], dtype=np.float64)
    for f in frames:
        xyz = _read_xyz(f)
        lo = np.minimum(lo, xyz.min(axis=0))
        hi = np.maximum(hi, xyz.max(axis=0))
    # Pad by 1% so quantization clipping at the boundary never bites.
    span = hi - lo
    lo -= span * 0.005
    hi += span * 0.005
    return lo.astype(np.float32), hi.astype(np.float32)


def _quantize(xyz: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> np.ndarray:
    norm = (xyz - lo) / (hi - lo)
    q = norm * 65535.0 - 32768.0
    return np.clip(q, -32768, 32767).astype(np.int16)


def _dequantize(q: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> np.ndarray:
    norm = (q.astype(np.float32) + 32768.0) / 65535.0
    return norm * (hi - lo) + lo


def pack(seq_dir: Path) -> Path:
    frames_dir = seq_dir / "frames"
    frames = sorted(p for p in frames_dir.iterdir()
                    if p.is_file() and p.name.startswith("frame_") and p.suffix == ".ply")
    if not frames:
        raise SystemExit(f"no frame_*.ply in {frames_dir}")

    n_frames = len(frames)
    n_splats = _read_xyz(frames[0]).shape[0]
    print(f"[pack] {seq_dir.name}: {n_frames} frames × {n_splats:,} splats")

    print(f"[pack] computing global bbox over {n_frames} frames...")
    lo, hi = _global_bbox(frames)
    print(f"[pack]   bbox lo={lo}  hi={hi}  span={hi - lo}")

    out_path = seq_dir / "frames.bin"
    expected_bytes = HEADER_SIZE + n_frames * n_splats * 3 * 2
    print(f"[pack] writing {out_path}  (expected {expected_bytes / 1e6:.1f} MB)")

    with open(out_path, "wb") as f:
        # Header
        f.write(MAGIC)
        f.write(struct.pack("<I", VERSION))
        f.write(struct.pack("<I", n_splats))
        f.write(struct.pack("<I", n_frames))
        f.write(struct.pack("<6f", *lo, *hi))
        # Per-frame int16 xyz
        for i, frame_path in enumerate(frames):
            xyz = _read_xyz(frame_path)
            if xyz.shape[0] != n_splats:
                raise SystemExit(
                    f"splat count mismatch at {frame_path.name}: "
                    f"{xyz.shape[0]} vs {n_splats}"
                )
            q = _quantize(xyz, lo, hi)
            f.write(q.tobytes())
            if (i + 1) % 25 == 0 or i + 1 == n_frames:
                print(f"  {i+1}/{n_frames}", flush=True)

    actual = out_path.stat().st_size
    print(f"[pack] done. on-disk: {actual / 1e6:.1f} MB  (expected {expected_bytes / 1e6:.1f} MB)")
    return out_path


def check(seq_dir: Path) -> int:
    """Round-trip the packed data, compare against source. Returns max error in meters."""
    packed = seq_dir / "frames.bin"
    frames_dir = seq_dir / "frames"
    if not packed.is_file():
        raise SystemExit(f"no frames.bin at {packed}")

    with open(packed, "rb") as f:
        magic = f.read(4)
        if magic != MAGIC:
            raise SystemExit(f"bad magic: {magic!r}")
        version, n_splats, n_frames = struct.unpack("<3I", f.read(12))
        lo = np.array(struct.unpack("<3f", f.read(12)), dtype=np.float32)
        hi = np.array(struct.unpack("<3f", f.read(12)), dtype=np.float32)
        print(f"[check] header: v{version}  n_splats={n_splats:,}  n_frames={n_frames}")
        print(f"[check]   bbox lo={lo}  hi={hi}")
        # Read all xyz_int16 at once
        body = np.frombuffer(f.read(), dtype=np.int16)
        body = body.reshape(n_frames, n_splats, 3)

    # Round-trip check on a few frames
    frames = sorted(p for p in frames_dir.iterdir()
                    if p.is_file() and p.name.startswith("frame_") and p.suffix == ".ply")
    test_indices = [0, n_frames // 4, n_frames // 2, n_frames - 1]
    max_err_global = 0.0
    for idx in test_indices:
        src_xyz = _read_xyz(frames[idx])
        recovered = _dequantize(body[idx], lo, hi)
        err = np.abs(recovered - src_xyz)
        max_err = float(err.max())
        mean_err = float(err.mean())
        max_err_global = max(max_err_global, max_err)
        print(f"[check] frame {idx:3d}: max={max_err * 1000:.4f} mm  mean={mean_err * 1000:.6f} mm")
    print(f"[check] overall max error: {max_err_global * 1000:.4f} mm")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("sequence", help="Library sequence name")
    ap.add_argument("--check", action="store_true",
                    help="After packing (or on existing frames.bin), round-trip diff")
    args = ap.parse_args()

    seq_dir = LIB / args.sequence
    if not seq_dir.is_dir():
        print(f"ERROR: sequence dir not found: {seq_dir}", file=sys.stderr)
        return 2

    if not (seq_dir / "frames.bin").exists() or not args.check:
        pack(seq_dir)
    if args.check:
        check(seq_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
