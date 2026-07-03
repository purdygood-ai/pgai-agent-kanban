#!/usr/bin/env bash
# debug-logs.sh
# Merged, color-coded debug log stream for the dashboard debug-logs window.
#
# When at least one project has [debug] verbose_mode = true in project.cfg,
# tails all seven agents' per-project debug log files and merges them into a
# single interleaved stream with tail -F style following.
# Each line is tagged with the agent name and current time in HH:MM:SS format.
# When no project has verbose_mode enabled: shows a placeholder and waits.
#
# Color scheme (same as dashboard-logs.sh — reused, not reinvented):
#   pm       = cyan
#   po       = red
#   coder    = green
#   writer   = yellow
#   tester   = blue
#   cm       = magenta
#   overwatch = dim
#
# Debug log files are expected at:
#   $KANBAN_ROOT/projects/<name>/logs/debug/<agent>.log
#
# One set of log files is followed per verbose-enabled project.
# Missing log files are handled gracefully: tail -F waits for files that do
# not yet exist and begins following once they appear.
#
# Usage:
#   dashboard-debug-logs.sh [--kanban-root <path>] [--stdout]
#
# Flags:
#   --kanban-root <path>   Override the kanban root path
#   --stdout               Print the last 30 lines of each per-project debug log
#                          file once to stdout and exit 0. The blocking tail -F
#                          pipeline is NOT started. Safe to call standalone from
#                          a terminal without tmux or verbose mode enabled.
#
# Environment:
#   PGAI_AGENT_KANBAN_ROOT_PATH  — kanban root (default: ~/pgai_agent_kanban)
#   NO_COLOR                     — set to 1 to disable color

# Not using set -euo pipefail — long-running tail pipeline; pipe closes should
# not abort the process.

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
KANBAN_ROOT_OVERRIDE=""
STDOUT_MODE=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --kanban-root)
      KANBAN_ROOT_OVERRIDE="${2:-}"
      shift 2
      ;;
    --stdout)
      STDOUT_MODE=true
      shift
      ;;
    --*)
      shift
      ;;
    *)
      shift
      ;;
  esac
done

# ---------------------------------------------------------------------------
# Resolve kanban root and project log directory
# ---------------------------------------------------------------------------
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}"
if [[ -n "$KANBAN_ROOT_OVERRIDE" ]]; then
  KANBAN_ROOT="$KANBAN_ROOT_OVERRIDE"
fi

_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/project_paths.sh
source "${_SCRIPT_DIR}/../lib/project_paths.sh"
# shellcheck source=lib/projects.sh
source "${_SCRIPT_DIR}/../lib/projects.sh"
# shellcheck source=lib/temp.sh
source "${_SCRIPT_DIR}/../lib/temp.sh"
unset _SCRIPT_DIR

# Debug logs are per-project at projects/<name>/logs/debug/.
# Each project with [debug] verbose_mode = true contributes its own
# logs/debug/<agent>.log files to the merged stream.

# ---------------------------------------------------------------------------
# Enumerate projects with verbose mode enabled.
# When more than one project has [debug] verbose_mode = true, each rendered
# log line is prefixed with [project-name] so the operator can distinguish
# overlapping streams. When exactly one project (or none) is verbose, output
# is unchanged — no prefix is added.
# A projects_cfg_list failure means the project registry is unreadable — a
# real error, not an occasion to fall back to a single guessed project.
# ---------------------------------------------------------------------------
_all_projects=()
if ! mapfile -t _all_projects < <(projects_cfg_list 2>/dev/null); then
  echo "ERROR: projects_cfg_list failed — projects.cfg may be missing or unreadable" >&2
  exit 1
fi

VERBOSE_PROJECTS=()
for _proj in "${_all_projects[@]}"; do
  [[ -z "$_proj" ]] && continue
  _vm="$(pp_verbose_mode "$_proj" 2>/dev/null || echo "false")"
  if [[ "$_vm" == "true" ]]; then
    VERBOSE_PROJECTS+=("$_proj")
  fi
done
unset _all_projects

# MULTI_PROJECT_MODE: non-empty string when more than one project is verbose.
MULTI_PROJECT_MODE=""
if [[ "${#VERBOSE_PROJECTS[@]}" -gt 1 ]]; then
  MULTI_PROJECT_MODE="true"
fi
unset _proj _vm

# ---------------------------------------------------------------------------
# Activation gate
# Activates the tail/display path when VERBOSE_PROJECTS is non-empty (at least
# one registered project has [debug] verbose_mode = true in its project.cfg).
# --stdout bypasses this gate: it always prints available log content.
# ---------------------------------------------------------------------------
if [[ "${#VERBOSE_PROJECTS[@]}" -eq 0 ]] && [[ "$STDOUT_MODE" != "true" ]]; then
  while true; do
    clear
    printf '\n  [debug-logs] (verbose mode disabled — enable [debug] verbose_mode in a project.cfg to activate)\n\n'
    printf '  Per-project log directories: %s\n' "${KANBAN_ROOT}/projects/*/logs/debug/"
    sleep 30
  done
fi

# ---------------------------------------------------------------------------
# Color support
# ---------------------------------------------------------------------------
USE_COLOR=true
if [[ "${TERM:-}" == "dumb" ]] || [[ "${NO_COLOR:-}" == "1" ]]; then
  USE_COLOR=false
fi

if [[ "$USE_COLOR" == "true" ]]; then
  C_CYAN=$'\033[0;36m'
  C_GREEN=$'\033[0;32m'
  C_YELLOW=$'\033[0;33m'
  C_BLUE=$'\033[0;34m'
  C_MAGENTA=$'\033[0;35m'
  C_RED=$'\033[0;31m'
  C_DIM=$'\033[2m'
  C_RESET=$'\033[0m'
else
  C_CYAN=""
  C_GREEN=""
  C_YELLOW=""
  C_BLUE=""
  C_MAGENTA=""
  C_RED=""
  C_DIM=""
  C_RESET=""
fi

# ---------------------------------------------------------------------------
# Agent definitions: name -> color
# Canonical seven-agent set: pm po coder writer tester cm overwatch
# ---------------------------------------------------------------------------
declare -A AGENT_COLOR
AGENT_COLOR[pm]="$C_CYAN"
AGENT_COLOR[po]="$C_RED"
AGENT_COLOR[coder]="$C_GREEN"
AGENT_COLOR[writer]="$C_YELLOW"
AGENT_COLOR[tester]="$C_BLUE"
AGENT_COLOR[cm]="$C_MAGENTA"
AGENT_COLOR[overwatch]="$C_DIM"

AGENT_ORDER=(pm po coder writer tester cm overwatch)

# ---------------------------------------------------------------------------
# Build per-project log file lists.
# Each verbose project contributes one log file per agent, located at:
#   $KANBAN_ROOT/projects/<project>/logs/debug/<agent>.log
#
# ALL_LOG_FILES: flat list of all (project, agent) log paths for tail -F.
# Each entry is the absolute path; missing files are tolerated — tail -F
# waits for them to appear once created.
# ---------------------------------------------------------------------------
declare -a ALL_LOG_FILES=()

# LOG_FILE_REGISTRY: parallel arrays mapping log file path -> (project, agent)
# Used by awk (written to AGENT_MAP_FILE) to reconstruct per-line context.
declare -a _REG_PATHS=()
declare -a _REG_AGENTS=()
declare -a _REG_PROJECTS=()

for _proj in "${VERBOSE_PROJECTS[@]}"; do
  _proj_debug_dir="${KANBAN_ROOT}/projects/${_proj}/logs/debug"
  for _agent in "${AGENT_ORDER[@]}"; do
    _logfile="${_proj_debug_dir}/${_agent}.log"
    ALL_LOG_FILES+=("$_logfile")
    _REG_PATHS+=("$_logfile")
    _REG_AGENTS+=("$_agent")
    _REG_PROJECTS+=("$_proj")
  done
done
unset _proj _proj_debug_dir _agent _logfile

# ---------------------------------------------------------------------------
# --stdout mode: print the last 30 lines of each debug log once, then exit.
# Provides a standalone snapshot of recent debug activity suitable for
# terminal inspection without a running tmux session or PGAI_VERBOSE_MODE.
# ---------------------------------------------------------------------------
if [[ "$STDOUT_MODE" == "true" ]]; then
  printf '=== Recent Debug Logs (last 30 lines each) ===\n'
  if [[ "${#VERBOSE_PROJECTS[@]}" -gt 0 ]]; then
    printf '  Verbose projects: %s\n' "${VERBOSE_PROJECTS[*]}"
  fi
  for _proj in "${VERBOSE_PROJECTS[@]}"; do
    _proj_debug_dir="${KANBAN_ROOT}/projects/${_proj}/logs/debug"
    for _agent in "${AGENT_ORDER[@]}"; do
      _logfile="${_proj_debug_dir}/${_agent}.log"
      _color="${AGENT_COLOR[$_agent]}"
      if [[ "$MULTI_PROJECT_MODE" == "true" ]]; then
        _hdr="${_proj}/${_agent}"
      else
        _hdr="$_agent"
      fi
      printf '\n%s--- %s ---%s\n' "${_color}" "$_hdr" "${C_RESET:-}"
      if [[ -f "$_logfile" ]]; then
        tail -n 30 "$_logfile" 2>/dev/null | \
          awk -v color="${_color}" -v reset="${C_RESET:-}" \
              -v agent="$_agent" -v proj="$_proj" \
              -v multi="$MULTI_PROJECT_MODE" \
            '{
              if (multi == "true") {
                printf "%s[%s/%s]%s %s\n", color, proj, agent, reset, $0
              } else {
                printf "%s[%s]%s %s\n", color, agent, reset, $0
              }
            }'
      else
        printf '  (no log file yet: %s)\n' "$_logfile"
      fi
    done
  done
  unset _proj _proj_debug_dir _agent _logfile _color _hdr
  exit 0
fi

# ---------------------------------------------------------------------------
# Warn if no verbose-project debug directories exist yet.
# tail -F tolerates missing files and waits for them to appear.
# ---------------------------------------------------------------------------
_any_dir_missing=false
for _proj in "${VERBOSE_PROJECTS[@]}"; do
  _dir="${KANBAN_ROOT}/projects/${_proj}/logs/debug"
  if [[ ! -d "$_dir" ]]; then
    echo "WARNING: debug log directory does not exist yet: ${_dir}" >&2
    _any_dir_missing=true
  fi
done
if [[ "$_any_dir_missing" == "true" ]]; then
  echo "         Waiting for directories to be created by agent wakes..." >&2
fi
unset _proj _dir _any_dir_missing

# ---------------------------------------------------------------------------
# Write per-file map to a temp file for awk.
# Format: full_path<TAB>agent_name<TAB>project_name<TAB>color_escape<TAB>reset_escape
# awk reads this to resolve (agent, project, color) from the tail -F header path.
# ---------------------------------------------------------------------------
_DASH_TEMP="$(pgai_temp_subdir dashboard)"
AGENT_MAP_FILE="$(mktemp "${_DASH_TEMP}/debug-logs-agents.XXXXXX")"
unset _DASH_TEMP
trap 'rm -f "$AGENT_MAP_FILE"' EXIT

_n="${#_REG_PATHS[@]}"
for (( _i=0; _i < _n; _i++ )); do
  _path="${_REG_PATHS[$_i]}"
  _agent="${_REG_AGENTS[$_i]}"
  _proj="${_REG_PROJECTS[$_i]}"
  _color="${AGENT_COLOR[$_agent]:-}"
  printf '%s\t%s\t%s\t%s\t%s\n' "$_path" "$_agent" "$_proj" "$_color" "$C_RESET" >> "$AGENT_MAP_FILE"
done
unset _n _i _path _agent _proj _color

# ---------------------------------------------------------------------------
# Build tail -F argument list — all (project x agent) paths, missing tolerated
# ---------------------------------------------------------------------------
TAIL_ARGS=("${ALL_LOG_FILES[@]}")

# ---------------------------------------------------------------------------
# Run tail -F and pipe through awk for agent tagging and colorization.
#
# tail -F prints "==> <filename> <==" when switching between files; awk uses
# these headers to look up the full path in the per-file map and prefixes
# each log line with [agent HH:MM:SS] (single project) or
# [project/agent HH:MM:SS] (multi-project) in the agent's color.
# ---------------------------------------------------------------------------
tail -F "${TAIL_ARGS[@]}" 2>/dev/null | \
awk \
  -v use_color="$USE_COLOR" \
  -v agent_map_file="$AGENT_MAP_FILE" \
  -v c_reset="$C_RESET" \
  -v c_dim="$C_DIM" \
  -v multi_project="$MULTI_PROJECT_MODE" \
  'BEGIN {
    # Load per-file map: full_path -> (agent, project, color, reset)
    while ((getline line < agent_map_file) > 0) {
      n = split(line, parts, "\t")
      if (n >= 5) {
        path_agent[parts[1]]   = parts[2]
        path_project[parts[1]] = parts[3]
        path_color[parts[1]]   = parts[4]
        path_reset[parts[1]]   = parts[5]
      } else if (n >= 3) {
        path_agent[parts[1]]   = parts[2]
        path_project[parts[1]] = parts[3]
        path_color[parts[1]]   = ""
        path_reset[parts[1]]   = ""
      }
    }
    close(agent_map_file)

    current_path    = ""
    current_agent   = ""
    current_project = ""
    current_color   = ""
    current_reset   = ""
  }
  {
    line = $0

    # Detect tail -F file-switch header: ==> /path/to/file <==
    if (line ~ /^==> .* <==$/) {
      path = line
      sub(/^==> /, "", path)
      sub(/ <==$/, "", path)
      current_path = path
      if (path in path_agent) {
        current_agent   = path_agent[path]
        current_project = path_project[path]
        current_color   = path_color[path]
        current_reset   = path_reset[path]
      } else {
        # Fallback: derive agent from basename
        n = split(path, parts, "/")
        basename = parts[n]
        sub(/\.log$/, "", basename)
        current_agent   = basename
        current_project = ""
        current_color   = ""
        current_reset   = ""
      }
      next
    }

    # Skip blank lines
    if (line ~ /^[[:space:]]*$/) next

    # Build tag label: [project/agent HH:MM:SS] in multi-project mode,
    # [agent HH:MM:SS] in single-project mode.
    cmd = "date +%H:%M:%S"
    cmd | getline ts
    close(cmd)

    if (multi_project == "true" && current_project != "") {
      label = current_project "/" current_agent
    } else {
      label = current_agent
    }
    tag = "[" label " " ts "]"

    if (use_color == "true" && current_color != "") {
      printf "%s%s%s %s\n", current_color, tag, current_reset, line
    } else {
      printf "%s %s\n", tag, line
    }
  }'

echo "(debug log stream ended — restart pane to reconnect)"
sleep 10
