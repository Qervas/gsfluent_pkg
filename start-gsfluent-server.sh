#!/usr/bin/env bash
# Server-side gsfluent launcher.
#
# Starts `gsfluent serve` in the background with the right environment
# for the MPM sim subprocess. Without this, POST /api/runs preflights
# fail with:
#   ERROR: sim interpreter not on PATH: $GSFLUENT_SIM_PYTHON=python
# (because `tools/run_sim.sh` defaults to bare `python`, which the
# gsfluent-api conda env doesn't carry torch/warp/taichi for — only the
# GaussianFluent env does).
#
# Usage:
#   ./start-gsfluent-server.sh          # use built-in defaults (your-server layout)
#
# Or override anything via env vars:
#   PORT=18080 ./start-gsfluent-server.sh
#
#   GSFLUENT_SIM_HOME=/opt/GaussianFluent \
#   GSFLUENT_SIM_PYTHON=/opt/conda/envs/sim/bin/python \
#   GSFLUENT_BIN=/opt/conda/envs/api/bin/gsfluent \
#   PKG_ROOT=/opt/gsfluent_pkg \
#   PORT=18080 \
#   ./start-gsfluent-server.sh
#
# To stop the server:    pkill -f "gsfluent serve"
# To tail the log:       tail -f $LOG_FILE  (default /tmp/gsfluent_server.log)
set -euo pipefail

# ---- defaults (match the current your-server deployment) --------------
: "${GSFLUENT_SIM_HOME:=$GSFLUENT_SIM_HOME}"
: "${GSFLUENT_SIM_PYTHON:=$CONDA_ROOT/envs/GaussianFluent/bin/python}"
: "${GSFLUENT_BIN:=$CONDA_ROOT/envs/gsfluent-api/bin/gsfluent}"
: "${PKG_ROOT:=$GSFLUENT_PKG_ROOT_tmp}"
: "${PORT:=18080}"
: "${HOST:=0.0.0.0}"
: "${LOG_FILE:=/tmp/gsfluent_server.log}"

# ---- sanity checks --------------------------------------------------
if [[ ! -d "$GSFLUENT_SIM_HOME" ]]; then
    echo "ERROR: GSFLUENT_SIM_HOME does not exist: $GSFLUENT_SIM_HOME" >&2
    echo "       Override with: GSFLUENT_SIM_HOME=/path/to/GaussianFluent ./start-gsfluent-server.sh" >&2
    exit 1
fi
if [[ ! -x "$GSFLUENT_SIM_PYTHON" ]]; then
    echo "ERROR: GSFLUENT_SIM_PYTHON is not an executable: $GSFLUENT_SIM_PYTHON" >&2
    echo "       This Python must have torch + warp + taichi (the sim env)." >&2
    exit 1
fi
if [[ ! -x "$GSFLUENT_BIN" ]]; then
    echo "ERROR: gsfluent CLI not found at: $GSFLUENT_BIN" >&2
    echo "       Override with: GSFLUENT_BIN=/path/to/gsfluent ./start-gsfluent-server.sh" >&2
    exit 1
fi
if [[ ! -d "$PKG_ROOT" ]]; then
    echo "ERROR: PKG_ROOT does not exist: $PKG_ROOT" >&2
    exit 1
fi

# Refuse to launch a second copy on the same port.
if ss -tlnp 2>/dev/null | grep -qE ":${PORT}\s"; then
    echo "ERROR: port $PORT already in use. Run \`pkill -f 'gsfluent serve'\` and retry." >&2
    exit 1
fi

# ---- export sim env for the spawned subprocess ----------------------
export GSFLUENT_SIM_HOME
export GSFLUENT_SIM_PYTHON

# ---- launch ---------------------------------------------------------
cd "$PKG_ROOT"

nohup "$GSFLUENT_BIN" serve \
    --host "$HOST" --port "$PORT" --no-browser \
    > "$LOG_FILE" 2>&1 < /dev/null &
disown
PID=$!

# Wait briefly for the port to come up so the user sees a definitive
# "OK" rather than racing the healthcheck.
for _ in $(seq 1 20); do
    if ss -tlnp 2>/dev/null | grep -qE ":${PORT}\s"; then
        break
    fi
    sleep 0.3
done

cat <<EOF
gsfluent serve started (pid=$PID)
  HOST          $HOST
  PORT          $PORT
  PKG_ROOT      $PKG_ROOT
  SIM_HOME      $GSFLUENT_SIM_HOME
  SIM_PYTHON    $GSFLUENT_SIM_PYTHON
  GSFLUENT_BIN  $GSFLUENT_BIN
  LOG_FILE      $LOG_FILE

Health check (should return {"status":"ok",...}):
  curl http://localhost:${PORT}/api/health

To stop:
  pkill -f "gsfluent serve"

To tail the log:
  tail -f $LOG_FILE
EOF
