"""Batch-convert every sequence in the library to a viser-loadable .npz.

Output: `work/cache/viser/<sequence>.npz` for each sequence under
`work/library/sequences/`. Skips sequences whose .npz exists, has the
current schema version, AND is newer than the source `frames/frame_0000.ply`.
Pass `--force` to rebuild everything regardless.

Schema versioning: sequence_to_viser_npz.py emits v2 when per-frame
rotation fields are available. Older v1 caches lack the per-frame quats
needed for the sharp-motion path in viser_headless.py — this script
detects them and triggers a rebuild even when the file mtime alone
wouldn't.

Usage:
    python server/tools/batch_convert_to_npz.py            # all sequences, skip up-to-date
    python server/tools/batch_convert_to_npz.py --force    # rebuild everything
    python server/tools/batch_convert_to_npz.py <seq>      # one sequence only
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LIB = REPO / "work" / "library" / "sequences"
CACHE = REPO / "work" / "cache" / "viser"
CONVERTER = Path(__file__).parent / "sequence_to_viser_npz.py"

# The schema we expect on disk. Bump in lockstep with
# sequence_to_viser_npz.py + viser_headless.py whenever the npz layout
# changes. Old caches written under a different version are detected as
# stale by `_is_stale` and rebuilt.
CURRENT_SCHEMA = 2


def _cached_schema(out: Path) -> int:
    """Read the npz's `version` field; absent → v1 (the old layout had
    no version key). Done with `np.load`'s lazy zip reader so we don't
    page in any of the big arrays just to check the version."""
    try:
        import numpy as np
        with np.load(out) as d:
            if "version" in d.files:
                return int(d["version"])
            return 1
    except Exception:
        # Unreadable / corrupt → treat as stale.
        return 0


def _is_stale(seq_name: str) -> bool:
    """True if the cached .npz needs rebuilding. Causes:
    1. Missing (no .npz at all)
    2. Older than the source frame_0000.ply
    3. Schema version not equal to CURRENT_SCHEMA

    Note: we use `!=` rather than `<` so a future version downgrade
    (e.g. building this script from an older checkout against a newer
    cache) correctly flags rebuild rather than silently accepting
    forward-incompatible data."""
    out = CACHE / f"{seq_name}.npz"
    src = LIB / seq_name / "frames" / "frame_0000.ply"
    if not src.is_file():
        return False  # not a valid sequence; skip
    if not out.is_file():
        return True
    if out.stat().st_mtime < src.stat().st_mtime:
        return True
    if _cached_schema(out) != CURRENT_SCHEMA:
        return True
    return False


def _convert_one(seq_name: str) -> int:
    """Shell out to sequence_to_viser_npz.py with --out targeting the cache dir.
    Returns the subprocess exit code."""
    out_path = CACHE / f"{seq_name}.npz"
    CACHE.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, str(CONVERTER), seq_name, "--out", str(out_path)]
    print(f"[batch] {seq_name} -> {out_path.name}")
    return subprocess.call(cmd)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("sequence", nargs="?",
                    help="A single sequence name. Omit to process all sequences.")
    ap.add_argument("--force", action="store_true",
                    help="Rebuild even if the .npz is newer than the source")
    args = ap.parse_args()

    if not LIB.is_dir():
        print(f"ERROR: library dir not found: {LIB}", file=sys.stderr)
        return 2

    if args.sequence:
        seq_names = [args.sequence]
    else:
        seq_names = sorted(
            p.name for p in LIB.iterdir()
            if p.is_dir() and (p / "frames").is_dir()
        )
    if not seq_names:
        print("[batch] nothing to do — no sequences in library.")
        return 0

    failures = 0
    converted = 0
    skipped = 0
    for name in seq_names:
        if not args.force and not _is_stale(name):
            print(f"[batch] {name}: up-to-date, skip")
            skipped += 1
            continue
        rc = _convert_one(name)
        if rc != 0:
            print(f"[batch] {name}: converter exited {rc}", file=sys.stderr)
            failures += 1
        else:
            converted += 1

    print(f"\n[batch] converted={converted} skipped={skipped} failed={failures}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
