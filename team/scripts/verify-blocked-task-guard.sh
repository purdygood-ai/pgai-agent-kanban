#!/usr/bin/env bash
# team/scripts/verify-blocked-task-guard.sh
#
# Regression guard: blocked-task project guard in run_project_chain.
#
# The guard holds task dispatch for one project only while its human-gated
# blocks (BLOCKED + Needs Human: yes, across every agent queue) are
# systemic; the decision is pgai_agent_kanban.overwatch.block_storm's, and
# this script runs that module (through pp_run_ops, the way the driver
# does) against synthetic queues before checking the driver's wiring.
#
# Verifies that:
#   AC-1  A lone task-local block does not hold dispatch: one BLOCKED +
#         Needs Human yes task whose Block Record names per_task_timeout,
#         beside an unrelated BACKLOG task, decides task_local; the BACKLOG
#         task stays queued for dispatch.
#   AC-2  Systemic blocks do hold it, and only human-gated BLOCKED tasks
#         count:
#           - two per_task_timeout blocks 10 minutes apart decide systemic
#             (block storm inside block_storm_window, measured back from
#             the decision time), the same two blocks two hours apart
#             decide task_local;
#           - one plugin_load_failed block decides systemic on its own
#             (default systemic list), and task_local once --systemic-codes
#             is none;
#           - a BLOCKED task with Needs Human no, and a DONE task with Needs
#             Human yes (a stale [B] marker), are not counted at all (the
#             two halves of the human-gated predicate, each on its own).
#   AC-3  The guard lives in the shared wake driver, team/scripts/lib/
#         wake_driver.sh, inside run_project_chain:
#           - the driver exists, passes bash -n and carries no inline
#             Python heredoc;
#           - exactly one guard comment header, inside run_project_chain,
#             below which the driver calls the block_storm module's decide;
#           - the overwatch branch (never held) is tested before the branch
#             that logs the hold and sets _dispatch_gated=true;
#           - the dispatch loop sits inside the
#             `if [[ "$_dispatch_gated" == "false" ]]` block, which closes
#             with its end-comment fi;
#           - recheck_waiting_tasks and recheck_blocked_tasks run after that
#             closing fi, so a held wake still re-checks;
#           - after that block, >= 2 _dispatch_gated == "true" checks
#             (discovery + post-discovery) and the "intake unaffected" log
#             line keep PM discovery running while dispatch is held.
#   AC-4  Both provider scripts reach the shared driver: team/scripts/wake/
#         claude.sh and codex.sh each exist, pass bash -n, source
#         lib/wake_driver.sh, and do not define their own run_project_chain
#         (which would shadow the driver's guarded one).
#
# Usage:
#   team/scripts/verify-blocked-task-guard.sh [--verbose] [--help]
#
# Exit codes:
#   0  All assertions passed.
#   1  Assertion failure.
#   2  Configuration or environment error.
#
# Safety invariants:
#   - All fixtures are created under the framework temp root via pgai_mktemp_d.
#   - Fixtures are cleaned up via trap EXIT on both success and failure paths.
#   - The script never touches the real dev tree or the live kanban tree.
#   - Idempotent: safe to re-run; every run starts with a fresh fixture.

set -euo pipefail

# ---------------------------------------------------------------------------
# Locate this script and source shared helpers
# ---------------------------------------------------------------------------
_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

for _helper in temp.sh task_ids.sh pp_run_ops.sh; do
    if [[ ! -f "${_SCRIPT_DIR}/lib/${_helper}" ]]; then
        echo "ERROR: ${_helper} not found at: ${_SCRIPT_DIR}/lib/${_helper}" >&2
        exit 2
    fi
    # shellcheck disable=SC1090
    source "${_SCRIPT_DIR}/lib/${_helper}"
done
unset _helper

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
VERBOSE=false
PASS_COUNT=0
FAIL_COUNT=0

# ---------------------------------------------------------------------------
# Color helpers (only when stdout is a tty)
# ---------------------------------------------------------------------------
if [[ -t 1 ]]; then
    C_RED=$'\033[0;31m'
    C_GREEN=$'\033[0;32m'
    C_RESET=$'\033[0m'
else
    C_RED="" C_GREEN="" C_RESET=""
fi

pass() { echo "${C_GREEN}PASS${C_RESET}: $*"; (( PASS_COUNT++ )) || true; }
fail() { echo "${C_RED}FAIL${C_RESET}: $*"; (( FAIL_COUNT++ )) || true; }

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --verbose|-v)
            VERBOSE=true
            shift
            ;;
        --help|-h)
            cat <<EOF
Usage: $(basename "$0") [--verbose]

Regression guard: blocked-task project guard. Runs the hold decision
(pgai_agent_kanban.overwatch.block_storm) against synthetic queues -- a lone
task-local block does not hold dispatch, a block storm or a systemic reason
code does -- then checks the guard's wiring in lib/wake_driver.sh and that
both provider wake scripts reach it.

Options:
  --verbose, -v   Enable verbose bash tracing.
  --help, -h      Show this help and exit.

Exit codes:
  0  All assertions passed.
  1  Assertion failure.
  2  Configuration / environment error.
EOF
            exit 0
            ;;
        *)
            echo "ERROR: Unknown argument: $1" >&2
            exit 2
            ;;
    esac
done

[[ "$VERBOSE" == "true" ]] && set -x

# ---------------------------------------------------------------------------
# Create fixture under framework temp root — cleaned up on EXIT
# ---------------------------------------------------------------------------
_FIXTURE_ROOT="$(pgai_mktemp_d blocked_task_guard_verify)"

cleanup_fixture() {
    local exit_code=$?
    if [[ -d "$_FIXTURE_ROOT" ]]; then
        rm -rf "$_FIXTURE_ROOT"
    fi
    exit "$exit_code"
}
trap cleanup_fixture EXIT

WAKE_DRIVER_SH="${_SCRIPT_DIR}/lib/wake_driver.sh"
CLAUDE_SH="${_SCRIPT_DIR}/wake/claude.sh"
CODEX_SH="${_SCRIPT_DIR}/wake/codex.sh"
GATE_MODULE="pgai_agent_kanban.overwatch.block_storm"

echo "==================================================================="
echo "  verify-blocked-task-guard.sh"
echo "  fixture root  : $_FIXTURE_ROOT"
echo "  framework tmp : $(pgai_temp_dir)"
echo "==================================================================="
echo ""

# ---------------------------------------------------------------------------
# Synthetic queue helpers. Ids use the canonical AGENT-YYYYMMDD-NNN-slug
# shape: the guard skips any other shape, so the fixture must use it.
# ---------------------------------------------------------------------------
TASKS_DIR="${_FIXTURE_ROOT}/tasks"
QUEUE_DIR="${TASKS_DIR}/queues"
ID_PATTERN="$(kanban_task_id_pattern)"

# reset_fixture: empty task tree and an empty coder queue.
reset_fixture() {
    rm -rf "$TASKS_DIR"
    mkdir -p "$QUEUE_DIR"
    printf '# Coder Backlog\n\n' > "${QUEUE_DIR}/coder_backlog.md"
}

# add_task ID MARKER STATE NEEDS_HUMAN [REASON_CODE BLOCKED_AT]
#   Queue ID with MARKER and write its status.md; with REASON_CODE, record a
#   Block Record whose last_blocked_at is BLOCKED_AT.
add_task() {
    local id="$1" marker="$2" state="$3" needs_human="$4"
    local code="${5:-}" blocked_at="${6:-}"
    mkdir -p "${TASKS_DIR}/${id}"
    printf -- '- [%s] %s\n' "$marker" "$id" >> "${QUEUE_DIR}/coder_backlog.md"
    printf '# Status\n\n## Task\n%s\n\n## State\n%s\n\n## Needs Human\n%s\n' \
        "$id" "$state" "$needs_human" > "${TASKS_DIR}/${id}/status.md"
    if [[ -n "$code" ]]; then
        pp_run_ops pgai_agent_kanban.overwatch.block_record write --now "$blocked_at" \
            "${TASKS_DIR}/${id}/status.md" "$code" CODER "worktree=gone" >/dev/null
    fi
}

# gate_field FIELD [decide options...]
#   Run the guard's decision over the fixture and print one field of it.
#   The decision is taken at GATE_NOW (the window and hold cap are measured
#   back from it), so the fixed fixture times below decide the same way on
#   any day the script runs.
GATE_NOW=""
gate_field() {
    local field="$1"
    shift
    local line
    while IFS= read -r line; do
        if [[ "${line%%=*}" == "$field" ]]; then
            printf '%s\n' "${line#*=}"
            return 0
        fi
    done < <(pp_run_ops "$GATE_MODULE" decide "$QUEUE_DIR" "$TASKS_DIR" \
                 --id-pattern "$ID_PATTERN" --now "$GATE_NOW" "$@" 2>&1 || true)
    printf 'unreadable\n'
}

FIXTURE_DAY="20000101"
BLOCKED_A="CODER-${FIXTURE_DAY}-001-guard-fixture-blocked-a"
BLOCKED_B="CODER-${FIXTURE_DAY}-002-guard-fixture-blocked-b"
BACKLOG_ID="CODER-${FIXTURE_DAY}-003-guard-fixture-backlog"
T0="2026-01-01T10:00:00+00:00"
T0_PLUS_10M="2026-01-01T10:10:00+00:00"
T0_PLUS_2H="2026-01-01T12:00:00+00:00"

# ---------------------------------------------------------------------------
# AC-1: a lone task-local block does not hold dispatch
# ---------------------------------------------------------------------------
echo "==================================================================="
echo "  AC-1: A lone task-local block does not hold dispatch"
echo "==================================================================="
echo ""

reset_fixture
GATE_NOW="$T0"
add_task "$BLOCKED_A" B BLOCKED yes per_task_timeout "$T0"
add_task "$BACKLOG_ID" " " BACKLOG no
AC1_VERDICT="$(gate_field verdict)"
AC1_COUNT="$(gate_field count)"
echo "--- one task-local block: verdict=${AC1_VERDICT} count=${AC1_COUNT}"
if [[ "$AC1_VERDICT" == "task_local" && "$AC1_COUNT" == "1" ]]; then
    pass "AC-1 (task-local): one per_task_timeout block decides task_local (count 1) -- dispatch is not held"
else
    fail "AC-1 (task-local): expected verdict task_local, count 1 for one per_task_timeout block; got verdict '${AC1_VERDICT}', count '${AC1_COUNT}' -- run: python3 -m ${GATE_MODULE} decide --help"
fi
if grep -q "^- \[ \] ${BACKLOG_ID}" "${QUEUE_DIR}/coder_backlog.md"; then
    pass "AC-1 (backlog-intact): the unrelated BACKLOG task stays queued for dispatch"
else
    fail "AC-1 (backlog-intact): BACKLOG task ${BACKLOG_ID} not found in the fixture queue"
fi
echo ""

# ---------------------------------------------------------------------------
# AC-2: systemic blocks hold dispatch; only human-gated BLOCKED tasks count
# ---------------------------------------------------------------------------
echo "==================================================================="
echo "  AC-2: Systemic blocks hold dispatch"
echo "==================================================================="
echo ""

reset_fixture
add_task "$BLOCKED_A" B BLOCKED yes per_task_timeout "$T0"
add_task "$BLOCKED_B" B BLOCKED yes per_task_timeout "$T0_PLUS_10M"
GATE_NOW="$T0_PLUS_10M"
STORM_IN="$(gate_field verdict --window 3600)"
STORM_REASON="$(gate_field reason_code --window 3600)"
reset_fixture
add_task "$BLOCKED_A" B BLOCKED yes per_task_timeout "$T0"
add_task "$BLOCKED_B" B BLOCKED yes per_task_timeout "$T0_PLUS_2H"
GATE_NOW="$T0_PLUS_2H"
STORM_OUT="$(gate_field verdict --window 3600)"
echo "--- two per_task_timeout blocks: 10 min apart verdict=${STORM_IN} reason_code=${STORM_REASON}; 2 h apart verdict=${STORM_OUT}"
if [[ "$STORM_IN" == "systemic" && "$STORM_REASON" == "per_task_timeout" && "$STORM_OUT" == "task_local" ]]; then
    pass "AC-2 (storm): two same-code blocks inside block_storm_window hold dispatch; the same two outside it do not"
else
    fail "AC-2 (storm): expected systemic/per_task_timeout inside the window and task_local outside it; got '${STORM_IN}'/'${STORM_REASON}' and '${STORM_OUT}'"
fi

reset_fixture
add_task "$BLOCKED_A" B BLOCKED yes plugin_load_failed "$T0"
GATE_NOW="$T0"
LISTED="$(gate_field verdict)"
UNLISTED="$(gate_field verdict --systemic-codes none)"
echo "--- one plugin_load_failed block: default list verdict=${LISTED}; --systemic-codes none verdict=${UNLISTED}"
if [[ "$LISTED" == "systemic" && "$UNLISTED" == "task_local" ]]; then
    pass "AC-2 (systemic list): a block whose reason_code is on the systemic list holds dispatch on its own"
else
    fail "AC-2 (systemic list): expected systemic with the default list and task_local with --systemic-codes none; got '${LISTED}' and '${UNLISTED}'"
fi

reset_fixture
add_task "$BLOCKED_A" B BLOCKED no plugin_load_failed "$T0"
NO_HUMAN_COUNT="$(gate_field count)"
reset_fixture
add_task "$BLOCKED_A" B DONE yes plugin_load_failed "$T0"
NOT_BLOCKED_COUNT="$(gate_field count)"
echo "--- BLOCKED/Needs Human no count=${NO_HUMAN_COUNT}; DONE/Needs Human yes count=${NOT_BLOCKED_COUNT}"
if [[ "$NO_HUMAN_COUNT" == "0" && "$NOT_BLOCKED_COUNT" == "0" ]]; then
    pass "AC-2 (scanner): a BLOCKED task with Needs Human no and a DONE task with Needs Human yes are not human-gated blocks"
else
    fail "AC-2 (scanner): expected count 0 for a BLOCKED/Needs Human no task and for a DONE/Needs Human yes task; got '${NO_HUMAN_COUNT}' and '${NOT_BLOCKED_COUNT}' -- inspect scan_human_gated_blocks in the ${GATE_MODULE} module"
fi
echo ""

# ---------------------------------------------------------------------------
# Line-number helpers for the structural checks below.  Each prints a line
# number (or a count) on stdout, and prints nothing / 0 when nothing matches,
# so a missing construct surfaces as a FAIL line rather than a set -e abort.
# The ERE travels through the environment, not awk -v, because -v would
# rewrite its backslash escapes (\[ and \$) before awk compiled it.
# ---------------------------------------------------------------------------

# first_line_matching FILE ERE [AFTER]
#   Line number of the first line matching ERE that sits below line AFTER
#   (default 0, i.e. anywhere in FILE).
first_line_matching() {
    _MATCH_RE="$2" awk -v after="${3:-0}" \
        'NR > after && $0 ~ ENVIRON["_MATCH_RE"] { print NR; exit }' "$1"
}

# count_lines_matching FILE ERE [AFTER]
#   Number of lines matching ERE below line AFTER (default 0).
count_lines_matching() {
    _MATCH_RE="$2" awk -v after="${3:-0}" \
        'NR > after && $0 ~ ENVIRON["_MATCH_RE"] { n++ } END { print n + 0 }' "$1"
}

# ---------------------------------------------------------------------------
# AC-3: The guard lives in the shared wake driver, ahead of task dispatch
# ---------------------------------------------------------------------------
echo "==================================================================="
echo "  AC-3: Guard in the shared wake driver holds dispatch, not re-checks"
echo "==================================================================="
echo ""
echo "--- wake driver: ${WAKE_DRIVER_SH}"

if [[ ! -f "$WAKE_DRIVER_SH" ]]; then
    fail "AC-3 (driver present): wake driver not found at ${WAKE_DRIVER_SH} — restore team/scripts/lib/wake_driver.sh"
else
    pass "AC-3 (driver present): wake driver found"

    DRIVER_SYNTAX=0
    bash -n "$WAKE_DRIVER_SH" 2>/dev/null || DRIVER_SYNTAX=$?
    if [[ $DRIVER_SYNTAX -eq 0 ]]; then
        pass "AC-3 (driver syntax): bash -n passes on wake_driver.sh"
    else
        fail "AC-3 (driver syntax): bash -n FAILED on wake_driver.sh — run: bash -n ${WAKE_DRIVER_SH}"
    fi

    HEREDOC_COUNT="$(count_lines_matching "$WAKE_DRIVER_SH" 'python3[[:space:]]+-[[:space:]].*<<')"
    if [[ "$HEREDOC_COUNT" -eq 0 ]]; then
        pass "AC-3 (no inline python): wake_driver.sh carries no python3 heredoc"
    else
        fail "AC-3 (no inline python): wake_driver.sh carries ${HEREDOC_COUNT} python3 heredoc(s) — move the logic into a pgai_agent_kanban module"
    fi

    # --- Guard header, inside run_project_chain --------------------------
    GUARD_HDR='Blocked-task project guard'
    CHAIN_LINE="$(first_line_matching "$WAKE_DRIVER_SH" '^[[:space:]]*(function[[:space:]]+)?run_project_chain[[:space:]]*\(\)')"
    HDR_COUNT="$(grep -cF "$GUARD_HDR" "$WAKE_DRIVER_SH" || true)"
    HDR_LINE="$(grep -nF -m1 "$GUARD_HDR" "$WAKE_DRIVER_SH" | cut -d: -f1 || true)"
    echo "--- run_project_chain defined at line ${CHAIN_LINE:-none}; guard header count=${HDR_COUNT} at line ${HDR_LINE:-none}"
    if [[ -n "$CHAIN_LINE" && "$HDR_COUNT" -eq 1 && "${HDR_LINE:-0}" -gt "$CHAIN_LINE" ]]; then
        pass "AC-3 (header): exactly 1 guard comment header, inside run_project_chain"
    else
        fail "AC-3 (header): expected exactly 1 '${GUARD_HDR}' header below run_project_chain() — found ${HDR_COUNT} (chain line ${CHAIN_LINE:-none}, header line ${HDR_LINE:-none})"
    fi

    DECIDE_LINE="$(first_line_matching "$WAKE_DRIVER_SH" "pp_run_ops[[:space:]]+${GATE_MODULE//./\\.}[[:space:]]+decide" "${HDR_LINE:-0}")"
    echo "--- ${GATE_MODULE} decide called at line ${DECIDE_LINE:-none}"
    if [[ -n "$HDR_LINE" && -n "$DECIDE_LINE" ]]; then
        pass "AC-3 (decision): the guard calls pp_run_ops ${GATE_MODULE} decide below its header"
    else
        fail "AC-3 (decision): no 'pp_run_ops ${GATE_MODULE} decide' call below the '${GUARD_HDR}' header in ${WAKE_DRIVER_SH}"
    fi

    # --- Overwatch exemption ahead of the hold ----------------------------
    HOLD_LOG_PATTERN='BLOCKED task(s) need human attention; systemic reason_code='
    OVERWATCH_LINE="$(first_line_matching "$WAKE_DRIVER_SH" '_gate_verdict.*==[[:space:]]*"systemic".*AGENT.*==[[:space:]]*"overwatch"' "${DECIDE_LINE:-0}")"
    HELD_LOG_LINE="$(first_line_matching "$WAKE_DRIVER_SH" 'not dispatching new tasks' "${DECIDE_LINE:-0}")"
    SET_GATED_LINE="$(first_line_matching "$WAKE_DRIVER_SH" '^[[:space:]]*_dispatch_gated=true[[:space:]]*$' "${HELD_LOG_LINE:-0}")"
    HOLD_LOG_COUNT="$(grep -cF "$HOLD_LOG_PATTERN" "$WAKE_DRIVER_SH" || true)"
    echo "--- overwatch branch at line ${OVERWATCH_LINE:-none}; hold log line at line ${HELD_LOG_LINE:-none}; _dispatch_gated=true at line ${SET_GATED_LINE:-none}; systemic log lines=${HOLD_LOG_COUNT}"
    if [[ -n "$OVERWATCH_LINE" && -n "$HELD_LOG_LINE" && "$OVERWATCH_LINE" -lt "$HELD_LOG_LINE" ]]; then
        pass "AC-3 (overwatch): the overwatch branch is tested before the hold, so overwatch is never held"
    else
        fail "AC-3 (overwatch): expected an '_gate_verdict == \"systemic\" && AGENT == \"overwatch\"' branch before the 'not dispatching new tasks' log line in ${WAKE_DRIVER_SH}"
    fi
    if [[ -n "$SET_GATED_LINE" && "$HOLD_LOG_COUNT" -ge 1 ]]; then
        pass "AC-3 (log line): the hold logs the count and the systemic reason_code, then sets _dispatch_gated=true"
    else
        fail "AC-3 (log line): expected a '${HOLD_LOG_PATTERN}' log line followed by _dispatch_gated=true — found ${HOLD_LOG_COUNT} log line(s), _dispatch_gated=true at line ${SET_GATED_LINE:-none}"
    fi

    # --- Dispatch loop wrapped by the hold --------------------------------
    GATE_OPEN_LINE="$(first_line_matching "$WAKE_DRIVER_SH" '^[[:space:]]*if[[:space:]]+\[\[[[:space:]]*"\$_dispatch_gated"[[:space:]]*==[[:space:]]*"false"[[:space:]]*\]\];[[:space:]]*then[[:space:]]*$' "${SET_GATED_LINE:-0}")"
    GATE_CLOSE_MARKER='end: if [[ "$_dispatch_gated" == "false" ]]'
    GATE_CLOSE_COUNT="$(grep -cF "$GATE_CLOSE_MARKER" "$WAKE_DRIVER_SH" || true)"
    GATE_CLOSE_LINE="$(grep -nF -m1 "$GATE_CLOSE_MARKER" "$WAKE_DRIVER_SH" | cut -d: -f1 || true)"
    LOOP_LINE="$(first_line_matching "$WAKE_DRIVER_SH" '^[[:space:]]*while[[:space:]]' "${GATE_OPEN_LINE:-0}")"
    echo "--- dispatch hold: opens at line ${GATE_OPEN_LINE:-none}, dispatch loop at line ${LOOP_LINE:-none}, closing fi (count=${GATE_CLOSE_COUNT}) at line ${GATE_CLOSE_LINE:-none}"
    if [[ -n "$GATE_OPEN_LINE" && -n "$LOOP_LINE" && "$GATE_CLOSE_COUNT" -eq 1 \
          && "$LOOP_LINE" -lt "${GATE_CLOSE_LINE:-0}" ]]; then
        pass "AC-3 (dispatch hold): the dispatch loop sits inside the _dispatch_gated == \"false\" block, which closes with its end-comment fi"
    else
        fail "AC-3 (dispatch hold): expected 'if [[ \"\$_dispatch_gated\" == \"false\" ]]' below the hold, a while loop inside it, and exactly 1 '${GATE_CLOSE_MARKER}' closing fi after the loop"
    fi

    # --- Re-checks run held or not ----------------------------------------
    RECHECK_WAITING_LINE="$(first_line_matching "$WAKE_DRIVER_SH" '^[[:space:]]*recheck_waiting_tasks[[:space:]]*$' "${GATE_CLOSE_LINE:-0}")"
    RECHECK_BLOCKED_LINE="$(first_line_matching "$WAKE_DRIVER_SH" '^[[:space:]]*recheck_blocked_tasks[[:space:]]*$' "${GATE_CLOSE_LINE:-0}")"
    NEXT_GATED_CHECK_LINE="$(first_line_matching "$WAKE_DRIVER_SH" '_dispatch_gated' "${GATE_CLOSE_LINE:-0}")"
    echo "--- after the hold's closing fi: recheck_waiting_tasks at line ${RECHECK_WAITING_LINE:-none}, recheck_blocked_tasks at line ${RECHECK_BLOCKED_LINE:-none}, next _dispatch_gated test at line ${NEXT_GATED_CHECK_LINE:-none}"
    if [[ -n "$GATE_CLOSE_LINE" && -n "$RECHECK_WAITING_LINE" && -n "$RECHECK_BLOCKED_LINE" \
          && "$RECHECK_WAITING_LINE" -lt "${NEXT_GATED_CHECK_LINE:-999999}" \
          && "$RECHECK_BLOCKED_LINE" -lt "${NEXT_GATED_CHECK_LINE:-999999}" ]]; then
        pass "AC-3 (re-checks): recheck_waiting_tasks and recheck_blocked_tasks run after the hold's closing fi, unconditionally"
    else
        fail "AC-3 (re-checks): expected recheck_waiting_tasks and recheck_blocked_tasks after the '${GATE_CLOSE_MARKER}' fi and before any further _dispatch_gated test, so a held wake still re-checks"
    fi

    # --- Discovery/intake still runs while dispatch is held --------------
    INTAKE_LOG_PATTERN='dispatch gated (blocked tasks), running discovery pipeline (intake unaffected)'
    DISC_GATE_COUNT="$(count_lines_matching "$WAKE_DRIVER_SH" '_dispatch_gated.*==.*"true"' "${GATE_CLOSE_LINE:-0}")"
    INTAKE_LINE="$(grep -nF -m1 "$INTAKE_LOG_PATTERN" "$WAKE_DRIVER_SH" | cut -d: -f1 || true)"
    echo "--- after the hold: _dispatch_gated==\"true\" checks=${DISC_GATE_COUNT}  intake log line at line ${INTAKE_LINE:-none}"
    if [[ -n "$GATE_CLOSE_LINE" && "$DISC_GATE_COUNT" -ge 2 ]]; then
        pass "AC-3 (discovery gate): ${DISC_GATE_COUNT} _dispatch_gated == \"true\" checks after the hold (discovery + post-discovery)"
    else
        fail "AC-3 (discovery gate): expected >= 2 _dispatch_gated == \"true\" checks after the dispatch hold's closing fi, found ${DISC_GATE_COUNT}"
    fi
    if [[ -n "$INTAKE_LINE" && "$INTAKE_LINE" -gt "${GATE_CLOSE_LINE:-0}" ]]; then
        pass "AC-3 (intake log): wake_driver.sh logs that intake is unaffected when held"
    else
        fail "AC-3 (intake log): wake_driver.sh has no '${INTAKE_LOG_PATTERN}' log line after the dispatch hold"
    fi
fi
echo ""

# ---------------------------------------------------------------------------
# AC-4: Both provider scripts reach the shared driver
# ---------------------------------------------------------------------------
echo "==================================================================="
echo "  AC-4: Both provider scripts reach the shared wake driver"
echo "==================================================================="
echo ""

for PROVIDER_SH in "$CLAUDE_SH" "$CODEX_SH"; do
    PROVIDER_NAME="$(basename "$PROVIDER_SH")"
    if [[ ! -f "$PROVIDER_SH" ]]; then
        fail "AC-4 (${PROVIDER_NAME}): provider script not found at ${PROVIDER_SH}"
        continue
    fi

    PROVIDER_SYNTAX=0
    bash -n "$PROVIDER_SH" 2>/dev/null || PROVIDER_SYNTAX=$?
    if [[ $PROVIDER_SYNTAX -eq 0 ]]; then
        pass "AC-4 (${PROVIDER_NAME} syntax): bash -n passes"
    else
        fail "AC-4 (${PROVIDER_NAME} syntax): bash -n FAILED — run: bash -n ${PROVIDER_SH}"
    fi

    # An uncommented `source`/`.` line naming lib/wake_driver.sh.  Provider
    # scripts live in wake/, so their ../lib/wake_driver.sh is the driver
    # AC-3 checked.
    SOURCES_DRIVER="$(count_lines_matching "$PROVIDER_SH" '^[[:space:]]*(source|\.)[[:space:]]+[^#]*lib/wake_driver\.sh')"
    if [[ "$SOURCES_DRIVER" -ge 1 ]]; then
        pass "AC-4 (${PROVIDER_NAME} sources driver): sources lib/wake_driver.sh"
    else
        fail "AC-4 (${PROVIDER_NAME} sources driver): no 'source \"\${SCRIPT_DIR}/../lib/wake_driver.sh\"' line — this provider never reaches the guarded run_project_chain"
    fi

    # A provider-local run_project_chain would shadow the driver's guarded one.
    OWN_CHAIN="$(count_lines_matching "$PROVIDER_SH" '^[[:space:]]*(function[[:space:]]+run_project_chain|run_project_chain[[:space:]]*\(\))')"
    if [[ "$OWN_CHAIN" -eq 0 ]]; then
        pass "AC-4 (${PROVIDER_NAME} no shadow): does not define its own run_project_chain"
    else
        fail "AC-4 (${PROVIDER_NAME} no shadow): defines run_project_chain ${OWN_CHAIN} time(s), shadowing the shared driver's guarded one — remove it from ${PROVIDER_SH}"
    fi
done
echo ""

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo "==================================================================="
echo "  RESULTS: ${PASS_COUNT} passed, ${FAIL_COUNT} failed"
echo "==================================================================="
echo ""

if [[ "$FAIL_COUNT" -gt 0 ]]; then
    echo "${C_RED}FAILED: ${FAIL_COUNT} assertion(s) failed.${C_RESET}" >&2
    exit 1
else
    echo "${C_GREEN}ALL PASSED: ${PASS_COUNT} assertions.${C_RESET}"
    exit 0
fi
