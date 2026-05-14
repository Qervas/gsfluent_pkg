#!/usr/bin/env bash
# Server-side launcher — runs on your-server (or whichever box has the
# canonical sim core + library data).
#
# Brings up:
#   - `gsfluent serve` on $API_PORT (FastAPI: REST + SPA + /api/stream WS for live runs)
# Sim itself is spawned per-request by runner.py via tools/run_sim.sh.
# The viser splat renderer + sync daemon + Points WS run on the LAPTOP
# side via run-laptop.sh, NOT here.
#
# Environment overrides (set in shell or systemd unit):
#   API_PORT                  default 8080
#   GSFLUENT_SIM_HOME         default $GSFLUENT_SIM_HOME
#   GSFLUENT_SIM_PYTHON       default `python` (caller's env)
#   GSFLUENT_SIM_ENV          conda env name to activate for sims (optional)
#   GSFLUENT_SIM_SCRIPT_RUNNER override the wrapper script
#                              (default <PKG_ROOT>/tools/run_sim.sh)
#
# First-time setup on a new server:
#   ./setup-server.sh

set -euo pipefail

PKG_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
API_PORT="${API_PORT:-8080}"

if ! command -v gsfluent >/dev/null 2>&1; then
    echo "ERROR: 'gsfluent' console script not on PATH." >&2
    echo "       Run ./setup-server.sh first." >&2
    exit 1
fi

cat <<EOF
>>> gsfluent server on :$API_PORT
>>> sim home:           ${GSFLUENT_SIM_HOME:-$GSFLUENT_SIM_HOME}
>>> sim python:         ${GSFLUENT_SIM_PYTHON:-python (PATH)}
>>> sim conda env:      ${GSFLUENT_SIM_ENV:-<inherit caller env>}
>>> sim wrapper:        ${GSFLUENT_SIM_SCRIPT_RUNNER:-$PKG_ROOT/tools/run_sim.sh}
>>> open http://$(hostname):$API_PORT  (or via SSH tunnel for off-LAN access)
EOF

exec gsfluent serve --host 0.0.0.0 --port "$API_PORT" --no-browser
