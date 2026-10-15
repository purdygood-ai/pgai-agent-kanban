#!/usr/bin/env bash
# team/scripts/lib/block_record.sh
#
# Sourced bash entry points for the durable ## Block Record section of a
# task status.md. Every function is a thin call into the Python module
# pgai_agent_kanban.overwatch.block_record through pp_run_ops; the record's
# shape, its closed vocabularies and every rule live there (see its module
# docstring, or run it with --help). This file carries no inline Python.
#
# Usage
# -----
#   source team/scripts/lib/block_record.sh
#
#   block_record_write [--now ISO] <status_file> <reason_code> <owner_role> [evidence...]
#       Record a block. The first write appends the section with attempts 0,
#       cooldown_until none, resume fresh, requeues 0 and requeue_reasons
#       none; a re-block keeps first_blocked_at, attempts, cooldown_until,
#       resume, requeues and requeue_reasons, and replaces reason_code,
#       owner_role, evidence and last_blocked_at. Each evidence
#       argument is one key=value token; they are joined with "; ", e.g.
#         block_record_write "$status" per_task_timeout CODER \
#             "worktree=gone" "branch=ai_feature/example-task" "ahead=2"
#       Accepted codes and roles:
#         pp_run_ops pgai_agent_kanban.overwatch.block_record codes [--owner-roles]
#
#   block_record_field <status_file> <field> [default]
#       Print one field of the record (reason_code, owner_role, attempts,
#       first_blocked_at, last_blocked_at, cooldown_until, evidence, resume,
#       requeues, requeue_reasons), or <default> (empty when omitted) when
#       the task has no record.
#
#   block_record_update <status_file> [--attempts N | --bump-attempts]
#                       [--cooldown-until ISO|none] [--resume branch|fresh]
#                       [--record-requeue REASON_CODE]
#       Change the triage fields of an existing record; --record-requeue
#       adds one to requeues and appends the code to requeue_reasons.
#
#   block_record_remove <status_file>
#       Remove the section (a no-op when there is none); for DONE / WONT-DO.
#
# Gathering what a block site records (pgai_agent_kanban.overwatch.block_evidence;
# every one is best-effort, exits 0 and prints a documented placeholder for
# anything it cannot read — worktree=gone, branch=none, ahead=0, merged=no,
# batch_log=none, diag=none, span_s=unknown):
#
#   block_record_owner_role <status_file> [candidate...]
#       Print the first candidate that is an owner role, else the ## Role of
#       the README beside <status_file>, else nothing.
#
#   block_record_evidence [--worktree PATH] [--repo PATH] [--branch NAME]
#                         [--source NAME] [--task-dir DIR] [--refusal-signals]
#                         [--diag-log PATH]
#       Print the one-line evidence value: worktree, branch, ahead and merged,
#       then batch_log=<path>#<from>-<to> (--task-dir), span_s and signature
#       (--refusal-signals) and diag=<log>:<line> (--diag-log). Pass it to
#       block_record_write as one quoted argument.
#
#   block_record_worktree_failure_code <repo> <source_branch>
#       Print source_branch_missing when <source_branch> is absent from
#       <repo>, else worktree_create_failed.
#
#   block_record_zero_progress_code <task_dir>
#       Print provider_refusal when the task's latest dispatch lasted under
#       60 s or its batch-log tail carries an authentication, model-not-found
#       or usage-limit signature, else zero_progress.
#
# Return status: 0 on success; 1 when the module refused — it has printed
# error/message/remedy on stderr and status.md is unchanged (an unknown
# reason_code or owner_role, a missing status.md, an update with nothing to
# change or no record to change).
#
# A block site calls block_record_write beside its own BLOCKED transition
# and never lets a refusal stop that transition — under `set -e`, write
#   block_record_write ... || log "WARNING: Block Record not written for ${task_id}"
# A wake block site looks like (owner role from the parsed ## Role, falling
# back to the queue agent; evidence from git and the batch log):
#   block_record_write "$task_status" per_task_timeout \
#     "$(block_record_owner_role "$task_status" "$role" "$SUBAGENT")" \
#     "$(block_record_evidence --worktree "$wt" --branch "$fb" --source "$sb" \
#          --task-dir "$task_dir" --diag-log "$LOG_FILE")" \
#     || log "task ${task_id}: WARNING: Block Record not written"
# The write is atomic and preserves every other status.md section
# byte-for-byte. promote_blocked_to_backlog (wake_task_state.sh) leaves the
# section in place, so the record survives a return to BACKLOG.

_BLOCK_RECORD_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# A caller's own pp_run_ops (wake_bootstrap.sh's, or a test stub) wins.
# shellcheck source=pp_run_ops.sh
declare -F pp_run_ops >/dev/null || source "${_BLOCK_RECORD_LIB_DIR}/pp_run_ops.sh"

block_record_write() {
  pp_run_ops pgai_agent_kanban.overwatch.block_record write "$@"
}

block_record_field() {
  pp_run_ops pgai_agent_kanban.overwatch.block_record read "$1" --field="$2" --default="${3:-}"
}

block_record_update() {
  pp_run_ops pgai_agent_kanban.overwatch.block_record update "$@"
}

block_record_remove() {
  pp_run_ops pgai_agent_kanban.overwatch.block_record remove "$1"
}

block_record_owner_role() {
  pp_run_ops pgai_agent_kanban.overwatch.block_evidence owner-role "$@"
}

block_record_evidence() {
  pp_run_ops pgai_agent_kanban.overwatch.block_evidence evidence "$@"
}

block_record_worktree_failure_code() {
  pp_run_ops pgai_agent_kanban.overwatch.block_evidence worktree-failure-code "$@"
}

block_record_zero_progress_code() {
  pp_run_ops pgai_agent_kanban.overwatch.block_evidence zero-progress-code "$@"
}
