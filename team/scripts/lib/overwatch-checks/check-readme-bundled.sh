#!/usr/bin/env bash
# team/scripts/lib/overwatch-checks/check-readme-bundled.sh
#
# OVERWATCH detection module: detect PM tasks whose ## Inputs section contains
# a path pointing at README.md or a templates/<...> file, which indicates
# the discovery pipeline incorrectly bundled a directory-level documentation
# file as a requirements document.
#
# Example: pm-agent.sh selects requirements/README.md as a requirements input,
# creating a malformed PM task that blocks itself.
#
# Detection logic:
#   1. Scan $KANBAN_ROOT/projects/$OVERWATCH_PROJECT/tasks/ for PM task folders
#      (directories named as a task ID whose role is PM -- PM-YYYYMMDD-NNN-slug,
#      or the legacy CLAUDE-PM-... form -- per task_ids.sh's kanban_task_id_role).
#   2. For each PM task, read its README.md ## Inputs section.
#   3. Check each input line for:
#      a. A path ending in /README.md (or == README.md), OR
#      b. A path containing /templates/ or starting with templates/
#   4. If a match is found AND the task's status.md shows State: BACKLOG or WORKING:
#      a. File a bug report describing the mis-queued PM task through intake
#         (overwatch_deposit_bug), which claims its BUG-NNNN number; a report
#         intake refuses as the same defect as a live bug names that bug
#         instead, and no second bug is filed
#      b. Mark the offending PM task WONT-DO in its status.md (with a Blocked
#         Reason naming the filed or already-live bug)
#      c. Log the action via overwatch_log_action
#
# This script does NOT delete the offending PM task folder. It only:
#   - Files a bug report through intake
#   - Sets the PM task status to WONT-DO
#
# This script is both:
#   - Sourceable (for the OVERWATCH driver): source this file and call
#     overwatch_check_readme_bundled [--dry-run]
#   - Directly invokable: bash check-readme-bundled.sh [--dry-run]
#
# Required environment variables (when sourced by OVERWATCH driver):
#   KANBAN_ROOT      — absolute path to the kanban root
#   OVERWATCH_PROJECT — project name (e.g. "pgai-agent-kanban")
#
# When invoked directly, KANBAN_ROOT defaults to:
#   ${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}
# OVERWATCH_PROJECT defaults to "pgai-agent-kanban".
#
# Usage:
#   bash check-readme-bundled.sh [--dry-run]
#
# Exit codes:
#   0 — completed successfully (no bad inputs found, or bad inputs remediated)
#   1 — internal error (missing dependencies, unreadable state dir, etc.)
#
# --dry-run: scans and logs findings but does NOT modify task status or file bugs.

# This file's directory, and the lib directory above it, as absolute paths
# resolved once at load: no function depends on the caller's current
# directory or on whether this file was named by a relative path.
_CRB_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)" || _CRB_SELF_DIR=""
_CRB_LIB_DIR="${_CRB_SELF_DIR%/*}"

# overwatch_registered_projects: the shared projects.cfg reader;
# overwatch_deposit_bug: filing through intake.
# shellcheck source=../overwatch_config.sh
source "$(dirname "${BASH_SOURCE[0]}")/../overwatch_config.sh"

# ---------------------------------------------------------------------------
# _crb_resolve_env
# Resolve KANBAN_ROOT and OVERWATCH_PROJECT from the environment.
# Returns 0 on success, 1 on failure.
# ---------------------------------------------------------------------------
_crb_resolve_env() {
    overwatch_resolve_check_project check-readme-bundled || return 1
    return 0
}

# ---------------------------------------------------------------------------
# _crb_load_md_field
# Make read_md_field (../md_field.sh, the one bash section reader) available,
# sourcing it from this file's own lib directory on first use unless the
# caller already defines it. A pp_run_ops the caller defined before the
# load (its own copy, or a test wrapper) stays the active one: md_field.sh
# leaves it in place, and it is also saved and put back here. Returns 0
# when read_md_field is defined.
# ---------------------------------------------------------------------------
_crb_load_md_field() {
    declare -F read_md_field >/dev/null 2>&1 && return 0
    local runner=""
    runner="$(declare -f pp_run_ops 2>/dev/null)" || runner=""
    # shellcheck source=../md_field.sh
    source "${_CRB_LIB_DIR}/md_field.sh" || return 1
    [[ -n "${runner}" ]] && eval "${runner}"
    declare -F read_md_field >/dev/null 2>&1
}

# ---------------------------------------------------------------------------
# _crb_extract_inputs_section <readme_file>
# Echo the contents of the ## Inputs section from a task README.md, one line
# per item, with the blank lines around the body removed. Empty when the
# README or its Inputs section is missing. The section is read by
# read_md_field: the next "##" heading that is not "###" ends it.
# ---------------------------------------------------------------------------
_crb_extract_inputs_section() {
    local readme_file="$1"
    if [[ ! -f "${readme_file}" ]]; then
        return 0
    fi

    _crb_load_md_field || return 0
    read_md_field "${readme_file}" Inputs 2>/dev/null || true
}

# ---------------------------------------------------------------------------
# _crb_extract_task_state <status_file>
# Echo the State field from a task status.md -- the first non-blank line of
# the section, read by read_md_field -- with whitespace removed, upper-cased.
# Echoes "UNKNOWN" if not found or status file missing.
# ---------------------------------------------------------------------------
_crb_extract_task_state() {
    local status_file="$1"
    if [[ ! -f "${status_file}" ]]; then
        echo "UNKNOWN"
        return 0
    fi

    local state=""
    if _crb_load_md_field; then
        state="$(read_md_field "${status_file}" State 2>/dev/null)" || state=""
    fi
    state="${state%%$'\n'*}"
    state="$(printf '%s' "${state}" | tr -d '[:space:]' | tr '[:lower:]' '[:upper:]')"

    if [[ -z "${state}" ]]; then
        echo "UNKNOWN"
    else
        echo "${state}"
    fi
}

# ---------------------------------------------------------------------------
# _crb_input_is_bad <input_line>
# Returns 0 if the input line contains a bad path (README.md or templates/).
# Returns 1 otherwise.
# ---------------------------------------------------------------------------
_crb_input_is_bad() {
    local line="$1"

    # Strip leading "- " and whitespace from the line
    local path
    path="$(echo "${line}" | sed 's/^[[:space:]]*-\?[[:space:]]*//')"

    # Empty path: not a bad input
    [[ -z "${path}" ]] && return 1

    # Pattern 1: path ends with /README.md or IS exactly README.md
    if [[ "${path}" =~ /README\.md$ ]] || [[ "${path}" == "README.md" ]]; then
        return 0
    fi

    # Pattern 2: path contains /templates/ or begins with templates/
    if [[ "${path}" =~ /templates/ ]] || [[ "${path}" =~ ^templates/ ]]; then
        return 0
    fi

    return 1
}

# ---------------------------------------------------------------------------
# _crb_mark_status_wont_do <status_file> <bad_input> <bug_num> <bug_file> <detected_at>
# Rewrite the PM task's status.md in place to WONT-DO: State, Summary,
# Needs Human and Blocked Reason (added after ## Blockers when absent), as
# pgai_agent_kanban.overwatch.pm_task_wont_do's docstring describes.
# Exits 0 when rewritten; exits 1 with one stderr line when the file cannot
# be read (left unchanged) or written. Runs in a subshell that loads this
# file's own lib directory's pp_run_ops, so a caller's definition never
# answers for the status file.
# ---------------------------------------------------------------------------
_crb_mark_status_wont_do() (
    # shellcheck source=../pp_run_ops.sh
    source "${_CRB_LIB_DIR}/pp_run_ops.sh" || exit 1
    pp_run_ops pgai_agent_kanban.overwatch.pm_task_wont_do -- "$1" "$2" "$3" "$4" "$5"
)

# ---------------------------------------------------------------------------
# _crb_do_remediate
# Inner function invoked via overwatch_halt_first_fix for one offending PM task.
# Reads from environment:
#   _CRB_TASK_ID       — PM task ID
#   _CRB_TASK_DIR      — absolute path to PM task directory
#   _CRB_BAD_INPUT     — the offending input path
#   _CRB_PROJECT       — the project name the bug is filed for
#
# Actions:
#   1. Backup the PM task's status.md via overwatch_backup_file
#   2. File a bug describing the problem through intake
#      (overwatch_deposit_bug); a same-defect refusal names the live bug
#   3. Set PM task status.md State -> WONT-DO with Blocked Reason naming
#      that bug
#   4. Log action via overwatch_log_action
# ---------------------------------------------------------------------------
_crb_do_remediate() {
    local task_id="${_CRB_TASK_ID}"
    local task_dir="${_CRB_TASK_DIR}"
    local bad_input="${_CRB_BAD_INPUT}"
    local project="${_CRB_PROJECT}"

    local task_status="${task_dir}/status.md"
    local bug_slug="pm-task-readme-or-template-as-requirements-input"
    local detected_at
    detected_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

    # Backup the status.md before modification
    local bpath
    bpath="$(overwatch_backup_file "${task_status}")" || {
        echo "check-readme-bundled: backup failed for ${task_status}" >&2
        return 1
    }

    # Determine the bad input category for the bug report
    local input_category
    if [[ "${bad_input}" =~ /README\.md$ ]] || [[ "${bad_input}" == "README.md" ]]; then
        input_category="README.md (directory documentation file)"
    else
        input_category="templates/ path (template/placeholder file)"
    fi

    # File the bug report through intake
    local bug_file="" deposit_rc=0 filed_note
    bug_file="$(overwatch_deposit_bug check-readme-bundled "${project}" "${bug_slug}" \
        "PM task queued with ${input_category} as requirements input" <<EOF
**Filed By:** OVERWATCH check-readme-bundled
**Date:** ${detected_at}
**Offending Task:** ${task_id}

## Status
open

## Symptom

PM task ${task_id} was queued with an input path pointing at a
${input_category}, not a valid requirements bundle file.

Offending input line:
  ${bad_input}

This is the mis-selection scenario: pm-agent.sh's file selection logic
incorrectly identified a directory-level documentation file (README.md or
a templates/ file) as a requirements document.

## Root Cause Hypothesis

pm-agent.sh discovery logic uses a glob that matches README.md or
templates/ entries in the requirements directory. The selection should
filter by strict bundle filename patterns only:
  - v*.md  (version bundles)
  - PRIORITY-*.md  (priority bundles)
  - BUG-*.md  (bug bundles)

README.md, templates/, and any other directory-level files should be
explicitly excluded.

## Files Involved

- pm-agent.sh (or the pm-agent discovery pipeline that queues PM tasks)
- Offending PM task: ${task_dir}/README.md

## Fix

Update pm-agent.sh to filter requirements/ contents by strict pattern.
Skip README.md, templates/, and any non-bundle files.

## Acceptance

- [ ] pm-agent.sh ignores requirements/README.md when scanning for input
- [ ] pm-agent.sh ignores requirements/templates/ directory
- [ ] pm-agent.sh only considers files matching bundle filename pattern
- [ ] No new "decompose-readme" PM tasks appear after the fix

## Severity

medium — produces malformed PM tasks that block themselves or are marked
WONT-DO. Adds noise and requires automated cleanup. Does not damage the
release chain directly but wastes PM agent runs.

## Detected By

OVERWATCH check-readme-bundled at ${detected_at}
Offending task: ${task_id}
Bad input path: ${bad_input}
Status backup: ${bpath}
EOF
)" || deposit_rc=$?

    case "${deposit_rc}" in
        0) filed_note="bug filed: ${bug_file}" ;;
        2) filed_note="already filed as ${bug_file}" ;;
        *)
            echo "check-readme-bundled: bug deposit failed for ${task_id}; status left unchanged" >&2
            return 1
            ;;
    esac
    # The number the status rewrite names: from the filed or live bug's name.
    local bug_num=""
    [[ "${bug_file##*/}" =~ ^BUG-([0-9]+) ]] && bug_num="${BASH_REMATCH[1]}"
    if [[ -z "${bug_num}" ]]; then
        echo "check-readme-bundled: intake named no bug file for ${task_id} (${bug_file:-nothing}); status left unchanged" >&2
        return 1
    fi

    # Update PM task status.md: State -> WONT-DO, add Blocked Reason
    local update_exit=0
    _crb_mark_status_wont_do "${task_status}" "${bad_input}" "${bug_num}" "${bug_file}" "${detected_at}" \
        || update_exit=$?

    if (( update_exit != 0 )); then
        echo "check-readme-bundled: status update failed for ${task_id}" >&2
        return 1
    fi

    # Log the action
    overwatch_log_action \
        "check-readme-bundled" \
        "${task_id}" \
        "pm-task-readme-input-remediated" \
        "${bpath}" \
        "PM task ${task_id} marked WONT-DO; bad input: ${bad_input}; ${filed_note}" \
    || true

    echo "check-readme-bundled: remediated ${task_id}: marked WONT-DO; ${filed_note}" >&2
    return 0
}

# ---------------------------------------------------------------------------
# overwatch_check_readme_bundled [--dry-run]
# Main detection function.
# Returns 0 on success, 1 on internal error.
# ---------------------------------------------------------------------------
overwatch_check_readme_bundled() {
    local dry_run=0
    local arg
    for arg in "$@"; do
        if [[ "${arg}" == "--dry-run" ]]; then
            dry_run=1
        fi
    done

    _crb_resolve_env || return 1

    local project_name="${OVERWATCH_PROJECT}"
    local project_root="${KANBAN_ROOT}/projects/${project_name}"
    local tasks_root="${project_root}/tasks"
    local bugs_dir="${project_root}/bugs"

    if [[ ! -d "${project_root}" ]]; then
        echo "check-readme-bundled: project root does not exist: ${project_root}" >&2
        return 1
    fi

    # The shared task-ID shape picks out PM task folders.
    if ! declare -f kanban_task_id_role >/dev/null 2>&1; then
        local ids_lib
        ids_lib="${_CRB_LIB_DIR}/task_ids.sh"
        if [[ ! -f "${ids_lib}" ]]; then
            echo "check-readme-bundled: task_ids.sh not found at ${ids_lib}" >&2
            return 1
        fi
        # shellcheck source=../task_ids.sh
        source "${ids_lib}"
    fi

    # Source overwatch_protocol.sh if not already loaded
    if ! declare -f overwatch_log_action >/dev/null 2>&1; then
        local lib_dir
        lib_dir="${_CRB_SELF_DIR}"
        [[ -n "${lib_dir}" ]] || {
            echo "check-readme-bundled: cannot resolve lib dir" >&2
            return 1
        }
        if [[ -f "${lib_dir}/../overwatch_protocol.sh" ]]; then
            # shellcheck source=/dev/null
            source "${lib_dir}/../overwatch_protocol.sh"
        else
            echo "check-readme-bundled: overwatch_protocol.sh not found relative to ${lib_dir}" >&2
            return 1
        fi
    fi

    if (( dry_run == 0 )); then
        # Verify overwatch state dir exists for live mode
        local state_dir="${KANBAN_ROOT}/projects/${project_name}/overwatch"
        if [[ ! -d "${state_dir}" ]]; then
            echo "check-readme-bundled: overwatch state dir missing: ${state_dir}" >&2
            return 1
        fi

        # Ensure bugs dir exists; if not, log and skip (non-fatal)
        if [[ ! -d "${bugs_dir}" ]]; then
            echo "check-readme-bundled: bugs dir does not exist: ${bugs_dir}; cannot file bugs; skipping" >&2
            return 0
        fi
    fi

    if [[ ! -d "${tasks_root}" ]]; then
        echo "check-readme-bundled: tasks root does not exist: ${tasks_root}; nothing to scan" >&2
        return 0
    fi

    echo "check-readme-bundled: scanning PM tasks for README.md or templates/ inputs in ${tasks_root}" >&2

    local remediated=0
    local task_dir task_id task_readme task_status

    # Scan PM task directories (a task-ID name whose role is PM)
    while IFS= read -r task_dir; do
        [[ -d "${task_dir}" ]] || continue

        task_id="$(basename "${task_dir}")"
        [[ "$(kanban_task_id_role "${task_id}")" == "PM" ]] || continue
        task_readme="${task_dir}/README.md"
        task_status="${task_dir}/status.md"

        # Must have both README.md and status.md
        if [[ ! -f "${task_readme}" ]] || [[ ! -f "${task_status}" ]]; then
            continue
        fi

        # Only act on tasks in BACKLOG or WORKING state (not yet completed)
        local state
        state="$(_crb_extract_task_state "${task_status}")"
        if [[ "${state}" != "BACKLOG" && "${state}" != "WORKING" ]]; then
            # Task is already done/blocked/wont-do — skip
            continue
        fi

        # Extract the ## Inputs section
        local inputs_text
        inputs_text="$(_crb_extract_inputs_section "${task_readme}")"

        if [[ -z "${inputs_text}" ]]; then
            continue
        fi

        # Check each input line for bad paths
        local bad_input=""
        local line
        while IFS= read -r line; do
            [[ -z "${line}" ]] && continue
            # Skip comment lines
            [[ "${line}" =~ ^[[:space:]]*# ]] && continue

            if _crb_input_is_bad "${line}"; then
                # Extract just the path (strip leading "- " list marker)
                bad_input="$(echo "${line}" | sed 's/^[[:space:]]*-\?[[:space:]]*//')"
                break
            fi
        done <<< "${inputs_text}"

        [[ -z "${bad_input}" ]] && continue

        echo "check-readme-bundled: ${task_id}: bad input detected (state=${state}): ${bad_input}" >&2

        if (( dry_run == 1 )); then
            echo "check-readme-bundled: [dry-run] would mark ${task_id} WONT-DO and file a bug for: ${bad_input}" >&2
            overwatch_log_action \
                "check-readme-bundled" \
                "${task_id}" \
                "dry-run-readme-input-detected" \
                "none" \
                "PM task has README.md or templates/ input: ${bad_input}; state=${state}; dry-run, no action taken" \
            2>/dev/null || true
            continue
        fi

        # Live mode: remediate via overwatch_halt_first_fix
        export _CRB_TASK_ID="${task_id}"
        export _CRB_TASK_DIR="${task_dir}"
        export _CRB_BAD_INPUT="${bad_input}"
        export _CRB_PROJECT="${project_name}"

        local fix_exit=0
        overwatch_halt_first_fix _crb_do_remediate || fix_exit=$?

        unset _CRB_TASK_ID _CRB_TASK_DIR _CRB_BAD_INPUT _CRB_PROJECT

        if (( fix_exit == 3 )); then
            echo "check-readme-bundled: HALT_OVERWATCH guard tripped; aborting" >&2
            return 0
        elif (( fix_exit == 4 )); then
            echo "check-readme-bundled: per-repo flock contended; aborting" >&2
            return 0
        elif (( fix_exit == 5 )); then
            echo "check-readme-bundled: fix deferred: HALT present; aborting" >&2
            return 0
        elif (( fix_exit != 0 )); then
            echo "check-readme-bundled: remediation failed for ${task_id} (exit ${fix_exit}); continuing" >&2
            continue
        fi

        remediated=$(( remediated + 1 ))

    done < <(find "${tasks_root}" -maxdepth 1 -mindepth 1 -type d 2>/dev/null | sort)

    echo "check-readme-bundled: complete; remediated ${remediated} PM task(s)" >&2
    return 0
}

# ---------------------------------------------------------------------------
# Standalone invocation entry point.
# When this script is executed directly (not sourced), call the main function.
# ---------------------------------------------------------------------------
if [[ "${BASH_SOURCE[0]:-}" == "${0}" ]]; then
    overwatch_check_readme_bundled "$@"
    exit $?
fi
