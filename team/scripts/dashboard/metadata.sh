#!/usr/bin/env bash
# metadata.sh
# Emits a formatted metadata overview for the pgai kanban dashboard.
#
# Designed to run under `watch -n 10 metadata.sh` in the
# tmux `metadata` window created by dashboard/create.sh.
#
# Output sections:
#   === Kanban Metadata ===
#     Kanban version, PM mode, HALT state
#
#   === Registered projects ===
#     Per-project rows: name, workflow type, active RC, last released,
#     max minor, max major
#
# Usage:
#   metadata.sh [--kanban-root <path>] [--no-color]
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
#
# Read-only: this script never modifies any kanban state.

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
      printf '%s\n' "${__help_lines[@]:0:28}"
      exit 0
      ;;
    *)
      echo "ERROR: Unknown argument: $1" >&2
      echo "Usage: $0 [--kanban-root <path>] [--no-color]" >&2
      exit 1
      ;;
  esac
done

# ---------------------------------------------------------------------------
# Source project libraries
# ---------------------------------------------------------------------------
# shellcheck source=lib/project_paths.sh
source "${SCRIPT_DIR}/../lib/project_paths.sh"

# Local-only render guard: this is a dashboard render path, so
# pp_last_released_version below must never perform a network git fetch.
# Honored by pp_last_released_version before its `git fetch origin --tags`
# call; local tags/refs already on disk are authoritative for last-released.
export PGAI_GIT_LOCAL_ONLY=1

# shellcheck source=lib/projects.sh
source "${SCRIPT_DIR}/../lib/projects.sh"

# Source dev_tree helper (resolve_global_dev_tree / require_dev_tree)
# shellcheck source=lib/dev_tree.sh
source "${SCRIPT_DIR}/../lib/dev_tree.sh"

# Source shared ANSI color primitive (single home for the color-name ->
# escape-code table -- see team/scripts/lib/script_common.sh).
# shellcheck source=lib/script_common.sh
source "${SCRIPT_DIR}/../lib/script_common.sh"

# ---------------------------------------------------------------------------
# Source optional config files (same pattern as dashboard/status-bottom.sh)
# so PGAI_KANBAN_PM_MODE is available in subshell invocations.
# ---------------------------------------------------------------------------
[[ -f "$KANBAN_ROOT/bashrc" ]]               && source "$KANBAN_ROOT/bashrc"            2>/dev/null || true
[[ -f "$KANBAN_ROOT/env" ]]                  && source "$KANBAN_ROOT/env"               2>/dev/null || true
[[ -f "$HOME/.config/pgai-kanban.cfg" ]]     && source "$HOME/.config/pgai-kanban.cfg" 2>/dev/null || true
# Source: kanban.cfg [chain/paths/dashboard] — INI format replaces legacy config.cfg
# read_ini available via project_paths.sh (sourced above).
if [[ -f "${KANBAN_ROOT}/kanban.cfg" ]]; then
    export PGAI_KANBAN_PM_MODE="${PGAI_KANBAN_PM_MODE:-$(read_ini "${KANBAN_ROOT}/kanban.cfg" chain pm_mode automatic)}"
    DASHBOARD_ROWS_PER_COLUMN="${DASHBOARD_ROWS_PER_COLUMN:-$(read_ini "${KANBAN_ROOT}/kanban.cfg" dashboard rows_per_column 21)}"
    export DASHBOARD_ROWS_PER_COLUMN
fi
export PGAI_DEV_TREE_PATH="${PGAI_DEV_TREE_PATH:-$(resolve_global_dev_tree)}"

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
# Helpers
# ---------------------------------------------------------------------------

# Read a field value from project.cfg (or PROJECT.cfg) without sourcing it.
_cfg_field() {
  local cfg_file="$1"
  local field="$2"
  grep -E "^[[:space:]]*${field}[[:space:]]*=" "$cfg_file" 2>/dev/null \
    | head -n1 \
    | sed 's/^[^=]*=[[:space:]]*//' \
    | sed "s/^['\"]//; s/['\"]$//" \
    | tr -d '[:space:]'
}

# _active_rc <project> none -- a project's Active RC, or "none" (lib/cm/common.sh).
# shellcheck source=../lib/cm/common.sh
source "${SCRIPT_DIR}/../lib/cm/common.sh"

# Unset placeholder (em-dash fallback to hyphen if locale is restrictive)
UNSET_PLACEHOLDER="—"

# _ceiling_cell <accessor> <project> -- one version ceiling as the pane shows
# it: the number in effect, "none" for an explicit no-ceiling, or "invalid"
# when the accessor refuses a malformed value (its own stderr line, naming
# the file, key and value, is not discarded).
_ceiling_cell() {
  local _value
  if _value="$("$1" "$2")"; then
    printf '%s' "${_value:-none}"
  else
    printf 'invalid'
  fi
}

# ---------------------------------------------------------------------------
# Section header helper (lib/dashboard_common.sh)
# ---------------------------------------------------------------------------
# shellcheck source=../lib/dashboard_common.sh
source "${SCRIPT_DIR}/../lib/dashboard_common.sh"

# ---------------------------------------------------------------------------
# Kanban-wide metadata
# ---------------------------------------------------------------------------

# Kanban version: prefer VERSION file from kanban root
KANBAN_VERSION="unknown"
VERSION_FILE="${KANBAN_ROOT}/VERSION"
if [[ -f "$VERSION_FILE" ]]; then
  KANBAN_VERSION="$(tr -d '[:space:]' < "$VERSION_FILE" 2>/dev/null)" || KANBAN_VERSION="unknown"
  [[ -z "$KANBAN_VERSION" ]] && KANBAN_VERSION="unknown"
fi

# PM mode
PM_RAW="${PGAI_KANBAN_PM_MODE:-automatic}"
if [[ "$PM_RAW" == "manual" ]]; then
  PM_DISPLAY="manual"
else
  PM_DISPLAY="automatic"
fi

# HALT state
if [[ -f "${KANBAN_ROOT}/HALT" ]]; then
  HALT_DISPLAY="on"
else
  HALT_DISPLAY="off"
fi

# ---------------------------------------------------------------------------
# Output — Kanban Metadata section
# ---------------------------------------------------------------------------
section_header "Kanban Metadata"
printf '%-18s %s%s%s\n' "Kanban version:" "${GREEN}${BOLD}" "$KANBAN_VERSION" "$RESET"
printf '%-18s %s\n'     "PM mode:"         "$PM_DISPLAY"
printf '%-18s %s\n'     "HALT:"            "$HALT_DISPLAY"
printf '\n'

# ---------------------------------------------------------------------------
# Output — Registered projects section
# ---------------------------------------------------------------------------
section_header "Registered projects"

# Read registered projects from projects.cfg
REGISTERED_PROJECTS=()
while IFS= read -r _p; do
  [[ -z "$_p" ]] && continue
  REGISTERED_PROJECTS+=("$_p")
done < <(KANBAN_ROOT="$KANBAN_ROOT" projects_cfg_list 2>/dev/null || true)

if [[ ${#REGISTERED_PROJECTS[@]} -eq 0 ]]; then
  printf '%s(no projects registered)%s\n' "$DIM" "$RESET"
else
  for _proj in "${REGISTERED_PROJECTS[@]}"; do
    printf '\n'
    printf '%s%s%s\n' "${BOLD}" "$_proj" "$RESET"

    # project.cfg for this project (prefer lowercase; fall back to uppercase for legacy installs)
    _proj_root="$(pp_project_root "$_proj" 2>/dev/null)" || _proj_root=""
    _cfg=""
    if [[ -n "$_proj_root" ]]; then
      if [[ -f "${_proj_root}/project.cfg" ]]; then
        _cfg="${_proj_root}/project.cfg"
      elif [[ -f "${_proj_root}/PROJECT.cfg" ]]; then
        _cfg="${_proj_root}/PROJECT.cfg"
      fi
    fi

    # workflow type
    _workflow="${UNSET_PLACEHOLDER}"
    if [[ -n "$_cfg" && -f "$_cfg" ]]; then
      _wf="$(_cfg_field "$_cfg" "workflow_type")"
      [[ -n "$_wf" ]] && _workflow="$_wf"
    fi

    # active RC
    _active_rc_val="$(_active_rc "$_proj" none)"

    # last released (via pp_last_released_version — uses git tags on dev tree)
    _last_released="$(pp_last_released_version "$_proj" 2>/dev/null || echo "v0.0.0")"
    [[ -z "$_last_released" ]] && _last_released="v0.0.0"

    # Version ceilings in effect (pp_max_* apply the documented default to an
    # absent key and echo empty for an explicit `none`). A malformed value
    # shows as "invalid"; its accessor's one line naming the file, key and
    # value is left on stderr.
    _max_minor="$(_ceiling_cell pp_max_minor "$_proj")"
    _max_major="$(_ceiling_cell pp_max_major "$_proj")"
    _max_patch="$(_ceiling_cell pp_max_patch "$_proj")"

    # Print per-project row — indented 2 spaces, values aligned at col 20
    printf '  %-16s %s\n' "workflow:"      "$_workflow"
    printf '  %-16s %s\n' "active RC:"    "$_active_rc_val"
    printf '  %-16s %s\n' "last released:" "$_last_released"
    printf '  %-16s %s\n' "max minor:"    "$_max_minor"
    printf '  %-16s %s\n' "max major:"    "$_max_major"
    printf '  %-16s %s\n' "max patch:"    "$_max_patch"
  done
fi

printf '\n'

# ---------------------------------------------------------------------------
# Footer: last-updated timestamp
# ---------------------------------------------------------------------------
printf '%supdated %s%s\n' "$DIM" "$(date '+%Y-%m-%d %H:%M:%S')" "$RESET"
