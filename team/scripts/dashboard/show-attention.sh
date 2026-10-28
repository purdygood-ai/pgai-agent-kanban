#!/usr/bin/env bash
# show-attention.sh
# Renders the Window 3 "Attention" view showing BLOCKED tasks.
#
# Layout when tasks are blocked:
#   ┌─────────────────────────────────────────────────────────────────────────┐
#   │ ⚠ BLOCKED TASKS                                                          │
#   ├─────────────────────────────────────────────────────────────────────────┤
#   │                                                                          │
#   │ ▶ CLAUDE-CM-YYYYMMDD-NNN-release-vX-Y-Z                                  │
#   │   Blocked since: 14:23:18 (2m ago)                                       │
#   │   Reason: cm/release.sh exited 1 at Step 4 ...                           │
#   │   Next step: Update main's release-state.md ...                         │
#   └─────────────────────────────────────────────────────────────────────────┘
#
# Layout when nothing blocked:
#   ⚠ BLOCKED TASKS
#
#   (no blocked tasks — system running normally)
#
# Usage:
#   show-attention.sh [--kanban-root <path>] [-h|--help]
#
# Auto-refresh is handled by the caller (dashboard/create.sh uses watch -n N).
#
# Environment:
#   NO_COLOR=1 — disables all ANSI codes
#   TERM=dumb  — disables all ANSI codes

# -h/--help prints the header comment above and exits 0. It is handled before
# env_bootstrap.sh so help never resolves a kanban root, reads an argument as
# data, or starts the script's own work.
for __help_arg in "$@"; do
  case "$__help_arg" in
    -h|--help)
      # shellcheck source=../lib/header_help.sh
      source "$(dirname "${BASH_SOURCE[0]}")/../lib/header_help.sh"
      header_help_print "${BASH_SOURCE[0]}"
      exit 0
      ;;
  esac
done

# shellcheck source=../lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/env_bootstrap.sh"

# --- Resolve script dir ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source project_paths lib for pp_* helpers
# shellcheck source=lib/project_paths.sh
source "${SCRIPT_DIR}/../lib/project_paths.sh"
# Source projects lib for projects_cfg_list (resolve project from registry)
# shellcheck source=lib/projects.sh
source "${SCRIPT_DIR}/../lib/projects.sh"
# shellcheck source=lib/dev_tree.sh
source "${SCRIPT_DIR}/../lib/dev_tree.sh"
# shellcheck source=lib/script_common.sh
source "${SCRIPT_DIR}/../lib/script_common.sh"
# pp_run_ops: the renderer below runs from this tree's own pgai_agent_kanban
# package.
# shellcheck source=../lib/pp_run_ops.sh
source "${SCRIPT_DIR}/../lib/pp_run_ops.sh"

# --- Pass through args ---
DATA_ARGS=("$@")

# --- Source config (non-strict) ---
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
_args=("$@")
_i=0
while [[ $_i -lt ${#_args[@]} ]]; do
  if [[ "${_args[$_i]}" == "--kanban-root" ]]; then
    _next=$(( _i + 1 ))
    KANBAN_ROOT="${_args[$_next]:-$KANBAN_ROOT}"
    break
  fi
  _i=$(( _i + 1 ))
done
# Operator user config, then [chain] pm_mode (config_loader.sh).
# shellcheck source=../lib/config_loader.sh
source "${SCRIPT_DIR}/../lib/config_loader.sh"
config_startup_prelude "$KANBAN_ROOT"
export PGAI_DEV_TREE_PATH="${PGAI_DEV_TREE_PATH:-$(resolve_global_dev_tree)}"

set -euo pipefail

# --- ANSI color support ---
USE_COLOR=true
if [[ "${TERM:-}" == "dumb" ]] || [[ "${NO_COLOR:-}" == "1" ]]; then
  USE_COLOR=false
fi

# ansi_code() is defined in lib/script_common.sh (sourced above).
RESET="$(ansi_code reset)"
C_BOLD="$(ansi_code bold)"
C_DIM="$(ansi_code dim)"
C_RED="$(ansi_code red)"
C_CYAN="$(ansi_code cyan)"
C_YELLOW="$(ansi_code yellow)"
C_GREEN="$(ansi_code green)"

# --- Header ---
if [[ "$USE_COLOR" == "true" ]]; then
  printf '%s%s⚠ BLOCKED TASKS%s\n' "$C_RED" "$C_BOLD" "$RESET"
else
  printf '! BLOCKED TASKS\n'
fi
echo ""

# --- Render blocked tasks ---
# Iterate all registered projects (iterate-all).
# The renderer is called once per project so blocked tasks from every
# registered project are shown.  On a single-project install the loop runs
# once and behaviour is unchanged.  When no projects are registered the
# renderer receives "" for the tasks directory.  task_panes reads every ##
# section through pgai_agent_kanban.task_status in one interpreter per
# project, where a read_md_field call per status.md would start one per task.
_sha_task_roots=()
while IFS= read -r _sha_project; do
    [[ -z "$_sha_project" ]] && continue
    _sha_dir="$(KANBAN_ROOT="$KANBAN_ROOT" pp_tasks_dir "$_sha_project" 2>/dev/null || true)"
    [[ -n "$_sha_dir" ]] && _sha_task_roots+=("$_sha_dir")
done < <(KANBAN_ROOT="$KANBAN_ROOT" projects_cfg_list 2>/dev/null || true)
if [[ ${#_sha_task_roots[@]} -eq 0 ]]; then
    _sha_task_roots=("")
fi
for _sha_tasks_dir in "${_sha_task_roots[@]}"; do
  pp_run_ops pgai_agent_kanban.dashboard.task_panes attention-blocked "${_sha_tasks_dir}" \
    --use-color "$USE_COLOR" --reset "$RESET" --red "$C_RED" --bold "$C_BOLD" \
    --dim "$C_DIM" --cyan "$C_CYAN" --yellow "$C_YELLOW"
done
