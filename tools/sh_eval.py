"""Spherical harmonics evaluator for 3DGS .ply (degree 3, 16 bases).

Standard 3DGS coefficient layout:
    f_dc_0..2          = l=0 (3 = 1 basis × 3 channels)
    f_rest_0..2        = l=1, m=-1 (per-channel)
    f_rest_3..5        = l=1, m=0
    f_rest_6..8        = l=1, m=1
    f_rest_9..14       = l=2 (5 bases × 3 ch -> stored as f_rest_9..23 in some forks)

Actually the canonical gaussian-splatting layout is INTERLEAVED across bases:
    features_dc shape (N, 1, 3)   -> SH degree 0
    features_rest shape (N, 15, 3) -> SH degrees 1..3

In the .ply, fields are flattened as `f_rest_<i>` for i in [0, 45).
Reading order: features_rest is reshaped from (N, 15, 3) to (N, 45) using
.transpose(1, 2).flatten() in the original ply exporter. That means:
    f_rest_<basis*3 + ch>  for basis in [0,15), ch in [0,3)

So f_rest_0 = R for basis 0; f_rest_1 = G for basis 0; etc.
Verified by inspecting gaussian-splatting/scene/dataset_readers.py.
"""
import numpy as np


# SH basis constants (sqrt(value/pi))
C0 = 0.28209479177387814
C1 = 0.4886025119029199
C2 = (1.0925484305920792, -1.0925484305920792, 0.31539156525252005,
      -1.0925484305920792, 0.5462742152960396)
C3 = (-0.5900435899266435, 2.890611442640554, -0.4570457994644658,
      0.3731763325901154, -0.4570457994644658, 1.445305721320277,
      -0.5900435899266435)


def eval_sh(coeffs: np.ndarray, dirs: np.ndarray, degree: int = 3) -> np.ndarray:
    """Evaluate SH at unit direction(s) dirs.

    Args:
        coeffs: (N, 16, 3) SH coefficients per particle, per basis, per channel.
        dirs: (N, 3) or (3,) unit-length viewing directions. If a single (3,)
              vector is passed, it's broadcast.
        degree: SH degree (max 3).

    Returns:
        (N, 3) RGB values, **before** the +0.5 offset.
    """
    if dirs.ndim == 1:
        dirs = np.broadcast_to(dirs, (coeffs.shape[0], 3))
    x, y, z = dirs[:, 0], dirs[:, 1], dirs[:, 2]

    result = C0 * coeffs[:, 0]
    if degree >= 1:
        result = result + (
            -C1 * y[:, None] * coeffs[:, 1] +
             C1 * z[:, None] * coeffs[:, 2] +
            -C1 * x[:, None] * coeffs[:, 3]
        )
    if degree >= 2:
        xx, yy, zz, xy, yz, xz = x*x, y*y, z*z, x*y, y*z, x*z
        result = result + (
            C2[0] * xy[:, None] * coeffs[:, 4] +
            C2[1] * yz[:, None] * coeffs[:, 5] +
            C2[2] * (2.0 * zz - xx - yy)[:, None] * coeffs[:, 6] +
            C2[3] * xz[:, None] * coeffs[:, 7] +
            C2[4] * (xx - yy)[:, None] * coeffs[:, 8]
        )
    if degree >= 3:
        result = result + (
            C3[0] * (y * (3.0 * xx - yy))[:, None] * coeffs[:, 9] +
            C3[1] * (xy * z)[:, None] * coeffs[:, 10] +
            C3[2] * (y * (4.0 * zz - xx - yy))[:, None] * coeffs[:, 11] +
            C3[3] * (z * (2.0 * zz - 3.0 * xx - 3.0 * yy))[:, None] * coeffs[:, 12] +
            C3[4] * (x * (4.0 * zz - xx - yy))[:, None] * coeffs[:, 13] +
            C3[5] * (z * (xx - yy))[:, None] * coeffs[:, 14] +
            C3[6] * (x * (xx - 3.0 * yy))[:, None] * coeffs[:, 15]
        )
    return result.astype(np.float32)


def assemble_sh_coeffs(v) -> np.ndarray:
    """Build (N, 16, 3) coefficient tensor from a 3DGS .ply structured array.

    The reference gaussian-splatting exporter calls .transpose(1, 2) on
    features_rest of shape (N, 15, 3) before flattening, producing
    **channel-major** layout: f_rest_<c*15 + (basis-1)> stores channel c
    of the (basis-th) higher-order coefficient.
    """
    N = len(v)
    coeffs = np.empty((N, 16, 3), dtype=np.float32)
    coeffs[:, 0, 0] = v["f_dc_0"]
    coeffs[:, 0, 1] = v["f_dc_1"]
    coeffs[:, 0, 2] = v["f_dc_2"]
    for ch in range(3):
        for basis in range(1, 16):
            field = f"f_rest_{ch * 15 + (basis - 1)}"
            if field in v.dtype.names:
                coeffs[:, basis, ch] = v[field]
            else:
                coeffs[:, basis, ch] = 0.0
    return coeffs
