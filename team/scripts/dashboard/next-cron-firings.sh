#!/usr/bin/env bash
# dashboard-next-cron-firings.sh
# Emits the 'Next Agent Wake (incl. stagger)' pane content for the pgai kanban
# dashboard.
#
# Reads crontab -l, parses agent-targeted cron lines, computes next work-start
# time per agent (cron fire delta + --sleep=N stagger offset, using
# team/pm-agent/lib/cron_parser.py), sorts ascending by remaining time, and
# prints the rendered pane content.
#
# Designed to run under `watch -n 5 dashboard-next-cron-firings.sh` in
# the new bottom-row right pane created by dashboard-create.sh.
#
# Output format:
#   === Next Agent Wake (incl. stagger) ===
#
#   PM       in  0:40
#   CODER    in  0:52
#   CM       in  1:04
#   WRITER   in  1:16
#   TESTER   in  1:28
#   CLEANUP  Sun 4am
#
#   (HALT: on — firings will skip work)    <- only when HALT marker is set
#
# Times display in M:SS format (e.g. "0:42", "4:32") for entries firing
# within one hour.  Entries with more than one hour remaining display a
# day+time hint (e.g. "Sun 4am") instead of a numeric countdown.
#
# Rows are sorted ascending by time-remaining so the soonest firing is first.
# Each 5-second watch refresh recomputes remaining time from the system clock.
#
# When no agent cron entries are found:
#   === Next Cron Firings ===
#
#   no cron entries found — manual mode only
#
# Usage:
#   dashboard-next-cron-firings.sh [--kanban-root <path>] [--no-color]
#
# Options:
#   --kanban-root <path>   Override the kanban root directory
#   --no-color             Disable ANSI color output
#   -h, --help             Show this help and exit
#
# Environment variables:
#   PGAI_AGENT_KANBAN_ROOT_PATH  Kanban root override
#   NO_COLOR                            Set non-empty to disable colors
#   TERM=dumb                           Also disables colors

set -euo pipefail
# shellcheck source=../lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/env_bootstrap.sh"

# ---------------------------------------------------------------------------
# Resolve script directory and library path
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEAM_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
CRON_PARSER_LIB="${TEAM_DIR}/pm-agent/lib/cron_parser.py"

# ---------------------------------------------------------------------------
# Temp-file management
# ---------------------------------------------------------------------------
# shellcheck source=lib/temp.sh
source "${SCRIPT_DIR}/../lib/temp.sh"

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
NO_COLOR_ARG=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --kanban-root)
      KANBAN_ROOT="${2:-$KANBAN_ROOT}"
      shift 2
      ;;
    --no-color)
      NO_COLOR_ARG=true
      shift
      ;;
    -h|--help)
      grep '^#' "$0" | sed 's/^# \{0,1\}//' | head -40
      exit 0
      ;;
    *)
      # Unknown arguments silently ignored for forward compatibility
      shift
      ;;
  esac
done

# ---------------------------------------------------------------------------
# Color support (honor NO_COLOR, --no-color, and TERM=dumb)
# ---------------------------------------------------------------------------
USE_COLOR=true
if [[ -n "${NO_COLOR:-}" ]] || [[ "${TERM:-}" == "dumb" ]] || [[ "$NO_COLOR_ARG" == "true" ]]; then
  USE_COLOR=false
fi

_c() {
  [[ "$USE_COLOR" != "true" ]] && return
  case "$1" in
    reset)   printf '\033[0m'    ;;
    bold)    printf '\033[1m'    ;;
    dim)     printf '\033[2m'    ;;
    cyan)    printf '\033[0;36m' ;;
    green)   printf '\033[0;32m' ;;
    yellow)  printf '\033[0;33m' ;;
    red)     printf '\033[0;31m' ;;
    white)   printf '\033[0;37m' ;;
    *)       printf ''           ;;
  esac
}

RESET="$(_c reset)"
BOLD="$(_c bold)"
DIM="$(_c dim)"
CYAN="$(_c cyan)"
GREEN="$(_c green)"
YELLOW="$(_c yellow)"
RED="$(_c red)"

# ---------------------------------------------------------------------------
# Print section header
# ---------------------------------------------------------------------------
printf '%s%s%s\n' "${CYAN}${BOLD}" "=== Next Agent Wake (incl. stagger) ===" "$RESET"
printf '\n'

# ---------------------------------------------------------------------------
# Verify python3 is available
# ---------------------------------------------------------------------------
if ! command -v python3 &>/dev/null; then
  printf '%sno cron entries found \xe2\x80\x94 manual mode only%s\n' "${DIM}" "$RESET" >&2
  exit 0
fi

# ---------------------------------------------------------------------------
# Verify cron_parser.py library exists
# ---------------------------------------------------------------------------
if [[ ! -f "$CRON_PARSER_LIB" ]]; then
  printf '%sno cron entries found \xe2\x80\x94 manual mode only%s\n' "${DIM}" "$RESET" >&2
  exit 0
fi

# ---------------------------------------------------------------------------
# Read crontab (non-zero exit means no crontab installed — treat as empty)
# ---------------------------------------------------------------------------
CRONTAB_TEXT=""
CRONTAB_TEXT=$(crontab -l 2>/dev/null) || CRONTAB_TEXT=""

# ---------------------------------------------------------------------------
# Capture a single reference timestamp so all per-agent computations in this
# render tick use the same "now".  Passed to the Python script as argv[2].
# ---------------------------------------------------------------------------
NOW_TS="$(date +%s)"

# ---------------------------------------------------------------------------
# Parse and format via cron_parser.py
#
# Pass crontab text via stdin using a temp file to avoid heredoc/pipe
# interaction issues.  The Python script:
#   1. Imports next_firings from cron_parser.py
#   2. Returns structured "TYPE AGENT LABEL" lines for the shell to render
#   3. Returns "NO_AGENTS" when no kanban entries are found
#
# Output line formats:
#   INT AGENT M:SS     — time-remaining in M:SS (< 1 hour from now)
#   STR AGENT sentinel — day+time hint for far-future or weekly entries
#   NO_AGENTS          — no matching entries in crontab
# ---------------------------------------------------------------------------
_DASH_TEMP="$(pgai_temp_subdir dashboard)"
_PARSER_SCRIPT="$(mktemp "${_DASH_TEMP}/ncf_parser_XXXXXX.py")"
_PARSER_ERR="$(mktemp "${_DASH_TEMP}/ncf_parser_err.XXXXXX")"
unset _DASH_TEMP
# Ensure the temp files are removed on exit regardless of how we exit
# shellcheck disable=SC2064
trap "rm -f '${_PARSER_SCRIPT}' '${_PARSER_ERR}'" EXIT

cat > "$_PARSER_SCRIPT" << 'PYEOF'
import sys
import os
from datetime import datetime

lib_path = sys.argv[1]
now_ts   = float(sys.argv[2]) if len(sys.argv) > 2 else None

lib_dir = os.path.dirname(os.path.abspath(lib_path))
sys.path.insert(0, lib_dir)

from cron_parser import next_firings

# Use the shell-captured timestamp so all agents share the same render clock.
# This prevents sub-second drift between the crontab parse and the display.
if now_ts is not None:
    now = datetime.fromtimestamp(now_ts)
else:
    now = datetime.now()

crontab_text = sys.stdin.read()

try:
    firings = next_firings(crontab_text, now=now)
except Exception as e:
    sys.stderr.write(f"WARNING: cron_parser error: {e}\n")
    firings = {}

if not firings:
    print("NO_AGENTS")
    sys.exit(0)

# Threshold in seconds: entries with >= this many seconds remaining are
# displayed as a day+time hint rather than a MM:SS countdown.
FAR_FUTURE_THRESHOLD = 3600  # 1 hour

_DOW_NAMES = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']

def _day_time_hint(secs_remaining):
    """Convert seconds-remaining to a 'DayName HHam/pm' hint string."""
    from datetime import timedelta
    firing_dt = now + timedelta(seconds=secs_remaining)
    # Python weekday(): Mon=0..Sun=6 -> cron: Sun=0..Sat=6
    cron_dow = (firing_dt.weekday() + 1) % 7
    dow_name = _DOW_NAMES[cron_dow]
    hour = firing_dt.hour
    if hour == 0:
        hour_str = '12am'
    elif hour < 12:
        hour_str = f'{hour}am'
    elif hour == 12:
        hour_str = '12pm'
    else:
        hour_str = f'{hour - 12}pm'
    return f'{dow_name} {hour_str}'

def format_remaining(secs):
    """Format seconds as M:SS (e.g. 102 -> '1:42')."""
    s = int(secs)
    m = s // 60
    ss = s % 60
    return f"{m}:{ss:02d}"

# Separate integer entries (sortable seconds) from string sentinels (weekly)
int_entries = [(agent, secs) for agent, secs in firings.items() if isinstance(secs, int)]
str_entries = [(agent, label) for agent, label in firings.items() if isinstance(label, str)]

# Sort int entries ascending by seconds remaining (soonest first)
int_entries.sort(key=lambda x: x[1])

for agent, secs in int_entries:
    if secs >= FAR_FUTURE_THRESHOLD:
        # Show day+time hint for far-future entries instead of long countdown
        print(f"STR {agent.upper()} {_day_time_hint(secs)}")
    else:
        print(f"INT {agent.upper()} {format_remaining(secs)}")

# String sentinels (weekly/infrequent — already a day+time label from parser)
for agent, sentinel in str_entries:
    print(f"STR {agent.upper()} {sentinel}")
PYEOF

# Run the parser — pass crontab text via stdin; argv[2] is the render-time timestamp
PYTHON_OUTPUT=""
PYTHON_OUTPUT=$(printf '%s' "$CRONTAB_TEXT" | python3 "$_PARSER_SCRIPT" "$CRON_PARSER_LIB" "$NOW_TS" 2>"$_PARSER_ERR") || {
  # Python exited non-zero — log stderr and fall through to empty result
  if [[ -s "$_PARSER_ERR" ]]; then
    cat "$_PARSER_ERR" >&2
  fi
  PYTHON_OUTPUT="NO_AGENTS"
}
rm -f "$_PARSER_ERR"

# ---------------------------------------------------------------------------
# Render output
# ---------------------------------------------------------------------------
if [[ "$PYTHON_OUTPUT" == "NO_AGENTS" ]] || [[ -z "$PYTHON_OUTPUT" ]]; then
  printf '%sno cron entries found \xe2\x80\x94 manual mode only%s\n' "${DIM}" "$RESET"
else
  # Parse and render each line from the Python output
  while IFS= read -r py_line; do
    [[ -z "$py_line" ]] && continue

    entry_type="${py_line%% *}"    # INT or STR
    rest="${py_line#* }"           # "AGENT label"
    agent="${rest%% *}"            # e.g. "CODER"
    time_label="${rest#* }"        # e.g. "4:00" or "Sun 4am"

    case "$entry_type" in
      INT)
        # Regular sub-hourly schedule: show agent name + "in M:SS"
        printf '%s%-8s%s in %s%s%s\n' \
          "${GREEN}" "$agent" "$RESET" \
          "${BOLD}" "$time_label" "$RESET"
        ;;
      STR)
        # Weekly or infrequent sentinel: display the label directly (dim)
        printf '%s%-8s%s %s%s%s\n' \
          "${YELLOW}" "$agent" "$RESET" \
          "${DIM}" "$time_label" "$RESET"
        ;;
      *)
        # Unknown type — skip silently (forward compatibility)
        ;;
    esac
  done <<< "$PYTHON_OUTPUT"
fi

# ---------------------------------------------------------------------------
# HALT footer — shown at the bottom when the HALT marker file exists.
# Displayed even when agents are listed so operators know firings will skip.
# ---------------------------------------------------------------------------
HALT_FILE="${KANBAN_ROOT}/HALT"
if [[ -f "$HALT_FILE" ]]; then
  printf '\n'
  printf '%s(HALT: on \xe2\x80\x94 firings will skip work)%s\n' "${RED}" "$RESET"
fi
