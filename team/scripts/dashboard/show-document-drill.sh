#!/usr/bin/env bash
# show-document-drill.sh
# Renders the document-workflow drill pane for a registered document project.
#
# This script is the document analog of the release drill's combined
# RC-state + pipeline pane. It renders two sections:
#
#   ARTIFACT LIBRARY — lists versioned deliverables from projects/<name>/artifacts/
#                      newest version first, with file basenames.
#   PIPELINE PROGRESS — shows current task states across the document pipeline
#                       (WRITER outline/draft/polish, TESTER review, CM finalize).
#
# Example output:
#   === pgai-three-bears — Artifact Library ===
#   v0.0.2-polished.md   (current)
#   v0.0.1-polished.md
#   (2 artifacts)
#
#   === Document Pipeline ===
#   writer:  2/3 done, 1 working
#   tester:  0/1 (waiting)
#   cm:      0/1 (waiting)
#
# Usage:
#   show-document-drill.sh --project <name> [--kanban-root <path>]
#
# Flags:
#   --project <name>     Project name (required)
#   --kanban-root <path> Override kanban root path
#   -h, --help           Show this help and exit
#
# Environment:
#   PGAI_AGENT_KANBAN_ROOT_PATH  — kanban root (default: ~/pgai_agent_kanban)
#   TERM=dumb                    — disables ANSI codes
#   NO_COLOR=1                   — disables ANSI codes

set -euo pipefail
# shellcheck source=../lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/env_bootstrap.sh"

# ---------------------------------------------------------------------------
# Resolve script dir
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source project_paths lib for pp_* helpers
# shellcheck source=lib/project_paths.sh
source "${SCRIPT_DIR}/../lib/project_paths.sh"

# Source script_common.sh for the shared ansi_code() helper.
# shellcheck source=lib/script_common.sh
source "${SCRIPT_DIR}/../lib/script_common.sh"
# pp_run_ops: the drill renderer runs from this tree's own pgai_agent_kanban
# package.
# shellcheck source=../lib/pp_run_ops.sh
source "${SCRIPT_DIR}/../lib/pp_run_ops.sh"

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
PROJECT_NAME=""
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project)
      PROJECT_NAME="${2:-}"
      shift 2
      ;;
    --kanban-root)
      KANBAN_ROOT="${2:-$KANBAN_ROOT}"
      shift 2
      ;;
    -h|--help)
      # shellcheck source=../lib/header_help.sh
      source "$(dirname "${BASH_SOURCE[0]}")/../lib/header_help.sh"
      header_help_print "${BASH_SOURCE[0]}"
      exit 0
      ;;
    *)
      # Ignore unknown flags silently (forward-compat)
      shift
      ;;
  esac
done

if [[ -z "$PROJECT_NAME" ]]; then
  echo "ERROR: --project <name> is required." >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Source config (non-strict)
# ---------------------------------------------------------------------------
if [[ -f "${KANBAN_ROOT}/kanban.cfg" ]]; then
  export PGAI_DASHBOARD_COLOR_DONE="${PGAI_DASHBOARD_COLOR_DONE:-$(read_ini "${KANBAN_ROOT}/kanban.cfg" dashboard color_done green)}"
  export PGAI_DASHBOARD_COLOR_WORKING="${PGAI_DASHBOARD_COLOR_WORKING:-$(read_ini "${KANBAN_ROOT}/kanban.cfg" dashboard color_working yellow)}"
  export PGAI_DASHBOARD_COLOR_BLOCKED="${PGAI_DASHBOARD_COLOR_BLOCKED:-$(read_ini "${KANBAN_ROOT}/kanban.cfg" dashboard color_blocked red)}"
  export PGAI_DASHBOARD_COLOR_LABEL="${PGAI_DASHBOARD_COLOR_LABEL:-$(read_ini "${KANBAN_ROOT}/kanban.cfg" dashboard color_label cyan)}"
fi

# ---------------------------------------------------------------------------
# ANSI color support
# ---------------------------------------------------------------------------
USE_COLOR=true
if [[ "${TERM:-}" == "dumb" ]] || [[ "${NO_COLOR:-}" == "1" ]]; then
  USE_COLOR=false
fi

# ansi_code() is defined in lib/script_common.sh (sourced above).
RESET="$(ansi_code reset)"
C_DONE="$(ansi_code "${PGAI_DASHBOARD_COLOR_DONE:-green}")"
C_WORK="$(ansi_code "${PGAI_DASHBOARD_COLOR_WORKING:-yellow}")"
C_BLCK="$(ansi_code "${PGAI_DASHBOARD_COLOR_BLOCKED:-red}")"
C_LABL="$(ansi_code "${PGAI_DASHBOARD_COLOR_LABEL:-cyan}")"
C_BOLD="$(ansi_code bold)"
C_DIM="$(ansi_code dim)"

# ---------------------------------------------------------------------------
# Section 1: Artifact version library
#
# Lists basenames from projects/<name>/artifacts/, sorted newest-first by
# filename (version-aware descending sort). The first entry is labeled
# "(current)".  A summary line shows total artifact count.
# ---------------------------------------------------------------------------

ARTIFACTS_DIR="${KANBAN_ROOT}/projects/${PROJECT_NAME}/artifacts"

printf '%s=== %s — Artifact Library ===%s\n' "${C_LABL}${C_BOLD}" "${PROJECT_NAME}" "${RESET}"

if [[ ! -d "$ARTIFACTS_DIR" ]]; then
  printf '%s(artifacts/ directory not found)%s\n' "${C_DIM}" "${RESET}"
else
  # Collect artifact basenames, sorted reverse-version order so newest is first.
  # Use version sort (-V) in descending order to handle vX.Y.Z prefixes correctly.
  mapfile -t _ARTIFACTS < <(
    ls -1 "$ARTIFACTS_DIR" 2>/dev/null \
      | sort -Vr \
      | grep -v '^\..*'
  )

  if [[ ${#_ARTIFACTS[@]} -eq 0 ]]; then
    printf '%s(no artifacts yet)%s\n' "${C_DIM}" "${RESET}"
  else
    _FIRST=true
    for _artifact in "${_ARTIFACTS[@]}"; do
      if [[ "$_FIRST" == "true" ]]; then
        printf '%s%s%s  %s(current)%s\n' \
          "${C_DONE}" "${_artifact}" "${RESET}" \
          "${C_DIM}" "${RESET}"
        _FIRST=false
      else
        printf '%s%s%s\n' "${C_DIM}" "${_artifact}" "${RESET}"
      fi
    done
    printf '\n%s(%d artifact%s)%s\n' \
      "${C_DIM}" \
      "${#_ARTIFACTS[@]}" \
      "$([ "${#_ARTIFACTS[@]}" -eq 1 ] && echo "" || echo "s")" \
      "${RESET}"
  fi
fi

echo ""

# ---------------------------------------------------------------------------
# Section 2: Document-pipeline progress
#
# Scans the project's tasks directory and counts tasks by role and state.
# Shows WRITER / TESTER / CM queue summaries — the three roles that own the
# document pipeline (outline -> draft -> polish -> review -> finalize).
# Does NOT show PM or CODER tasks (those are not document-pipeline roles).
# ---------------------------------------------------------------------------

printf '%s=== Document Pipeline ===%s\n' "${C_LABL}${C_BOLD}" "${RESET}"

TASKS_DIR="${KANBAN_ROOT}/projects/${PROJECT_NAME}/tasks"

if [[ ! -d "$TASKS_DIR" ]]; then
  printf '%s(tasks/ directory not found)%s\n' "${C_DIM}" "${RESET}"
else
  # task_panes reads each task's Role and State through
  # pgai_agent_kanban.task_status in one interpreter.
  pp_run_ops pgai_agent_kanban.dashboard.task_panes document-pipeline "${TASKS_DIR}" \
    --use-color "${USE_COLOR}" --label "${C_LABL}" --done "${C_DONE}" \
    --working "${C_WORK}" --blocked "${C_BLCK}" --dim "${C_DIM}" --reset "${RESET}"
fi
