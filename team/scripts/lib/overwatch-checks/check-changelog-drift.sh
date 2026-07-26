#!/usr/bin/env bash
# team/scripts/lib/overwatch-checks/check-changelog-drift.sh
#
# OVERWATCH Tier-1 detection module: detect CHANGELOG.md drift on the main
# branch and regenerate-and-commit when drift is present.
#
# Drift definition: the checked-in CHANGELOG.md at the dev tree root differs
# from a fresh regeneration produced by changelog_writer.py against the current
# bug ledger.  This drift occurs when new bug files are filed on ai_main after
# a release (the release step updates CHANGELOG, but subsequent bug filings do
# not trigger a re-run).
#
# Detection logic:
#   1. Resolve the project's dev tree path and branch_prefix from project.cfg.
#   2. Verify the dev tree's current HEAD branch is the main branch (branch_prefix
#      + "main", e.g. "ai_main").  Skip silently when HEAD is on any other branch
#      (RC branches, feature branches, detached HEAD).  Committing to an RC
#      branch mid-release would corrupt the release pipeline.
#   3. Regenerate CHANGELOG.md to a temp buffer via changelog_writer.py.
#   4. Byte-compare the buffer against the checked-in CHANGELOG.md.
#   5. When they match: log "changelog-drift-none" and return 0 (no drift).
#   6. When they differ: install the buffer as CHANGELOG.md, stage it, and
#      commit to ai_main using git commit.  Log "changelog-drift-fixed" and
#      return 0.
#
# Auto-fix class: deterministic write — the changelog_writer is idempotent
# and its output is fully determined by the bug ledger and release notes.
# Regeneration is safe to retry without human review.
#
# Skip conditions (return 0 silently):
#   - Dev tree path not configured or directory absent.
#   - HEAD is not on the main branch.
#   - CHANGELOG.md absent from dev tree (unusual state; do not create it here).
#   - Regeneration fails (Python/import error); logs the failure and returns 0
#     so the sweep continues rather than halting on a transient error.
#   - An active RC is present for the project (release pipeline owns the
#     changelog during an active RC; OVERWATCH must not interfere).
#
# Module contract:
#   - Sourceable without side effects.
#   - Exports: overwatch_check_changelog_drift
#   - Zero arguments.  All context from environment variables.
#   - Returns 0 on success (drift detected and fixed, no drift, or skipped).
#   - Returns 1 on internal error (missing required libraries, env resolution).
#
# Required environment variables (set by OVERWATCH driver or sweep runner):
#   KANBAN_ROOT       — absolute path to the kanban root
#   OVERWATCH_PROJECT — project name (e.g. "pgai-agent-kanban")
#
# Optional:
#   PGAI_DEV_TREE_PATH            — fallback dev tree path
#   PGAI_AGENT_KANBAN_TEMP_DIR    — temp root for changelog regen buffer
#
# Usage (standalone):
#   bash check-changelog-drift.sh [--dry-run]
#
# Exit codes:
#   0 — completed (no drift, drift fixed, or skipped due to guard condition)
#   1 — internal error (missing libraries or unresolvable environment)
#
# --dry-run: detects drift but does NOT modify CHANGELOG.md or commit.

# ---------------------------------------------------------------------------
# _ccd_resolve_env
# Resolve KANBAN_ROOT, OVERWATCH_PROJECT, dev tree path, and branch prefix.
# Sets _CCD_DEV_TREE and _CCD_BRANCH_PREFIX in the calling scope.
# Returns 0 on success, 1 on failure.
# ---------------------------------------------------------------------------
_ccd_resolve_env() {
    if [[ -z "${KANBAN_ROOT:-}" ]]; then
        KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-${HOME}/pgai_agent_kanban}"
    fi
    if [[ ! -d "${KANBAN_ROOT}" ]]; then
        echo "check-changelog-drift: KANBAN_ROOT does not exist: ${KANBAN_ROOT}" >&2
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
            echo "check-changelog-drift: ERROR: OVERWATCH_PROJECT is not set and multiple projects are registered." >&2
            echo "  Set OVERWATCH_PROJECT to the target project name before running this check." >&2
            return 1
        fi
        OVERWATCH_PROJECT="$(echo "${_all_projects}" | grep '[^[:space:]]' | head -n1)"
        if [[ -z "${OVERWATCH_PROJECT:-}" ]]; then
            echo "check-changelog-drift: ERROR: no project specified and none resolvable from projects.cfg" >&2
            return 1
        fi
    fi

    # Resolve dev tree path from project.cfg (falls back to PROJECT.cfg, then env).
    local _proj_root="${KANBAN_ROOT}/projects/${OVERWATCH_PROJECT}"
    local _cfg_file=""
    if [[ -f "${_proj_root}/project.cfg" ]]; then
        _cfg_file="${_proj_root}/project.cfg"
    elif [[ -f "${_proj_root}/PROJECT.cfg" ]]; then
        _cfg_file="${_proj_root}/PROJECT.cfg"
    fi

    _CCD_DEV_TREE=""
    _CCD_BRANCH_PREFIX=""
    if [[ -n "${_cfg_file}" ]]; then
        _CCD_DEV_TREE="$(grep -E '^[[:space:]]*dev_tree_path[[:space:]]*=' "${_cfg_file}" \
            | head -n1 \
            | sed 's|^[^=]*=[[:space:]]*||; s|[[:space:]]*$||; s|^["'"'"']||; s|["'"'"']$||')"
        local _raw_prefix
        _raw_prefix="$(grep -E '^[[:space:]]*branch_prefix[[:space:]]*=' "${_cfg_file}" \
            | head -n1 \
            | sed 's|^[^=]*=[[:space:]]*||; s|[[:space:]]*$||; s|^["'"'"']||; s|["'"'"']$||')"
        _CCD_BRANCH_PREFIX="${_raw_prefix:-}"
    fi

    if [[ -z "${_CCD_DEV_TREE}" ]] && [[ -n "${PGAI_DEV_TREE_PATH:-}" ]]; then
        _CCD_DEV_TREE="${PGAI_DEV_TREE_PATH}"
    fi

    if [[ -z "${_CCD_DEV_TREE}" ]]; then
        echo "check-changelog-drift: cannot resolve dev tree path (set PGAI_DEV_TREE_PATH or ensure project.cfg has dev_tree_path)" >&2
        return 1
    fi
    if [[ ! -d "${_CCD_DEV_TREE}" ]]; then
        echo "check-changelog-drift: dev tree path does not exist: ${_CCD_DEV_TREE}" >&2
        return 1
    fi

    return 0
}

# ---------------------------------------------------------------------------
# _ccd_read_active_rc <release_state_file>
# Echo the current Active RC value from release-state.md, or "none".
# ---------------------------------------------------------------------------
_ccd_read_active_rc() {
    local rs_file="$1"
    if [[ ! -f "${rs_file}" ]]; then
        echo "none"
        return 0
    fi
    local val
    val="$(awk '/^## Active RC/{found=1; next} found && /^[[:space:]]*$/{next} found{print; exit}' "${rs_file}" \
        | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
    if [[ -z "${val}" ]]; then
        echo "none"
    else
        echo "${val}"
    fi
}

# ---------------------------------------------------------------------------
# _ccd_current_branch <dev_tree>
# Echo the current HEAD branch name for the given git worktree.
# Returns empty string on detached HEAD or error.
# ---------------------------------------------------------------------------
_ccd_current_branch() {
    local dev_tree="$1"
    git -C "${dev_tree}" symbolic-ref --short HEAD 2>/dev/null || true
}

# ---------------------------------------------------------------------------
# _ccd_regenerate_changelog <dev_tree> <bugs_dir> <temp_root> <out_var>
# Regenerate CHANGELOG.md to a temp file.
# Sets the variable named by <out_var> to the temp file path on success.
# Returns 0 on success, 1 on failure.
# ---------------------------------------------------------------------------
_ccd_regenerate_changelog() {
    local dev_tree="$1"
    local bugs_dir="$2"
    local temp_root="$3"
    local out_var="$4"

    # Locate the changelog_writer module relative to the dev tree.
    # The writer lives at <dev_tree>/team/pgai_agent_kanban/cm/changelog_writer.py.
    local team_dir="${dev_tree}/team"
    if [[ ! -d "${team_dir}" ]]; then
        echo "check-changelog-drift: team/ directory not found in dev tree: ${dev_tree}" >&2
        return 1
    fi

    # Write to a temp file under the configured temp root.
    mkdir -p "${temp_root}" 2>/dev/null || true
    local tmp_file
    tmp_file="$(mktemp "${temp_root}/changelog_drift_XXXXXX.md")" || {
        echo "check-changelog-drift: failed to create temp file under ${temp_root}" >&2
        return 1
    }

    # Source pp_run_ops if not already loaded.  pp_run_ops.sh lives one directory
    # above this file (in the parent lib/ directory).  Loading it here ensures
    # cwd-independent invocation regardless of how this module was sourced.
    if ! declare -f pp_run_ops >/dev/null 2>&1; then
        local _ccd_pp_helper
        _ccd_pp_helper="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && cd .. && pwd)/pp_run_ops.sh"
        # shellcheck source=../pp_run_ops.sh
        [[ -f "${_ccd_pp_helper}" ]] && source "${_ccd_pp_helper}"
    fi

    # Regenerate.  PYTHONHASHSEED=0 ensures stable frozenset ordering across
    # independent process invocations, matching the seed used when CHANGELOG.md
    # was last committed.  PYTHONPATH is pre-set to team_dir so the caller-set
    # entry takes precedence in pp_run_ops's own-tree + KANBAN_ROOT composition.
    PYTHONHASHSEED=0 PYTHONPATH="${team_dir}" \
        pp_run_ops pgai_agent_kanban.cm.changelog_writer \
        "${dev_tree}" "${bugs_dir}" \
        > "${tmp_file}" 2>/dev/null
    local exit_code=$?

    if [[ $exit_code -ne 0 ]]; then
        rm -f "${tmp_file}" 2>/dev/null || true
        echo "check-changelog-drift: changelog_writer.py exited ${exit_code}; skipping drift check" >&2
        return 1
    fi

    # Assign the temp file path to the caller's variable.
    printf -v "${out_var}" '%s' "${tmp_file}"
    return 0
}

# ---------------------------------------------------------------------------
# overwatch_check_changelog_drift [--dry-run]
#
# Main entry point sourced by the OVERWATCH sweep runner.
#
# Detects CHANGELOG.md drift on the main branch and regenerates-and-commits
# when drift is present.  Returns 0 in all non-error cases.
# ---------------------------------------------------------------------------
overwatch_check_changelog_drift() {
    local dry_run=0
    if [[ "${1:-}" == "--dry-run" ]]; then
        dry_run=1
    fi

    # Resolve environment.
    _ccd_resolve_env || return 1

    local dev_tree="${_CCD_DEV_TREE}"
    local branch_prefix="${_CCD_BRANCH_PREFIX}"
    local main_branch="${branch_prefix}main"
    local project_name="${OVERWATCH_PROJECT}"
    local kanban_root="${KANBAN_ROOT}"

    # Guard 1: verify HEAD is on the main branch.
    # Committing to any other branch (RC, feature, detached HEAD) is unsafe.
    local current_branch
    current_branch="$(_ccd_current_branch "${dev_tree}")"
    if [[ "${current_branch}" != "${main_branch}" ]]; then
        echo "check-changelog-drift: [${project_name}] HEAD is '${current_branch}', not '${main_branch}'; skipping drift check." >&2
        return 0
    fi

    # Guard 2: skip when an active RC is in progress.
    # The release pipeline owns CHANGELOG.md during an active RC.
    local release_state_file="${kanban_root}/projects/${project_name}/release-state.md"
    local active_rc
    active_rc="$(_ccd_read_active_rc "${release_state_file}")"
    if [[ "${active_rc}" != "none" ]]; then
        echo "check-changelog-drift: [${project_name}] active RC '${active_rc}' in progress; skipping drift check." >&2
        return 0
    fi

    # Guard 3: CHANGELOG.md must exist in the dev tree.
    local changelog_path="${dev_tree}/CHANGELOG.md"
    if [[ ! -f "${changelog_path}" ]]; then
        echo "check-changelog-drift: [${project_name}] CHANGELOG.md not found at ${changelog_path}; skipping." >&2
        return 0
    fi

    # Resolve the project-scoped bug ledger directory.
    local bugs_dir="${kanban_root}/projects/${project_name}/bugs"
    if [[ ! -d "${bugs_dir}" ]]; then
        echo "check-changelog-drift: [${project_name}] bug ledger not found at ${bugs_dir}; skipping." >&2
        return 0
    fi

    # Resolve temp root for the regeneration buffer.
    # anti-pattern-allowlist: 2 (justification: the literal is the resolver's
    # documented last-resort fallback, mirroring the behaviour of temp.sh's
    # pgai_temp_dir(); callers should set PGAI_AGENT_KANBAN_TEMP_DIR)
    local temp_root
    temp_root="${PGAI_AGENT_KANBAN_TEMP_DIR:-/tmp/pgai_kanban_tmp}/changelog_drift"

    # Regenerate CHANGELOG.md to a temp file.
    local regen_file=""
    if ! _ccd_regenerate_changelog "${dev_tree}" "${bugs_dir}" "${temp_root}" regen_file; then
        # Regeneration failed (transient error); log and skip without failing the sweep.
        if declare -f overwatch_log_action >/dev/null 2>&1; then
            overwatch_log_action \
                "check-changelog-drift" \
                "${project_name}" \
                "changelog-drift-regen-error" \
                "none" \
                "changelog_writer.py failed; drift check skipped" \
            2>/dev/null || true
        fi
        return 0
    fi

    # Byte-compare the regeneration buffer against the checked-in artifact.
    local drift_detected=0
    if ! cmp -s "${changelog_path}" "${regen_file}"; then
        drift_detected=1
    fi

    if [[ $drift_detected -eq 0 ]]; then
        echo "check-changelog-drift: [${project_name}] CHANGELOG.md is fresh (no drift)." >&2
        rm -f "${regen_file}" 2>/dev/null || true

        if declare -f overwatch_log_action >/dev/null 2>&1; then
            overwatch_log_action \
                "check-changelog-drift" \
                "${project_name}" \
                "changelog-drift-none" \
                "none" \
                "CHANGELOG.md matches fresh regeneration" \
            2>/dev/null || true
        fi
        return 0
    fi

    # Drift detected.
    echo "check-changelog-drift: [${project_name}] CHANGELOG.md drift detected on ${main_branch}." >&2

    if [[ $dry_run -eq 1 ]]; then
        echo "check-changelog-drift: [${project_name}] dry-run mode — not committing regenerated CHANGELOG.md." >&2
        rm -f "${regen_file}" 2>/dev/null || true

        if declare -f overwatch_log_action >/dev/null 2>&1; then
            overwatch_log_action \
                "check-changelog-drift" \
                "${project_name}" \
                "changelog-drift-dry-run" \
                "none" \
                "drift detected on ${main_branch}; dry-run — no commit made" \
            2>/dev/null || true
        fi
        return 0
    fi

    # Backup the current CHANGELOG.md before overwriting.
    local backup_path=""
    if declare -f overwatch_backup_file >/dev/null 2>&1; then
        overwatch_backup_file "${changelog_path}" "${project_name}" \
        2>/dev/null || true
        # overwatch_backup_file writes the backup path to its own log; we pass
        # "none" to overwatch_log_action below since we do not capture the path.
        backup_path="none"
    fi

    # Install the fresh regeneration as CHANGELOG.md.
    if ! cp "${regen_file}" "${changelog_path}"; then
        echo "check-changelog-drift: [${project_name}] failed to install regenerated CHANGELOG.md" >&2
        rm -f "${regen_file}" 2>/dev/null || true
        return 0
    fi
    rm -f "${regen_file}" 2>/dev/null || true

    # Stage and commit.
    local commit_msg="Regenerate CHANGELOG.md (drift detected by OVERWATCH sweep)"
    local commit_rc=0

    git -C "${dev_tree}" add "CHANGELOG.md" 2>/dev/null || {
        echo "check-changelog-drift: [${project_name}] git add CHANGELOG.md failed" >&2
        return 0
    }

    git -C "${dev_tree}" commit -m "${commit_msg}" 2>/dev/null || {
        commit_rc=$?
        echo "check-changelog-drift: [${project_name}] git commit failed (rc=${commit_rc})" >&2
        # Unstage to leave the working tree clean.
        git -C "${dev_tree}" reset HEAD CHANGELOG.md 2>/dev/null || true
        return 0
    }

    echo "check-changelog-drift: [${project_name}] CHANGELOG.md regenerated and committed to ${main_branch}." >&2

    if declare -f overwatch_log_action >/dev/null 2>&1; then
        overwatch_log_action \
            "check-changelog-drift" \
            "${project_name}" \
            "changelog-drift-fixed" \
            "${backup_path:-none}" \
            "CHANGELOG.md drift on ${main_branch} regenerated and committed" \
        2>/dev/null || true
    fi

    return 0
}

# ---------------------------------------------------------------------------
# Standalone invocation support.
# When this file is executed directly (not sourced), resolve environment and
# call the check function.
# ---------------------------------------------------------------------------
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    # Source required libraries when running standalone.
    _CCD_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    _CCD_LIB_DIR="$(cd "${_CCD_SELF_DIR}/.." && pwd)"

    for _lib in temp.sh project_paths.sh projects.sh overwatch_lib.sh overwatch_protocol.sh; do
        if [[ -f "${_CCD_LIB_DIR}/${_lib}" ]]; then
            # shellcheck source=/dev/null
            source "${_CCD_LIB_DIR}/${_lib}" 2>/dev/null || true
        fi
    done
    unset _lib _CCD_LIB_DIR _CCD_SELF_DIR

    overwatch_check_changelog_drift "$@"
    exit $?
fi
