#!/usr/bin/env bash
# dashboard-create.sh
# Creates and attaches to the pgai-kanban tmux dashboard session.
#
# Layout (Window 0 — dashboard):
#   ┌─────────────────────────────────────────┬──────────────────────────────┐
#   │ LEFT column (~75% width)                │ RIGHT column (~25% width)    │
#   │ HEADER (top ~11% height)                │                              │
#   ├─────────────────────────────────────────┤ QUEUES / per-project status  │
#   │ LIVE LOGS (middle ~40% — logs.sh)       │ (full height)                │
#   ├─────────────────────────────────────────┤ watch show-multi queues      │
#   │ PROGRESS (~65%) │ CRON (~35%)           │ all projects' 5-agent        │
#   │ watch progress  │ watch next-cron-fire  │ breakdown + working task     │
#   └─────────────────┴───────────────────────┴──────────────────────────────┘
#
# QUEUES right pane is full-height so all registered projects fit without
# clipping. The left column is subdivided into HEADER (top),
# LOGS (middle ~40%), and PROGRESS left ~65% + CRON right ~35% (bottom).
#
# Auxiliary windows (in creation order):
#   W2: Visibility        — unified 3+5 visibility window (Dashboard 2.1)
#                           LEFT region (~40%): bugs / priority / requirements
#                           RIGHT region (~60%): pm / coder / writer / tester / cm
#                           Side-by-side horizontal split
#                           Each pane runs: watch -c -n <interval> dashboard-column-render.sh <args>
#                           Designed for 120-char-wide terminals
#   W3: Attention         — watch dashboard-attention.sh (BLOCKED tasks)
#   W4: Git               — two vertical panes:
#                           Left:  watch -n 10 dashboard-git-status.sh
#                                  (branch, sync status, uncommitted changes, rc/* branches,
#                                   recent commits on develop)
#                           Right: watch -n 10 dashboard-git-recent-tags.sh
#                                  (Recent Tags listing, >=10 newest tags, refreshed every 10s)
#   W5: Metadata          — single pane running watch -n 10 dashboard-metadata.sh
#                           shows kanban version, PM mode, HALT, and per-project rows
#                           (workflow type, active RC, last released, max_minor, max_major)
#   W6: Metrics           — two vertical panes:
#                           Left:  watch -t -c -n $REFRESH_INTERVAL dashboard-metrics.sh
#                                  shows today's per-project metrics (RCs shipped, wall time, tokens, tasks)
#                                  and current-RC progress (tasks done, elapsed, tokens so far)
#                           Right: watch -t -c -n $REFRESH_INTERVAL show-metrics.sh
#                                  shows historical RC metrics from history.csv (last 10 RCs)
#   W7: Logs full-screen  — dashboard-logs.sh
#   W8: debug-logs        — single-pane unified merged stream of all agents' debug logs
#                           ($KANBAN_ROOT/logs/debug/<agent>.log)
#                           color-coded per agent; gated by PGAI_VERBOSE_MODE=1
#                           missing log files tolerated (tail -F waits for them)
#   W9: training-logs     — single-pane unified view of all agents' newest training traces
#                           ($KANBAN_ROOT/projects/<project>/logs/training/<agent>/<ts>-<task-id>.md)
#                           color-coded per agent, sorted by mtime (newest at top)
#                           gated by PGAI_REASONING_TRACE=1; refreshed via watch -n 30
#   W10: Terminal         — three interactive shell panes
#   W11+: drill-N         — one window per registered project (all workflow types),
#                           numbered in projects.cfg registration order (drill-1, drill-2, ...).
#                           Release/feature projects: 5-pane release layout scoped to
#                           project N (header / queues / progress / cron / logs).
#                           Document-workflow projects: 2-pane document layout —
#                           top pane: show-document-drill.sh (artifact version library +
#                           document-pipeline progress); bottom pane: shared logs.
#                           Each drill-N window tab is colored with its project's
#                           dashboard_color.
#
# Window order: main, visibility, attention, git, metadata, metrics (metrics.sh + show-metrics.sh),
#               logs, debug-logs, training-logs, terminal, drill-1, drill-2, ...
#
# Per-project drill/overview toggle (Feature 4):
#   Key binding: prefix + p
#   Cycles through: dashboard (overview)  →  drill-1  →  drill-2  →  ...  →  dashboard
#   Implemented by dashboard-project-toggle.sh.
#   With 1 project: cycles dashboard → drill-1 → dashboard (drill == overview).
#   With 2+ projects: each drill-N shows only project N's panes.
#
# Usage:
#   dashboard-create.sh [--no-tmux] [--kanban-root <path>] [--session <name>]
#
# Flags:
#   --no-tmux         One-shot non-interactive output (print all panes to stdout)
#   --kanban-root     Override the kanban root path
#   --session         Override the tmux session name (default: pgai-kanban-dashboard)
#   -h, --help        Show this help and exit
#
# Environment variables:
#   PGAI_DASHBOARD_REFRESH_SECONDS  Refresh interval in seconds (default: 5, range: 1-3600)
#
# Requirements:
#   tmux >= 2.0

set -euo pipefail

# ---------------------------------------------------------------------------
# Resolve script directory
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source project_paths lib for pp_* helpers
# shellcheck source=lib/project_paths.sh
source "${SCRIPT_DIR}/../lib/project_paths.sh"

# Source projects lib for projects_cfg_* helpers (multi-project registry)
# shellcheck source=lib/projects.sh
source "${SCRIPT_DIR}/../lib/projects.sh"

# Source dev_tree helper (resolve_global_dev_tree / require_dev_tree)
# shellcheck source=lib/dev_tree.sh
source "${SCRIPT_DIR}/../lib/dev_tree.sh"

# Source shared dashboard constants (PGAI_WINDOW0_RIGHT_COL_PCT, etc.)
# shellcheck source=lib/dashboard_constants.sh
source "${SCRIPT_DIR}/../lib/dashboard_constants.sh"

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}"
SESSION_NAME="pgai-kanban-dashboard"
NO_TMUX=false

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-tmux)
      NO_TMUX=true
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
      grep '^#' "$0" | sed 's/^# \{0,1\}//' | head -30
      exit 0
      ;;
    *)
      echo "ERROR: Unknown argument: $1" >&2
      echo "Usage: $0 [--no-tmux] [--kanban-root <path>] [--session <name>]" >&2
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
export PGAI_AGENT_KANBAN_ROOT_PATH="${PGAI_AGENT_KANBAN_ROOT_PATH:-${KANBAN_ROOT}}"

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
  # --no-tmux mode: dashboard-cron-tail.sh is designed for persistent tmux panes
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

# Check tmux version >= 2.0
TMUX_VERSION="$(tmux -V 2>/dev/null | awk '{print $2}' | sed 's/[^0-9.]//g')"
TMUX_MAJOR="$(echo "$TMUX_VERSION" | cut -d. -f1)"
if [[ -z "$TMUX_MAJOR" ]] || [[ "$TMUX_MAJOR" -lt 2 ]]; then
  echo "ERROR: tmux 2.0+ is required (found: tmux ${TMUX_VERSION:-unknown})." >&2
  exit 1
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
HEADER_CMD="watch -t -c -n ${REFRESH_INTERVAL} -- ${SCRIPT_DIR}/show-multi.sh --mode header ${RENDERER_ARGS[*]}"
QUEUES_CMD="watch -t -c -n ${REFRESH_INTERVAL} -- ${SCRIPT_DIR}/show-multi.sh --mode queues ${RENDERER_ARGS[*]}"
PROGRESS_CMD="watch -t -c -n ${REFRESH_INTERVAL} -- ${SCRIPT_DIR}/show-multi.sh --mode progress ${RENDERER_ARGS[*]}"
# Next Cron Firings pane — bottom-right of Window 1 middle row (Dashboard 2.2)
CRON_FIRINGS_CMD="watch -t -c -n ${REFRESH_INTERVAL} -- ${SCRIPT_DIR}/next-cron-firings.sh --kanban-root ${KANBAN_ROOT}"
ATTENTION_CMD="watch -t -c -n ${REFRESH_INTERVAL} -- ${SCRIPT_DIR}/attention.sh --kanban-root ${KANBAN_ROOT}"
LOGS_PANE_CMD="${SCRIPT_DIR}/logs.sh --kanban-root ${KANBAN_ROOT}"
LOGS_WIN_CMD="${SCRIPT_DIR}/logs.sh --kanban-root ${KANBAN_ROOT}"

# Visibility window — unified 8-column mixed-project layout (Dashboard 2.1)
#
# All 8 columns render data from EVERY registered project simultaneously.
# The --all-projects flag is passed to dashboard-column-render.sh; the
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
# Dashboard re-reads projects.cfg on each iteration: dashboard-column-render.sh
# reads projects.cfg on every refresh cycle.  No cached project list is embedded
# in the command.
#
# IMPORTANT — why while-sleep loops, not watch:
#   procps-ng watch(1) with -c / --color only preserves basic 16-color ANSI
#   sequences (\033[3Xm, \033[9Xm).  It silently drops 24-bit truecolor sequences
#   (\033[38;2;R;G;Bm) that project_tag_color() emits for per-project ■ glyphs.
#   The while-sleep-clear pattern below is equivalent but passes all ANSI sequences
#   through to tmux unmodified, so per-project display_color values render correctly.
#   Cleanup: tmux pane exit kills the shell running the loop; no orphaned processes.
_vis_render_script="${SCRIPT_DIR}/column-render.sh"
_all_proj_flags="--all-projects --kanban-root ${KANBAN_ROOT}"

# Top row: discovery pipeline columns — input subcommand with placeholder paths.
# "none" placeholders satisfy positional arg requirements; --all-projects overrides.
VIS_BUGS_CMD="clear; while true; do tput cup 0 0; while IFS= read -r _line; do printf '%s' \"\$_line\"; tput el; printf '\\n'; done < <(${_vis_render_script} input none none ${DASHBOARD_ROWS_PER_COLUMN} 29 --label BUGS ${_all_proj_flags}); tput ed; sleep ${REFRESH_INTERVAL}; done"
VIS_PRIORITY_CMD="clear; while true; do tput cup 0 0; while IFS= read -r _line; do printf '%s' \"\$_line\"; tput el; printf '\\n'; done < <(${_vis_render_script} input none none ${DASHBOARD_ROWS_PER_COLUMN} 29 --label PRIORITIES ${_all_proj_flags}); tput ed; sleep ${REFRESH_INTERVAL}; done"
VIS_REQUIREMENTS_CMD="clear; while true; do tput cup 0 0; while IFS= read -r _line; do printf '%s' \"\$_line\"; tput el; printf '\\n'; done < <(${_vis_render_script} input none none ${DASHBOARD_ROWS_PER_COLUMN} 29 --label REQUIREMENTS ${_all_proj_flags}); tput ed; sleep ${REFRESH_INTERVAL}; done"

# Bottom row: agent queue columns — queue subcommand with placeholder path.
VIS_PM_CMD="clear; while true; do tput cup 0 0; while IFS= read -r _line; do printf '%s' \"\$_line\"; tput el; printf '\\n'; done < <(${_vis_render_script} queue none ${DASHBOARD_ROWS_PER_COLUMN} 26 --label PM ${_all_proj_flags}); tput ed; sleep ${REFRESH_INTERVAL}; done"
VIS_CODER_CMD="clear; while true; do tput cup 0 0; while IFS= read -r _line; do printf '%s' \"\$_line\"; tput el; printf '\\n'; done < <(${_vis_render_script} queue none ${DASHBOARD_ROWS_PER_COLUMN} 26 --label CODER ${_all_proj_flags}); tput ed; sleep ${REFRESH_INTERVAL}; done"
VIS_WRITER_CMD="clear; while true; do tput cup 0 0; while IFS= read -r _line; do printf '%s' \"\$_line\"; tput el; printf '\\n'; done < <(${_vis_render_script} queue none ${DASHBOARD_ROWS_PER_COLUMN} 26 --label WRITER ${_all_proj_flags}); tput ed; sleep ${REFRESH_INTERVAL}; done"
VIS_TESTER_CMD="clear; while true; do tput cup 0 0; while IFS= read -r _line; do printf '%s' \"\$_line\"; tput el; printf '\\n'; done < <(${_vis_render_script} queue none ${DASHBOARD_ROWS_PER_COLUMN} 26 --label TESTER ${_all_proj_flags}); tput ed; sleep ${REFRESH_INTERVAL}; done"
VIS_CM_CMD="clear; while true; do tput cup 0 0; while IFS= read -r _line; do printf '%s' \"\$_line\"; tput el; printf '\\n'; done < <(${_vis_render_script} queue none ${DASHBOARD_ROWS_PER_COLUMN} 26 --label CM ${_all_proj_flags}); tput ed; sleep ${REFRESH_INTERVAL}; done"

# Git window — two vertical panes.
# Left pane: watch -n 10 against dashboard-git-status.sh (branch/working-tree/remotes).
# Right pane: watch -n 10 against dashboard-git-recent-tags.sh (recent tags >=10, newest first).
# Both scripts are multi-project-aware and resolve each project's dev_tree_path from
# per-project config (project.cfg); neither depends on the global PGAI_DEV_TREE_PATH.
GIT_STATUS_CMD="watch -t -c -n 10 -- ${SCRIPT_DIR}/git-status.sh --kanban-root ${KANBAN_ROOT}"
GIT_RECENT_TAGS_CMD="watch -t -c -n 10 -- ${SCRIPT_DIR}/git-recent-tags.sh --kanban-root ${KANBAN_ROOT}"

# Metadata window — single pane running watch -n 10 against dashboard-metadata.sh.
# Shows kanban-wide state (version, PM mode, HALT) and per-project rows
# (workflow type, active RC, last released, max_minor, max_major).
METADATA_CMD="watch -t -c -n 10 -- ${SCRIPT_DIR}/metadata.sh --kanban-root ${KANBAN_ROOT}"

# Metrics window — left pane running watch against dashboard-metrics.sh.
# Shows today's per-project metrics (RCs shipped, wall time, tokens with cache
# hit %, task count) and current-RC progress (tasks done, elapsed, tokens so
# far).  Reads from metrics/day/<date>.json and metrics/rc/<v>.json.
# Uses the same refresh interval as other dashboard panes.
METRICS_CMD="watch -t -c -n ${REFRESH_INTERVAL} -- ${SCRIPT_DIR}/metrics.sh --kanban-root ${KANBAN_ROOT}"

# Metrics window — right pane running watch against show-metrics.sh.
# Shows historical RC metrics from history.csv (last 10 RCs): rc, wall time,
# input/output/cache tokens, cache hit rate, task count.
# Uses the same refresh interval as other dashboard panes.
SHOW_METRICS_CMD="watch -t -c -n ${REFRESH_INTERVAL} -- ${SCRIPT_DIR}/show-metrics.sh --kanban-root ${KANBAN_ROOT}"

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
#   Step 2: split pane 0 -h -p $QUEUES_PCT — pane 0=left ~65%, pane 1=right ~35% (QUEUES, full height)
#   Step 3: split pane 0 -v -p 40  — pane 0=left-top ~60%, pane 2=left-bottom 40% (LOGS)
#   Step 4: split pane 0 -v -p 82  — pane 0=HEADER (~11% of left), pane 3=middle left (~49%)
#   Step 5: split pane 3 -h -p 36  — pane 3=PROGRESS (~64% of mid-left), pane 4=CRON (~36%)
#   Step 6: send commands to all panes
#
# Target proportions at 120-char terminal:
#   pane 1 (QUEUES/per-project): ~48 chars wide, full height
#   Left column (~72 chars):
#     pane 0 (HEADER):    ~72 chars wide, ~5-6 rows tall
#     pane 3 (PROGRESS):  ~46 chars wide, middle height
#     pane 4 (CRON):      ~26 chars wide, middle height
#     pane 2 (LOGS):      ~72 chars wide, bottom 40% height
#
# Final pane indices after all splits:
#   0 = HEADER (top-left, ~11% height)
#   1 = QUEUES / per-project status (right column, ~35% width, full height)
#   2 = LOGS (bottom-left, ~40% height)
#   3 = PROGRESS (middle-left, ~65% width of left column)
#   4 = CRON FIRINGS (middle-right within left column, ~35% width of left column)
#
# Drill-N windows (W11+): left as-is with the original 5-pane layout (HEADER /
# QUEUES-middle-left / PROGRESS-middle-center / CRON-middle-right / LOGS-bottom).
# Drill windows scope to ONE project, so the multi-project overflow problem that
# motivated this change does not apply there. Drill-N geometry consistency with
# window 0 is nice-to-have and deferred to a future ticket if desired.
# ---------------------------------------------------------------------------
tmux new-session -d -s "$SESSION_NAME" -n "main" -x "220" -y "50"

# ---------------------------------------------------------------------------
# Truecolor (24-bit) support — required for project tag color rendering
#
# dashboard-column-render.sh and dashboard-legend-render.sh emit ANSI
# 24-bit truecolor sequences (\033[38;2;R;G;Bm) to render each project's
# configured display_color from projects.cfg.  Without these two options
# tmux strips those sequences (Tc capability is absent from its default
# terminal config), causing all project tag squares ■ to render in the
# default terminal color instead of their configured colors.
#
# Fix:
#   1. Set default-terminal to "tmux-256color" — this is the tmux-native
#      terminal type and carries the Tc flag internally without needing
#      a terminal-overrides entry.
#   2. Add terminal-overrides to propagate Tc and the 24-bit RGB capability
#      to the outer xterm-256color client terminal, covering both tmux-internal
#      and client-side rendering paths.
#
# Basic 8-color ANSI (status colors: green/amber/red) is unaffected — they
# do not use the Tc path and continue to work regardless of this setting.
# ---------------------------------------------------------------------------
tmux set-option -t "$SESSION_NAME" -g default-terminal "tmux-256color"
tmux set-option -t "$SESSION_NAME" -ga terminal-overrides ",xterm-256color:Tc"
tmux set-option -t "$SESSION_NAME" -ga terminal-overrides ",xterm-256color:RGB"

# ---------------------------------------------------------------------------
# Status bar configuration — three-line layout
#
# Line 0 (top):    session name on left + FRAMEWORK windows only (index 0-9,
#                  names NOT starting with drill-).  Drills excluded so the
#                  framework window list never truncates.
# Line 1 (middle): DRILL windows only (index 10+, names starting with drill-).
#                  Each drill tab uses its project's dashboard_color via the
#                  per-window tab-color option set at creation time (see below).
#                  Renders blank (no error) when no drill windows exist.
# Line 2 (bottom): project/version/workflow/RC/PM/HALT/time via
#                  dashboard-status-bottom.sh (same #() pattern as
#                  the former status-right invocation).
# ---------------------------------------------------------------------------
tmux set-option -t "$SESSION_NAME" -g status 3
tmux set-option -t "$SESSION_NAME" -g status-style "bg=#2a2a2a,fg=white"
tmux set-option -t "$SESSION_NAME" -g status-interval "${REFRESH_INTERVAL}"

# Line 0: session name on the left, FRAMEWORK window list fills the rest.
# #{W:...} iterates all windows; the inner #{?#{m:drill-*,#{window_name}},,OUTPUT}
# conditional emits output only for windows whose name does NOT start with drill-,
# so drill-N windows are silently skipped.  Active window is highlighted
# bold/white; inactive windows fall through to the global window-status-style
# (fg=colour250 grey) — drill-N windows are excluded here so their per-window
# window-status-style does not appear on this line.
# Per-project color for drill-N tabs is handled on line 1.
tmux set-option -t "$SESSION_NAME" -g window-status-style "fg=colour250"
tmux set-option -t "$SESSION_NAME" -g "status-format[0]" \
  "#[bg=#2a2a2a,fg=cyan,bold][#S]#[default,bg=#2a2a2a] #{W:#{?#{m:drill-*,#{window_name}},,#{?window_active,#[fg=white bold],#[window-status-style]}#{window_index}:#{window_name}#{?window_active,*,} }}"

# Line 1: DRILL windows only.
# #{W:...} iterates all windows; the inner #{?#{m:drill-*,#{window_name}},OUTPUT,}
# conditional emits output only for windows whose name starts with drill-.
# Per-window tab color (set-window-option window-status-style, done below for
# each drill-N window) applies the project's dashboard_color to inactive drill
# tabs; the format token #[window-status-style] references it.
# When no drill windows exist all conditional branches produce empty strings and
# the line renders blank — no error.
tmux set-option -t "$SESSION_NAME" -g "status-format[1]" \
  "#[bg=#2a2a2a,fg=colour250]#{W:#{?#{m:drill-*,#{window_name}},#{?window_active,#[fg=white bold],#[window-status-style]}#{window_index}:#{window_name}#{?window_active,*,} ,}}"

# Line 2: invoke dashboard-status-bottom.sh via the same #() shell-
# invocation pattern tmux uses for status-right.  The script accepts
# KANBAN_ROOT as $1 and emits a single formatted line.
tmux set-option -t "$SESSION_NAME" -g "status-format[2]" \
  "#[bg=#1a1a1a,fg=colour250]#(${SCRIPT_DIR}/status-bottom.sh ${KANBAN_ROOT})#[default]"

# Step 2: split full window horizontally — right ~25% (full height, eventually QUEUES).
# This creates the full-height right-column pane.
# At this point: pane 0 = left ~75%, pane 1 = right ~25%.
# NOTE: after subsequent left-column vertical splits (steps 3-4), tmux renumbers panes
# in top-to-bottom, left-to-right order — the right-column pane ends up as pane 4, NOT pane 1.
# QUEUES_PCT is sourced from lib/dashboard_constants.sh (value 25).
tmux split-window -t "${SESSION_NAME}:main.0" -h -p "${QUEUES_PCT}"

# Step 3: split the left area (pane 0) vertically — left-bottom 40% = LOGS.
# After split: left-top (pane 0), left-bottom (pane 1), right (pane 2 after renumber).
tmux split-window -t "${SESSION_NAME}:main.0" -v -p 40

# Step 4: split the top-left area (pane 0) vertically — HEADER at top (~11%), middle below.
# After split: pane 0=HEADER(top-left), pane 1=middle-left, pane 2=LOGS(bottom-left), pane 3=RIGHT.
tmux split-window -t "${SESSION_NAME}:main.0" -v -p 82

# Step 5: split the bottom-left pane (pane 2) horizontally — PROGRESS left, CRON right.
# After steps 2-4, tmux renumbers panes in top-to-bottom, left-to-right order:
#   pane 0 = HEADER (top-left), pane 1 = middle-left (full-width LOGS area),
#   pane 2 = bottom-left (PROGRESS+CRON area), pane 3 = full-height RIGHT column (QUEUES).
# Targeting pane 2 (bottom-left) produces the correct 5-pane layout:
#   pane 0=HEADER, pane 1=LOGS(middle,full-width), pane 2=PROGRESS(bottom-left),
#   pane 3=CRON(bottom-right), pane 4=QUEUES(right,full-height)
# CRON (new pane 3) gets 36% of the left-column bottom width
# PROGRESS (pane 2) keeps 64% of the left-column bottom width
tmux split-window -t "${SESSION_NAME}:main.2" -h -p 36

# ---------------------------------------------------------------------------
# Post-creation explicit resize for window 0 — absolute widths and heights.
# Mirrors the window-2 approach: use explicit post-creation resize rather than
# chained percentage splits.
#
# WHY NEEDED: chained "split-window -p N" applies each percentage against the
# shrinking remainder of the prior split, not the original window size.  Under
# tmux 3.2a, integer rounding compounds at each step and produces scrambled
# proportions.
#
# SIZING APPROACH:
#   1. All 5 panes are already created above with rough percentage splits
#      (exact creation-time sizes do not matter).
#   2. Capture the true window width and height after all splits.
#   3. Force-resize every pane to an absolute character width/height:
#
#   WIDTH (25/75 SPLIT — constant 25%; single-sourced via lib/dashboard_constants.sh):
#     QUEUES_BUDGET = floor(MAIN_WIDTH * QUEUES_PCT / 100)  — right column budget
#     LEFT_W        = MAIN_WIDTH - QUEUES_BUDGET - 1        — left column (-1 divider)
#     Within left column, PROGRESS and CRON split horizontally (64%/36%):
#       MID_AVAIL   = LEFT_W - 1                       — minus PROGRESS/CRON border
#       PROG_W      = floor(MID_AVAIL * 64 / 100)
#       CRON_W      = MID_AVAIL - PROG_W
#
#   HEIGHT (three left-column rows: HEADER / LOGS / PROGRESS+CRON):
#     LEFT_CONTENT_H = MAIN_HEIGHT - 2   — 2 borders for 3 stacked rows
#     HEADER_H       = floor(MAIN_HEIGHT * 11 / 100)   — ~11% for header row
#     LOGS_H         = floor(LEFT_CONTENT_H * 40 / 100) — ~40% for LOGS row (middle)
#     MID_H          = LEFT_CONTENT_H - HEADER_H - LOGS_H — remainder for PROGRESS+CRON row (bottom)
#     QUEUES_H       = MAIN_HEIGHT - 1   — full height minus top border
#
#   Minimums enforced: LEFT_W >= 30, QUEUES_BUDGET >= 20,
#     HEADER_H >= 5, MID_H >= 5, LOGS_H >= 5, QUEUES_H >= 15,
#     PROG_W >= 15, CRON_W >= 10.
#
#   All 5 panes receive an explicit -x and -y resize — no pane absorbs a
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
# Right column (QUEUES, pane 4):
#   QUEUES_BUDGET = floor(MAIN_WIDTH * QUEUES_PCT / 100)
#
# Left column (panes 0/1/2/3):
#   LEFT_W = MAIN_WIDTH - QUEUES_BUDGET - 1  (subtract 1 for left/right divider)
#
# Within left column, PROGRESS (pane 2) and CRON (pane 3) share the bottom
# row with a horizontal split.  The divider costs 1 char:
#   MID_AVAIL = LEFT_W - 1
#   PROG_W    = floor(MID_AVAIL * 64 / 100)   (~64% for PROGRESS)
#   CRON_W    = MID_AVAIL - PROG_W             (~36% for CRON)
QUEUES_BUDGET=$(( MAIN_WIDTH * QUEUES_PCT / 100 ))
[[ "$QUEUES_BUDGET" -lt 20 ]] && QUEUES_BUDGET=20
LEFT_W=$(( MAIN_WIDTH - QUEUES_BUDGET - 1 ))
[[ "$LEFT_W" -lt 30 ]] && LEFT_W=30

MID_AVAIL=$(( LEFT_W - 1 ))
PROG_W=$(( MID_AVAIL * 64 / 100 ))
CRON_W=$(( MID_AVAIL - PROG_W ))
[[ "$PROG_W" -lt 15 ]] && PROG_W=15
[[ "$CRON_W" -lt 10 ]] && CRON_W=10

# Compute row heights for the left column.
# Row order: HEADER (top) / LOGS (middle) / PROGRESS+CRON (bottom).
#
# Three stacked rows (HEADER / LOGS / PROGRESS+CRON) share the full window height with
# 2 inter-row borders:
#   LEFT_CONTENT_H = MAIN_HEIGHT - 2
#   HEADER_H       = floor(MAIN_HEIGHT * 11 / 100)   (~11% — small header row, top)
#   LOGS_H         = floor(LEFT_CONTENT_H * 40 / 100) (~40% — LOGS row, middle)
#   MID_H          = LEFT_CONTENT_H - HEADER_H - LOGS_H  (remainder — PROGRESS+CRON row, bottom)
#
# QUEUES (pane 4, right column) is full height; it uses MAIN_HEIGHT - 1 (top border).
LEFT_CONTENT_H=$(( MAIN_HEIGHT - 2 ))
[[ "$LEFT_CONTENT_H" -lt 15 ]] && LEFT_CONTENT_H=15

HEADER_H=$(( MAIN_HEIGHT * 11 / 100 ))
[[ "$HEADER_H" -lt 5 ]] && HEADER_H=5

LOGS_H=$(( LEFT_CONTENT_H * 40 / 100 ))
[[ "$LOGS_H" -lt 5 ]] && LOGS_H=5

MID_H=$(( LEFT_CONTENT_H - HEADER_H - LOGS_H ))
[[ "$MID_H" -lt 5 ]] && MID_H=5

QUEUES_H=$(( MAIN_HEIGHT - 1 ))
[[ "$QUEUES_H" -lt 15 ]] && QUEUES_H=15

# Resize all 5 window-0 panes to their absolute target dimensions.
# Pane index → role mapping (tmux renumbers in top-to-bottom, left-to-right
# order after all splits; verified by tmux list-panes inspection):
#   pane 0 = HEADER        — left column, top row
#   pane 1 = LOGS          — left column, middle row (full-width)
#   pane 2 = PROGRESS      — left column, bottom row left (~64%)
#   pane 3 = CRON          — left column, bottom row right (~36%)
#   pane 4 = QUEUES        — right column, full height  ← physically rightmost (largest pane_left)
tmux resize-pane -t "${SESSION_NAME}:main.0" -x "${LEFT_W}"        -y "${HEADER_H}"
tmux resize-pane -t "${SESSION_NAME}:main.1" -x "${LEFT_W}"        -y "${LOGS_H}"
tmux resize-pane -t "${SESSION_NAME}:main.2" -x "${PROG_W}"        -y "${MID_H}"
tmux resize-pane -t "${SESSION_NAME}:main.3" -x "${CRON_W}"        -y "${MID_H}"
tmux resize-pane -t "${SESSION_NAME}:main.4" -x "${QUEUES_BUDGET}" -y "${QUEUES_H}"

# Step 6: send commands to all panes
# Pane mapping (physical positions verified by tmux list-panes pane_left inspection):
#   0=HEADER(top-left), 1=LOGS(middle-left,full-width), 2=PROGRESS(bottom-left),
#   3=CRON(bottom-right-within-left-col), 4=QUEUES(right,full-height — physically rightmost ~35%)
tmux send-keys -t "${SESSION_NAME}:main.0" "${HEADER_CMD}" Enter
tmux send-keys -t "${SESSION_NAME}:main.1" "${LOGS_PANE_CMD}" Enter
tmux send-keys -t "${SESSION_NAME}:main.2" "${PROGRESS_CMD}" Enter
tmux send-keys -t "${SESSION_NAME}:main.3" "${CRON_FIRINGS_CMD}" Enter
tmux send-keys -t "${SESSION_NAME}:main.4" "${QUEUES_CMD}" Enter

# ---------------------------------------------------------------------------
# Window 2: Visibility — unified 3+5 pane layout + legend (Dashboard 2.1)
#
# Three discovery-pipeline columns on the LEFT (~40% width) and five
# agent-queue columns on the RIGHT (~60% width), arranged side-by-side
# (horizontal split), with a one-line persistent legend at the bottom.
# Designed for 120-char-wide terminals; degrades gracefully at narrower widths
# (tmux clips individual panes rather than scrolling horizontally).
#
# SIZING APPROACH — explicit absolute widths, not chained percentages
# -----------------------------------------------------------------------
# Chained "split-window -h -p N" applies each percentage against the
# shrinking remainder of the previous split, not against the original window
# width.  Under tmux 3.2a, integer rounding compounds at each step and
# produces catastrophically unequal panes.
#
# Instead we:
#   1. Create all 9 panes using simple percentage splits (exact sizes do not
#      matter at creation time; post-creation resize-pane corrects them).
#   2. Capture the true window width and height AFTER creation.
#   3. Force-resize every pane to an absolute character width/height using
#      border-aware accounting:
#        LEFT_AVAIL  = floor(VIS_WIDTH * 40 / 100) - 2  (40% budget minus 2 borders)
#        TOP_COL     = LEFT_AVAIL / 3   (3 equal input columns)
#        RIGHT_AVAIL = VIS_WIDTH - floor(VIS_WIDTH * 40 / 100) - 4 - 1
#                      (60% budget minus 4 borders minus 1 left/right divider)
#        BOT_COL     = RIGHT_AVAIL / 5  (5 equal queue columns)
#        COL_H       = VIS_HEIGHT - LEGEND_H - 1  (full content height; -1 for border)
#        LEGEND_H    = 1  (single-line legend)
#      Remainder from integer division goes to a non-edge pane (pane 1 in
#      the left region, pane 5 in the right region) so edge panes are not
#      oversized.  ALL panes receive an explicit -x resize — no pane absorbs
#      a tmux remainder implicitly.  All panes receive an explicit -x resize
#      to prevent edge panes from absorbing integer-division remainders.
#
# Split sequence for 9 panes (horizontal LEFT/RIGHT first):
#   Step 1: window created → pane 0 (full)
#   Step 2: split pane 0 -v -l 1 → pane 0=content (top), pane 1=legend (1 row)
#   Step 3: split pane 0 -h -p 60 → pane 0=left-40%, pane 1=right-60%,
#                                    legend renumbered → pane 2
#   Step 4: split pane 0 -h -p 67 → pane 0=bugs, pane 1=prio+reqs,
#                                    pane 2=right-60%, pane 3=legend
#   Step 5: split pane 1 -h -p 50 → pane 1=priority, pane 2=requirements,
#                                    pane 3=right-60%, pane 4=legend
#   Step 6: split pane 3 -h -p 80 → pane 3=PM, pane 4=CODER+WRITER+TESTER+CM,
#                                    pane 5=legend
#   Step 7: split pane 4 -h -p 75 → pane 4=CODER, pane 5=WRITER+TESTER+CM,
#                                    pane 6=legend
#   Step 8: split pane 5 -h -p 67 → pane 5=WRITER, pane 6=TESTER+CM,
#                                    pane 7=legend
#   Step 9: split pane 6 -h -p 50 → pane 6=TESTER, pane 7=CM, pane 8=legend
#
# After all splits, resize-pane -x/-y fixes every pane to the correct
# absolute width and height.  Minimum guaranteed: 15 chars wide, 5 rows tall.
#
# Final layout (pane indices):
#   ┌──────────────────────────────┬──────────────────────────────────────────┐
#   │ LEFT ~40% (Inputs)           │ RIGHT ~60% (Agent Queues)                │
#   │ bugs (0) │ prio (1) │req (2) │ pm (3) │coder(4)│writ(5)│test(6)│cm (7) │
#   │ ~WIDTH/3  ~WIDTH/3  ~WIDTH/3 │  ~W/5    ~W/5    ~W/5    ~W/5    ~W/5   │
#   ├──────────────────────────────┴──────────────────────────────────────────┤
#   │ LEGEND (pane 8) — PROJECT: ■ name ... | STATUS: open  running ...      │
#   └──────────────────────────────────────────────────────────────────────────┘
# ---------------------------------------------------------------------------
tmux new-window -t "${SESSION_NAME}" -n "visibility"

# Step 2: carve a 1-row legend pane at the bottom of the full window.
# pane 0=content (top, large), pane 1=legend (bottom, 1 row).
tmux split-window -t "${SESSION_NAME}:visibility.0" -v -l 1

# Step 3: split content area (pane 0) into LEFT (~40%) and RIGHT (~60%) regions.
# New right pane becomes pane 1; legend shifts to pane 2.
tmux split-window -t "${SESSION_NAME}:visibility.0" -h -p 60

# Left region — 3 input columns (bugs / priority / requirements)
# Step 4: split left region (pane 0) into bugs (left) and prio+reqs (right ~67%).
# New right-sub becomes pane 1; right-60% shifts to pane 2; legend to pane 3.
tmux split-window -t "${SESSION_NAME}:visibility.0" -h -p 67
# Step 5: split prio+reqs (pane 1) into priority (left) and requirements (right 50%).
# New right-sub becomes pane 2; right-60% shifts to pane 3; legend to pane 4.
tmux split-window -t "${SESSION_NAME}:visibility.1" -h -p 50

# Right region — 5 agent-queue columns (PM / CODER / WRITER / TESTER / CM)
# After steps 4-5, the full-height right-60% area is pane 3.  Split it 4 times.
# The legend pane shifts right with each split and ends up at pane 8.
# Step 6: PM (left ~20% of right) vs. remaining 4 queue cols (right ~80%).
tmux split-window -t "${SESSION_NAME}:visibility.3" -h -p 80
# Step 7: CODER (left ~25% of remaining) vs. WRITER+TESTER+CM (right ~75%).
tmux split-window -t "${SESSION_NAME}:visibility.4" -h -p 75
# Step 8: WRITER (left ~33% of remaining) vs. TESTER+CM (right ~67%).
tmux split-window -t "${SESSION_NAME}:visibility.5" -h -p 67
# Step 9: TESTER (left 50%) vs. CM (right 50%).
tmux split-window -t "${SESSION_NAME}:visibility.6" -h -p 50

# ---------------------------------------------------------------------------
# Post-creation explicit resize — compute absolute sizes from window dimensions.
# Every column is sized against the full window width, not a shrinking remainder.
# Minimum enforced: 15 chars wide per pane, 5 rows tall per content column.
#
# 40/60 WIDTH SPLIT:
#   LEFT region  (panes 0-2, 3 input columns):  ~40% of total window width
#   RIGHT region (panes 3-7, 5 queue columns):  ~60% of total window width
#   Border accounting:
#     Input col:  TOP_BUDGET = floor(VIS_WIDTH * 40 / 100)
#                 TOP_AVAIL  = TOP_BUDGET - 2  (2 inter-pane borders in left region)
#                 TOP_COL    = TOP_AVAIL / 3
#     Queue col:  BOT_BUDGET = VIS_WIDTH - TOP_BUDGET - 1
#                              (subtract 1 for the left/right region divider)
#                 BOT_AVAIL  = BOT_BUDGET - 4  (4 inter-pane borders in right region)
#                 BOT_COL    = BOT_AVAIL / 5
#
# HEIGHT (single content row spanning full height):
#   CONTENT_H = VIS_HEIGHT - LEGEND_H - 1  (-1 for border between content and legend)
#   Legend row = 1 line (constant)
# ---------------------------------------------------------------------------
VIS_WIDTH=$(tmux display-message -t "${SESSION_NAME}:visibility" -p '#{window_width}')
VIS_HEIGHT=$(tmux display-message -t "${SESSION_NAME}:visibility" -p '#{window_height}')

# Guard: fall back to safe minima if tmux query returns empty or zero
VIS_WIDTH=${VIS_WIDTH:-120}
VIS_HEIGHT=${VIS_HEIGHT:-24}
[[ "$VIS_WIDTH"  -lt 45  ]] && VIS_WIDTH=45    # 45 = 3 * 15 minimum
[[ "$VIS_HEIGHT" -lt 16  ]] && VIS_HEIGHT=16   # 16 = 13 content rows + 1 label + 1 legend + 1 border

# Compute column widths with 40/60 split and explicit border accounting.
#
# Left region (3 panes, 2 inter-pane borders):
#   40% budget     = floor(VIS_WIDTH * 40 / 100)
#   Available      = budget - 2  (borders)
#   TOP_COL        = available / 3  (integer division)
#   TOP_REM        = available mod 3  (extra chars given to non-edge pane 1)
#
# Right region (5 panes, 4 inter-pane borders, 1 left/right divider):
#   60% budget     = VIS_WIDTH - 40% budget - 1  (subtract divider between regions)
#   Available      = budget - 4  (borders within right region)
#   BOT_COL        = available / 5  (integer division)
#   BOT_REM        = available mod 5  (extra chars given to non-edge pane 5)
#
# All panes are sized explicitly — no pane absorbs a tmux remainder implicitly.
TOP_BUDGET=$(( VIS_WIDTH * 40 / 100 ))
TOP_AVAIL=$(( TOP_BUDGET - 2 ))
TOP_COL=$(( TOP_AVAIL / 3 ))
TOP_REM=$(( TOP_AVAIL % 3 ))

# Re-weight the left region: bugs/priority narrower, requirements wider.
# Must sum to TOP_AVAIL (= 40% budget - 2). TOP_REM still absorbed by priority.
REQ_COL=$(( TOP_AVAIL * 35 / 100 ))                 # requirements ~35% of left region
BUGS_COL=$(( TOP_AVAIL * 30 / 100 ))                # bugs ~30%
PRIO_COL=$(( TOP_AVAIL - REQ_COL - BUGS_COL ))      # priority gets the exact remainder (~35%)

BOT_BUDGET=$(( VIS_WIDTH - TOP_BUDGET - 1 ))
BOT_AVAIL=$(( BOT_BUDGET - 4 ))
BOT_COL=$(( BOT_AVAIL / 5 ))
BOT_REM=$(( BOT_AVAIL % 5 ))

LEGEND_H=1
# Content height: all columns span the full height minus legend and border.
# Minimum 14: 13 data rows + 1 header label line per column.
CONTENT_H=$(( VIS_HEIGHT - LEGEND_H - 1 ))
[[ "$CONTENT_H" -lt 14 ]] && CONTENT_H=14

# Enforce per-column width minimums
[[ "$TOP_COL" -lt 15 ]] && TOP_COL=15
[[ "$BOT_COL" -lt 15 ]] && BOT_COL=15

# Remainder allocation: non-edge pane 1 (priority) gets TOP_REM extra chars;
# non-edge pane 5 (writer) gets BOT_REM extra chars.
TOP_MID_COL=$(( TOP_COL + TOP_REM ))
BOT_MID_COL=$(( BOT_COL + BOT_REM ))

# Resize left region (input columns) — all 3 panes explicitly sized.
#   pane 0 = bugs         = TOP_COL
#   pane 1 = priority     = TOP_COL + TOP_REM  (non-edge; absorbs remainder)
#   pane 2 = requirements = TOP_COL            (explicit)
tmux resize-pane -t "${SESSION_NAME}:visibility.0" -x "${BUGS_COL}"    -y "${CONTENT_H}"
tmux resize-pane -t "${SESSION_NAME}:visibility.1" -x "${PRIO_COL}"    -y "${CONTENT_H}"
tmux resize-pane -t "${SESSION_NAME}:visibility.2" -x "${REQ_COL}"     -y "${CONTENT_H}"

# Resize right region (queue columns) — all 5 panes explicitly sized.
#   pane 3 = pm     = BOT_COL
#   pane 4 = coder  = BOT_COL
#   pane 5 = writer = BOT_COL + BOT_REM  (non-edge; absorbs remainder)
#   pane 6 = tester = BOT_COL
#   pane 7 = cm     = BOT_COL            (explicit)
tmux resize-pane -t "${SESSION_NAME}:visibility.3" -x "${BOT_COL}"     -y "${CONTENT_H}"
tmux resize-pane -t "${SESSION_NAME}:visibility.4" -x "${BOT_COL}"     -y "${CONTENT_H}"
tmux resize-pane -t "${SESSION_NAME}:visibility.5" -x "${BOT_MID_COL}" -y "${CONTENT_H}"
tmux resize-pane -t "${SESSION_NAME}:visibility.6" -x "${BOT_COL}"     -y "${CONTENT_H}"
tmux resize-pane -t "${SESSION_NAME}:visibility.7" -x "${BOT_COL}"     -y "${CONTENT_H}"

# Resize legend pane (pane 8) — full width, 1 row.
tmux resize-pane -t "${SESSION_NAME}:visibility.8" -x "${VIS_WIDTH}"   -y "${LEGEND_H}"

# Send commands to each pane:
#   0=left(bugs)      1=left(priority)  2=left(requirements)
#   3=right(pm)       4=right(coder)    5=right(writer)
#   6=right(tester)   7=right(cm)
#   8=legend (full-width, 1 row)
# Left region: discovery pipeline input columns
tmux send-keys -t "${SESSION_NAME}:visibility.0" "${VIS_BUGS_CMD}" Enter
tmux send-keys -t "${SESSION_NAME}:visibility.1" "${VIS_PRIORITY_CMD}" Enter
tmux send-keys -t "${SESSION_NAME}:visibility.2" "${VIS_REQUIREMENTS_CMD}" Enter
# Right region: agent queue columns
tmux send-keys -t "${SESSION_NAME}:visibility.3" "${VIS_PM_CMD}" Enter
tmux send-keys -t "${SESSION_NAME}:visibility.4" "${VIS_CODER_CMD}" Enter
tmux send-keys -t "${SESSION_NAME}:visibility.5" "${VIS_WRITER_CMD}" Enter
tmux send-keys -t "${SESSION_NAME}:visibility.6" "${VIS_TESTER_CMD}" Enter
tmux send-keys -t "${SESSION_NAME}:visibility.7" "${VIS_CM_CMD}" Enter
# Legend pane: persistent color legend beneath the grid.
# Uses while-sleep-clear loop (not watch) to preserve 24-bit truecolor sequences
# emitted by dashboard-legend-render.sh for per-project color swatches.
VIS_LEGEND_CMD="clear; while true; do tput cup 0 0; while IFS= read -r _line; do printf '%s' \"\$_line\"; tput el; printf '\\n'; done < <(${SCRIPT_DIR}/legend-render.sh --kanban-root ${KANBAN_ROOT}); tput ed; sleep ${REFRESH_INTERVAL}; done"
tmux send-keys -t "${SESSION_NAME}:visibility.8" "${VIS_LEGEND_CMD}" Enter

# ---------------------------------------------------------------------------
# Window 3: Attention — blocked tasks panel (dashboard-attention.sh)
# ---------------------------------------------------------------------------
tmux new-window -t "${SESSION_NAME}" -n "attention"
tmux send-keys -t "${SESSION_NAME}:attention" "${ATTENTION_CMD}" Enter

# ---------------------------------------------------------------------------
# Window 4: Git — two vertical panes
#
# Left pane:  watch -n 10 dashboard-git-status.sh
#   Shows live git state of the dev tree:
#     - Current branch + sync-with-origin status
#     - Uncommitted changes summary
#     - In-flight rc/* branches
#     - Recent commits on develop (top 5)
#
# Right pane: watch -n 10 dashboard-git-recent-tags.sh
#   Shows Recent Tags listing (>=10 newest tags, newest first).
#   Refreshed on the same 10-second cadence as the left pane.
#
# Both panes are multi-project-aware and resolve each project's dev_tree_path
# from per-project config (project.cfg).  Neither depends on PGAI_DEV_TREE_PATH.
#
# Split sequence:
#   Step 1: window created  → pane 0 (full width)
#   Step 2: split pane 0 -h -p 35 → pane 0=left (~65%), pane 1=right (~35%)
#   The right pane is sized to show 10 tags comfortably; the left pane keeps
#   the wider view for the full git-status output.
# ---------------------------------------------------------------------------
tmux new-window -t "${SESSION_NAME}" -n "git"
tmux split-window -t "${SESSION_NAME}:git.0" -h -p 35
tmux send-keys -t "${SESSION_NAME}:git.0" "${GIT_STATUS_CMD}" Enter
tmux send-keys -t "${SESSION_NAME}:git.1" "${GIT_RECENT_TAGS_CMD}" Enter

# ---------------------------------------------------------------------------
# Window 5: Metadata — kanban-wide state + per-project pacing (single pane)
#
# Shows kanban version, PM_MODE, HALT, and per-project rows including
# workflow type, active RC, last released, max_minor, max_major.
# Refreshes every 10 seconds via watch.
# ---------------------------------------------------------------------------
tmux new-window -t "${SESSION_NAME}" -n "metadata"
tmux send-keys -t "${SESSION_NAME}:metadata" "${METADATA_CMD}" Enter

# ---------------------------------------------------------------------------
# Window 6: Metrics — two vertical panes
#
# Left pane:  watch -t -c -n $REFRESH_INTERVAL dashboard-metrics.sh
#   Shows today's per-project metrics (RCs shipped, wall time, tokens with
#   cache hit %, task count) and current-RC block (tasks done, elapsed wall
#   time, tokens so far).  Reads from metrics/day/<date>.json and
#   metrics/rc/<v>.json.
#
# Right pane: watch -t -c -n $REFRESH_INTERVAL show-metrics.sh
#   Shows historical RC metrics from history.csv (last 10 RCs):
#   rc, wall time, input/output/cache tokens, cache hit rate, task count.
#
# Both panes refresh on the standard dashboard interval.
#
# Split sequence:
#   Step 1: window created → pane 0 (full width)
#   Step 2: split pane 0 -h -p 60 → pane 0=left ~40% (metrics.sh), pane 1=right ~60% (show-metrics.sh)
#   show-metrics.sh table pane gets ~60% so the cache-write column
#   does not get truncated by tmux.
# ---------------------------------------------------------------------------
tmux new-window -t "${SESSION_NAME}" -n "metrics"
tmux split-window -t "${SESSION_NAME}:metrics.0" -h -p 60
tmux send-keys -t "${SESSION_NAME}:metrics.0" "${METRICS_CMD}" Enter
tmux send-keys -t "${SESSION_NAME}:metrics.1" "${SHOW_METRICS_CMD}" Enter

# ---------------------------------------------------------------------------
# Window 7: Logs full-screen (named "logs")
# ---------------------------------------------------------------------------
tmux new-window -t "${SESSION_NAME}" -n "logs"
tmux send-keys -t "${SESSION_NAME}:logs" "${LOGS_WIN_CMD}" Enter

# ---------------------------------------------------------------------------
# Window 8: debug-logs — unified, merged, color-coded debug log stream
#
# Single pane showing all agents' central debug logs in one interleaved,
# color-coded stream (same color scheme as dashboard-logs.sh).
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
tmux send-keys -t "${SESSION_NAME}:debug-logs.0" \
  "env PGAI_VERBOSE_MODE=${_VERBOSE_MODE} ${SCRIPT_DIR}/debug-logs.sh --kanban-root ${KANBAN_ROOT}" Enter

# ---------------------------------------------------------------------------
# Window 9: training-logs — unified, merged, color-coded training trace viewer
#
# Single pane showing all agents' newest training traces in one color-coded
# view (same color scheme as dashboard-logs.sh), refreshed every 30 seconds.
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

# Single pane: send the unified merged trace command via watch (30s refresh).
# env passes PGAI_REASONING_TRACE so the helper script sees it at startup.
tmux send-keys -t "${SESSION_NAME}:training-logs.0" \
  "watch -t -c -n 30 -- env PGAI_REASONING_TRACE=${_REASONING_TRACE} ${SCRIPT_DIR}/training-logs.sh --kanban-root ${KANBAN_ROOT}" Enter

# ---------------------------------------------------------------------------
# Window 10: Terminal — two-pane split: live logs (left) + KANBAN_ROOT shell (right)
#
# Left pane (0):  live log stream via logs.sh (same feed as the "logs" window).
# Right pane (1): a single interactive bash shell started at $KANBAN_ROOT.
#                 NOT pre-cd'd into any per-project task/queue subdirectory.
#
# Layout: horizontal split, logs occupy 60% of width on the left.
# ---------------------------------------------------------------------------
tmux new-window -t "${SESSION_NAME}" -n "terminal"
tmux split-window -t "${SESSION_NAME}:terminal.0" -h -p 40

tmux send-keys -t "${SESSION_NAME}:terminal.0" "${LOGS_WIN_CMD}" Enter
tmux send-keys -t "${SESSION_NAME}:terminal.1" "cd ${KANBAN_ROOT} && exec bash" Enter

# ---------------------------------------------------------------------------
# Windows W11+: Per-project drill windows (Feature 4 — per-project view selector)
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
#   Step B: split pane 0 -v -p 40 → pane 0 (top 60%), pane 1 (bottom LOGS 40%)
#   Step C: split pane 0 -v -p 82 → pane 0 (HEADER ~11%), pane 2 (middle 49%)
#   Step D: split pane 2 -h -p 70 → pane 2 (QUEUES ~30%), pane 3 (right 70%)
#   Step E: split pane 3 -h -p 36 → pane 3 (PROGRESS ~45%), pane 4 (CRON ~25%)
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

  _DRILL_LOGS_CMD="${SCRIPT_DIR}/logs.sh --kanban-root ${KANBAN_ROOT}"

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
    _DRILL_DOC_CMD="watch -t -c -n ${REFRESH_INTERVAL} -- ${SCRIPT_DIR}/show-document-drill.sh --project ${_DRILL_PROJ} ${RENDERER_ARGS[*]}"

    tmux new-window -t "${SESSION_NAME}" -n "${_DRILL_WIN}"

    # Split: bottom 45% = LOGS, top 55% = document drill pane
    tmux split-window -t "${SESSION_NAME}:${_DRILL_WIN}.0" -v -p 45

    tmux send-keys -t "${SESSION_NAME}:${_DRILL_WIN}.0" "${_DRILL_DOC_CMD}" Enter
    tmux send-keys -t "${SESSION_NAME}:${_DRILL_WIN}.1" "${_DRILL_LOGS_CMD}" Enter

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
    _DRILL_HEADER_CMD="watch -t -c -n ${REFRESH_INTERVAL} -- ${SCRIPT_DIR}/show-header.sh --project ${_DRILL_PROJ} ${RENDERER_ARGS[*]}"
    _DRILL_QUEUES_CMD="watch -t -c -n ${REFRESH_INTERVAL} -- ${SCRIPT_DIR}/show-queues.sh --project ${_DRILL_PROJ} ${RENDERER_ARGS[*]}"
    _DRILL_PROGRESS_CMD="watch -t -c -n ${REFRESH_INTERVAL} -- ${SCRIPT_DIR}/show-progress.sh --project ${_DRILL_PROJ} ${RENDERER_ARGS[*]}"
    _DRILL_CRON_CMD="watch -t -c -n ${REFRESH_INTERVAL} -- ${SCRIPT_DIR}/next-cron-firings.sh --kanban-root ${KANBAN_ROOT}"

    tmux new-window -t "${SESSION_NAME}" -n "${_DRILL_WIN}"

    # Step B: bottom 40% = LOGS
    tmux split-window -t "${SESSION_NAME}:${_DRILL_WIN}.0" -v -p 40
    # Step C: split top area — HEADER at top (~11%), middle below
    tmux split-window -t "${SESSION_NAME}:${_DRILL_WIN}.0" -v -p 82
    # Step D: split middle — QUEUES left (~30%), right 70%
    tmux split-window -t "${SESSION_NAME}:${_DRILL_WIN}.2" -h -p 70
    # Step E: split right half — PROGRESS (~45% total), CRON right (~25% total)
    tmux split-window -t "${SESSION_NAME}:${_DRILL_WIN}.3" -h -p 36

    tmux send-keys -t "${SESSION_NAME}:${_DRILL_WIN}.0" "${_DRILL_HEADER_CMD}" Enter
    tmux send-keys -t "${SESSION_NAME}:${_DRILL_WIN}.2" "${_DRILL_QUEUES_CMD}" Enter
    tmux send-keys -t "${SESSION_NAME}:${_DRILL_WIN}.3" "${_DRILL_PROGRESS_CMD}" Enter
    tmux send-keys -t "${SESSION_NAME}:${_DRILL_WIN}.4" "${_DRILL_CRON_CMD}" Enter
    tmux send-keys -t "${SESSION_NAME}:${_DRILL_WIN}.1" "${_DRILL_LOGS_CMD}" Enter
  fi

  # Rename the window to include the project name for operator clarity.
  # The window is created and wired using the plain drill-N name (avoiding colon
  # ambiguity in tmux target syntax), then renamed to "drill-N: <project>" once
  # all splits and send-keys are in place.
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
# dashboard-project-toggle.sh handles the logic; run-shell executes it in
# the background so tmux does not wait for it.
# ---------------------------------------------------------------------------
tmux bind-key -T prefix p run-shell \
  "${SCRIPT_DIR}/project-toggle.sh --session ${SESSION_NAME}"

# ---------------------------------------------------------------------------
# Return focus to main window and attach
# ---------------------------------------------------------------------------
tmux select-window -t "${SESSION_NAME}:main"
tmux select-pane -t "${SESSION_NAME}:main.0"

echo "Attaching to session '${SESSION_NAME}'..."
exec tmux attach-session -t "$SESSION_NAME"
