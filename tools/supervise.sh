#!/usr/bin/env bash
# gsfluent demo supervisor — restart viser_headless and v1 backend
# if they die.
#
# Designed to survive overnight on the GPU host without docker / systemd. A
# parent watcher loop runs in the background, polls each child every 5 s,
# and respawns with the same args + env. Logs all restarts to
# ${GSFLUENT_PKG_ROOT}/work/logs/supervisor.log.
#
# v2 api was retired 2026-05-20: it had collapsed to a pure
# reverse-proxy in front of v1 after the laptop-pkg rollout, so v1 now
# binds 0.0.0.0:7869 directly and the public NAT 24701 lands on it.
#
# Idempotent: invoking it again just kills the old watcher + children
# and starts a fresh round (use the `stop` arg to just kill).

set -u

# Source .env from the repo root so paths + interpreters carry through.
# See .env.example at the repo root for the full key set.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ -f "$HERE/.env" ]; then
  set -a
  . "$HERE/.env"
  set +a
fi
: "${GSFLUENT_PKG_ROOT:=$HERE}"
: "${GSFLUENT_BACKEND_PORT:=7869}"

LOG=${GSFLUENT_PKG_ROOT}/work/logs/supervisor.log
PIDFILE=${GSFLUENT_PKG_ROOT}/work/logs/supervisor.pid
PKG=${GSFLUENT_PKG_ROOT}
PY_VISER=${GSFLUENT_SIM_PYTHON}
# v1 backend imports under Python 3.11 (its FastAPI code uses 3.10+
# union-type syntax), but the sim subprocess it spawns needs a python
# with torch/warp/taichi — pointing GSFLUENT_SIM_PYTHON at the
# GaussianFluent env keeps both happy.
PY_V1=${GSFLUENT_API_PYTHON}
PY_SIM=${GSFLUENT_SIM_PYTHON}

stamp() { date '+%F %T'; }

start_viser() {
  cd "$PKG"
  nohup "$PY_VISER" tools/viser_headless.py \
    --npz_dir "$PKG/work/cache/viser" \
    --viser_port 8091 --control_port 8092 --bind 127.0.0.1 \
    --server http://127.0.0.1:${GSFLUENT_BACKEND_PORT} \
    >> "$PKG/work/logs/viser_headless.log" 2>&1 &
  echo $!
}

start_v1() {
  cd "$PKG"
  PYTHONPATH="$PKG/server" \
  GSFLUENT_SIM_PYTHON="$PY_SIM" \
  nohup "$PY_V1" -m gsfluent serve --port ${GSFLUENT_BACKEND_PORT} --host 0.0.0.0 --no-browser \
    >> "$PKG/work/logs/v1.log" 2>&1 &
  echo $!
}

cmd=${1:-up}
case "$cmd" in
  stop)
    if [ -f "$PIDFILE" ]; then
      kill "$(cat "$PIDFILE")" 2>/dev/null || true
      rm -f "$PIDFILE"
    fi
    pkill -f tools/viser_headless.py 2>/dev/null || true
    pkill -f 'uvicorn gsfluent_api.main:app' 2>/dev/null || true  # legacy v2 api; safe no-op now
    pkill -f 'gsfluent serve' 2>/dev/null || true
    echo "stopped" | tee -a "$LOG"
    exit 0
    ;;
  up)
    # Kill any previous supervisor / children so the new one owns the
    # ports. Without this, double-invocation leaves stale duplicates.
    if [ -f "$PIDFILE" ]; then
      kill "$(cat "$PIDFILE")" 2>/dev/null || true
      rm -f "$PIDFILE"
    fi
    pkill -f tools/viser_headless.py 2>/dev/null || true
    pkill -f 'uvicorn gsfluent_api.main:app' 2>/dev/null || true  # legacy v2 api; safe no-op now
    pkill -f 'gsfluent serve' 2>/dev/null || true
    sleep 2

    vpid=$(start_viser); echo "$(stamp) viser started pid=$vpid" >> "$LOG"
    v1pid=$(start_v1);   echo "$(stamp) v1    started pid=$v1pid" >> "$LOG"

    # Watcher loop. Backgrounded so this script returns immediately.
    (
      while :; do
        sleep 5
        if ! kill -0 "$vpid" 2>/dev/null; then
          vpid=$(start_viser); echo "$(stamp) viser respawned pid=$vpid" >> "$LOG"
        fi
        if ! kill -0 "$v1pid" 2>/dev/null; then
          v1pid=$(start_v1);   echo "$(stamp) v1    respawned pid=$v1pid" >> "$LOG"
        fi
      done
    ) &
    echo $! > "$PIDFILE"
    echo "$(stamp) supervisor watcher pid=$(cat "$PIDFILE")" >> "$LOG"
    echo "supervisor up: viser=$vpid v1=$v1pid watcher=$(cat "$PIDFILE")"
    ;;
  status)
    echo "watcher:" "$(cat "$PIDFILE" 2>/dev/null || echo none)"
    pgrep -af tools/viser_headless.py || echo "viser: down"
    pgrep -af 'gsfluent serve'         || echo "v1: down"
    ;;
  *)
    echo "usage: $0 {up|stop|status}"; exit 2;;
esac
