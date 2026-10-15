#!/usr/bin/env bash
# suggested-commands.sh
# Quick reference to the operator's most common commands, printed on demand.
#
# A command the operator runs from the interactive shell row of dashboard
# window 1 (the operator window), or from any shell: it prints a static,
# curated list of the scripts/ commands documented in
# docs/operator-commands.md and docs/OPERATIONS.md, one line of command
# syntax followed by a one-line description, and exits. It NEVER executes
# anything on the operator's behalf -- it only prints text. Copy a line
# from its output and run it yourself in that same shell.
#
# Usage:
#   suggested-commands.sh [--kanban-root <path>] [--no-color]
#
# Options:
#   --kanban-root <path>   Kanban root shown in the example commands
#                          (default: $PGAI_AGENT_KANBAN_ROOT_PATH)
#   --no-color             Disable ANSI color output
#   -h, --help             Show this help and exit
#
# Environment:
#   PGAI_AGENT_KANBAN_ROOT_PATH  Kanban root override
#   NO_COLOR                     Set non-empty to disable colors
#   TERM=dumb                    Also disables colors
#
# Read-only: never writes kanban state, never invokes another script, never
# shells out. See docs/operator-commands.md for the full command reference
# this list summarizes.

set -euo pipefail
# shellcheck source=../lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/env_bootstrap.sh"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck source=lib/script_common.sh
source "${SCRIPT_DIR}/../lib/script_common.sh"

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
NO_COLOR_ARG=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --kanban-root)
      KANBAN_ROOT="${2:-$KANBAN_ROOT}"
      shift 2
      ;;
    --no-color)
      NO_COLOR_ARG=true
      shift
      ;;
    -h|--help)
      # Read the whole header via mapfile so grep/sed always finish writing
      # before anything reads their output, then slice to the line limit
      # locally -- head -N as a pipe stage can close its input early and
      # SIGPIPE the writers under pipefail.
      mapfile -t __help_lines < <(grep '^#' "$0" | grep -v '^#!' | sed 's/^# \{0,1\}//')
      printf '%s\n' "${__help_lines[@]:0:28}"
      exit 0
      ;;
    *)
      echo "ERROR: Unknown argument: $1" >&2
      echo "Usage: $0 [--kanban-root <path>] [--no-color]" >&2
      exit 1
      ;;
  esac
done

USE_COLOR=true
if [[ "${TERM:-}" == "dumb" ]] || [[ -n "${NO_COLOR:-}" ]] || [[ "$NO_COLOR_ARG" == "true" ]]; then
  USE_COLOR=false
fi

RESET="$(ansi_code reset)"
C_HEADER="$(ansi_code bold_cyan)"
C_CMD="$(ansi_code green_bright)"
C_DIM="$(ansi_code dim)"

# ---------------------------------------------------------------------------
# Render one command row: the command line, then its description indented
# beneath it. Purely textual -- no command in this list is ever invoked by
# this script itself.
# ---------------------------------------------------------------------------
_row() {
  local cmd="$1" desc="$2"
  printf '%s%s%s\n' "$C_CMD" "$cmd" "$RESET"
  printf '  %s%s%s\n' "$C_DIM" "$desc" "$RESET"
}

printf '%s=== SUGGESTED COMMANDS (read-only reference) ===%s\n' "$C_HEADER" "$RESET"
printf '%sCopy a line from this list and run it in the shell -- this command never runs anything for you.%s\n\n' "$C_DIM" "$RESET"

_row "scripts/show.sh --project <proj> --key <key>" \
  "Print a task's or intake item's content (read-only)."
_row "scripts/close.sh --project <proj> --key <key>" \
  "Close an item by key -- e.g. approve a pending HUMAN-APPROVE gate."
_row "scripts/wontdo.sh --project <proj> --key <key>" \
  "Retire a task or intake item as WONT-DO."
_row "scripts/reset.sh --project <proj> --key <key>" \
  "Return a task or intake item to a re-pickable state."
_row "scripts/intake.sh <file>" \
  "Drop a staged bug/priority/requirement file into a project."
_row "scripts/why.sh --project <proj>" \
  "One verdict per project: cause, remedy, exit code."
_row "scripts/halt.sh / scripts/unhalt.sh --project <proj>" \
  "Pause / resume this project's wake loop."
_row "scripts/halt-after.sh --project <proj> [--until vX.Y.Z]" \
  "Soft halt: drain in-flight work, then stop."
_row "PGAI_CONTAINER_TIER=1 scripts/deep-verify.sh --project <proj> --image all (alias: cold-build-now.sh)" \
  "Run the cold-build DEEP-VERIFY tier on demand (operator-only; --image picks the images)."
_row "scripts/show-test-report.sh --project <proj> --key <rc-version>" \
  "Print a TESTER verification report (read-only)."
_row "scripts/dashboard/agent-metrics.sh --all [--project <proj>]" \
  "Per-agent metrics over every shipped RC; --since vX.Y.Z or --last N narrows the span."

echo ""
printf '%sKanban root: %s%s\n' "$C_DIM" "$KANBAN_ROOT" "$RESET"
printf '%sFull reference: docs/operator-commands.md, docs/OPERATIONS.md%s\n' "$C_DIM" "$RESET"
