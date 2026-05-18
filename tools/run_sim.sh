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
# Environment overrides — REQUIRED, set them in .env or your shell:
#   GSFLUENT_SIM_HOME    canonical GaussianFluent install root (no default)
#   GSFLUENT_SIM_PYTHON  python interpreter with torch/warp/taichi
#                        (default: `python` from PATH — usually wrong)
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
SIM_HOME="${GSFLUENT_SIM_HOME:-}"
SIM_PY="${GSFLUENT_SIM_PYTHON:-python}"

if [[ -z "$SIM_HOME" ]]; then
    cat >&2 <<EOF
ERROR: GSFLUENT_SIM_HOME is not set.

This wrapper needs to know where the GaussianFluent source tree lives
on this host. Set it in the environment that spawned gsfluent serve,
e.g. via the repo's .env file:

  cp .env.example .env
  \$EDITOR .env                  # fill in GSFLUENT_SIM_HOME + SIM_PYTHON
  ./start-gsfluent-server.sh

Or inline:
  GSFLUENT_SIM_HOME=/path/to/GaussianFluent \\
  GSFLUENT_SIM_PYTHON=/path/to/sim-env/bin/python \\
  ./start-gsfluent-server.sh
EOF
    exit 1
fi

# Pre-flight: bail fast with a clear, deploy-aware error if the sim env
# isn't mounted/installed. Without this the script would barrel into
# `cd "$SIM_HOME"` and surface a cryptic "no such file or directory"
# to the workbench's run.log. The Docker bundled-image case (no
# SIM_HOST_DIR mounted) hits this path on a leader's POST /api/runs.
if [[ ! -d "$SIM_HOME" ]]; then
    cat >&2 <<EOF
ERROR: no sim environment installed at \$GSFLUENT_SIM_HOME=$SIM_HOME

This deploy has the gsfluent API but no MPM sim. POST /api/runs requires
a working GaussianFluent install (the upstream sim core with Warp +
Taichi + torch). Three ways to fix:

  1. Mount your sim install into the container:
     SIM_HOST_DIR=/path/to/GaussianFluent docker compose up -d
     (and uncomment the SIM_HOST_DIR volume line in docker/compose.yml)

  2. Point at a sim install already on the host:
     GSFLUENT_SIM_HOME=/path/to/GaussianFluent ./run-server.sh

  3. Use a sim-capable backend elsewhere (your GPU server) and connect
     this workbench to it via SSH tunnel — see README "Mode B".
EOF
    exit 1
fi
if ! command -v "$SIM_PY" >/dev/null 2>&1; then
    echo "ERROR: sim interpreter not on PATH: \$GSFLUENT_SIM_PYTHON=$SIM_PY" >&2
    echo "       Point it at the python that has torch/warp/taichi installed." >&2
    exit 1
fi

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
# --no_cfl_override and --graph_capture are upstream speed flags that
# trade safety for ~30-40% perf. They break time-varying-BC scenarios
# (earthquake's four cuboids alternate every 0.3s) and any recipe whose
# substep_dt exceeds CFL (illegal memory access from numerical blowup).
# We don't ship them by default; recipes that genuinely need them can
# opt back in by setting GSFLUENT_SIM_FAST=1 in the server's environment.
EXTRA_FLAGS=()
if [[ "${GSFLUENT_SIM_FAST:-0}" == "1" ]]; then
    EXTRA_FLAGS+=("--no_cfl_override" "--graph_capture")
fi
"$SIM_PY" gs_simulation/watermelon/gs_simulation_building.py \
    --model_path     "$MODEL_DIR" \
    --output_path    "$SIM_OUTPUT_DIR" \
    --config         "$CONFIG" \
    --target_particles "$PARTICLES" \
    --output_ply --async_io --output_cov \
    "${EXTRA_FLAGS[@]}"

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
