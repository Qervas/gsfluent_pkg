#!/usr/bin/env bash
# Server-side one-time setup. Run on your-server (or whichever host has
# the canonical sim core).
#
# What this does:
#   1. pip-installs the gsfluent package (FastAPI + REST/WS + SPA serving).
#      The sim dependencies (torch/warp/taichi) are NOT installed by this
#      script — they're already present in the canonical sim env.
#   2. builds the React SPA and copies it into server/gsfluent/static/ so
#      `gsfluent serve` serves the SPA at /.
#
# Pre-reqs:
#   - python3 (the same one that has the sim deps, ideally)
#   - npm (Node 18+ recommended) for the SPA build
#
# Usage:
#   ./setup-server.sh                    # default python3 + npm on PATH
#   PYTHON=python3.11 ./setup-server.sh  # specific python

set -euo pipefail

PKG_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PYTHON:-python3}"

note() { echo ">>> $*"; }
err()  { echo "ERROR: $*" >&2; exit 1; }

command -v "$PY" >/dev/null 2>&1 || err "$PY not found on PATH"
note "using python: $($PY -V)  ($(command -v "$PY"))"

note "installing gsfluent server (FastAPI + plyfile + numpy + pydantic + watchfiles)"
"$PY" -m pip install -e "$PKG_ROOT/server"

if ! command -v gsfluent >/dev/null 2>&1; then
    err "post-install: 'gsfluent' console script not on PATH. Check pip install output."
fi
note "gsfluent CLI at: $(command -v gsfluent)"

note "building React SPA"
if ! command -v npm >/dev/null 2>&1; then
    note "  npm not on PATH — skipping SPA build."
    note "  Install Node 18+ and re-run, or build elsewhere and copy:"
    note "    cd frontend && npm install && npm run build"
    note "    cp -r dist/* server/gsfluent/static/"
else
    (
        cd "$PKG_ROOT/frontend"
        npm install
        npm run build
    )
    mkdir -p "$PKG_ROOT/server/gsfluent/static"
    rm -rf "$PKG_ROOT/server/gsfluent/static"/*
    cp -r "$PKG_ROOT/frontend/dist/." "$PKG_ROOT/server/gsfluent/static/"
    note "SPA copied to $PKG_ROOT/server/gsfluent/static/"
fi

note "done."
echo ""
echo "Next:"
echo "  ./run-server.sh                 # start the backend on :8080"
echo ""
echo "From the laptop:"
echo "  GSFLUENT_SERVER=http://$(hostname):8080 ./run-laptop.sh"
