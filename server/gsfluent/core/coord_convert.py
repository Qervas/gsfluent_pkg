"""Y-up -> Z-up coordinate conversion for 3DGS data at IMPORT time.

The workbench is Z-up. External datasets that ship Y-up (PhysGaussian
ficus, some Inria 3DGS exports, our sim's native output) get rewritten
on the way IN; the display pipeline never sees Y-up data after that
point. This module is the single place where the math lives.

Convention: "Y-up" here means +Y is the SKY direction (PhysGaussian /
sim convention). The earlier Rx(-pi/2) math in this file matched the
COLMAP convention (+Y down) and produced inverted output for sim data;
that bug got papered over by a follow-up recover_zup_migration.py pass.

Rx(+pi/2) maps:
  positions: (x, y, z) -> (x, -z, y)
  +Y  -> +Z   (sky goes up, as expected)
  +X  -> +X
  +Z  -> -Y   (forward stays in horizontal plane)
"""
from __future__ import annotations

from pathlib import Path

import numpy as np


def rotate_positions_y_up_to_z_up(xyz: np.ndarray) -> np.ndarray:
    """Apply Rx(+pi/2) to an (N, 3) positions array. Returns a NEW array.

    Math: (x, y, z) -> (x, -z, y). dtype preserved.
    +Y (sky in Y-up) -> +Z (sky in Z-up).
    """
    out = np.empty_like(xyz)
    out[:, 0] = xyz[:, 0]
    out[:, 1] = -xyz[:, 2]
    out[:, 2] = xyz[:, 1]
    return out


def rotate_quaternions_y_up_to_z_up(quats_wxyz: np.ndarray) -> np.ndarray:
    """Compose each quaternion with the Rx(+pi/2) axis rotation.

    Input/output are (N, 4) arrays in (w, x, y, z) order -- matching the
    3DGS .ply convention (rot_0=w, rot_1=x, rot_2=y, rot_3=z).

    Hamilton product: q_new = q_axis * q_old, where
      q_axis = (cos(+pi/4), sin(+pi/4)*1, 0, 0) = (sqrt(2)/2, sqrt(2)/2, 0, 0)
    """
    c = float(np.cos(np.pi / 4))
    s = float(np.sin(np.pi / 4))
    wA, xA, yA, zA = c, s, 0.0, 0.0
    wB = quats_wxyz[:, 0]
    xB = quats_wxyz[:, 1]
    yB = quats_wxyz[:, 2]
    zB = quats_wxyz[:, 3]
    new_w = wA * wB - xA * xB - yA * yB - zA * zB
    new_x = wA * xB + xA * wB + yA * zB - zA * yB
    new_y = wA * yB - xA * zB + yA * wB + zA * xB
    new_z = wA * zB + xA * yB - yA * xB + zA * wB
    return np.stack([new_w, new_x, new_y, new_z], axis=1).astype(quats_wxyz.dtype)


def rotate_normals_y_up_to_z_up(nxyz: np.ndarray) -> np.ndarray:
    """Apply Rx(-pi/2) to (N, 3) normals -- same math as positions.
    3DGS plys carry zero normals in practice; defensive but cheap."""
    return rotate_positions_y_up_to_z_up(nxyz)


def convert_full_3dgs_ply(input_path: Path, output_path: Path) -> None:
    """Read a full 3DGS .ply, rewrite with Y-up -> Z-up applied to:
      - x, y, z (positions)
      - rot_0, rot_1, rot_2, rot_3 (per-gaussian rotation quaternion)
      - nx, ny, nz (normals if present)
    All other fields (scales, opacity, SH coefficients f_dc_*, f_rest_*)
    are passed through unchanged.

    The output .ply is binary little-endian, same vertex count, same
    attribute set as input. Atomic write via tmp + replace.
    """
    from plyfile import PlyData, PlyElement

    pd = PlyData.read(str(input_path))
    v = pd["vertex"].data
    out = v.copy()

    # positions
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

    # quaternions if present (rot_0..rot_3 in w,x,y,z order)
    if all(k in v.dtype.names for k in ("rot_0", "rot_1", "rot_2", "rot_3")):
        q = np.stack(
            [
                np.asarray(v["rot_0"], dtype=np.float32),
                np.asarray(v["rot_1"], dtype=np.float32),
                np.asarray(v["rot_2"], dtype=np.float32),
                np.asarray(v["rot_3"], dtype=np.float32),
            ],
            axis=1,
        )
        new_q = rotate_quaternions_y_up_to_z_up(q)
        out["rot_0"] = new_q[:, 0]
        out["rot_1"] = new_q[:, 1]
        out["rot_2"] = new_q[:, 2]
        out["rot_3"] = new_q[:, 3]

    # normals if present (defensive -- usually 0 in 3DGS)
    if all(k in v.dtype.names for k in ("nx", "ny", "nz")):
        n = np.stack(
            [
                np.asarray(v["nx"], dtype=np.float32),
                np.asarray(v["ny"], dtype=np.float32),
                np.asarray(v["nz"], dtype=np.float32),
            ],
            axis=1,
        )
        new_n = rotate_normals_y_up_to_z_up(n)
        out["nx"] = new_n[:, 0]
        out["ny"] = new_n[:, 1]
        out["nz"] = new_n[:, 2]

    tmp = output_path.with_suffix(output_path.suffix + ".tmp")
    PlyData([PlyElement.describe(out, "vertex")], text=False).write(str(tmp))
    tmp.replace(output_path)
