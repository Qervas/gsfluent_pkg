"""Recover from the over/under-rotated migration of work/library/sequences/.

The Y-up -> Z-up migration we ran applied a rotation that was correct
for COLMAP-style data (+Y down) but WRONG for sim outputs (+Y up). The
agent ran a partial pass before context-out (left no sentinel on done
sequences); the retry then re-rotated those same sequences. Net state:

    Group A (1x rotated, Rx(-pi/2) once):
      bbox: Z largest in [-1, 0], building is inverted (sky at z=0).
    Group B (2x rotated, Rx(-pi/2) twice = Rx(-pi)):
      bbox: Y largest in [-1, 0], building flipped 180 deg around X.

Both groups need a different correction to land at the goal:
    bbox: Z largest in [0, +1], sky at +Z (matches Z-up world).

This script:
  1. Probes each sequence's current state by reading frame_0000.ply bbox.
  2. Applies the correct rotation to every frame_*.ply (positions for
     xyz-only, positions+quats+normals for full 3DGS) and rewrites the
     _meta.json's bbox_initial.
  3. Drops a `_zup_recovered` sentinel so the operation is idempotent.

Skips any sequence already in the goal state (Z largest, Z mostly +ve).
Skips any sequence with status=running in manifest.json.

CLI:
    python tools/recover_zup_migration.py [--dry-run]
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

import numpy as np
from plyfile import PlyData, PlyElement

_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SEQ_DIR = _ROOT / "work" / "library" / "sequences"

_SENTINEL = "_zup_recovered"


def _classify_state(ply_path: Path) -> str:
    """Inspect frame_0000.ply bbox to decide which correction is needed.

    Returns one of: "goal" | "group_a" | "group_b" | "unknown" | "missing".
    """
    if not ply_path.exists():
        return "missing"
    try:
        v = PlyData.read(str(ply_path))["vertex"].data
    except Exception:
        return "unknown"
    x = np.asarray(v["x"], dtype=np.float64)
    y = np.asarray(v["y"], dtype=np.float64)
    z = np.asarray(v["z"], dtype=np.float64)
    ex, ey, ez = x.max() - x.min(), y.max() - y.min(), z.max() - z.min()

    # Goal: Z is largest extent, Z range mostly +ve (max > 0.5)
    if ez >= max(ex, ey) and z.max() > 0.5:
        return "goal"
    # Group A: Z largest but Z is mostly -ve (z.max() near 0)
    if ez >= max(ex, ey) and z.max() < 0.1:
        return "group_a"
    # Group B: Y largest with Y in [-1, 0] (post-Rx(-pi))
    if ey >= max(ex, ez) and y.max() < 0.1:
        return "group_b"
    # Group untouched: Y largest with Y in [0, 1]
    if ey >= max(ex, ez) and y.min() > -0.1:
        return "untouched"
    return "unknown"


def _quat_for_axis_angle(angle_rad: float) -> tuple[float, float, float, float]:
    """Return (w, x, y, z) quaternion for rotation around X axis by angle."""
    return (
        float(np.cos(angle_rad / 2)),
        float(np.sin(angle_rad / 2)),
        0.0,
        0.0,
    )


def _quat_compose(q_left, q_right_wxyz: np.ndarray) -> np.ndarray:
    """Hamilton product q_left * q_right where q_right is (N, 4) wxyz."""
    wA, xA, yA, zA = q_left
    wB = q_right_wxyz[:, 0]
    xB = q_right_wxyz[:, 1]
    yB = q_right_wxyz[:, 2]
    zB = q_right_wxyz[:, 3]
    return np.stack([
        wA * wB - xA * xB - yA * yB - zA * zB,
        wA * xB + xA * wB + yA * zB - zA * yB,
        wA * yB - xA * zB + yA * wB + zA * xB,
        wA * zB + xA * yB - yA * xB + zA * wB,
    ], axis=1).astype(q_right_wxyz.dtype)


def _rotate_pos_group_a(xyz: np.ndarray) -> np.ndarray:
    """Rx(pi): (x, y, z) -> (x, -y, -z). Recovers Group A to goal."""
    out = np.empty_like(xyz)
    out[:, 0] = xyz[:, 0]
    out[:, 1] = -xyz[:, 1]
    out[:, 2] = -xyz[:, 2]
    return out


def _rotate_pos_group_b(xyz: np.ndarray) -> np.ndarray:
    """Rx(-pi/2): (x, y, z) -> (x, z, -y). Recovers Group B to goal."""
    out = np.empty_like(xyz)
    out[:, 0] = xyz[:, 0]
    out[:, 1] = xyz[:, 2]
    out[:, 2] = -xyz[:, 1]
    return out


def _correction_for(state: str):
    """Return (pos_fn, quat_left) for the correction. None if no fix needed."""
    if state == "group_a":
        return _rotate_pos_group_a, _quat_for_axis_angle(np.pi)
    if state == "group_b":
        return _rotate_pos_group_b, _quat_for_axis_angle(-np.pi / 2)
    return None


def _rewrite_full_3dgs(src: Path, pos_fn, quat_left) -> None:
    """Apply correction to a full 3DGS frame in place."""
    pd = PlyData.read(str(src))
    v = pd["vertex"].data
    out = v.copy()
    xyz = np.stack([
        np.asarray(v["x"], dtype=np.float32),
        np.asarray(v["y"], dtype=np.float32),
        np.asarray(v["z"], dtype=np.float32),
    ], axis=1)
    new_xyz = pos_fn(xyz)
    out["x"] = new_xyz[:, 0]
    out["y"] = new_xyz[:, 1]
    out["z"] = new_xyz[:, 2]
    if all(k in v.dtype.names for k in ("rot_0", "rot_1", "rot_2", "rot_3")):
        q = np.stack([
            np.asarray(v["rot_0"], dtype=np.float32),
            np.asarray(v["rot_1"], dtype=np.float32),
            np.asarray(v["rot_2"], dtype=np.float32),
            np.asarray(v["rot_3"], dtype=np.float32),
        ], axis=1)
        new_q = _quat_compose(quat_left, q)
        out["rot_0"] = new_q[:, 0]
        out["rot_1"] = new_q[:, 1]
        out["rot_2"] = new_q[:, 2]
        out["rot_3"] = new_q[:, 3]
    if all(k in v.dtype.names for k in ("nx", "ny", "nz")):
        n = np.stack([
            np.asarray(v["nx"], dtype=np.float32),
            np.asarray(v["ny"], dtype=np.float32),
            np.asarray(v["nz"], dtype=np.float32),
        ], axis=1)
        new_n = pos_fn(n)
        out["nx"] = new_n[:, 0]
        out["ny"] = new_n[:, 1]
        out["nz"] = new_n[:, 2]
    tmp = src.with_suffix(src.suffix + ".tmp")
    PlyData([PlyElement.describe(out, "vertex")], text=False).write(str(tmp))
    tmp.replace(src)


def _rewrite_xyz_only(src: Path, pos_fn) -> None:
    """Apply correction to an xyz-only frame in place."""
    pd = PlyData.read(str(src))
    v = pd["vertex"].data
    out = v.copy()
    xyz = np.stack([
        np.asarray(v["x"], dtype=np.float32),
        np.asarray(v["y"], dtype=np.float32),
        np.asarray(v["z"], dtype=np.float32),
    ], axis=1)
    new_xyz = pos_fn(xyz)
    out["x"] = new_xyz[:, 0]
    out["y"] = new_xyz[:, 1]
    out["z"] = new_xyz[:, 2]
    tmp = src.with_suffix(src.suffix + ".tmp")
    PlyData([PlyElement.describe(out, "vertex")], text=False).write(str(tmp))
    tmp.replace(src)


def _is_full_3dgs(ply: Path) -> bool:
    try:
        v = PlyData.read(str(ply))["vertex"].data
    except Exception:
        return False
    return all(k in v.dtype.names for k in ("scale_0", "rot_0", "f_dc_0"))


def _is_running(seq_dir: Path) -> bool:
    mf = seq_dir / "manifest.json"
    if not mf.is_file():
        return False
    try:
        return json.loads(mf.read_text()).get("status", "").lower() == "running"
    except (OSError, json.JSONDecodeError):
        return False


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_bbox(ply: Path):
    try:
        v = PlyData.read(str(ply))["vertex"].data
    except Exception:
        return None
    x = np.asarray(v["x"], dtype=np.float64)
    y = np.asarray(v["y"], dtype=np.float64)
    z = np.asarray(v["z"], dtype=np.float64)
    return [
        [float(x.min()), float(y.min()), float(z.min())],
        [float(x.max()), float(y.max()), float(z.max())],
    ]


def _recover_one(seq_dir: Path, dry_run: bool) -> tuple[str, str]:
    if (seq_dir / _SENTINEL).exists():
        return ("skipped-sentinel", "")
    if _is_running(seq_dir):
        return ("skipped-running", "manifest.status=running")
    frames_dir = seq_dir / "frames"
    if not frames_dir.exists():
        return ("skipped-no-frames", "frames/ missing")
    f0 = frames_dir / "frame_0000.ply"
    state = _classify_state(f0)
    if state in ("goal", "untouched"):
        # Already at goal; no correction needed. Mark sentinel.
        if not dry_run:
            (seq_dir / _SENTINEL).write_text(f"{_now_iso()} state={state}\n")
        return ("skipped-already-good", state)
    correction = _correction_for(state)
    if correction is None:
        return ("failed", f"unknown state: {state}")
    pos_fn, quat_left = correction
    frames = sorted(
        (p for p in frames_dir.iterdir()
         if p.is_file() and p.name.startswith("frame_") and p.suffix == ".ply"),
        key=lambda p: p.name,
    )
    if dry_run:
        return ("would-recover", f"state={state} frames={len(frames)}")
    n_full = n_xyz = 0
    try:
        for f in frames:
            if _is_full_3dgs(f):
                _rewrite_full_3dgs(f, pos_fn, quat_left)
                n_full += 1
            else:
                _rewrite_xyz_only(f, pos_fn)
                n_xyz += 1
    except Exception as e:
        return ("failed", f"during {f.name}: {e}")
    # Refresh meta + sentinel
    meta_path = seq_dir / "_meta.json"
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text())
            new_bbox = _read_bbox(frames[0])
            if new_bbox is not None:
                meta["bbox_initial"] = new_bbox
            meta["recovered_to_zup_at"] = _now_iso()
            meta_path.write_text(json.dumps(meta, indent=2))
        except (OSError, json.JSONDecodeError):
            pass
    (seq_dir / _SENTINEL).write_text(f"{_now_iso()} from={state}\n")
    return ("recovered", f"from={state} {n_full} full + {n_xyz} xyz-only")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("--root", type=Path, default=DEFAULT_SEQ_DIR)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    if not args.root.is_dir():
        print(f"ERROR: not a dir: {args.root}", file=sys.stderr)
        return 2
    n_recovered = n_skipped = n_failed = 0
    print(f"[recover-zup] root={args.root} dry_run={args.dry_run}")
    for seq in sorted(p for p in args.root.iterdir() if p.is_dir()):
        action, detail = _recover_one(seq, args.dry_run)
        if action.startswith("recovered") or action == "would-recover":
            n_recovered += 1
            print(f"  [OK]   {seq.name}: {detail}")
        elif action.startswith("skipped"):
            n_skipped += 1
            print(f"  [skip] {seq.name}: {action.split('-', 1)[1]}{(': ' + detail) if detail else ''}")
        else:
            n_failed += 1
            print(f"  [FAIL] {seq.name}: {detail}", file=sys.stderr)
    print(f"[recover-zup] recovered={n_recovered} skipped={n_skipped} failed={n_failed}")
    return 0 if n_failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
