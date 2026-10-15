#!/usr/bin/env bash
# git-status.sh
# Prints a multi-section git status view for all registered release-workflow projects.
#
# Designed to run under `watch -n 10 git-status.sh` in the
# tmux `git` window created by dashboard/create.sh.
#
# For each registered project whose workflow_type is "release" (or "feature"),
# a labeled section is rendered showing:
#   1. Dev tree path + current branch + sync status against the project's
#      remote (project.cfg [project] git_remote_name, origin when absent or
#      blank) for the current branch and <branch_prefix>main
#   2. Uncommitted changes summary (staged + unstaged counts)
#   3. In-flight rc/* branches (filtered by branch_prefix when configured)
#   4. Recent commits on main (top 5)
#   5. Recent tags (last 5, filtered by branch_prefix when configured)
#
# Document-workflow projects (workflow_type=document) are omitted.
# A release project whose dev tree is missing, unconfigured, or not a git
# repo renders a clear-error line ("git tree not available: <path>") for
# that project's section and continues to the next project, without
# breaking the window or performing any network git operation.
#
# All git data rendered by this script comes from the local dev tree only.
# No git subcommand invoked here ever reaches the network (no fetch, pull,
# push, clone, or ls-remote) — including the remote sync comparison, which
# reads the already-resolved local <remote>/<branch> remote-tracking ref
# rather than contacting the remote.
#
# Usage:
#   git-status.sh [--kanban-root <path>] [--project <name>]
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
# shellcheck source=../lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/env_bootstrap.sh"

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
      # shellcheck source=../lib/header_help.sh
      source "$(dirname "${BASH_SOURCE[0]}")/../lib/header_help.sh"
      header_help_print "${BASH_SOURCE[0]}"
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

KANBAN_ROOT="${KANBAN_ROOT_ARG:-${PGAI_AGENT_KANBAN_ROOT_PATH}}"
export KANBAN_ROOT

# Source project_paths lib for pp_* helpers and _pp_* internals.
# shellcheck source=../lib/project_paths.sh
source "${LIB_DIR}/project_paths.sh"
# Source projects lib for projects_cfg_list.
# shellcheck source=../lib/projects.sh
source "${LIB_DIR}/projects.sh"
# Source shared ANSI color primitive (single home for the color-name ->
# escape-code table -- see team/scripts/lib/script_common.sh).
# shellcheck source=../lib/script_common.sh
source "${LIB_DIR}/script_common.sh"

# ---------------------------------------------------------------------------
# ANSI color support (honor NO_COLOR and TERM=dumb)
# ---------------------------------------------------------------------------
USE_COLOR=true
if [[ -n "${NO_COLOR:-}" ]] || [[ "${TERM:-}" == "dumb" ]]; then
  USE_COLOR=false
fi

RESET="$(ansi_code reset)"
BOLD="$(ansi_code bold)"
DIM="$(ansi_code dim)"
CYAN="$(ansi_code cyan)"
GREEN="$(ansi_code green)"
YELLOW="$(ansi_code yellow)"
RED="$(ansi_code red)"
MAGENTA="$(ansi_code magenta)"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# section_header --rule <title>: title over a dim 60-char rule
# (lib/dashboard_common.sh).
# shellcheck source=../lib/dashboard_common.sh
source "${LIB_DIR}/dashboard_common.sh"

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
# render_project_git_status <project_name> <dev_tree> <branch_prefix> <remote>
# Renders the full git status for one project. Gracefully degrades when
# the dev tree is missing or not a git repo — prints a warning and returns 0.
# <remote> is the project's git remote (pp_git_remote_name); empty when its
# git_remote_name is invalid, which renders a one-line note in place of the
# sync status.
# ---------------------------------------------------------------------------
render_project_git_status() {
  local proj_name="$1"
  local dev_tree="$2"
  local branch_prefix="$3"
  local remote="${4:-}"
  local main_branch="${branch_prefix}main"

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
  # Validate dev tree — clear-error render, never a prompt or hang.
  # -------------------------------------------------------------------------
  if [[ -z "$dev_tree" ]]; then
    printf '%sgit tree not available: (dev tree path not configured for project %s)%s\n' "$YELLOW" "$proj_name" "$RESET"
    printf '\n'
    return 0
  fi

  if [[ ! -d "$dev_tree" ]]; then
    printf '%sgit tree not available: %s%s\n' "$YELLOW" "$dev_tree" "$RESET"
    printf '\n'
    return 0
  fi

  # Verify it is a git repository (local check only — never touches network)
  if ! git -C "$dev_tree" rev-parse --git-dir &>/dev/null; then
    printf '%sgit tree not available: %s%s\n' "$YELLOW" "$dev_tree" "$RESET"
    printf '\n'
    return 0
  fi

  # -------------------------------------------------------------------------
  # Section 1: Current branch + sync status
  # -------------------------------------------------------------------------
  section_header --rule "Branch"

  local current_branch
  current_branch="$(git -C "$dev_tree" symbolic-ref --short HEAD 2>/dev/null || echo "(detached HEAD)")"

  # Sync status for a branch vs the project's remote
  _sync_status() {
    local _branch="$1"
    if [[ -z "$remote" ]]; then
      printf '%s(git_remote_name in project.cfg is not a valid remote name)%s' "$YELLOW" "$RESET"
      return
    fi
    local _remote_ref
    _remote_ref="$(git -C "$dev_tree" rev-parse --verify "${remote}/${_branch}" 2>/dev/null || echo "")"
    if [[ -z "$_remote_ref" ]]; then
      printf '%s(no remote tracking branch)%s' "$DIM" "$RESET"
      return
    fi
    local _ahead _behind
    _ahead="$(git -C "$dev_tree" rev-list "${remote}/${_branch}..${_branch}" --count 2>/dev/null || echo 0)"
    _behind="$(git -C "$dev_tree" rev-list "${_branch}..${remote}/${_branch}" --count 2>/dev/null || echo 0)"
    if [[ "$_ahead" -eq 0 && "$_behind" -eq 0 ]]; then
      printf '%sin sync with %s/%s%s' "$GREEN" "$remote" "$_branch" "$RESET"
    elif [[ "$_ahead" -gt 0 && "$_behind" -eq 0 ]]; then
      printf '%s%d ahead of %s/%s%s' "$YELLOW" "$_ahead" "$remote" "$_branch" "$RESET"
    elif [[ "$_ahead" -eq 0 && "$_behind" -gt 0 ]]; then
      printf '%s%d behind %s/%s%s' "$YELLOW" "$_behind" "$remote" "$_branch" "$RESET"
    else
      printf '%s%d ahead, %d behind %s/%s%s' "$RED" "$_ahead" "$_behind" "$remote" "$_branch" "$RESET"
    fi
  }

  printf '  Current:  %s%s%s  ' "${BOLD}" "$current_branch" "$RESET"
  _sync_status "$current_branch"
  printf '\n'

  # Also show the project's main branch (<branch_prefix>main) sync status
  if [[ "$current_branch" != "$main_branch" ]]; then
    if git -C "$dev_tree" rev-parse --verify "$main_branch" &>/dev/null; then
      printf '  %-10s' "${main_branch}:"
      _sync_status "$main_branch"
      printf '\n'
    fi
  fi

  printf '\n'

  # -------------------------------------------------------------------------
  # Section 2: Uncommitted changes
  # -------------------------------------------------------------------------
  section_header --rule "Uncommitted Changes"

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
  section_header --rule "In-flight RC Branches"

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
  # Section 4: Recent commits on main (top 5)
  # -------------------------------------------------------------------------
  section_header --rule "Recent Commits (main, last 5)"

  if git -C "$dev_tree" rev-parse --verify main &>/dev/null; then
    git -C "$dev_tree" log main --oneline -5 --format="%C(dim)%h%Creset  %s" 2>/dev/null \
      | while IFS= read -r line; do
          printf '  %s\n' "$line"
        done
  else
    printf '  %s(main branch not found)%s\n' "$DIM" "$RESET"
  fi

  printf '\n'

  # -------------------------------------------------------------------------
  # Section 5: Recent tags (last 5, filtered by branch_prefix)
  # -------------------------------------------------------------------------
  section_header --rule "Recent Tags (last 5)"

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

  # Resolve the project's git remote (origin when git_remote_name is absent
  # or blank; empty when it is invalid -- a render never refuses).
  _git_remote="$(pp_git_remote_name --quiet "$_proj" 2>/dev/null)" || _git_remote=""

  # Render the project header and git status.
  project_header "$_proj" "$_dev_tree"
  render_project_git_status "$_proj" "$_dev_tree" "$_branch_prefix" "$_git_remote"
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
