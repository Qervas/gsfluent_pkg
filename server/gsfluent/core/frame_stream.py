"""Read fused frame plys + emit binary xyz blobs over WebSocket.

Each frame_*.ply file is a full 3DGS reconstruction at one timestep:
- xyz positions (animate per-frame)
- per-point covariances (constant across frames; sent once)
- per-point RGB (constant; sent once via SH band-0 reconstruction)
- per-point opacity (constant; sent once via sigmoid)

The fuse pipeline writes frames in y-up convention (vkgs origin); we
rotate them to z-up to match the React Three Fiber scene which uses
the same convention as Blender (XY plane, Z up).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from plyfile import PlyData

# 0th-order spherical harmonic coefficient for diffuse-color reconstruction.
_SH_C0 = 0.28209479177387814

# 3x3 matrix that rotates y-up vectors (vkgs convention) to z-up (R3F scene).
_M_YUP_TO_ZUP = np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0]], dtype=np.float32)


def parse_frame_xyz(ply_path: Path) -> np.ndarray:
    """Returns (n, 3) float32 xyz, rotated y-up → z-up. Allocation-free
    enough for streaming use (single np.stack)."""
    v = PlyData.read(str(ply_path))["vertex"].data
    xyz = np.stack([v["x"], v["y"], v["z"]], axis=1).astype(np.float32)
    return np.stack([xyz[:, 0], -xyz[:, 2], xyz[:, 1]], axis=1)


def parse_static_attrs(ply_path: Path) -> dict | None:
    """Read the per-point attrs that are constant across frames.

    Returns a dict { R: (n,3,3) float32, scales: (n,3) float32,
    rgb: (n,3) float32 in [0,1], opacity: (n,) float32, n: int }
    or None if the ply doesn't carry the full 3DGS attribute set."""
    v = PlyData.read(str(ply_path))["vertex"].data
    needed = (
        "scale_0", "scale_1", "scale_2",
        "rot_0", "rot_1", "rot_2", "rot_3",
        "f_dc_0", "f_dc_1", "f_dc_2", "opacity",
    )
    if not all(k in v.dtype.names for k in needed):
        return None
    n = v.shape[0]
    scales = np.exp(np.stack([v["scale_0"], v["scale_1"], v["scale_2"]], axis=1)).astype(np.float32)
    quats = np.stack([v["rot_0"], v["rot_1"], v["rot_2"], v["rot_3"]], axis=1).astype(np.float32)
    norms = np.linalg.norm(quats, axis=1, keepdims=True)
    # Replace zero, NaN, and inf norms with 1.0 so the divide is safe and
    # produces a sensible (identity-like) rotation rather than NaN.
    bad = ~np.isfinite(norms) | (norms == 0)
    norms[bad] = 1.0
    # Also zero-out any NaN/inf inside the input quat itself before the
    # divide, so we don't propagate NaN through the matrix build.
    quats = np.nan_to_num(quats, nan=0.0, posinf=0.0, neginf=0.0)
    quats /= norms
    qw, qx, qy, qz = quats.T
    R = np.empty((n, 3, 3), dtype=np.float32)
    R[:, 0, 0] = 1 - 2 * (qy * qy + qz * qz);  R[:, 0, 1] = 2 * (qx * qy - qz * qw);  R[:, 0, 2] = 2 * (qx * qz + qy * qw)
    R[:, 1, 0] = 2 * (qx * qy + qz * qw);      R[:, 1, 1] = 1 - 2 * (qx * qx + qz * qz);  R[:, 1, 2] = 2 * (qy * qz - qx * qw)
    R[:, 2, 0] = 2 * (qx * qz - qy * qw);      R[:, 2, 1] = 2 * (qy * qz + qx * qw);      R[:, 2, 2] = 1 - 2 * (qx * qx + qy * qy)
    R = np.einsum("ij,njk->nik", _M_YUP_TO_ZUP, R)
    rgb = np.clip(
        np.stack([v["f_dc_0"], v["f_dc_1"], v["f_dc_2"]], axis=1) * _SH_C0 + 0.5,
        0, 1
    ).astype(np.float32)
    op = (1.0 / (1.0 + np.exp(-v["opacity"].astype(np.float32)))).astype(np.float32)
    return {"R": R, "scales": scales, "rgb": rgb, "opacity": op, "n": n}
