#!/usr/bin/env bash
# Server-side gsfluent launcher.
#
# Starts `gsfluent serve` in the background with the right environment
# for the MPM sim subprocess (POST /api/runs preflights fail otherwise).
#
# Quick start:
#   cp .env.example .env
#   $EDITOR .env                   # fill in YOUR paths
#   ./start-gsfluent-server.sh
#
# Required env vars (set in .env or inline):
#   GSFLUENT_SIM_HOME    — path to your GaussianFluent source tree
#   GSFLUENT_SIM_PYTHON  — Python with torch+warp+taichi (the sim env)
#
# Optional env vars (have sensible defaults):
#   GSFLUENT_BIN         — path to gsfluent CLI (auto-detected via PATH)
#   PORT                 — listener port (default 18080)
#   HOST                 — bind address (default 0.0.0.0)
#   LOG_FILE             — stdout/stderr destination (default /tmp/gsfluent_server.log)
#   PKG_ROOT             — repo root (auto-detected from script location)
#
# To stop the server:    pkill -f "gsfluent serve"
# To tail the log:       tail -f $LOG_FILE
set -euo pipefail

# ---- self-locate -----------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---- source local .env if present -----------------------------------
# Lets the team keep their paths in one place per machine, gitignored.
if [[ -f "$SCRIPT_DIR/.env" ]]; then
    # shellcheck disable=SC1091
    set -a; source "$SCRIPT_DIR/.env"; set +a
fi

# ---- defaults for the things that have universal defaults -----------
: "${PKG_ROOT:=$SCRIPT_DIR}"
: "${PORT:=18080}"
: "${HOST:=0.0.0.0}"
: "${LOG_FILE:=/tmp/gsfluent_server.log}"

# Auto-detect gsfluent CLI via PATH unless overridden.
if [[ -z "${GSFLUENT_BIN:-}" ]]; then
    if command -v gsfluent >/dev/null 2>&1; then
        GSFLUENT_BIN="$(command -v gsfluent)"
    fi
fi

# ---- required vars (no fake defaults) -------------------------------
missing=()
[[ -z "${GSFLUENT_SIM_HOME:-}" ]]   && missing+=("GSFLUENT_SIM_HOME")
[[ -z "${GSFLUENT_SIM_PYTHON:-}" ]] && missing+=("GSFLUENT_SIM_PYTHON")
[[ -z "${GSFLUENT_BIN:-}" ]]        && missing+=("GSFLUENT_BIN (or have gsfluent on PATH)")

if (( ${#missing[@]} > 0 )); then
    cat >&2 <<EOF
ERROR: required environment variables not set:

  $(printf '  - %s\n' "${missing[@]}")

Easiest fix: copy .env.example to .env and fill in your paths.

  cd $SCRIPT_DIR
  cp .env.example .env
  \$EDITOR .env
  ./start-gsfluent-server.sh
EOF
    exit 1
fi

# Catch the common "copied .env.example without editing" case so the
# user gets a directive error instead of a confusing "no such directory".
if [[ "$GSFLUENT_SIM_HOME" == *__FILL_ME_IN__* ]] \
|| [[ "$GSFLUENT_SIM_PYTHON" == *__FILL_ME_IN__* ]]; then
    cat >&2 <<EOF
ERROR: $SCRIPT_DIR/.env still contains the placeholder __FILL_ME_IN__.

You copied .env.example to .env but didn't edit it yet. Open .env and
replace the __FILL_ME_IN__ markers with your actual paths:

  GSFLUENT_SIM_HOME    your local GaussianFluent source tree
  GSFLUENT_SIM_PYTHON  Python interpreter with torch+warp+taichi

  \$EDITOR $SCRIPT_DIR/.env
EOF
    exit 1
fi

# ---- sanity checks --------------------------------------------------
if [[ ! -d "$GSFLUENT_SIM_HOME" ]]; then
    echo "ERROR: GSFLUENT_SIM_HOME does not exist: $GSFLUENT_SIM_HOME" >&2
    exit 1
fi
if [[ ! -f "$GSFLUENT_SIM_HOME/gs_simulation/watermelon/gs_simulation_building.py" ]]; then
    echo "ERROR: GSFLUENT_SIM_HOME doesn't look like a GaussianFluent checkout:" >&2
    echo "       missing gs_simulation/watermelon/gs_simulation_building.py" >&2
    echo "       (this should be a clone of github.com/whc1992/GaussianFluent)" >&2
    exit 1
fi
if [[ ! -x "$GSFLUENT_SIM_PYTHON" ]]; then
    echo "ERROR: GSFLUENT_SIM_PYTHON is not an executable: $GSFLUENT_SIM_PYTHON" >&2
    echo "       Must be a Python with torch + warp + taichi installed." >&2
    exit 1
fi
if [[ ! -x "$GSFLUENT_BIN" ]]; then
    echo "ERROR: gsfluent CLI not found / not executable: $GSFLUENT_BIN" >&2
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
