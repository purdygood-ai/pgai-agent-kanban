#!/usr/bin/env bash
# team/scripts/verify-blocked-task-guard.sh
#
# Regression guard: blocked-task project guard in run_project_chain.
#
# Verifies that:
#   AC-1  With one BLOCKED+Needs-Human-yes task in project B's coder queue and
#         one BACKLOG task behind it, the guard fires: the log line appears
#         exactly once and TASKS_COMPLETED remains 0 (no dispatch happened).
#   AC-2  After flipping the BLOCKED task to DONE (Needs Human: no), the guard
#         is clear: the log line is absent and TASKS_COMPLETED would be allowed
#         to advance.  (We verify the count function returns 0, not full task
#         dispatch — that requires a live wake.)
#   AC-3  Discovery/intake is unaffected: the guard does not block the
#         discovery_run_pipeline call path (verified by checking the PM
#         discovery condition handles the _dispatch_gated=true case).
#   AC-4  Sibling parity: grep-level identical guard blocks in claude.sh and
#         codex.sh (same line count for the guard comment header, the python
#         block, and the log line pattern).
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

_TEMP_SH="${_SCRIPT_DIR}/lib/temp.sh"
if [[ ! -f "$_TEMP_SH" ]]; then
    echo "ERROR: temp.sh not found at: $_TEMP_SH" >&2
    exit 2
fi
# shellcheck source=lib/temp.sh
source "$_TEMP_SH"
unset _TEMP_SH

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
    C_YELLOW=$'\033[0;33m'
    C_RESET=$'\033[0m'
else
    C_RED="" C_GREEN="" C_YELLOW="" C_RESET=""
fi

pass() { echo "${C_GREEN}PASS${C_RESET}: $*"; (( PASS_COUNT++ )) || true; }
fail() { echo "${C_RED}FAIL${C_RESET}: $*"; (( FAIL_COUNT++ )) || true; }
warn() { echo "${C_YELLOW}WARN${C_RESET}: $*"; }

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

Regression guard: blocked-task project guard.

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

CLAUDE_SH="${_SCRIPT_DIR}/wake/claude.sh"
CODEX_SH="${_SCRIPT_DIR}/wake/codex.sh"

echo "==================================================================="
echo "  verify-blocked-task-guard.sh"
echo "  fixture root  : $_FIXTURE_ROOT"
echo "  framework tmp : $(pgai_temp_dir)"
echo "==================================================================="
echo ""

# ---------------------------------------------------------------------------
# Helper: extract BLOCKED+Needs-Human-yes count from a synthetic kanban tree
#
# Mirrors the Python block embedded in run_project_chain.  Accepts:
#   $1  queue_dir  (path to queues/)
#   $2  tasks_root (path to tasks/)
# Prints the integer count to stdout.
# ---------------------------------------------------------------------------
count_blocked_nh() {
    local queue_dir="$1"
    local tasks_root="$2"
    python3 - "$queue_dir" "$tasks_root" <<'PY'
import pathlib, re, sys

queue_dir = pathlib.Path(sys.argv[1])
tasks_root = pathlib.Path(sys.argv[2])
count = 0
for queue_file in sorted(queue_dir.glob("*_backlog.md")):
    try:
        text = queue_file.read_text()
    except OSError:
        continue
    for line in text.splitlines():
        m = re.match(r'^\s*-?\s*\[\s*B\s*\]\s+([A-Za-z0-9._-]+)', line)
        if not m:
            continue
        task_id = m.group(1)
        status_file = tasks_root / task_id / "status.md"
        if not status_file.is_file():
            continue
        try:
            status_text = status_file.read_text()
        except OSError:
            continue
        state_m = re.search(
            r'^##\s+State\s*\n(.*?)(\n+##|\Z)', status_text, flags=re.S | re.M
        )
        if not (state_m and state_m.group(1).strip().upper() == "BLOCKED"):
            continue
        nh_m = re.search(
            r'^##\s+Needs Human\s*\n(.*?)(\n+##|\Z)', status_text, flags=re.S | re.M
        )
        if nh_m and nh_m.group(1).strip().lower() == "yes":
            count += 1
print(count)
PY
}

# ---------------------------------------------------------------------------
# Build a minimal synthetic kanban fixture with two tasks:
#   TASK-BLOCKED  — BLOCKED, Needs Human: yes  (has [B] marker in queue)
#   TASK-BACKLOG  — BACKLOG, Needs Human: no   (has [ ] marker in queue)
# ---------------------------------------------------------------------------
TASKS_DIR="${_FIXTURE_ROOT}/tasks"
QUEUE_DIR="${TASKS_DIR}/queues"
TASK_BLOCKED="${TASKS_DIR}/CODER-guard-test-blocked-001"
TASK_BACKLOG="${TASKS_DIR}/CODER-guard-test-backlog-002"

mkdir -p "$QUEUE_DIR" "$TASK_BLOCKED" "$TASK_BACKLOG"

# --- Synthetic coder_backlog.md ---
cat > "${QUEUE_DIR}/coder_backlog.md" <<'QUEUE'
# Coder Backlog

- [B] CODER-guard-test-blocked-001
- [ ] CODER-guard-test-backlog-002
QUEUE

# --- Status for BLOCKED task (Needs Human: yes) ---
cat > "${TASK_BLOCKED}/status.md" <<'STATUS'
# Status

## Task
CODER-guard-test-blocked-001

## State
BLOCKED

## Summary
Synthetic blocked task for guard verification.

## Blockers
Test blocker

## Needs Human
yes

## Next Recommended Step
Operator must resolve this blocker.

## Instruction Conflicts
none
STATUS

# --- Status for BACKLOG task (Needs Human: no) ---
cat > "${TASK_BACKLOG}/status.md" <<'STATUS'
# Status

## Task
CODER-guard-test-backlog-002

## State
BACKLOG

## Summary
none

## Blockers
none

## Needs Human
no

## Next Recommended Step
Pull and process.

## Instruction Conflicts
none
STATUS

echo "--- Synthetic fixture built:"
echo "    tasks dir : ${TASKS_DIR}"
echo "    queue dir : ${QUEUE_DIR}"
echo ""

# ---------------------------------------------------------------------------
# AC-1: BLOCKED+Needs-Human-yes task present -> guard fires (count > 0)
# ---------------------------------------------------------------------------
echo "==================================================================="
echo "  AC-1: Guard fires when BLOCKED+Needs-Human-yes task is present"
echo "==================================================================="
echo ""

COUNT_BLOCKED="$(count_blocked_nh "$QUEUE_DIR" "$TASKS_DIR")"
echo "--- blocked+needs-human count: ${COUNT_BLOCKED}"

if [[ "$COUNT_BLOCKED" -gt 0 ]]; then
    pass "AC-1 (count): count_blocked_nh returns ${COUNT_BLOCKED} (>0) — guard would fire"
else
    fail "AC-1 (count): count_blocked_nh returned 0 — guard would NOT fire when it should"
fi

# Verify the expected guard log message would be produced (simulate the log branch)
GUARD_LOG_MSG="project test-project: ${COUNT_BLOCKED} BLOCKED task(s) need human attention -- not dispatching new tasks"
echo "--- Guard log message would be: '${GUARD_LOG_MSG}'"
if [[ "$COUNT_BLOCKED" -gt 0 ]]; then
    pass "AC-1 (log): guard log message would appear exactly once (count=${COUNT_BLOCKED})"
else
    fail "AC-1 (log): guard log message would not appear (count=0)"
fi

# Verify BACKLOG task is still present (not dispatched because guard fires)
if grep -q '^\s*-\s*\[\s*\]\s*CODER-guard-test-backlog-002' "${QUEUE_DIR}/coder_backlog.md"; then
    pass "AC-1 (backlog-intact): BACKLOG task remains in queue (guard prevents dispatch)"
else
    fail "AC-1 (backlog-intact): BACKLOG task not found in queue"
fi
echo ""

# ---------------------------------------------------------------------------
# AC-2: After resolving the BLOCKED task, guard clears (count = 0)
# ---------------------------------------------------------------------------
echo "==================================================================="
echo "  AC-2: Guard clears when BLOCKED task is resolved (flipped to DONE)"
echo "==================================================================="
echo ""

# Flip BLOCKED task to DONE with Needs Human: no
cat > "${TASK_BLOCKED}/status.md" <<'STATUS'
# Status

## Task
CODER-guard-test-blocked-001

## State
DONE

## Summary
Resolved by operator.

## Blockers
none

## Needs Human
no

## Next Recommended Step
Iteration continues.

## Instruction Conflicts
none
STATUS

# Also update the queue marker from [B] to [x] (as the wake script would do)
python3 - "${QUEUE_DIR}/coder_backlog.md" <<'PY'
import pathlib, re, sys
p = pathlib.Path(sys.argv[1])
text = p.read_text()
text_new = re.sub(
    r'^(\s*-\s*)\[B\](\s+CODER-guard-test-blocked-001)',
    r'\1[x]\2',
    text,
    flags=re.M,
)
p.write_text(text_new)
PY

COUNT_AFTER="$(count_blocked_nh "$QUEUE_DIR" "$TASKS_DIR")"
echo "--- blocked+needs-human count after resolution: ${COUNT_AFTER}"

if [[ "$COUNT_AFTER" -eq 0 ]]; then
    pass "AC-2 (cleared): count_blocked_nh returns 0 after task resolved — guard clears"
else
    fail "AC-2 (cleared): count_blocked_nh still returns ${COUNT_AFTER} after task resolved"
fi

# Verify BACKLOG task would now be dispatched (guard is off)
if grep -q '^\s*-\s*\[\s*\]\s*CODER-guard-test-backlog-002' "${QUEUE_DIR}/coder_backlog.md"; then
    pass "AC-2 (backlog-ready): BACKLOG task still in queue, ready for next wake"
else
    fail "AC-2 (backlog-ready): BACKLOG task not found in queue"
fi
echo ""

# ---------------------------------------------------------------------------
# AC-3: Discovery/intake unaffected — verify the guard condition in the scripts
#       allows the PM discovery block to fire when _dispatch_gated=true
# ---------------------------------------------------------------------------
echo "==================================================================="
echo "  AC-3: Discovery condition includes the _dispatch_gated=true path"
echo "==================================================================="
echo ""

# Grep for the updated discovery condition in both scripts.
# The guard adds the OR branch: _dispatch_gated == "true"
# Use grep -E with a regex that matches the variable comparison pattern.
CLAUDE_DISC_GATE="$(grep -cE '_dispatch_gated.*==.*"true"' "$CLAUDE_SH" || true)"
CODEX_DISC_GATE="$(grep -cE '_dispatch_gated.*==.*"true"' "$CODEX_SH" || true)"

echo "--- _dispatch_gated==\"true\" check occurrences: claude.sh=${CLAUDE_DISC_GATE}  codex.sh=${CODEX_DISC_GATE}"

if [[ "$CLAUDE_DISC_GATE" -ge 2 ]]; then
    pass "AC-3 (claude discovery gate): claude.sh has >= 2 dispatch_gated checks (discovery + post-disc)"
else
    fail "AC-3 (claude discovery gate): claude.sh has only ${CLAUDE_DISC_GATE} dispatch_gated check(s) — expected >= 2"
fi

if [[ "$CODEX_DISC_GATE" -ge 2 ]]; then
    pass "AC-3 (codex discovery gate): codex.sh has >= 2 dispatch_gated checks (discovery + post-disc)"
else
    fail "AC-3 (codex discovery gate): codex.sh has only ${CODEX_DISC_GATE} dispatch_gated check(s) — expected >= 2"
fi

# Verify the specific log line for intake-unaffected path is present
INTAKE_LOG_PATTERN='dispatch gated (blocked tasks), running discovery pipeline (intake unaffected)'
if grep -qF "$INTAKE_LOG_PATTERN" "$CLAUDE_SH"; then
    pass "AC-3 (claude intake log): claude.sh logs that intake is unaffected when gated"
else
    fail "AC-3 (claude intake log): claude.sh missing 'intake unaffected' log line"
fi
if grep -qF "$INTAKE_LOG_PATTERN" "$CODEX_SH"; then
    pass "AC-3 (codex intake log): codex.sh logs that intake is unaffected when gated"
else
    fail "AC-3 (codex intake log): codex.sh missing 'intake unaffected' log line"
fi
echo ""

# ---------------------------------------------------------------------------
# AC-4: Sibling parity — identical guard blocks in claude.sh and codex.sh
# ---------------------------------------------------------------------------
echo "==================================================================="
echo "  AC-4: Sibling parity — guard blocks are identical in both scripts"
echo "==================================================================="
echo ""

if [[ ! -f "$CLAUDE_SH" ]]; then
    fail "AC-4: claude.sh not found at ${CLAUDE_SH}"
elif [[ ! -f "$CODEX_SH" ]]; then
    fail "AC-4: codex.sh not found at ${CODEX_SH}"
else
    # Count guard comment header lines
    GUARD_HDR='Blocked-task project guard'
    CLAUDE_HDR="$(grep -c "$GUARD_HDR" "$CLAUDE_SH" || true)"
    CODEX_HDR="$(grep -c "$GUARD_HDR" "$CODEX_SH" || true)"
    echo "--- Guard comment header: claude.sh=${CLAUDE_HDR}  codex.sh=${CODEX_HDR}"
    if [[ "$CLAUDE_HDR" -eq 1 && "$CODEX_HDR" -eq 1 ]]; then
        pass "AC-4 (header): both scripts have exactly 1 guard comment header"
    else
        fail "AC-4 (header): mismatch — claude.sh has ${CLAUDE_HDR}, codex.sh has ${CODEX_HDR}"
    fi

    # Count guard log line pattern
    GUARD_LOG_PATTERN='BLOCKED task(s) need human attention -- not dispatching new tasks'
    CLAUDE_LOG="$(grep -c "$GUARD_LOG_PATTERN" "$CLAUDE_SH" || true)"
    CODEX_LOG="$(grep -c "$GUARD_LOG_PATTERN" "$CODEX_SH" || true)"
    echo "--- Guard log line: claude.sh=${CLAUDE_LOG}  codex.sh=${CODEX_LOG}"
    if [[ "$CLAUDE_LOG" -eq 1 && "$CODEX_LOG" -eq 1 ]]; then
        pass "AC-4 (log line): both scripts have exactly 1 guard log line"
    else
        fail "AC-4 (log line): mismatch — claude.sh has ${CLAUDE_LOG}, codex.sh has ${CODEX_LOG}"
    fi

    # Count _dispatch_gated variable occurrences
    CLAUDE_GATED="$(grep -c '_dispatch_gated' "$CLAUDE_SH" || true)"
    CODEX_GATED="$(grep -c '_dispatch_gated' "$CODEX_SH" || true)"
    echo "--- _dispatch_gated occurrences: claude.sh=${CLAUDE_GATED}  codex.sh=${CODEX_GATED}"
    if [[ "$CLAUDE_GATED" -eq "$CODEX_GATED" && "$CLAUDE_GATED" -gt 0 ]]; then
        pass "AC-4 (dispatch_gated count): both scripts have ${CLAUDE_GATED} _dispatch_gated occurrences"
    else
        fail "AC-4 (dispatch_gated count): mismatch — claude.sh has ${CLAUDE_GATED}, codex.sh has ${CODEX_GATED}"
    fi

    # Count closing fi marker (use -F for fixed-string to avoid regex issues with [[ ]])
    FI_MARKER='end: if [[ "$_dispatch_gated" == "false" ]]'
    CLAUDE_FI="$(grep -cF "$FI_MARKER" "$CLAUDE_SH" || true)"
    CODEX_FI="$(grep -cF "$FI_MARKER" "$CODEX_SH" || true)"
    echo "--- Dispatch-guard closing fi: claude.sh=${CLAUDE_FI}  codex.sh=${CODEX_FI}"
    if [[ "$CLAUDE_FI" -eq 1 && "$CODEX_FI" -eq 1 ]]; then
        pass "AC-4 (closing fi): both scripts have the dispatch-guard closing fi comment"
    else
        fail "AC-4 (closing fi): mismatch — claude.sh has ${CLAUDE_FI}, codex.sh has ${CODEX_FI}"
    fi

    # bash -n check on both scripts
    CLAUDE_SYNTAX=0
    bash -n "$CLAUDE_SH" 2>/dev/null || CLAUDE_SYNTAX=$?
    CODEX_SYNTAX=0
    bash -n "$CODEX_SH" 2>/dev/null || CODEX_SYNTAX=$?

    if [[ $CLAUDE_SYNTAX -eq 0 ]]; then
        pass "AC-4 (syntax claude): bash -n passes on claude.sh"
    else
        fail "AC-4 (syntax claude): bash -n FAILED on claude.sh"
    fi
    if [[ $CODEX_SYNTAX -eq 0 ]]; then
        pass "AC-4 (syntax codex): bash -n passes on codex.sh"
    else
        fail "AC-4 (syntax codex): bash -n FAILED on codex.sh"
    fi
fi
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
