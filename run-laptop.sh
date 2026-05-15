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
# Two ways to point at the server:
#
#   (A) Auto-tunnel (one command):
#       SERVER_SSH=your-server ./run-laptop.sh
#       → opens `ssh -N -L 8080:localhost:8080 $SERVER_SSH` for you and
#         points everything at localhost:8080. Tunnel dies on ctrl-C.
#
#   (B) Manual: bring your own $GSFLUENT_SERVER (existing tunnel, LAN, etc.):
#       GSFLUENT_SERVER=http://your-server:8080 ./run-laptop.sh
#
# Optional env:
#   SERVER_SSH       SSH host alias for auto-tunnel (mode A)
#   LOCAL_PORT       default 8080      laptop side of the tunnel
#   REMOTE_PORT      default 8080      server side of the tunnel (matches API_PORT)
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

LOCAL_PORT="${LOCAL_PORT:-8080}"
REMOTE_PORT="${REMOTE_PORT:-8080}"

# Mode A: auto-tunnel via SERVER_SSH (host alias). We open the tunnel
# below in the same PID-tracked pool as the other services so ctrl-C
# tears it down together. Defaults GSFLUENT_SERVER to the laptop side
# of the tunnel so the user gets one variable to think about, not two.
if [[ -n "${SERVER_SSH:-}" ]]; then
    if [[ -z "${GSFLUENT_SERVER:-}" ]]; then
        GSFLUENT_SERVER="http://localhost:$LOCAL_PORT"
    fi
fi

if [[ -z "${GSFLUENT_SERVER:-}" ]]; then
    cat >&2 <<EOF
ERROR: no server target. Pick one:

    SERVER_SSH=your-server ./run-laptop.sh
        → auto-tunnel to the server's API_PORT and point at localhost.

    GSFLUENT_SERVER=http://host:port ./run-laptop.sh
        → use an existing tunnel or LAN-reachable backend.
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

# Stage 0: SSH tunnel, if SERVER_SSH is set. We use a background
# `ssh -N -L` and let the trap clean it up alongside the python services.
# Probe the laptop side of the tunnel before continuing so the next
# stages don't race a port that isn't bound yet.
if [[ -n "${SERVER_SSH:-}" ]]; then
    echo ">>> ssh tunnel       $LOCAL_PORT → $SERVER_SSH:$REMOTE_PORT"
    ssh -N -L "$LOCAL_PORT:localhost:$REMOTE_PORT" "$SERVER_SSH" &
    PIDS+=($!)
    # Wait up to 10s for the local port to bind.
    for _ in $(seq 1 20); do
        if (echo > "/dev/tcp/127.0.0.1/$LOCAL_PORT") 2>/dev/null; then
            break
        fi
        sleep 0.5
    done
    if ! (echo > "/dev/tcp/127.0.0.1/$LOCAL_PORT") 2>/dev/null; then
        echo "ERROR: SSH tunnel failed to bind localhost:$LOCAL_PORT after 10s" >&2
        echo "       Check 'ssh $SERVER_SSH' works interactively and that the server" >&2
        echo "       is listening on :$REMOTE_PORT (./run-server.sh)." >&2
        exit 1
    fi
fi

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
