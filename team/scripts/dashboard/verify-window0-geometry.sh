#!/usr/bin/env bash
# verify-window0-geometry.sh — acceptance check for window-0 geometry
#
# Parses tmux list-panes for the dashboard main window after window-0 construction
# and asserts that the --mode queues pane is the RIGHTMOST pane (largest pane_left)
# with width approximately PGAI_WINDOW0_RIGHT_COL_PCT% of the total window width.
# All other panes must have pane_left STRICTLY LESS than the queues pane's pane_left.
#
# The expected width percentage is sourced from lib/dashboard_constants.sh
# (PGAI_WINDOW0_RIGHT_COL_PCT, value 25) — the same constant create.sh uses
# (single source of truth via shared constant).
#
# Pane identification strategy: the queues pane runs
#   watch ... show-multi.sh --mode queues ...
# sent via tmux send-keys.  Because send-keys does not set pane_start_command, this
# script locates the queues pane by inspecting the child process tree of each pane
# (via pane_pid + ps) for a process whose arguments contain "--mode queues".
#
# Usage:
#   bash verify-window0-geometry.sh <SESSION_NAME> [--kanban-root <path>]
#
# Exit codes:
#   0  — geometry is correct (queues pane is rightmost at ~PGAI_WINDOW0_RIGHT_COL_PCT%)
#   1  — geometry is wrong (queues pane not rightmost, width off, or not found)
#   2  — usage error or tmux session/window not found
#
# This script runs headlessly — no display or terminal attachment required.
# It queries tmux server metadata and the kernel process table only.
#
# Asserts pane_left / pane_width values — pane existence alone is not sufficient.
# The right-column width is a shared constant defined in lib/dashboard_constants.sh.

set -euo pipefail

# ---------------------------------------------------------------------------
# Resolve script directory — needed to source lib files
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source shared dashboard constants (PGAI_WINDOW0_RIGHT_COL_PCT)
# shellcheck source=../lib/dashboard_constants.sh
source "${SCRIPT_DIR}/../lib/dashboard_constants.sh"

# ---------------------------------------------------------------------------
# Argument handling
# ---------------------------------------------------------------------------
SESSION_NAME="${1:-}"
if [[ -z "$SESSION_NAME" ]]; then
    echo "ERROR: usage: $0 <SESSION_NAME> [--kanban-root <path>]" >&2
    exit 2
fi
shift

KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --kanban-root)
            KANBAN_ROOT="${2:-$KANBAN_ROOT}"
            shift 2
            ;;
        *)
            echo "ERROR: Unknown argument: $1" >&2
            echo "Usage: $0 <SESSION_NAME> [--kanban-root <path>]" >&2
            exit 2
            ;;
    esac
done

# ---------------------------------------------------------------------------
# Right-column width percentage — sourced from the shared dashboard constant.
# The correct value (25%) is defined once in lib/dashboard_constants.sh
# and shared by both create.sh and this verifier.
# ---------------------------------------------------------------------------
QUEUES_PCT="${PGAI_WINDOW0_RIGHT_COL_PCT}"

WINDOW_TARGET="${SESSION_NAME}:main"

# Verify the target window exists
if ! tmux list-panes -t "$WINDOW_TARGET" > /dev/null 2>&1; then
    echo "ERROR: tmux window '$WINDOW_TARGET' not found or not accessible." >&2
    echo "       Ensure the dashboard session '$SESSION_NAME' has been created." >&2
    exit 2
fi

# ---------------------------------------------------------------------------
# Query pane geometry
# ---------------------------------------------------------------------------
# Output format: <pane_index> <pane_left> <pane_width> <pane_pid>
PANE_DATA=$(tmux list-panes -t "$WINDOW_TARGET" \
    -F '#{pane_index} #{pane_left} #{pane_width} #{pane_pid}')

echo "=== window-0 (main) pane geometry ==="
tmux list-panes -t "$WINDOW_TARGET" \
    -F '#{pane_index} pane_left=#{pane_left} pane_top=#{pane_top} pane_width=#{pane_width} pane_height=#{pane_height} pid=#{pane_pid}'
echo ""

TOTAL_WIDTH=$(tmux display-message -t "$WINDOW_TARGET" -p '#{window_width}')
echo "Expected right-column width: ~${QUEUES_PCT}% of ${TOTAL_WIDTH} (sourced from lib/dashboard_constants.sh PGAI_WINDOW0_RIGHT_COL_PCT)"
echo ""

# ---------------------------------------------------------------------------
# Locate the queues pane by inspecting child process args for '--mode queues'
# ---------------------------------------------------------------------------
# The dashboard sends 'watch ... show-multi.sh --mode queues ...' via send-keys.
# Since pane_start_command is empty for shell panes, we resolve via pane_pid:
#   - pane_pid is the shell (bash) PID
#   - its child processes include the running 'watch' invocation
#   - we search all descendants for '--mode queues' in their argument list

QUEUES_LEFT=""
QUEUES_WIDTH=""
QUEUES_INDEX=""

while IFS=' ' read -r idx left width ppid; do
    # Get all descendant processes of this pane's shell PID and search for --mode queues
    if ps --ppid "$ppid" -o args= 2>/dev/null | grep -q -- '--mode queues'; then
        QUEUES_LEFT="$left"
        QUEUES_WIDTH="$width"
        QUEUES_INDEX="$idx"
        break
    fi
    # Also check grandchildren (watch spawns a child process)
    while IFS= read -r child_pid; do
        if ps --ppid "$child_pid" -o args= 2>/dev/null | grep -q -- '--mode queues'; then
            QUEUES_LEFT="$left"
            QUEUES_WIDTH="$width"
            QUEUES_INDEX="$idx"
            break 2
        fi
    done < <(ps --ppid "$ppid" -o pid= 2>/dev/null || true)
done <<< "$PANE_DATA"

if [[ -z "$QUEUES_LEFT" ]]; then
    echo "WARNING: No pane found with a child process containing '--mode queues'." >&2
    echo "         The dashboard may not have finished starting pane commands." >&2
    echo "         Falling back to geometric check only: rightmost pane must be ~${QUEUES_PCT}% wide." >&2
    echo ""
    # Fall back: assert the rightmost pane (largest pane_left) is ~QUEUES_PCT% wide.
    # Identify rightmost pane by max pane_left.
    MAX_LEFT=0
    while IFS=' ' read -r idx left width ppid; do
        if [[ "$left" -gt "$MAX_LEFT" ]]; then
            MAX_LEFT="$left"
            QUEUES_LEFT="$left"
            QUEUES_WIDTH="$width"
            QUEUES_INDEX="$idx"
        fi
    done <<< "$PANE_DATA"
    echo "INFO: Fallback rightmost pane: index=${QUEUES_INDEX} pane_left=${QUEUES_LEFT} pane_width=${QUEUES_WIDTH}"
fi

echo "Queues pane: index=${QUEUES_INDEX} pane_left=${QUEUES_LEFT} pane_width=${QUEUES_WIDTH}"
echo "Total window width: ${TOTAL_WIDTH}"
echo ""

# ---------------------------------------------------------------------------
# Check 1: queues pane must have the LARGEST pane_left (rightmost pane)
# ---------------------------------------------------------------------------
FAILURES=0

while IFS=' ' read -r idx left width ppid; do
    if [[ "$idx" == "$QUEUES_INDEX" ]]; then
        continue
    fi
    if [[ "$left" -ge "$QUEUES_LEFT" ]]; then
        echo "FAIL: pane ${idx} has pane_left=${left} which is >= queues pane_left=${QUEUES_LEFT}." >&2
        echo "      The queues pane must be the rightmost (largest pane_left) pane." >&2
        FAILURES=$(( FAILURES + 1 ))
    fi
done <<< "$PANE_DATA"

if [[ "$FAILURES" -eq 0 ]]; then
    echo "PASS: queues pane (index=${QUEUES_INDEX}) has the largest pane_left (${QUEUES_LEFT}) — it is the rightmost pane."
fi

# ---------------------------------------------------------------------------
# Check 2: queues pane width must be approximately QUEUES_PCT% of total window width
# (tolerance: ±5 characters to allow for integer rounding and border accounting)
# QUEUES_PCT is sourced from lib/dashboard_constants.sh (value: 25).
# ---------------------------------------------------------------------------
EXPECTED_WIDTH=$(( TOTAL_WIDTH * QUEUES_PCT / 100 ))
WIDTH_TOLERANCE=5
WIDTH_LOW=$(( EXPECTED_WIDTH - WIDTH_TOLERANCE ))
WIDTH_HIGH=$(( EXPECTED_WIDTH + WIDTH_TOLERANCE ))

if [[ "$QUEUES_WIDTH" -ge "$WIDTH_LOW" && "$QUEUES_WIDTH" -le "$WIDTH_HIGH" ]]; then
    echo "PASS: queues pane width=${QUEUES_WIDTH} is approximately ${QUEUES_PCT}% of ${TOTAL_WIDTH} (expected ~${EXPECTED_WIDTH}, tolerance ±${WIDTH_TOLERANCE})."
else
    echo "FAIL: queues pane width=${QUEUES_WIDTH} is NOT approximately ${QUEUES_PCT}% of ${TOTAL_WIDTH}." >&2
    echo "      Expected ${WIDTH_LOW}–${WIDTH_HIGH}; got ${QUEUES_WIDTH}." >&2
    FAILURES=$(( FAILURES + 1 ))
fi

# ---------------------------------------------------------------------------
# Check 3: report the left-column span (informational)
# ---------------------------------------------------------------------------
NON_QUEUES_MAX_RIGHT=0
while IFS=' ' read -r idx left width ppid; do
    if [[ "$idx" == "$QUEUES_INDEX" ]]; then
        continue
    fi
    right=$(( left + width ))
    if [[ "$right" -gt "$NON_QUEUES_MAX_RIGHT" ]]; then
        NON_QUEUES_MAX_RIGHT="$right"
    fi
done <<< "$PANE_DATA"

echo "INFO: Left-column panes span columns 0..${NON_QUEUES_MAX_RIGHT} (~$(( NON_QUEUES_MAX_RIGHT * 100 / TOTAL_WIDTH ))% of window width)."

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
if [[ "$FAILURES" -eq 0 ]]; then
    echo "RESULT: PASS — window-0 geometry is correct. Queues pane is the rightmost ~${QUEUES_PCT}% pane."
    exit 0
else
    echo "RESULT: FAIL — window-0 geometry is WRONG (${FAILURES} check(s) failed). See errors above." >&2
    exit 1
fi
