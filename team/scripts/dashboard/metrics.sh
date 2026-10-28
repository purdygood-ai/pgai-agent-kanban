#!/usr/bin/env bash
# metrics.sh
# Renders the Metrics pane for the pgai kanban dashboard.
#
# Displays four sections:
#   Section 1: Today — per-project summary (RCs shipped, wall time, tokens, tasks)
#   Section 2: Current RC — per-project open RC progress (tasks done/total, elapsed, blocked minutes, tokens so far)
#   Section 3: Reliability — per-project Hands-off and Clean streaks
#              (RCs / hours since the last block, its time and cause), one
#              line each, at most 70 columns; the same lines
#              metrics-report.sh --reliability prints
#   Section 4: Bugs — per-project bug count split three ways, e.g.
#                2 open / 3 deferred / 1 needs-operator
#              open bugs the deferral posture keeps, open bugs it defers
#              (the level the bottom status line counts with; 0 when
#              deferral is off), and bugs waiting on an operator answer.
#              Only projects with a bug to count get a line; with none
#              anywhere the section is omitted.
#
# Reads from:
#   projects/<name>/metrics/day/<YYYY-MM-DD>.json  (today block)
#   projects/<name>/metrics/rc/<v>.json             (current RC block)
#   projects/<name>/release-state.md               (active RC discovery)
#   projects/<name>/metrics/reliability.json       (reliability block)
#   projects/<name>/bugs/*.md                       (bugs block)
#   [release] deferral posture: DEFER-LOW-BUGS, project.cfg, kanban.cfg
#                                                  (bugs block, deferred count)
#
# Designed to run under `watch -t -c -n $REFRESH_INTERVAL metrics.sh`
# in the tmux Metrics window created by dashboard/create.sh.
# The -c flag in watch preserves ANSI color sequences (truecolor safe).
# This script does NOT use `clear` at the top level — truecolor is preserved
# because watch -c handles screen clearing itself.
#
# Usage:
#   metrics.sh [--kanban-root <path>] [--no-color] [--now <iso>]
#
# Options:
#   --kanban-root <path>   Override the kanban root directory
#   --no-color             Disable ANSI color output
#   --now <iso>            Render as of this ISO-8601 time instead of now
#                          (the Reliability hours, the Current RC elapsed
#                          time and the Updated line), e.g.
#                          --now 2026-10-18T12:00:00+00:00
#   -h, --help             Show this help and exit
#
# Environment variables:
#   PGAI_AGENT_KANBAN_ROOT_PATH  Kanban root override
#   NO_COLOR                            Set non-empty to disable colors
#   TERM=dumb                           Also disables colors
#
# Read-only: this script never modifies any kanban state.
# Exits 0 always; renders friendly placeholders on missing data.

set -euo pipefail
# shellcheck source=../lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/env_bootstrap.sh"

# ---------------------------------------------------------------------------
# Resolve script directory
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
NO_COLOR_ARG=false
RENDER_NOW=""

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
    --now)
      RENDER_NOW="${2:-}"
      shift 2
      ;;
    -h|--help)
      # shellcheck source=../lib/header_help.sh
      source "$(dirname "${BASH_SOURCE[0]}")/../lib/header_help.sh"
      header_help_print "${BASH_SOURCE[0]}"
      exit 0
      ;;
    *)
      echo "ERROR: Unknown argument: $1" >&2
      echo "Usage: $0 [--kanban-root <path>] [--no-color] [--now <iso>]" >&2
      exit 1
      ;;
  esac
done

# ---------------------------------------------------------------------------
# Source project libraries
# ---------------------------------------------------------------------------
# shellcheck source=lib/project_paths.sh
source "${SCRIPT_DIR}/../lib/project_paths.sh"

# shellcheck source=lib/projects.sh
source "${SCRIPT_DIR}/../lib/projects.sh"
# Source shared ANSI color primitive (single home for the color-name ->
# escape-code table -- see team/scripts/lib/script_common.sh).
# shellcheck source=lib/script_common.sh
source "${SCRIPT_DIR}/../lib/script_common.sh"

# ---------------------------------------------------------------------------
# ANSI color support (honor NO_COLOR, TERM=dumb, and --no-color flag)
# ---------------------------------------------------------------------------
USE_COLOR=true
if [[ "$NO_COLOR_ARG" == "true" ]] || \
   [[ -n "${NO_COLOR:-}" ]] || \
   [[ "${TERM:-}" == "dumb" ]]; then
  USE_COLOR=false
fi

RESET="$(ansi_code reset)"
BOLD="$(ansi_code bold)"
DIM="$(ansi_code dim)"
CYAN="$(ansi_code cyan)"
GREEN="$(ansi_code green)"
YELLOW="$(ansi_code yellow)"

# ---------------------------------------------------------------------------
# Shared helpers: section_header (lib/dashboard_common.sh) and
# release_state_section, the release-state.md field reader (lib/cm/common.sh).
# ---------------------------------------------------------------------------
# shellcheck source=../lib/dashboard_common.sh
source "${SCRIPT_DIR}/../lib/dashboard_common.sh"
# shellcheck source=../lib/cm/common.sh
source "${SCRIPT_DIR}/../lib/cm/common.sh"
# pp_run_ops: the pane renderer runs from this tree's own pgai_agent_kanban
# package.
# shellcheck source=../lib/pp_run_ops.sh
source "${SCRIPT_DIR}/../lib/pp_run_ops.sh"

# ---------------------------------------------------------------------------
# Helper: find the per-day metrics rollup file for a project.
# Reads from projects/<name>/metrics/day/<date>.json
# ---------------------------------------------------------------------------
_day_metrics_file() {
  local project_name="$1"
  local date_str="$2"
  local proj_root
  proj_root="$(KANBAN_ROOT="$KANBAN_ROOT" pp_project_root "$project_name" 2>/dev/null)" || { echo ""; return; }
  local candidate="${proj_root}/metrics/day/${date_str}.json"
  if [[ -f "$candidate" ]]; then
    echo "$candidate"
  else
    echo ""
  fi
}

# ---------------------------------------------------------------------------
# Helper: find the per-RC metrics rollup file for a project.
# Reads from projects/<name>/metrics/rc/<v>.json
# ---------------------------------------------------------------------------
_rc_metrics_file() {
  local project_name="$1"
  local rc_version="$2"
  local proj_root
  proj_root="$(KANBAN_ROOT="$KANBAN_ROOT" pp_project_root "$project_name" 2>/dev/null)" || { echo ""; return; }
  local candidate="${proj_root}/metrics/rc/${rc_version}.json"
  if [[ -f "$candidate" ]]; then
    echo "$candidate"
  else
    echo ""
  fi
}

# ---------------------------------------------------------------------------
# Metrics renderer: pgai_agent_kanban.lib.metrics.metrics_pane renders all
# four sections -- the per-project today block (metrics/day/<date>.json),
# the per-project current-RC block (metrics/rc/<v>.json), the
# per-project reliability block (metrics/reliability.json, through
# pgai_agent_kanban.metrics.reliability_view) and the per-project bugs
# block (bugs/, with the deferral posture resolved from kanban_root) --
# from the project descriptors JSON, with the escapes computed above.
# ---------------------------------------------------------------------------

_render_metrics() {
  # Arguments:
  #   $1  use_color        "true" or "false"
  #   $2  today_date       YYYY-MM-DD
  #   $3  projects_json    JSON array of project descriptors:
  #                        [ { "name": "...", "day_file": "...", "rc": "...",
  #                            "rc_file": "...", "opened_at": "...",
  #                            "reliability_file": "...",
  #                            "bugs_dir": "...", "kanban_root": "..." }, ... ]
  # RENDER_NOW (--now) is the render time; empty means the current time.
  pp_run_ops pgai_agent_kanban.lib.metrics metrics_pane "$1" "$2" "$3" \
      "$RESET" "$BOLD" "$DIM" "$CYAN" "$GREEN" "$YELLOW" "$RENDER_NOW"
}

# ---------------------------------------------------------------------------
# Gather registered projects
# ---------------------------------------------------------------------------
TODAY="$(date +%Y-%m-%d)"

REGISTERED_PROJECTS=()
while IFS= read -r _p; do
  [[ -z "$_p" ]] && continue
  REGISTERED_PROJECTS+=("$_p")
done < <(KANBAN_ROOT="$KANBAN_ROOT" projects_cfg_list 2>/dev/null || true)

# ---------------------------------------------------------------------------
# Build the JSON descriptor array the renderer reads
# ---------------------------------------------------------------------------
_build_project_descriptors_json() {
  local first=true
  printf '['
  for _proj in "${REGISTERED_PROJECTS[@]}"; do
    local _day_file _rc _rc_file _opened_at _proj_root _release_state _reliability_file _bugs_dir

    _day_file="$(_day_metrics_file "$_proj" "$TODAY")"
    _rc=""
    _rc_file=""
    _opened_at=""
    _reliability_file=""
    _bugs_dir=""

    # Active RC and RC Opened At in one read of the project's release-state.md:
    # _rc is a vX.Y.Z version or "" (no RC); _opened_at is the value as
    # written, or "" when the heading or value is missing.
    _proj_root="$(KANBAN_ROOT="$KANBAN_ROOT" pp_project_root "$_proj" 2>/dev/null)" || _proj_root=""
    if [[ -n "$_proj_root" ]]; then
      # The streak file's path; the renderer shows a placeholder when it is
      # absent or unreadable.
      _reliability_file="${_proj_root}/metrics/reliability.json"
      # The bug files the Bugs block counts; a missing directory counts none.
      _bugs_dir="${_proj_root}/bugs"
      _release_state="$(release_state_section "${_proj_root}/release-state.md" "Active RC" "RC Opened At")" || true
      { IFS= read -r _rc || true; IFS= read -r _opened_at || true; } <<< "$_release_state"
      _rc="${_rc//[[:space:]]/}"
      [[ "$_rc" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || _rc=""
    fi

    if [[ -n "$_rc" ]]; then
      _rc_file="$(_rc_metrics_file "$_proj" "$_rc")"
      _opened_at="${_opened_at//[[:space:]]/}"
    else
      _opened_at=""
    fi

    [[ "$first" == "false" ]] && printf ','
    first=false

    # Use printf to build safe JSON (no jq dependency)
    # Escape backslashes and double quotes in string values
    _safe_name="$(printf '%s' "$_proj" | sed 's/\\/\\\\/g; s/"/\\"/g')"
    _safe_day_file="$(printf '%s' "$_day_file" | sed 's/\\/\\\\/g; s/"/\\"/g')"
    _safe_rc="$(printf '%s' "$_rc" | sed 's/\\/\\\\/g; s/"/\\"/g')"
    _safe_rc_file="$(printf '%s' "$_rc_file" | sed 's/\\/\\\\/g; s/"/\\"/g')"
    _safe_opened_at="$(printf '%s' "$_opened_at" | sed 's/\\/\\\\/g; s/"/\\"/g')"
    _safe_reliability_file="$(printf '%s' "$_reliability_file" | sed 's/\\/\\\\/g; s/"/\\"/g')"
    _safe_bugs_dir="$(printf '%s' "$_bugs_dir" | sed 's/\\/\\\\/g; s/"/\\"/g')"
    _safe_kanban_root="$(printf '%s' "$KANBAN_ROOT" | sed 's/\\/\\\\/g; s/"/\\"/g')"

    printf '{"name":"%s","day_file":"%s","rc":"%s","rc_file":"%s","opened_at":"%s","reliability_file":"%s","bugs_dir":"%s","kanban_root":"%s"}' \
      "$_safe_name" "$_safe_day_file" "$_safe_rc" "$_safe_rc_file" "$_safe_opened_at" \
      "$_safe_reliability_file" "$_safe_bugs_dir" "$_safe_kanban_root"
  done
  printf ']'
}

# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------
if [[ ${#REGISTERED_PROJECTS[@]} -eq 0 ]]; then
  section_header "Metrics"
  printf '\n'
  printf '%s(no projects registered)%s\n' "$DIM" "$RESET"
  printf '\n'
  printf '%supdated %s%s\n' "$DIM" "$(date '+%Y-%m-%d %H:%M:%S')" "$RESET"
else
  _PROJECTS_JSON="$(_build_project_descriptors_json)"
  _render_metrics "$USE_COLOR" "$TODAY" "$_PROJECTS_JSON"
fi
