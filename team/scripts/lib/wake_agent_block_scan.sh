#!/usr/bin/env bash
# team/scripts/lib/wake_agent_block_scan.sh
#
# Wake post-exit stage: the provider-error check for a block an agent wrote
# itself with the catch-all reason code. An agent whose subagent died of a
# provider error can survive it, record BLOCKED through the status-writer
# tool (reason_code agent_unclassified, Needs Human yes) and exit cleanly.
# Such a task never reaches handle_working_after_exit's WORKING branch, so
# this stage gives its log tail a transient check of its own: a transient
# error the provider itself reported turns the block into a transient retry
# instead of a wait for a human.
#
# What can reclassify the block
# -----------------------------
# The log tail ends with the agent's own final reply, and that reply names
# files, numbers and words that the transient signatures also match (a file
# rate_limit.sh, "500 chars", an "HTTP 503" section). So only the provider's
# own error lines in the tail count, as agent_block_provider_lines selects
# them:
#   * a line that opens with "API Error:" (after optional leading
#     whitespace) -- how the Claude CLI reports a failed API call;
#   * a JSON event line reporting an error -- "type": "error" or
#     "type": "turn.failed" (the codex event stream), or "is_error": true
#     (a Claude CLI result object written to the log unparsed).
# The block becomes a transient retry only when the whole tail classes
# transient (so a billing or refusal signature anywhere in it still
# outranks a 5xx) AND those provider lines alone class transient. Prose,
# however many signature words it carries, reclassifies nothing: the
# agent's record, ## Blockers and Needs Human stay as the agent wrote them.
# The WORKING-task check is unchanged; it still classes every tail line.
#
# Public entry points
# -------------------
#   reclassify_agent_unclassified_block <task_id> <task_status> <log_file> <agent_exit>
#       Reads the ## Block Record reason_code. Only agent_unclassified is
#       checked; any other code (agent_transient_provider_error and
#       agent_operator_data_action included) and a task with no record
#       return 1 with the status file untouched. For agent_unclassified it
#       calls handle_working_after_exit in agent-block mode
#       (wake_lifecycle.sh), which classes the tail of <log_file> by the
#       rule above. On a transient class only, that function's transient
#       path records ## Transient Retry (it is the one writer, with the one
#       counter and ceiling), writes Blocked Reason, Blockers and Needs
#       Human by that path's own rule (no below the ceiling, yes at it),
#       quoting the provider's own line, and rewrites the Block Record to
#       transient_api_error with evidence from the log; this function then
#       prints BLOCKED and returns 0. A billing or refusal signature, a
#       transient word only in prose, or none, leaves the agent's record and
#       every other section as written and returns 1. ## State stays
#       BLOCKED either way.
#
#   agent_block_provider_lines <text>
#       Print the lines of <text> that are the provider's own error lines
#       (the list above), in order; print nothing when there are none.
#       Always exits 0. Called by handle_working_after_exit in agent-block
#       mode.
#
# Called by wd_finalize_task (wake_driver_finalize.sh) for a task its agent
# left BLOCKED, never for one the per-task timeout blocked.
#
# Dependencies (defined before the function runs):
#   wake_lifecycle.sh -- handle_working_after_exit and its own dependencies
#                        (the signature reader, the retry record, the state
#                        writers), as a wake has them loaded.
#   block_record.sh   -- block_record_field; self-sourced below when absent.
#   grep -E           -- agent_block_provider_lines.
#   log()             -- wake_bootstrap.sh.

# shellcheck source=block_record.sh
declare -F block_record_field >/dev/null || source "$(dirname "${BASH_SOURCE[0]}")/block_record.sh"

reclassify_agent_unclassified_block() {
  local task_id="${1:-}"
  local task_status="${2:-}"
  local log_file="${3:-}"
  local agent_exit="${4:-}"

  local _code
  _code="$(block_record_field "$task_status" reason_code "")" || _code=""
  [[ "$_code" == "agent_unclassified" ]] || return 1

  if handle_working_after_exit "$task_id" "$task_status" "$log_file" "$agent_exit" agent-block; then
    log "task ${task_id}: agent's agent_unclassified block reclassified transient_api_error (transient provider error in ${log_file})"
    return 0
  fi
  log "task ${task_id}: agent's agent_unclassified block stands (no transient provider error in the tail of ${log_file})"
  return 1
}

# The provider's own error lines, as the header lists them; matched
# case-insensitively with grep -E, one line at a time.
_AGENT_BLOCK_PROVIDER_LINE_RE='^[[:space:]]*(API Error:|\{.*"(type"[[:space:]]*:[[:space:]]*"(error|turn\.failed)"|is_error"[[:space:]]*:[[:space:]]*true))'

agent_block_provider_lines() {
  # Here-string, not echo piped into grep (see provider_signatures.sh).
  grep -iE -- "$_AGENT_BLOCK_PROVIDER_LINE_RE" <<< "${1:-}" || true
}
