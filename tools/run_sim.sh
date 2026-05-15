#!/usr/bin/env bash
# Server-side simulation wrapper, invoked by server/gsfluent/core/runner.py.
#
# Runs two stages and exits 0 only if both succeed:
#   1. The canonical MPM sim (`gs_simulation_building.py`)
#      → simulation_ply/iteration_*.ply in $SIM_OUTPUT_DIR
#   2. Fuse                              (`tools/fuse_to_full_ply.py`)
#      → frame_*.ply in work/library/sequences/<run>/frames/
#
# CLI (matches what runner.py spawns):
#   bash run_sim.sh <model_dir> --config <recipe.json> \
#                   --particles N --output <run_name>
#
# Environment overrides — set these in run-server.sh or the server's env:
#   GSFLUENT_SIM_HOME    canonical install root
#                        default: $GSFLUENT_SIM_HOME
#   GSFLUENT_SIM_PYTHON  python interpreter for the sim
#                        default: the python on PATH that satisfies
#                                 torch/warp/taichi (the server's conda env)
#   GSFLUENT_SIM_ENV     conda env name for `conda activate` (optional)
#                        default: empty (assumes the calling env is correct)
#
# Adapt to your server's directory layout via the env vars above.
# runner.py always passes the merged recipe JSON via --config (built
# from the user's recipe + sim_area frame translation).

set -euo pipefail

# ---------- arg parse --------------------------------------------------------
MODEL_DIR=""
CONFIG=""
PARTICLES=""
OUTPUT=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --config)    CONFIG="$2"; shift 2 ;;
        --particles) PARTICLES="$2"; shift 2 ;;
        --output)    OUTPUT="$2"; shift 2 ;;
        -*)          echo "unknown option: $1" >&2; exit 2 ;;
        *)
            if [[ -z "$MODEL_DIR" ]]; then
                MODEL_DIR="$1"; shift
            else
                echo "extra positional: $1" >&2; exit 2
            fi
            ;;
    esac
done

for v in MODEL_DIR CONFIG PARTICLES OUTPUT; do
    if [[ -z "${!v}" ]]; then
        echo "ERROR: missing required arg: $v" >&2; exit 2
    fi
done

# ---------- resolve paths ----------------------------------------------------
PKG_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SIM_HOME="${GSFLUENT_SIM_HOME:-$GSFLUENT_SIM_HOME}"
SIM_PY="${GSFLUENT_SIM_PYTHON:-python}"

# Conda activation, optional. If GSFLUENT_SIM_ENV is set we activate it;
# otherwise we trust the caller's env (e.g. systemd unit, run-server.sh)
# has already set up the right python.
if [[ -n "${GSFLUENT_SIM_ENV:-}" ]]; then
    if command -v conda >/dev/null 2>&1; then
        # shellcheck disable=SC1091
        eval "$(conda shell.bash hook)"
        conda activate "$GSFLUENT_SIM_ENV"
    else
        echo "WARN: GSFLUENT_SIM_ENV=$GSFLUENT_SIM_ENV set but conda not on PATH" >&2
    fi
fi

SIM_OUTPUT_DIR="$SIM_HOME/output/$OUTPUT"
SIM_PLY_DIR="$SIM_OUTPUT_DIR/simulation_ply"
LIBRARY_SEQ_DIR="$PKG_ROOT/work/library/sequences/$OUTPUT"
FUSED_DIR="$LIBRARY_SEQ_DIR/frames"

# Reference ply for fuse: latest iteration_*/point_cloud.ply in the model.
# Use version-sort so `iteration_30000` > `iteration_7000` (lex sort
# would pick iteration_7000 incorrectly).
REFERENCE_PLY="$(find "$MODEL_DIR/point_cloud" -name 'point_cloud.ply' 2>/dev/null | sort -V | tail -n 1)"
if [[ -z "$REFERENCE_PLY" ]]; then
    echo "ERROR: no reference ply under $MODEL_DIR/point_cloud/" >&2
    exit 1
fi

mkdir -p "$SIM_OUTPUT_DIR" "$LIBRARY_SEQ_DIR" "$FUSED_DIR"

# Preserve recipe.json in the library entry early so a sim crash doesn't lose it.
cp -f "$CONFIG" "$LIBRARY_SEQ_DIR/recipe.json"

cat <<EOF
=== run_sim.sh plan ===
  model         : $MODEL_DIR
  config        : $CONFIG
  output name   : $OUTPUT
  particles     : $PARTICLES
  sim home      : $SIM_HOME
  sim python    : $SIM_PY
  sim output    : $SIM_OUTPUT_DIR
  reference ply : $REFERENCE_PLY
  fused frames  : $FUSED_DIR
EOF

# ---------- step 1: MPM sim --------------------------------------------------
echo ""
echo "=== step 1: MPM simulation ==="
cd "$SIM_HOME"
"$SIM_PY" gs_simulation/watermelon/gs_simulation_building.py \
    --model_path     "$MODEL_DIR" \
    --output_path    "$SIM_OUTPUT_DIR" \
    --config         "$CONFIG" \
    --no_cfl_override \
    --graph_capture \
    --target_particles "$PARTICLES" \
    --output_ply --async_io

# ---------- step 2: fuse to per-frame splat plys -----------------------------
echo ""
echo "=== step 2: fuse to frame_*.ply ==="
cd "$PKG_ROOT"
"$SIM_PY" "$PKG_ROOT/tools/fuse_to_full_ply.py" \
    --reference_ply "$REFERENCE_PLY" \
    --sim_dir       "$SIM_PLY_DIR" \
    --out_dir       "$FUSED_DIR" \
    --knn 8 --no_zup
# Note: --knn_rotation requires --no_zup (per-frame rotation lives in
# sim space, no basis transform to a rotated output). With --no_zup
# the output stays in source coords (Y-up if source is Y-up); for
# our cluster_6_15 source which is already Z-up, --no_zup is correct
# anyway. If you later add a Y-up source, revisit.

# ---------- done -------------------------------------------------------------
echo ""
echo "=== run_sim.sh done: $OUTPUT ==="
echo "  frames at: $FUSED_DIR"
echo "  runner.py will now build the .npz cache."
