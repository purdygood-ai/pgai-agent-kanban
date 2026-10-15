#!/usr/bin/env bash
# dashboard_constants.sh — Shared constants for dashboard layout and verification.
#
# Source this file in any script that needs to know the canonical dashboard
# layout values.  Define each constant ONCE here; consumers must not
# hardcode these values independently (anti-divergence guarantee).
#
# Usage:
#   source "${SCRIPT_DIR}/../lib/dashboard_constants.sh"   # from dashboard/
#   source "${SCRIPT_DIR}/lib/dashboard_constants.sh"      # from scripts/

# ---------------------------------------------------------------------------
# Window-0 layout constants
# ---------------------------------------------------------------------------

# PGAI_WINDOW0_RIGHT_COL_PCT — Width of window-0's right column (QUEUES pane)
# as an integer percentage of the total window width.
#
# This value is consumed by:
#   - team/scripts/dashboard/create.sh       (split-window + resize-pane)
#   - team/scripts/dashboard/verify-window0-geometry.sh (geometry assertion)
#
# Changing this value here automatically propagates to both the layout and
# the verifier, preserving the anti-divergence guarantee.
#
# Value: 25  (confirmed correct in production; hardcoded here as convention over configuration)
PGAI_WINDOW0_RIGHT_COL_PCT=25

# ---------------------------------------------------------------------------
# Operator-window layout constants
# ---------------------------------------------------------------------------

# PGAI_OPERATOR_SHELL_PCT — Height of the operator window's interactive shell
# row (between the board and the legend bar) as an integer percentage of the
# window height, floored.
#
# PGAI_OPERATOR_SHELL_MIN_ROWS — The shell row is never shorter than this
# many rows, whatever the percentage yields on a short terminal.
#
# Consumed by team/scripts/dashboard/create.sh, which passes both to
# dashboard_build_board_window (team/scripts/dashboard/lib/board_window.sh).
PGAI_OPERATOR_SHELL_PCT=15
PGAI_OPERATOR_SHELL_MIN_ROWS=3

# ---------------------------------------------------------------------------
# Visibility-aware refresh cadence constants
# ---------------------------------------------------------------------------

# PGAI_DASHBOARD_HIDDEN_MULTIPLIER — a hidden pane (its window is not the
# active one, or no client is attached) does not render at all; this sets
# how often it re-checks whether it has become visible, as a multiple of
# its visible refresh interval (PGAI_DASHBOARD_REFRESH_SECONDS, or the
# pane's own fixed interval). The re-check is one tmux query, no render --
# a safety net behind the activation hook, which wakes a pane the moment
# its window becomes visible.
#
# Consumed by:
#   - team/scripts/dashboard/lib/cadence.sh (dashboard_cadence_run_loop,
#     dashboard_cadence_run_stream)
#   - team/scripts/dashboard/cadence-loop.sh (the pane-loop entry point every
#     rendering pane runs under)
#
# Value: 10
PGAI_DASHBOARD_HIDDEN_MULTIPLIER=10

# PGAI_DASHBOARD_JITTER_MAX_SECONDS — upper bound (exclusive) in seconds of
# the one-time, per-pane-deterministic stagger jitter cadence-loop.sh
# applies once, before a pane's FIRST render (never re-rolled on later
# ticks). Spreads a dozen-plus panes launched together off the same
# wall-clock beat so the host does not see a synchronized render spike
# every PGAI_DASHBOARD_REFRESH_SECONDS.
#
# Chosen well below the default active interval (5s) so the stagger can
# never meaningfully delay a pane's first paint, while still being large
# enough (a handful of discrete integer offsets) to visibly separate two
# panes launched in the same tmux window split. Deterministic per pane
# (hash of "<window>.<pane-index>" mod this bound — see
# dashboard_cadence_jitter_seconds), so re-running create.sh against the
# same window/pane layout reproduces the same offsets.
#
# Value: 3
PGAI_DASHBOARD_JITTER_MAX_SECONDS=3

# PGAI_DASHBOARD_HIDDEN_CPU_BUDGET_RATIO — the hidden-window CPU-sample
# test's pass/fail threshold, expressed as a RATIO against a same-run,
# same-host baseline rather than an absolute CPU-seconds figure (an
# absolute figure flakes on a loaded CI/dev host; a same-run ratio does
# not, because both arms of the comparison absorb the same host load).
#
# MEASUREMENT METHOD (how this constant was derived, and how the test
# re-derives its inputs on every run):
#   1. "Baseline" arm: run cadence-loop.sh against a real dashboard
#      renderer (legend-render.sh) with PGAI_DASHBOARD_CADENCE_FORCE_STATE
#      pinned to "active" — this reproduces the PRE-CHANGE behavior, where
#      every pane ticked at the flat active rate regardless of window
#      visibility — sampled over a fixed window of the loop's own schedule.
#   2. "Hidden" arm: the same renderer, same sampling window, with
#      PGAI_DASHBOARD_CADENCE_FORCE_STATE pinned to "hidden" — a hidden
#      pane renders once on arrival and then only re-checks visibility,
#      so this arm's cost is one render plus the loop's own start-up.
#   3. The window is counted on a virtual clock, not the wall clock: the
#      test puts a `sleep` test double on the loop's PATH that returns at
#      once and advances the clock by each interval the loop requests, and
#      the sample is taken when the loop first asks to sleep at or past the
#      window. Each arm is therefore charged for exactly the renders its
#      schedule fits into the window (first paint plus one per elapsed
#      interval) however busy the host is; a wall-clock window let a
#      starved baseline render fewer times and pushed the ratio up.
#   4. CPU cost for each arm is read from /proc/<loop_pid>/stat fields 14+15
#      (utime+stime, the loop's own CPU) plus 16+17 (cutime+cstime, CPU of
#      every renderer child process the loop already reaped via its own
#      `wait`/command-substitution), converted to seconds via
#      os.sysconf("SC_CLK_TCK"). This captures the loop AND every renderer
#      invocation it spawned during the sampling window.
#   5. When this budget was set (team/tests/unit/test_dashboard_cadence.py,
#      sampling window 6s, active interval 0.2s, so the baseline arm
#      rendered 31x and a hidden arm that still re-rendered every
#      10 intervals rendered 4x), four repeated measurements gave
#      baseline_cpu ~= 3.17-3.34s, hidden_cpu ~= 0.45-0.50s, hidden/baseline
#      ratio ~= 0.138-0.152 -- a little above the 4/31 ~= 0.129 render-count
#      ratio, because a fixed per-loop start-up cost (sourcing the
#      libraries, resolving pane identity) does not shrink with fewer
#      ticks and so makes up a larger share of the hidden arm's small
#      total. Still a clear, repeatable ~7x reduction. A hidden pane now
#      renders only once (its arrival render), so the measured ratio is
#      lower still.
#   6. This constant sets the asserted budget at 0.5 (50% of baseline) —
#      more than 3x the measured ~0.138-0.152 ratio — so the test
#      tolerates a slower renderer script or a different host without
#      flaking, while still failing loudly if the hidden-window slowdown
#      regresses to anywhere near the flat pre-change rate (a hidden arm
#      on the active interval measures a ratio of ~1).
#
# Value: 0.5
PGAI_DASHBOARD_HIDDEN_CPU_BUDGET_RATIO=0.5

# ---------------------------------------------------------------------------
# Schedule-roster pane sizing
# ---------------------------------------------------------------------------

# PGAI_SCHEDULE_HEADER_RESERVED_ROWS — rows window-0's HEADER tile prints
# unconditionally ABOVE its per-agent roster rows: the project/version line,
# the blank line below it, and the "Next cron firings:" column title. Must
# stay equal to show-header.sh's own RESERVED_ROWS (the two are the same
# accounting, kept as one named constant so a future edit to either cannot
# silently diverge from the other).
#
# Value: 3
PGAI_SCHEDULE_HEADER_RESERVED_ROWS=3

# PGAI_STATUS_BAND_RESERVED_ROWS — rows window-4's list band (top pane,
# status-band.sh) prints unconditionally ABOVE its per-agent rows: the
# "AGENT  LAST FIRED  LAST RESULT  NEXT" column header. Must stay equal to
# status-band.sh's own header-row count (that script prints exactly one,
# unconditionally, before iterating the roster); the list band's height
# (dashboard/lib/schedule_layout.sh) is built from it.
#
# Value: 1
PGAI_STATUS_BAND_RESERVED_ROWS=1

# PGAI_SCHEDULE_EVENT_MIN_ROWS — rows the schedule window is laid out to
# leave the event band (bottom pane, event-band.sh) below the list band. The
# event band is the one that shrinks when the window is shorter than the
# list plus this many rows; the list keeps every roster row.
#
# Value: 5
PGAI_SCHEDULE_EVENT_MIN_ROWS=5

# ---------------------------------------------------------------------------
# Roster band layout
# ---------------------------------------------------------------------------

# PGAI_ROSTER_BAND_MAX_ROWS — the maximum number of rows the columnar
# roster band (team/scripts/dashboard/lib/roster_band.sh) may pack the full
# roster into before it counts as "does not fit" and the renderer falls
# back to the vertical per-entry list. Shared by both next-fire homes
# (next-cron-firings.sh's drill-window CRON pane and show-header.sh's
# window-0 tile) so neither can drift from the other on how many rows the
# band is allowed to use.
#
# Value: 2
PGAI_ROSTER_BAND_MAX_ROWS=2

# PGAI_ROSTER_BAND_SEPARATOR — the string joining adjacent entries within a
# packed band row (e.g. "PM :00 (in 0:37) · CODER :12 (in 0:49)"). U+00B7
# MIDDLE DOT padded with a space on each side.
#
# Value: " · "  (space, U+00B7, space)
PGAI_ROSTER_BAND_SEPARATOR=" $(printf '\xc2\xb7') "

# ---------------------------------------------------------------------------
# dashboard_schedule_roster_count <cron_parser_lib>
#
# Echoes the number of agent entries the installed crontab resolves to via
# team/pm-agent/lib/cron_parser.py's next_firings() — the SAME roster
# next-cron-firings.sh (window-4 top pane) and show-header.sh (window-0
# HEADER tile, reached through show-multi.sh's header-mode delegation)
# render at watch-refresh time. create.sh calls this ONCE, at session-build
# time, to size each pane's minimum height so the full roster fits without
# the renderer's own "+N more" truncation marker ever needing to fire on a
# freshly-created pane.
#
# <cron_parser_lib>  path to team/pm-agent/lib/cron_parser.py.
#
# Echoes 0 — never a guess, never the pre-OVERWATCH literal five — when the
# crontab is empty/unreadable, the parser file is missing, or python3 is
# unavailable: callers combine this count with their own reserved-row
# constant above, so a rosterless host still gets a small, usable pane
# rather than an oversized guess.
# ---------------------------------------------------------------------------
if ! declare -F pp_run_ops >/dev/null 2>&1; then
  # shellcheck source=pp_run_ops.sh
  source "$(dirname "${BASH_SOURCE[0]}")/pp_run_ops.sh"
fi
dashboard_schedule_roster_count() {
  local cron_parser_lib="$1"
  local crontab_text=""
  crontab_text="$(crontab -l 2>/dev/null)" || crontab_text=""

  if [[ -z "$crontab_text" ]] || [[ ! -f "$cron_parser_lib" ]] || ! command -v python3 >/dev/null 2>&1; then
    printf '0\n'
    return 0
  fi

  local count=""
  count="$(pp_run_ops pgai_agent_kanban.dashboard.cron_firings --count "$crontab_text" "$cron_parser_lib" 2>/dev/null)" \
    || count=""

  if [[ "$count" =~ ^[0-9]+$ ]]; then
    printf '%s\n' "$count"
  else
    printf '0\n'
  fi
}
