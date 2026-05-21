#!/usr/bin/env bash
# Interactive .env setup — walks the user through filling in the two
# required env vars (GSFLUENT_SIM_HOME, GSFLUENT_SIM_PYTHON), probes
# the system for likely candidates, validates each input, and writes
# a clean .env at the repo root.
#
# Run it directly:
#
#   ./server/setup-env.sh
#
# Or invoke through start-gsfluent-server.sh, which calls this script
# automatically the first time it finds no .env (or detects unedited
# __FILL_ME_IN__ placeholders).
set -euo pipefail

PKG_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$PKG_ROOT/.env"

bold()  { printf '\033[1m%s\033[0m\n' "$*"; }
note()  { printf '\033[36m%s\033[0m\n' "$*"; }   # cyan
warn()  { printf '\033[33mWARN:\033[0m %s\n' "$*" >&2; }
err()   { printf '\033[31mERROR:\033[0m %s\n' "$*" >&2; }
divider() { printf '\n\033[90m%s\033[0m\n' "────────────────────────────────────"; }

# ---- prompt helpers --------------------------------------------------

# Read an answer with a default. Usage: ans=$(ask "Prompt" "default")
ask() {
    local prompt="$1" default="${2:-}"
    local reply
    if [[ -n "$default" ]]; then
        read -r -p "$prompt [$default]: " reply
        printf '%s\n' "${reply:-$default}"
    else
        read -r -p "$prompt: " reply
        printf '%s\n' "$reply"
    fi
}

# Yes/no with a default. Returns 0 for yes, 1 for no.
confirm() {
    local prompt="$1" default="${2:-N}"
    local hint="[y/N]"
    [[ "$default" =~ ^[yY] ]] && hint="[Y/n]"
    local reply
    read -r -p "$prompt $hint " reply
    reply="${reply:-$default}"
    [[ "$reply" =~ ^[yY] ]]
}

# ---- guard: already configured? --------------------------------------

if [[ -f "$ENV_FILE" ]] && ! grep -q '__FILL_ME_IN__' "$ENV_FILE"; then
    bold "$ENV_FILE already exists (no placeholders)."
    if ! confirm "Overwrite?" "N"; then
        note "Leaving existing .env untouched. Edit it manually with \$EDITOR $ENV_FILE."
        exit 0
    fi
fi

# ---- header ----------------------------------------------------------

cat <<'EOF'

  ┌─────────────────────────────────────────┐
  │  gsfluent — interactive .env setup      │
  └─────────────────────────────────────────┘

  This walks through the two required env vars and writes a .env file
  the launchers source. Press Enter to accept any [default in brackets].

EOF

# ---- 1/3 : GSFLUENT_SIM_HOME -----------------------------------------

divider
bold "Step 1/3 — GaussianFluent source tree"
note "Need: a directory containing gs_simulation/watermelon/gs_simulation_building.py"
note "Don't have it? Clone from https://github.com/whc1992/GaussianFluent"
echo

# Probe for likely locations. shopt -s nullglob so empty globs don't
# leak the literal pattern through.
shopt -s nullglob
declare -a SIM_HOME_CANDIDATES=()
for pat in \
    "$HOME/GaussianFluent" \
    "$HOME/work/GaussianFluent" \
    "$HOME/Desktop/work/GaussianFluent" \
    "$HOME/projects/GaussianFluent" \
    "$(dirname "$PKG_ROOT")/GaussianFluent" \
    "/opt/GaussianFluent" \
    /data/*/GaussianFluent
do
    if [[ -d "$pat/gs_simulation/watermelon" ]]; then
        SIM_HOME_CANDIDATES+=("$pat")
    fi
done
shopt -u nullglob

SIM_HOME=""
if (( ${#SIM_HOME_CANDIDATES[@]} > 0 )); then
    bold "  Detected on this machine:"
    for i in "${!SIM_HOME_CANDIDATES[@]}"; do
        printf "    [%d] %s\n" $((i+1)) "${SIM_HOME_CANDIDATES[$i]}"
    done
    printf "    [m] enter path manually  (or just paste the path)\n\n"
    pick=$(ask "  pick" "1")
    case "$pick" in
        [mM]) SIM_HOME=$(ask "  path") ;;
        /*|~*|./*|*/*) SIM_HOME="$pick" ;;  # user pasted a path directly
        *[!0-9]*) SIM_HOME=$(ask "  path") ;;
        '') SIM_HOME="${SIM_HOME_CANDIDATES[0]}" ;;  # accept default
        *)
            idx=$((pick-1))
            if (( idx >= 0 && idx < ${#SIM_HOME_CANDIDATES[@]} )); then
                SIM_HOME="${SIM_HOME_CANDIDATES[$idx]}"
            else
                SIM_HOME=$(ask "  path")
            fi
            ;;
    esac
else
    warn "no GaussianFluent found in common locations"
    SIM_HOME=$(ask "  path to GaussianFluent")
fi

# Expand ~ if the user typed one.
SIM_HOME="${SIM_HOME/#~/$HOME}"

if [[ ! -f "$SIM_HOME/gs_simulation/watermelon/gs_simulation_building.py" ]]; then
    warn "$SIM_HOME doesn't look like a GaussianFluent checkout"
    warn "expected: gs_simulation/watermelon/gs_simulation_building.py"
    if ! confirm "  use this path anyway?" "N"; then
        err "aborting — re-run after fixing the path"
        exit 1
    fi
fi

# ---- 2/3 : GSFLUENT_SIM_PYTHON ---------------------------------------

divider
bold "Step 2/3 — Python interpreter with torch + warp + taichi"
note "This is the sim env, usually a separate conda env from the gsfluent API"
echo

declare -a PY_CANDIDATES=()
if command -v conda >/dev/null 2>&1; then
    while IFS= read -r line; do
        # Skip headers + comment lines.
        [[ "$line" =~ ^# ]] && continue
        [[ -z "$line" ]] && continue
        # Each non-base line: "<envname>  <path>" or " *  <path>" for active.
        env_path=$(awk '{print $NF}' <<<"$line")
        candidate="$env_path/bin/python"
        if [[ -x "$candidate" ]]; then
            # Test for the required deps without importing them all
            # (faster and works even for envs the wrong Python version).
            if "$candidate" -c '
import importlib.util as u, sys
for m in ("torch", "warp", "taichi"):
    if u.find_spec(m) is None:
        sys.exit(1)
' >/dev/null 2>&1; then
                PY_CANDIDATES+=("$candidate")
            fi
        fi
    done < <(conda env list 2>/dev/null)
fi

SIM_PY=""
if (( ${#PY_CANDIDATES[@]} > 0 )); then
    bold "  Conda envs with torch+warp+taichi:"
    for i in "${!PY_CANDIDATES[@]}"; do
        printf "    [%d] %s\n" $((i+1)) "${PY_CANDIDATES[$i]}"
    done
    printf "    [m] enter path manually  (or just paste the path)\n\n"
    pick=$(ask "  pick" "1")
    case "$pick" in
        [mM]) SIM_PY=$(ask "  path") ;;
        /*|~*|./*|*/*) SIM_PY="$pick" ;;
        *[!0-9]*) SIM_PY=$(ask "  path") ;;
        '') SIM_PY="${PY_CANDIDATES[0]}" ;;
        *)
            idx=$((pick-1))
            if (( idx >= 0 && idx < ${#PY_CANDIDATES[@]} )); then
                SIM_PY="${PY_CANDIDATES[$idx]}"
            else
                SIM_PY=$(ask "  path")
            fi
            ;;
    esac
else
    if command -v conda >/dev/null 2>&1; then
        warn "no conda env has all of torch + warp + taichi installed"
    else
        warn "conda not on PATH — can't auto-probe Python envs"
    fi
    note "Example: /opt/conda/envs/GaussianFluent/bin/python"
    SIM_PY=$(ask "  path to sim Python")
fi
SIM_PY="${SIM_PY/#~/$HOME}"

if [[ ! -x "$SIM_PY" ]]; then
    warn "$SIM_PY is not executable"
    if ! confirm "  use this path anyway?" "N"; then
        err "aborting — re-run after fixing the path"
        exit 1
    fi
elif ! "$SIM_PY" -c 'import torch, warp, taichi' >/dev/null 2>&1; then
    warn "$SIM_PY can't import torch+warp+taichi — sim runs will fail"
    if ! confirm "  use this Python anyway?" "N"; then
        err "aborting — re-run after fixing the path"
        exit 1
    fi
fi

# ---- 3/3 : port + write ----------------------------------------------

divider
bold "Step 3/3 — API port"
note "Default 18080 matches the README and firewall examples"
echo
PORT=$(ask "  port" "18080")
case "$PORT" in
    ''|*[!0-9]*)
        warn "not a number, defaulting to 18080"
        PORT=18080
        ;;
esac

# ---- write -----------------------------------------------------------

divider
bold "Writing $ENV_FILE"
cat > "$ENV_FILE" <<EOF
# gsfluent .env — generated by server/setup-env.sh on $(date -Iseconds)
# Re-run that script (or edit this file directly) to change paths.

# ---- REQUIRED ----
GSFLUENT_SIM_HOME=$SIM_HOME
GSFLUENT_SIM_PYTHON=$SIM_PY

# ---- network ----
PORT=$PORT
# HOST=0.0.0.0
# LOG_FILE=/tmp/gsfluent_server.log

# ---- optional ----
# GSFLUENT_BIN=/path/to/gsfluent      # default: \`command -v gsfluent\`
# GSFLUENT_MODEL_CACHE_DIR=...        # default: <repo>/work/cache/model_files
# GSFLUENT_SIM_FAST=0                 # 1 re-enables fast (but unsafe-for-some-recipes) sim flags
EOF
chmod 600 "$ENV_FILE"

note "  $ENV_FILE  (mode 600)"
echo
bold "Done."
echo
note "Start the server:    ./start-gsfluent-server.sh"
note "Validate config:     ./start-gsfluent-server.sh --validate"
echo
