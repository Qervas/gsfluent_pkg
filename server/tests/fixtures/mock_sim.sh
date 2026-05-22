#!/usr/bin/env bash
# Configurable fake sim binary for integration tests.
#
# Per the spec (Section 5 "mock_sim.sh fixture - the unlock"), this
# script is parametrized via env vars so every dangerous-path test can
# be deterministic and CI-able with no real GPU.
#
# Env knobs (all optional, all with defaults):
#   MOCK_SIM_FRAMES=3              how many frame_*.ply stubs to emit
#   MOCK_SIM_DELAY_SEC=0.0         per-frame pause (cancel / timeout tests)
#   MOCK_SIM_IGNORE_SIGTERM=0      trap SIGTERM (SIGKILL escalation tests)
#   MOCK_SIM_EXIT=0                final exit code
#   MOCK_SIM_STDERR_PATTERN=       inject a sim-style stderr line (classifier tests)
#                                   Examples: "out of memory" / "CFL violation"
#                                             / "illegal memory access" / "NaN positions"
#
# CLI: the same args the real run_sim.sh accepted, so AsyncioRunManager
# can use this fixture as a drop-in via env-var override.
#   bash mock_sim.sh <model_dir> --config <recipe.json> \
#                    --particles N --output <run_name>

set -euo pipefail

MODEL_DIR=""
CONFIG=""
PARTICLES=""
OUTPUT=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --config)    CONFIG="$2"; shift 2 ;;
        --particles) PARTICLES="$2"; shift 2 ;;
        --output)    OUTPUT="$2"; shift 2 ;;
        -*)          echo "mock_sim: unknown option: $1" >&2; exit 2 ;;
        *)
            if [[ -z "$MODEL_DIR" ]]; then
                MODEL_DIR="$1"; shift
            else
                echo "mock_sim: extra positional: $1" >&2; exit 2
            fi
            ;;
    esac
done

FRAMES="${MOCK_SIM_FRAMES:-3}"
DELAY="${MOCK_SIM_DELAY_SEC:-0.0}"
IGNORE_SIGTERM="${MOCK_SIM_IGNORE_SIGTERM:-0}"
EXIT_CODE="${MOCK_SIM_EXIT:-0}"
STDERR_PATTERN="${MOCK_SIM_STDERR_PATTERN:-}"

# Resolve output dirs the same way the real script did.
# server/tests/fixtures -> server/tests -> server -> repo root.
PKG_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
LIB_DIR="$PKG_ROOT/work/library/sequences/$OUTPUT"
FRAMES_DIR="$LIB_DIR/frames"
mkdir -p "$FRAMES_DIR"

# Preserve the recipe so downstream tests can read it (mirrors run_sim.sh).
if [[ -n "$CONFIG" && -f "$CONFIG" ]]; then
    cp -f "$CONFIG" "$LIB_DIR/recipe.json"
fi

# Optional: trap SIGTERM and ignore it. This is how we exercise the
# SIGKILL escalation in test_sigterm_ignoring_sim_gets_sigkill.py -
# the run manager sends SIGTERM to the PG, this script swallows it,
# after the grace period the manager sends SIGKILL.
if [[ "$IGNORE_SIGTERM" == "1" ]]; then
    trap 'echo "mock_sim: trapped SIGTERM (ignoring per MOCK_SIM_IGNORE_SIGTERM=1)" >&2' TERM
fi

echo "mock_sim: starting (frames=$FRAMES delay=$DELAY ignore_sigterm=$IGNORE_SIGTERM exit=$EXIT_CODE)"

i=0
while [[ "$i" -lt "$FRAMES" ]]; do
    printf "mock frame %d\n" "$i" > "$FRAMES_DIR/$(printf 'frame_%04d.ply' "$i")"
    echo "mock_sim: emitted frame $i"
    i=$((i + 1))
    if [[ "$DELAY" != "0" && "$DELAY" != "0.0" ]]; then
        # `sleep` accepts fractional seconds on coreutils sleep.
        sleep "$DELAY" &
        wait $!
    fi
done

# Optionally inject a sim-style stderr line so the classifier kicks in.
if [[ -n "$STDERR_PATTERN" ]]; then
    echo "mock_sim STDERR: $STDERR_PATTERN" >&2
fi

echo "mock_sim: exiting with rc=$EXIT_CODE"
exit "$EXIT_CODE"
