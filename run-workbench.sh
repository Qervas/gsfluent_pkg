#!/usr/bin/env bash
# gsfluent_pkg — open the unified browser workbench.
#
# Usage:
#   ./run-workbench.sh [--port 8080]
#
# A single page where the team uploads a 3DGS .ply, picks a recipe,
# tweaks parameters with sliders, clicks Run, and watches the building
# deform live in the same page. No CLI knowledge required after first
# install.

set -euo pipefail

PKG_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_NAME="${GSFLUENT_ENV:-gsfluent}"
PORT=8080

while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help) sed -n '2,/^# install/p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'; exit 0 ;;
        --port) PORT="$2"; shift 2 ;;
        *) echo "unknown: $1" >&2; exit 2 ;;
    esac
done

eval "$(conda shell.bash hook)"
if ! conda env list | awk '{print $1}' | grep -qx "$ENV_NAME"; then
    echo "ERROR: conda env '$ENV_NAME' not found. Run ./setup.sh first." >&2
    exit 1
fi
conda activate "$ENV_NAME"

echo ">>> opening workbench at http://localhost:$PORT"
exec python "$PKG_ROOT/tools/workbench.py" --pkg-root "$PKG_ROOT" --port "$PORT" --env "$ENV_NAME"
