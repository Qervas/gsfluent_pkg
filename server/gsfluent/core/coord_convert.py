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

from collections.abc import Callable
from pathlib import Path

import numpy as np


def _axis_rotation_matrix(axis: str, degrees: float) -> np.ndarray:
    rad = np.deg2rad(degrees)
    c = float(np.cos(rad))
    s = float(np.sin(rad))
    if axis == "x":
        return np.array([[1, 0, 0], [0, c, -s], [0, s, c]], dtype=np.float64)
    if axis == "y":
        return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]], dtype=np.float64)
    if axis == "z":
        return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], dtype=np.float64)
    raise ValueError(f"unknown rotation axis {axis!r}")


def _axis_quaternion(axis: str, degrees: float) -> tuple[float, float, float, float]:
    rad = np.deg2rad(degrees)
    c = float(np.cos(rad / 2.0))
    s = float(np.sin(rad / 2.0))
    if axis == "x":
        return c, s, 0.0, 0.0
    if axis == "y":
        return c, 0.0, s, 0.0
    if axis == "z":
        return c, 0.0, 0.0, s
    raise ValueError(f"unknown rotation axis {axis!r}")


def rotate_positions_axis(xyz: np.ndarray, axis: str, degrees: float) -> np.ndarray:
    """Apply a right-handed axis rotation to an (N, 3) positions array."""
    r = _axis_rotation_matrix(axis, degrees)
    return (xyz.astype(np.float64) @ r.T).astype(xyz.dtype, copy=False)


def rotate_quaternions_axis(
    quats_wxyz: np.ndarray, axis: str, degrees: float,
) -> np.ndarray:
    """Compose each (w, x, y, z) quaternion with an axis rotation."""
    wA, xA, yA, zA = _axis_quaternion(axis, degrees)
    wB = quats_wxyz[:, 0]
    xB = quats_wxyz[:, 1]
    yB = quats_wxyz[:, 2]
    zB = quats_wxyz[:, 3]
    new_w = wA * wB - xA * xB - yA * yB - zA * zB
    new_x = wA * xB + xA * wB + yA * zB - zA * yB
    new_y = wA * yB - xA * zB + yA * wB + zA * xB
    new_z = wA * zB + xA * yB - yA * xB + zA * wB
    return np.stack([new_w, new_x, new_y, new_z], axis=1).astype(quats_wxyz.dtype)


def _pos(axis: str, degrees: float) -> Callable[[np.ndarray], np.ndarray]:
    return lambda xyz: rotate_positions_axis(xyz, axis, degrees)


def _quat(axis: str, degrees: float) -> Callable[[np.ndarray], np.ndarray]:
    return lambda q: rotate_quaternions_axis(q, axis, degrees)


def rotate_positions_y_up_to_z_up(xyz: np.ndarray) -> np.ndarray:
    """Apply Rx(+pi/2) to an (N, 3) positions array. Returns a NEW array.

    Math: (x, y, z) -> (x, -z, y). dtype preserved.
    +Y (sky in Y-up) -> +Z (sky in Z-up).
    """
    return rotate_positions_axis(xyz, "x", 90)


def rotate_quaternions_y_up_to_z_up(quats_wxyz: np.ndarray) -> np.ndarray:
    """Compose each quaternion with the Rx(+pi/2) axis rotation.

    Input/output are (N, 4) arrays in (w, x, y, z) order -- matching the
    3DGS .ply convention (rot_0=w, rot_1=x, rot_2=y, rot_3=z).

    Hamilton product: q_new = q_axis * q_old, where
      q_axis = (cos(+pi/4), sin(+pi/4)*1, 0, 0) = (sqrt(2)/2, sqrt(2)/2, 0, 0)
    """
    return rotate_quaternions_axis(quats_wxyz, "x", 90)


def rotate_normals_y_up_to_z_up(nxyz: np.ndarray) -> np.ndarray:
    """Apply Rx(+pi/2) to (N, 3) normals -- same math as positions.
    3DGS plys carry zero normals in practice; defensive but cheap."""
    return rotate_positions_y_up_to_z_up(nxyz)


def flip_180_positions(xyz: np.ndarray) -> np.ndarray:
    """Rx(pi) — 180 deg about X: (x, y, z) -> (x, -y, -z). Returns a NEW array.

    Use to right an upside-down model (top/bottom + front/back swap), e.g.
    after a Y-up -> Z-up convert landed the model head-down."""
    return rotate_positions_axis(xyz, "x", 180)


def flip_180_quaternions(quats_wxyz: np.ndarray) -> np.ndarray:
    """Compose each (w, x, y, z) quaternion with Rx(pi).

    q_new = q_axis * q_old, q_axis = (cos(pi/2), sin(pi/2), 0, 0) = (0, 1, 0, 0).
    Reduces to: (w, x, y, z) -> (-x, w, -z, y)."""
    return rotate_quaternions_axis(quats_wxyz, "x", 180)


# Named orientation transforms the reorient API exposes. Each pairs a
# position/normal rotation with its matching quaternion composition — keep
# the two in lockstep, or every splat ends up tilted relative to its center.
_TRANSFORMS = {
    "rotate_x_pos_90": (_pos("x", 90), _quat("x", 90)),
    "rotate_x_neg_90": (_pos("x", -90), _quat("x", -90)),
    "rotate_y_pos_90": (_pos("y", 90), _quat("y", 90)),
    "rotate_y_neg_90": (_pos("y", -90), _quat("y", -90)),
    "rotate_z_pos_90": (_pos("z", 90), _quat("z", 90)),
    "rotate_z_neg_90": (_pos("z", -90), _quat("z", -90)),
    "rotate_x_180": (_pos("x", 180), _quat("x", 180)),
    "rotate_y_180": (_pos("y", 180), _quat("y", 180)),
    "rotate_z_180": (_pos("z", 180), _quat("z", 180)),
    # Back-compatible aliases used by the import path and older frontend.
    "y_up_to_z_up": (rotate_positions_y_up_to_z_up, rotate_quaternions_y_up_to_z_up),
    "flip_180": (flip_180_positions, flip_180_quaternions),
}

TRANSFORM_NAMES = tuple(_TRANSFORMS)


def _rewrite_ply(input_path: Path, output_path: Path, pos_fn, quat_fn) -> None:
    """Apply `pos_fn` to positions+normals and `quat_fn` to rot_0..3, passing
    every other field (scales, opacity, SH) through unchanged. Binary
    little-endian, same vertex count, atomic tmp+replace. input==output is OK
    (read fully before write)."""
    from plyfile import PlyData, PlyElement

    pd = PlyData.read(str(input_path))
    v = pd["vertex"].data
    out = v.copy()

    xyz = np.stack(
        [np.asarray(v[k], dtype=np.float32) for k in ("x", "y", "z")], axis=1,
    )
    new_xyz = pos_fn(xyz)
    out["x"], out["y"], out["z"] = new_xyz[:, 0], new_xyz[:, 1], new_xyz[:, 2]

    if all(k in v.dtype.names for k in ("rot_0", "rot_1", "rot_2", "rot_3")):
        q = np.stack(
            [np.asarray(v[k], dtype=np.float32) for k in ("rot_0", "rot_1", "rot_2", "rot_3")],
            axis=1,
        )
        new_q = quat_fn(q)
        out["rot_0"], out["rot_1"] = new_q[:, 0], new_q[:, 1]
        out["rot_2"], out["rot_3"] = new_q[:, 2], new_q[:, 3]

    if all(k in v.dtype.names for k in ("nx", "ny", "nz")):
        n = np.stack(
            [np.asarray(v[k], dtype=np.float32) for k in ("nx", "ny", "nz")], axis=1,
        )
        new_n = pos_fn(n)
        out["nx"], out["ny"], out["nz"] = new_n[:, 0], new_n[:, 1], new_n[:, 2]

    tmp = output_path.with_suffix(output_path.suffix + ".tmp")
    PlyData([PlyElement.describe(out, "vertex")], text=False).write(str(tmp))
    tmp.replace(output_path)


def transform_3dgs_ply(input_path: Path, output_path: Path, transform: str) -> None:
    """Apply a named orientation transform (see TRANSFORM_NAMES) to a full
    3DGS .ply: positions, per-gaussian quaternions, and normals. Raises
    ValueError on an unknown transform."""
    try:
        pos_fn, quat_fn = _TRANSFORMS[transform]
    except KeyError:
        raise ValueError(
            f"unknown transform {transform!r}; expected one of {list(TRANSFORM_NAMES)}"
        ) from None
    _rewrite_ply(input_path, output_path, pos_fn, quat_fn)


def convert_full_3dgs_ply(input_path: Path, output_path: Path) -> None:
    """Read a full 3DGS .ply, rewrite with Y-up -> Z-up applied to positions,
    per-gaussian quaternions (rot_0..3), and normals; all other fields
    (scales, opacity, SH) pass through unchanged. Binary little-endian,
    atomic tmp+replace. Thin alias over `transform_3dgs_ply(..., "y_up_to_z_up")`
    kept for the import path's existing call site."""
    transform_3dgs_ply(input_path, output_path, "y_up_to_z_up")
