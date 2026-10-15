#!/usr/bin/env bash
# status-band.sh
# Per-agent status band for the schedule window: one row per roster agent
# answering what that lane last did, when it last fired, and when it fires
# next -- so an operator reads a short verdict instead of a raw log tail.
#
# The roster (which agents exist, and their NEXT countdown/label) comes
# from the exact same data path the roster band renderer uses
# (team/pgai_agent_kanban/dashboard/roster_entries.py, fetched through
# lib/roster_band.sh's roster_band_fetch_entries) -- never a hardcoded
# agent tuple, and never a second copy of the NEXT computation: this
# script strips the leading "<AGENT> " token off the SAME display text
# roster_band_fetch_entries produces, so a row's NEXT column is always
# byte-identical to what the roster band shows for that agent under an
# identical crontab and clock.
#
# LAST FIRED and LAST RESULT are read from the agent's own cron log
# ($KANBAN_ROOT/logs/cron-<agent>.log, lower-cased -- the same log
# cron-tail.sh tails and event-band.sh merges). LAST RESULT is distilled
# from the wake's own terminal "done: completed=N ... reason=\"...\""
# line (team/scripts/lib/wake_driver.sh), or, when the wake never reached
# that line this firing, the line that explains why:
#   - "completed <N>"       -- done: completed=<N> with N >= 1
#   - "idle (no BACKLOG)"   -- done: completed=0, no HALT/contention reason
#   - "HALT skip"           -- a HALT (or per-project HALT) skip line
#   - "contended"           -- "repo lock held by ... deferring for an
#                              end-of-iteration retry" (no done: line this
#                              firing -- the lock holder pre-empted it)
# Exactly one of these four renders; the raw log line is never shown.
#
# An agent whose cron log does not exist yet (a freshly installed
# schedule entry that has not fired once) renders the literal value
# "no log yet" in both columns rather than being dropped from the band.
#
# Output shape (default, one line per roster agent):
#   AGENT      LAST FIRED           LAST RESULT          NEXT
#   PM         2026-09-19 02:01:11  idle (no BACKLOG)     :12 (in 0:40)
#   CODER      no log yet           no log yet            :24 (in 1:52)
#   OVERWATCH  2026-09-18 03:30:05  contended             Thu 3am
#
# When the crontab resolves no roster agents at all:
#   AGENT      LAST FIRED           LAST RESULT           NEXT
#   no roster agents found — manual mode only
#
# Usage:
#   status-band.sh [--kanban-root <path>] [--no-color]
#
# Options:
#   --kanban-root <path>   Override the kanban root (default:
#                          PGAI_AGENT_KANBAN_ROOT_PATH or ~/pgai_agent_kanban)
#   --no-color             Disable ANSI color output
#   -h, --help             Show this help and exit
#
# Log path pattern read per agent:
#   $KANBAN_ROOT/logs/cron-<agent>.log
#
# Environment:
#   PGAI_AGENT_KANBAN_ROOT_PATH  — kanban root directory
#   NO_COLOR                     — set to any non-empty value to disable color
#   TERM=dumb                    — also disables color
#
# Runs standalone outside tmux: prints the current band once to stdout and
# exits 0 (no polling, no tail -F). A hosting pane re-renders it by
# re-invoking this script on its own refresh cadence, the same pattern
# every other renderer in this directory uses.

set -euo pipefail
# shellcheck source=../lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/env_bootstrap.sh"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEAM_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
CRON_PARSER_LIB="${TEAM_DIR}/pm-agent/lib/cron_parser.py"
ROSTER_ENTRIES_PY="${TEAM_DIR}/pgai_agent_kanban/dashboard/roster_entries.py"

# Source shared ANSI color primitive (single home for the color-name ->
# escape-code table -- see team/scripts/lib/script_common.sh).
# shellcheck source=../lib/script_common.sh
source "${SCRIPT_DIR}/../lib/script_common.sh"

# Source the shared roster fetcher -- the SAME data path the roster band
# renderer uses (roster_entries.py, invoked via roster_band_fetch_entries)
# so this band's agent list and NEXT text can never disagree with the
# roster band's own. Only roster_band_fetch_entries is called here; the
# packing/rendering half of this library (roster_band_pack/render, which
# needs next_fire_viewport.sh) is not used -- the status band always
# renders one row per agent, never a packed multi-entry line.
# shellcheck source=lib/roster_band.sh
source "${SCRIPT_DIR}/lib/roster_band.sh"

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
KANBAN_ROOT_OVERRIDE=""
NO_COLOR_ARG=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --kanban-root)
      KANBAN_ROOT_OVERRIDE="${2:-}"
      shift 2
      ;;
    --no-color)
      NO_COLOR_ARG=true
      shift
      ;;
    -h|--help)
      # Read the whole header via mapfile so grep/sed always finish writing
      # before anything reads their output, then slice to the line limit
      # locally -- head -N as a pipe stage can close its input early and
      # SIGPIPE the writers under pipefail (docs/coding-standards.md, "A
      # help path prints completely and exits 0").
      mapfile -t __help_lines < <(grep '^#' "$0" | grep -v '^#!' | sed 's/^# \{0,1\}//')
      printf '%s\n' "${__help_lines[@]:0:64}"
      exit 0
      ;;
    *)
      # Unknown arguments silently ignored for forward compatibility,
      # consistent with the sibling dashboard renderers.
      shift
      ;;
  esac
done

# ---------------------------------------------------------------------------
# Resolve kanban root
# ---------------------------------------------------------------------------
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
if [[ -n "$KANBAN_ROOT_OVERRIDE" ]]; then
  KANBAN_ROOT="$KANBAN_ROOT_OVERRIDE"
fi
LOG_DIR="${KANBAN_ROOT}/logs"

# ---------------------------------------------------------------------------
# Color support (honor NO_COLOR, --no-color, and TERM=dumb)
# ---------------------------------------------------------------------------
USE_COLOR=true
if [[ -n "${NO_COLOR:-}" ]] || [[ "${TERM:-}" == "dumb" ]] || [[ "$NO_COLOR_ARG" == "true" ]]; then
  USE_COLOR=false
fi

RESET="$(ansi_code reset)"
BOLD="$(ansi_code bold)"
DIM="$(ansi_code dim)"

# ---------------------------------------------------------------------------
# Column widths -- fixed so rows line up; AGENT and LAST RESULT are sized
# for the longest known token/verdict, LAST FIRED for the fixed
# "YYYY-MM-DD HH:MM:SS" shape, NEXT is left unpadded (last column).
# ---------------------------------------------------------------------------
_COL_AGENT_W=10
_COL_FIRED_W=19
_COL_RESULT_W=18

_status_band_row() {
  local agent="$1" fired="$2" result="$3" next="$4"
  printf '%-*s %-*s %-*s %s\n' \
    "$_COL_AGENT_W" "$agent" \
    "$_COL_FIRED_W" "$fired" \
    "$_COL_RESULT_W" "$result" \
    "$next"
}

# ---------------------------------------------------------------------------
# _status_band_distill <log_file>
#
# Populates STATUS_BAND_LAST_FIRED and STATUS_BAND_LAST_RESULT from the
# tail of <log_file> (the most recent activity), never printing anything.
#
# LAST FIRED is the timestamp of the most recent line carrying a
# parseable leading "[ISO8601] " prefix (team/scripts/lib/wake_bootstrap.sh's
# log() function) -- the log's own last heartbeat, formatted
# "YYYY-MM-DD HH:MM:SS".
#
# LAST RESULT is distilled by scanning from the end of the file for the
# first line matching one of three shapes, in the order encountered (a
# real wake log only ever ends in one of them for a given firing):
#   1. a lock-contention deferral line               -> "contended"
#   2. a HALT / per-project-HALT skip line            -> "HALT skip"
#   3. the wake's own "done: completed=N ... reason=" line
#        N >= 1                                       -> "completed N"
#        N == 0, reason names HALT                    -> "HALT skip"
#        N == 0, otherwise (no more BACKLOG tasks, a
#          project-cap/task-cap stop, etc.)            -> "idle (no BACKLOG)"
#
# Scan window is capped to the log's last 5000 lines so a years-old,
# ever-appended cron log never makes a render tick slow.
#
# Both outputs default to "no log yet" when <log_file> does not exist,
# "no data yet" / "no result yet" when it exists but no matching line was
# found within the scan window (e.g. a firing still in progress, or a log
# that predates the timestamped log() convention) -- never blank, and
# never a raw log line.
# ---------------------------------------------------------------------------
_status_band_distill() {
  local log_file="$1"
  STATUS_BAND_LAST_FIRED="no log yet"
  STATUS_BAND_LAST_RESULT="no log yet"

  [[ -f "$log_file" ]] || return 0

  STATUS_BAND_LAST_FIRED="no data yet"
  STATUS_BAND_LAST_RESULT="no result yet"

  local line
  while IFS= read -r line; do
    [[ -z "$line" ]] && continue

    if [[ "$STATUS_BAND_LAST_FIRED" == "no data yet" ]] \
       && [[ "$line" =~ ^\[([0-9]{4}-[0-9]{2}-[0-9]{2})T([0-9]{2}:[0-9]{2}:[0-9]{2})[^]]*\] ]]; then
      STATUS_BAND_LAST_FIRED="${BASH_REMATCH[1]} ${BASH_REMATCH[2]}"
    fi

    if [[ "$STATUS_BAND_LAST_RESULT" == "no result yet" ]]; then
      if [[ "$line" =~ repo\ lock\ held\ by.*deferring\ for\ an\ end-of-iteration\ retry ]]; then
        STATUS_BAND_LAST_RESULT="contended"
      elif [[ "$line" =~ HALT[-_A-Za-z]*\ flag\ detected ]] \
        || [[ "$line" =~ per-project\ HALT\ present ]]; then
        STATUS_BAND_LAST_RESULT="HALT skip"
      elif [[ "$line" =~ done:\ completed=([0-9]+).*reason=\"([^\"]*)\" ]]; then
        local _sbd_count="${BASH_REMATCH[1]}" _sbd_reason="${BASH_REMATCH[2]}"
        if [[ "$_sbd_count" =~ ^0*[1-9][0-9]*$ ]]; then
          STATUS_BAND_LAST_RESULT="completed ${_sbd_count}"
        elif [[ "$_sbd_reason" == *HALT* ]]; then
          STATUS_BAND_LAST_RESULT="HALT skip"
        else
          STATUS_BAND_LAST_RESULT="idle (no BACKLOG)"
        fi
      fi
    fi

    if [[ "$STATUS_BAND_LAST_FIRED" != "no data yet" ]] \
       && [[ "$STATUS_BAND_LAST_RESULT" != "no result yet" ]]; then
      break
    fi
  done < <(tail -n 5000 "$log_file" | tac)

  # The while loop's own exit status is whatever its last executed body
  # command returned (bash's documented behaviour for `while`/`until`) --
  # here that is the `break` guard's compound test. A log whose scanned
  # window never satisfies both halves (a log mid-firing, freshly
  # created, or carrying only stagger/timing lines) leaves that test
  # false on the final iteration, so the loop -- and with it this
  # function, whose last statement the loop is -- would otherwise return
  # non-zero and abort an unguarded caller under `set -euo pipefail`.
  # STATUS_BAND_LAST_FIRED / STATUS_BAND_LAST_RESULT are already set
  # correctly (to their placeholders or a real distilled value) by this
  # point regardless of how the loop ended, so an explicit success
  # return is always correct here.
  return 0
}

# ---------------------------------------------------------------------------
# Build the roster via the shared fetcher -- the same data path the roster
# band renderer uses, so agent identity, order, and NEXT text can never
# drift from what that band shows for the same crontab and clock.
# ---------------------------------------------------------------------------
CRONTAB_TEXT=""
CRONTAB_TEXT=$(crontab -l 2>/dev/null) || CRONTAB_TEXT=""
NOW_TS="$(date +%s)"

roster_band_fetch_entries "$ROSTER_ENTRIES_PY" "$CRON_PARSER_LIB" "$CRONTAB_TEXT" "$NOW_TS"

# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------
printf '%s%s%s\n' "${BOLD}" "$(_status_band_row AGENT 'LAST FIRED' 'LAST RESULT' 'NEXT')" "$RESET"

if [[ ${#ROSTER_BAND_AGENTS[@]} -eq 0 ]]; then
  printf '%sno roster agents found \xe2\x80\x94 manual mode only%s\n' "${DIM}" "$RESET"
  exit 0
fi

for _sb_i in "${!ROSTER_BAND_AGENTS[@]}"; do
  _sb_agent="${ROSTER_BAND_AGENTS[$_sb_i]}"
  _sb_display="${ROSTER_BAND_DISPLAY[$_sb_i]}"
  # NEXT is the same display text roster_band_fetch_entries built for this
  # agent, minus its own leading "<AGENT> " token -- never recomputed.
  _sb_next="${_sb_display#"${_sb_agent}" }"

  _sb_log_file="${LOG_DIR}/cron-${_sb_agent,,}.log"
  _status_band_distill "$_sb_log_file"

  _status_band_row "$_sb_agent" "$STATUS_BAND_LAST_FIRED" "$STATUS_BAND_LAST_RESULT" "$_sb_next"
done
