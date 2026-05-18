#!/usr/bin/env bash
# Server-side launcher — runs on the box that owns the sim core + GPUs.
#
# Brings up the gsfluent API on $API_PORT (FastAPI: REST + /api/stream
# WS for live runs). The sim itself is spawned per-request by
# runner.py via tools/run_sim.sh. The viser splat renderer + sync
# daemon + Points WS live on the CLIENT side under run-client.sh —
# strong split, this script never serves a SPA.
#
# Required env vars (no defaults — set in .env or your shell):
#   GSFLUENT_SIM_HOME         path to GaussianFluent source tree
#   GSFLUENT_SIM_PYTHON       Python with torch + warp + taichi
#
# Optional env vars:
#   API_PORT                  default 8080
#   GSFLUENT_SIM_ENV          conda env name to activate for sims
#   GSFLUENT_SIM_SCRIPT_RUNNER override the wrapper script
#                              (default <PKG_ROOT>/tools/run_sim.sh)
#
# First-time setup on a new server:
#   ./setup-server.sh
#   cp .env.example .env   # then edit .env to point at your sim install

set -euo pipefail

PKG_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
API_PORT="${API_PORT:-8080}"

# Source local .env if present (gitignored; per-deploy config).
if [[ -f "$PKG_ROOT/.env" ]]; then
    # shellcheck disable=SC1091
    set -a; source "$PKG_ROOT/.env"; set +a
fi

# Required vars — fail loud rather than silently using a your-server-specific default.
if [[ -z "${GSFLUENT_SIM_HOME:-}" ]] || [[ -z "${GSFLUENT_SIM_PYTHON:-}" ]]; then
    cat >&2 <<EOF
ERROR: GSFLUENT_SIM_HOME and/or GSFLUENT_SIM_PYTHON not set.

Fix: copy .env.example to .env and fill in your paths.
  cd $PKG_ROOT
  cp .env.example .env
  \$EDITOR .env
  ./run-server.sh
EOF
    exit 1
fi

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
>>> sim home:           $GSFLUENT_SIM_HOME
>>> sim python:         $GSFLUENT_SIM_PYTHON
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
