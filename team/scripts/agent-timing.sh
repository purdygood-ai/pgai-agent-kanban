#!/usr/bin/env bash
# team/scripts/agent-timing.sh
# Per-agent-role average wake-time report for the pgai kanban system.
#
# Parses wake log lines that emit elapsed=Ns and aggregates average wake
# times per agent role and project, printing a plain-text table.
#
# USAGE:
#   agent-timing.sh [--days N] [--all-time] [--kanban-root <path>] [-h|--help]
#
# FLAGS:
#   --days N             Include wake log entries from the last N days.
#                        Default: 7.
#   --all-time           Include all wake log entries regardless of age.
#   --kanban-root <path> Override PGAI_AGENT_KANBAN_ROOT_PATH.
#   -h, --help           Show this help and exit.
#
# OUTPUT:
#   Plain-text table with columns:
#     agent_role  project  avg_seconds  wake_count  total_elapsed_seconds
#
# EXIT CODES:
#   0 -- success (warnings may appear on stderr)
#   1 -- usage error or unrecoverable configuration failure

set -euo pipefail
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh"

# ---------------------------------------------------------------------------
# Resolve script location
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEAM_DIR="${SCRIPT_DIR}/.."

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
DAYS_FLAG=""
ALL_TIME_FLAG=""
KANBAN_ROOT_OVERRIDE=""

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --days)
            [[ -n "${2:-}" ]] || { echo "ERROR: --days requires an integer argument" >&2; exit 1; }
            DAYS_FLAG="$2"
            shift 2
            ;;
        --all-time)
            ALL_TIME_FLAG="--all-time"
            shift
            ;;
        --kanban-root)
            [[ -n "${2:-}" ]] || { echo "ERROR: --kanban-root requires a path argument" >&2; exit 1; }
            KANBAN_ROOT_OVERRIDE="$2"
            shift 2
            ;;
        -h|--help)
            sed -n '2,/^set -euo pipefail/{ /^set -euo pipefail/d; s/^# \{0,1\}//; p }' "$0"
            exit 0
            ;;
        *)
            echo "ERROR: unknown argument: $1" >&2
            echo "Run with --help for usage." >&2
            exit 1
            ;;
    esac
done

# ---------------------------------------------------------------------------
# Apply kanban-root override to environment
# ---------------------------------------------------------------------------
if [[ -n "$KANBAN_ROOT_OVERRIDE" ]]; then
    export PGAI_AGENT_KANBAN_ROOT_PATH="$KANBAN_ROOT_OVERRIDE"
fi

# ---------------------------------------------------------------------------
# Kanban root is established by env_bootstrap.sh (sourced above) — no check needed.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Build python argument list
# ---------------------------------------------------------------------------
PY_ARGS=()
if [[ -n "$ALL_TIME_FLAG" ]]; then
    PY_ARGS+=( "--all-time" )
elif [[ -n "$DAYS_FLAG" ]]; then
    PY_ARGS+=( "--days" "$DAYS_FLAG" )
fi

# ---------------------------------------------------------------------------
# Locate agent_timing.py
# ---------------------------------------------------------------------------
AGENT_TIMING_PY="${TEAM_DIR}/pgai_agent_kanban/reports/agent_timing.py"

if [[ ! -f "$AGENT_TIMING_PY" ]]; then
    echo "ERROR: agent_timing.py not found: ${AGENT_TIMING_PY}" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Run the report
# ---------------------------------------------------------------------------
python3 "$AGENT_TIMING_PY" "${PY_ARGS[@]+"${PY_ARGS[@]}"}"
