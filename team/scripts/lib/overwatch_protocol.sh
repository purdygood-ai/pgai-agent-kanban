#!/usr/bin/env bash
# team/scripts/lib/overwatch_protocol.sh
#
# Protocol-level helpers for OVERWATCH — shared action log, backup-before-modify,
# and the HALT-first wrapper used by detection scripts that apply corrective fixes.
#
# SOURCE THIS FILE; do not execute it directly:
#   source "$(dirname "${BASH_SOURCE[0]}")/overwatch_protocol.sh"
#
# This file is side-effect-free on source: no commands run at the top level,
# no files are created, no variables are set outside function definitions.
#
# Dependencies:
#   - team/scripts/lib/overwatch_lib.sh should be sourced first when
#     overwatch_state_dir, overwatch_halt_flag_path, overwatch_acquire_firing_lock,
#     or overwatch_release_firing_lock are needed by the caller. This file
#     does not call those functions itself.
#   - team/scripts/lib/project_paths.sh provides pp_repo_lock_path, which
#     overwatch_halt_first_fix uses to resolve the per-project repo lock —
#     the SAME lock file the wake dispatchers (wake/claude.sh, wake/codex.sh,
#     wake/harness.sh) hold while a wake is processing that project — and
#     pp_project_root, which it uses to resolve the project-scoped HALT
#     sentinel it touches. Callers that already source project_paths.sh
#     (e.g. overwatch-sweep.sh) get this for free; overwatch_halt_first_fix
#     defensively sources it itself (relative to this file) if it is not
#     already loaded, so this file also works when sourced standalone.
#
# Required environment variables (must be set before calling any function):
#   KANBAN_ROOT        — absolute path to the kanban installation root
#   OVERWATCH_PROJECT   — project name (e.g. "pgai-agent-kanban")
#
# Optional environment variables:
#   HALT_OVERWATCH      — if non-empty, overwatch_halt_first_fix refuses to act
#                        (checked in addition to the HALT_OVERWATCH flag file)
#   OVERWATCH_FIRING_TIMESTAMP — ISO-8601 UTC timestamp set at firing start;
#                        used by overwatch_backup_file to group backups per firing.
#                        If unset, a fresh timestamp is generated per call.
#   OVERWATCH_LOCK_WAIT_SECONDS — whole seconds overwatch_halt_first_fix keeps
#                        retrying a contended per-project repo lock (with
#                        backoff) before declining with sentinel 4. Unset,
#                        0 or not a whole number: one attempt, no waiting.
#                        overwatch-sweep.sh sets it from its --lock-wait flag.
#
# Sentinel exit codes used by overwatch_halt_first_fix:
#   3 — HALT_OVERWATCH guard tripped (env var or flag file present)
#   4 — per-repo flock contended (another agent is running)
#   5 — fix deferred: HALT present (the fleet-wide $KANBAN_ROOT/HALT, or a
#       project HALT this invocation did not create); nothing was changed
#
# Functions defined here:
#   overwatch_backup_file   <src_path>                              — backup a file
#   overwatch_log_action    <name> <target> <action> <backup_path> <reason>  — log
#   overwatch_halt_first_fix [--ship-policy-resolution] <fn>       — HALT-first wrapper
#   overwatch_load_module_runner                                   — load pp_run_ops/pgai_atomic_write/block_record.sh on first use
#   transient_retry_limit                                          — resolve the ONE retry ceiling
#   transient_retry_field  <status_file> <field> [default]         — read the durable retry record
#   transient_retry_write   <status_file> <reason_code> <retry_count>
#                           <retry_limit> <provider> <last_error>  — write the durable retry record
#   zero_progress_cap                                              — resolve the ONE zero-progress ceiling
#   zero_progress_field     <status_file> <field> [default]         — read the durable zero-progress record
#   zero_progress_record    <status_file> <reason_code> <count>
#                           <limit> <detail>                       — write the durable zero-progress record
#   zero_progress_reset     <status_file>                          — clear the record back to "none"/0
#   dispatch_record_append  <task_dir> <event> <batch_log> <offset_start>
#                           [offset_end] [exit_code]             — append to the per-task dispatch ledger
#   dispatch_record_latest  <task_dir>                            — resolve the task's most recent dispatch
#
# transient_retry_*: a single durable, machine-readable retry
# record replacing the two former uncoordinated authorities (wake's prose
# "retry N/M" embedded in ## Blocked Reason, and OVERWATCH's separate
# ## Transient Requeue Count field with its own ceiling). The record lives
# in a dedicated ## Transient Retry status.md section that survives BACKLOG
# promotion (unlike ## Blocked Reason, which promote_blocked_to_backlog resets
# to "none"). wake_common.sh's handle_working_after_exit is the ONE classifier
# that writes this record; recheck_blocked_tasks and OVERWATCH's
# check-transient-api-error.sh both read it via transient_retry_field/limit
# rather than maintaining their own counters or ceilings. These three
# functions have no KANBAN_ROOT/OVERWATCH_PROJECT requirement (unlike the
# three above) — they operate purely on the <status_file> path argument
# (transient_retry_limit optionally consults KANBAN_ROOT/kanban.cfg, falling
# back cleanly when unset).
#
# zero_progress_*: a SEPARATE durable record for a DIFFERENT concern than
# transient_retry_*. transient_retry_* counts retries of a SPECIFIC,
# log-detected condition (a transient provider error signature). This record
# instead counts consecutive cycles where a task was found (or left) in
# WORKING with NO forward motion and no other explanation — an orphaned
# [A]-marker task with no live process (reconcile_stale_active,
# wake_reconcile_sweeps.sh) or an agent that exited without moving the task out of
# WORKING and without a transient signature in its log
# (handle_working_after_exit's non-transient fallback, wake_lifecycle.sh). A
# transient-classified cycle touches neither counter — it is accounted for
# entirely by transient_retry_*, so the two ceilings never double-count the
# same cycle. Kept in its own ## Zero Progress section (not reused from
# ## Transient Retry) so the two budgets stay visibly independent to an
# operator. Survives BACKLOG promotion the same way ## Transient Retry does,
# and shares transient_retry_limit's cfg-resolution precedence and
# strictly-less-than-cap comparison (see zero_progress_cap below).

# ---------------------------------------------------------------------------
# _overwatch_protocol_check_env
# Internal helper: verify KANBAN_ROOT and OVERWATCH_PROJECT are set.
# Emits an error to stderr and returns 1 if either is missing.
# ---------------------------------------------------------------------------
_overwatch_protocol_check_env() {
    local caller="${1:-overwatch_protocol}"
    if [[ -z "${KANBAN_ROOT:-}" ]]; then
        echo "overwatch_protocol.sh: ${caller}: KANBAN_ROOT is not set" >&2
        return 1
    fi
    if [[ -z "${OVERWATCH_PROJECT:-}" ]]; then
        echo "overwatch_protocol.sh: ${caller}: OVERWATCH_PROJECT is not set" >&2
        return 1
    fi
    return 0
}

# ---------------------------------------------------------------------------
# overwatch_backup_file <src_path>
# Copy <src_path> to a timestamped backup directory under the project's
# OVERWATCH state directory and echo the backup path to stdout.
#
# Destination layout -- keyed on the source's own path, so two different
# sources never share a backup (two tasks' status.md backed up in one
# firing land in two files):
#   .../overwatch/backups/<TIMESTAMP>/project-root/<path relative to the project root>
#   .../overwatch/backups/<TIMESTAMP>/absolute/<absolute path without its leading />
# The second form is for a source outside the project root. overwatch-revert.sh
# reads the source path back out of the backup path, so a revert restores
# each backup to exactly the file it was taken from.
#
# Where <TIMESTAMP> is:
#   - $OVERWATCH_FIRING_TIMESTAMP if set (groups all backups from one firing;
#     a caller may append "/<group>" to sub-divide one firing's backups)
#   - otherwise: $(date -u +%Y-%m-%dT%H:%M:%SZ) at call time
#
# Idempotency: if the destination file already exists (same firing, same
# source), the function succeeds without overwriting and echoes the existing
# path -- the first backup of a file in a firing is its pre-firing content.
#
# Args:
#   $1  src_path — absolute path to the file to back up
#
# Returns:
#   0  — backup succeeded (or already existed); backup path echoed on stdout
#   1  — error (missing args, missing source file, state dir problem, copy fail)
#
# Emits structured debug line to stderr on entry.
# ---------------------------------------------------------------------------
overwatch_backup_file() {
    local src_path="$1"
    local state_dir backup_base_dir ts backup_dir dst_path

    echo "overwatch_protocol: overwatch_backup_file: src=${src_path}" >&2

    # Validate args and environment
    if [[ -z "$src_path" ]]; then
        echo "overwatch_protocol.sh: overwatch_backup_file: src_path argument is required" >&2
        return 1
    fi
    _overwatch_protocol_check_env "overwatch_backup_file" || return 1

    if [[ ! -f "$src_path" ]]; then
        echo "overwatch_protocol.sh: overwatch_backup_file: source file does not exist: ${src_path}" >&2
        return 1
    fi

    # Resolve state and backup directories
    state_dir="${KANBAN_ROOT}/projects/${OVERWATCH_PROJECT}/overwatch"
    backup_base_dir="${state_dir}/backups"

    if [[ ! -d "$backup_base_dir" ]]; then
        echo "overwatch_protocol.sh: overwatch_backup_file: backups dir does not exist: ${backup_base_dir}" >&2
        return 1
    fi

    # Use firing timestamp if available; otherwise generate a call-time timestamp.
    # ISO 8601 UTC per constraints.
    ts="${OVERWATCH_FIRING_TIMESTAMP:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}"

    # Create the per-timestamp subdirectory (idempotent)
    backup_dir="${backup_base_dir}/${ts}"
    mkdir -p "$backup_dir" 2>/dev/null || {
        echo "overwatch_protocol.sh: overwatch_backup_file: cannot create backup dir: ${backup_dir}" >&2
        return 1
    }

    # Key the backup on the source's own path (see the layout above).
    local project_root_abs src_abs rel_key
    project_root_abs="$(realpath -m "${KANBAN_ROOT}/projects/${OVERWATCH_PROJECT}")"
    src_abs="$(realpath -m "$src_path")"
    if [[ "$src_abs" == "${project_root_abs}/"* ]]; then
        rel_key="project-root/${src_abs#"${project_root_abs}/"}"
    else
        rel_key="absolute/${src_abs#/}"
    fi
    dst_path="${backup_dir}/${rel_key}"
    mkdir -p "$(dirname "$dst_path")" 2>/dev/null || {
        echo "overwatch_protocol.sh: overwatch_backup_file: cannot create backup dir: $(dirname "$dst_path")" >&2
        return 1
    }

    # Idempotency: if destination already exists, succeed without overwriting.
    if [[ -f "$dst_path" ]]; then
        echo "overwatch_protocol: overwatch_backup_file: already backed up: ${dst_path}" >&2
        echo "$dst_path"
        return 0
    fi

    # Perform the copy
    if ! cp "$src_path" "$dst_path"; then
        echo "overwatch_protocol.sh: overwatch_backup_file: copy failed: ${src_path} -> ${dst_path}" >&2
        return 1
    fi

    echo "overwatch_protocol: overwatch_backup_file: backed up to ${dst_path}" >&2
    echo "$dst_path"
    return 0
}

# ---------------------------------------------------------------------------
# overwatch_log_action <name> <target> <action> <backup_path> <reason>
# Append one structured plain-text record to the project's OVERWATCH action log:
#   $KANBAN_ROOT/projects/<OVERWATCH_PROJECT>/overwatch/actions.log
#
# Record format (tab-separated fields, one record per line):
#   <timestamp>\t<name>\t<target>\t<action>\t<backup_path>\t<reason>
#
# Where <timestamp> is ISO 8601 UTC: YYYY-MM-DDTHH:MM:SSZ
#
# The log is append-only: this function never reads or rewrites existing
# content. Each call produces exactly one new line.
#
# Args:
#   $1  name         — detection script name (e.g. "check-stale-working-tasks")
#   $2  target       — the specific resource affected (task ID, file path, branch)
#   $3  action       — what was done or observed (e.g. "backup", "halt-first-fix")
#   $4  backup_path  — path returned by overwatch_backup_file, or "none"; a
#                      fix that backs up several files lists every backup
#                      path, comma-separated, so overwatch-revert.sh restores
#                      them all
#   $5  reason       — human-readable explanation of why the action was taken
#
# Returns:
#   0  — record appended successfully
#   1  — error (missing args, state dir missing, write failure)
#
# Creates actions.log if it does not exist.
# Emits structured debug line to stderr on entry.
# ---------------------------------------------------------------------------
overwatch_log_action() {
    local name="$1"
    local target="$2"
    local action="$3"
    local backup_path="$4"
    local reason="$5"
    local state_dir log_file timestamp

    echo "overwatch_protocol: overwatch_log_action: name=${name} target=${target} action=${action}" >&2

    # Validate required args
    if [[ -z "$name" || -z "$target" || -z "$action" ]]; then
        echo "overwatch_protocol.sh: overwatch_log_action: name, target, and action are required" >&2
        return 1
    fi
    _overwatch_protocol_check_env "overwatch_log_action" || return 1

    state_dir="${KANBAN_ROOT}/projects/${OVERWATCH_PROJECT}/overwatch"
    log_file="${state_dir}/actions.log"

    if [[ ! -d "$state_dir" ]]; then
        echo "overwatch_protocol.sh: overwatch_log_action: state dir does not exist: ${state_dir}" >&2
        return 1
    fi

    # ISO 8601 UTC timestamp per constraints
    timestamp="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

    # Append-only: use >> exclusively; never rewrite.
    printf '%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$timestamp" \
        "$name" \
        "$target" \
        "$action" \
        "${backup_path:-none}" \
        "${reason:-}" \
        >> "$log_file" || {
        echo "overwatch_protocol.sh: overwatch_log_action: write failed to ${log_file}" >&2
        return 1
    }

    echo "overwatch_protocol: overwatch_log_action: appended to ${log_file}" >&2
    return 0
}

# ---------------------------------------------------------------------------
# overwatch_halt_first_fix [--ship-policy-resolution] <fn>
# Safety wrapper for OVERWATCH functions that apply corrective changes to
# kanban state. Implements the HALT-first protocol: stop chain agents before
# mutating, restore after — while holding the SAME per-project repo lock a
# wake dispatcher holds, for the duration of the mutation only.
#
# Scope: every caller of this wrapper (see team/scripts/lib/overwatch-checks/)
# repairs exactly one project's state — the one named by $OVERWATCH_PROJECT.
# The HALT it touches is therefore the PROJECT-SCOPED sentinel
# ($KANBAN_ROOT/projects/$OVERWATCH_PROJECT/HALT), never the fleet-wide
# $KANBAN_ROOT/HALT: a one-project repair has no fleet-wide fault to report,
# and touching the root file would park every sibling project's chain for a
# fault none of them has. The wrapper only ever READS the fleet-wide file,
# to defer (below); it never writes or removes it.
#
# A present HALT means "keep observing read-only, skip the fix": an
# operator's (or CM's) stop is honoured, not worked through. The fleet-wide
# HALT, or a project HALT this invocation did not create, defers the fix —
# <fn> is not run, nothing is changed, and one "fix deferred: HALT present"
# row is narrated on stderr and in the action log. Detection, reports and
# bug filing are the callers' and are untouched by a deferral.
#
# OVERWATCH remains independent: this wrapper is called only around the
# short state-mutation transaction, never around detection/observation. The
# lock is acquired immediately before the HALT touch and released immediately
# after the HALT restore — OVERWATCH is never serialized behind agents for
# the (much longer) detection phase.
#
# Protocol (in order):
#  1. Guard: if HALT_OVERWATCH env var is non-empty OR the HALT_OVERWATCH flag
#     file ($KANBAN_ROOT/HALT_OVERWATCH) exists, log to stderr and return 3
#     without taking any action.
#  2. Guard: if the fleet-wide $KANBAN_ROOT/HALT exists, narrate the
#     deferral and return 5 without taking any action — with or without
#     --ship-policy-resolution (CM never writes the fleet-wide file, so a
#     HALT there is an operator stopping every project).
#  3. Resolve the per-project repo lock path via pp_repo_lock_path
#     ($OVERWATCH_PROJECT) — the identical helper wake dispatch uses, so
#     OVERWATCH always inspects the lock a wake actually holds for this
#     project rather than a name derived (or hardcoded) independently.
#  4. Guard + hold: open the resolved lock file and attempt a non-blocking
#     exclusive flock. If contended (another agent is running), retry with
#     backoff for up to $OVERWATCH_LOCK_WAIT_SECONDS (default 0: a single
#     attempt); still contended after that, log to stderr and return 4
#     without taking any action. If acquired, KEEP the
#     lock held for the remainder of this function — through the HALT
#     touch, <fn>, and the HALT restore — so no wake can start processing
#     this project between our contention check and our first state write
#     (closes the TOCTOU the previous check-then-release sequence had).
#  5. Under the lock, check whether the project-scoped HALT
#     ($KANBAN_ROOT/projects/$OVERWATCH_PROJECT/HALT) already exists BEFORE
#     we touch it. Every OVERWATCH-created HALT is created and removed under
#     this same lock, so one present now was not created by a running
#     OVERWATCH fix.
#     - If it already exists: release the lock, narrate the deferral and
#       return 5 — unless --ship-policy-resolution was given, in which case
#       run <fn> with the HALT left exactly as it is (never touched, never
#       removed).
#       A HALT a dead OVERWATCH repair left behind (its creator marker names
#       a process that no longer runs; see overwatch_repair_halt_state) is
#       removed first and does not count as present.
#     - If it does not exist: create it to stop this project's chain agents
#       from starting new tasks -- carrying the creator marker
#       (overwatch_write_repair_halt) -- and remember to remove it during
#       cleanup.
#     Register a trap to run cleanup on EXIT (removes HALT only if this
#     invocation created it, and always releases the repo lock fd), so
#     cleanup always happens even if <fn> fails or the shell exits
#     abnormally.
#  6. Run <fn> (called as a shell function — must be defined in the caller's
#     environment). Capture its exit code.
#  7. Run cleanup explicitly (the trap also handles this if step 6 exits
#     unexpectedly): remove HALT only if this invocation created it in step
#     5, then release the repo lock fd. Unregister the trap.
#  8. Return the exit code captured in step 6.
#
# --ship-policy-resolution: the one opt-in past a pre-existing project HALT.
#     Passed only by block triage handing a ship_policy_halt task to the
#     resolution stage, whose whole job is to classify and resolve the HALT
#     CM wrote — that path classifies the HALT itself (block_stage_deferral's
#     ship_policy_halt_state) and writes nothing when it is not CM's. No
#     other caller may pass it.
#
# Sentinel exit codes:
#   3 — HALT_OVERWATCH guard tripped (step 1 above)
#   4 — per-repo flock contended (step 4 above)
#   5 — fix deferred: HALT present (step 2 or step 5 above)
#
# Args:
#   --ship-policy-resolution  (optional) see above
#   $1  fn  — name of a bash function to invoke
#
# Returns:
#   <fn>'s exit code on normal execution
#   3 if HALT_OVERWATCH guard tripped
#   4 if per-repo flock is contended
#   5 if a HALT defers the fix
#   1 on setup error (KANBAN_ROOT/OVERWATCH_PROJECT unset, fn not callable,
#     repo lock path unresolvable, unknown option)
#
# Emits structured debug lines to stderr throughout.
# ---------------------------------------------------------------------------
overwatch_halt_first_fix() {
    local ship_policy_resolution="false"
    if [[ "${1:-}" == "--ship-policy-resolution" ]]; then
        ship_policy_resolution="true"
        shift
    elif [[ "${1:-}" == --* ]]; then
        echo "overwatch_protocol.sh: overwatch_halt_first_fix: unknown option '${1}' (accepted: --ship-policy-resolution)" >&2
        return 1
    fi
    local fn="${1:-}"
    local halt_flag halt_overwatch_flag global_halt_flag project_lock_file project_lock_fd fn_exit_code
    local halt_pre_existing="false"

    echo "overwatch_protocol: overwatch_halt_first_fix: fn=${fn} ship_policy_resolution=${ship_policy_resolution}" >&2

    # Validate args and environment
    if [[ -z "$fn" ]]; then
        echo "overwatch_protocol.sh: overwatch_halt_first_fix: fn argument is required" >&2
        return 1
    fi
    _overwatch_protocol_check_env "overwatch_halt_first_fix" || return 1

    # Verify fn is callable
    if ! declare -f "$fn" > /dev/null 2>&1; then
        echo "overwatch_protocol.sh: overwatch_halt_first_fix: '${fn}' is not a defined bash function" >&2
        return 1
    fi

    # pp_repo_lock_path is the single source of truth for the per-project
    # repo lock path (team/scripts/lib/project_paths.sh). Source it
    # defensively if the caller hasn't already, so this file keeps working
    # when sourced standalone.
    if ! declare -f pp_repo_lock_path > /dev/null 2>&1; then
        local _ohff_lib_dir
        _ohff_lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)" || {
            echo "overwatch_protocol.sh: overwatch_halt_first_fix: cannot resolve lib dir to source project_paths.sh" >&2
            return 1
        }
        if [[ -f "${_ohff_lib_dir}/project_paths.sh" ]]; then
            # shellcheck source=/dev/null
            source "${_ohff_lib_dir}/project_paths.sh"
        else
            echo "overwatch_protocol.sh: overwatch_halt_first_fix: project_paths.sh not found at ${_ohff_lib_dir}" >&2
            return 1
        fi
    fi

    # pgai_lock_acquire_waiting/pgai_lock_release are the shared acquire/release
    # helpers (team/scripts/lib/lock_provenance.sh) that record who holds a
    # lock as part of taking it. Source defensively, same idiom as above.
    if ! declare -f pgai_lock_acquire_waiting > /dev/null 2>&1; then
        local _ohff_lock_lib_dir
        _ohff_lock_lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)" || {
            echo "overwatch_protocol.sh: overwatch_halt_first_fix: cannot resolve lib dir to source lock_provenance.sh" >&2
            return 1
        }
        if [[ -f "${_ohff_lock_lib_dir}/lock_provenance.sh" ]]; then
            # shellcheck source=/dev/null
            source "${_ohff_lock_lib_dir}/lock_provenance.sh"
        else
            echo "overwatch_protocol.sh: overwatch_halt_first_fix: lock_provenance.sh not found at ${_ohff_lock_lib_dir}" >&2
            return 1
        fi
    fi

    # Project-scoped HALT: this wrapper's callers each repair exactly one
    # project's state (named by $OVERWATCH_PROJECT), so the sentinel it
    # touches lives at that project's own HALT, not the fleet-wide
    # $KANBAN_ROOT/HALT. pp_project_root is defined by project_paths.sh,
    # already sourced (or defensively sourced above) by the time we get here.
    halt_flag="$(pp_project_root "$OVERWATCH_PROJECT")/HALT"
    halt_overwatch_flag="${KANBAN_ROOT}/HALT_OVERWATCH"
    global_halt_flag="${KANBAN_ROOT}/HALT"

    # Guard 1: HALT_OVERWATCH — env var or flag file
    if [[ -n "${HALT_OVERWATCH:-}" ]]; then
        echo "overwatch_protocol: overwatch_halt_first_fix: HALT_OVERWATCH env var is set; declining to act (sentinel 3)" >&2
        return 3
    fi
    if [[ -f "$halt_overwatch_flag" ]]; then
        echo "overwatch_protocol: overwatch_halt_first_fix: HALT_OVERWATCH flag file present (${halt_overwatch_flag}); declining to act (sentinel 3)" >&2
        return 3
    fi

    # Guard 2: the fleet-wide HALT defers every fix, opt-in or not.
    if [[ -f "$global_halt_flag" ]]; then
        _overwatch_hff_narrate_deferral "$fn" "$global_halt_flag"
        return 5
    fi

    # Resolve the SAME per-project repo lock the wake dispatchers hold.
    project_lock_file="$(pp_repo_lock_path "$OVERWATCH_PROJECT")" || {
        echo "overwatch_protocol.sh: overwatch_halt_first_fix: cannot resolve repo lock path for project '${OVERWATCH_PROJECT}'" >&2
        return 1
    }

    # Guard 3 + mutation lock: acquire the project's repo flock and KEEP it
    # held for the remainder of this function (through the HALT touch, <fn>,
    # and the HALT restore). Holding across the mutation — rather than
    # checking contention and releasing before acting — closes the TOCTOU
    # window where a wake could start processing this project between our
    # check and our first state write. pgai_lock_acquire records
    # holder/pid/op/task as part of taking the lock (lock_provenance.sh);
    # its own stderr message on contention already names the current
    # holder. There is no task ID to name here (a wrapped repair function
    # is not scoped to one task), so task is the explicit literal "none".
    # A caller that expects the lock to be released shortly (the sweep a
    # wake chain nudges as it ends) sets OVERWATCH_LOCK_WAIT_SECONDS, and
    # contention is retried with backoff for that long before declining.
    local _ohff_lock_rc _ohff_lock_wait="${OVERWATCH_LOCK_WAIT_SECONDS:-0}"
    [[ "$_ohff_lock_wait" =~ ^[0-9]+$ ]] || _ohff_lock_wait=0
    _ohff_lock_rc=0
    pgai_lock_acquire_waiting "$_ohff_lock_wait" "$project_lock_file" "overwatch" "$$" "overwatch-halt-first-fix:${fn}" "none" project_lock_fd \
        || _ohff_lock_rc=$?
    if [[ $_ohff_lock_rc -ne 0 ]]; then
        if (( _ohff_lock_wait > 0 )); then
            echo "overwatch_protocol: overwatch_halt_first_fix: per-repo flock still contended after retrying for up to ${_ohff_lock_wait}s; declining to act (sentinel 4)" >&2
        else
            echo "overwatch_protocol: overwatch_halt_first_fix: per-repo flock contended; declining to act (sentinel 4)" >&2
        fi
        return 4
    fi

    # Step 5: Under the lock, record whether HALT already existed BEFORE we
    # touch it. An operator-placed (or otherwise pre-existing) HALT is
    # inviolate — we only ever remove a HALT this invocation created itself,
    # or one whose creator marker names an OVERWATCH repair that is no
    # longer running — and it defers the fix unless this is the ship-policy
    # resolution path.
    overwatch_clear_dead_repair_halt "$halt_flag" || true
    if [[ -f "$halt_flag" ]]; then
        halt_pre_existing="true"
        if [[ "$ship_policy_resolution" != "true" ]]; then
            pgai_lock_release "$project_lock_file" "$project_lock_fd"
            _overwatch_hff_narrate_deferral "$fn" "$halt_flag"
            return 5
        fi
        echo "overwatch_protocol: overwatch_halt_first_fix: HALT already present at ${halt_flag}; ship-policy resolution opt-in given, running ${fn} without touching or removing it" >&2
    else
        echo "overwatch_protocol: overwatch_halt_first_fix: touching HALT at ${halt_flag}" >&2
        overwatch_write_repair_halt "$halt_flag" "$fn" || {
            echo "overwatch_protocol.sh: overwatch_halt_first_fix: cannot touch HALT file: ${halt_flag}" >&2
            pgai_lock_release "$project_lock_file" "$project_lock_fd"
            return 1
        }
    fi

    # Trap: run cleanup on EXIT (covers normal exit, error exit, and signal
    # exit) — removes HALT only if this invocation created it, and always
    # releases the repo lock fd.
    _overwatch_hff_cleanup() {
        if [[ "$halt_pre_existing" == "false" && -f "${halt_flag}" ]]; then
            rm -f "${halt_flag}" 2>/dev/null || true
            echo "overwatch_protocol: overwatch_halt_first_fix: HALT removed at ${halt_flag}" >&2
        fi
        pgai_lock_release "$project_lock_file" "$project_lock_fd"
    }
    trap '_overwatch_hff_cleanup' EXIT

    # Step 6: Run the wrapped function
    echo "overwatch_protocol: overwatch_halt_first_fix: invoking ${fn}" >&2
    fn_exit_code=0
    "$fn" || fn_exit_code=$?
    echo "overwatch_protocol: overwatch_halt_first_fix: ${fn} returned ${fn_exit_code}" >&2

    # Step 7: Clean up explicitly (trap also covers unexpected exits).
    _overwatch_hff_cleanup
    trap - EXIT

    return "$fn_exit_code"
}

# ---------------------------------------------------------------------------
# Repair-HALT creator marker
#
# A project HALT overwatch_halt_first_fix creates carries one marker line
# naming the process that created it:
#   # overwatch-repair-halt: pid=<pid> start=<start> fn=<fn> at=<UTC time>
# <start> is the process start time from /proc/<pid>/stat (clock ticks since
# boot), so a recycled pid never reads as the creator. A sweep stopped
# mid-repair (SIGKILL, a container stop) cannot remove its own HALT; the
# marker lets the next sweep tell that HALT from an operator's and remove
# it. A HALT without a well-formed marker -- any operator or CM HALT -- is
# never removed by OVERWATCH.
# ---------------------------------------------------------------------------
OVERWATCH_REPAIR_HALT_MARKER="# overwatch-repair-halt:"

# _overwatch_proc_start_time <pid>
# Echo the start time of <pid> (field 22 of /proc/<pid>/stat, read past the
# parenthesised command name); return 1 when it cannot be read.
_overwatch_proc_start_time() {
    local stat rest
    stat="$(cat "/proc/$1/stat" 2>/dev/null)" || return 1
    rest="${stat##*) }"
    local -a fields
    read -r -a fields <<< "$rest"
    [[ "${fields[19]:-}" =~ ^[0-9]+$ ]] || return 1
    printf '%s\n' "${fields[19]}"
}

# overwatch_write_repair_halt <halt_file> <fn>
# Create <halt_file> as an OVERWATCH repair HALT carrying the creator marker
# for this shell ($$). Returns non-zero when the file cannot be written.
overwatch_write_repair_halt() {
    local halt_file="$1" fn="$2" start
    start="$(_overwatch_proc_start_time "$$")" || start=0
    printf '%s\n%s\n' \
        "# HALT created by OVERWATCH while it repairs this project (${fn}); removed when the repair ends." \
        "${OVERWATCH_REPAIR_HALT_MARKER} pid=$$ start=${start} fn=${fn} at=$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        > "$halt_file"
}

# overwatch_repair_halt_state <halt_file>
# Echo "live" when <halt_file> is an OVERWATCH repair HALT whose creator
# still runs, "dead" when it is one whose creator is gone, and "other" for
# anything else (an operator or CM HALT, a malformed marker, no file).
# Fails closed: only an exact marker line can make a HALT "dead".
overwatch_repair_halt_state() {
    local halt_file="$1" line pid start now
    [[ -f "$halt_file" ]] || { echo other; return 0; }
    while IFS= read -r line; do
        if [[ "$line" =~ ^\#\ overwatch-repair-halt:\ pid=([0-9]+)\ start=([0-9]+)\ fn=[^[:space:]]+\ at=[0-9TZ:-]+$ ]]; then
            pid="${BASH_REMATCH[1]}"
            start="${BASH_REMATCH[2]}"
            now="$(_overwatch_proc_start_time "$pid")" || now=""
            if [[ -n "$now" && "$now" == "$start" ]]; then
                echo live
            else
                echo dead
            fi
            return 0
        fi
    done < "$halt_file"
    echo other
}

# overwatch_clear_dead_repair_halt <halt_file>
# Remove <halt_file> when it is an OVERWATCH repair HALT whose creator is
# gone (overwatch_repair_halt_state "dead"), narrating the removal on stderr
# and in the project's action log. Returns 0 when it removed the file, 1
# otherwise (no file, a live repair's HALT, or a HALT OVERWATCH did not
# create -- which it never touches).
overwatch_clear_dead_repair_halt() {
    local halt_file="$1"
    [[ "$(overwatch_repair_halt_state "$halt_file")" == "dead" ]] || return 1
    local marker
    marker="$(grep -m1 -F "${OVERWATCH_REPAIR_HALT_MARKER}" "$halt_file" 2>/dev/null)"
    rm -f "$halt_file" 2>/dev/null || return 1
    echo "overwatch_protocol: removed ${halt_file}: an OVERWATCH repair HALT whose creator is no longer running (${marker#"${OVERWATCH_REPAIR_HALT_MARKER} "})" >&2
    overwatch_log_action \
        "overwatch-halt-first-fix" \
        "${halt_file}" \
        "stale-repair-halt-removed" \
        "none" \
        "removed the project HALT a stopped OVERWATCH repair left behind (${marker#"${OVERWATCH_REPAIR_HALT_MARKER} "}); an operator or CM HALT is never removed" \
        2>/dev/null || true
    return 0
}

# ---------------------------------------------------------------------------
# _overwatch_hff_narrate_deferral <fn> <halt_path>
# The one "fix deferred: HALT present" narration overwatch_halt_first_fix
# makes when a HALT defers a fix (sentinel 5): the dual-channel error
# contract on stderr (shared emitter, error_contract.sh) and an action-log
# row naming the HALT file and the fix left unrun. Both writes are
# best-effort — a missing state directory or emitter never turns a deferral
# into an error. Always returns 0.
# ---------------------------------------------------------------------------
_overwatch_hff_narrate_deferral() {
    local fn="$1" halt_path="$2"
    if ! declare -f pgai_emit_error_contract > /dev/null 2>&1; then
        local _ohff_ec_dir
        _ohff_ec_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)"
        # shellcheck source=/dev/null
        [[ -n "$_ohff_ec_dir" && -f "${_ohff_ec_dir}/error_contract.sh" ]] && source "${_ohff_ec_dir}/error_contract.sh"
    fi
    # BLOCK-WORTHY: an invariant by design. A HALT is an operator's (or
    # CM's) stop and only the operator removes it; OVERWATCH keeps observing
    # read-only and leaves the fix unrun rather than working through the
    # stop. Nothing is changed; the caller moves on.
    # CENSUS-ANCHOR: overwatch-fix-deferred-halt-01
    if declare -f pgai_emit_error_contract > /dev/null 2>&1; then
        pgai_emit_error_contract "overwatch.fix_deferred_halt_present" \
            "fix deferred: HALT present at ${halt_path}; OVERWATCH left ${fn} unrun and changed nothing. Read-only scans and bug filing continue; fixes resume once the operator removes the HALT." \
            "cat ${halt_path}" "${OVERWATCH_PROJECT:-}" || true
    else
        echo "overwatch_protocol: overwatch_halt_first_fix: fix deferred: HALT present at ${halt_path}; ${fn} not run, nothing changed (sentinel 5)" >&2
    fi
    overwatch_log_action \
        "overwatch-halt-first-fix" \
        "${fn}" \
        "fix-deferred" \
        "none" \
        "fix deferred: HALT present at ${halt_path}; ${fn} not run, nothing changed" \
        2>/dev/null || true
    return 0
}

# ---------------------------------------------------------------------------
# transient_retry_limit
# Resolve the ONE retry ceiling shared by every transient-retry consumer:
# wake_common.sh's handle_working_after_exit / recheck_blocked_tasks, and
# OVERWATCH's check-transient-api-error.sh. This single resolver replaces
# what were once two independently-hardcoded ceilings, so both paths always
# agree on the same value.
#
# Precedence (highest first):
#   1. TRANSIENT_RETRY_CAP env var, if already set. wake_common.sh's
#      config_loader.sh populates this from kanban.cfg [wake]
#      transient_retry_cap before any transient-retry function runs, so the
#      wake path always hits this branch.
#   2. kanban.cfg [wake] transient_retry_cap, read directly via a minimal
#      awk scan (no ini_parser.sh dependency) — keeps this function callable
#      from OVERWATCH's sweep runner, which does not source config_loader.sh.
#      Uses KANBAN_ROOT (falls back to PGAI_AGENT_KANBAN_ROOT_PATH) to find
#      kanban.cfg.
#   3. Default: 3.
#
# Echoes the resolved integer ceiling to stdout. Always succeeds (returns 0).
# ---------------------------------------------------------------------------
transient_retry_limit() {
    if [[ -n "${TRANSIENT_RETRY_CAP:-}" && "${TRANSIENT_RETRY_CAP}" =~ ^[0-9]+$ ]]; then
        echo "${TRANSIENT_RETRY_CAP}"
        return 0
    fi

    local _root="${KANBAN_ROOT:-${PGAI_AGENT_KANBAN_ROOT_PATH:-}}"
    local _cfg="${_root}/kanban.cfg"
    local _raw=""
    if [[ -n "${_root}" && -f "${_cfg}" ]]; then
        _raw="$(awk '
            /^\[wake\]/ { in_wake=1; next }
            /^\[/ { in_wake=0 }
            in_wake && $0 ~ /^[[:space:]]*transient_retry_cap[[:space:]]*=/ {
                sub(/^[^=]*=[[:space:]]*/, "")
                gsub(/[[:space:]]+$/, "")
                print
                exit
            }
        ' "${_cfg}" 2>/dev/null)"
    fi

    if [[ "${_raw}" =~ ^[0-9]+$ ]]; then
        echo "${_raw}"
    else
        echo "3"
    fi
}

# ---------------------------------------------------------------------------
# overwatch_load_module_runner
# Make pp_run_ops (pp_run_ops.sh), pgai_atomic_write (atomic_write.sh) and
# the ## Block Record writers (block_record.sh: block_record_write and its
# evidence helpers) available, sourcing each from this file's own directory
# only when the
# caller has not already defined it — a caller's own copy, or a test stub,
# wins. Loaded on first use rather than at source time so sourcing this file
# stays side-effect-free. The durable-record readers and writers below, and
# check-transient-api-error.sh's status writers, call it before their first
# module call.
#
# Returns 0 when all three are defined afterwards; 1, with a message on
# stderr naming the missing file, otherwise.
# ---------------------------------------------------------------------------
overwatch_load_module_runner() {
    local _owp_lib_dir
    _owp_lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)" || {
        echo "overwatch_protocol.sh: overwatch_load_module_runner: cannot resolve lib dir" >&2
        return 1
    }
    if ! declare -F pp_run_ops >/dev/null 2>&1; then
        # shellcheck source=pp_run_ops.sh
        source "${_owp_lib_dir}/pp_run_ops.sh" 2>/dev/null || {
            echo "overwatch_protocol.sh: cannot load ${_owp_lib_dir}/pp_run_ops.sh" >&2
            return 1
        }
    fi
    if ! declare -F pgai_atomic_write >/dev/null 2>&1; then
        # shellcheck source=atomic_write.sh
        source "${_owp_lib_dir}/atomic_write.sh" 2>/dev/null || {
            echo "overwatch_protocol.sh: cannot load ${_owp_lib_dir}/atomic_write.sh" >&2
            return 1
        }
    fi
    if ! declare -F block_record_write >/dev/null 2>&1; then
        # shellcheck source=block_record.sh
        source "${_owp_lib_dir}/block_record.sh" 2>/dev/null || {
            echo "overwatch_protocol.sh: cannot load ${_owp_lib_dir}/block_record.sh" >&2
            return 1
        }
    fi
}

# ---------------------------------------------------------------------------
# _overwatch_write_record <caller> <status_file> <heading> <body>
# Set the ## <heading> record in <status_file> to <body>, verbatim, through
# pgai_agent_kanban.task_status render --append-missing: the body is
# replaced in place when the section exists (heading line and the separator
# before the next section kept byte-for-byte; every other section
# untouched), and the section is appended at the end of the file when it
# does not. The new file is committed with pgai_atomic_write, so a reader
# sees the old or the new bytes, the file keeps its mode, and a failure
# leaves it untouched. On failure prints "<caller>: cannot write
# <status_file>" to stderr after the module's own line and returns 1.
# ---------------------------------------------------------------------------
_overwatch_write_record() {
    local caller="$1"
    local status_file="$2"
    local heading="$3"
    local body="$4"

    overwatch_load_module_runner || return 1
    pgai_atomic_write "${status_file}" pp_run_ops pgai_agent_kanban.task_status render \
        "${status_file}" --section="${heading}" --body="${body}" --append-missing="${heading}" \
        || { echo "${caller}: cannot write ${status_file}" >&2; return 1; }
}

# ---------------------------------------------------------------------------
# transient_retry_field <status_file> <field_name> [default]
# Read one "key: value" line out of the durable ## Transient Retry record in
# <status_file>. The record holds five keys: reason_code, retry_count,
# retry_limit, provider, last_error.
#
# Returns <default> (empty string if omitted) when:
#   - <status_file> does not exist or is unreadable
#   - the ## Transient Retry section is absent
#   - the section body is the literal "none" (no retry has ever occurred)
#   - the named field is not present inside the section
#
# Args:
#   $1  status_file  — absolute path to the task's status.md
#   $2  field_name    — one of reason_code, retry_count, retry_limit,
#                        provider, last_error
#   $3  default       — value to echo when unset (optional, default "")
#
# Echoes the resolved value to stdout and returns 0; returns 1 (stderr
# names the file) only when <status_file> exists but cannot be read. The
# value is read by pgai_agent_kanban.task_status read --field.
# ---------------------------------------------------------------------------
transient_retry_field() {
    local status_file="$1"
    local field_name="$2"
    local default_value="${3:-}"

    overwatch_load_module_runner || return 1
    pp_run_ops pgai_agent_kanban.task_status read "${status_file}" \
        --section="Transient Retry" --field="${field_name}" --default="${default_value}"
}

# ---------------------------------------------------------------------------
# transient_retry_write <status_file> <reason_code> <retry_count>
#                        <retry_limit> <provider> <last_error>
# Write (or update) the durable ## Transient Retry record in <status_file>.
# This is the ONE writer for the record — only wake_common.sh's
# handle_working_after_exit calls it, so there is a single owning controller.
# If the section is absent, it is appended at the end of the file; if
# present, its body is replaced in place — all other sections (including
# ## Blocked Reason, which stays human narrative only) are untouched. The
# write goes through _overwatch_write_record (task_status render
# --append-missing, committed atomically).
#
# Deliberately NOT touched by promote_blocked_to_backlog / promote_waiting_to_backlog
# / OVERWATCH's requeue path — the record must survive BACKLOG promotion so the
# count never resets while a task is still within the retry ceiling.
#
# Args:
#   $1  status_file  — absolute path to the task's status.md
#   $2  reason_code  — machine-readable classification (e.g. transient_api_error)
#   $3  retry_count  — cumulative count of transient failures observed so far
#   $4  retry_limit  — the ceiling in effect when this record was written
#                       (transient_retry_limit's resolved value)
#   $5  provider     — which provider CLI hit the error (claude, codex, ...)
#   $6  last_error   — short human-readable fragment of the matched signature
#
# Returns 0 on success, non-zero if the status file cannot be read/written.
# ---------------------------------------------------------------------------
transient_retry_write() {
    local status_file="$1"
    local reason_code="$2"
    local retry_count="$3"
    local retry_limit="$4"
    local provider="$5"
    local last_error="$6"

    local body
    body="reason_code: ${reason_code}"$'\n'"retry_count: ${retry_count}"$'\n'
    body+="retry_limit: ${retry_limit}"$'\n'"provider: ${provider}"$'\n'"last_error: ${last_error}"
    _overwatch_write_record transient_retry_write "${status_file}" "Transient Retry" "${body}"
}

# ---------------------------------------------------------------------------
# transient_backoff_due <status_file>
# Whether the backoff window before the next transient-error requeue of the
# task has passed. Both transient requeue paths ask before requeueing: the
# wake's recheck_blocked_tasks auto-retry and check-transient-api-error.sh.
# The window grows with the ## Transient Retry retry_count (5, 10, then 20
# minutes as shipped: the transient_api_error row of the block-triage
# classifier table, block-classifier.yaml) and runs from the ## Block
# Record's last_blocked_at; pgai_agent_kanban.overwatch.transient_backoff
# computes it.
#
# Returns 0 when the requeue may happen now; 1, printing the not-before
# time (ISO-8601) on stdout, when it must wait. Fails open: no Block Record,
# an unloadable table, or a module that cannot run reads as due (0) — the
# backoff delays a retry, it never stops one. The retry ceiling is the
# callers' own (transient_retry_limit) and is not checked here.
# ---------------------------------------------------------------------------
transient_backoff_due() {
    local status_file="$1"
    overwatch_load_module_runner || return 0
    local out="" rc=0
    out="$(pp_run_ops pgai_agent_kanban.overwatch.transient_backoff due "${status_file}")" || rc=$?
    if (( rc == 3 )); then
        printf '%s\n' "${out#not-before }"
        return 1
    fi
    return 0
}

# ---------------------------------------------------------------------------
# dispatch_record_* — the per-task dispatch ledger.
#
# The provider CLI's stdout/stderr for a dispatched task lands in the wake
# firing's batch log ($KANBAN_ROOT/logs/agents/<agent>-batch-<ts>.log, the
# LOG_FILE wake_bootstrap.sh opens once per firing), NOT in the task's own
# logs/ directory — that holds only what the agent itself writes. One batch
# log can carry several tasks' output in sequence, so "which log" alone is
# not enough to attribute a provider error to a task: the ledger also records
# the byte span of the batch log that belongs to this dispatch.
#
# File: <task_dir>/logs/dispatch.jsonl — append-only, one JSON object per
# line, written by wd_invoke_task (wake_driver_invoke.sh) around each
# provider invocation and read by OVERWATCH's check-transient-api-error.sh:
#   {"recorded_at": "<UTC ISO-8601>", "event": "dispatched"|"returned",
#    "agent": "<agent>", "provider": "<provider>", "batch_log": "<abs path>",
#    "offset_start": <bytes>, "offset_end": <bytes>|null, "exit_code": <int>|null}
# "dispatched" is appended just before the provider runs (offset_start is the
# batch log's size at that moment; offset_end/exit_code are null); "returned"
# is appended after it exits with the span closed and the exit code. The
# LAST line is always the task's most recent dispatch, so a re-dispatch
# supersedes the earlier record without rewriting the file. A task that was
# never dispatched by a wake (pre-existing tasks, hand-made fixtures) simply
# has no ledger — readers treat that as "unknown", never as an error.
# ---------------------------------------------------------------------------

# _dispatch_record_path <task_dir>
# Echo the ledger path for a task directory.
_dispatch_record_path() {
    echo "${1%/}/logs/dispatch.jsonl"
}

# ---------------------------------------------------------------------------
# dispatch_record_append <task_dir> <event> <batch_log> <offset_start>
#                        [offset_end] [exit_code]
# Append one line to the task's dispatch ledger. <event> is "dispatched" or
# "returned"; <offset_start>/<offset_end> are byte offsets into <batch_log>
# (an empty or non-numeric offset_end/exit_code is recorded as null). The
# agent and provider names come from AGENT and ACTIVE_PROVIDER when set.
# Returns 0 on success; non-zero (with a message on stderr) when the ledger
# cannot be written. Callers on the wake path treat a failure as a warning:
# a missing ledger only costs OVERWATCH a skip, never the dispatch itself.
# ---------------------------------------------------------------------------
dispatch_record_append() {
    local task_dir="$1"
    local event="$2"
    local batch_log="$3"
    local offset_start="$4"
    local offset_end="${5:-}"
    local exit_code="${6:-}"

    if [[ -z "${task_dir}" || -z "${event}" || -z "${batch_log}" ]]; then
        echo "dispatch_record_append: task_dir, event and batch_log are required (got task_dir='${task_dir}', event='${event}', batch_log='${batch_log}')" >&2
        return 1
    fi

    local ledger
    ledger="$(_dispatch_record_path "${task_dir}")"
    mkdir -p "$(dirname "${ledger}")" 2>/dev/null || {
        echo "dispatch_record_append: cannot create $(dirname "${ledger}")" >&2
        return 1
    }

    python3 - "${ledger}" "${event}" "${AGENT:-unknown}" "${ACTIVE_PROVIDER:-unknown}" \
        "${batch_log}" "${offset_start}" "${offset_end}" "${exit_code}" <<'PY'
import datetime, json, sys

ledger, event, agent, provider, batch_log, offset_start, offset_end, exit_code = sys.argv[1:9]

def as_int(raw):
    raw = (raw or "").strip()
    return int(raw) if raw.isdigit() else None

record = {
    "recorded_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "event": event,
    "agent": agent,
    "provider": provider,
    "batch_log": batch_log,
    "offset_start": as_int(offset_start) or 0,
    "offset_end": as_int(offset_end),
    "exit_code": as_int(exit_code),
}
try:
    with open(ledger, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")
except OSError as e:
    raise SystemExit(f"dispatch_record_append: cannot write {ledger}: {e}")
PY
}

# ---------------------------------------------------------------------------
# dispatch_record_latest <task_dir>
# Resolve the task's most recent dispatch from its ledger. Echoes ONE line,
# tab-separated: <batch_log>\t<offset_start>\t<offset_end> — offset_end is
# empty when the latest record is still open (no "returned" line yet), in
# which case the span runs to the end of the batch log. Lines that are not
# well-formed JSON objects are ignored (a torn write must never mask an
# earlier good record). Returns 1, echoing nothing, when the task has no
# ledger or no usable record — "never dispatched by a wake", not a fault.
# ---------------------------------------------------------------------------
dispatch_record_latest() {
    local task_dir="$1"
    local ledger
    ledger="$(_dispatch_record_path "${task_dir}")"
    [[ -f "${ledger}" ]] || return 1

    python3 - "${ledger}" <<'PY'
import json, sys

latest = None
with open(sys.argv[1], encoding="utf-8", errors="replace") as fh:
    for line in fh:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict) and obj.get("batch_log"):
            latest = obj

if latest is None:
    raise SystemExit(1)

start = latest.get("offset_start")
end = latest.get("offset_end")
print("\t".join([
    str(latest["batch_log"]),
    str(start if isinstance(start, int) and start >= 0 else 0),
    str(end) if isinstance(end, int) and end >= 0 else "",
]))
PY
}

# ---------------------------------------------------------------------------
# zero_progress_cap
# Resolve the ONE ceiling for the durable ## Zero Progress record (see the
# zero_progress_* header block above for the zero-progress definition and why
# this is a separate concern from transient_retry_limit()).
#
# Precedence (highest first), mirroring transient_retry_limit() exactly:
#   1. ZERO_PROGRESS_CAP env var, if already set (config_loader.sh populates
#      this from kanban.cfg [wake] zero_progress_cap before any zero-progress
#      function runs).
#   2. kanban.cfg [wake] zero_progress_cap, read directly via a minimal awk
#      scan — keeps this function callable from contexts that do not source
#      config_loader.sh.
#   3. Default: 3.
#
# Echoes the resolved integer ceiling to stdout. Always succeeds (returns 0).
# ---------------------------------------------------------------------------
zero_progress_cap() {
    if [[ -n "${ZERO_PROGRESS_CAP:-}" && "${ZERO_PROGRESS_CAP}" =~ ^[0-9]+$ ]]; then
        echo "${ZERO_PROGRESS_CAP}"
        return 0
    fi

    local _root="${KANBAN_ROOT:-${PGAI_AGENT_KANBAN_ROOT_PATH:-}}"
    local _cfg="${_root}/kanban.cfg"
    local _raw=""
    if [[ -n "${_root}" && -f "${_cfg}" ]]; then
        _raw="$(awk '
            /^\[wake\]/ { in_wake=1; next }
            /^\[/ { in_wake=0 }
            in_wake && $0 ~ /^[[:space:]]*zero_progress_cap[[:space:]]*=/ {
                sub(/^[^=]*=[[:space:]]*/, "")
                gsub(/[[:space:]]+$/, "")
                print
                exit
            }
        ' "${_cfg}" 2>/dev/null)"
    fi

    if [[ "${_raw}" =~ ^[0-9]+$ ]]; then
        echo "${_raw}"
    else
        echo "3"
    fi
}

# ---------------------------------------------------------------------------
# zero_progress_field <status_file> <field_name> [default]
# Read one "key: value" line (reason_code, count, limit, or detail) out of
# the durable ## Zero Progress record in <status_file>. Mirrors
# transient_retry_field exactly: returns <default> when the file/section is
# absent, the body is "none", or the field is not present; returns 1 only
# when the file exists but cannot be read.
# ---------------------------------------------------------------------------
zero_progress_field() {
    local status_file="$1"
    local field_name="$2"
    local default_value="${3:-}"

    overwatch_load_module_runner || return 1
    pp_run_ops pgai_agent_kanban.task_status read "${status_file}" \
        --section="Zero Progress" --field="${field_name}" --default="${default_value}"
}

# ---------------------------------------------------------------------------
# zero_progress_record <status_file> <reason_code> <count> <limit> <detail>
# Write (or update) the durable ## Zero Progress record in <status_file>.
# Writers: reconcile_stale_active (wake_reconcile_sweeps.sh) and
# handle_working_after_exit's non-transient fallback (wake_lifecycle.sh) for
# a non-zero count; zero_progress_reset (below) for a cleared record.
# Ensure-or-append on an absent section, in-place replace otherwise, through
# _overwatch_write_record — mirrors transient_retry_write. Deliberately NOT touched by
# promote_blocked_to_backlog / promote_waiting_to_backlog, so the record
# survives BACKLOG promotion. reason_code is a machine classification (e.g.
# stale_active_orphan, clean_exit_no_state_change, or "none" when reset);
# limit is the ceiling in effect when written; detail is a short
# operator-facing note. Returns 0 on success, non-zero on read/write failure.
# ---------------------------------------------------------------------------
zero_progress_record() {
    local status_file="$1"
    local reason_code="$2"
    local count="$3"
    local limit="$4"
    local detail="$5"

    local body
    body="reason_code: ${reason_code}"$'\n'"count: ${count}"$'\n'
    body+="limit: ${limit}"$'\n'"detail: ${detail}"
    _overwatch_write_record zero_progress_record "${status_file}" "Zero Progress" "${body}"
}

# ---------------------------------------------------------------------------
# zero_progress_reset <status_file>
# Clear the durable ## Zero Progress record back to count=0/"none". The ONE
# reset path — called from wd_finalize_task.sh once per cycle whenever a
# task's own final state differs from WORKING through means OTHER than the
# reconcile_stale_active / handle_working_after_exit fallback paths (i.e.
# the agent itself moved the task's state) — "Any real progress resets the
# count to zero."
# ---------------------------------------------------------------------------
zero_progress_reset() {
    local status_file="$1"
    zero_progress_record "$status_file" "none" "0" "$(zero_progress_cap)" "none"
}

# ---------------------------------------------------------------------------
# zero_progress_advance_or_escalate <task_id> <task_status> <reason_code>
#                                    <detail_prefix> <error_key>
#
# Shared decision-AND-transition core for the zero-progress ceiling, called
# by BOTH reconcile_stale_active (wake_reconcile_sweeps.sh) and
# handle_working_after_exit's non-transient fallback (wake_lifecycle.sh).
# Both callers need the identical shape (increment count, compare to cap,
# transition to BACKLOG below cap or BLOCKED at cap, write the durable
# record) and the identical below/at-cap target states, so the mechanics
# live here ONCE rather than duplicated per caller — which also means there
# is exactly ONE physical refusal site for the at-cap escalation (one
# CENSUS-ANCHOR, not one per caller). Placed here rather than in
# wake_task_state.sh/wake_lifecycle.sh because both of those files already
# sit at the shared 800-line-per-file ceiling and this file does not; it
# is still purely mechanics behind the durable record this file already
# owns, not a new conceptual home.
#
# Calls transition_task_state (wake_task_transition.sh), normalize_status_file
# and set_block (wake_task_state.sh), log() (wake_bootstrap.sh), and
# pgai_format_error_contract (error_contract.sh) — all resolved at call time,
# not at this file's source time, so the caller must have already sourced
# them (every real caller has, by the time reconcile_stale_active or
# handle_working_after_exit ever runs).
#
# Args:
#   $1  task_id
#   $2  task_status    — absolute path to status.md
#   $3  reason_code    — durable-record classification (e.g.
#                         stale_active_orphan, clean_exit_no_progress)
#   $4  detail_prefix  — human text describing the zero-progress condition,
#                         WITHOUT a cycle suffix ("(cycle N/M)" is appended
#                         here so every caller's wording is consistent)
#   $5  error_key      — dual-channel error-contract machine key for the
#                         at-cap escalation (callers use distinct keys so an
#                         operator can tell which call site fired)
#
# At the cap the task's ## Block Record is written too (block_record.sh):
# reason_code provider_refusal when the latest dispatch ran under 60 s or its
# batch-log tail carries an auth, model-not-found or usage-limit signature,
# zero_progress otherwise; owner_role from the task README's ## Role. A
# refused record write is logged and never stops the escalation.
#
# Echoes the resulting state ("BACKLOG" or "BLOCKED") to stdout.
# ---------------------------------------------------------------------------
zero_progress_advance_or_escalate() {
    local task_id="$1"
    local task_status="$2"
    local reason_code="$3"
    local detail_prefix="$4"
    local error_key="$5"

    local _zp_count _zp_cap
    _zp_count="$(zero_progress_field "$task_status" "count" "0")"
    [[ "$_zp_count" =~ ^[0-9]+$ ]] || _zp_count=0
    _zp_count=$(( _zp_count + 1 ))
    _zp_cap="$(zero_progress_cap)"
    local _zp_detail="${detail_prefix} (cycle ${_zp_count}/${_zp_cap})"

    if [[ "$_zp_count" -lt "$_zp_cap" ]]; then
        if transition_task_state "$task_id" "$task_status" "BACKLOG"; then
            zero_progress_record "$task_status" "$reason_code" "$_zp_count" "$_zp_cap" "$_zp_detail"
            set_block "$task_status" "Summary" "Reset to BACKLOG by wake reconciliation: ${_zp_detail}."
            log "task ${task_id}: zero-progress cycle ${_zp_count}/${_zp_cap} — reset to BACKLOG for another attempt"
            normalize_status_file "$task_status"
            printf 'BACKLOG\n'
            return 0
        fi
        log "task ${task_id}: WARNING atomic transition to BACKLOG failed; status.md/marker left as they were (no partial update)"
        printf 'BACKLOG\n'
        return 1
    fi

    log "task ${task_id}: zero-progress ceiling reached (${_zp_count}/${_zp_cap}) — escalating to Needs Human instead of resetting"
    # BLOCK-WORTHY (invariant-by-design, SOP "The Loop"): a task learning
    # nothing in N consecutive cycles is not healed by another reset.
    # CENSUS-ANCHOR: zero-progress-cap-01
    if transition_task_state "$task_id" "$task_status" "BLOCKED"; then
        zero_progress_record "$task_status" "$reason_code" "$_zp_count" "$_zp_cap" "$_zp_detail"
        set_block "$task_status" "Blockers" "$(pgai_format_error_contract \
            "$error_key" \
            "This task made zero observable progress ${_zp_count} consecutive times (ceiling ${_zp_cap}) — the wake reconciler is no longer resetting it automatically. ${_zp_detail}." \
            "Investigate why the agent never reaches a terminal state for this task; once fixed, flip ## State back to BACKLOG and reset ## Zero Progress to count 0.")"
        set_block "$task_status" "Needs Human" "yes"
        # The Block Record names a provider refusal (the latest dispatch ran
        # under 60 s, or its batch-log tail shows an auth, model-not-found or
        # usage-limit signature) apart from a genuine zero-progress loop.
        block_record_write "$task_status" \
            "$(block_record_zero_progress_code "$(dirname "$task_status")" || echo zero_progress)" \
            "$(block_record_owner_role "$task_status")" \
            "$(block_record_evidence --task-dir "$(dirname "$task_status")" --refusal-signals --diag-log "${LOG_FILE:-}")" \
            || log "task ${task_id}: WARNING: Block Record not written"
        normalize_status_file "$task_status"
        printf 'BLOCKED\n'
        return 0
    fi
    log "task ${task_id}: WARNING atomic transition to BLOCKED failed; status.md/marker left as they were (no partial update)"
    printf 'BLOCKED\n'
    return 1
}
