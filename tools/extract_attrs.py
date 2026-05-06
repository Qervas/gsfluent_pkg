"""For one cell's frame-0 sim_*.ply, look up each particle's nearest
reference gaussian and copy its (cov, rgb, opacity). Save with the
per-frame xyz positions to a single npz.

This produces a renderable "textured" splat dataset: anisotropic gaussians
with proper colors, only the centers move per frame.

Usage:
    python extract_attrs.py --reference_ply <ref.ply> \
        --sim_dir <plys/cell> --out <cell.npz> [--subsample 200000]
"""
import argparse
from pathlib import Path
import numpy as np
from plyfile import PlyData
from scipy.spatial import cKDTree

from sh_eval import assemble_sh_coeffs, eval_sh


def load_reference(ref_path: str, view_dir: np.ndarray, sh_degree: int = 3):
    """Load reference ply, evaluate SH at fixed view_dir for each particle.
    Returns (xyz, log_scales, quats, rgb, opacity)."""
    ply = PlyData.read(ref_path)
    v = ply["vertex"].data
    xyz = np.stack([v["x"], v["y"], v["z"]], axis=1).astype(np.float32)
    log_scales = np.stack([v["scale_0"], v["scale_1"], v["scale_2"]], axis=1).astype(np.float32)
    quats = np.stack([v["rot_0"], v["rot_1"], v["rot_2"], v["rot_3"]], axis=1).astype(np.float32)
    sh_coeffs = assemble_sh_coeffs(v)
    rgb_pre = eval_sh(sh_coeffs, view_dir, degree=sh_degree)
    rgb = np.clip(rgb_pre + 0.5, 0, 1).astype(np.float32)
    opacity = (1.0 / (1.0 + np.exp(-v["opacity"].astype(np.float32)))).reshape(-1, 1)
    return xyz, log_scales, quats, rgb, opacity


def quat_to_rotmat(q):
    q = q / np.linalg.norm(q, axis=1, keepdims=True)
    w, x, y, z = q[:, 0], q[:, 1], q[:, 2], q[:, 3]
    R = np.empty((q.shape[0], 3, 3), dtype=np.float32)
    R[:, 0, 0] = 1 - 2 * (y * y + z * z)
    R[:, 0, 1] = 2 * (x * y - w * z); R[:, 0, 2] = 2 * (x * z + w * y)
    R[:, 1, 0] = 2 * (x * y + w * z); R[:, 1, 1] = 1 - 2 * (x * x + z * z); R[:, 1, 2] = 2 * (y * z - w * x)
    R[:, 2, 0] = 2 * (x * z - w * y); R[:, 2, 1] = 2 * (y * z + w * x); R[:, 2, 2] = 1 - 2 * (x * x + y * y)
    return R


def normalize_to_unit(xyz):
    aabb_min = xyz.min(0); aabb_max = xyz.max(0)
    center = (aabb_min + aabb_max) / 2.0
    extent = float((aabb_max - aabb_min).max())
    return ((xyz - center) / extent + 1.0).astype(np.float32), center, extent


def build_covariances(log_scales, quats, scale_origin: float):
    s = np.exp(log_scales) * scale_origin   # bring scale into normalized space
    R = quat_to_rotmat(quats)
    Rs = R * s[:, None, :]
    cov = np.einsum("nij,nkj->nik", Rs, Rs).astype(np.float32)
    return cov


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--reference_ply", required=True)
    p.add_argument("--sim_dir", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--subsample", type=int, default=200000)
    p.add_argument("--view_dir", nargs=3, type=float, default=[0.0, -1.0, 0.0],
                   help="Fixed unit direction for SH evaluation (e.g., camera-to-particle).")
    p.add_argument("--sh_degree", type=int, default=3)
    args = p.parse_args()

    view = np.asarray(args.view_dir, dtype=np.float32)
    view = view / np.linalg.norm(view)
    print(f"SH eval at view_dir = {view.tolist()}")
    print("Loading reference...")
    ref_xyz_raw, ref_log_scales, ref_quats, ref_rgb, ref_opacity = load_reference(
        args.reference_ply, view, sh_degree=args.sh_degree)
    ref_xyz_norm, ref_center, ref_extent = normalize_to_unit(ref_xyz_raw)
    scale_origin = 1.0 / ref_extent
    print(f"  ref: {len(ref_xyz_norm)} gaussians, extent {ref_extent:.2f}, scale_origin {scale_origin:.4f}")
    ref_cov = build_covariances(ref_log_scales, ref_quats, scale_origin)

    plys = sorted(Path(args.sim_dir).glob("sim_*.ply"))
    print(f"Loading {len(plys)} sim frames...")
    first = PlyData.read(str(plys[0]))["vertex"].data
    n_total = len(first)
    if args.subsample is not None and n_total > args.subsample:
        rng = np.random.default_rng(0)
        kept = rng.choice(n_total, size=args.subsample, replace=False)
        kept.sort()
    else:
        kept = np.arange(n_total)
    print(f"  kept {len(kept)} of {n_total} per frame")

    # NN lookup: for each kept sim particle (frame 0), find nearest reference gaussian
    print("Building NN lookup (frame 0 sim -> reference)...")
    sim_xyz_t0 = np.stack([first["x"][kept], first["y"][kept], first["z"][kept]], axis=1).astype(np.float32)
    tree = cKDTree(ref_xyz_norm)
    _, nn_idx = tree.query(sim_xyz_t0, k=1, workers=-1)
    print(f"  matched {len(nn_idx)} particles")

    # Extract static attrs
    cov = ref_cov[nn_idx]                  # (N, 3, 3)
    rgb = ref_rgb[nn_idx]                  # (N, 3)
    opacity = ref_opacity[nn_idx]          # (N, 1)

    # Extract per-frame xyz
    print("Loading per-frame xyz...")
    frames = np.empty((len(plys), len(kept), 3), dtype=np.float32)
    for i, p in enumerate(plys):
        v = PlyData.read(str(p))["vertex"].data
        frames[i, :, 0] = v["x"][kept]
        frames[i, :, 1] = v["y"][kept]
        frames[i, :, 2] = v["z"][kept]
        if i % 25 == 0:
            print(f"  {i}/{len(plys)}")

    np.savez_compressed(args.out, frames=frames, cov=cov, rgb=rgb, opacity=opacity)
    total_mb = (frames.nbytes + cov.nbytes + rgb.nbytes + opacity.nbytes) / 1e6
    print(f"Wrote {args.out}  ({total_mb:.1f} MB uncompressed)")


if __name__ == "__main__":
    main()
