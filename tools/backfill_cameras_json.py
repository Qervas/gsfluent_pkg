"""Backfill missing cameras.json for any model under work/uploads/.

The sim core (utils/camera_view_utils.py:get_camera_view) requires
`<model_dir>/cameras.json` to exist. New uploads auto-generate it via
gsfluent.core.models._ensure_cameras_json. This script does the same
for already-uploaded models that predate that fix.

Usage:
    python tools/backfill_cameras_json.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make `gsfluent` importable when running from the repo root.
THIS = Path(__file__).resolve()
sys.path.insert(0, str(THIS.parent.parent / "server"))

from gsfluent.core.models import _ensure_cameras_json, UPLOADS_DIR


def main() -> int:
    if not UPLOADS_DIR.exists():
        print(f"no uploads dir at {UPLOADS_DIR} — nothing to backfill")
        return 0

    fixed = 0
    skipped = 0
    failed = 0
    for d in sorted(UPLOADS_DIR.iterdir()):
        if not d.is_dir():
            continue
        if (d / "cameras.json").exists():
            skipped += 1
            continue
        pc = d / "point_cloud"
        if not pc.is_dir():
            print(f"  - {d.name}: no point_cloud/ subdir, skipping")
            continue
        iters = []
        for it in pc.glob("iteration_*"):
            if not (it / "point_cloud.ply").is_file():
                continue
            try:
                iters.append((int(it.name.split("_")[1]), it))
            except (IndexError, ValueError):
                continue
        if not iters:
            print(f"  - {d.name}: no iteration_*/point_cloud.ply, skipping")
            continue
        iters.sort()
        ply = iters[-1][1] / "point_cloud.ply"
        try:
            _ensure_cameras_json(d, ply)
            print(f"  + {d.name}: cameras.json written from {ply.relative_to(d)}")
            fixed += 1
        except Exception as e:
            print(f"  ! {d.name}: {type(e).__name__}: {e}")
            failed += 1

    print(f"\nbackfilled={fixed} skipped(already-had)={skipped} failed={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
