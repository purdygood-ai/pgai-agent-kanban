#!/usr/bin/env bash
# team/scripts/lib/overwatch-checks/test-sweep-runner.sh
#
# Two-project fixture test for overwatch-sweep.sh.
#
# Verifies:
#   1. The sweep iterates both projects and writes per-project sweep logs
#      under projects/<name>/logs/overwatch/sweep.log.
#   2. HALT alone suppresses the run (exit 0, no logs written).
#   3. HALT_OVERWATCH alone suppresses the run (exit 0, no logs written).
#   4. Both together suppress (exit 0).
#   5. The sweep exits 0 on a two-project fixture with no checks failing.
#
# All work is done inside a self-contained tmpdir; the live kanban
# installation is never touched.
#
# Usage:
#   bash test-sweep-runner.sh [--keep-tmpdir]
#
# Exit codes:
#   0  — all assertions passed
#   1  — one or more assertions failed

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEAM_SCRIPTS_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SWEEP_SCRIPT="${TEAM_SCRIPTS_DIR}/overwatch-sweep.sh"

# Source temp.sh for pgai_mktemp_d
_TEMP_SH="${SCRIPT_DIR}/../temp.sh"
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
# Test state
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

# ---------------------------------------------------------------------------
# Tmpdir setup
# ---------------------------------------------------------------------------
FIXTURE_DIR="$(pgai_mktemp_d sweep_runner_test)"

_cleanup() {
    if (( KEEP_TMPDIR == 0 )); then
        rm -rf "${FIXTURE_DIR}" 2>/dev/null || true
    else
        echo "Kept tmpdir: ${FIXTURE_DIR}"
    fi
}
trap _cleanup EXIT

# ---------------------------------------------------------------------------
# Build the two-project fixture
# ---------------------------------------------------------------------------
_build_fixture() {
    local kanban_root="$1"
    local proj_a="alpha-project"
    local proj_b="beta-project"

    # --- projects.cfg (INI format) ---
    mkdir -p "${kanban_root}"
    cat > "${kanban_root}/projects.cfg" <<INI
[project:${proj_a}]
priority=1

[project:${proj_b}]
priority=2
INI

    # --- Bootstrap each project directory ---
    local proj
    for proj in "${proj_a}" "${proj_b}"; do
        mkdir -p \
            "${kanban_root}/projects/${proj}/overwatch/backups" \
            "${kanban_root}/projects/${proj}/logs/overwatch" \
            "${kanban_root}/projects/${proj}/tasks/queues" \
            "${kanban_root}/locks"
        : > "${kanban_root}/projects/${proj}/overwatch/actions.log"
    done

    # --- Minimal project.cfg for each project (no dev tree needed for sweep test) ---
    for proj in "${proj_a}" "${proj_b}"; do
        cat > "${kanban_root}/projects/${proj}/project.cfg" <<CFG
[project]
project_name = ${proj}
workflow_type = release
CFG
    done

    echo "${proj_a} ${proj_b}"
}

# ===========================================================================
# TEST 1: Two-project sweep — both projects get per-project log entries
# ===========================================================================
echo ""
echo "=== TEST 1: Two-project sweep iterates both projects ==="

T1_ROOT="${FIXTURE_DIR}/t1"
_build_fixture "${T1_ROOT}" >/dev/null

# Run the sweep with no checks (checks dir has check-*.sh; the fixture has none
# scoped to it; the sweep will pick up the real checks dir but OVERWATCH_PROJECT
# is set and those checks need a real project, so they may fail gracefully).
# We override the checks dir by setting KANBAN_ROOT to our fixture — the sweep
# resolves checks relative to its own BASH_SOURCE[0] location, not KANBAN_ROOT.
# The sweep will find the real check scripts. To avoid side effects we run with
# the fixture kanban root and let checks gracefully skip if their state dirs
# don't match.
T1_EXIT=0
T1_OUT="$(KANBAN_ROOT="${T1_ROOT}" bash "${SWEEP_SCRIPT}" 2>&1)" || T1_EXIT=$?

if (( T1_EXIT == 0 )); then
    _pass "two-project-sweep/exit-zero"
else
    _fail "two-project-sweep/exit-zero" "sweep exited ${T1_EXIT}"
fi

# Both projects must have sweep log files written
if [[ -f "${T1_ROOT}/projects/alpha-project/logs/overwatch/sweep.log" ]]; then
    _pass "two-project-sweep/alpha-project-log-exists"
else
    _fail "two-project-sweep/alpha-project-log-exists" "missing: ${T1_ROOT}/projects/alpha-project/logs/overwatch/sweep.log"
fi

if [[ -f "${T1_ROOT}/projects/beta-project/logs/overwatch/sweep.log" ]]; then
    _pass "two-project-sweep/beta-project-log-exists"
else
    _fail "two-project-sweep/beta-project-log-exists" "missing: ${T1_ROOT}/projects/beta-project/logs/overwatch/sweep.log"
fi

# Sweep log must contain sweep-start and sweep-end entries for each project
if grep -q "sweep-start" "${T1_ROOT}/projects/alpha-project/logs/overwatch/sweep.log" 2>/dev/null; then
    _pass "two-project-sweep/alpha-project-sweep-start"
else
    _fail "two-project-sweep/alpha-project-sweep-start"
fi

if grep -q "sweep-end" "${T1_ROOT}/projects/alpha-project/logs/overwatch/sweep.log" 2>/dev/null; then
    _pass "two-project-sweep/alpha-project-sweep-end"
else
    _fail "two-project-sweep/alpha-project-sweep-end"
fi

if grep -q "sweep-start" "${T1_ROOT}/projects/beta-project/logs/overwatch/sweep.log" 2>/dev/null; then
    _pass "two-project-sweep/beta-project-sweep-start"
else
    _fail "two-project-sweep/beta-project-sweep-start"
fi

if grep -q "sweep-end" "${T1_ROOT}/projects/beta-project/logs/overwatch/sweep.log" 2>/dev/null; then
    _pass "two-project-sweep/beta-project-sweep-end"
else
    _fail "two-project-sweep/beta-project-sweep-end"
fi

# Action log must have entries
if [[ -s "${T1_ROOT}/projects/alpha-project/overwatch/actions.log" ]]; then
    _pass "two-project-sweep/alpha-project-action-log-populated"
else
    _fail "two-project-sweep/alpha-project-action-log-populated"
fi

if [[ -s "${T1_ROOT}/projects/beta-project/overwatch/actions.log" ]]; then
    _pass "two-project-sweep/beta-project-action-log-populated"
else
    _fail "two-project-sweep/beta-project-action-log-populated"
fi

# ===========================================================================
# TEST 2: HALT alone suppresses the run
# ===========================================================================
echo ""
echo "=== TEST 2: HALT flag suppresses sweep ==="

T2_ROOT="${FIXTURE_DIR}/t2"
_build_fixture "${T2_ROOT}" >/dev/null
touch "${T2_ROOT}/HALT"

T2_EXIT=0
T2_OUT="$(KANBAN_ROOT="${T2_ROOT}" bash "${SWEEP_SCRIPT}" 2>&1)" || T2_EXIT=$?

if (( T2_EXIT == 0 )); then
    _pass "halt-suppresses/exit-zero"
else
    _fail "halt-suppresses/exit-zero" "expected 0 got ${T2_EXIT}"
fi

# No sweep logs should have been written
if [[ ! -s "${T2_ROOT}/projects/alpha-project/logs/overwatch/sweep.log" ]]; then
    _pass "halt-suppresses/no-alpha-sweep-log"
else
    _fail "halt-suppresses/no-alpha-sweep-log" "sweep log was written despite HALT"
fi

if echo "${T2_OUT}" | grep -q "HALT flag present"; then
    _pass "halt-suppresses/message"
else
    _fail "halt-suppresses/message" "expected HALT message in output"
fi

# ===========================================================================
# TEST 3: HALT_OVERWATCH alone suppresses the run
# ===========================================================================
echo ""
echo "=== TEST 3: HALT_OVERWATCH flag suppresses sweep ==="

T3_ROOT="${FIXTURE_DIR}/t3"
_build_fixture "${T3_ROOT}" >/dev/null
touch "${T3_ROOT}/HALT_OVERWATCH"

T3_EXIT=0
T3_OUT="$(KANBAN_ROOT="${T3_ROOT}" bash "${SWEEP_SCRIPT}" 2>&1)" || T3_EXIT=$?

if (( T3_EXIT == 0 )); then
    _pass "halt-overwatch-suppresses/exit-zero"
else
    _fail "halt-overwatch-suppresses/exit-zero" "expected 0 got ${T3_EXIT}"
fi

if [[ ! -s "${T3_ROOT}/projects/alpha-project/logs/overwatch/sweep.log" ]]; then
    _pass "halt-overwatch-suppresses/no-alpha-sweep-log"
else
    _fail "halt-overwatch-suppresses/no-alpha-sweep-log" "sweep log written despite HALT_OVERWATCH"
fi

if echo "${T3_OUT}" | grep -q "HALT_OVERWATCH flag present"; then
    _pass "halt-overwatch-suppresses/message"
else
    _fail "halt-overwatch-suppresses/message" "expected HALT_OVERWATCH message"
fi

# ===========================================================================
# TEST 4: Both HALT and HALT_OVERWATCH together suppress the run
# ===========================================================================
echo ""
echo "=== TEST 4: Both HALT and HALT_OVERWATCH suppress sweep ==="

T4_ROOT="${FIXTURE_DIR}/t4"
_build_fixture "${T4_ROOT}" >/dev/null
touch "${T4_ROOT}/HALT"
touch "${T4_ROOT}/HALT_OVERWATCH"

T4_EXIT=0
T4_OUT="$(KANBAN_ROOT="${T4_ROOT}" bash "${SWEEP_SCRIPT}" 2>&1)" || T4_EXIT=$?

if (( T4_EXIT == 0 )); then
    _pass "both-halts-suppress/exit-zero"
else
    _fail "both-halts-suppress/exit-zero" "expected 0 got ${T4_EXIT}"
fi

if [[ ! -s "${T4_ROOT}/projects/alpha-project/logs/overwatch/sweep.log" ]]; then
    _pass "both-halts-suppress/no-sweep-log"
else
    _fail "both-halts-suppress/no-sweep-log" "sweep log written despite both halts"
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
