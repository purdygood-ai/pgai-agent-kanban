#!/usr/bin/env bash
# dashboard-git-status.sh
# Prints a multi-section git status view for all registered release-workflow projects.
#
# Designed to run under `watch -n 10 dashboard-git-status.sh` in the
# tmux `git` window created by dashboard-create.sh.
#
# For each registered project whose workflow_type is "release" (or "feature"),
# a labeled section is rendered showing:
#   1. Dev tree path + current branch + sync-with-origin status
#   2. Uncommitted changes summary (staged + unstaged counts)
#   3. In-flight rc/* branches (filtered by branch_prefix when configured)
#   4. Recent commits on develop (top 5)
#   5. Recent tags (last 5, filtered by branch_prefix when configured)
#
# Document-workflow projects (workflow_type=document) are omitted.
# A release project whose dev tree is missing or not a git repo degrades that
# project's section gracefully (shows a warning) without breaking the window
# or affecting other projects' sections.
#
# Usage:
#   dashboard-git-status.sh [--kanban-root <path>] [--project <name>]
#
# Options:
#   --kanban-root <path>  Override the kanban root (default: $PGAI_AGENT_KANBAN_ROOT_PATH)
#   --project <name>      Render only this project (omits all others). Useful for testing.
#   -h, --help            Show this help and exit
#
# Environment:
#   PGAI_AGENT_KANBAN_ROOT_PATH   Path to the kanban root
#   NO_COLOR                      Set non-empty to disable ANSI colors
#   TERM=dumb                     Also disables ANSI colors
#
# When a project's dev tree path does not exist or is not a git repository, the
# script prints a clear degraded section for that project and continues to the
# next project.  The overall exit code is always 0 so `watch` does not spin.

set -euo pipefail

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
KANBAN_ROOT_ARG=""
PROJECT_FILTER=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --kanban-root)
      KANBAN_ROOT_ARG="${2:-}"
      shift 2
      ;;
    --project)
      PROJECT_FILTER="${2:-}"
      shift 2
      ;;
    -h|--help)
      grep '^#' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "ERROR: Unknown argument: $1" >&2
      echo "Usage: $0 [--kanban-root <path>] [--project <name>]" >&2
      exit 1
      ;;
  esac
done

# ---------------------------------------------------------------------------
# Resolve kanban root and load project_paths + projects helpers
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB_DIR="${SCRIPT_DIR}/../lib"

KANBAN_ROOT="${KANBAN_ROOT_ARG:-${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}}"
export KANBAN_ROOT

# Source project_paths lib for pp_* helpers and _pp_* internals.
# shellcheck source=../lib/project_paths.sh
source "${LIB_DIR}/project_paths.sh"
# Source projects lib for projects_cfg_list.
# shellcheck source=../lib/projects.sh
source "${LIB_DIR}/projects.sh"

# ---------------------------------------------------------------------------
# ANSI color support (honor NO_COLOR and TERM=dumb)
# ---------------------------------------------------------------------------
USE_COLOR=true
if [[ -n "${NO_COLOR:-}" ]] || [[ "${TERM:-}" == "dumb" ]]; then
  USE_COLOR=false
fi

c() {
  # c <color-name> — emit ANSI code or empty string
  if [[ "$USE_COLOR" != "true" ]]; then echo ""; return; fi
  case "$1" in
    bold)    printf '\033[1m' ;;
    dim)     printf '\033[2m' ;;
    reset)   printf '\033[0m' ;;
    cyan)    printf '\033[0;36m' ;;
    green)   printf '\033[0;32m' ;;
    yellow)  printf '\033[0;33m' ;;
    red)     printf '\033[0;31m' ;;
    blue)    printf '\033[0;34m' ;;
    white)   printf '\033[0;37m' ;;
    magenta) printf '\033[0;35m' ;;
    *)       printf '' ;;
  esac
}

RESET="$(c reset)"
BOLD="$(c bold)"
DIM="$(c dim)"
CYAN="$(c cyan)"
GREEN="$(c green)"
YELLOW="$(c yellow)"
RED="$(c red)"
MAGENTA="$(c magenta)"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

section_header() {
  local title="$1"
  printf '%s%s%s\n' "${CYAN}${BOLD}" "$title" "$RESET"
  printf '%s%s%s\n' "$DIM" "$(printf '─%.0s' {1..60})" "$RESET"
}

project_header() {
  local proj_name="$1"
  local dev_tree="$2"
  printf '\n'
  printf '%s%s%s\n' "${MAGENTA}${BOLD}" "=== Git Status: ${proj_name} ===" "$RESET"
  printf '%s%s%s\n' "$DIM" "$(printf '═%.0s' {1..60})" "$RESET"
  if [[ -n "$dev_tree" ]]; then
    printf '%sDev tree: %s%s\n' "$DIM" "$dev_tree" "$RESET"
  fi
  printf '\n'
}

# ---------------------------------------------------------------------------
# render_project_git_status <project_name> <dev_tree> <branch_prefix>
# Renders the full git status for one project. Gracefully degrades when
# the dev tree is missing or not a git repo — prints a warning and returns 0.
# ---------------------------------------------------------------------------
render_project_git_status() {
  local proj_name="$1"
  local dev_tree="$2"
  local branch_prefix="$3"

  # RC branch list pattern: ${PREFIX}rc/* or rc/* when prefix is empty.
  local rc_pattern="${branch_prefix}rc/*"

  # Tag list pattern for git tag --list:
  #   - When prefix is empty, use '*' (passing '' to --list matches nothing).
  #   - When prefix is set, use '${PREFIX}*'.
  local tag_pattern
  if [[ -z "$branch_prefix" ]]; then
    tag_pattern="*"
  else
    tag_pattern="${branch_prefix}*"
  fi

  # -------------------------------------------------------------------------
  # Validate dev tree — degrade gracefully without breaking the window
  # -------------------------------------------------------------------------
  if [[ -z "$dev_tree" ]]; then
    printf '%sdev tree path not configured for project %s%s\n' "$YELLOW" "$proj_name" "$RESET"
    printf '\n'
    return 0
  fi

  if [[ ! -d "$dev_tree" ]]; then
    printf '%sdev tree unavailable: %s%s\n' "$YELLOW" "$dev_tree" "$RESET"
    printf '\n'
    return 0
  fi

  # Verify it is a git repository
  if ! git -C "$dev_tree" rev-parse --git-dir &>/dev/null; then
    printf '%sdev tree at %s is not a git repository%s\n' "$YELLOW" "$dev_tree" "$RESET"
    printf '\n'
    return 0
  fi

  # -------------------------------------------------------------------------
  # Section 1: Current branch + sync status
  # -------------------------------------------------------------------------
  section_header "Branch"

  local current_branch
  current_branch="$(git -C "$dev_tree" symbolic-ref --short HEAD 2>/dev/null || echo "(detached HEAD)")"

  # Sync status for a branch vs origin
  _sync_status() {
    local _branch="$1"
    local _remote_ref
    _remote_ref="$(git -C "$dev_tree" rev-parse --verify "origin/${_branch}" 2>/dev/null || echo "")"
    if [[ -z "$_remote_ref" ]]; then
      printf '%s(no remote tracking branch)%s' "$DIM" "$RESET"
      return
    fi
    local _ahead _behind
    _ahead="$(git -C "$dev_tree" rev-list "origin/${_branch}..${_branch}" --count 2>/dev/null || echo 0)"
    _behind="$(git -C "$dev_tree" rev-list "${_branch}..origin/${_branch}" --count 2>/dev/null || echo 0)"
    if [[ "$_ahead" -eq 0 && "$_behind" -eq 0 ]]; then
      printf '%sin sync with origin/%s%s' "$GREEN" "$_branch" "$RESET"
    elif [[ "$_ahead" -gt 0 && "$_behind" -eq 0 ]]; then
      printf '%s%d ahead of origin/%s%s' "$YELLOW" "$_ahead" "$_branch" "$RESET"
    elif [[ "$_ahead" -eq 0 && "$_behind" -gt 0 ]]; then
      printf '%s%d behind origin/%s%s' "$YELLOW" "$_behind" "$_branch" "$RESET"
    else
      printf '%s%d ahead, %d behind origin/%s%s' "$RED" "$_ahead" "$_behind" "$_branch" "$RESET"
    fi
  }

  printf '  Current:  %s%s%s  ' "${BOLD}" "$current_branch" "$RESET"
  _sync_status "$current_branch"
  printf '\n'

  # Also show develop sync status if we're not already on develop
  if [[ "$current_branch" != "develop" ]]; then
    if git -C "$dev_tree" rev-parse --verify develop &>/dev/null; then
      printf '  develop:  '
      _sync_status develop
      printf '\n'
    fi
  fi

  # Also show main sync status
  if [[ "$current_branch" != "main" ]]; then
    if git -C "$dev_tree" rev-parse --verify main &>/dev/null; then
      printf '  main:     '
      _sync_status main
      printf '\n'
    fi
  fi

  printf '\n'

  # -------------------------------------------------------------------------
  # Section 2: Uncommitted changes
  # -------------------------------------------------------------------------
  section_header "Uncommitted Changes"

  local status_output
  status_output="$(git -C "$dev_tree" status --porcelain 2>/dev/null)"

  if [[ -z "$status_output" ]]; then
    printf '  %snone%s\n' "$GREEN" "$RESET"
  else
    local staged=0 unstaged=0 untracked=0
    while IFS= read -r line; do
      local X Y
      X="${line:0:1}"
      Y="${line:1:1}"
      if [[ "$X" != " " && "$X" != "?" ]]; then
        staged=$(( staged + 1 ))
      fi
      if [[ "$Y" == "M" || "$Y" == "D" ]]; then
        unstaged=$(( unstaged + 1 ))
      fi
      if [[ "$X" == "?" && "$Y" == "?" ]]; then
        untracked=$(( untracked + 1 ))
      fi
    done <<< "$status_output"

    [[ "$staged"    -gt 0 ]] && printf '  %s%d staged%s\n'    "$YELLOW" "$staged"    "$RESET"
    [[ "$unstaged"  -gt 0 ]] && printf '  %s%d unstaged%s\n'  "$YELLOW" "$unstaged"  "$RESET"
    [[ "$untracked" -gt 0 ]] && printf '  %s%d untracked%s\n' "$DIM"    "$untracked" "$RESET"
  fi

  printf '\n'

  # -------------------------------------------------------------------------
  # Section 3: In-flight rc/* branches (filtered by branch_prefix)
  # -------------------------------------------------------------------------
  section_header "In-flight RC Branches"

  local rc_branches
  rc_branches="$(git -C "$dev_tree" branch --list "$rc_pattern" --format='%(refname:short)' 2>/dev/null || true)"

  if [[ -z "$rc_branches" ]]; then
    printf '  %snone%s\n' "$DIM" "$RESET"
  else
    while IFS= read -r branch; do
      [[ -z "$branch" ]] && continue
      local branch_date
      branch_date="$(git -C "$dev_tree" log --format="%ar" -1 "$branch" 2>/dev/null || echo "unknown")"
      printf '  %s%s%s  %s(%s)%s\n' "$YELLOW" "$branch" "$RESET" "$DIM" "$branch_date" "$RESET"
    done <<< "$rc_branches"
  fi

  printf '\n'

  # -------------------------------------------------------------------------
  # Section 4: Recent commits on develop (top 5)
  # -------------------------------------------------------------------------
  section_header "Recent Commits (develop, last 5)"

  if git -C "$dev_tree" rev-parse --verify develop &>/dev/null; then
    git -C "$dev_tree" log develop --oneline -5 --format="%C(dim)%h%Creset  %s" 2>/dev/null \
      | while IFS= read -r line; do
          printf '  %s\n' "$line"
        done
  else
    printf '  %s(develop branch not found)%s\n' "$DIM" "$RESET"
  fi

  printf '\n'

  # -------------------------------------------------------------------------
  # Section 5: Recent tags (last 5, filtered by branch_prefix)
  # -------------------------------------------------------------------------
  section_header "Recent Tags (last 5)"

  local recent_tags
  recent_tags="$(git -C "$dev_tree" tag --list "$tag_pattern" --sort=-version:refname 2>/dev/null | head -5 || true)"

  if [[ -z "$recent_tags" ]]; then
    printf '  %s(no tags)%s\n' "$DIM" "$RESET"
  else
    local tags_line
    tags_line="$(echo "$recent_tags" | tr '\n' ' ' | sed 's/ $//')"
    printf '  %s%s%s\n' "$GREEN" "$tags_line" "$RESET"
  fi

  printf '\n'
}

# ---------------------------------------------------------------------------
# Main: enumerate registered projects, filter to release/feature, render each
# ---------------------------------------------------------------------------

# Build the list of projects to render.
# If --project was given, render only that project; otherwise render all.
_PROJECTS=()
if [[ -n "$PROJECT_FILTER" ]]; then
  _PROJECTS=("$PROJECT_FILTER")
else
  while IFS= read -r _p; do
    [[ -z "$_p" ]] && continue
    _PROJECTS+=("$_p")
  done < <(KANBAN_ROOT="$KANBAN_ROOT" projects_cfg_list 2>/dev/null || true)
fi

# If no projects found at all (empty registry or no projects.cfg), emit a
# single informational message and exit 0 so watch does not spin on error.
if [[ ${#_PROJECTS[@]} -eq 0 ]]; then
  printf '%s%s%s\n' "${YELLOW}${BOLD}" "=== Git Status ===" "$RESET"
  printf '\n'
  printf '%sNo projects registered in projects.cfg.%s\n' "$YELLOW" "$RESET"
  printf '%sRegister a project via scripts/create-project.sh or add-project.sh.%s\n' "$DIM" "$RESET"
  exit 0
fi

_rendered_count=0
for _proj in "${_PROJECTS[@]}"; do
  [[ -z "$_proj" ]] && continue

  # Resolve the project config file via pp layer (handles both project.cfg and PROJECT.cfg)
  _proj_root="${KANBAN_ROOT}/projects/${_proj}"
  _proj_cfg_file="$(_pp_project_cfg_file "${_proj_root}" 2>/dev/null || true)"

  # Read workflow_type from config (default: release)
  _wf_type="release"
  if [[ -n "$_proj_cfg_file" && -f "$_proj_cfg_file" ]]; then
    _wf_type="$(_pp_read_cfg_key "$_proj_cfg_file" project workflow_type "release" 2>/dev/null || echo "release")"
  fi

  # Skip document-workflow projects — they have no git lifecycle.
  if [[ "$_wf_type" == "document" ]]; then
    continue
  fi

  # Resolve dev_tree_path from project config.
  _dev_tree=""
  if [[ -n "$_proj_cfg_file" && -f "$_proj_cfg_file" ]]; then
    _dev_tree="$(_pp_read_cfg_key "$_proj_cfg_file" project dev_tree_path "" 2>/dev/null || true)"
  fi

  # Resolve branch_prefix for this project (best-effort; empty = no prefix).
  _branch_prefix=""
  if [[ -n "$_proj_cfg_file" && -f "$_proj_cfg_file" ]]; then
    _raw_prefix="$(_pp_read_cfg_key "$_proj_cfg_file" project branch_prefix "" 2>/dev/null || true)"
    # Strip surrounding double-quotes (mirrors pp_branch_prefix strip treatment).
    _raw_prefix="${_raw_prefix%\"}"
    _raw_prefix="${_raw_prefix#\"}"
    _branch_prefix="$_raw_prefix"
  fi

  # Render the project header and git status.
  project_header "$_proj" "$_dev_tree"
  render_project_git_status "$_proj" "$_dev_tree" "$_branch_prefix"
  _rendered_count=$(( _rendered_count + 1 ))
done

# If no release-workflow projects were found (all were document-workflow), emit a message.
if [[ "$_rendered_count" -eq 0 ]]; then
  printf '%s%s%s\n' "${YELLOW}${BOLD}" "=== Git Status ===" "$RESET"
  printf '\n'
  printf '%sNo release-workflow projects found in projects.cfg.%s\n' "$YELLOW" "$RESET"
  printf '%sDocument-workflow projects are excluded from the git window.%s\n' "$DIM" "$RESET"
fi

# ---------------------------------------------------------------------------
# Footer: timestamp
# ---------------------------------------------------------------------------
NOW="$(date '+%Y-%m-%d %H:%M:%S')"
printf '%supdated %s%s\n' "$DIM" "$NOW" "$RESET"
