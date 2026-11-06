#!/usr/bin/env bash
# team/scripts/lib/operator_inbox_notice.sh
#
# One operator inbox entry for a step that failed after the work around it
# went ahead: a write the release, the RC open or the wake finalize could
# not make, which the step reports and carries on past. The warning on
# stderr reaches only the agent log; this entry is what a returning operator
# reads, naming the file and the command that finishes the step by hand.
#
#   operator_inbox_notice <kanban_root> <project> <source> <question> <meanwhile> <how_to_answer>
#
# Written through the inbox module (pgai_agent_kanban.overwatch.inbox
# write_entry --once): a re-run that meets the same failure, with the same
# source and question, leaves one open entry rather than one per run.
#
# Prints one line on stdout and never fails the caller's strict mode:
#   returns 0 -- the entry's INBOX-NNNN ID (written now, or the matching
#                open entry already in the inbox)
#   returns 1 -- why nothing was written (a blank kanban root or project, a
#                project directory that does not exist, the module's own
#                refusal or failure); the caller's warning stays the only
#                record
#
# Example:
#   if entry="$(operator_inbox_notice "$KANBAN_ROOT" "$PROJECT_NAME" "rc:v1.2.3" \
#       "Q?" "Meanwhile." "Run this by hand.")"; then
#     echo "Operator inbox: ${entry} records the failure."
#   fi
#
# Dependency: pp_run_ops (pp_run_ops.sh beside this file, sourced on first
# use when the caller has not loaded it). No inline Python.

# Guard against double-sourcing.
if declare -F operator_inbox_notice >/dev/null 2>&1; then
  return 0
fi

# This file's directory, as an absolute path resolved once at load: no
# function depends on the caller's current directory.
_OPERATOR_INBOX_NOTICE_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)" || _OPERATOR_INBOX_NOTICE_LIB_DIR=""

operator_inbox_notice() {
  local kanban_root="${1:-}" project="${2:-}" source="${3:-}"
  local question="${4:-}" meanwhile="${5:-}" how_to_answer="${6:-}"
  if [[ -z "$kanban_root" || -z "$project" ]]; then
    echo "no inbox entry written: a kanban root and a project are required (got root='${kanban_root}' project='${project}')"
    return 1
  fi
  if ! declare -F pp_run_ops >/dev/null 2>&1; then
    if [[ -n "$_OPERATOR_INBOX_NOTICE_LIB_DIR" && -f "${_OPERATOR_INBOX_NOTICE_LIB_DIR}/pp_run_ops.sh" ]]; then
      # shellcheck source=pp_run_ops.sh
      source "${_OPERATOR_INBOX_NOTICE_LIB_DIR}/pp_run_ops.sh"
    fi
  fi
  if ! declare -F pp_run_ops >/dev/null 2>&1; then
    echo "no inbox entry written: pp_run_ops.sh not found beside ${_OPERATOR_INBOX_NOTICE_LIB_DIR:-this library}"
    return 1
  fi
  local out="" rc=0
  out="$(pp_run_ops pgai_agent_kanban.overwatch.inbox write_entry \
    --kanban-root "$kanban_root" --project "$project" --source "$source" \
    --question "$question" --meanwhile "$meanwhile" --how-to-answer "$how_to_answer" \
    --once 2>&1)" || rc=$?
  if [[ $rc -eq 0 && "$out" =~ (INBOX-[0-9]+) ]]; then
    printf '%s\n' "${BASH_REMATCH[1]}"
    return 0
  fi
  echo "no inbox entry written (exit ${rc}: ${out//$'\n'/ })"
  return 1
}
