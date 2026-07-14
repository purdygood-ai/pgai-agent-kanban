#!/usr/bin/env bash
# team/scripts/lib/overwatch-checks/tests/test-check-version-divergence.sh
#
# Fixture test for check-version-divergence.sh.
#
# Verifies the acceptance criteria:
#   1. When installed VERSION differs from `git describe`, a report entry is
#      produced in the action log (no auto-fix).
#   2. When versions match, no divergence entry is logged.
#   3. When no dev tree is configured, the check skips silently.
#   4. Source is side-effect-free.
#   5. bash -n passes.
#
# All work is done in a self-contained tmpdir using a real git repo as the
# dev tree. The live kanban installation is never touched.
#
# Usage:
#   bash test-check-version-divergence.sh [--keep-tmpdir]
#
# Exit codes:
#   0  — all assertions passed
#   1  — one or more assertions failed

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHECKS_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
CHECK_SCRIPT="${CHECKS_DIR}/check-version-divergence.sh"
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

FIXTURE_DIR="$(pgai_mktemp_d check_version_divergence_test)"

_cleanup() {
    if (( KEEP_TMPDIR == 0 )); then
        rm -rf "${FIXTURE_DIR}" 2>/dev/null || true
    else
        echo "Kept tmpdir: ${FIXTURE_DIR}"
    fi
}
trap _cleanup EXIT

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
# Helper: build a minimal git dev tree with a tag.
# Returns the dev tree path via stdout.
# ---------------------------------------------------------------------------
_build_fixture_dev_tree() {
    local dev_tree="$1"
    local tag_name="${2:-v0.1.0}"

    mkdir -p "${dev_tree}"
    git -C "${dev_tree}" init -b main 2>/dev/null || git -C "${dev_tree}" init 2>/dev/null
    git -C "${dev_tree}" config user.email "test@fixture.local"
    git -C "${dev_tree}" config user.name "Test Fixture"

    echo "fixture" > "${dev_tree}/README.md"
    git -C "${dev_tree}" add README.md
    git -C "${dev_tree}" commit -m "initial commit" 2>/dev/null
    git -C "${dev_tree}" tag "${tag_name}" 2>/dev/null
}

# ===========================================================================
# TEST 1: bash -n clean
# ===========================================================================
echo ""
echo "=== TEST 1: bash -n clean ==="

if bash -n "${CHECK_SCRIPT}" 2>/dev/null; then
    _pass "bash-n-clean"
else
    _fail "bash-n-clean" "bash -n failed on ${CHECK_SCRIPT}"
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
echo "=== TEST 3: Function overwatch_check_version_divergence is defined ==="

if bash -c "source '${CHECK_SCRIPT}'; declare -f overwatch_check_version_divergence >/dev/null 2>&1 && echo 'defined'" | grep -q 'defined'; then
    _pass "function-defined"
else
    _fail "function-defined"
fi

# ===========================================================================
# TEST 4: No dev tree configured — skips silently
# ===========================================================================
echo ""
echo "=== TEST 4: No dev tree configured — skips silently ==="

T4_ROOT="${FIXTURE_DIR}/t4"
_build_fixture_kanban "${T4_ROOT}" "test-project"  # no dev_tree arg
echo "v1.0.0" > "${T4_ROOT}/VERSION"

T4_EXIT=0
T4_OUT="$(KANBAN_ROOT="${T4_ROOT}" \
    OVERWATCH_PROJECT="test-project" \
    PGAI_DEV_TREE_PATH="" \
    bash -c "
        unset PGAI_DEV_TREE_PATH
        source '${PROTOCOL_SH}'
        source '${CHECK_SCRIPT}'
        overwatch_check_version_divergence
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

# Action log should have a skipped entry.
if grep -q "skipped" "${T4_ROOT}/projects/test-project/overwatch/actions.log" 2>/dev/null; then
    _pass "no-dev-tree/action-log-skipped"
else
    _fail "no-dev-tree/action-log-skipped"
fi

# ===========================================================================
# TEST 5: VERSION matches dev describe — no divergence logged
# ===========================================================================
echo ""
echo "=== TEST 5: Versions match — no divergence entry ==="

T5_ROOT="${FIXTURE_DIR}/t5"
T5_DEV="${FIXTURE_DIR}/t5-dev"
_build_fixture_dev_tree "${T5_DEV}" "v1.2.3"
_build_fixture_kanban "${T5_ROOT}" "test-project" "${T5_DEV}"
# Write VERSION to match the tag exactly.
echo "v1.2.3" > "${T5_ROOT}/VERSION"

T5_EXIT=0
T5_OUT="$(KANBAN_ROOT="${T5_ROOT}" \
    OVERWATCH_PROJECT="test-project" \
    bash -c "
        source '${PROTOCOL_SH}'
        source '${CHECK_SCRIPT}'
        overwatch_check_version_divergence
    " 2>&1)" || T5_EXIT=$?

if (( T5_EXIT == 0 )); then
    _pass "versions-match/exit-zero"
else
    _fail "versions-match/exit-zero" "exit ${T5_EXIT}"
fi

if echo "${T5_OUT}" | grep -qi "no divergence\|version-match"; then
    _pass "versions-match/no-divergence-message"
else
    _fail "versions-match/no-divergence-message" "unexpected output: ${T5_OUT}"
fi

# Action log should NOT have a report:version-divergence action entry.
# (The check name "check-version-divergence" will appear in every entry; we check
# specifically for the action field value "report:version-divergence".)
if ! grep -q "report:version-divergence" "${T5_ROOT}/projects/test-project/overwatch/actions.log" 2>/dev/null; then
    _pass "versions-match/no-divergence-in-log"
else
    _fail "versions-match/no-divergence-in-log"
fi

# ===========================================================================
# TEST 6: VERSION differs from dev describe — report entry produced, no auto-fix
# ===========================================================================
echo ""
echo "=== TEST 6: Installed VERSION differs from git describe — report entry produced ==="

T6_ROOT="${FIXTURE_DIR}/t6"
T6_DEV="${FIXTURE_DIR}/t6-dev"
_build_fixture_dev_tree "${T6_DEV}" "v1.3.0"
_build_fixture_kanban "${T6_ROOT}" "test-project" "${T6_DEV}"
# Write VERSION that doesn't match the dev describe.
echo "v1.2.0" > "${T6_ROOT}/VERSION"

T6_EXIT=0
T6_OUT="$(KANBAN_ROOT="${T6_ROOT}" \
    OVERWATCH_PROJECT="test-project" \
    bash -c "
        source '${PROTOCOL_SH}'
        source '${CHECK_SCRIPT}'
        overwatch_check_version_divergence
    " 2>&1)" || T6_EXIT=$?

# REPORT-ONLY: must always exit 0.
if (( T6_EXIT == 0 )); then
    _pass "version-divergence/exit-zero"
else
    _fail "version-divergence/exit-zero" "expected 0 got ${T6_EXIT}"
fi

# Output should mention divergence.
if echo "${T6_OUT}" | grep -qi "divergence\|diverge"; then
    _pass "version-divergence/divergence-in-output"
else
    _fail "version-divergence/divergence-in-output" "no divergence message; output: ${T6_OUT}"
fi

# Action log must contain a report entry.
if grep -q "version-divergence" "${T6_ROOT}/projects/test-project/overwatch/actions.log" 2>/dev/null; then
    _pass "version-divergence/action-log-populated"
else
    _fail "version-divergence/action-log-populated" "no version-divergence in action log"
fi

# Action log entry must say REPORT-ONLY (no auto-fix language).
if grep -q "REPORT-ONLY" "${T6_ROOT}/projects/test-project/overwatch/actions.log" 2>/dev/null; then
    _pass "version-divergence/report-only-in-log"
else
    _fail "version-divergence/report-only-in-log"
fi

# VERSION file must not have been modified.
FINAL_VERSION="$(cat "${T6_ROOT}/VERSION" 2>/dev/null)"
if [[ "${FINAL_VERSION}" == "v1.2.0" ]]; then
    _pass "version-divergence/version-file-not-modified"
else
    _fail "version-divergence/version-file-not-modified" "VERSION was modified to: ${FINAL_VERSION}"
fi

# Dev tree must not have been modified.
if git -C "${T6_DEV}" diff --quiet HEAD 2>/dev/null; then
    _pass "version-divergence/dev-tree-not-modified"
else
    _fail "version-divergence/dev-tree-not-modified"
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
