#!/usr/bin/env bash
# sim_one.sh — friendly one-command wrapper:
#   sim a 3DGS model -> fuse to vk_plys -> print vkgs launch command.
#
# Usage:
#   sim_one.sh <model_path> [options]
#
# Examples:
#   sim_one.sh model/cluster_6_15
#   sim_one.sh model/cluster_6_15 --recipe demolition --output collapse_test
#   sim_one.sh ~/my3dgs/scene --recipe jelly --particles 200000
#
# Env overrides:
#   GSFLUENT_HOME   path to local GaussianFluent install (default: ~/gsfluent_local)
#   GSFLUENT_ENV    conda env name (default: mpm)

set -euo pipefail

# --- defaults ----------------------------------------------------------------
RECIPE="jelly"
OUTPUT_NAME=""
PARTICLES=500000
GSFLUENT_HOME="${GSFLUENT_HOME:-$HOME/gsfluent_local}"
GSFLUENT_ENV="${GSFLUENT_ENV:-mpm}"
TOOLS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
R7_DIR="$(cd "$TOOLS_DIR/.." && pwd)"
RECIPES_DIR="$TOOLS_DIR/recipes"
VK_PLYS_DIR="$R7_DIR/vk_plys"
DRY_RUN=0
SKIP_FUSE=0
SKIP_VKGS_CMD=0
BENCH=0
LIVE=0
SKIP_VKGS_LAUNCH=0
VKGS_BIN="${VKGS_BIN:-$HOME/Desktop/work/vk_gaussian_splatting/_bin/Release/vk_gaussian_splatting}"
CONFIG_OVERRIDE=""
REFERENCE_PLY=""

usage() {
    cat <<EOF
sim_one.sh — sim a 3DGS model and prep it for vkgs playback.

Usage: $0 <model_path> [options]

Required:
  <model_path>          path to a 3DGS model dir (must contain point_cloud/iteration_*/)

Options:
  --recipe NAME         recipe in tools/recipes/<NAME>.json (default: jelly)
                        list with: ls $RECIPES_DIR
  --config PATH         use a specific config JSON instead of a recipe
  --output NAME         output dir name (default: <model>_<recipe>_<date>)
  --particles N         target particles (B.1 subsampling); default $PARTICLES
  --reference-ply PATH  reference 3DGS ply for fuse step
                        (default: <model>/point_cloud/iteration_*/point_cloud.ply)
  --no-fuse             skip the fuse-to-full-ply step
  --no-vkgs-cmd         skip printing the final vkgs command
  --bench               run sim in --bench_only mode (no rendering, no fuse)
  --live                LIVE PREVIEW: launch vkgs in --watch_dir mode + run
                        fuse in --watch mode in parallel with sim. You see
                        the building animate as the sim computes it. vkgs
                        stays open after sim completes.
  --no-vkgs-launch      With --live: do not spawn vkgs (assume the caller is
                        already running it and watching the output dir).
                        Used by vkgs's in-app Run-Sim panel.
  --dry-run             print what would run, don't execute
  -h, --help            show this

Recipes available:
$(shopt -s nullglob; for f in "$RECIPES_DIR"/*.json; do echo "  $(basename "${f%.json}")"; done)

Environment:
  GSFLUENT_HOME=$GSFLUENT_HOME
  GSFLUENT_ENV=$GSFLUENT_ENV
EOF
}

# --- parse args --------------------------------------------------------------
if [[ $# -eq 0 ]]; then usage; exit 1; fi

MODEL_PATH=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help) usage; exit 0 ;;
        --recipe)        RECIPE="$2"; shift 2 ;;
        --config)        CONFIG_OVERRIDE="$2"; shift 2 ;;
        --output)        OUTPUT_NAME="$2"; shift 2 ;;
        --particles)     PARTICLES="$2"; shift 2 ;;
        --reference-ply) REFERENCE_PLY="$2"; shift 2 ;;
        --no-fuse)       SKIP_FUSE=1; shift ;;
        --no-vkgs-cmd)   SKIP_VKGS_CMD=1; shift ;;
        --bench)         BENCH=1; SKIP_FUSE=1; shift ;;
        --live)          LIVE=1; shift ;;
        --no-vkgs-launch) SKIP_VKGS_LAUNCH=1; shift ;;
        --dry-run)       DRY_RUN=1; shift ;;
        --)              shift; break ;;
        -*)              echo "unknown option: $1" >&2; usage; exit 2 ;;
        *)               if [[ -z "$MODEL_PATH" ]]; then MODEL_PATH="$1"; else
                             echo "extra positional: $1" >&2; exit 2; fi
                         shift ;;
    esac
done

[[ -z "$MODEL_PATH" ]] && { echo "ERROR: <model_path> required" >&2; usage; exit 2; }

# --- resolve paths -----------------------------------------------------------
# Allow model_path relative to GSFLUENT_HOME
if [[ ! -d "$MODEL_PATH" ]] && [[ -d "$GSFLUENT_HOME/$MODEL_PATH" ]]; then
    MODEL_PATH="$GSFLUENT_HOME/$MODEL_PATH"
fi
MODEL_PATH="$(cd "$MODEL_PATH" 2>/dev/null && pwd)" || {
    echo "ERROR: model_path not found: $MODEL_PATH" >&2; exit 1; }

if [[ -n "$CONFIG_OVERRIDE" ]]; then
    CONFIG_PATH="$(cd "$(dirname "$CONFIG_OVERRIDE")" && pwd)/$(basename "$CONFIG_OVERRIDE")"
    [[ -f "$CONFIG_PATH" ]] || { echo "ERROR: config not found: $CONFIG_PATH" >&2; exit 1; }
    RECIPE_LABEL="$(basename "${CONFIG_PATH%.json}")"
else
    CONFIG_PATH="$RECIPES_DIR/$RECIPE.json"
    [[ -f "$CONFIG_PATH" ]] || {
        echo "ERROR: recipe '$RECIPE' not found at $CONFIG_PATH" >&2
        echo "Available recipes:" >&2
        for f in "$RECIPES_DIR"/*.json; do [[ -f $f ]] && echo "  $(basename "${f%.json}")" >&2; done
        exit 1; }
    RECIPE_LABEL="$RECIPE"
fi

if [[ -z "$OUTPUT_NAME" ]]; then
    OUTPUT_NAME="$(basename "$MODEL_PATH")_${RECIPE_LABEL}_$(date +%Y%m%d-%H%M)"
fi

if [[ -z "$REFERENCE_PLY" && $SKIP_FUSE -eq 0 ]]; then
    REFERENCE_PLY="$(find "$MODEL_PATH/point_cloud" -name 'point_cloud.ply' 2>/dev/null | sort | tail -1)"
    [[ -n "$REFERENCE_PLY" ]] || {
        echo "ERROR: no reference ply under $MODEL_PATH/point_cloud/. Pass --reference-ply." >&2
        exit 1; }
fi

SIM_OUTPUT_DIR="$GSFLUENT_HOME/output/$OUTPUT_NAME"
SIM_PLY_DIR="$SIM_OUTPUT_DIR/simulation_ply"
FUSED_DIR="$VK_PLYS_DIR/$OUTPUT_NAME"

# --- print plan --------------------------------------------------------------
if [[ $LIVE -eq 1 && $BENCH -eq 1 ]]; then
    echo "ERROR: --live and --bench are mutually exclusive (live needs frames)" >&2; exit 2
fi
if [[ $LIVE -eq 1 && $SKIP_FUSE -eq 1 ]]; then
    echo "ERROR: --live needs fuse (uses fuse --watch internally)" >&2; exit 2
fi

cat <<EOF
=== sim_one.sh plan ===
  model         : $MODEL_PATH
  recipe        : $RECIPE_LABEL
  config        : $CONFIG_PATH
  output name   : $OUTPUT_NAME
  particles     : $PARTICLES
  sim output    : $SIM_OUTPUT_DIR
  reference ply : ${REFERENCE_PLY:-<skipped (no fuse)>}
  fused frames  : ${FUSED_DIR:-<skipped>}
  bench mode    : $([ $BENCH -eq 1 ] && echo yes || echo no)
  live preview  : $([ $LIVE -eq 1 ] && echo yes || echo no)
  dry run       : $([ $DRY_RUN -eq 1 ] && echo yes || echo no)
EOF

run() {
    echo ""
    echo "+ $*"
    [[ $DRY_RUN -eq 1 ]] || eval "$@"
}

# --- conda env ---------------------------------------------------------------
if [[ $DRY_RUN -eq 0 ]]; then
    eval "$(conda shell.bash hook)"
    conda activate "$GSFLUENT_ENV"
fi

# --- step 1: sim -------------------------------------------------------------
SIM_FLAGS=(
    --model_path "$MODEL_PATH"
    --output_path "$SIM_OUTPUT_DIR"
    --config "$CONFIG_PATH"
    --no_cfl_override
    --graph_capture
    --target_particles "$PARTICLES"
)
if [[ $BENCH -eq 1 ]]; then
    SIM_FLAGS+=( --bench_only )
else
    SIM_FLAGS+=( --output_ply --async_io )
fi

mkdir -p "$SIM_OUTPUT_DIR"
[[ $LIVE -eq 1 ]] && mkdir -p "$SIM_PLY_DIR" "$FUSED_DIR"

# --- live mode: spin up watchers BEFORE sim ---------------------------------
VKGS_PID=""; FUSE_PID=""
cleanup_live() {
    if [[ -n "$FUSE_PID" ]] && kill -0 "$FUSE_PID" 2>/dev/null; then
        echo "[live] stopping fuse (pid $FUSE_PID)..."
        kill "$FUSE_PID" 2>/dev/null || true
        wait "$FUSE_PID" 2>/dev/null || true
    fi
    [[ -n "$VKGS_PID" ]] && echo "[live] vkgs (pid $VKGS_PID) left running — close the window to exit it."
}
if [[ $LIVE -eq 1 && $DRY_RUN -eq 0 ]]; then
    trap cleanup_live EXIT INT TERM
    echo ""
    if [[ $SKIP_VKGS_LAUNCH -eq 1 ]]; then
        echo "=== live (vkgs already running): launching fuse (--watch) before sim ==="
    else
        if [[ ! -x "$VKGS_BIN" ]]; then
            echo "ERROR: vkgs binary not found at $VKGS_BIN" >&2
            echo "  set VKGS_BIN env var to override." >&2
            exit 1
        fi
        echo "=== live: launching vkgs (watch_dir) + fuse (--watch) before sim ==="
        "$VKGS_BIN" --inputFile "$REFERENCE_PLY" --watch_dir "$FUSED_DIR" \
            > "$SIM_OUTPUT_DIR/vkgs.log" 2>&1 &
        VKGS_PID=$!
        echo "[live] vkgs pid: $VKGS_PID  (log: $SIM_OUTPUT_DIR/vkgs.log)"
    fi
    python "$TOOLS_DIR/fuse_to_full_ply.py" \
        --reference_ply "$REFERENCE_PLY" \
        --sim_dir "$SIM_PLY_DIR" \
        --out_dir "$FUSED_DIR" \
        --watch --watch_quiet_seconds 600 \
        > "$SIM_OUTPUT_DIR/fuse.log" 2>&1 &
    FUSE_PID=$!
    echo "[live] fuse pid: $FUSE_PID  (log: $SIM_OUTPUT_DIR/fuse.log)"
    echo "[live] both watching $FUSED_DIR — sim starts now"
    sleep 1  # give vkgs window time to come up
fi

echo ""
echo "=== step 1/3: simulate ==="
# gs_simulation_building.py imports from utils/, gaussian-splatting/ at the
# GSFLUENT_HOME root and uses _stubs/ for missing optional deps; mirror the
# canonical setup from laptop_smoke.sh.
export PYTHONPATH="$GSFLUENT_HOME:$GSFLUENT_HOME/gaussian-splatting:$GSFLUENT_HOME/_stubs${PYTHONPATH:+:$PYTHONPATH}"
# Taichi 1.7.4 hangs in densify_grids JIT on Blackwell (sm_120). The sim has
# a runtime override: GSFLUENT_TI_ARCH=cpu forces Taichi to CPU just for the
# (cheap) particle-fill step; the rest stays on CUDA via Warp. Default it on.
export GSFLUENT_TI_ARCH="${GSFLUENT_TI_ARCH:-cpu}"
run "cd '$GSFLUENT_HOME' && python gs_simulation/watermelon/gs_simulation_building.py ${SIM_FLAGS[*]}"

# --- step 2: fuse ------------------------------------------------------------
if [[ $LIVE -eq 1 ]]; then
    echo ""
    echo "=== step 2/3: live fuse already running (waiting for it to drain) ==="
    if [[ $DRY_RUN -eq 0 && -n "$FUSE_PID" ]]; then
        wait "$FUSE_PID" || true
        FUSE_PID=""  # consumed
    fi
elif [[ $SKIP_FUSE -eq 0 ]]; then
    echo ""
    echo "=== step 2/3: fuse to vk_plys ==="
    if [[ $DRY_RUN -eq 0 && ! -d "$SIM_PLY_DIR" ]]; then
        echo "ERROR: expected $SIM_PLY_DIR after sim, not found" >&2; exit 1
    fi
    mkdir -p "$FUSED_DIR"
    run "python '$TOOLS_DIR/fuse_to_full_ply.py' \
            --reference_ply '$REFERENCE_PLY' \
            --sim_dir '$SIM_PLY_DIR' \
            --out_dir '$FUSED_DIR'"
fi

# --- step 3: launch hint -----------------------------------------------------
if [[ $LIVE -eq 1 ]]; then
    echo ""
    if [[ $SKIP_VKGS_LAUNCH -eq 1 ]]; then
        echo "=== step 3/3: in-app run done ==="
        echo "  Frames written to: $FUSED_DIR"
    else
        echo "=== step 3/3: vkgs is already open ==="
        echo "  Close the vkgs window to exit. Frames are at:"
        echo "    $FUSED_DIR"
    fi
elif [[ $SKIP_VKGS_CMD -eq 0 && $SKIP_FUSE -eq 0 ]]; then
    cat <<EOF

=== step 3/3: open in vkgs ===

To play it back, run:

    ~/Desktop/work/vk_gaussian_splatting/_bin/vk_gaussian_splatting \\
        --frames_dir $FUSED_DIR

Or to compare with other cells in $VK_PLYS_DIR:

    ~/Desktop/work/vk_gaussian_splatting/_bin/vk_gaussian_splatting \\
        --cells_dir $VK_PLYS_DIR

EOF
fi

echo ""
echo "=== sim_one.sh: done ==="
