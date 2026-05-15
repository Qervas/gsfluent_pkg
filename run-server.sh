#!/usr/bin/env bash
# Server-side launcher — runs on the box that owns the sim core + GPUs.
#
# Brings up the gsfluent API on $API_PORT (FastAPI: REST + /api/stream
# WS for live runs). The sim itself is spawned per-request by
# runner.py via tools/run_sim.sh. The viser splat renderer + sync
# daemon + Points WS live on the CLIENT side under run-client.sh —
# strong split, this script never serves a SPA.
#
# Environment overrides (set in shell or systemd unit):
#   API_PORT                  default 8080
#   GSFLUENT_SIM_HOME         default /data/yinshaoxuan/GaussianFluent
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

if [[ ! -x "$PKG_ROOT/server/.venv/bin/gsfluent" ]]; then
    cat >&2 <<EOF
ERROR: server/.venv/bin/gsfluent not found.

Run ./setup-server.sh first; it creates the uv-managed venv and
installs the gsfluent CLI from server/uv.lock.
EOF
    exit 1
fi

cat <<EOF
>>> gsfluent server on :$API_PORT
>>> sim home:           ${GSFLUENT_SIM_HOME:-/data/yinshaoxuan/GaussianFluent}
>>> sim python:         ${GSFLUENT_SIM_PYTHON:-python (PATH)}
>>> sim conda env:      ${GSFLUENT_SIM_ENV:-<inherit caller env>}
>>> sim wrapper:        ${GSFLUENT_SIM_SCRIPT_RUNNER:-$PKG_ROOT/tools/run_sim.sh}
>>> client connects via:
>>>     ssh -N -L $API_PORT:localhost:$API_PORT $(hostname)
>>>   or just:
>>>     SERVER_SSH=$(hostname) ./run-client.sh   # on the client
EOF

# `uv run` re-syncs the venv before invoking gsfluent, so an out-of-date
# lockfile is caught here rather than silently running stale code.
exec uv run --project "$PKG_ROOT/server" gsfluent serve \
    --host 0.0.0.0 --port "$API_PORT" --no-browser
