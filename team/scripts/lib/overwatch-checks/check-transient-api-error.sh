#!/usr/bin/env bash
# team/scripts/lib/overwatch-checks/check-transient-api-error.sh
#
# OVERWATCH Tier-1 detection module: detect BLOCKED tasks whose provider
# output tail matches known transient API error signatures, then either
# auto-requeue the task or bug-file it (if the requeue ceiling is reached).
#
# Which log is read: the provider CLI's stdout/stderr for a dispatched task
# goes to the wake firing's batch log ($KANBAN_ROOT/logs/agents/
# <agent>-batch-<ts>.log), not to the task's own logs/ directory (which
# holds only what the agent itself writes). The wake records, per dispatch,
# which batch log it wrote and the byte span belonging to that task in the
# task's logs/dispatch.jsonl ledger (dispatch_record_* in
# overwatch_protocol.sh). This check resolves the task's most recent
# dispatch from that ledger, extracts that span of the batch log, and
# matches the signatures against its last 50 lines. A BLOCKED task with no
# ledger (never dispatched by a wake, or dispatched before the ledger
# existed) or whose recorded batch log is gone is narrated as skipped —
# never an error, never a handled case, and never a reason to stop the
# sweep. The task's own logs/*.log files are no longer consulted.
#
# Transient signatures (any match triggers):
#   - "API Error: 5xx"  (any 5-series HTTP status in that phrase)
#   - "Overloaded"
#   - "overloaded_error"
#   - "rate limit"
#   - "429"
#   - "503"
#   - "529"
#
# This module reads and writes the SAME durable ## Transient Retry
# record that wake_common.sh's handle_working_after_exit owns (see
# overwatch_protocol.sh: transient_retry_field / transient_retry_write /
# transient_retry_limit). There is exactly one retry_count and one ceiling —
# this module no longer maintains an independent ## Transient Requeue Count
# field or a hardcoded cap; the ceiling is transient_retry_limit()'s resolved
# value (kanban.cfg [wake] transient_retry_cap, default 3 — the same value
# handle_working_after_exit enforces).
#
# Backoff (ceiling not reached): a requeue waits out a window that grows with
# the record's retry_count — 5, 10, then 20 minutes as shipped (the
# transient_api_error row of the block-triage classifier table) — measured
# from the ## Block Record's last_blocked_at (transient_backoff_due in
# overwatch_protocol.sh, the same gate the wake's auto-retry uses). Inside
# the window the task is left exactly as found, a "transient-requeue-backoff"
# row is logged, and a later sweep looks again. No Block Record means no
# window (requeued at once, as before the backoff existed).
#
# Requeue path (ceiling not reached, backoff window passed):
#   1. Back up status.md via overwatch_backup_file.
#   2. Reset State to BACKLOG.
#   3. Increment retry_count in the durable ## Transient Retry record via
#      transient_retry_write (append the record if absent).
#   4. Append "TRANSIENT" to ## Labels in status.md (append field if absent).
#   5. Log action via overwatch_log_action.
#
# Ceiling path (retry_count already >= transient_retry_limit):
#   1. Write a bug report file under $KANBAN_ROOT/projects/$OVERWATCH_PROJECT/bugs/.
#   2. Log action via overwatch_log_action.
#   (Task is NOT modified — a human or future iteration acts on the bug.)
#
# Worktree cleanup (requeue path only, inside the locked fix):
#   The requeue runs under overwatch_halt_first_fix, holding the project's
#   repo lock. Before the task's state flips to BACKLOG, each of the task's
#   worktrees in the dev tree goes through the same safe removal path the
#   stale-worktree check uses for finished tasks
#   (pgai_agent_kanban.overwatch.worktree_residue assess-finished /
#   remove-finished): `git worktree remove` without --force, then prune.
#     - A clean worktree reaching no commit found elsewhere is removed and
#       logged "worktree-pruned" with backup "none" (nothing restorable is
#       saved for a checkout git can recreate).
#     - A worktree reaching commits no other branch, tag or remote-tracking
#       ref holds (checked against every ref, never a bare "main") is kept
#       and bug-filed ("worktree-carries-commits-bug-filed").
#     - Any other worktree that must be kept — uncommitted or untracked
#       changes, locked, unreadable — is kept and logged "worktree-kept".
#   A wake cannot take the lock between the cleanup and the requeue, so it
#   never dispatches the task onto a worktree being removed. The ceiling
#   (escalation) path leaves every worktree in place: it is the state the
#   human is asked to investigate.
#
# This script is both:
#   - Sourceable (for the OVERWATCH driver or sweep runner): source this file and call
#     overwatch_check_transient_api_error [--dry-run]
#   - Directly invokable: bash check-transient-api-error.sh [--dry-run]
#
# Required environment variables (when sourced by OVERWATCH driver):
#   KANBAN_ROOT       — absolute path to the kanban root
#   OVERWATCH_PROJECT — project name (e.g. "pgai-agent-kanban")
#
# When invoked directly, KANBAN_ROOT defaults to:
#   ${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}
# OVERWATCH_PROJECT defaults to the single registered project (errors on multi).
#
# Usage:
#   bash check-transient-api-error.sh [--dry-run]
#
# Exit codes:
#   0 — completed successfully (no transient tasks, or transient tasks handled)
#   1 — internal error (missing dependencies, unreadable state, etc.), OR one
#       or more repair attempts (requeue-to-BACKLOG or ceiling-hit
#       Needs-Human escalation) failed partway through. A failed repair is
#       never reported as success: overwatch_check_transient_api_error counts
#       repair failures and returns non-zero (with a narrated summary line on
#       stderr) whenever that count is greater than zero, even though other
#       tasks in the same sweep may have been handled successfully.
#
# --dry-run: scans and logs findings but does NOT modify any task files.

# ---------------------------------------------------------------------------
# _ctae_resolve_env
# Resolve KANBAN_ROOT, OVERWATCH_PROJECT, and dev tree path from the environment.
# Sets _CTAE_DEV_TREE and _CTAE_BRANCH_PREFIX in the calling scope.
# Returns 0 on success, 1 on failure.
# ---------------------------------------------------------------------------
_ctae_resolve_env() {
    if [[ -z "${KANBAN_ROOT:-}" ]]; then
        KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-${HOME}/pgai_agent_kanban}"
    fi
    if [[ ! -d "${KANBAN_ROOT}" ]]; then
        echo "check-transient-api-error: KANBAN_ROOT does not exist: ${KANBAN_ROOT}" >&2
        return 1
    fi

    if [[ -z "${OVERWATCH_PROJECT:-}" ]]; then
        local _all_projects=""
        if declare -f projects_cfg_list >/dev/null 2>&1; then
            _all_projects="$(projects_cfg_list 2>/dev/null)"
        else
            local _cfg="${KANBAN_ROOT}/projects.cfg"
            _all_projects="$(awk '/^\[project:[a-zA-Z0-9_-]+\]/{match($0,/\[project:([a-zA-Z0-9_-]+)\]/,a);print a[1]}' "${_cfg}" 2>/dev/null)"
        fi
        local _project_count
        _project_count="$(echo "${_all_projects}" | grep -c '[^[:space:]]' 2>/dev/null || echo 0)"
        if [[ "${_project_count}" -gt 1 ]]; then
            echo "check-transient-api-error: ERROR: OVERWATCH_PROJECT is not set and multiple projects are registered." >&2
            echo "  Set OVERWATCH_PROJECT to the target project name before running this check." >&2
            echo "  Registered projects: $(echo "${_all_projects}" | tr '\n' ' ')" >&2
            return 1
        fi
        OVERWATCH_PROJECT="$(echo "${_all_projects}" | grep '[^[:space:]]' | head -n1)"
        if [[ -z "${OVERWATCH_PROJECT:-}" ]]; then
            echo "check-transient-api-error: ERROR: no project specified and none resolvable from projects.cfg" >&2
            echo "  Set OVERWATCH_PROJECT or register a project in ${KANBAN_ROOT}/projects.cfg" >&2
            return 1
        fi
    fi

    # Resolve dev tree path from project.cfg (or PROJECT.cfg legacy)
    local _proj_root="${KANBAN_ROOT}/projects/${OVERWATCH_PROJECT}"
    local _cfg_file=""
    if [[ -f "${_proj_root}/project.cfg" ]]; then
        _cfg_file="${_proj_root}/project.cfg"
    elif [[ -f "${_proj_root}/PROJECT.cfg" ]]; then
        _cfg_file="${_proj_root}/PROJECT.cfg"
    fi
    _CTAE_DEV_TREE=""
    if [[ -n "${_cfg_file}" ]]; then
        _CTAE_DEV_TREE="$(grep -E '^[[:space:]]*dev_tree_path[[:space:]]*=' "${_cfg_file}" \
            | head -n1 \
            | sed 's|^[^=]*=[[:space:]]*||; s|[[:space:]]*$||; s|^["'"'"']||; s|["'"'"']$||')"
    fi
    if [[ -z "${_CTAE_DEV_TREE}" ]] && [[ -n "${PGAI_DEV_TREE_PATH:-}" ]]; then
        _CTAE_DEV_TREE="${PGAI_DEV_TREE_PATH}"
    fi

    # branch_prefix — optional; default empty
    _CTAE_BRANCH_PREFIX=""
    if [[ -n "${_cfg_file}" ]]; then
        local _raw_prefix
        _raw_prefix="$(grep -E '^[[:space:]]*branch_prefix[[:space:]]*=' "${_cfg_file}" \
            | head -n1 \
            | sed 's|^[^=]*=[[:space:]]*||; s|[[:space:]]*$||; s|^["'"'"']||; s|["'"'"']$||')"
        _CTAE_BRANCH_PREFIX="${_raw_prefix:-}"
    fi

    return 0
}

# ---------------------------------------------------------------------------
# _ctae_extract_field <status_file> <field_name>
# Extract the body of a ## Field section from a status.md file.
# Echoes the trimmed content, or empty string if not found.
# ---------------------------------------------------------------------------
_ctae_extract_field() {
    local status_file="$1"
    local field_name="$2"

    if [[ ! -f "${status_file}" ]]; then
        echo ""
        return 0
    fi

    awk -v field="${field_name}" '
        $0 ~ "^## " field "$" { found=1; next }
        found && /^## / { exit }
        found { lines = lines $0 "\n" }
        END {
            gsub(/^[[:space:]]+/, "", lines)
            gsub(/[[:space:]]+$/, "", lines)
            print lines
        }
    ' "${status_file}"
}

# ---------------------------------------------------------------------------
# _ctae_log_tail <task_id> <tasks_root>
# Emit the last 50 lines of the provider output belonging to the task's most
# recent dispatch: the byte span of the wake batch log that the task's
# logs/dispatch.jsonl ledger records (dispatch_record_latest). An open span
# (no "returned" record yet) runs to the end of the batch log.
#
# Returns:
#   0 — span extracted (output may still be empty if the span is empty)
#   2 — no dispatch ledger / no usable record for this task
#   3 — the recorded batch log no longer exists (rotated or deleted)
# On 2 and 3 nothing is emitted; the caller narrates the skip from the exit
# code. Neither is an error condition.
# ---------------------------------------------------------------------------
_ctae_log_tail() {
    local task_id="$1"
    local tasks_root="$2"

    local task_dir="${tasks_root}/${task_id}"
    local record
    if ! record="$(dispatch_record_latest "${task_dir}")" || [[ -z "${record}" ]]; then
        return 2
    fi

    local batch_log offset_start offset_end
    IFS=$'\t' read -r batch_log offset_start offset_end <<< "${record}"
    if [[ -z "${batch_log}" || ! -f "${batch_log}" ]]; then
        return 3
    fi
    [[ "${offset_start}" =~ ^[0-9]+$ ]] || offset_start=0

    # tail -c +N is 1-based: byte offset N-1 is the first byte emitted.
    if [[ "${offset_end}" =~ ^[0-9]+$ ]] && (( offset_end > offset_start )); then
        tail -c +"$(( offset_start + 1 ))" "${batch_log}" 2>/dev/null \
            | head -c "$(( offset_end - offset_start ))" \
            | tail -n 50
    else
        tail -c +"$(( offset_start + 1 ))" "${batch_log}" 2>/dev/null | tail -n 50
    fi
    return 0
}

# ---------------------------------------------------------------------------
# _ctae_matches_transient_signature <text>
# Returns 0 (true) if the text contains any known transient API error signature.
#
# Each signature is tested with a here-string, never by piping echo into
# grep -q: grep -q exits on its first match, a still-writing echo then dies of
# SIGPIPE, and under the callers' `set -o pipefail` that turns a match into a
# miss.
# ---------------------------------------------------------------------------
_ctae_matches_transient_signature() {
    local text="$1"

    # Check each transient signature
    if grep -qE 'API Error: 5[0-9][0-9]' <<< "${text}"; then
        return 0
    fi
    if grep -q 'Overloaded' <<< "${text}"; then
        return 0
    fi
    if grep -q 'overloaded_error' <<< "${text}"; then
        return 0
    fi
    if grep -qi 'rate limit' <<< "${text}"; then
        return 0
    fi
    if grep -qE '\b429\b' <<< "${text}"; then
        return 0
    fi
    if grep -qE '\b503\b' <<< "${text}"; then
        return 0
    fi
    if grep -qE '\b529\b' <<< "${text}"; then
        return 0
    fi
    return 1
}

# ---------------------------------------------------------------------------
# _ctae_read_requeue_count <status_file>
# Echo the integer retry_count from the durable ## Transient Retry record
# (the SAME record wake_common.sh's handle_working_after_exit
# writes; see overwatch_protocol.sh: transient_retry_field), or 0 if the
# record is absent or the field is non-numeric.
# ---------------------------------------------------------------------------
_ctae_read_requeue_count() {
    local status_file="$1"
    local raw
    raw="$(transient_retry_field "${status_file}" "retry_count" "0")"
    raw="$(echo "${raw}" | tr -d '[:space:]')"
    if [[ "${raw}" =~ ^[0-9]+$ ]]; then
        echo "${raw}"
    else
        echo "0"
    fi
}

# ---------------------------------------------------------------------------
# _ctae_bug_file <task_id> <reason> <bugs_dir>
# Write a bug report for the task under bugs_dir.
# Echoes the path to the created bug file.
# Returns 0 on success, 1 on failure.
# ---------------------------------------------------------------------------
_ctae_bug_file() {
    local task_id="$1"
    local reason="$2"
    local bugs_dir="$3"
    local timestamp
    timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
    local bug_file="${bugs_dir}/BUG-overwatch-transient-${task_id}-${timestamp}.md"
    local _cap
    _cap="$(transient_retry_limit)"

    mkdir -p "${bugs_dir}" 2>/dev/null || {
        echo "check-transient-api-error: cannot create bugs dir: ${bugs_dir}" >&2
        return 1
    }

    cat > "${bug_file}" <<EOF
# Bug: Transient API Error — ${task_id}

## Status
open

## Filed By
overwatch/check-transient-api-error

## Filed At
${timestamp}

## Task
${task_id}

## Reason
${reason}

## Description
OVERWATCH detected a transient API error pattern in the provider batch log
recorded for the task's most recent dispatch, but the
auto-requeue ceiling (${_cap}, from the durable ## Transient Retry record)
has been reached, or the task has a worktree carrying commits
that must not be auto-pruned. Manual intervention required.
EOF

    echo "${bug_file}"
    return 0
}

# ---------------------------------------------------------------------------
# _ctae_render_requeue <status_file> <new_count>
# Rewrite <status_file> for a transient requeue through
# pgai_agent_kanban.task_status (read --raw for ## Labels, then one render
# committed by pgai_atomic_write), in this order:
#   ## State -> BACKLOG
#   ## Labels: "TRANSIENT" appended to the existing labels (one space
#     between) unless the word is already there; an empty body becomes
#     "TRANSIENT"; an absent section is appended at the end of the file
#   ## Blockers -> none, ## Needs Human -> no
#   ## Summary -> the requeue narration naming <new_count>
#   every run of blank lines cut to one
# A section other than Labels that the file lacks is left alone. Returns
# non-zero, leaving the file untouched, when it cannot be read or written.
# ---------------------------------------------------------------------------
_ctae_render_requeue() {
    local status_file="$1"
    local new_count="$2"

    overwatch_load_module_runner || return 1

    local labels="" labels_rc=0
    labels="$(pp_run_ops pgai_agent_kanban.task_status read "${status_file}" \
        --section=Labels --raw)" || labels_rc=$?

    local -a label_args=()
    case "${labels_rc}" in
        0)
            if [[ "${labels}" != *TRANSIENT* ]]; then
                label_args=(--section=Labels --body="${labels:+${labels} }TRANSIENT")
            fi
            ;;
        3)
            label_args=(--section=Labels --body=TRANSIENT --append-missing=Labels)
            ;;
        *)
            return 1
            ;;
    esac

    pgai_atomic_write "${status_file}" pp_run_ops pgai_agent_kanban.task_status render \
        "${status_file}" \
        --section=State --body=BACKLOG \
        "${label_args[@]}" \
        --section=Blockers --body=none \
        --section="Needs Human" --body=no \
        --section=Summary \
        --body="Auto-requeued by OVERWATCH check-transient-api-error: transient API error detected in provider batch log. Requeue #${new_count}." \
        --skip-missing --collapse-blank-lines
}

# ---------------------------------------------------------------------------
# _ctae_do_requeue
# Inner function invoked by the requeue path.
# Reads: _CTAE_TASK_ID, _CTAE_STATUS_FILE, _CTAE_NEW_COUNT, _CTAE_QUEUES_DIR
# from the environment.
#
# Actions:
#   1. Backup status.md
#   2. Update the durable ## Transient Retry record (retry_count, retry_limit,
#      reason_code, provider, last_error) via transient_retry_write — the
#      SAME record and ceiling wake_common.sh's handle_working_after_exit uses
#      (one record, one ceiling, no independent OVERWATCH counter).
#   3. Reset State -> BACKLOG, add TRANSIENT label, clear Blockers/Needs Human
#   4. Log the action
# ---------------------------------------------------------------------------
_ctae_do_requeue() {
    local task_id="${_CTAE_TASK_ID}"
    local status_file="${_CTAE_STATUS_FILE}"
    local new_count="${_CTAE_NEW_COUNT}"
    local retry_limit
    retry_limit="$(transient_retry_limit)"

    # Backup status.md before any change
    local backup_path="none"
    local bpath
    bpath="$(overwatch_backup_file "${status_file}")" || {
        echo "check-transient-api-error: backup failed for ${status_file}" >&2
        return 1
    }
    backup_path="${bpath}"

    # Update the ONE durable retry record first (transient_retry_write is the
    # shared writer defined in overwatch_protocol.sh; see the module header).
    if ! transient_retry_write "${status_file}" "transient_api_error" "${new_count}" \
        "${retry_limit}" "overwatch" "transient signature matched in provider batch log"; then
        echo "check-transient-api-error: transient_retry_write failed for ${status_file}" >&2
        return 1
    fi

    # Update the remaining status.md fields in one atomic write: State ->
    # BACKLOG, TRANSIENT added to ## Labels (the section appended when
    # absent), Blockers -> none, Needs Human -> no, a requeue Summary, then
    # blank-line runs cut to one. A section the file lacks (other than
    # Labels) is left alone.
    local edit_exit=0
    _ctae_render_requeue "${status_file}" "${new_count}" || edit_exit=$?

    if (( edit_exit != 0 )); then
        echo "check-transient-api-error: status update failed for ${task_id}" >&2
        return 1
    fi

    # Log the action
    overwatch_log_action \
        "check-transient-api-error" \
        "${task_id}" \
        "transient-auto-requeued" \
        "${backup_path}" \
        "Transient API error in provider batch log; reset to BACKLOG (requeue #${new_count} of ${retry_limit})" \
    || true

    return 0
}

# ---------------------------------------------------------------------------
# _ctae_do_escalate
# Inner function invoked when the retry ceiling is reached.
# Reads: _CTAE_TASK_ID, _CTAE_STATUS_FILE, _CTAE_CURRENT_COUNT, _CTAE_CAP
# from the environment.
#
# Sets Needs Human=yes and narrates Blocked Reason so a ceiling hit detected
# by OVERWATCH is exactly as consistent as one detected by wake_common.sh's
# handle_working_after_exit — "at ceiling: BLOCKED + Needs Human=yes,
# narrated" applies identically regardless of which controller observed the
# ceiling. The task is NOT requeued (bug-filing already happened in the
# caller); this only escalates the human-attention flag.
# ---------------------------------------------------------------------------
_ctae_do_escalate() {
    local task_id="${_CTAE_TASK_ID}"
    local status_file="${_CTAE_STATUS_FILE}"
    local current_count="${_CTAE_CURRENT_COUNT}"
    local cap="${_CTAE_CAP}"

    local backup_path="none"
    local bpath
    bpath="$(overwatch_backup_file "${status_file}")" || {
        echo "check-transient-api-error: backup failed for ${status_file}" >&2
        return 1
    }
    backup_path="${bpath}"

    # Needs Human -> yes and the ceiling narrated in ## Blocked Reason (the
    # section appended when absent), then blank-line runs cut to one, in
    # one atomic write.
    local edit_exit=0
    overwatch_load_module_runner || return 1
    pgai_atomic_write "${status_file}" pp_run_ops pgai_agent_kanban.task_status render \
        "${status_file}" \
        --section="Needs Human" --body="yes" \
        --section="Blocked Reason" \
        --body="Transient API error retry ceiling reached (${current_count}/${cap}, see ## Transient Retry record); human investigation required." \
        --append-missing="Blocked Reason" --skip-missing --collapse-blank-lines \
        || edit_exit=$?

    if (( edit_exit != 0 )); then
        echo "check-transient-api-error: needs-human escalation failed for ${task_id}" >&2
        return 1
    fi

    # The escalated block's ## Block Record (block_record.sh, loaded by
    # overwatch_load_module_runner above); a refused write is only narrated.
    block_record_write "${status_file}" transient_api_error "$(block_record_owner_role "${status_file}")" \
        "$(block_record_evidence --task-dir "$(dirname "${status_file}")")" \
        || echo "check-transient-api-error: ${task_id}: Block Record not written" >&2

    overwatch_log_action \
        "check-transient-api-error" \
        "${task_id}" \
        "transient-ceiling-needs-human-escalated" \
        "${backup_path}" \
        "Transient retry ceiling ${current_count}/${cap} reached; Needs Human set to yes" \
    || true

    return 0
}

# ---------------------------------------------------------------------------
# _ctae_find_task_worktrees <dev_tree> <task_id>
# List all worktree paths for the given task_id using git worktree list.
# Echoes one absolute path per line.
# ---------------------------------------------------------------------------
_ctae_find_task_worktrees() {
    local dev_tree="$1"
    local task_id="$2"

    if [[ -z "${dev_tree}" || ! -d "${dev_tree}" ]]; then
        return 0
    fi

    # git worktree list output: <path> <sha> [<branch>]
    git -C "${dev_tree}" worktree list --porcelain 2>/dev/null \
        | awk '/^worktree /{print $2}' \
        | grep -F "${task_id}" || true
}

# ---------------------------------------------------------------------------
# _ctae_clear_task_worktrees <task_id> <dev_tree> <bugs_dir> <dry_run>
# The requeue path's worktree cleanup (see the module header). Live runs
# happen inside the locked fix, before the status edit; a dry run narrates
# what a live run would do and changes nothing.
# Returns 0 when every worktree was removed or deliberately kept, 1 when a
# removal failed partway (narrated on stderr).
# ---------------------------------------------------------------------------
_ctae_clear_task_worktrees() {
    local task_id="$1" dev_tree="$2" bugs_dir="$3" dry_run="$4"

    if [[ -z "${dev_tree}" ]]; then
        # Dev tree not configured — nothing to clean up
        return 0
    fi

    local wt_paths
    wt_paths="$(_ctae_find_task_worktrees "${dev_tree}" "${task_id}")"
    if [[ -z "${wt_paths}" ]]; then
        echo "check-transient-api-error: ${task_id}: no per-task worktrees found; residue check clean" >&2
        return 0
    fi
    overwatch_load_module_runner || return 1

    local wt_path verdict why out rc failures=0
    while IFS= read -r wt_path; do
        [[ -z "${wt_path}" ]] && continue
        echo "check-transient-api-error: ${task_id}: found worktree ${wt_path}" >&2
        verdict="$(pp_run_ops pgai_agent_kanban.overwatch.worktree_residue assess-finished \
            --repo "${dev_tree}" --path "${wt_path}" 2>/dev/null)" || verdict="keep"$'\t'"the worktree could not be assessed"
        why="${verdict#*$'\t'}"

        if [[ "${verdict}" == removable* ]]; then
            if (( dry_run == 1 )); then
                echo "check-transient-api-error: [dry-run] would remove worktree ${wt_path}" >&2
                overwatch_log_action "check-transient-api-error" "${wt_path}" "dry-run-worktree-would-prune" "none" \
                    "Clean worktree for ${task_id} at ${wt_path}; dry-run, no action" 2>/dev/null || true
                continue
            fi
            rc=0
            out="$(pp_run_ops pgai_agent_kanban.overwatch.worktree_residue remove-finished \
                --repo "${dev_tree}" --path "${wt_path}" --project "${OVERWATCH_PROJECT:-}")" || rc=$?
            if (( rc == 0 )); then
                echo "check-transient-api-error: ${task_id}: removed worktree ${wt_path}" >&2
                overwatch_log_action "check-transient-api-error" "${wt_path}" "worktree-pruned" "none" \
                    "Worktree for requeued task ${task_id} removed under the repo lock before the requeue, without --force; no branch or commit deleted; was ${out#*$'\t'}" || true
                continue
            elif (( rc == 3 )); then
                # The re-check under the lock found a reason to keep it (it
                # refused dual-channel on stderr itself).
                why="the re-check under the repo lock refused the removal"
            else
                echo "check-transient-api-error: ${task_id}: worktree removal of ${wt_path} failed (exit ${rc})" >&2
                failures=$(( failures + 1 ))
                continue
            fi
        fi

        if [[ "${why}" == *"found nowhere else"* ]]; then
            echo "check-transient-api-error: ${task_id}: worktree ${wt_path} carries commits; kept and bug-filed" >&2
            if (( dry_run == 1 )); then
                overwatch_log_action "check-transient-api-error" "${wt_path}" "dry-run-worktree-commits-would-bug-file" "none" \
                    "Worktree for ${task_id} at ${wt_path} has local commits; dry-run, no action" 2>/dev/null || true
                continue
            fi
            local bug_path
            bug_path="$(_ctae_bug_file "${task_id}" \
                "Worktree ${wt_path} carries commits; cannot auto-prune" \
                "${bugs_dir}")" || true
            overwatch_log_action "check-transient-api-error" "${wt_path}" "worktree-carries-commits-bug-filed" "none" \
                "Worktree for ${task_id} at ${wt_path} kept: ${why}; bug filed at ${bug_path:-unknown}" || true
        else
            echo "check-transient-api-error: ${task_id}: worktree ${wt_path} kept: ${why}" >&2
            overwatch_log_action "check-transient-api-error" "${wt_path}" \
                "$( (( dry_run == 1 )) && echo dry-run-worktree-would-keep || echo worktree-kept )" "none" \
                "Worktree for ${task_id} at ${wt_path} kept: ${why}" 2>/dev/null || true
        fi
    done <<< "${wt_paths}"

    (( failures == 0 ))
}

# ---------------------------------------------------------------------------
# _ctae_do_requeue_clearing_worktrees
# The requeue fix run by overwatch_halt_first_fix: the task's worktrees are
# cleared first (lock held, task still BLOCKED), then _ctae_do_requeue
# flips it to BACKLOG. Reads _CTAE_FIX_DEV_TREE and _CTAE_FIX_BUGS_DIR besides
# _ctae_do_requeue's own inputs. A failed removal leaves the task BLOCKED
# for a later sweep.
# ---------------------------------------------------------------------------
_ctae_do_requeue_clearing_worktrees() {
    _ctae_clear_task_worktrees "${_CTAE_TASK_ID}" "${_CTAE_FIX_DEV_TREE:-}" "${_CTAE_FIX_BUGS_DIR:-}" 0 || {
        echo "check-transient-api-error: ${_CTAE_TASK_ID}: worktree cleanup failed; task left BLOCKED for a later sweep" >&2
        return 1
    }
    _ctae_do_requeue
}

# ---------------------------------------------------------------------------
# _ctae_find_blocked_task_ids <queues_dir> <tasks_root>
# Scan all queue *_backlog.md files for [B] markers pointing to tasks whose
# status.md confirms State: BLOCKED.
# Echoes one task_id per line (unique).
# ---------------------------------------------------------------------------
_ctae_find_blocked_task_ids() {
    local queues_dir="$1"
    local tasks_root="$2"

    if [[ ! -d "${queues_dir}" ]]; then
        return 0
    fi

    local seen_ids=()
    local queue_file task_id status_file state

    while IFS= read -r queue_file; do
        [[ -f "${queue_file}" ]] || continue

        while IFS= read -r line; do
            if [[ "${line}" =~ ^[[:space:]]*-?[[:space:]]*\[[[:space:]]*B[[:space:]]*\][[:space:]]+([A-Za-z0-9._-]+) ]]; then
                task_id="${BASH_REMATCH[1]}"
                [[ -z "${task_id}" ]] && continue

                # Dedup
                local dup=0
                local seen_id
                for seen_id in "${seen_ids[@]}"; do
                    if [[ "${seen_id}" == "${task_id}" ]]; then
                        dup=1; break
                    fi
                done
                (( dup == 1 )) && continue

                # Confirm status.md says BLOCKED
                status_file="${tasks_root}/${task_id}/status.md"
                if [[ ! -f "${status_file}" ]]; then
                    continue
                fi
                state="$(_ctae_extract_field "${status_file}" "State" | tr '[:lower:]' '[:upper:]')"
                if [[ "${state}" == "BLOCKED" ]]; then
                    echo "${task_id}"
                    seen_ids+=("${task_id}")
                fi
            fi
        done < "${queue_file}"
    done < <(find "${queues_dir}" -maxdepth 2 -name '*_backlog.md' -type f 2>/dev/null)
}

# ---------------------------------------------------------------------------
# overwatch_check_transient_api_error [--dry-run]
# Main detection and action function.
# Returns 0 on success, 1 on internal error.
# ---------------------------------------------------------------------------
overwatch_check_transient_api_error() {
    local dry_run=0
    local arg
    for arg in "$@"; do
        if [[ "${arg}" == "--dry-run" ]]; then
            dry_run=1
        fi
    done

    _ctae_resolve_env || return 1

    local project_name="${OVERWATCH_PROJECT}"
    local project_root="${KANBAN_ROOT}/projects/${project_name}"
    local tasks_root="${project_root}/tasks"
    local queues_dir="${tasks_root}/queues"
    local bugs_dir="${project_root}/bugs"
    local dev_tree="${_CTAE_DEV_TREE:-}"

    if [[ ! -d "${project_root}" ]]; then
        echo "check-transient-api-error: project root does not exist: ${project_root}" >&2
        return 1
    fi

    # Source overwatch_protocol.sh if not already loaded
    if ! declare -f overwatch_log_action >/dev/null 2>&1; then
        local lib_dir
        lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)" || {
            echo "check-transient-api-error: cannot resolve lib dir" >&2
            return 1
        }
        if [[ -f "${lib_dir}/../overwatch_protocol.sh" ]]; then
            # shellcheck source=/dev/null
            source "${lib_dir}/../overwatch_protocol.sh"
        else
            echo "check-transient-api-error: overwatch_protocol.sh not found relative to ${lib_dir}" >&2
            return 1
        fi
    fi

    if (( dry_run == 0 )); then
        local state_dir="${project_root}/overwatch"
        if [[ ! -d "${state_dir}" ]]; then
            echo "check-transient-api-error: overwatch state dir missing: ${state_dir}" >&2
            return 1
        fi
    fi

    if [[ ! -d "${queues_dir}" ]]; then
        echo "check-transient-api-error: queues dir does not exist: ${queues_dir}" >&2
        return 0
    fi

    # Find all BLOCKED task IDs
    local blocked_ids
    blocked_ids="$(_ctae_find_blocked_task_ids "${queues_dir}" "${tasks_root}")"

    if [[ -z "${blocked_ids}" ]]; then
        echo "check-transient-api-error: no BLOCKED tasks found in queue files" >&2
        return 0
    fi

    echo "check-transient-api-error: scanning BLOCKED tasks for transient API error signatures" >&2

    # The ONE ceiling — resolved once per sweep, shared with
    # wake_common.sh's handle_working_after_exit via the same
    # transient_retry_limit() function (overwatch_protocol.sh). Never
    # hardcode a second ceiling here.
    local transient_cap
    transient_cap="$(transient_retry_limit)"

    local handled=0
    local repair_failures=0
    local task_id

    while IFS= read -r task_id; do
        [[ -z "${task_id}" ]] && continue

        local task_dir="${tasks_root}/${task_id}"
        local task_status="${task_dir}/status.md"

        if [[ ! -f "${task_status}" ]]; then
            echo "check-transient-api-error: ${task_id}: status.md missing; skipping" >&2
            continue
        fi

        # Resolve the provider output span for this task's most recent
        # dispatch (see _ctae_log_tail). No ledger or a vanished batch log is
        # a narrated skip, not a fault: the task is left exactly as found and
        # the sweep continues with the next one.
        local log_tail log_tail_exit=0
        log_tail="$(_ctae_log_tail "${task_id}" "${tasks_root}")" || log_tail_exit=$?
        if (( log_tail_exit == 2 )); then
            echo "check-transient-api-error: ${task_id}: no dispatch record at ${task_dir}/logs/dispatch.jsonl (task never dispatched by a wake); skipping" >&2
            continue
        elif (( log_tail_exit != 0 )); then
            echo "check-transient-api-error: ${task_id}: batch log named in ${task_dir}/logs/dispatch.jsonl no longer exists; skipping" >&2
            continue
        fi

        # Check for transient signature
        if ! _ctae_matches_transient_signature "${log_tail}"; then
            echo "check-transient-api-error: ${task_id}: no transient signature in batch log span; skipping" >&2
            continue
        fi

        echo "check-transient-api-error: ${task_id}: transient API error signature detected" >&2

        # Read current requeue count
        local current_count
        current_count="$(_ctae_read_requeue_count "${task_status}")"
        local new_count=$(( current_count + 1 ))

        if (( dry_run == 1 )); then
            echo "check-transient-api-error: [dry-run] ${task_id}: count=${current_count}, cap=${transient_cap}; would $(( current_count >= transient_cap )) && echo 'bug-file' || echo 'requeue'" >&2
            if (( current_count >= transient_cap )); then
                overwatch_log_action \
                    "check-transient-api-error" \
                    "${task_id}" \
                    "dry-run-transient-ceiling-would-bug-file" \
                    "none" \
                    "Transient count=${current_count} >= ${transient_cap}; dry-run, would bug-file" \
                2>/dev/null || true
            else
                overwatch_log_action \
                    "check-transient-api-error" \
                    "${task_id}" \
                    "dry-run-transient-would-requeue" \
                    "none" \
                    "Transient count=${current_count}; dry-run, would requeue (#${new_count} of ${transient_cap})" \
                2>/dev/null || true
            fi
            # A requeue would clear the task's worktrees; narrate what it would do.
            if (( current_count < transient_cap )); then
                _ctae_clear_task_worktrees "${task_id}" "${dev_tree}" "${bugs_dir}" 1 || true
            fi
            continue
        fi

        # Ceiling check: at most transient_cap auto-requeues; the occurrence
        # AT the cap bug-files (exactly-at-cap is never left in
        # limbo — same "reached, don't retry further" semantics as
        # handle_working_after_exit's Needs Human=yes branch).
        if (( current_count >= transient_cap )); then
            echo "check-transient-api-error: ${task_id}: ceiling reached (count=${current_count}); bug-filing" >&2
            local bug_path
            bug_path="$(_ctae_bug_file "${task_id}" \
                "Transient API error ceiling reached (${current_count} requeues already performed)" \
                "${bugs_dir}")" || {
                echo "check-transient-api-error: ${task_id}: bug-file write failed" >&2
                continue
            }

            overwatch_log_action \
                "check-transient-api-error" \
                "${task_id}" \
                "transient-ceiling-bug-filed" \
                "none" \
                "Transient ceiling=${current_count}; bug filed at ${bug_path}" \
            || true

            echo "check-transient-api-error: ${task_id}: bug filed at ${bug_path}" >&2

            # Escalate Needs Human=yes so exactly-at-cap is never
            # left in limbo — consistent with wake_common.sh's
            # handle_working_after_exit at the same ceiling.
            export _CTAE_TASK_ID="${task_id}"
            export _CTAE_STATUS_FILE="${task_status}"
            export _CTAE_CURRENT_COUNT="${current_count}"
            export _CTAE_CAP="${transient_cap}"

            local escalate_exit=0
            overwatch_halt_first_fix _ctae_do_escalate || escalate_exit=$?

            unset _CTAE_TASK_ID _CTAE_STATUS_FILE _CTAE_CURRENT_COUNT _CTAE_CAP

            if (( escalate_exit == 3 )); then
                echo "check-transient-api-error: HALT_OVERWATCH guard tripped; aborting" >&2
                break
            elif (( escalate_exit == 4 )); then
                echo "check-transient-api-error: per-repo flock contended; aborting" >&2
                break
            elif (( escalate_exit == 5 )); then
                echo "check-transient-api-error: fix deferred: HALT present; aborting" >&2
                break
            elif (( escalate_exit != 0 )); then
                echo "check-transient-api-error: ${task_id}: needs-human escalation failed (exit ${escalate_exit}); bug remains filed" >&2
                repair_failures=$(( repair_failures + 1 ))
            fi

            handled=$(( handled + 1 ))
        else
            # Backoff: the requeue waits out the window that grows with the
            # retry count (transient_backoff_due, overwatch_protocol.sh). A
            # task inside it is left exactly as found, residue included,
            # and is looked at again on a later sweep.
            local not_before=""
            if declare -F transient_backoff_due >/dev/null 2>&1 \
                && ! not_before="$(transient_backoff_due "${task_status}")"; then
                echo "check-transient-api-error: ${task_id}: backoff: requeue #${new_count} of ${transient_cap} not before ${not_before}; skipping" >&2
                overwatch_log_action \
                    "check-transient-api-error" \
                    "${task_id}" \
                    "transient-requeue-backoff" \
                    "none" \
                    "Transient count=${current_count}; requeue #${new_count} waits for its backoff window (not before ${not_before})" \
                || true
                continue
            fi

            # Requeue path: invoke via overwatch_halt_first_fix; the task's
            # worktrees are cleared inside the same locked fix, first.
            export _CTAE_TASK_ID="${task_id}"
            export _CTAE_STATUS_FILE="${task_status}"
            export _CTAE_NEW_COUNT="${new_count}"
            export _CTAE_FIX_DEV_TREE="${dev_tree}"
            export _CTAE_FIX_BUGS_DIR="${bugs_dir}"

            local fix_exit=0
            overwatch_halt_first_fix _ctae_do_requeue_clearing_worktrees || fix_exit=$?

            unset _CTAE_TASK_ID _CTAE_STATUS_FILE _CTAE_NEW_COUNT _CTAE_FIX_DEV_TREE _CTAE_FIX_BUGS_DIR

            if (( fix_exit == 3 )); then
                echo "check-transient-api-error: HALT_OVERWATCH guard tripped; aborting" >&2
                break
            elif (( fix_exit == 4 )); then
                echo "check-transient-api-error: per-repo flock contended; aborting" >&2
                break
            elif (( fix_exit == 5 )); then
                echo "check-transient-api-error: fix deferred: HALT present; aborting" >&2
                break
            elif (( fix_exit != 0 )); then
                echo "check-transient-api-error: requeue failed for ${task_id} (exit ${fix_exit}); continuing" >&2
                repair_failures=$(( repair_failures + 1 ))
                continue
            fi

            echo "check-transient-api-error: ${task_id}: requeued to BACKLOG (requeue #${new_count})" >&2
            handled=$(( handled + 1 ))
        fi

    done <<< "${blocked_ids}"

    echo "check-transient-api-error: complete; handled ${handled} transient task(s)" >&2

    # A failed repair must never report success from the outer check: if any
    # requeue or ceiling-escalation attempt failed partway through this sweep,
    # surface it as a narrated non-zero exit rather than silently returning 0.
    if (( repair_failures > 0 )); then
        echo "check-transient-api-error: ${repair_failures} repair failure(s) detected this sweep; a repair was attempted and failed, so this check is exiting non-zero instead of reporting silent success" >&2
        return 1
    fi

    return 0
}

# ---------------------------------------------------------------------------
# Standalone invocation entry point.
# When this script is executed directly (not sourced), call the main function.
# ---------------------------------------------------------------------------
if [[ "${BASH_SOURCE[0]:-}" == "${0}" ]]; then
    overwatch_check_transient_api_error "$@"
    exit $?
fi
