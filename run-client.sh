#!/usr/bin/env bash
# Client-side launcher — runs on the machine that views the workbench.
#
# Brings up four local services + opens the SPA in a browser, all
# managed by this script so a single Ctrl-C tears the stack down:
#
#   1. SPA          — vite preview on :$SPA_PORT, serves frontend/dist/
#   2. viser        — viser :$VISER_PORT  + control API :$CONTROL_PORT (Splats mode)
#   3. sync_daemon  — mirrors the server's .npz cache locally
#   4. local_stream — ws :$STREAM_PORT (Points mode, mmaps the synced .npz)
#
# Optionally + recommended:
#   0. SSH tunnel   — laptop:$LOCAL_PORT ↔ $SERVER_SSH:$REMOTE_PORT
#
# Strong split: the server (./run-server.sh) is a pure API. The SPA is
# served from this machine; it talks to the server via the tunnel (or
# whatever $GSFLUENT_SERVER you provide).
#
# Two ways to point at the server:
#
#   (A) Auto-tunnel (recommended, one command):
#       SERVER_SSH=<host> ./run-client.sh
#       → opens `ssh -N -L $LOCAL_PORT:localhost:$REMOTE_PORT $SERVER_SSH`
#         and points everything at localhost. Tunnel dies on Ctrl-C.
#
#   (B) Existing tunnel or LAN-reachable backend:
#       GSFLUENT_SERVER=http://host:port ./run-client.sh
#
# Environment:
#   SERVER_SSH       SSH host alias for auto-tunnel (mode A)
#   GSFLUENT_SERVER  explicit backend URL (mode B). Defaults to
#                    http://localhost:$LOCAL_PORT when SERVER_SSH is set.
#   LOCAL_PORT       default 8080      client side of the tunnel
#   REMOTE_PORT      default 8080      server side of the tunnel (matches API_PORT)
#   SPA_PORT         default 4173      vite preview (SPA) port
#   VISER_PORT       default 8091
#   CONTROL_PORT     default 8092
#   STREAM_PORT      default 8083
#   POLL_INTERVAL    default 10        sync_daemon poll, seconds
#   OPEN_BROWSER     default 1         set 0 to skip
#
# First-time setup:
#   ./setup-client.sh

set -euo pipefail

PKG_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PY="$PKG_ROOT/server/.venv/bin/python"

LOCAL_PORT="${LOCAL_PORT:-8080}"
REMOTE_PORT="${REMOTE_PORT:-8080}"
SPA_PORT="${SPA_PORT:-4173}"
VISER_PORT="${VISER_PORT:-8091}"
CONTROL_PORT="${CONTROL_PORT:-8092}"
STREAM_PORT="${STREAM_PORT:-8083}"
POLL_INTERVAL="${POLL_INTERVAL:-10}"
CACHE_ROOT="$PKG_ROOT/work/cache"
OPEN_BROWSER="${OPEN_BROWSER:-1}"

# Resolve server URL up front so error messages are unambiguous.
if [[ -n "${SERVER_SSH:-}" ]]; then
    GSFLUENT_SERVER="${GSFLUENT_SERVER:-http://localhost:$LOCAL_PORT}"
fi
if [[ -z "${GSFLUENT_SERVER:-}" ]]; then
    cat >&2 <<EOF
ERROR: no server target. Pick one:

    SERVER_SSH=<host> ./run-client.sh
        → auto-tunnel to the server's API_PORT.

    GSFLUENT_SERVER=http://host:port ./run-client.sh
        → use an existing tunnel or LAN-reachable backend.
EOF
    exit 2
fi
SERVER="${GSFLUENT_SERVER%/}"

# ---- preflight ----
if [[ ! -x "$VENV_PY" ]]; then
    cat >&2 <<EOF
ERROR: server/.venv/bin/python not found.

Run ./setup-client.sh first; it creates the uv-managed venv with
viser + numpy and builds frontend/dist/.
EOF
    exit 1
fi

if [[ ! -f "$PKG_ROOT/frontend/dist/index.html" ]]; then
    cat >&2 <<EOF
ERROR: frontend/dist/index.html missing.

Run ./setup-client.sh first — it builds the SPA into frontend/dist/.
To rebuild after a frontend change:
    cd frontend && npm run build
EOF
    exit 1
fi

mkdir -p "$CACHE_ROOT/viser" "$CACHE_ROOT/frames-bin"

# ---- PID tracking + cleanup ----
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
echo ">>> SPA:             http://localhost:$SPA_PORT/"

# ---- Stage 0: SSH tunnel ----
# `ssh -N -L` runs in the background; we wait for the local port to
# bind via bash's /dev/tcp so the next stages don't race the tunnel.
if [[ -n "${SERVER_SSH:-}" ]]; then
    echo ">>> ssh tunnel       :$LOCAL_PORT → $SERVER_SSH:$REMOTE_PORT"
    ssh -N -L "$LOCAL_PORT:localhost:$REMOTE_PORT" "$SERVER_SSH" &
    PIDS+=($!)
    for _ in $(seq 1 20); do
        if (echo > "/dev/tcp/127.0.0.1/$LOCAL_PORT") 2>/dev/null; then
            break
        fi
        sleep 0.5
    done
    if ! (echo > "/dev/tcp/127.0.0.1/$LOCAL_PORT") 2>/dev/null; then
        echo "ERROR: SSH tunnel failed to bind localhost:$LOCAL_PORT after 10s" >&2
        echo "       Check 'ssh $SERVER_SSH' works interactively and that the" >&2
        echo "       server is listening on :$REMOTE_PORT (./run-server.sh)." >&2
        exit 1
    fi
fi

# ---- Stage 1: SPA (vite preview) ----
# vite preview serves the already-built frontend/dist/ + proxies /api
# to whichever backend port is wired in vite.config.ts. We set
# GSFLUENT_BACKEND_PORT so the proxy lands on our tunnel-local port.
echo ">>> SPA              vite preview :$SPA_PORT (proxy → :$LOCAL_PORT)"
(
    cd "$PKG_ROOT/frontend"
    GSFLUENT_BACKEND_PORT="$LOCAL_PORT" \
        npx vite preview --port "$SPA_PORT" --strictPort
) &
PIDS+=($!)

# ---- Stage 2: viser headless ----
echo ">>> viser_headless   :$VISER_PORT   (control :$CONTROL_PORT)"
"$VENV_PY" "$PKG_ROOT/tools/viser_headless.py" \
    --npz_dir "$CACHE_ROOT/viser" \
    --viser_port "$VISER_PORT" \
    --control_port "$CONTROL_PORT" &
PIDS+=($!)

# ---- Stage 3: sync_daemon ----
echo ">>> sync_daemon      polling every ${POLL_INTERVAL}s"
"$VENV_PY" "$PKG_ROOT/tools/sync_daemon.py" \
    --server "$SERVER" \
    --cache-root "$CACHE_ROOT" \
    --viser-control "http://localhost:$CONTROL_PORT" \
    --interval "$POLL_INTERVAL" \
    --verbose &
PIDS+=($!)

# ---- Stage 4: local_stream (Points WS) ----
echo ">>> local_stream     ws://localhost:$STREAM_PORT/api/stream"
"$VENV_PY" "$PKG_ROOT/tools/local_stream.py" \
    --cache-root "$CACHE_ROOT" \
    --port "$STREAM_PORT" \
    --host 127.0.0.1 &
PIDS+=($!)

# ---- open browser ----
if [[ "$OPEN_BROWSER" -eq 1 ]]; then
    sleep 3
    SPA_URL="http://localhost:$SPA_PORT/"
    if command -v xdg-open >/dev/null 2>&1; then
        xdg-open "$SPA_URL" >/dev/null 2>&1 || true
    elif command -v open >/dev/null 2>&1; then
        open "$SPA_URL" >/dev/null 2>&1 || true
    fi
fi

echo ""
echo ">>> client stack running. Ctrl-C to stop everything."
echo ">>> SPA:             http://localhost:$SPA_PORT/"
echo ">>> sync status:     ${XDG_RUNTIME_DIR:-/tmp/$(id -u)}/gsfluent_sync_status.json"

# Wait on the first child to exit; the trap then takes down the rest.
wait -n
