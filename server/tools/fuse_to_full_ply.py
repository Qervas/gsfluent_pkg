"""Fuse per-frame sim xyz with reference 3DGS static attrs.

Input:
    --reference_ply: original 3DGS .ply (xyz, normals, opacity, scale, rot, SH)
    --sim_dir: dir with sim_*.ply (xyz only — output of gs_simulation_building.py)
    --out_dir: where to write full per-frame plys

Output:
    out_dir/frame_NNN.ply (one per sim frame, full 3DGS format vkSplatting can render)

Coordinate convention:
    The fuse output is **Z-up** by default — matching the workbench's
    "all stored data is Z-up" invariant. The simulator emits Y-up
    natively, so this script applies Rx(-pi/2) to positions, per-gaussian
    rotation quaternions, and normals on the way out (math lives in
    `gsfluent.core.coord_convert`). Pass `--no_zup` (alias `--keep_y_up`)
    to opt out and write the sim's native Y-up frame straight through.

Method:
    1. Read reference -> raw_xyz, attrs
    2. Normalize reference xyz: longest axis -> 1.0, center -> (1,1,1)
       (matches what the simulator does in transform2origin)
    3. For each sim frame's xyz, NN-match against normalized reference xyz.
       Build a one-time NN map (frame 0 has positions closest to reference).
    4. Per-frame: write full ply with sim positions + reference attrs[nn_idx],
       scale of attrs adjusted by 1/extent (since sim coords are normalized).
"""
import argparse
import os
import re
import sys
import time
from pathlib import Path
import numpy as np
from plyfile import PlyData, PlyElement
from scipy.spatial import cKDTree

# Make `gsfluent` importable when this script runs from a checkout without
# pip install. Mirrors the pattern in server/tools/migrate_to_library.py.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "server") not in sys.path:
    sys.path.insert(0, str(_ROOT / "server"))

from gsfluent.core.coord_convert import (  # noqa: E402
    rotate_normals_y_up_to_z_up as _rotate_norm,
    rotate_positions_y_up_to_z_up as _rotate_pos,
    rotate_quaternions_y_up_to_z_up as _rotate_quat,
)


_SIM_RE = re.compile(r"sim_(\d+)\.ply$")


def _sim_idx(path):
    m = _SIM_RE.search(str(path))
    return int(m.group(1)) if m else None


def _transform_sim_xyz(sim_xyz, args, *, extent=None, center=None):
    """Map normalized sim positions back to output coord system.

    The sim runs in a normalized [0, 2] cube (built from the reference
    ply's bbox in `main`). Two output coord systems are supported:

    --output_source_scale (default): un-normalize back to source-world
        scale. Per-frame ply positions land at the same world extents
        as the reference ply, which means splat scales (kept at their
        source log values) and positions are consistent. Crucially for
        viser/web rendering: cov² stays above float16 subnormal so the
        WebGL splat shader doesn't drop ~40 % of splats. Pass
        --center_at_origin to translate the bbox center to (0,0,0)
        after un-normalizing.

    Legacy mode (--no-output_source_scale): keep positions in the
        normalized [0, 2] cube (centered to [-1, 1] when
        --center_at_origin). Pairs with the legacy
        log_scale_shift=-log(extent) applied to scales. Use ONLY
        with the diff_gaussian_rasterization (CUDA fp32) renderer
        which doesn't suffer from float16 precision loss; viser
        will mis-render this output.

    The simulator emits Y-up. By default we rotate to Z-up via
    Rx(-pi/2) (delegated to coord_convert). Pass --no_zup /
    --keep_y_up to skip the rotation."""
    sx = sim_xyz[:, 0].astype(np.float32, copy=True)
    sy = sim_xyz[:, 1].astype(np.float32, copy=True)
    sz = sim_xyz[:, 2].astype(np.float32, copy=True)

    if args.output_source_scale:
        # Un-normalize: undo `(x - center) / extent + 1.0` from main().
        # Pull extent/center from kwargs or fall back to the values
        # stashed on args during main().
        ext = extent if extent is not None else getattr(args, "extent", None)
        ctr = center if center is not None else getattr(args, "center", None)
        assert ext is not None and ctr is not None, \
            "output_source_scale needs extent + center (set in main)"
        sx = (sx - 1.0) * ext + ctr[0]
        sy = (sy - 1.0) * ext + ctr[1]
        sz = (sz - 1.0) * ext + ctr[2]
        if args.center_at_origin:
            sx -= ctr[0]; sy -= ctr[1]; sz -= ctr[2]
    else:
        # Legacy normalized output. Kept for parity with the CUDA
        # renderer's expected coord system.
        if args.center_at_origin:
            sx -= 1.0; sy -= 1.0; sz -= 0.5

    stacked = np.stack([sx, sy, sz], axis=1)
    if args.zup:
        return _rotate_pos(stacked)
    return stacked


def _batched_kabsch_rotation(p_rel_0, q_rel_t, weights):
    """Weighted Kabsch over a batch of N point-clouds.

    p_rel_0: (N, K, 3)  reference neighborhood (centered) at t=0
    q_rel_t: (N, K, 3)  same neighborhood at t (centered)
    weights: (N, K)     per-neighbor weights summing to 1 along axis 1

    Returns R: (N, 3, 3) — the proper rotation that best maps p_rel_0 to
    q_rel_t under the weighted sum-of-squares objective.

    Algorithm: H = Σ_k w_k q_k p_k^T = U S V^T → R = U diag(1,1,sign(det(U V^T))) V^T.
    The diag term flips the smallest singular vector when SVD picks a
    reflection (det = -1), preserving det(R) = +1.
    """
    H = np.einsum("nk,nki,nkj->nij", weights, q_rel_t, p_rel_0)             # (N, 3, 3)
    U, _, Vt = np.linalg.svd(H)
    det = np.linalg.det(np.einsum("nij,njk->nik", U, Vt))
    D = np.broadcast_to(np.eye(3, dtype=H.dtype), (H.shape[0], 3, 3)).copy()
    D[:, 2, 2] = np.sign(det)
    return np.einsum("nij,njk,nkl->nil", U, D, Vt)


def _cov6_to_quat_logscale(cov6: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Decompose per-particle covariance into per-frame quaternion + log-scale.

    Input:
      cov6 : (N, 6) — upper-triangular cov entries in order
                       (c00, c01, c02, c11, c12, c22)

    Output:
      quat   : (N, 4)  — rotation as (w, x, y, z), columns of the
                          symmetric-eigvec basis sorted by descending eigval,
                          right-handed (det(R)=+1)
      log_s  : (N, 3)  — per-axis log-scales: log(sqrt(eigval)), descending

    For a 3DGS-style covariance Σ = R · diag(s²) · Rᵀ, eigh on Σ gives:
        eigvals = s² (ascending), eigvecs = R columns (ascending).
    We flip both to descending so the largest principal axis lands first.

    Eigvecs from np.linalg.eigh are orthonormal but the orientation
    (right- vs left-handed) is sign-arbitrary. We flip the last column
    when det < 0 to enforce a proper rotation — the quat extraction
    needs that.
    """
    n = cov6.shape[0]
    C = np.empty((n, 3, 3), dtype=cov6.dtype)
    C[:, 0, 0] = cov6[:, 0]
    C[:, 0, 1] = cov6[:, 1]; C[:, 1, 0] = cov6[:, 1]
    C[:, 0, 2] = cov6[:, 2]; C[:, 2, 0] = cov6[:, 2]
    C[:, 1, 1] = cov6[:, 3]
    C[:, 1, 2] = cov6[:, 4]; C[:, 2, 1] = cov6[:, 4]
    C[:, 2, 2] = cov6[:, 5]
    eigvals, eigvecs = np.linalg.eigh(C)
    # eigh returns ascending; reverse so [:, 0] is largest principal axis.
    eigvals = eigvals[:, ::-1]
    eigvecs = eigvecs[:, :, ::-1]
    # Make sure the basis is right-handed (det = +1). When det < 0, flip
    # the sign of the last (smallest) eigvec column — preserves orthonormality
    # and lands in SO(3).
    dets = np.linalg.det(eigvecs)
    flip = (dets < 0).astype(eigvecs.dtype)
    # Multiply last column by (1 - 2*flip) → +1 when det>0, -1 when det<0.
    eigvecs[..., 2] *= (1.0 - 2.0 * flip)[:, None]
    quat = _rotmat_to_quat(eigvecs.astype(np.float32, copy=False))
    # Clamp eigvals to a small positive floor before log+sqrt — covariance
    # diagonals from MPM can dip to ~0 for axes the solver has crushed flat,
    # and log(0) propagates -inf into the 3DGS shader.
    log_s = 0.5 * np.log(np.maximum(eigvals, 1e-12)).astype(np.float32)
    return quat, log_s


def _rotmat_to_quat(R):
    """Batched (N, 3, 3) rotation matrices -> (N, 4) quaternions in (w,x,y,z) order.

    Standard Shepperd / Shoemake method: pick the largest-magnitude
    diagonal-derived component, then back-fill the others. Numerically
    stable for any proper rotation.
    """
    m = R
    t = m[:, 0, 0] + m[:, 1, 1] + m[:, 2, 2]
    out = np.zeros((m.shape[0], 4), dtype=m.dtype)
    # Case A: trace > 0
    mask_a = t > 0
    s = np.sqrt(t[mask_a] + 1.0) * 2.0
    out[mask_a, 0] = 0.25 * s
    out[mask_a, 1] = (m[mask_a, 2, 1] - m[mask_a, 1, 2]) / s
    out[mask_a, 2] = (m[mask_a, 0, 2] - m[mask_a, 2, 0]) / s
    out[mask_a, 3] = (m[mask_a, 1, 0] - m[mask_a, 0, 1]) / s
    # Remaining: pick the biggest diagonal entry to seed
    remaining = ~mask_a
    rem_idx = np.argmax(np.stack([m[:, 0, 0], m[:, 1, 1], m[:, 2, 2]], axis=1), axis=1)
    # Case B: m[0,0] largest
    mb = remaining & (rem_idx == 0)
    s = np.sqrt(1.0 + m[mb, 0, 0] - m[mb, 1, 1] - m[mb, 2, 2]) * 2.0
    out[mb, 0] = (m[mb, 2, 1] - m[mb, 1, 2]) / s
    out[mb, 1] = 0.25 * s
    out[mb, 2] = (m[mb, 0, 1] + m[mb, 1, 0]) / s
    out[mb, 3] = (m[mb, 0, 2] + m[mb, 2, 0]) / s
    # Case C: m[1,1] largest
    mc = remaining & (rem_idx == 1)
    s = np.sqrt(1.0 + m[mc, 1, 1] - m[mc, 0, 0] - m[mc, 2, 2]) * 2.0
    out[mc, 0] = (m[mc, 0, 2] - m[mc, 2, 0]) / s
    out[mc, 1] = (m[mc, 0, 1] + m[mc, 1, 0]) / s
    out[mc, 2] = 0.25 * s
    out[mc, 3] = (m[mc, 1, 2] + m[mc, 2, 1]) / s
    # Case D: m[2,2] largest
    md = remaining & (rem_idx == 2)
    s = np.sqrt(1.0 + m[md, 2, 2] - m[md, 0, 0] - m[md, 1, 1]) * 2.0
    out[md, 0] = (m[md, 1, 0] - m[md, 0, 1]) / s
    out[md, 1] = (m[md, 0, 2] + m[md, 2, 0]) / s
    out[md, 2] = (m[md, 1, 2] + m[md, 2, 1]) / s
    out[md, 3] = 0.25 * s
    return out


def _quat_mul(q1, q2):
    """Hamilton product q1 ⊗ q2, both (N, 4) in (w,x,y,z) order. Returns (N, 4)."""
    w1, x1, y1, z1 = q1[:, 0], q1[:, 1], q1[:, 2], q1[:, 3]
    w2, x2, y2, z2 = q2[:, 0], q2[:, 1], q2[:, 2], q2[:, 3]
    return np.stack([
        w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
        w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
        w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
        w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2,
    ], axis=1)


def _write_frame_atomic(full_attrs, nn_idx, kept_sim_idx, sim_xyz, args, out_path, text_mode):
    """Write a fused frame ply atomically (tmp + rename so vkgs --watch_dir
    polling never sees a partially-written file).

    `full_attrs` is the FULL reference splat array (n_ref splats, with
    scale/rotation/normal permutations already applied and rest positions
    pre-baked). For each frame we copy it and overlay the moving subset's
    positions on the reference indices that the sim particles claimed
    (`nn_idx`). Reference splats outside `sim_area` keep their rest
    positions, so the output ply contains the WHOLE building per frame.

    Frame 0 is always the FULL ply (~161 MB for 683k splats) — needed by
    the workbench splat-mode bootstrap and any external viewers like vkgs
    that read all attrs once. Frames 1+ are xyz-only (~8 MB) when
    `args.xyz_only_after_first` is set: the WS pump only reads x/y/z
    per frame, and the in-browser splat mesh's static attrs come from
    frame 0. 20× disk savings, no quality loss for the workbench path."""
    out = full_attrs.copy()
    sim_kept_xyz = _transform_sim_xyz(sim_xyz[kept_sim_idx], args)
    out["x"][nn_idx] = sim_kept_xyz[:, 0]
    out["y"][nn_idx] = sim_kept_xyz[:, 1]
    out["z"][nn_idx] = sim_kept_xyz[:, 2]
    tmp_path = Path(str(out_path) + ".tmp")
    PlyData([PlyElement.describe(out, "vertex")], text=text_mode).write(tmp_path)
    os.replace(tmp_path, out_path)


def _write_frame_xyz_only(full_attrs, nn_idx, kept_sim_idx, sim_xyz, args, out_path, text_mode):
    """Write an xyz-only ply for frames 1+. ~20× smaller than the full
    ply — only the per-frame-changing positions, no scales/rotations/SH/etc.
    Composed of (n_ref, 3) floats. Compatible with the existing
    parse_frame_xyz parser; parse_static_attrs returns None for these,
    so no spurious static_attrs re-sends."""
    # Same overlay logic as the full writer, but we extract only x/y/z
    # into a minimal (x,y,z) structured array.
    sim_kept_xyz = _transform_sim_xyz(sim_xyz[kept_sim_idx], args)
    n = len(full_attrs)
    out = np.empty(n, dtype=[("x", np.float32), ("y", np.float32), ("z", np.float32)])
    out["x"] = full_attrs["x"]
    out["y"] = full_attrs["y"]
    out["z"] = full_attrs["z"]
    out["x"][nn_idx] = sim_kept_xyz[:, 0]
    out["y"][nn_idx] = sim_kept_xyz[:, 1]
    out["z"][nn_idx] = sim_kept_xyz[:, 2]
    tmp_path = Path(str(out_path) + ".tmp")
    PlyData([PlyElement.describe(out, "vertex")], text=text_mode).write(tmp_path)
    os.replace(tmp_path, out_path)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--reference_ply", required=True)
    p.add_argument("--sim_dir", required=True)
    p.add_argument("--out_dir", required=True)
    p.add_argument("--subsample", type=int, default=None,
                   help="If set, subsample to N gaussians per frame (for performance)")
    # Default: rotate sim's Y-up output to the workbench's Z-up convention
    # (Rx(-pi/2)). Opt out via --no_zup / --keep_y_up if you want raw Y-up.
    p.add_argument("--zup", action="store_true", default=True,
                   help="Rotate sim's native Y-up frame to Z-up via Rx(-pi/2). "
                        "On by default; pass --no_zup to keep Y-up.")
    p.add_argument("--no_zup", "--keep_y_up", dest="zup", action="store_false",
                   help="Skip the Y-up -> Z-up rotation; write sim's native "
                        "Y-up frame straight through (positions, quaternions, "
                        "and normals).")
    p.add_argument("--output_source_scale", action="store_true", default=True,
                   help="(default) Output per-frame plys in source-world coord "
                        "system: positions un-normalized back to reference-ply "
                        "extents, splat log-scales kept at source values. "
                        "Avoids float16-subnormal loss when viser renders cov.")
    p.add_argument("--no-output_source_scale", dest="output_source_scale",
                   action="store_false",
                   help="Legacy: keep positions in the normalized [0, 2] cube "
                        "and shrink scales accordingly. Use only with CUDA "
                        "fp32 renderers (diff_gaussian_rasterization).")
    p.add_argument("--center_at_origin", action="store_true", default=True,
                   help="Translate so building base sits at (0,0,0) instead of "
                        "the simulator's normalized (1,1,1)")
    p.add_argument("--no-center_at_origin", dest="center_at_origin", action="store_false")
    p.add_argument("--watch", action="store_true",
                   help="After processing existing frames, keep polling sim_dir "
                        "for new sim_*.ply and fuse them as they appear. Pairs "
                        "with vkgs --watch_dir for live preview.")
    p.add_argument("--watch_quiet_seconds", type=float, default=300.0,
                   help="Exit watch mode after this many seconds with no new frames "
                        "(default 300s = 5min, which covers Warp+Taichi kernel "
                        "compilation + first-frame latency on cold caches).")
    p.add_argument("--max_frames", type=int, default=0,
                   help="If >0, exit watch mode as soon as this many fused frames "
                        "have been produced. Lets run_sim.sh wrap up immediately "
                        "after the sim's expected frame count is reached, instead "
                        "of waiting for the quiet-seconds timeout.")
    p.add_argument("--xyz_only_after_first", action="store_true", default=False,
                   help="Frame 0 is the full ~161 MB ply with all attrs (used "
                        "by the workbench to bootstrap the splat renderer); "
                        "frames 1+ are xyz-only (~8 MB each, ~20× smaller). "
                        "The WS pump only reads x/y/z per frame so this loses "
                        "no info on the workbench path. Disable if you need "
                        "external viewers (e.g. vkgs) to read every frame's "
                        "full attrs from disk.")
    p.add_argument("--knn", type=int, default=0,
                   help="K-NN skinning. K=0 (default): legacy 1-NN binding "
                        "(only sim-claimed ref splats move; the other 80% "
                        "stay at rest — the 'ghost' problem). K>=1: each ref "
                        "splat is driven by the K nearest sim particles via "
                        "inverse-distance weights, so every ref splat moves "
                        "smoothly with the local sim displacement field. "
                        "K=8 is a sensible default for a continuous field.")
    p.add_argument("--min_opacity", type=float, default=0.0,
                   help="Drop reference splats whose sigmoid-opacity is below "
                        "this threshold BEFORE NN/K-NN binding. Use to remove "
                        "the low-opacity 'ambient noise' splats that produce "
                        "the sparkle/spike artifact in Splats-mode playback. "
                        "0.0 = keep all (default). 0.05–0.1 cuts visible noise "
                        "without making holes. Higher than 0.2 starts removing "
                        "legitimate surface splats.")
    p.add_argument("--knn_rotation", action="store_true", default=False,
                   help="When --knn>0: also compute the local rotation at each "
                        "ref splat per frame via weighted Kabsch (batched 3x3 "
                        "SVD over the K-NN neighborhood), compose with the "
                        "rest-pose quaternion, and write rot_0..rot_3 in every "
                        "frame ply. Fixes the Splats-mode smear by keeping "
                        "each splat aligned with the deforming local surface. "
                        "Requires --no_zup (sim output coord == fuse output "
                        "coord); for --zup we'd need a basis-transform on R.")
    p.add_argument("--ghost_cull_factor", type=float, default=4.0,
                   help="Detect 'ghost' splats whose K-NN partners spread "
                        "more than `factor × median frame-0 neighborhood "
                        "radius` at any frame, and zero their opacity in "
                        "frame 0 (which propagates to all frames via the "
                        "npz cache). Fixes the 'invisible cracked parts' "
                        "artifact where K-NN weighted-average places a "
                        "ref splat halfway between two diverging chunks "
                        "in empty space. 0 disables. Default 4 is "
                        "conservative — only flags genuinely cracked "
                        "regions, not normal deformation. Requires --knn.")
    args = p.parse_args()

    print(f"Loading reference: {args.reference_ply}")
    ref_ply = PlyData.read(args.reference_ply)
    ref_v = ref_ply["vertex"].data
    # Optional low-opacity filter. The reference 3DGS typically contains a
    # long tail of near-transparent "ambient" gaussians used during training
    # as soft fillers. They contribute sparkle/spike artifacts in Splats-mode
    # playback (their orientations are random; once skinned to a moving sim
    # they smear outward as visible noise). Drop them at the source so every
    # downstream array (positions, KD-tree, K-NN map, full_attrs) is built
    # over the cleaner set. Default 0.0 preserves the original behavior.
    if args.min_opacity > 0:
        if "opacity" not in ref_v.dtype.names:
            raise SystemExit("--min_opacity set but reference ply has no `opacity` field")
        op_sig = 1.0 / (1.0 + np.exp(-ref_v["opacity"].astype(np.float32)))
        keep_mask = op_sig >= args.min_opacity
        n_before = len(ref_v)
        n_kept = int(keep_mask.sum())
        if n_kept == 0:
            raise SystemExit(f"--min_opacity {args.min_opacity} dropped ALL splats; lower it")
        ref_v = ref_v[keep_mask]
        print(f"Opacity filter (sigmoid>={args.min_opacity}): "
              f"kept {n_kept:,}/{n_before:,} ({n_kept/n_before*100:.1f}%)")
    ref_xyz_raw = np.stack([ref_v["x"], ref_v["y"], ref_v["z"]], axis=1).astype(np.float32)
    aabb_min = ref_xyz_raw.min(0); aabb_max = ref_xyz_raw.max(0)
    center = (aabb_min + aabb_max) / 2.0
    extent = float((aabb_max - aabb_min).max())
    scale_origin = 1.0 / extent
    ref_xyz_norm = ((ref_xyz_raw - center) / extent + 1.0).astype(np.float32)
    print(f"  ref: {len(ref_xyz_raw)} gaussians, extent {extent:.2f}, scale_origin {scale_origin:.4f}")
    # Stash on args so _transform_sim_xyz (called from the per-frame
    # write helpers) can un-normalize back to source-world coords
    # without us threading these through every helper signature.
    args.extent = extent
    args.center = center

    # Sim plys — in watch mode wait for any sim_*.ply to exist.
    sim_dir = Path(args.sim_dir)
    if args.watch:
        waited = 0.0
        while True:
            existing = sorted(sim_dir.glob("sim_*.ply")) if sim_dir.exists() else []
            existing = [p for p in existing if p.stat().st_size >= 1024]
            if existing:
                break
            if waited == 0.0:
                print(f"[watch] waiting for first sim_*.ply in {sim_dir}...")
            time.sleep(1.0); waited += 1.0
            if waited > args.watch_quiet_seconds:
                print(f"[watch] no first frame after {waited:.0f}s, exiting"); return
    sim_plys = sorted(sim_dir.glob("sim_*.ply"))
    print(f"Found {len(sim_plys)} sim frames")

    # Determine subsample / NN map from frame 0
    print("Building NN map from sim frame 0 -> reference (normalized)...")
    first_data = PlyData.read(str(sim_plys[0]))["vertex"].data
    sim_xyz_t0 = np.stack([first_data["x"], first_data["y"], first_data["z"]], axis=1).astype(np.float32)
    n_sim = len(sim_xyz_t0)

    # Particle_F path detect: sim wrapper writes cov_00..cov_22 per particle
    # alongside xyz when --output_cov is set. When present, the fuse step
    # switches to 1-NN binding (each ref splat rigidly skinned to one sim
    # particle) and inherits the sim's per-frame cov via eigendecomposition.
    # Eliminates the K-NN ghost (weighted-average splats placed in empty
    # space across crack lines) because there's no averaging — each ref
    # splat goes with its single bound sim particle.
    _COV_FIELDS = ("cov_00", "cov_01", "cov_02", "cov_11", "cov_12", "cov_22")
    has_cov = all(f in first_data.dtype.names for f in _COV_FIELDS)
    if has_cov:
        print("  particle_F path: cov fields detected; 1-NN binding will replace K-NN averaging")
        if args.zup:
            raise SystemExit("particle_F (cov-fields in sim ply) currently requires "
                             "--no_zup (the sim's cov is in sim coords; rotating it "
                             "into Z-up space needs a basis transform we haven't "
                             "wired yet). Pass --no_zup or drop --output_cov on the "
                             "sim side.")

    if args.subsample is not None and n_sim > args.subsample:
        rng = np.random.default_rng(0)
        kept_sim_idx = rng.choice(n_sim, size=args.subsample, replace=False)
        kept_sim_idx.sort()
    else:
        kept_sim_idx = np.arange(n_sim)
    print(f"  using {len(kept_sim_idx)} of {n_sim} sim particles per frame")

    # NN: each kept sim particle -> nearest reference gaussian
    tree = cKDTree(ref_xyz_norm)
    _, nn_idx = tree.query(sim_xyz_t0[kept_sim_idx], k=1, workers=-1)
    print(f"  NN map ready ({len(nn_idx)} matches)")

    # Particle_F path: build the rest-time 1-NN map ref→sim and pre-compute
    # the per-splat translation offset (so each ref splat translates rigidly
    # with its bound sim particle, preserving its rest-time position relative
    # to that particle). Skip K-NN entirely — it's the source of the ghost.
    pf_1nn_idx = None
    pf_ref_rest_offset = None
    sim_xyz_t0_kept = sim_xyz_t0[kept_sim_idx]
    if has_cov:
        print(f"Building 1-NN rest map (each ref splat -> nearest sim particle at rest)...")
        sim_tree_pf = cKDTree(sim_xyz_t0_kept)
        pf_dists, pf_1nn_idx = sim_tree_pf.query(ref_xyz_norm, k=1, workers=-1)
        pf_ref_rest_offset = (ref_xyz_norm - sim_xyz_t0_kept[pf_1nn_idx]).astype(np.float32)
        print(f"  1-NN bindings: {len(pf_1nn_idx):,} ref splats, "
              f"median rest distance to bound sim particle: "
              f"{float(np.median(pf_dists)):.4f} (normalized sim units)")

    # K-NN skinning map: for each REF splat, find K nearest SIM particles
    # at frame 0 in normalized sim space. Used per-frame to compute a
    # weighted displacement field over ALL ref splats — eliminates the
    # 1-NN ghost where unclaimed ref splats stay static.
    knn_idx = None
    knn_weights = None
    if not has_cov and args.knn > 0:
        K = int(args.knn)
        print(f"Building K-NN map (K={K}) from each ref splat -> {len(kept_sim_idx)} sim particles...")
        sim_tree = cKDTree(sim_xyz_t0_kept)
        dists, knn_idx = sim_tree.query(ref_xyz_norm, k=K, workers=-1)
        # k=1 returns 1-D arrays; force 2-D for uniform broadcasting.
        if K == 1:
            dists = dists[:, None]
            knn_idx = knn_idx[:, None]
        # Inverse-distance weights, normalized per ref splat. Epsilon
        # prevents div-by-zero on coincident points.
        inv_d = 1.0 / (dists.astype(np.float32) + 1e-6)
        knn_weights = (inv_d / inv_d.sum(axis=1, keepdims=True)).astype(np.float32)
        print(f"  K-NN map: ref→sim shape={knn_idx.shape}, weights summed to 1.0 per row")
        print(f"  median NN distance: {np.median(dists[:, 0]):.4f}  "
              f"max NN distance: {dists[:, 0].max():.4f} (normalized sim units)")

    # Pre-compute the rest-pose neighborhood for K-NN rotation extraction:
    # for each ref splat, the K sim particle positions at frame 0 expressed
    # relative to the weighted centroid. Reused every frame in fuse_one.
    knn_p_rel_0 = None
    knn_rest_quat = None
    if args.knn_rotation:
        if not args.knn or not args.knn > 0:
            raise SystemExit("--knn_rotation requires --knn >= 1")
        if args.zup:
            raise SystemExit("--knn_rotation requires --no_zup (the local "
                             "rotation R is computed in sim space; we'd need a "
                             "basis transform to apply it to a Z-up output)")
        print(f"Building K-NN rotation prerequisites (rest-pose p_rel)...")
        p_indexed = sim_xyz_t0_kept[knn_idx]                                  # (n_ref, K, 3)
        knn_p_centroid_0 = (knn_weights[..., None] * p_indexed).sum(axis=1)   # (n_ref, 3)
        knn_p_rel_0 = (p_indexed - knn_p_centroid_0[:, None, :]).astype(np.float32)
        # Snapshot the rest-pose quaternions BEFORE the Z-up rotation block
        # below tweaks them. (For --no_zup, that block is skipped anyway, so
        # ref_v carries the canonical rest quats already.)
        knn_rest_quat = np.stack([
            ref_v["rot_0"].astype(np.float32),
            ref_v["rot_1"].astype(np.float32),
            ref_v["rot_2"].astype(np.float32),
            ref_v["rot_3"].astype(np.float32),
        ], axis=1)
        # Normalize defensively — Inria 3DGS plys sometimes drift from unit norm.
        norms = np.linalg.norm(knn_rest_quat, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        knn_rest_quat /= norms
        print(f"  rest-pose neighborhoods + quaternions cached: {len(knn_p_rel_0):,} splats")

    # Build the FULL reference attribute array (one row per ref splat).
    # Output frames will start as a copy of this and overlay sim positions
    # only on the matched indices — so the static remainder (splats not
    # claimed by any sim particle) keeps its rest position and the viewer
    # sees the whole building per frame.
    out_dtype = ref_v.dtype
    full_attrs = np.empty(len(ref_v), dtype=out_dtype)
    for field in out_dtype.names:
        full_attrs[field] = ref_v[field]

    # Splat scale handling:
    #   - source-scale output (default): keep log-scales at source values.
    #     Positions are un-normalized in _transform_sim_xyz so the bbox
    #     matches source extents; scales need to match positions in
    #     absolute units so they don't drift into float16-subnormal land
    #     when viser casts cov to fp16 for WS transport.
    #   - legacy normalized output: subtract log(extent) so scales shrink
    #     to match the [-1, 1] cube. Required for the diff_gaussian_
    #     rasterization CUDA renderer which works in normalized coords.
    if args.output_source_scale:
        print(f"  splat log-scales kept at source values (source-scale output)")
    else:
        log_scale_shift = float(np.log(scale_origin))
        print(f"  splat log-scales shifted by log(1/extent) = {log_scale_shift:.4f}  "
              f"(legacy normalized output)")
        for k in ("scale_0", "scale_1", "scale_2"):
            if k in full_attrs.dtype.names:
                full_attrs[k] = full_attrs[k] + log_scale_shift

    # Y-up -> Z-up axis rotation. The sim emits Y-up; the workbench's
    # invariant says all stored data is Z-up. Rx(-pi/2) maps
    # (x, y, z) -> (x, z, -y) and applies to:
    #   - positions (per-frame, in _transform_sim_xyz)
    #   - per-gaussian rotation quaternions (here, once)
    #   - normals (here, once)
    #
    # Math lives in core/coord_convert.py — same Rx(-pi/2) the
    # import-time Y-up -> Z-up converter uses.
    if args.zup:
        # Quaternions (rot_0..rot_3 in w,x,y,z order). Pack into (N, 4),
        # rotate, unpack back into the structured array.
        q = np.stack([
            full_attrs["rot_0"],
            full_attrs["rot_1"],
            full_attrs["rot_2"],
            full_attrs["rot_3"],
        ], axis=1).astype(np.float32)
        new_q = _rotate_quat(q)
        full_attrs["rot_0"] = new_q[:, 0]
        full_attrs["rot_1"] = new_q[:, 1]
        full_attrs["rot_2"] = new_q[:, 2]
        full_attrs["rot_3"] = new_q[:, 3]
        # Permute normals likewise (these are all 0 in 3DGS plys but be defensive)
        if all(k in full_attrs.dtype.names for k in ("nx", "ny", "nz")):
            n = np.stack([
                full_attrs["nx"],
                full_attrs["ny"],
                full_attrs["nz"],
            ], axis=1).astype(np.float32)
            new_n = _rotate_norm(n)
            full_attrs["nx"] = new_n[:, 0]
            full_attrs["ny"] = new_n[:, 1]
            full_attrs["nz"] = new_n[:, 2]
        print(f"  Y-up -> Z-up rotation applied to quaternions + normals")

    # Bake REST positions into full_attrs in the final output coord space.
    # Per-frame fuse will copy full_attrs and overwrite x/y/z only on the
    # ref indices claimed by sim particles; the rest stay at these rest
    # positions. (`ref_xyz_norm` is in normalized sim space — same coord
    # system the sim outputs land in.)
    rest_xyz = _transform_sim_xyz(ref_xyz_norm, args)
    full_attrs["x"] = rest_xyz[:, 0]
    full_attrs["y"] = rest_xyz[:, 1]
    full_attrs["z"] = rest_xyz[:, 2]
    print(f"  rest positions baked: {len(full_attrs)} ref splats in output space")

    # Per-frame: replace x/y/z with sim positions and write
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    text_mode = ref_ply.text
    processed = set()

    def fuse_one(sp, idx):
        v = PlyData.read(str(sp))["vertex"].data
        sim_xyz = np.stack([v["x"], v["y"], v["z"]], axis=1).astype(np.float32)
        out_path = out_dir / f"frame_{idx:04d}.ply"

        if has_cov:
            # Particle_F path. Each ref splat is rigidly bound to its
            # nearest sim particle at rest (pf_1nn_idx); per frame, its
            # position is the bound particle's current xyz plus the rest
            # offset, and its rotation comes from the bound particle's
            # current covariance via eigendecomposition. The 3DGS log-
            # scales are kept at frame-0 source values (the npz schema is
            # v2: per-frame quat + static scales). Eigvals beyond the
            # initial scales² aren't preserved — for true scale stretch
            # we'd need a v3 npz with per-frame scales; revisit if the
            # visuals show truncation.
            sim_kept = sim_xyz[kept_sim_idx]
            sim_cov6 = np.stack(
                [v[f] for f in _COV_FIELDS], axis=1,
            ).astype(np.float32)
            sim_cov6_kept = sim_cov6[kept_sim_idx]

            # Position: bound sim particle's current xyz + rest offset.
            ref_pos_norm = sim_kept[pf_1nn_idx] + pf_ref_rest_offset      # (n_ref, 3)
            out_xyz_world = _transform_sim_xyz(ref_pos_norm, args)

            # Rotation: bound sim particle's current cov → quat via eigh.
            # Sim cov is in normalized [0, 2]³ space. Positions get
            # un-normalized to source-scale in _transform_sim_xyz (when
            # --output_source_scale, default ON), so cov must scale by
            # extent² to stay in the same world frame. Without this, the
            # cov-derived log-scales come out log(extent) ≈ -4 too small
            # and ~100% of splats land below fp16 subnormal → workbench
            # culls them silently.
            cov_world_factor = (
                float(args.extent) ** 2 if args.output_source_scale else 1.0
            )
            ref_cov_t = sim_cov6_kept[pf_1nn_idx] * cov_world_factor       # (n_ref, 6)
            new_quat, _log_s = _cov6_to_quat_logscale(ref_cov_t)

            # Frame-0 only: also write the cov-derived log-scales so the
            # npz cache picks them up as the static scales for the whole
            # cell. Avoids the "splat-shape ghost" where ref splats keep
            # their reference-ply scales while the cov rotates around
            # them — visually a fan of rotating sticks.
            if idx == 0:
                full_attrs["scale_0"] = _log_s[:, 0]
                full_attrs["scale_1"] = _log_s[:, 1]
                full_attrs["scale_2"] = _log_s[:, 2]

            if idx == 0 or not args.xyz_only_after_first:
                out = full_attrs.copy()
                out["x"] = out_xyz_world[:, 0]
                out["y"] = out_xyz_world[:, 1]
                out["z"] = out_xyz_world[:, 2]
                out["rot_0"] = new_quat[:, 0]
                out["rot_1"] = new_quat[:, 1]
                out["rot_2"] = new_quat[:, 2]
                out["rot_3"] = new_quat[:, 3]
                tmp_path = Path(str(out_path) + ".tmp")
                PlyData([PlyElement.describe(out, "vertex")], text=text_mode).write(tmp_path)
                os.replace(tmp_path, out_path)
            else:
                # Compact frame: xyz + per-frame quat. ~24 MB vs ~161 MB.
                fields = [("x", np.float32), ("y", np.float32), ("z", np.float32),
                          ("rot_0", np.float32), ("rot_1", np.float32),
                          ("rot_2", np.float32), ("rot_3", np.float32)]
                out = np.empty(len(full_attrs), dtype=fields)
                out["x"] = out_xyz_world[:, 0]
                out["y"] = out_xyz_world[:, 1]
                out["z"] = out_xyz_world[:, 2]
                out["rot_0"] = new_quat[:, 0]
                out["rot_1"] = new_quat[:, 1]
                out["rot_2"] = new_quat[:, 2]
                out["rot_3"] = new_quat[:, 3]
                tmp_path = Path(str(out_path) + ".tmp")
                PlyData([PlyElement.describe(out, "vertex")], text=text_mode).write(tmp_path)
                os.replace(tmp_path, out_path)
            return out_path

        if knn_idx is not None:
            # K-NN skinning path. Every ref splat displaces by a weighted
            # sum of the displacements of its K nearest sim particles
            # (computed in normalized sim space, then transformed to output).
            sim_kept = sim_xyz[kept_sim_idx]                                  # (n_kept, 3)
            sim_disp = sim_kept - sim_xyz_t0_kept                             # (n_kept, 3)
            neighbors = sim_disp[knn_idx]                                     # (n_ref, K, 3)
            ref_disp = (knn_weights[..., None] * neighbors).sum(axis=1)       # (n_ref, 3)
            ref_xyz_displaced = ref_xyz_norm + ref_disp                       # (n_ref, 3)
            out_xyz_world = _transform_sim_xyz(ref_xyz_displaced, args)       # (n_ref, 3)

            # Optional per-frame rotation update via weighted Kabsch.
            # Only valid for --no_zup (sim and output share a basis).
            new_quat = None
            if knn_p_rel_0 is not None:
                q_indexed = sim_kept[knn_idx]                                 # (n_ref, K, 3)
                q_centroid = (knn_weights[..., None] * q_indexed).sum(axis=1) # (n_ref, 3)
                q_rel_t = (q_indexed - q_centroid[:, None, :]).astype(np.float32)
                R_local = _batched_kabsch_rotation(knn_p_rel_0, q_rel_t, knn_weights)
                q_local = _rotmat_to_quat(R_local)                            # (n_ref, 4)
                new_quat = _quat_mul(q_local, knn_rest_quat)                  # (n_ref, 4)

            # Overwrite ALL splats — no notion of "matched/unmatched" left.
            if idx == 0 or not args.xyz_only_after_first:
                out = full_attrs.copy()
                out["x"] = out_xyz_world[:, 0]
                out["y"] = out_xyz_world[:, 1]
                out["z"] = out_xyz_world[:, 2]
                if new_quat is not None:
                    out["rot_0"] = new_quat[:, 0]
                    out["rot_1"] = new_quat[:, 1]
                    out["rot_2"] = new_quat[:, 2]
                    out["rot_3"] = new_quat[:, 3]
                tmp_path = Path(str(out_path) + ".tmp")
                PlyData([PlyElement.describe(out, "vertex")], text=text_mode).write(tmp_path)
                os.replace(tmp_path, out_path)
            else:
                # Compact frame: xyz + (optional) per-splat quat. Even with
                # quats this is ~24 MB vs ~161 MB for a full ply — still a
                # 7x reduction over the full-attr frame.
                fields = [("x", np.float32), ("y", np.float32), ("z", np.float32)]
                if new_quat is not None:
                    fields += [("rot_0", np.float32), ("rot_1", np.float32),
                               ("rot_2", np.float32), ("rot_3", np.float32)]
                out = np.empty(len(full_attrs), dtype=fields)
                out["x"] = out_xyz_world[:, 0]
                out["y"] = out_xyz_world[:, 1]
                out["z"] = out_xyz_world[:, 2]
                if new_quat is not None:
                    out["rot_0"] = new_quat[:, 0]
                    out["rot_1"] = new_quat[:, 1]
                    out["rot_2"] = new_quat[:, 2]
                    out["rot_3"] = new_quat[:, 3]
                tmp_path = Path(str(out_path) + ".tmp")
                PlyData([PlyElement.describe(out, "vertex")], text=text_mode).write(tmp_path)
                os.replace(tmp_path, out_path)
            return out_path

        # Legacy 1-NN overlay path (kept for backwards compatibility +
        # debugging the K-NN behavior against the prior baseline).
        if idx == 0 or not args.xyz_only_after_first:
            _write_frame_atomic(full_attrs, nn_idx, kept_sim_idx, sim_xyz, args, out_path, text_mode)
        else:
            _write_frame_xyz_only(full_attrs, nn_idx, kept_sim_idx, sim_xyz, args, out_path, text_mode)
        return out_path

    # Initial pass: process whatever's already on disk.
    for sp in sim_plys:
        idx = _sim_idx(sp)
        if idx is None: continue
        out_path = fuse_one(sp, idx)
        processed.add(sp)
        if idx % 25 == 0:
            print(f"  wrote {len(processed)}/{len(sim_plys)}: {out_path.name}")
    print(f"Initial batch done: {len(processed)} fused plys in {out_dir}")

    # Watch loop: keep polling sim_dir for new sim_*.ply files until quiet timeout
    # or max_frames is reached.
    if args.watch:
        if args.max_frames > 0 and len(processed) >= args.max_frames:
            print(f"[watch] already at {len(processed)} >= max_frames={args.max_frames}, skipping watch")
            return
        print(f"[watch] polling {sim_dir} every 0.5s for new frames "
              f"(exits after {args.watch_quiet_seconds:.0f}s of silence"
              f"{f' or after {args.max_frames} total frames' if args.max_frames > 0 else ''})...")
        quiet_t = 0.0
        while quiet_t < args.watch_quiet_seconds:
            if args.max_frames > 0 and len(processed) >= args.max_frames:
                print(f"[watch] reached max_frames={args.max_frames}, exiting "
                      f"({len(processed)} total frames in {out_dir})")
                return
            current = sorted(sim_dir.glob("sim_*.ply"))
            new_plys = [sp for sp in current if sp not in processed]
            if not new_plys:
                time.sleep(0.5); quiet_t += 0.5; continue
            quiet_t = 0.0
            for sp in new_plys:
                idx = _sim_idx(sp)
                if idx is None: continue
                # Skip files still being written (size < 1KB or recently modified).
                try:
                    if sp.stat().st_size < 1024: continue
                except FileNotFoundError:
                    continue
                try:
                    out_path = fuse_one(sp, idx)
                    processed.add(sp)
                    print(f"  [watch] +frame {idx:04d}")
                except Exception as e:
                    # Likely partial write — retry next poll.
                    print(f"  [watch] skip {sp.name} ({e}); will retry")
            time.sleep(0.2)
        print(f"[watch] {args.watch_quiet_seconds:.0f}s quiet, exiting "
              f"({len(processed)} total frames in {out_dir})")


if __name__ == "__main__":
    main()
