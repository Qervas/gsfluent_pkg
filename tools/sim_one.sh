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
# Fused output goes to work/fused/<run_name>/ — the canonical run dir
# where runner.py also writes manifest.json + run.log. Keeping all per-run
# artifacts in one directory means the WS pump finds frames + manifest +
# logs in the same place. (vk_plys/ used to be the destination but the
# split caused the workbench to subscribe and find no frames.)
VK_PLYS_DIR="$R7_DIR/work/fused"
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
# Phase-1 library layout: fused frames now land at
#   work/library/sequences/<run>/frames/frame_*.ply
# alongside _meta.json (and recipe.json copied from CONFIG_PATH).
# The legacy work/fused/<run>/ dir is still where runner.py writes
# manifest.json + run.log; we copy manifest into the library at the
# end of the run for the history endpoint to merge.
LIBRARY_SEQ_DIR="$R7_DIR/work/library/sequences/$OUTPUT_NAME"
LEGACY_FUSED_DIR="$VK_PLYS_DIR/$OUTPUT_NAME"
FUSED_DIR="$LIBRARY_SEQ_DIR/frames"

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
mkdir -p "$LIBRARY_SEQ_DIR" "$FUSED_DIR"
# Preserve the recipe early (before sim) so a crash doesn't lose it.
# Copy CONFIG_PATH (the recipe the user invoked) verbatim into the
# library entry as recipe.json.
if [[ $DRY_RUN -eq 0 && -f "$CONFIG_PATH" ]]; then
    cp -f "$CONFIG_PATH" "$LIBRARY_SEQ_DIR/recipe.json" || \
        echo "WARN: could not copy recipe to $LIBRARY_SEQ_DIR/recipe.json" >&2
fi
[[ $LIVE -eq 1 ]] && mkdir -p "$SIM_PLY_DIR" "$FUSED_DIR"

# --- live mode: spin up watchers BEFORE sim ---------------------------------
VKGS_PID=""; FUSE_PID=""
cleanup_live() {
    if [[ -n "$FUSE_PID" ]] && kill -0 "$FUSE_PID" 2>/dev/null; then
        echo "[live] stopping fuse (pid $FUSE_PID)..."
        kill "$FUSE_PID" 2>/dev/null || true
        wait "$FUSE_PID" 2>/dev/null || true
    fi
    if [[ -n "$VKGS_PID" ]]; then
        echo "[live] vkgs (pid $VKGS_PID) left running — close the window to exit it."
    fi
    # Force a successful exit status — the EXIT trap inherits the last
    # statement's exit code under `set -e`, and a `[[ -n "" ]]` short-
    # circuit (when VKGS_PID is empty under --no-vkgs-launch) returns 1,
    # poisoning the wrapper's exit code and making runner.py mark the
    # run as "error" despite a clean simulation. Always return 0.
    return 0
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
    # Extract frame_num from the recipe so fuse can exit as soon as it has
    # produced that many frames — without this, fuse sits idle on its 600s
    # watch_quiet_seconds timer after the sim is done, blocking sim_one.sh's
    # step 2/3 wait. Falls back to 0 (= no limit, watch_quiet_seconds applies).
    EXPECTED_FRAMES=$(python -c "import json,sys; print(json.load(open('$CONFIG_PATH')).get('frame_num', 0))" 2>/dev/null || echo 0)
    # Process substitution: fuse stdout/stderr is teed to fuse.log AND
    # piped through sed (line-buffered with -u) to prefix every line with
    # "[fuse] " before it hits the wrapper's stdout. The wrapper's stdout
    # is captured by the workbench's log pump, so fuse activity is now
    # visible in the React console live as frames are produced.
    # `$!` correctly returns python's PID (not tee/sed) because process
    # substitution attaches to python via FD redirection, not pipeline.
    python "$TOOLS_DIR/fuse_to_full_ply.py" \
        --reference_ply "$REFERENCE_PLY" \
        --sim_dir "$SIM_PLY_DIR" \
        --out_dir "$FUSED_DIR" \
        --watch --watch_quiet_seconds 30 \
        --max_frames "$EXPECTED_FRAMES" \
        --xyz_only_after_first \
        > >(tee "$SIM_OUTPUT_DIR/fuse.log" | sed -u 's/^/[fuse] /') 2>&1 &
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

# --- finalize: write library/sequences/<run>/_meta.json ----------------------
# Computes n_splats and bbox_initial from frame_0000.ply, infers model_ref
# from the model dir name. Best-effort: a write failure here doesn't fail
# the sim — frames + recipe.json are already on disk and reach playback.
if [[ $DRY_RUN -eq 0 && $BENCH -eq 0 ]]; then
    MODEL_NAME="$(basename "$MODEL_PATH")"
    python - "$LIBRARY_SEQ_DIR" "$MODEL_NAME" "$OUTPUT_NAME" <<'PY' || \
        echo "WARN: failed to write library _meta.json" >&2
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

seq_dir = Path(sys.argv[1])
model_ref = sys.argv[2]
name = sys.argv[3]

frames_dir = seq_dir / "frames"
frames = sorted(frames_dir.glob("frame_*.ply")) if frames_dir.is_dir() else []
frame_count = len(frames)

n_splats = None
bbox = None
if frames:
    try:
        from plyfile import PlyData
        v = PlyData.read(str(frames[0]))["vertex"].data
        n_splats = int(v.shape[0])
        if n_splats > 0:
            xs = v["x"].astype(float)
            ys = v["y"].astype(float)
            zs = v["z"].astype(float)
            bbox = [
                [float(xs.min()), float(ys.min()), float(zs.min())],
                [float(xs.max()), float(ys.max()), float(zs.max())],
            ]
    except Exception as e:
        sys.stderr.write(f"WARN: bbox/count read failed: {e}\n")

payload = {
    "name": name,
    "kind": "sequence",
    "source": "sim",
    "source_path": None,
    "model_ref": model_ref,
    "frame_count": frame_count,
    "fps_hint": 24,
    "n_splats": n_splats,
    "bbox_initial": bbox,
    "coord_convention": "z-up",
    "first_frame_full": True,
    "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
}
meta_path = seq_dir / "_meta.json"
tmp = meta_path.with_suffix(".json.tmp")
tmp.write_text(json.dumps(payload, indent=2))
tmp.replace(meta_path)
print(f"wrote {meta_path}")
PY

    # Copy manifest.json from the runner's location into the library entry
    # so /api/runs/history can merge particles + recipe_source + status
    # alongside _meta.json. Best-effort; a sim launched outside the runner
    # (no runner-written manifest) just skips this step.
    if [[ -f "$LEGACY_FUSED_DIR/manifest.json" ]]; then
        cp -f "$LEGACY_FUSED_DIR/manifest.json" "$LIBRARY_SEQ_DIR/manifest.json" 2>/dev/null || true
    fi
fi

echo ""
echo "=== sim_one.sh: done ==="
