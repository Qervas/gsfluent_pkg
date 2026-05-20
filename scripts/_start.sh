#!/usr/bin/env bash
# Laptop-local launcher (implementation detail — call via npm).
#
# The teammate-facing entry point is `npm start` from frontend/. This
# file stays callable directly (`bash scripts/_start.sh`, or via the
# legacy `bash scripts/start-local.sh` shim) for engineers debugging.
#
# Starts the two local services + opens the SPA, all managed by this
# script so a single Ctrl-C tears the stack down:
#
#   1. viser_headless    — :8091 (splat WS)  + :8092 (control API),
#                          both bound to 127.0.0.1 (loopback only).
#   2. vite preview      — :$UI_PORT, serves frontend/dist/ and proxies
#                          /api/*, /api/stream (WS), and /v1/* at the
#                          shared backend on your-server.
#
# Strong split: NO process here talks to anything except 127.0.0.1
# (viser) and the your-server backend at $GSFLUENT_BACKEND_URL. The browser's
# /api/* fetches go through the vite preview proxy → your-server; the splat
# iframe + control fetches go straight to 127.0.0.1 (zero WAN hops on
# the high-bandwidth splat WS — the point of the split).
#
# Environment (all have safe defaults):
#   GSFLUENT_BACKEND_URL   default http://your-backend:port
#                          shared your-server v2 api (NAT 24701 → 7869)
#   UI_PORT                default 5173
#   VISER_PORT             default 8091
#   CONTROL_PORT           default 8092
#   VISER_NPZ_DIR          default $PKG_ROOT/work/cache/viser
#                          where viser_headless looks for .npz frame caches
#   OPEN_BROWSER           default 1   (0 disables the xdg-open / open call)

set -euo pipefail

PKG_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PY="$PKG_ROOT/.venv/bin/python"

GSFLUENT_BACKEND_URL="${GSFLUENT_BACKEND_URL:-http://your-backend:port}"
UI_PORT="${UI_PORT:-5173}"
VISER_PORT="${VISER_PORT:-8091}"
CONTROL_PORT="${CONTROL_PORT:-8092}"
VISER_NPZ_DIR="${VISER_NPZ_DIR:-$PKG_ROOT/work/cache/viser}"
OPEN_BROWSER="${OPEN_BROWSER:-1}"

# ---- preflight ---------------------------------------------------------

if [[ ! -x "$VENV_PY" ]]; then
    cat >&2 <<EOF
ERROR: $VENV_PY not found.

Run \`cd frontend && npm install\` first; it creates the venv + builds the SPA.
(Or \`bash scripts/_install.sh\` directly.)
EOF
    exit 1
fi

if [[ ! -f "$PKG_ROOT/frontend/dist/index.html" ]]; then
    cat >&2 <<EOF
ERROR: frontend/dist/index.html missing.

Run \`cd frontend && npm install\` first — it builds the SPA into frontend/dist/.
To rebuild after a frontend change without re-running pip:
    (cd frontend && npm run build)
EOF
    exit 1
fi

if [[ ! -f "$PKG_ROOT/tools/viser_headless.py" ]]; then
    echo "ERROR: tools/viser_headless.py missing — wrong working tree?" >&2
    exit 1
fi

mkdir -p "$VISER_NPZ_DIR"

# ---- PID tracking + cleanup -------------------------------------------

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

echo ">>> backend:         $GSFLUENT_BACKEND_URL"
echo ">>> SPA:             http://localhost:$UI_PORT/"
echo ">>> viser_headless:  127.0.0.1:$VISER_PORT (splats)  127.0.0.1:$CONTROL_PORT (control)"
echo ">>> npz cache:       $VISER_NPZ_DIR"

# ---- viser headless (loopback) -----------------------------------------
# viser_headless.py binds 0.0.0.0 by default, but the SPA hits it through
# the inlined http://127.0.0.1:8091 URL (set at build time in
# frontend/.env.production). Keep it that way — explicit loopback would
# require a flag the script doesn't expose today.

echo ">>> starting viser_headless"
"$VENV_PY" "$PKG_ROOT/tools/viser_headless.py" \
    --npz_dir "$VISER_NPZ_DIR" \
    --viser_port "$VISER_PORT" \
    --control_port "$CONTROL_PORT" &
PIDS+=($!)

# ---- vite preview (SPA + /api proxy) -----------------------------------
# `GSFLUENT_BACKEND_URL` is read by vite.config.ts and applied to the
# preview server's proxy table for /api/*, /api/stream (WS), /v1/*.
# `--strictPort` makes a port collision fail loudly instead of silently
# binding to UI_PORT+1.

echo ">>> starting vite preview"
(
    cd "$PKG_ROOT/frontend"
    GSFLUENT_BACKEND_URL="$GSFLUENT_BACKEND_URL" \
        npx vite preview --port "$UI_PORT" --strictPort
) &
PIDS+=($!)

# ---- wait for ports + open browser ------------------------------------
# Probe both ports with bash's /dev/tcp before launching the browser so
# the first page load isn't a connection-refused screen. ~10 s budget
# is generous; vite usually binds in <1 s.

wait_port() {
    # Probe both IPv4 + IPv6 loopback. Vite 5 binds IPv6-only by default
    # (`[::1]:port` in `ss -ltn` output), so a v4-only probe would
    # false-negative and trigger the WARN even when vite is happy.
    local port="$1"
    for _ in $(seq 1 20); do
        if (echo > "/dev/tcp/127.0.0.1/$port") 2>/dev/null \
            || (echo > "/dev/tcp/::1/$port") 2>/dev/null; then
            return 0
        fi
        sleep 0.5
    done
    return 1
}

if ! wait_port "$UI_PORT"; then
    echo "WARN: SPA on :$UI_PORT did not bind in 10 s; check vite output above" >&2
fi
if ! wait_port "$CONTROL_PORT"; then
    echo "WARN: viser control on :$CONTROL_PORT did not bind in 10 s; check viser output above" >&2
fi

if [[ "$OPEN_BROWSER" -eq 1 ]]; then
    SPA_URL="http://localhost:$UI_PORT/"
    if command -v xdg-open >/dev/null 2>&1; then
        xdg-open "$SPA_URL" >/dev/null 2>&1 || true
    elif command -v open >/dev/null 2>&1; then
        open "$SPA_URL" >/dev/null 2>&1 || true
    fi
fi

echo ""
echo ">>> laptop stack running. Ctrl-C to stop everything."
echo ">>> SPA:             http://localhost:$UI_PORT/"

# Wait on the first child to exit; trap above takes down the rest.
wait -n
