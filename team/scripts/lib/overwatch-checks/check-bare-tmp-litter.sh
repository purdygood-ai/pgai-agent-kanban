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
#   1. Discover the pre-dispatch litter snapshots the wake bracket wrote for
#      the swept project's tasks, in the one directory the wake writes them:
#        <project temp dir>/tasks/*/litter/pre_dispatch_tmp_snapshot
#      where <project temp dir> is resolved as described under "Snapshot
#      discovery root" below. No other directory is read.
#      Each snapshot records the /tmp state at the moment the agent was dispatched.
#      Snapshot format (written by wake_tmp_litter_take_snapshot in temp.sh):
#        Line 1: epoch=<N>          — session start epoch (seconds since Unix epoch)
#        Lines 2+: one record per /tmp top-level entry, dot-named ones included:
#          <mtime_epoch>\t<basename>     — a basename without a line feed,
#                                          byte for byte
#          esc=<mtime_epoch>\t<escaped>  — a basename holding a line feed,
#                                          each backslash written `\\` and
#                                          each line feed `\n`
#      Everything after a record's first tab is the name. Records are keyed
#      through temp.sh's _wake_tmp_litter_snapshot_record_key, and /tmp
#      entries through _wake_tmp_litter_record_key, so an entry matches its
#      record whatever its name holds.
#
#   2. Build the "known task window": the union of all session epochs across
#      discovered snapshots.  The earliest session start and the latest mtime
#      in any snapshot bracket the window inside which a /tmp entry is
#      "framework-session-attributed."
#
#   3. Scan /tmp top-level entries, dot-named ones included (enumerated in a
#      subshell, so the caller's dotglob and other shell options are left as
#      they were), and flag each that satisfies ALL of:
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
#   One LF-terminated line per reported /tmp basename; check only ever appends.
#     <basename>       — a basename without a line feed, byte for byte
#     esc=<escaped>    — a basename holding a line feed, escaped as in the
#                        snapshot's esc= record
#   On read every line suppresses the basename it spells byte for byte, and
#   an esc= line also suppresses the line-feed name it decodes to, so a
#   plain name that happens to start with `esc=` still deduplicates.
#   Reset the file to re-report already-flagged entries (operator action).
#
# Snapshot discovery root:
#   <project temp dir> is the PP_TEMP_DIR that pp_load_config
#   (team/scripts/lib/project_paths.sh) exports for OVERWATCH_PROJECT -- the
#   same value wake_bracket_pre_dispatch writes under -- so with <temp root>
#   the framework temp root:
#     <temp root>/projects/<project>  -- project.cfg sets no [project] temp_dir
#     <temp root>/<temp_dir>          -- project.cfg [project] temp_dir is set
#     <temp root>                     -- the project has no config file
#                                        (pp_load_config fails; the wake then
#                                        falls back to pgai_temp_dir too)
#   <temp root> is PGAI_AGENT_KANBAN_TEMP_DIR or the kanban default temp root
#   (see team/scripts/lib/temp.sh for the resolution order and fallback).
#   The value is resolved fresh for every sweep; an inherited PP_TEMP_DIR is
#   never used, since the sweep runner does not set it per project.
#
# Module contract:
#   - Sourceable without side effects.
#   - Exports: overwatch_check_bare_tmp_litter
#   - Zero arguments. All context from environment variables.
#   - Returns 0 in all cases (REPORT-ONLY; errors in detection are logged,
#     not fatal to the sweep).
#
# Required environment variables (set by OVERWATCH driver or sweep runner):
#   KANBAN_ROOT        — absolute path to the kanban root (its
#                        projects/<project>/project.cfg names the temp_dir)
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

# This file's directory, as an absolute path
# resolved once at load: no function depends on the caller's current
# directory or on whether this file was named by a relative path.
_CBTL_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)" || _CBTL_SELF_DIR=""

# overwatch_registered_projects: the shared projects.cfg reader.
# shellcheck source=../overwatch_config.sh
source "$(dirname "${BASH_SOURCE[0]}")/../overwatch_config.sh"

# ---------------------------------------------------------------------------
# _cbtl_resolve_env
# Resolve KANBAN_ROOT and OVERWATCH_PROJECT from the environment, and load
# the temp root resolver (temp.sh) and the project config loader
# (project_paths.sh) from beside this tree when the caller has not.
# Sets _CBTL_FW_TEMP_ROOT in the calling scope.
# Returns 0 on success, 1 on failure.
# ---------------------------------------------------------------------------
_cbtl_resolve_env() {
    overwatch_resolve_check_project check-bare-tmp-litter || return 1

    # Resolve the framework temp root via temp.sh's pgai_temp_dir, the one
    # resolver. A missing temp.sh beside this tree is a broken install: say
    # so, naming the path, rather than guessing the root by hand.
    # The same file carries the snapshot record-key helpers the scan keys
    # every name through.
    local _temp_sh="${_CBTL_SELF_DIR}/../temp.sh"
    if ! declare -f pgai_temp_dir >/dev/null 2>&1 \
        || ! declare -f _wake_tmp_litter_record_key >/dev/null 2>&1 \
        || ! declare -f _wake_tmp_litter_snapshot_record_key >/dev/null 2>&1; then
        if [[ -f "${_temp_sh}" ]]; then
            # shellcheck source=/dev/null
            source "${_temp_sh}"
        fi
    fi

    if ! declare -f pgai_temp_dir >/dev/null 2>&1 \
        || ! declare -f _wake_tmp_litter_record_key >/dev/null 2>&1 \
        || ! declare -f _wake_tmp_litter_snapshot_record_key >/dev/null 2>&1; then
        echo "check-bare-tmp-litter: WARNING: temp root resolver or litter record-key helpers not found at ${_temp_sh}; skipping the check (reinstall the kanban so lib/temp.sh is present)" >&2
        return 1
    fi

    # pp_load_config resolves the project temp dir the wake writes
    # snapshots under; the check reads only that directory.
    local _project_paths_sh="${_CBTL_SELF_DIR}/../project_paths.sh"
    if ! declare -f pp_load_config >/dev/null 2>&1 && [[ -f "${_project_paths_sh}" ]]; then
        # shellcheck source=/dev/null
        source "${_project_paths_sh}"
    fi
    if ! declare -f pp_load_config >/dev/null 2>&1; then
        echo "check-bare-tmp-litter: WARNING: project config loader pp_load_config not found at ${_project_paths_sh}; skipping the check (reinstall the kanban so lib/project_paths.sh is present)" >&2
        return 1
    fi
    _CBTL_FW_TEMP_ROOT="$(pgai_temp_dir)"

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
    lib_dir="${_CBTL_SELF_DIR}"
    [[ -n "${lib_dir}" ]] || {
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
# _cbtl_project_temp_dir <project> <fw_temp_root>
# Echo the temp dir wake_bracket_pre_dispatch writes <project>'s snapshots
# under: the PP_TEMP_DIR pp_load_config exports for <project>, or
# <fw_temp_root> when pp_load_config fails (no config file), the wake's own
# pgai_temp_dir fallback. pp_load_config runs in a subshell, so none of the
# PP_* names it exports reaches the sweep, and an inherited PP_TEMP_DIR
# never stands in for the project's own.
# ---------------------------------------------------------------------------
_cbtl_project_temp_dir() {
    local project="$1"
    local fw_temp_root="$2"
    local dir=""
    if dir="$(pp_load_config "${project}" >/dev/null 2>&1 && printf '%s' "${PP_TEMP_DIR:-}")" \
        && [[ -n "${dir}" ]]; then
        printf '%s' "${dir}"
    else
        printf '%s' "${fw_temp_root}"
    fi
}

# ---------------------------------------------------------------------------
# _cbtl_discover_snapshots <project_temp_dir>
# Print one path per line for every pre-dispatch snapshot file at
# <project_temp_dir>/tasks/*/litter/pre_dispatch_tmp_snapshot.
# Outputs to stdout; returns 0 (a missing directory is not an error — just no sessions).
# ---------------------------------------------------------------------------
_cbtl_discover_snapshots() {
    local project_temp_dir="$1"

    local f
    for f in "${project_temp_dir}"/tasks/*/litter/pre_dispatch_tmp_snapshot; do
        [[ -f "$f" ]] || continue
        echo "$f"
    done
}

# ---------------------------------------------------------------------------
# _cbtl_read_snapshot <snapshot_file> <epoch_var> <pre_names_assoc>
# Read a snapshot file into caller-scoped variables.
#   <epoch_var>       — name of a variable to set to the session epoch (integer)
#   <pre_names_assoc> — name of an associative array to populate with the
#                       record key (_wake_tmp_litter_record_key) of every
#                       pre-session entry
#
# Snapshot format (see the header): line 1 `epoch=<N>`, then one
# `<mtime_epoch>\t<basename>` or `esc=<mtime_epoch>\t<escaped>` record per
# entry, keyed by _wake_tmp_litter_snapshot_record_key (temp.sh).
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

    # Read pre-session record keys
    local _cbtl_rec _cbtl_rec_key
    while IFS= read -r _cbtl_rec; do
        _wake_tmp_litter_snapshot_record_key _cbtl_rec_key "${_cbtl_rec}" || continue
        _cbtl_pre_names["${_cbtl_rec_key}"]=1
    done < <(tail -n +2 "${snapshot_file}" 2>/dev/null)

    return 0
}

# ---------------------------------------------------------------------------
# _cbtl_read_dedup <dedup_file> <reported_assoc>
# Populate the caller's associative array <reported_assoc> with the record
# keys (_wake_tmp_litter_record_key) the dedup file suppresses: `r:<line>`
# for every non-empty line, plus `e:<escaped>` for a line `esc=<escaped>`.
# A missing file suppresses nothing.
# ---------------------------------------------------------------------------
_cbtl_read_dedup() {
    local dedup_file="$1"
    local -n _cbtl_reported="$2"  # nameref to caller's assoc array
    [[ -f "${dedup_file}" ]] || return 0
    local _cbtl_line
    while IFS= read -r _cbtl_line; do
        [[ -z "${_cbtl_line}" ]] && continue
        _cbtl_reported["r:${_cbtl_line}"]=1
        if [[ "${_cbtl_line}" == esc=* ]]; then
            _cbtl_reported["e:${_cbtl_line#esc=}"]=1
        fi
    done < "${dedup_file}"
    return 0
}

# ---------------------------------------------------------------------------
# _cbtl_dedup_line <out_var> <basename>
# Assign to <out_var> the dedup-file line recording <basename>: the name
# itself when it holds no line feed, `esc=<escaped>` (the escaping of
# _wake_tmp_litter_record_key) when it does, so the line round-trips through
# _cbtl_read_dedup to the key the scan looks the entry up by.
# ---------------------------------------------------------------------------
_cbtl_dedup_line() {
    local _cbtl_key
    _wake_tmp_litter_record_key _cbtl_key "$2"
    if [[ "${_cbtl_key}" == e:* ]]; then
        printf -v "$1" 'esc=%s' "${_cbtl_key#e:}"
    else
        printf -v "$1" '%s' "$2"
    fi
}

# ---------------------------------------------------------------------------
# _cbtl_list_tmp_entries
# Print every top-level /tmp entry path, dot-named ones included, each
# NUL-terminated so a name holding a line feed (trailing or not) survives
# intact. The glob runs in a subshell with its own glob settings, so the
# caller's dotglob, nullglob, failglob and GLOBIGNORE are never touched;
# `.` and `..` never match.
# ---------------------------------------------------------------------------
_cbtl_list_tmp_entries() {
    (
        unset GLOBIGNORE
        shopt -s dotglob nullglob
        shopt -u failglob
        local _cbtl_entry
        for _cbtl_entry in /tmp/*; do
            [[ -e "${_cbtl_entry}" || -L "${_cbtl_entry}" ]] || continue
            printf '%s\0' "${_cbtl_entry}"
        done
    )
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

    # The one directory the wake writes this project's snapshots under.
    local project_temp_dir snap_dir
    project_temp_dir="$(_cbtl_project_temp_dir "${project}" "${fw_temp_root}")"
    snap_dir="${project_temp_dir}/tasks/*/litter/"

    # Resolve the framework temp root basename for exclusion.
    local fw_temp_basename
    fw_temp_basename="$(basename "${fw_temp_root}")"

    # Discover snapshot files.
    local snapshots=()
    while IFS= read -r snap; do
        snapshots+=("${snap}")
    done < <(_cbtl_discover_snapshots "${project_temp_dir}")

    echo "check-bare-tmp-litter: discovered ${#snapshots[@]} snapshot(s) under ${snap_dir}" >&2

    if [[ "${#snapshots[@]}" -eq 0 ]]; then
        echo "check-bare-tmp-litter: no task snapshots found; nothing to check" >&2
        overwatch_log_action \
            "check-bare-tmp-litter" \
            "${project}" \
            "skipped-no-snapshots" \
            "none" \
            "No pre-dispatch litter snapshots found under ${snap_dir}; nothing to check" \
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
    declare -A _all_pre_names=()  # record key -> 1

    local snap
    for snap in "${snapshots[@]}"; do
        local snap_epoch=0
        declare -A _snap_pre_names=()

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
    declare -A _already_reported=()  # record key -> 1
    _cbtl_read_dedup "${dedup_file}" _already_reported
    echo "check-bare-tmp-litter: already_reported=${#_already_reported[@]} keys from dedup file" >&2

    # Scan /tmp top-level entries, dot-named ones included. Each basename is
    # cut from its path by parameter expansion, which keeps a trailing line
    # feed that $(basename ...) would strip.
    local flagged=()
    local entry
    while IFS= read -r -d '' entry; do
        local bn key
        bn="${entry#/tmp/}"
        _wake_tmp_litter_record_key key "${bn}"

        # Skip the framework temp root.
        [[ "${bn}" == "${fw_temp_basename}" ]] && continue

        # Skip allowlist patterns.
        case "${bn}" in
            systemd-*|tmux-*|pytest-of-*)
                continue
                ;;
        esac

        # Skip entries already reported.
        [[ -n "${_already_reported[${key}]+_}" ]] && continue

        # Skip entries that were present before any known session (not litter).
        [[ -n "${_all_pre_names[${key}]+_}" ]] && continue

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
    done < <(_cbtl_list_tmp_entries)

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

        # Append to the dedup state file so this entry is not re-reported:
        # one line whatever the name holds (_cbtl_dedup_line).
        local dedup_line
        _cbtl_dedup_line dedup_line "${bn}"
        printf '%s\n' "${dedup_line}" >> "${dedup_file}" 2>/dev/null || \
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
