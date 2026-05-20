#!/usr/bin/env bash
# gsfluent demo supervisor — restart viser_headless, v1 backend, and
# v2 api if they die.
#
# Designed to survive overnight on sxyin without docker / systemd. A
# parent watcher loop runs in the background, polls each child every 5 s,
# and respawns with the same args + env. Logs all restarts to
# /data/yinshaoxuan/gsfluent_pkg/work/logs/supervisor.log.
#
# Idempotent: invoking it again just kills the old watcher + children
# and starts a fresh round (use the `stop` arg to just kill).

set -u

LOG=/data/yinshaoxuan/gsfluent_pkg/work/logs/supervisor.log
PIDFILE=/data/yinshaoxuan/gsfluent_pkg/work/logs/supervisor.pid
PKG=/data/yinshaoxuan/gsfluent_pkg
V2=/data/yinshaoxuan/gsfluent_v2
PY_VISER=/data/yinshaoxuan/miniconda3/envs/GaussianFluent/bin/python
PY_API=/data/yinshaoxuan/miniconda3/envs/gsfluent-api/bin/python
# v1 backend imports under Python 3.11 (its FastAPI code uses 3.10+
# union-type syntax), but the sim subprocess it spawns needs a python
# with torch/warp/taichi — pointing GSFLUENT_SIM_PYTHON at the
# GaussianFluent env keeps both happy.
PY_V1=/data/yinshaoxuan/miniconda3/envs/gsfluent-api/bin/python
PY_SIM=/data/yinshaoxuan/miniconda3/envs/GaussianFluent/bin/python

stamp() { date '+%F %T'; }

start_viser() {
  cd "$PKG"
  nohup "$PY_VISER" tools/viser_headless.py \
    --npz_dir "$PKG/work/cache/viser" \
    --viser_port 8091 --control_port 8092 --bind 127.0.0.1 \
    --server http://127.0.0.1:7870 \
    >> "$PKG/work/logs/viser_headless.log" 2>&1 &
  echo $!
}

start_api() {
  cd "$V2/apps/api"
  DATABASE_URL=postgresql+asyncpg://gsfluent:test-pg-pass@127.0.0.1:15432/gsfluent_v2 \
  REDIS_URL=redis://127.0.0.1:16379/0 \
  MINIO_ENDPOINT=127.0.0.1:19000 \
  MINIO_ACCESS_KEY=gsfluent MINIO_SECRET_KEY=test-minio-pass \
  SPA_DIR=/data/yinshaoxuan/gsfluent_pkg/server/gsfluent/static \
  V1_API_BASE=http://127.0.0.1:7870 \
  VISER_HTTP_BASE=http://127.0.0.1:8091 \
  VISER_CTRL_BASE=http://127.0.0.1:8092 \
  nohup "$PY_API" -m uvicorn gsfluent_api.main:app --host 0.0.0.0 --port 7869 \
    >> "$PKG/work/logs/v2api.log" 2>&1 &
  echo $!
}

start_v1() {
  cd "$PKG"
  PYTHONPATH="$PKG/server" \
  GSFLUENT_SIM_PYTHON="$PY_SIM" \
  nohup "$PY_V1" -m gsfluent serve --port 7870 --host 127.0.0.1 --no-browser \
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
    pkill -f 'uvicorn gsfluent_api.main:app' 2>/dev/null || true
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
    pkill -f 'uvicorn gsfluent_api.main:app' 2>/dev/null || true
    pkill -f 'gsfluent serve' 2>/dev/null || true
    sleep 2

    vpid=$(start_viser); echo "$(stamp) viser started pid=$vpid" >> "$LOG"
    v1pid=$(start_v1);   echo "$(stamp) v1    started pid=$v1pid" >> "$LOG"
    apid=$(start_api);   echo "$(stamp) api   started pid=$apid" >> "$LOG"

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
        if ! kill -0 "$apid" 2>/dev/null; then
          apid=$(start_api);   echo "$(stamp) api   respawned pid=$apid" >> "$LOG"
        fi
      done
    ) &
    echo $! > "$PIDFILE"
    echo "$(stamp) supervisor watcher pid=$(cat "$PIDFILE")" >> "$LOG"
    echo "supervisor up: viser=$vpid v1=$v1pid api=$apid watcher=$(cat "$PIDFILE")"
    ;;
  status)
    echo "watcher:" "$(cat "$PIDFILE" 2>/dev/null || echo none)"
    pgrep -af tools/viser_headless.py || echo "viser: down"
    pgrep -af 'gsfluent serve'         || echo "v1: down"
    pgrep -af 'uvicorn gsfluent_api.main:app' || echo "api: down"
    ;;
  *)
    echo "usage: $0 {up|stop|status}"; exit 2;;
esac
