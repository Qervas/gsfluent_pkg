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
import sys
import time
from pathlib import Path
import numpy as np
from plyfile import PlyData, PlyElement
from scipy.spatial import cKDTree

# Make `gsfluent` importable when this script runs from a checkout without
# pip install. Mirrors the pattern in tools/migrate_to_library.py.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "server") not in sys.path:
    sys.path.insert(0, str(_ROOT / "server"))

from gsfluent.core.coord_convert import (  # noqa: E402
    rotate_normals_y_up_to_z_up as _rotate_norm,
    rotate_quaternions_y_up_to_z_up as _rotate_quat,
)


_SIM_RE = re.compile(r"sim_(\d+)\.ply$")


def _sim_idx(path):
    m = _SIM_RE.search(str(path))
    return int(m.group(1)) if m else None


def _transform_sim_xyz(sim_xyz, args):
    """Apply --center_at_origin shift + --zup_to_yup permutation to sim
    positions. Returns (n, 3) array in the final output coord system."""
    sx = sim_xyz[:, 0].astype(np.float32, copy=True)
    sy = sim_xyz[:, 1].astype(np.float32, copy=True)
    sz = sim_xyz[:, 2].astype(np.float32, copy=True)
    if args.center_at_origin:
        sx -= 1.0; sy -= 1.0; sz -= 0.5
    if args.zup_to_yup:
        return np.stack([sx, sz, -sy], axis=1)
    return np.stack([sx, sy, sz], axis=1)


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
    p.add_argument("--max_frames", type=int, default=0,
                   help="If >0, exit watch mode as soon as this many fused frames "
                        "have been produced. Lets sim_one.sh wrap up immediately "
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

    # Build the FULL reference attribute array (one row per ref splat).
    # Output frames will start as a copy of this and overlay sim positions
    # only on the matched indices — so the static remainder (splats not
    # claimed by any sim particle) keeps its rest position and the viewer
    # sees the whole building per frame.
    out_dtype = ref_v.dtype
    full_attrs = np.empty(len(ref_v), dtype=out_dtype)
    for field in out_dtype.names:
        full_attrs[field] = ref_v[field]

    # Adjust scale_0/1/2: sim is in normalized space, scales must shrink by log(scale_origin)
    log_scale_shift = float(np.log(scale_origin))
    print(f"  scale shift (log-space): {log_scale_shift:.4f}")
    for k in ("scale_0", "scale_1", "scale_2"):
        if k in full_attrs.dtype.names:
            full_attrs[k] = full_attrs[k] + log_scale_shift

    # Z-up -> Y-up axis permutation. Sim has +Z up; vkSplatting (and most viewers)
    # expect +Y up. The change of basis is (x, y, z) -> (x, z, -y), which is
    # equivalent to a -90 deg rotation around the X axis (Rx(-pi/2)). We need
    # to apply it to:
    #   - positions (per-frame, below)
    #   - per-gaussian rotation quaternions (here, once)
    #   - normals (here, once)
    #
    # The math lives in core/coord_convert.py — it's the same Rx(-pi/2)
    # used by the import-time Y-up -> Z-up converter. Both directions
    # share this matrix because the (x,y,z)->(x,z,-y) permutation is
    # numerically identical regardless of the semantic label.
    if args.zup_to_yup:
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
        print(f"  Z-up -> Y-up permutation applied to rotations + normals")

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
        # Frame 0 always full (workbench/vkgs bootstrap); frames 1+ slim
        # to xyz-only when the flag is set — same overlay logic, smaller
        # disk write.
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
