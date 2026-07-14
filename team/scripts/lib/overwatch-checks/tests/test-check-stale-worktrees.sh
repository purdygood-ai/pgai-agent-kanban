#!/usr/bin/env bash
# team/scripts/lib/overwatch-checks/tests/test-check-stale-worktrees.sh
#
# Fixture test for check-stale-worktrees.sh.
#
# Verifies the acceptance criteria:
#   1. A terminal task with a stale worktree (no commits) → prune-candidate
#      produced and prune executed.
#   2. A terminal task with a stale worktree that carries commits → bug-filed.
#   3. A non-terminal task's worktree is not touched.
#   4. A worktree below the age threshold is not touched.
#   5. Source is side-effect-free.
#   6. bash -n passes.
#
# All work is done in a self-contained tmpdir using a real git repo.
# The live kanban installation is never touched.
#
# Usage:
#   bash test-check-stale-worktrees.sh [--keep-tmpdir]
#
# Exit codes:
#   0  — all assertions passed
#   1  — one or more assertions failed

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHECKS_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
CHECK_SCRIPT="${CHECKS_DIR}/check-stale-worktrees.sh"
LIB_DIR="$(cd "${CHECKS_DIR}/.." && pwd)"
PROTOCOL_SH="${LIB_DIR}/overwatch_protocol.sh"

_TEMP_SH="${LIB_DIR}/temp.sh"
if [[ ! -f "$_TEMP_SH" ]]; then
    echo "ERROR: temp.sh not found: $_TEMP_SH" >&2
    exit 1
fi
# shellcheck source=../temp.sh
source "$_TEMP_SH"

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
# Test infrastructure
# ---------------------------------------------------------------------------
PASS_COUNT=0
FAIL_COUNT=0

_pass() {
    local name="$1" msg="${2:-}"
    PASS_COUNT=$(( PASS_COUNT + 1 ))
    echo "[PASS] ${name}${msg:+: ${msg}}"
}

_fail() {
    local name="$1" msg="${2:-}"
    FAIL_COUNT=$(( FAIL_COUNT + 1 ))
    echo "[FAIL] ${name}${msg:+: ${msg}}" >&2
}

FIXTURE_DIR="$(pgai_mktemp_d check_stale_worktrees_test)"

_cleanup() {
    if (( KEEP_TMPDIR == 0 )); then
        rm -rf "${FIXTURE_DIR}" 2>/dev/null || true
    else
        echo "Kept tmpdir: ${FIXTURE_DIR}"
    fi
}
trap _cleanup EXIT

# ---------------------------------------------------------------------------
# Helper: build a minimal git dev tree with a branch and worktree.
# Args:
#   $1  — dev_tree base path
#   $2  — task_id to use as the worktree branch/dir name
#   $3  — worktrees_dir: where to place the worktree
# Returns the worktree path via the global _WT_PATH variable.
# ---------------------------------------------------------------------------
_build_dev_tree_with_worktree() {
    local dev_tree="$1"
    local task_id="$2"
    local worktrees_dir="$3"

    # Initialize dev tree if not already done.
    if [[ ! -d "${dev_tree}/.git" ]]; then
        mkdir -p "${dev_tree}"
        git -C "${dev_tree}" init -b main 2>/dev/null || git -C "${dev_tree}" init 2>/dev/null
        git -C "${dev_tree}" config user.email "test@fixture.local"
        git -C "${dev_tree}" config user.name "Test Fixture"
        echo "main" > "${dev_tree}/README.md"
        git -C "${dev_tree}" add README.md
        git -C "${dev_tree}" commit -m "initial" 2>/dev/null
    fi

    # Create a feature branch for this task WITHOUT checking it out in the dev tree.
    # (checkout -b would put the dev tree on the branch, causing worktree add to fail
    # because the branch is already checked out.)
    local branch_name="ai_feature/${task_id}"
    git -C "${dev_tree}" branch "${branch_name}" 2>/dev/null

    # Add a linked worktree at worktrees_dir/task_id.
    local wt_path="${worktrees_dir}/${task_id}"
    mkdir -p "${worktrees_dir}"
    git -C "${dev_tree}" worktree add "${wt_path}" "${branch_name}" 2>/dev/null

    _WT_PATH="${wt_path}"
}

# ---------------------------------------------------------------------------
# Helper: build a minimal fixture kanban root with overwatch state dirs.
# ---------------------------------------------------------------------------
_build_fixture_kanban() {
    local kanban_root="$1"
    local project_name="${2:-test-project}"
    local dev_tree="${3:-}"

    mkdir -p \
        "${kanban_root}/projects/${project_name}/overwatch/backups" \
        "${kanban_root}/projects/${project_name}/logs/overwatch" \
        "${kanban_root}/projects/${project_name}/tasks/queues" \
        "${kanban_root}/projects/${project_name}/bugs" \
        "${kanban_root}/locks"
    : > "${kanban_root}/projects/${project_name}/overwatch/actions.log"

    cat > "${kanban_root}/projects.cfg" <<CFG
[project:${project_name}]
priority=1
CFG

    if [[ -n "${dev_tree}" ]]; then
        cat > "${kanban_root}/projects/${project_name}/project.cfg" <<PCFG
[project]
project_name = ${project_name}
workflow_type = release
dev_tree_path = ${dev_tree}
PCFG
    else
        cat > "${kanban_root}/projects/${project_name}/project.cfg" <<PCFG
[project]
project_name = ${project_name}
workflow_type = release
PCFG
    fi
}

# ---------------------------------------------------------------------------
# Helper: write a task status.md with a given state.
# ---------------------------------------------------------------------------
_write_task_status() {
    local tasks_root="$1"
    local task_id="$2"
    local state="$3"

    mkdir -p "${tasks_root}/${task_id}"
    cat > "${tasks_root}/${task_id}/status.md" <<EOF
# Status

## Task
${task_id}

## State
${state}

## Summary
Fixture task.

## Blockers
none

## Needs Human
no
EOF
}

# ===========================================================================
# TEST 1: bash -n clean
# ===========================================================================
echo ""
echo "=== TEST 1: bash -n clean ==="

if bash -n "${CHECK_SCRIPT}" 2>/dev/null; then
    _pass "bash-n-clean"
else
    _fail "bash-n-clean" "bash -n failed"
fi

# ===========================================================================
# TEST 2: Source is side-effect-free
# ===========================================================================
echo ""
echo "=== TEST 2: Source is side-effect-free ==="

SOURCE_OUTPUT="$(bash -c "source '${CHECK_SCRIPT}'; echo 'sourced'" 2>&1)"
if [[ "${SOURCE_OUTPUT}" == "sourced" ]]; then
    _pass "source-side-effect-free"
else
    _fail "source-side-effect-free" "Unexpected output: ${SOURCE_OUTPUT}"
fi

# ===========================================================================
# TEST 3: Function defined after source
# ===========================================================================
echo ""
echo "=== TEST 3: Function overwatch_check_stale_worktrees is defined ==="

if bash -c "source '${CHECK_SCRIPT}'; declare -f overwatch_check_stale_worktrees >/dev/null 2>&1 && echo 'defined'" | grep -q 'defined'; then
    _pass "function-defined"
else
    _fail "function-defined"
fi

# ===========================================================================
# TEST 4: No dev tree configured — skips silently
# ===========================================================================
echo ""
echo "=== TEST 4: No dev tree configured — skips ==="

T4_ROOT="${FIXTURE_DIR}/t4"
_build_fixture_kanban "${T4_ROOT}" "test-project"

T4_EXIT=0
T4_OUT="$(KANBAN_ROOT="${T4_ROOT}" \
    OVERWATCH_PROJECT="test-project" \
    bash -c "
        source '${PROTOCOL_SH}'
        source '${CHECK_SCRIPT}'
        overwatch_check_stale_worktrees
    " 2>&1)" || T4_EXIT=$?

if (( T4_EXIT == 0 )); then
    _pass "no-dev-tree/exit-zero"
else
    _fail "no-dev-tree/exit-zero" "exit ${T4_EXIT}"
fi

if echo "${T4_OUT}" | grep -qi "skip\|no dev"; then
    _pass "no-dev-tree/skip-message"
else
    _fail "no-dev-tree/skip-message" "no skip message in: ${T4_OUT}"
fi

# ===========================================================================
# TEST 5: Terminal task with stale worktree (no extra commits) — pruned
# ===========================================================================
echo ""
echo "=== TEST 5: Terminal task with stale worktree (no commits) — pruned ==="

T5_ROOT="${FIXTURE_DIR}/t5"
T5_DEV="${FIXTURE_DIR}/t5-dev"
T5_WTS="${FIXTURE_DIR}/t5-worktrees"
T5_TASK_ID="CODER-20260101-001-fixture-stale"
T5_TASKS_ROOT="${T5_ROOT}/projects/test-project/tasks"

_build_dev_tree_with_worktree "${T5_DEV}" "${T5_TASK_ID}" "${T5_WTS}"
T5_WT_PATH="${_WT_PATH}"

_build_fixture_kanban "${T5_ROOT}" "test-project" "${T5_DEV}"
_write_task_status "${T5_TASKS_ROOT}" "${T5_TASK_ID}" "DONE"

# Age the worktree directory to be beyond threshold (backdate mtime by 10 days).
touch -t "$(date -d '10 days ago' +%Y%m%d%H%M.%S 2>/dev/null || date -v-10d +%Y%m%d%H%M.%S 2>/dev/null || echo '202601010000.00')" \
    "${T5_WT_PATH}" 2>/dev/null || true

T5_EXIT=0
T5_OUT="$(KANBAN_ROOT="${T5_ROOT}" \
    OVERWATCH_PROJECT="test-project" \
    OVERWATCH_STALE_WORKTREE_THRESHOLD_DAYS="1" \
    bash -c "
        source '${PROTOCOL_SH}'
        source '${CHECK_SCRIPT}'
        overwatch_check_stale_worktrees
    " 2>&1)" || T5_EXIT=$?

if (( T5_EXIT == 0 )); then
    _pass "stale-clean-worktree/exit-zero"
else
    _fail "stale-clean-worktree/exit-zero" "exit ${T5_EXIT}; output: ${T5_OUT}"
fi

# The worktree should no longer exist.
if [[ ! -d "${T5_WT_PATH}" ]]; then
    _pass "stale-clean-worktree/worktree-pruned"
else
    _fail "stale-clean-worktree/worktree-pruned" "worktree still exists: ${T5_WT_PATH}"
fi

# Output should mention pruned.
if echo "${T5_OUT}" | grep -qi "prun"; then
    _pass "stale-clean-worktree/prune-message"
else
    _fail "stale-clean-worktree/prune-message" "no prune message in: ${T5_OUT}"
fi

# Action log should have a pruned entry.
if grep -q "worktree-pruned" "${T5_ROOT}/projects/test-project/overwatch/actions.log" 2>/dev/null; then
    _pass "stale-clean-worktree/action-log-pruned"
else
    _fail "stale-clean-worktree/action-log-pruned"
fi

# ===========================================================================
# TEST 6: Terminal task with stale worktree that carries commits — bug-filed
# ===========================================================================
echo ""
echo "=== TEST 6: Terminal task with branch-carrying worktree — bug-filed ==="

T6_ROOT="${FIXTURE_DIR}/t6"
T6_DEV="${FIXTURE_DIR}/t6-dev"
T6_WTS="${FIXTURE_DIR}/t6-worktrees"
T6_TASK_ID="CODER-20260101-002-fixture-carrying"
T6_TASKS_ROOT="${T6_ROOT}/projects/test-project/tasks"

_build_dev_tree_with_worktree "${T6_DEV}" "${T6_TASK_ID}" "${T6_WTS}"
T6_WT_PATH="${_WT_PATH}"

# Make a commit in the worktree (not yet merged to main).
git -C "${T6_WT_PATH}" config user.email "test@fixture.local"
git -C "${T6_WT_PATH}" config user.name "Test Fixture"
echo "carrying commit" > "${T6_WT_PATH}/carrying.txt"
git -C "${T6_WT_PATH}" add carrying.txt
git -C "${T6_WT_PATH}" commit -m "carrying commit" 2>/dev/null

_build_fixture_kanban "${T6_ROOT}" "test-project" "${T6_DEV}"
_write_task_status "${T6_TASKS_ROOT}" "${T6_TASK_ID}" "DONE"

# Age the worktree directory.
touch -t "$(date -d '10 days ago' +%Y%m%d%H%M.%S 2>/dev/null || date -v-10d +%Y%m%d%H%M.%S 2>/dev/null || echo '202601010000.00')" \
    "${T6_WT_PATH}" 2>/dev/null || true

T6_EXIT=0
T6_OUT="$(KANBAN_ROOT="${T6_ROOT}" \
    OVERWATCH_PROJECT="test-project" \
    OVERWATCH_STALE_WORKTREE_THRESHOLD_DAYS="1" \
    bash -c "
        source '${PROTOCOL_SH}'
        source '${CHECK_SCRIPT}'
        overwatch_check_stale_worktrees
    " 2>&1)" || T6_EXIT=$?

if (( T6_EXIT == 0 )); then
    _pass "carrying-worktree/exit-zero"
else
    _fail "carrying-worktree/exit-zero" "exit ${T6_EXIT}"
fi

# Worktree should NOT have been pruned.
if [[ -d "${T6_WT_PATH}" ]]; then
    _pass "carrying-worktree/not-pruned"
else
    _fail "carrying-worktree/not-pruned" "worktree was removed: ${T6_WT_PATH}"
fi

# A bug file should exist.
T6_BUG_COUNT="$(find "${T6_ROOT}/projects/test-project/bugs" -name "BUG-overwatch-stale-worktree-*" -type f 2>/dev/null | wc -l | tr -d '[:space:]')"
if [[ "${T6_BUG_COUNT}" -gt 0 ]]; then
    _pass "carrying-worktree/bug-filed"
else
    _fail "carrying-worktree/bug-filed" "no bug file in ${T6_ROOT}/projects/test-project/bugs/"
fi

# Action log should have a bug-filed entry.
if grep -q "bug-filed" "${T6_ROOT}/projects/test-project/overwatch/actions.log" 2>/dev/null; then
    _pass "carrying-worktree/action-log-bug-filed"
else
    _fail "carrying-worktree/action-log-bug-filed"
fi

# ===========================================================================
# TEST 7: Non-terminal task — worktree not touched
# ===========================================================================
echo ""
echo "=== TEST 7: Non-terminal task — worktree not touched ==="

T7_ROOT="${FIXTURE_DIR}/t7"
T7_DEV="${FIXTURE_DIR}/t7-dev"
T7_WTS="${FIXTURE_DIR}/t7-worktrees"
T7_TASK_ID="CODER-20260101-003-fixture-working"
T7_TASKS_ROOT="${T7_ROOT}/projects/test-project/tasks"

_build_dev_tree_with_worktree "${T7_DEV}" "${T7_TASK_ID}" "${T7_WTS}"
T7_WT_PATH="${_WT_PATH}"

_build_fixture_kanban "${T7_ROOT}" "test-project" "${T7_DEV}"
_write_task_status "${T7_TASKS_ROOT}" "${T7_TASK_ID}" "WORKING"

# Age the worktree.
touch -t "$(date -d '10 days ago' +%Y%m%d%H%M.%S 2>/dev/null || date -v-10d +%Y%m%d%H%M.%S 2>/dev/null || echo '202601010000.00')" \
    "${T7_WT_PATH}" 2>/dev/null || true

T7_EXIT=0
T7_OUT="$(KANBAN_ROOT="${T7_ROOT}" \
    OVERWATCH_PROJECT="test-project" \
    OVERWATCH_STALE_WORKTREE_THRESHOLD_DAYS="1" \
    bash -c "
        source '${PROTOCOL_SH}'
        source '${CHECK_SCRIPT}'
        overwatch_check_stale_worktrees
    " 2>&1)" || T7_EXIT=$?

if (( T7_EXIT == 0 )); then
    _pass "working-task/exit-zero"
else
    _fail "working-task/exit-zero" "exit ${T7_EXIT}"
fi

# Worktree should NOT have been pruned.
if [[ -d "${T7_WT_PATH}" ]]; then
    _pass "working-task/not-pruned"
else
    _fail "working-task/not-pruned" "active task worktree was removed"
fi

# ===========================================================================
# TEST 8: Worktree below age threshold — not touched
# ===========================================================================
echo ""
echo "=== TEST 8: Worktree below age threshold — not touched ==="

T8_ROOT="${FIXTURE_DIR}/t8"
T8_DEV="${FIXTURE_DIR}/t8-dev"
T8_WTS="${FIXTURE_DIR}/t8-worktrees"
T8_TASK_ID="CODER-20260101-004-fixture-fresh"
T8_TASKS_ROOT="${T8_ROOT}/projects/test-project/tasks"

_build_dev_tree_with_worktree "${T8_DEV}" "${T8_TASK_ID}" "${T8_WTS}"
T8_WT_PATH="${_WT_PATH}"

_build_fixture_kanban "${T8_ROOT}" "test-project" "${T8_DEV}"
_write_task_status "${T8_TASKS_ROOT}" "${T8_TASK_ID}" "DONE"
# Do NOT age the worktree — it should be "fresh" relative to a 30-day threshold.

T8_EXIT=0
T8_OUT="$(KANBAN_ROOT="${T8_ROOT}" \
    OVERWATCH_PROJECT="test-project" \
    OVERWATCH_STALE_WORKTREE_THRESHOLD_DAYS="30" \
    bash -c "
        source '${PROTOCOL_SH}'
        source '${CHECK_SCRIPT}'
        overwatch_check_stale_worktrees
    " 2>&1)" || T8_EXIT=$?

if (( T8_EXIT == 0 )); then
    _pass "below-threshold/exit-zero"
else
    _fail "below-threshold/exit-zero" "exit ${T8_EXIT}"
fi

# Worktree should not have been pruned (it's below the threshold).
if [[ -d "${T8_WT_PATH}" ]]; then
    _pass "below-threshold/not-pruned"
else
    _fail "below-threshold/not-pruned" "fresh worktree was unexpectedly removed"
fi

# ===========================================================================
# Summary
# ===========================================================================
echo ""
echo "=== Summary ==="
echo "PASS: ${PASS_COUNT}"
echo "FAIL: ${FAIL_COUNT}"

if (( FAIL_COUNT > 0 )); then
    echo "OVERALL: FAIL"
    exit 1
fi
echo "OVERALL: PASS"
exit 0
