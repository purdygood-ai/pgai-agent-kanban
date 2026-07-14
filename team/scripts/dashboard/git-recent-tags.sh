#!/usr/bin/env bash
# dashboard-git-recent-tags.sh
# Prints a Recent Tags listing for all registered release/feature projects.
#
# Designed to run under `watch -n 10 dashboard-git-recent-tags.sh` in the
# right pane of the tmux `git` window created by dashboard-create.sh.
#
# For each registered project whose workflow_type is "release" (or "feature"),
# a labeled section is rendered showing the 10 most recent semver tags
# (newest first), one per line with the tag date shown alongside each tag.
#
# Document-workflow projects (workflow_type=document) are omitted.
# A release project whose dev tree is missing or not a git repo degrades that
# project's section gracefully without breaking the window or affecting other
# projects' sections.
#
# Usage:
#   dashboard-git-recent-tags.sh [--kanban-root <path>] [--project <name>]
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

KANBAN_ROOT="${KANBAN_ROOT_ARG:-${PGAI_AGENT_KANBAN_ROOT_PATH}}"
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

# ---------------------------------------------------------------------------
# render_project_recent_tags <project_name> <dev_tree> <branch_prefix>
# Renders the recent tags block for one project. Gracefully degrades when
# the dev tree is missing or not a git repo — prints a warning and returns 0.
# ---------------------------------------------------------------------------
render_project_recent_tags() {
  local proj_name="$1"
  local dev_tree="$2"
  local branch_prefix="$3"

  # Project header
  printf '\n'
  printf '%s%s%s\n' "${CYAN}${BOLD}" "=== Recent Tags (newest first): ${proj_name} ===" "$RESET"
  printf '%s%s%s\n' "$DIM" "$(printf '─%.0s' {1..60})" "$RESET"
  printf '\n'

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
  # Build the git tag --list pattern:
  #   - When prefix is empty, use '*' to match all tags (passing '' matches nothing).
  #   - When prefix is set, use '${branch_prefix}*' to match only project-owned tags.
  # -------------------------------------------------------------------------
  local tag_pattern
  if [[ -z "$branch_prefix" ]]; then
    tag_pattern="*"
  else
    tag_pattern="${branch_prefix}*"
  fi

  # -------------------------------------------------------------------------
  # List the 10 most recent semver tags, newest first
  # Uses --sort=-version:refname so tags sort correctly (version sort handles
  # v0.2.0 > v0.10.0 correctly; naive string sort would not).
  # -------------------------------------------------------------------------
  local tags_output
  tags_output="$(git -C "$dev_tree" tag --list "$tag_pattern" --sort=-version:refname 2>/dev/null | head -10 || true)"

  if [[ -z "$tags_output" ]]; then
    printf '  %s(no tags found)%s\n' "$DIM" "$RESET"
  else
    while IFS= read -r tag; do
      [[ -z "$tag" ]] && continue
      # Get the tag date — dereference annotated tags to the tag object itself.
      # %(*objectname) is empty for lightweight tags; fall back to commit date.
      local tag_date
      tag_date="$(git -C "$dev_tree" log -1 --format="%ai" "${tag}" 2>/dev/null \
                  | cut -c1-10 || echo "unknown")"
      printf '  %s%-30s%s  %s%s%s\n' \
        "${GREEN}${BOLD}" "$tag" "$RESET" \
        "$DIM" "${tag_date:-unknown}" "$RESET"
    done <<< "$tags_output"
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
  printf '%s%s%s\n' "${YELLOW}${BOLD}" "=== Recent Tags ===" "$RESET"
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

  # Render the recent tags block for this project.
  render_project_recent_tags "$_proj" "$_dev_tree" "$_branch_prefix"
  _rendered_count=$(( _rendered_count + 1 ))
done

# If no release-workflow projects were found (all were document-workflow), emit a message.
if [[ "$_rendered_count" -eq 0 ]]; then
  printf '%s%s%s\n' "${YELLOW}${BOLD}" "=== Recent Tags ===" "$RESET"
  printf '\n'
  printf '%sNo release-workflow projects found in projects.cfg.%s\n' "$YELLOW" "$RESET"
  printf '%sDocument-workflow projects are excluded from the git window.%s\n' "$DIM" "$RESET"
fi

# ---------------------------------------------------------------------------
# Footer: timestamp
# ---------------------------------------------------------------------------
NOW="$(date '+%Y-%m-%d %H:%M:%S')"
printf '%supdated %s%s\n' "$DIM" "$NOW" "$RESET"
