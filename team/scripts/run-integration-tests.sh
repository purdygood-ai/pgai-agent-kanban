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
TEAM_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"

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
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh"

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

# --- Run ICD freshness gate ---
# Asserts that docs/api/icd.json is byte-identical to a fresh regeneration from
# the current codebase.  A stale artifact means the checked-in contract no longer
# matches the served /openapi.json — consumers would pin the wrong schema.
# Run 'bash team/scripts/generate-icd.sh' and commit the result to fix.
echo "Running ICD freshness gate..."
set +e
python3 scripts/lint_icd_freshness.py
ICD_FRESHNESS_EXIT=$?
set -e

if [[ $ICD_FRESHNESS_EXIT -ne 0 ]]; then
  echo "ICD freshness gate FAILED (exit code: $ICD_FRESHNESS_EXIT)" >&2
  echo "Fix: run 'bash team/scripts/generate-icd.sh' from the repository root" >&2
  echo "and commit docs/api/icd.json." >&2
  exit 1
fi

# --- Run ICD compatibility gate ---
# Asserts that docs/api/icd.json is a compatible superset of every ICD version
# listed in docs/api/baselines/SUPPORTED.  A breaking change (removed path,
# removed response field, new required request field, enum shrink) against any
# supported baseline fails immediately, enforcing the "breaking changes require
# a major ICD version and an operator-approved RC" policy.
# Wired here alongside the freshness gate, before pytest.
echo "Running ICD compatibility gate..."
set +e
python3 scripts/lint_icd_compat.py
ICD_COMPAT_EXIT=$?
set -e

if [[ $ICD_COMPAT_EXIT -ne 0 ]]; then
  echo "ICD compatibility gate FAILED (exit code: $ICD_COMPAT_EXIT)" >&2
  echo "Fix: resolve the compatibility break or retire the affected baseline" >&2
  echo "version from docs/api/baselines/SUPPORTED (operator-approved RC required)." >&2
  exit 1
fi

# --- Run CHANGELOG freshness gate ---
# Asserts that CHANGELOG.md is byte-identical to a fresh regeneration from the
# current codebase via changelog_writer.  A stale artifact means the committed
# CHANGELOG no longer reflects the actual release notes and bug ledger state.
# Regenerate using cm-release or: python3 -m pgai_agent_kanban.cm.changelog_writer
# <repo_root> <bugs_dir> and commit the result to fix.
#
# RC-mode tolerance: when the worktree HEAD is on an rc/* or ai_rc/* branch,
# PGAI_LINT_CHANGELOG_MODE=rc is set so the lint tolerates CHANGELOG staleness
# caused only by BUG-NNNN files filed after the CHANGELOG.md commit.  This
# prevents post-RC bug filings from blocking TESTER verification on in-flight
# RCs.  The variable is left unset on all other branches (including ai_main),
# keeping the gate strictly byte-exact on the main branch and at release time.
#
# PYTHONHASHSEED=0: changelog_writer uses frozenset over string section headings
# whose iteration order is hash-seed-dependent.  Pinning the seed ensures the
# gate's regeneration produces byte-identical output across independent process
# invocations, matching the CHANGELOG.md committed under the same seed.
_CHANGELOG_MODE_VAR=""
_RI_CURRENT_BRANCH="$(git -C "$REPO_ROOT" symbolic-ref --short HEAD 2>/dev/null || true)"
if [[ -z "$_RI_CURRENT_BRANCH" ]]; then
  # Detached HEAD — check if it is at a commit reachable from an rc/* branch.
  _RI_CURRENT_BRANCH="$(git -C "$REPO_ROOT" branch --format='%(refname:short)' \
    --points-at HEAD 2>/dev/null | grep -E '^(rc/|ai_rc/)' | head -1 || true)"
fi
if [[ "$_RI_CURRENT_BRANCH" =~ ^(rc/|ai_rc/) ]]; then
  _CHANGELOG_MODE_VAR="rc"
fi
unset _RI_CURRENT_BRANCH
echo "Running CHANGELOG freshness gate..."
set +e
PYTHONHASHSEED=0 PGAI_LINT_CHANGELOG_MODE="${_CHANGELOG_MODE_VAR}" \
  python3 scripts/lint_changelog_freshness.py
CHANGELOG_FRESHNESS_EXIT=$?
set -e
unset _CHANGELOG_MODE_VAR

if [[ $CHANGELOG_FRESHNESS_EXIT -ne 0 ]]; then
  echo "CHANGELOG freshness gate FAILED (exit code: $CHANGELOG_FRESHNESS_EXIT)" >&2
  echo "Fix: regenerate CHANGELOG.md via the changelog_writer and commit it." >&2
  echo "  python3 -m pgai_agent_kanban.cm.changelog_writer <repo_root> <bugs_dir>" >&2
  exit 1
fi

# --- Run lib-function-dedupe lint ---
# Asserts that no function name is defined in more than one team/scripts/lib/*.sh
# file.  A duplicated function name causes bash's last-sourced-wins semantics to
# silently shadow one implementation whenever both files are sourced — exactly the
# latent defect class documented in BUG-0031.  This check makes the constraint
# permanent and gated so future edits that reintroduce a duplicate are caught
# before they reach production.
echo "Running lib-function-dedupe lint..."
set +e
python3 scripts/lint_lib_function_dedupe.py
LIB_DEDUPE_LINT_EXIT=$?
set -e

if [[ $LIB_DEDUPE_LINT_EXIT -ne 0 ]]; then
  echo "Lib-function-dedupe lint FAILED (exit code: $LIB_DEDUPE_LINT_EXIT)" >&2
  echo "Fix: ensure each function name is defined in exactly one team/scripts/lib/*.sh" >&2
  echo "file.  If the duplication is intentional (provider-interface), add both" >&2
  echo "defining files to the allow-list via --allow-file." >&2
  exit 1
fi

# --- Run env-bootstrap lint ---
# Asserts the env-bootstrap unification contract on both runtimes:
#   - bash side: every executable entry point that references PGAI_AGENT_KANBAN_ROOT_PATH
#     sources env_bootstrap.sh or wake_common.sh before using it.
#   - python side: every Python entry point imports resolve_kanban_root from
#     pgai_agent_kanban.env rather than reading the env var directly via os.environ.
# A failure here means a newly-added script bypassed the canonical bootstrap,
# reintroducing the class of silent-failure bug the RC closes.
echo "Running env-bootstrap lint..."
set +e
python3 scripts/lint_env_bootstrap.py
ENV_BOOTSTRAP_LINT_EXIT=$?
set -e

if [[ $ENV_BOOTSTRAP_LINT_EXIT -ne 0 ]]; then
  echo "Env-bootstrap lint FAILED (exit code: $ENV_BOOTSTRAP_LINT_EXIT)" >&2
  echo "Fix: add 'source \"\$(dirname \"\${BASH_SOURCE[0]}\")/lib/env_bootstrap.sh\"'" >&2
  echo "as the first line (after the shebang/set lines) in any flagged bash entry point," >&2
  echo "or route Python entry points through resolve_kanban_root from pgai_agent_kanban.env." >&2
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

# --- Post-suite listener cleanliness assertion ---
# Assert the suite left no framework-rooted TCP listeners alive after pytest.
# Runs only on success so the exit code reflects test failures cleanly.
# Only listeners rooted in this project's own temp subtree are flagged; sibling
# projects' leaked listeners are not attributed to this runner.  When the runner
# is invoked from a path inside the framework temp tree the project subtree is
# derived automatically; otherwise the check falls back to the wider basename
# match (backward-compatible behavior for out-of-tree runs).
_PGAI_TEMP_ROOT="$(pgai_temp_dir)"
export PGAI_PROJECT_TEMP_SUBTREE=""
if [[ "${REPO_ROOT}" == "${_PGAI_TEMP_ROOT}/projects/"* ]]; then
    # REPO_ROOT is under <temp_root>/projects/<name>/worktrees/<branch>.
    # Extract the project subtree as <temp_root>/projects/<name>.
    _rel="${REPO_ROOT#"${_PGAI_TEMP_ROOT}/projects/"}"
    _proj_name="${_rel%%/*}"
    if [[ -n "${_proj_name}" ]]; then
        export PGAI_PROJECT_TEMP_SUBTREE="${_PGAI_TEMP_ROOT}/projects/${_proj_name}"
    fi
fi
unset _PGAI_TEMP_ROOT _rel _proj_name
echo "Checking for framework-rooted TCP listener leaks..."
set +e
pgai_listener_cleanliness_check
_LISTENER_EXIT=$?
set -e
if [[ $_LISTENER_EXIT -ne 0 ]]; then
  echo "Listener cleanliness check FAILED: framework-rooted listener(s) survived the suite." >&2
  exit 1
fi
echo "Listener cleanliness check PASSED"

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
