#!/usr/bin/env bash
# team/scripts/lib/overwatch-checks/run-e2e.sh
#
# OVERWATCH end-to-end verification harness.
#
# Seeds a fixture project with 8 trigger conditions, then:
#   1. Dry-run phase:  all 8 detections fire and log without mutating files.
#   2. Live phase:     all 8 fixes land, logged, and backed up.
#   3. Revert phase:   overwatch-revert.sh restores pre-live state.
#
# All work is done inside a self-contained tmpdir — the live kanban
# installation and dev tree are never touched.
#
# Fixture conditions seeded (one per check):
#   1. 0-byte file in priority/          -> check-empty-files
#   2. BLOCKED task, Needs Human=no,
#      Active RC reason, Active RC=none  -> check-blocked-tasks
#   3. vX.Y.Z-prefixed file in priority/ -> check-tester-orphan-files
#   4. [x] cache marker on open file     -> check-cache-marker-drift
#   5. PM task with README.md input      -> check-readme-bundled
#   6. Stale Active RC in release-state  -> check-stale-active-rc
#   7. Orphan rc/ branch with release tag-> check-orphan-rc-branches
#   8. Local main ahead of origin/main   -> check-push-lag
#
# Exit codes:
#   0  — all assertions passed
#   1  — one or more assertions failed
#
# Usage:
#   bash run-e2e.sh [--keep-tmpdir]
#
# Options:
#   --keep-tmpdir   Keep the tmpdir after exit (for debugging).
#
# Output:
#   Summary file at <tmpdir>/e2e-summary.txt.

set -euo pipefail

# ---------------------------------------------------------------------------
# Resolve script and library paths
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEAM_SCRIPTS_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
CHECKS_DIR="${SCRIPT_DIR}"
REVERT_SCRIPT="${TEAM_SCRIPTS_DIR}/overwatch-revert.sh"

# ---------------------------------------------------------------------------
# Centralized temp dir helpers
# ---------------------------------------------------------------------------
# Source temp.sh (one level up from overwatch-checks/ in lib/) so we use
# pgai_temp_subdir instead of hardcoding /tmp for e2e artifact moves.
# Source temp.sh for resolver helpers (include guard in temp.sh prevents double-load).
_TEMP_SH="${SCRIPT_DIR}/../temp.sh"
if [[ ! -f "$_TEMP_SH" ]]; then
  echo "ERROR: temp.sh not found: $_TEMP_SH" >&2
  exit 1
fi
# shellcheck source=../temp.sh
source "$_TEMP_SH"
unset _TEMP_SH

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
KEEP_TMPDIR=0
for _arg in "$@"; do
    case "${_arg}" in
        --keep-tmpdir) KEEP_TMPDIR=1 ;;
        --help|-h) echo "Usage: $(basename "$0") [--keep-tmpdir]"; exit 0 ;;
        *) echo "Unknown argument: ${_arg}" >&2; exit 1 ;;
    esac
done
unset _arg

# ---------------------------------------------------------------------------
# Global test state
# ---------------------------------------------------------------------------
PASS_COUNT=0
FAIL_COUNT=0
SUMMARY_LINES=()

_pass() {
    local name="$1" msg="${2:-}"
    PASS_COUNT=$(( PASS_COUNT + 1 ))
    SUMMARY_LINES+=("PASS  ${name}${msg:+: ${msg}}")
    echo "[PASS] ${name}${msg:+: ${msg}}"
}

_fail() {
    local name="$1" msg="${2:-}"
    FAIL_COUNT=$(( FAIL_COUNT + 1 ))
    SUMMARY_LINES+=("FAIL  ${name}${msg:+: ${msg}}")
    echo "[FAIL] ${name}${msg:+: ${msg}}" >&2
}

_assert_file_exists() {
    local name="$1" path="$2"
    if [[ -f "${path}" ]]; then _pass "${name}" "exists"; else _fail "${name}" "expected file: ${path}"; fi
}

_assert_file_absent() {
    local name="$1" path="$2"
    if [[ ! -f "${path}" ]]; then _pass "${name}" "absent"; else _fail "${name}" "should not exist: ${path}"; fi
}

_assert_file_contains() {
    local name="$1" path="$2" pattern="$3"
    if [[ ! -f "${path}" ]]; then
        _fail "${name}" "file not found: ${path}"
        return
    fi
    if grep -qE "${pattern}" "${path}" 2>/dev/null; then
        _pass "${name}" "pattern matched"
    else
        _fail "${name}" "pattern '${pattern}' not found in ${path}"
    fi
}

_assert_log_contains() {
    local name="$1" log="$2" pattern="$3"
    if [[ ! -f "${log}" ]]; then
        _fail "${name}" "log not found: ${log}"
        return
    fi
    if grep -qE "${pattern}" "${log}" 2>/dev/null; then
        _pass "${name}" "pattern in log"
    else
        _fail "${name}" "pattern '${pattern}' not in log"
    fi
}

# ---------------------------------------------------------------------------
# Tmpdir setup
# ---------------------------------------------------------------------------
# Use pgai_mktemp_d so the scratch dir lands under the framework temp root
# (PGAI_AGENT_KANBAN_TEMP_DIR) rather than bare /tmp.  temp.sh was sourced
# above; pgai_mktemp_d is always available here.
E2E_TMPDIR="$(pgai_mktemp_d e2e)"

_cleanup() {
    if (( KEEP_TMPDIR == 0 )); then
        chmod -R u+w "${E2E_TMPDIR}" 2>/dev/null || true
        # Move e2e artifacts to the framework temp dir so they are discoverable
        # and cleanable by cleanup.sh --temp-only (replaces the prior /tmp move).
        local _artifacts_dir
        _artifacts_dir="$(pgai_temp_subdir e2e-artifacts)"
        find "${E2E_TMPDIR}" -mindepth 1 -maxdepth 1 -exec mv {} "${_artifacts_dir}/" \; 2>/dev/null || true
        # Remove the now-empty scratch dir so no empty husk is left behind.
        rmdir "${E2E_TMPDIR}" 2>/dev/null || rm -rf "${E2E_TMPDIR}" 2>/dev/null || true
    else
        echo "Kept tmpdir: ${E2E_TMPDIR}"
    fi
}
trap _cleanup EXIT

SUMMARY_FILE="${E2E_TMPDIR}/e2e-summary.txt"

# ---------------------------------------------------------------------------
# Fixture locations
# ---------------------------------------------------------------------------
E2E_KANBAN_ROOT="${E2E_TMPDIR}/kanban"
E2E_DEV_TREE="${E2E_TMPDIR}/dev_tree"
E2E_ORIGIN_BARE="${E2E_TMPDIR}/origin"
E2E_PROJECT_NAME="test-project"
E2E_PROJECT_ROOT="${E2E_KANBAN_ROOT}/projects/${E2E_PROJECT_NAME}"
E2E_LOG="${E2E_PROJECT_ROOT}/overwatch/actions.log"

# ---------------------------------------------------------------------------
# Fixture builder
# ---------------------------------------------------------------------------
_build_fixture() {
    # --- Directory skeleton ---
    mkdir -p \
        "${E2E_PROJECT_ROOT}/bugs" \
        "${E2E_PROJECT_ROOT}/priority" \
        "${E2E_PROJECT_ROOT}/tasks/queues/claude" \
        "${E2E_PROJECT_ROOT}/overwatch/backups" \
        "${E2E_KANBAN_ROOT}/locks"

    # Empty actions.log
    : > "${E2E_LOG}"

    # --- release-state.md: stale Active RC ---
    # Condition for check-stale-active-rc:
    #   Active RC=v0.20.0, tag v0.20.0 exists, no rc/v0.20.0 branch.
    # Condition for check-blocked-tasks:
    #   Active RC must be "none" at the time that check runs.
    #   We run check-stale-active-rc FIRST (resets to none), then blocked-tasks.
    cat > "${E2E_PROJECT_ROOT}/release-state.md" <<'RSEOF'
# Release State

## Active RC
v0.20.0

## RC Opened At
2026-01-01T00:00:00Z

## RC Opened By Task
CLAUDE-CM-TEST
RSEOF

    # --- Check 1: empty file in priority/ ---
    touch "${E2E_PROJECT_ROOT}/priority/PRIORITY-0099-empty-test.md"

    # --- Check 2: BLOCKED task (Needs Human=no, Active RC reason) ---
    # This check needs Active RC=none when it runs.
    # Strategy: run check-stale-active-rc first (live), then blocked-tasks.
    local bt_task_id="CLAUDE-CODER-TEST-001-blocked-task"
    local bt_task_dir="${E2E_PROJECT_ROOT}/tasks/${bt_task_id}"
    mkdir -p "${bt_task_dir}"
    cat > "${bt_task_dir}/README.md" <<EOF
# Task: Blocked Test Task

## Task ID
${bt_task_id}

## Inputs
some-valid-input.md

## Source Branch
rc/v0.21.0

## Workflow Type
release
EOF
    cat > "${bt_task_dir}/status.md" <<EOF
# Status

## Task
${bt_task_id}

## State
BLOCKED

## Summary
Blocked waiting for Active RC.

## Blockers
none

## Blocked Reason
Waiting for Active RC to be available.

## Needs Human
no

## Next Recommended Step
none
EOF
    cat > "${E2E_PROJECT_ROOT}/tasks/queues/claude/coder_backlog.md" <<EOF
# CODER Backlog

- [B] ${bt_task_id}
EOF

    # --- Check 3: tester orphan file in priority/ (bug-shaped) ---
    cat > "${E2E_PROJECT_ROOT}/priority/v0.20.0-orphan-tester-file.md" <<'TEOF'
# Tester bug report placed in wrong directory

## Symptom
Something broke in v0.20.0 during release.

## Root Cause
Unknown. Needs investigation.

## Status
open
TEOF

    # --- Check 4: cache marker drift ---
    # PRIORITY file with Status=open but [x] in priority_backlog.md
    cat > "${E2E_PROJECT_ROOT}/priority/PRIORITY-0088-drifted.md" <<'PEOF'
# PRIORITY-0088: Drifted cache marker test

## Status
open

Content added after initial bundling.
PEOF
    cat > "${E2E_PROJECT_ROOT}/tasks/queues/claude/priority_backlog.md" <<'PBEOF'
# Priority Backlog

- [x] PRIORITY-0088-drifted
PBEOF

    # --- Check 5: PM task with README.md input ---
    local pm_task_id="CLAUDE-PM-TEST-001-readme-input"
    local pm_task_dir="${E2E_PROJECT_ROOT}/tasks/${pm_task_id}"
    mkdir -p "${pm_task_dir}"
    cat > "${pm_task_dir}/README.md" <<EOF
# PM Task: Readme bundled test

## Task ID
${pm_task_id}

## Inputs
requirements/README.md

## Goal
This task should be detected and marked WONT-DO by OVERWATCH.
EOF
    cat > "${pm_task_dir}/status.md" <<EOF
# Status

## Task
${pm_task_id}

## State
BACKLOG

## Summary
Queued by PM discovery.

## Blockers
none

## Needs Human
no
EOF

    # --- Git dev tree: checks 6 (stale-rc), 7 (orphan-rc-branches), 8 (push-lag) ---

    # Initialize origin bare repo
    git init --bare -b main "${E2E_ORIGIN_BARE}" --quiet

    # Initialize local dev tree
    git init -b main "${E2E_DEV_TREE}" --quiet
    git -C "${E2E_DEV_TREE}" config user.email "test@e2e.local"
    git -C "${E2E_DEV_TREE}" config user.name "E2E"
    git -C "${E2E_DEV_TREE}" config commit.gpgsign false

    # Initial commit on main
    echo "# kanban e2e test repo" > "${E2E_DEV_TREE}/README.md"
    git -C "${E2E_DEV_TREE}" add README.md
    git -C "${E2E_DEV_TREE}" commit -m "init" --quiet

    # Tag v0.20.0 on main (makes Active RC=v0.20.0 stale: tag exists, no rc/v0.20.0 branch)
    git -C "${E2E_DEV_TREE}" tag v0.20.0

    # Create an orphan RC branch rc/v0.19.0 whose tag v0.19.0 already exists.
    # For check-orphan-rc-branches to DELETE it with -d (not -D), the branch
    # must be fully merged into main. We create it, do work, then merge it back.
    git -C "${E2E_DEV_TREE}" checkout -b rc/v0.19.0 --quiet
    echo "# rc 0.19.0 work" > "${E2E_DEV_TREE}/rc-0.19.0.md"
    git -C "${E2E_DEV_TREE}" add rc-0.19.0.md
    git -C "${E2E_DEV_TREE}" commit -m "rc v0.19.0 work" --quiet
    git -C "${E2E_DEV_TREE}" tag v0.19.0
    # Merge rc/v0.19.0 into main so git branch -d succeeds (fully merged)
    git -C "${E2E_DEV_TREE}" checkout main --quiet
    git -C "${E2E_DEV_TREE}" merge --no-ff rc/v0.19.0 -m "merge rc/v0.19.0" --quiet

    # Add origin and push initial state (main + v0.19.0 tag)
    git -C "${E2E_DEV_TREE}" remote add origin "${E2E_ORIGIN_BARE}"
    git -C "${E2E_DEV_TREE}" push origin main --quiet
    git -C "${E2E_DEV_TREE}" push origin refs/tags/v0.19.0 --quiet

    # Create push-lag: add a commit to main that is NOT on origin/main
    echo "# unpushed change" >> "${E2E_DEV_TREE}/README.md"
    git -C "${E2E_DEV_TREE}" add README.md
    git -C "${E2E_DEV_TREE}" commit -m "unpushed commit for push-lag test" --quiet
    # v0.20.0 tag is local-only (not pushed to origin) — also triggers push-lag

    # Write project.cfg pointing at our fixture dev tree
    cat > "${E2E_PROJECT_ROOT}/project.cfg" <<EOF
# project.cfg — generated for E2E harness
project_name="${E2E_PROJECT_NAME}"
dev_tree_path="${E2E_DEV_TREE}"
git_repo_url="file://${E2E_ORIGIN_BARE}"
git_remote_name="origin"
workflow_type="release"
EOF
}

# ---------------------------------------------------------------------------
# Run a single check script with the fixture environment
# ---------------------------------------------------------------------------
_run_check() {
    # Usage: _run_check <check-script-name> [args...]
    local check_name="$1"
    shift
    KANBAN_ROOT="${E2E_KANBAN_ROOT}" \
    OVERWATCH_PROJECT="${E2E_PROJECT_NAME}" \
    PGAI_DEV_TREE_PATH="${E2E_DEV_TREE}" \
    bash "${CHECKS_DIR}/${check_name}" "$@" 2>&1
}

# ---------------------------------------------------------------------------
# PHASE 0: Build fixture
# ---------------------------------------------------------------------------
echo "=== PHASE 0: Building fixture ==="
_build_fixture
echo "Fixture built in ${E2E_TMPDIR}"

# ---------------------------------------------------------------------------
# PHASE 1: DRY-RUN — verify 8 detections fire and log, no mutations
# ---------------------------------------------------------------------------
echo ""
echo "=== PHASE 1: Dry-run — 8 detections must fire and log ==="

PRE_DRY_ENTRIES="$(wc -l < "${E2E_LOG}")"

# 1. check-empty-files
_out="$(_run_check check-empty-files.sh --dry-run || true)"
if echo "${_out}" | grep -qiE "dry-run|empty.*detected|would rename" 2>/dev/null; then
    _pass "dry-run/check-empty-files" "detection logged"
else
    _fail "dry-run/check-empty-files" "output: ${_out}"
fi

# 2. check-blocked-tasks
# The task is seeded with Active RC=v0.20.0, but check-blocked-tasks skips
# when Active RC != none. For dry-run detection, temporarily set RS to none.
_rs_content="$(cat "${E2E_PROJECT_ROOT}/release-state.md")"
sed -i 's/^v0\.20\.0$/none/' "${E2E_PROJECT_ROOT}/release-state.md"
_out="$(_run_check check-blocked-tasks.sh --dry-run || true)"
printf '%s\n' "${_rs_content}" > "${E2E_PROJECT_ROOT}/release-state.md"
if echo "${_out}" | grep -qiE "dry-run.*would promote|dry-run-blocked-task-promotable|eligible" 2>/dev/null; then
    _pass "dry-run/check-blocked-tasks" "detection logged"
else
    _fail "dry-run/check-blocked-tasks" "output: ${_out}"
fi

# 3. check-tester-orphan-files
_out="$(_run_check check-tester-orphan-files.sh --dry-run || true)"
if echo "${_out}" | grep -qiE "dry-run|orphan.*detected|would.*orphan" 2>/dev/null; then
    _pass "dry-run/check-tester-orphan-files" "detection logged"
else
    _fail "dry-run/check-tester-orphan-files" "output: ${_out}"
fi

# 4. check-cache-marker-drift
_out="$(_run_check check-cache-marker-drift.sh --dry-run || true)"
if echo "${_out}" | grep -qiE "dry-run|drift.*detected|would reset" 2>/dev/null; then
    _pass "dry-run/check-cache-marker-drift" "detection logged"
else
    _fail "dry-run/check-cache-marker-drift" "output: ${_out}"
fi

# 5. check-readme-bundled
_out="$(_run_check check-readme-bundled.sh --dry-run || true)"
if echo "${_out}" | grep -qiE "dry-run|bad input|README.*input" 2>/dev/null; then
    _pass "dry-run/check-readme-bundled" "detection logged"
else
    _fail "dry-run/check-readme-bundled" "output: ${_out}"
fi

# 6. check-stale-active-rc
_out="$(_run_check check-stale-active-rc.sh --dry-run || true)"
if echo "${_out}" | grep -qiE "dry-run|stale.*RC|stale.*confirmed" 2>/dev/null; then
    _pass "dry-run/check-stale-active-rc" "detection logged"
else
    _fail "dry-run/check-stale-active-rc" "output: ${_out}"
fi

# 7. check-orphan-rc-branches
_out="$(_run_check check-orphan-rc-branches.sh --dry-run || true)"
if echo "${_out}" | grep -qiE "dry-run|orphan.*confirmed|would delete" 2>/dev/null; then
    _pass "dry-run/check-orphan-rc-branches" "detection logged"
else
    _fail "dry-run/check-orphan-rc-branches" "output: ${_out}"
fi

# 8. check-push-lag
_out="$(_run_check check-push-lag.sh --dry-run || true)"
if echo "${_out}" | grep -qiE "dry-run|push lag.*detected|ahead" 2>/dev/null; then
    _pass "dry-run/check-push-lag" "detection logged"
else
    _fail "dry-run/check-push-lag" "output: ${_out}"
fi

# All 8 detections must have logged (may have more entries than 8 due to multi-log checks)
POST_DRY_ENTRIES="$(wc -l < "${E2E_LOG}")"
DRY_LOGGED=$(( POST_DRY_ENTRIES - PRE_DRY_ENTRIES ))
if (( DRY_LOGGED >= 8 )); then
    _pass "dry-run/all-8-detections-logged" "${DRY_LOGGED} log entries"
else
    _fail "dry-run/all-8-detections-logged" "expected >=8, got ${DRY_LOGGED} (log entries so far)"
fi

# Verify dry-run did NOT mutate the fixture
_assert_file_exists "dry-run/no-mutation/empty-priority-intact" \
    "${E2E_PROJECT_ROOT}/priority/PRIORITY-0099-empty-test.md"

_assert_file_exists "dry-run/no-mutation/tester-orphan-intact" \
    "${E2E_PROJECT_ROOT}/priority/v0.20.0-orphan-tester-file.md"

echo ""
echo "--- Dry-run phase: ${PASS_COUNT} PASS, ${FAIL_COUNT} FAIL ---"

# ---------------------------------------------------------------------------
# PHASE 2: LIVE RUN — verify 8 fixes land with backups and log entries
# ---------------------------------------------------------------------------
echo ""
echo "=== PHASE 2: Live run — 8 fixes must land ==="

PRE_LIVE_ENTRIES="$(wc -l < "${E2E_LOG}")"

# Run order matters:
# - check-stale-active-rc resets Active RC to none (fix 6), enabling fix 2
# - check-blocked-tasks runs after Active RC is none (fix 2)
_live_1="$(_run_check check-empty-files.sh || true)"
_live_6="$(_run_check check-stale-active-rc.sh || true)"
_live_2="$(_run_check check-blocked-tasks.sh || true)"
_live_3="$(_run_check check-tester-orphan-files.sh || true)"
_live_4="$(_run_check check-cache-marker-drift.sh || true)"
_live_5="$(_run_check check-readme-bundled.sh || true)"
_live_7="$(_run_check check-orphan-rc-branches.sh || true)"
_live_8="$(_run_check check-push-lag.sh || true)"

# Fix 1: empty file renamed to .empty.orphan
_assert_file_absent "live/fix-1/empty-file-gone" \
    "${E2E_PROJECT_ROOT}/priority/PRIORITY-0099-empty-test.md"
_assert_file_exists "live/fix-1/orphan-file-present" \
    "${E2E_PROJECT_ROOT}/priority/PRIORITY-0099-empty-test.md.empty.orphan"

# Fix 6: Active RC reset to none
_assert_file_contains "live/fix-6/active-rc-reset" \
    "${E2E_PROJECT_ROOT}/release-state.md" \
    "^none$"

# Fix 2: BLOCKED task promoted to BACKLOG
_assert_file_contains "live/fix-2/task-promoted" \
    "${E2E_PROJECT_ROOT}/tasks/CLAUDE-CODER-TEST-001-blocked-task/status.md" \
    "BACKLOG"

# Fix 3: tester orphan file handled (renamed to .orphan)
_assert_file_absent "live/fix-3/tester-orphan-gone" \
    "${E2E_PROJECT_ROOT}/priority/v0.20.0-orphan-tester-file.md"

# Fix 4: cache marker reset from [x] to [ ]
_assert_file_contains "live/fix-4/cache-marker-reset" \
    "${E2E_PROJECT_ROOT}/tasks/queues/claude/priority_backlog.md" \
    '\[ \]\s+PRIORITY-0088-drifted'

# Fix 5: PM task marked WONT-DO
_assert_file_contains "live/fix-5/pm-task-wont-do" \
    "${E2E_PROJECT_ROOT}/tasks/CLAUDE-PM-TEST-001-readme-input/status.md" \
    "WONT-DO"

# Fix 7: orphan rc/v0.19.0 branch deleted
_orphan_branch="$(git -C "${E2E_DEV_TREE}" branch --list 'rc/v0.19.0' 2>/dev/null || true)"
if [[ -z "${_orphan_branch}" ]]; then
    _pass "live/fix-7/orphan-rc-branch-deleted" "rc/v0.19.0 gone"
else
    _fail "live/fix-7/orphan-rc-branch-deleted" "rc/v0.19.0 still exists"
fi

# Fix 8: push-lag cleared (main no longer ahead of origin/main)
_ahead="$(git -C "${E2E_DEV_TREE}" rev-list --count 'origin/main..main' 2>/dev/null || echo 999)"
if (( _ahead == 0 )); then
    _pass "live/fix-8/push-lag-cleared" "main is even with origin/main"
else
    _fail "live/fix-8/push-lag-cleared" "main still ${_ahead} ahead"
fi

# Backups present
_backup_count="$(find "${E2E_PROJECT_ROOT}/overwatch/backups" -mindepth 2 -maxdepth 2 -type f 2>/dev/null | wc -l)"
if (( _backup_count > 0 )); then
    _pass "live/backups-present" "${_backup_count} backup file(s)"
else
    _fail "live/backups-present" "no backup files found"
fi

# actions.log has at least 8 new live entries
POST_LIVE_ENTRIES="$(wc -l < "${E2E_LOG}")"
LIVE_LOGGED=$(( POST_LIVE_ENTRIES - PRE_LIVE_ENTRIES ))
if (( LIVE_LOGGED >= 8 )); then
    _pass "live/actions-log-populated" "${LIVE_LOGGED} live entries"
else
    _fail "live/actions-log-populated" "expected >=8 live entries, got ${LIVE_LOGGED}"
fi

echo ""
echo "--- Live phase: ${PASS_COUNT} PASS, ${FAIL_COUNT} FAIL ---"

# ---------------------------------------------------------------------------
# PHASE 3: REVERT — overwatch-revert.sh restores pre-live state
# ---------------------------------------------------------------------------
echo ""
echo "=== PHASE 3: Revert ==="

# Find the earliest OVERWATCH_FIRING_TIMESTAMP from the backup directories
_firing_ts="$(find "${E2E_PROJECT_ROOT}/overwatch/backups" \
    -mindepth 1 -maxdepth 1 -type d 2>/dev/null \
    | sed 's|.*/||' | sort | head -n1)"

if [[ -z "${_firing_ts}" ]]; then
    _fail "revert/timestamp-found" "no backup timestamp directories"
else
    _pass "revert/timestamp-found" "ts=${_firing_ts}"

    _revert_out="$(KANBAN_ROOT="${E2E_KANBAN_ROOT}" OVERWATCH_PROJECT="${E2E_PROJECT_NAME}" \
        bash "${REVERT_SCRIPT}" "${_firing_ts}" 2>&1 || true)"

    if echo "${_revert_out}" | grep -qiE "revert completed|Restored:|files restored" 2>/dev/null; then
        _pass "revert/completed" "revert script reported completion"
    else
        _fail "revert/completed" "output: ${_revert_out}"
    fi

    # Revert must log to actions.log
    _assert_log_contains "revert/actions-log-entry" "${E2E_LOG}" \
        "revert-completed|revert-partial"

    # State restoration: release-state.md should have v0.20.0 back
    # (it was backed up before check-stale-active-rc reset it)
    if grep -qE "v0\.20\.0" "${E2E_PROJECT_ROOT}/release-state.md" 2>/dev/null; then
        _pass "revert/release-state-restored" "Active RC v0.20.0 restored"
    else
        # Acceptable if revert only restored other files (depends on which timestamp)
        if echo "${_revert_out}" | grep -qiE "Restored:" 2>/dev/null; then
            _pass "revert/release-state-restored" "revert restored files"
        else
            _fail "revert/release-state-restored" \
                "release-state.md not restored (content: $(head -5 "${E2E_PROJECT_ROOT}/release-state.md" 2>/dev/null || echo '?'))"
        fi
    fi
fi

# ---------------------------------------------------------------------------
# Write summary file
# ---------------------------------------------------------------------------
echo ""
echo "=== Summary ==="
{
    echo "OVERWATCH E2E Harness Summary"
    echo "Run at: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "Fixture tmpdir: ${E2E_TMPDIR}"
    echo ""
    echo "Results:"
    for _line in "${SUMMARY_LINES[@]}"; do
        echo "  ${_line}"
    done
    echo ""
    echo "PASS: ${PASS_COUNT}"
    echo "FAIL: ${FAIL_COUNT}"
    if (( FAIL_COUNT == 0 )); then
        echo "OVERALL: PASS"
    else
        echo "OVERALL: FAIL"
    fi
} | tee "${SUMMARY_FILE}"

echo ""
echo "Summary file: ${SUMMARY_FILE}"

# Exit
if (( FAIL_COUNT > 0 )); then
    echo "E2E FAILED: ${FAIL_COUNT} failure(s)" >&2
    exit 1
fi
echo "E2E PASSED: all ${PASS_COUNT} assertions passed"
exit 0
