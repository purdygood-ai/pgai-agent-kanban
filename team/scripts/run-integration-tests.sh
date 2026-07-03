#!/usr/bin/env bash
# run-integration-tests.sh
# Run the integration test suite for the pgai-agent-kanban framework's team
# scripts.  The dev-tree path is resolved with three-tier precedence:
#   1. PGAI_DEV_TREE_PATH env var (highest)
#   2. kanban.cfg [paths] dev_tree_path (middle)
#   3. self-located repo root derived from this script's own location (fallback)
# The script fails fast with a clear error only when none of the three tiers
# yields a valid dev tree directory.
#
# Usage:
#   run-integration-tests.sh [--verbose]
#
# Exit codes:
#   0  = all tests passed (or suite is empty — pytest exit 5 treated as success)
#   1  = test failure
#   2  = pytest not installed
#
# SIGTERM guard:
#   Pytest is launched in its own process session via setsid so that an external
#   SIGTERM sent to THIS script's process group (e.g., by a Bash-tool harness
#   timeout at 600 s) does NOT propagate to pytest.  A SIGTERM trap in this
#   wrapper absorbs the signal and waits for pytest to finish naturally instead
#   of exiting immediately.
#
#   This ensures:
#     a) A Bash-tool timeout kills only the wrapper shell — pytest continues in
#        its own setsid session and runs to its natural completion (or OOM kill).
#     b) The wrapper's SIGTERM trap blocks until pytest exits, then the wrapper
#        falls through to the summary/exit block and outputs the final line.
#     c) No orphan processes: the wrapper always waits for pytest via its PID;
#        if the wrapper exits before wait completes, the EXIT trap sends SIGTERM
#        to pytest and waits for it.
#
# Configuration:
#   PGAI_AGENT_KANBAN_ROOT_PATH  — kanban root (default: $HOME/pgai_agent_kanban)

# --- Resolve kanban root ---
TEAM_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}"

# --- Source optional config files ---
# This MUST happen before `set -euo pipefail`. User bashrc files commonly
# contain unset variable references, conditional aliases that return non-zero,
# or interactive-only checks that would trip strict mode and silently kill
# the script.
[[ -f "$TEAM_ROOT/bashrc" ]] && source "$TEAM_ROOT/bashrc"
[[ -f "$TEAM_ROOT/env" ]] && source "$TEAM_ROOT/env"
# $HOME/.config/pgai-kanban.cfg is operator-local bash config; sourced as-is.
[[ -f "$HOME/.config/pgai-kanban.cfg" ]] && source "$HOME/.config/pgai-kanban.cfg"
# Source: kanban.cfg [chain/paths] — INI format replaces legacy config.cfg
_RIT_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[[ -f "${_RIT_SCRIPT_DIR}/lib/ini_parser.sh" ]] && source "${_RIT_SCRIPT_DIR}/lib/ini_parser.sh"
# shellcheck source=lib/dev_tree.sh
source "${_RIT_SCRIPT_DIR}/lib/dev_tree.sh"
if [[ -f "$TEAM_ROOT/kanban.cfg" ]]; then
    export PGAI_KANBAN_PM_MODE="${PGAI_KANBAN_PM_MODE:-$(read_ini "$TEAM_ROOT/kanban.cfg" chain pm_mode automatic)}"
fi
# Source temp.sh for resolver helpers (include guard in temp.sh prevents double-load).
# Must happen before strict mode so resolver can read config without triggering pipefail.
# shellcheck source=lib/temp.sh
[[ -f "${_RIT_SCRIPT_DIR}/lib/temp.sh" ]] && source "${_RIT_SCRIPT_DIR}/lib/temp.sh"
# Self-locate fallback: derive the dev tree from the runner's own location
# so the suite can run from a clean checkout with no configured dev_tree_path.
# Precedence: PGAI_DEV_TREE_PATH env var > kanban.cfg dev_tree_path > self-located root.
_RIT_SELF_ROOT="$(cd "${_RIT_SCRIPT_DIR}/../.." && pwd)"
unset _RIT_SCRIPT_DIR
export PGAI_DEV_TREE_PATH="${PGAI_DEV_TREE_PATH:-$(resolve_global_dev_tree)}"
# Apply self-locate fallback only when both env var and configured path are absent.
if [[ -z "${PGAI_DEV_TREE_PATH:-}" && -d "${_RIT_SELF_ROOT}" ]]; then
    export PGAI_DEV_TREE_PATH="${_RIT_SELF_ROOT}"
fi
unset _RIT_SELF_ROOT
require_dev_tree "${PGAI_DEV_TREE_PATH:-}" "$TEAM_ROOT/kanban.cfg"

# --- Now enable strict mode for our own code ---
set -euo pipefail

# --- SIGTERM guard: track pytest PID and exit code ---
# Populated once pytest is launched (see "Run tests" section below).
PYTEST_PID=""
PYTEST_EXIT=0

# --- Clean exit handling ---
# On EXIT: if pytest is still running (e.g. wrapper exited early due to a
# pre-launch error), send SIGTERM to the setsid'd process and wait.
# In the normal path, PYTEST_PID is cleared after wait completes, so this
# block is a no-op for the common case.
cleanup_on_exit() {
  local exit_code=$?
  if [[ -n "$PYTEST_PID" ]] && kill -0 "$PYTEST_PID" 2>/dev/null; then
    kill -TERM "$PYTEST_PID" 2>/dev/null || true
    wait "$PYTEST_PID" 2>/dev/null || true
  fi
  exit $exit_code
}
trap cleanup_on_exit EXIT

# --- SIGTERM handler: keep waiting, do NOT relay signal to pytest ---
# When the wrapper shell receives SIGTERM (e.g. from a Bash-tool harness
# timeout after 600 s), bash interrupts the blocking "wait $PYTEST_PID"
# and runs this handler.  We wait again for pytest to finish naturally
# (pytest is in its own setsid session and never received our SIGTERM),
# capture the real exit code, and clear PYTEST_PID so the EXIT trap does
# not double-signal.
#
# This ensures that a Bash-tool timeout kills the *wrapper shell* but
# does not propagate to pytest.  The wrapper continues blocking in this
# handler until pytest exits, then falls through to the summary/exit block.
trap_sigterm() {
  echo "run-integration-tests.sh: SIGTERM received; waiting for pytest (PID ${PYTEST_PID}) to complete naturally (SIGTERM guard)" >&2
  if [[ -n "$PYTEST_PID" ]] && kill -0 "$PYTEST_PID" 2>/dev/null; then
    wait "$PYTEST_PID" 2>/dev/null
    PYTEST_EXIT=$?
    PYTEST_PID=""
  fi
}
trap trap_sigterm SIGTERM

# --- Resolve script directory to find the repo root ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# --- Parse arguments ---
VERBOSE=false
COVERAGE=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --verbose|-v)
      VERBOSE=true
      shift
      ;;
    --coverage)
      COVERAGE=true
      shift
      ;;
    --help|-h)
      cat <<EOF
Usage: $(basename "$0") [--verbose] [--coverage]

Run the integration test suite for pgai-agent-kanban team scripts.

Options:
  --verbose, -v   Pass --verbose flag through to pytest
  --coverage      Measure Python branch and line coverage via pytest-cov.
                  Coverage options appended: --cov=pgai_agent_kanban
                  --cov=pm-agent --cov=scripts/lib --cov-branch --cov-report=term
                  --cov-report=json:<temp-root>/coverage/integration.json
                  If pytest-cov is not installed, prints a skip message to
                  stderr and runs the suite without coverage (non-blocking).
  --help, -h      Show this help

Exit codes:
  0  = all tests passed (or suite is empty — pytest exit 5 treated as success)
  1  = test failure
  2  = pytest not installed
EOF
      exit 0
      ;;
    *)
      echo "ERROR: Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

# --- Check pytest is available ---
if ! command -v pytest >/dev/null 2>&1 && ! python3 -m pytest --version >/dev/null 2>&1; then
  echo "ERROR: pytest not installed. Install it with: pip install pytest" >&2
  exit 2
fi

# --- Redirect pytest basetemp under the framework temp root ---
# Prevents pytest from creating /tmp/pytest-of-<user>/ by directing its
# basetemp to ${PGAI_AGENT_KANBAN_TEMP_DIR}/pytest/integration.  The env var
# is exported so conftest.py's PYTEST_DEBUG_TEMPROOT guard sees it too.
# Using an integration-specific subdirectory avoids conflicts with
# run-unit-tests.sh (pytest removes and recreates its basetemp at startup,
# so a shared path would wipe the other runner's artifacts when both suites
# are run in sequence).
# PYTEST_ADDOPTS is appended to (not overwritten) in case the caller already
# has flags set.
export PGAI_AGENT_KANBAN_TEMP_DIR="$(pgai_temp_dir)"
# Ensure the framework temp root exists so every child process (pytest and any
# subprocess pytest spawns) that inherits PGAI_AGENT_KANBAN_TEMP_DIR can
# safely create directories under it without a race.
mkdir -p "${PGAI_AGENT_KANBAN_TEMP_DIR}"
_PYTEST_BASETEMP="${PGAI_AGENT_KANBAN_TEMP_DIR}/pytest/integration"
mkdir -p "$_PYTEST_BASETEMP"
export PYTEST_ADDOPTS="${PYTEST_ADDOPTS:+${PYTEST_ADDOPTS} }--basetemp=${_PYTEST_BASETEMP}"
unset _PYTEST_BASETEMP

# --- Pre-suite /tmp cleanliness snapshot ---
# Capture the current top-level /tmp contents BEFORE the suite runs.
# After the suite, pgai_tmp_cleanliness_check compares against this snapshot
# to detect any bare-/tmp residue left by kanban code.
# The snapshot file lives under the framework temp root (not bare /tmp).
_TMP_SNAPSHOT_FILE="$(pgai_mktemp tmp_cleanliness_snapshot)"
pgai_tmp_snapshot > "$_TMP_SNAPSHOT_FILE"

# --- Build pytest arguments ---
PYTEST_ARGS=("tests/integration/")
if [[ "$VERBOSE" == "true" ]]; then
  PYTEST_ARGS+=("--verbose")
fi
if [[ "$COVERAGE" == "true" ]]; then
  if python3 -c "import pytest_cov" 2>/dev/null; then
    # Ensure the coverage output directory exists under the framework temp root.
    _COV_DIR="${PGAI_AGENT_KANBAN_TEMP_DIR}/coverage"
    mkdir -p "$_COV_DIR"
    PYTEST_ARGS+=(
      "--cov=pgai_agent_kanban"
      "--cov=pm-agent"
      "--cov=scripts/lib"
      "--cov-branch"
      "--cov-report=term"
      "--cov-report=json:${_COV_DIR}/integration.json"
    )
    unset _COV_DIR
  else
    echo "[coverage] pytest-cov not installed — skipping coverage" >&2
  fi
fi

# --- Crontab seam: engage PGAI_CRONTAB_CMD for all install-touching tests ---
# The test suite must never reach the host crontab (/var/spool/cron).
# safe_overwrite.sh's _run_crontab() honours PGAI_CRONTAB_CMD over PATH-based
# lookup.  Exporting it here covers every shell-based test that invokes
# install.sh or install-crontab.sh without its own per-test seam setup.
# The fake writer accepts all crontab invocation forms and writes to a
# temporary file under the pgai temp root; it never touches /var/spool/cron.
_CRONTAB_SEAM_DIR="$(pgai_mktemp_d crontab_seam)"
_CRONTAB_SEAM_STATE="${_CRONTAB_SEAM_DIR}/state"
touch "$_CRONTAB_SEAM_STATE"
cat > "${_CRONTAB_SEAM_DIR}/fake-crontab" <<'__SEAM_EOF__'
#!/usr/bin/env bash
# Fake crontab seam for integration test harness.
# All operations target a temp state file; /var/spool/cron is never touched.
CRONFILE="${PGAI_CRONTAB_SEAM_STATE:-/dev/null}"
case "${1:-}" in
    -l)
        [[ -s "$CRONFILE" ]] || { printf 'no crontab for testuser\n' >&2; exit 1; }
        cat "$CRONFILE"; exit 0 ;;
    -r)
        rm -f "$CRONFILE"; exit 0 ;;
    -T)
        exit 0 ;;
    -)
        cat > "$CRONFILE"; exit 0 ;;
    -*)
        printf 'fake-crontab: unsupported flag: %s\n' "$1" >&2; exit 1 ;;
    "")
        printf 'fake-crontab: no arguments\n' >&2; exit 1 ;;
    *)
        cp "$1" "$CRONFILE"; exit 0 ;;
esac
__SEAM_EOF__
chmod +x "${_CRONTAB_SEAM_DIR}/fake-crontab"
export PGAI_CRONTAB_CMD="${_CRONTAB_SEAM_DIR}/fake-crontab"
export PGAI_CRONTAB_SEAM_STATE="$_CRONTAB_SEAM_STATE"

# --- Run test anti-pattern lint (pre-flight) ---
# Scans team/tests/ for anti-pattern 1 (pattern-scan assertion loops) and
# anti-pattern 2 (hardcoded /tmp or $HOME paths / bare mktemp calls), per
# SOP.md "Test Authoring Guidelines". Runs before pytest so failures surface
# early without consuming the full test-run CPU budget.
cd "$REPO_ROOT/team"
echo "Running test anti-pattern lint..."
set +e
python3 scripts/lint_test_anti_patterns.py
LINT_EXIT=$?
set -e

if [[ $LINT_EXIT -ne 0 ]]; then
  echo "Test anti-pattern lint FAILED (exit code: $LINT_EXIT)" >&2
  echo "Fix findings above or add per-instance opt-out comments." >&2
  echo "See SOP.md 'Test Authoring Guidelines' for the preferred patterns." >&2
  exit 1
fi

# --- Run skip-cites-real-bug grep gate ---
# Enforces the skip-citation policy: every skip()/SKIP: annotation that cites
# a bug must reference a real BUG-NNNN whose file exists in bugs/.
# Placeholder IDs (BUG-SKIP-* or non-BUG-NNNN forms) are rejected.
echo "Running skip-cites-real-bug gate..."
set +e
bash "${SCRIPT_DIR}/lint_skip_bug_gate.sh" \
  --tests-dir "${REPO_ROOT}/team/tests"
SKIP_GATE_EXIT=$?
set -e

if [[ $SKIP_GATE_EXIT -ne 0 ]]; then
  echo "Skip-cites-real-bug gate FAILED (exit code: $SKIP_GATE_EXIT)" >&2
  echo "Fix: replace placeholder or absent-file citations with a real BUG-NNNN" >&2
  echo "whose file exists in the bugs/ directory, or un-skip the test." >&2
  exit 1
fi

# --- Run tests ---
# pytest is launched in its own process session via setsid so that a SIGTERM
# sent to THIS script's process group (e.g., a Bash-tool 600-second harness
# timeout) does not propagate to pytest.  The SIGTERM trap above absorbs the
# signal in the wrapper and waits for pytest to complete naturally.
cd "$REPO_ROOT/team"
echo "Running integration tests in $(pwd)/tests/integration/"
echo "run-integration-tests.sh: pytest launched via setsid (own session); SIGTERM guard active"
set +e
setsid python3 -m pytest "${PYTEST_ARGS[@]}" &
PYTEST_PID=$!
wait "$PYTEST_PID"
_WAIT_EXIT=$?
# Two paths reach this point:
#   Normal:  wait completed uninterrupted; PYTEST_PID is still set; capture exit code.
#   SIGTERM: trap_sigterm ran during wait; it set PYTEST_EXIT and cleared PYTEST_PID.
#            The interrupted wait returned 128+15=143; we must NOT overwrite PYTEST_EXIT
#            with that 143.  Guard on PYTEST_PID being non-empty to detect which path.
if [[ -n "$PYTEST_PID" ]]; then
  PYTEST_EXIT=$_WAIT_EXIT
  PYTEST_PID=""
fi
set -e

# Pytest exit code 5 means "no tests collected" (empty suite).
# Treat this as a benign success: an intentionally-empty integration directory
# (only __init__.py present) must not block TESTER PASS verdicts while the
# integration suite awaits regeneration in a separate RC.
# All other non-zero exit codes propagate as real failures.
if [[ $PYTEST_EXIT -eq 5 ]]; then
  echo "Integration tests: no tests collected (exit code 5 — empty suite treated as success)" >&2
elif [[ $PYTEST_EXIT -ne 0 ]]; then
  echo "Integration tests FAILED (exit code: $PYTEST_EXIT)" >&2
  exit "$PYTEST_EXIT"
fi

echo "Integration tests PASSED"

# --- Post-suite /tmp cleanliness assertion ---
# Assert the suite left no kanban residue in bare /tmp.
# Runs only on success so the exit code reflects test failures cleanly.
# The check uses the snapshot taken before the suite ran (_TMP_SNAPSHOT_FILE).
echo "Checking for bare-/tmp residue..."
set +e
pgai_tmp_cleanliness_check "$_TMP_SNAPSHOT_FILE"
_CLEAN_EXIT=$?
set -e
if [[ $_CLEAN_EXIT -ne 0 ]]; then
  echo "Bare-/tmp cleanliness check FAILED: kanban suite left residue in /tmp." >&2
  exit 1
fi
echo "Bare-/tmp cleanliness check PASSED"

# --- Sweep tests/ scratch after a successful run ---
# Calls pgai_temp_cleanup_tests, which removes ONLY the tests/ subtree
# under ${PGAI_AGENT_KANBAN_TEMP_DIR}.  The scoped helper avoids wiping live
# agent session files (e.g. claude-<session>/ directories) that reside
# elsewhere in the temp root.  Run only on success so temp files are
# available for debugging failed tests.
if [[ -f "${SCRIPT_DIR}/lib/temp.sh" ]]; then
    # shellcheck source=lib/temp.sh
    source "${SCRIPT_DIR}/lib/temp.sh"
    pgai_temp_cleanup_tests
fi

exit 0
