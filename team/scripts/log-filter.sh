#!/usr/bin/env bash
# log-filter.sh
# Colorizes and noise-filters cron/wake-claude log output for live display.
#
# Reads from stdin (pipe) or a log file argument.
#
# Usage:
#   log-filter.sh [logfile]
#   tail -f /path/to/log | log-filter.sh
#   log-filter.sh /path/to/log
#
# Noise filters (lines suppressed by default):
#   - Empty lines
#   - Duplicate adjacent lines
#   - Claude internal "thinking" noise (configurable)
#
# Color coding:
#   - Lines containing "ERROR" or "error"  -> red
#   - Lines containing "WARN" or "warn"    -> yellow
#   - Lines containing "DONE" or "DONE"    -> green
#   - Lines containing "BLOCKED"           -> red bold
#   - Lines containing "WORKING"           -> yellow
#   - Lines containing "WAITING"           -> dim
#   - Timestamp prefix [2026-...]          -> dim timestamp, normal message
#   - All other lines                      -> normal
#
# Configuration (via config.cfg):
#   PGAI_DASHBOARD_LOG_SUPPRESS_PATTERNS — colon-separated list of grep-E patterns to suppress
#
# Environment:
#   TERM=dumb  — disables all ANSI codes
#   PGAI_LOG_FILTER_VERBOSE=1 — disable noise filtering (show all lines)

# --- Argument parsing: strip --* flags before passing args to awk ---
# Recognized flags:
#   --kanban-root <path>   (sets KANBAN_ROOT override; value is consumed and stored)
#   any other --* flag     (silently discarded)
# Remaining non-flag arguments are passed through to awk as the log file path.
_REMAINING_ARGS=()
_KANBAN_ROOT_OVERRIDE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --help|-h)
      sed -n '2,34p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    --kanban-root)
      # Consume the flag and its value; store for use after env-var fallback
      _KANBAN_ROOT_OVERRIDE="${2:-}"
      shift 2
      ;;
    --*)
      # Discard any other double-dash flag (no value consumed)
      shift
      ;;
    *)
      # Non-flag argument: keep it
      _REMAINING_ARGS+=("$1")
      shift
      ;;
  esac
done

# --- Source config (non-strict) ---
# Start with env var fallback, then apply --kanban-root override if provided
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}"
if [[ -n "$_KANBAN_ROOT_OVERRIDE" ]]; then
  KANBAN_ROOT="$_KANBAN_ROOT_OVERRIDE"
fi
# Source: kanban.cfg [chain/paths] — INI format replaces legacy config.cfg
# Source ini_parser.sh for read_ini; dev_tree.sh for resolve/require helpers.
_LF_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[[ -f "${_LF_SCRIPT_DIR}/lib/ini_parser.sh" ]] && source "${_LF_SCRIPT_DIR}/lib/ini_parser.sh"
# shellcheck source=lib/dev_tree.sh
source "${_LF_SCRIPT_DIR}/lib/dev_tree.sh"
if [[ -f "${KANBAN_ROOT}/kanban.cfg" ]]; then
    export PGAI_KANBAN_PM_MODE="${PGAI_KANBAN_PM_MODE:-$(read_ini "${KANBAN_ROOT}/kanban.cfg" chain pm_mode automatic)}"
fi
unset _LF_SCRIPT_DIR
export PGAI_DEV_TREE_PATH="${PGAI_DEV_TREE_PATH:-$(resolve_global_dev_tree)}"
# Classification (a): log-filter reads log files from stdin or a file argument;
# it does not access any dev tree. Global require_dev_tree removed (D5).

# --- Centralized temp dir helpers ---
# Source temp.sh for resolver helpers (include guard in temp.sh prevents double-load).
_TEMP_SH="$(dirname "${BASH_SOURCE[0]}")/lib/temp.sh"
if [[ ! -f "$_TEMP_SH" ]]; then
  echo "ERROR: temp.sh not found: $_TEMP_SH" >&2
  exit 1
fi
# shellcheck source=lib/temp.sh
source "$_TEMP_SH"
unset _TEMP_SH

# Not using set -euo pipefail here because we are reading from a pipe
# and partial reads on pipe close should not abort the filter.

# --- ANSI color support ---
USE_COLOR=true
if [[ "${TERM:-}" == "dumb" ]] || [[ "${NO_COLOR:-}" == "1" ]]; then
  USE_COLOR=false
fi

# --- Noise filter toggle ---
VERBOSE="${PGAI_LOG_FILTER_VERBOSE:-0}"

# --- ANSI codes ---
if [[ "$USE_COLOR" == "true" ]]; then
  C_RED=$'\033[0;31m'
  C_RED_BOLD=$'\033[1;31m'
  C_YELLOW=$'\033[0;33m'
  C_GREEN=$'\033[0;32m'
  C_CYAN=$'\033[0;36m'
  C_DIM=$'\033[2m'
  C_BOLD=$'\033[1m'
  C_RESET=$'\033[0m'
else
  C_RED=""
  C_RED_BOLD=""
  C_YELLOW=""
  C_GREEN=""
  C_CYAN=""
  C_DIM=""
  C_BOLD=""
  C_RESET=""
fi

# --- Default noise patterns (lines to suppress) ---
# These are extended-grep patterns. One per entry in the array.
DEFAULT_NOISE_PATTERNS=(
  '^[[:space:]]*$'                   # blank lines
  '^\[.*\] Waiting for '             # Claude polling messages
  '^data: \[DONE\]'                  # SSE stream end markers
  '^data: {"type":"content_block'    # raw SSE JSON chunks
  '^event: '                         # SSE event lines
  '^\s*"type"\s*:\s*"ping"'          # heartbeat pings
)

# --- User-configured additional suppress patterns ---
IFS=':' read -ra USER_PATTERNS <<< "${PGAI_DASHBOARD_LOG_SUPPRESS_PATTERNS:-}"

# --- Build combined noise pattern for awk ---
# We'll use awk for the main loop to handle: colorization + dedup + filtering.
# Build a ||-joined pattern string for suppression.

build_suppress_awk() {
  # Emits an awk condition that returns 1 (suppress) if line matches noise
  local patterns=()
  for p in "${DEFAULT_NOISE_PATTERNS[@]}"; do
    patterns+=("$p")
  done
  for p in "${USER_PATTERNS[@]}"; do
    [[ -n "$p" ]] && patterns+=("$p")
  done

  # Output as newline-separated for awk BEGINFILE or a loop approach
  printf '%s\n' "${patterns[@]}"
}

NOISE_PATTERNS_FILE="$(pgai_mktemp log-filter-noise)"
trap 'rm -f "$NOISE_PATTERNS_FILE"' EXIT
build_suppress_awk > "$NOISE_PATTERNS_FILE"

# --- Run awk for colorization + filtering ---
awk \
  -v use_color="$USE_COLOR" \
  -v verbose="$VERBOSE" \
  -v c_red="$C_RED" \
  -v c_red_bold="$C_RED_BOLD" \
  -v c_yellow="$C_YELLOW" \
  -v c_green="$C_GREEN" \
  -v c_cyan="$C_CYAN" \
  -v c_dim="$C_DIM" \
  -v c_bold="$C_BOLD" \
  -v c_reset="$C_RESET" \
  -v noise_file="$NOISE_PATTERNS_FILE" \
  'BEGIN {
    # Load noise patterns
    n_noise = 0
    while ((getline pat < noise_file) > 0) {
      noise[n_noise++] = pat
    }
    close(noise_file)
    prev_line = ""
  }
  {
    line = $0

    # --- Noise filter (unless verbose) ---
    if (verbose != "1") {
      # Suppress duplicate adjacent lines
      if (line == prev_line) next

      # Suppress lines matching noise patterns
      suppress = 0
      for (i = 0; i < n_noise; i++) {
        if (line ~ noise[i]) {
          suppress = 1
          break
        }
      }
      if (suppress) next
    }
    prev_line = line

    # --- Colorize ---
    if (use_color == "true") {
      # Timestamp prefix: [2026-04-26T18:42:31+00:00] or similar ISO8601
      # Colorize the timestamp dim, rest of line normal
      colored = line
      if (line ~ /^\[20[0-9][0-9]-[0-9][0-9]-[0-9][0-9]/) {
        # Extract timestamp token
        ts_end = index(line, "]")
        if (ts_end > 0) {
          ts   = substr(line, 1, ts_end)
          rest = substr(line, ts_end + 1)
          colored = c_dim ts c_reset rest
        }
      }

      # Apply message-level colors (on the full line for matching)
      if (line ~ /[Ee][Rr][Rr][Oo][Rr]/) {
        colored = c_red colored c_reset
      } else if (line ~ /BLOCKED/) {
        colored = c_red_bold colored c_reset
      } else if (line ~ /[Ww][Aa][Rr][Nn]/) {
        colored = c_yellow colored c_reset
      } else if (line ~ /WORKING/) {
        colored = c_yellow colored c_reset
      } else if (line ~ /WAITING/) {
        colored = c_dim colored c_reset
      } else if (line ~ /DONE|finished.*DONE|state DONE/) {
        colored = c_green colored c_reset
      }

      print colored
    } else {
      print line
    }
  }' "${_REMAINING_ARGS[0]:--}"
