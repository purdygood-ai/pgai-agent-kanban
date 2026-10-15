#!/usr/bin/env bash
# team/scripts/lib/overwatch-checks/check-block-triage.sh
#
# OVERWATCH detection module: block triage. For every BLOCKED task carrying
# a ## Block Record — whatever its ## Needs Human value — classify the
# record and carry out the decision through one executor,
# pgai_agent_kanban.overwatch.block_triage (plan, then apply), inside the
# full safety envelope this module owns.
#
# Scope (what it may change):
#   - The task's status.md: the ## Block Record triage fields (attempts,
#     cooldown_until, resume; the epoch restarts once a verified requeue is
#     followed by a new incident), ## Needs Human -> yes and the one-paragraph
#     ## OVERWATCH Diagnosis on an escalation, and the ## Zero Progress count
#     (reset to 0 by a provider_refusal requeue).
#   - The task's README.md: one ## Triage Note line per action taken (the
#     next dispatch reads it).
#   - The task's artifacts/block-evidence/: one evidence file per write,
#     written before anything else is touched; a per_task_timeout requeue's
#     commits-ahead snapshot (read-only git); a gate_dirty_worktree
#     requeue's dirty.patch, untracked.txt and dirty.patch.sha256 (the
#     uncommitted changes, saved and never committed); the
#     worktree-residue-<UTC>.txt list written before a stale worktree is
#     cleared; verification.json, one entry per action, settled on the
#     task's next block (or at once, for a recomputed gate that passes).
#   - ## Needs Human -> no and ## Blockers -> none when a recomputed
#     completion gate passes and the task closes as DONE.
#   - ## State and the queue marker, only through promote_blocked_to_backlog
#     or transition_task_state, and only when the executor asks for it.
#   - A stale worktree registration inside the worktree base that blocks a
#     task's worktree create, and only one whose commits all exist on
#     another ref: git worktree remove (never --force) and git worktree
#     prune, which delete no branch and no commit.
#   - The bug files an allowlisted operator data action names
#     (agent_operator_data_action): restamp-fixed-in stamps "## Fixed In" on
#     done bugs whose value is empty, run in-process with validated typed
#     arguments; its output and an idempotence re-run's output go to
#     artifacts/block-evidence/verb-<verb>-<UTC>.txt.
#   - Nothing of a ship_policy_halt task: it is handed to the existing
#     resolution stage (pgai_agent_kanban.overwatch.resolution_stage), and
#     every write that follows is the stage's own; this module adds only its
#     action-log row.
#   Beyond that cleanup it runs no repository-mutating git verb and never
#   resets a task: work that needs git is requeued to the task's owning
#   role with a note.
#
# Envelope, per task with something to write:
#   1. overwatch_halt_first_fix wraps everything below. HALT_OVERWATCH (flag
#      file or env var) or a held per-project repo lock leaves the task
#      unchanged; the refusal is logged (the lock refusal names the holder)
#      and no further task is attempted this run. A present HALT — the
#      fleet-wide one, or the project's own — defers the task's fix: it is
#      left unchanged, the wrapper logs "fix deferred: HALT present", and
#      the next task is tried (each is deferred the same way). The one
#      exception is a planned "defer" (a ship_policy_halt task), which
#      passes --ship-policy-resolution so the resolution stage still runs
#      under the project HALT CM wrote; the fleet-wide HALT defers it too.
#   2. overwatch_backup_file backs up status.md and the queue file, grouped
#      under backups/<firing timestamp>/<task id>/ (each backup is keyed on
#      its source's own path, so two sources never share one).
#   3. block_triage apply writes the evidence file, then its own writes,
#      and names the state mutation (none | promote | transition:<STATE>).
#   4. The mutation, through promote_blocked_to_backlog / transition_task_state.
#   5. overwatch_log_action records "block-triage:<reason_code>:<action>"
#      with both backup paths, comma-separated (status.md first, then the
#      queue file), so overwatch-revert.sh restores both.
#   A task the plan names "defer" takes steps 1, 3 and 5 only: no backup
#   (nothing of the task is written), apply runs with --defer-only (it
#   writes nothing unless the plan under the lock is still defer), and the
#   row's backup path is "none".
#
# Outcomes (see the executor's module docstring for the full rules):
#   skip      nothing written (epoch already escalated, cooling down,
#             block_triage=false for an action, or an action the executor
#             does not perform yet on a task whose Needs Human is no)
#   record    first sighting of a wait-first row: cooldown_until recorded
#   escalate  Needs Human -> yes + ## OVERWATCH Diagnosis; the task stays
#             BLOCKED. An action the executor does not perform yet escalates
#             (fail closed).
#   act       an action with a registered handler: Triage Note, attempts +1,
#             cooldown_until set, a pending verification entry, then the
#             state mutation. Handlers exist for requeue_owner on
#             per_task_timeout, provider_refusal, zero_progress,
#             gate_merge_not_found (a clean merge-tree: "merge only, then
#             re-complete"), gate_dirty_worktree (after saving dirty.patch)
#             and gate_empty_commit (quoting the shas); recompute_gate on
#             gate_merge_not_found (a branch already merged: the gate is
#             recomputed read-only and the task closes as DONE); and
#             prune_worktree_then_requeue on worktree_create_failed (stale
#             worktree residue cleared, then requeued). Each refuses, and
#             escalates instead, when its own precondition no longer holds.
#             run_allowlisted_verb on agent_operator_data_action: an
#             allowlisted verb whose typed arguments validate and whose
#             targets check out (restamp-fixed-in: an existing project, a
#             vX.Y.Z version, BUG-NNNN bugs that exist and are done) runs
#             once, a re-run must report 0 changes, then the owner is
#             requeued. A prose-only remedy, a verb not on the allowlist, an
#             invalid argument or a target that does not check out escalates
#             and nothing is run.
#             transient_api_error is left to the transient-error handlers
#             below their retry ceiling and escalates at it.
#   defer     ship_policy_halt with a ship-policy HALT on the project: the
#             resolution stage runs once under its own rules and ceiling;
#             the row's detail reports its decision, verified by its
#             "## OVERWATCH Decision" event. A decline leaves the task with
#             a human, unchanged. Without a ship-policy HALT the task is
#             skipped (it stays with a human).
#
# Verification: when a requeued task blocks again, the requeue's
# verification runs against the new block — passed restarts the block
# epoch, failed escalates (unless the same failure may still be retried
# under its row's attempts), and a block occurrence already acted on is
# never acted on again.
#
# Worktree base: resolved once per run with worktree.sh's
# pgai_worktree_base (PGAI_WORKTREE_BASE, then kanban.cfg [paths]
# worktree_base, then the project's temp subtree) and passed to the
# executor, which clears worktree residue only strictly inside it.
#
# Config: [overwatch] block_triage (project.cfg first, then kanban.cfg,
# resolved by pp_block_triage). false keeps triage to records and
# diagnoses: Block Record writes and escalations still happen; no state,
# queue or README mutation does. A value pp_block_triage refuses is a
# safety refusal: nothing is changed and the refusal is logged.
#
# Callers:
#   - team/scripts/overwatch-sweep.sh sources this file (filename discovery)
#     and calls overwatch_check_block_triage with KANBAN_ROOT and
#     OVERWATCH_PROJECT set.
#   - The overwatch wake (run_overwatch_block_triage in wake_project.sh)
#     runs the same function before the wake takes the project's repo lock.
#   - Directly: bash check-block-triage.sh [--dry-run]
#
# Required environment variables (when sourced):
#   KANBAN_ROOT       — absolute path to the kanban root
#   OVERWATCH_PROJECT — project name (e.g. "pgai-agent-kanban")
# Invoked directly, KANBAN_ROOT defaults to
# ${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban} and
# OVERWATCH_PROJECT to the single registered project (an error when several
# are registered).
#
# Usage:
#   bash check-block-triage.sh [--dry-run] [--help]
#
#   --dry-run  plan and narrate what would be done, logging one
#              "block-triage-dry-run" action-log row per task that would be
#              written; no task file, backup or evidence file is written.
#   --help     print this usage and exit 0.
#
# Exit codes:
#   0 — completed (including safety refusals, which leave tasks unchanged)
#   1 — internal error (missing library, unreadable project), or one or more
#       tasks whose triage writes failed partway (each narrated on stderr)

# ---------------------------------------------------------------------------
# _cbtr_resolve_env
# Resolve KANBAN_ROOT and OVERWATCH_PROJECT. Returns 0 on success, 1 on failure.
# ---------------------------------------------------------------------------
_cbtr_resolve_env() {
    if [[ -z "${KANBAN_ROOT:-}" ]]; then
        KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-${HOME}/pgai_agent_kanban}"
    fi
    export KANBAN_ROOT
    if [[ ! -d "${KANBAN_ROOT}" ]]; then
        echo "check-block-triage: KANBAN_ROOT does not exist: ${KANBAN_ROOT}" >&2
        return 1
    fi
    if [[ -z "${OVERWATCH_PROJECT:-}" ]]; then
        local _projects=""
        if declare -F projects_cfg_list >/dev/null 2>&1; then
            _projects="$(projects_cfg_list 2>/dev/null)"
        fi
        local _count
        _count="$(grep -c '[^[:space:]]' <<< "${_projects}" || true)"
        if [[ "${_count}" != "1" ]]; then
            echo "check-block-triage: OVERWATCH_PROJECT is not set and ${_count:-0} projects are registered; set OVERWATCH_PROJECT to the project to triage" >&2
            return 1
        fi
        OVERWATCH_PROJECT="$(grep '[^[:space:]]' <<< "${_projects}")"
    fi
    export OVERWATCH_PROJECT
    return 0
}

# ---------------------------------------------------------------------------
# _cbtr_load_libs
# Source the libraries this module calls, each only when the caller has not
# already defined what it provides (a caller's own copy, or a test stub,
# wins). Every path is resolved from this file's own directory.
# ---------------------------------------------------------------------------
_cbtr_load_libs() {
    local lib_dir
    lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd)" || {
        echo "check-block-triage: cannot resolve the lib directory" >&2
        return 1
    }
    local -a wanted=(
        "overwatch_halt_first_fix:overwatch_protocol.sh"
        "pp_block_triage:project_paths.sh"
        "projects_cfg_list:projects.sh"
        "pgai_lock_read_provenance:lock_provenance.sh"
        "kanban_task_id_pattern:task_ids.sh"
        "pp_run_ops:pp_run_ops.sh"
        "promote_blocked_to_backlog:wake_task_state.sh"
        "transition_task_state:wake_task_transition.sh"
        "pgai_worktree_base:worktree.sh"
    )
    local entry fn file
    for entry in "${wanted[@]}"; do
        fn="${entry%%:*}"
        file="${entry#*:}"
        declare -F "${fn}" >/dev/null 2>&1 && continue
        if [[ ! -f "${lib_dir}/${file}" ]]; then
            echo "check-block-triage: required library not found: ${lib_dir}/${file}" >&2
            return 1
        fi
        # shellcheck source=/dev/null
        source "${lib_dir}/${file}" || {
            echo "check-block-triage: cannot load ${lib_dir}/${file}" >&2
            return 1
        }
    done
    return 0
}

# ---------------------------------------------------------------------------
# _cbtr_worktree_base
# Print the worktree base every task worktree of OVERWATCH_PROJECT lives
# under (worktree.sh's resolver, with this run's kanban root and project);
# prints nothing when it cannot be resolved, so residue is never cleared.
# ---------------------------------------------------------------------------
_cbtr_worktree_base() {
    PGAI_AGENT_KANBAN_ROOT_PATH="${KANBAN_ROOT}" PP_project_name="${OVERWATCH_PROJECT}" \
        pgai_worktree_base 2>/dev/null || true
}

# ---------------------------------------------------------------------------
# _cbtr_log_refusal <target> <reason>
# Record a safety refusal in the action log (the task itself is untouched).
# ---------------------------------------------------------------------------
_cbtr_log_refusal() {
    overwatch_log_action "check-block-triage" "$1" "block-triage-refused" "none" "$2" 2>/dev/null || true
}

# ---------------------------------------------------------------------------
# _cbtr_apply_one
# The wrapped mutation, run by overwatch_halt_first_fix for one task. Reads
# _CBTR_TASK_ID, _CBTR_QUEUE_FILE, _CBTR_TRIAGE_ENABLED, _CBTR_OUTCOME (the
# planned outcome) and _CBTR_WORKTREE_BASE (may be empty) from the
# environment. A planned "defer" takes no backup and runs apply with
# --defer-only.
# Returns 0 when the task's writes landed (or there was nothing to write),
# 1 when any step failed (narrated on stderr).
# ---------------------------------------------------------------------------
_cbtr_apply_one() {
    local task_id="${_CBTR_TASK_ID}"
    local queue_file="${_CBTR_QUEUE_FILE}"
    local project_root="${KANBAN_ROOT}/projects/${OVERWATCH_PROJECT}"
    local status_file="${project_root}/tasks/${task_id}/status.md"
    local firing_ts="${OVERWATCH_FIRING_TIMESTAMP:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}"

    # Each task's backups are grouped under its own sub-directory of the
    # firing's backup directory; overwatch_backup_file keys each one on its
    # source's own path within it.
    local status_backup="none" queue_backup="none"
    local -a mode_args=()
    if [[ "${_CBTR_OUTCOME:-}" == "defer" ]]; then
        # A deferral writes nothing of the task, so there is nothing to back up.
        mode_args=(--defer-only)
    else
        status_backup="$(OVERWATCH_FIRING_TIMESTAMP="${firing_ts}/${task_id}" overwatch_backup_file "${status_file}")" || {
            echo "check-block-triage: ${task_id}: backup of ${status_file} failed; task left unchanged" >&2
            return 1
        }
        queue_backup="$(OVERWATCH_FIRING_TIMESTAMP="${firing_ts}/${task_id}" overwatch_backup_file "${queue_file}")" || {
            echo "check-block-triage: ${task_id}: backup of ${queue_file} failed; task left unchanged" >&2
            return 1
        }
        mode_args=(--status-backup "${status_backup}" --queue-backup "${queue_backup}")
    fi

    local -a base_args=()
    [[ -n "${_CBTR_WORKTREE_BASE:-}" ]] && base_args=(--worktree-base "${_CBTR_WORKTREE_BASE}")
    local result
    result="$(pp_run_ops pgai_agent_kanban.overwatch.block_triage apply \
        --kanban-root "${KANBAN_ROOT}" --project "${OVERWATCH_PROJECT}" \
        --task-id "${task_id}" --queue-file "${queue_file}" \
        --triage-enabled "${_CBTR_TRIAGE_ENABLED}" "${base_args[@]}" "${mode_args[@]}")" || {
        echo "check-block-triage: ${task_id}: executor writes failed (see the error above); no state mutation performed" >&2
        return 1
    }

    local outcome code action mutation detail
    IFS=$'\t' read -r outcome code action mutation detail <<< "${result}"

    if [[ "${outcome}" == "skip" ]]; then
        echo "check-block-triage: ${task_id}: nothing to do under the lock (${detail})" >&2
        return 0
    fi

    # The one state mutation the executor asked for, through the sanctioned
    # writers. BACKLOG is the queue file those writers rewrite.
    local BACKLOG="${queue_file}"
    case "${mutation}" in
        none) ;;
        promote)
            promote_blocked_to_backlog "${task_id}" "${status_file}" "${detail}" || {
                echo "check-block-triage: ${task_id}: promote_blocked_to_backlog failed after the triage writes; the task stays BLOCKED with attempts already counted" >&2
                return 1
            }
            ;;
        transition:*)
            transition_task_state "${task_id}" "${status_file}" "${mutation#transition:}" || {
                echo "check-block-triage: ${task_id}: transition_task_state ${mutation#transition:} failed after the triage writes" >&2
                return 1
            }
            ;;
        *)
            echo "check-block-triage: ${task_id}: executor named an unknown mutation '${mutation}'; nothing mutated" >&2
            return 1
            ;;
    esac

    # Every file this fix backed up, machine-readable, for overwatch-revert.sh.
    local backup_column="none"
    [[ "${status_backup}" != "none" ]] && backup_column="${status_backup},${queue_backup}"
    overwatch_log_action \
        "check-block-triage" \
        "${task_id}" \
        "block-triage:${code}:${action}" \
        "${backup_column}" \
        "${detail}; queue backup ${queue_backup}" \
    || true
    echo "check-block-triage: ${task_id}: block-triage:${code}:${action} (${outcome})" >&2
    return 0
}

# ---------------------------------------------------------------------------
# overwatch_check_block_triage [--dry-run]
# Main function. Returns 0 on success (safety refusals included), 1 on an
# internal error or a task whose writes failed partway.
# ---------------------------------------------------------------------------
overwatch_check_block_triage() {
    local dry_run=0 arg
    for arg in "$@"; do
        case "${arg}" in
            --dry-run) dry_run=1 ;;
            --help|-h)
                # Drain the whole header comment into an array first, then
                # print it: awk reads the file itself, so no pipe stage can
                # close early.
                local -a _cbtr_help=()
                mapfile -t _cbtr_help < <(awk 'NR == 1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "${BASH_SOURCE[0]}")
                printf '%s\n' "${_cbtr_help[@]}"
                return 0
                ;;
            *)
                echo "check-block-triage: unknown argument '${arg}' (accepted: --dry-run, --help)" >&2
                return 1
                ;;
        esac
    done

    _cbtr_load_libs || return 1
    _cbtr_resolve_env || return 1

    local project="${OVERWATCH_PROJECT}"
    local project_root="${KANBAN_ROOT}/projects/${project}"
    if [[ ! -d "${project_root}" ]]; then
        echo "check-block-triage: project root does not exist: ${project_root}" >&2
        return 1
    fi
    if (( dry_run == 0 )); then
        mkdir -p "${project_root}/overwatch/backups" 2>/dev/null || {
            echo "check-block-triage: cannot create ${project_root}/overwatch/backups" >&2
            return 1
        }
    fi

    # [overwatch] block_triage. A refused value is a safety refusal: nothing
    # is planned or changed, and the refusal is logged.
    local triage_enabled
    if ! triage_enabled="$(pp_block_triage "${project}")"; then
        echo "check-block-triage: [overwatch] block_triage could not be resolved for ${project} (see the error above); no task triaged" >&2
        (( dry_run == 0 )) && _cbtr_log_refusal "${project}" "[overwatch] block_triage unresolvable; no task triaged"
        return 0
    fi

    local worktree_base
    worktree_base="$(_cbtr_worktree_base)"
    local -a base_args=()
    [[ -n "${worktree_base}" ]] && base_args=(--worktree-base "${worktree_base}")

    local plan
    plan="$(pp_run_ops pgai_agent_kanban.overwatch.block_triage plan \
        --kanban-root "${KANBAN_ROOT}" --project "${project}" \
        --id-pattern "$(kanban_task_id_pattern)" --triage-enabled "${triage_enabled}" "${base_args[@]}")" || {
        echo "check-block-triage: the executor's plan step failed for ${project}" >&2
        return 1
    }
    if [[ -z "${plan}" ]]; then
        echo "check-block-triage: ${project}: no BLOCKED task carries a Block Record" >&2
        return 0
    fi

    local acted=0 failed=0 skipped=0
    local task_id queue_file outcome code action detail fix_exit
    while IFS=$'\t' read -r task_id queue_file outcome code action detail; do
        [[ -n "${task_id}" ]] || continue
        if [[ "${outcome}" == "skip" ]]; then
            echo "check-block-triage: ${task_id}: ${code}: skip (${detail})" >&2
            skipped=$(( skipped + 1 ))
            continue
        fi
        if (( dry_run == 1 )); then
            echo "check-block-triage: [dry-run] ${task_id}: ${code}: would ${outcome} (${action}: ${detail})" >&2
            overwatch_log_action "check-block-triage" "${task_id}" "block-triage-dry-run" "none" \
                "would ${outcome} ${code} (${action}: ${detail}); nothing written" 2>/dev/null || true
            continue
        fi

        export _CBTR_TASK_ID="${task_id}" _CBTR_QUEUE_FILE="${queue_file}" _CBTR_TRIAGE_ENABLED="${triage_enabled}"
        export _CBTR_WORKTREE_BASE="${worktree_base}" _CBTR_OUTCOME="${outcome}"
        # A ship_policy_halt deferral is the one fix that runs under the
        # project HALT CM wrote: the resolution stage exists to classify and
        # resolve exactly that HALT, and apply --defer-only re-checks under
        # the lock that it is CM's before calling the stage. Every other
        # outcome is deferred by a present HALT (sentinel 5).
        local -a hff_opts=()
        [[ "${outcome}" == "defer" ]] && hff_opts=(--ship-policy-resolution)
        fix_exit=0
        overwatch_halt_first_fix "${hff_opts[@]}" _cbtr_apply_one || fix_exit=$?
        unset _CBTR_TASK_ID _CBTR_QUEUE_FILE _CBTR_TRIAGE_ENABLED _CBTR_WORKTREE_BASE _CBTR_OUTCOME

        if (( fix_exit == 3 )); then
            echo "check-block-triage: HALT_OVERWATCH is set; ${task_id} left unchanged, no further task attempted" >&2
            _cbtr_log_refusal "${task_id}" "HALT_OVERWATCH present; task left unchanged (would ${outcome}: ${code})"
            break
        elif (( fix_exit == 5 )); then
            # The wrapper already logged the "fix deferred: HALT present" row.
            echo "check-block-triage: fix deferred: HALT present; ${task_id} left unchanged (would ${outcome}: ${code})" >&2
            skipped=$(( skipped + 1 ))
            continue
        elif (( fix_exit == 4 )); then
            local lock_file holder="an unrecorded holder"
            lock_file="$(pp_repo_lock_path "${project}" 2>/dev/null)" || lock_file=""
            if [[ -n "${lock_file}" ]]; then
                holder="$(pgai_lock_describe_provenance "$(pgai_lock_read_provenance "${lock_file}" 2>/dev/null)" 2>/dev/null)" \
                    || holder="an unrecorded holder"
            fi
            echo "check-block-triage: repo lock ${lock_file:-for ${project}} held by ${holder}; ${task_id} left unchanged, no further task attempted" >&2
            _cbtr_log_refusal "${task_id}" "repo lock held by ${holder}; task left unchanged (would ${outcome}: ${code})"
            break
        elif (( fix_exit != 0 )); then
            failed=$(( failed + 1 ))
            continue
        fi
        acted=$(( acted + 1 ))
    done <<< "${plan}"

    echo "check-block-triage: ${project}: complete; ${acted} task(s) written, ${skipped} skipped, ${failed} failed" >&2
    (( failed == 0 )) || return 1
    return 0
}

# ---------------------------------------------------------------------------
# Standalone invocation entry point.
# ---------------------------------------------------------------------------
if [[ "${BASH_SOURCE[0]:-}" == "${0}" ]]; then
    overwatch_check_block_triage "$@"
    exit $?
fi
