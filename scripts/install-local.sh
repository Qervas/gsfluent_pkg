#!/usr/bin/env bash
# Laptop-local installer.
#
# Strong split: only the API stays on your-server. The SPA + viser_headless
# run on this laptop, talking to the shared backend at
# http://your-backend:port (NAT-mapped → v2 api on :7869).
#
# Two commands, total. This is one. The other is scripts/start-local.sh.
#
# What this does:
#   1. Creates a Python venv at .venv/  (no sudo, no conda)
#   2. Installs viser+uvicorn+fastapi+httpx+eval_type_backport via pip
#   3. Runs `npm ci && npm run build` so frontend/dist/ is ready
#
# Pre-reqs (no install attempted, just probed):
#   - Python 3.10+ on PATH (for venv + viser)
#   - node 18+ + npm on PATH (for the SPA build)
#
# Usage:
#   bash scripts/install-local.sh
#
# Idempotent — safe to re-run after a `git pull`.

set -euo pipefail

PKG_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$PKG_ROOT/.venv"

note() { echo ">>> $*"; }
err()  { echo "ERROR: $*" >&2; exit 1; }

# ---- 1/3: python preflight ----------------------------------------------

# `python3` should resolve to a 3.10+ interpreter on a modern distro. If
# the user's default python3 is older, we ask them to point at a newer
# one with PYTHON_BIN=/path/to/python3.11 — no fancy detection.
PYTHON_BIN="${PYTHON_BIN:-python3}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    err "python3 not on PATH. Install Python 3.10+ or set PYTHON_BIN=/path/to/python3."
fi
PY_VER="$("$PYTHON_BIN" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
note "python: $PYTHON_BIN ($PY_VER)"
"$PYTHON_BIN" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
    || err "Python 3.10+ required; got $PY_VER. Set PYTHON_BIN=/path/to/python3.10 (or newer)."

# ---- 2/3: venv + python deps -------------------------------------------

if [[ ! -d "$VENV_DIR" ]]; then
    note "creating venv at $VENV_DIR"
    "$PYTHON_BIN" -m venv "$VENV_DIR"
fi
VENV_PY="$VENV_DIR/bin/python"
[[ -x "$VENV_PY" ]] || err "venv creation failed; expected $VENV_PY"

note "upgrading pip in venv (quiet)"
"$VENV_PY" -m pip install --quiet --upgrade pip

# viser 1.x pinned to match tools/viser_headless.py's import surface
# (GaussianSplatHandle, on_client_connect, initial_camera).
# fastapi+uvicorn power the sidecar control API at :8092.
# httpx isn't used by viser_headless itself today but is the obvious
# dependency for any future "tiny laptop-side proxy" — bundling now
# keeps a future scripts/* change from triggering another pip install.
# eval_type_backport is the runtime backport viser pulls in on 3.10/3.11
# for `from __future__ import annotations` introspection; listing it
# explicitly avoids viser refusing to start with an obscure pydantic
# error if the transitive dep ever gets pruned.
note "installing python deps into venv"
"$VENV_PY" -m pip install --quiet \
    'viser>=1.0,<2' \
    'numpy>=1.24' \
    'fastapi>=0.110' \
    'uvicorn[standard]>=0.30' \
    'httpx>=0.27' \
    'eval_type_backport>=0.2'

"$VENV_PY" - <<'PYEOF'
from importlib.metadata import version
for pkg in ("viser", "numpy", "fastapi", "uvicorn", "httpx"):
    try:
        print(f"  {pkg:8s} {version(pkg)}")
    except Exception as exc:  # noqa: BLE001
        print(f"  {pkg:8s} MISSING ({exc})")
PYEOF

# ---- 3/3: SPA build ----------------------------------------------------

command -v npm >/dev/null 2>&1 || err "npm not on PATH (install Node 18+)"
NODE_VER="$(node --version)"
NPM_VER="$(npm --version)"
note "node: $NODE_VER   npm: $NPM_VER"

note "installing frontend npm deps (npm ci — honors lockfile)"
(cd "$PKG_ROOT/frontend" && npm ci --no-fund --no-audit)

note "building React SPA into frontend/dist/"
(cd "$PKG_ROOT/frontend" && npm run build)

[[ -f "$PKG_ROOT/frontend/dist/index.html" ]] \
    || err "build finished but frontend/dist/index.html is missing — check npm output"

# ---- bootstrap: placeholder npz so viser_headless can start fresh ------
# viser_headless refuses to start if its --npz_dir has no .npz files (it
# pre-mmaps the cell index). On a fresh laptop the cache is empty, so we
# drop in a one-splat placeholder that satisfies the loader. The real
# sequences are lazy-loaded on demand when the SPA's outliner picks one,
# so the placeholder never appears in the workbench — it's just enough
# to get viser past its empty-dir guard.
#
# Naming starts with `_` so it sorts before real sequences in viser's
# internal cell list, and the leading underscore is rejected by the
# server's sequence-name regex (no risk of clashing with a real cell).

VISER_NPZ_DIR="$PKG_ROOT/work/cache/viser"
mkdir -p "$VISER_NPZ_DIR"
if ! compgen -G "$VISER_NPZ_DIR/*.npz" >/dev/null; then
    note "writing placeholder .npz so viser_headless can start on an empty cache"
    "$VENV_PY" - "$VISER_NPZ_DIR/_placeholder.npz" <<'PYEOF'
import sys
from pathlib import Path

import numpy as np

dest = Path(sys.argv[1])
n_frames, n_splats = 1, 1
# One invisible splat at the origin — opacity 0 so even if viser auto-
# focuses the placeholder before the user picks a real cell, there's
# nothing visually distracting.
np.savez(
    dest,
    frames=np.zeros((n_frames, n_splats, 3), dtype=np.float32),
    cov=np.eye(3, dtype=np.float32)[None].repeat(n_splats, axis=0),
    rgb=np.zeros((n_splats, 3), dtype=np.float32),
    opacity=np.zeros((n_splats,), dtype=np.float32),
)
print(f"  wrote {dest}")
PYEOF
fi

note "done."
echo ""
echo "Next:"
echo "  bash scripts/start-local.sh"
