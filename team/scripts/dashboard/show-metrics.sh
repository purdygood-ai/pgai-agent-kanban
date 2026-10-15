#!/usr/bin/env bash
# show-metrics.sh
# Historical RC metrics viewer for the pgai kanban dashboard.
#
# Reads metrics from:
#   projects/<name>/metrics/history.csv   (default view, all projects)
#   projects/<name>/metrics/rc/v*.json    (--per-agent view, single project)
#
# Usage:
#   show-metrics.sh [options]
#
# Options:
#   --project <name>          Project name (default: all registered projects)
#   --last <N>                Show last N rows (default: 10)
#   --history-csv <path>      Override CSV path (for testing; implies single-project mode)
#   --per-agent               Show per-agent token breakdown for most recent RC
#   --kanban-root <path>      Override kanban root directory
#   --no-color                Disable ANSI color output
#   -h, --help                Show this help and exit
#
# Default CSV columns:  rc | wall_time | blocked | input | output | cache_read | cache_write | tasks
# Per-agent columns:    agent | input | output | cache_read | cache_write | invocations
#
# Wall time shows '--' when opened_at/closed_at/wall_time_minutes
# are not yet populated in history.csv.
#
# Blocked shows the merged blocked-interval minutes for the row's RC, a
# real 0 for a clean run, or a single '-' when the row's history.csv
# entry carries no value for that column -- the table then prints one
# footer line explaining when the column started being tracked.
#
# Numbers are formatted with commas for readability (e.g. 5,644,879).
#
# Environment:
#   PGAI_AGENT_KANBAN_ROOT_PATH  Kanban root override
#   NO_COLOR                            Set non-empty to disable colors
#   TERM=dumb                           Also disables colors

# shellcheck source=../lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/env_bootstrap.sh"

# ---------------------------------------------------------------------------
# Resolve script directory (before set -euo pipefail so sourcing works safely)
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---------------------------------------------------------------------------
# Parse arguments (before set -euo pipefail — arg parsing tolerates unset vars)
# ---------------------------------------------------------------------------
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
PROJECT_NAME=""
LAST_N=10
HISTORY_CSV_OVERRIDE=""
PER_AGENT=false
NO_COLOR_ARG=false

_args=("$@")
_i=0
while [[ $_i -lt ${#_args[@]} ]]; do
  arg="${_args[$_i]}"
  case "$arg" in
    --project)
      _next=$(( _i + 1 ))
      PROJECT_NAME="${_args[$_next]:-}"
      _i=$(( _i + 1 ))
      ;;
    --last)
      _next=$(( _i + 1 ))
      LAST_N="${_args[$_next]:-10}"
      _i=$(( _i + 1 ))
      ;;
    --history-csv)
      _next=$(( _i + 1 ))
      HISTORY_CSV_OVERRIDE="${_args[$_next]:-}"
      _i=$(( _i + 1 ))
      ;;
    --per-agent)
      PER_AGENT=true
      ;;
    --kanban-root)
      _next=$(( _i + 1 ))
      KANBAN_ROOT="${_args[$_next]:-$KANBAN_ROOT}"
      _i=$(( _i + 1 ))
      ;;
    --no-color)
      NO_COLOR_ARG=true
      ;;
    -h|--help)
      # shellcheck source=../lib/header_help.sh
      source "$(dirname "${BASH_SOURCE[0]}")/../lib/header_help.sh"
      header_help_print "${BASH_SOURCE[0]}"
      exit 0
      ;;
    *)
      echo "ERROR: Unknown argument: $arg" >&2
      echo "Usage: $0 [--project <name>] [--last N] [--per-agent] [--no-color] [--kanban-root <path>]" >&2
      exit 1
      ;;
  esac
  _i=$(( _i + 1 ))
done

set -euo pipefail

# ---------------------------------------------------------------------------
# Rendering is pgai_agent_kanban.lib.metrics.show_metrics(), the same
# function GET /metrics calls: the --per-agent table, the --history-csv
# table, one project's table, or (no --project) every project registered in
# projects.cfg, in priority order, with "(no releases yet)" for a project
# that has no history.csv. Colour follows --no-color, NO_COLOR and
# TERM=dumb. --per-agent and --history-csv need a project: --project, else
# $PGAI_PROJECT_NAME.
# ---------------------------------------------------------------------------
# shellcheck source=../lib/pp_run_ops.sh
source "${SCRIPT_DIR}/../lib/pp_run_ops.sh"

pp_run_ops pgai_agent_kanban.lib.metrics show_metrics \
  --kanban-root="$KANBAN_ROOT" \
  --project="$PROJECT_NAME" \
  --last="$LAST_N" \
  --history-csv="$HISTORY_CSV_OVERRIDE" \
  --per-agent="$PER_AGENT" \
  --no-color="$NO_COLOR_ARG" \
  --env-project="${PGAI_PROJECT_NAME:-}"
