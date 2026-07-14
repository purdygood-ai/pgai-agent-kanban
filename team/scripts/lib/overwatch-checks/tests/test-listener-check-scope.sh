#!/usr/bin/env bash
# team/scripts/lib/overwatch-checks/tests/test-listener-check-scope.sh
#
# Regression test for BUG-0015: pgai_listener_cleanliness_check scope narrowing.
#
# Verifies the acceptance criteria:
#   1. A listener whose cwd is under a SIBLING project's subtree is NOT flagged
#      when the check is scoped to THIS project's subtree — exits 0 (clean).
#   2. A listener whose cwd is under THIS project's own subtree IS flagged —
#      exits 1 (fail).
#
# Both cases exercise the PGAI_PROJECT_TEMP_SUBTREE / positional-arg narrowing
# added to pgai_listener_cleanliness_check in temp.sh.
#
# All work is done in a self-contained tmpdir.  The live kanban installation
# is never touched.
#
# Usage:
#   bash test-listener-check-scope.sh [--keep-tmpdir]
#
# Exit codes:
#   0  — all assertions passed
#   1  — one or more assertions failed

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
TEMP_SH="${LIB_DIR}/temp.sh"

if [[ ! -f "${TEMP_SH}" ]]; then
    echo "ERROR: temp.sh not found: ${TEMP_SH}" >&2
    exit 1
fi
# shellcheck source=../temp.sh
source "${TEMP_SH}"

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

FIXTURE_DIR="$(pgai_mktemp_d listener_scope_test)"

_cleanup() {
    if (( KEEP_TMPDIR == 0 )); then
        rm -rf "${FIXTURE_DIR}" 2>/dev/null || true
    else
        echo "Kept tmpdir: ${FIXTURE_DIR}"
    fi
}
trap _cleanup EXIT

# ---------------------------------------------------------------------------
# Helper: spawn a background listening process with its cwd set to a given dir.
# Writes the PID to <pidfile>.  Waits up to 2 seconds for the socket to appear.
# Caller is responsible for killing the process after the test.
#
# Usage: _spawn_listener <cwd_dir> <port> <pidfile>
# ---------------------------------------------------------------------------
_spawn_listener() {
    local cwd_dir="$1" port="$2" pidfile="$3"
    mkdir -p "${cwd_dir}"
    setsid python3 -c "
import os, socket, time
os.chdir('${cwd_dir}')
with open('${pidfile}', 'w') as f:
    f.write(str(os.getpid()))
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(('127.0.0.1', ${port}))
s.listen(1)
time.sleep(60)
" &
    # Wait up to 2 seconds for the pidfile to appear.
    local _wait=0
    while [[ ! -f "${pidfile}" ]] && (( _wait < 20 )); do
        sleep 0.1
        _wait=$(( _wait + 1 ))
    done
}

# ---------------------------------------------------------------------------
# TEST 1: bash -n clean on temp.sh (checks the modified function syntax)
# ---------------------------------------------------------------------------
echo ""
echo "=== TEST 1: bash -n clean on temp.sh ==="

if bash -n "${TEMP_SH}" 2>/dev/null; then
    _pass "bash-n-temp-sh"
else
    _fail "bash-n-temp-sh" "bash -n failed on ${TEMP_SH}"
fi

# ---------------------------------------------------------------------------
# TEST 2: Foreign listener (sibling project subtree) is NOT flagged when check
#         is scoped to THIS project's subtree.
#
# Setup:
#   - Shared temp root: FIXTURE_DIR/shared-temp-root
#   - This project's subtree: shared-temp-root/projects/this-project
#   - Sibling project's subtree: shared-temp-root/projects/sibling-project
#   - Listener's cwd: shared-temp-root/projects/sibling-project/worktrees/branch
#   - Check scoped to: shared-temp-root/projects/this-project
#
# Expected: pgai_listener_cleanliness_check exits 0 (no own-project leaks).
# ---------------------------------------------------------------------------
echo ""
echo "=== TEST 2: Foreign listener (sibling project) not flagged by project-scoped check ==="

T2_TEMP_ROOT="${FIXTURE_DIR}/t2-shared-root"
T2_OWN_SUBTREE="${T2_TEMP_ROOT}/projects/this-project"
T2_SIBLING_CWD="${T2_TEMP_ROOT}/projects/sibling-project/worktrees/some-branch"
T2_PIDFILE="${T2_TEMP_ROOT}/t2-listener.pid"

_spawn_listener "${T2_SIBLING_CWD}" 49973 "${T2_PIDFILE}"
T2_PID="$(cat "${T2_PIDFILE}" 2>/dev/null || echo "")"

if [[ -z "${T2_PID}" ]]; then
    _fail "foreign-listener-not-flagged/spawn" "failed to capture fixture PID for TEST 2"
    T2_PID="0"
fi

# Run the check scoped to the OWN project subtree — should not flag the sibling.
T2_EXIT=0
T2_OUT="$(PGAI_AGENT_KANBAN_TEMP_DIR="${T2_TEMP_ROOT}" \
    PGAI_PROJECT_TEMP_SUBTREE="${T2_OWN_SUBTREE}" \
    bash -c "
        source '${TEMP_SH}'
        pgai_listener_cleanliness_check
    " 2>&1)" || T2_EXIT=$?

# Kill the fixture process.
kill "${T2_PID}" 2>/dev/null || true
wait "${T2_PID}" 2>/dev/null || true

if (( T2_EXIT == 0 )); then
    _pass "foreign-listener-not-flagged/exit-zero"
else
    _fail "foreign-listener-not-flagged/exit-zero" \
        "expected exit 0, got ${T2_EXIT}; sibling listener was falsely flagged. output: ${T2_OUT}"
fi

# The output must NOT mention the sibling PID in any FAIL line.
if echo "${T2_OUT}" | grep -q "\[pgai-listener\] FAIL"; then
    _fail "foreign-listener-not-flagged/no-fail-message" \
        "FAIL message emitted even though the listener is from a sibling project. output: ${T2_OUT}"
else
    _pass "foreign-listener-not-flagged/no-fail-message"
fi

# ---------------------------------------------------------------------------
# TEST 3: Own-project listener IS flagged when check is scoped to THIS project's
#         subtree.
#
# Setup:
#   - Shared temp root: FIXTURE_DIR/t3-shared-root
#   - This project's subtree: t3-shared-root/projects/this-project
#   - Listener's cwd: t3-shared-root/projects/this-project/worktrees/main-branch
#   - Check scoped to: t3-shared-root/projects/this-project
#
# Expected: pgai_listener_cleanliness_check exits 1 (own-project leak detected).
# ---------------------------------------------------------------------------
echo ""
echo "=== TEST 3: Own-project listener IS flagged by project-scoped check ==="

T3_TEMP_ROOT="${FIXTURE_DIR}/t3-shared-root"
T3_OWN_SUBTREE="${T3_TEMP_ROOT}/projects/this-project"
T3_OWN_CWD="${T3_OWN_SUBTREE}/worktrees/main-branch"
T3_PIDFILE="${T3_TEMP_ROOT}/t3-listener.pid"

_spawn_listener "${T3_OWN_CWD}" 49974 "${T3_PIDFILE}"
T3_PID="$(cat "${T3_PIDFILE}" 2>/dev/null || echo "")"

if [[ -z "${T3_PID}" ]]; then
    _fail "own-listener-flagged/spawn" "failed to capture fixture PID for TEST 3"
    T3_PID="0"
fi

T3_EXIT=0
T3_OUT="$(PGAI_AGENT_KANBAN_TEMP_DIR="${T3_TEMP_ROOT}" \
    PGAI_PROJECT_TEMP_SUBTREE="${T3_OWN_SUBTREE}" \
    bash -c "
        source '${TEMP_SH}'
        pgai_listener_cleanliness_check
    " 2>&1)" || T3_EXIT=$?

# Kill the fixture process regardless of assertions.
kill "${T3_PID}" 2>/dev/null || true
wait "${T3_PID}" 2>/dev/null || true

if (( T3_EXIT != 0 )); then
    _pass "own-listener-flagged/exit-nonzero"
else
    _fail "own-listener-flagged/exit-nonzero" \
        "expected non-zero exit, got 0; own-project listener was not flagged. output: ${T3_OUT}"
fi

if echo "${T3_OUT}" | grep -q "\[pgai-listener\] FAIL"; then
    _pass "own-listener-flagged/fail-message"
else
    _fail "own-listener-flagged/fail-message" \
        "expected FAIL message; output: ${T3_OUT}"
fi

if echo "${T3_OUT}" | grep -q "${T3_PID}"; then
    _pass "own-listener-flagged/pid-in-output"
else
    _fail "own-listener-flagged/pid-in-output" \
        "PID ${T3_PID} not found in output: ${T3_OUT}"
fi

# ---------------------------------------------------------------------------
# TEST 4: Wide-scope fallback — when no subtree is set, basename match still
#         catches listeners under the temp root (backward compatibility).
# ---------------------------------------------------------------------------
echo ""
echo "=== TEST 4: Wide-scope fallback — no subtree set, basename match still works ==="

T4_TEMP_ROOT="${FIXTURE_DIR}/t4-root"
T4_CWD="${T4_TEMP_ROOT}/some/nested/dir"
T4_PIDFILE="${T4_TEMP_ROOT}/t4-listener.pid"

_spawn_listener "${T4_CWD}" 49975 "${T4_PIDFILE}"
T4_PID="$(cat "${T4_PIDFILE}" 2>/dev/null || echo "")"

if [[ -z "${T4_PID}" ]]; then
    _fail "wide-scope-fallback/spawn" "failed to capture fixture PID for TEST 4"
    T4_PID="0"
fi

T4_EXIT=0
T4_OUT="$(PGAI_AGENT_KANBAN_TEMP_DIR="${T4_TEMP_ROOT}" \
    bash -c "
        unset PGAI_PROJECT_TEMP_SUBTREE
        source '${TEMP_SH}'
        pgai_listener_cleanliness_check
    " 2>&1)" || T4_EXIT=$?

kill "${T4_PID}" 2>/dev/null || true
wait "${T4_PID}" 2>/dev/null || true

if (( T4_EXIT != 0 )); then
    _pass "wide-scope-fallback/exit-nonzero"
else
    _fail "wide-scope-fallback/exit-nonzero" \
        "expected non-zero exit, got 0; wide-scope check failed to catch listener. output: ${T4_OUT}"
fi

if echo "${T4_OUT}" | grep -q "\[pgai-listener\] FAIL"; then
    _pass "wide-scope-fallback/fail-message"
else
    _fail "wide-scope-fallback/fail-message" \
        "expected FAIL message in wide-scope fallback mode; output: ${T4_OUT}"
fi

# ---------------------------------------------------------------------------
# TEST 5: Prefix-collision regression (BUG-0017) — a sibling project whose
#         name is a proper prefix extension of this project's name is NOT
#         flagged when the check is scoped to this project.
#
# Setup:
#   - This project's subtree: FIXTURE_DIR/t5-root/projects/pgai-agent-kanban
#   - Sibling project's subtree:
#       FIXTURE_DIR/t5-root/projects/pgai-agent-kanban-ui
#   - Listener cwd: .../pgai-agent-kanban-ui/worktrees/some-feature
#     The cmdline produced by _spawn_listener embeds the cwd path via
#     os.chdir('${cwd_dir}') — so "pgai-agent-kanban" appears as a
#     substring of the cmdline inside "pgai-agent-kanban-ui/...".
#   - Check scoped to: .../pgai-agent-kanban  (NO trailing slash)
#
# Expected: pgai_listener_cleanliness_check exits 0 — the sibling listener
#   must NOT be flagged even though "pgai-agent-kanban" is a substring of
#   the cmdline argument "pgai-agent-kanban-ui/...".
# ---------------------------------------------------------------------------
echo ""
echo "=== TEST 5: Prefix-collision (BUG-0017) — sibling with prefix-matching name not flagged ==="

T5_TEMP_ROOT="${FIXTURE_DIR}/t5-root"
T5_OWN_SUBTREE="${T5_TEMP_ROOT}/projects/pgai-agent-kanban"
T5_SIBLING_CWD="${T5_TEMP_ROOT}/projects/pgai-agent-kanban-ui/worktrees/some-feature"
T5_PIDFILE="${T5_TEMP_ROOT}/t5-listener.pid"

_spawn_listener "${T5_SIBLING_CWD}" 49976 "${T5_PIDFILE}"
T5_PID="$(cat "${T5_PIDFILE}" 2>/dev/null || echo "")"

if [[ -z "${T5_PID}" ]]; then
    _fail "prefix-collision-not-flagged/spawn" "failed to capture fixture PID for TEST 5"
    T5_PID="0"
fi

T5_EXIT=0
T5_OUT="$(PGAI_AGENT_KANBAN_TEMP_DIR="${T5_TEMP_ROOT}" \
    PGAI_PROJECT_TEMP_SUBTREE="${T5_OWN_SUBTREE}" \
    bash -c "
        source '${TEMP_SH}'
        pgai_listener_cleanliness_check
    " 2>&1)" || T5_EXIT=$?

kill "${T5_PID}" 2>/dev/null || true
wait "${T5_PID}" 2>/dev/null || true

if (( T5_EXIT == 0 )); then
    _pass "prefix-collision-not-flagged/exit-zero"
else
    _fail "prefix-collision-not-flagged/exit-zero" \
        "expected exit 0, got ${T5_EXIT}; sibling pgai-agent-kanban-ui listener was falsely flagged. output: ${T5_OUT}"
fi

if echo "${T5_OUT}" | grep -q "\[pgai-listener\] FAIL"; then
    _fail "prefix-collision-not-flagged/no-fail-message" \
        "FAIL message emitted for sibling-with-prefix-name listener (BUG-0017 regression). output: ${T5_OUT}"
else
    _pass "prefix-collision-not-flagged/no-fail-message"
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
