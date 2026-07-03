#!/usr/bin/env bash
# cleanup_logs.sh
# Prune small, low-value log files from the wake-script log directory.
#
# Purpose: Remove log files that are both small (<1024 bytes) and stale
#          (older than 1 day). These are typically empty or near-empty wake
#          logs that carry no diagnostic value.
#
# Cadence: Safe to run from cron at any interval (hourly, daily). Idempotent.
#
# Configuration:
#   DRY_RUN=1   — preview what would be deleted without removing anything
#
# Exit codes:
#   0 = success (including zero files pruned)
#   1 = error (missing KANBAN_ROOT, log dir not found, etc.)

# ---------------------------------------------------------------------------
# Bootstrap: resolve paths via project_paths.sh
# ---------------------------------------------------------------------------
_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/project_paths.sh
source "${_SCRIPT_DIR}/../lib/project_paths.sh"
unset _SCRIPT_DIR

set -euo pipefail

# ---------------------------------------------------------------------------
# Resolve the log directory using pp_* helpers
# ---------------------------------------------------------------------------
KANBAN_ROOT="${KANBAN_ROOT:-${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}}"
export KANBAN_ROOT

# Wake batch logs live at $KANBAN_ROOT/logs/agents/.
# No project-scoped path is needed for the log directory.
LOG_DIR="${KANBAN_ROOT}/logs/agents"

if [[ ! -d "$LOG_DIR" ]]; then
    echo "cleanup_logs: log directory not found: $LOG_DIR" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Prune small + old log files
# ---------------------------------------------------------------------------
COUNT=0

while IFS= read -r -d '' file; do
    if [[ "${DRY_RUN:-0}" == "1" ]]; then
        echo "cleanup_logs: [DRY-RUN] would delete: $file"
    else
        rm -f "$file"
    fi
    COUNT=$(( COUNT + 1 ))
done < <(find "$LOG_DIR" -maxdepth 1 -type f -name '*.log' -mtime +1 -size -1024c -print0 2>/dev/null)

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo "cleanup_logs: pruned $COUNT small log files (<1024 bytes, >1 day old) from $LOG_DIR"

exit 0
