#!/usr/bin/env bash
# team/scripts/list-stale-requirements.sh
#
# Operator inventory tool: lists stale operator-authored requirements files
# across all registered projects (or one project with --project NAME).
#
# A requirements file is "stale" when its declared target_version is <=
# current_live for the project AND there is no corresponding release-notes
# entry (meaning the file was never shipped — it is orphaned).
#
# Bundle files (*-bugfix-bundle-*.md, *-priority-bundle-*.md) are always
# excluded — they are legitimate historical artifacts, not orphaned files.
#
# Output format (one stanza per stale file):
#
#   project: <name>
#   file:    <filename>
#   target:  <target_version>
#   live:    <current_live>
#   hint:    <other-project-name>   (only when cross-project match found)
#   recover: mv projects/<name>/requirements/<filename> \
#               projects/<hint>/requirements/
#            — or delete if truly obsolete
#
# When no stale files are found, emits a single "(no stale requirements found)"
# line and exits 0.
#
# Usage:
#   list-stale-requirements.sh [--project NAME] [--kanban-root PATH] [--no-color]
#
# Options:
#   --project NAME     Scope output to one project only.
#   --kanban-root PATH Override the kanban root (default: $PGAI_AGENT_KANBAN_ROOT_PATH
#                      or ~/pgai_agent_kanban).
#   --no-color         Suppress ANSI color output (also honored via NO_COLOR env var).
#
# Exit codes:
#   0 — scan complete (zero or more stale files found; see output)
#   1 — argument error (unknown flag, --project with no name)
#   2 — kanban root not found
#
# Environment:
#   NO_COLOR                           — set to suppress ANSI colors

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEAM_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}"
PROJECT_FILTER=""
USE_COLOR=true

while [[ $# -gt 0 ]]; do
  case "$1" in
    --help|-h)
      sed -n '2,45p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    --project)
      if [[ -z "${2:-}" ]]; then
        echo "ERROR: --project requires a project name" >&2
        exit 1
      fi
      PROJECT_FILTER="$2"
      shift 2
      ;;
    --kanban-root)
      if [[ -z "${2:-}" ]]; then
        echo "ERROR: --kanban-root requires a path" >&2
        exit 1
      fi
      KANBAN_ROOT="$2"
      shift 2
      ;;
    --no-color)
      USE_COLOR=false
      shift
      ;;
    -*)
      echo "ERROR: unknown option: $1" >&2
      echo "Usage: list-stale-requirements.sh [--project NAME] [--kanban-root PATH] [--no-color]" >&2
      exit 1
      ;;
    *)
      echo "ERROR: unexpected argument: $1" >&2
      echo "Usage: list-stale-requirements.sh [--project NAME] [--kanban-root PATH] [--no-color]" >&2
      exit 1
      ;;
  esac
done

# Honor NO_COLOR env var and TERM=dumb
if [[ -n "${NO_COLOR:-}" ]] || [[ "${TERM:-}" == "dumb" ]]; then
  USE_COLOR=false
fi

# ---------------------------------------------------------------------------
# Validate kanban root
# ---------------------------------------------------------------------------
if [[ ! -d "$KANBAN_ROOT" ]]; then
  echo "ERROR: kanban root not found: $KANBAN_ROOT" >&2
  exit 2
fi

# ---------------------------------------------------------------------------
# ANSI helpers
# ---------------------------------------------------------------------------
if [[ "$USE_COLOR" == "true" ]]; then
  C_BOLD=$'\033[1m'
  C_YELLOW=$'\033[0;33m'
  C_DIM=$'\033[2m'
  C_RESET=$'\033[0m'
else
  C_BOLD=""
  C_YELLOW=""
  C_DIM=""
  C_RESET=""
fi

# ---------------------------------------------------------------------------
# Source required libraries.
# TEAM_ROOT is required by discovery.sh for its internal log shim.
# KANBAN_ROOT must be exported so pp_* functions can resolve the kanban root.
# ---------------------------------------------------------------------------
TEAM_ROOT="${TEAM_DIR}"
export KANBAN_ROOT TEAM_ROOT

# shellcheck source=lib/project_paths.sh
source "${SCRIPT_DIR}/lib/project_paths.sh"

# shellcheck source=lib/semver.sh
source "${SCRIPT_DIR}/lib/semver.sh"

# shellcheck source=lib/projects.sh
source "${SCRIPT_DIR}/lib/projects.sh"

# shellcheck source=lib/discovery.sh
source "${SCRIPT_DIR}/lib/discovery.sh"

# ---------------------------------------------------------------------------
# Build the list of projects to scan
# ---------------------------------------------------------------------------
if [[ -n "$PROJECT_FILTER" ]]; then
  # Scope to one project. Validate it is registered (warn but continue if not).
  if ! projects_cfg_has "$PROJECT_FILTER" 2>/dev/null; then
    echo "WARNING: project '$PROJECT_FILTER' is not registered in projects.cfg — scanning anyway" >&2
  fi
  project_list=("$PROJECT_FILTER")
else
  # All projects in priority order.  When the registry is unreadable, fail loud:
  # a missing or corrupt projects.cfg is a real error, not an occasion to fall
  # back to a single guessed project.
  if ! mapfile -t project_list < <(projects_cfg_list 2>/dev/null); then
    echo "ERROR: projects_cfg_list failed — projects.cfg may be missing or unreadable" >&2
    echo "       Register a project via scripts/create-project.sh or pass --project <name>" >&2
    exit 1
  fi
  if [[ "${#project_list[@]}" -eq 0 ]]; then
    echo "ERROR: no projects registered in projects.cfg; pass --project <name> to scan one explicitly" >&2
    exit 1
  fi
fi

# ---------------------------------------------------------------------------
# Scan each project
# ---------------------------------------------------------------------------
found_any=false

for proj in "${project_list[@]}"; do
  [[ -z "$proj" ]] && continue

  # Collect stale entries for this project.
  stale_entries=()
  while IFS=$'\t' read -r fname tver clive hint; do
    [[ -z "$fname" ]] && continue
    stale_entries+=("${fname}	${tver}	${clive}	${hint}")
  done < <(discovery_scan_stale_requirements "$proj" 2>/dev/null || true)

  [[ "${#stale_entries[@]}" -eq 0 ]] && continue

  found_any=true

  for entry in "${stale_entries[@]}"; do
    IFS=$'\t' read -r fname tver clive hint <<< "$entry"

    printf '%sproject:%s %s\n' "$C_BOLD" "$C_RESET" "$proj"
    printf '%sfile:   %s %s\n' "$C_BOLD" "$C_RESET" "$fname"
    printf '%starget: %s %s\n' "$C_BOLD" "$C_RESET" "$tver"
    printf '%slive:   %s %s\n' "$C_BOLD" "$C_RESET" "$clive"

    if [[ -n "$hint" ]]; then
      printf '%shint:   %s %s%s%s (filename references this project)\n' \
        "$C_BOLD" "$C_RESET" "$C_YELLOW" "$hint" "$C_RESET"
      # Resolve the hint project's requirements dir for the recovery hint.
      hint_req_dir="$(pp_requirements_dir "$hint" 2>/dev/null || true)"
      if [[ -n "$hint_req_dir" ]]; then
        printf '%srecover:%s mv projects/%s/requirements/%s %s/\n' \
          "$C_BOLD" "$C_RESET" "$proj" "$fname" "$hint_req_dir"
      else
        printf '%srecover:%s mv file to the correct project'\''s requirements/ directory\n' \
          "$C_BOLD" "$C_RESET"
      fi
    else
      printf '%srecover:%s mv or delete if obsolete\n' "$C_BOLD" "$C_RESET"
    fi

    printf '\n'
  done
done

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
if [[ "$found_any" == "false" ]]; then
  printf '%s(no stale requirements found)%s\n' "$C_DIM" "$C_RESET"
fi

exit 0
