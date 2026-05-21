#!/usr/bin/env bash
# Apply our local viser patch + rebuild its bundled client.
#
# Why we patch: viser's vertex shader has two early-return culls that
# drop renderable splats by camera-angle (a `lambda2<0` pos-def guard
# and a `weightedDeterminant<0.25` perf cull). On 3DGS reconstructions
# with many anisotropic / low-opacity splats, those produce visible
# region-shaped "winking" as the camera orbits. The patch in
# frontend/patches/viser-no-cull.patch comments both out — see that file for
# the full rationale.
#
# Why it needs a script: viser ships a pre-built client bundle.
# Editing the .ts source has no effect until we `npm run build`
# inside the installed viser/client/. We do both here.
#
# Idempotent — re-running detects the patch is already applied and
# skips the diff step. Safe to call from setup-client.sh after every
# `uv sync`.
#
# Usage:
#   ./frontend/patches/patch-viser.sh                  # auto-detect venv
#   VIRTUAL_ENV=/path/to/.venv ./frontend/patches/patch-viser.sh
#
# Pre-reqs: patch, node, npm.

set -euo pipefail

# Locate the repo root (script lives at frontend/patches/, so `..\..`).
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG_ROOT="$(cd "$HERE/../.." && pwd)"
PATCH_FILE="$HERE/viser-no-cull.patch"

note() { echo ">>> patch-viser: $*"; }
err()  { echo "ERROR: patch-viser: $*" >&2; exit 1; }

[[ -f "$PATCH_FILE" ]] || err "patch file not found: $PATCH_FILE"

# Find the installed viser client dir. The client-local install puts
# its venv at <repo>/.venv (created by frontend/scripts/install.mjs).
VISER_CLIENT=""
if [[ -d "$PKG_ROOT/.venv" ]]; then
    for candidate in "$PKG_ROOT/.venv"/lib/python3.*/site-packages/viser/client; do
        if [[ -d "$candidate" ]]; then
            VISER_CLIENT="$candidate"
            break
        fi
    done
fi
if [[ -z "$VISER_CLIENT" ]]; then
    # Probe whichever python is on PATH (or in VIRTUAL_ENV).
    VISER_CLIENT="$(python3 -c '
import importlib.util, pathlib
spec = importlib.util.find_spec("viser")
if spec and spec.origin:
    print(pathlib.Path(spec.origin).parent / "client")
' 2>/dev/null || true)"
fi
[[ -n "$VISER_CLIENT" && -d "$VISER_CLIENT" ]] || \
    err "viser client dir not found. Did you run setup-client.sh first?"

SHADER="$VISER_CLIENT/src/Splatting/GaussianSplatsHelpers.ts"
[[ -f "$SHADER" ]] || err "expected shader at $SHADER — viser layout changed?"

note "viser client at $VISER_CLIENT"

# ---- 1. apply the source patch (idempotent) ----
# `--dry-run` lets us check if the patch would apply cleanly. If the
# patch is already applied, --dry-run reports "Reversed (or previously
# applied) patch detected" and we skip the real apply step.
if patch --dry-run -p1 -d "$(dirname "$SHADER")" -i "$PATCH_FILE" >/dev/null 2>&1; then
    note "applying source patch"
    patch -p1 -d "$(dirname "$SHADER")" -i "$PATCH_FILE"
elif grep -q "Local patch (gsfluent)" "$SHADER"; then
    note "patch already applied to source — skipping"
else
    err "patch doesn't apply and our marker isn't in the source. "\
"Either viser was updated (re-pin and regenerate the patch) or the "\
"source is in an unexpected state."
fi

# ---- 2. rebuild the client bundle ----
# vite emits build/index.html as a single inlined file. If we don't
# rebuild, the browser still loads the OLD bundle and sees the cull.
if [[ ! -d "$VISER_CLIENT/node_modules" ]]; then
    note "installing viser client npm deps (one-time, ~14s)"
    (cd "$VISER_CLIENT" && npm install --no-fund --no-audit) >/dev/null
fi

note "rebuilding viser client bundle"
(cd "$VISER_CLIENT" && npm run build 2>&1) | tail -3

note "done. The patched bundle is at $VISER_CLIENT/build/index.html"
note "viser_headless on next launch will serve it to the browser."
