#!/usr/bin/env bash
# team/scripts/lib/overwatch-checks/tests/test-check-bare-tmp-litter.sh
#
# Fixture tests for check-bare-tmp-litter.sh.
#
# Acceptance criteria tested:
#   A. bash -n passes.
#   B. Source is side-effect-free.
#   C. Function overwatch_check_bare_tmp_litter is defined after source.
#   D. Crashed-session litter: flagged exactly once; not flagged again on rerun
#      (dedup proven).
#   E. Operator-shaped file (wrong owner) NOT flagged.
#   F. Pre-existing /tmp entry (present before any session) NOT flagged.
#   G. Allowlist entries (systemd-*, tmux-*, pytest-of-*) NOT flagged.
#   H. Entry under framework temp root NOT flagged.
#   I. After every check run, all fixture /tmp proxy files still exist on disk.
#   J. No snapshots found: check exits 0 and logs "no snapshots".
#   K. Check errors do NOT abort the sweep (check returns 0 even on bad env).
#   L. Live-runner retest: sweep runner discovers the check via the drop-in seam.
#
# Test strategy for mock /tmp:
#   Tests D-I exercise the /tmp scan logic. We cannot reliably intercept the
#   /tmp/* glob inside a sourced bash function via eval/sed because declare -A
#   arrays inside eval'd functions have different scoping under set -uo pipefail.
#
#   Instead, we run each of those tests in a subprocess that uses a helper
#   bash script wrapping the check — but WITHOUT set -uo pipefail in the
#   wrapper itself, avoiding the strict-mode interaction. The wrapper uses
#   a custom MOCK_TMP_DIR env var that the check detects via an overriding
#   of the /tmp scan.
#
#   For tests that only need the check logic without /tmp, we run in-process.
#
# All work is done inside a self-contained tmpdir.  The live kanban
# installation is never touched.
#
# Usage:
#   bash test-check-bare-tmp-litter.sh [--keep-tmpdir]
#
# Exit codes:
#   0  — all assertions passed
#   1  — one or more assertions failed

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHECKS_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
CHECK_SCRIPT="${CHECKS_DIR}/check-bare-tmp-litter.sh"
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

FIXTURE_DIR="$(pgai_mktemp_d check_bare_tmp_litter_test)"

_cleanup() {
    if (( KEEP_TMPDIR == 0 )); then
        rm -rf "${FIXTURE_DIR}" 2>/dev/null || true
    else
        echo "Kept tmpdir: ${FIXTURE_DIR}"
    fi
}
trap _cleanup EXIT

# ---------------------------------------------------------------------------
# Helper: build a minimal fixture kanban root.
# ---------------------------------------------------------------------------
_build_fixture_kanban() {
    local kanban_root="$1"
    local project_name="${2:-test-project}"

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

    cat > "${kanban_root}/projects/${project_name}/project.cfg" <<PCFG
[project]
project_name = ${project_name}
workflow_type = release
PCFG
}

# ---------------------------------------------------------------------------
# Helper: build a task snapshot file at the expected wake-bracket path.
#
# Arguments:
#   $1 fw_temp_root   — the framework temp dir
#   $2 task_id        — task identifier
#   $3 session_epoch  — epoch to write as the session start
#   $4+ pre_names     — basenames present BEFORE the session
# ---------------------------------------------------------------------------
_build_task_snapshot() {
    local fw_temp_root="$1"
    local task_id="$2"
    local session_epoch="$3"
    shift 3
    local pre_names=("$@")

    local snap_dir="${fw_temp_root}/tasks/${task_id}/litter"
    mkdir -p "${snap_dir}"
    local snap_file="${snap_dir}/pre_dispatch_tmp_snapshot"

    {
        echo "epoch=${session_epoch}"
        local bn
        for bn in "${pre_names[@]}"; do
            printf '%s\t%s\n' "$(( session_epoch - 60 ))" "${bn}"
        done | sort -k2
    } > "${snap_file}"
}

# ---------------------------------------------------------------------------
# Helper: run the check in a subprocess via a helper wrapper script.
#
# The wrapper is written without set -uo pipefail so that bash associative
# arrays inside the check function work correctly.  The wrapper monkey-patches
# the check to redirect /tmp scanning to a MOCK_TMP_DIR.
#
# Arguments:
#   $1 kanban_root
#   $2 project_name
#   $3 fw_temp_root
#   $4 mock_tmp_dir   — directory whose non-hidden files are treated as /tmp entries
#   $5 fw_user        — the username returned by whoami (framework user identity)
#   $6 fw_temp_basename — the basename of the framework temp root (excluded from scan)
#
# Ownership and mtime for each entry are controlled via:
#   .owners/<basename> — contains owner username
#   .mtimes/<basename> — contains mtime epoch
# If these files are absent, defaults: owner=other-user, mtime=current epoch.
# ---------------------------------------------------------------------------
_run_check_mocked() {
    local kanban_root="$1"
    local project_name="$2"
    local fw_temp_root="$3"
    local mock_tmp_dir="$4"
    local fw_user="$5"
    local fw_temp_basename="$6"

    local wrapper="${FIXTURE_DIR}/_wrapper_$$.sh"

    # Write the wrapper: NO set -uo pipefail so declare -A works inside eval.
    # shellcheck disable=SC2016
    cat > "${wrapper}" <<WRAP
#!/usr/bin/env bash
# Wrapper: no strict mode so declare -A inside eval'd check works correctly.

source '${PROTOCOL_SH}'
source '${CHECK_SCRIPT}'

# Override _cbtl_file_owner: reads from .owners/<basename> in mock_tmp_dir.
_cbtl_file_owner() {
    local path="\$1"
    local bn; bn="\$(basename "\${path}")"
    local f="${mock_tmp_dir}/.owners/\${bn}"
    if [[ -f "\${f}" ]]; then cat "\${f}"; else echo "other-user"; fi
}

# Override _cbtl_file_mtime: reads from .mtimes/<basename> in mock_tmp_dir.
_cbtl_file_mtime() {
    local path="\$1"
    local bn; bn="\$(basename "\${path}")"
    local f="${mock_tmp_dir}/.mtimes/\${bn}"
    if [[ -f "\${f}" ]]; then cat "\${f}"; else date +%s; fi
}

# Override whoami to return the fixture framework user.
whoami() { echo '${fw_user}'; }

# Patch the main function: replace /tmp/* glob with mock_tmp_dir/*.
# Use eval without strict mode — associative arrays work correctly here.
_orig_fn="\$(declare -f overwatch_check_bare_tmp_litter)"
_patched_fn="\$(printf '%s' "\${_orig_fn}" | sed 's|for entry in /tmp/\*|for entry in ${mock_tmp_dir}/*|g')"
eval "\${_patched_fn}"

KANBAN_ROOT='${kanban_root}' \
OVERWATCH_PROJECT='${project_name}' \
PGAI_AGENT_KANBAN_TEMP_DIR='${fw_temp_root}' \
    overwatch_check_bare_tmp_litter
WRAP
    chmod +x "${wrapper}"
    bash "${wrapper}" 2>&1
    local rc=$?
    rm -f "${wrapper}" 2>/dev/null || true
    return ${rc}
}

# ---------------------------------------------------------------------------
# Helper: create a mock /tmp entry with owner and mtime.
# ---------------------------------------------------------------------------
_create_mock_entry() {
    local mock_tmp_dir="$1"
    local basename="$2"
    local owner="$3"
    local mtime_epoch="$4"

    mkdir -p "${mock_tmp_dir}/.owners" "${mock_tmp_dir}/.mtimes"
    touch "${mock_tmp_dir}/${basename}"
    echo "${owner}" > "${mock_tmp_dir}/.owners/${basename}"
    echo "${mtime_epoch}" > "${mock_tmp_dir}/.mtimes/${basename}"
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
echo "=== TEST 3: Function overwatch_check_bare_tmp_litter is defined ==="

if bash -c "source '${CHECK_SCRIPT}'; declare -f overwatch_check_bare_tmp_litter >/dev/null 2>&1 && echo 'defined'" | grep -q 'defined'; then
    _pass "function-defined"
else
    _fail "function-defined"
fi

# ===========================================================================
# TEST 4: No snapshots found — exits 0, logs no-snapshots
# ===========================================================================
echo ""
echo "=== TEST 4: No snapshots found — skips silently ==="

T4_ROOT="${FIXTURE_DIR}/t4"
T4_FW_TEMP="${FIXTURE_DIR}/t4-fwtmp"
_build_fixture_kanban "${T4_ROOT}" "test-project"
mkdir -p "${T4_FW_TEMP}"

T4_EXIT=0
T4_OUT="$(KANBAN_ROOT="${T4_ROOT}" \
    OVERWATCH_PROJECT="test-project" \
    PGAI_AGENT_KANBAN_TEMP_DIR="${T4_FW_TEMP}" \
    bash -c "
        source '${PROTOCOL_SH}'
        source '${CHECK_SCRIPT}'
        overwatch_check_bare_tmp_litter
    " 2>&1)" || T4_EXIT=$?

if (( T4_EXIT == 0 )); then
    _pass "no-snapshots/exit-zero"
else
    _fail "no-snapshots/exit-zero" "exit ${T4_EXIT}"
fi

if echo "${T4_OUT}" | grep -qi "no.*snapshot\|nothing to check"; then
    _pass "no-snapshots/message"
else
    _fail "no-snapshots/message" "output: ${T4_OUT}"
fi

if grep -q "skipped-no-snapshots" \
    "${T4_ROOT}/projects/test-project/overwatch/actions.log" 2>/dev/null; then
    _pass "no-snapshots/action-log-skipped"
else
    _fail "no-snapshots/action-log-skipped" \
        "action log: $(cat "${T4_ROOT}/projects/test-project/overwatch/actions.log" 2>/dev/null)"
fi

# ===========================================================================
# TEST 5: Crashed-session litter — flagged exactly once; dedup on rerun
# ===========================================================================
echo ""
echo "=== TEST 5: Crashed-session litter flagged once; not again on rerun ==="

T5_ROOT="${FIXTURE_DIR}/t5"
T5_FW_TEMP="${FIXTURE_DIR}/t5-fwtmp"
T5_MOCK="${FIXTURE_DIR}/t5-mock"
_build_fixture_kanban "${T5_ROOT}" "test-project"
mkdir -p "${T5_MOCK}"

SESS_EPOCH_5=$(( $(date +%s) - 300 ))
_build_task_snapshot "${T5_FW_TEMP}" "TASK-001" "${SESS_EPOCH_5}" "legit-pre-entry"

FW_USER_5="$(whoami)"
FW_TEMP_BN_5="$(basename "${T5_FW_TEMP}")"
LITTER_MTIME_5=$(( SESS_EPOCH_5 + 60 ))
PRE_MTIME_5=$(( SESS_EPOCH_5 - 60 ))

# tester_r1.log: owned by fw user, created after session (litter from crashed session)
_create_mock_entry "${T5_MOCK}" "tester_r1.log" "${FW_USER_5}" "${LITTER_MTIME_5}"
# legit-pre-entry: present before session (not litter — in snapshot pre-names)
_create_mock_entry "${T5_MOCK}" "legit-pre-entry" "${FW_USER_5}" "${PRE_MTIME_5}"
# framework temp root basename: excluded by name
_create_mock_entry "${T5_MOCK}" "${FW_TEMP_BN_5}" "${FW_USER_5}" "${LITTER_MTIME_5}"

T5_EXIT=0
T5_OUT="$(_run_check_mocked \
    "${T5_ROOT}" "test-project" \
    "${T5_FW_TEMP}" "${T5_MOCK}" "${FW_USER_5}" "${FW_TEMP_BN_5}" 2>&1)" || T5_EXIT=$?

if (( T5_EXIT == 0 )); then
    _pass "litter-first-run/exit-zero"
else
    _fail "litter-first-run/exit-zero" "exit ${T5_EXIT}; output: ${T5_OUT}"
fi

T5_ACTION_LOG="${T5_ROOT}/projects/test-project/overwatch/actions.log"
if grep -q "tester_r1.log" "${T5_ACTION_LOG}" 2>/dev/null; then
    _pass "litter-first-run/entry-flagged"
else
    _fail "litter-first-run/entry-flagged" \
        "action log: $(cat "${T5_ACTION_LOG}" 2>/dev/null)"
fi

if grep -q "litter-found" "${T5_ACTION_LOG}" 2>/dev/null; then
    _pass "litter-first-run/litter-found-action"
else
    _fail "litter-first-run/litter-found-action"
fi

T5_DEDUP="${T5_ROOT}/projects/test-project/overwatch/litter-reported.txt"
if grep -q "tester_r1.log" "${T5_DEDUP}" 2>/dev/null; then
    _pass "litter-first-run/dedup-written"
else
    _fail "litter-first-run/dedup-written" \
        "dedup file: $(cat "${T5_DEDUP}" 2>/dev/null)"
fi

# Proxy files must still exist (REPORT-ONLY: nothing deleted).
if [[ -f "${T5_MOCK}/tester_r1.log" ]]; then
    _pass "litter-first-run/file-survived"
else
    _fail "litter-first-run/file-survived" "litter proxy file was deleted!"
fi

if [[ -f "${T5_MOCK}/legit-pre-entry" ]]; then
    _pass "litter-first-run/pre-entry-survived"
else
    _fail "litter-first-run/pre-entry-survived" "pre-entry proxy file was deleted!"
fi

# Count action log entries before second run.
T5_LINES_BEFORE="$(wc -l < "${T5_ACTION_LOG}")"

# --- Second run: litter should NOT be flagged again (dedup) ---
T5B_EXIT=0
T5B_OUT="$(_run_check_mocked \
    "${T5_ROOT}" "test-project" \
    "${T5_FW_TEMP}" "${T5_MOCK}" "${FW_USER_5}" "${FW_TEMP_BN_5}" 2>&1)" || T5B_EXIT=$?

if (( T5B_EXIT == 0 )); then
    _pass "litter-rerun/exit-zero"
else
    _fail "litter-rerun/exit-zero" "exit ${T5B_EXIT}"
fi

T5_LINES_AFTER="$(wc -l < "${T5_ACTION_LOG}")"
T5_NEW_LINES=$(( T5_LINES_AFTER - T5_LINES_BEFORE ))
T5_RERUN_CONTENT="$(tail -n "${T5_NEW_LINES}" "${T5_ACTION_LOG}" 2>/dev/null)"

# On rerun, "litter-found" for tester_r1.log must NOT appear again.
if echo "${T5_RERUN_CONTENT}" | grep -q "tester_r1.log"; then
    _fail "litter-rerun/not-reflagged" \
        "tester_r1.log appeared again on rerun: ${T5_RERUN_CONTENT}"
else
    _pass "litter-rerun/not-reflagged"
fi

# Proxy files must still exist after rerun.
if [[ -f "${T5_MOCK}/tester_r1.log" ]]; then
    _pass "litter-rerun/file-survived"
else
    _fail "litter-rerun/file-survived" "litter proxy file was deleted on rerun!"
fi

# ===========================================================================
# TEST 6: Operator-shaped file (wrong owner) NOT flagged
# ===========================================================================
echo ""
echo "=== TEST 6: Operator-owned file NOT flagged ==="

T6_ROOT="${FIXTURE_DIR}/t6"
T6_FW_TEMP="${FIXTURE_DIR}/t6-fwtmp"
T6_MOCK="${FIXTURE_DIR}/t6-mock"
_build_fixture_kanban "${T6_ROOT}" "test-project"
mkdir -p "${T6_MOCK}"

SESS_EPOCH_6=$(( $(date +%s) - 300 ))
_build_task_snapshot "${T6_FW_TEMP}" "TASK-002" "${SESS_EPOCH_6}"

FW_USER_6="$(whoami)"
FW_TEMP_BN_6="$(basename "${T6_FW_TEMP}")"
OP_MTIME_6=$(( SESS_EPOCH_6 + 60 ))

# Operator's file: owned by "other-user" (different from fw user)
_create_mock_entry "${T6_MOCK}" "operator-report.tar.gz" "other-user" "${OP_MTIME_6}"

T6_EXIT=0
T6_OUT="$(_run_check_mocked \
    "${T6_ROOT}" "test-project" \
    "${T6_FW_TEMP}" "${T6_MOCK}" "${FW_USER_6}" "${FW_TEMP_BN_6}" 2>&1)" || T6_EXIT=$?

if (( T6_EXIT == 0 )); then
    _pass "operator-file/exit-zero"
else
    _fail "operator-file/exit-zero" "exit ${T6_EXIT}"
fi

T6_ACTION_LOG="${T6_ROOT}/projects/test-project/overwatch/actions.log"
if ! grep -q "operator-report" "${T6_ACTION_LOG}" 2>/dev/null; then
    _pass "operator-file/not-flagged"
else
    _fail "operator-file/not-flagged" \
        "operator file appeared in action log: $(cat "${T6_ACTION_LOG}")"
fi

if [[ -f "${T6_MOCK}/operator-report.tar.gz" ]]; then
    _pass "operator-file/file-survived"
else
    _fail "operator-file/file-survived" "operator file was deleted!"
fi

# ===========================================================================
# TEST 7: Pre-existing entry (in snapshot, before session) NOT flagged
# ===========================================================================
echo ""
echo "=== TEST 7: Pre-existing /tmp entry NOT flagged ==="

T7_ROOT="${FIXTURE_DIR}/t7"
T7_FW_TEMP="${FIXTURE_DIR}/t7-fwtmp"
T7_MOCK="${FIXTURE_DIR}/t7-mock"
_build_fixture_kanban "${T7_ROOT}" "test-project"
mkdir -p "${T7_MOCK}"

SESS_EPOCH_7=$(( $(date +%s) - 300 ))
# old-file was present in the snapshot (it existed before the session).
_build_task_snapshot "${T7_FW_TEMP}" "TASK-003" "${SESS_EPOCH_7}" "old-file.log"

FW_USER_7="$(whoami)"
FW_TEMP_BN_7="$(basename "${T7_FW_TEMP}")"
OLD_MTIME_7=$(( SESS_EPOCH_7 - 120 ))

_create_mock_entry "${T7_MOCK}" "old-file.log" "${FW_USER_7}" "${OLD_MTIME_7}"

T7_EXIT=0
T7_OUT="$(_run_check_mocked \
    "${T7_ROOT}" "test-project" \
    "${T7_FW_TEMP}" "${T7_MOCK}" "${FW_USER_7}" "${FW_TEMP_BN_7}" 2>&1)" || T7_EXIT=$?

if (( T7_EXIT == 0 )); then
    _pass "pre-existing/exit-zero"
else
    _fail "pre-existing/exit-zero" "exit ${T7_EXIT}"
fi

T7_ACTION_LOG="${T7_ROOT}/projects/test-project/overwatch/actions.log"
if ! grep -q "old-file" "${T7_ACTION_LOG}" 2>/dev/null; then
    _pass "pre-existing/not-flagged"
else
    _fail "pre-existing/not-flagged" \
        "pre-existing file appeared in action log: $(cat "${T7_ACTION_LOG}")"
fi

if [[ -f "${T7_MOCK}/old-file.log" ]]; then
    _pass "pre-existing/file-survived"
else
    _fail "pre-existing/file-survived" "old file was deleted!"
fi

# ===========================================================================
# TEST 8: Allowlist entries NOT flagged
# ===========================================================================
echo ""
echo "=== TEST 8: Allowlist entries NOT flagged ==="

T8_ROOT="${FIXTURE_DIR}/t8"
T8_FW_TEMP="${FIXTURE_DIR}/t8-fwtmp"
T8_MOCK="${FIXTURE_DIR}/t8-mock"
_build_fixture_kanban "${T8_ROOT}" "test-project"
mkdir -p "${T8_MOCK}"

SESS_EPOCH_8=$(( $(date +%s) - 300 ))
_build_task_snapshot "${T8_FW_TEMP}" "TASK-004" "${SESS_EPOCH_8}"

FW_USER_8="$(whoami)"
FW_TEMP_BN_8="$(basename "${T8_FW_TEMP}")"
AL_MTIME_8=$(( SESS_EPOCH_8 + 60 ))

# Allowlist entries: systemd-*, tmux-*, pytest-of-*
_create_mock_entry "${T8_MOCK}" "systemd-private-xyz" "${FW_USER_8}" "${AL_MTIME_8}"
_create_mock_entry "${T8_MOCK}" "tmux-1000" "${FW_USER_8}" "${AL_MTIME_8}"
_create_mock_entry "${T8_MOCK}" "pytest-of-rocky" "${FW_USER_8}" "${AL_MTIME_8}"

T8_EXIT=0
T8_OUT="$(_run_check_mocked \
    "${T8_ROOT}" "test-project" \
    "${T8_FW_TEMP}" "${T8_MOCK}" "${FW_USER_8}" "${FW_TEMP_BN_8}" 2>&1)" || T8_EXIT=$?

if (( T8_EXIT == 0 )); then
    _pass "allowlist/exit-zero"
else
    _fail "allowlist/exit-zero" "exit ${T8_EXIT}"
fi

T8_ACTION_LOG="${T8_ROOT}/projects/test-project/overwatch/actions.log"
# Check that "report:litter-found" (a flagged entry) does NOT appear;
# "report:no-litter-found" (clean summary) is acceptable.
if ! grep -q "report:litter-found" "${T8_ACTION_LOG}" 2>/dev/null; then
    _pass "allowlist/none-flagged"
else
    _fail "allowlist/none-flagged" \
        "allowlist entry flagged: $(cat "${T8_ACTION_LOG}")"
fi

for _al_bn in "systemd-private-xyz" "tmux-1000" "pytest-of-rocky"; do
    if [[ -f "${T8_MOCK}/${_al_bn}" ]]; then
        _pass "allowlist/${_al_bn}-survived"
    else
        _fail "allowlist/${_al_bn}-survived" "${_al_bn} was deleted!"
    fi
done

# ===========================================================================
# TEST 9: Entry under framework temp root NOT flagged
# ===========================================================================
echo ""
echo "=== TEST 9: Framework temp root entry NOT flagged ==="

T9_ROOT="${FIXTURE_DIR}/t9"
T9_FW_TEMP="${FIXTURE_DIR}/t9-fwtmp"
T9_MOCK="${FIXTURE_DIR}/t9-mock"
_build_fixture_kanban "${T9_ROOT}" "test-project"
mkdir -p "${T9_MOCK}"

SESS_EPOCH_9=$(( $(date +%s) - 300 ))
_build_task_snapshot "${T9_FW_TEMP}" "TASK-005" "${SESS_EPOCH_9}"

FW_USER_9="$(whoami)"
FW_TEMP_BN_9="$(basename "${T9_FW_TEMP}")"
FW_MTIME_9=$(( SESS_EPOCH_9 + 60 ))

# An entry whose basename matches the framework temp root — should be excluded.
_create_mock_entry "${T9_MOCK}" "${FW_TEMP_BN_9}" "${FW_USER_9}" "${FW_MTIME_9}"

T9_EXIT=0
T9_OUT="$(_run_check_mocked \
    "${T9_ROOT}" "test-project" \
    "${T9_FW_TEMP}" "${T9_MOCK}" "${FW_USER_9}" "${FW_TEMP_BN_9}" 2>&1)" || T9_EXIT=$?

if (( T9_EXIT == 0 )); then
    _pass "fw-temp-root/exit-zero"
else
    _fail "fw-temp-root/exit-zero" "exit ${T9_EXIT}"
fi

T9_ACTION_LOG="${T9_ROOT}/projects/test-project/overwatch/actions.log"
# Check that "report:litter-found" (a flagged entry) does NOT appear;
# "report:no-litter-found" (clean summary) is acceptable.
if ! grep -q "report:litter-found" "${T9_ACTION_LOG}" 2>/dev/null; then
    _pass "fw-temp-root/not-flagged"
else
    _fail "fw-temp-root/not-flagged" \
        "fw temp root entry flagged: $(cat "${T9_ACTION_LOG}")"
fi

if [[ -f "${T9_MOCK}/${FW_TEMP_BN_9}" ]]; then
    _pass "fw-temp-root/file-survived"
else
    _fail "fw-temp-root/file-survived" "fw temp root proxy file was deleted!"
fi

# ===========================================================================
# TEST 10: Check errors do not abort the sweep (bad env returns 0)
# ===========================================================================
echo ""
echo "=== TEST 10: Check errors do not abort the sweep ==="

T10_EXIT=0
T10_OUT="$(KANBAN_ROOT="/nonexistent/kanban/root" \
    OVERWATCH_PROJECT="test-project" \
    bash -c "
        source '${CHECK_SCRIPT}'
        overwatch_check_bare_tmp_litter
    " 2>&1)" || T10_EXIT=$?

if (( T10_EXIT == 0 )); then
    _pass "bad-env/exit-zero"
else
    _fail "bad-env/exit-zero" "exit ${T10_EXIT}"
fi

# ===========================================================================
# TEST 11: Live-runner drop-in seam — sweep runner discovers the check
# ===========================================================================
echo ""
echo "=== TEST 11: Live sweep runner discovers check-bare-tmp-litter via drop-in seam ==="

TEAM_SCRIPTS_DIR="$(cd "${CHECKS_DIR}/../.." && pwd)"
SWEEP_SCRIPT="${TEAM_SCRIPTS_DIR}/overwatch-sweep.sh"

T11_ROOT="${FIXTURE_DIR}/t11"
# Build two projects (matching multi-project sweep pattern).
_build_fixture_kanban "${T11_ROOT}" "alpha-project"
_build_fixture_kanban "${T11_ROOT}" "beta-project"
cat > "${T11_ROOT}/projects.cfg" <<CFG
[project:alpha-project]
priority=1

[project:beta-project]
priority=2
CFG

T11_EXIT=0
T11_OUT="$(KANBAN_ROOT="${T11_ROOT}" bash "${SWEEP_SCRIPT}" 2>&1)" || T11_EXIT=$?

if (( T11_EXIT == 0 )); then
    _pass "live-runner/exit-zero"
else
    _fail "live-runner/exit-zero" "sweep exited ${T11_EXIT}"
fi

# The sweep stderr mentions check-bare-tmp-litter when it sources and invokes it.
if echo "${T11_OUT}" | grep -qi "check-bare-tmp-litter"; then
    _pass "live-runner/check-discovered"
else
    _fail "live-runner/check-discovered" \
        "check-bare-tmp-litter not mentioned in sweep output (output: ${T11_OUT:0:500})"
fi

# Both project action logs should contain entries from check-bare-tmp-litter.
if grep -q "check-bare-tmp-litter" \
    "${T11_ROOT}/projects/alpha-project/overwatch/actions.log" 2>/dev/null; then
    _pass "live-runner/action-log-has-entry/alpha"
else
    _fail "live-runner/action-log-has-entry/alpha" \
        "action log: $(cat "${T11_ROOT}/projects/alpha-project/overwatch/actions.log" 2>/dev/null)"
fi

if grep -q "check-bare-tmp-litter" \
    "${T11_ROOT}/projects/beta-project/overwatch/actions.log" 2>/dev/null; then
    _pass "live-runner/action-log-has-entry/beta"
else
    _fail "live-runner/action-log-has-entry/beta" \
        "action log: $(cat "${T11_ROOT}/projects/beta-project/overwatch/actions.log" 2>/dev/null)"
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
