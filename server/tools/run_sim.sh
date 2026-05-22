#!/usr/bin/env bash
# Thin conda-activate shim. All orchestration moved to
# server/gsfluent/core/sim_engines/mpm.py - this script only handles
# the bash-context conda activation (which Python cannot do for itself)
# and hands the rest off to the Python entry point.
#
# CLI (unchanged from the prior 197-line version, so callers keep working):
#   bash run_sim.sh <model_dir> --config <recipe.json> \
#                   --particles N --output <run_name>
#
# Env contract (unchanged):
#   GSFLUENT_SIM_HOME    canonical GaussianFluent install root
#   GSFLUENT_SIM_PYTHON  python interpreter with torch / warp / taichi
#   GSFLUENT_SIM_ENV     optional conda env name
set -euo pipefail

if [[ -n "${GSFLUENT_SIM_ENV:-}" ]] && command -v conda >/dev/null 2>&1; then
    # shellcheck disable=SC1091
    eval "$(conda shell.bash hook)"
    conda activate "$GSFLUENT_SIM_ENV"
fi

PY="${GSFLUENT_SIM_PYTHON:-python}"
PKG_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

cd "$PKG_ROOT"
exec "$PY" -m gsfluent.core.sim_engines "$@"
