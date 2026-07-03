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
#      (directories whose name matches CLAUDE-PM-*).
#   2. For each PM task, read its README.md ## Inputs section.
#   3. Check each input line for:
#      a. A path ending in /README.md (or == README.md), OR
#      b. A path containing /templates/ or starting with templates/
#   4. If a match is found AND the task's status.md shows State: BACKLOG or WORKING:
#      a. Allocate the next BUG-NNNN number from bugs/
#      b. File a bug report describing the mis-queued PM task
#      c. Mark the offending PM task WONT-DO in its status.md (with a Blocked Reason)
#      d. Log the action via overwatch_log_action
#
# This script does NOT delete the offending PM task folder. It only:
#   - Files a bug report in bugs/
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

# ---------------------------------------------------------------------------
# _crb_resolve_env
# Resolve KANBAN_ROOT and OVERWATCH_PROJECT from the environment.
# Returns 0 on success, 1 on failure.
# ---------------------------------------------------------------------------
_crb_resolve_env() {
    if [[ -z "${KANBAN_ROOT:-}" ]]; then
        KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-${HOME}/pgai_agent_kanban}"
    fi
    if [[ ! -d "${KANBAN_ROOT}" ]]; then
        echo "check-readme-bundled: KANBAN_ROOT does not exist: ${KANBAN_ROOT}" >&2
        return 1
    fi

    if [[ -z "${OVERWATCH_PROJECT:-}" ]]; then
        # Resolve from projects.cfg — never silently fall back to first project.
        # On a multi-project install the caller MUST set OVERWATCH_PROJECT; this
        # path is only safe for single-project installs.
        local _all_projects=""
        if declare -f projects_cfg_list >/dev/null 2>&1; then
            _all_projects="$(projects_cfg_list 2>/dev/null)"
        else
            local _cfg="${KANBAN_ROOT}/projects.cfg"
            _all_projects="$(awk '/^\[project:[a-zA-Z0-9_-]+\]/{match($0,/\[project:([a-zA-Z0-9_-]+)\]/,a);print a[1]}' "$_cfg" 2>/dev/null)"
        fi
        local _project_count
        _project_count="$(echo "${_all_projects}" | grep -c '[^[:space:]]' 2>/dev/null || echo 0)"
        if [[ "${_project_count}" -gt 1 ]]; then
            echo "check-readme-bundled: ERROR: OVERWATCH_PROJECT is not set and multiple projects are registered." >&2
            echo "  Set OVERWATCH_PROJECT to the target project name before running this check." >&2
            echo "  Registered projects: $(echo "${_all_projects}" | tr '\n' ' ')" >&2
            return 1
        fi
        OVERWATCH_PROJECT="$(echo "${_all_projects}" | grep '[^[:space:]]' | head -n1)"
        if [[ -z "${OVERWATCH_PROJECT:-}" ]]; then
            echo "check-readme-bundled: ERROR: no project specified and none resolvable from projects.cfg" >&2
            echo "  Set OVERWATCH_PROJECT or register a project in ${KANBAN_ROOT}/projects.cfg" >&2
            return 1
        fi
    fi
    return 0
}

# ---------------------------------------------------------------------------
# _crb_next_bug_number <bugs_dir>
# Determine the next BUG-NNNN sequence number by scanning existing BUG-NNNN
# files in <bugs_dir>. Echoes a zero-padded 4-digit number.
# ---------------------------------------------------------------------------
_crb_next_bug_number() {
    local bugs_dir="$1"
    local highest=0
    local num

    if [[ -d "${bugs_dir}" ]]; then
        while IFS= read -r f; do
            num="$(basename "${f}" | grep -oE '^BUG-[0-9]+' | grep -oE '[0-9]+' | head -n1)"
            if [[ -n "${num}" ]] && (( 10#${num} > highest )); then
                highest=$(( 10#${num} ))
            fi
        done < <(find "${bugs_dir}" -maxdepth 1 -name 'BUG-[0-9]*' -type f 2>/dev/null)
    fi

    printf '%04d' $(( highest + 1 ))
}

# ---------------------------------------------------------------------------
# _crb_extract_inputs_section <readme_file>
# Echo the contents of the ## Inputs section from a task README.md.
# Echoes one line per item. May be empty if no Inputs section exists.
# ---------------------------------------------------------------------------
_crb_extract_inputs_section() {
    local readme_file="$1"
    if [[ ! -f "${readme_file}" ]]; then
        return 0
    fi

    awk '
        /^## Inputs$/ { found=1; next }
        found && /^## / { exit }
        found { print }
    ' "${readme_file}"
}

# ---------------------------------------------------------------------------
# _crb_extract_task_state <status_file>
# Echo the State field from a task status.md, upper-cased.
# Echoes "UNKNOWN" if not found or status file missing.
# ---------------------------------------------------------------------------
_crb_extract_task_state() {
    local status_file="$1"
    if [[ ! -f "${status_file}" ]]; then
        echo "UNKNOWN"
        return 0
    fi

    local state
    state="$(awk '
        /^## State$/ { found=1; next }
        found && /^## / { exit }
        found && /[^[:space:]]/ { print; exit }
    ' "${status_file}" | tr -d '[:space:]' | tr '[:lower:]' '[:upper:]')"

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
# _crb_do_remediate
# Inner function invoked via overwatch_halt_first_fix for one offending PM task.
# Reads from environment:
#   _CRB_TASK_ID       — PM task ID
#   _CRB_TASK_DIR      — absolute path to PM task directory
#   _CRB_BAD_INPUT     — the offending input path
#   _CRB_BUGS_DIR      — path to bugs/
#   _CRB_BUG_NUM       — pre-allocated BUG number (zero-padded 4-digit string)
#
# Actions:
#   1. Backup the PM task's status.md via overwatch_backup_file
#   2. Set PM task status.md State -> WONT-DO with Blocked Reason
#   3. Create bugs/BUG-NNNN-<slug>.md describing the problem
#   4. Log action via overwatch_log_action
# ---------------------------------------------------------------------------
_crb_do_remediate() {
    local task_id="${_CRB_TASK_ID}"
    local task_dir="${_CRB_TASK_DIR}"
    local bad_input="${_CRB_BAD_INPUT}"
    local bugs_dir="${_CRB_BUGS_DIR}"
    local bug_num="${_CRB_BUG_NUM}"

    local task_status="${task_dir}/status.md"
    local bug_slug="pm-task-readme-or-template-as-requirements-input"
    local bug_file="${bugs_dir}/BUG-${bug_num}-${bug_slug}.md"
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

    # Create the bug report
    cat > "${bug_file}" <<EOF
# BUG-${bug_num}: PM task queued with ${input_category} as requirements input

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

    if [[ ! -f "${bug_file}" ]]; then
        echo "check-readme-bundled: failed to write bug file: ${bug_file}" >&2
        return 1
    fi

    # Update PM task status.md: State -> WONT-DO, add Blocked Reason
    # Use Python for atomic rewrite (same pattern as other checks)
    local update_exit=0
    python3 - "${task_status}" "${task_id}" "${bad_input}" "${bug_num}" "${bug_file}" "${detected_at}" <<'PY' || update_exit=$?
import pathlib, re, sys

status_path = pathlib.Path(sys.argv[1])
task_id     = sys.argv[2]
bad_input   = sys.argv[3]
bug_num     = sys.argv[4]
bug_file    = sys.argv[5]
detected_at = sys.argv[6]

try:
    text = status_path.read_text()
except OSError as e:
    raise SystemExit(f"Cannot read status file: {e}")

new_summary = (
    f"PM task marked WONT-DO by OVERWATCH check-readme-bundled at {detected_at}: "
    f"input path points at a README.md or templates/ file, not a valid requirements bundle. "
    f"Bug BUG-{bug_num} filed. Offending input: {bad_input}"
)

blocked_reason = (
    f"Input path points at a README.md or templates/ file: {bad_input}. "
    f"This is not a valid requirements bundle. "
    f"Bug BUG-{bug_num} filed at {bug_file}."
)

def replace_block(text, heading, new_body):
    pattern = rf'(^## {re.escape(heading)}\s*\n)(.*?)(\n+##|\Z)'
    text_new, n = re.subn(
        pattern,
        lambda m: m.group(1) + new_body.strip() + "\n" + (m.group(3) if m.group(3) else ''),
        text,
        flags=re.S | re.M,
    )
    if n == 0:
        # Field absent — skip silently
        return text
    return text_new

new_text = replace_block(text,     "State",          "WONT-DO")
new_text = replace_block(new_text, "Summary",        new_summary)
new_text = replace_block(new_text, "Needs Human",    "no")

# Inject Blocked Reason: insert after "## Blockers" if it exists,
# otherwise append at end. If "## Blocked Reason" already exists, replace it.
if re.search(r'^## Blocked Reason\s*$', new_text, flags=re.M):
    new_text = replace_block(new_text, "Blocked Reason", blocked_reason)
elif re.search(r'^## Blockers\s*$', new_text, flags=re.M):
    new_text = re.sub(
        r'(^## Blockers\s*\n.*?)(\n+##|\Z)',
        lambda m: m.group(1) + f"\n\n## Blocked Reason\n{blocked_reason.strip()}\n" + (m.group(2) if m.group(2) else ''),
        new_text,
        flags=re.S | re.M,
        count=1,
    )
else:
    # Append Blocked Reason at end
    new_text = new_text.rstrip() + f"\n\n## Blocked Reason\n{blocked_reason.strip()}\n"

# Normalize: collapse 3+ consecutive blank lines to 2
new_text = re.sub(r'\n\n\n+', '\n\n', new_text)

try:
    status_path.write_text(new_text)
except OSError as e:
    raise SystemExit(f"Failed to write status file: {e}")
PY

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
        "PM task ${task_id} marked WONT-DO; bad input: ${bad_input}; bug filed: ${bug_file}" \
    || true

    echo "check-readme-bundled: remediated ${task_id}: marked WONT-DO; filed BUG-${bug_num} at ${bug_file}" >&2
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

    # Source overwatch_protocol.sh if not already loaded
    if ! declare -f overwatch_log_action >/dev/null 2>&1; then
        local lib_dir
        lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)" || {
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

    # Scan PM task directories (name prefix: CLAUDE-PM-)
    while IFS= read -r task_dir; do
        [[ -d "${task_dir}" ]] || continue

        task_id="$(basename "${task_dir}")"
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

        # Allocate next bug number (recalculate each iteration for accuracy)
        local bug_num
        bug_num="$(_crb_next_bug_number "${bugs_dir}")"

        # Live mode: remediate via overwatch_halt_first_fix
        export _CRB_TASK_ID="${task_id}"
        export _CRB_TASK_DIR="${task_dir}"
        export _CRB_BAD_INPUT="${bad_input}"
        export _CRB_BUGS_DIR="${bugs_dir}"
        export _CRB_BUG_NUM="${bug_num}"

        local fix_exit=0
        overwatch_halt_first_fix _crb_do_remediate || fix_exit=$?

        unset _CRB_TASK_ID _CRB_TASK_DIR _CRB_BAD_INPUT _CRB_BUGS_DIR _CRB_BUG_NUM

        if (( fix_exit == 3 )); then
            echo "check-readme-bundled: HALT_OVERWATCH guard tripped; aborting" >&2
            return 0
        elif (( fix_exit == 4 )); then
            echo "check-readme-bundled: per-repo flock contended; aborting" >&2
            return 0
        elif (( fix_exit != 0 )); then
            echo "check-readme-bundled: remediation failed for ${task_id} (exit ${fix_exit}); continuing" >&2
            continue
        fi

        remediated=$(( remediated + 1 ))

    done < <(find "${tasks_root}" -maxdepth 1 -type d -name 'CLAUDE-PM-*' 2>/dev/null | sort)

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
