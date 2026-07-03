#!/usr/bin/env bash
# team/scripts/lib/overwatch-checks/check-blocked-tasks.sh
#
# OVERWATCH detection module: scan all per-agent task queues for BLOCKED tasks
# that can be auto-promoted to BACKLOG because their blocker condition has cleared.
#
# Promotion criteria (all must hold):
#   1. Queue entry is marked [B] (BLOCKED), AND
#   2. task status.md confirms State: BLOCKED, AND
#   3. Needs Human field in status.md is NOT "yes", AND
#   4. Blocked Reason field mentions "Active RC" (case-insensitive), AND
#   5. Current release-state.md Active RC is "none".
#
# Tasks that do not satisfy all criteria are skipped (not an error).
#
# When a task qualifies, this script:
#   1. Backs up the task's status.md and the queue file via overwatch_backup_file
#   2. Atomically updates the queue marker [B] -> [ ] and resets status.md
#      (State -> BACKLOG, Blockers/Blocked By Agent/Blocked Reason/Needs Human cleared)
#   3. Logs the action via overwatch_log_action
#
# This script is both:
#   - Sourceable (for the OVERWATCH driver): source this file and call
#     overwatch_check_blocked_tasks [--dry-run]
#   - Directly invokable: bash check-blocked-tasks.sh [--dry-run]
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
#   bash check-blocked-tasks.sh [--dry-run]
#
# Exit codes:
#   0 — completed successfully
#   1 — internal error (missing dependencies, unreadable state dir, etc.)
#
# --dry-run: scans and logs findings but does NOT modify any files.

# ---------------------------------------------------------------------------
# _cbt_resolve_env
# Resolve KANBAN_ROOT and OVERWATCH_PROJECT from the environment.
# Returns 0 on success, 1 on failure.
# ---------------------------------------------------------------------------
_cbt_resolve_env() {
    if [[ -z "${KANBAN_ROOT:-}" ]]; then
        KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-${HOME}/pgai_agent_kanban}"
    fi
    if [[ ! -d "${KANBAN_ROOT}" ]]; then
        echo "check-blocked-tasks: KANBAN_ROOT does not exist: ${KANBAN_ROOT}" >&2
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
            echo "check-blocked-tasks: ERROR: OVERWATCH_PROJECT is not set and multiple projects are registered." >&2
            echo "  Set OVERWATCH_PROJECT to the target project name before running this check." >&2
            echo "  Registered projects: $(echo "${_all_projects}" | tr '\n' ' ')" >&2
            return 1
        fi
        OVERWATCH_PROJECT="$(echo "${_all_projects}" | grep '[^[:space:]]' | head -n1)"
        if [[ -z "${OVERWATCH_PROJECT:-}" ]]; then
            echo "check-blocked-tasks: ERROR: no project specified and none resolvable from projects.cfg" >&2
            echo "  Set OVERWATCH_PROJECT or register a project in ${KANBAN_ROOT}/projects.cfg" >&2
            return 1
        fi
    fi
    return 0
}

# ---------------------------------------------------------------------------
# _cbt_read_release_state_active_rc <release_state_file>
# Echo the current Active RC value, or "none" if absent/empty.
# ---------------------------------------------------------------------------
_cbt_read_release_state_active_rc() {
    local rs_file="$1"
    if [[ ! -f "${rs_file}" ]]; then
        echo "none"
        return 0
    fi
    local val
    val="$(awk '/^## Active RC$/{found=1; next} found && /^[[:space:]]*$/{next} found{print; exit}' "${rs_file}" \
        | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
    if [[ -z "${val}" ]]; then
        echo "none"
    else
        echo "${val}"
    fi
}

# ---------------------------------------------------------------------------
# _cbt_extract_field <status_file> <field_name>
# Extract the body of a ## Field section from a status.md file.
# Echoes the trimmed content of the field, or empty string if not found.
# ---------------------------------------------------------------------------
_cbt_extract_field() {
    local status_file="$1"
    local field_name="$2"

    if [[ ! -f "${status_file}" ]]; then
        echo ""
        return 0
    fi

    # Use awk to extract content between this ## heading and the next ##
    awk -v field="${field_name}" '
        $0 ~ "^## " field "$" { found=1; next }
        found && /^## / { exit }
        found { lines = lines $0 "\n" }
        END {
            # Trim leading/trailing whitespace from the accumulated block
            gsub(/^[[:space:]]+/, "", lines)
            gsub(/[[:space:]]+$/, "", lines)
            print lines
        }
    ' "${status_file}"
}

# ---------------------------------------------------------------------------
# _cbt_find_blocked_task_ids <queues_dir> <tasks_root>
# Scan all queue *.md files under <queues_dir> for [B] markers.
# For each, verify that status.md confirms State=BLOCKED.
# Echoes one task_id per line (unique).
# ---------------------------------------------------------------------------
_cbt_find_blocked_task_ids() {
    local queues_dir="$1"
    local tasks_root="$2"

    if [[ ! -d "${queues_dir}" ]]; then
        return 0
    fi

    local seen_ids=()
    local queue_file task_id status_file state

    # Scan all *_backlog.md files recursively under queues_dir
    while IFS= read -r queue_file; do
        [[ -f "${queue_file}" ]] || continue

        while IFS= read -r line; do
            # Match lines like: - [B] TASK-ID or - [ B ] TASK-ID
            if [[ "${line}" =~ ^[[:space:]]*-?[[:space:]]*\[[[:space:]]*B[[:space:]]*\][[:space:]]+([A-Za-z0-9._-]+) ]]; then
                task_id="${BASH_REMATCH[1]}"
                [[ -z "${task_id}" ]] && continue

                # Dedup: skip if already seen
                local dup=0
                local seen_id
                for seen_id in "${seen_ids[@]}"; do
                    if [[ "${seen_id}" == "${task_id}" ]]; then
                        dup=1
                        break
                    fi
                done
                (( dup == 1 )) && continue

                # Verify status.md confirms BLOCKED
                status_file="${tasks_root}/${task_id}/status.md"
                if [[ ! -f "${status_file}" ]]; then
                    continue
                fi

                state="$(_cbt_extract_field "${status_file}" "State" | tr '[:lower:]' '[:upper:]')"
                if [[ "${state}" == "BLOCKED" ]]; then
                    echo "${task_id}"
                    seen_ids+=("${task_id}")
                fi
            fi
        done < "${queue_file}"
    done < <(find "${queues_dir}" -maxdepth 2 -name '*_backlog.md' -type f 2>/dev/null)
}

# ---------------------------------------------------------------------------
# _cbt_do_promote
# Inner function invoked via overwatch_halt_first_fix for one task.
# Reads _CBT_TASK_ID, _CBT_TASK_STATUS, _CBT_QUEUE_FILE, _CBT_CLEARED_REASON
# from the environment.
#
# Actions:
#   1. Backup status.md and queue file via overwatch_backup_file
#   2. Atomically: update queue [B] -> [ ] and reset status.md fields
#   3. Log action via overwatch_log_action
# ---------------------------------------------------------------------------
_cbt_do_promote() {
    local task_id="${_CBT_TASK_ID}"
    local task_status="${_CBT_TASK_STATUS}"
    local queue_file="${_CBT_QUEUE_FILE}"
    local cleared_reason="${_CBT_CLEARED_REASON}"

    # Backup status.md
    local status_backup="none"
    local bpath
    bpath="$(overwatch_backup_file "${task_status}")" || {
        echo "check-blocked-tasks: backup failed for ${task_status}" >&2
        return 1
    }
    status_backup="${bpath}"

    # Backup queue file
    local queue_backup="none"
    bpath="$(overwatch_backup_file "${queue_file}")" || {
        echo "check-blocked-tasks: backup failed for ${queue_file}" >&2
        return 1
    }
    queue_backup="${bpath}"

    # Atomic promotion via Python (same pattern as promote_blocked_to_backlog in wake/claude.sh)
    local promote_exit=0
    python3 - "${queue_file}" "${task_id}" "${task_status}" "${cleared_reason}" <<'PY' || promote_exit=$?
import pathlib, re, sys

queue_path = pathlib.Path(sys.argv[1])
task_id    = sys.argv[2]
status_path = pathlib.Path(sys.argv[3])
cleared_reason = sys.argv[4]

# Read both files up front
try:
    queue_text = queue_path.read_text()
except OSError as e:
    raise SystemExit(f"Cannot read queue file: {e}")

try:
    status_text = status_path.read_text()
except OSError as e:
    raise SystemExit(f"Cannot read status file: {e}")

# Prepare new queue text: [B] -> [ ]
queue_pattern = rf'^(\s*-\s*)\[\s*B\s*\](\s+{re.escape(task_id)})(\s.*)?$'
queue_new, queue_n = re.subn(
    queue_pattern,
    lambda m: m.group(1) + '[ ]' + m.group(2) + (m.group(3) if m.group(3) else ''),
    queue_text,
    flags=re.M,
)
if queue_n == 0:
    raise SystemExit(f"Could not find [B] queue entry for {task_id}")

# Prepare new status text
new_summary = (
    f"Promoted from BLOCKED to BACKLOG by OVERWATCH check-blocked-tasks: "
    f"blocker cleared ({cleared_reason})."
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
        # Field absent in this status.md — skip silently
        return text
    return text_new

status_new = replace_block(status_text,  "State",           "BACKLOG")
status_new = replace_block(status_new,   "Blockers",        "none")
status_new = replace_block(status_new,   "Blocked By Agent","none")
status_new = replace_block(status_new,   "Blocked Reason",  "none")
status_new = replace_block(status_new,   "Needs Human",     "no")
status_new = replace_block(status_new,   "Summary",         new_summary)

# Normalize: collapse 3+ consecutive blank lines to 2
status_new = re.sub(r'\n\n\n+', '\n\n', status_new)

# Write queue file first
try:
    queue_path.write_text(queue_new)
except OSError as e:
    raise SystemExit(f"Failed to write queue file: {e}")

# Write status file; roll back queue on failure
try:
    status_path.write_text(status_new)
except OSError as e:
    try:
        queue_path.write_text(queue_text)
    except OSError as rollback_err:
        raise SystemExit(
            f"CRITICAL: Failed to write status file ({e}) AND failed to roll back "
            f"queue file ({rollback_err}). Manual intervention required for {task_id}."
        )
    raise SystemExit(f"Failed to write status file (queue rolled back): {e}")
PY

    if (( promote_exit != 0 )); then
        echo "check-blocked-tasks: promotion failed for ${task_id}" >&2
        return 1
    fi

    # Log: use the queue file as the secondary detail; status backup as primary
    overwatch_log_action \
        "check-blocked-tasks" \
        "${task_id}" \
        "promoted-blocked-to-backlog" \
        "${status_backup}" \
        "Blocker cleared (${cleared_reason}); queue [B]->[]; status reset to BACKLOG" \
    || true

    return 0
}

# ---------------------------------------------------------------------------
# _cbt_find_queue_for_task <queues_dir> <task_id>
# Find the queue file that contains a [B] entry for <task_id>.
# Echoes the path to the queue file, or empty if not found.
# ---------------------------------------------------------------------------
_cbt_find_queue_for_task() {
    local queues_dir="$1"
    local task_id="$2"
    local queue_file

    while IFS= read -r queue_file; do
        [[ -f "${queue_file}" ]] || continue
        if grep -qE "^\s*-?\s*\[\s*B\s*\]\s+${task_id}(\s|$)" "${queue_file}" 2>/dev/null; then
            echo "${queue_file}"
            return 0
        fi
    done < <(find "${queues_dir}" -maxdepth 2 -name '*_backlog.md' -type f 2>/dev/null)
    echo ""
    return 0
}

# ---------------------------------------------------------------------------
# overwatch_check_blocked_tasks [--dry-run]
# Main detection function.
# Returns 0 on success, 1 on internal error.
# ---------------------------------------------------------------------------
overwatch_check_blocked_tasks() {
    local dry_run=0
    local arg
    for arg in "$@"; do
        if [[ "${arg}" == "--dry-run" ]]; then
            dry_run=1
        fi
    done

    _cbt_resolve_env || return 1

    local project_name="${OVERWATCH_PROJECT}"
    local project_root="${KANBAN_ROOT}/projects/${project_name}"
    local rs_file="${project_root}/release-state.md"
    local tasks_root="${project_root}/tasks"
    local queues_dir="${tasks_root}/queues"

    if [[ ! -d "${project_root}" ]]; then
        echo "check-blocked-tasks: project root does not exist: ${project_root}" >&2
        return 1
    fi

    # Source overwatch_protocol.sh if not already loaded
    if ! declare -f overwatch_log_action >/dev/null 2>&1; then
        local lib_dir
        lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)" || {
            echo "check-blocked-tasks: cannot resolve lib dir" >&2
            return 1
        }
        if [[ -f "${lib_dir}/../overwatch_protocol.sh" ]]; then
            # shellcheck source=/dev/null
            source "${lib_dir}/../overwatch_protocol.sh"
        else
            echo "check-blocked-tasks: overwatch_protocol.sh not found relative to ${lib_dir}" >&2
            return 1
        fi
    fi

    if (( dry_run == 0 )); then
        # Verify overwatch state dir for live mode
        local state_dir="${KANBAN_ROOT}/projects/${project_name}/overwatch"
        if [[ ! -d "${state_dir}" ]]; then
            echo "check-blocked-tasks: overwatch state dir missing: ${state_dir}" >&2
            return 1
        fi
    fi

    if [[ ! -d "${queues_dir}" ]]; then
        echo "check-blocked-tasks: queues dir does not exist: ${queues_dir}" >&2
        return 0
    fi

    # Read current Active RC — promotion only happens when Active RC is none
    local active_rc
    active_rc="$(_cbt_read_release_state_active_rc "${rs_file}")"

    # Find all BLOCKED task IDs from queue files
    local blocked_ids
    blocked_ids="$(_cbt_find_blocked_task_ids "${queues_dir}" "${tasks_root}")"

    if [[ -z "${blocked_ids}" ]]; then
        echo "check-blocked-tasks: no BLOCKED tasks found in queue files" >&2
        return 0
    fi

    echo "check-blocked-tasks: scanning BLOCKED tasks (Active RC=${active_rc})" >&2

    local promoted=0
    local task_id

    while IFS= read -r task_id; do
        [[ -z "${task_id}" ]] && continue

        local task_dir="${tasks_root}/${task_id}"
        local task_status="${task_dir}/status.md"

        if [[ ! -f "${task_status}" ]]; then
            echo "check-blocked-tasks: ${task_id}: status.md missing; skipping" >&2
            continue
        fi

        # Check Needs Human field: skip if yes
        local needs_human
        needs_human="$(_cbt_extract_field "${task_status}" "Needs Human" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')"
        if [[ "${needs_human}" == "yes" ]]; then
            echo "check-blocked-tasks: ${task_id}: Needs Human=yes; skipping" >&2
            continue
        fi

        # Check Blocked Reason: must mention "Active RC" (case-insensitive)
        local blocked_reason
        blocked_reason="$(_cbt_extract_field "${task_status}" "Blocked Reason")"
        if ! echo "${blocked_reason}" | grep -qi "active rc"; then
            echo "check-blocked-tasks: ${task_id}: Blocked Reason does not mention Active RC; skipping" >&2
            continue
        fi

        # Condition: Active RC must be none for this pattern to clear
        if [[ -n "${active_rc}" && "${active_rc}" != "none" ]]; then
            echo "check-blocked-tasks: ${task_id}: Active RC is ${active_rc} (not none); blocker not cleared; skipping" >&2
            continue
        fi

        echo "check-blocked-tasks: ${task_id}: Active RC=none, Blocked Reason mentions Active RC — eligible for promotion" >&2

        if (( dry_run == 1 )); then
            echo "check-blocked-tasks: [dry-run] would promote ${task_id} from BLOCKED to BACKLOG" >&2
            overwatch_log_action \
                "check-blocked-tasks" \
                "${task_id}" \
                "dry-run-blocked-task-promotable" \
                "none" \
                "Active RC=none, Blocked Reason mentions Active RC; eligible (dry-run, no action taken)" \
            2>/dev/null || true
            continue
        fi

        # Find the queue file that contains this task's [B] entry
        local queue_file
        queue_file="$(_cbt_find_queue_for_task "${queues_dir}" "${task_id}")"
        if [[ -z "${queue_file}" ]]; then
            echo "check-blocked-tasks: ${task_id}: no queue file with [B] entry found; skipping" >&2
            continue
        fi

        # Live mode: promote via overwatch_halt_first_fix
        export _CBT_TASK_ID="${task_id}"
        export _CBT_TASK_STATUS="${task_status}"
        export _CBT_QUEUE_FILE="${queue_file}"
        export _CBT_CLEARED_REASON="Active RC=none"

        local fix_exit=0
        overwatch_halt_first_fix _cbt_do_promote || fix_exit=$?

        unset _CBT_TASK_ID _CBT_TASK_STATUS _CBT_QUEUE_FILE _CBT_CLEARED_REASON

        if (( fix_exit == 3 )); then
            echo "check-blocked-tasks: HALT_OVERWATCH guard tripped; aborting further promotions" >&2
            break
        elif (( fix_exit == 4 )); then
            echo "check-blocked-tasks: per-repo flock contended; aborting further promotions" >&2
            break
        elif (( fix_exit != 0 )); then
            echo "check-blocked-tasks: promotion failed for ${task_id} (exit ${fix_exit}); continuing" >&2
            continue
        fi

        echo "check-blocked-tasks: promoted ${task_id} from BLOCKED to BACKLOG" >&2
        promoted=$(( promoted + 1 ))

    done <<< "${blocked_ids}"

    echo "check-blocked-tasks: complete; promoted ${promoted} task(s) from BLOCKED to BACKLOG" >&2
    return 0
}

# ---------------------------------------------------------------------------
# Standalone invocation entry point.
# When this script is executed directly (not sourced), call the main function.
# ---------------------------------------------------------------------------
if [[ "${BASH_SOURCE[0]:-}" == "${0}" ]]; then
    overwatch_check_blocked_tasks "$@"
    exit $?
fi
