"""CLI wrapper around gsfluent.core.fusers.knn_kabsch.KNNKabschFuser.

The K-NN skinning + Kabsch logic now lives in
server/gsfluent/core/fusers/knn_kabsch.py. This script handles only:
  - argparse (production defaults)
  - delegating to KNNKabschFuser.fuse_sequence_dir

Legacy script flags (--no_zup, --knn_rotation, --watch, --subsample,
--min_opacity, --max_frames, --ghost_cull_factor, --no-output_source_scale,
--no-center_at_origin, --xyz_only_after_first, particle_F cov-field path)
are NOT exposed in the Phase 2 wrapper. The Protocol contract enshrines the
production defaults: K-NN with K>=1, source-scale output, Y-up to Z-up,
centered at origin, no rotation update. Bring them back in a future sprint
if the use cases reappear.

Usage:
    python server/tools/fuse_to_full_ply.py \\
        --reference_ply path/to/ref.ply \\
        --sim_dir path/to/sim_output \\
        --out_dir path/to/fused_frames \\
        [--knn 8]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Bootstrap so `gsfluent` is importable without pip install.
_BOOTSTRAP_ROOT = Path(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT / "server") not in sys.path:
    sys.path.insert(0, str(_BOOTSTRAP_ROOT / "server"))

from gsfluent.core.fusers.knn_kabsch import KNNKabschFuser  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--reference_ply", required=True)
    p.add_argument("--sim_dir", required=True)
    p.add_argument("--out_dir", required=True)
    p.add_argument("--knn", type=int, default=8,
                   help="K for K-NN skinning. Default 8 (production setting).")
    # Legacy flag aliases — accept without warning so existing run_sim.sh
    # invocations don't break. They no-op in the Phase 2 wrapper because the
    # Protocol-conforming KNNKabschFuser only supports the production defaults.
    p.add_argument("--zup", action="store_true", default=True,
                   help="(legacy, always on)")
    p.add_argument("--no_zup", action="store_true", default=False,
                   help="(legacy; Phase 2 always applies the production "
                        "default zup transform — flag is accepted but no-ops)")
    p.add_argument("--output_source_scale", action="store_true", default=True,
                   help="(legacy, always on)")
    p.add_argument("--center_at_origin", action="store_true", default=True,
                   help="(legacy, always on)")
    args, unknown = p.parse_known_args()
    if unknown:
        print(
            f"[fuse_to_full_ply] note: ignoring legacy flags {unknown} — "
            f"only production defaults are supported in Phase 2",
            file=sys.stderr,
        )

    fuser = KNNKabschFuser(k=args.knn)
    n = fuser.fuse_sequence_dir(
        reference_ply_path=Path(args.reference_ply),
        sim_dir=Path(args.sim_dir),
        out_dir=Path(args.out_dir),
    )
    print(f"[fuse_to_full_ply] wrote {n} frames to {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
