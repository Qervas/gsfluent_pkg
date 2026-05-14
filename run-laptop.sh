#!/usr/bin/env bash
# Laptop-side launcher — runs on your local machine.
#
# Brings up the three local services + opens the workbench in a browser
# pointed at the server's SPA. All three are pure-Python (no CUDA, no
# torch — laptop is viewer-only):
#
#   1. tools/viser_headless.py  → viser :8091 + control API :8092 (Splats mode)
#   2. tools/sync_daemon.py     → mirrors server's .npz cache + frames.bin locally
#   3. tools/local_stream.py    → ws :8083  (Points mode, mmaps the synced .npz)
#
# Required: $GSFLUENT_SERVER must point at the running server, e.g.
#   GSFLUENT_SERVER=http://your-server:8080 ./run-laptop.sh
# or via SSH tunnel (no LAN):
#   ssh -L 8080:localhost:8080 your-server &
#   GSFLUENT_SERVER=http://localhost:8080 ./run-laptop.sh
#
# Optional env:
#   VISER_PORT       default 8091
#   CONTROL_PORT     default 8092
#   STREAM_PORT      default 8083
#   POLL_INTERVAL    default 10 (seconds)
#   OPEN_BROWSER     default 1 (set 0 to skip xdg-open)
#
# First-time setup:
#   ./setup-view.sh
set -euo pipefail

PKG_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PYTHON:-python3}"

if [[ -z "${GSFLUENT_SERVER:-}" ]]; then
    cat >&2 <<EOF
ERROR: \$GSFLUENT_SERVER not set.

Either:
    GSFLUENT_SERVER=http://your-server:8080 ./run-laptop.sh
or set it once in your shell rc.
EOF
    exit 2
fi
SERVER="${GSFLUENT_SERVER%/}"

VISER_PORT="${VISER_PORT:-8091}"
CONTROL_PORT="${CONTROL_PORT:-8092}"
STREAM_PORT="${STREAM_PORT:-8083}"
POLL_INTERVAL="${POLL_INTERVAL:-10}"
CACHE_ROOT="$PKG_ROOT/work/cache"
OPEN_BROWSER="${OPEN_BROWSER:-1}"

# Preflight: pure-Python deps.
if ! "$PY" -c "import viser, numpy, fastapi, uvicorn, plyfile, pydantic" 2>/dev/null; then
    cat >&2 <<EOF
ERROR: missing laptop deps in '$PY'.

This workbench is pure-Python on the laptop (no CUDA, no torch). First-time:
    ./setup-view.sh
or for a specific python:
    PYTHON=python3.11 ./setup-view.sh

Then re-run this script.
EOF
    exit 1
fi

mkdir -p "$CACHE_ROOT/viser" "$CACHE_ROOT/frames-bin"

# PID tracking + cleanup so ctrl-C takes the whole stack down.
PIDS=()
cleanup() {
    echo
    echo ">>> shutting down…"
    for pid in "${PIDS[@]}"; do
        if kill -0 "$pid" 2>/dev/null; then kill "$pid" 2>/dev/null || true; fi
    done
    sleep 2
    for pid in "${PIDS[@]}"; do
        if kill -0 "$pid" 2>/dev/null; then kill -9 "$pid" 2>/dev/null || true; fi
    done
}
trap cleanup EXIT INT TERM

echo ">>> server:          $SERVER"
echo ">>> cache root:      $CACHE_ROOT"

# Stage 1: viser headless (Splats mode renderer)
echo ">>> viser_headless  :$VISER_PORT   (control :$CONTROL_PORT)"
"$PY" "$PKG_ROOT/tools/viser_headless.py" \
    --npz_dir "$CACHE_ROOT/viser" \
    --viser_port "$VISER_PORT" \
    --control_port "$CONTROL_PORT" &
PIDS+=($!)

# Stage 2: sync daemon (server → laptop cache mirror)
echo ">>> sync_daemon     polling every ${POLL_INTERVAL}s"
"$PY" "$PKG_ROOT/tools/sync_daemon.py" \
    --server "$SERVER" \
    --cache-root "$CACHE_ROOT" \
    --viser-control "http://localhost:$CONTROL_PORT" \
    --interval "$POLL_INTERVAL" \
    --verbose &
PIDS+=($!)

# Stage 3: local Points stream (mmap'd .npz over ws://localhost)
echo ">>> local_stream    ws://localhost:$STREAM_PORT/api/stream"
"$PY" "$PKG_ROOT/tools/local_stream.py" \
    --cache-root "$CACHE_ROOT" \
    --port "$STREAM_PORT" \
    --host 127.0.0.1 &
PIDS+=($!)

# Open the SPA in a browser, pointed at the server.
if [[ "$OPEN_BROWSER" -eq 1 ]]; then
    # Wait a beat for the local services to bind their ports so the SPA's
    # initial fetches don't race a not-yet-ready local stream.
    sleep 2
    if command -v xdg-open >/dev/null 2>&1; then
        xdg-open "$SERVER/" >/dev/null 2>&1 || true
    elif command -v open >/dev/null 2>&1; then
        open "$SERVER/" >/dev/null 2>&1 || true
    fi
fi

echo ""
echo ">>> all three local services running. Ctrl-C to stop them all."
echo ">>> sync status:    /tmp/gsfluent_sync_status.json"

# Wait on the first child to exit; the trap then takes down the rest.
wait -n
