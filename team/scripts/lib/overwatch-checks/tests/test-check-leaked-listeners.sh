#!/usr/bin/env bash
# team/scripts/lib/overwatch-checks/tests/test-check-leaked-listeners.sh
#
# Fixture test for check-leaked-listeners.sh.
#
# Verifies the acceptance criteria:
#   1. A process with cwd under the framework temp root is detected; the kill
#      path fires only on the cwd match.
#   2. The check runs without error when no leaked processes exist.
#   3. Source is side-effect-free (no output on source).
#   4. bash -n passes.
#
# All work is done in a self-contained tmpdir. The live kanban installation
# is never touched.
#
# Usage:
#   bash test-check-leaked-listeners.sh [--keep-tmpdir]
#
# Exit codes:
#   0  — all assertions passed
#   1  — one or more assertions failed

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHECKS_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
CHECK_SCRIPT="${CHECKS_DIR}/check-leaked-listeners.sh"
LIB_DIR="$(cd "${CHECKS_DIR}/.." && pwd)"
PROTOCOL_SH="${LIB_DIR}/overwatch_protocol.sh"

# Source temp.sh for pgai_mktemp_d
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

FIXTURE_DIR="$(pgai_mktemp_d check_leaked_listeners_test)"

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

    cat > "${kanban_root}/projects/${project_name}/project.cfg" <<PCFG
[project]
project_name = ${project_name}
workflow_type = release
PCFG
}

# ---------------------------------------------------------------------------
# Helper: stub the overwatch_log_action function in the current shell so
# fixture tests don't need a full actions.log setup.
# ---------------------------------------------------------------------------
_stub_log_action() {
    # shellcheck disable=SC2034
    _STUBBED_LOG_CALLS=()
    overwatch_log_action() {
        _STUBBED_LOG_CALLS+=("$*")
    }
    export -f overwatch_log_action
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
    _fail "source-side-effect-free" "Unexpected output on source: ${SOURCE_OUTPUT}"
fi

# ===========================================================================
# TEST 3: Function defined after source
# ===========================================================================
echo ""
echo "=== TEST 3: Function overwatch_check_leaked_listeners is exported ==="

if bash -c "source '${CHECK_SCRIPT}'; declare -f overwatch_check_leaked_listeners >/dev/null 2>&1 && echo 'defined'" | grep -q 'defined'; then
    _pass "function-defined"
else
    _fail "function-defined" "overwatch_check_leaked_listeners not defined after source"
fi

# ===========================================================================
# TEST 4: No-op when no processes have cwd under temp root
# ===========================================================================
echo ""
echo "=== TEST 4: No-op when no leaked processes exist ==="

T4_ROOT="${FIXTURE_DIR}/t4"
_build_fixture_kanban "${T4_ROOT}" "test-project"
# Use a temp root that no real process will have as cwd
T4_TEMP_ROOT="${FIXTURE_DIR}/t4-temp-root-unique-$$"
mkdir -p "${T4_TEMP_ROOT}"

T4_EXIT=0
T4_OUT="$(KANBAN_ROOT="${T4_ROOT}" \
    OVERWATCH_PROJECT="test-project" \
    PGAI_AGENT_KANBAN_TEMP_DIR="${T4_TEMP_ROOT}" \
    bash -c "
        source '${PROTOCOL_SH}'
        source '${CHECK_SCRIPT}'
        overwatch_check_leaked_listeners --dry-run
    " 2>&1)" || T4_EXIT=$?

if (( T4_EXIT == 0 )); then
    _pass "no-leaked-processes/exit-zero"
else
    _fail "no-leaked-processes/exit-zero" "expected 0 got ${T4_EXIT}; output: ${T4_OUT}"
fi

if echo "${T4_OUT}" | grep -q "no processes found"; then
    _pass "no-leaked-processes/message"
else
    _fail "no-leaked-processes/message" "expected 'no processes found' in output"
fi

# ===========================================================================
# TEST 5: Process with cwd under temp root is detected (dry-run)
# ===========================================================================
echo ""
echo "=== TEST 5: Process with cwd under temp root is detected ==="

T5_ROOT="${FIXTURE_DIR}/t5"
_build_fixture_kanban "${T5_ROOT}" "test-project"

# Create a temp root directory for the fixture.
T5_TEMP_ROOT="${FIXTURE_DIR}/t5-temp-root"
mkdir -p "${T5_TEMP_ROOT}/active-subdir"

# Spawn a listening process with its cwd set to the temp subdir.
# The check targets processes with:
#   (a) cwd under the framework temp root, AND
#   (b) an active TCP listening socket.
# Use python3 to open a listen socket, then change cwd to the fixture subdir.
# Use setsid to place the process in a new session, outside the current pgroup.
T5_CWD="${T5_TEMP_ROOT}/active-subdir"
T5_PIDFILE="${T5_TEMP_ROOT}/fixture.pid"
setsid python3 -c "
import os, socket, time, sys
os.chdir('${T5_CWD}')
with open('${T5_PIDFILE}', 'w') as f:
    f.write(str(os.getpid()))
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(('127.0.0.1', 49971))
s.listen(1)
time.sleep(60)
" &

# Wait for the pidfile to appear (up to 2 seconds).
_T5_WAIT=0
while [[ ! -f "${T5_PIDFILE}" ]] && (( _T5_WAIT < 20 )); do
    sleep 0.1
    _T5_WAIT=$(( _T5_WAIT + 1 ))
done
T5_PID="$(cat "${T5_PIDFILE}" 2>/dev/null || echo "")"

if [[ -z "${T5_PID}" ]]; then
    _fail "leaked-process-detected" "failed to capture fixture PID"
    T5_PID="0"
fi

T5_EXIT=0
T5_OUT="$(KANBAN_ROOT="${T5_ROOT}" \
    OVERWATCH_PROJECT="test-project" \
    PGAI_AGENT_KANBAN_TEMP_DIR="${T5_TEMP_ROOT}" \
    bash -c "
        source '${PROTOCOL_SH}'
        source '${CHECK_SCRIPT}'
        overwatch_check_leaked_listeners --dry-run
    " 2>&1)" || T5_EXIT=$?

# Clean up the test process whether or not assertions pass.
kill "${T5_PID}" 2>/dev/null || true
wait "${T5_PID}" 2>/dev/null || true

if (( T5_EXIT == 0 )); then
    _pass "leaked-process-detected/exit-zero"
else
    _fail "leaked-process-detected/exit-zero" "exit ${T5_EXIT}"
fi

# The output should mention the PID and the "would kill" dry-run message.
if echo "${T5_OUT}" | grep -q "${T5_PID}"; then
    _pass "leaked-process-detected/pid-in-output"
else
    _fail "leaked-process-detected/pid-in-output" "PID ${T5_PID} not found in output: ${T5_OUT}"
fi

if echo "${T5_OUT}" | grep -qi "would kill\|dry-run"; then
    _pass "leaked-process-detected/dry-run-message"
else
    _fail "leaked-process-detected/dry-run-message" "no dry-run message; output: ${T5_OUT}"
fi

# ===========================================================================
# TEST 6: Kill path fires on live run (cwd match + listener)
# ===========================================================================
echo ""
echo "=== TEST 6: Kill path fires on live run (cwd under temp root + listener) ==="

T6_ROOT="${FIXTURE_DIR}/t6"
_build_fixture_kanban "${T6_ROOT}" "test-project"
T6_TEMP_ROOT="${FIXTURE_DIR}/t6-temp-root"
mkdir -p "${T6_TEMP_ROOT}/subdir"

# Spawn a listener process with cwd under the temp root in a new session.
T6_CWD="${T6_TEMP_ROOT}/subdir"
T6_PIDFILE="${T6_TEMP_ROOT}/fixture.pid"
setsid python3 -c "
import os, socket, time
os.chdir('${T6_CWD}')
with open('${T6_PIDFILE}', 'w') as f:
    f.write(str(os.getpid()))
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(('127.0.0.1', 49972))
s.listen(1)
time.sleep(60)
" &

# Wait for pidfile.
_T6_WAIT=0
while [[ ! -f "${T6_PIDFILE}" ]] && (( _T6_WAIT < 20 )); do
    sleep 0.1
    _T6_WAIT=$(( _T6_WAIT + 1 ))
done
T6_PID="$(cat "${T6_PIDFILE}" 2>/dev/null || echo "0")"

T6_EXIT=0
T6_OUT="$(KANBAN_ROOT="${T6_ROOT}" \
    OVERWATCH_PROJECT="test-project" \
    PGAI_AGENT_KANBAN_TEMP_DIR="${T6_TEMP_ROOT}" \
    bash -c "
        source '${PROTOCOL_SH}'
        source '${CHECK_SCRIPT}'
        overwatch_check_leaked_listeners
    " 2>&1)" || T6_EXIT=$?

if (( T6_EXIT == 0 )); then
    _pass "kill-path-fires/exit-zero"
else
    _fail "kill-path-fires/exit-zero" "exit ${T6_EXIT}"
fi

# The output should mention "killing" (not dry-run).
if echo "${T6_OUT}" | grep -qi "killing\|kill.*${T6_PID}\|auto-fix"; then
    _pass "kill-path-fires/kill-message"
else
    _fail "kill-path-fires/kill-message" "no kill message in output: ${T6_OUT}"
fi

# The process should be gone (SIGTERM sent).
sleep 0.5
if ! kill -0 "${T6_PID}" 2>/dev/null; then
    _pass "kill-path-fires/process-killed"
else
    # Process still alive — clean it up and fail.
    kill "${T6_PID}" 2>/dev/null || true
    wait "${T6_PID}" 2>/dev/null || true
    _fail "kill-path-fires/process-killed" "process ${T6_PID} still alive after check ran"
fi

# Action log should have an entry.
if [[ -s "${T6_ROOT}/projects/test-project/overwatch/actions.log" ]]; then
    _pass "kill-path-fires/action-log-populated"
else
    _fail "kill-path-fires/action-log-populated"
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
