#!/usr/bin/env bash
# team/scripts/lib/overwatch-checks/check-leaked-listeners.sh
#
# OVERWATCH Tier-1 detection module: detect listener processes whose cwd is
# under the framework temp root (PGAI_AGENT_KANBAN_TEMP_DIR).
#
# Detection:
#   - Scan /proc/*/cwd symlinks for any process with a cwd that resolves to a
#     path under the framework temp root.
#   - For each match, also check if the process has any listening TCP/UDP socket
#     (via /proc/net/tcp and /proc/net/tcp6, or ss if available) to classify it
#     as a "leaked listener". Processes with cwd under the temp root but no
#     listening socket are still reported — they may be other fixture residue.
#
# Auto-kill path (cwd under temp root):
#   - Send SIGTERM to the process. Log the action via overwatch_log_action.
#   - No backup is needed for a process kill (no file is modified).
#   - Only fires when the process cwd is provably under the framework temp root.
#
# Bug-file path (cwd not under temp root):
#   - If a listener is detected by other heuristics but its cwd is NOT under the
#     temp root, write a bug report and log it. Never auto-kill in this case.
#
# Module contract:
#   - Sourceable without side effects.
#   - Exports: overwatch_check_leaked_listeners
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
#   OVERWATCH_LEAKED_LISTENER_PORT_SCAN — set to "1" to enable ss-based port
#     scanning for processes that might be listeners without cwd match.
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

    # Resolve the framework temp root via the canonical resolver in temp.sh.
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

# ---------------------------------------------------------------------------
# _cll_bug_file <pid> <cmdline> <cwd> <reason> <bugs_dir>
# Write a bug report for a leaked listener process.
# Echoes the path to the created bug file.
# Returns 0 on success, 1 on failure.
# ---------------------------------------------------------------------------
_cll_bug_file() {
    local pid="$1"
    local cmdline="$2"
    local cwd="$3"
    local reason="$4"
    local bugs_dir="$5"
    local timestamp
    timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
    local bug_file="${bugs_dir}/BUG-overwatch-leaked-listener-${pid}-${timestamp}.md"

    mkdir -p "${bugs_dir}" 2>/dev/null || {
        echo "check-leaked-listeners: cannot create bugs dir: ${bugs_dir}" >&2
        return 1
    }

    cat > "${bug_file}" <<EOF
# Bug: Leaked Listener Process — PID ${pid}

## Status
open

## Filed By
overwatch/check-leaked-listeners

## Filed At
${timestamp}

## PID
${pid}

## Cmdline
${cmdline}

## CWD
${cwd}

## Reason
${reason}

## Description
OVERWATCH detected a process that appears to be a leaked listener. The process
cwd is not under the framework temp root, so it cannot be auto-killed. Manual
inspection is required to determine whether this process is legitimate.
EOF

    echo "${bug_file}"
    return 0
}

# ---------------------------------------------------------------------------
# _cll_scan_proc_cwd <temp_root>
# Scan /proc/*/cwd for processes with a cwd under temp_root.
# Echoes tab-separated lines: <pid>\t<cwd>\t<cmdline>
# ---------------------------------------------------------------------------
_cll_scan_proc_cwd() {
    local temp_root="$1"
    # Normalize temp_root: remove trailing slash
    temp_root="${temp_root%/}"

    if [[ ! -d "/proc" ]]; then
        # Non-Linux or /proc not mounted — cannot scan
        return 0
    fi

    local pid cwd_link cwd cmdline
    for pid_dir in /proc/[0-9]*/; do
        pid="${pid_dir%/}"
        pid="${pid##*/}"
        [[ "${pid}" =~ ^[0-9]+$ ]] || continue

        cwd_link="/proc/${pid}/cwd"
        [[ -L "${cwd_link}" ]] || continue

        cwd="$(readlink "${cwd_link}" 2>/dev/null || true)"
        [[ -z "${cwd}" ]] && continue

        # Check if cwd is under (or equal to) temp_root
        if [[ "${cwd}" == "${temp_root}" || "${cwd}" == "${temp_root}/"* ]]; then
            # Read cmdline (NUL-separated args; replace NUL with space for display)
            cmdline="$(tr '\0' ' ' < "/proc/${pid}/cmdline" 2>/dev/null | head -c 256 || true)"
            printf '%s\t%s\t%s\n' "${pid}" "${cwd}" "${cmdline}"
        fi
    done
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

    echo "check-leaked-listeners: scanning /proc for listener processes with cwd under ${temp_root}" >&2

    # Scan /proc/*/cwd for processes under the temp root.
    # The current process group (OVERWATCH sweep and all its children) is always skipped
    # to prevent the check from killing itself or other framework processes running tests.
    local my_pid="$$"
    local my_pgrp
    my_pgrp="$(cat "/proc/${my_pid}/status" 2>/dev/null | awk '/^Pgrp:/{print $2}' || echo "0")"
    my_pgrp="${my_pgrp:-0}"

    local scan_results
    scan_results="$(_cll_scan_proc_cwd "${temp_root}" 2>/dev/null)"

    if [[ -z "${scan_results}" ]]; then
        echo "check-leaked-listeners: no processes found with cwd under ${temp_root}" >&2
        return 0
    fi

    local handled=0
    local pid cwd cmdline

    while IFS=$'\t' read -r pid cwd cmdline; do
        [[ -z "${pid}" ]] && continue

        # Never kill any process in the current process group (which includes the OVERWATCH
        # sweep and all its child processes spawned during this firing).
        if [[ "${my_pgrp}" != "0" ]]; then
            local pid_pgrp
            pid_pgrp="$(cat "/proc/${pid}/status" 2>/dev/null | awk '/^Pgrp:/{print $2}' || echo "0")"
            if [[ "${pid_pgrp}" == "${my_pgrp}" ]]; then
                echo "check-leaked-listeners: pid=${pid} is in current process group (${my_pgrp}); skipping" >&2
                continue
            fi
        fi

        # Verify the process still exists (it may have exited during scan)
        if [[ ! -d "/proc/${pid}" ]]; then
            echo "check-leaked-listeners: pid=${pid} exited before action; skipping" >&2
            continue
        fi

        # Check if this process has a listening socket (the "listener" criterion).
        local is_listener=0
        if _cll_process_has_listening_socket "${pid}"; then
            is_listener=1
        fi

        echo "check-leaked-listeners: found process pid=${pid} cwd=${cwd} is_listener=${is_listener}" >&2

        # Only target processes that are actually listening on a port.
        # A process with cwd under the temp root but no listening socket is NOT
        # a "leaked listener" — it may be a test runner, a log writer, or other
        # legitimate framework process. Skip non-listeners silently.
        if (( is_listener == 0 )); then
            echo "check-leaked-listeners: pid=${pid} has no listening socket; skipping (not a listener)" >&2
            continue
        fi

        # cwd IS under temp root AND process IS a listener — auto-kill path.
        if (( dry_run == 1 )); then
            echo "check-leaked-listeners: [dry-run] would kill pid=${pid} (cwd=${cwd} is under temp root)" >&2
            overwatch_log_action \
                "check-leaked-listeners" \
                "pid:${pid}" \
                "dry-run-would-kill-leaked-listener" \
                "none" \
                "Process cwd=${cwd} is under temp root ${temp_root}; is_listener=${is_listener}; cmdline: ${cmdline:0:128}; dry-run, no action" \
            2>/dev/null || true
        else
            echo "check-leaked-listeners: killing pid=${pid} (cwd under temp root; fixture-spawned)" >&2
            if kill -SIGTERM "${pid}" 2>/dev/null; then
                overwatch_log_action \
                    "check-leaked-listeners" \
                    "pid:${pid}" \
                    "auto-fix:killed-leaked-listener" \
                    "none" \
                    "Process cwd=${cwd} is under framework temp root ${temp_root}; SIGTERM sent; cmdline: ${cmdline:0:128}" \
                || true
                handled=$(( handled + 1 ))
            else
                echo "check-leaked-listeners: kill failed for pid=${pid} (already gone or permission denied)" >&2
                overwatch_log_action \
                    "check-leaked-listeners" \
                    "pid:${pid}" \
                    "kill-failed" \
                    "none" \
                    "Process cwd=${cwd} under temp root; SIGTERM failed (pid gone or permission denied)" \
                || true
            fi
        fi
    done <<< "${scan_results}"

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
