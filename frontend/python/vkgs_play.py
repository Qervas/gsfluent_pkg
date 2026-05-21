"""Launch vkgs on a gsfluent sequence, applying Z-up -> Y-up rotation
ONLY when the sequence's metadata declares the data is Z-up. vkgs's
world is hardcoded Y-up (floor on the XZ plane, +Y is gravity), so
Z-up data has to be rotated to render upright. Y-up data passes
through unchanged. No bbox guessing — `_meta.json:coord_convention` or
an explicit `--source-up` flag is the only source of truth.

Conversion math: Rx(-pi/2) maps (x, y, z) -> (x, z, -y), and per-splat
quaternions compose with q_axis = (cos(-pi/4), sin(-pi/4), 0, 0). The
converted sequence is dumped into a `<seq>_yup_for_vkgs/` sibling dir
(idempotent — skipped on re-run) and vkgs is launched against that.

Usage:
    python frontend/python/vkgs_play.py <sequence>
    python frontend/python/vkgs_play.py --source-up y <sequence>   # force, ignore meta
    python frontend/python/vkgs_play.py --no-launch <sequence>     # convert only
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
from plyfile import PlyData, PlyElement

REPO = Path(__file__).resolve().parents[1]
LIB = REPO / "work" / "library" / "sequences"
# Path to the vkgs binary. Configure with VKGS_BIN env var, e.g.
#   export VKGS_BIN=/opt/vk_gaussian_splatting/_bin/Release/vk_gaussian_splatting
# Defaults to a sibling checkout next to this repo, but you'll almost
# certainly want to override.
VKGS_BIN = Path(os.environ.get(
    "VKGS_BIN",
    str(REPO.parent / "vk_gaussian_splatting/_bin/Release/vk_gaussian_splatting"),
))


def read_source_up(seq_dir: Path) -> str | None:
    """Return 'y' or 'z' from `_meta.json:coord_convention`, else None.
    Strict — no bbox-based guessing. Caller must handle None."""
    meta_path = seq_dir / "_meta.json"
    if not meta_path.is_file():
        return None
    try:
        meta = json.loads(meta_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    conv = str(meta.get("coord_convention", "")).strip().lower()
    if conv == "z-up":
        return "z"
    if conv == "y-up":
        return "y"
    return None


def _rotate_xyz_zup_to_yup(xyz: np.ndarray) -> np.ndarray:
    """Rx(-pi/2): (x, y, z) -> (x, z, -y). +Z (sky in Z-up) -> +Y (sky in Y-up)."""
    out = np.empty_like(xyz)
    out[:, 0] = xyz[:, 0]
    out[:, 1] = xyz[:, 2]
    out[:, 2] = -xyz[:, 1]
    return out


def _rotate_quats_zup_to_yup(q_wxyz: np.ndarray) -> np.ndarray:
    """Compose each gaussian quat with q_axis = (cos(-pi/4), sin(-pi/4), 0, 0)
    on the left (Hamilton). Input/output (N, 4) in (w, x, y, z) order."""
    c = float(np.cos(-np.pi / 4))
    s = float(np.sin(-np.pi / 4))
    wA, xA, yA, zA = c, s, 0.0, 0.0
    wB, xB, yB, zB = q_wxyz[:, 0], q_wxyz[:, 1], q_wxyz[:, 2], q_wxyz[:, 3]
    new_w = wA * wB - xA * xB - yA * yB - zA * zB
    new_x = wA * xB + xA * wB + yA * zB - zA * yB
    new_y = wA * yB - xA * zB + yA * wB + zA * xB
    new_z = wA * zB + xA * yB - yA * xB + zA * wB
    return np.stack([new_w, new_x, new_y, new_z], axis=1).astype(q_wxyz.dtype)


def _convert_ply_zup_to_yup(src: Path, dst: Path) -> None:
    """Read a frame ply, rotate xyz + quats (+ normals if present), write atomically."""
    pd = PlyData.read(str(src))
    v = pd["vertex"].data
    out = v.copy()
    xyz = np.stack(
        [np.asarray(v["x"], dtype=np.float32),
         np.asarray(v["y"], dtype=np.float32),
         np.asarray(v["z"], dtype=np.float32)],
        axis=1,
    )
    nxyz = _rotate_xyz_zup_to_yup(xyz)
    out["x"], out["y"], out["z"] = nxyz[:, 0], nxyz[:, 1], nxyz[:, 2]
    if all(k in v.dtype.names for k in ("rot_0", "rot_1", "rot_2", "rot_3")):
        q = np.stack(
            [np.asarray(v["rot_0"], dtype=np.float32),
             np.asarray(v["rot_1"], dtype=np.float32),
             np.asarray(v["rot_2"], dtype=np.float32),
             np.asarray(v["rot_3"], dtype=np.float32)],
            axis=1,
        )
        nq = _rotate_quats_zup_to_yup(q)
        out["rot_0"], out["rot_1"], out["rot_2"], out["rot_3"] = nq[:, 0], nq[:, 1], nq[:, 2], nq[:, 3]
    if all(k in v.dtype.names for k in ("nx", "ny", "nz")):
        n = np.stack(
            [np.asarray(v["nx"], dtype=np.float32),
             np.asarray(v["ny"], dtype=np.float32),
             np.asarray(v["nz"], dtype=np.float32)],
            axis=1,
        )
        nn = _rotate_xyz_zup_to_yup(n)
        out["nx"], out["ny"], out["nz"] = nn[:, 0], nn[:, 1], nn[:, 2]
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    PlyData([PlyElement.describe(out, "vertex")], text=False).write(str(tmp))
    tmp.replace(dst)


def convert_sequence(src_frames: Path, dst_frames: Path) -> None:
    """Rotate every frame_*.ply in src into dst. Idempotent per-file (skips
    on existing non-empty files), but does NOT detect partial-mismatch."""
    frames = sorted(p for p in src_frames.iterdir()
                    if p.is_file() and p.name.startswith("frame_") and p.suffix == ".ply")
    dst_frames.mkdir(parents=True, exist_ok=True)
    for i, f in enumerate(frames):
        target = dst_frames / f.name
        if target.exists() and target.stat().st_size > 0:
            continue
        _convert_ply_zup_to_yup(f, target)
        if (i + 1) % 10 == 0 or i + 1 == len(frames):
            print(f"  {i+1}/{len(frames)}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("sequence", help="Name of a sequence in work/library/sequences/")
    ap.add_argument(
        "--source-up", choices=["y", "z"], default=None,
        help="Override the source's up-axis. By default we read it from "
             "_meta.json:coord_convention; this flag wins if both are set.",
    )
    ap.add_argument("--no-launch", action="store_true", help="Convert only, do not spawn vkgs")
    args = ap.parse_args()

    seq_dir = LIB / args.sequence
    src_frames = seq_dir / "frames"
    if not src_frames.is_dir():
        print(f"ERROR: no frames/ dir at {src_frames}", file=sys.stderr)
        return 2

    src_up = args.source_up or read_source_up(seq_dir)
    if src_up is None:
        print(
            f"ERROR: cannot determine source up-axis for '{args.sequence}'.\n"
            f"  No `_meta.json:coord_convention` found and no `--source-up` flag passed.\n"
            f"  Pass `--source-up y` or `--source-up z` explicitly.",
            file=sys.stderr,
        )
        return 2
    print(f"[vkgs_play] sequence={args.sequence}  source up-axis: {src_up}")

    if src_up == "y":
        target_frames = src_frames
    else:
        yup_dir = LIB / f"{args.sequence}_yup_for_vkgs"
        target_frames = yup_dir / "frames"
        src_count = len(list(src_frames.glob("frame_*.ply")))
        dst_count = len(list(target_frames.glob("frame_*.ply"))) if target_frames.is_dir() else 0
        if dst_count == src_count and src_count > 0:
            print(f"[vkgs_play] reusing existing Y-up copy at {target_frames}")
        else:
            print(f"[vkgs_play] converting Z-up -> Y-up into {target_frames}")
            convert_sequence(src_frames, target_frames)

    if args.no_launch:
        print(f"[vkgs_play] done. Frames at: {target_frames}")
        return 0

    if not VKGS_BIN.is_file():
        print(f"ERROR: vkgs binary not found at {VKGS_BIN}", file=sys.stderr)
        return 3

    frame0 = target_frames / "frame_0000.ply"
    env = {**os.environ}
    env.pop("WAYLAND_DISPLAY", None)
    env.setdefault("DISPLAY", ":0")
    cmd = [str(VKGS_BIN), "--inputFile", str(frame0), "--frames_dir", str(target_frames)]
    print(f"[vkgs_play] launching: {' '.join(cmd)}", flush=True)
    return subprocess.call(cmd, env=env)


if __name__ == "__main__":
    sys.exit(main())
