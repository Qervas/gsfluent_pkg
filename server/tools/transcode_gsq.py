"""Transcode a v1 .gsq file to v2 format (keyframe + delta encoding).

  python server/tools/transcode_gsq.py <in.gsq> [--out PATH] [--keyframe-interval 30]

Output defaults to <in>.v2.gsq.  Prints: in size -> out size (ratio) -> out path.
"""
from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

import numpy as np
import zstandard as zstd

_BOOTSTRAP = Path(__file__).resolve().parents[2]
if str(_BOOTSTRAP / "server") not in sys.path:
    sys.path.insert(0, str(_BOOTSTRAP / "server"))

from gsfluent.core.codecs.gsq import (  # noqa: E402
    GSQ_KEYFRAME_INTERVAL,
    HEADER_SIZE,
    INDEX_ENTRY_SIZE,
    MAGIC,
    VERSION,
    ZSTD_LEVEL,
    _v2_frame_payloads,
    decode_frame_raw_i16,
    parse_header_bytes,
)


def transcode_to_v2(
    raw: bytes,
    keyframe_interval: int = GSQ_KEYFRAME_INTERVAL,
) -> bytes:
    """Convert a .gsq byte buffer (any version) to v2 format.

    The static block is copied verbatim from the source.  All per-frame data is
    decoded to absolute int16 via decode_frame_raw_i16, then re-encoded with
    the v2 keyframe+delta scheme.

    Args:
        raw: Source .gsq bytes (v1 or v2).
        keyframe_interval: Keyframe period (default 30).

    Returns:
        v2 .gsq bytes.
    """
    h = parse_header_bytes(raw)
    n = h["n_splats"]
    T = h["n_frames"]
    fps = h["fps_hint"]
    bbox_min = h["bbox_min"]
    bbox_max = h["bbox_max"]
    static_offset = h["static_offset"]
    static_size = h["static_size"]

    # Copy static block verbatim (rgb/opacity/scales are frame-0 attributes).
    static_compressed = bytes(raw[static_offset : static_offset + static_size])

    # Decode every frame to absolute int16.
    xyz_q = np.empty((T, n, 3), dtype=np.int16)
    quat_q = np.empty((T, n, 3), dtype=np.int16)
    for t in range(T):
        xyz_q[t], quat_q[t] = decode_frame_raw_i16(raw, t)

    # Re-encode with v2 keyframe+delta payloads.
    cctx = zstd.ZstdCompressor(level=ZSTD_LEVEL)
    payloads, flags = _v2_frame_payloads(xyz_q, quat_q, cctx, keyframe_interval)

    # Assemble v2 container.
    out = bytearray()
    out += MAGIC
    out += struct.pack("<III", VERSION, n, T)           # version=2
    out += struct.pack("<f", float(fps))
    out += bbox_min.astype(np.float32).tobytes()
    out += bbox_max.astype(np.float32).tobytes()
    frame_data_start = HEADER_SIZE + T * INDEX_ENTRY_SIZE + len(static_compressed)
    out += struct.pack("<QI", HEADER_SIZE + T * INDEX_ENTRY_SIZE, len(static_compressed))
    out += b"\x00" * 24                                # pad to HEADER_SIZE (80)

    assert len(out) == HEADER_SIZE, f"header size drift: {len(out)}"

    off = frame_data_start
    for c, fl in zip(payloads, flags):
        out += struct.pack("<QII", off, len(c), fl)
        off += len(c)

    out += static_compressed
    for c in payloads:
        out += c

    return bytes(out)


def main() -> int:
    p = argparse.ArgumentParser(
        description="Transcode a .gsq file (any version) to v2 format."
    )
    p.add_argument("input", help="Input .gsq file path")
    p.add_argument("--out", default=None, help="Output path (default: <input>.v2.gsq)")
    p.add_argument(
        "--keyframe-interval",
        type=int,
        default=GSQ_KEYFRAME_INTERVAL,
        help=f"Keyframe interval (default: {GSQ_KEYFRAME_INTERVAL})",
    )
    args = p.parse_args()

    in_path = Path(args.input)
    out_path = Path(args.out) if args.out else in_path.with_suffix(".v2.gsq")

    raw = in_path.read_bytes()
    result = transcode_to_v2(raw, keyframe_interval=args.keyframe_interval)
    out_path.write_bytes(result)

    ratio = len(result) / len(raw) if len(raw) else float("nan")
    print(
        f"{len(raw):,} B  ->  {len(result):,} B  "
        f"({ratio:.3f}x)  ->  {out_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
