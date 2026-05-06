#!/usr/bin/env bash
# gsfluent_pkg — play pre-fused frames in the browser.
#
# Usage:
#   ./run-viewer.sh <frames_dir> [--port 8080]
#
# <frames_dir>: a directory of frame_NNNN.ply files (output of fuse_to_full_ply.py
# or any sim_one.sh / run-sim.sh run). The viewer renders them as a point cloud.

set -euo pipefail

PKG_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_NAME="${GSFLUENT_ENV:-gsfluent}"
PORT=8080

usage() { sed -n '2,/^# is a directory/p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'; }

FRAMES_DIR=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help) usage; exit 0 ;;
        --port) PORT="$2"; shift 2 ;;
        -*) echo "unknown: $1" >&2; usage; exit 2 ;;
        *) FRAMES_DIR="$1"; shift ;;
    esac
done

[[ -z "$FRAMES_DIR" ]] && { echo "ERROR: frames dir required"; usage; exit 2; }
[[ -d "$FRAMES_DIR" ]] || { echo "ERROR: $FRAMES_DIR is not a directory"; exit 1; }

eval "$(conda shell.bash hook)"
conda activate "$ENV_NAME"

echo ">>> serving $FRAMES_DIR at http://localhost:$PORT"
exec python "$PKG_ROOT/tools/view_points.py" --sim_dir "$FRAMES_DIR" --port "$PORT"
