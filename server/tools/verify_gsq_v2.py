"""Bit-exact + performance verification for .gsq v2 transcoding.

Verifies that transcode_to_v2 produces a byte-identical decoded result
compared to the source file, for every frame. Also reports sequential
decode throughput and worst-case scrub latency.

Usage:
    python server/tools/verify_gsq_v2.py <path/to/file.gsq>

Exit code 0 = bit-exact (max diff == 0); non-zero = mismatch found.
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
import time
from pathlib import Path

import numpy as np

_BOOTSTRAP = Path(__file__).resolve().parents[2]
if str(_BOOTSTRAP / "server") not in sys.path:
    sys.path.insert(0, str(_BOOTSTRAP / "server"))

from gsfluent.core.codecs.gsq import (  # noqa: E402
    GSQ_KEYFRAME_INTERVAL,
    decode_frame_raw_i16,
    parse_header_bytes,
)

# Load the sibling transcode_gsq module by file path (no package __init__ needed).
_spec = importlib.util.spec_from_file_location(
    "transcode_gsq", Path(__file__).resolve().parent / "transcode_gsq.py"
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]
transcode_to_v2 = _mod.transcode_to_v2


def main() -> int:
    p = argparse.ArgumentParser(
        description="Bit-exact + perf verification for .gsq v2 transcoding."
    )
    p.add_argument("gsq", help="Source .gsq file (v1 or v2)")
    p.add_argument(
        "--keyframe-interval",
        type=int,
        default=GSQ_KEYFRAME_INTERVAL,
        help=f"Keyframe interval for transcode (default: {GSQ_KEYFRAME_INTERVAL})",
    )
    args = p.parse_args()

    src_path = Path(args.gsq)
    if not src_path.exists():
        print(f"ERROR: file not found: {src_path}", file=sys.stderr)
        return 2

    # --- 1. Read source ---
    print(f"Reading source: {src_path}")
    raw = src_path.read_bytes()
    h_src = parse_header_bytes(raw)
    src_version = h_src["version"]
    n_splats = h_src["n_splats"]
    n_frames = h_src["n_frames"]
    src_mb = len(raw) / 1e6

    print(f"  source version : {src_version}")
    print(f"  n_splats       : {n_splats:,}")
    print(f"  n_frames       : {n_frames}")
    print(f"  source size    : {src_mb:.1f} MB")

    # --- 2. Transcode to v2 ---
    K = args.keyframe_interval
    print(f"\nTranscoding to v2 (keyframe_interval={K}) ...")
    t_transcode0 = time.perf_counter()
    v2 = transcode_to_v2(raw, keyframe_interval=K)
    t_transcode1 = time.perf_counter()
    v2_mb = len(v2) / 1e6
    ratio = v2_mb / src_mb if src_mb > 0 else float("nan")
    h_v2 = parse_header_bytes(v2)
    print(f"  transcode time : {t_transcode1 - t_transcode0:.1f}s")
    print(f"  v2 size        : {v2_mb:.1f} MB")
    print(f"  ratio          : {ratio:.3f}x  ({(1-ratio)*100:.1f}% smaller)")
    print(f"  v2 version     : {h_v2['version']}")

    # --- 3. Bit-exact check: every frame ---
    print(f"\nVerifying {n_frames} frames (bit-exact diff) ...")
    max_xyz_diff = 0
    max_q_diff = 0
    mismatch_frames: list[int] = []

    for t in range(n_frames):
        xs, qs = decode_frame_raw_i16(raw, t)
        xv, qv = decode_frame_raw_i16(v2, t)
        # Compute diffs in int32 to avoid int16 overflow on the subtraction.
        d_xyz = int(np.abs(xs.astype(np.int32) - xv.astype(np.int32)).max())
        d_q = int(np.abs(qs.astype(np.int32) - qv.astype(np.int32)).max())
        if d_xyz > max_xyz_diff:
            max_xyz_diff = d_xyz
        if d_q > max_q_diff:
            max_q_diff = d_q
        if d_xyz != 0 or d_q != 0:
            mismatch_frames.append(t)
        if (t + 1) % 20 == 0 or t == n_frames - 1:
            print(
                f"  [{t+1:>4}/{n_frames}]  max_xyz_diff={max_xyz_diff}"
                f"  max_q_diff={max_q_diff}",
                flush=True,
            )

    # --- 4. Timing: sequential decode of all v2 frames ---
    print(f"\nTiming: sequential decode of all {n_frames} v2 frames ...")
    t_seq0 = time.perf_counter()
    for t in range(n_frames):
        decode_frame_raw_i16(v2, t)
    t_seq1 = time.perf_counter()
    seq_total_ms = (t_seq1 - t_seq0) * 1000.0
    seq_ms_per_frame = seq_total_ms / n_frames if n_frames > 0 else 0.0

    # --- 5. Timing: worst-case scrub (last delta frame before 2nd keyframe) ---
    # Frame 2*K - 1 is the last frame in the second keyframe window,
    # requiring accumulation of K-1 deltas — the most reconstruction work.
    worst_idx = min(2 * K - 1, n_frames - 1)
    print(f"Timing: worst-case scrub at frame {worst_idx} (2*K-1={2*K-1}) ...")
    t_scrub0 = time.perf_counter()
    decode_frame_raw_i16(v2, worst_idx)
    t_scrub1 = time.perf_counter()
    scrub_ms = (t_scrub1 - t_scrub0) * 1000.0

    # --- 6. Report ---
    bit_exact = max_xyz_diff == 0 and max_q_diff == 0
    print()
    print("=" * 60)
    print("VERIFICATION REPORT")
    print("=" * 60)
    print(f"  Source file      : {src_path}")
    print(f"  Source version   : {src_version}")
    print(f"  n_splats         : {n_splats:,}")
    print(f"  n_frames         : {n_frames}")
    print(f"  Source size      : {src_mb:.1f} MB")
    print(f"  v2 size          : {v2_mb:.1f} MB")
    print(f"  Ratio            : {ratio:.3f}x  ({(1 - ratio) * 100:.1f}% smaller)")
    print(
        f"  Max abs xyz diff : {max_xyz_diff}"
        f"  {'OK' if max_xyz_diff == 0 else 'MISMATCH!'}"
    )
    print(
        f"  Max abs q diff   : {max_q_diff}"
        f"  {'OK' if max_q_diff == 0 else 'MISMATCH!'}"
    )
    if mismatch_frames:
        preview = mismatch_frames[:10]
        suffix = "..." if len(mismatch_frames) > 10 else ""
        print(f"  Mismatch frames  : {len(mismatch_frames)}  {preview}{suffix}")
    else:
        print(f"  Mismatch frames  : 0")
    print(f"  Sequential decode: {seq_ms_per_frame:.2f} ms/frame  (total {seq_total_ms:.0f} ms)")
    print(f"  Worst-case scrub : {scrub_ms:.1f} ms  (frame {worst_idx})")
    print("=" * 60)
    if bit_exact:
        print("RESULT: BIT-EXACT -- all frames match (max diff = 0)")
    else:
        print("RESULT: MISMATCH DETECTED -- v2 decode does NOT match source!")
        print(f"  max_xyz_diff={max_xyz_diff}, max_q_diff={max_q_diff}")
        print(f"  First mismatching frames: {mismatch_frames[:20]}")
    print("=" * 60)

    return 0 if bit_exact else 1


if __name__ == "__main__":
    raise SystemExit(main())
