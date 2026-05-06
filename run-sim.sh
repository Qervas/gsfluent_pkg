#!/usr/bin/env bash
# gsfluent_pkg — one command to sim a building and watch it in your browser.
#
# Usage:
#   ./run-sim.sh <ply_or_model_dir> [options]
#
# <ply_or_model_dir>:
#   - A 3DGS model directory (must contain point_cloud/iteration_*/point_cloud.ply)
#   - OR a single .ply file (auto-wrapped into a temporary model dir for the sim)
#
# Options:
#   --recipe NAME       recipe in tools/recipes/<NAME>.json (default: jelly)
#                       see: ls tools/recipes/
#   --particles N       MPM particle count (default: 200000)
#   --output NAME       output dir name (default: <model>_<recipe>_<date>)
#   --no-viewer         skip auto-launching the browser viewer (just sim+fuse)
#   --port N            viewer HTTP port (default: 8080)
#   --dry-run           print commands without running
#
# Environment:
#   GSFLUENT_ENV    conda env name (default: gsfluent)

set -euo pipefail

PKG_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_NAME="${GSFLUENT_ENV:-gsfluent}"

# --- args ----------------------------------------------------------------
INPUT=""
RECIPE="jelly"
PARTICLES=200000
OUTPUT_NAME=""
SKIP_VIEWER=0
PORT=8080
DRY_RUN=0

usage() {
    sed -n '2,/^# Environment:/p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'
    echo ""
    echo "Recipes available:"
    for f in "$PKG_ROOT"/tools/recipes/*.json; do
        [[ -f "$f" ]] && echo "  $(basename "${f%.json}")"
    done
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help) usage; exit 0 ;;
        --recipe)     RECIPE="$2"; shift 2 ;;
        --particles)  PARTICLES="$2"; shift 2 ;;
        --output)     OUTPUT_NAME="$2"; shift 2 ;;
        --no-viewer)  SKIP_VIEWER=1; shift ;;
        --port)       PORT="$2"; shift 2 ;;
        --dry-run)    DRY_RUN=1; shift ;;
        -*) echo "unknown option: $1" >&2; usage; exit 2 ;;
        *)  if [[ -z "$INPUT" ]]; then INPUT="$1"; else
                echo "extra positional: $1" >&2; exit 2; fi
            shift ;;
    esac
done

[[ -z "$INPUT" ]] && { echo "ERROR: <ply_or_model_dir> required" >&2; usage; exit 2; }
[[ -e "$INPUT" ]] || { echo "ERROR: $INPUT not found" >&2; exit 1; }

# --- resolve model_path: either a model dir or a single .ply ----------
INPUT_ABS="$(cd "$(dirname "$INPUT")" && pwd)/$(basename "$INPUT")"
if [[ -d "$INPUT_ABS" ]]; then
    if [[ -d "$INPUT_ABS/point_cloud" ]]; then
        MODEL_DIR="$INPUT_ABS"
    else
        echo "ERROR: $INPUT_ABS is a dir but missing point_cloud/iteration_*/point_cloud.ply" >&2
        exit 1
    fi
elif [[ "$INPUT_ABS" == *.ply ]]; then
    BASE="$(basename "${INPUT_ABS%.ply}")"
    MODEL_DIR="$PKG_ROOT/work/_wrapped/$BASE"
    if [[ ! -f "$MODEL_DIR/point_cloud/iteration_30000/point_cloud.ply" ]]; then
        echo ">>> wrapping $INPUT_ABS into a 3DGS-compatible model dir at $MODEL_DIR"
        mkdir -p "$MODEL_DIR/point_cloud/iteration_30000"
        ln -sf "$INPUT_ABS" "$MODEL_DIR/point_cloud/iteration_30000/point_cloud.ply"
    fi
else
    echo "ERROR: $INPUT must be a .ply file or a 3DGS model directory" >&2
    exit 1
fi

[[ -z "$OUTPUT_NAME" ]] && OUTPUT_NAME="$(basename "$MODEL_DIR")_${RECIPE}_$(date +%Y%m%d-%H%M)"
OUTPUT_DIR="$PKG_ROOT/work/output/$OUTPUT_NAME"
FUSED_DIR="$PKG_ROOT/work/fused/$OUTPUT_NAME"
SIM_PLY_DIR="$OUTPUT_DIR/simulation_ply"
mkdir -p "$SIM_PLY_DIR" "$FUSED_DIR"

# --- print plan -----------------------------------------------------------
cat <<EOF
=== run-sim.sh ===
  input         : $INPUT_ABS
  model dir     : $MODEL_DIR
  recipe        : $RECIPE
  particles     : $PARTICLES
  output name   : $OUTPUT_NAME
  sim output    : $OUTPUT_DIR
  fused frames  : $FUSED_DIR
  viewer port   : $PORT  (skip=$SKIP_VIEWER)
  dry run       : $DRY_RUN
EOF

run() { echo ""; echo "+ $*"; [[ $DRY_RUN -eq 1 ]] || eval "$@"; }

# --- conda env ------------------------------------------------------------
if [[ $DRY_RUN -eq 0 ]]; then
    eval "$(conda shell.bash hook)"
    if ! conda env list | awk '{print $1}' | grep -qx "$ENV_NAME"; then
        echo "ERROR: conda env '$ENV_NAME' not found. Run ./setup.sh first." >&2
        echo "(Or set GSFLUENT_ENV=<existing_env> if you've installed deps elsewhere.)" >&2
        exit 1
    fi
    conda activate "$ENV_NAME"
fi

# --- spin up viewer (background) so it picks up frames live ---------------
VIEWER_PID=""
cleanup() {
    [[ -n "$VIEWER_PID" ]] && kill "$VIEWER_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

if [[ $SKIP_VIEWER -eq 0 && $DRY_RUN -eq 0 ]]; then
    echo ""
    echo "=== launching browser viewer (point cloud) ==="
    # Wait for first fused frame to exist before starting viewer
    # — but spin up the viewer once the sim+fuse pipeline begins producing them.
    # The viewer can poll the dir; if empty, it shows "loading...".
    python "$PKG_ROOT/tools/view_points.py" \
        --sim_dir "$FUSED_DIR" --port "$PORT" \
        > "$OUTPUT_DIR/viewer.log" 2>&1 &
    VIEWER_PID=$!
    echo "viewer pid=$VIEWER_PID  log=$OUTPUT_DIR/viewer.log"
    echo "open http://localhost:$PORT once frames start arriving"
fi

# --- delegate the heavy lifting to sim_one.sh ---------------------------------
# sim_one.sh handles sim + fuse with its own --live mode; we tell it not to
# launch vkgs (the browser viewer is our renderer here).
echo ""
echo "=== starting sim+fuse via sim_one.sh ==="
SIM_ARGS=(
    "$MODEL_DIR"
    --recipe "$RECIPE"
    --particles "$PARTICLES"
    --output "$OUTPUT_NAME"
)
[[ $DRY_RUN -eq 1 ]] && SIM_ARGS+=( --dry-run )
# Override sim_one's GSFLUENT_HOME defaults so it points at our bundled core/.
export GSFLUENT_HOME="$PKG_ROOT/core_runtime"
mkdir -p "$GSFLUENT_HOME"
# Mirror the sim layout sim_one.sh expects (gsfluent uses these as CWD-relative paths).
ln -sfn "$PKG_ROOT/core/gs_simulation"     "$GSFLUENT_HOME/gs_simulation"
ln -sfn "$PKG_ROOT/core/mpm_solver_warp"   "$GSFLUENT_HOME/mpm_solver_warp"
ln -sfn "$PKG_ROOT/core/particle_filling"  "$GSFLUENT_HOME/particle_filling"
ln -sfn "$PKG_ROOT/core/utils"             "$GSFLUENT_HOME/utils"
ln -sfn "$PKG_ROOT/core/gaussian-splatting" "$GSFLUENT_HOME/gaussian-splatting"
mkdir -p "$GSFLUENT_HOME/output" "$GSFLUENT_HOME/model"
ln -sfn "$MODEL_DIR" "$GSFLUENT_HOME/model/$(basename "$MODEL_DIR")"
# sim_one.sh writes its sim output under $GSFLUENT_HOME/output/<name>/, but our
# OUTPUT_DIR is at $PKG_ROOT/work/output/<name>. Symlink so they're the same dir.
rm -rf "$GSFLUENT_HOME/output/$OUTPUT_NAME" 2>/dev/null
ln -sfn "$OUTPUT_DIR" "$GSFLUENT_HOME/output/$OUTPUT_NAME"

# Make fuse write to OUR fused dir, not sim_one's default.
# sim_one.sh derives FUSED_DIR from R7_DIR/vk_plys; create the symlink so it lands here.
SIM_ONE_R7="$(cd "$PKG_ROOT/tools" && pwd)/.."
mkdir -p "$SIM_ONE_R7/vk_plys"
rm -rf "$SIM_ONE_R7/vk_plys/$OUTPUT_NAME" 2>/dev/null
ln -sfn "$FUSED_DIR" "$SIM_ONE_R7/vk_plys/$OUTPUT_NAME"

# Use a custom sim_one with the watch fuse but no vkgs launch.
GSFLUENT_ENV="$ENV_NAME" \
VKGS_BIN="/dev/null" \
"$PKG_ROOT/tools/sim_one.sh" "${SIM_ARGS[@]}" --live --no-vkgs-launch

echo ""
echo "=== done ==="
if [[ $SKIP_VIEWER -eq 0 ]]; then
    echo "Viewer is still running at http://localhost:$PORT (pid $VIEWER_PID)."
    echo "Press Ctrl-C in this terminal to stop it."
    # Wait on viewer so user can keep watching after sim ends
    wait "$VIEWER_PID" 2>/dev/null || true
else
    echo "Frames written to: $FUSED_DIR"
    echo "To play later: ./run-viewer.sh $FUSED_DIR"
fi
