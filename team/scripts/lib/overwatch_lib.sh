#!/usr/bin/env bash
# team/scripts/lib/overwatch_lib.sh
#
# Shared helpers for OVERWATCH — the autonomous kanban watchdog.
#
# SOURCE THIS FILE; do not execute it directly:
#   source "$(dirname "${BASH_SOURCE[0]}")/overwatch_lib.sh"
#
# This file is side-effect-free on source: no commands run at the top level,
# no files are created, no variables are set outside the function definitions
# and the two read-only constant declarations below.
#
# Requirements:
#   - KANBAN_ROOT must be set in the environment before calling any function
#     that resolves the state directory (overwatch_state_dir).
#   - The caller is responsible for creating the state directory before
#     calling write-side helpers (see overwatch_protocol.sh).
#     install.sh seeds the state directory on first run.
#
# Functions defined here:
#   overwatch_state_dir        <project_name>  — resolve projects/<p>/overwatch/
#   overwatch_acquire_firing_lock  <project_name>  — per-firing flock acquire
#   overwatch_release_firing_lock  <project_name>  — per-firing flock release
#   overwatch_run_with_firing_lock <project_name> <entry> <command> [args...]
#                                  — run one OVERWATCH entry point's work for
#                                    a project under the firing lock; skip it
#                                    (exit 0, narrated) when another firing
#                                    holds the lock
#
# NOTE: overwatch_log_action and overwatch_backup_file are defined in
#   overwatch_protocol.sh (the canonical single implementation). Source
#   overwatch_protocol.sh for those functions.
#
# Constants defined here:
#   HALT_OVERWATCH_FLAG  — path to the per-OVERWATCH halt flag file
#                         ($KANBAN_ROOT/HALT_OVERWATCH, evaluated at call time
#                         via a function rather than a static string so that
#                         KANBAN_ROOT can be set after sourcing this file)

# ---------------------------------------------------------------------------
# HALT_OVERWATCH_FLAG
# The conventional path for the OVERWATCH-specific halt flag.
# This is the file OVERWATCH checks before each firing: if it exists, OVERWATCH
# skips its checks and exits cleanly.
#
# Distinct from the existing $KANBAN_ROOT/HALT flag (which stops ALL agents).
# HALT_OVERWATCH_FLAG stops only the OVERWATCH process, leaving normal coder /
# CM / PM cycles unaffected.
#
# Usage:
#   if [[ -f "$HALT_OVERWATCH_FLAG" ]]; then
#       echo "OVERWATCH halted" >&2
#       exit 0
#   fi
#
# NOTE: HALT_OVERWATCH_FLAG is a name-only constant. Its value is set by
# overwatch_halt_flag_path (below) because KANBAN_ROOT may be set after
# sourcing this library. If you prefer a static constant, ensure KANBAN_ROOT
# is exported before sourcing this file.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# overwatch_halt_flag_path
# Echoes the absolute path to the OVERWATCH halt flag.
# Exits with status 1 and an error message if KANBAN_ROOT is unset.
# ---------------------------------------------------------------------------
overwatch_halt_flag_path() {
    if [[ -z "${KANBAN_ROOT:-}" ]]; then
        echo "overwatch_lib.sh: KANBAN_ROOT is not set" >&2
        return 1
    fi
    echo "${KANBAN_ROOT}/HALT_OVERWATCH"
}

# Define a convenience alias. This evaluates KANBAN_ROOT at the time the
# variable is first read, provided KANBAN_ROOT is already set.
# Callers that set KANBAN_ROOT before sourcing this file get a static value;
# callers that set it after should use overwatch_halt_flag_path() directly.
if [[ -n "${KANBAN_ROOT:-}" ]]; then
    HALT_OVERWATCH_FLAG="${KANBAN_ROOT}/HALT_OVERWATCH"
    readonly HALT_OVERWATCH_FLAG
fi

# ---------------------------------------------------------------------------
# overwatch_state_dir <project_name>
# Echoes the absolute path to the per-project OVERWATCH state directory:
#   $KANBAN_ROOT/projects/<project_name>/overwatch/
#
# Does NOT create the directory — call `mkdir -p` if you need it to exist.
#
# Args:
#   $1  project_name — e.g. "pgai-agent-kanban"
#
# Exits with status 1 if KANBAN_ROOT is unset.
# ---------------------------------------------------------------------------
overwatch_state_dir() {
    local project_name="$1"
    if [[ -z "${KANBAN_ROOT:-}" ]]; then
        echo "overwatch_lib.sh: overwatch_state_dir: KANBAN_ROOT is not set" >&2
        return 1
    fi
    if [[ -z "$project_name" ]]; then
        echo "overwatch_lib.sh: overwatch_state_dir: project_name argument is required" >&2
        return 1
    fi
    echo "${KANBAN_ROOT}/projects/${project_name}/overwatch"
}

# ---------------------------------------------------------------------------
# overwatch_acquire_firing_lock <project_name>
# Attempt to acquire an exclusive advisory flock for the current OVERWATCH
# firing. Uses a kanban-root-scoped lock file so only one OVERWATCH process
# at a time operates on the same installation, regardless of project.
#
# The lock is held via an open file descriptor stored in OVERWATCH_FIRING_LOCK_FD.
# Call overwatch_release_firing_lock when done. Acquisition goes through the
# shared pgai_lock_acquire helper (lock_provenance.sh), defensively sourced
# below if not already loaded, so the lock file records holder/pid/op/task
# instead of being an empty flock target — a contended firing can then name
# who holds it. There is no per-firing task to name (a firing spans however
# many projects/sites it processes, not one task), so task is the explicit
# literal "none" rather than left blank.
#
# Lock file path:
#   $KANBAN_ROOT/locks/overwatch-<project_name>.lock
#
# Args:
#   $1  project_name — e.g. "pgai-agent-kanban"
#
# Returns 0 if the lock was acquired.
# Returns 1 if another process already holds the lock (non-blocking).
# Returns 2 on setup error (KANBAN_ROOT unset, locks/ dir not writable, or
#   -- see lock_provenance.sh -- a provenance field came out blank).
#
# Sets OVERWATCH_FIRING_LOCK_FD (exported) on success so the caller can
# close it explicitly if needed. Also sets OVERWATCH_FIRING_LOCK_FILE.
# ---------------------------------------------------------------------------
overwatch_acquire_firing_lock() {
    local project_name="$1"
    local lock_dir lock_file

    if [[ -z "${KANBAN_ROOT:-}" ]]; then
        echo "overwatch_lib.sh: overwatch_acquire_firing_lock: KANBAN_ROOT is not set" >&2
        return 2
    fi

    if [[ -z "$project_name" ]]; then
        echo "overwatch_lib.sh: overwatch_acquire_firing_lock: project_name is required" >&2
        return 2
    fi

    # pgai_lock_acquire is the shared acquire/release helper (see
    # lock_provenance.sh). Source it defensively if the caller hasn't
    # already, so this file keeps working when sourced standalone —
    # mirrors the same defensive-source idiom overwatch_protocol.sh uses
    # for project_paths.sh.
    if ! declare -f pgai_lock_acquire > /dev/null 2>&1; then
        local _oafl_lib_dir
        _oafl_lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)" || {
            echo "overwatch_lib.sh: overwatch_acquire_firing_lock: cannot resolve lib dir to source lock_provenance.sh" >&2
            return 2
        }
        if [[ -f "${_oafl_lib_dir}/lock_provenance.sh" ]]; then
            # shellcheck source=/dev/null
            source "${_oafl_lib_dir}/lock_provenance.sh"
        else
            echo "overwatch_lib.sh: overwatch_acquire_firing_lock: lock_provenance.sh not found at ${_oafl_lib_dir}" >&2
            return 2
        fi
    fi

    lock_dir="${KANBAN_ROOT}/locks"
    lock_file="${lock_dir}/overwatch-${project_name}.lock"

    local _acquire_rc
    pgai_lock_acquire "$lock_file" "overwatch" "$$" "overwatch-firing:${project_name}" "none" OVERWATCH_FIRING_LOCK_FD
    _acquire_rc=$?
    if [[ $_acquire_rc -ne 0 ]]; then
        unset OVERWATCH_FIRING_LOCK_FD
        return "$_acquire_rc"
    fi

    export OVERWATCH_FIRING_LOCK_FD
    export OVERWATCH_FIRING_LOCK_FILE="$lock_file"
    return 0
}

# ---------------------------------------------------------------------------
# overwatch_release_firing_lock
# Release the advisory flock acquired by overwatch_acquire_firing_lock.
#
# Clears the lock file's provenance record and closes
# OVERWATCH_FIRING_LOCK_FD via the shared pgai_lock_release helper
# (lock_provenance.sh). The lock file itself is left in place, empty
# (advisory flock; the OS releases it when all fds are closed).
# Unsets OVERWATCH_FIRING_LOCK_FD and OVERWATCH_FIRING_LOCK_FILE.
#
# Safe to call even if the lock was never acquired (no-op if FD is unset).
# ---------------------------------------------------------------------------
overwatch_release_firing_lock() {
    if [[ -n "${OVERWATCH_FIRING_LOCK_FD:-}" ]]; then
        pgai_lock_release "${OVERWATCH_FIRING_LOCK_FILE:-}" "${OVERWATCH_FIRING_LOCK_FD}"
        unset OVERWATCH_FIRING_LOCK_FD
    fi
    unset OVERWATCH_FIRING_LOCK_FILE
    return 0
}

# ---------------------------------------------------------------------------
# overwatch_run_with_firing_lock <project_name> <entry> <command> [args...]
# The one way an OVERWATCH entry point -- the sweep (overwatch-sweep.sh, both
# the scheduled run and the on-BLOCK "--only block-triage" nudge) and the
# overwatch wake's scripted steps (wake_project.sh) -- runs its work for a
# project: under the project's firing lock, so two firings never act on one
# project at once.
#
#   - Lock acquired: runs <command> [args...], releases the lock, and returns
#     the command's status.
#   - Lock held by another firing: runs nothing, narrates the skip
#     dual-channel on stderr (the holder is named by the acquire just above
#     it), appends a "firing-skipped:lock-held" row to the project's action
#     log when its state directory exists, and returns 0 -- the next tick
#     does the work.
#   - Lock could not be set up (exit 2 from the acquire): narrated, and the
#     command runs without it, exactly as it did before the lock existed.
#
# <entry> names the caller in the narration and the action-log row (e.g.
# "overwatch-sweep", "overwatch-wake-block-triage").
# ---------------------------------------------------------------------------
overwatch_run_with_firing_lock() {
    local project_name="$1" entry="$2"
    shift 2
    if [[ -z "${KANBAN_ROOT:-}" || -z "$project_name" || $# -eq 0 ]]; then
        echo "overwatch_lib.sh: overwatch_run_with_firing_lock: KANBAN_ROOT, a project name and a command are required" >&2
        return 2
    fi
    mkdir -p "${KANBAN_ROOT}/locks" 2>/dev/null || true
    local lock_file="${KANBAN_ROOT}/locks/overwatch-${project_name}.lock"

    local acquire_rc=0
    overwatch_acquire_firing_lock "$project_name" || acquire_rc=$?
    if (( acquire_rc == 1 )); then
        local message="${entry} for project ${project_name} skipped: another OVERWATCH firing holds the project's firing lock ${lock_file}; nothing was done, and the next tick tries again."
        if ! declare -F pgai_emit_error_contract >/dev/null 2>&1; then
            local _owrl_lib_dir
            _owrl_lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)" || _owrl_lib_dir=""
            # shellcheck source=error_contract.sh
            [[ -n "$_owrl_lib_dir" && -f "${_owrl_lib_dir}/error_contract.sh" ]] \
                && source "${_owrl_lib_dir}/error_contract.sh"
        fi
        # BLOCK-WORTHY: an invariant by design -- one OVERWATCH firing per
        # project at a time; a contended entry point does nothing and exits 0.
        # CENSUS-ANCHOR: overwatch-lib-firing-lock-held-01
        if declare -F pgai_emit_error_contract >/dev/null 2>&1; then
            pgai_emit_error_contract "overwatch.firing_lock_held" "$message" "cat ${lock_file}" || true
        else
            printf 'error:   %s\nmessage: %s\nremedy:  %s\n' "overwatch.firing_lock_held" "$message" "cat ${lock_file}" >&2
        fi
        if declare -F overwatch_log_action >/dev/null 2>&1 \
            && [[ -d "${KANBAN_ROOT}/projects/${project_name}/overwatch" ]]; then
            OVERWATCH_PROJECT="$project_name" overwatch_log_action "$entry" "$project_name" \
                "firing-skipped:lock-held" "none" "$message" 2>/dev/null || true
        fi
        return 0
    elif (( acquire_rc != 0 )); then
        echo "overwatch_lib.sh: ${entry} for project ${project_name}: the firing lock ${lock_file} could not be set up (exit ${acquire_rc}); running without it" >&2
        "$@"
        return $?
    fi

    local rc=0
    "$@" || rc=$?
    overwatch_release_firing_lock
    return "$rc"
}
