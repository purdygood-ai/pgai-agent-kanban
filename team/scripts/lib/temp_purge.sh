#!/usr/bin/env bash
# team/scripts/lib/temp_purge.sh
# Shared allowlist + live-state guard for framework temp-directory purge.
#
# This library is sourced by cleanup.sh to provide a consistent, safe sweep
# of the framework temp root (PGAI_AGENT_KANBAN_TEMP_DIR).  It is the single
# source of truth for:
#   1. The allowlist of kanban-owned transient top-level basenames.
#   2. The live-state guard that checks wake locks before any purge.
#   3. The per-entry decision logic (foreign skip, live-task worktree skip, deletion).
#
# Consumers must set these variables BEFORE sourcing this file, or rely on the
# cleanup.sh caller to set them:
#   DRY_RUN           — "true" / "false"
#   TEAM_ROOT         — absolute path to the kanban root (used for lock paths)
#   PGAI_TASKS_DIR    — (optional) path to the project tasks directory for
#                       live-task worktree skip; defaults to "" (skip disabled)
#
# Public functions:
#   temp_purge_check_wake_locks [lock_dir]
#       Test whether any wake lock in lock_dir (default: $TEAM_ROOT/locks) is
#       currently held.  Prints a warning to stdout and returns 1 when a held
#       lock is found; returns 0 when all locks are free (or lock_dir absent).
#
#   temp_purge_sweep_dir <dir> <label> [tasks_dir]
#       Sweep the contents of <dir>, applying the allowlist + live-state guard
#       to each top-level entry.  Foreign entries are skipped with a logged
#       notice.  Within the worktrees/ allowlist entry, subdirectories whose
#       task (looked up in [tasks_dir]) is WORKING, BLOCKED or TESTING are
#       also skipped.  <label> is used in log lines.
#       Increments TEMP_ITEMS_PURGED for every item actually removed (or that
#       would be removed in dry-run mode).
#
#   temp_purge_scan_root <dir>
#       "each" when some process runs with its working directory inside
#       <dir>, else "none": one scan per sweep, so temp_purge_remove scans
#       an entry only when something is rooted under the swept directory.
#
#   temp_purge_remove <path>
#       Remove one entry the sweep chose. A directory is removed only after
#       every process whose working directory is inside it has been stopped
#       (SIGTERM, then SIGKILL after KILL_GRACE_SECONDS, default 30), each one
#       logged with its pid and command. A process attached to a terminal
#       (an operator's shell) is never signalled: the directory is kept and
#       the log says why. In dry-run mode it logs what it would stop and
#       removes nothing. Returns 0 when the entry is removed (or would be),
#       1 when it is kept.
#
# Dependencies: md_field.sh (read_md_field, the task ## State read), loaded
# from beside this file unless the caller already defines read_md_field;
# pp_run_ops (pp_run_ops.sh) for the process scan
# (pgai_agent_kanban.lib.process_reap), loaded the same way.
#
# Include guard: safe to source multiple times.
[[ -n "${_PGAI_TEMP_PURGE_SH_LOADED:-}" ]] && return 0
_PGAI_TEMP_PURGE_SH_LOADED=1

# shellcheck source=md_field.sh
declare -F read_md_field >/dev/null || source "$(dirname "${BASH_SOURCE[0]}")/md_field.sh"
# shellcheck source=pp_run_ops.sh
declare -F pp_run_ops >/dev/null || source "$(dirname "${BASH_SOURCE[0]}")/pp_run_ops.sh"

# ---------------------------------------------------------------------------
# Allowlist of kanban-owned transient top-level basenames.
#
# Every basename listed here is considered "kanban-owned" and eligible for
# purge (subject to the live-task guard for worktrees/).  Any basename NOT
# in this list is "foreign" and is never deleted.
#
# Maintenance: add a new basename here whenever a new subsystem begins writing
# a top-level directory under PGAI_AGENT_KANBAN_TEMP_DIR.  Keep the list
# alphabetically sorted for readability.
#
# Pattern rules:
#   - Exact match strings match that exact basename.
#   - Glob patterns (containing * ? [) are matched via bash's == operator
#     against the basename using the test "[[ name == pattern ]]" syntax.
#   - The "tmp.*" pattern covers per-project temp dirs written by pp_load_config
#     (e.g., tmp.pgai-agent-kanban, tmp.proj-a).
#   - The "crontab_seam.*" pattern covers temp seam dirs used by crontab tests.
#   - The "e2e.*" pattern covers e2e test scratch directories.
#   - The "pgai_tmp.*" pattern covers bare pgai_mktemp / pgai_mktemp_d output.
#   - The "disc_*" pattern covers discovery temp files.
#   - The "collect_traces*" pattern covers reasoning-trace collection temp.
#   - The "export-project" directory written by export-project.sh.
#
# Provider session directory fence:
#   Provider CLIs (claude, codex, gemini) write session state into the temp
#   root via the provider TMPDIR bridge.  These directories follow patterns
#   such as claude-*, codex-*, gemini-*.  They are NOT listed in this
#   allowlist, so temp_purge_basename_allowed() returns false for them and
#   temp_purge_sweep_dir() skips them as "foreign entries".
#
#   This exclusion is STRUCTURAL — absence from the allowlist is the fence.
#   Do NOT add "claude-*", "codex-*", "gemini-*", or any provider session
#   pattern to this list.  The safety invariant is:
#     "if it is not in the allowlist, it is foreign and is never deleted."
# ---------------------------------------------------------------------------
_TEMP_PURGE_ALLOWLIST=(
    "crontab_seam"
    "crontab_seam.*"
    "dashboard"
    "disc_*"
    "e2e"
    "e2e.*"
    "e2e-artifacts"
    "export-project"
    "collect_traces*"
    "pgai_tmp.*"
    "pollution"
    "pytest"
    "reset-archive"
    "scratch"
    "tests"
    "test_tmp"
    "tmp.*"
    "token_capture"
    "wakeup"
    "worktrees"
)

# ---------------------------------------------------------------------------
# temp_purge_basename_allowed <basename>
# Return 0 (true) if <basename> is in the allowlist; 1 (false) otherwise.
# Public so callers that have additional pre-filters (e.g. the per-project
# exclusion in --temp-only mode) can re-use the check inline.
# ---------------------------------------------------------------------------
temp_purge_basename_allowed() {
    local name="$1"
    local pattern
    for pattern in "${_TEMP_PURGE_ALLOWLIST[@]}"; do
        # Exact match first (fast path for most entries).
        if [[ "$name" == "$pattern" ]]; then
            return 0
        fi
        # Glob pattern match (only when pattern contains glob metacharacters).
        if [[ "$pattern" == *[\*\?\[]* ]]; then
            # shellcheck disable=SC2254 — intended glob expansion against name
            if [[ "$name" == $pattern ]]; then
                return 0
            fi
        fi
    done
    return 1
}

# ---------------------------------------------------------------------------
# _temp_purge_read_task_state <task_status_file>
# Echo the ## State field value from a task status.md -- the first non-blank
# line of the section, trimmed -- or "" when the file, the heading or a value
# is missing. Always returns 0. The section is read by read_md_field
# (md_field.sh) with --free-spacing --end-at-subheading: "##State" and
# "## State" both match, in any letter case, and the next "##" line ends the
# section.
# ---------------------------------------------------------------------------
_temp_purge_read_task_state() {
    local status_file="$1"
    [[ -f "$status_file" ]] || { echo ""; return 0; }
    local val
    val="$(read_md_field --free-spacing --end-at-subheading "$status_file" State)" || val=""
    val="${val%%$'\n'*}"
    val="${val%"${val##*[![:space:]]}"}"
    echo "$val"
}

# ---------------------------------------------------------------------------
# _temp_purge_find_task_status <task_id> [tasks_dir]
# Try to locate the task's status.md.  Returns the path on stdout, or "" if
# not found.  Searches:
#   1. <tasks_dir>/<task_id>/status.md   (when tasks_dir is provided and non-empty)
# ---------------------------------------------------------------------------
_temp_purge_find_task_status() {
    local task_id="$1"
    local tasks_dir="${2:-}"
    if [[ -n "$tasks_dir" && -f "${tasks_dir}/${task_id}/status.md" ]]; then
        echo "${tasks_dir}/${task_id}/status.md"
        return 0
    fi
    echo ""
}

# ---------------------------------------------------------------------------
# temp_purge_check_wake_locks [lock_dir]
#
# Test whether any wake lock file in <lock_dir> (default: $TEAM_ROOT/locks)
# is currently held by an active flock.
#
# Returns:
#   0  — no held locks found; purge may proceed
#   1  — at least one held lock found; purge should abort
#
# When a held lock is found, prints a warning line of the form:
#   "temp purge: wake lock held: <path> — aborting temp purge"
#
# When lock_dir does not exist or contains no *.lock files, returns 0 silently.
# ---------------------------------------------------------------------------
temp_purge_check_wake_locks() {
    local lock_dir="${1:-${TEAM_ROOT:-}/locks}"

    # No lock dir — nothing to check.
    if [[ ! -d "$lock_dir" ]]; then
        return 0
    fi

    local lock_file
    for lock_file in "${lock_dir}"/*.lock; do
        # Glob produced no matches.
        [[ -e "$lock_file" ]] || continue

        # Try to acquire the lock non-blocking.  If flock -n succeeds, the
        # file is not currently held — release immediately and continue.
        # If flock -n fails, the file IS held by another process.
        if ! flock -n "$lock_file" true 2>/dev/null; then
            echo "temp purge: wake lock held: $lock_file — aborting temp purge"
            return 1
        fi
    done

    return 0
}

# ---------------------------------------------------------------------------
# temp_purge_remove <path>
#
# Remove <path> (rm -rf), first stopping every process whose working
# directory is <path> or inside it, so nothing keeps running from a deleted
# directory. Only processes rooted there are scanned
# (pgai_agent_kanban.lib.process_reap --cwd-under, which never signals this
# process or its ancestors); one rooted there that has a controlling
# terminal -- an operator's shell -- is never signalled, and the entry is
# kept with a log line naming it. Each stopped process is logged with its
# pid, outcome and command before the removal line.
#
# Uses the caller's DRY_RUN and log. Returns 0 when <path> is removed (or,
# in dry-run mode, would be), 1 when it is kept.
# ---------------------------------------------------------------------------
temp_purge_remove() {
    local _path="$1"
    local _grace="${KILL_GRACE_SECONDS:-30}"
    [[ "$_grace" =~ ^[0-9]+$ ]] || _grace=30
    if [[ -d "$_path" && ! -L "$_path" && "${_TEMP_PURGE_SCAN:-each}" != "none" ]]; then
        local -a _reap_args=(--cwd-under "$_path" --grace "$_grace" --skip-terminals)
        [[ "$DRY_RUN" == "true" ]] && _reap_args+=(--dry-run)
        local _reap_out _reap_rc=0 _reap_line
        _reap_out="$(pp_run_ops pgai_agent_kanban.lib.process_reap "${_reap_args[@]}" 2>&1)" || _reap_rc=$?
        while IFS= read -r _reap_line; do
            [[ -n "$_reap_line" ]] || continue
            if [[ "$_reap_line" != pid=* ]]; then
                log "temp purge: process scan of $_path: $_reap_line"
            elif [[ "$DRY_RUN" == "true" ]]; then
                log "[DRY-RUN] temp purge: would stop a process running in $_path: $_reap_line"
            else
                log "temp purge: stopped a process running in $_path before removing it: $_reap_line"
            fi
        done <<< "$_reap_out"
        if [[ $_reap_rc -eq 3 ]]; then
            log "temp purge: keeping $_path: a process running in it was left running (outcome=kept: attached to a terminal; outcome=survived: in kernel teardown); remove it once that process has exited"
            return 1
        elif [[ $_reap_rc -ne 0 ]]; then
            log "temp purge: keeping $_path: the scan for processes running in it failed (exit ${_reap_rc})"
            return 1
        fi
    fi
    if [[ "$DRY_RUN" == "false" ]]; then
        rm -rf "$_path"
    fi
    return 0
}

# ---------------------------------------------------------------------------
# temp_purge_scan_root <dir>
#
# One process scan for a whole sweep: echoes "each" when some process has
# its working directory inside <dir> (so temp_purge_remove must scan each
# entry it removes), "none" when no process does. A sweep keeps the answer
# in a local _TEMP_PURGE_SCAN, which temp_purge_remove reads; unset, every
# entry is scanned. A scan that fails answers "each".
# ---------------------------------------------------------------------------
temp_purge_scan_root() {
    local _dir="$1" _out _rc=0
    [[ -d "$_dir" ]] || { echo "none"; return 0; }
    _out="$(pp_run_ops pgai_agent_kanban.lib.process_reap --cwd-under "$_dir" --dry-run 2>/dev/null)" || _rc=$?
    if [[ $_rc -eq 0 && -z "$_out" ]]; then
        echo "none"
    else
        echo "each"
    fi
}

# ---------------------------------------------------------------------------
# temp_purge_sweep_worktrees <worktrees_dir> <label> [tasks_dir]
#
# Sweep the contents of a worktrees/ directory, applying the live-task
# guard.  Each immediate child of <worktrees_dir> is treated as a task
# worktree named by its task ID.  Entries whose task status is WORKING,
# BLOCKED (a retained worktree an operator or a retry may still need) or
# TESTING are skipped with a logged notice; all others -- a terminal task's
# worktree, or one no task in <tasks_dir> names -- are purged (or dry-run
# logged).  <tasks_dir> must be the tasks dir of the project that owns
# <worktrees_dir>: a worktree judged against another project's tasks finds
# no status and is purged.
#
# This helper is extracted so both temp_purge_sweep_dir and the install-wide
# loop in cleanup.sh can call it without code duplication.
#
# Arguments:
#   $1 — worktrees_dir : the worktrees base directory to sweep
#   $2 — label         : human-readable label for log messages
#   $3 — tasks_dir     : (optional) path to tasks directory for the live-task check
#
# Uses caller's DRY_RUN, log_action, log, TEMP_ITEMS_PURGED.
# ---------------------------------------------------------------------------
temp_purge_sweep_worktrees() {
    local _wt_dir="$1"
    local _label="$2"
    local _tasks_dir="${3:-${PGAI_TASKS_DIR:-}}"

    if [[ ! -d "$_wt_dir" ]]; then
        log "temp purge: worktrees directory does not exist, skipping (${_label}): $_wt_dir"
        return 0
    fi
    local _TEMP_PURGE_SCAN
    _TEMP_PURGE_SCAN="$(temp_purge_scan_root "$_wt_dir")"

    for _wt_entry in "${_wt_dir}"/*; do
        [[ -e "$_wt_entry" ]] || continue

        local _wt_basename
        _wt_basename="$(basename "$_wt_entry")"

        # Derive task_id = basename (worktree dirs are named after task IDs).
        local _task_status_file
        _task_status_file="$(_temp_purge_find_task_status "$_wt_basename" "$_tasks_dir")"

        local _task_state=""
        if [[ -n "$_task_status_file" ]]; then
            _task_state="$(_temp_purge_read_task_state "$_task_status_file")"
        fi

        case "$_task_state" in
            WORKING|BLOCKED|TESTING)
                log "temp purge: skipping ${_task_state}-task worktree: $_wt_entry"
                continue
                ;;
        esac

        log_action "temp purge (${_label}): $_wt_entry"
        if temp_purge_remove "$_wt_entry"; then
            TEMP_ITEMS_PURGED=$(( TEMP_ITEMS_PURGED + 1 ))
        fi
    done
    unset _wt_entry _wt_basename _task_status_file _task_state
}

# ---------------------------------------------------------------------------
# temp_purge_sweep_dir <dir> <label> [tasks_dir]
#
# Sweep the top-level contents of <dir>, applying the allowlist + live-state
# guard to each entry.  Updates the caller's TEMP_ITEMS_PURGED counter.
#
# For each top-level entry under <dir>:
#   1. If the basename is NOT in the allowlist:
#        log "temp purge: skipping foreign entry <path>" and skip.
#        This includes provider session dirs (claude-*, codex-*, gemini-*)
#        which are explicitly absent from the allowlist since they are owned
#        by the provider CLI process, not the kanban.
#   2. If the basename IS "worktrees" and the entry is a directory:
#        Delegate to temp_purge_sweep_worktrees for the per-task live-state guard.
#   3. Otherwise (allowlisted, not worktrees):
#        Apply the normal purge decision (dry-run or real rm).
#
# Arguments:
#   $1 — dir        : directory whose contents to sweep
#   $2 — label      : human-readable label for log messages
#   $3 — tasks_dir  : (optional) path to tasks directory for the live-task check
#
# The function uses the caller's DRY_RUN variable (must be "true" or "false").
# It uses the caller's log and log_action functions (defined in cleanup.sh).
# It updates the caller's TEMP_ITEMS_PURGED counter (must be declared in caller).
# ---------------------------------------------------------------------------
temp_purge_sweep_dir() {
    local _dir="$1"
    local _label="$2"
    local _tasks_dir="${3:-${PGAI_TASKS_DIR:-}}"

    if [[ ! -d "$_dir" ]]; then
        log "temp purge: directory does not exist, skipping (${_label}): $_dir"
        return 0
    fi
    local _TEMP_PURGE_SCAN
    _TEMP_PURGE_SCAN="$(temp_purge_scan_root "$_dir")"

    for _item in "${_dir}"/*; do
        [[ -e "$_item" ]] || continue

        local _basename
        _basename="$(basename "$_item")"

        # --- Allowlist check ---
        if ! temp_purge_basename_allowed "$_basename"; then
            log "temp purge: skipping foreign entry $_item"
            continue
        fi

        # --- Special handling for worktrees/ directory ---
        if [[ "$_basename" == "worktrees" && -d "$_item" ]]; then
            temp_purge_sweep_worktrees "$_item" "${_label}/worktrees" "$_tasks_dir"
            continue
        fi

        # --- Normal allowlisted entry ---
        log_action "temp purge (${_label}): $_item"
        if temp_purge_remove "$_item"; then
            TEMP_ITEMS_PURGED=$(( TEMP_ITEMS_PURGED + 1 ))
        fi
    done
    unset _item _basename
}
