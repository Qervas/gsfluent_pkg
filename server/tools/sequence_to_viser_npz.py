"""Pack a fused sequence into the .npz layout viser_headless.py expects.

Schema versions
---------------
v1 (legacy, single static cov):
    frames:  (n_frames, n_splats, 3) float32   per-frame xyz
    cov:     (n_splats, 3, 3)         float32   frame-0 covariance R₀·S²·R₀ᵀ
    rgb:     (n_splats, 3)            float32   diffuse color (SH band 0)
    opacity: (n_splats, 1)            float32   post-sigmoid opacity

v2 (current — emitted when per-frame rotations are available):
    version: 2
    frames:  (n_frames, n_splats, 3) float32   per-frame xyz
    quats:   (n_frames, n_splats, 4) float32   per-frame rotation (w,x,y,z)
    scales:  (n_splats, 3)            float32   static, from frame 0
    rgb:     (n_splats, 3)            float32
    opacity: (n_splats, 1)            float32

v2 lets viser_headless reconstruct Σᵢ = Rᵢ·diag(scales²)·Rᵢᵀ per pushed
frame, so splat ellipsoids rotate with the deformation instead of
smearing. Cost: ~+60% on disk vs v1; <1 ms CPU per push for 683k splats.

We auto-pick the schema:
    - Every frame's ply has rot_0..rot_3 → v2 (per-frame covariance)
    - Only frame 0 has rotation fields    → v1 (back-compat with
                                              --xyz_only_after_first
                                              fuse mode)

Usage:
    python server/tools/sequence_to_viser_npz.py <sequence_name>
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
from plyfile import PlyData

REPO = Path(__file__).resolve().parents[2]
LIB = REPO / "work" / "library" / "sequences"
SH_C0 = 0.28209479177387814


def _quat_to_R(qw, qx, qy, qz):
    """Batched (N,) quaternion components → (N, 3, 3) rotation matrices.
    Assumes input is already unit-normalized."""
    n = qw.shape[0]
    R = np.empty((n, 3, 3), dtype=np.float32)
    R[:, 0, 0] = 1 - 2 * (qy * qy + qz * qz)
    R[:, 0, 1] = 2 * (qx * qy - qz * qw)
    R[:, 0, 2] = 2 * (qx * qz + qy * qw)
    R[:, 1, 0] = 2 * (qx * qy + qz * qw)
    R[:, 1, 1] = 1 - 2 * (qx * qx + qz * qz)
    R[:, 1, 2] = 2 * (qy * qz - qx * qw)
    R[:, 2, 0] = 2 * (qx * qz - qy * qw)
    R[:, 2, 1] = 2 * (qy * qz + qx * qw)
    R[:, 2, 2] = 1 - 2 * (qx * qx + qy * qy)
    return R


def _norm_quats(qw, qx, qy, qz):
    """Normalize and fix sign so the scalar component is non-negative.
    The sign flip is harmless mathematically (q and -q encode the same
    rotation) but keeps a continuous trajectory across frames so any
    future interpolation paths don't see spurious 180° jumps."""
    qnorm = np.sqrt(qw * qw + qx * qx + qy * qy + qz * qz)
    qnorm[qnorm == 0] = 1.0
    qw, qx, qy, qz = qw / qnorm, qx / qnorm, qy / qnorm, qz / qnorm
    flip = qw < 0
    qw[flip] = -qw[flip]; qx[flip] = -qx[flip]
    qy[flip] = -qy[flip]; qz[flip] = -qz[flip]
    return qw, qx, qy, qz


def _has_rotation_fields(vertex_data) -> bool:
    return all(f in vertex_data.dtype.names for f in
               ("rot_0", "rot_1", "rot_2", "rot_3"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("sequence", help="Library sequence name")
    ap.add_argument("--out", type=Path, default=None,
                    help="Output .npz path (default: <seq>/viser.npz)")
    ap.add_argument("--force-v1", action="store_true",
                    help="Always emit v1 schema even if per-frame rotations "
                         "are available. Mostly for A/B testing the sharpness fix.")
    args = ap.parse_args()

    seq_dir = LIB / args.sequence
    frames_dir = seq_dir / "frames"
    if not frames_dir.is_dir():
        print(f"ERROR: no frames/ in {seq_dir}", file=sys.stderr)
        return 2

    frame_paths = sorted(p for p in frames_dir.iterdir()
                         if p.is_file() and p.name.startswith("frame_") and p.suffix == ".ply")
    if not frame_paths:
        print(f"ERROR: no frame_*.ply in {frames_dir}", file=sys.stderr)
        return 2

    # Frame 0 carries the static attrs (scales, rgb, opacity).
    print(f"reading frame 0 for static attrs: {frame_paths[0].name}")
    v0 = PlyData.read(str(frame_paths[0]))["vertex"].data
    n_splats = v0.shape[0]
    print(f"  {n_splats:,} splats")

    if not _has_rotation_fields(v0):
        print("ERROR: frame 0 has no rot_0..rot_3 fields. "
              "Was this fused with the splat-attr pipeline?", file=sys.stderr)
        return 2

    # ---- Decide on the schema --------------------------------------------
    # Sample frame 1 (or 0 if it's a 1-frame run) to see if per-frame rotations
    # exist. If yes → v2; if no → v1 (per-frame xyz only, static cov from F0).
    probe_frame = frame_paths[1] if len(frame_paths) > 1 else frame_paths[0]
    v_probe = PlyData.read(str(probe_frame))["vertex"].data
    has_per_frame_rot = _has_rotation_fields(v_probe) and not args.force_v1
    schema = 2 if has_per_frame_rot else 1
    print(f"schema: v{schema}  "
          f"({'per-frame covariance, sharp during motion' if schema == 2 else 'static covariance, splats smear during motion'})")

    # ---- Static: scales, rgb, opacity (from frame 0) ---------------------
    # `scale_*` is logged-scale; viser wants the linear stddev. The cov
    # reconstruction (in v2: per-push) uses scales² so we expose the
    # linear value as the canonical artifact.
    #
    # fp16 covariance floor: viser casts covariance entries to fp16 for
    # GPU upload. fp16's smallest normal value is ~6.1e-5. Any scale²
    # below that lands in subnormal land → clamps to zero on GPU →
    # covariance becomes singular along the affected axis → the splat
    # is effectively a needle whose rasterized projection winks out
    # depending on view angle (the "view-dependent culling" symptom).
    #
    # 3DGS training routinely produces splats with one axis < 1e-3
    # world units (long thin features); on our cluster_6_15 source,
    # 68% of splats have at least one cov-diagonal entry below the
    # fp16 floor. We clamp each axis to sqrt(6.1e-5) ≈ 7.81e-3 so that
    # the squared cov entry stays in fp16 normal range. The visible
    # effect is invisible: 7.81e-3 world units is 0.015% of a 50-unit
    # scene, well below screen-pixel resolution.
    print("extracting scales, rgb, opacity from frame 0…")
    sx = np.exp(np.asarray(v0["scale_0"], dtype=np.float32))
    sy = np.exp(np.asarray(v0["scale_1"], dtype=np.float32))
    sz = np.exp(np.asarray(v0["scale_2"], dtype=np.float32))
    scales = np.stack([sx, sy, sz], axis=1).astype(np.float32)        # (n, 3)
    _FP16_COV_FLOOR_SQRT = np.float32(np.sqrt(6.1e-5))                # ≈ 7.81e-3
    n_clamped = int((scales < _FP16_COV_FLOOR_SQRT).any(axis=1).sum())
    if n_clamped:
        print(f"  clamping {n_clamped} splats with scale^2 below fp16 normal "
              f"({n_clamped/len(scales)*100:.1f}%) — avoids viser fp16-cov culling")
        np.maximum(scales, _FP16_COV_FLOOR_SQRT, out=scales)

    rgb = np.stack([
        0.5 + np.asarray(v0["f_dc_0"], dtype=np.float32) * SH_C0,
        0.5 + np.asarray(v0["f_dc_1"], dtype=np.float32) * SH_C0,
        0.5 + np.asarray(v0["f_dc_2"], dtype=np.float32) * SH_C0,
    ], axis=1).astype(np.float32)
    op_logit = np.asarray(v0["opacity"], dtype=np.float32)
    opacity  = (1.0 / (1.0 + np.exp(-op_logit))).reshape(-1, 1).astype(np.float32)

    # ---- Per-frame: xyz [+ quats if v2] ----------------------------------
    n_frames = len(frame_paths)
    frames = np.empty((n_frames, n_splats, 3), dtype=np.float32)
    quats  = np.empty((n_frames, n_splats, 4), dtype=np.float32) if schema == 2 else None
    print(f"reading {n_frames} frames…")
    t0 = time.time()
    for i, p in enumerate(frame_paths):
        v = PlyData.read(str(p))["vertex"].data
        if v.shape[0] != n_splats:
            print(f"ERROR: {p.name} has {v.shape[0]} splats, expected {n_splats}", file=sys.stderr)
            return 2
        frames[i, :, 0] = v["x"]
        frames[i, :, 1] = v["y"]
        frames[i, :, 2] = v["z"]
        if quats is not None:
            qw = np.asarray(v["rot_0"], dtype=np.float32)
            qx = np.asarray(v["rot_1"], dtype=np.float32)
            qy = np.asarray(v["rot_2"], dtype=np.float32)
            qz = np.asarray(v["rot_3"], dtype=np.float32)
            qw, qx, qy, qz = _norm_quats(qw, qx, qy, qz)
            quats[i, :, 0] = qw
            quats[i, :, 1] = qx
            quats[i, :, 2] = qy
            quats[i, :, 3] = qz
        if (i + 1) % 25 == 0 or i + 1 == n_frames:
            print(f"  {i+1}/{n_frames}", flush=True)
    print(f"frame read: {time.time() - t0:.1f}s")

    # NaN sanitization. The upstream MPM solver occasionally produces
    # NaN positions for particles that escape the bounding box (slip
    # boundary + large substep_dt = numerical blow-up); the K-NN fuse
    # propagates those NaN to whatever ref splats picked them as a
    # neighbor. Downstream, the viser WASM splat sorter dereferences
    # those positions and crashes with "memory access out of bounds",
    # taking the iframe down. Replace any NaN xyz with the prior
    # frame's position so the splat stays put — affected splats freeze
    # in place rather than disappearing or crashing the renderer.
    nan_mask = np.isnan(frames).any(axis=2)  # (n_frames, n_splats)
    if nan_mask.any():
        n_bad = int(nan_mask.sum())
        print(f"  sanitizing {n_bad} NaN positions (forward-fill from prior frame)…")
        # Frame 0 has nothing to fall back to; clamp to scene centroid
        # so the splat doesn't bleed into +inf land. Subsequent frames
        # carry-forward by walking T in order.
        if nan_mask[0].any():
            valid_xyz = frames[0, ~nan_mask[0]]
            centroid = (valid_xyz.mean(axis=0) if len(valid_xyz)
                        else np.zeros(3, dtype=np.float32))
            frames[0, nan_mask[0]] = centroid
        for t in range(1, n_frames):
            bad = nan_mask[t]
            if bad.any():
                frames[t, bad] = frames[t - 1, bad]
        # Quats can NaN under the same conditions; carry-forward too.
        if quats is not None:
            qnan = np.isnan(quats).any(axis=2)
            if qnan.any():
                if qnan[0].any():
                    # Identity quaternion as a safe zero-rotation default.
                    quats[0, qnan[0]] = np.array([1, 0, 0, 0], dtype=np.float32)
                for t in range(1, n_frames):
                    qbad = qnan[t]
                    if qbad.any():
                        quats[t, qbad] = quats[t - 1, qbad]

    out_path = args.out or (seq_dir / "viser.npz")
    print(f"writing {out_path}…")
    # Atomic write: stream to `.npz.tmp` then `os.replace` so a SIGINT or
    # crash mid-write can't leave a corrupt .npz that downstream
    # batch_convert_to_npz._is_stale would treat as fresh and never rebuild.
    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
    if schema == 2:
        np.savez(
            tmp_path,
            version=np.int32(2),
            frames=frames,
            quats=quats,
            scales=scales,
            rgb=rgb,
            opacity=opacity,
        )
    else:
        # v1 — derive static cov from frame 0 the way viser_headless used to
        qw = np.asarray(v0["rot_0"], dtype=np.float32)
        qx = np.asarray(v0["rot_1"], dtype=np.float32)
        qy = np.asarray(v0["rot_2"], dtype=np.float32)
        qz = np.asarray(v0["rot_3"], dtype=np.float32)
        qw, qx, qy, qz = _norm_quats(qw, qx, qy, qz)
        R = _quat_to_R(qw, qx, qy, qz)
        S2 = scales * scales                                # (n, 3)
        R_S2 = R * S2[:, None, :]
        cov = np.einsum("nij,nkj->nik", R_S2, R).astype(np.float32)
        np.savez(
            tmp_path,
            frames=frames,
            cov=cov,
            rgb=rgb,
            opacity=opacity,
        )
    # numpy writes "<tmp_path>.npz" when given a path without that extension;
    # be defensive and rename whichever variant actually landed.
    actual_tmp = tmp_path if tmp_path.exists() else tmp_path.with_suffix(tmp_path.suffix + ".npz")
    actual_tmp.replace(out_path)
    size_mb = out_path.stat().st_size / 1e6
    print(f"done: {out_path.name} v{schema} = {size_mb:.1f} MB"
          f"  ({n_splats:,} splats × {n_frames} frames)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
