#!/usr/bin/env bash
# team/scripts/lib/sentinel_leak_check.sh
# Post-suite check that a test run left the LIVE kanban root's pause and
# posture sentinels exactly as it found them.
#
# WHY THIS EXISTS
# ---------------
# The action-parity fixtures (and the verb fixture matrix before them) arm
# HALT, HALT-AFTER and DEFER-LOW-BUGS files as part of proving the halt and
# deferral verbs. Every one of those fixtures arms its sentinel inside a
# synthetic board and removes it on the way out -- but a fixture that
# resolved the wrong root would arm a REAL one, and a leaked HALT pauses a
# real lane while a leaked DEFER-LOW-BUGS silently changes what the discovery
# pipeline bundles. Neither shows up as a test failure. This check makes the
# live root's sentinel set an observed invariant of the gated runners: a
# snapshot before pytest, the same snapshot after, any difference reported.
#
# It reports and never repairs: only the operator removes a HALT file
# (team/SOP-core.md "HALT Flags"), and a sentinel this run REMOVED is as much
# a finding as one it added -- an unhalted lane is a live-root mutation too.
#
# WHAT IS A SENTINEL HERE
# -----------------------
# The three basenames HALT, HALT-AFTER and DEFER-LOW-BUGS, looked for at the
# kanban root itself (the fleet-scoped HALT / HALT-AFTER) and directly under
# every projects/<name>/ (the project-scoped forms). Nothing deeper: those
# are the only two scopes the framework arms.
#
# WHO WROTE IT (PROVENANCE)
# -------------------------
# A long suite leaves room for the operator, or an agent, to arm a sentinel
# on the live root while it runs, and the framework's own HALT-AFTER steps
# rewrite and promote sentinels as a release moves; both look exactly like a
# leak. Every writer that records appends one line to
#
#   <root>/logs/sentinel-writes.log
#
# after a write that changed (or removed) its sentinel. The writers that
# record, by op name:
#
#   halt, halt_global,       pgai_agent_kanban.ops.write -- the functions
#   halt_after, defer        behind halt.sh, halt-global.sh, halt-after.sh,
#                            defer.sh and the dashboard.
#   drain_capture            pgai_agent_kanban.halt_after.drain check_drain,
#                            rewriting a bare "rc" HALT-AFTER to the
#                            in-flight "rc:vX.Y.Z".
#   promote                  pgai_agent_kanban.halt_after.promote promote,
#                            creating HALT and removing HALT-AFTER (one
#                            record each).
#
# A record is one line of tab-separated key=value fields: time, epoch, op,
# path, sha256 ("absent" when the write removed the sentinel), entry (the
# writing process's command line), pid, ppid, parent (its parent's command
# line), user, test (the pytest node id when the write ran under pytest,
# else "-"). pgai_agent_kanban.lib.sentinel_provenance defines the format.
# The sentinel's own bytes never change.
#
# Before reporting a difference, the check looks up the latest record for
# that path written at or after the pre-suite snapshot (the snapshot file's
# mtime; any record at all when no snapshot is given). For an ADDED or
# CHANGED sentinel the record must describe a write that left a file; for a
# REMOVED one it must say sha256=absent. A record of the other kind explains
# nothing and reads as no record.
#
#   record with test=-       an operator or agent write. Narrated on stdout
#                            as ATTRIBUTED, naming op, pid, time, entry and
#                            parent, and NOT counted as a finding.
#   record naming a test     still reported ADDED / CHANGED / REMOVED, with
#                            the test that wrote it appended to the line.
#   no record                reported ADDED / CHANGED / REMOVED as a
#                            finding -- the shape of a fixture writing or
#                            deleting the file directly on the live root.
#
# A record matches a path when both name the same string, the same file
# (bash -ef), or the same basename in the same directory -- the last is how
# a removed file, which -ef cannot see, is matched. The ops remove verbs
# (unhalt, unhalt_global, undefer) leave no record, so a sentinel they
# remove is still reported REMOVED.
#
# USAGE
# -----
#   source "$(dirname "${BASH_SOURCE[0]}")/lib/sentinel_leak_check.sh"
#
#   snap="$(pgai_mktemp sentinel_presuite_snapshot)"
#   pgai_sentinel_snapshot "$PGAI_AGENT_KANBAN_ROOT_PATH" > "$snap"
#   ... run the suite ...
#   pgai_sentinel_leak_check "$PGAI_AGENT_KANBAN_ROOT_PATH" "$snap" || <record finding>
#
# Both functions accept the root positionally; when it is omitted they fall
# back to $PGAI_AGENT_KANBAN_ROOT_PATH, then $KANBAN_ROOT. A root that is
# empty or does not exist is not an error: the snapshot is empty and the
# check narrates that it had no live root to guard, returning 0. Both are
# safe under a caller's `set -euo pipefail` -- no bare pipeline whose first
# stage can fail, no unguarded assignment of a failing command.
#
# OUTPUT
# ------
#   pgai_sentinel_snapshot    stdout: one "<path>\t<sha256>" line per
#                             sentinel present, sorted by path; nothing when
#                             none exist. Always returns 0.
#   pgai_sentinel_leak_check  stderr: one "[pgai-sentinel] ..." line per
#                             difference (ADDED / CHANGED / REMOVED, with the
#                             path), then a one-line verdict. stdout: one
#                             "[pgai-sentinel] ATTRIBUTED: ..." line per
#                             difference a provenance record attributes to an
#                             operator or agent write, and the clean verdict.
#                             Returns 1 when any unattributed difference was
#                             found, 0 otherwise.

# Basenames a sentinel may carry -- kept in one place so the two functions
# below and any future reader agree on the set.
_PGAI_SENTINEL_BASENAMES=(HALT HALT-AFTER DEFER-LOW-BUGS)

# The provenance log, relative to the root (see WHO WROTE IT above); the
# same path as pgai_agent_kanban.lib.sentinel_provenance.LOG_RELPATH.
_PGAI_SENTINEL_WRITE_LOG="logs/sentinel-writes.log"

# The sha256 a record carries when its write removed the sentinel; the same
# value as pgai_agent_kanban.lib.sentinel_provenance.ABSENT_DIGEST.
_PGAI_SENTINEL_ABSENT_DIGEST="absent"

# ---------------------------------------------------------------------------
# _pgai_sentinel_resolve_root [root]
# Print the root to guard: the argument, else $PGAI_AGENT_KANBAN_ROOT_PATH,
# else $KANBAN_ROOT, else nothing. Always returns 0.
# ---------------------------------------------------------------------------
_pgai_sentinel_resolve_root() {
    local root="${1:-}"
    if [[ -z "${root}" ]]; then
        root="${PGAI_AGENT_KANBAN_ROOT_PATH:-${KANBAN_ROOT:-}}"
    fi
    printf '%s' "${root%/}"
    return 0
}

# ---------------------------------------------------------------------------
# _pgai_sentinel_hash_line <path>
# Print "<path>\t<sha256>" for a regular file; nothing for anything else
# (a directory named HALT is not a sentinel). Always returns 0.
# ---------------------------------------------------------------------------
_pgai_sentinel_hash_line() {
    local path="$1"
    [[ -f "${path}" ]] || return 0
    local digest=""
    digest="$(sha256sum -- "${path}" 2>/dev/null)" && true || true
    digest="${digest%% *}"
    printf '%s\t%s\n' "${path}" "${digest:-unreadable}"
    return 0
}

# ---------------------------------------------------------------------------
# _pgai_sentinel_provenance <root> <path> <since_epoch>
# Print the latest provenance record for <path> whose epoch is at or after
# <since_epoch>, as seven tab-separated values -- op, time, pid, entry,
# parent, test, sha256 -- or nothing when there is none (no log, no match,
# or only older records). A record matches <path> when it names the same
# string, the same file, or the same basename in the same directory. Unknown keys in a record are ignored and a missing
# one reads as "-", so a record written by a newer writer still parses.
# Always returns 0.
# ---------------------------------------------------------------------------
_pgai_sentinel_provenance() {
    local root="$1" path="$2" since="${3:-0}"
    local log="${root}/${_PGAI_SENTINEL_WRITE_LOG}"
    [[ -f "${log}" && -r "${log}" ]] || return 0
    [[ "${since}" =~ ^[0-9]+$ ]] || since=0

    local found="" line field key value
    local r_path r_epoch r_op r_time r_pid r_entry r_parent r_test r_sha
    local -a fields
    while IFS= read -r line || [[ -n "${line}" ]]; do
        [[ -n "${line}" ]] || continue
        r_path="" r_epoch="" r_op="-" r_time="-" r_pid="-" r_entry="-"
        r_parent="-" r_test="-" r_sha="-"
        IFS=$'\t' read -r -a fields <<< "${line}"
        for field in "${fields[@]}"; do
            key="${field%%=*}"
            value="${field#*=}"
            key="${key//[[:space:]]/}"
            case "${key}" in
                path) r_path="${value}" ;;
                epoch) r_epoch="${value//[[:space:]]/}" ;;
                op) r_op="${value}" ;;
                time) r_time="${value}" ;;
                pid) r_pid="${value}" ;;
                entry) r_entry="${value}" ;;
                parent) r_parent="${value}" ;;
                test) r_test="${value}" ;;
                sha256) r_sha="${value}" ;;
            esac
        done
        [[ -n "${r_path}" && "${r_epoch}" =~ ^[0-9]+$ ]] || continue
        (( r_epoch >= since )) || continue
        if [[ "${r_path}" == "${path}" || "${r_path}" -ef "${path}" ]] \
            || [[ "${r_path##*/}" == "${path##*/}" && "${r_path%/*}" -ef "${path%/*}" ]]; then
            found="${r_op}"$'\t'"${r_time}"$'\t'"${r_pid}"$'\t'"${r_entry}"$'\t'"${r_parent}"$'\t'"${r_test:--}"$'\t'"${r_sha}"
        fi
    done < "${log}"
    [[ -n "${found}" ]] && printf '%s\n' "${found}"
    return 0
}

# ---------------------------------------------------------------------------
# pgai_sentinel_snapshot [root]
# Print the current sentinel set under root (see OUTPUT above).
# ---------------------------------------------------------------------------
pgai_sentinel_snapshot() {
    local root
    root="$(_pgai_sentinel_resolve_root "${1:-}")"
    if [[ -z "${root}" || ! -d "${root}" ]]; then
        return 0
    fi
    local lines=""
    local name scope
    local scopes=("${root}")
    if [[ -d "${root}/projects" ]]; then
        local project_dir
        for project_dir in "${root}/projects"/*/; do
            [[ -d "${project_dir}" ]] || continue
            scopes+=("${project_dir%/}")
        done
    fi
    for scope in "${scopes[@]}"; do
        for name in "${_PGAI_SENTINEL_BASENAMES[@]}"; do
            lines+="$(_pgai_sentinel_hash_line "${scope}/${name}")"
            [[ -n "${lines}" && "${lines: -1}" != $'\n' ]] && lines+=$'\n'
        done
    done
    if [[ -n "${lines}" ]]; then
        printf '%s' "${lines}" | sort
    fi
    return 0
}

# ---------------------------------------------------------------------------
# pgai_sentinel_leak_check [root] [presuite_snapshot_file]
# Compare the sentinel set now against the pre-suite snapshot; report every
# difference on stderr; return 1 if any, else 0. Without a snapshot file,
# every sentinel present now is reported as ADDED (nothing is known to have
# been there before) -- pass the pre-suite snapshot to make the check exact.
# Every difference is first looked up in the provenance log (see WHO WROTE
# IT above) and, when an operator or agent write explains it, narrated as
# ATTRIBUTED instead of counted.
# ---------------------------------------------------------------------------
pgai_sentinel_leak_check() {
    local root
    root="$(_pgai_sentinel_resolve_root "${1:-}")"
    local snapshot_file="${2:-}"

    if [[ -z "${root}" || ! -d "${root}" ]]; then
        echo "[pgai-sentinel] no live kanban root to guard (PGAI_AGENT_KANBAN_ROOT_PATH / KANBAN_ROOT unset or not a directory); nothing checked." >&2
        return 0
    fi

    local -A before=()
    # Provenance records older than the snapshot explain nothing about this
    # run; with no snapshot, every record is considered.
    local since=0
    if [[ -n "${snapshot_file}" ]]; then
        if [[ -f "${snapshot_file}" ]]; then
            since="$(stat -c %Y -- "${snapshot_file}" 2>/dev/null)" && true || true
            [[ "${since}" =~ ^[0-9]+$ ]] || since=0
            local _line _path _digest
            while IFS=$'\t' read -r _path _digest; do
                [[ -n "${_path}" ]] && before["${_path}"]="${_digest}"
            done < "${snapshot_file}"
        else
            echo "[pgai-sentinel] WARNING: pre-suite snapshot file not found (${snapshot_file}); treating as empty (every sentinel present now reads as ADDED)." >&2
        fi
    fi

    local -A now=()
    local current=""
    current="$(pgai_sentinel_snapshot "${root}")" && true || true
    if [[ -n "${current}" ]]; then
        local _line _path _digest
        while IFS=$'\t' read -r _path _digest; do
            [[ -n "${_path}" ]] && now["${_path}"]="${_digest}"
        done <<< "${current}"
    fi

    local findings=0 attributed=0
    local path kind record written_by
    local p_op p_time p_pid p_entry p_parent p_test p_sha
    for path in "${!now[@]}"; do
        if [[ -z "${before[${path}]+set}" ]]; then
            kind="added"
        elif [[ "${before[${path}]}" != "${now[${path}]}" ]]; then
            kind="changed"
        else
            continue
        fi
        record="$(_pgai_sentinel_provenance "${root}" "${path}" "${since}")" && true || true
        written_by=""
        if [[ -n "${record}" ]]; then
            IFS=$'\t' read -r p_op p_time p_pid p_entry p_parent p_test p_sha <<< "${record}"
            # A removal record explains nothing about a file that is present.
            [[ "${p_sha}" == "${_PGAI_SENTINEL_ABSENT_DIGEST}" ]] && record=""
        fi
        if [[ -n "${record}" ]]; then
            if [[ -z "${p_test}" || "${p_test}" == "-" ]]; then
                local drift=""
                if [[ -n "${p_sha}" && "${p_sha}" != "-" && "${p_sha}" != "${now[${path}]}" ]]; then
                    drift=" Its content has changed since that write."
                fi
                echo "[pgai-sentinel] ATTRIBUTED: ${path} -- ${kind} during the run by ${p_op} (pid ${p_pid} at ${p_time}; entry: ${p_entry}; parent: ${p_parent}), an operator or agent write, not a sentinel the suite left behind; not counted.${drift} Record: ${root}/${_PGAI_SENTINEL_WRITE_LOG}."
                attributed=$((attributed + 1))
                continue
            fi
            written_by=" Written by ${p_op} under test ${p_test} (pid ${p_pid} at ${p_time})."
        fi
        if [[ "${kind}" == "added" ]]; then
            echo "[pgai-sentinel] ADDED:   ${path} -- a sentinel the suite left behind on the live root; a HALT/HALT-AFTER pauses a real lane, a DEFER-LOW-BUGS changes what discovery bundles.${written_by}" >&2
        else
            echo "[pgai-sentinel] CHANGED: ${path} -- a live sentinel's content differs from the pre-suite snapshot.${written_by}" >&2
        fi
        findings=$((findings + 1))
    done
    for path in "${!before[@]}"; do
        [[ -z "${now[${path}]+set}" ]] || continue
        record="$(_pgai_sentinel_provenance "${root}" "${path}" "${since}")" && true || true
        written_by=""
        if [[ -n "${record}" ]]; then
            IFS=$'\t' read -r p_op p_time p_pid p_entry p_parent p_test p_sha <<< "${record}"
            # Only a removal record explains a sentinel that is gone.
            [[ "${p_sha}" == "${_PGAI_SENTINEL_ABSENT_DIGEST}" ]] || record=""
        fi
        if [[ -n "${record}" ]]; then
            if [[ -z "${p_test}" || "${p_test}" == "-" ]]; then
                echo "[pgai-sentinel] ATTRIBUTED: ${path} -- removed during the run by ${p_op} (pid ${p_pid} at ${p_time}; entry: ${p_entry}; parent: ${p_parent}), an operator or agent write, not a sentinel the suite removed; not counted. Record: ${root}/${_PGAI_SENTINEL_WRITE_LOG}."
                attributed=$((attributed + 1))
                continue
            fi
            written_by=" Removed by ${p_op} under test ${p_test} (pid ${p_pid} at ${p_time})."
        fi
        echo "[pgai-sentinel] REMOVED: ${path} -- a sentinel present before the suite is gone; only the operator removes a HALT file.${written_by}" >&2
        findings=$((findings + 1))
    done

    if (( findings > 0 )); then
        echo "[pgai-sentinel] ${findings} sentinel difference(s) on the live kanban root ${root}. Not repaired: inspect each path above, restore or remove it by hand, and find the fixture that resolved the live root instead of its synthetic board." >&2
        return 1
    fi
    if (( attributed > 0 )); then
        echo "[pgai-sentinel] live kanban root ${root}: HALT / HALT-AFTER / DEFER-LOW-BUGS set unchanged by the suite (${attributed} change(s) above attributed to operator or agent writes)."
    else
        echo "[pgai-sentinel] live kanban root ${root}: HALT / HALT-AFTER / DEFER-LOW-BUGS set unchanged by the suite."
    fi
    return 0
}
