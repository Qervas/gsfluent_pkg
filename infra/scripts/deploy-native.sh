#!/usr/bin/env bash
# gsfluent v2 — native deploy fallback.
#
# Use this when docker isn't available on the target host (the your-server GPU
# box currently has no docker installed; ask IT, or use this).
#
# Installs postgres + redis + minio into $GSFLUENT_V2_HOME via conda (when
# available) or pre-built binaries, initializes the v2 schema, and starts
# each service as a backgrounded process tracked in $GSFLUENT_V2_HOME/run/.
#
# Usage:
#   GSFLUENT_V2_HOME=$USER_HOME/gsfluent_v2 \
#   GSFLUENT_V2_PYTHON=$CONDA_ROOT/bin/python \
#       bash infra/scripts/deploy-native.sh up
#
#   GSFLUENT_V2_HOME=... bash infra/scripts/deploy-native.sh down
#   GSFLUENT_V2_HOME=... bash infra/scripts/deploy-native.sh status

set -euo pipefail

HOME_DIR="${GSFLUENT_V2_HOME:?GSFLUENT_V2_HOME (deployment root) is required}"
PYTHON="${GSFLUENT_V2_PYTHON:-python3}"
PG_PORT="${POSTGRES_PORT:-15432}"
REDIS_PORT="${REDIS_PORT:-16379}"
MINIO_API_PORT="${MINIO_API_PORT:-19000}"
MINIO_CONSOLE_PORT="${MINIO_CONSOLE_PORT:-19001}"
API_PORT="${API_PORT:-18000}"
MINIO_USER="${MINIO_ROOT_USER:-gsfluent}"
MINIO_PASS="${MINIO_ROOT_PASSWORD:?MINIO_ROOT_PASSWORD required}"
PG_PASS="${POSTGRES_PASSWORD:?POSTGRES_PASSWORD required}"

PG_DATA="$HOME_DIR/postgres-data"
REDIS_DATA="$HOME_DIR/redis-data"
MINIO_DATA="$HOME_DIR/minio-data"
RUN_DIR="$HOME_DIR/run"
LOG_DIR="$HOME_DIR/log"
BIN_DIR="$HOME_DIR/bin"

mkdir -p "$RUN_DIR" "$LOG_DIR" "$BIN_DIR"

# -------- helpers ---------------------------------------------------

_have() { command -v "$1" >/dev/null 2>&1; }
_log()  { printf '[deploy-native] %s\n' "$*" >&2; }
_pid()  { local svc="$1"; cat "$RUN_DIR/${svc}.pid" 2>/dev/null || true; }
_running() { local pid="$1"; [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; }

_ensure_minio_binary() {
  if _have minio; then return; fi
  if [[ -x "$BIN_DIR/minio" ]]; then export PATH="$BIN_DIR:$PATH"; return; fi
  _log "downloading minio binary..."
  curl -fsSL https://dl.min.io/server/minio/release/linux-amd64/minio \
    -o "$BIN_DIR/minio"
  chmod +x "$BIN_DIR/minio"
  export PATH="$BIN_DIR:$PATH"
}

_ensure_mc_binary() {
  if _have mc; then return; fi
  if [[ -x "$BIN_DIR/mc" ]]; then export PATH="$BIN_DIR:$PATH"; return; fi
  _log "downloading mc binary..."
  curl -fsSL https://dl.min.io/client/mc/release/linux-amd64/mc \
    -o "$BIN_DIR/mc"
  chmod +x "$BIN_DIR/mc"
  export PATH="$BIN_DIR:$PATH"
}

_assert_postgres_binaries() {
  if _have initdb && _have pg_ctl && _have psql; then return; fi
  cat >&2 <<EOF
postgres binaries not on PATH. Options:
  conda install -c conda-forge postgresql=16
  OR ask company IT for a system postgres install
  OR set PATH manually
EOF
  return 1
}

_assert_redis_binary() {
  if _have redis-server && _have redis-cli; then return; fi
  cat >&2 <<EOF
redis binaries not on PATH. Options:
  conda install -c conda-forge redis-server
  OR ask company IT to install redis
EOF
  return 1
}

# -------- start ----------------------------------------------------

_start_postgres() {
  if [[ ! -f "$PG_DATA/PG_VERSION" ]]; then
    _log "initializing postgres at $PG_DATA"
    initdb -D "$PG_DATA" -U gsfluent --auth-host=scram-sha-256 \
           --pwfile=<(echo "$PG_PASS") >"$LOG_DIR/pg-init.log" 2>&1
    echo "host all all 127.0.0.1/32 scram-sha-256" >> "$PG_DATA/pg_hba.conf"
    echo "listen_addresses = '127.0.0.1'" >> "$PG_DATA/postgresql.conf"
    echo "port = $PG_PORT" >> "$PG_DATA/postgresql.conf"
  fi
  if _running "$(_pid postgres)"; then
    _log "postgres already running pid=$(_pid postgres)"
    return
  fi
  _log "starting postgres on :$PG_PORT"
  pg_ctl -D "$PG_DATA" -l "$LOG_DIR/postgres.log" \
         -o "-p $PG_PORT" start
  echo $(pgrep -f "postgres -D $PG_DATA" | head -1) > "$RUN_DIR/postgres.pid"
  # Create gsfluent_v2 db if missing.
  PGPASSWORD="$PG_PASS" psql -h 127.0.0.1 -p "$PG_PORT" -U gsfluent -d postgres \
    -tAc "SELECT 1 FROM pg_database WHERE datname='gsfluent_v2'" | grep -q 1 \
    || PGPASSWORD="$PG_PASS" psql -h 127.0.0.1 -p "$PG_PORT" -U gsfluent -d postgres \
       -c "CREATE DATABASE gsfluent_v2"
  PGPASSWORD="$PG_PASS" psql -h 127.0.0.1 -p "$PG_PORT" -U gsfluent -d gsfluent_v2 \
    -f "$(dirname "$0")/../postgres/init.sql"
}

_start_redis() {
  if _running "$(_pid redis)"; then
    _log "redis already running pid=$(_pid redis)"
    return
  fi
  mkdir -p "$REDIS_DATA"
  _log "starting redis on :$REDIS_PORT"
  nohup redis-server \
    --port "$REDIS_PORT" \
    --dir "$REDIS_DATA" \
    --appendonly yes \
    --bind 127.0.0.1 \
    > "$LOG_DIR/redis.log" 2>&1 &
  echo $! > "$RUN_DIR/redis.pid"
  sleep 1
}

_start_minio() {
  if _running "$(_pid minio)"; then
    _log "minio already running pid=$(_pid minio)"
    return
  fi
  _ensure_minio_binary
  _ensure_mc_binary
  mkdir -p "$MINIO_DATA"
  _log "starting minio on :$MINIO_API_PORT (console :$MINIO_CONSOLE_PORT)"
  MINIO_ROOT_USER="$MINIO_USER" MINIO_ROOT_PASSWORD="$MINIO_PASS" \
    nohup "$BIN_DIR/minio" server "$MINIO_DATA" \
      --address ":$MINIO_API_PORT" \
      --console-address ":$MINIO_CONSOLE_PORT" \
      > "$LOG_DIR/minio.log" 2>&1 &
  echo $! > "$RUN_DIR/minio.pid"
  sleep 3
  # Create buckets.
  mc alias set gsfluent "http://127.0.0.1:$MINIO_API_PORT" \
     "$MINIO_USER" "$MINIO_PASS" >/dev/null
  for b in gsfluent-models gsfluent-runs gsfluent-misc; do
    mc mb -p "gsfluent/$b" 2>/dev/null || true
  done
}

_start_api() {
  if _running "$(_pid api)"; then
    _log "api already running pid=$(_pid api)"
    return
  fi
  _log "starting api on :$API_PORT"
  local repo_root
  repo_root="$(cd "$(dirname "$0")/../.." && pwd)"
  cd "$repo_root/apps/api"

  DATABASE_URL="postgresql+asyncpg://gsfluent:${PG_PASS}@127.0.0.1:${PG_PORT}/gsfluent_v2" \
  REDIS_URL="redis://127.0.0.1:${REDIS_PORT}/0" \
  MINIO_ENDPOINT="127.0.0.1:${MINIO_API_PORT}" \
  MINIO_ACCESS_KEY="$MINIO_USER" \
  MINIO_SECRET_KEY="$MINIO_PASS" \
  PYTHONPATH="$repo_root/apps/api/src" \
    nohup "$PYTHON" -m uvicorn gsfluent_api.main:app \
      --host 127.0.0.1 --port "$API_PORT" \
      > "$LOG_DIR/api.log" 2>&1 &
  echo $! > "$RUN_DIR/api.pid"
  sleep 2
}

_stop_one() {
  local name="$1"
  local pid
  pid="$(_pid "$name")"
  if _running "$pid"; then
    _log "stopping $name pid=$pid"
    kill "$pid" 2>/dev/null || true
    sleep 1
    kill -9 "$pid" 2>/dev/null || true
  fi
  rm -f "$RUN_DIR/${name}.pid"
}

cmd="${1:?up | down | status | smoke}"
case "$cmd" in
  up)
    _assert_postgres_binaries
    _assert_redis_binary
    _start_postgres
    _start_redis
    _start_minio
    _start_api
    _log "all up. api at http://127.0.0.1:${API_PORT}/v1/system/health"
    ;;
  down)
    _stop_one api
    _stop_one minio
    _stop_one redis
    if _have pg_ctl; then
      pg_ctl -D "$PG_DATA" stop -m fast 2>/dev/null || _stop_one postgres
    else
      _stop_one postgres
    fi
    ;;
  status)
    for svc in postgres redis minio api; do
      pid="$(_pid $svc)"
      if _running "$pid"; then
        printf '%-9s up   pid=%s\n' "$svc" "$pid"
      else
        printf '%-9s down\n' "$svc"
      fi
    done
    ;;
  smoke)
    curl -fsS "http://127.0.0.1:${API_PORT}/v1/system/health" | head -c 1024
    echo
    ;;
  *)
    echo "unknown command: $cmd" >&2
    exit 2
    ;;
esac
