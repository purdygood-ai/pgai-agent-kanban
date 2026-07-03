#!/usr/bin/env bash
# team/scripts/project-summary.sh
# Comprehensive historical audit report for a named project.
#
# Thin bash wrapper around team/pgai_agent_kanban/reports/project_summary.py.
# All argument parsing, data loading, and rendering is done in Python; this
# script resolves the kanban root, activates the venv when present, and
# delegates immediately.
#
# Usage:
#   project-summary.sh [options]
#
# Options:
#   --project <name>      Project to report on (required when multiple projects
#                         are registered; auto-detected when only one exists).
#   --project all         Report on every registered project plus aggregate totals.
#   --days N              Limit "recent" sections to the last N days.
#   --brief               Counts only — no per-item summaries.
#   --all                 Show all items in every section (override truncation).
#   --llm                 Use LLM mode for summaries. Requires ANTHROPIC_API_KEY.
#   --format text|md|json Output format (default: text).
#   --output FILE         Write to FILE instead of stdout.
#   --kanban-root <path>  Override PGAI_AGENT_KANBAN_ROOT_PATH.
#   -h, --help            Show this help and exit.
#
# Environment:
#   PGAI_AGENT_KANBAN_ROOT_PATH         Kanban root (default: ~/pgai_agent_kanban).
#   PGAI_AGENT_KANBAN_ROOT_PATH  Kanban root path.
#   ANTHROPIC_API_KEY                   Required when --llm is used.
#
# Exit codes:
#   0  Success.
#   1  Usage error or unrecoverable configuration failure.

set -euo pipefail

# ---------------------------------------------------------------------------
# Resolve script location and module path
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEAM_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${TEAM_DIR}/.." && pwd)"

# Source shared Python-helper resolver (live-install anchor first — D3 fix).
# The resolver lives in dashboard/lib/ relative to scripts/.
# shellcheck source=dashboard/lib/helper_resolver.sh
source "${SCRIPT_DIR}/dashboard/lib/helper_resolver.sh"

# ---------------------------------------------------------------------------
# Parse --kanban-root early (before delegating to Python)
# ---------------------------------------------------------------------------
KANBAN_ROOT_OVERRIDE=""
PASS_THROUGH_ARGS=()
_i=0
_args=("$@")
while [[ $_i -lt ${#_args[@]} ]]; do
    arg="${_args[$_i]}"
    if [[ "$arg" == "--kanban-root" ]]; then
        _i=$(( _i + 1 ))
        if [[ $_i -ge ${#_args[@]} ]]; then
            echo "ERROR: --kanban-root requires a path argument" >&2
            exit 1
        fi
        KANBAN_ROOT_OVERRIDE="${_args[$_i]}"
    elif [[ "$arg" == "-h" || "$arg" == "--help" ]]; then
        # Let Python print its own help (more detailed)
        PASS_THROUGH_ARGS+=("$arg")
    else
        PASS_THROUGH_ARGS+=("$arg")
    fi
    _i=$(( _i + 1 ))
done

# ---------------------------------------------------------------------------
# Export kanban root for the Python module
# ---------------------------------------------------------------------------
_KANBAN_ROOT_RESOLVED="${KANBAN_ROOT_OVERRIDE:-${PGAI_AGENT_KANBAN_ROOT_PATH:-${HOME}/pgai_agent_kanban}}"
export PGAI_AGENT_KANBAN_ROOT_PATH="$_KANBAN_ROOT_RESOLVED"
unset _KANBAN_ROOT_RESOLVED

# ---------------------------------------------------------------------------
# Activate venv when available (mirrors the pattern used by other scripts)
# ---------------------------------------------------------------------------
VENV_ACTIVATE="${PGAI_AGENT_KANBAN_ROOT_PATH}/venv/bin/activate"
if [[ -f "$VENV_ACTIVATE" ]]; then
    # shellcheck source=/dev/null
    source "$VENV_ACTIVATE"
fi

# ---------------------------------------------------------------------------
# Locate the Python module via shared helper resolver (live-install anchor first — D3 fix).
# ---------------------------------------------------------------------------
# resolve_dashboard_helper probes $KANBAN_ROOT/pgai_agent_kanban/<rel> first,
# then $PGAI_DEV_TREE_PATH/team/pgai_agent_kanban/<rel> as a fallback.
# For project-summary.sh, KANBAN_ROOT = PGAI_AGENT_KANBAN_ROOT_PATH (already exported).
# PGAI_DEV_TREE_PATH may be empty in a live-install-only layout; the resolver handles that.
MODULE_PATH="$(resolve_dashboard_helper \
    "$PGAI_AGENT_KANBAN_ROOT_PATH" \
    "${PGAI_DEV_TREE_PATH:-}" \
    "reports/project_summary.py")"

if [[ -z "$MODULE_PATH" ]]; then
    # Neither live-install anchor nor dev-tree fallback contained the module.
    echo "ERROR: project_summary.py not found at:" >&2
    echo "  ${PGAI_AGENT_KANBAN_ROOT_PATH}/pgai_agent_kanban/reports/project_summary.py" >&2
    echo "  ${PGAI_DEV_TREE_PATH:-<unset>}/team/pgai_agent_kanban/reports/project_summary.py" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Delegate to Python
# ---------------------------------------------------------------------------
exec python3 "$MODULE_PATH" "${PASS_THROUGH_ARGS[@]+"${PASS_THROUGH_ARGS[@]}"}"
