#!/usr/bin/env bash
# run-unit-tests.sh
# Run the unit test suite for the pgai-agent-kanban framework's team scripts.
# The dev-tree path is resolved with three-tier precedence:
#   1. PGAI_DEV_TREE_PATH env var (highest)
#   2. kanban.cfg [paths] dev_tree_path (middle)
#   3. self-located repo root derived from this script's own location (fallback)
# The script fails fast with a clear error only when none of the three tiers
# yields a valid dev tree directory.
#
# Usage:
#   run-unit-tests.sh [--verbose]
#
# Exit codes:
#   0  = all tests passed
#   1  = test failure
#   2  = pytest not installed
#
# Configuration:
#   PGAI_AGENT_KANBAN_ROOT_PATH  — kanban root (default: $HOME/pgai_agent_kanban)

# --- Resolve kanban root and script directory ---
TEAM_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}"
_RUT_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- Source optional config files ---
# This MUST happen before `set -euo pipefail`. User bashrc files commonly
# contain unset variable references, conditional aliases that return non-zero,
# or interactive-only checks that would trip strict mode and silently kill
# the script.
[[ -f "$TEAM_ROOT/bashrc" ]] && source "$TEAM_ROOT/bashrc"
[[ -f "$TEAM_ROOT/env" ]] && source "$TEAM_ROOT/env"
[[ -f "$HOME/.config/pgai-kanban.cfg" ]] && source "$HOME/.config/pgai-kanban.cfg"
# Source ini_parser.sh for read_ini; dev_tree.sh for resolve/require helpers.
# shellcheck source=lib/ini_parser.sh
[[ -f "${_RUT_SCRIPT_DIR}/lib/ini_parser.sh" ]] && source "${_RUT_SCRIPT_DIR}/lib/ini_parser.sh"
# shellcheck source=lib/dev_tree.sh
source "${_RUT_SCRIPT_DIR}/lib/dev_tree.sh"
if [[ -f "$TEAM_ROOT/kanban.cfg" ]]; then
    export PGAI_KANBAN_PM_MODE="${PGAI_KANBAN_PM_MODE:-$(read_ini "$TEAM_ROOT/kanban.cfg" chain pm_mode automatic)}"
fi
# Source temp.sh for resolver helpers (include guard in temp.sh prevents double-load).
# Must happen before strict mode so resolver can read config without triggering pipefail.
# shellcheck source=lib/temp.sh
[[ -f "${_RUT_SCRIPT_DIR}/lib/temp.sh" ]] && source "${_RUT_SCRIPT_DIR}/lib/temp.sh"
# Self-locate fallback: derive the dev tree from the runner's own location
# so the suite can run from a clean checkout with no configured dev_tree_path.
# Precedence: PGAI_DEV_TREE_PATH env var > kanban.cfg dev_tree_path > self-located root.
_RUT_SELF_ROOT="$(cd "${_RUT_SCRIPT_DIR}/../.." && pwd)"
unset _RUT_SCRIPT_DIR
export PGAI_DEV_TREE_PATH="${PGAI_DEV_TREE_PATH:-$(resolve_global_dev_tree)}"
# Apply self-locate fallback only when both env var and configured path are absent.
if [[ -z "${PGAI_DEV_TREE_PATH:-}" && -d "${_RUT_SELF_ROOT}" ]]; then
    export PGAI_DEV_TREE_PATH="${_RUT_SELF_ROOT}"
fi
unset _RUT_SELF_ROOT
require_dev_tree "${PGAI_DEV_TREE_PATH:-}" "$TEAM_ROOT/kanban.cfg"

# --- Now enable strict mode for our own code ---
set -euo pipefail

# --- Clean exit handling ---
cleanup_on_exit() {
  local exit_code=$?
  jobs -p 2>/dev/null | xargs -r kill 2>/dev/null || true
  wait 2>/dev/null || true
  exit $exit_code
}
trap cleanup_on_exit EXIT

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

Run the unit test suite for pgai-agent-kanban team scripts.

Options:
  --verbose, -v   Pass --verbose flag through to pytest
  --coverage      Measure Python branch and line coverage via pytest-cov.
                  Coverage options appended: --cov=pgai_agent_kanban
                  --cov=pm-agent --cov=scripts/lib --cov-branch --cov-report=term
                  --cov-report=json:<temp-root>/coverage/unit.json
                  If pytest-cov is not installed, prints a skip message to
                  stderr and runs the suite without coverage (non-blocking).
  --help, -h      Show this help

Exit codes:
  0  = all tests passed
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
# basetemp to ${PGAI_AGENT_KANBAN_TEMP_DIR}/pytest/unit.  The env var is
# exported so conftest.py's PYTEST_DEBUG_TEMPROOT guard sees it too.
# Using a unit-specific subdirectory avoids conflicts with run-integration-tests.sh
# (pytest removes and recreates its basetemp at startup, so a shared path would
# wipe the other runner's artifacts when both suites are run in sequence).
# PYTEST_ADDOPTS is appended to (not overwritten) in case the caller already
# has flags set.
export PGAI_AGENT_KANBAN_TEMP_DIR="$(pgai_temp_dir)"
# Ensure the framework temp root exists so every child process (pytest and any
# subprocess pytest spawns) that inherits PGAI_AGENT_KANBAN_TEMP_DIR can
# safely create directories under it without a race.
mkdir -p "${PGAI_AGENT_KANBAN_TEMP_DIR}"
_PYTEST_BASETEMP="${PGAI_AGENT_KANBAN_TEMP_DIR}/pytest/unit"
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
# tests/unit/    — the main unit test tree
# tests/test_pgai_agent_kanban_cm_*.py — module-level tests for extracted cm packages
#                  (these live at the tests/ top level, not under unit/, so they are
#                  listed explicitly rather than relying on glob discovery inside unit/)
PYTEST_ARGS=(
  "tests/unit/"
)
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
      "--cov-report=json:${_COV_DIR}/unit.json"
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
# Fake crontab seam for unit test harness.
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

# --- Run test anti-pattern lint ---
# Scans team/tests/ for anti-pattern 1 (pattern-scan assertion loops) and
# anti-pattern 2 (hardcoded /tmp or $HOME paths), per SOP.md
# "Test Authoring Guidelines". Runs before pytest so failures surface early.
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
echo "Running unit tests in $(pwd)/tests/ (unit/ + cm module tests)"
set +e
python3 -m pytest "${PYTEST_ARGS[@]}"
PYTEST_EXIT=$?
set -e

if [[ $PYTEST_EXIT -ne 0 ]]; then
  echo "Unit tests FAILED (exit code: $PYTEST_EXIT)" >&2
  exit 1
fi

echo "Unit tests PASSED"

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
