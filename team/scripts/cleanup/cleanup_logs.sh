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
# Usage:
#   cleanup_logs.sh [--dry-run] [--help]
#
# Options:
#   --dry-run   List each file that would be deleted, delete nothing
#   --help, -h  Print usage and exit 0 without touching anything
#
# Configuration:
#   DRY_RUN=1   — same as --dry-run (kept for existing cron lines)
#
# Exit codes:
#   0 = success (including zero files pruned), or --help
#   1 = error (missing KANBAN_ROOT, log dir not found, etc.)
#   2 = unknown argument (nothing is deleted)

# ---------------------------------------------------------------------------
# Bootstrap: resolve paths via project_paths.sh
# ---------------------------------------------------------------------------
_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_CLEANUP_LOGS_LIB_DIR="${_SCRIPT_DIR}/../lib"
# shellcheck source=lib/project_paths.sh
source "${_CLEANUP_LOGS_LIB_DIR}/project_paths.sh"
# shellcheck source=lib/operator_args.sh
source "${_CLEANUP_LOGS_LIB_DIR}/operator_args.sh"
# shellcheck source=lib/error_contract.sh
source "${_CLEANUP_LOGS_LIB_DIR}/error_contract.sh"
unset _CLEANUP_LOGS_LIB_DIR

set -euo pipefail

# ---------------------------------------------------------------------------
# Arguments: parsed before anything is resolved or deleted, so --help and a
# refused argument never reach the prune loop.
# ---------------------------------------------------------------------------
OPERATOR_VALID_FLAGS=(dry-run help)
declare -A OPERATOR_FLAG_HELP=(
    [dry-run]="  --dry-run          List each file that would be deleted; delete nothing"
)

operator_args_parse "$@"

if argparse_has help; then
    operator_args_render_help_for_flags "cleanup_logs.sh" \
        "Prune small (<1024 bytes), stale (>1 day old) *.log files from \$KANBAN_ROOT/logs/agents." \
        OPERATOR_VALID_FLAGS \
        "" \
        "With no arguments the prune runs. DRY_RUN=1 in the environment is the" \
        "same as --dry-run." \
        "" \
        "Exit codes:" \
        "  0  Success (including zero files pruned), or --help" \
        "  1  Log directory not found" \
        "  2  Unknown argument (nothing is deleted)"
    exit 0
fi

_cleanup_logs_unknown=""
for _cleanup_logs_flag in "${!ARGPARSE_FLAGS[@]}"; do
    case "${_cleanup_logs_flag}" in
        dry-run|help) ;;
        *) _cleanup_logs_unknown="--${_cleanup_logs_flag}"; break ;;
    esac
done
if [[ -z "${_cleanup_logs_unknown}" && ${#ARGPARSE_MISSING[@]} -gt 0 ]]; then
    _cleanup_logs_unknown="--${ARGPARSE_MISSING[0]}"
fi
if [[ -z "${_cleanup_logs_unknown}" && ${#ARGPARSE_POSITIONAL[@]} -gt 0 ]]; then
    _cleanup_logs_unknown="${ARGPARSE_POSITIONAL[0]}"
fi
if [[ -n "${_cleanup_logs_unknown}" ]]; then
    # BLOCK-WORTHY: an argument this script does not define may be an
    # operator expecting a narrower or safer run than the default prune;
    # refused before anything is deleted rather than ignored.
    pgai_emit_error_contract "cleanup_logs.unknown_argument" \
        "cleanup_logs.sh: unknown argument '${_cleanup_logs_unknown}'. Accepted: --dry-run, --help. Nothing was deleted." \
        "${_SCRIPT_DIR}/cleanup_logs.sh --dry-run"
    # CENSUS-ANCHOR: cleanup-logs-unknown-argument-01
    exit 2
fi

if argparse_has dry-run; then
    DRY_RUN=1
fi

# ---------------------------------------------------------------------------
# Resolve the log directory using pp_* helpers
# ---------------------------------------------------------------------------
KANBAN_ROOT="${KANBAN_ROOT:-${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}}"
export KANBAN_ROOT

# Wake batch logs live at $KANBAN_ROOT/logs/agents/.
# No project-scoped path is needed for the log directory.
LOG_DIR="${KANBAN_ROOT}/logs/agents"

if [[ ! -d "$LOG_DIR" ]]; then
    # The remedy names a real next step for both causes: at the default root
    # the wake scripts have not logged yet, so the directory is created (an
    # empty one prunes nothing); anywhere else KANBAN_ROOT most likely names
    # the wrong tree, so the preview is pointed at the default root.
    _cleanup_logs_default_root="${HOME}/pgai_agent_kanban"
    _cleanup_logs_preview="$(printf '%q' "${_SCRIPT_DIR}/cleanup_logs.sh") --dry-run"
    if [[ "${KANBAN_ROOT}" == "${_cleanup_logs_default_root}" ]]; then
        _cleanup_logs_remedy="Create the directory the wake scripts log to, then preview the prune: mkdir -p $(printf '%q' "${LOG_DIR}") && ${_cleanup_logs_preview}"
    else
        _cleanup_logs_remedy="Point KANBAN_ROOT at the kanban root whose logs/agents directory holds the wake logs, then preview the prune, for example: KANBAN_ROOT=$(printf '%q' "${_cleanup_logs_default_root}") ${_cleanup_logs_preview}"
    fi
    # BLOCK-WORTHY: the prune deletes only inside an existing
    # $KANBAN_ROOT/logs/agents; a missing one means KANBAN_ROOT names a tree
    # the wake scripts never logged to, so the run refuses rather than
    # reporting "pruned 0" for a root it was never pointed at.
    pgai_emit_error_contract "cleanup_logs.log_dir_not_found" \
        "cleanup_logs.sh: log directory not found: ${LOG_DIR} (KANBAN_ROOT=${KANBAN_ROOT}). Nothing was deleted." \
        "${_cleanup_logs_remedy}"
    # CENSUS-ANCHOR: cleanup-logs-log-dir-not-found-01
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
