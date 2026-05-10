"""Read fused frame plys + emit binary xyz blobs over WebSocket.

Each frame_*.ply file is a full 3DGS reconstruction at one timestep:
- xyz positions (animate per-frame)
- per-point covariances (constant across frames; sent once)
- per-point RGB (constant; sent once via SH band-0 reconstruction)
- per-point opacity (constant; sent once via sigmoid)

Coordinate convention: all stored frames are Z-up at rest (workbench
invariant — see `core/coord_convert.py` for the import-time rotation
and `tools/fuse_to_full_ply.py` for the sim-time rotation). The
display pipeline therefore reads positions and quaternions through
without any further rotation; the React Three Fiber scene is also
Z-up, so the bytes that go on the wire match the bytes on disk.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from plyfile import PlyData

# 0th-order spherical harmonic coefficient for diffuse-color reconstruction.
_SH_C0 = 0.28209479177387814


def parse_frame_xyz(ply_path: Path) -> np.ndarray:
    """Returns (n, 3) float32 xyz, straight from disk.

    Stored data is Z-up (workbench invariant), so no display-time
    rotation is applied. Allocation-light: a single np.stack."""
    v = PlyData.read(str(ply_path))["vertex"].data
    return np.stack([v["x"], v["y"], v["z"]], axis=1).astype(np.float32)


def parse_static_attrs(ply_path: Path) -> dict | None:
    """Read the per-point attrs that are constant across frames.

    Returns a dict { R: (n,3,3) float32, scales: (n,3) float32,
    rgb: (n,3) float32 in [0,1], opacity: (n,) float32, n: int }
    or None if the ply doesn't carry the full 3DGS attribute set.

    R is the per-gaussian rotation matrix derived from the stored
    quaternion. Stored data is Z-up at rest, so no extra basis
    rotation is composed in here."""
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
    rgb = np.clip(
        np.stack([v["f_dc_0"], v["f_dc_1"], v["f_dc_2"]], axis=1) * _SH_C0 + 0.5,
        0, 1
    ).astype(np.float32)
    op = (1.0 / (1.0 + np.exp(-v["opacity"].astype(np.float32)))).astype(np.float32)
    return {"R": R, "scales": scales, "rgb": rgb, "opacity": op, "n": n}
