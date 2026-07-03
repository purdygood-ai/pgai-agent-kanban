#!/usr/bin/env bash
# dashboard-logs.sh
# Merged, color-coded log stream for dashboard Window 1.
#
# Tails all 6 cron log files (pm, coder, writer, tester, cm, cleanup) and
# merges them into a single interleaved stream with tail -F style following.
# Each line is tagged with the agent name and current time in HH:MM:SS format.
#
# Color scheme (ANSI, suppressed when NO_COLOR=1 or TERM=dumb):
#   pm      = cyan
#   coder   = green
#   writer  = yellow
#   tester  = blue
#   cm      = magenta
#   cleanup = dim
#
# Usage:
#   dashboard-logs.sh [--kanban-root <path>]
#
# Flags:
#   --kanban-root <path>   Override the kanban root path
#   --stdout               Print the last 30 lines of each log file once to stdout
#                          and exit 0. The blocking tail -F pipeline is NOT started.
#                          Safe to call standalone from a terminal without tmux.
#
# Environment:
#   PGAI_AGENT_KANBAN_ROOT_PATH  — kanban root (default: ~/pgai_agent_kanban)
#   NO_COLOR                            — set to 1 to disable color
#
# Missing log files are handled gracefully (skipped with a warning on stderr).

# Not using set -euo pipefail — we are a long-running tail pipeline and pipe
# closes should not abort the process.

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
KANBAN_ROOT_OVERRIDE=""
STDOUT_MODE=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --kanban-root)
      KANBAN_ROOT_OVERRIDE="${2:-}"
      shift 2
      ;;
    --stdout)
      STDOUT_MODE=true
      shift
      ;;
    --*)
      # Discard unrecognized flags silently
      shift
      ;;
    *)
      shift
      ;;
  esac
done

# ---------------------------------------------------------------------------
# Resolve kanban root
# ---------------------------------------------------------------------------
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}"
if [[ -n "$KANBAN_ROOT_OVERRIDE" ]]; then
  KANBAN_ROOT="$KANBAN_ROOT_OVERRIDE"
fi

# Source project_paths lib for pp_* helpers
_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/project_paths.sh
source "${_SCRIPT_DIR}/../lib/project_paths.sh"
# shellcheck source=lib/temp.sh
source "${_SCRIPT_DIR}/../lib/temp.sh"
unset _SCRIPT_DIR

# cron-<agent>.log files live at $KANBAN_ROOT/logs/ — that's where the
# cron-suggested.txt template redirects each wake-claude.sh firing's stdout.
# (Per-firing batch logs live separately at $KANBAN_ROOT/logs/agents/.)
LOG_DIR="${KANBAN_ROOT}/logs"

# ---------------------------------------------------------------------------
# Color support
# ---------------------------------------------------------------------------
USE_COLOR=true
if [[ "${TERM:-}" == "dumb" ]] || [[ "${NO_COLOR:-}" == "1" ]]; then
  USE_COLOR=false
fi

if [[ "$USE_COLOR" == "true" ]]; then
  C_CYAN=$'\033[0;36m'
  C_GREEN=$'\033[0;32m'
  C_YELLOW=$'\033[0;33m'
  C_BLUE=$'\033[0;34m'
  C_MAGENTA=$'\033[0;35m'
  C_DIM=$'\033[2m'
  C_RESET=$'\033[0m'
else
  C_CYAN=""
  C_GREEN=""
  C_YELLOW=""
  C_BLUE=""
  C_MAGENTA=""
  C_DIM=""
  C_RESET=""
fi

# ---------------------------------------------------------------------------
# Log file definitions: agent_name:log_filename
# ---------------------------------------------------------------------------
# Verify which log files exist and collect tail targets.
# Log files: cron-pm.log, cron-coder.log, cron-writer.log, cron-tester.log,
#            cron-cm.log, cleanup.log
# ---------------------------------------------------------------------------
declare -A AGENT_COLOR
AGENT_COLOR[pm]="$C_CYAN"
AGENT_COLOR[coder]="$C_GREEN"
AGENT_COLOR[writer]="$C_YELLOW"
AGENT_COLOR[tester]="$C_BLUE"
AGENT_COLOR[cm]="$C_MAGENTA"
AGENT_COLOR[cleanup]="$C_DIM"

declare -A AGENT_LOGFILE
AGENT_LOGFILE[pm]="${LOG_DIR}/cron-pm.log"
AGENT_LOGFILE[coder]="${LOG_DIR}/cron-coder.log"
AGENT_LOGFILE[writer]="${LOG_DIR}/cron-writer.log"
AGENT_LOGFILE[tester]="${LOG_DIR}/cron-tester.log"
AGENT_LOGFILE[cm]="${LOG_DIR}/cron-cm.log"
AGENT_LOGFILE[cleanup]="${LOG_DIR}/cleanup.log"

AGENT_ORDER=(pm coder writer tester cm cleanup)

# ---------------------------------------------------------------------------
# --stdout mode: print the last 30 lines of each log file once, then exit.
# This provides a standalone snapshot of recent log activity suitable for
# terminal inspection without a running tmux session.
# ---------------------------------------------------------------------------
if [[ "$STDOUT_MODE" == "true" ]]; then
  printf '=== Recent Cron Logs (last 30 lines each) ===\n'
  for agent in "${AGENT_ORDER[@]}"; do
    logfile="${AGENT_LOGFILE[$agent]}"
    color="${AGENT_COLOR[$agent]}"
    printf '\n%s--- %s ---%s\n' "${color}" "$agent" "${C_RESET:-}"
    if [[ -f "$logfile" ]]; then
      tail -n 30 "$logfile" 2>/dev/null | \
        awk -v color="${color}" -v reset="${C_RESET:-}" -v agent="$agent" \
          '{printf "%s[%s]%s %s\n", color, agent, reset, $0}'
    else
      printf '  (no log file yet: %s)\n' "$logfile"
    fi
  done
  exit 0
fi

# ---------------------------------------------------------------------------
# Build the list of (agent, logfile) pairs that exist or can be waited on.
# tail -F will wait for files that don't exist yet and begin following when
# they appear, so we always include all 6 paths regardless of current existence.
# We do emit a notice to stderr for completely absent directories.
# ---------------------------------------------------------------------------
if [[ ! -d "$LOG_DIR" ]]; then
  echo "WARNING: log directory does not exist: ${LOG_DIR}" >&2
  echo "         Waiting for it to be created..." >&2
fi

# ---------------------------------------------------------------------------
# Use a temp file to pass the color/agent mapping into awk.
# Format: agent<TAB>color_escape<TAB>reset_escape
# ---------------------------------------------------------------------------
_DASH_TEMP="$(pgai_temp_subdir dashboard)"
AGENT_MAP_FILE="$(mktemp "${_DASH_TEMP}/logs-agents.XXXXXX")"
unset _DASH_TEMP
trap 'rm -f "$AGENT_MAP_FILE"' EXIT

for agent in "${AGENT_ORDER[@]}"; do
  logfile="${AGENT_LOGFILE[$agent]}"
  color="${AGENT_COLOR[$agent]}"
  printf '%s\t%s\t%s\n' "$agent" "$color" "$C_RESET" >> "$AGENT_MAP_FILE"
done

# ---------------------------------------------------------------------------
# Build the tail -F argument list.
# All 6 paths are passed; tail -F handles missing files gracefully by waiting.
# ---------------------------------------------------------------------------
TAIL_ARGS=()
for agent in "${AGENT_ORDER[@]}"; do
  TAIL_ARGS+=("${AGENT_LOGFILE[$agent]}")
done

# ---------------------------------------------------------------------------
# Run tail -F and pipe through awk for agent tagging and colorization.
#
# tail -F prints a header line "==> <filename> <==" each time it switches
# between files. We use these headers to track which agent is currently
# active, then prefix each subsequent line with [agent HH:MM:SS].
# ---------------------------------------------------------------------------
tail -F "${TAIL_ARGS[@]}" 2>/dev/null | \
awk \
  -v use_color="$USE_COLOR" \
  -v agent_map_file="$AGENT_MAP_FILE" \
  -v c_reset="$C_RESET" \
  -v c_dim="$C_DIM" \
  'BEGIN {
    # Load agent -> (color, reset) map from file
    while ((getline line < agent_map_file) > 0) {
      n = split(line, parts, "\t")
      if (n >= 3) {
        agent_color[parts[1]] = parts[2]
        agent_reset[parts[1]] = parts[3]
      } else if (n == 2) {
        agent_color[parts[1]] = parts[2]
        agent_reset[parts[1]] = ""
      }
    }
    close(agent_map_file)

    # Filename suffix -> agent name mapping
    # cron-pm.log -> pm, cleanup.log -> cleanup, etc.
    file_to_agent["cron-pm.log"]      = "pm"
    file_to_agent["cron-coder.log"]   = "coder"
    file_to_agent["cron-writer.log"]  = "writer"
    file_to_agent["cron-tester.log"]  = "tester"
    file_to_agent["cron-cm.log"]      = "cm"
    file_to_agent["cleanup.log"]      = "cleanup"

    current_agent = ""
  }
  {
    line = $0

    # Detect tail -F file-switch header: ==> /path/to/file <==
    if (line ~ /^==> .* <==$/) {
      # Extract the filename (basename)
      path = line
      sub(/^==> /, "", path)
      sub(/ <==$/, "", path)
      # Get basename
      n = split(path, parts, "/")
      basename = parts[n]
      if (basename in file_to_agent) {
        current_agent = file_to_agent[basename]
      } else {
        current_agent = basename
      }
      # Do not print the header line itself — just track the agent
      next
    }

    # Skip blank lines
    if (line ~ /^[[:space:]]*$/) next

    # Build the prefix tag [agent HH:MM:SS]
    cmd = "date +%H:%M:%S"
    cmd | getline ts
    close(cmd)

    tag = "[" current_agent " " ts "]"

    if (use_color == "true" && current_agent != "" && current_agent in agent_color) {
      color  = agent_color[current_agent]
      reset  = agent_reset[current_agent]
      printf "%s%s%s %s\n", color, tag, reset, line
    } else {
      printf "%s %s\n", tag, line
    }
  }'

# If tail exits (e.g., signal), print a reconnect message.
echo "(log stream ended — restart pane to reconnect)"
sleep 10
