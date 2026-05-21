#!/usr/bin/env bash
# gsfluent backend supervisor — restart the v1 backend if it dies.
#
# Designed to survive overnight on the GPU host without docker / systemd. A
# parent watcher loop runs in the background, polls the child every 5 s,
# and respawns with the same args + env. Logs all restarts to
# ${GSFLUENT_PKG_ROOT}/work/logs/supervisor.log.
#
# Idempotent: invoking it again just kills the old watcher + child
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
PY_V1=${GSFLUENT_API_PYTHON}
PY_SIM=${GSFLUENT_SIM_PYTHON}

stamp() { date '+%F %T'; }

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
    pkill -f 'gsfluent serve' 2>/dev/null || true
    echo "stopped" | tee -a "$LOG"
    exit 0
    ;;
  up)
    # Kill any previous supervisor / child so the new one owns the port.
    if [ -f "$PIDFILE" ]; then
      kill "$(cat "$PIDFILE")" 2>/dev/null || true
      rm -f "$PIDFILE"
    fi
    pkill -f 'gsfluent serve' 2>/dev/null || true
    sleep 2

    v1pid=$(start_v1); echo "$(stamp) v1 started pid=$v1pid" >> "$LOG"

    # Watcher loop. Backgrounded so this script returns immediately.
    (
      while :; do
        sleep 5
        if ! kill -0 "$v1pid" 2>/dev/null; then
          v1pid=$(start_v1); echo "$(stamp) v1 respawned pid=$v1pid" >> "$LOG"
        fi
      done
    ) &
    echo $! > "$PIDFILE"
    echo "$(stamp) supervisor watcher pid=$(cat "$PIDFILE")" >> "$LOG"
    echo "supervisor up: v1=$v1pid watcher=$(cat "$PIDFILE")"
    ;;
  status)
    echo "watcher:" "$(cat "$PIDFILE" 2>/dev/null || echo none)"
    pgrep -af 'gsfluent serve' || echo "v1: down"
    ;;
  *)
    echo "usage: $0 {up|stop|status}"; exit 2;;
esac
