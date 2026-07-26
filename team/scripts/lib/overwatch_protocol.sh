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
#     wake/harness.sh) hold while a wake is processing that project. Callers
#     that already source project_paths.sh (e.g. overwatch-sweep.sh) get this
#     for free; overwatch_halt_first_fix defensively sources it itself
#     (relative to this file) if it is not already loaded, so this file also
#     works when sourced standalone.
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
#   transient_retry_limit                                          — resolve the ONE retry ceiling
#   transient_retry_field   <status_file> <field> [default]         — read the durable retry record
#   transient_retry_write   <status_file> <reason_code> <retry_count>
#                           <retry_limit> <provider> <last_error>  — write the durable retry record
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
# mutating, restore after — while holding the SAME per-project repo lock a
# wake dispatcher holds, for the duration of the mutation only.
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
#  2. Resolve the per-project repo lock path via pp_repo_lock_path
#     ($OVERWATCH_PROJECT) — the identical helper wake dispatch uses, so
#     OVERWATCH always inspects the lock a wake actually holds for this
#     project rather than a name derived (or hardcoded) independently.
#  3. Guard + hold: open the resolved lock file and attempt a non-blocking
#     exclusive flock. If contended (another agent is running), log to
#     stderr and return 4 without taking any action. If acquired, KEEP the
#     lock held for the remainder of this function — through the HALT
#     touch, <fn>, and the HALT restore — so no wake can start processing
#     this project between our contention check and our first state write
#     (closes the TOCTOU the previous check-then-release sequence had).
#  4. Record whether $KANBAN_ROOT/HALT already exists BEFORE we touch it.
#     - If it already exists: do NOT touch it, and do NOT remove it later.
#       An operator-placed (or otherwise pre-existing) HALT is inviolate —
#       OVERWATCH only ever removes a HALT it created itself.
#     - If it does not exist: touch it to stop chain agents from starting
#       new tasks, and remember to remove it during cleanup.
#     Register a trap to run cleanup on EXIT (removes HALT only if this
#     invocation created it, and always releases the repo lock fd), so
#     cleanup always happens even if <fn> fails or the shell exits
#     abnormally.
#  5. Run <fn> (called as a shell function — must be defined in the caller's
#     environment). Capture its exit code.
#  6. Run cleanup explicitly (the trap also handles this if step 5 exits
#     unexpectedly): remove HALT only if this invocation created it in step
#     4, then release the repo lock fd. Unregister the trap.
#  7. Return the exit code captured in step 5.
#
# Sentinel exit codes:
#   3 — HALT_OVERWATCH guard tripped (step 1 above)
#   4 — per-repo flock contended (step 3 above)
#
# Args:
#   $1  fn  — name of a bash function to invoke
#
# Returns:
#   <fn>'s exit code on normal execution
#   3 if HALT_OVERWATCH guard tripped
#   4 if per-repo flock is contended
#   1 on setup error (KANBAN_ROOT/OVERWATCH_PROJECT unset, fn not callable,
#     repo lock path unresolvable)
#
# Emits structured debug lines to stderr throughout.
# ---------------------------------------------------------------------------
overwatch_halt_first_fix() {
    local fn="$1"
    local halt_flag halt_overwatch_flag project_lock_file project_lock_fd fn_exit_code
    local halt_pre_existing="false"

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

    # Resolve the SAME per-project repo lock the wake dispatchers hold.
    project_lock_file="$(pp_repo_lock_path "$OVERWATCH_PROJECT")" || {
        echo "overwatch_protocol.sh: overwatch_halt_first_fix: cannot resolve repo lock path for project '${OVERWATCH_PROJECT}'" >&2
        return 1
    }

    # Guard 2 + mutation lock: acquire the project's repo flock and KEEP it
    # held for the remainder of this function (through the HALT touch, <fn>,
    # and the HALT restore). Holding across the mutation — rather than
    # checking contention and releasing before acting — closes the TOCTOU
    # window where a wake could start processing this project between our
    # check and our first state write.
    exec {project_lock_fd}>"$project_lock_file" 2>/dev/null || {
        echo "overwatch_protocol: overwatch_halt_first_fix: cannot open repo lock file; backing off (sentinel 4)" >&2
        return 4
    }
    if ! flock -n "$project_lock_fd" 2>/dev/null; then
        exec {project_lock_fd}>&- 2>/dev/null || true
        echo "overwatch_protocol: overwatch_halt_first_fix: per-repo flock contended; declining to act (sentinel 4)" >&2
        return 4
    fi

    # Step 4: Record whether HALT already existed BEFORE we touch it. An
    # operator-placed (or otherwise pre-existing) HALT is inviolate — we
    # only ever remove a HALT this invocation created itself.
    if [[ -f "$halt_flag" ]]; then
        halt_pre_existing="true"
        echo "overwatch_protocol: overwatch_halt_first_fix: HALT already present at ${halt_flag}; will not touch or remove it" >&2
    else
        echo "overwatch_protocol: overwatch_halt_first_fix: touching HALT at ${halt_flag}" >&2
        touch "$halt_flag" || {
            echo "overwatch_protocol.sh: overwatch_halt_first_fix: cannot touch HALT file: ${halt_flag}" >&2
            exec {project_lock_fd}>&- 2>/dev/null || true
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
        exec {project_lock_fd}>&- 2>/dev/null || true
    }
    trap '_overwatch_hff_cleanup' EXIT

    # Step 5: Run the wrapped function
    echo "overwatch_protocol: overwatch_halt_first_fix: invoking ${fn}" >&2
    fn_exit_code=0
    "$fn" || fn_exit_code=$?
    echo "overwatch_protocol: overwatch_halt_first_fix: ${fn} returned ${fn_exit_code}" >&2

    # Step 6: Clean up explicitly (trap also covers unexpected exits).
    _overwatch_hff_cleanup
    trap - EXIT

    return "$fn_exit_code"
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
# Echoes the resolved value to stdout. Always succeeds (returns 0).
# ---------------------------------------------------------------------------
transient_retry_field() {
    local status_file="$1"
    local field_name="$2"
    local default_value="${3:-}"

    python3 - "${status_file}" "${field_name}" "${default_value}" <<'PY'
import pathlib, re, sys

status_file, field_name, default_value = sys.argv[1], sys.argv[2], sys.argv[3]
path = pathlib.Path(status_file)
text = path.read_text(encoding="utf-8") if path.is_file() else ""

value = default_value
m = re.search(r'^## Transient Retry\s*\n(.*?)(\n+##|\Z)', text, flags=re.S | re.M)
if m:
    body = m.group(1).strip()
    if body and body.strip().lower() != "none":
        fm = re.search(rf'^{re.escape(field_name)}:\s*(.*)$', body, flags=re.M)
        if fm:
            value = fm.group(1).strip()

print(value)
PY
}

# ---------------------------------------------------------------------------
# transient_retry_write <status_file> <reason_code> <retry_count>
#                        <retry_limit> <provider> <last_error>
# Write (or update) the durable ## Transient Retry record in <status_file>.
# This is the ONE writer for the record — only wake_common.sh's
# handle_working_after_exit calls it, so there is a single owning controller.
# If the section is absent, it is appended at the end of the file (mirrors
# the ensure-or-append pattern used elsewhere in the wake/OVERWATCH substrate,
# e.g. check-transient-api-error.sh's append_section helper). If present,
# its body is replaced in place — all other sections (including
# ## Blocked Reason, which stays human narrative only) are untouched.
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

    python3 - "${status_file}" "${reason_code}" "${retry_count}" "${retry_limit}" "${provider}" "${last_error}" <<'PY'
import pathlib, re, sys

status_file, reason_code, retry_count, retry_limit, provider, last_error = sys.argv[1:7]
path = pathlib.Path(status_file)

try:
    text = path.read_text(encoding="utf-8")
except OSError as e:
    raise SystemExit(f"transient_retry_write: cannot read {status_file}: {e}")

new_body = "\n".join([
    f"reason_code: {reason_code}",
    f"retry_count: {retry_count}",
    f"retry_limit: {retry_limit}",
    f"provider: {provider}",
    f"last_error: {last_error}",
])

pattern = r'(^## Transient Retry\s*\n)(.*?)(\n+##|\Z)'
new_text, n = re.subn(
    pattern,
    lambda m: m.group(1) + new_body + "\n" + (m.group(3) if m.group(3) else ''),
    text,
    count=1,
    flags=re.S | re.M,
)
if n == 0:
    if not text.endswith("\n"):
        text += "\n"
    new_text = text + f"\n## Transient Retry\n{new_body}\n"

try:
    path.write_text(new_text, encoding="utf-8")
except OSError as e:
    raise SystemExit(f"transient_retry_write: cannot write {status_file}: {e}")
PY
}
