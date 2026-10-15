#!/usr/bin/env bash
# create.sh
# Creates and attaches to the pgai-kanban tmux dashboard session.
#
# Layout (Window "main" — dashboard overview):
#   ┌─────────────────────────────────────────┬──────────────────────────────┐
#   │ LEFT column (~75% width)                │ RIGHT column (~25% width)    │
#   │ HEADER (top ~11% height)                │                              │
#   ├─────────────────────────────────────────┤ QUEUES / per-project status  │
#   │ LIVE LOGS (middle ~40% — logs.sh)       │ (full height)                │
#   ├─────────────────────────────────────────┤ watch show-multi queues      │
#   │ PROGRESS (full width of left column)    │ all projects' 5-agent        │
#   │ watch show-multi --mode progress        │ breakdown + working task     │
#   └─────────────────┴───────────────────────┴──────────────────────────────┘
#
# QUEUES right pane is full-height so all registered projects fit without
# clipping. The left column is subdivided into HEADER (top), LOGS (middle
# ~40%), and PROGRESS (bottom, full column width — the CRON pane that used to
# share this row now lives in its own "schedule" window, see below).
#
# Auxiliary windows (in creation order — see team/config/dashboard_windows.census
# for the authoritative, machine-readable window/pane list this comment
# summarizes in prose; the census is what create.sh is checked against):
#   operator       — the visibility window plus a command line, three stacked rows:
#                           Rationale: inside a container attach there is no "just open another
#                           terminal" — this window is the container operator's whole workspace:
#                           watch the board and run commands, one screen.
#                           Top row (board): the visibility window's eight columns at the
#                             same widths, running the same column-render.sh commands.
#                           Middle row (cmdline, 15% height, min 3 rows, full width):
#                             interactive bash login shell with shell-env sourced,
#                             cwd = $KANBAN_ROOT; pane title "operator".
#                           Bottom row: the visibility window's 1-row legend bar.
#                           Both windows are built by lib/board_window.sh's
#                           dashboard_build_board_window, so their columns cannot drift.
#   visibility     — unified 3+5 visibility window (Dashboard 2.1)
#                           LEFT region (~40%): bugs / priority / requirements
#                           RIGHT region (~60%): pm / coder / writer / tester / cm
#                           Side-by-side horizontal split
#                           Each pane runs: watch -c -n <interval> dashboard/column-render.sh <args>
#                           Designed for 120-char-wide terminals
#   attention      — watch dashboard/attention.sh (BLOCKED tasks)
#   schedule       — cron/schedule content, split out of window "main"'s former
#                           CRON FIRINGS pane into its own window: two
#                           full-width bands stacked top to bottom.
#                           Top band (list, one row per roster agent plus
#                             its header, re-applied on resize and attach):
#                             status-band.sh — per-agent LAST FIRED /
#                             LAST RESULT / NEXT.
#                           Bottom band (event, remaining height):
#                             event-band.sh — merged, signal-only,
#                             chronologic wake-log stream across every agent.
#   git            — two vertical panes, each under cadence-loop.sh at a 10s
#                           active-window interval (visibility-aware: 10x slower
#                           while hidden, immediate refresh on activation — see
#                           lib/cadence.sh):
#                           Left:  dashboard/git-status.sh
#                                  (branch, sync status, uncommitted changes, rc/* branches,
#                                   recent commits on main)
#                           Right: dashboard/git-recent-tags.sh
#                                  (Recent Tags listing, >=10 newest tags)
#   metadata       — single pane running dashboard/metadata.sh under
#                           cadence-loop.sh at a 10s active-window interval
#                           (visibility-aware — see lib/cadence.sh); shows
#                           kanban version, PM mode, HALT, and per-project rows
#                           (workflow type, active RC, last released, max_minor, max_major)
#   rc-metrics     — two vertical panes (history and current-RC economics),
#                           each under cadence-loop.sh at the standard
#                           $REFRESH_INTERVAL active-window interval
#                           (visibility-aware — see lib/cadence.sh):
#                           Left:  dashboard/metrics.sh
#                                  shows today's per-project metrics (RCs shipped, wall time, tokens, tasks)
#                                  and current-RC progress (tasks done, elapsed, tokens so far)
#                           Right: show-metrics.sh
#                                  shows historical RC metrics from history.csv (last 10 RCs)
#   agent-metrics  — two vertical panes running agent-metrics.sh (average wall
#                           time, tokens, task count and totals per agent, per project;
#                           thousands-separated; CLI twin of the REST endpoint), each under
#                           cadence-loop.sh at the standard $REFRESH_INTERVAL active-window
#                           interval (visibility-aware — see lib/cadence.sh):
#                           Left:  live table over the newest 10 shipped RCs (the default span)
#                           Right: full-history report over every shipped RC (--all)
#   logs           — Logs full-screen — dashboard/logs.sh
#   debug-logs     — single-pane unified merged stream of all agents' debug logs
#                           ($KANBAN_ROOT/logs/debug/<agent>.log)
#                           color-coded per agent; gated by PGAI_VERBOSE_MODE=1
#                           missing log files tolerated (tail -F waits for them)
#   training-logs  — single-pane unified view of all agents' newest training traces
#                           ($KANBAN_ROOT/projects/<project>/logs/training/<agent>/<ts>-<task-id>.md)
#                           color-coded per agent, sorted by mtime (newest at top)
#                           gated by PGAI_REASONING_TRACE=1; runs under cadence-loop.sh
#                           at a 30s active-window interval (visibility-aware — see lib/cadence.sh)
#   terminal       — three interactive shell panes
#   human-review   — two vertical panes:
#                           Left (Approvals, ~65%): listing of all pending HUMAN-APPROVE
#                             gate tasks across ALL projects; one row per pending gate
#                             (project, RC, age, show content, approve cmd, reject cmd).
#                             Empty state: "no approvals pending." Unchanged from before
#                             (human-review.sh itself is untouched — byte-identical behavior).
#                           Right (Auto-Resolutions, ~35%): read-only listing of OVERWATCH's
#                             autonomous resolution ledger entries across ALL projects
#                             (RC, attempt N/M, decision, outcome, evidence links).
#                             Empty state: "no auto-resolutions." No action affordances —
#                             informs only, never approves or mutates state.
#                           Both panes run under cadence-loop.sh at the standard
#                           $REFRESH_INTERVAL active-window interval (visibility-aware
#                           — see lib/cadence.sh).
#   drill-N (N=1,2,...) — one window per registered project (all workflow types),
#                           numbered in projects.cfg registration order (drill-1, drill-2, ...).
#                           Release/feature projects: 5-pane release layout scoped to
#                           project N (header / queues / progress / cron / logs).
#                           Document-workflow projects: 2-pane document layout —
#                           top pane: show-document-drill.sh (artifact version library +
#                           document-pipeline progress); bottom pane: shared logs.
#                           Each drill-N window tab is colored with its project's
#                           dashboard_color.
#
# CENSUS-GENERATED:BEGIN window-order
# generated by team/scripts/dashboard/generate_window_docs.py from
# team/config/dashboard_windows.census. Do not hand-edit between
# these markers; run 'python3 team/scripts/dashboard/generate_window_docs.py'
# (from the repo root) and commit the result instead.
# Window order: main, operator, visibility, attention, schedule, git, metadata,
#               rc-metrics, agent-metrics, logs, debug-logs, training-logs,
#               terminal, human-review, drill-1, drill-2, ...
# CENSUS-GENERATED:END window-order
#
# Per-project drill/overview toggle (Feature 4):
#   Key binding: prefix + p
#   Cycles through: dashboard (overview)  →  drill-1  →  drill-2  →  ...  →  dashboard
#   Implemented by dashboard/project-toggle.sh.
#   With 1 project: cycles dashboard → drill-1 → dashboard (drill == overview).
#   With 2+ projects: each drill-N shows only project N's panes.
#
# Usage:
#   create.sh [--no-tmux] [--print-schedule-floors] [--kanban-root <path>] [--session <name>]
#
# Flags:
#   --no-tmux               One-shot non-interactive output (print all panes to stdout)
#   --print-schedule-floors Diagnostic dry-run: print the schedule window's
#                           roster-derived heights (SCHEDULE_ROSTER_COUNT,
#                           HEADER_MIN_H, STATUS_BAND_MIN_H, SCHED_LIST_H,
#                           SCHED_EVENT_MIN_H) and exit before any tmux
#                           session is touched
#   --kanban-root           Override the kanban root path
#   --session               Override the tmux session name (default: pgai-kanban-dashboard)
#   -h, --help              Show this help and exit
#
# Environment variables:
#   PGAI_DASHBOARD_REFRESH_SECONDS  Refresh interval in seconds (default: 5, range: 1-3600)
#
# Requirements:
#   tmux >= 3.1

set -euo pipefail
# shellcheck source=../lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/env_bootstrap.sh"

# ---------------------------------------------------------------------------
# Resolve script directory
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source project_paths lib for pp_* helpers
# shellcheck source=lib/project_paths.sh
source "${SCRIPT_DIR}/../lib/project_paths.sh"

# Source shared dashboard palette lib for DASHBOARD_TMUX_ACTIVE_WINDOW_STYLE
# (single home for the active-window tmux markup used in both
# status-format templates below).
# shellcheck source=../lib/dashboard_palette.sh
source "${SCRIPT_DIR}/../lib/dashboard_palette.sh"

# Source projects lib for projects_cfg_* helpers (multi-project registry)
# shellcheck source=lib/projects.sh
source "${SCRIPT_DIR}/../lib/projects.sh"

# Source dev_tree helper (resolve_global_dev_tree / require_dev_tree)
# shellcheck source=lib/dev_tree.sh
source "${SCRIPT_DIR}/../lib/dev_tree.sh"

# Source shared dashboard constants (PGAI_WINDOW0_RIGHT_COL_PCT, etc.)
# shellcheck source=lib/dashboard_constants.sh
source "${SCRIPT_DIR}/../lib/dashboard_constants.sh"

# Source pane signal-safety + all-armed readiness handshake (trap-first-line
# preamble every pane command goes through, and the blocking wait create.sh's
# return is gated on below). See lib/pane_readiness.sh for the full design.
# shellcheck source=lib/pane_readiness.sh
source "${SCRIPT_DIR}/lib/pane_readiness.sh"

# Source the session-scoped activation-hook lifecycle (install/remove).
# Installed only after the all-armed handshake below succeeds — see
# lib/activation_hook.sh for the full design and why installing any
# earlier would reopen the race the handshake exists to close.
# shellcheck source=lib/activation_hook.sh
source "${SCRIPT_DIR}/lib/activation_hook.sh"
# shellcheck source=lib/schedule_layout.sh
source "${SCRIPT_DIR}/lib/schedule_layout.sh"

# Source the shared board-window builder: the one split ladder and
# absolute-width resize pass both the operator and visibility windows are
# built from (see lib/board_window.sh's header for the sizing rules).
# shellcheck source=lib/board_window.sh
source "${SCRIPT_DIR}/lib/board_window.sh"

# Source the dual-channel error-contract emitter for the all-armed timeout
# refusal below (BLOCK-WORTHY; see that refusal site for the classification
# argument).
# shellcheck source=../lib/error_contract.sh
source "${SCRIPT_DIR}/../lib/error_contract.sh"

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
SESSION_NAME="pgai-kanban-dashboard"
NO_TMUX=false
PRINT_SCHEDULE_FLOORS=false

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-tmux)
      NO_TMUX=true
      shift
      ;;
    --print-schedule-floors)
      # Diagnostic dry-run: print SCHEDULE_ROSTER_COUNT, HEADER_MIN_H,
      # STATUS_BAND_MIN_H, SCHED_LIST_H and SCHED_EVENT_MIN_H and exit before
      # any tmux session is touched. Exists so
      # team/tests/unit/test_schedule_roster_completeness.py can pin the
      # creation-time roster floors against a fixture crontab without
      # spinning up a real tmux session; no production call site uses this
      # flag.
      PRINT_SCHEDULE_FLOORS=true
      shift
      ;;
    --kanban-root)
      KANBAN_ROOT="${2:-$KANBAN_ROOT}"
      shift 2
      ;;
    --session)
      SESSION_NAME="${2:-$SESSION_NAME}"
      shift 2
      ;;
    -h|--help)
      # Read only the file's LEADING comment block (the header, terminated
      # by the first non-'#' line — "set -euo pipefail" below) via awk,
      # skipping the '#!' shebang line; awk reads to EOF of its own
      # accord rather than being closed early by a downstream `head`.
      # The header comment block runs well past 30 lines (window-layout diagrams and per-window design
      # notes) with the Usage:/Flags:/Environment/Requirements block near
      # its end, so a fixed line-count slice either misses that block
      # entirely or, printed unbounded, would spill into every OTHER
      # inline '#' comment in the rest of the file (grep '^#' "$0" alone
      # matches those too) — awk's own `next`/`exit` pair stops reading
      # at the header's last line, so neither failure mode is possible.
      # Ignoring SIGPIPE for the final print (and swallowing its exit
      # status) covers a caller piping OUR stdout into a reader that
      # itself closes early (e.g. `--help | head -1`) — that must not
      # turn into a fatal signal for us either.
      #
      # --help shows the two-line script description plus everything
      # from the "Usage:" line onward (Flags, Environment variables,
      # Requirements), found by that literal marker rather than a
      # hardcoded line count so a future header edit that adds or
      # removes layout prose ahead of it can never push the block back
      # out of --help's reach.
      trap '' PIPE
      mapfile -t __help_lines < <(awk '
        /^#!/ { next }
        /^#/ { line = $0; sub(/^# ?/, "", line); print line; next }
        { exit }
      ' "$0")
      __help_usage_start=-1
      for __help_i in "${!__help_lines[@]}"; do
        if [[ "${__help_lines[$__help_i]}" == "Usage:" ]]; then
          __help_usage_start=$__help_i
          break
        fi
      done
      if [[ $__help_usage_start -ge 0 ]]; then
        printf '%s\n' "${__help_lines[@]:0:2}"
        printf '%s\n' "${__help_lines[@]:$__help_usage_start}"
      else
        # No "Usage:" marker found at all (a malformed header) — fall
        # back to the full header block rather than printing nothing.
        printf '%s\n' "${__help_lines[@]}"
      fi || true
      exit 0
      ;;
    *)
      echo "ERROR: Unknown argument: $1" >&2
      echo "Usage: $0 [--no-tmux] [--print-schedule-floors] [--kanban-root <path>] [--session <name>]" >&2
      exit 1
      ;;
  esac
done

# Pass kanban-root through to renderer scripts
RENDERER_ARGS=(--kanban-root "$KANBAN_ROOT")

# ---------------------------------------------------------------------------
# Establish framework environment — source shell-env (mirrors wake_common.sh)
#
# PRINCIPLE: every kanban entry point must establish its own environment by
# sourcing shell-env before spawning child processes.  Wake scripts do this via
# wake_common.sh; the dashboard must do the same so pane scripts (logs.sh,
# debug-logs.sh, next-cron-firings.sh, etc.) inherit PGAI_AGENT_KANBAN_ROOT_PATH,
# PATH, the Python venv activation, and model vars — without relying on the
# operator's interactive shell having pre-loaded any of these values.
#
# shell-env is optional — the dashboard works without it if the operator's shell
# already carries the required vars, or if the operator prefers not to use it.
# ---------------------------------------------------------------------------
if [[ -f "${KANBAN_ROOT}/shell-env" ]]; then
    # shellcheck source=../../shell-env
    source "${KANBAN_ROOT}/shell-env"
fi
# Export PGAI_AGENT_KANBAN_ROOT_PATH so spawned pane child processes inherit it
# even when shell-env does not re-export it (belt-and-suspenders).
export PGAI_AGENT_KANBAN_ROOT_PATH

# ---------------------------------------------------------------------------
# Source config — INI format (kanban.cfg) replaces legacy config.cfg
# read_ini available via project_paths.sh (sourced above).
# ---------------------------------------------------------------------------
if [[ -f "${KANBAN_ROOT}/kanban.cfg" ]]; then
    export PGAI_KANBAN_PM_MODE="${PGAI_KANBAN_PM_MODE:-$(read_ini "${KANBAN_ROOT}/kanban.cfg" chain pm_mode automatic)}"
    export PGAI_DASHBOARD_SESSION_NAME="${PGAI_DASHBOARD_SESSION_NAME:-$(read_ini "${KANBAN_ROOT}/kanban.cfg" dashboard session_name pgai-kanban-dashboard)}"
    export PGAI_DASHBOARD_REFRESH_SECONDS="${PGAI_DASHBOARD_REFRESH_SECONDS:-$(read_ini "${KANBAN_ROOT}/kanban.cfg" dashboard refresh_seconds 5)}"
fi
export PGAI_DEV_TREE_PATH="${PGAI_DEV_TREE_PATH:-$(resolve_global_dev_tree)}"

# Allow session name override from kanban.cfg
SESSION_NAME="${PGAI_DASHBOARD_SESSION_NAME:-$SESSION_NAME}"

# ---------------------------------------------------------------------------
# Refresh interval — PGAI_DASHBOARD_REFRESH_SECONDS (default: 5)
# Must be a positive integer in [1, 3600]. Invalid values fall back to 5.
# ---------------------------------------------------------------------------
_DEFAULT_REFRESH=5
_raw_refresh="${PGAI_DASHBOARD_REFRESH_SECONDS:-}"
if [[ -z "$_raw_refresh" ]]; then
  REFRESH_INTERVAL=$_DEFAULT_REFRESH
elif [[ "$_raw_refresh" =~ ^[0-9]+$ ]] && \
     [[ "$_raw_refresh" -ge 1 ]] && \
     [[ "$_raw_refresh" -le 3600 ]]; then
  REFRESH_INTERVAL=$_raw_refresh
else
  echo "WARNING: PGAI_DASHBOARD_REFRESH_SECONDS='${_raw_refresh}' is invalid (must be integer 1-3600); defaulting to ${_DEFAULT_REFRESH}s." >&2
  REFRESH_INTERVAL=$_DEFAULT_REFRESH
fi
unset _raw_refresh _DEFAULT_REFRESH

# ---------------------------------------------------------------------------
# Rows per column — DASHBOARD_ROWS_PER_COLUMN (default: 13)
# Kanban-level (global) setting: controls how many items each column in the
# unified visibility window renders.  Not a per-project setting.
# Must be a positive integer.  Invalid values (non-integer, <=0) log a
# warning and fall back to 13.
# ---------------------------------------------------------------------------
_DEFAULT_ROWS=13
_raw_rows="${DASHBOARD_ROWS_PER_COLUMN:-}"
if [[ -z "$_raw_rows" ]]; then
  DASHBOARD_ROWS_PER_COLUMN=$_DEFAULT_ROWS
elif [[ "$_raw_rows" =~ ^[0-9]+$ ]] && [[ "$_raw_rows" -gt 0 ]]; then
  DASHBOARD_ROWS_PER_COLUMN=$_raw_rows
else
  echo "WARNING: DASHBOARD_ROWS_PER_COLUMN='${_raw_rows}' is invalid (must be a positive integer); defaulting to ${_DEFAULT_ROWS}." >&2
  DASHBOARD_ROWS_PER_COLUMN=$_DEFAULT_ROWS
fi
export DASHBOARD_ROWS_PER_COLUMN
unset _raw_rows _DEFAULT_ROWS

# ---------------------------------------------------------------------------
# Right-column width percentage — sourced from the shared dashboard constant.
# The value (25%) is defined once in lib/dashboard_constants.sh and shared
# by both the layout (here) and verify-window0-geometry.sh so they cannot
# diverge.
# ---------------------------------------------------------------------------
QUEUES_PCT="${PGAI_WINDOW0_RIGHT_COL_PCT}"
export QUEUES_PCT

# ---------------------------------------------------------------------------
# --no-tmux mode: one-shot output to stdout
# ---------------------------------------------------------------------------
if [[ "$NO_TMUX" == "true" ]]; then
  SEPARATOR="════════════════════════════════════════════════════════════════════════"

  echo "$SEPARATOR"
  echo "  HEADER"
  echo "$SEPARATOR"
  "${SCRIPT_DIR}/show-multi.sh" --mode header "${RENDERER_ARGS[@]}" || true

  echo ""
  echo "$SEPARATOR"
  echo "  QUEUES"
  echo "$SEPARATOR"
  "${SCRIPT_DIR}/show-multi.sh" --mode queues "${RENDERER_ARGS[@]}" || true

  echo ""
  echo "$SEPARATOR"
  echo "  PROGRESS"
  echo "$SEPARATOR"
  "${SCRIPT_DIR}/show-multi.sh" --mode progress "${RENDERER_ARGS[@]}" || true

  echo ""
  echo "$SEPARATOR"
  echo "  NEXT CRON FIRINGS"
  echo "$SEPARATOR"
  "${SCRIPT_DIR}/next-cron-firings.sh" --kanban-root "${KANBAN_ROOT}" || true

  echo ""
  echo "$SEPARATOR"
  echo "  ATTENTION (BLOCKED TASKS)"
  echo "$SEPARATOR"
  "${SCRIPT_DIR}/attention.sh" "${RENDERER_ARGS[@]}" || true

  echo ""
  echo "$SEPARATOR"
  echo "  VISIBILITY — DISCOVERY PIPELINE (all projects, mixed)"
  echo "$SEPARATOR"
  # --all-projects mode: render each column from all registered projects.
  # Placeholder positional args ("none") satisfy arg-parsing requirements
  # but are ignored when --all-projects is set.
  for _col_label in BUGS PRIORITIES REQUIREMENTS; do
    "${SCRIPT_DIR}/column-render.sh" input none none \
      --label "${_col_label}" --kanban-root "${KANBAN_ROOT}" --all-projects || true
    echo ""
  done

  echo ""
  echo "$SEPARATOR"
  echo "  VISIBILITY — AGENT QUEUES (all projects, mixed)"
  echo "$SEPARATOR"
  for _q in PM CODER WRITER TESTER CM; do
    "${SCRIPT_DIR}/column-render.sh" queue none \
      --label "${_q}" --kanban-root "${KANBAN_ROOT}" --all-projects || true
    echo ""
  done

  echo ""
  echo "$SEPARATOR"
  echo "  VISIBILITY LEGEND"
  echo "$SEPARATOR"
  "${SCRIPT_DIR}/legend-render.sh" --kanban-root "${KANBAN_ROOT}" || true

  echo ""
  echo "$SEPARATOR"
  echo "  CRON LOGS (last 10 lines each)"
  echo "$SEPARATOR"
  # --no-tmux mode: cron-tail.sh is designed for persistent tmux panes
  # (it blocks indefinitely).  Use direct one-shot tail here instead.
  # cron-<agent>.log files are at $KANBAN_ROOT/logs/ — the cron
  # template redirects there directly, not into any project subdir.
  _CRON_LOG_DIR="${KANBAN_ROOT}/logs"
  for _a in pm cm coder writer tester; do
    _CRON_LOG="${_CRON_LOG_DIR}/cron-${_a}.log"
    echo "--- cron-${_a}.log ---"
    if [[ -f "$_CRON_LOG" ]]; then
      tail -n 10 "$_CRON_LOG" 2>/dev/null || true
    else
      echo "(no log yet)"
    fi
    echo ""
  done

  echo ""
  echo "$SEPARATOR"
  echo "  GIT STATUS"
  echo "$SEPARATOR"
  PGAI_DEV_TREE_PATH="${PGAI_DEV_TREE_PATH:-}" "${SCRIPT_DIR}/git-status.sh" || true

  echo ""
  echo "$SEPARATOR"
  echo "  METRICS HISTORY (show-metrics.sh)"
  echo "$SEPARATOR"
  "${SCRIPT_DIR}/show-metrics.sh" --kanban-root "${KANBAN_ROOT}" || true

  echo ""
  echo "$SEPARATOR"
  echo "  AGENT METRICS (live table, agent-metrics.sh)"
  echo "$SEPARATOR"
  "${SCRIPT_DIR}/agent-metrics.sh" --kanban-root "${KANBAN_ROOT}" || true

  echo ""
  echo "$SEPARATOR"
  echo "  AGENT METRICS (full-history report, agent-metrics.sh --all)"
  echo "$SEPARATOR"
  "${SCRIPT_DIR}/agent-metrics.sh" --all --kanban-root "${KANBAN_ROOT}" || true

  echo ""
  echo "$SEPARATOR"
  echo "  RECENT LOGS"
  echo "$SEPARATOR"
  # Tail recent activity from both cron logs ($KANBAN_ROOT/logs/cron-*.log)
  # and per-firing wake batch logs ($KANBAN_ROOT/logs/agents/*-batch-*.log).
  LOG_FILES=()
  while IFS= read -r f; do
    LOG_FILES+=("$f")
  done < <(ls -t "${KANBAN_ROOT}/logs"/cron-*.log "${KANBAN_ROOT}/logs/agents"/*-batch-*.log 2>/dev/null | head -5 || true)

  if [[ ${#LOG_FILES[@]} -gt 0 ]]; then
    tail -n 30 "${LOG_FILES[@]}" 2>/dev/null | "${SCRIPT_DIR}/../log-filter.sh" || true
  else
    echo "(no log files found)"
  fi

  echo ""
  echo "$SEPARATOR"
  echo "  HUMAN-REVIEW (PENDING APPROVALS)"
  echo "$SEPARATOR"
  "${SCRIPT_DIR}/human-review.sh" --kanban-root "${KANBAN_ROOT}" || true

  echo ""
  echo "$SEPARATOR"
  echo "  HUMAN-REVIEW (AUTO-RESOLUTIONS)"
  echo "$SEPARATOR"
  "${SCRIPT_DIR}/auto-resolutions.sh" --kanban-root "${KANBAN_ROOT}" || true

  exit 0
fi

# ---------------------------------------------------------------------------
# tmux mode: check requirements
# ---------------------------------------------------------------------------
if ! command -v tmux &>/dev/null; then
  echo "ERROR: tmux is not installed or not in PATH." >&2
  echo "  Please install tmux 2.0+ and re-run this script." >&2
  echo "  On RHEL/Rocky Linux: sudo dnf install -y tmux" >&2
  exit 1
fi

# Check tmux version >= 3.1 (split-window -l N% requires 3.1+)
TMUX_VERSION="$(tmux -V 2>/dev/null | awk '{print $2}' | sed 's/[^0-9.]//g')"
TMUX_MAJOR="$(echo "$TMUX_VERSION" | cut -d. -f1)"
TMUX_MINOR="$(echo "$TMUX_VERSION" | cut -d. -f2 | sed 's/[^0-9]//g')"
TMUX_MINOR="${TMUX_MINOR:-0}"
if [[ -z "$TMUX_MAJOR" ]] || \
   [[ "$TMUX_MAJOR" -lt 3 ]] || \
   { [[ "$TMUX_MAJOR" -eq 3 ]] && [[ "$TMUX_MINOR" -lt 1 ]]; }; then
  echo "tmux >= 3.1 required (found ${TMUX_VERSION:-unknown}): the dashboard uses split-window -l N% sizing" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Schedule roster count — resolved ONCE, here, for every home that needs a
# roster-derived pane height: window-0's HEADER tile and window-4's list
# band (status-band.sh) — see further below for both. Both are sized so the
# FULL roster fits without a runtime "+N more" truncation marker (HEADER
# tile) or a dropped agent row (list band) ever firing. Uses the same team/pm-agent/lib/cron_parser.py
# next_firings() the renderers themselves call at watch-refresh time — never
# a hardcoded count, and never the pre-OVERWATCH literal five.
# ---------------------------------------------------------------------------
SCHEDULE_CRON_PARSER_LIB="${SCRIPT_DIR}/../../pm-agent/lib/cron_parser.py"
SCHEDULE_ROSTER_COUNT="$(dashboard_schedule_roster_count "$SCHEDULE_CRON_PARSER_LIB")"
[[ "$SCHEDULE_ROSTER_COUNT" =~ ^[0-9]+$ ]] || SCHEDULE_ROSTER_COUNT=0

# HEADER_MIN_H, STATUS_BAND_MIN_H and SCHED_LIST_H — the roster-derived
# pane heights that make window-0's HEADER tile and window-4's list band
# tall enough for the FULL roster (see the resize blocks further below,
# which use these values and never recompute them — single source of
# truth, matching every other constant in lib/dashboard_constants.sh).
# Pure arithmetic on SCHEDULE_ROSTER_COUNT plus each renderer's own
# reserved-row constant; none depend on a live tmux window's size, so all
# are resolved here, before any tmux session query. SCHED_LIST_H is the
# list band's exact height (lib/schedule_layout.sh), the value the
# window's resize/attach hooks re-apply; SCHED_EVENT_MIN_H is the event
# band height the window is laid out to leave below it, the band that
# shrinks first when the window is shorter.
HEADER_MIN_H=$(( SCHEDULE_ROSTER_COUNT + PGAI_SCHEDULE_HEADER_RESERVED_ROWS ))
STATUS_BAND_MIN_H=$(( SCHEDULE_ROSTER_COUNT + PGAI_STATUS_BAND_RESERVED_ROWS ))
SCHED_LIST_H="$(dashboard_schedule_list_rows "$SCHEDULE_ROSTER_COUNT")"
SCHED_EVENT_MIN_H="$PGAI_SCHEDULE_EVENT_MIN_ROWS"

if [[ "$PRINT_SCHEDULE_FLOORS" == "true" ]]; then
  printf 'SCHEDULE_ROSTER_COUNT=%s\n' "$SCHEDULE_ROSTER_COUNT"
  printf 'HEADER_MIN_H=%s\n' "$HEADER_MIN_H"
  printf 'STATUS_BAND_MIN_H=%s\n' "$STATUS_BAND_MIN_H"
  printf 'SCHED_LIST_H=%s\n' "$SCHED_LIST_H"
  printf 'SCHED_EVENT_MIN_H=%s\n' "$SCHED_EVENT_MIN_H"
  exit 0
fi

# ---------------------------------------------------------------------------
# If session already exists, attach and exit
# ---------------------------------------------------------------------------
if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
  echo "Session '$SESSION_NAME' already exists — attaching."
  exec tmux attach-session -t "$SESSION_NAME"
fi

# ---------------------------------------------------------------------------
# Build per-pane shell commands
# ---------------------------------------------------------------------------
HEADER_CMD="${SCRIPT_DIR}/cadence-loop.sh --interval ${REFRESH_INTERVAL} -- ${SCRIPT_DIR}/show-multi.sh --mode header ${RENDERER_ARGS[*]}"
QUEUES_CMD="${SCRIPT_DIR}/cadence-loop.sh --interval ${REFRESH_INTERVAL} -- ${SCRIPT_DIR}/show-multi.sh --mode queues ${RENDERER_ARGS[*]}"
PROGRESS_CMD="${SCRIPT_DIR}/cadence-loop.sh --interval ${REFRESH_INTERVAL} -- ${SCRIPT_DIR}/show-multi.sh --mode progress ${RENDERER_ARGS[*]}"
ATTENTION_CMD="${SCRIPT_DIR}/cadence-loop.sh --interval ${REFRESH_INTERVAL} -- ${SCRIPT_DIR}/attention.sh --kanban-root ${KANBAN_ROOT}"
# The log panes stream forever (logs.sh follows its logs with tail -F), so
# they run under cadence-loop.sh --stream: the stream is stopped while its
# pane is hidden and continued when it is visible again (lib/cadence.sh).
LOGS_PANE_CMD="${SCRIPT_DIR}/cadence-loop.sh --stream --interval ${REFRESH_INTERVAL} -- ${SCRIPT_DIR}/logs.sh --kanban-root ${KANBAN_ROOT}"
LOGS_WIN_CMD="${SCRIPT_DIR}/cadence-loop.sh --stream --interval ${REFRESH_INTERVAL} -- ${SCRIPT_DIR}/logs.sh --kanban-root ${KANBAN_ROOT}"

# Visibility window — unified 8-column mixed-project layout (Dashboard 2.1)
#
# All 8 columns render data from EVERY registered project simultaneously.
# The --all-projects flag is passed to column-render.sh; the
# per-project directory/file placeholders ("none") are positional
# requirements only — the renderer ignores them and reads projects.cfg instead.
#
# Width guidance (40/60 split at ~220-char terminal, leaving room for borders):
#   Input cols (3, left 40%):  floor(0.4 * 220 / 3) ≈ 29 chars each
#   Queue cols (5, right 60%): floor(0.6 * 220 / 5) ≈ 26 chars each
# Exact widths are computed post-creation by resize-pane (see below).
# The placeholder widths here are for while-loop pane commands; they control
# Python's truncation budget.  We use conservative estimates so truncation
# does not clip during the resize window.
#
# Dashboard re-reads projects.cfg on each iteration: column-render.sh
# reads projects.cfg on every refresh cycle.  No cached project list is embedded
# in the command.
#
# IMPORTANT — why cadence-loop.sh, not watch:
#   procps-ng watch(1) with -c / --color only preserves basic 16-color ANSI
#   sequences (\033[3Xm, \033[9Xm).  It silently drops 24-bit truecolor sequences
#   (\033[38;2;R;G;Bm) that project_tag_color() emits for per-project ■ glyphs.
#   cadence-loop.sh's render step (lib/cadence.sh dashboard_cadence_render_once)
#   is watch-equivalent but passes all ANSI sequences through to tmux unmodified,
#   so per-project display_color values render correctly — and additionally
#   makes every one of these panes visibility-aware (hidden windows tick
#   PGAI_DASHBOARD_HIDDEN_MULTIPLIER-times slower, refresh-on-activate wakes
#   them immediately) and staggers their first render with a bounded per-pane
#   jitter. Cleanup: tmux pane exit kills the shell running the loop; no
#   orphaned processes.
_vis_render_script="${SCRIPT_DIR}/column-render.sh"
# Render widths of the board's input and queue columns, and the same widths
# declared to every board column with --board-widths: whichever column builds
# a tick pre-renders all eight at these widths, so the other seven print
# their pre-rendered slice without starting Python (column-render.sh header;
# lib/dashboard/column_render_board_serve.sh). Change a width here and both
# follow.
_vis_input_width=29
_vis_queue_width=26
_all_proj_flags="--all-projects --kanban-root ${KANBAN_ROOT} --board-widths input=${_vis_input_width},queue=${_vis_queue_width}"

# Top row: discovery pipeline columns — input subcommand with placeholder paths.
# "none" placeholders satisfy positional arg requirements; --all-projects overrides.
#
# Cadence: each of these now runs under cadence-loop.sh (visibility-aware
# interval, refresh-on-activate, per-pane stagger jitter — see
# lib/cadence.sh) instead of a hand-rolled "clear; while true; do ...;
# sleep; done" loop with a flat interval. The renderer invocation after
# `--` is unchanged; cadence-loop.sh's render step reproduces the exact
# same flicker-free tput redraw the old inline loop body used.
VIS_BUGS_CMD="${SCRIPT_DIR}/cadence-loop.sh --interval ${REFRESH_INTERVAL} -- ${_vis_render_script} input none none ${DASHBOARD_ROWS_PER_COLUMN} ${_vis_input_width} --label BUGS ${_all_proj_flags}"
VIS_PRIORITY_CMD="${SCRIPT_DIR}/cadence-loop.sh --interval ${REFRESH_INTERVAL} -- ${_vis_render_script} input none none ${DASHBOARD_ROWS_PER_COLUMN} ${_vis_input_width} --label PRIORITIES ${_all_proj_flags}"
VIS_REQUIREMENTS_CMD="${SCRIPT_DIR}/cadence-loop.sh --interval ${REFRESH_INTERVAL} -- ${_vis_render_script} input none none ${DASHBOARD_ROWS_PER_COLUMN} ${_vis_input_width} --label REQUIREMENTS ${_all_proj_flags}"

# Bottom row: agent queue columns — queue subcommand with placeholder path.
VIS_PM_CMD="${SCRIPT_DIR}/cadence-loop.sh --interval ${REFRESH_INTERVAL} -- ${_vis_render_script} queue none ${DASHBOARD_ROWS_PER_COLUMN} ${_vis_queue_width} --label PM ${_all_proj_flags}"
VIS_CODER_CMD="${SCRIPT_DIR}/cadence-loop.sh --interval ${REFRESH_INTERVAL} -- ${_vis_render_script} queue none ${DASHBOARD_ROWS_PER_COLUMN} ${_vis_queue_width} --label CODER ${_all_proj_flags}"
VIS_WRITER_CMD="${SCRIPT_DIR}/cadence-loop.sh --interval ${REFRESH_INTERVAL} -- ${_vis_render_script} queue none ${DASHBOARD_ROWS_PER_COLUMN} ${_vis_queue_width} --label WRITER ${_all_proj_flags}"
VIS_TESTER_CMD="${SCRIPT_DIR}/cadence-loop.sh --interval ${REFRESH_INTERVAL} -- ${_vis_render_script} queue none ${DASHBOARD_ROWS_PER_COLUMN} ${_vis_queue_width} --label TESTER ${_all_proj_flags}"
VIS_CM_CMD="${SCRIPT_DIR}/cadence-loop.sh --interval ${REFRESH_INTERVAL} -- ${_vis_render_script} queue none ${DASHBOARD_ROWS_PER_COLUMN} ${_vis_queue_width} --label CM ${_all_proj_flags}"

# Legend bar: persistent color legend beneath the board columns.
# Runs under cadence-loop.sh (not watch) to preserve the 24-bit truecolor
# sequences legend-render.sh emits for per-project color swatches.
VIS_LEGEND_CMD="${SCRIPT_DIR}/cadence-loop.sh --interval ${REFRESH_INTERVAL} -- ${SCRIPT_DIR}/legend-render.sh --kanban-root ${KANBAN_ROOT}"

# Operator window aggregated commands:
# Each command combines all columns for its region into a single pane — stacked vertically
# within one refresh loop — using the same _vis_render_script and _all_proj_flags as the
# VIS_*_CMD variables above (same code path; fixture string-compare targets these variables).
# The combined renderer sequence is wrapped in `bash -c '{ ...; }'` so it is
# passed to cadence-loop.sh as a single argv element cadence-loop.sh's
# render step can execute directly (see lib/cadence.sh
# dashboard_cadence_render_once), exactly reproducing the old inline
# compound-group body.
#
# Left aggregate: bugs + priority + requirements (mirrors Visibility window left region).
OP_VIS_LEFT_AGG_CMD="${SCRIPT_DIR}/cadence-loop.sh --interval ${REFRESH_INTERVAL} -- bash -c '{ ${_vis_render_script} input none none ${DASHBOARD_ROWS_PER_COLUMN} ${_vis_input_width} --label BUGS ${_all_proj_flags}; echo; ${_vis_render_script} input none none ${DASHBOARD_ROWS_PER_COLUMN} ${_vis_input_width} --label PRIORITIES ${_all_proj_flags}; echo; ${_vis_render_script} input none none ${DASHBOARD_ROWS_PER_COLUMN} ${_vis_input_width} --label REQUIREMENTS ${_all_proj_flags}; }'"
# Right aggregate: pm + coder + writer + tester + cm (mirrors Visibility window right region).
OP_VIS_RIGHT_AGG_CMD="${SCRIPT_DIR}/cadence-loop.sh --interval ${REFRESH_INTERVAL} -- bash -c '{ ${_vis_render_script} queue none ${DASHBOARD_ROWS_PER_COLUMN} ${_vis_queue_width} --label PM ${_all_proj_flags}; echo; ${_vis_render_script} queue none ${DASHBOARD_ROWS_PER_COLUMN} ${_vis_queue_width} --label CODER ${_all_proj_flags}; echo; ${_vis_render_script} queue none ${DASHBOARD_ROWS_PER_COLUMN} ${_vis_queue_width} --label WRITER ${_all_proj_flags}; echo; ${_vis_render_script} queue none ${DASHBOARD_ROWS_PER_COLUMN} ${_vis_queue_width} --label TESTER ${_all_proj_flags}; echo; ${_vis_render_script} queue none ${DASHBOARD_ROWS_PER_COLUMN} ${_vis_queue_width} --label CM ${_all_proj_flags}; }'"

# Git window — two vertical panes, each under cadence-loop.sh at a 10s
# active-window interval (visibility-aware — see lib/cadence.sh).
# Left pane: git-status.sh (branch/working-tree/remotes).
# Right pane: git-recent-tags.sh (recent tags >=10, newest first).
# Both scripts are multi-project-aware and resolve each project's dev_tree_path from
# per-project config (project.cfg); neither depends on the global PGAI_DEV_TREE_PATH.
GIT_STATUS_CMD="${SCRIPT_DIR}/cadence-loop.sh --interval 10 -- ${SCRIPT_DIR}/git-status.sh --kanban-root ${KANBAN_ROOT}"
GIT_RECENT_TAGS_CMD="${SCRIPT_DIR}/cadence-loop.sh --interval 10 -- ${SCRIPT_DIR}/git-recent-tags.sh --kanban-root ${KANBAN_ROOT}"

# Metadata window — single pane running metadata.sh under
# cadence-loop.sh at a 10s active-window interval (visibility-aware —
# see lib/cadence.sh). Shows kanban-wide state (version, PM mode, HALT)
# and per-project rows (workflow type, active RC, last released, max_minor,
# max_major).
METADATA_CMD="${SCRIPT_DIR}/cadence-loop.sh --interval 10 -- ${SCRIPT_DIR}/metadata.sh --kanban-root ${KANBAN_ROOT}"

# Metrics window — left pane running metrics.sh under
# cadence-loop.sh (visibility-aware — see lib/cadence.sh).
# Shows today's per-project metrics (RCs shipped, wall time, tokens with cache
# hit %, task count) and current-RC progress (tasks done, elapsed, tokens so
# far).  Reads from metrics/day/<date>.json and metrics/rc/<v>.json.
# Uses the same refresh interval as other dashboard panes.
METRICS_CMD="${SCRIPT_DIR}/cadence-loop.sh --interval ${REFRESH_INTERVAL} -- ${SCRIPT_DIR}/metrics.sh --kanban-root ${KANBAN_ROOT}"

# Human-review window — left pane listing pending HUMAN-APPROVE gate tasks.
HUMAN_REVIEW_CMD="${SCRIPT_DIR}/cadence-loop.sh --interval ${REFRESH_INTERVAL} -- ${SCRIPT_DIR}/human-review.sh --kanban-root ${KANBAN_ROOT}"

# Human-review window — right pane: read-only OVERWATCH auto-resolutions
# ledger listing (RC, attempt N/M, decision, outcome, evidence links).
# Beside Approvals; informs only, never approves or mutates state.
AUTO_RESOLUTIONS_CMD="${SCRIPT_DIR}/cadence-loop.sh --interval ${REFRESH_INTERVAL} -- ${SCRIPT_DIR}/auto-resolutions.sh --kanban-root ${KANBAN_ROOT}"

# Metrics window — right pane running watch against show-metrics.sh.
# Shows historical RC metrics from history.csv (last 10 RCs): rc, wall time,
# input/output/cache tokens, cache hit rate, task count.
# Uses the same refresh interval as other dashboard panes.
SHOW_METRICS_CMD="${SCRIPT_DIR}/cadence-loop.sh --interval ${REFRESH_INTERVAL} -- ${SCRIPT_DIR}/show-metrics.sh --kanban-root ${KANBAN_ROOT}"

# Debug-logs and training-logs windows — the helper scripts resolve log paths
# internally via pp_project_root.  We only need to pass
# KANBAN_ROOT (via --kanban-root) and the relevant env-var gates at invocation.
#
# PGAI_VERBOSE_MODE and PGAI_REASONING_TRACE are passed through the environment
# at session-creation time so pane commands respect the operator's current
# settings. The helper scripts gate on these vars at startup.
_VERBOSE_MODE="${PGAI_VERBOSE_MODE:-}"
_REASONING_TRACE="${PGAI_REASONING_TRACE:-}"

# ---------------------------------------------------------------------------
# Create the session (detached) with Window 0 — dashboard
# Split sequence (full-height right pane for per-project status):
#   Step 1: session created  — pane 0 fills full window
#   Step 2: split pane 0 -h -l $QUEUES_PCT% — pane 0=left ~65%, pane 1=right ~35% (QUEUES, full height)
#   Step 3: split pane 0 -v -l 40%  — pane 0=left-top ~60%, pane 2=left-bottom 40% (LOGS)
#   Step 4: split pane 0 -v -l 82%  — pane 0=HEADER (~11% of left), pane 3=middle left (~49%, PROGRESS)
#   Step 5: send commands to all panes
#
# Cron/schedule content (the former CRON FIRINGS pane) now lives in its own
# "schedule" window instead of a pane here — the PROGRESS pane takes the
# full width of the middle-left row that CRON used to share with it.
#
# Target proportions at 120-char terminal:
#   pane 1 (QUEUES/per-project): ~48 chars wide, full height
#   Left column (~72 chars):
#     pane 0 (HEADER):    ~72 chars wide, ~5-6 rows tall
#     pane 3 (PROGRESS):  ~72 chars wide, middle height (full left-column width)
#     pane 2 (LOGS):      ~72 chars wide, bottom 40% height
#
# Final pane indices after all splits:
#   0 = HEADER (top-left, ~11% height)
#   1 = QUEUES / per-project status (right column, ~35% width, full height)
#   2 = LOGS (bottom-left, ~40% height)
#   3 = PROGRESS (middle-left, full width of left column)
#
# Drill-N windows: left as-is with the original 5-pane layout (HEADER /
# QUEUES-middle-left / PROGRESS-middle-center / CRON-middle-right / LOGS-bottom).
# Drill windows scope to ONE project, so the multi-project overflow problem that
# motivated this change does not apply there. Drill-N geometry consistency with
# window 0 is nice-to-have and deferred to a future ticket if desired.
# ---------------------------------------------------------------------------
tmux new-session -d -s "$SESSION_NAME" -n "main" -x "220" -y "50"

# ---------------------------------------------------------------------------
# Pane signal-safety: readiness directory + armed-pane tracking
#
# Every pane command below is dispatched through dashboard_send_armed_keys
# (never a bare `tmux send-keys` for a pane's FIRST command) so the trap
# installation in lib/pane_readiness.sh's dashboard_pane_wrap_cmd is that
# pane's literal first executed statement, ahead of env_bootstrap.sh
# sourcing (which happens later, inside the invoked scripts) and ahead of
# the watch/while-loop/shell command itself. See lib/pane_readiness.sh for
# the full design and the empirical proof of the bug it closes.
# ---------------------------------------------------------------------------
PANE_ARMED_DIR="$(dashboard_pane_armed_dir "$KANBAN_ROOT" "$SESSION_NAME")"
# Fresh state for this invocation: a marker left by an earlier run under
# this same session name must never make the all-armed handshake below
# pass falsely for a pane this run has not actually armed yet.
find "$PANE_ARMED_DIR" -mindepth 1 -delete 2>/dev/null || true

# The exact set of pane labels this run has dispatched a wrapped command
# to; the all-armed handshake at the end of pane creation polls precisely
# this set. Populated exclusively by dashboard_send_armed_keys.
PANE_ARMED_LABELS=()

# dashboard_send_armed_keys <pane-target-without-session> <inner-command> [--defer-marker]
# Wraps <inner-command> via dashboard_pane_wrap_cmd and sends it to
# "${SESSION_NAME}:<pane-target>" as that pane's first command, recording
# <pane-target> in PANE_ARMED_LABELS. This is the ONLY call site in this
# file that types a pane's FIRST command — every window below routes its
# initial per-pane command through this function so no pane can skip the
# trap. A pane's SUBSEQUENT send-keys calls (e.g. the operator shell pane's
# bashrc/shell-env re-source lines, typed into the shell after its first
# command already installed the trap) use plain `tmux send-keys` directly —
# the ignored disposition this function's wrap installs survives fork(2)
# and exec(2) (POSIX), so it is already in effect for anything that shell
# goes on to run and does not need reinstalling.
#
# --defer-marker: for the two panes whose first command ends in a bare
# `exec bash` / `exec bash -l` (see lib/pane_readiness.sh's
# dashboard_pane_wrap_cmd_no_marker header for why that exec drops the
# ignore trap this function would otherwise install). Wraps via
# dashboard_pane_wrap_cmd_no_marker instead — trap only, no armed-report
# touch — and still records <pane-target> in PANE_ARMED_LABELS, so
# create.sh's own handshake correctly BLOCKS on this pane until its
# caller's own re-arm send-keys (typed into the actually-resident shell)
# touches the marker itself. Never used directly by the caller to decide
# armed-ness; only the marker file is that proof.
dashboard_send_armed_keys() {
    local pane_target="$1" inner_cmd="$2" mode="${3:-}"
    local wrapped
    if [[ "$mode" == "--defer-marker" ]]; then
        wrapped="$(dashboard_pane_wrap_cmd_no_marker "$inner_cmd")"
    else
        wrapped="$(dashboard_pane_wrap_cmd "$PANE_ARMED_DIR" "$pane_target" "$inner_cmd")"
    fi
    tmux send-keys -t "${SESSION_NAME}:${pane_target}" "$wrapped" Enter
    PANE_ARMED_LABELS+=("$pane_target")
}

# dashboard_dispatch_board_panes <window>
# Sends the board's first commands to a window dashboard_build_board_window
# (lib/board_window.sh) has just laid out: the eight column commands to
# panes 0-7 and the legend command to the legend pane the builder
# recorded in DASHBOARD_BOARD_LEGEND_PANE. The operator and visibility
# windows both call this, so the two boards always run the same commands.
# A shell row, when the window has one, is left for the caller to start.
dashboard_dispatch_board_panes() {
    local board_win="$1"
    dashboard_send_armed_keys "${board_win}.0" "${VIS_BUGS_CMD}"
    dashboard_send_armed_keys "${board_win}.1" "${VIS_PRIORITY_CMD}"
    dashboard_send_armed_keys "${board_win}.2" "${VIS_REQUIREMENTS_CMD}"
    dashboard_send_armed_keys "${board_win}.3" "${VIS_PM_CMD}"
    dashboard_send_armed_keys "${board_win}.4" "${VIS_CODER_CMD}"
    dashboard_send_armed_keys "${board_win}.5" "${VIS_WRITER_CMD}"
    dashboard_send_armed_keys "${board_win}.6" "${VIS_TESTER_CMD}"
    dashboard_send_armed_keys "${board_win}.7" "${VIS_CM_CMD}"
    dashboard_send_armed_keys "${board_win}.${DASHBOARD_BOARD_LEGEND_PANE}" "${VIS_LEGEND_CMD}"
}

# ---------------------------------------------------------------------------
# DASHBOARD_ONE_SHOT_STATIC_PANES — the pane-refresh census exemption list.
#
# Every pane's first command sent through dashboard_send_armed_keys above
# must, once running, keep refreshing on its own — either wrapped in
# cadence-loop.sh (the shared visibility-aware refresh cadence every
# renderer column below uses, and --stream for the log panes that follow
# their logs) or named here by its EXACT pane-target
# string (the same literal text passed as dashboard_send_armed_keys's
# first argument, unresolved variable references included, e.g.
# "operator.${OP_SHELL_PANE}"). A pane in neither category is a bare
# renderer invocation: create.sh types its command once, the render
# prints and returns, and the pane's shell falls through to an
# interactive prompt underneath — a shell prompt and the echoed command
# sitting in a dashboard pane. That shape shipped once, undetected,
# because the schedule window's five per-agent columns carried the
# trap/armed-marker preamble (so the readiness handshake never noticed)
# but not the refresh wrap. team/scripts/dashboard/lint_pane_refresh_cadence.py
# is the check that now catches it: it fails on any pane meeting neither
# rule, and this list is the one place that grants the "meets rule 2"
# exemption.
#
# Panes named below are genuinely one-shot by design: the operator and
# terminal shell panes are interactive login shells meant to sit at a
# prompt. (The log panes, which stream forever, run under
# cadence-loop.sh --stream like every other rendering pane.)
#
# This is plain DATA, read by lint_pane_refresh_cadence.py as text between
# the two markers below — never executed, so a pane-target expression
# referencing a variable this script assigns further down (${OP_SHELL_PANE},
# ${_DRILL_WIN}, ...) can be named here in its literal, unexpanded form
# without tripping `set -u` at a point in the script before that variable
# is ever assigned. One pane-target string per line; leading/trailing
# whitespace on each line is not significant.
# CENSUS-EXEMPT-PANES:BEGIN
#   operator.${OP_SHELL_PANE}
#   terminal.1
# CENSUS-EXEMPT-PANES:END

# ---------------------------------------------------------------------------
# Session options — scoped so no other tmux session on the server changes
#
# dashboard_apply_session_options <session> <refresh_interval>
#
# Every option below is a SESSION option set on <session> alone (never -g,
# which would hand every session on the server the dashboard's three-line
# status bar), except terminal-overrides, which tmux keeps per server and
# which is therefore appended only when the server does not already carry
# the entry -- re-creating the dashboard never stacks duplicates. The
# window-status-style the status formats reference is a window option tmux
# cannot scope to a session; dashboard_apply_window_status_style sets it on
# each of this session's windows once they exist. @pgai_dashboard marks the
# session as a dashboard: the prefix+p binding and kill.sh key on it.
#
# Truecolor (24-bit) support — required for project tag color rendering.
# column-render.sh and legend-render.sh emit ANSI 24-bit truecolor
# sequences (\033[38;2;R;G;Bm) to render each project's configured
# display_color from projects.cfg. Without default-terminal tmux-256color
# (which carries the Tc flag) and the Tc/RGB terminal-overrides for the
# outer xterm-256color client, tmux strips those sequences and every
# project tag square ■ renders in the default terminal color. Basic
# 8-color ANSI (status colors: green/amber/red) is unaffected.
#
# Status bar — three-line layout:
# Line 0 (top):    session name on left + FRAMEWORK windows only (index 0-9,
#                  names NOT starting with drill-).  Drills excluded so the
#                  framework window list never truncates.
# Line 1 (middle): DRILL windows only (index 10+, names starting with drill-).
#                  Each drill tab uses its project's dashboard_color via the
#                  per-window tab-color option set at creation time (see below).
#                  Renders blank (no error) when no drill windows exist.
# Line 2 (bottom): project/version/workflow/RC/PM/HALT/time rendered by
#                  status-bottom.sh into the session's
#                  @pgai_status_bottom option by status-bottom-loop.sh.
#
# Line 0's #{W:...} iterates all windows; the inner
# #{?#{m:drill-*,#{window_name}},,OUTPUT} conditional emits output only for
# windows whose name does NOT start with drill-. Active window is
# highlighted bold/white; inactive windows use their window-status-style
# (fg=colour250 grey) — drill-N windows are excluded here so their
# per-window window-status-style does not appear on this line.
# Line 1's conditional is the inverse: drill windows only, each inactive
# tab in its project's dashboard_color (set-window-option
# window-status-style, done for each drill-N window below). With no drill
# window the line renders blank — no error.
# Line 2 is the session's @pgai_status_bottom option, which tmux expands
# with no fork. One background loop (status-bottom-loop.sh), started below
# with the session and exiting when the session ends, renders
# status-bottom.sh into that option every REFRESH_INTERVAL seconds while a
# client is attached. A #() job here would be re-run by tmux on almost
# every status redraw, once per attached client.
# ---------------------------------------------------------------------------
dashboard_apply_session_options() {
  local session="$1" interval="$2" override existing
  tmux set-option -t "$session" @pgai_dashboard 1
  tmux set-option -t "$session" default-terminal "tmux-256color"
  existing="$(tmux show-options -sv terminal-overrides 2>/dev/null || true)"
  for override in "xterm-256color:Tc" "xterm-256color:RGB"; do
    grep -qF -- "$override" <<< "$existing" || tmux set-option -sa terminal-overrides ",${override}"
  done

  tmux set-option -t "$session" status 3
  tmux set-option -t "$session" status-style "bg=#2a2a2a,fg=white"
  tmux set-option -t "$session" status-interval "${interval}"
  tmux set-option -t "$session" "status-format[0]" \
    "#[bg=#2a2a2a,fg=cyan,bold][#S]#[default,bg=#2a2a2a] #{W:#{?#{m:drill-*,#{window_name}},,#{?window_active,${DASHBOARD_TMUX_ACTIVE_WINDOW_STYLE},#[window-status-style]}#{window_index}:#{window_name}#{?window_active,*,} }}"
  tmux set-option -t "$session" "status-format[1]" \
    "#[bg=#2a2a2a,fg=colour250]#{W:#{?#{m:drill-*,#{window_name}},#{?window_active,${DASHBOARD_TMUX_ACTIVE_WINDOW_STYLE},#[window-status-style]}#{window_index}:#{window_name}#{?window_active,*,} ,}}"
  tmux set-option -t "$session" "status-format[2]" \
    "#[bg=#1a1a1a,fg=colour250]#{@pgai_status_bottom}#[default]"
}

# ---------------------------------------------------------------------------
# dashboard_apply_window_status_style <session>
#
# Give each of <session>'s windows that has no window-status-style of its
# own (a drill window carries its project's color) the dashboard's grey
# inactive-tab style, window by window -- the per-session stand-in for a
# global window option, which would restyle every session's tabs.
# ---------------------------------------------------------------------------
dashboard_apply_window_status_style() {
  local session="$1" window_id
  while IFS= read -r window_id; do
    [[ -n "$window_id" ]] || continue
    [[ -n "$(tmux show-options -wqv -t "$window_id" window-status-style 2>/dev/null)" ]] && continue
    tmux set-option -w -t "$window_id" window-status-style "fg=colour250"
  done < <(tmux list-windows -t "$session" -F '#{window_id}' 2>/dev/null || true)
}

dashboard_apply_session_options "$SESSION_NAME" "$REFRESH_INTERVAL"
nohup "${SCRIPT_DIR}/status-bottom-loop.sh" --session "$SESSION_NAME" \
  --interval "$REFRESH_INTERVAL" --option "@pgai_status_bottom" \
  -- "${SCRIPT_DIR}/status-bottom.sh" "$KANBAN_ROOT" </dev/null >/dev/null 2>&1 &
disown

# Step 2: split full window horizontally — right ~25% (full height, eventually QUEUES).
# This creates the full-height right-column pane.
# At this point: pane 0 = left ~75%, pane 1 = right ~25%.
# NOTE: after subsequent left-column vertical splits (steps 3-4), tmux renumbers panes
# in top-to-bottom, left-to-right order — the right-column pane ends up as pane 4, NOT pane 1.
# QUEUES_PCT is sourced from lib/dashboard_constants.sh (value 25).
tmux split-window -t "${SESSION_NAME}:main.0" -h -l "${QUEUES_PCT}%"

# Step 3: split the left area (pane 0) vertically — left-bottom 40% = LOGS.
# After split: left-top (pane 0), left-bottom (pane 1), right (pane 2 after renumber).
tmux split-window -t "${SESSION_NAME}:main.0" -v -l 40%

# Step 4: split the top-left area (pane 0) vertically — HEADER at top (~11%), middle below.
# After split: pane 0=HEADER(top-left), pane 1=middle-left, pane 2=LOGS(bottom-left), pane 3=RIGHT.
tmux split-window -t "${SESSION_NAME}:main.0" -v -l 82%

# After steps 2-4, tmux renumbers panes in top-to-bottom, left-to-right order,
# producing the final 4-pane layout directly (no further split needed now that
# CRON has its own "schedule" window):
#   pane 0=HEADER, pane 1=LOGS(middle,full-width), pane 2=PROGRESS(bottom-left,
#   full width of the left column), pane 3=QUEUES(right,full-height)

# ---------------------------------------------------------------------------
# Post-creation explicit resize for window 0 — absolute widths and heights.
# Mirrors the window-2 approach: use explicit post-creation resize rather than
# chained percentage splits.
#
# WHY NEEDED: chained "split-window -l N%" applies each percentage against the
# shrinking remainder of the prior split, not the original window size.  Under
# tmux 3.2a, integer rounding compounds at each step and produces scrambled
# proportions.
#
# SIZING APPROACH:
#   1. All 4 panes are already created above with rough percentage splits
#      (exact creation-time sizes do not matter).
#   2. Capture the true window width and height after all splits.
#   3. Force-resize every pane to an absolute character width/height:
#
#   WIDTH (25/75 SPLIT — constant 25%; single-sourced via lib/dashboard_constants.sh):
#     QUEUES_BUDGET = floor(MAIN_WIDTH * QUEUES_PCT / 100)  — right column budget
#     LEFT_W        = MAIN_WIDTH - QUEUES_BUDGET - 1        — left column (-1 divider)
#     PROGRESS now spans the full left-column width (no CRON sibling to share
#     the row with — CRON has its own "schedule" window): PROG_W = LEFT_W.
#
#   HEIGHT (three left-column rows: HEADER / LOGS / PROGRESS):
#     LEFT_CONTENT_H = MAIN_HEIGHT - 2   — 2 borders for 3 stacked rows
#     HEADER_H       = floor(MAIN_HEIGHT * 11 / 100)   — ~11% for header row
#     LOGS_H         = floor(LEFT_CONTENT_H * 40 / 100) — ~40% for LOGS row (middle)
#     MID_H          = LEFT_CONTENT_H - HEADER_H - LOGS_H — remainder for PROGRESS row (bottom)
#     QUEUES_H       = MAIN_HEIGHT - 1   — full height minus top border
#
#   Minimums enforced: LEFT_W >= 30, QUEUES_BUDGET >= 20,
#     HEADER_H >= max(5, SCHEDULE_ROSTER_COUNT + PGAI_SCHEDULE_HEADER_RESERVED_ROWS)
#       — roster-sized: never smaller than the current cron roster's row
#         count plus show-header.sh's own reserved-lines accounting
#         (lib/dashboard_constants.sh), so the HEADER tile's cron-firings
#         column shows the full roster without truncation on a
#         freshly-created pane,
#     MID_H >= 5, LOGS_H >= 5, QUEUES_H >= 15, PROG_W >= 15.
#
#   All 4 panes receive an explicit -x and -y resize — no pane absorbs a
#   tmux remainder implicitly.
# ---------------------------------------------------------------------------
MAIN_WIDTH=$(tmux display-message -t "${SESSION_NAME}:main" -p '#{window_width}')
MAIN_HEIGHT=$(tmux display-message -t "${SESSION_NAME}:main" -p '#{window_height}')

# Guard: fall back to safe minima if tmux query returns empty or zero
MAIN_WIDTH=${MAIN_WIDTH:-120}
MAIN_HEIGHT=${MAIN_HEIGHT:-24}
[[ "$MAIN_WIDTH"  -lt 50 ]] && MAIN_WIDTH=50
[[ "$MAIN_HEIGHT" -lt 17 ]] && MAIN_HEIGHT=17   # 17 = 5+5+5 rows + 2 borders + 0 legend

# Compute column widths with 25/75 split and explicit border accounting.
# QUEUES_PCT sourced from lib/dashboard_constants.sh (value 25).
#
# Right column (QUEUES, pane 3):
#   QUEUES_BUDGET = floor(MAIN_WIDTH * QUEUES_PCT / 100)
#
# Left column (panes 0/1/2):
#   LEFT_W = MAIN_WIDTH - QUEUES_BUDGET - 1  (subtract 1 for left/right divider)
#
# PROGRESS (pane 2) now spans the full left-column width — CRON moved to its
# own "schedule" window, so there is no sibling to share the bottom row with.
QUEUES_BUDGET=$(( MAIN_WIDTH * QUEUES_PCT / 100 ))
[[ "$QUEUES_BUDGET" -lt 20 ]] && QUEUES_BUDGET=20
LEFT_W=$(( MAIN_WIDTH - QUEUES_BUDGET - 1 ))
[[ "$LEFT_W" -lt 30 ]] && LEFT_W=30

PROG_W="$LEFT_W"
[[ "$PROG_W" -lt 15 ]] && PROG_W=15

# Compute row heights for the left column.
# Row order: HEADER (top) / LOGS (middle) / PROGRESS (bottom).
#
# Three stacked rows (HEADER / LOGS / PROGRESS) share the full window height with
# 2 inter-row borders:
#   LEFT_CONTENT_H = MAIN_HEIGHT - 2
#   HEADER_H       = floor(MAIN_HEIGHT * 11 / 100)   (~11% — small header row, top)
#   LOGS_H         = floor(LEFT_CONTENT_H * 40 / 100) (~40% — LOGS row, middle)
#   MID_H          = LEFT_CONTENT_H - HEADER_H - LOGS_H  (remainder — PROGRESS row, bottom)
#
# QUEUES (pane 3, right column) is full height; it uses MAIN_HEIGHT - 1 (top border).
LEFT_CONTENT_H=$(( MAIN_HEIGHT - 2 ))
[[ "$LEFT_CONTENT_H" -lt 15 ]] && LEFT_CONTENT_H=15

HEADER_H=$(( MAIN_HEIGHT * 11 / 100 ))
[[ "$HEADER_H" -lt 5 ]] && HEADER_H=5
# Roster floor: the HEADER tile's cron-firings column reserves
# PGAI_SCHEDULE_HEADER_RESERVED_ROWS lines (project/version, blank, title —
# lib/dashboard_constants.sh) above its per-agent rows, so the pane needs at
# least that many plus one row per current roster agent (SCHEDULE_ROSTER_COUNT,
# resolved once above from the live crontab) to show every agent without
# show-header.sh's own "+N more" truncation marker firing on creation.
# HEADER_MIN_H itself is resolved once, above, alongside SCHEDULE_ROSTER_COUNT
# (single source of truth — this block only clamps up to it).
[[ "$HEADER_H" -lt "$HEADER_MIN_H" ]] && HEADER_H="$HEADER_MIN_H"

LOGS_H=$(( LEFT_CONTENT_H * 40 / 100 ))
[[ "$LOGS_H" -lt 5 ]] && LOGS_H=5

MID_H=$(( LEFT_CONTENT_H - HEADER_H - LOGS_H ))
[[ "$MID_H" -lt 5 ]] && MID_H=5

QUEUES_H=$(( MAIN_HEIGHT - 1 ))
[[ "$QUEUES_H" -lt 15 ]] && QUEUES_H=15

# Resize all 4 window-0 panes to their absolute target dimensions.
# Pane index → role mapping (tmux renumbers in top-to-bottom, left-to-right
# order after all splits; verified by tmux list-panes inspection):
#   pane 0 = HEADER        — left column, top row
#   pane 1 = LOGS          — left column, middle row (full-width)
#   pane 2 = PROGRESS      — left column, bottom row (full width of left column)
#   pane 3 = QUEUES        — right column, full height  ← physically rightmost (largest pane_left)
tmux resize-pane -t "${SESSION_NAME}:main.0" -x "${LEFT_W}"        -y "${HEADER_H}"
tmux resize-pane -t "${SESSION_NAME}:main.1" -x "${LEFT_W}"        -y "${LOGS_H}"
tmux resize-pane -t "${SESSION_NAME}:main.2" -x "${PROG_W}"        -y "${MID_H}"
tmux resize-pane -t "${SESSION_NAME}:main.3" -x "${QUEUES_BUDGET}" -y "${QUEUES_H}"

# Step 6: send commands to all panes
# Pane mapping (physical positions verified by tmux list-panes pane_left inspection):
#   0=HEADER(top-left), 1=LOGS(middle-left,full-width), 2=PROGRESS(bottom-left,
#   full width of the left column), 3=QUEUES(right,full-height — physically rightmost ~35%)
dashboard_send_armed_keys "main.0" "${HEADER_CMD}"
dashboard_send_armed_keys "main.1" "${LOGS_PANE_CMD}"
dashboard_send_armed_keys "main.2" "${PROGRESS_CMD}"
dashboard_send_armed_keys "main.3" "${QUEUES_CMD}"

# ---------------------------------------------------------------------------
# Window: Operator — the visibility window plus a command line
#
# Rationale: inside a container attach there is no "just open another terminal"
# — this window is the container operator's whole workspace: watch the board
# and run commands, one screen.
#
# Layout, top to bottom (the visibility window exactly, with one full-width
# interactive shell row inserted between its board and its legend bar):
#   ┌──────────────────────────────┬──────────────────────────────────────────┐
#   │ LEFT ~40% (Inputs)           │ RIGHT ~60% (Agent Queues)                │
#   │ bugs (0) │ prio (1) │req (2) │ pm (3) │coder(4)│writ(5)│test(6)│cm (7) │
#   ├──────────────────────────────┴──────────────────────────────────────────┤
#   │ CMDLINE (pane 8) — interactive bash login shell, PGAI_OPERATOR_SHELL_PCT │
#   │ of window height (floor), at least PGAI_OPERATOR_SHELL_MIN_ROWS rows      │
#   ├──────────────────────────────────────────────────────────────────────────┤
#   │ LEGEND (pane 9) — PROJECT: ■ name ... | STATUS: open  running ...      │
#   └──────────────────────────────────────────────────────────────────────────┘
#
# The splits, column widths and row heights all come from
# dashboard_build_board_window (lib/board_window.sh) — the same builder the
# Visibility window below uses — so panes 0-7 and the legend have exactly the
# visibility window's widths. dashboard_dispatch_board_panes sends both
# windows the same eight column commands and the same legend command.
# ---------------------------------------------------------------------------
tmux new-window -t "${SESSION_NAME}" -n "operator"
dashboard_build_board_window "${SESSION_NAME}" "operator" \
    "${PGAI_OPERATOR_SHELL_PCT}" "${PGAI_OPERATOR_SHELL_MIN_ROWS}"

# Track the shell pane's index (recorded by the builder) in a variable rather
# than hardcoding ".8" at every use site below.
OP_SHELL_PANE="${DASHBOARD_BOARD_SHELL_PANE}"

dashboard_dispatch_board_panes "operator"

# Shell row (between the board and the legend): interactive bash login shell
# with kanban env sourced, cwd = KANBAN_ROOT.
# Source shell-env the same way create.sh does at startup (lines ~170-176):
# wrap in bash -l so /etc/profile.d/ and ~/.bash_profile run, giving the operator
# a complete login environment with PATH, pyenv, etc.
_OPERATOR_SHELL_INIT="cd ${KANBAN_ROOT}"
if [[ -f "${KANBAN_ROOT}/shell-env" ]]; then
    _OPERATOR_SHELL_INIT="source ${KANBAN_ROOT}/shell-env && ${_OPERATOR_SHELL_INIT}"
fi
# This is the pane's FIRST command, so it goes through
# dashboard_send_armed_keys like every other pane's first command: the
# trap installed ahead of it survives the `bash -l -c` fork below (a fork
# alone -- no exec of a NEW bash -- always preserves an ignored
# disposition, POSIX). The FINAL `exec bash -l` inside that `-c` string is
# a different story, verified empirically (not just reasoned about)
# against tmux 3.2a / bash 5.x: a freshly-started bash resets an inherited
# SIG_IGN for a non-job-control signal (confirmed via
# /proc/<pid>/status SigIgn straddling the exec) even though POSIX exec(2)
# itself would have preserved it -- bash's own startup path re-establishes
# its signal set rather than trusting what it inherited. The resident
# shell that actually stays running in this pane is therefore UNPROTECTED
# again immediately after the `exec bash -l` above returns, until the
# follow-on send-keys immediately below re-installs the ignore trap
# in-process (a `trap` builtin call, not another exec, so THIS one does
# take hold and persists for the pane's remaining lifetime).
#
# --defer-marker: the armed-report touch this function would otherwise
# fold into the SAME command as the trap install must NOT land yet -- it
# would be true of the shell running THIS line (correctly armed), not the
# `exec bash -l` shell that actually stays resident (unprotected until the
# re-arm below runs). Touching it early let a session-window-changed hook
# firing in that gap (a real, if usually brief, window -- exactly what a
# rapid-switch storm with no settling time raises the odds of hitting)
# treat this pane as safe to signal when it was not, killing it outright.
# The re-arm send-keys below now touches the marker itself, once the
# ignore trap is ACTUALLY back in the shell that is going to keep running.
dashboard_send_armed_keys "operator.${OP_SHELL_PANE}" "bash -l -c '${_OPERATOR_SHELL_INIT}; exec bash -l'" --defer-marker

# Re-arm: the `exec bash -l` above starts a brand new bash that does NOT
# keep the ignore trap the wrapped first command installed (see the
# comment above) -- re-install it now, as the very first thing typed into
# the now-resident shell, before any of the follow-ups below run. This is
# a `trap` builtin call in the SAME already-running process, not an exec,
# so it takes effect immediately and is not itself at risk of being reset
# again. The armed-report touch is folded into this SAME typed line (never
# a separate send-keys call) so the marker's existence is inseparable from
# -- and can never outrun -- this specific process's own trap install: by
# the time create.sh's all-armed handshake (or any activation-hook firing)
# observes the marker, the ignore trap it depends on is already in effect
# in the exact process #{pane_pid} still names.
tmux send-keys -t "${SESSION_NAME}:operator.${OP_SHELL_PANE}" "trap '' ${PGAI_DASHBOARD_PANE_SIGNAL}; : > $(printf '%q' "${PANE_ARMED_DIR}/operator.${OP_SHELL_PANE}")" Enter

# Guarded ~/.bashrc source so the operator's aliases, prompt, and other
# interactive conveniences are present. This is sent as a SEPARATE
# send-keys call, typed into the shell AFTER it starts and after the
# re-arm above, rather than folded into _OPERATOR_SHELL_INIT: the
# "exec bash -l" on the dispatch line replaces the process, which would
# discard any aliases a `source ~/.bashrc` had defined earlier in that
# same process. Typing it here means it runs in the shell that actually
# stays resident for the operator, so the aliases survive. Guarded so
# hosts without a ~/.bashrc start silently and cleanly (no error output
# in the pane). Not wrapped: this is a follow-up typed into the shell
# AFTER the re-arm send-keys above already re-installed its trap.
tmux send-keys -t "${SESSION_NAME}:operator.${OP_SHELL_PANE}" '[ -f "$HOME/.bashrc" ] && source "$HOME/.bashrc"' Enter

# Ordering: the operator's ~/.bashrc is arbitrary and may redefine PATH or
# unset/override a PGAI_* value. Sourcing bashrc AFTER shell-env (naively
# "kanban env wins because it ran first") is backwards -- whichever source
# runs LAST wins, so if bashrc runs after shell-env it can clobber kanban
# env. To guarantee PGAI_* / kanban-env values are correct once the pane
# is ready, re-source shell-env LAST, after bashrc, so kanban's values are
# always the ones left standing regardless of what bashrc does.
if [[ -f "${KANBAN_ROOT}/shell-env" ]]; then
    tmux send-keys -t "${SESSION_NAME}:operator.${OP_SHELL_PANE}" "source ${KANBAN_ROOT}/shell-env" Enter
fi

# Label the shell row so its border identifies it as the operator shell
tmux select-pane -t "${SESSION_NAME}:operator.${OP_SHELL_PANE}" -T "operator"

# Make the shell pane the window's active pane: entering the operator
# window should land the operator on the command line, not on a
# read-only column pane. The last split performed above
# (building the 8-column ladder) leaves an unrelated column pane as this
# window's remembered "active" pane, and `select-pane -T` immediately
# above sets only the pane's border title -- it does not change which
# pane is active. An explicit selection, via the tracked OP_SHELL_PANE
# variable rather than a hardcoded literal, is required so this keeps
# tracking the shell pane even if the column ladder changes later.
tmux select-pane -t "${SESSION_NAME}:operator.${OP_SHELL_PANE}"

# ---------------------------------------------------------------------------
# Window: Visibility — unified 3+5 pane layout + legend (Dashboard 2.1)
#
# Three discovery-pipeline columns on the LEFT (~40% width) and five
# agent-queue columns on the RIGHT (~60% width), arranged side-by-side
# (horizontal split), with a one-line persistent legend at the bottom.
# Designed for 120-char-wide terminals; degrades gracefully at narrower widths
# (tmux clips individual panes rather than scrolling horizontally).
#
# Final layout (pane indices):
#   ┌──────────────────────────────┬──────────────────────────────────────────┐
#   │ LEFT ~40% (Inputs)           │ RIGHT ~60% (Agent Queues)                │
#   │ bugs (0) │ prio (1) │req (2) │ pm (3) │coder(4)│writ(5)│test(6)│cm (7) │
#   ├──────────────────────────────┴──────────────────────────────────────────┤
#   │ LEGEND (pane 8) — PROJECT: ■ name ... | STATUS: open  running ...      │
#   └──────────────────────────────────────────────────────────────────────────┘
#
# The split ladder and the absolute-width resize pass (every column sized
# against the full window width, not a shrinking chained-percentage
# remainder) live in dashboard_build_board_window (lib/board_window.sh),
# shared with the Operator window above; shell_pct 0 builds the board and
# legend only.
# ---------------------------------------------------------------------------
tmux new-window -t "${SESSION_NAME}" -n "visibility"
dashboard_build_board_window "${SESSION_NAME}" "visibility" 0
dashboard_dispatch_board_panes "visibility"

# ---------------------------------------------------------------------------
# Window: Attention — blocked tasks panel (attention.sh)
# ---------------------------------------------------------------------------
tmux new-window -t "${SESSION_NAME}" -n "attention"
dashboard_send_armed_keys "attention" "${ATTENTION_CMD}"

# ---------------------------------------------------------------------------
# Window: schedule — cron/schedule content, split out of window 0's former
# CRON FIRINGS pane into its own named window (v2 census). Two full-width
# bands stacked top to bottom.
#
# Top band (list): status-band.sh — one row per roster agent (LAST FIRED /
#   LAST RESULT / NEXT) under its column header. Exactly SCHED_LIST_H rows
#   (lib/schedule_layout.sh: header + one row per roster agent + one row
#   of headroom), derived from SCHEDULE_ROSTER_COUNT above, never a fixed
#   count. tmux resizes panes proportionally when a client attaches with a
#   different terminal size or the window is resized, which would squeeze
#   the list below its rows; the hooks installed at the end of this script
#   (dashboard_install_schedule_layout_hook) re-apply SCHED_LIST_H. This
#   window's own window-layout-changed hook runs after tmux has applied a
#   new size, so it covers the first view after a client attaches at
#   another size and a live terminal resize; the session hooks cover an
#   explicit resize-window and a window switch. Nothing is re-applied while
#   the window is zoomed.
# Bottom band (event, takes the remaining height): event-band.sh — the
#   merged, chronologic, signal-only wake-log stream across every agent
#   (the raw per-agent logs remain available via the "logs" window and the
#   standalone per-agent tail script for forensics — this is a second,
#   filtered view, not a replacement). When the window is too short for
#   both, this band shrinks first; the list keeps its rows.
#
# The next-agent-wake countdown (next-cron-firings.sh) is not in this
# window: the list's NEXT column carries the same information, one agent
# per row. Window 0's HEADER tile and the drill windows keep their own.
#
# Each band is routed through cadence-loop.sh, same as every other
# renderer pane in this file — the trap/armed-marker preamble
# dashboard_send_armed_keys installs stays exactly where it already lives
# (ahead of this command, in the wrap), and cadence-loop.sh's one-time
# per-pane jitter plus its `clear` before the first render are what a bare
# renderer invocation would otherwise be missing (see
# lint_pane_refresh_cadence.py).
#
# Split sequence:
#   Step 1: window created → pane 0 (full)
#   Step 2: split pane 0 -v → pane 0=list (top), pane 1=event (bottom); the
#           resize below sets the list to SCHED_LIST_H and the event band
#           takes the rest.
# ---------------------------------------------------------------------------
SCHED_STATUS_CMD="${SCRIPT_DIR}/cadence-loop.sh --interval ${REFRESH_INTERVAL} -- ${SCRIPT_DIR}/status-band.sh --kanban-root ${KANBAN_ROOT}"
SCHED_EVENT_CMD="${SCRIPT_DIR}/cadence-loop.sh --interval ${REFRESH_INTERVAL} -- ${SCRIPT_DIR}/event-band.sh --kanban-root ${KANBAN_ROOT}"

tmux new-window -t "${SESSION_NAME}" -n "schedule"

# Step 2: list band on top, event band below.
tmux split-window -t "${SESSION_NAME}:schedule.0" -v
# Final: 0=list 1=event

SCHED_WIDTH=$(tmux display-message -t "${SESSION_NAME}:schedule" -p '#{window_width}')
SCHED_WIDTH=${SCHED_WIDTH:-120}
[[ "$SCHED_WIDTH"  -lt 60 ]] && SCHED_WIDTH=60    # status-band.sh's fixed-width columns need this much
tmux resize-pane -t "${SESSION_NAME}:schedule.0" -x "${SCHED_WIDTH}" -y "${SCHED_LIST_H}"

dashboard_send_armed_keys "schedule.0" "${SCHED_STATUS_CMD}"
dashboard_send_armed_keys "schedule.1" "${SCHED_EVENT_CMD}"

# ---------------------------------------------------------------------------
# Window: Git — two vertical panes
#
# Left pane:  git-status.sh, under cadence-loop.sh at a 10s
#   active-window interval (visibility-aware: 10x slower while hidden,
#   immediate refresh on activation — see lib/cadence.sh)
#   Shows live git state of the dev tree:
#     - Current branch + sync-with-origin status
#     - Uncommitted changes summary
#     - In-flight rc/* branches
#     - Recent commits on main (top 5)
#
# Right pane: git-recent-tags.sh, same cadence-loop.sh 10s cadence
#   Shows Recent Tags listing (>=10 newest tags, newest first).
#
# Both panes are multi-project-aware and resolve each project's dev_tree_path
# from per-project config (project.cfg).  Neither depends on PGAI_DEV_TREE_PATH.
#
# Split sequence:
#   Step 1: window created  → pane 0 (full width)
#   Step 2: split pane 0 -h -l 35% → pane 0=left (~65%), pane 1=right (~35%)
#   The right pane is sized to show 10 tags comfortably; the left pane keeps
#   the wider view for the full git-status output.
# ---------------------------------------------------------------------------
tmux new-window -t "${SESSION_NAME}" -n "git"
tmux split-window -t "${SESSION_NAME}:git.0" -h -l 35%
dashboard_send_armed_keys "git.0" "${GIT_STATUS_CMD}"
dashboard_send_armed_keys "git.1" "${GIT_RECENT_TAGS_CMD}"

# ---------------------------------------------------------------------------
# Window: Metadata — kanban-wide state + per-project pacing (single pane)
#
# Shows kanban version, PM_MODE, HALT, and per-project rows including
# workflow type, active RC, last released, max_minor, max_major.
# Runs under cadence-loop.sh at a 10s active-window interval
# (visibility-aware — see lib/cadence.sh).
# ---------------------------------------------------------------------------
tmux new-window -t "${SESSION_NAME}" -n "metadata"
dashboard_send_armed_keys "metadata" "${METADATA_CMD}"

# ---------------------------------------------------------------------------
# Window: rc-metrics — two vertical panes (history and current-RC economics)
#
# Left pane:  metrics.sh, under cadence-loop.sh at the standard
#   $REFRESH_INTERVAL active-window interval (visibility-aware — see lib/cadence.sh)
#   Shows today's per-project metrics (RCs shipped, wall time, tokens with
#   cache hit %, task count) and current-RC block (tasks done, elapsed wall
#   time, tokens so far).  Reads from metrics/day/<date>.json and
#   metrics/rc/<v>.json.
#
# Right pane: show-metrics.sh, same cadence-loop.sh interval
#   Shows historical RC metrics from history.csv (last 10 RCs):
#   rc, wall time, input/output/cache tokens, cache hit rate, task count.
#
# Split sequence:
#   Step 1: window created → pane 0 (full width)
#   Step 2: split pane 0 -h -l 60% → pane 0=left ~40% (metrics.sh), pane 1=right ~60% (show-metrics.sh)
#   show-metrics.sh table pane gets ~60% so the cache-write column
#   does not get truncated by tmux.
# ---------------------------------------------------------------------------
tmux new-window -t "${SESSION_NAME}" -n "rc-metrics"
tmux split-window -t "${SESSION_NAME}:rc-metrics.0" -h -l 60%
dashboard_send_armed_keys "rc-metrics.0" "${METRICS_CMD}"
dashboard_send_armed_keys "rc-metrics.1" "${SHOW_METRICS_CMD}"

# ---------------------------------------------------------------------------
# Window: agent-metrics — two vertical panes of average wall time, tokens,
# task count and totals per agent, per project (agent-metrics.sh).
#
# Left pane:  the live table over agent-metrics.sh's default span (the
#   newest 10 shipped RCs).
# Right pane: the full-history report over every shipped RC
#   (agent-metrics.sh --all).
#
# Both panes run under cadence-loop.sh at the standard $REFRESH_INTERVAL
# active-window interval (visibility-aware — see lib/cadence.sh), so a
# hidden window slows down like every other interval pane.
#
# Split sequence (same shape as the rc-metrics window above):
#   Step 1: window created → pane 0 (full width)
#   Step 2: split pane 0 -h -l 50% → pane 0=left ~50% (live table),
#           pane 1=right ~50% (full-history report)
# ---------------------------------------------------------------------------
AGENT_METRICS_CMD="${SCRIPT_DIR}/cadence-loop.sh --interval ${REFRESH_INTERVAL} -- ${SCRIPT_DIR}/agent-metrics.sh --kanban-root ${KANBAN_ROOT}"
AGENT_METRICS_ALL_CMD="${SCRIPT_DIR}/cadence-loop.sh --interval ${REFRESH_INTERVAL} -- ${SCRIPT_DIR}/agent-metrics.sh --all --kanban-root ${KANBAN_ROOT}"
tmux new-window -t "${SESSION_NAME}" -n "agent-metrics"
tmux split-window -t "${SESSION_NAME}:agent-metrics.0" -h -l 50%
dashboard_send_armed_keys "agent-metrics.0" "${AGENT_METRICS_CMD}"
dashboard_send_armed_keys "agent-metrics.1" "${AGENT_METRICS_ALL_CMD}"

# ---------------------------------------------------------------------------
# Window: Logs full-screen (named "logs")
# ---------------------------------------------------------------------------
tmux new-window -t "${SESSION_NAME}" -n "logs"
dashboard_send_armed_keys "logs" "${LOGS_WIN_CMD}"

# ---------------------------------------------------------------------------
# Window: debug-logs — unified, merged, color-coded debug log stream
#
# Single pane showing all agents' central debug logs in one interleaved,
# color-coded stream (same color scheme as logs.sh).
# When PGAI_VERBOSE_MODE=1: tails all agents' debug logs merged via tail -F.
# When PGAI_VERBOSE_MODE=0 or unset: shows a placeholder and waits.
#
# Debug log files: $KANBAN_ROOT/logs/debug/<agent>.log
# Missing log files are tolerated (tail -F waits for them to appear).
#
# Layout: single pane (full window).
# ---------------------------------------------------------------------------
tmux new-window -t "${SESSION_NAME}" -n "debug-logs"

# Single pane: send the unified merged stream command.
# env passes PGAI_VERBOSE_MODE so the helper script sees it at startup.
dashboard_send_armed_keys "debug-logs.0" \
  "${SCRIPT_DIR}/cadence-loop.sh --stream --interval ${REFRESH_INTERVAL} -- env PGAI_VERBOSE_MODE=${_VERBOSE_MODE} ${SCRIPT_DIR}/debug-logs.sh --kanban-root ${KANBAN_ROOT}"

# ---------------------------------------------------------------------------
# Window: training-logs — unified, merged, color-coded training trace viewer
#
# Single pane showing all agents' newest training traces in one color-coded
# view (same color scheme as logs.sh), refreshed every 30 seconds.
# When PGAI_REASONING_TRACE=1: shows each agent's newest trace sorted by
#   modification time (oldest first, most recent at bottom).
# When PGAI_REASONING_TRACE=0 or unset: shows a placeholder message.
#
# Trace file location: $KANBAN_ROOT/logs/training/<agent>/<ts>-<task-id>.md
# Missing training directories are tolerated gracefully.
#
# Layout: single pane (full window).
# ---------------------------------------------------------------------------
tmux new-window -t "${SESSION_NAME}" -n "training-logs"

# Single pane: send the unified merged trace command under cadence-loop.sh
# (30s active-window interval, visibility-aware — see lib/cadence.sh).
# env passes PGAI_REASONING_TRACE so the helper script sees it at startup.
dashboard_send_armed_keys "training-logs.0" \
  "${SCRIPT_DIR}/cadence-loop.sh --interval 30 -- env PGAI_REASONING_TRACE=${_REASONING_TRACE} ${SCRIPT_DIR}/training-logs.sh --kanban-root ${KANBAN_ROOT}"

# ---------------------------------------------------------------------------
# Window: Terminal — two-pane split: live logs (left) + KANBAN_ROOT shell (right)
#
# Left pane (0):  live log stream via logs.sh (same feed as the "logs" window).
# Right pane (1): a single interactive bash shell started at $KANBAN_ROOT.
#                 NOT pre-cd'd into any per-project task/queue subdirectory.
#
# Layout: horizontal split, logs occupy 60% of width on the left.
# ---------------------------------------------------------------------------
tmux new-window -t "${SESSION_NAME}" -n "terminal"
tmux split-window -t "${SESSION_NAME}:terminal.0" -h -l 40%

dashboard_send_armed_keys "terminal.0" "${LOGS_WIN_CMD}"
# --defer-marker: see the matching comment at the operator shell pane's
# own dashboard_send_armed_keys call, above -- the armed-report touch must
# wait for the re-arm below, not land here ahead of the `exec bash` that
# drops the ignore trap this line installs.
dashboard_send_armed_keys "terminal.1" "cd ${KANBAN_ROOT} && exec bash" --defer-marker
# Re-arm after the exec: `exec bash` above starts a brand new bash that
# does NOT keep the ignore trap the wrapped first command installed (see
# the matching comment at the operator shell pane's own re-arm send-keys,
# above, for the empirical proof). A `trap` builtin call typed into the
# now-resident shell, not another exec, so it takes hold immediately. The
# armed-report touch is folded into this SAME typed line (never a separate
# send-keys call) so the marker can never exist ahead of this specific,
# actually-resident process's own trap install -- see the operator shell
# pane's matching comment for why that ordering is load-bearing.
tmux send-keys -t "${SESSION_NAME}:terminal.1" "trap '' ${PGAI_DASHBOARD_PANE_SIGNAL}; : > $(printf '%q' "${PANE_ARMED_DIR}/terminal.1")" Enter

# ---------------------------------------------------------------------------
# Window: human-review — Approvals (left) + Auto-Resolutions (right)
#
# Left pane (Approvals): lists every pending HUMAN-APPROVE task across ALL
# registered projects.  Renders: project, target RC, age, show content,
# approve command, reject command.  Empty state: "no approvals pending."
# human-review.sh itself is unchanged — its rendered output is
# byte-identical to before this pane gained a neighbor.
#
# Right pane (Auto-Resolutions): read-only listing of OVERWATCH's
# autonomous resolution ledger entries across ALL registered projects
# (RC, attempt N/M, decision, outcome, evidence links).  Empty state:
# "no auto-resolutions."  No action affordances — informs, never
# approves or mutates state.
#
# Split sequence:
#   Step 1: window created → pane 0 (full width)
#   Step 2: split pane 0 -h -l 35% → pane 0=left Approvals (~65%),
#           pane 1=right Auto-Resolutions (~35%)
#   The narrower right pane matches the Git window's tags-pane sizing
#   convention (a compact secondary listing beside the primary one).
#
# Both panes run under cadence-loop.sh at the standard $REFRESH_INTERVAL
# active-window interval (visibility-aware — see lib/cadence.sh).
# ---------------------------------------------------------------------------
tmux new-window -t "${SESSION_NAME}" -n "human-review"
tmux split-window -t "${SESSION_NAME}:human-review.0" -h -l 35%
dashboard_send_armed_keys "human-review.0" "${HUMAN_REVIEW_CMD}"
dashboard_send_armed_keys "human-review.1" "${AUTO_RESOLUTIONS_CMD}"

# ---------------------------------------------------------------------------
# Windows drill-N (N=1,2,...): Per-project drill windows (Feature 4 — per-project view selector)
#
# One window named "drill-<N>" is created for each project registered in
# projects.cfg.  Each drill window uses the same 4-pane layout as the
# main window but filters all pane commands to a single project by
# passing --project <name> to the show-* scripts.
#
# With a single registered project, drill-1 is effectively identical to the
# main overview — no error, just one view.
#
# The prefix+p key binding (registered below) cycles through the windows:
#   main  →  drill-1  →  drill-2  →  ...  →  main
#
# Split sequence per drill window (uses a single-project layout;
# drill windows scope to ONE project and do not need the multi-project overflow
# handling used by window 0):
#   Step A: window created → pane 0 (full)
#   Step B: split pane 0 -v -l 40% → pane 0 (top 60%), pane 1 (bottom LOGS 40%)
#   Step C: split pane 0 -v -l 82% → pane 0 (HEADER ~11%), pane 2 (middle 49%)
#   Step D: split pane 2 -h -l 70% → pane 2 (QUEUES ~30%), pane 3 (right 70%)
#   Step E: split pane 3 -h -l 36% → pane 3 (PROGRESS ~45%), pane 4 (CRON ~25%)
#
# Final pane indices:
#   0 = HEADER (top ~11%)          — scoped to project N
#   1 = LOGS (bottom 40%)          — shared (log stream not project-gated)
#   2 = QUEUES (middle left ~30%)  — scoped to project N
#   3 = PROGRESS (middle center ~45%) — scoped to project N
#   4 = CRON FIRINGS (middle right ~25%) — shared (cron is a system schedule)
# ---------------------------------------------------------------------------

# Read registered projects (priority-sorted) from projects.cfg.
# If projects.cfg is absent or empty, _DRILL_PROJECTS stays empty and no
# drill windows are created (graceful degradation).
_DRILL_PROJECTS=()
while IFS= read -r _p; do
  [[ -z "$_p" ]] && continue
  _DRILL_PROJECTS+=("$_p")
done < <(KANBAN_ROOT="$KANBAN_ROOT" projects_cfg_list 2>/dev/null || true)

_DRILL_IDX=1
for _DRILL_PROJ in "${_DRILL_PROJECTS[@]}"; do

  # ---------------------------------------------------------------------------
  # Determine workflow type for this project so we can route to the correct
  # drill layout: release/feature → release-shaped (5 panes), document → document-
  # shaped (2 panes: artifact library + pipeline on top, logs on bottom).
  # ---------------------------------------------------------------------------
  _drill_proj_root="${KANBAN_ROOT}/projects/${_DRILL_PROJ}"
  _drill_cfg_file="$(_pp_project_cfg_file "${_drill_proj_root}")"
  _drill_wftype="release"
  if [[ -n "$_drill_cfg_file" ]]; then
    _drill_wftype="$(_pp_read_cfg_key "$_drill_cfg_file" project workflow_type "release" 2>/dev/null || echo "release")"
  fi

  _DRILL_WIN="drill-${_DRILL_IDX}"

  # ---------------------------------------------------------------------------
  # Look up the project's registered dashboard_color (with palette fallback)
  # and apply it as window-status-style so the drill tab in the tmux status
  # bar is visually colored per project.
  # ---------------------------------------------------------------------------
  _drill_proj_color="$(KANBAN_ROOT="$KANBAN_ROOT" projects_cfg_color "${_DRILL_PROJ}" 2>/dev/null || echo "")"

  _DRILL_LOGS_CMD="${SCRIPT_DIR}/cadence-loop.sh --stream --interval ${REFRESH_INTERVAL} -- ${SCRIPT_DIR}/logs.sh --kanban-root ${KANBAN_ROOT}"

  if [[ "$_drill_wftype" == "document" ]]; then
    # ---------------------------------------------------------------------------
    # Document-workflow drill: 2-pane layout
    #   Pane 0 (top ~55%): artifact version library + document-pipeline progress
    #   Pane 1 (bottom ~45%): shared log stream
    #
    # This replaces the release-shaped drill (RC state / agent queues / task
    # progress) which would show empty or misleading data for a document project.
    # show-document-drill.sh renders artifact basenames from
    # projects/<name>/artifacts/ and WRITER/TESTER/CM task-state summaries.
    # ---------------------------------------------------------------------------
    _DRILL_DOC_CMD="${SCRIPT_DIR}/cadence-loop.sh --interval ${REFRESH_INTERVAL} -- ${SCRIPT_DIR}/show-document-drill.sh --project ${_DRILL_PROJ} ${RENDERER_ARGS[*]}"

    tmux new-window -t "${SESSION_NAME}" -n "${_DRILL_WIN}"

    # Split: bottom 45% = LOGS, top 55% = document drill pane
    tmux split-window -t "${SESSION_NAME}:${_DRILL_WIN}.0" -v -l 45%

    dashboard_send_armed_keys "${_DRILL_WIN}.0" "${_DRILL_DOC_CMD}"
    dashboard_send_armed_keys "${_DRILL_WIN}.1" "${_DRILL_LOGS_CMD}"

  else
    # ---------------------------------------------------------------------------
    # Release/feature-workflow drill: 5-pane layout (original behavior, unchanged)
    #
    # Per-project watch commands — scoped to project N where applicable.
    # CRON FIRINGS is shared: cron entries are registered system-wide against the
    # host crontab and are not per-project; showing the same next-firing countdown
    # in every drill window gives operators consistent orientation regardless of
    # which project drill they are viewing.
    # ---------------------------------------------------------------------------
    _DRILL_HEADER_CMD="${SCRIPT_DIR}/cadence-loop.sh --interval ${REFRESH_INTERVAL} -- ${SCRIPT_DIR}/show-header.sh --project ${_DRILL_PROJ} ${RENDERER_ARGS[*]}"
    _DRILL_QUEUES_CMD="${SCRIPT_DIR}/cadence-loop.sh --interval ${REFRESH_INTERVAL} -- ${SCRIPT_DIR}/show-queues.sh --project ${_DRILL_PROJ} ${RENDERER_ARGS[*]}"
    _DRILL_PROGRESS_CMD="${SCRIPT_DIR}/cadence-loop.sh --interval ${REFRESH_INTERVAL} -- ${SCRIPT_DIR}/show-progress.sh --project ${_DRILL_PROJ} ${RENDERER_ARGS[*]}"
    _DRILL_CRON_CMD="${SCRIPT_DIR}/cadence-loop.sh --interval ${REFRESH_INTERVAL} -- ${SCRIPT_DIR}/next-cron-firings.sh --kanban-root ${KANBAN_ROOT}"

    tmux new-window -t "${SESSION_NAME}" -n "${_DRILL_WIN}"

    # Step B: bottom 40% = LOGS
    tmux split-window -t "${SESSION_NAME}:${_DRILL_WIN}.0" -v -l 40%
    # Step C: split top area — HEADER at top (~11%), middle below
    tmux split-window -t "${SESSION_NAME}:${_DRILL_WIN}.0" -v -l 82%
    # Step D: split middle — QUEUES left (~30%), right 70%
    tmux split-window -t "${SESSION_NAME}:${_DRILL_WIN}.2" -h -l 70%
    # Step E: split right half — PROGRESS (~45% total), CRON right (~25% total)
    tmux split-window -t "${SESSION_NAME}:${_DRILL_WIN}.3" -h -l 36%

    dashboard_send_armed_keys "${_DRILL_WIN}.0" "${_DRILL_HEADER_CMD}"
    dashboard_send_armed_keys "${_DRILL_WIN}.2" "${_DRILL_QUEUES_CMD}"
    dashboard_send_armed_keys "${_DRILL_WIN}.3" "${_DRILL_PROGRESS_CMD}"
    dashboard_send_armed_keys "${_DRILL_WIN}.4" "${_DRILL_CRON_CMD}"
    dashboard_send_armed_keys "${_DRILL_WIN}.1" "${_DRILL_LOGS_CMD}"
  fi

  # Rename the window to include the project name for operator clarity.
  # The window is created and wired using the plain drill-N name (avoiding colon
  # ambiguity in tmux target syntax), then renamed to "drill-N: <project>" once
  # all splits and send-keys are in place. Its panes reported armed under the
  # plain name, so that name is pinned first as the window's @pgai_arm_label:
  # activation-hook-sender.sh looks the armed markers up under it, since the
  # renamed window's own name never matches one.
  tmux set-option -w -t "${SESSION_NAME}:${_DRILL_WIN}" @pgai_arm_label "${_DRILL_WIN}"
  tmux rename-window -t "${SESSION_NAME}:${_DRILL_WIN}" "${_DRILL_WIN}: ${_DRILL_PROJ}"

  # Pin the label so tmux's automatic-rename does not overwrite it as the
  # long-lived 'watch' commands run in the panes.
  # allow-rename off also blocks application-driven OSC title escapes.
  tmux set-window-option -t "${SESSION_NAME}:${_DRILL_WIN}" automatic-rename off
  tmux set-window-option -t "${SESSION_NAME}:${_DRILL_WIN}" allow-rename off

  # Apply dashboard_color to the drill window's tab in the status bar.
  # window-status-style is referenced as #[window-status-style] in status-format[0]
  # for inactive windows, so this color appears in the tmux tab bar for this project.
  if [[ -n "$_drill_proj_color" ]]; then
    tmux set-window-option -t "${SESSION_NAME}:${_DRILL_WIN}" \
      window-status-style "fg=${_drill_proj_color}"
  fi

  _DRILL_IDX=$(( _DRILL_IDX + 1 ))
done

# ---------------------------------------------------------------------------
# Key binding: prefix + p — per-project drill/overview toggle (Feature 4)
#
# Cycles the current window through:
#   dashboard (overview)  →  drill-1  →  drill-2  →  ...  →  dashboard
#
# project-toggle.sh handles the logic; run-shell executes it in
# the background so tmux does not wait for it.
#
# Key bindings are server-wide, so the binding is conditional: in a session
# marked @pgai_dashboard (dashboard_apply_session_options) it toggles that
# session; in every other session it is tmux's own prefix+p,
# previous-window. kill.sh restores the plain default once no dashboard
# session remains.
# ---------------------------------------------------------------------------
dashboard_apply_window_status_style "$SESSION_NAME"
tmux bind-key -T prefix p if-shell -F '#{@pgai_dashboard}' \
  "run-shell \"${SCRIPT_DIR}/project-toggle.sh --session '#{session_name}'\"" previous-window

# ---------------------------------------------------------------------------
# All-armed readiness handshake — create.sh's return is gated on this.
#
# Every pane command dispatched above went through dashboard_send_armed_keys,
# so PANE_ARMED_LABELS names every pane this run is responsible for. Block
# here, polling lib/pane_readiness.sh's dashboard_wait_all_armed (a real
# wait, never a fixed sleep — a fixed sleep is precisely the race this
# ticket exists to remove) until every one of those panes has installed its
# trap and reported armed, or the bound below elapses with no further pane
# reporting armed. The bound is a stall bound — each newly armed pane
# restarts it — so a loaded host that arms panes slowly but steadily is
# never refused; only a dashboard whose arming has stopped is.
#
# PGAI_DASHBOARD_ARM_TIMEOUT_SECONDS overrides the default bound (seconds
# allowed to pass without any further pane reporting armed); a
# non-positive-integer value falls back to the default rather than
# disabling the bound (an unbounded wait would hang create.sh forever on a
# pane whose script never starts).
# ---------------------------------------------------------------------------
_ARM_TIMEOUT_DEFAULT=20
_arm_timeout_raw="${PGAI_DASHBOARD_ARM_TIMEOUT_SECONDS:-}"
if [[ "$_arm_timeout_raw" =~ ^[0-9]+$ ]] && [[ "$_arm_timeout_raw" -ge 1 ]]; then
  ARM_TIMEOUT_SECONDS="$_arm_timeout_raw"
else
  ARM_TIMEOUT_SECONDS="$_ARM_TIMEOUT_DEFAULT"
fi
unset _arm_timeout_raw _ARM_TIMEOUT_DEFAULT

_ARM_PENDING="$(dashboard_wait_all_armed "$PANE_ARMED_DIR" "$ARM_TIMEOUT_SECONDS" "${PANE_ARMED_LABELS[@]}")" \
  && _ARM_STATUS=0 || _ARM_STATUS=$?

if [[ "$_ARM_STATUS" -ne 0 ]]; then
  # BLOCK-WORTHY (docs/blocking-site-census.md): a pane that never reports
  # armed within the bound never got its signal trap installed, so it is
  # still exactly as vulnerable to the dies-on-first-signal failure the
  # trap preamble exists to prevent — attaching the operator to a session
  # with one or more panes in that state is the very race the readiness
  # handshake exists to prevent, and a future activation-hook signal to
  # that pane would kill it. The session is left running (not torn down)
  # so the operator can inspect the stalled pane's script directly; the
  # remedy below names both the diagnostic path and the retry path.
  _arm_pending_oneline="$(printf '%s, ' $_ARM_PENDING)"
  _arm_pending_oneline="${_arm_pending_oneline%, }"
  pgai_emit_error_contract \
    "dashboard_create.pane_arm_timeout" \
    "create.sh gave up after ${ARM_TIMEOUT_SECONDS}s with no further pane reporting its signal trap armed; still-unarmed pane(s): ${_arm_pending_oneline}." \
    "Inspect the stalled pane(s) directly: tmux attach -t ${SESSION_NAME} (or 'tmux capture-pane -t \"${SESSION_NAME}:<pane>\" -p') to see why that pane's script has not reached its first line yet. Once fixed, tear down with team/scripts/dashboard/kill.sh --session ${SESSION_NAME} and re-run create.sh. To raise the bound instead, re-run with PGAI_DASHBOARD_ARM_TIMEOUT_SECONDS=<n> set higher than ${ARM_TIMEOUT_SECONDS}."
  # CENSUS-ANCHOR: dashboard-create-pane-arm-timeout-01
  exit 1
fi
unset _ARM_PENDING _ARM_STATUS

# ---------------------------------------------------------------------------
# Session-scoped activation hook — installed only now, strictly after the
# all-armed handshake above has succeeded for every pane this run
# dispatched. Installing any earlier (e.g. during pane creation) would
# reopen the exact race the handshake exists to close: a hook firing
# against a pane whose signal trap has not landed yet kills it outright
# under the OS default disposition. Session-scoped (`-t "$SESSION_NAME"`,
# never `-g`) so the hook can never fire against an unrelated tmux session
# sharing this server. See lib/activation_hook.sh for the full design.
# ---------------------------------------------------------------------------
dashboard_install_activation_hook "$KANBAN_ROOT" "$SESSION_NAME" "$SCRIPT_DIR"

# Schedule window layout hooks — re-apply the list band's SCHED_LIST_H on
# every window resize, client resize, client attach and window switch, so
# a smaller terminal never squeezes the per-agent list below its rows.
# Installed after the activation hook: that install sets index 0 of the
# client-attached and session-window-changed hooks (clearing the array),
# and these occupy index 1 of the same two. See lib/schedule_layout.sh.
dashboard_install_schedule_layout_hook "$SESSION_NAME" "$SCHED_LIST_H"

# ---------------------------------------------------------------------------
# Return focus to main window and attach
# ---------------------------------------------------------------------------
tmux select-window -t "${SESSION_NAME}:main"
tmux select-pane -t "${SESSION_NAME}:main.0"

echo "Attaching to session '${SESSION_NAME}'..."
exec tmux attach-session -t "$SESSION_NAME"
