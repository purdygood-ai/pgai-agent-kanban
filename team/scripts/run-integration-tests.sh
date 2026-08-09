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

# --- Bootstrap: self-locate → source shell-env → fail loud ---
# Must happen before the first use of PGAI_AGENT_KANBAN_ROOT_PATH so the
# script runs from a fresh shell without manual pre-sourcing.  Explicit
# operator exports win via env_bootstrap.sh's idempotency guard.
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh" || exit 1

# --- Resolve kanban root ---
# PGAI_AGENT_KANBAN_ROOT_PATH is now set by env_bootstrap.sh or the operator.
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
# Source error_contract.sh (dual-channel error contract emitter) and
# gated_runner_lint.sh (breakage-class/quality-class pre-flight split).
# shellcheck source=lib/error_contract.sh
[[ -f "${_RIT_SCRIPT_DIR}/lib/error_contract.sh" ]] && source "${_RIT_SCRIPT_DIR}/lib/error_contract.sh"
# shellcheck source=lib/gated_runner_lint.sh
[[ -f "${_RIT_SCRIPT_DIR}/lib/gated_runner_lint.sh" ]] && source "${_RIT_SCRIPT_DIR}/lib/gated_runner_lint.sh"
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

# --- Resolve this project's temp subtree ---
# Scopes both the pre-suite listener snapshot and the post-suite listener
# cleanliness check (see below) to this project instead of the wide framework
# temp root, so a sibling project's leaked listener is never attributed here.
# Resolved once, up front, so the pre-suite snapshot and the post-suite check
# agree on the same scope.  Deliberately kept as a plain (unexported) shell
# variable here — NOT exported into the environment until after pytest exits
# (see the post-suite section below) — so pytest and any fixture it spawns do
# not inherit a narrowed scope; tests that invoke the listener helpers with no
# explicit subtree continue to see the same wide-scope fallback they always
# have.
_PGAI_TEMP_ROOT="$(pgai_temp_dir)"
_RIT_PROJECT_TEMP_SUBTREE=""
if [[ "${REPO_ROOT}" == "${_PGAI_TEMP_ROOT}/projects/"* ]]; then
    # REPO_ROOT is under <temp_root>/projects/<name>/worktrees/<branch>.
    # Extract the project subtree as <temp_root>/projects/<name>.
    _rel="${REPO_ROOT#"${_PGAI_TEMP_ROOT}/projects/"}"
    _proj_name="${_rel%%/*}"
    if [[ -n "${_proj_name}" ]]; then
        _RIT_PROJECT_TEMP_SUBTREE="${_PGAI_TEMP_ROOT}/projects/${_proj_name}"
    fi
fi
unset _PGAI_TEMP_ROOT _rel _proj_name

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
      # Partial remedy (docs/coding-standards.md "Gates prescribe, not just
      # diagnose"; classified in docs/audits/v1.27.2-gate-remedy-census.md,
      # row 32): the offending argument is named deterministically, but which
      # valid flag the operator meant to pass is user intent this runner
      # cannot resolve — --help names every accepted flag as the next step.
      echo "FAIL: unrecognized argument '$1'." >&2
      echo "" >&2
      echo "Cause(s):" >&2
      echo "  - '$1' does not match any flag this runner accepts." >&2
      echo "" >&2
      echo "To fix:" >&2
      echo "  1. bash \"${BASH_SOURCE[0]}\" --help          # list every accepted flag" >&2
      echo "  2. Re-run with the corrected flag, e.g.: bash \"${BASH_SOURCE[0]}\" --verbose" >&2
      echo "" >&2
      echo "Rule: this runner accepts only the flags listed in its --help output; an" >&2
      echo "unrecognized argument is rejected rather than silently ignored." >&2
      exit 1
      ;;
  esac
done

# --- Check pytest is available ---
# Full remedy (docs/coding-standards.md "Gates prescribe, not just diagnose";
# classified in docs/audits/v1.27.2-gate-remedy-census.md, row 31): pytest's
# absence has one universal, verbatim-runnable fix.
if ! command -v pytest >/dev/null 2>&1 && ! python3 -m pytest --version >/dev/null 2>&1; then
  echo "FAIL: pytest is not installed in this Python environment." >&2
  echo "" >&2
  echo "Cause(s):" >&2
  echo "  - no 'pytest' executable found on PATH" >&2
  echo "  - 'python3 -m pytest --version' also failed (pytest not importable)" >&2
  echo "" >&2
  echo "To fix:" >&2
  echo "  1. pip install pytest" >&2
  echo "  2. bash \"${BASH_SOURCE[0]}\"" >&2
  echo "" >&2
  echo "Rule: this runner requires pytest as a hard dependency; it cannot execute" >&2
  echo "the test suite without it." >&2
  exit 2
fi

# --- Run breakage-class gate (fatal; the only fatal pre-flight check) ---
# Syntax errors, compile failures, and unresolved first-party imports mean
# running the suite is meaningless -- this is the one pre-flight check that
# still blocks before pytest.  Every other pre-flight check below is
# quality-class: non-fatal, findings filed, runner continues to pytest.
# See team/scripts/lib/gated_runner_lint.sh for the split rationale and the
# findings-file schema quality-class checks write into.
cd "$REPO_ROOT/team"
echo "Running breakage-class gate..."
set +e
python3 scripts/check_breakage_class.py
BREAKAGE_EXIT=$?
set -e

if [[ $BREAKAGE_EXIT -ne 0 ]]; then
  # Dual-channel error contract (team/scripts/lib/error_contract.sh): this
  # is a fail-loud site -- the breakage-class gate is fatal, unlike every
  # quality-class lint below.
  pgai_emit_error_contract \
    "run-integration-tests.breakage-class-gate" \
    "The breakage-class pre-flight gate found a syntax error, compile failure, or unresolved first-party import; running the integration test suite would produce no meaningful signal until this is fixed." \
    "Scroll up to the Cause(s) list printed by check_breakage_class.py above, fix each file:line it names, then re-run bash \"${BASH_SOURCE[0]}\"."
  exit 1
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

# --- Pre-suite listener snapshot ---
# Capture the pids of framework-rooted TCP listeners that exist BEFORE this
# suite launches pytest, scoped to _RIT_PROJECT_TEMP_SUBTREE (resolved above).
# The post-suite listener-cleanliness check (below) excludes these pids, so a
# pre-existing orphan left behind by an earlier, unrelated task is not
# attributed to this run — only a listener first observed after this
# snapshot (i.e. spawned during this run) is flagged.
_LISTENER_SNAPSHOT_FILE="$(pgai_mktemp listener_presuite_snapshot)"
pgai_listener_snapshot "${_RIT_PROJECT_TEMP_SUBTREE}" > "$_LISTENER_SNAPSHOT_FILE"

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

# --- Run quality-class pre-flight lints (non-fatal: warn, file, continue) ---
# Every lint below is quality-class per the breakage/quality split (see
# team/scripts/lib/gated_runner_lint.sh): findings print in full via
# gated_runner_run_quality_lint, are recorded into GATED_RUNNER_FINDINGS,
# and the runner ALWAYS continues to the next lint and eventually to
# pytest, regardless of any individual lint's exit code.  The breakage-class
# gate above is the only fatal pre-flight check.
cd "$REPO_ROOT/team"

# Test anti-pattern lint: scans team/tests/ for anti-pattern 1 (pattern-scan
# assertion loops) and anti-pattern 2 (hardcoded /tmp or $HOME paths / bare
# mktemp calls), per SOP.md "Test Authoring Guidelines".
echo "Running test anti-pattern lint..."
gated_runner_run_quality_lint "test_anti_patterns" -- \
  python3 scripts/lint_test_anti_patterns.py

# Skip-cites-real-bug grep gate: enforces the skip-citation policy — every
# skip()/SKIP: annotation that cites a bug must reference a real BUG-NNNN
# whose file exists in bugs/.  Placeholder IDs (BUG-SKIP-* or non-BUG-NNNN
# forms) are flagged.
echo "Running skip-cites-real-bug gate..."
gated_runner_run_quality_lint "skip_cites_real_bug" -- \
  bash "${SCRIPT_DIR}/lint_skip_bug_gate.sh" --tests-dir "${REPO_ROOT}/team/tests"

# ICD freshness gate: asserts that docs/api/icd.json is byte-identical to a
# fresh regeneration from the current codebase.  Fix: run
# 'bash team/scripts/generate-icd.sh' and commit the result.
echo "Running ICD freshness gate..."
gated_runner_run_quality_lint "icd_freshness" -- \
  python3 scripts/lint_icd_freshness.py

# ICD compatibility gate: asserts that docs/api/icd.json is a compatible
# superset of every ICD version listed in docs/api/baselines/SUPPORTED.
echo "Running ICD compatibility gate..."
gated_runner_run_quality_lint "icd_compat" -- \
  python3 scripts/lint_icd_compat.py

# CHANGELOG freshness gate: asserts that CHANGELOG.md is byte-identical to
# a fresh regeneration from the current codebase via changelog_writer.
#
# RC-mode tolerance: when the worktree HEAD is on an rc/* or ai_rc/* branch,
# PGAI_LINT_CHANGELOG_MODE=rc is set so the lint tolerates CHANGELOG
# staleness caused only by BUG-NNNN files filed after the CHANGELOG.md
# commit.  PYTHONHASHSEED=0 pins changelog_writer's frozenset iteration
# order so the gate's regeneration is byte-identical across invocations.
_RI_CURRENT_BRANCH="$(git -C "$REPO_ROOT" symbolic-ref --short HEAD 2>/dev/null || true)"
if [[ -z "$_RI_CURRENT_BRANCH" ]]; then
  # Detached HEAD — check if it is at a commit reachable from an rc/* branch.
  _RI_CURRENT_BRANCH="$(git -C "$REPO_ROOT" branch --format='%(refname:short)' \
    --points-at HEAD 2>/dev/null | grep -E '^(rc/|ai_rc/)' | head -1 || true)"
fi
export PYTHONHASHSEED=0
export PGAI_LINT_CHANGELOG_MODE=""
if [[ "$_RI_CURRENT_BRANCH" =~ ^(rc/|ai_rc/) ]]; then
  export PGAI_LINT_CHANGELOG_MODE="rc"
fi
unset _RI_CURRENT_BRANCH
echo "Running CHANGELOG freshness gate..."
gated_runner_run_quality_lint "changelog_freshness" -- \
  python3 scripts/lint_changelog_freshness.py
unset PYTHONHASHSEED PGAI_LINT_CHANGELOG_MODE

# Lib-function-dedupe lint: asserts that no function name is defined in
# more than one team/scripts/lib/*.sh file (bash's last-sourced-wins
# semantics would silently shadow one implementation).
echo "Running lib-function-dedupe lint..."
gated_runner_run_quality_lint "lib_function_dedupe" -- \
  python3 scripts/lint_lib_function_dedupe.py

# Env-bootstrap lint: asserts the env-bootstrap unification contract on
# both runtimes (bash entry points source env_bootstrap.sh/wake_common.sh;
# Python entry points import resolve_kanban_root rather than reading
# PGAI_AGENT_KANBAN_ROOT_PATH directly).
echo "Running env-bootstrap lint..."
gated_runner_run_quality_lint "env_bootstrap" -- \
  python3 scripts/lint_env_bootstrap.py

# Help-presence lint: asserts that every operator-facing shell script under
# team/scripts/ implements a --help or -h flag (Directive 6,
# docs/coding-standards.md).  Scripts in help_presence_exempt.txt are exempt.
echo "Running help-presence lint..."
gated_runner_run_quality_lint "help_presence" -- \
  python3 scripts/lint_help_presence.py

# Comment-provenance lint: asserts that code files in team/scripts/ and
# team/pgai_agent_kanban/, and release-summary prose in release-notes/, do
# not cite internal bug IDs, task IDs, priority IDs, or version-bundle
# names (Directive 8, docs/coding-standards.md).
echo "Running comment-provenance lint..."
gated_runner_run_quality_lint "comment_provenance" -- \
  python3 scripts/lint_comment_provenance.py

# Dashboard network-git gate: asserts that no network-capable git verb
# (ls-remote, fetch, pull, push, clone) appears anywhere under
# team/scripts/dashboard/ — the dashboard render path must stay local-only.
# CONVERT-class per docs/blocking-site-census.md row 39: previously a
# standalone script with no call site in either gated runner; wired in here
# so a regression is filed as a finding instead of never surfacing at all.
echo "Running dashboard network-git gate..."
gated_runner_run_quality_lint "dashboard_network_git_gate" -- \
  python3 scripts/lint_dashboard_network_git_gate.py

# Dual-channel error-contract pair lint: flags any fail-loud call site
# missing the error/message/remedy triple.  Always exits 0 by design; it
# already emits per-instance structured findings via --findings-file, so
# its output is merged directly into the aggregate rather than wrapped
# through gated_runner_run_quality_lint's coarse pass/fail shape.
echo "Running dual-channel error-contract pair lint..."
_RI_PAIR_LINT_FINDINGS="${PGAI_AGENT_KANBAN_TEMP_DIR}/lint/dual_channel_pair_integration.json"
mkdir -p "$(dirname "$_RI_PAIR_LINT_FINDINGS")"
python3 scripts/lint_dual_channel_pair.py --findings-file "$_RI_PAIR_LINT_FINDINGS"
gated_runner_merge_findings_file "$_RI_PAIR_LINT_FINDINGS"
unset _RI_PAIR_LINT_FINDINGS

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
  # Non-deterministic (exempt) per docs/coding-standards.md "The
  # non-deterministic exemption" — a real test/assertion failure has no
  # verbatim fix; point at the evidence pytest already printed above.
  echo "Integration tests FAILED (exit code: $PYTEST_EXIT)" >&2
  echo "Next diagnostic step: scroll up to the pytest output directly above —" >&2
  echo "its short-test-summary 'FAILED <file>::<test>' lines name the specific" >&2
  echo "test file and assertion that failed. Read that test's traceback for the" >&2
  echo "actual defect; there is no command that fixes a real test failure." >&2
  exit "$PYTEST_EXIT"
fi

echo "Integration tests PASSED"

# --- Post-suite /tmp cleanliness assertion (quality-class: warn, file, continue) ---
# CONVERT-class per docs/blocking-site-census.md row 52: previously a hard
# `exit 1` on residue detection.  Wrapped through gated_runner_run_quality_lint
# so a leak is recorded as a non-blocking finding instead of aborting the
# runner — pytest's own PYTEST_EXIT check above is the only fatal exit left
# in this script.  Runs only after a passing pytest run, unchanged from
# before.  The check uses the snapshot taken before the suite ran
# (_TMP_SNAPSHOT_FILE).
_run_integration_bare_tmp_cleanliness_check() {
  if ! pgai_tmp_cleanliness_check "$_TMP_SNAPSHOT_FILE"; then
    # Non-deterministic (exempt) per docs/coding-standards.md — which test
    # bypassed the temp resolver is not computable at detection time;
    # pgai_tmp_cleanliness_check already printed the offending paths above as
    # the starting evidence.
    echo "Next diagnostic step: the '[pgai-cleanliness]' lines above name each" >&2
    echo "residue path; find which test wrote there directly (bare /tmp or" >&2
    echo "open()/tempfile calls) instead of via pgai_mktemp / pgai_mktemp_d." >&2
    return 1
  fi
}
echo "Checking for bare-/tmp residue..."
gated_runner_run_quality_lint "bare_tmp_cleanliness" -- _run_integration_bare_tmp_cleanliness_check

# --- Post-suite listener cleanliness assertion (quality-class: warn, file, continue) ---
# CONVERT-class per docs/blocking-site-census.md row 53: previously a hard
# `exit 1` when a framework-rooted TCP listener survived the suite.  Same
# non-blocking treatment as the cleanliness check above.  Only listeners
# rooted in this project's own temp subtree are flagged; sibling projects'
# leaked listeners are not attributed to this runner.  The subtree was
# resolved once, before pytest launched (_RIT_PROJECT_TEMP_SUBTREE), so the
# pre-suite snapshot and this check agree on the same scope;
# PGAI_PROJECT_TEMP_SUBTREE is exported here — after pytest has already
# exited — so callers/tests that read the env var directly still see it,
# without pytest itself (or fixtures it spawns) ever inheriting a narrowed
# scope.  Listeners already present in the pre-suite snapshot
# (_LISTENER_SNAPSHOT_FILE) are pre-existing orphans and are not flagged;
# only a listener first observed after that snapshot — i.e. spawned by this
# run — is recorded as a finding.
export PGAI_PROJECT_TEMP_SUBTREE="${_RIT_PROJECT_TEMP_SUBTREE}"
_run_integration_listener_cleanliness_check() {
  if ! pgai_listener_cleanliness_check "${PGAI_PROJECT_TEMP_SUBTREE}" "${_LISTENER_SNAPSHOT_FILE}"; then
    # Non-deterministic (exempt) per docs/coding-standards.md — which test
    # leaked the listener is not computable at detection time;
    # pgai_listener_cleanliness_check already printed the surviving pid(s)
    # and cmdline(s) above as the starting evidence.
    echo "Next diagnostic step: the '[pgai-listener]' lines above name each" >&2
    echo "surviving pid and cmdline; identify which test spawned it and confirm" >&2
    echo "the test tears its server/socket fixture down in a finally block or" >&2
    echo "guaranteed teardown fixture." >&2
    return 1
  fi
}
echo "Checking for framework-rooted TCP listener leaks..."
gated_runner_run_quality_lint "listener_cleanliness" -- _run_integration_listener_cleanliness_check

# --- Write the aggregate quality-class findings file ---
# Written unconditionally (even when GATED_RUNNER_FINDINGS is empty) so a
# downstream consumer (TESTER's "Lint Findings (non-blocking)" report
# section) can rely on the file's existence as proof this pre-flight ran.
# Written here — after the pre-flight lints above AND the two post-suite
# cleanliness checks — so the findings array is a complete record of every
# quality-class check this run performed, not just the pre-pytest subset.
# See team/scripts/lib/gated_runner_lint.sh for the findings-file schema.
_RI_FINDINGS_FILE="${PGAI_AGENT_KANBAN_TEMP_DIR}/lint/findings-integration.json"
gated_runner_write_findings_file "$_RI_FINDINGS_FILE" "run-integration-tests.sh"
unset _RI_FINDINGS_FILE

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
