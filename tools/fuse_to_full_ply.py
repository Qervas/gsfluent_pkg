"""Fuse per-frame sim xyz with reference 3DGS static attrs.

Input:
    --reference_ply: original 3DGS .ply (xyz, normals, opacity, scale, rot, SH)
    --sim_dir: dir with sim_*.ply (xyz only — output of gs_simulation_building.py)
    --out_dir: where to write full per-frame plys

Output:
    out_dir/frame_NNN.ply (one per sim frame, full 3DGS format vkSplatting can render)

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
import time
from pathlib import Path
import numpy as np
from plyfile import PlyData, PlyElement
from scipy.spatial import cKDTree


_SIM_RE = re.compile(r"sim_(\d+)\.ply$")


def _sim_idx(path):
    m = _SIM_RE.search(str(path))
    return int(m.group(1)) if m else None


def _write_frame_atomic(static_attrs, kept_sim_idx, sim_xyz, args, out_path, text_mode):
    """Write a fused frame ply atomically (tmp + rename so vkgs --watch_dir
    polling never sees a partially-written file)."""
    out = static_attrs.copy()
    sx = sim_xyz[kept_sim_idx, 0]
    sy = sim_xyz[kept_sim_idx, 1]
    sz = sim_xyz[kept_sim_idx, 2]
    if args.center_at_origin:
        sx = sx - 1.0; sy = sy - 1.0; sz = sz - 0.5
    if args.zup_to_yup:
        out["x"] = sx; out["y"] = sz; out["z"] = -sy
    else:
        out["x"] = sx; out["y"] = sy; out["z"] = sz
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
    p.add_argument("--zup_to_yup", action="store_true", default=True,
                   help="Permute axes from sim's Z-up convention to viewer's Y-up "
                        "(swap so output (x, y, z) = (sim_x, sim_z, -sim_y))")
    p.add_argument("--no-zup_to_yup", dest="zup_to_yup", action="store_false")
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
    args = p.parse_args()

    print(f"Loading reference: {args.reference_ply}")
    ref_ply = PlyData.read(args.reference_ply)
    ref_v = ref_ply["vertex"].data
    ref_xyz_raw = np.stack([ref_v["x"], ref_v["y"], ref_v["z"]], axis=1).astype(np.float32)
    aabb_min = ref_xyz_raw.min(0); aabb_max = ref_xyz_raw.max(0)
    center = (aabb_min + aabb_max) / 2.0
    extent = float((aabb_max - aabb_min).max())
    scale_origin = 1.0 / extent
    ref_xyz_norm = ((ref_xyz_raw - center) / extent + 1.0).astype(np.float32)
    print(f"  ref: {len(ref_xyz_raw)} gaussians, extent {extent:.2f}, scale_origin {scale_origin:.4f}")

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

    # Build the static attribute structured array (one row per kept particle)
    # Use the same dtype as ref_v but with our subsampled count
    out_dtype = ref_v.dtype
    static_attrs = np.empty(len(kept_sim_idx), dtype=out_dtype)
    for field in out_dtype.names:
        static_attrs[field] = ref_v[field][nn_idx]

    # Adjust scale_0/1/2: sim is in normalized space, scales must shrink by log(scale_origin)
    log_scale_shift = float(np.log(scale_origin))
    print(f"  scale shift (log-space): {log_scale_shift:.4f}")
    for k in ("scale_0", "scale_1", "scale_2"):
        if k in static_attrs.dtype.names:
            static_attrs[k] = static_attrs[k] + log_scale_shift

    # Z-up -> Y-up axis permutation. Sim has +Z up; vkSplatting (and most viewers)
    # expect +Y up. The change of basis is (x, y, z) -> (x, z, -y), which is
    # equivalent to a -90 deg rotation around the X axis. We need to apply it to:
    #   - positions (per-frame, below)
    #   - per-gaussian rotation quaternions (here, once)
    #   - normals (here, once)
    if args.zup_to_yup:
        # -90 deg around X axis (Z-up -> Y-up, right-handed: cross(x,z)=-y so
        # new_z = -old_y). Quaternion (w, x, y, z) layout.
        c = np.cos(-np.pi / 4); s = np.sin(-np.pi / 4)
        q_axis = np.array([c, s, 0.0, 0.0], dtype=np.float32)  # (w, x, y, z)
        # Compose: q_new = q_axis * q_old (Hamilton product, broadcast over particles)
        wA, xA, yA, zA = q_axis
        wB = static_attrs["rot_0"]; xB = static_attrs["rot_1"]
        yB = static_attrs["rot_2"]; zB = static_attrs["rot_3"]
        new_w = wA*wB - xA*xB - yA*yB - zA*zB
        new_x = wA*xB + xA*wB + yA*zB - zA*yB
        new_y = wA*yB - xA*zB + yA*wB + zA*xB
        new_z = wA*zB + xA*yB - yA*xB + zA*wB
        static_attrs["rot_0"] = new_w
        static_attrs["rot_1"] = new_x
        static_attrs["rot_2"] = new_y
        static_attrs["rot_3"] = new_z
        # Permute normals likewise (these are all 0 in 3DGS plys but be defensive)
        nx = static_attrs["nx"].copy() if "nx" in static_attrs.dtype.names else None
        ny = static_attrs["ny"].copy() if "ny" in static_attrs.dtype.names else None
        nz = static_attrs["nz"].copy() if "nz" in static_attrs.dtype.names else None
        if nx is not None:
            static_attrs["nx"] = nx
            static_attrs["ny"] = nz
            static_attrs["nz"] = -ny
        print(f"  Z-up -> Y-up permutation applied to rotations + normals")

    # Per-frame: replace x/y/z with sim positions and write
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    text_mode = ref_ply.text
    processed = set()

    def fuse_one(sp, idx):
        v = PlyData.read(str(sp))["vertex"].data
        sim_xyz = np.stack([v["x"], v["y"], v["z"]], axis=1).astype(np.float32)
        out_path = out_dir / f"frame_{idx:04d}.ply"
        # Sim particles live in normalized [0,2]^3 with center (1,1,1) and
        # the slip floor at z=0.5; --center_at_origin subtracts (1,1,0.5).
        # --zup_to_yup permutes (x,y,z) -> (x,z,-y) so sim +Z (up) becomes
        # viewer +Y.
        _write_frame_atomic(static_attrs, kept_sim_idx, sim_xyz, args, out_path, text_mode)
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

    # Watch loop: keep polling sim_dir for new sim_*.ply files until quiet timeout.
    if args.watch:
        print(f"[watch] polling {sim_dir} every 0.5s for new frames "
              f"(exits after {args.watch_quiet_seconds:.0f}s of silence)...")
        quiet_t = 0.0
        while quiet_t < args.watch_quiet_seconds:
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
