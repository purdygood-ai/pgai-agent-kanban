#!/usr/bin/env bash
# next-cron-firings.sh
# Emits the 'Next Agent Wake (incl. stagger)' pane content for the pgai kanban
# dashboard.
#
# Reads crontab -l, parses agent-targeted cron lines, computes next work-start
# time per agent (cron fire delta + --sleep=N stagger offset, using
# team/pm-agent/lib/cron_parser.py), and prints the rendered pane content in
# roster order -- each agent's first-seen position in the crontab, pm first.
#
# Designed to run under `watch -n 5 next-cron-firings.sh` in
# the new bottom-row right pane created by dashboard/create.sh.
#
# Output format (default geometry -- the full roster packs into a compact
# columnar band, at most two rows):
#   === Next Agent Wake (incl. stagger) ===
#
#   PM :00 (in 0:40) · CODER :12 (in 0:52) · WRITER :24 (in 1:16) ·
#   TESTER :36 (in 1:28) · CM :48 (in 1:04) · OVERWATCH Thu 3am · CLEANUP Sun 4am
#
#   (HALT: on — firings will skip work)    <- only when HALT marker is set
#
# Each sub-hour entry carries its fire mark (":MM", the minute-of-hour it
# next fires at) and a live M:SS countdown (e.g. "0:42", "4:32"). Entries
# with more than one hour remaining display a day+time hint (e.g. "Sun
# 4am") instead. When the hosting pane is too narrow for the band to fit
# in two rows, output falls back to one entry per line -- see lib/
# roster_band.sh -- with a trailing "+N more" line when even that does not
# fit the pane's queryable height.
#
# Entries render in fixed roster order -- pm first, then every other agent
# in the order it first appears in the crontab -- the same ordering
# mechanism show-header.sh's window-0 tile uses, so both next-fire homes
# agree on entry order and identity between renders.  This is an ordering
# change from time-remaining: an entry's position no longer reflects how
# soon it fires, so watch the countdown value itself for urgency, not
# position.  Each 5-second watch refresh recomputes each entry's own
# remaining time from the system clock; only the values change on refresh,
# never the order.
#
# The roster is every agent entry team/pm-agent/lib/cron_parser.py finds in
# the crontab -- never a fixed list -- so it grows or shrinks with the
# installed schedule.  At default geometry the whole roster packs into a
# compact columnar band across at most two rows (see
# team/scripts/lib/dashboard_constants.sh's PGAI_ROSTER_BAND_MAX_ROWS),
# through the shared renderer at lib/roster_band.sh.  When the hosting
# pane is too narrow for the band to fit in that many rows, the renderer
# falls back to the vertical one-entry-per-line list and fits THAT to the
# pane's queryable height: rows are dropped from the BOTTOM of the
# roster-ordered list -- i.e. the agents nearest the end of the crontab
# (weekly entries such as OVERWATCH and CLEANUP first, then working back
# up toward CM, TESTER, WRITER, CODER) are truncated first, regardless of
# which of them is actually about to fire soonest -- and a trailing
# '+N more' line names exactly how many were cut (never a silent loss).
# Outside tmux, when pane geometry cannot be queried (piped output,
# --no-tmux one-shot mode, a plain terminal), the band is treated as
# unconstrained and every entry renders untruncated.
#
# When no agent cron entries are found:
#   === Next Cron Firings ===
#
#   no cron entries found — manual mode only
#
# Usage:
#   next-cron-firings.sh [--kanban-root <path>] [--no-color]
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
ROSTER_ENTRIES_PY="${TEAM_DIR}/pgai_agent_kanban/dashboard/roster_entries.py"

# Source shared ANSI color primitive (single home for the color-name ->
# escape-code table -- see team/scripts/lib/script_common.sh).
# shellcheck source=lib/script_common.sh
source "${SCRIPT_DIR}/../lib/script_common.sh"
# Source shared pane-geometry query + row-fit-with-marker helpers — the
# same functions show-header.sh's next-fire tile uses, so both next-fire
# homes truncate identically rather than drifting apart.
# shellcheck source=lib/next_fire_viewport.sh
source "${SCRIPT_DIR}/lib/next_fire_viewport.sh"
# Source the shared roster band renderer — the single "one renderer, two
# call sites" home for packing the roster into a columnar band or falling
# back to the vertical list, also used by show-header.sh's window-0 tile.
# shellcheck source=lib/roster_band.sh
source "${SCRIPT_DIR}/lib/roster_band.sh"
# Source shared dashboard layout constants (PGAI_ROSTER_BAND_MAX_ROWS,
# PGAI_ROSTER_BAND_SEPARATOR) — single source of truth, never hardcoded
# independently here.
# shellcheck source=../lib/dashboard_constants.sh
source "${SCRIPT_DIR}/../lib/dashboard_constants.sh"

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
      # Read the whole header via mapfile so grep/sed always finish writing
      # before anything reads their output, then slice to the line limit
      # locally -- head -N as a pipe stage can close its input early and
      # SIGPIPE the writers under pipefail.
      mapfile -t __help_lines < <(grep '^#' "$0" | grep -v '^#!' | sed 's/^# \{0,1\}//')
      printf '%s\n' "${__help_lines[@]:0:74}"
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

RESET="$(ansi_code reset)"
BOLD="$(ansi_code bold)"
DIM="$(ansi_code dim)"
CYAN="$(ansi_code cyan)"
RED="$(ansi_code red)"

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
# Build the roster via the shared renderer's entry fetcher
# (roster_entries.py, invoked by roster_band_fetch_entries in
# lib/roster_band.sh) — the same data path show-header.sh's window-0 tile
# uses, so the two next-fire homes can never disagree about which entries
# exist, their order, or their fire-mark/countdown text. Populates
# ROSTER_BAND_DISPLAY (roster order, pm first) — empty when the crontab
# has no kanban entries.
# ---------------------------------------------------------------------------
roster_band_fetch_entries "$ROSTER_ENTRIES_PY" "$CRON_PARSER_LIB" "$CRONTAB_TEXT" "$NOW_TS"

ROW_LINES=("${ROSTER_BAND_DISPLAY[@]}")
if [[ ${#ROW_LINES[@]} -eq 0 ]]; then
  ROW_LINES+=("$(printf '%sno cron entries found \xe2\x80\x94 manual mode only%s' "${DIM}" "$RESET")")
fi

# ---------------------------------------------------------------------------
# HALT footer — shown at the bottom when the HALT marker file exists.
# Displayed even when agents are listed so operators know firings will skip.
# Reserved out of the fit budget below (2 header lines always, +2 more when
# this footer will print) so the footer itself is never what pushes a
# roster row off the bottom uncounted.
# ---------------------------------------------------------------------------
HALT_FILE="${KANBAN_ROOT}/HALT"
HALT_ACTIVE=false
[[ -f "$HALT_FILE" ]] && HALT_ACTIVE=true

RESERVED_ROWS=2  # "=== ... ===" title + the blank line already printed above
if [[ "$HALT_ACTIVE" == "true" ]]; then
  RESERVED_ROWS=$(( RESERVED_ROWS + 2 ))  # blank + halt line
fi

PANE_HEIGHT="$(next_fire_pane_height)"
if [[ -n "$PANE_HEIGHT" ]]; then
  BUDGET=$(( PANE_HEIGHT - RESERVED_ROWS ))
  (( BUDGET < 0 )) && BUDGET=0
else
  BUDGET=""
fi

# ---------------------------------------------------------------------------
# Render: the shared roster band renderer packs ROW_LINES into a compact
# columnar band (at most PGAI_ROSTER_BAND_MAX_ROWS rows) when the pane's
# queryable width allows it, or falls back to the vertical one-entry-per-
# line list fitted to BUDGET (the pre-existing height-based fit, unchanged)
# when it does not — see lib/roster_band.sh.
# ---------------------------------------------------------------------------
PANE_WIDTH="$(next_fire_pane_width)"
roster_band_render "$PANE_WIDTH" "$PGAI_ROSTER_BAND_MAX_ROWS" \
  "$PGAI_ROSTER_BAND_SEPARATOR" "$BUDGET" "${ROW_LINES[@]}"

if [[ "$HALT_ACTIVE" == "true" ]]; then
  printf '\n'
  printf '%s(HALT: on \xe2\x80\x94 firings will skip work)%s\n' "${RED}" "$RESET"
fi
