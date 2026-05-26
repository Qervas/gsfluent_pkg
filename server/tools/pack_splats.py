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
import os
import sys
import time
from pathlib import Path

# Bootstrap so `gsfluent` is importable without pip install (server/tools/ is
# outside the package).
_BOOTSTRAP_ROOT = Path(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT / "server") not in sys.path:
    sys.path.insert(0, str(_BOOTSTRAP_ROOT / "server"))

from gsfluent._paths import SEQUENCES, CACHE_SPLATS  # noqa: E402
from gsfluent.core.codecs.gsq import GSQCodec, parse_header_bytes  # noqa: E402
from gsfluent.core.codecs.gsq_prune import prune_to_retention  # noqa: E402
from gsfluent.observability.jsonlog import StdlibJSONEmitter  # noqa: E402

# Default retention for the post-pack prune step. Pruning is ON by default for
# all NEW sequences built through this tool (validated at retention 0.98,
# accepted). Override via the GSFLUENT_PRUNE_RETENTION env var; set it to "0"
# or "" to disable pruning entirely (full-resolution .gsq).
DEFAULT_PRUNE_RETENTION = 0.98


def _resolve_prune_retention() -> float:
    """Read GSFLUENT_PRUNE_RETENTION; return 0.0 (disabled) or the target.

    Empty / unset → default. "0" / "" → disabled. Out-of-range → disabled
    with a warning (a malformed env var should never silently keep full-res
    OR over-prune; disabling is the safe failure).
    """
    raw = os.environ.get("GSFLUENT_PRUNE_RETENTION")
    if raw is None:
        return DEFAULT_PRUNE_RETENTION
    raw = raw.strip()
    if raw == "" or raw == "0":
        return 0.0
    try:
        val = float(raw)
    except ValueError:
        print(f"[pack_splats] WARN: bad GSFLUENT_PRUNE_RETENTION={raw!r}; "
              f"disabling prune", file=sys.stderr)
        return 0.0
    if not (0.0 < val <= 1.0):
        print(f"[pack_splats] WARN: GSFLUENT_PRUNE_RETENTION={val} out of "
              f"(0,1]; disabling prune", file=sys.stderr)
        return 0.0
    return val


def _prune_in_place(out: Path, retention: float) -> None:
    """Read the just-written .gsq at `out`, prune to `retention`, overwrite.

    No-op-safe: if the helper decides nothing should drop at this retention it
    returns the original bytes and we still report before==after. Logs a
    structured-ish line: n_splats + size before→after + retention.
    """
    raw = out.read_bytes()
    n_before = parse_header_bytes(raw)["n_splats"]
    sz_before = len(raw)
    pruned = prune_to_retention(raw, retention)
    n_after = parse_header_bytes(pruned)["n_splats"]
    out.write_bytes(pruned)
    print(
        f"  pruned retention={retention:.3f}  "
        f"splats {n_before:,}→{n_after:,}  "
        f"size {sz_before/1e6:.1f}MB→{len(pruned)/1e6:.1f}MB"
    )


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("sequence", nargs="?", default=None,
                   help="single sequence name; omit for all")
    p.add_argument("--force", action="store_true",
                   help="rebuild even if .gsq is newer than the source frames")
    args = p.parse_args()

    CACHE_SPLATS.mkdir(parents=True, exist_ok=True)

    if args.sequence:
        seq_names = [args.sequence]
    else:
        seq_names = sorted(
            p.name for p in SEQUENCES.iterdir()
            if p.is_dir() and (p / "frames").is_dir()
        )

    prune_retention = _resolve_prune_retention()
    if prune_retention:
        print(f"[pack_splats] prune ON  retention={prune_retention:.3f}")
    else:
        print("[pack_splats] prune OFF (full-resolution output)")

    codec = GSQCodec()
    # The CLI logs to stdout in plain text (matches the prior behavior the
    # runner subprocess capture relies on). JSON events also stream to stderr
    # via StdlibJSONEmitter for downstream parsing if anyone wires it up.
    obs = StdlibJSONEmitter(stream=sys.stderr)

    n_built = n_skipped = n_failed = 0
    for name in seq_names:
        out = CACHE_SPLATS / f"{name}.gsq"
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
            print(
                f"  done  {out.stat().st_size / 1e6:.1f} MB  "
                f"({meta.n_frames} frames, {meta.n_splats} splats, "
                f"{time.time() - t0:.1f}s)"
            )
            if prune_retention:
                _prune_in_place(out, prune_retention)
            print()
            n_built += 1
        except Exception as e:
            print(f"  FAILED: {e!r}\n", file=sys.stderr)
            n_failed += 1

    print(f"[pack_splats] built={n_built} skipped={n_skipped} failed={n_failed}")
    return 0 if n_failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
