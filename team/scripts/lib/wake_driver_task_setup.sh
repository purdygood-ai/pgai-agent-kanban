#!/usr/bin/env bash
# team/scripts/lib/wake_driver_task_setup.sh
#
# Wake task-setup stage: the provider-neutral primitives the shared wake
# pipeline reaches for when it starts on a task -- the task's own
# log/artifact directories, and the two task-README fields that decide how
# its working tree is prepared (## Workflow Type, ## Source Branch). One of
# the single-concern lib stages wake_driver.sh sources ahead of the worktree
# stage, kept separate so the driver stays under the engine-decomposition
# line ceiling (team/scripts/lint_engine_size_dupe.py). Behavior-preserving:
# no stdout line, exit code, or on-disk effect changes as a result of this
# file existing.
#
# Public entry points
# -------------------
#   setup_project_directories <task_log_dir> <task_artifact_dir>
#       Creates the per-task log and artifact directories. Returns 1, after
#       logging a FATAL line, when either cannot be created; process_one_task
#       (wake_driver.sh) reads that return to stop before touching the task.
#
#   get_workflow_type_from_readme <readme_path>
#       Prints the task README's ## Workflow Type value, lowercased (release,
#       feature, document, or a plugin type). Defaults to "release" when the
#       field is absent or empty, or the file is unreadable -- the
#       absent-field default roles/CODER.md documents. Called by the worktree
#       stage (wake_driver_worktree.sh).
#
#   get_source_branch_from_readme <readme_path>
#       Prints the task README's ## Source Branch value. Returns non-zero
#       with no output when the field is absent, empty, "none", or the file
#       is unreadable, so a caller can `|| echo none` or branch on it. Called
#       by the worktree stage (wake_driver_worktree.sh).
#
# Dependencies (must already be defined by the caller before these
# functions run)
# -----------------------------------------------------------------------
#   log() (wake_bootstrap.sh, via wake_common.sh) for the FATAL narration in
#   setup_project_directories. The two README readers read their section
#   through read_md_field (md_field.sh, loaded below from beside this file
#   unless the caller already defines it) and take the field's value: the
#   first line of the section, trimmed, with a whole-line **bold** or
#   __bold__ wrapper removed.
#
# wake/harness.sh carries its own self-contained copies of these three
# functions and sources neither this file nor wake_driver.sh.

# shellcheck source=md_field.sh
declare -F read_md_field >/dev/null || source "$(dirname "${BASH_SOURCE[0]}")/md_field.sh"

# ---------------------------------------------------------------------------
# setup_project_directories <task_log_dir> <task_artifact_dir>
# Create the per-task log/artifact directories. Provider-neutral.
# ---------------------------------------------------------------------------
setup_project_directories() {
  local task_log_dir="$1"
  local task_artifact_dir="$2"
  if ! mkdir -p "$task_log_dir" "$task_artifact_dir"; then
    log "FATAL: cannot create task directories: ${task_log_dir} ${task_artifact_dir}"
    log "       Check filesystem permissions, disk space, and read-only mounts."
    return 1
  fi
}

# --- _wd_readme_field <readme_path> <heading> ---
# Print the value of a task README's "## <heading>" field: the first line of
# the section (which ends at the next "##" line, "###" included), trimmed,
# with a whole-line **bold** or __bold__ wrapper removed; an empty line when
# the section is empty. Returns non-zero with no output when the heading is
# absent or the README cannot be read.
_wd_readme_field() {
  local value
  value="$(read_md_field --end-at-subheading "$1" "$2" 2>/dev/null)" || return 1
  value="${value%%$'\n'*}"
  value="${value%"${value##*[![:space:]]}"}"
  if [[ "$value" =~ ^\*\*(.+)\*\*$ || "$value" =~ ^__(.+)__$ ]]; then
    value="${BASH_REMATCH[1]}"
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
  fi
  printf '%s\n' "$value"
}

# --- get_workflow_type_from_readme <readme_path> ---
# Extract "## Workflow Type" from a task README.
# Prints the value (release, feature, or document) to stdout.
# Defaults to "release" when the field is absent or unreadable.
get_workflow_type_from_readme() {
  local readme="$1" value
  value="$(_wd_readme_field "$readme" "Workflow Type")" || value=""
  value="${value,,}"
  printf '%s\n' "${value:-release}"
}

# --- get_source_branch_from_readme <readme_path> ---
# Extract "## Source Branch" from a task README.
# Prints the value to stdout; returns non-zero with no output if field is absent.
get_source_branch_from_readme() {
  local readme="$1" value
  value="$(_wd_readme_field "$readme" "Source Branch")" || return 1
  [[ -n "$value" && "${value,,}" != "none" ]] || return 1
  printf '%s\n' "$value"
}
