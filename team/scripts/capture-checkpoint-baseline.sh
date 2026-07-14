#!/usr/bin/env bash
# team/scripts/capture-checkpoint-baseline.sh
#
# Capture a baseline snapshot for the operator pre-port / post-port diff used
# in the v1.1.0 behavior-verbatim checkpoint procedure documented at:
#
#   docs/operator-checkpoints/v1-1-1/release-rc-checkpoint.md
#   docs/operator-checkpoints/v1-1-1/document-workflow-checkpoint.md
#   docs/operator-checkpoints/v1-1-1/custom-workflow-walkthrough.md
#
# Referenced by: PRIORITY-0001-e2e-managed-project-checkpoint-v1-1-0.md
#
# Run this script on a pre-port baseline checkout (e.g., v1.0.0) and then on
# the post-port live install (v1.1.0+) to produce comparable metadata that
# proves behavior-verbatim outcomes.
#
# Usage:
#   capture-checkpoint-baseline.sh --project <name> --out <dir> [--help]
#
# Options:
#   --project <name>   Registered project name (e.g., chomp-man). Required.
#   --out <dir>        Output directory. Created if absent. Required.
#   --help             Print this usage text and exit 0.
#
# Outputs (written to <dir>/):
#   tags.txt        — git tags scoped to the project prefix (<name>/*)
#   artifacts.txt   — file paths under projects/<name>/artifacts/
#   task-graph.txt  — task IDs and roles under projects/<name>/tasks/
#
# Exit codes:
#   0  — success
#   1  — usage error or unrecognised project name
#
# Idempotent: re-running against the same output directory overwrites cleanly.
# Read-only: the script never modifies the checked-out tree.

set -euo pipefail
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh"

# ---------------------------------------------------------------------------
# Resolve kanban root and script directory
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB_DIR="${SCRIPT_DIR}/lib"
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"

# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------
usage() {
    cat <<'EOF'
Usage: capture-checkpoint-baseline.sh --project <name> --out <dir> [--help]

Capture a baseline snapshot for the operator pre-port / post-port diff used
in the v1.1.0 behavior-verbatim checkpoint procedure.

Options:
  --project <name>   Registered project name (e.g., chomp-man). Required.
  --out <dir>        Output directory. Created if absent. Required.
  --help             Print this usage text and exit 0.

Outputs written to <dir>/:
  tags.txt        — git tags scoped to the project prefix (<name>/*)
  artifacts.txt   — file paths under projects/<name>/artifacts/
  task-graph.txt  — task IDs and roles under projects/<name>/tasks/

Exit codes:
  0  Success.
  1  Usage error or unrecognised project name.
EOF
}

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
PROJECT_NAME=""
OUT_DIR=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --project)
            if [[ $# -lt 2 || -z "$2" ]]; then
                echo "ERROR: --project requires a non-empty argument" >&2
                exit 1
            fi
            PROJECT_NAME="$2"
            shift 2
            ;;
        --out)
            if [[ $# -lt 2 || -z "$2" ]]; then
                echo "ERROR: --out requires a non-empty argument" >&2
                exit 1
            fi
            OUT_DIR="$2"
            shift 2
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            echo "ERROR: Unknown argument: $1" >&2
            echo "Run with --help for usage." >&2
            exit 1
            ;;
    esac
done

if [[ -z "$PROJECT_NAME" ]]; then
    echo "ERROR: --project is required" >&2; echo "Run with --help for usage." >&2; exit 1
fi
if [[ -z "$OUT_DIR" ]]; then
    echo "ERROR: --out is required" >&2; echo "Run with --help for usage." >&2; exit 1
fi

# ---------------------------------------------------------------------------
# Validate project name against the registry
# ---------------------------------------------------------------------------
PROJECTS_CFG="${KANBAN_ROOT}/projects.cfg"
PROJECT_FOUND=0

if [[ -f "${LIB_DIR}/projects.sh" && -f "$PROJECTS_CFG" ]]; then
    # Source the library with the correct root so projects_cfg_has works.
    # shellcheck source=lib/projects.sh
    PGAI_AGENT_KANBAN_ROOT_PATH="$KANBAN_ROOT" source "${LIB_DIR}/projects.sh"
    if projects_cfg_has "$PROJECT_NAME" 2>/dev/null; then
        PROJECT_FOUND=1
    fi
else
    # projects.cfg unavailable (raw git checkout without live install).
    # Fall back to checking whether the project directory exists.
    if [[ -d "${KANBAN_ROOT}/projects/${PROJECT_NAME}" ]]; then
        PROJECT_FOUND=1
    fi
fi

if [[ "$PROJECT_FOUND" -eq 0 ]]; then
    echo "ERROR: unknown project '${PROJECT_NAME}'" >&2
    echo "       Check registered projects in: ${PROJECTS_CFG}" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Prepare output directory
# ---------------------------------------------------------------------------
mkdir -p "$OUT_DIR"

TAGS_FILE="${OUT_DIR}/tags.txt"
ARTIFACTS_FILE="${OUT_DIR}/artifacts.txt"
TASK_GRAPH_FILE="${OUT_DIR}/task-graph.txt"

echo "Capturing baseline for project '${PROJECT_NAME}' into '${OUT_DIR}' ..."

# ---------------------------------------------------------------------------
# tags.txt — git tags scoped to this project's prefix
# ---------------------------------------------------------------------------
git tag --list "${PROJECT_NAME}/*" --sort=-version:refname > "$TAGS_FILE"
TAG_COUNT=$(wc -l < "$TAGS_FILE" | tr -d ' ')
echo "  tags.txt: ${TAG_COUNT} tag(s)"

# ---------------------------------------------------------------------------
# artifacts.txt — file paths under projects/<name>/artifacts/
# ---------------------------------------------------------------------------
ARTIFACTS_DIR="${KANBAN_ROOT}/projects/${PROJECT_NAME}/artifacts"
if [[ -d "$ARTIFACTS_DIR" ]]; then
    find "$ARTIFACTS_DIR" -type f -print | sort > "$ARTIFACTS_FILE"
    ARTIFACT_COUNT=$(wc -l < "$ARTIFACTS_FILE" | tr -d ' ')
    echo "  artifacts.txt: ${ARTIFACT_COUNT} file(s)"
else
    > "$ARTIFACTS_FILE"
    echo "  artifacts.txt: 0 file(s) (artifacts directory absent)"
fi

# ---------------------------------------------------------------------------
# task-graph.txt — task IDs and roles under projects/<name>/tasks/
# ---------------------------------------------------------------------------
TASKS_DIR="${KANBAN_ROOT}/projects/${PROJECT_NAME}/tasks"
> "$TASK_GRAPH_FILE"
if [[ -d "$TASKS_DIR" ]]; then
    while IFS= read -r task_dir; do
        task_id="$(basename "$task_dir")"
        # Role is the first dash-separated token of the task ID (e.g., CODER, WRITER)
        role="${task_id%%-*}"
        printf '%-60s  %s\n' "$task_id" "$role" >> "$TASK_GRAPH_FILE"
    done < <(find "$TASKS_DIR" -maxdepth 1 -mindepth 1 -type d -print | sort)
    TASK_COUNT=$(wc -l < "$TASK_GRAPH_FILE" | tr -d ' ')
    echo "  task-graph.txt: ${TASK_COUNT} task(s)"
else
    echo "  task-graph.txt: 0 task(s) (tasks directory absent)"
fi

echo "Done."
