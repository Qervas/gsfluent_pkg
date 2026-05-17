"""Static 3DGS .ply → single-frame viser .npz cell.

Mirror image of `tools/sequence_to_viser_npz.py` for the multi-frame
sequence case, restricted to a single frame: a static model uploaded via
/api/models/upload has no animation, but the Splat-mode viewport still
needs a viser cell to render it. Without one, switching the viewport to
Splat mode shows whatever cell viser happened to load last — which is
wrong (user's model never appears) and silent (no error to debug).

The conversion matches `sequence_to_viser_npz.py`'s v2 schema exactly so
viser_headless doesn't have to special-case static models:

    version: int32(2)
    frames:  (1, N, 3) float32   single frame of positions
    quats:   (1, N, 4) float32   normalized w,x,y,z (one frame)
    scales:  (N, 3)    float32   exp() of log-scales, with fp16 floor clamp
    rgb:     (N, 3)    float32   0.5 + SH_C0 * f_dc, NOT clipped (matches ref)
    opacity: (N, 1)    float32   sigmoid(opacity_raw)

Saved with `np.savez` (NOT `savez_compressed`) so viser can mmap the
file without paying decompression cost on every cell switch.

Best-effort: every caller wraps this in try/except so a cell-gen failure
during upload doesn't fail the upload itself. The user still gets a
working Points-mode preview; Splat mode just won't have a cell for this
model until the user re-uploads or the lazy migration path fires.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from plyfile import PlyData

# Identical to `tools/sequence_to_viser_npz.py` — keep in sync if the
# convention ever changes (it shouldn't; this is the 3DGS SH band-0
# constant from the original Inria paper).
SH_C0 = 0.28209479177387814

# fp16 covariance floor — see the long comment in sequence_to_viser_npz.py.
# viser casts cov to fp16 on GPU upload; values below this floor land in
# subnormal land and clamp to zero → singular cov → view-dependent culling.
# We clamp the linear-space scale (not scale²) so squaring keeps us in
# normal fp16 range.
_FP16_COV_FLOOR_SQRT = np.float32(np.sqrt(6.1e-5))  # ≈ 7.81e-3


def _norm_quats(qw: np.ndarray, qx: np.ndarray, qy: np.ndarray, qz: np.ndarray):
    """Normalize quaternion tuple and force scalar component non-negative.

    Sign flip is mathematically harmless (q and -q encode the same
    rotation) but keeps any future per-frame interpolation continuous —
    matches the reference implementation in sequence_to_viser_npz.py.
    """
    qnorm = np.sqrt(qw * qw + qx * qx + qy * qy + qz * qz)
    qnorm[qnorm == 0] = 1.0
    qw = qw / qnorm
    qx = qx / qnorm
    qy = qy / qnorm
    qz = qz / qnorm
    flip = qw < 0
    qw[flip] = -qw[flip]
    qx[flip] = -qx[flip]
    qy[flip] = -qy[flip]
    qz[flip] = -qz[flip]
    return qw, qx, qy, qz


def build_viser_cell(ply_path: Path, out_npz: Path) -> None:
    """Convert a 3DGS .ply into a single-frame viser .npz cell.

    The .ply is expected to have the full 59-float 3DGS attribute set:
    `x,y,z`, `f_dc_0..2`, `f_rest_0..44`, `opacity`, `scale_0..2`,
    `rot_0..3`. Higher-order SH (`f_rest_*`) is ignored — viser's static
    splat path doesn't use view-dependent color.

    Writes atomically by saving to `<out>.tmp` then renaming, so a partial
    file never appears in viser's cache mid-write.

    Raises on any unrecoverable error (missing fields, malformed ply). The
    upload-side caller is responsible for catching and logging.
    """
    v = PlyData.read(str(ply_path))["vertex"].data

    required = ("x", "y", "z", "f_dc_0", "f_dc_1", "f_dc_2",
                "opacity", "scale_0", "scale_1", "scale_2",
                "rot_0", "rot_1", "rot_2", "rot_3")
    missing = [f for f in required if f not in v.dtype.names]
    if missing:
        raise ValueError(
            f"ply missing required 3DGS fields: {missing}. "
            f"Is this a trained 3DGS point cloud and not a vanilla mesh?"
        )

    n_splats = v.shape[0]

    # Positions: single frame, shape (1, N, 3).
    frames = np.empty((1, n_splats, 3), dtype=np.float32)
    frames[0, :, 0] = v["x"]
    frames[0, :, 1] = v["y"]
    frames[0, :, 2] = v["z"]

    # Quats: normalize, fix sign, replicate once for the single frame.
    qw = np.asarray(v["rot_0"], dtype=np.float32)
    qx = np.asarray(v["rot_1"], dtype=np.float32)
    qy = np.asarray(v["rot_2"], dtype=np.float32)
    qz = np.asarray(v["rot_3"], dtype=np.float32)
    qw, qx, qy, qz = _norm_quats(qw, qx, qy, qz)
    quats = np.empty((1, n_splats, 4), dtype=np.float32)
    quats[0, :, 0] = qw
    quats[0, :, 1] = qx
    quats[0, :, 2] = qy
    quats[0, :, 3] = qz

    # Scales: exp() of the log-space values, then clamp each axis to
    # sqrt(fp16 normal floor) so cov² stays in fp16 normal range on GPU
    # upload (avoids viser's view-dependent culling on thin splats).
    sx = np.exp(np.asarray(v["scale_0"], dtype=np.float32))
    sy = np.exp(np.asarray(v["scale_1"], dtype=np.float32))
    sz = np.exp(np.asarray(v["scale_2"], dtype=np.float32))
    scales = np.stack([sx, sy, sz], axis=1).astype(np.float32)
    np.maximum(scales, _FP16_COV_FLOOR_SQRT, out=scales)

    # RGB: same formula as the sequence converter. Reference does NOT
    # clip into [0,1]; we don't either, to keep round-trip identical.
    rgb = np.stack([
        0.5 + np.asarray(v["f_dc_0"], dtype=np.float32) * SH_C0,
        0.5 + np.asarray(v["f_dc_1"], dtype=np.float32) * SH_C0,
        0.5 + np.asarray(v["f_dc_2"], dtype=np.float32) * SH_C0,
    ], axis=1).astype(np.float32)

    # Opacity: pre-sigmoid in the .ply, post-sigmoid in the cell.
    # Shape is (N, 1) to match the reference exactly.
    op_logit = np.asarray(v["opacity"], dtype=np.float32)
    opacity = (1.0 / (1.0 + np.exp(-op_logit))).reshape(-1, 1).astype(np.float32)

    # Atomic write: stage at <out>.<pid>.tmp.npz then rename. viser's
    # cache scan can fire any time; never let it observe a half-written
    # file. The tmp filename MUST end in `.npz` so `np.savez` doesn't
    # silently auto-append `.npz` and break the subsequent rename (was
    # caught in smoke testing: `foo.npz.tmp` → np.savez wrote
    # `foo.npz.tmp.npz`, and `Path("foo.npz.tmp").replace("foo.npz")`
    # then failed silently because the staged path didn't exist).
    #
    # np.savez (NOT savez_compressed) — viser mmaps these, and zip
    # compression defeats mmap.
    import os
    out_npz.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_npz.with_name(f"{out_npz.stem}.{os.getpid()}.tmp.npz")
    np.savez(
        str(tmp_path),
        version=np.int32(2),
        frames=frames,
        quats=quats,
        scales=scales,
        rgb=rgb,
        opacity=opacity,
    )
    tmp_path.replace(out_npz)
