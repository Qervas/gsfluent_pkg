"""CLI wrapper around gsfluent.core.codecs.gsq.GSQCodec.

The encode/sanitize/quantize pipeline lives in
server/gsfluent/core/codecs/gsq.py. This script handles only:
  - argparse
  - sequence discovery from work/library/sequences/
  - up-to-date staleness check vs source frame mtimes
  - delegating to GSQCodec.encode_sequence_dir

Usage (unchanged from the prior implementation):
    python server/tools/pack_splats.py                # all sequences
    python server/tools/pack_splats.py <seq>          # one sequence
    python server/tools/pack_splats.py --force <seq>  # rebuild
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# Bootstrap so `gsfluent` is importable without pip install (server/tools/ is
# outside the package).
_BOOTSTRAP_ROOT = Path(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT / "server") not in sys.path:
    sys.path.insert(0, str(_BOOTSTRAP_ROOT / "server"))

from gsfluent._paths import SEQUENCES, CACHE_VISER  # noqa: E402
from gsfluent.core.codecs.gsq import GSQCodec  # noqa: E402
from gsfluent.observability.jsonlog import StdlibJSONEmitter  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("sequence", nargs="?", default=None,
                   help="single sequence name; omit for all")
    p.add_argument("--force", action="store_true",
                   help="rebuild even if .gsq is newer than the source frames")
    args = p.parse_args()

    CACHE_VISER.mkdir(parents=True, exist_ok=True)

    if args.sequence:
        seq_names = [args.sequence]
    else:
        seq_names = sorted(
            p.name for p in SEQUENCES.iterdir()
            if p.is_dir() and (p / "frames").is_dir()
        )

    codec = GSQCodec()
    # The CLI logs to stdout in plain text (matches the prior behavior the
    # runner subprocess capture relies on). JSON events also stream to stderr
    # via StdlibJSONEmitter for downstream parsing if anyone wires it up.
    obs = StdlibJSONEmitter(stream=sys.stderr)

    n_built = n_skipped = n_failed = 0
    for name in seq_names:
        out = CACHE_VISER / f"{name}.gsq"
        frames_dir = SEQUENCES / name / "frames"
        if not frames_dir.is_dir():
            print(f"[pack_splats] {name}: no frames/ — skip")
            n_skipped += 1
            continue
        if out.is_file() and not args.force:
            newest_src = max(
                p.stat().st_mtime for p in frames_dir.iterdir() if p.suffix == ".ply"
            )
            if out.stat().st_mtime >= newest_src:
                print(f"[pack_splats] {name}: up-to-date, skip")
                n_skipped += 1
                continue
        print(f"[pack_splats] {name}: building")
        t0 = time.time()
        try:
            meta = codec.encode_sequence_dir(frames_dir, out, on_event=obs)
            n_built += 1
            print(
                f"  done  {out.stat().st_size / 1e6:.1f} MB  "
                f"({meta.n_frames} frames, {meta.n_splats} splats, "
                f"{time.time() - t0:.1f}s)\n"
            )
        except Exception as e:
            print(f"  FAILED: {e!r}\n", file=sys.stderr)
            n_failed += 1

    print(f"[pack_splats] built={n_built} skipped={n_skipped} failed={n_failed}")
    return 0 if n_failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
