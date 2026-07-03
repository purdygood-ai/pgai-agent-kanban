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
#     calling write-side helpers (overwatch_log_action, overwatch_backup_file).
#     install.sh seeds the state directory on first run.
#
# Functions defined here:
#   overwatch_state_dir        <project_name>  — resolve projects/<p>/overwatch/
#   overwatch_log_action       <project_name> <action> [detail]  — append to actions.log
#   overwatch_backup_file      <project_name> <src_path>  — timestamped backup into backups/
#   overwatch_acquire_firing_lock  <project_name>  — per-firing flock acquire
#   overwatch_release_firing_lock  <project_name>  — per-firing flock release
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
# overwatch_log_action <project_name> <action> [detail]
# Append a timestamped entry to the project's OVERWATCH action log:
#   $KANBAN_ROOT/projects/<project_name>/overwatch/actions.log
#
# Log format (one line per call):
#   YYYY-MM-DDTHH:MM:SS±HHMM  <action>  <detail>
#
# Args:
#   $1  project_name — e.g. "pgai-agent-kanban"
#   $2  action       — short label for the event (e.g. "halt-flag-detected")
#   $3  detail       — optional free-form detail string (may be empty)
#
# Creates the log file if it does not exist.
# Exits with status 1 if the state directory does not exist.
# ---------------------------------------------------------------------------
overwatch_log_action() {
    local project_name="$1"
    local action="$2"
    local detail="${3:-}"
    local state_dir log_file timestamp

    state_dir="$(overwatch_state_dir "$project_name")" || return 1
    log_file="${state_dir}/actions.log"

    if [[ ! -d "$state_dir" ]]; then
        echo "overwatch_lib.sh: overwatch_log_action: state dir does not exist: ${state_dir}" >&2
        return 1
    fi

    timestamp="$(date -Iseconds 2>/dev/null || date '+%Y-%m-%dT%H:%M:%S')"
    printf '%s\t%s\t%s\n' "$timestamp" "$action" "$detail" >> "$log_file"
}

# ---------------------------------------------------------------------------
# overwatch_backup_file <project_name> <src_path>
# Copy <src_path> into the backups/ subdirectory of the OVERWATCH state dir
# with a timestamp appended to the filename.
#
# Destination pattern:
#   $KANBAN_ROOT/projects/<project_name>/overwatch/backups/<basename>.<YYYYMMDD-HHMMSS>
#
# Args:
#   $1  project_name — e.g. "pgai-agent-kanban"
#   $2  src_path     — absolute path to the file to back up
#
# Returns 0 on success.
# Returns 1 if the source file does not exist, state dir is missing, or copy fails.
# ---------------------------------------------------------------------------
overwatch_backup_file() {
    local project_name="$1"
    local src_path="$2"
    local state_dir backup_dir basename timestamp dst_path

    if [[ -z "$src_path" ]]; then
        echo "overwatch_lib.sh: overwatch_backup_file: src_path argument is required" >&2
        return 1
    fi

    if [[ ! -f "$src_path" ]]; then
        echo "overwatch_lib.sh: overwatch_backup_file: source file does not exist: ${src_path}" >&2
        return 1
    fi

    state_dir="$(overwatch_state_dir "$project_name")" || return 1
    backup_dir="${state_dir}/backups"

    if [[ ! -d "$backup_dir" ]]; then
        echo "overwatch_lib.sh: overwatch_backup_file: backups dir does not exist: ${backup_dir}" >&2
        return 1
    fi

    basename="$(basename "$src_path")"
    timestamp="$(date '+%Y%m%d-%H%M%S')"
    dst_path="${backup_dir}/${basename}.${timestamp}"

    if ! cp "$src_path" "$dst_path"; then
        echo "overwatch_lib.sh: overwatch_backup_file: copy failed: ${src_path} -> ${dst_path}" >&2
        return 1
    fi

    echo "$dst_path"
    return 0
}

# ---------------------------------------------------------------------------
# overwatch_acquire_firing_lock <project_name>
# Attempt to acquire an exclusive advisory flock for the current OVERWATCH
# firing. Uses a kanban-root-scoped lock file so only one OVERWATCH process
# at a time operates on the same installation, regardless of project.
#
# The lock is held via an open file descriptor stored in OVERWATCH_FIRING_LOCK_FD.
# Call overwatch_release_firing_lock when done.
#
# Lock file path:
#   $KANBAN_ROOT/locks/overwatch-<project_name>.lock
#
# Args:
#   $1  project_name — e.g. "pgai-agent-kanban"
#
# Returns 0 if the lock was acquired.
# Returns 1 if another process already holds the lock (non-blocking).
# Returns 2 on setup error (KANBAN_ROOT unset, locks/ dir not writable).
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

    lock_dir="${KANBAN_ROOT}/locks"
    lock_file="${lock_dir}/overwatch-${project_name}.lock"

    # Ensure the locks directory exists (idempotent; install.sh creates it on
    # first run, but a missed install or manual test environment may lack it).
    mkdir -p "$lock_dir" 2>/dev/null || {
        echo "overwatch_lib.sh: overwatch_acquire_firing_lock: cannot create locks dir: ${lock_dir}" >&2
        return 2
    }

    # Open the lock file on a dynamically-allocated file descriptor.
    exec {OVERWATCH_FIRING_LOCK_FD}>"$lock_file" 2>/dev/null || {
        echo "overwatch_lib.sh: overwatch_acquire_firing_lock: cannot open lock file: ${lock_file}" >&2
        return 2
    }

    # Non-blocking exclusive flock.
    if ! flock -n "$OVERWATCH_FIRING_LOCK_FD" 2>/dev/null; then
        # Another process holds the lock — close the fd and signal contention.
        exec {OVERWATCH_FIRING_LOCK_FD}>&- 2>/dev/null || true
        unset OVERWATCH_FIRING_LOCK_FD
        return 1
    fi

    export OVERWATCH_FIRING_LOCK_FD
    export OVERWATCH_FIRING_LOCK_FILE="$lock_file"
    return 0
}

# ---------------------------------------------------------------------------
# overwatch_release_firing_lock
# Release the advisory flock acquired by overwatch_acquire_firing_lock.
#
# Closes OVERWATCH_FIRING_LOCK_FD. The lock file itself is left in place
# (advisory flock; the OS releases it when all fds are closed).
# Unsets OVERWATCH_FIRING_LOCK_FD and OVERWATCH_FIRING_LOCK_FILE.
#
# Safe to call even if the lock was never acquired (no-op if FD is unset).
# ---------------------------------------------------------------------------
overwatch_release_firing_lock() {
    if [[ -n "${OVERWATCH_FIRING_LOCK_FD:-}" ]]; then
        exec {OVERWATCH_FIRING_LOCK_FD}>&- 2>/dev/null || true
        unset OVERWATCH_FIRING_LOCK_FD
    fi
    unset OVERWATCH_FIRING_LOCK_FILE
    return 0
}
