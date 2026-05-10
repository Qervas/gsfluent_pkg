"""One-shot migration: rewrite every frame_*.ply in
work/library/sequences/<name>/ from Y-up to Z-up.

Why this exists:
    The workbench invariant says all stored frame data is Z-up; in
    practice every fused sequence on disk before this script runs is
    Y-up because tools/fuse_to_full_ply.py used to emit Y-up by
    default. With the fuse fix landed, NEW sequences come out Z-up,
    but the existing ones still need a one-time pass to bring them
    into the invariant.

What it does:
    For each <library>/sequences/<name>/:
      1. Skip if `<name>/_zup_migrated` exists (idempotency sentinel).
      2. Skip if `<name>/manifest.json:status == "running"` — a sim
         actively producing frames must not be touched mid-stream.
      3. For every frame_*.ply (sorted by index):
           - Full 3DGS frames (have rot_*, scale_*, f_dc_*) go through
             gsfluent.core.coord_convert.convert_full_3dgs_ply
             (rotates positions, quaternions, normals).
           - xyz-only frames (only x/y/z) go through a small inline
             rewriter that calls rotate_positions_y_up_to_z_up.
         Both writes are atomic (tmp + replace).
      4. Update <name>/_meta.json:
           - `bbox_initial` re-read from the post-rotation frame_0.
           - `migrated_to_zup_at` ISO-8601 UTC timestamp.
      5. Touch the `_zup_migrated` sentinel so re-runs skip.

CLI:
    python tools/migrate_sequences_to_zup.py [--dry-run]
    python tools/migrate_sequences_to_zup.py --root /custom/sequences/dir

Safety:
    - The script rewrites files in place. Before running on production
      data, exercise `_migrate_sequence` on a tmp-dir copy of one
      sequence to confirm the round-trip looks right.
    - `--dry-run` prints what it would do, touches nothing on disk.
    - Idempotent: re-running after a successful pass is a no-op.
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

import numpy as np
from plyfile import PlyData, PlyElement

# Make `gsfluent` importable when the script runs from a checkout.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "server") not in sys.path:
    sys.path.insert(0, str(_ROOT / "server"))

from gsfluent.core.coord_convert import (  # noqa: E402
    convert_full_3dgs_ply,
    rotate_positions_y_up_to_z_up,
)


_FULL_3DGS_KEYS = ("scale_0", "rot_0", "f_dc_0")
_SENTINEL_NAME = "_zup_migrated"


def _is_full_3dgs_frame(ply_path: Path) -> bool:
    """Cheap probe: does this frame carry the full 3DGS attribute set?
    A single key check per category is enough — `convert_full_3dgs_ply`
    already does the per-field guard for the rest."""
    try:
        v = PlyData.read(str(ply_path))["vertex"].data
    except Exception:
        return False
    return all(k in v.dtype.names for k in _FULL_3DGS_KEYS)


def _rewrite_xyz_only_ply(src: Path, dst: Path) -> None:
    """Y-up -> Z-up for an xyz-only frame_*.ply. Atomic via tmp + replace.
    Preserves the source dtype (typically (x,y,z) float32) so downstream
    parsers see the same on-disk format."""
    pd = PlyData.read(str(src))
    v = pd["vertex"].data
    out = v.copy()
    xyz = np.stack(
        [
            np.asarray(v["x"], dtype=np.float32),
            np.asarray(v["y"], dtype=np.float32),
            np.asarray(v["z"], dtype=np.float32),
        ],
        axis=1,
    )
    new_xyz = rotate_positions_y_up_to_z_up(xyz)
    out["x"] = new_xyz[:, 0]
    out["y"] = new_xyz[:, 1]
    out["z"] = new_xyz[:, 2]
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    PlyData([PlyElement.describe(out, "vertex")], text=False).write(str(tmp))
    tmp.replace(dst)


def _read_bbox(ply_path: Path) -> list[list[float]] | None:
    """Compute (min, max) bbox of a frame ply. Returns None on failure
    so `--dry-run` and re-runs degrade gracefully."""
    try:
        v = PlyData.read(str(ply_path))["vertex"].data
    except Exception:
        return None
    if v.shape[0] == 0:
        return None
    x = np.asarray(v["x"], dtype=np.float64)
    y = np.asarray(v["y"], dtype=np.float64)
    z = np.asarray(v["z"], dtype=np.float64)
    return [
        [float(x.min()), float(y.min()), float(z.min())],
        [float(x.max()), float(y.max()), float(z.max())],
    ]


def _now_iso() -> str:
    """ISO-8601 UTC timestamp matching the rest of the codebase
    (see core/library.py:_now_iso)."""
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_running(seq_dir: Path) -> bool:
    """Bail-out for sequences that a sim is currently writing into.
    Honors `manifest.json:status == "running"` (the runner.py contract).
    Absent or unreadable manifest -> assume not running (fail-open)."""
    mf = seq_dir / "manifest.json"
    if not mf.is_file():
        return False
    try:
        data = json.loads(mf.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    return str(data.get("status", "")).lower() == "running"


def _migrate_sequence(seq_dir: Path, *, dry_run: bool = False) -> tuple[str, str]:
    """Migrate one sequence dir. Returns (action, detail).

    Possible actions:
      "migrated"           — frames rewritten, sentinel + meta updated
      "skipped-sentinel"   — already migrated (idempotent re-run)
      "skipped-running"    — sim still writing; left alone
      "skipped-no-frames"  — no frame_*.ply found
      "failed"             — exception during the pass
    """
    if (seq_dir / _SENTINEL_NAME).exists():
        return ("skipped-sentinel", "")
    if _is_running(seq_dir):
        return ("skipped-running", "manifest.json:status=running")

    frames_dir = seq_dir / "frames"
    if not frames_dir.exists():
        return ("skipped-no-frames", "frames/ missing")

    # frame_*.ply, sorted by integer index for stable progress output.
    frames = sorted(
        (p for p in frames_dir.iterdir()
         if p.is_file() and p.name.startswith("frame_") and p.suffix == ".ply"),
        key=lambda p: p.name,
    )
    if not frames:
        return ("skipped-no-frames", "no frame_*.ply files")

    if dry_run:
        n_full = sum(1 for f in frames if _is_full_3dgs_frame(f))
        n_xyz = len(frames) - n_full
        return ("migrated", f"would rewrite {len(frames)} frames "
                            f"({n_full} full + {n_xyz} xyz-only)")

    # Real run: per-frame rewrite. Per-file failure aborts the sequence
    # (do NOT touch the sentinel) so a re-run resumes from scratch
    # rather than producing a half-rotated mix.
    n_full = 0
    n_xyz = 0
    try:
        for f in frames:
            if _is_full_3dgs_frame(f):
                convert_full_3dgs_ply(f, f)
                n_full += 1
            else:
                _rewrite_xyz_only_ply(f, f)
                n_xyz += 1
    except Exception as e:
        return ("failed", f"during {f.name}: {e}")

    # Refresh meta from the post-rotation frame 0.
    meta_path = seq_dir / "_meta.json"
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text())
            new_bbox = _read_bbox(frames[0])
            if new_bbox is not None:
                meta["bbox_initial"] = new_bbox
            meta["migrated_to_zup_at"] = _now_iso()
            meta_path.write_text(json.dumps(meta, indent=2))
        except (OSError, json.JSONDecodeError) as e:
            # Don't fail the whole migration on a meta glitch — frames
            # are already rotated. The sentinel still lands so the next
            # run skips this sequence.
            print(f"  WARN: could not update _meta.json: {e}", file=sys.stderr)

    # Drop the idempotency sentinel last so a partial run can resume.
    (seq_dir / _SENTINEL_NAME).write_text(_now_iso() + "\n")

    return ("migrated", f"rewrote {len(frames)} frames "
                        f"({n_full} full + {n_xyz} xyz-only)")


def _default_root() -> Path:
    """Default migration root: `<repo>/work/library/sequences`. Mirrors
    core.library.SEQUENCES_DIR but resolved without importing the full
    server package (we only need coord_convert)."""
    return _ROOT / "work" / "library" / "sequences"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument(
        "--root", type=Path, default=_default_root(),
        help="sequences root to walk (default: <repo>/work/library/sequences)",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="print what would change; touch no files on disk",
    )
    args = p.parse_args(argv)

    root: Path = args.root
    if not root.is_dir():
        print(f"ERROR: sequences root not found: {root}", file=sys.stderr)
        return 2

    n_migrated = 0
    n_skipped = 0
    n_failed = 0

    print(f"[migrate-zup] root={root}  dry_run={args.dry_run}")
    seqs = sorted(p for p in root.iterdir() if p.is_dir())
    for seq in seqs:
        action, detail = _migrate_sequence(seq, dry_run=args.dry_run)
        if action == "migrated":
            n_migrated += 1
            print(f"  [OK]   {seq.name}: {detail}")
        elif action.startswith("skipped"):
            n_skipped += 1
            tag = action.split("-", 1)[1] if "-" in action else "skipped"
            print(f"  [skip] {seq.name}: {tag}{(' — ' + detail) if detail else ''}")
        else:  # failed
            n_failed += 1
            print(f"  [FAIL] {seq.name}: {detail}", file=sys.stderr)

    print(f"[migrate-zup] migrated={n_migrated} "
          f"skipped(already)={n_skipped} failed={n_failed}")
    return 0 if n_failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
