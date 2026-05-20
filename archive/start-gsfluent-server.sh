#!/usr/bin/env bash
# Server-side gsfluent launcher.
#
# Starts `gsfluent serve` in the background with the right environment
# for the MPM sim subprocess (POST /api/runs preflights fail otherwise).
#
# Quick start (fresh clone):
#   ./start-gsfluent-server.sh        # runs interactive setup on first launch
#
# Quick start (manual .env):
#   cp .env.example .env
#   $EDITOR .env
#   ./start-gsfluent-server.sh
#
# Modes:
#   ./start-gsfluent-server.sh                 launch (interactive setup if .env missing)
#   ./start-gsfluent-server.sh --validate      check config, don't start
#   ./start-gsfluent-server.sh --setup         force re-run the interactive wizard
#   ./start-gsfluent-server.sh --help          print this header
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

# ---- flag parse ------------------------------------------------------
MODE="launch"
for arg in "$@"; do
    case "$arg" in
        --validate) MODE="validate" ;;
        --setup)    MODE="setup" ;;
        --help|-h)
            sed -n '2,30p' "$0" | sed 's/^# \?//'
            exit 0
            ;;
        *)
            echo "unknown flag: $arg (try --help)" >&2
            exit 2
            ;;
    esac
done

# ---- helpers ---------------------------------------------------------
ok()    { printf '\033[32m✓\033[0m %s\n' "$*"; }
warn()  { printf '\033[33m!\033[0m %s\n' "$*" >&2; }
fail()  { printf '\033[31m✗\033[0m %s\n' "$*" >&2; }

# ---- run interactive wizard if needed --------------------------------
needs_setup=false
if [[ "$MODE" == "setup" ]]; then
    needs_setup=true
elif [[ ! -f "$SCRIPT_DIR/.env" ]]; then
    needs_setup=true
elif grep -q '__FILL_ME_IN__' "$SCRIPT_DIR/.env" 2>/dev/null; then
    needs_setup=true
fi

# Only run interactive wizard when stdin is a TTY. In CI / scripted
# contexts we want loud errors, not a hanging prompt.
if $needs_setup; then
    if [[ -t 0 ]]; then
        if [[ -x "$SCRIPT_DIR/tools/setup-env.sh" ]]; then
            "$SCRIPT_DIR/tools/setup-env.sh"
        else
            fail "tools/setup-env.sh not found or not executable"
            exit 1
        fi
        # Continue to the regular launch flow below.
    else
        fail "no .env or .env has __FILL_ME_IN__ placeholders, and stdin is not a TTY"
        fail "set GSFLUENT_SIM_HOME and GSFLUENT_SIM_PYTHON, or run with a terminal so the wizard can prompt"
        exit 1
    fi
fi

# ---- source .env -----------------------------------------------------
if [[ -f "$SCRIPT_DIR/.env" ]]; then
    # shellcheck disable=SC1091
    set -a; source "$SCRIPT_DIR/.env"; set +a
fi

# ---- defaults --------------------------------------------------------
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

# ---- validation block (shared by --validate and launch) --------------
errors=()
[[ -z "${GSFLUENT_SIM_HOME:-}"   ]] && errors+=("GSFLUENT_SIM_HOME not set")
[[ -z "${GSFLUENT_SIM_PYTHON:-}" ]] && errors+=("GSFLUENT_SIM_PYTHON not set")
[[ -z "${GSFLUENT_BIN:-}"        ]] && errors+=("GSFLUENT_BIN not set and no gsfluent on PATH")
if [[ "${GSFLUENT_SIM_HOME:-}"   == *__FILL_ME_IN__* ]] || \
   [[ "${GSFLUENT_SIM_PYTHON:-}" == *__FILL_ME_IN__* ]]; then
    errors+=(".env still contains __FILL_ME_IN__ placeholders")
fi
[[ -n "${GSFLUENT_SIM_HOME:-}" && ! -d "$GSFLUENT_SIM_HOME" ]] && \
    errors+=("GSFLUENT_SIM_HOME does not exist: $GSFLUENT_SIM_HOME")
if [[ -n "${GSFLUENT_SIM_HOME:-}" && -d "$GSFLUENT_SIM_HOME" \
      && ! -f "$GSFLUENT_SIM_HOME/gs_simulation/watermelon/gs_simulation_building.py" ]]; then
    errors+=("$GSFLUENT_SIM_HOME doesn't look like a GaussianFluent checkout (missing gs_simulation/watermelon/gs_simulation_building.py)")
fi
[[ -n "${GSFLUENT_SIM_PYTHON:-}" && ! -x "$GSFLUENT_SIM_PYTHON" ]] && \
    errors+=("GSFLUENT_SIM_PYTHON is not executable: $GSFLUENT_SIM_PYTHON")
[[ -n "${GSFLUENT_BIN:-}" && ! -x "$GSFLUENT_BIN" ]] && \
    errors+=("GSFLUENT_BIN not executable: $GSFLUENT_BIN")
[[ ! -d "$PKG_ROOT" ]] && errors+=("PKG_ROOT does not exist: $PKG_ROOT")

if (( ${#errors[@]} > 0 )); then
    fail "config has problems:"
    for e in "${errors[@]}"; do
        printf '    - %s\n' "$e" >&2
    done
    printf '\nRe-run interactive setup with:\n  %s --setup\n' "$0" >&2
    exit 1
fi

if [[ "$MODE" == "validate" ]]; then
    ok "GSFLUENT_SIM_HOME   $GSFLUENT_SIM_HOME"
    ok "GSFLUENT_SIM_PYTHON $GSFLUENT_SIM_PYTHON"
    ok "GSFLUENT_BIN        $GSFLUENT_BIN"
    ok "PORT                $PORT"
    ok "HOST                $HOST"
    ok "LOG_FILE            $LOG_FILE"
    # Cheap deps probe to confirm the sim Python actually imports the
    # libraries the wrapper depends on.
    if "$GSFLUENT_SIM_PYTHON" -c 'import torch, warp, taichi' >/dev/null 2>&1; then
        ok "sim Python imports torch + warp + taichi"
    else
        warn "sim Python at $GSFLUENT_SIM_PYTHON can't import torch/warp/taichi — sim runs will fail"
        exit 1
    fi
    echo
    ok "config is valid; ready to launch"
    exit 0
fi

# Refuse to launch a second copy on the same port.
if command -v ss >/dev/null 2>&1; then
    if ss -tlnp 2>/dev/null | grep -qE ":${PORT}\s"; then
        fail "port $PORT already in use. Run \`pkill -f 'gsfluent serve'\` and retry."
        exit 1
    fi
elif command -v nc >/dev/null 2>&1; then
    if nc -z 127.0.0.1 "$PORT" 2>/dev/null; then
        fail "port $PORT already in use. Run \`pkill -f 'gsfluent serve'\` and retry."
        exit 1
    fi
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

# Persist the PID so the user can stop just this instance (instead of
# `pkill -f gsfluent serve` nuking every gsfluent on the host).
PID_FILE="${LOG_FILE}.pid"
echo "$PID" > "$PID_FILE"

# Wait briefly for the port to come up so the user sees a definitive
# "OK" rather than racing the healthcheck.
for _ in $(seq 1 20); do
    if command -v ss >/dev/null 2>&1 && ss -tlnp 2>/dev/null | grep -qE ":${PORT}\s"; then
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
  PID_FILE      $PID_FILE

Health check (should return {"status":"ok",...}):
  curl http://localhost:${PORT}/api/health

To stop just this instance:
  kill \$(cat $PID_FILE)

To tail the log:
  tail -f $LOG_FILE
EOF
