#!/usr/bin/env bash
# team/scripts/lib/overwatch-checks/check-bare-tmp-litter.sh
#
# OVERWATCH Tier-1 detection module: backstop for bare /tmp litter left by
# agent sessions that crashed before the wake-bracket post-check ran.
#
# This check is REPORT-ONLY. It never deletes any file, never modifies any
# task state, and never auto-fixes anything.
#
# Detection:
#   1. Discover all pre-dispatch litter snapshots written by the wake bracket:
#        ${PGAI_AGENT_KANBAN_TEMP_DIR}/tasks/*/litter/pre_dispatch_tmp_snapshot
#      Each snapshot records the /tmp state at the moment the agent was dispatched.
#      Snapshot format:
#        Line 1: epoch=<N>          — session start epoch (seconds since Unix epoch)
#        Lines 2+: <mtime_epoch>\t<basename>  — /tmp top-level entries at snapshot time
#
#   2. Build the "known task window": the union of all session epochs across
#      discovered snapshots.  The earliest session start and the latest mtime
#      in any snapshot bracket the window inside which a /tmp entry is
#      "framework-session-attributed."
#
#   3. Scan /tmp top-level entries and flag each that satisfies ALL of:
#        a. Owned by the framework user (the OS user running the kanban).
#        b. mtime is AFTER the earliest known task session start epoch.
#        c. Not present in ANY snapshot's pre-session name set
#           (i.e., it appeared during or after a session, not before).
#        d. Not under the framework temp root (${PGAI_AGENT_KANBAN_TEMP_DIR}).
#        e. Not on the allowlist: systemd-*, tmux-*, pytest-of-*.
#        f. Not already reported in the dedup state file.
#
#   4. For each newly-flagged entry: log to the project's actions.log via
#      overwatch_log_action and add to the dedup state file.
#
# Dedup state file:
#   ${KANBAN_ROOT}/projects/${OVERWATCH_PROJECT}/overwatch/litter-reported.txt
#   One line per reported /tmp basename; check only ever appends.
#   Reset the file to re-report already-flagged entries (operator action).
#
# Snapshot discovery root:
#   Resolved via PGAI_AGENT_KANBAN_TEMP_DIR (env var) or the kanban default
#   temp root (see team/scripts/lib/temp.sh for the resolution order and fallback).
#
# Module contract:
#   - Sourceable without side effects.
#   - Exports: overwatch_check_bare_tmp_litter
#   - Zero arguments. All context from environment variables.
#   - Returns 0 in all cases (REPORT-ONLY; errors in detection are logged,
#     not fatal to the sweep).
#
# Required environment variables (set by OVERWATCH driver or sweep runner):
#   KANBAN_ROOT        — absolute path to the kanban root
#   OVERWATCH_PROJECT  — project name (e.g. "pgai-agent-kanban")
#
# Optional:
#   PGAI_AGENT_KANBAN_TEMP_DIR — framework temp root (resolved by temp.sh; see that file for defaults)
#
# Usage (standalone):
#   bash check-bare-tmp-litter.sh
#
# Exit codes:
#   0 — always (REPORT-ONLY check)

# ---------------------------------------------------------------------------
# _cbtl_resolve_env
# Resolve KANBAN_ROOT and OVERWATCH_PROJECT from the environment.
# Sets _CBTL_FW_TEMP_ROOT in the calling scope.
# Returns 0 on success, 1 on failure.
# ---------------------------------------------------------------------------
_cbtl_resolve_env() {
    if [[ -z "${KANBAN_ROOT:-}" ]]; then
        KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-${HOME}/pgai_agent_kanban}"
    fi
    if [[ ! -d "${KANBAN_ROOT}" ]]; then
        echo "check-bare-tmp-litter: KANBAN_ROOT does not exist: ${KANBAN_ROOT}" >&2
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
            echo "check-bare-tmp-litter: ERROR: OVERWATCH_PROJECT is not set and multiple projects are registered." >&2
            echo "  Set OVERWATCH_PROJECT to the target project name before running this check." >&2
            echo "  Registered projects: $(echo "${_all_projects}" | tr '\n' ' ')" >&2
            return 1
        fi
        OVERWATCH_PROJECT="$(echo "${_all_projects}" | grep '[^[:space:]]' | head -n1)"
        if [[ -z "${OVERWATCH_PROJECT:-}" ]]; then
            echo "check-bare-tmp-litter: ERROR: no project specified and none resolvable from projects.cfg" >&2
            return 1
        fi
    fi

    # Resolve the framework temp root via temp.sh when available; fall back to
    # the env-var direct value when the library is not yet sourced.
    if ! declare -f pgai_temp_dir >/dev/null 2>&1; then
        local _lib_dir
        _lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)" || true
        local _temp_sh="${_lib_dir}/../temp.sh"
        if [[ -f "${_temp_sh}" ]]; then
            # shellcheck source=/dev/null
            source "${_temp_sh}"
        fi
    fi

    if declare -f pgai_temp_dir >/dev/null 2>&1; then
        _CBTL_FW_TEMP_ROOT="$(pgai_temp_dir)"
    else
        # temp.sh unavailable: honour env var directly (resolver not loaded).
        _CBTL_FW_TEMP_ROOT="${PGAI_AGENT_KANBAN_TEMP_DIR:-}"
        if [[ -z "${_CBTL_FW_TEMP_ROOT}" ]]; then
            echo "check-bare-tmp-litter: WARNING: temp.sh not found and PGAI_AGENT_KANBAN_TEMP_DIR unset; skipping temp-root resolution" >&2
            return 1
        fi
    fi

    return 0
}

# ---------------------------------------------------------------------------
# _cbtl_load_protocol
# Ensure overwatch_log_action is available.
# Returns 0 on success, 1 on failure.
# ---------------------------------------------------------------------------
_cbtl_load_protocol() {
    if declare -f overwatch_log_action >/dev/null 2>&1; then
        return 0
    fi
    local lib_dir
    lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)" || {
        echo "check-bare-tmp-litter: cannot resolve lib dir" >&2
        return 1
    }
    local protocol_sh="${lib_dir}/../overwatch_protocol.sh"
    if [[ ! -f "${protocol_sh}" ]]; then
        echo "check-bare-tmp-litter: overwatch_protocol.sh not found at ${protocol_sh}" >&2
        return 1
    fi
    # shellcheck source=/dev/null
    source "${protocol_sh}"
}

# ---------------------------------------------------------------------------
# _cbtl_discover_snapshots <fw_temp_root>
# Print one snapshot path per line for all discovered pre-dispatch snapshot files.
# Outputs to stdout; returns 0 (missing temp root is not an error — just no sessions).
# ---------------------------------------------------------------------------
_cbtl_discover_snapshots() {
    local fw_temp_root="$1"
    local snap_glob="${fw_temp_root}/tasks/*/litter/pre_dispatch_tmp_snapshot"

    local f
    for f in ${snap_glob}; do
        [[ -f "$f" ]] || continue
        echo "$f"
    done
}

# ---------------------------------------------------------------------------
# _cbtl_read_snapshot <snapshot_file> <epoch_var> <pre_names_assoc>
# Read a snapshot file into caller-scoped variables.
#   <epoch_var>       — name of a variable to set to the session epoch (integer)
#   <pre_names_assoc> — name of an associative array to populate with pre-session basenames
#
# Snapshot format:
#   Line 1: epoch=<N>
#   Lines 2+: <mtime_epoch>\t<basename>
#
# Returns 0 on success, 1 on error.
# ---------------------------------------------------------------------------
_cbtl_read_snapshot() {
    local snapshot_file="$1"
    local epoch_var="$2"
    local -n _cbtl_pre_names="$3"  # nameref to caller's assoc array

    if [[ ! -f "${snapshot_file}" ]]; then
        echo "check-bare-tmp-litter: snapshot file not found: ${snapshot_file}" >&2
        return 1
    fi

    # Read epoch from header line
    local header
    header="$(head -1 "${snapshot_file}" 2>/dev/null || echo "")"
    if [[ "${header}" == epoch=* ]]; then
        printf -v "${epoch_var}" '%s' "${header#epoch=}"
    else
        printf -v "${epoch_var}" '%s' "0"
    fi

    # Read pre-session names
    while IFS=$'\t' read -r _mt _bn; do
        [[ -z "${_bn}" || "${_bn}" == epoch=* ]] && continue
        _cbtl_pre_names["${_bn}"]=1
    done < <(tail -n +2 "${snapshot_file}" 2>/dev/null)

    return 0
}

# ---------------------------------------------------------------------------
# _cbtl_file_owner <path>
# Echo the username owning <path>, or empty string on error.
# ---------------------------------------------------------------------------
_cbtl_file_owner() {
    local path="$1"
    stat -c '%U' "${path}" 2>/dev/null || true
}

# ---------------------------------------------------------------------------
# _cbtl_file_mtime <path>
# Echo the mtime epoch of <path>, or 0 on error.
# ---------------------------------------------------------------------------
_cbtl_file_mtime() {
    local path="$1"
    stat -c '%Y' "${path}" 2>/dev/null || echo 0
}

# ---------------------------------------------------------------------------
# overwatch_check_bare_tmp_litter
# Main detection function. REPORT-ONLY — never modifies or deletes any file.
# Returns 0 in all cases.
# ---------------------------------------------------------------------------
overwatch_check_bare_tmp_litter() {
    _cbtl_resolve_env || return 0
    _cbtl_load_protocol || return 0

    local kanban_root="${KANBAN_ROOT}"
    local project="${OVERWATCH_PROJECT}"
    local fw_temp_root="${_CBTL_FW_TEMP_ROOT}"

    local state_dir="${kanban_root}/projects/${project}/overwatch"
    local dedup_file="${state_dir}/litter-reported.txt"

    # Ensure the state dir exists (driver bootstraps it, but be defensive).
    if [[ ! -d "${state_dir}" ]]; then
        echo "check-bare-tmp-litter: overwatch state dir missing: ${state_dir}; skipping" >&2
        return 0
    fi

    # Determine the framework user (the user running the kanban framework).
    local fw_user
    fw_user="$(whoami 2>/dev/null || id -un 2>/dev/null || echo "")"
    if [[ -z "${fw_user}" ]]; then
        echo "check-bare-tmp-litter: cannot determine framework user; skipping" >&2
        return 0
    fi

    echo "check-bare-tmp-litter: framework user=${fw_user} fw_temp_root=${fw_temp_root}" >&2

    # Resolve the framework temp root basename for exclusion.
    local fw_temp_basename
    fw_temp_basename="$(basename "${fw_temp_root}")"

    # Discover snapshot files.
    local snapshots=()
    while IFS= read -r snap; do
        snapshots+=("${snap}")
    done < <(_cbtl_discover_snapshots "${fw_temp_root}")

    echo "check-bare-tmp-litter: discovered ${#snapshots[@]} snapshot(s) under ${fw_temp_root}/tasks/" >&2

    if [[ "${#snapshots[@]}" -eq 0 ]]; then
        echo "check-bare-tmp-litter: no task snapshots found; nothing to check" >&2
        overwatch_log_action \
            "check-bare-tmp-litter" \
            "${project}" \
            "skipped-no-snapshots" \
            "none" \
            "No pre-dispatch litter snapshots found under ${fw_temp_root}/tasks/; nothing to check" \
        || true
        return 0
    fi

    # Build a unified view:
    #   - Earliest session epoch (all tasks combined)
    #   - Union of all pre-session basenames across all snapshots
    # An entry is "known pre-session" if it appeared in ANY snapshot taken
    # before ANY session.  This is conservative: we skip entries present
    # before any session started rather than trying to correlate per-task.
    local earliest_epoch=0
    declare -A _all_pre_names  # basename -> 1

    local snap
    for snap in "${snapshots[@]}"; do
        local snap_epoch=0
        declare -A _snap_pre_names

        if _cbtl_read_snapshot "${snap}" snap_epoch _snap_pre_names; then
            if [[ "${snap_epoch}" -gt 0 ]]; then
                if [[ "${earliest_epoch}" -eq 0 || "${snap_epoch}" -lt "${earliest_epoch}" ]]; then
                    earliest_epoch="${snap_epoch}"
                fi
            fi
            local bn
            for bn in "${!_snap_pre_names[@]}"; do
                _all_pre_names["${bn}"]=1
            done
        else
            echo "check-bare-tmp-litter: failed to read snapshot ${snap}; skipping it" >&2
        fi

        unset _snap_pre_names
    done

    echo "check-bare-tmp-litter: earliest_session_epoch=${earliest_epoch} known_pre_names=${#_all_pre_names[@]}" >&2

    # Load the dedup state file into memory.
    declare -A _already_reported  # basename -> 1
    if [[ -f "${dedup_file}" ]]; then
        while IFS= read -r _bn; do
            [[ -z "${_bn}" ]] && continue
            _already_reported["${_bn}"]=1
        done < "${dedup_file}"
    fi
    echo "check-bare-tmp-litter: already_reported=${#_already_reported[@]} entries in dedup file" >&2

    # Scan /tmp top-level entries.
    local flagged=()
    local entry
    for entry in /tmp/*; do
        [[ -e "${entry}" || -L "${entry}" ]] || continue

        local bn
        bn="$(basename "${entry}")"

        # Skip the framework temp root.
        [[ "${bn}" == "${fw_temp_basename}" ]] && continue

        # Skip allowlist patterns.
        case "${bn}" in
            systemd-*|tmux-*|pytest-of-*)
                continue
                ;;
        esac

        # Skip entries already reported.
        [[ -n "${_already_reported[${bn}]+_}" ]] && continue

        # Skip entries that were present before any known session (not litter).
        [[ -n "${_all_pre_names[${bn}]+_}" ]] && continue

        # Check ownership — only flag entries owned by the framework user.
        local owner
        owner="$(_cbtl_file_owner "${entry}")"
        if [[ "${owner}" != "${fw_user}" ]]; then
            echo "check-bare-tmp-litter: skipping /tmp/${bn} (owner=${owner} != fw_user=${fw_user})" >&2
            continue
        fi

        # Apply time window gate: only flag entries created AFTER the earliest
        # known session start. Entries that predate all known sessions are not
        # attributable to a framework task.
        if [[ "${earliest_epoch}" -gt 0 ]]; then
            local mtime
            mtime="$(_cbtl_file_mtime "${entry}")"
            if [[ "${mtime}" -le "${earliest_epoch}" ]]; then
                echo "check-bare-tmp-litter: skipping /tmp/${bn} (mtime=${mtime} <= earliest_epoch=${earliest_epoch}; predates all sessions)" >&2
                continue
            fi
        fi

        # All gates passed — this entry is candidate litter.
        flagged+=("${bn}")
    done

    echo "check-bare-tmp-litter: flagged ${#flagged[@]} new litter entry(ies)" >&2

    if [[ "${#flagged[@]}" -eq 0 ]]; then
        overwatch_log_action \
            "check-bare-tmp-litter" \
            "${project}" \
            "report:no-litter-found" \
            "none" \
            "Scanned /tmp against ${#snapshots[@]} session snapshot(s); no unreported framework-user litter found" \
        || true
        return 0
    fi

    # Report each newly-found litter entry.
    local bn
    for bn in "${flagged[@]}"; do
        overwatch_log_action \
            "check-bare-tmp-litter" \
            "/tmp/${bn}" \
            "report:litter-found" \
            "none" \
            "Bare /tmp entry /tmp/${bn} is owned by framework user ${fw_user}, created after a known task session, and was not reported by the wake bracket — likely from a crashed session; REPORT-ONLY; do not delete manually without operator review" \
        || true

        echo "check-bare-tmp-litter: flagged /tmp/${bn} (owner=${fw_user}; litter from crashed session)" >&2

        # Append to the dedup state file so this entry is not re-reported.
        printf '%s\n' "${bn}" >> "${dedup_file}" 2>/dev/null || \
            echo "check-bare-tmp-litter: WARNING: failed to write dedup entry for ${bn} to ${dedup_file}" >&2
    done

    # Log a summary to the action log.
    overwatch_log_action \
        "check-bare-tmp-litter" \
        "${project}" \
        "report:litter-summary" \
        "none" \
        "${#flagged[@]} new bare /tmp litter entry(ies) flagged: ${flagged[*]}; dedup state at ${dedup_file}; REPORT-ONLY" \
    || true

    return 0
}

# ---------------------------------------------------------------------------
# Standalone invocation entry point.
# When this script is executed directly (not sourced), call the main function.
# ---------------------------------------------------------------------------
if [[ "${BASH_SOURCE[0]:-}" == "${0}" ]]; then
    overwatch_check_bare_tmp_litter "$@"
    exit $?
fi
