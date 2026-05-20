#!/usr/bin/env bash
# Server-side one-time setup.
#
# Installs the gsfluent API package into a uv-managed virtualenv at
# server/.venv/, pinned to the versions in server/uv.lock so every
# install is byte-identical. No Node, no SPA, no GPU deps here: the
# server is a pure API + sim runner under the strong frontend/backend
# split.
#
# Pre-reqs (all checked below):
#   - python (>= 3.10) somewhere on PATH (uv will pick it up)
#   - uv (https://docs.astral.sh/uv/) — the install line is printed if missing
#
# Usage:
#   ./setup-server.sh

set -euo pipefail

PKG_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

note() { echo ">>> $*"; }
err()  { echo "ERROR: $*" >&2; exit 1; }

# ---- 1/2: uv preflight ----
if ! command -v uv >/dev/null 2>&1; then
    cat >&2 <<EOF
ERROR: uv not found on PATH.

uv is the Python package manager this project uses for reproducible
installs (https://docs.astral.sh/uv/). Install it once with:

    curl -LsSf https://astral.sh/uv/install.sh | sh

then re-run this script. uv is a single static binary — no Python
needed to install it.
EOF
    exit 1
fi
note "uv: $(uv --version)"

# ---- 2/2: sync ----
# `uv sync --frozen` reads server/uv.lock and produces server/.venv
# with exactly the locked versions. --frozen refuses to re-resolve
# (and thus refuses to rewrite uv.lock) so byte-identical installs
# are guaranteed across machines. Idempotent: no-op when nothing changed.
note "syncing server dependencies into server/.venv/ (from uv.lock)"
(cd "$PKG_ROOT/server" && uv sync --frozen)

# Sanity probe the console script.
if ! "$PKG_ROOT/server/.venv/bin/gsfluent" --help >/dev/null 2>&1; then
    err "post-install: 'gsfluent' console script in server/.venv/bin/ isn't runnable. Check uv sync output."
fi
note "gsfluent CLI ready: $PKG_ROOT/server/.venv/bin/gsfluent"

note "done."
echo ""
echo "Next:"
echo "  ./run-server.sh                 # start the API on :8080"
echo ""
echo "On the client machine:"
echo "  SERVER_SSH=$(hostname) ./run-client.sh"
