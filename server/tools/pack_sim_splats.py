"""Encode RAW sim particles (pre-fuse) into a .gsq for A/B comparison.

Counterpart to pack_splats.py: instead of reading the fused
frame_*.ply (683k splats with full 3DGS attrs), this reads the sim's
own sim_*.ply (200k particles with just xyz) and produces a .gsq that
viser can render. Output stem is "<seq>_rawsim" so it shows up
alongside the fused sequence and you can click between them.

The point: visualize what the MPM solver actually emits, so you can
see what the fuse step contributes. Expect the raw view to be:
  - Sparser (200k vs 683k splats)
  - Uniform color (no per-splat rgb from the reference)
  - Roughly-isotropic blobs (no per-splat scales/rotations from the reference)
  - But physically truthful — exactly where the sim says particles are

Field expectations:
  - Sim plys have at minimum (x, y, z). If they also have cov_xx
    fields (particle_F mode), we IGNORE them here — they don't fit
    the v1 .gsq schema (which stores scales static, quat per-frame).
    The point of this tool is "show the sim's geometry," not "the
    most accurate possible render of sim cov" — that latter would
    need a v3 schema.

Usage:
    python server/tools/pack_sim_splats.py <run_name>
    python server/tools/pack_sim_splats.py <run_name> --sim-home /path/to/GaussianFluent

The sim plys live at  $GSFLUENT_SIM_HOME/output/<run_name>/simulation_ply/sim_*.ply.
If the runner cleaned them up after a successful sim, you need
GSFLUENT_KEEP_PLYS=1 on the original run to preserve them.
"""
from __future__ import annotations

import argparse
import os
import struct
import sys
import time
from pathlib import Path

import numpy as np
import zstandard as zstd

REPO = Path(__file__).resolve().parents[2]
CACHE = REPO / "work" / "cache" / "viser"
LIB = REPO / "work" / "library" / "sequences"

MAGIC = b"GSQ1"
VERSION = 1
HEADER_SIZE = 80
INDEX_ENTRY_SIZE = 16
ZSTD_LEVEL = 9


def _quantize_xyz(xyz, bmin, bmax):
    span = (bmax - bmin).astype(np.float64)
    span = np.where(span > 0, span, 1.0)
    q = (xyz.astype(np.float64) - bmin) / span * 65535.0
    q = np.clip(np.round(q), 0, 65535).astype(np.int32) - 32768
    return q.astype(np.int16)


def encode_sim_to_gsq(sim_ply_dir: Path, out_path: Path,
                      splat_radius: float = 0.01) -> None:
    """Read sim_*.ply files → write a .gsq with synthetic static attrs.

    splat_radius sets the visual size of each particle's Gaussian. ~0.01
    in normalized sim units = small but visible. Bump it if the result
    looks too sparse.
    """
    from plyfile import PlyData

    sim_paths = sorted(p for p in sim_ply_dir.iterdir()
                       if p.is_file() and p.name.startswith("sim_") and p.suffix == ".ply")
    if not sim_paths:
        raise SystemExit(f"no sim_*.ply in {sim_ply_dir}")
    n_frames = len(sim_paths)

    print(f"  reading {n_frames} sim plys from {sim_ply_dir}")
    v0 = PlyData.read(str(sim_paths[0]))["vertex"].data
    n_splats = v0.shape[0]
    print(f"  n_particles: {n_splats}")

    xyz_all = np.empty((n_frames, n_splats, 3), dtype=np.float32)
    t0 = time.time()
    for i, p in enumerate(sim_paths):
        v = PlyData.read(str(p))["vertex"].data
        if v.shape[0] != n_splats:
            raise SystemExit(f"{p.name} has {v.shape[0]} particles, expected {n_splats}")
        xyz_all[i, :, 0] = v["x"]
        xyz_all[i, :, 1] = v["y"]
        xyz_all[i, :, 2] = v["z"]
        if (i + 1) % 25 == 0 or i + 1 == n_frames:
            print(f"    {i+1}/{n_frames}  ({time.time()-t0:.1f}s)", flush=True)

    # NaN forward-fill — same defense as pack_splats.
    nan_mask = np.isnan(xyz_all).any(axis=2)
    if nan_mask.any():
        n_bad = int(nan_mask.sum())
        print(f"  forward-filling {n_bad} NaN positions")
        if nan_mask[0].any():
            finite = ~nan_mask[0]
            ctr = xyz_all[0][finite].mean(axis=0) if finite.any() else np.zeros(3, dtype=np.float32)
            xyz_all[0][nan_mask[0]] = ctr
        for t in range(1, n_frames):
            bad = nan_mask[t]
            if bad.any():
                xyz_all[t][bad] = xyz_all[t - 1][bad]

    bbox_min = xyz_all.reshape(-1, 3).min(axis=0).astype(np.float32)
    bbox_max = xyz_all.reshape(-1, 3).max(axis=0).astype(np.float32)
    print(f"  bbox: {bbox_min.tolist()} .. {bbox_max.tolist()}")

    # Synthetic static attrs: uniform color, full opacity, isotropic scales.
    # Distinct color from any reference so the "raw" view is obviously
    # the sim — a warmer beige so it reads as "physics particles."
    rgb_f16 = np.tile(np.array([0.9, 0.7, 0.5], dtype=np.float16), (n_splats, 1))
    opacity_u8 = np.full((n_splats,), 220, dtype=np.uint8)  # ~0.86 alpha
    scales_f16 = np.full((n_splats, 3), splat_radius, dtype=np.float16)

    # Identity quaternion per frame (w=1, x=y=z=0).
    quat_all = np.zeros((n_frames, n_splats, 4), dtype=np.float32)
    quat_all[..., 0] = 1.0

    print("  quantizing + compressing")
    xyz_q = _quantize_xyz(xyz_all, bbox_min, bbox_max)
    # All-zero (i16) for quat axis-vec — identity rotation, perfect compression.
    quat_q = np.zeros((n_frames, n_splats, 3), dtype=np.int16)

    cctx = zstd.ZstdCompressor(level=ZSTD_LEVEL)
    static_uncompressed = rgb_f16.tobytes() + opacity_u8.tobytes() + scales_f16.tobytes()
    static_compressed = cctx.compress(static_uncompressed)

    frame_chunks = []
    for t in range(n_frames):
        raw = xyz_q[t].tobytes() + quat_q[t].tobytes()
        frame_chunks.append(cctx.compress(raw))

    static_offset = HEADER_SIZE + n_frames * INDEX_ENTRY_SIZE
    static_size = len(static_compressed)
    off = static_offset + static_size
    index_entries = []
    for c in frame_chunks:
        index_entries.append((off, len(c)))
        off += len(c)

    print(f"  writing  {out_path}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(MAGIC)
        f.write(struct.pack("<III", VERSION, n_splats, n_frames))
        f.write(struct.pack("<f", 24.0))
        f.write(bbox_min.tobytes())
        f.write(bbox_max.tobytes())
        f.write(struct.pack("<QI", static_offset, static_size))
        f.write(b"\x00" * 24)
        for off, sz in index_entries:
            f.write(struct.pack("<QII", off, sz, 0))
        f.write(static_compressed)
        for c in frame_chunks:
            f.write(c)

    print(f"  done  {out_path.stat().st_size/1e6:.1f} MB")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("run_name", help="sim run name (the dir under <SIM_HOME>/output/)")
    p.add_argument("--sim-home", default=os.environ.get("GSFLUENT_SIM_HOME"),
                   help="defaults to $GSFLUENT_SIM_HOME")
    p.add_argument("--out-stem", default=None,
                   help="output filename stem (default: <run_name>_rawsim)")
    p.add_argument("--splat-radius", type=float, default=0.01,
                   help="isotropic Gaussian scale per particle (default 0.01)")
    args = p.parse_args()

    if not args.sim_home:
        print("ERROR: --sim-home or $GSFLUENT_SIM_HOME is required", file=sys.stderr)
        return 2
    sim_ply_dir = Path(args.sim_home) / "output" / args.run_name / "simulation_ply"
    if not sim_ply_dir.is_dir():
        print(f"ERROR: {sim_ply_dir} not found. Was GSFLUENT_KEEP_PLYS=1 on the run?",
              file=sys.stderr)
        return 2
    stem = args.out_stem or f"{args.run_name}_rawsim"
    out = CACHE / f"{stem}.gsq"
    CACHE.mkdir(parents=True, exist_ok=True)
    try:
        encode_sim_to_gsq(sim_ply_dir, out, splat_radius=args.splat_radius)
    except Exception as e:
        print(f"FAILED: {e!r}", file=sys.stderr)
        return 1

    # Register a minimal library entry so the SPA outliner surfaces it
    # next to the fused version. We don't write frame plys here — the
    # entry is purely metadata pointing at the .gsq we just built.
    import json
    from datetime import datetime, timezone
    seq_dir = LIB / stem
    seq_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "name": stem,
        "kind": "sequence",
        "source": "rawsim",
        "source_path": str(sim_ply_dir),
        "model_ref": args.run_name.split("_", 2)[0] if "_" in args.run_name else None,
        "frame_count": None,  # backend reads this from the .gsq if needed
        "fps_hint": 24,
        "n_splats": None,
        "coord_convention": "z-up",
        "first_frame_full": True,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "converted_from": None,
    }
    meta_path = seq_dir / "_meta.json"
    meta_path.write_text(json.dumps(meta, indent=2))
    print(f"  registered library entry: {seq_dir}")
    print(f"\nLoad in the SPA via the outliner — it should appear as 'sequence:{stem}'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
