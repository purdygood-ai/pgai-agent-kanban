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
#   - team/scripts/lib/overwatch_lib.sh must be sourced first (provides
#     overwatch_state_dir, overwatch_halt_flag_path, overwatch_acquire_firing_lock,
#     overwatch_release_firing_lock).
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
#
# Sentinel exit codes used by overwatch_halt_first_fix:
#   3 — HALT_OVERWATCH guard tripped (env var or flag file present)
#   4 — per-repo flock contended (another agent is running)
#
# Functions defined here:
#   overwatch_backup_file   <src_path>                              — backup a file
#   overwatch_log_action    <name> <target> <action> <backup_path> <reason>  — log
#   overwatch_halt_first_fix <fn>                                  — HALT-first wrapper

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
# Destination layout:
#   $KANBAN_ROOT/projects/<OVERWATCH_PROJECT>/overwatch/backups/<TIMESTAMP>/<basename>
#
# Where <TIMESTAMP> is:
#   - $OVERWATCH_FIRING_TIMESTAMP if set (groups all backups from one firing)
#   - otherwise: $(date -u +%Y-%m-%dT%H:%M:%SZ) at call time
#
# Idempotency: if the destination file already exists (same firing, same source),
# the function succeeds without overwriting and echoes the existing path.
# This makes it safe to call backup_file twice on the same file in one firing.
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
    local state_dir backup_base_dir ts backup_dir basename dst_path

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

    basename="$(basename "$src_path")"
    dst_path="${backup_dir}/${basename}"

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
#   $4  backup_path  — path returned by overwatch_backup_file, or "none"
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
# overwatch_halt_first_fix <fn>
# Safety wrapper for OVERWATCH functions that apply corrective changes to
# kanban state. Implements the HALT-first protocol: stop chain agents before
# mutating, restore after.
#
# Protocol (in order):
#  1. Guard: if HALT_OVERWATCH env var is non-empty OR the HALT_OVERWATCH flag
#     file ($KANBAN_ROOT/HALT_OVERWATCH) exists, log to stderr and return 3
#     without taking any action.
#  2. Guard: attempt a non-blocking check of the per-repo flock
#     ($KANBAN_ROOT/locks/repo-wake-pgai-kanban.lock). If that lock is
#     currently held by another process (another agent is running), log to
#     stderr and return 4 without taking any action.
#  3. Touch $KANBAN_ROOT/HALT to stop chain agents from starting new tasks.
#     Register a trap to remove $KANBAN_ROOT/HALT on EXIT, so HALT is always
#     removed even if <fn> fails or the shell exits abnormally.
#  4. Run <fn> (called as a shell function — must be defined in the caller's
#     environment). Capture its exit code.
#  5. Remove $KANBAN_ROOT/HALT (the trap also handles this if step 4 exits
#     unexpectedly). Unregister the trap.
#  6. Return the exit code captured in step 4.
#
# Sentinel exit codes:
#   3 — HALT_OVERWATCH guard tripped (step 1 above)
#   4 — per-repo flock contended (step 2 above)
#
# Args:
#   $1  fn  — name of a bash function to invoke
#
# Returns:
#   <fn>'s exit code on normal execution
#   3 if HALT_OVERWATCH guard tripped
#   4 if per-repo flock is contended
#   1 on setup error (KANBAN_ROOT/OVERWATCH_PROJECT unset, fn not callable)
#
# Emits structured debug lines to stderr throughout.
# ---------------------------------------------------------------------------
overwatch_halt_first_fix() {
    local fn="$1"
    local halt_flag halt_overwatch_flag repo_lock_file repo_lock_fd fn_exit_code

    echo "overwatch_protocol: overwatch_halt_first_fix: fn=${fn}" >&2

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

    halt_flag="${KANBAN_ROOT}/HALT"
    halt_overwatch_flag="${KANBAN_ROOT}/HALT_OVERWATCH"

    # Guard 1: HALT_OVERWATCH — env var or flag file
    if [[ -n "${HALT_OVERWATCH:-}" ]]; then
        echo "overwatch_protocol: overwatch_halt_first_fix: HALT_OVERWATCH env var is set; declining to act (sentinel 3)" >&2
        return 3
    fi
    if [[ -f "$halt_overwatch_flag" ]]; then
        echo "overwatch_protocol: overwatch_halt_first_fix: HALT_OVERWATCH flag file present (${halt_overwatch_flag}); declining to act (sentinel 3)" >&2
        return 3
    fi

    # Guard 2: per-repo flock (non-blocking check)
    # The per-repo lock file is held by wake/claude.sh while an agent
    # is actively running. If it is held, we must not apply changes now.
    repo_lock_file="${KANBAN_ROOT}/locks/repo-wake-pgai-kanban.lock"
    if [[ -f "$repo_lock_file" ]]; then
        # Attempt a non-blocking exclusive flock. If we can't get it, someone
        # else holds it — back off.
        exec {repo_lock_fd}>"$repo_lock_file" 2>/dev/null || {
            echo "overwatch_protocol: overwatch_halt_first_fix: cannot open repo lock file; backing off (sentinel 4)" >&2
            return 4
        }
        if ! flock -n "$repo_lock_fd" 2>/dev/null; then
            exec {repo_lock_fd}>&- 2>/dev/null || true
            echo "overwatch_protocol: overwatch_halt_first_fix: per-repo flock contended; declining to act (sentinel 4)" >&2
            return 4
        fi
        # We got the lock, which means nobody else holds it — release immediately.
        # We only wanted to check contention, not hold the lock ourselves.
        exec {repo_lock_fd}>&- 2>/dev/null || true
    fi

    # Step 3: Touch HALT to stop chain agents. Register trap to always remove it.
    echo "overwatch_protocol: overwatch_halt_first_fix: touching HALT at ${halt_flag}" >&2
    touch "$halt_flag" || {
        echo "overwatch_protocol.sh: overwatch_halt_first_fix: cannot touch HALT file: ${halt_flag}" >&2
        return 1
    }

    # Trap: remove HALT on EXIT (covers normal exit, error exit, and signal exit).
    # Capture the outer trap state so we can restore it after.
    _overwatch_hff_cleanup() {
        if [[ -f "${halt_flag}" ]]; then
            rm -f "${halt_flag}" 2>/dev/null || true
            echo "overwatch_protocol: overwatch_halt_first_fix: HALT removed at ${halt_flag}" >&2
        fi
    }
    trap '_overwatch_hff_cleanup' EXIT

    # Step 4: Run the wrapped function
    echo "overwatch_protocol: overwatch_halt_first_fix: invoking ${fn}" >&2
    fn_exit_code=0
    "$fn" || fn_exit_code=$?
    echo "overwatch_protocol: overwatch_halt_first_fix: ${fn} returned ${fn_exit_code}" >&2

    # Step 5: Remove HALT explicitly (trap also covers unexpected exits).
    _overwatch_hff_cleanup
    trap - EXIT

    return "$fn_exit_code"
}
