#!/usr/bin/env bash
# team/scripts/harness/pm-hallucination-capture.sh
#
# PM Hallucination Investigation Harness — Evidence Capture
#
# ===========================================================================
# USAGE
# ===========================================================================
#
# Operator-driven evidence capture for diagnosing PM git_repo URL
# hallucination (PM emits incorrect git_repo_url instead of reading from
# project.cfg).
#
# OPERATOR WORKFLOW
# -----------------
# 1. Enable verbose debug logging for the kanban project:
#
#      # In $KANBAN_ROOT/projects/<project-name>/project.cfg
#      # (where <project-name> is the project under investigation; pass it via
#      # --project to this harness, or let it default to the first project in
#      # projects.cfg).  Add or edit the [debug] section:
#      [debug]
#      verbose_mode = true
#      verbose_agents = pm
#
# 2. Wake the PM agent on a requirements bundle (normal cron or manual):
#
#      $KANBAN_ROOT/scripts/wake-batch.sh --agent=pm --sleep=0
#      # OR via cron — the next scheduled PM firing suffices.
#
# 3. Immediately after the PM wake completes, run this harness:
#
#      team/scripts/harness/pm-hallucination-capture.sh
#
#    The harness will:
#      a) Verify that [debug] verbose_mode is true and pm is in verbose_agents.
#         If not, it prints a corrective hint and exits non-zero.
#      b) Snapshot $KANBAN_ROOT/logs/debug/pm.log to a timestamped capture
#         file under $KANBAN_ROOT/logs/debug/captures/pm-<TIMESTAMP>.log
#      c) Print next-step instructions for attaching the capture to the bug file.
#
# IMPORTANT CONSTRAINTS
# ---------------------
# - Operator-driven only. No cron, no auto-invoke from wake scripts.
# - Idempotent: re-running creates a new timestamped file, never overwrites.
# - Does NOT modify PM's behavior, prompt, or configuration.
# - Captures evidence for diagnosis only; does not alter PM behavior.
#
# ARGUMENTS
#   --project <name>   Project name (required; no silent default)
#   --kanban-root <p>  Override the kanban root path
#   --help             Print this usage block and exit 0
#
# EXIT CODES
#   0   Capture succeeded (or --help was requested).
#   1   Precondition failure (verbose_mode not set, pm not in agents, etc.).
#   2   Source log file missing (pm.log does not exist after a PM wake).
# ===========================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Resolve script dir and source required libraries
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB_DIR="${SCRIPT_DIR}/../lib"

# shellcheck source=../lib/ini_parser.sh
source "${LIB_DIR}/ini_parser.sh"
# shellcheck source=../lib/project_paths.sh
source "${LIB_DIR}/project_paths.sh"
# shellcheck source=../lib/projects.sh
source "${LIB_DIR}/projects.sh"

# ---------------------------------------------------------------------------
# Defaults — no hardcoded project name; resolved from projects.cfg below
# ---------------------------------------------------------------------------
PROJECT_NAME=""
KANBAN_ROOT_OVERRIDE=""

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --help|-h)
            # Extract the top-of-file usage comment block.
            # The block runs from line 1 up to (but not including) the first
            # non-comment, non-blank line after the header (i.e. "set -euo pipefail").
            awk '/^set -euo pipefail/{exit} /^#/{sub(/^# ?/,""); print}' "${BASH_SOURCE[0]}"
            exit 0
            ;;
        --project)
            PROJECT_NAME="${2:-}"
            shift 2
            ;;
        --kanban-root)
            KANBAN_ROOT_OVERRIDE="${2:-}"
            shift 2
            ;;
        *)
            echo "pm-hallucination-capture.sh: unknown argument: $1" >&2
            echo "  Run with --help for usage." >&2
            exit 1
            ;;
    esac
done

# ---------------------------------------------------------------------------
# Resolve kanban root
# ---------------------------------------------------------------------------
# Canonical var first, legacy var as backward-compat fallback, new-path default.
export KANBAN_ROOT="${KANBAN_ROOT_OVERRIDE:-${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}}"

if [[ ! -d "$KANBAN_ROOT" ]]; then
    echo "pm-hallucination-capture.sh: KANBAN_ROOT not found: ${KANBAN_ROOT}" >&2
    echo "  Set PGAI_AGENT_KANBAN_ROOT_PATH or pass --kanban-root <path>." >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Resolve project name — explicit only; no silent default.
# ---------------------------------------------------------------------------
if [[ -z "${PROJECT_NAME:-}" ]]; then
    echo "pm-hallucination-capture.sh: ERROR: no project specified. Pass --project <name>." >&2
    echo "  Run with --help for usage." >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Locate project config
# ---------------------------------------------------------------------------
export PGAI_PROJECT_NAME="$PROJECT_NAME"

PROJECT_ROOT="$(pp_project_root "$PROJECT_NAME")" || {
    echo "pm-hallucination-capture.sh: cannot resolve project root for '${PROJECT_NAME}'." >&2
    exit 1
}

CFG_FILE=""
if [[ -f "${PROJECT_ROOT}/project.cfg" ]]; then
    CFG_FILE="${PROJECT_ROOT}/project.cfg"
elif [[ -f "${PROJECT_ROOT}/PROJECT.cfg" ]]; then
    CFG_FILE="${PROJECT_ROOT}/PROJECT.cfg"
fi

# ---------------------------------------------------------------------------
# PRECONDITION 1: verbose_mode must be true
# ---------------------------------------------------------------------------
VERBOSE_MODE="false"
if [[ -n "$CFG_FILE" ]]; then
    VERBOSE_MODE="$(pp_verbose_mode "$PROJECT_NAME")"
fi

if [[ "$VERBOSE_MODE" != "true" ]]; then
    echo "pm-hallucination-capture.sh: PRECONDITION FAILED — verbose_mode is not enabled." >&2
    echo "" >&2
    echo "  To enable verbose logging for PM, add or edit the [debug] section in:" >&2
    echo "    ${PROJECT_ROOT}/project.cfg" >&2
    echo "" >&2
    echo "  Required settings:" >&2
    echo "    [debug]" >&2
    echo "    verbose_mode = true" >&2
    echo "    verbose_agents = pm" >&2
    echo "" >&2
    echo "  After saving the config, wake the PM agent, then re-run this script." >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# PRECONDITION 2: pm must be listed in verbose_agents
# ---------------------------------------------------------------------------
VERBOSE_AGENTS="$(pp_verbose_agents "$PROJECT_NAME")"

pm_in_agents=false
IFS=',' read -ra _agents_arr <<< "$VERBOSE_AGENTS"
for _a in "${_agents_arr[@]}"; do
    _a_trimmed="${_a// /}"   # strip spaces
    if [[ "$_a_trimmed" == "pm" ]]; then
        pm_in_agents=true
        break
    fi
done
unset _agents_arr _a _a_trimmed

if [[ "$pm_in_agents" != "true" ]]; then
    echo "pm-hallucination-capture.sh: PRECONDITION FAILED — 'pm' is not in verbose_agents." >&2
    echo "" >&2
    echo "  Current verbose_agents: ${VERBOSE_AGENTS}" >&2
    echo "" >&2
    echo "  Add 'pm' to [debug] verbose_agents in:" >&2
    echo "    ${PROJECT_ROOT}/project.cfg" >&2
    echo "" >&2
    echo "  Example:" >&2
    echo "    [debug]" >&2
    echo "    verbose_mode = true" >&2
    echo "    verbose_agents = pm" >&2
    echo "" >&2
    echo "  After saving the config, wake the PM agent, then re-run this script." >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Locate the PM debug log
# ---------------------------------------------------------------------------
DEBUG_LOG_ROOT="${KANBAN_ROOT}/logs/debug"
PM_LOG="${DEBUG_LOG_ROOT}/pm.log"

if [[ ! -f "$PM_LOG" ]]; then
    echo "pm-hallucination-capture.sh: SOURCE LOG NOT FOUND — ${PM_LOG}" >&2
    echo "" >&2
    echo "  pm.log does not exist. This means either:" >&2
    echo "    a) The PM agent has not yet been woken with verbose_mode=true, or" >&2
    echo "    b) The log was rotated or deleted." >&2
    echo "" >&2
    echo "  Run a PM wake cycle first, then re-run this script." >&2
    exit 2
fi

# ---------------------------------------------------------------------------
# Create captures directory (idempotent)
# ---------------------------------------------------------------------------
CAPTURES_DIR="${DEBUG_LOG_ROOT}/captures"
mkdir -p "$CAPTURES_DIR"

# ---------------------------------------------------------------------------
# Snapshot pm.log to a timestamped capture file
# ---------------------------------------------------------------------------
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
CAPTURE_FILE="${CAPTURES_DIR}/pm-${TIMESTAMP}.log"

# Guard: extremely unlikely collision; if same second re-runs, add sub-second.
if [[ -f "$CAPTURE_FILE" ]]; then
    TIMESTAMP="${TIMESTAMP}-$(date +%N | cut -c1-3)"
    CAPTURE_FILE="${CAPTURES_DIR}/pm-${TIMESTAMP}.log"
fi

cp "$PM_LOG" "$CAPTURE_FILE"

PM_LOG_LINES="$(wc -l < "$PM_LOG")"
CAPTURE_SIZE="$(wc -c < "$CAPTURE_FILE")"

# ---------------------------------------------------------------------------
# Print confirmation and next-step instructions
# ---------------------------------------------------------------------------
cat <<INSTRUCTIONS

pm-hallucination-capture.sh: capture complete.

  Source log : ${PM_LOG}
               (${PM_LOG_LINES} lines, ${CAPTURE_SIZE} bytes)

  Capture    : ${CAPTURE_FILE}

NEXT STEPS — Attach this capture to the PM hallucination bug file
-----------------------------------------------------------------
1. Review the capture for hallucinated git_repo_url values:

     grep -i "git_repo\|git@github\|hallucin" "${CAPTURE_FILE}" | head -30

2. Locate the most recent PM plan.json for comparison:

     ls "${PROJECT_ROOT}/tasks/" | grep "PM-.*-decompose" | sort | tail -3

3. Compare the git_repo field in plan.json with the canonical value:

     grep git_repo_url "${PROJECT_ROOT}/project.cfg"

4. Attach the capture file path and comparison output to the PM hallucination bug:

     ${PROJECT_ROOT}/bugs/BUG-NNNN-<hallucination-case>.md

5. If the capture confirms the hallucination pattern, file an update in
   the bug's ## Notes section referencing this capture file path and
   the affected bundle version.

INSTRUCTIONS

exit 0
