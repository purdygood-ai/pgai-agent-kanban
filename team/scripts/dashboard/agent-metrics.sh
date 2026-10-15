#!/usr/bin/env bash
# agent-metrics.sh
# Standalone per-agent, per-project averages renderer for the pgai kanban.
#
# The CLI twin of show-metrics.sh's per-agent view: that view renders a
# single RC's raw per-agent token breakdown, while this script averages wall
# time, tokens, and task count per agent, per project, and totals tokens and
# tasks, across a SPAN of SHIPPED per-RC metrics rollups
# (projects/<name>/metrics/rc/v*.json). Only rollups whose outcome is
# "shipped" count; cancelled ones enter no figure and are counted in a
# footer line instead. Figures are per agent PER PROJECT -- never blended
# across projects -- and a missing or malformed rollup file is skipped
# without taking a slot in the span.
#
# Usage:
#   agent-metrics.sh [options]
#
# Options:
#   --project <name>       Project to report on (default: all registered projects)
#   --window <N>           Newest N shipped RCs (default: 10)
#   --last <N>             Newest N shipped RCs (the same span as --window N)
#   --all                  Every shipped RC
#   --since <vX.Y.Z>       Every shipped RC at or above vX.Y.Z (semver order;
#                          the leading "v" is optional)
#   --kanban-root <path>   Override the kanban root directory
#   --no-color             Disable ANSI color output
#   -h, --help             Show this help and exit
#
#   --window, --last, --all and --since choose the span. They are
#   mutually exclusive: give at most one. With none, the span is
#   --window 10.
#
# Output, one block per project:
#   === Agent Metrics: <project> (<span>) ===
#   Agent  Avg Wall (min/task)  Avg Tokens (/RC)  Avg Tasks (/RC)  Total Tokens  Total Tasks
#   <one row per agent>
#   window: <N> shipped RCs (<M> cancelled excluded)
#
#   The header span reads "last N RCs", "all shipped RCs" or "shipped RCs
#   since vX.Y.Z", and the footer label "window", "all" or "since vX.Y.Z"
#   to match. N is the number of shipped RCs used, M the cancelled (or
#   otherwise unshipped) rollups inside the span's range; the footer prints
#   even when M is 0.
#
#   Averages print with exactly one decimal and thousands separators
#   (2.0, 4.7, 41,000.0); totals as thousands-separated integers. Avg Tokens
#   and Avg Tasks are means per invocation RC (the RCs an agent was actually
#   invoked in). Avg Wall is a mean per TASK, pooled from each invocation
#   RC's own per-agent wall-clock data, and prints '--' when none of an
#   agent's invocation RCs carry it. An agent with no invocation in the span
#   has no row.
#
# Environment:
#   PGAI_AGENT_KANBAN_ROOT_PATH  Kanban root override
#   NO_COLOR                     Set non-empty to disable colors
#   TERM=dumb                    Also disables colors
#
# Exit codes:
#   0  Report printed (also when metrics files are missing or malformed for
#      a project -- that block renders '(no metrics yet)')
#   1  Usage error: unknown argument, more than one span flag, --window or
#      --last not a positive integer, or --since not of the form vX.Y.Z
#
# Read-only: never writes kanban state. The arithmetic and rounding live in
# pgai_agent_kanban.lib.agent_metrics, the rendering in
# pgai_agent_kanban.lib.metrics.agents (reached through pp_run_ops, the
# same compute_agent_averages GET /agent-metrics calls); this script
# resolves the project list and validates arguments.

# shellcheck source=../lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/env_bootstrap.sh"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck source=lib/project_paths.sh
source "${SCRIPT_DIR}/../lib/project_paths.sh"
# shellcheck source=lib/projects.sh
source "${SCRIPT_DIR}/../lib/projects.sh"

# ---------------------------------------------------------------------------
# Parse arguments (before set -euo pipefail — arg parsing tolerates unset vars)
# ---------------------------------------------------------------------------
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
PROJECT_NAME=""
WINDOW=""
LAST=""
ALL_SHIPPED=false
SINCE=""
SPAN_FLAGS_GIVEN=()
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
    --window)
      _next=$(( _i + 1 ))
      WINDOW="${_args[$_next]:-10}"
      SPAN_FLAGS_GIVEN+=("--window")
      _i=$(( _i + 1 ))
      ;;
    --last)
      _next=$(( _i + 1 ))
      LAST="${_args[$_next]:-}"
      SPAN_FLAGS_GIVEN+=("--last")
      _i=$(( _i + 1 ))
      ;;
    --all)
      ALL_SHIPPED=true
      SPAN_FLAGS_GIVEN+=("--all")
      ;;
    --since)
      _next=$(( _i + 1 ))
      SINCE="${_args[$_next]:-}"
      SPAN_FLAGS_GIVEN+=("--since")
      _i=$(( _i + 1 ))
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
      # The help text is this file's leading comment block: every '#' line
      # after the shebang up to the first blank line. One sed reads it
      # (no pipe stage that could close early and SIGPIPE a writer).
      mapfile -t __help_lines < <(sed -n '2,/^$/{/^#/s/^# \{0,1\}//p}' "$0")
      printf '%s\n' "${__help_lines[@]}"
      exit 0
      ;;
    *)
      echo "ERROR: Unknown argument: $arg" >&2
      echo "Usage: $0 [--project <name>] [--window N | --last N | --all | --since vX.Y.Z] [--kanban-root <path>] [--no-color]" >&2
      exit 1
      ;;
  esac
  _i=$(( _i + 1 ))
done

set -euo pipefail

# Span flags: at most one; each value validated in the same shape.
if [[ ${#SPAN_FLAGS_GIVEN[@]} -gt 1 ]]; then
  echo "ERROR: --window, --last, --all and --since are mutually exclusive; give at most one (got: ${SPAN_FLAGS_GIVEN[*]})" >&2
  exit 1
fi

SPAN_ARGS=(--window 10)
if [[ -n "$WINDOW" ]]; then
  if ! [[ "$WINDOW" =~ ^[0-9]+$ ]] || [[ "$((10#$WINDOW))" -eq 0 ]]; then
    echo "ERROR: --window must be a positive integer (got: $WINDOW)" >&2
    exit 1
  fi
  SPAN_ARGS=(--window "$WINDOW")
elif [[ ${#SPAN_FLAGS_GIVEN[@]} -eq 1 && "${SPAN_FLAGS_GIVEN[0]}" == "--last" ]]; then
  if ! [[ "$LAST" =~ ^[0-9]+$ ]] || [[ "$((10#$LAST))" -eq 0 ]]; then
    echo "ERROR: --last must be a positive integer (got: $LAST)" >&2
    exit 1
  fi
  SPAN_ARGS=(--last "$LAST")
elif [[ "$ALL_SHIPPED" == "true" ]]; then
  SPAN_ARGS=(--all)
elif [[ ${#SPAN_FLAGS_GIVEN[@]} -eq 1 && "${SPAN_FLAGS_GIVEN[0]}" == "--since" ]]; then
  if ! [[ "$SINCE" =~ ^v?[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "ERROR: --since must be a version of the form vX.Y.Z (got: $SINCE)" >&2
    exit 1
  fi
  SPAN_ARGS=(--since "$SINCE")
fi

# ---------------------------------------------------------------------------
# ANSI color support (honor NO_COLOR, TERM=dumb, and --no-color flag)
# ---------------------------------------------------------------------------
USE_COLOR=true
if [[ "$NO_COLOR_ARG" == "true" ]] || \
   [[ -n "${NO_COLOR:-}" ]] || \
   [[ "${TERM:-}" == "dumb" ]]; then
  USE_COLOR=false
fi

# shellcheck source=lib/script_common.sh
source "${SCRIPT_DIR}/../lib/script_common.sh"
RESET="$(ansi_code reset)"
BOLD="$(ansi_code bold)"
DIM="$(ansi_code dim)"
CYAN="$(ansi_code cyan)"

# ---------------------------------------------------------------------------
# Resolve the project list: explicit --project, or every registered project
# (mirrors show-metrics.sh's default multi-project behavior).
# ---------------------------------------------------------------------------
REGISTERED_PROJECTS=()
if [[ -n "$PROJECT_NAME" ]]; then
  REGISTERED_PROJECTS=("$PROJECT_NAME")
else
  while IFS= read -r _p; do
    [[ -z "$_p" ]] && continue
    REGISTERED_PROJECTS+=("$_p")
  done < <(KANBAN_ROOT="$KANBAN_ROOT" projects_cfg_list 2>/dev/null || true)
fi

# shellcheck source=../lib/pp_run_ops.sh
source "${SCRIPT_DIR}/../lib/pp_run_ops.sh"

if [[ ${#REGISTERED_PROJECTS[@]} -eq 0 ]]; then
  printf '%s%s=== Agent Metrics ===%s\n' "$CYAN" "$BOLD" "$RESET"
  printf '\n'
  printf '  %s(no projects registered)%s\n' "$DIM" "$RESET"
  printf '\n'
  exit 0
fi

# ---------------------------------------------------------------------------
# Build a JSON descriptor array [{"name":..., "rc_dir":...}, ...] -- one
# entry per registered project -- and hand off to the shared Python
# averaging module. Bash owns project-list resolution (the canonical
# projects.cfg reader already lives in lib/projects.sh); Python owns the
# averaging arithmetic (pgai_agent_kanban.lib.agent_metrics) and rendering
# (pgai_agent_kanban.lib.metrics.agents).
# ---------------------------------------------------------------------------
_build_descriptors_json() {
  local first=true
  printf '['
  for _proj in "${REGISTERED_PROJECTS[@]}"; do
    local _proj_root _rc_dir _safe_name _safe_rc_dir
    _proj_root="$(KANBAN_ROOT="$KANBAN_ROOT" pp_project_root "$_proj" 2>/dev/null)" || _proj_root=""
    _rc_dir=""
    [[ -n "$_proj_root" ]] && _rc_dir="${_proj_root}/metrics/rc"

    [[ "$first" == "false" ]] && printf ','
    first=false

    _safe_name="$(printf '%s' "$_proj" | sed 's/\\/\\\\/g; s/"/\\"/g')"
    _safe_rc_dir="$(printf '%s' "$_rc_dir" | sed 's/\\/\\\\/g; s/"/\\"/g')"
    printf '{"name":"%s","rc_dir":"%s"}' "$_safe_name" "$_safe_rc_dir"
  done
  printf ']'
}

PROJECTS_JSON="$(_build_descriptors_json)"

pp_run_ops pgai_agent_kanban.lib.metrics compute_agent_averages \
  --projects-json "$PROJECTS_JSON" \
  "${SPAN_ARGS[@]}" \
  --color "$USE_COLOR" \
  --color-reset "$RESET" --color-bold "$BOLD" --color-dim "$DIM" --color-cyan "$CYAN"
