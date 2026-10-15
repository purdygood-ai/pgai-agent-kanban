#!/usr/bin/env bash
# team/scripts/lib/wake_autonomous_scan.sh
#
# Wake autonomous-scan stage: the requirements-queue and bug-bundling scans
# that back the discovery pipeline's autonomous PM-ticket-drop path, plus
# the release-state reader they share. Extracted from team/scripts/lib/
# wake_common.sh's former inline body so wake_common.sh's own remaining code
# stays a thinner shell over sourced lib stages. Behavior-preserving: no
# stdout/stderr line, exit code, or on-disk file content changes as a result
# of this file existing — it is one stage split out of a larger driver so
# each stays a focused, independently testable unit.
#
# Public entry point
# -------------------
#   scan_and_bundle_bugs
#       Scans the project's bugs/ directory for unhandled BUG-*.md reports,
#       bundles every unhandled report into one patch-versioned priority
#       requirements doc, and marks each bundled bug's backlog entry closed.
#       Called by the discovery pipeline's autonomous scan path (Step 1 of
#       the four-step pipeline in roles/PM.md, "The discovery pipeline").
#
# Other functions in this file — get_release_state_field,
# scan_requirements_queue, drop_self_pm_ticket — are shared primitives, not
# private helpers of scan_and_bundle_bugs alone:
#   get_release_state_field   — reads a named ## FIELD block from
#                                release-state.md; used by both scan
#                                functions below to read Active RC / Last
#                                Released before deciding whether to scan.
#   scan_requirements_queue   — Step 3 of the discovery pipeline: finds the
#                                lowest-version unprocessed requirements doc.
#   drop_self_pm_ticket       — creates a PM task folder and queues it, given
#                                one or more requirements doc paths; the
#                                common tail both scan functions' callers
#                                invoke once a doc is identified.
# Defined at source time, available as soon as this file is sourced.
#
# Dependencies (must already be sourced/defined by the caller before this
# file's functions are called)
# -----------------------------------------------------------------------
#   wake_bootstrap.sh — TASKS_ROOT, KANBAN_ROOT globals, and log().
#   semver.sh          — semver_from_filename, semver_gt (sourced by
#                        wake_common.sh immediately after wake_bootstrap.sh,
#                        i.e. before this file).
#   project_paths.sh, projects.sh (sourced transitively by wake_bootstrap.sh)
#                      — pp_release_state, pp_requirements_dir, pp_bugs_dir,
#                        pp_queue_path, pp_project_root.
#   task_ids.sh (sourced transitively by wake_bootstrap.sh) —
#                        kanban_task_id.
#   cm/common.sh       — release_state_section, self-sourced below when the
#                        caller has not already loaded it; it reads through
#                        the one release-state reader (cm/read_state_field.py),
#                        so python3 must be on PATH.

if ! declare -F release_state_section >/dev/null 2>&1; then
    # shellcheck source=cm/common.sh
    source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/cm/common.sh"
fi

# Sourced by wake_common.sh after wake_bootstrap.sh and semver.sh.

# ---------------------------------------------------------------------------
# get_release_state_field FIELD
# Print the current project's ## FIELD value from release-state.md, or an
# empty line when the heading has no value before the next "##" heading.
# Returns 1 with no output when release-state.md is missing or unreadable or
# the heading is absent.
# ---------------------------------------------------------------------------
get_release_state_field() {
    local field="$1"
    local value
    value="$(release_state_section "$(pp_release_state "${_CURRENT_PROJECT}")" "$field")" || return 1
    printf '%s\n' "$value"
}

# ---------------------------------------------------------------------------
# scan_requirements_queue
# Scan projects/<name>/requirements/ NON-RECURSIVELY for eligible .md files.
# ---------------------------------------------------------------------------
scan_requirements_queue() {
    local active_rc last_released requirements_dir eligible sorted lowest_ver
    local count_at_lowest f ver

    active_rc="$(get_release_state_field "Active RC" 2>/dev/null || true)"
    if [[ -n "$active_rc" && "$active_rc" != "none" ]]; then
        log "autonomous scan: Active RC is ${active_rc}, skipping requirements queue (RC in flight)"
        return 1
    fi

    if [[ -f "${TEAM_ROOT}/HALT" ]]; then
        log "autonomous scan: HALT flag present, skipping requirements queue"
        return 1
    fi

    last_released="$(get_release_state_field "Last Released" 2>/dev/null || true)"
    if [[ -z "$last_released" || "$last_released" == "none" ]]; then
        log "autonomous scan: cannot determine Last Released version from release-state.md"
        return 1
    fi

    requirements_dir="$(pp_requirements_dir "${_CURRENT_PROJECT}")"
    if [[ ! -d "$requirements_dir" ]]; then
        return 1
    fi

    eligible=()
    for f in "$requirements_dir"/*.md; do
        [[ -f "$f" ]] || continue
        ver="$(semver_from_filename "$f")"
        [[ -z "$ver" ]] && continue
        if semver_gt "$ver" "$last_released"; then
            eligible+=("${ver}"$'\t'"${f}")
        fi
    done

    if [[ ${#eligible[@]} -eq 0 ]]; then
        return 1
    fi

    sorted="$(printf '%s\n' "${eligible[@]}" | sort -t$'\t' -k1,1V -k2,2)"
    lowest_ver="$(printf '%s\n' "$sorted" | head -n1 | cut -f1)"

    count_at_lowest="$(printf '%s\n' "$sorted" | awk -F$'\t' -v v="$lowest_ver" '$1 == v {c++} END {print c+0}')"
    if [[ "$count_at_lowest" -gt 1 ]]; then
        log "autonomous scan: WARNING: ${count_at_lowest} requirements docs at version ${lowest_ver}; picking first lexically"
    fi

    printf '%s\n' "$sorted" | head -n1 | cut -f2
    return 0
}

# ---------------------------------------------------------------------------
# scan_and_bundle_bugs
# ---------------------------------------------------------------------------
scan_and_bundle_bugs() {
    local active_rc last_released bugs_dir bug_backlog priority_dir
    local today patch_ver req_doc_name req_doc_path
    local f slug summary line already_bundled

    active_rc="$(get_release_state_field "Active RC" 2>/dev/null || true)"
    if [[ -n "$active_rc" && "$active_rc" != "none" ]]; then
        log "autonomous scan (bugs): Active RC is ${active_rc}, skipping bug scan"
        return 1
    fi

    if [[ -f "${TEAM_ROOT}/HALT" ]]; then
        log "autonomous scan (bugs): HALT flag present, skipping bug scan"
        return 1
    fi

    last_released="$(get_release_state_field "Last Released" 2>/dev/null || true)"
    if [[ -z "$last_released" || "$last_released" == "none" ]]; then
        log "autonomous scan (bugs): cannot determine Last Released from release-state.md, skipping"
        return 1
    fi

    bugs_dir="$(pp_bugs_dir "${_CURRENT_PROJECT}")"
    bug_backlog="$(pp_queue_path "${_CURRENT_PROJECT}" "bug")"
    priority_dir="$(pp_requirements_dir "${_CURRENT_PROJECT}")/priority"

    local bundled_ids=()
    if [[ -f "$bug_backlog" ]]; then
        while IFS= read -r line; do
            local bid
            bid="$(printf '%s' "$line" | grep -oE '\[x\]\s+BUG-[0-9]+-[A-Za-z0-9-]+' | grep -oE 'BUG-[0-9]+-[A-Za-z0-9-]+' | head -n1 || true)"
            [[ -n "$bid" ]] && bundled_ids+=("$bid")
        done < "$bug_backlog"
    fi

    local unhandled_slugs=()
    local unhandled_files=()
    local unhandled_summaries=()

    for f in "$bugs_dir"/BUG-*.md; do
        [[ -f "$f" ]] || continue
        local basename_f
        basename_f="$(basename "$f")"
        [[ "$basename_f" == "BUG-TEMPLATE.md" ]] && continue
        [[ "$basename_f" == "README.md" ]] && continue

        slug="${basename_f%.md}"

        already_bundled=false
        local bid
        for bid in "${bundled_ids[@]+"${bundled_ids[@]}"}"; do
            if [[ "$bid" == "$slug" ]]; then
                already_bundled=true
                break
            fi
        done
        [[ "$already_bundled" == "true" ]] && continue

        summary="$(grep -m1 '^# ' "$f" 2>/dev/null | sed 's/^# //' || true)"
        if [[ -z "$summary" ]]; then
            summary="$slug"
        fi

        unhandled_slugs+=("$slug")
        unhandled_files+=("$f")
        unhandled_summaries+=("$summary")
    done

    local bug_count="${#unhandled_slugs[@]}"
    if [[ "$bug_count" -eq 0 ]]; then
        log "autonomous scan (bugs): no unhandled bugs found"
        return 1
    fi

    log "autonomous scan (bugs): found ${bug_count} unhandled bug(s), bundling"

    local ver_stripped major minor patch
    ver_stripped="${last_released#v}"
    major="$(printf '%s' "$ver_stripped" | cut -d. -f1)"
    minor="$(printf '%s' "$ver_stripped" | cut -d. -f2)"
    patch="$(printf '%s' "$ver_stripped" | cut -d. -f3)"
    patch=$(( patch + 1 ))
    patch_ver="v${major}.${minor}.${patch}"

    today="$(date +%Y%m%d)"
    req_doc_name="${patch_ver}-bugfix-bundle-${today}.md"
    req_doc_path="${priority_dir}/${req_doc_name}"

    mkdir -p "$priority_dir"

    local bug_sections=""
    local i
    for i in "${!unhandled_slugs[@]}"; do
        local s="${unhandled_slugs[$i]}"
        local sv="${unhandled_summaries[$i]}"
        local rel_path="${bugs_dir#${KANBAN_ROOT}/}/${s}.md"
        bug_sections="${bug_sections}
#### ${s}
- **File:** ${rel_path}
- **Summary:** ${sv}
"
    done

    local dev_tree_path
    dev_tree_path="${PP_dev_tree_path:-${PGAI_DEV_TREE_PATH:-}}"
    local project_git_repo="none"
    if [[ -n "${PP_git_repo_url:-}" ]]; then
        project_git_repo="${PP_git_repo_url}"
    elif [[ -d "${dev_tree_path}/.git" ]]; then
        project_git_repo="$(git -C "${dev_tree_path}" remote get-url origin 2>/dev/null || echo "none")"
    fi

    cat > "$req_doc_path" <<EOF
# Requirements: ${patch_ver} — Bug Fix Bundle ($(date +%Y-%m-%d))

## Target Version
${patch_ver}

## Workflow Type
release

## Test Required
true

## Working Directory
${dev_tree_path}

## Git Repo
${project_git_repo}

## Summary
Priority bug fix bundle containing ${bug_count} unhandled bug report(s).
The Working Directory above is propagated to every task PM decomposes from this brief.
Each worker task operates in a per-task worktree off the RC branch.

## Scope

### Bug Reports
${bug_sections}
## Suggested Decomposition
(Leave this to PM to determine during decomposition)

## Acceptance Criteria
- [ ] All listed bugs are addressed
- [ ] No regressions introduced
EOF

    log "autonomous scan (bugs): wrote priority requirements doc ${req_doc_path}"
    log "autonomous scan (bugs): project Working Directory = ${dev_tree_path}"
    log "autonomous scan (bugs): project Git Repo = ${project_git_repo}"

    mkdir -p "$(dirname "$bug_backlog")"
    if [[ ! -f "$bug_backlog" ]]; then
        cat > "$bug_backlog" <<'BEOF'
# Bug Agent Backlog

<!-- Managed by wake script bug scan on PM's behalf. -->
<!-- Format: - [x] BUG-NNNN-slug — summary -->

## Queue

BEOF
    elif ! grep -q '^## Queue' "$bug_backlog"; then
        printf '\n## Queue\n\n' >> "$bug_backlog"
    fi

    for i in "${!unhandled_slugs[@]}"; do
        local entry="- [x] ${unhandled_slugs[$i]} — ${unhandled_summaries[$i]}"
        printf '%s\n' "$entry" >> "$bug_backlog"
        log "autonomous scan (bugs): marked bundled: ${unhandled_slugs[$i]}"
    done

    return 0
}

# ---------------------------------------------------------------------------
# drop_self_pm_ticket PATHS
# Create a PM task folder and append it to pm_backlog.md.
# ---------------------------------------------------------------------------
drop_self_pm_ticket() {
    local raw_paths="$1"
    local today task_id task_dir first_path brief_ver inputs_block line

    today="$(date +%Y%m%d)"

    first_path="$(printf '%s\n' "$raw_paths" | head -n1 | tr -d '[:space:]')"
    brief_ver="$(semver_from_filename "$first_path" 2>/dev/null || true)"
    if [[ -z "$brief_ver" ]]; then
        log "drop_self_pm_ticket: cannot parse version from '${first_path}'; skipping ticket creation"
        return 0
    fi

    task_id="$(kanban_task_id "${TASKS_ROOT}" "PM" "${today}" "decompose-${brief_ver}")"
    task_dir="${TASKS_ROOT}/${task_id}"

    if [[ -d "$task_dir" ]]; then
        log "drop_self_pm_ticket: task ${task_id} already exists, skipping"
        return 0
    fi

    mkdir -p "$task_dir"

    inputs_block=""
    while IFS= read -r line; do
        [[ -z "$line" ]] && continue
        inputs_block="${inputs_block}- ${line}"$'\n'
    done <<< "$raw_paths"
    inputs_block="${inputs_block%$'\n'}"

    local pm_dev_tree
    # PP_dev_tree_path is the per-project override (set by pp_load_config);
    # fall back to the process-wide default only when the per-project value
    # is unset.  Matches the resolution pattern in scan_and_bundle_bugs.
    pm_dev_tree="${PP_dev_tree_path:-${PGAI_DEV_TREE_PATH:-}}"

    cat > "${task_dir}/README.md" <<EOF
# Task: Autonomous PM — requirements brief ${brief_ver}

## Task ID
${task_id}

## Owner
Claude

## Role
PM

## Assigned Agent
none

## Working Directory
${pm_dev_tree}

## Goal
Decompose requirements brief(s) into an implementation plan

## Inputs
${inputs_block}

## Prerequisites
none

## Release Version
${brief_ver}

## Notes
Self-ticket created by wake script autonomous scan on ${today}.
EOF

    cat > "${task_dir}/status.md" <<EOF
# Status

## Task
${task_id}

## Participant
Claude

## Role
PM

## State
BACKLOG

## Summary
Autonomous PM self-ticket for requirements brief ${brief_ver}.

## Artifacts
none

## Blockers
none

## Blocked By Agent
none

## Blocked Reason
none

## Needs Human
no

## Next Recommended Step
Process requirements brief(s) and produce a plan JSON.

## Instruction Conflicts
none
EOF

    local pm_backlog
    pm_backlog="$(pp_queue_path "${_CURRENT_PROJECT}" "pm")"
    mkdir -p "$(dirname "$pm_backlog")"
    if [[ ! -f "$pm_backlog" ]]; then
        cat > "$pm_backlog" <<'QEOF'
# PM Backlog

<!-- Auto-managed by wake script. One task per line. -->
<!-- Format: - [ ] TASK-ID -->

QEOF
    fi
    printf -- '- [ ] %s\n' "$task_id" >> "$pm_backlog"

    log "drop_self_pm_ticket: created task ${task_id} (${brief_ver}) from ${first_path}"
}
