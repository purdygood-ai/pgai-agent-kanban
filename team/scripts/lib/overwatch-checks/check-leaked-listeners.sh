#!/usr/bin/env bash
# team/scripts/lib/overwatch-checks/check-leaked-listeners.sh
#
# OVERWATCH Tier-1 detection module: detect listener processes whose cwd is
# under the framework temp root (PGAI_AGENT_KANBAN_TEMP_DIR) and stop the
# ones that are leaked.
#
# Scan (host-wide, once per sweep): overwatch_leaked_listener_scan walks
# /proc/*/cwd once and records every process whose cwd is under the temp root
# and that holds a TCP socket in LISTEN state (/proc/net/tcp, tcp6), with its
# age in seconds and the project and task it belongs to. overwatch-sweep.sh
# runs the scan before its project loop and passes the result to every
# project's check through OVERWATCH_LEAKED_LISTENER_SCAN; a standalone run
# scans for itself. Processes in the scanning shell's own process group (the
# sweep and its children) are never recorded.
#
# Attribution: a cwd under <temp root>/projects/<name>/ belongs to project
# <name> (when $KANBAN_ROOT/projects/<name> exists), and to the task whose
# worktree holds it (<temp root>/projects/<name>/worktrees/<task-id>). A cwd
# elsewhere under the temp root belongs to the project whose tasks/ holds a
# task ID named in that path (a pytest scratch directory carries one). A
# process that belongs to no registered project is reported and left alone.
# Each project's check acts only on its own processes and logs under itself.
#
# Leak candidate (SIGTERM, logged via overwatch_log_action): a listener of
# this project whose task is not WORKING, and whose age is at least
# [overwatch] leaked_listener_min_age_seconds (kanban.cfg; default 3600). A
# listener inside a WORKING task's worktree or scratch, or younger than the
# threshold, is skipped with a stderr line. No backup is needed for a
# process kill (no file is modified).
#
# Module contract:
#   - Sourceable without side effects.
#   - Exports: overwatch_check_leaked_listeners, overwatch_leaked_listener_scan
#   - Zero arguments. All context from environment variables.
#   - Returns 0 on success (regardless of whether any anomaly was found).
#   - Returns 1 on internal error (missing dependencies, unreadable state).
#
# Required environment variables (set by OVERWATCH driver or sweep runner):
#   KANBAN_ROOT       — absolute path to the kanban root
#   OVERWATCH_PROJECT — project name (e.g. "pgai-agent-kanban")
#
# Optional environment variables:
#   PGAI_AGENT_KANBAN_TEMP_DIR — temp root override
#   OVERWATCH_LEAKED_LISTENER_SCAN — a scan file this sweep already wrote
#     (overwatch_leaked_listener_scan's format); used instead of scanning.
#
# Usage (standalone):
#   bash check-leaked-listeners.sh [--dry-run]
#
# Exit codes:
#   0 — completed (anomalies found and handled, or none found)
#   1 — internal error
#
# --dry-run: scans and logs findings but does NOT kill any processes.

# ---------------------------------------------------------------------------
# _cll_resolve_env
# Resolve KANBAN_ROOT, OVERWATCH_PROJECT, and temp root from the environment.
# Sets _CLL_TEMP_ROOT in the calling scope.
# Returns 0 on success, 1 on failure.
# ---------------------------------------------------------------------------
_cll_resolve_env() {
    if [[ -z "${KANBAN_ROOT:-}" ]]; then
        KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-${HOME}/pgai_agent_kanban}"
    fi
    if [[ ! -d "${KANBAN_ROOT}" ]]; then
        echo "check-leaked-listeners: KANBAN_ROOT does not exist: ${KANBAN_ROOT}" >&2
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
            echo "check-leaked-listeners: ERROR: OVERWATCH_PROJECT not set and multiple projects registered." >&2
            return 1
        fi
        OVERWATCH_PROJECT="$(echo "${_all_projects}" | grep '[^[:space:]]' | head -n1)"
        if [[ -z "${OVERWATCH_PROJECT:-}" ]]; then
            echo "check-leaked-listeners: ERROR: no project specified and none resolvable from projects.cfg" >&2
            return 1
        fi
    fi

    _cll_resolve_temp_root
}

# ---------------------------------------------------------------------------
# _cll_resolve_temp_root
# Set _CLL_TEMP_ROOT to the framework temp root (pgai_temp_dir in temp.sh,
# sourced here when the caller has not). Returns 1 when temp.sh is missing.
# ---------------------------------------------------------------------------
_cll_resolve_temp_root() {
    # Source temp.sh if pgai_temp_dir is not already available (e.g. when this
    # module is invoked standalone rather than through the sweep runner).
    if ! declare -f pgai_temp_dir >/dev/null 2>&1; then
        local _cll_lib_dir
        _cll_lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)" || {
            echo "check-leaked-listeners: cannot resolve lib dir for temp.sh sourcing" >&2
            return 1
        }
        local _cll_temp_sh="${_cll_lib_dir}/../temp.sh"
        if [[ ! -f "${_cll_temp_sh}" ]]; then
            echo "check-leaked-listeners: temp.sh not found at ${_cll_temp_sh}" >&2
            return 1
        fi
        # shellcheck source=/dev/null
        source "${_cll_temp_sh}"
    fi
    _CLL_TEMP_ROOT="$(pgai_temp_dir)"

    return 0
}

# ---------------------------------------------------------------------------
# _cll_load_protocol
# Ensure overwatch_log_action is available. Sources overwatch_protocol.sh
# relative to this script's location if needed.
# Returns 0 on success, 1 on failure.
# ---------------------------------------------------------------------------
_cll_load_protocol() {
    if declare -f overwatch_log_action >/dev/null 2>&1; then
        return 0
    fi
    local lib_dir
    lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)" || {
        echo "check-leaked-listeners: cannot resolve lib dir" >&2
        return 1
    }
    local protocol_sh="${lib_dir}/../overwatch_protocol.sh"
    if [[ ! -f "${protocol_sh}" ]]; then
        echo "check-leaked-listeners: overwatch_protocol.sh not found at ${protocol_sh}" >&2
        return 1
    fi
    # shellcheck source=/dev/null
    source "${protocol_sh}"
}

# ---------------------------------------------------------------------------
# _cll_process_has_listening_socket <pid>
# Returns 0 if the process has any socket in TCP LISTEN state.
# Returns 1 otherwise.
# Uses /proc/net/tcp and /proc/net/tcp6 for detection (no external tools needed).
# ---------------------------------------------------------------------------
_cll_process_has_listening_socket() {
    local pid="$1"

    if [[ ! -d "/proc/${pid}" ]]; then
        return 1
    fi

    # Gather inode numbers for sockets owned by this process.
    local socket_inodes=()
    local fd_path fd_target
    while IFS= read -r fd_path; do
        [[ -L "${fd_path}" ]] || continue
        fd_target="$(readlink "${fd_path}" 2>/dev/null || true)"
        # Socket fds look like "socket:[INODE]"
        if [[ "${fd_target}" =~ ^socket:\[([0-9]+)\]$ ]]; then
            socket_inodes+=("${BASH_REMATCH[1]}")
        fi
    done < <(find "/proc/${pid}/fd" -maxdepth 1 -type l 2>/dev/null)

    if (( ${#socket_inodes[@]} == 0 )); then
        return 1
    fi

    # Check /proc/net/tcp and /proc/net/tcp6 for LISTEN state (state=0A hex).
    # Format: sl local_address rem_address st tx_queue rx_queue tr tm->when retrnsmt uid timeout inode
    # We look for lines where state field (4th field) is "0A" (TCP_LISTEN).
    local inode
    for inode in "${socket_inodes[@]}"; do
        if grep -qE "^[[:space:]]*[0-9]+:[[:space:]]+[0-9A-F]+:[0-9A-F]+[[:space:]]+[0-9A-F]+:[0-9A-F]+[[:space:]]+0A[[:space:]].*[[:space:]]${inode}[[:space:]]" \
            /proc/net/tcp /proc/net/tcp6 2>/dev/null; then
            return 0
        fi
    done

    return 1
}

# Default for [overwatch] leaked_listener_min_age_seconds.
_CLL_DEFAULT_MIN_AGE_SECONDS=3600

# ---------------------------------------------------------------------------
# _cll_min_age_seconds
# Echo the minimum listener age (seconds) before it is a leak candidate:
# kanban.cfg [overwatch] leaked_listener_min_age_seconds, or the default
# when absent or blank. A value that is not a whole number is reported on
# stderr and the default is used.
# ---------------------------------------------------------------------------
_cll_min_age_seconds() {
    if ! declare -F read_ini >/dev/null 2>&1; then
        local _cll_ini
        _cll_ini="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." 2>/dev/null && pwd)/ini_parser.sh"
        # shellcheck source=../ini_parser.sh
        [[ -f "${_cll_ini}" ]] && source "${_cll_ini}"
    fi
    local raw=""
    if declare -F read_ini >/dev/null 2>&1; then
        raw="$(read_ini "${KANBAN_ROOT}/kanban.cfg" overwatch leaked_listener_min_age_seconds "" 2>/dev/null)" || raw=""
    fi
    raw="${raw//\"/}"
    raw="${raw//[[:space:]]/}"
    if [[ -z "${raw}" ]]; then
        printf '%s\n' "${_CLL_DEFAULT_MIN_AGE_SECONDS}"
    elif [[ "${raw}" =~ ^[0-9]+$ ]]; then
        printf '%s\n' "$(( 10#${raw} ))"
    else
        echo "check-leaked-listeners: [overwatch] leaked_listener_min_age_seconds = '${raw}' in ${KANBAN_ROOT}/kanban.cfg is not a whole number of seconds; using ${_CLL_DEFAULT_MIN_AGE_SECONDS}" >&2
        printf '%s\n' "${_CLL_DEFAULT_MIN_AGE_SECONDS}"
    fi
}

# ---------------------------------------------------------------------------
# _cll_process_age_seconds <pid>
# Echo the seconds since <pid> started, or nothing when it cannot be read.
# ---------------------------------------------------------------------------
_cll_process_age_seconds() {
    local pid="$1" age
    age="$(ps -o etimes= -p "${pid}" 2>/dev/null)" || return 0
    age="${age//[[:space:]]/}"
    [[ "${age}" =~ ^[0-9]+$ ]] && printf '%s\n' "${age}"
}

# ---------------------------------------------------------------------------
# _cll_load_task_ids
# Make task_ids.sh's helpers available (the shared task-ID shape).
# ---------------------------------------------------------------------------
_cll_load_task_ids() {
    declare -F kanban_task_ids_in_files >/dev/null 2>&1 && return 0
    local ids_lib
    ids_lib="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." 2>/dev/null && pwd)/task_ids.sh"
    [[ -f "${ids_lib}" ]] || return 1
    # shellcheck source=../task_ids.sh
    source "${ids_lib}"
}

# ---------------------------------------------------------------------------
# _cll_attribute <temp_root> <cwd>
# Echo "<project>\t<task-id>" for the project (and task, "" when none) a
# listener's cwd belongs to -- see "Attribution" in the header. Returns 1,
# printing nothing, when it belongs to no registered project.
# ---------------------------------------------------------------------------
_cll_attribute() {
    local temp_root="$1" cwd="$2"
    local rest name task="" id proj_dir
    if [[ "${cwd}" == "${temp_root}/projects/"* ]]; then
        rest="${cwd#"${temp_root}/projects/"}"
        name="${rest%%/*}"
        if [[ -n "${name}" && -d "${KANBAN_ROOT}/projects/${name}" ]]; then
            rest="${rest#"${name}"}"
            rest="${rest#/}"
            if [[ "${rest}" == worktrees/* ]]; then
                id="${rest#worktrees/}"
                id="${id%%/*}"
                kanban_task_id_is_canonical "${id}" && task="${id}"
            fi
            printf '%s\t%s\n' "${name}" "${task}"
            return 0
        fi
    fi
    while IFS= read -r id; do
        [[ -z "${id}" ]] && continue
        for proj_dir in "${KANBAN_ROOT}"/projects/*/; do
            if [[ -d "${proj_dir}tasks/${id}" ]]; then
                name="${proj_dir%/}"
                printf '%s\t%s\n' "${name##*/}" "${id}"
                return 0
            fi
        done
    done < <(kanban_task_ids_in_files <(printf '%s\n' "${cwd}"))
    return 1
}

# ---------------------------------------------------------------------------
# _cll_task_state <project> <task-id>
# Echo the task's ## State upper-cased ("" when the folder or field is absent).
# ---------------------------------------------------------------------------
_cll_task_state() {
    local status="${KANBAN_ROOT}/projects/$1/tasks/$2/status.md"
    [[ -f "${status}" ]] || return 0
    if declare -F pp_run_ops >/dev/null 2>&1; then
        pp_run_ops pgai_agent_kanban.discovery.items task-state "${status}" 2>/dev/null || true
    fi
}

# ---------------------------------------------------------------------------
# overwatch_leaked_listener_scan <out_file>
# Scan /proc once for listeners whose cwd is under the framework temp root
# and write one tab-separated row per process to <out_file>:
#   <pid> <age-seconds> <project or -> <task-id or -> <cwd> <cmdline>
# Requires KANBAN_ROOT. Returns 1 when the temp root or task-ID helpers
# cannot be resolved; 0 otherwise (an empty file when nothing listens).
# ---------------------------------------------------------------------------
overwatch_leaked_listener_scan() {
    local out_file="$1"
    _cll_resolve_temp_root || return 1
    _cll_load_task_ids || { echo "check-leaked-listeners: task_ids.sh not found" >&2; return 1; }
    if ! declare -F pp_run_ops >/dev/null 2>&1; then
        local _cll_ops
        _cll_ops="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." 2>/dev/null && pwd)/pp_run_ops.sh"
        # shellcheck source=../pp_run_ops.sh
        [[ -f "${_cll_ops}" ]] && source "${_cll_ops}"
    fi
    local temp_root="${_CLL_TEMP_ROOT%/}"
    : > "${out_file}" || return 1
    echo "check-leaked-listeners: scanning /proc for listener processes with cwd under ${temp_root}" >&2
    [[ -d /proc ]] || return 0

    local my_pgrp pid pid_dir cwd cmdline pid_pgrp age owner project task
    my_pgrp="$(awk '/^Pgrp:/{print $2}' "/proc/$$/status" 2>/dev/null)"
    for pid_dir in /proc/[0-9]*/; do
        pid="${pid_dir%/}"
        pid="${pid##*/}"
        [[ "${pid}" =~ ^[0-9]+$ ]] || continue
        cwd="$(readlink "/proc/${pid}/cwd" 2>/dev/null || true)"
        [[ "${cwd}" == "${temp_root}" || "${cwd}" == "${temp_root}/"* ]] || continue
        pid_pgrp="$(awk '/^Pgrp:/{print $2}' "/proc/${pid}/status" 2>/dev/null)"
        [[ -n "${my_pgrp}" && "${pid_pgrp}" == "${my_pgrp}" ]] && continue
        _cll_process_has_listening_socket "${pid}" || continue
        age="$(_cll_process_age_seconds "${pid}")"
        cmdline="$(tr '\0' ' ' < "/proc/${pid}/cmdline" 2>/dev/null | head -c 256 || true)"
        cmdline="${cmdline//$'\t'/ }"
        project="-"
        task="-"
        if owner="$(_cll_attribute "${temp_root}" "${cwd}")"; then
            project="${owner%%$'\t'*}"
            task="${owner#*$'\t'}"
            task="${task:--}"
        fi
        printf '%s\t%s\t%s\t%s\t%s\t%s\n' "${pid}" "${age:-0}" "${project}" "${task}" "${cwd}" "${cmdline}" >> "${out_file}"
    done
    return 0
}

# ---------------------------------------------------------------------------
# overwatch_check_leaked_listeners [--dry-run]
# Main detection and action function.
# Returns 0 on success (including finding and handling anomalies), 1 on error.
# ---------------------------------------------------------------------------
overwatch_check_leaked_listeners() {
    local dry_run=0
    local arg
    for arg in "$@"; do
        if [[ "${arg}" == "--dry-run" ]]; then
            dry_run=1
        fi
    done

    _cll_resolve_env || return 1
    _cll_load_protocol || return 1

    local project_name="${OVERWATCH_PROJECT}"
    local project_root="${KANBAN_ROOT}/projects/${project_name}"
    local bugs_dir="${project_root}/bugs"
    local temp_root="${_CLL_TEMP_ROOT}"

    if [[ ! -d "${project_root}" ]]; then
        echo "check-leaked-listeners: project root does not exist: ${project_root}" >&2
        return 1
    fi

    # The sweep scans once for every project; a standalone run scans here.
    local scan_file="${OVERWATCH_LEAKED_LISTENER_SCAN:-}" own_scan=""
    if [[ -z "${scan_file}" || ! -r "${scan_file}" ]]; then
        own_scan="$(mktemp "${temp_root%/}/.leaked-listener-scan.XXXXXX" 2>/dev/null)" || {
            echo "check-leaked-listeners: cannot create a scan file under ${temp_root}" >&2
            return 1
        }
        if ! overwatch_leaked_listener_scan "${own_scan}"; then
            rm -f "${own_scan}"
            return 1
        fi
        scan_file="${own_scan}"
    fi

    local min_age
    min_age="$(_cll_min_age_seconds)"

    local handled=0 unattributed=0
    local pid age owner task cwd cmdline state

    while IFS=$'\t' read -r pid age owner task cwd cmdline; do
        [[ -z "${pid}" ]] && continue
        if [[ "${owner}" == "-" ]]; then
            unattributed=$(( unattributed + 1 ))
            continue
        fi
        [[ "${owner}" == "${project_name}" ]] || continue

        # Verify the process still exists (it may have exited since the scan)
        if [[ ! -d "/proc/${pid}" ]]; then
            echo "check-leaked-listeners: pid=${pid} exited before action; skipping" >&2
            continue
        fi

        if [[ "${task}" != "-" ]]; then
            state="$(_cll_task_state "${project_name}" "${task}")"
            if [[ "${state}" == "WORKING" ]]; then
                echo "check-leaked-listeners: pid=${pid} (cwd=${cwd}) belongs to WORKING task ${task}; skipping" >&2
                continue
            fi
        fi
        if (( age < min_age )); then
            echo "check-leaked-listeners: pid=${pid} (cwd=${cwd}) is ${age}s old, under the ${min_age}s threshold; skipping" >&2
            continue
        fi

        if (( dry_run == 1 )); then
            echo "check-leaked-listeners: [dry-run] would kill pid=${pid} (cwd=${cwd} is under temp root)" >&2
            overwatch_log_action \
                "check-leaked-listeners" \
                "pid:${pid}" \
                "dry-run-would-kill-leaked-listener" \
                "none" \
                "Listener cwd=${cwd} is under temp root ${temp_root}; task=${task}; age=${age}s; cmdline: ${cmdline:0:128}; dry-run, no action" \
            2>/dev/null || true
        else
            echo "check-leaked-listeners: killing pid=${pid} (cwd=${cwd}; task=${task}; age=${age}s)" >&2
            if kill -SIGTERM "${pid}" 2>/dev/null; then
                overwatch_log_action \
                    "check-leaked-listeners" \
                    "pid:${pid}" \
                    "auto-fix:killed-leaked-listener" \
                    "none" \
                    "Listener cwd=${cwd} is under framework temp root ${temp_root}; task=${task}; age=${age}s >= ${min_age}s; SIGTERM sent; cmdline: ${cmdline:0:128}" \
                || true
                handled=$(( handled + 1 ))
            else
                echo "check-leaked-listeners: kill failed for pid=${pid} (already gone or permission denied)" >&2
                overwatch_log_action \
                    "check-leaked-listeners" \
                    "pid:${pid}" \
                    "kill-failed" \
                    "none" \
                    "Listener cwd=${cwd} under temp root; SIGTERM failed (pid gone or permission denied)" \
                || true
            fi
        fi
    done < "${scan_file}"

    [[ -n "${own_scan}" ]] && rm -f "${own_scan}"
    if (( unattributed > 0 )); then
        echo "check-leaked-listeners: ${unattributed} listener(s) under ${temp_root} belong to no registered project; left alone" >&2
    fi
    echo "check-leaked-listeners: complete; handled ${handled} leaked listener(s)" >&2
    return 0
}

# ---------------------------------------------------------------------------
# Standalone invocation entry point.
# ---------------------------------------------------------------------------
if [[ "${BASH_SOURCE[0]:-}" == "${0}" ]]; then
    overwatch_check_leaked_listeners "$@"
    exit $?
fi
