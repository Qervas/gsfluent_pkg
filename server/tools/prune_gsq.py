"""Analyze + prune .gsq splat sequences.

  # show the retention curve (no file written) — find the safe ratio
  python server/tools/prune_gsq.py analyze work/cache/viser/<name>.gsq

  # prune to a target retention (keep that fraction of total significance)
  python server/tools/prune_gsq.py prune work/cache/viser/<name>.gsq \
      --retention 0.995 --out work/cache/viser/<name>.pruned.gsq

  # prune to an explicit keep-count
  python server/tools/prune_gsq.py prune <in> --keep 250000 --out <out>
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import zstandard as zstd

_BOOTSTRAP = Path(__file__).resolve().parents[2]
if str(_BOOTSTRAP / "server") not in sys.path:
    sys.path.insert(0, str(_BOOTSTRAP / "server"))

from gsfluent.core.codecs.gsq import parse_header_bytes  # noqa: E402
from gsfluent.core.codecs.gsq_prune import (  # noqa: E402
    compute_significance, select_keep_indices, retention_curve, prune_gsq_bytes,
)


def _load_static(raw: bytes):
    h = parse_header_bytes(raw)
    n = h["n_splats"]
    s_off, s_sz = h["static_offset"], h["static_size"]
    static = zstd.ZstdDecompressor().decompress(bytes(raw[s_off:s_off + s_sz]))
    op = np.frombuffer(static[n * 3 * 2: n * 3 * 2 + n], dtype=np.uint8).astype(np.float32) / 255.0
    sc = np.frombuffer(static[n * 3 * 2 + n: n * 3 * 2 + n + n * 3 * 2], dtype=np.float16).reshape(n, 3).astype(np.float32)
    return h, op, sc


def cmd_analyze(args) -> int:
    raw = Path(args.gsq).read_bytes()
    h, op, sc = _load_static(raw)
    sig = compute_significance(op, sc)
    print(f"{args.gsq}")
    print(f"  n_splats={h['n_splats']:,}  n_frames={h['n_frames']}  file={len(raw)/1e6:.0f} MB")
    print(f"  {'retention':>10}  {'keep':>10}  {'prune%':>7}  {'~file MB':>9}")
    for c in retention_curve(sig):
        approx_mb = len(raw) / 1e6 * c["keep_count"] / h["n_splats"]
        print(f"  {c['retention']:>10.3f}  {c['keep_count']:>10,}  "
              f"{c['prune_ratio']*100:>6.1f}%  {approx_mb:>8.0f}")
    return 0


def cmd_prune(args) -> int:
    raw = Path(args.gsq).read_bytes()
    h, op, sc = _load_static(raw)
    sig = compute_significance(op, sc)
    if args.keep is not None:
        keep_count = args.keep
    else:
        c = next(c for c in retention_curve(sig, (args.retention,)))
        keep_count = c["keep_count"]
    keep = select_keep_indices(sig, keep_count)
    t0 = time.time()
    pruned = prune_gsq_bytes(raw, keep)
    Path(args.out).write_bytes(pruned)
    print(f"pruned {h['n_splats']:,} → {len(keep):,} splats "
          f"({(1-len(keep)/h['n_splats'])*100:.1f}% dropped)  "
          f"{len(raw)/1e6:.0f} MB → {len(pruned)/1e6:.0f} MB  "
          f"in {time.time()-t0:.1f}s → {args.out}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("analyze"); a.add_argument("gsq")
    a.set_defaults(fn=cmd_analyze)
    pr = sub.add_parser("prune"); pr.add_argument("gsq")
    pr.add_argument("--retention", type=float, default=0.995)
    pr.add_argument("--keep", type=int, default=None)
    pr.add_argument("--out", required=True)
    pr.set_defaults(fn=cmd_prune)
    args = p.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
