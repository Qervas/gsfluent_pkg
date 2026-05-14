#!/usr/bin/env bash
# gsfluent_pkg — laptop-only setup (view + drive the workbench).
#
# Sim runs on the server (sxyin-host). The laptop's job is:
#   - serve the React SPA + REST/WS gateway (`gsfluent serve`)
#   - run the viser splat viewer (`tools/viser_headless.py`)
#   - drive remote sim submissions via SSH (future)
#
# That's pure-Python territory: no CUDA, no torch, no warp/taichi, no conda
# env needed. This script just pip-installs the seven light deps into
# whatever `python` is on PATH (system python, venv, conda base — your call).
#
# Usage:
#   ./setup-view.sh                # install into current python
#   PYTHON=python3.11 ./setup-view.sh
#
# To use the FULL sim stack on this machine (rare — only if you're also
# doing local sim development): run `./setup.sh` instead.

set -euo pipefail

PKG_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PYTHON:-python3}"

note() { echo ">>> $*"; }
err()  { echo "ERROR: $*" >&2; exit 1; }

command -v "$PY" >/dev/null 2>&1 || err "$PY not found on PATH"
note "using python: $($PY -V)  ($(command -v "$PY"))"

note "installing gsfluent (light deps: fastapi, uvicorn, plyfile, pydantic, …)"
"$PY" -m pip install -e "$PKG_ROOT/server"

note "installing viser + numpy"
# viser is pinned <2 — the 1.x API (initial_camera, GaussianSplatHandle,
# on_client_connect) is what tools/viser_headless.py depends on. A 2.x
# release may rename or remove those. Re-evaluate when 2.x stabilizes.
"$PY" -m pip install "viser>=1.0,<2" numpy

note "verifying imports…"
"$PY" - <<'PYEOF'
import gsfluent, viser, numpy, fastapi, uvicorn, plyfile, pydantic, watchfiles
print(f"  gsfluent      {gsfluent.__version__ if hasattr(gsfluent, '__version__') else 'ok'}")
print(f"  viser         {viser.__version__}")
print(f"  numpy         {numpy.__version__}")
print(f"  fastapi       {fastapi.__version__}")
print(f"  plyfile       {plyfile.__version__}")
PYEOF

note "checking for built SPA…"
if [[ -d "$PKG_ROOT/server/gsfluent/static" ]] && \
   [[ -n "$(ls -A "$PKG_ROOT/server/gsfluent/static" 2>/dev/null)" ]]; then
    note "  SPA present at server/gsfluent/static/"
else
    note "  no built SPA. For dev mode (HMR) skip this; for prod:"
    note "    cd frontend && npm install && npm run build"
    note "    cp -r frontend/dist/* server/gsfluent/static/"
fi

note "done."
echo ""
echo "Next:"
echo "  GSFLUENT_SERVER=http://<server>:8080 ./run-laptop.sh"
echo ""
echo "On the server side (sxyin-host or wherever the sim core lives):"
echo "  ./setup-server.sh && ./run-server.sh"
