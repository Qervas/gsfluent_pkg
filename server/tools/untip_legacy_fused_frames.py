"""Un-tip legacy fused frames produced by the pre-fix KNNKabschFuser.

Before the `source_y_up` gate landed, the fuser unconditionally applied
an Rx(+pi/2) Y-up -> Z-up rotation to positions/normals/quats at the
fuse stage -- which silently tipped every Z-up source onto its side
(see server/gsfluent/core/coord_convert.py + server/gsfluent/core/
fusers/knn_kabsch.py). Existing sequences fused before that fix still
have the spurious rotation baked in.

This tool fixes such sequences IN PLACE so they don't need a re-run:
for each frame_*.ply it applies Rx(-pi/2) to positions + normals and
pre-composes Rx(-pi/2) onto each quaternion. Writes atomically (tmp +
replace). After running, repack the .gsq with:

    python server/tools/pack_splats.py --force <seq_name>

Usage:
    python server/tools/untip_legacy_fused_frames.py <seq_dir> [<seq_dir>...]

Pass FULL paths to the sequence directories (each must contain
`frames/frame_*.ply`). NOT idempotent -- running twice will tip the
sequence the OTHER way. Only run on sequences known to have come from
the pre-fix fuser; newly-fused sequences are already correct and must
not be re-tipped.
"""
from __future__ import annotations
import sys
from pathlib import Path

import numpy as np
from plyfile import PlyData, PlyElement
from scipy.spatial.transform import Rotation as R

# Rx(-pi/2): (x, y, z) -> (x, z, -y). Undoes the fuser's spurious Rx(+pi/2).
R_INV = R.from_euler("x", -90, degrees=True)
R_INV_MAT = R_INV.as_matrix().astype(np.float32)


def untip_one(path: Path) -> None:
    pd = PlyData.read(str(path))
    v = pd["vertex"].data
    out = v.copy()

    # Positions
    xyz = np.stack([v["x"], v["y"], v["z"]], axis=1).astype(np.float32)
    new_xyz = (R_INV_MAT @ xyz.T).T.astype(np.float32)
    out["x"] = new_xyz[:, 0]
    out["y"] = new_xyz[:, 1]
    out["z"] = new_xyz[:, 2]

    # Normals (defensive: usually zero in 3DGS)
    if all(k in v.dtype.names for k in ("nx", "ny", "nz")):
        n = np.stack([v["nx"], v["ny"], v["nz"]], axis=1).astype(np.float32)
        new_n = (R_INV_MAT @ n.T).T.astype(np.float32)
        out["nx"] = new_n[:, 0]
        out["ny"] = new_n[:, 1]
        out["nz"] = new_n[:, 2]

    # Quaternions (rot_0..rot_3 in w,x,y,z order — 3DGS convention)
    if all(k in v.dtype.names for k in ("rot_0", "rot_1", "rot_2", "rot_3")):
        q_wxyz = np.stack(
            [v["rot_0"], v["rot_1"], v["rot_2"], v["rot_3"]], axis=1
        ).astype(np.float64)
        # Normalize defensively — pre-fix fuser writes normalized quats but
        # round-tripping through scipy is happier with strictly unit input.
        norms = np.linalg.norm(q_wxyz, axis=1, keepdims=True)
        q_wxyz = q_wxyz / np.where(norms > 1e-12, norms, 1.0)
        # scipy uses xyzw order.
        q_xyzw = np.concatenate([q_wxyz[:, 1:], q_wxyz[:, :1]], axis=1)
        r_stored = R.from_quat(q_xyzw)
        r_corrected = R_INV * r_stored  # pre-compose Rx(-pi/2)
        q_corr_xyzw = r_corrected.as_quat()
        q_corr_wxyz = np.concatenate(
            [q_corr_xyzw[:, 3:], q_corr_xyzw[:, :3]], axis=1
        ).astype(np.float32)
        out["rot_0"] = q_corr_wxyz[:, 0]
        out["rot_1"] = q_corr_wxyz[:, 1]
        out["rot_2"] = q_corr_wxyz[:, 2]
        out["rot_3"] = q_corr_wxyz[:, 3]

    tmp = path.with_suffix(path.suffix + ".tmp")
    PlyData([PlyElement.describe(out, "vertex")], text=False).write(str(tmp))
    tmp.replace(path)


def untip_seq(seq_dir: Path) -> int:
    frames = sorted((seq_dir / "frames").glob("frame_*.ply"))
    if not frames:
        print(f"[untip] {seq_dir.name}: no frame_*.ply — skip", flush=True)
        return 0
    for i, f in enumerate(frames):
        untip_one(f)
        if (i + 1) % 20 == 0 or i == len(frames) - 1:
            print(f"[untip] {seq_dir.name}: {i+1}/{len(frames)}", flush=True)
    return len(frames)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("usage: untip_fused_frames.py <seq_dir> [<seq_dir>...]")
    total = 0
    for arg in sys.argv[1:]:
        total += untip_seq(Path(arg))
    print(f"[untip] done — {total} frames rewritten", flush=True)
