#!/usr/bin/env bash
# team/scripts/lib/overwatch-checks/check-transient-api-error.sh
#
# OVERWATCH Tier-1 detection module: detect BLOCKED tasks whose agent log
# tail matches known transient API error signatures, then either auto-requeue
# the task or bug-file it (if the requeue ceiling is reached).
#
# Transient signatures (any match triggers):
#   - "API Error: 5xx"  (any 5-series HTTP status in that phrase)
#   - "Overloaded"
#   - "overloaded_error"
#   - "rate limit"
#   - "429"
#   - "503"
#   - "529"
#
# Requeue path (ceiling not reached):
#   1. Back up status.md via overwatch_backup_file.
#   2. Reset State to BACKLOG.
#   3. Increment ## Transient Requeue Count in status.md (append field if absent).
#   4. Append "TRANSIENT" to ## Labels in status.md (append field if absent).
#   5. Log action via overwatch_log_action.
#
# Ceiling path (requeue count already >= 2):
#   1. Write a bug report file under $KANBAN_ROOT/projects/$OVERWATCH_PROJECT/bugs/.
#   2. Log action via overwatch_log_action.
#   (Task is NOT modified — a human or future iteration acts on the bug.)
#
# Companion residue cleanup (runs for every transient-matched task, after the
# requeue-or-bug-file decision):
#   - For each per-task worktree found in the dev tree:
#     - If the worktree has no commits not reachable from its source branch
#       (determined by checking whether git worktree can be pruned) AND the
#       worktree directory exists with no extra commits ahead of origin/main:
#         → git worktree prune + log (worktree-prune class).
#     - If the worktree carries commits (has commits not in any named branch
#       other than itself):
#         → bug-file and preserve the worktree.
#
# This script is both:
#   - Sourceable (for the OVERWATCH driver or sweep runner): source this file and call
#     overwatch_check_transient_api_error [--dry-run]
#   - Directly invokable: bash check-transient-api-error.sh [--dry-run]
#
# Required environment variables (when sourced by OVERWATCH driver):
#   KANBAN_ROOT       — absolute path to the kanban root
#   OVERWATCH_PROJECT — project name (e.g. "pgai-agent-kanban")
#
# When invoked directly, KANBAN_ROOT defaults to:
#   ${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}
# OVERWATCH_PROJECT defaults to the single registered project (errors on multi).
#
# Usage:
#   bash check-transient-api-error.sh [--dry-run]
#
# Exit codes:
#   0 — completed successfully (no transient tasks, or transient tasks handled)
#   1 — internal error (missing dependencies, unreadable state, etc.)
#
# --dry-run: scans and logs findings but does NOT modify any task files.

# ---------------------------------------------------------------------------
# _ctae_resolve_env
# Resolve KANBAN_ROOT, OVERWATCH_PROJECT, and dev tree path from the environment.
# Sets _CTAE_DEV_TREE and _CTAE_BRANCH_PREFIX in the calling scope.
# Returns 0 on success, 1 on failure.
# ---------------------------------------------------------------------------
_ctae_resolve_env() {
    if [[ -z "${KANBAN_ROOT:-}" ]]; then
        KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-${HOME}/pgai_agent_kanban}"
    fi
    if [[ ! -d "${KANBAN_ROOT}" ]]; then
        echo "check-transient-api-error: KANBAN_ROOT does not exist: ${KANBAN_ROOT}" >&2
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
            echo "check-transient-api-error: ERROR: OVERWATCH_PROJECT is not set and multiple projects are registered." >&2
            echo "  Set OVERWATCH_PROJECT to the target project name before running this check." >&2
            echo "  Registered projects: $(echo "${_all_projects}" | tr '\n' ' ')" >&2
            return 1
        fi
        OVERWATCH_PROJECT="$(echo "${_all_projects}" | grep '[^[:space:]]' | head -n1)"
        if [[ -z "${OVERWATCH_PROJECT:-}" ]]; then
            echo "check-transient-api-error: ERROR: no project specified and none resolvable from projects.cfg" >&2
            echo "  Set OVERWATCH_PROJECT or register a project in ${KANBAN_ROOT}/projects.cfg" >&2
            return 1
        fi
    fi

    # Resolve dev tree path from project.cfg (or PROJECT.cfg legacy)
    local _proj_root="${KANBAN_ROOT}/projects/${OVERWATCH_PROJECT}"
    local _cfg_file=""
    if [[ -f "${_proj_root}/project.cfg" ]]; then
        _cfg_file="${_proj_root}/project.cfg"
    elif [[ -f "${_proj_root}/PROJECT.cfg" ]]; then
        _cfg_file="${_proj_root}/PROJECT.cfg"
    fi
    _CTAE_DEV_TREE=""
    if [[ -n "${_cfg_file}" ]]; then
        _CTAE_DEV_TREE="$(grep -E '^[[:space:]]*dev_tree_path[[:space:]]*=' "${_cfg_file}" \
            | head -n1 \
            | sed 's|^[^=]*=[[:space:]]*||; s|[[:space:]]*$||; s|^["'"'"']||; s|["'"'"']$||')"
    fi
    if [[ -z "${_CTAE_DEV_TREE}" ]] && [[ -n "${PGAI_DEV_TREE_PATH:-}" ]]; then
        _CTAE_DEV_TREE="${PGAI_DEV_TREE_PATH}"
    fi

    # branch_prefix — optional; default empty
    _CTAE_BRANCH_PREFIX=""
    if [[ -n "${_cfg_file}" ]]; then
        local _raw_prefix
        _raw_prefix="$(grep -E '^[[:space:]]*branch_prefix[[:space:]]*=' "${_cfg_file}" \
            | head -n1 \
            | sed 's|^[^=]*=[[:space:]]*||; s|[[:space:]]*$||; s|^["'"'"']||; s|["'"'"']$||')"
        _CTAE_BRANCH_PREFIX="${_raw_prefix:-}"
    fi

    return 0
}

# ---------------------------------------------------------------------------
# _ctae_extract_field <status_file> <field_name>
# Extract the body of a ## Field section from a status.md file.
# Echoes the trimmed content, or empty string if not found.
# ---------------------------------------------------------------------------
_ctae_extract_field() {
    local status_file="$1"
    local field_name="$2"

    if [[ ! -f "${status_file}" ]]; then
        echo ""
        return 0
    fi

    awk -v field="${field_name}" '
        $0 ~ "^## " field "$" { found=1; next }
        found && /^## / { exit }
        found { lines = lines $0 "\n" }
        END {
            gsub(/^[[:space:]]+/, "", lines)
            gsub(/[[:space:]]+$/, "", lines)
            print lines
        }
    ' "${status_file}"
}

# ---------------------------------------------------------------------------
# _ctae_log_tail <task_id> <tasks_root>
# Emit the last 50 lines of the most recently modified .log file under
# $tasks_root/$task_id/logs/, or empty output if no logs exist.
# ---------------------------------------------------------------------------
_ctae_log_tail() {
    local task_id="$1"
    local tasks_root="$2"

    local logs_dir="${tasks_root}/${task_id}/logs"
    if [[ ! -d "${logs_dir}" ]]; then
        return 0
    fi

    # Find the most recently modified .log file under the task's logs dir.
    local newest_log
    newest_log="$(find "${logs_dir}" -maxdepth 2 -name '*.log' -type f \
        -printf '%T@ %p\n' 2>/dev/null \
        | sort -rn | head -n1 | awk '{print $2}')"

    if [[ -z "${newest_log}" || ! -f "${newest_log}" ]]; then
        return 0
    fi

    tail -n 50 "${newest_log}"
}

# ---------------------------------------------------------------------------
# _ctae_matches_transient_signature <text>
# Returns 0 (true) if the text contains any known transient API error signature.
# ---------------------------------------------------------------------------
_ctae_matches_transient_signature() {
    local text="$1"

    # Check each transient signature
    if echo "${text}" | grep -qE 'API Error: 5[0-9][0-9]'; then
        return 0
    fi
    if echo "${text}" | grep -q 'Overloaded'; then
        return 0
    fi
    if echo "${text}" | grep -q 'overloaded_error'; then
        return 0
    fi
    if echo "${text}" | grep -qi 'rate limit'; then
        return 0
    fi
    if echo "${text}" | grep -qE '\b429\b'; then
        return 0
    fi
    if echo "${text}" | grep -qE '\b503\b'; then
        return 0
    fi
    if echo "${text}" | grep -qE '\b529\b'; then
        return 0
    fi
    return 1
}

# ---------------------------------------------------------------------------
# _ctae_read_requeue_count <status_file>
# Echo the integer value of ## Transient Requeue Count from status.md,
# or 0 if the field is absent or non-numeric.
# ---------------------------------------------------------------------------
_ctae_read_requeue_count() {
    local status_file="$1"
    local raw
    raw="$(_ctae_extract_field "${status_file}" "Transient Requeue Count")"
    raw="$(echo "${raw}" | tr -d '[:space:]')"
    if [[ "${raw}" =~ ^[0-9]+$ ]]; then
        echo "${raw}"
    else
        echo "0"
    fi
}

# ---------------------------------------------------------------------------
# _ctae_bug_file <task_id> <reason> <bugs_dir>
# Write a bug report for the task under bugs_dir.
# Echoes the path to the created bug file.
# Returns 0 on success, 1 on failure.
# ---------------------------------------------------------------------------
_ctae_bug_file() {
    local task_id="$1"
    local reason="$2"
    local bugs_dir="$3"
    local timestamp
    timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
    local bug_file="${bugs_dir}/BUG-overwatch-transient-${task_id}-${timestamp}.md"

    mkdir -p "${bugs_dir}" 2>/dev/null || {
        echo "check-transient-api-error: cannot create bugs dir: ${bugs_dir}" >&2
        return 1
    }

    cat > "${bug_file}" <<EOF
# Bug: Transient API Error — ${task_id}

## Status
open

## Filed By
overwatch/check-transient-api-error

## Filed At
${timestamp}

## Task
${task_id}

## Reason
${reason}

## Description
OVERWATCH detected a transient API error pattern in the task log but the
auto-requeue ceiling (2) has been reached, or the task has a worktree carrying
commits that must not be auto-pruned. Manual intervention required.
EOF

    echo "${bug_file}"
    return 0
}

# ---------------------------------------------------------------------------
# _ctae_do_requeue
# Inner function invoked by the requeue path.
# Reads: _CTAE_TASK_ID, _CTAE_STATUS_FILE, _CTAE_NEW_COUNT, _CTAE_QUEUES_DIR
# from the environment.
#
# Actions:
#   1. Backup status.md
#   2. Update State -> BACKLOG, increment Transient Requeue Count, add TRANSIENT label
#   3. Log the action
# ---------------------------------------------------------------------------
_ctae_do_requeue() {
    local task_id="${_CTAE_TASK_ID}"
    local status_file="${_CTAE_STATUS_FILE}"
    local new_count="${_CTAE_NEW_COUNT}"

    # Backup status.md before any change
    local backup_path="none"
    local bpath
    bpath="$(overwatch_backup_file "${status_file}")" || {
        echo "check-transient-api-error: backup failed for ${status_file}" >&2
        return 1
    }
    backup_path="${bpath}"

    # Use Python to atomically update the status.md
    local edit_exit=0
    python3 - "${status_file}" "${new_count}" <<'PY' || edit_exit=$?
import pathlib, re, sys

status_path = pathlib.Path(sys.argv[1])
new_count   = sys.argv[2]

try:
    text = status_path.read_text(encoding="utf-8")
except OSError as e:
    raise SystemExit(f"Cannot read status file: {e}")

def replace_section(text, heading, new_body):
    """Replace the body of a ## heading section; return (new_text, replaced_bool)."""
    pattern = rf'(^## {re.escape(heading)}\s*\n)(.*?)(\n+##|\Z)'
    new_text, n = re.subn(
        pattern,
        lambda m: m.group(1) + new_body.strip() + "\n" + (m.group(3) if m.group(3) else ''),
        text,
        flags=re.S | re.M,
    )
    return new_text, n > 0

def append_section(text, heading, body):
    """Append a new ## heading section at the end of the file."""
    if not text.endswith('\n'):
        text += '\n'
    text += f"\n## {heading}\n{body.strip()}\n"
    return text

# 1. Reset State to BACKLOG
text, _ = replace_section(text, "State", "BACKLOG")

# 2. Update or insert Transient Requeue Count
text, replaced = replace_section(text, "Transient Requeue Count", new_count)
if not replaced:
    text = append_section(text, "Transient Requeue Count", new_count)

# 3. Update or insert Labels with TRANSIENT
text, replaced = replace_section(
    text, "Labels",
    lambda m=None: (
        ("TRANSIENT" if not m else
         m if "TRANSIENT" in m else m + " TRANSIENT")
    )
)
# The lambda trick above won't work in re.subn — do it differently:
# Re-read the Labels field value to see if TRANSIENT is already there.
labels_raw, labels_found = "", False
label_match = re.search(r'^## Labels\s*\n(.*?)(\n+##|\Z)', text, flags=re.S | re.M)
if label_match:
    labels_raw = label_match.group(1).strip()
    labels_found = True

if labels_found:
    if "TRANSIENT" not in labels_raw:
        new_labels = (labels_raw + " TRANSIENT").strip() if labels_raw else "TRANSIENT"
        text, _ = replace_section(text, "Labels", new_labels)
else:
    text = append_section(text, "Labels", "TRANSIENT")

# 4. Clear blockers / needs human
text, _ = replace_section(text, "Blockers", "none")
text, _ = replace_section(text, "Needs Human", "no")

# Update Summary
new_summary = (
    f"Auto-requeued by OVERWATCH check-transient-api-error: "
    f"transient API error detected in task log. Requeue #{new_count}."
)
text, _ = replace_section(text, "Summary", new_summary)

# Normalize: collapse 3+ blank lines to 2
text = re.sub(r'\n\n\n+', '\n\n', text)

try:
    status_path.write_text(text, encoding="utf-8")
except OSError as e:
    raise SystemExit(f"Failed to write status file: {e}")
PY

    if (( edit_exit != 0 )); then
        echo "check-transient-api-error: status update failed for ${task_id}" >&2
        return 1
    fi

    # Log the action
    overwatch_log_action \
        "check-transient-api-error" \
        "${task_id}" \
        "transient-auto-requeued" \
        "${backup_path}" \
        "Transient API error in log tail; reset to BACKLOG (requeue #${new_count} of 2)" \
    || true

    return 0
}

# ---------------------------------------------------------------------------
# _ctae_find_task_worktrees <dev_tree> <task_id>
# List all worktree paths for the given task_id using git worktree list.
# Echoes one absolute path per line.
# ---------------------------------------------------------------------------
_ctae_find_task_worktrees() {
    local dev_tree="$1"
    local task_id="$2"

    if [[ -z "${dev_tree}" || ! -d "${dev_tree}" ]]; then
        return 0
    fi

    # git worktree list output: <path> <sha> [<branch>]
    git -C "${dev_tree}" worktree list --porcelain 2>/dev/null \
        | awk '/^worktree /{print $2}' \
        | grep -F "${task_id}" || true
}

# ---------------------------------------------------------------------------
# _ctae_worktree_has_commits <worktree_path>
# Returns 0 (true) if the worktree has local commits not reachable from
# any named remote or local tracking branch.
# Returns 1 (false) if the worktree is clean / no extra commits.
# Returns 1 also if the worktree path is not a valid git worktree
# (safely treats missing as no-commits so prune is allowed).
# ---------------------------------------------------------------------------
_ctae_worktree_has_commits() {
    local wt_path="$1"

    if [[ ! -d "${wt_path}" ]]; then
        # Worktree path gone — treat as no commits (prune safe)
        return 1
    fi

    # Check if HEAD is detached or on a branch
    local head_ref
    head_ref="$(git -C "${wt_path}" symbolic-ref HEAD 2>/dev/null || echo "detached")"

    if [[ "${head_ref}" == "detached" ]]; then
        # Detached HEAD — check if HEAD commit is reachable from any branch
        local reachable
        reachable="$(git -C "${wt_path}" branch --contains HEAD 2>/dev/null | grep -v '^*' | head -n1)"
        if [[ -z "${reachable}" ]]; then
            # HEAD commit not in any branch — has unique commits
            return 0
        fi
        return 1
    fi

    # On a branch — check if there are commits ahead of any remote tracking branch
    local ahead_count
    ahead_count="$(git -C "${wt_path}" rev-list --count "@{upstream}..HEAD" 2>/dev/null || echo "0")"
    if [[ "${ahead_count}" =~ ^[0-9]+$ ]] && [[ "${ahead_count}" -gt 0 ]]; then
        return 0
    fi

    # No upstream — count commits not reachable from main
    local extra_commits
    extra_commits="$(git -C "${wt_path}" log --oneline main..HEAD 2>/dev/null \
        | wc -l | tr -d '[:space:]')"
    if [[ "${extra_commits}" =~ ^[0-9]+$ ]] && [[ "${extra_commits}" -gt 0 ]]; then
        return 0
    fi

    return 1
}

# ---------------------------------------------------------------------------
# _ctae_handle_worktree_residue <task_id> <dev_tree> <dry_run> <bugs_dir>
# Inspect any per-task worktrees in the dev tree and either:
#   - Prune them (worktree-prune class, no commits)
#   - Bug-file them (carries commits, must not auto-fix)
# ---------------------------------------------------------------------------
_ctae_handle_worktree_residue() {
    local task_id="$1"
    local dev_tree="$2"
    local dry_run="$3"
    local bugs_dir="$4"

    if [[ -z "${dev_tree}" ]]; then
        # Dev tree not configured — skip residue cleanup silently
        return 0
    fi

    local wt_paths
    wt_paths="$(_ctae_find_task_worktrees "${dev_tree}" "${task_id}")"

    if [[ -z "${wt_paths}" ]]; then
        echo "check-transient-api-error: ${task_id}: no per-task worktrees found; residue check clean" >&2
        return 0
    fi

    local wt_path
    while IFS= read -r wt_path; do
        [[ -z "${wt_path}" ]] && continue

        echo "check-transient-api-error: ${task_id}: found worktree ${wt_path}" >&2

        if _ctae_worktree_has_commits "${wt_path}"; then
            # Carries commits → bug-file, never auto-prune
            echo "check-transient-api-error: ${task_id}: worktree ${wt_path} carries commits; bug-filing" >&2
            if (( dry_run == 0 )); then
                local bug_path
                bug_path="$(_ctae_bug_file "${task_id}" \
                    "Worktree ${wt_path} carries commits; cannot auto-prune" \
                    "${bugs_dir}")" || true

                overwatch_log_action \
                    "check-transient-api-error" \
                    "${wt_path}" \
                    "worktree-carries-commits-bug-filed" \
                    "none" \
                    "Worktree for ${task_id} at ${wt_path} has local commits; bug filed at ${bug_path:-unknown}" \
                || true
            else
                echo "check-transient-api-error: [dry-run] would bug-file worktree ${wt_path} (carries commits)" >&2
                overwatch_log_action \
                    "check-transient-api-error" \
                    "${wt_path}" \
                    "dry-run-worktree-commits-would-bug-file" \
                    "none" \
                    "Worktree for ${task_id} at ${wt_path} has local commits; dry-run, no action" \
                2>/dev/null || true
            fi
        else
            # No extra commits → worktree-prune class
            echo "check-transient-api-error: ${task_id}: worktree ${wt_path} has no extra commits; pruning" >&2
            if (( dry_run == 0 )); then
                # Backup a marker before pruning (satisfies "matching backup" requirement)
                local backup_marker
                backup_marker="$(overwatch_backup_file "${wt_path}/.git" 2>/dev/null \
                    || echo "none")"

                # Remove the worktree if its directory exists; then prune
                if [[ -d "${wt_path}" ]]; then
                    git -C "${dev_tree}" worktree remove --force "${wt_path}" 2>/dev/null || true
                fi
                git -C "${dev_tree}" worktree prune 2>/dev/null || true

                overwatch_log_action \
                    "check-transient-api-error" \
                    "${wt_path}" \
                    "worktree-pruned" \
                    "${backup_marker}" \
                    "Orphaned worktree for ${task_id} had no extra commits; pruned" \
                || true
            else
                echo "check-transient-api-error: [dry-run] would prune worktree ${wt_path}" >&2
                overwatch_log_action \
                    "check-transient-api-error" \
                    "${wt_path}" \
                    "dry-run-worktree-would-prune" \
                    "none" \
                    "Orphaned worktree for ${task_id} at ${wt_path}; dry-run, no action" \
                2>/dev/null || true
            fi
        fi
    done <<< "${wt_paths}"

    return 0
}

# ---------------------------------------------------------------------------
# _ctae_find_blocked_task_ids <queues_dir> <tasks_root>
# Scan all queue *_backlog.md files for [B] markers pointing to tasks whose
# status.md confirms State: BLOCKED.
# Echoes one task_id per line (unique).
# ---------------------------------------------------------------------------
_ctae_find_blocked_task_ids() {
    local queues_dir="$1"
    local tasks_root="$2"

    if [[ ! -d "${queues_dir}" ]]; then
        return 0
    fi

    local seen_ids=()
    local queue_file task_id status_file state

    while IFS= read -r queue_file; do
        [[ -f "${queue_file}" ]] || continue

        while IFS= read -r line; do
            if [[ "${line}" =~ ^[[:space:]]*-?[[:space:]]*\[[[:space:]]*B[[:space:]]*\][[:space:]]+([A-Za-z0-9._-]+) ]]; then
                task_id="${BASH_REMATCH[1]}"
                [[ -z "${task_id}" ]] && continue

                # Dedup
                local dup=0
                local seen_id
                for seen_id in "${seen_ids[@]}"; do
                    if [[ "${seen_id}" == "${task_id}" ]]; then
                        dup=1; break
                    fi
                done
                (( dup == 1 )) && continue

                # Confirm status.md says BLOCKED
                status_file="${tasks_root}/${task_id}/status.md"
                if [[ ! -f "${status_file}" ]]; then
                    continue
                fi
                state="$(_ctae_extract_field "${status_file}" "State" | tr '[:lower:]' '[:upper:]')"
                if [[ "${state}" == "BLOCKED" ]]; then
                    echo "${task_id}"
                    seen_ids+=("${task_id}")
                fi
            fi
        done < "${queue_file}"
    done < <(find "${queues_dir}" -maxdepth 2 -name '*_backlog.md' -type f 2>/dev/null)
}

# ---------------------------------------------------------------------------
# overwatch_check_transient_api_error [--dry-run]
# Main detection and action function.
# Returns 0 on success, 1 on internal error.
# ---------------------------------------------------------------------------
overwatch_check_transient_api_error() {
    local dry_run=0
    local arg
    for arg in "$@"; do
        if [[ "${arg}" == "--dry-run" ]]; then
            dry_run=1
        fi
    done

    _ctae_resolve_env || return 1

    local project_name="${OVERWATCH_PROJECT}"
    local project_root="${KANBAN_ROOT}/projects/${project_name}"
    local tasks_root="${project_root}/tasks"
    local queues_dir="${tasks_root}/queues"
    local bugs_dir="${project_root}/bugs"
    local dev_tree="${_CTAE_DEV_TREE:-}"

    if [[ ! -d "${project_root}" ]]; then
        echo "check-transient-api-error: project root does not exist: ${project_root}" >&2
        return 1
    fi

    # Source overwatch_protocol.sh if not already loaded
    if ! declare -f overwatch_log_action >/dev/null 2>&1; then
        local lib_dir
        lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)" || {
            echo "check-transient-api-error: cannot resolve lib dir" >&2
            return 1
        }
        if [[ -f "${lib_dir}/../overwatch_protocol.sh" ]]; then
            # shellcheck source=/dev/null
            source "${lib_dir}/../overwatch_protocol.sh"
        else
            echo "check-transient-api-error: overwatch_protocol.sh not found relative to ${lib_dir}" >&2
            return 1
        fi
    fi

    if (( dry_run == 0 )); then
        local state_dir="${project_root}/overwatch"
        if [[ ! -d "${state_dir}" ]]; then
            echo "check-transient-api-error: overwatch state dir missing: ${state_dir}" >&2
            return 1
        fi
    fi

    if [[ ! -d "${queues_dir}" ]]; then
        echo "check-transient-api-error: queues dir does not exist: ${queues_dir}" >&2
        return 0
    fi

    # Find all BLOCKED task IDs
    local blocked_ids
    blocked_ids="$(_ctae_find_blocked_task_ids "${queues_dir}" "${tasks_root}")"

    if [[ -z "${blocked_ids}" ]]; then
        echo "check-transient-api-error: no BLOCKED tasks found in queue files" >&2
        return 0
    fi

    echo "check-transient-api-error: scanning BLOCKED tasks for transient API error signatures" >&2

    local handled=0
    local task_id

    while IFS= read -r task_id; do
        [[ -z "${task_id}" ]] && continue

        local task_dir="${tasks_root}/${task_id}"
        local task_status="${task_dir}/status.md"

        if [[ ! -f "${task_status}" ]]; then
            echo "check-transient-api-error: ${task_id}: status.md missing; skipping" >&2
            continue
        fi

        # Get the log tail for this task
        local log_tail
        log_tail="$(_ctae_log_tail "${task_id}" "${tasks_root}")"

        # Check for transient signature
        if ! _ctae_matches_transient_signature "${log_tail}"; then
            echo "check-transient-api-error: ${task_id}: no transient signature in log tail; skipping" >&2
            continue
        fi

        echo "check-transient-api-error: ${task_id}: transient API error signature detected" >&2

        # Read current requeue count
        local current_count
        current_count="$(_ctae_read_requeue_count "${task_status}")"
        local new_count=$(( current_count + 1 ))

        if (( dry_run == 1 )); then
            echo "check-transient-api-error: [dry-run] ${task_id}: count=${current_count}; would $(( current_count >= 2 )) && echo 'bug-file' || echo 'requeue'" >&2
            if (( current_count >= 2 )); then
                overwatch_log_action \
                    "check-transient-api-error" \
                    "${task_id}" \
                    "dry-run-transient-ceiling-would-bug-file" \
                    "none" \
                    "Transient count=${current_count} >= 2; dry-run, would bug-file" \
                2>/dev/null || true
            else
                overwatch_log_action \
                    "check-transient-api-error" \
                    "${task_id}" \
                    "dry-run-transient-would-requeue" \
                    "none" \
                    "Transient count=${current_count}; dry-run, would requeue (#${new_count})" \
                2>/dev/null || true
            fi
            # Still run residue check in dry-run mode
            _ctae_handle_worktree_residue "${task_id}" "${dev_tree}" "${dry_run}" "${bugs_dir}"
            continue
        fi

        # Ceiling check: at most 2 auto-requeues; 3rd occurrence bug-files
        if (( current_count >= 2 )); then
            echo "check-transient-api-error: ${task_id}: ceiling reached (count=${current_count}); bug-filing" >&2
            local bug_path
            bug_path="$(_ctae_bug_file "${task_id}" \
                "Transient API error ceiling reached (${current_count} requeues already performed)" \
                "${bugs_dir}")" || {
                echo "check-transient-api-error: ${task_id}: bug-file write failed" >&2
                continue
            }

            overwatch_log_action \
                "check-transient-api-error" \
                "${task_id}" \
                "transient-ceiling-bug-filed" \
                "none" \
                "Transient ceiling=${current_count}; bug filed at ${bug_path}" \
            || true

            echo "check-transient-api-error: ${task_id}: bug filed at ${bug_path}" >&2
            handled=$(( handled + 1 ))
        else
            # Requeue path: invoke via overwatch_halt_first_fix
            export _CTAE_TASK_ID="${task_id}"
            export _CTAE_STATUS_FILE="${task_status}"
            export _CTAE_NEW_COUNT="${new_count}"

            local fix_exit=0
            overwatch_halt_first_fix _ctae_do_requeue || fix_exit=$?

            unset _CTAE_TASK_ID _CTAE_STATUS_FILE _CTAE_NEW_COUNT

            if (( fix_exit == 3 )); then
                echo "check-transient-api-error: HALT_OVERWATCH guard tripped; aborting" >&2
                break
            elif (( fix_exit == 4 )); then
                echo "check-transient-api-error: per-repo flock contended; aborting" >&2
                break
            elif (( fix_exit != 0 )); then
                echo "check-transient-api-error: requeue failed for ${task_id} (exit ${fix_exit}); continuing" >&2
                continue
            fi

            echo "check-transient-api-error: ${task_id}: requeued to BACKLOG (requeue #${new_count})" >&2
            handled=$(( handled + 1 ))
        fi

        # Companion residue cleanup (always runs after requeue or bug-file)
        _ctae_handle_worktree_residue "${task_id}" "${dev_tree}" "${dry_run}" "${bugs_dir}"

    done <<< "${blocked_ids}"

    echo "check-transient-api-error: complete; handled ${handled} transient task(s)" >&2
    return 0
}

# ---------------------------------------------------------------------------
# Standalone invocation entry point.
# When this script is executed directly (not sourced), call the main function.
# ---------------------------------------------------------------------------
if [[ "${BASH_SOURCE[0]:-}" == "${0}" ]]; then
    overwatch_check_transient_api_error "$@"
    exit $?
fi
