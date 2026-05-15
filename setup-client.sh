#!/usr/bin/env bash
# Client-side one-time setup.
#
# Installs both halves of the client stack:
#
#   1. Python tools (viser splat renderer + sync_daemon + Points WS),
#      managed in server/.venv by uv. Same lockfile as the server, just
#      with the [client] extras activated — so viser+numpy come along
#      and the server install stays lean.
#   2. React SPA, built into frontend/dist/ via npm + vite. Served from
#      the client at runtime (the server never hosts the SPA — strong
#      frontend/backend split).
#
# Pre-reqs (all checked below):
#   - uv (https://docs.astral.sh/uv/) — single static binary; install
#     line is printed if missing.
#   - node + npm (Node 18+ recommended) for the SPA build.
#
# Usage:
#   ./setup-client.sh

set -euo pipefail

PKG_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

note() { echo ">>> $*"; }
err()  { echo "ERROR: $*" >&2; exit 1; }

# ---- 1/3: uv preflight ----
if ! command -v uv >/dev/null 2>&1; then
    cat >&2 <<EOF
ERROR: uv not found on PATH.

uv is the Python package manager this project uses for reproducible
installs (https://docs.astral.sh/uv/). Install it once with:

    curl -LsSf https://astral.sh/uv/install.sh | sh

then re-run this script.
EOF
    exit 1
fi
note "uv: $(uv --version)"

# ---- 2/3: python tools via uv ----
# `--extra client` activates the optional-deps group in pyproject.toml
# that pulls viser + numpy. uv.lock is the same file the server uses;
# we just install a superset of its rows here.
note "syncing client dependencies into server/.venv/ (from uv.lock, with [client] extras)"
(cd "$PKG_ROOT/server" && uv sync --extra client)

VENV_PY="$PKG_ROOT/server/.venv/bin/python"
"$VENV_PY" - <<'PYEOF'
from importlib.metadata import version
import viser, numpy, fastapi, plyfile  # noqa: F401 (import-side validates install)
for pkg in ("viser", "numpy", "fastapi", "plyfile"):
    print(f"  {pkg:8s} {version(pkg)}")
PYEOF

# ---- 3/3: SPA build ----
command -v npm >/dev/null 2>&1 || err "npm not on PATH (install Node 18+)"
note "node: $(node --version)   npm: $(npm --version)"

note "installing frontend npm deps"
(cd "$PKG_ROOT/frontend" && npm install --no-fund --no-audit)

note "building React SPA into frontend/dist/"
(cd "$PKG_ROOT/frontend" && npm run build)

if [[ ! -f "$PKG_ROOT/frontend/dist/index.html" ]]; then
    err "build finished but frontend/dist/index.html is missing — check npm log"
fi
note "SPA built at $PKG_ROOT/frontend/dist/"

# ---- 4/3: apply local viser shader patch ----
# viser ships its own bundled client with two over-aggressive vertex-
# shader culls that drop renderable splats by camera angle. We patch
# the source + rebuild the bundle so the workbench's Splats render
# mode doesn't show view-dependent "winking" regions. Idempotent —
# safe to re-run after every uv sync. See patches/viser-no-cull.patch
# for the full diff + rationale.
note "applying viser shader patch + rebuilding its client bundle"
"$PKG_ROOT/tools/patch-viser.sh"

note "done."
echo ""
echo "Next:"
echo "  SERVER_SSH=<server-host> ./run-client.sh"
echo ""
echo "On the server (one-time):"
echo "  ssh <server-host>"
echo "  cd gsfluent_pkg && ./setup-server.sh"
