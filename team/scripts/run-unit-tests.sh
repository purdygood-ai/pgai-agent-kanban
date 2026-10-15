#!/usr/bin/env bash
# run-unit-tests.sh
# Run the unit test suite for the pgai-agent-kanban framework's team scripts.
# The dev-tree path is resolved with three-tier precedence:
#   1. PGAI_DEV_TREE_PATH env var (highest)
#   2. kanban.cfg [paths] dev_tree_path (middle)
#   3. self-located repo root derived from this script's own location (fallback)
# The script fails fast with a clear error only when none of the three tiers
# yields a valid dev tree directory.
# Repository root: run from a checkout (<repo>/team/scripts/), the runner
# works in that checkout, whatever PGAI_DEV_TREE_PATH names; run from an
# install (<kanban-root>/scripts/), it works in the resolved dev tree above.
# resolve_runner_repo_root in team/scripts/lib/dev_tree.sh holds the rule.
#
# Usage:
#   run-unit-tests.sh [--verbose] [--coverage] [--workers N] [--no-scratch-sweep]
#   run-unit-tests.sh --files tests/unit/test_semver.py [more test files ...]
#   run-unit-tests.sh --quarantined [--files PATH ...]
#   run-unit-tests.sh --reuse-if-green
#   run-unit-tests.sh --pre-merge [--base REF] [--workers N]
#
# Parallel workers and the serial lane: a full certification run reads
# kanban.cfg [testing] workers (config_testing_workers in
# team/scripts/lib/config_loader.sh; default 3). With workers above 1 and
# pytest-xdist importable, pytest runs twice, one after the other: the
# tests not marked @pytest.mark.serial with -n <workers> --dist loadfile,
# then the serial-marked tests in one process (the marker is registered in
# team/pyproject.toml). The two pytest exit codes combine into this run's
# one exit code, and the run still writes one findings file. Each lane
# prints its own "Replay this exact run:" line. With workers 0 or 1 the run
# is one single-process pytest run with no -n, exactly as before; with
# pytest-xdist not importable the run prints one narrated line and runs the
# same way, which is never a failure. --files and --quarantined runs are
# always single-process and never read workers. No more than [testing]
# workers pytest workers run at once. --workers N lowers the budget for one
# run and never raises it: the run uses the smaller of N and [testing]
# workers (run-gated-suites.sh uses it to split one budget between the two
# suites). The lane rules in full are in team/scripts/lib/gated_runner_lanes.py.
#
# Scratch sweep: a green certification run removes
# ${PGAI_AGENT_KANBAN_TEMP_DIR}/tests afterwards. --no-scratch-sweep skips
# that removal, for a caller that runs another suite at the same time --
# the other suite may still be using that scratch, so the caller sweeps
# once after both finish (run-gated-suites.sh does).
#
# Coverage is opt-in: --coverage is honoured only when kanban.cfg [testing]
# coverage = true, or when PGAI_TESTING_COVERAGE=1 is set in the operator's
# own shell outside an agent wake (the wake bootstrap unsets it, and it has
# no effect while PGAI_AGENT_WAKE_ACTIVE=1). Otherwise the run prints
# "coverage disabled by config ([testing] coverage)" and runs without
# coverage; the exit code is unaffected.
#
# Exit codes:
#   0  = all tests passed
#   1  = test failure, or (--files only) a per-file test-authoring lint
#        finding in a named file, or (--pre-merge only) a failed pre-merge
#        check
#   2  = pytest not installed, or (--pre-merge only) the pre-merge engine
#        could not start (unknown base, no RC or main branch to default to)
#
# Targeted runs (--files): pytest runs exactly the named test files, which
# must exist under tests/unit/ or pgai_agent_kanban/api/tests/ (paths are
# relative to team/). The fatal pre-flight gates still run. Of the
# quality-class lints, only the two that judge one test file at a time run,
# and only over the named files: the test anti-pattern lint
# (lint_test_anti_patterns.py --files) and the skip-cites-a-real-bug gate
# (lint_skip_bug_gate.sh --files). Their findings print in full and make
# the run exit 1 even when every named test passes; every other
# quality-class lint, and every whole-tree guard, is skipped. The run
# prints "targeted run — not a certification run", writes its exit code to
# ${PGAI_AGENT_KANBAN_TEMP_DIR}/targeted-unit.exit, never reads or writes
# the green ledger, and never claims, writes, or files bugs from
# lint/findings-unit.json -- a targeted run is a CODER's check of the files
# it touched, never the certification result TESTER reads. --files with
# --coverage is refused. Without --files the runner behaves exactly as
# described below.
#
# Pre-merge runs (--pre-merge [--base REF]): the cheap whole-tree checks a
# change should pass before it merges, run by the pre-merge engine
# (module pgai_agent_kanban.testing.pre_merge, via pp_run_ops): the by-path
# module test (tests/unit/test_package_modules_run_by_path.py), the five
# ratchet lints in fail-on-regression mode (inline Python, duplicated
# Python, engine size/dupe, API route impl, echo/printf piped into
# grep -q) and the tracked-files lint (tracked files an ignore rule matches,
# tracked generated files), four whole-tree guards run whatever the diff (env-bootstrap: scripts/lint_env_bootstrap.py;
# operator-verb-docs: scripts/generate_operator_verb_docs.py --check;
# dashboard-window-docs: scripts/dashboard/generate_window_docs.py --check;
# docs-citations: scripts/check_docs_citations.py -- a failing guard fails
# the run, and their added wall time is reported against the engine's
# 5.0s budget, which never fails it), and the unit test files that
# mention a script, module, function or CLI name changed in
# `git diff BASE...HEAD` or list the directory a changed file sits in
# (a test globbing scripts/dashboard/*.sh runs for a change to
# scripts/dashboard/attention.sh; ranked after the name matches). The
# engine prints one summary block naming each check, its verdict and
# duration, the base and the cap. --base REF names
# the diff base; without it the engine uses the local RC branch this
# worktree was cut from (with none, the main branch). The number of
# affected test files is capped by kanban.cfg [testing]
# pre_merge_affected_cap (config_testing_pre_merge_affected_cap; default
# 60, 0 = no cap), and pytest uses the [testing] workers budget, which
# --workers N may lower as on a full run. The fatal pre-flight gates and
# the quality-class lint block do not run. The run prints "pre-merge run
# — not a certification run", writes its exit code to
# ${PGAI_AGENT_KANBAN_TEMP_DIR}/pre-merge.exit (0 every check passed, 1 a
# check failed, 2 the engine could not start: an unknown base, no RC or
# main branch to default to), and never claims or writes
# lint/findings-unit.json, the green ledger or targeted-unit.exit.
# Nothing in the release path reads pre-merge.exit: a failed or skipped
# pre-merge run never blocks an RC by itself. --pre-merge with --files,
# --coverage, --quarantined or --reuse-if-green, and --base without
# --pre-merge, are refused before anything runs. The integration
# runner's equivalent (run-integration-tests.sh --pre-merge) runs the
# affected integration tests only.
#
# Slowest tests: a certification run (no --files, no --quarantined) passes
# every pytest lane --durations=25 --durations-min=1.0 plus a JUnit report,
# and after the last lane writes the slowest tests (at most 25, each at
# least 1.0s), the total run time and the test count to
# ${PGAI_AGENT_KANBAN_TEMP_DIR}/lint/durations-unit.json, beside the
# findings file (team/scripts/lib/gated_runner_durations.py). Informational
# only: it never gates, never files a bug, never changes the exit code.
#
# Green ledger: every certification run (any run without --files) appends
# {commit, suite, selection, exit, findings_sha, tree_clean} to the per-RC
# green ledger under the framework temp root. With --reuse-if-green the
# runner still runs every pre-flight gate and quality-class lint, then
# returns the recorded green result -- printing a "reused" line and exiting
# 0 without starting pytest -- when the ledger holds a green unit run at
# `git rev-parse HEAD` on a clean tree with the same marker selection and
# the same lint findings hash. Any mismatch, and any ledger problem, runs
# pytest normally after one "[green-ledger]" narration line. Ledger
# location, record fields and the full reuse rule are in the header of
# team/scripts/lib/green_ledger.sh.
#
# Quarantine marker: tests marked @pytest.mark.quarantine (registered in
# team/pyproject.toml; order- or timing-dependent verdicts) are deselected
# on every run by default (-m "not quarantine"), --files runs included.
# --quarantined inverts the selection (-m "quarantine") and runs exactly the
# quarantined tests -- not a certification run of the suite. A run whose
# marker selection leaves no test to run (pytest exit 5) exits 0 with a
# note. See "Quarantine marker selection" in
# team/scripts/lib/gated_runner_lint.sh.
#
# Wake tunables: pytest never inherits the [wake] tunables a wake's own
# config load exports -- MAX_TASKS_PER_WAKE, MAX_RUNTIME_SECONDS,
# PAUSE_BETWEEN_TASKS, STOP_ON_BLOCKED, STOP_FILE, PM_MAX_TASKS,
# MAX_TASK_SECONDS, MAX_PROJECT_SECONDS, KILL_GRACE_SECONDS,
# TRANSIENT_RETRY_CAP, ZERO_PROGRESS_CAP, BLOCK_STORM_WINDOW,
# BLOCK_HOLD_CAP, SYSTEMIC_BLOCK_REASONS, LOCK_WAIT_SECONDS,
# LOCK_STARVATION_FIRINGS, USAGE_LIMIT_PAUSE_SECONDS,
# MODEL_PREFLIGHT_TTL_SECONDS and MODEL_PREFLIGHT_TIMEOUT_SECONDS
# (CONFIG_LOADER_WAKE_ENV_NAMES in team/scripts/lib/config_loader.sh).
# This script unsets every one of them before pytest starts, as it does
# GIT_GUARD_REAL_GIT and PGAI_GIT_GUARD_DEPTH, so a run started inside an
# agent wake behaves like one started from a plain shell, and a test's
# sandboxed kanban.cfg [wake] values govern its own wakes. The "Replay
# this exact run:" line carries an `env -u NAME` for each. The operator
# override (`LOCK_WAIT_SECONDS=30 wake-batch.sh ...`) is unaffected: it
# applies to a wake, not to this script.
#
# Test order: pytest-randomly is neutralized (-p no:randomly), so this run
# and every other run of this script collect and execute tests in pytest's
# own stated, stable collection order -- not a random shuffle. A failing
# run is reproduced by pasting the "Replay this exact run:" line this
# script prints immediately before launching pytest; no seed capture is
# needed, and no other setup is needed either -- that line carries not
# just the pytest argv but every PYTEST_ADDOPTS/PYTHONHASHSEED/PGAI_*/
# KANBAN_ROOT state this script itself applied (see
# team/scripts/lib/gated_runner_lint.sh's gated_runner_replay_command), so
# the paste reproduces this run's environment regardless of what the
# operator's own shell happens to export. The pytest header this script
# prints confirms the ordering policy is in effect: the "plugins:" line
# omits "randomly-<version>" and there is no "Using --randomly-seed="
# line. See docs/test-authoring-guidelines.md "The two gated test
# scripts" for the full rationale.
#
# pytest basetemp: every invocation of this script claims its own pytest
# basetemp, ${PGAI_AGENT_KANBAN_TEMP_DIR}/pytest/unit-<scope>.XXXXXX, where
# <scope> is PGAI_TASK_ID when the wake layer exported one and pid<pid>
# otherwise, and the random suffix is mktemp's (gated_runner_pytest_basetemp
# in team/scripts/lib/gated_runner_lint.sh). pytest removes and recreates
# whatever --basetemp it is given at session start, so a path shared with
# any other live pytest session on the host -- a second run of this script,
# a sibling agent's run, run-integration-tests.sh -- would delete that
# session's fixture directories mid-run; a directory per invocation removes
# the interference entirely. The directory is removed on this script's own
# exit path, whatever the exit, so the pytest/ subtree never accumulates one
# directory per historical run. The "Replay this exact run:" line names the
# exact path this run used, and pasting it recreates that path (pytest makes
# its basetemp on startup; the pytest/ parent survives). A failing test's
# retained tmp_path therefore lives only as long as this script does -- to
# inspect one after the fact, paste the replay line, which runs pytest
# without this script's exit hook and leaves its basetemp in place.
#
# Post-suite checks on every exit code: the pre-suite snapshots (bare-/tmp
# residue, framework-rooted TCP listeners, and the live kanban root's
# HALT / HALT-AFTER / DEFER-LOW-BUGS sentinel set) are compared after
# pytest exits WHATEVER its exit code, so a red suite prints the same
# "[pgai-sentinel] ..." verdict line and records the same quality-class
# findings as a green one; the exit code stays pytest's. Only the
# scratch-tests sweep is green-only, so a failing test's scratch survives
# for debugging.
#
# Stale tmux sockets: on every exit, targeted and pre-merge runs included,
# the runner removes private tmux sockets that test runs left behind --
# only pgai-* files in the current user's tmux directory
# ($TMUX_TMPDIR/tmux-<uid>, /tmp when TMUX_TMPDIR is unset) that have no
# live server and are at least a minute old. It never kills a server and
# prints one "[gated-runner] tmux_socket_sweep: ..." line
# (gated_runner_tmux_socket_sweep in team/scripts/lib/gated_runner_lint.sh).
#
# Configuration:
#   PGAI_AGENT_KANBAN_ROOT_PATH  — kanban root (default: $HOME/pgai_agent_kanban)

# --- Bootstrap: self-locate → source shell-env → fail loud ---
# Must happen before the first use of PGAI_AGENT_KANBAN_ROOT_PATH so the
# script runs from a fresh shell without manual pre-sourcing.  Explicit
# operator exports win via env_bootstrap.sh's idempotency guard.
# shellcheck source=lib/env_bootstrap.sh
# BLOCK-WORTHY: an unreachable bootstrap prelude leaves every downstream
# tunable/path resolution undefined for this runner.
# CENSUS-ANCHOR: run-unit-tests-env-bootstrap-01
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh" || exit 1

# --- Resolve kanban root and script directory ---
# PGAI_AGENT_KANBAN_ROOT_PATH is now set by env_bootstrap.sh or the operator.
TEAM_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
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
# Source sentinel_leak_check.sh: the pre-suite snapshot / post-suite check that
# the LIVE kanban root's HALT / HALT-AFTER / DEFER-LOW-BUGS set is unchanged by
# the suite (see that file's header for why a leaked sentinel is never a test
# failure on its own).
# shellcheck source=lib/sentinel_leak_check.sh
[[ -f "${_RUT_SCRIPT_DIR}/lib/sentinel_leak_check.sh" ]] && source "${_RUT_SCRIPT_DIR}/lib/sentinel_leak_check.sh"
# Source error_contract.sh (dual-channel error contract emitter) and
# gated_runner_lint.sh (breakage-class/quality-class pre-flight split), and
# lint_bug_filer.sh (the hygiene lint's aggregate bug filer -- see its own
# call site below, at the findings-file write hook).
# shellcheck source=lib/error_contract.sh
[[ -f "${_RUT_SCRIPT_DIR}/lib/error_contract.sh" ]] && source "${_RUT_SCRIPT_DIR}/lib/error_contract.sh"
# shellcheck source=lib/gated_runner_lint.sh
[[ -f "${_RUT_SCRIPT_DIR}/lib/gated_runner_lint.sh" ]] && source "${_RUT_SCRIPT_DIR}/lib/gated_runner_lint.sh"
# shellcheck source=lib/lint_bug_filer.sh
[[ -f "${_RUT_SCRIPT_DIR}/lib/lint_bug_filer.sh" ]] && source "${_RUT_SCRIPT_DIR}/lib/lint_bug_filer.sh"
# Source green_ledger.sh: the per-RC green ledger every certification run
# appends to and --reuse-if-green reads (see its header).
# shellcheck source=lib/green_ledger.sh
[[ -f "${_RUT_SCRIPT_DIR}/lib/green_ledger.sh" ]] && source "${_RUT_SCRIPT_DIR}/lib/green_ledger.sh"
# Source config_loader.sh (the [testing] workers/coverage resolvers) and
# gated_runner_lanes.sh (the pytest lane plan and the lanes' combined exit
# code -- see "Plan the pytest lanes" below).
# shellcheck source=lib/config_loader.sh
source "${_RUT_SCRIPT_DIR}/lib/config_loader.sh"
# shellcheck source=lib/gated_runner_lanes.sh
source "${_RUT_SCRIPT_DIR}/lib/gated_runner_lanes.sh"
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
# shellcheck source=lib/script_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/script_common.sh"
trap cleanup_on_exit EXIT

# --- Resolve script directory and the repository root ---
# Run from a checkout (team/scripts/), the root is that checkout; run from an
# install (scripts/), it is the resolved dev tree, PGAI_DEV_TREE_PATH
# (resolve_runner_repo_root in lib/dev_tree.sh).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(resolve_runner_repo_root "$SCRIPT_DIR" "$TEAM_ROOT/kanban.cfg")"

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
_RUT_PROJECT_TEMP_SUBTREE=""
if [[ "${REPO_ROOT}" == "${_PGAI_TEMP_ROOT}/projects/"* ]]; then
    # REPO_ROOT is under <temp_root>/projects/<name>/worktrees/<branch>.
    # Extract the project subtree as <temp_root>/projects/<name>.
    _rel="${REPO_ROOT#"${_PGAI_TEMP_ROOT}/projects/"}"
    _proj_name="${_rel%%/*}"
    if [[ -n "${_proj_name}" ]]; then
        _RUT_PROJECT_TEMP_SUBTREE="${_PGAI_TEMP_ROOT}/projects/${_proj_name}"
    fi
fi
unset _PGAI_TEMP_ROOT _rel _proj_name

# --- Claim this run's aggregate findings file before any fatal gate ---
# Resolved and wired here -- before argument parsing and before the two
# fatal pre-flight gates below (pytest-missing, breakage-class) and the
# Dockerfile static lint gate -- so no abort between here and the
# quality-class lint block can leave a PRIOR invocation's findings file in
# place. This registration used to sit after the last quality-class lint
# (see "Run tests" below): a fatal abort before that point skipped the
# write entirely, and since PGAI_AGENT_KANBAN_TEMP_DIR is shared across
# every worktree and project on the host, whatever a PRIOR, unrelated
# invocation had written there looked like it described THIS run.
#
# gated_runner_claim_findings_file (team/scripts/lib/gated_runner_lint.sh)
# both clears the shared path for this run and records a run-identity
# token in a sidecar "<path>.runid" file, so gated_runner_assert_findings_
# claim below can tell whether the file still belongs to this run before
# handing it to the hygiene-lint aggregate bug filer.
export PGAI_AGENT_KANBAN_TEMP_DIR="$(pgai_temp_dir)"
mkdir -p "${PGAI_AGENT_KANBAN_TEMP_DIR}"
# A targeted run (--files) is decided here, ahead of the claim, because a
# targeted run never claims, writes, or files bugs from the findings file --
# that file describes certification runs only (see "Targeted
# (non-certification) runs" in team/scripts/lib/gated_runner_lint.sh). Its
# exit hook records the exit code in targeted-unit.exit instead.
_RU_TARGETED=false
if gated_runner_argv_requests_targeted_run "$@"; then
  _RU_TARGETED=true
fi
# A help invocation (--help/-h) is decided here too: it only prints usage,
# so it claims nothing and registers no exit hook below -- an existing
# findings file, its .runid claim and the green ledger stay byte-identical.
# The parse loop's --help|-h arm prints the usage and exits 0.
_RU_HELP_ONLY=false
if gated_runner_argv_requests_help "$@"; then
  _RU_HELP_ONLY=true
fi
# A pre-merge run (--pre-merge) is decided here as well: like a targeted
# run it is never a certification run, so it claims nothing; its exit hook
# records the exit code in pre-merge.exit instead (see "Pre-merge runs" in
# team/scripts/lib/gated_runner_lint.sh).
_RU_PRE_MERGE=false
if gated_runner_argv_requests_pre_merge "$@"; then
  _RU_PRE_MERGE=true
fi
_RU_TARGETED_EXIT_FILE="${PGAI_AGENT_KANBAN_TEMP_DIR}/targeted-unit.exit"
_RU_PRE_MERGE_EXIT_FILE="${PGAI_AGENT_KANBAN_TEMP_DIR}/pre-merge.exit"
if [[ "$_RU_TARGETED" != "true" && "$_RU_HELP_ONLY" != "true" && "$_RU_PRE_MERGE" != "true" ]]; then
_RU_FINDINGS_FILE="${PGAI_AGENT_KANBAN_TEMP_DIR}/lint/findings-unit.json"
_RU_RUN_TOKEN="$(gated_runner_claim_findings_file "$_RU_FINDINGS_FILE" "run-unit-tests.sh")"
fi
_ru_targeted_on_exit() {
  gated_runner_write_targeted_exit_file "$_RU_TARGETED_EXIT_FILE" "${1:-0}"
  gated_runner_pytest_basetemp_cleanup "${_RUT_PYTEST_BASETEMP:-}"
  gated_runner_tmux_socket_sweep
}
_ru_pre_merge_on_exit() {
  gated_runner_write_targeted_exit_file "$_RU_PRE_MERGE_EXIT_FILE" "${1:-0}"
  gated_runner_pytest_basetemp_cleanup "${_RUT_PYTEST_BASETEMP:-}"
  gated_runner_tmux_socket_sweep
}
_ru_write_findings_on_exit() {
  gated_runner_write_findings_file "$_RU_FINDINGS_FILE" "run-unit-tests.sh"
  # Consumer-side refusal (team/scripts/lib/gated_runner_lint.sh): only
  # hand the file just written to the aggregate bug filer when this run's
  # own claim on it is still intact -- a mismatch means a concurrent
  # invocation of a gated runner reclaimed the same shared path after
  # this run started, and filing bugs from content that may belong to
  # that other run would misattribute its findings to this one.
  if gated_runner_assert_findings_claim "$_RU_FINDINGS_FILE" "$_RU_RUN_TOKEN"; then
    # Hygiene-lint aggregate bug filer (team/scripts/lib/lint_bug_filer.sh):
    # files at most one bug per hygiene class present in the findings file
    # just written above, deduplicated against an already open/running bug
    # for that class. Runs on every exit path this hook covers, including a
    # failing pytest run, so a hygiene finding surfaces even when the suite
    # itself is red. KANBAN_ROOT is scoped locally to this one call because
    # this script unsets it ahead of its later pre-flight lints and pytest;
    # pp_bugs_dir needs it to resolve the project's own
    # bugs/ directory. Never allowed to affect this runner's own exit code:
    # wired into an autonomous, non-blocking gated runner
    # (team/SOP-core.md "Verification never stops the line for quality"), so
    # a filer failure is swallowed here rather than surfaced as a test
    # failure -- output is discarded rather than left to interleave with the
    # pytest summary a human or TESTER reads next.
    KANBAN_ROOT="$TEAM_ROOT" lint_bug_filer_file "${PGAI_PROJECT_NAME:-pgai-agent-kanban}" "$_RU_FINDINGS_FILE" >/dev/null 2>&1 || true
  fi
  # Append this certification run's record to the per-RC green ledger
  # (team/scripts/lib/green_ledger.sh). A no-op when the run aborted before
  # green_ledger_prepare (below, ahead of pytest) or was itself a reuse;
  # never affects this runner's exit code.
  if declare -F green_ledger_record_exit >/dev/null 2>&1; then
    green_ledger_record_exit "${1:-0}"
  fi
  # Release this invocation's own pytest basetemp (claimed further down, at
  # "Claim this invocation's own pytest basetemp") on the same exit path, so
  # the pytest/ subtree never keeps one directory per historical run. Empty
  # until that claim has happened, which the helper treats as a no-op; the
  # helper never affects this runner's own exit code.
  gated_runner_pytest_basetemp_cleanup "${_RUT_PYTEST_BASETEMP:-}"
  gated_runner_tmux_socket_sweep
}
if [[ "$_RU_HELP_ONLY" == "true" ]]; then
  : # help invocation: no exit hook, nothing written on exit
elif [[ "$_RU_PRE_MERGE" == "true" ]]; then
  CLEANUP_ON_EXIT_PRE_HOOK=_ru_pre_merge_on_exit
elif [[ "$_RU_TARGETED" == "true" ]]; then
  CLEANUP_ON_EXIT_PRE_HOOK=_ru_targeted_on_exit
else
CLEANUP_ON_EXIT_PRE_HOOK=_ru_write_findings_on_exit
fi

# --- Parse arguments ---
# One case arm per flag. A flag that takes a list (--files) consumes every
# following argument up to the next one starting with "-".
VERBOSE=false
PRE_MERGE_BASE=
COVERAGE=false
QUARANTINED=false
REUSE_IF_GREEN=false
NO_SCRATCH_SWEEP=false
REQUESTED_WORKERS=""
TARGETED_FILES=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --verbose|-v)
      VERBOSE=true
      shift
      ;;
    --workers)
      if [[ ! "${2:-}" =~ ^[0-9]+$ ]]; then
        # CENSUS-ANCHOR: run-unit-tests-workers-flag-01
        # AGENT-RESOLVABLE: the caller passes a whole number after --workers and re-runs.
        pgai_emit_error_contract "run-unit-tests.workers-flag-invalid" \
          "--workers needs a non-negative whole number in decimal digits after it (got '${2:-}'); it lowers this run's worker budget below kanban.cfg [testing] workers and never raises it." \
          "bash \"${BASH_SOURCE[0]}\" --workers 1"
        exit 1
      fi
      REQUESTED_WORKERS="$2"
      shift 2
      ;;
    --no-scratch-sweep)
      NO_SCRATCH_SWEEP=true
      shift
      ;;
    --coverage)
      COVERAGE=true
      shift
      ;;
    --quarantined)
      QUARANTINED=true
      shift
      ;;
    --reuse-if-green)
      REUSE_IF_GREEN=true
      shift
      ;;
    --files)
      shift
      while [[ $# -gt 0 && "$1" != -* ]]; do
        TARGETED_FILES+=("$1")
        shift
      done
      ;;
    --pre-merge)
      # Already decided by the argv pre-scan above (_RU_PRE_MERGE).
      shift
      ;;
    --base)
      if [[ -z "${2:-}" || "${2:-}" == -* ]]; then
        # CENSUS-ANCHOR: run-unit-tests-pre-merge-01
        # AGENT-RESOLVABLE: the caller names a git ref after --base, or drops --base for the default base, and re-runs.
        pgai_emit_error_contract "run-unit-tests.pre-merge-base-missing" \
          "--base needs a git ref after it (got '${2:-}'), e.g. --base ai_main; without --base a pre-merge run compares HEAD with the local RC branch this worktree was cut from." \
          "bash \"${BASH_SOURCE[0]}\" --pre-merge"
        exit 1
      fi
      PRE_MERGE_BASE="$2"
      shift 2
      ;;
    --help|-h)
      cat <<EOF
Usage: $(basename "$0") [--verbose] [--coverage] [--workers N] [--no-scratch-sweep] [--quarantined] [--reuse-if-green] [--files PATH [PATH ...]]
       $(basename "$0") --pre-merge [--base REF] [--workers N]

Run the unit test suite for pgai-agent-kanban team scripts.

Tests marked @pytest.mark.quarantine (order- or timing-dependent verdicts;
marker registered in team/pyproject.toml) are excluded from every run by
default: pytest is passed -m "not quarantine", --files runs included.

Parallel workers: a full run reads kanban.cfg [testing] workers (default
3). Above 1, with pytest-xdist importable, the tests not marked
@pytest.mark.serial run first with -n <workers> --dist loadfile, then the
serial-marked tests run in one process (marker registered in
team/pyproject.toml); the two results combine into one exit code and one
findings file, and each prints its own "Replay this exact run:" line.
Workers 0 or 1 runs one single-process pytest run with no -n. Without
pytest-xdist the run says so in one line and runs single-process -- never
a failure. --files and --quarantined runs are always single-process. No
more than [testing] workers pytest workers run at once.

Slowest tests: a certification run (no --files, no --quarantined) passes
pytest --durations=25 --durations-min=1.0 and writes the slowest tests,
the total run time and the test count to
<temp-root>/lint/durations-unit.json, beside the findings file.
Informational only: never a gate, never a filed bug, never a change to
the exit code.

Options:
  --verbose, -v   Pass --verbose flag through to pytest
  --workers N     Lower this run's worker budget to N (a whole number). The
                  run uses the smaller of N and kanban.cfg [testing]
                  workers, and says which in one line: the flag never
                  raises the budget. 0 or 1 runs one single-process pytest
                  run. Ignored by --files and --quarantined runs, which are
                  always single-process. run-gated-suites.sh passes it to
                  split one budget between the unit and integration suites.
  --no-scratch-sweep
                  Do not remove <temp-root>/tests after a green run. For a
                  caller running another suite at the same time, which may
                  still be using that scratch; the caller sweeps once after
                  both finish (run-gated-suites.sh does). The run prints one
                  line saying the sweep was left to the caller.
  --coverage      Request Python branch and line coverage via pytest-cov.
                  Honoured only when kanban.cfg [testing] coverage = true,
                  or when PGAI_TESTING_COVERAGE=1 is set in the operator's
                  own shell outside an agent wake (the variable has no
                  effect while PGAI_AGENT_WAKE_ACTIVE=1). Otherwise prints
                  "coverage disabled by config ([testing] coverage)" and
                  runs without coverage; the exit code is unaffected.
                  When honoured, appends: --cov=pgai_agent_kanban
                  --cov=pm-agent --cov=scripts/lib --cov-branch --cov-report=term
                  --cov-report=json:<temp-root>/coverage/unit.json
                  (plus --cov-append on the serial lane of a parallel run,
                  so the report covers both lanes).
                  If pytest-cov is not installed, prints a skip message to
                  stderr and runs the suite without coverage (non-blocking).
  --files PATH [PATH ...]
                  Targeted run -- not a certification run. pytest runs exactly
                  the named test files. Each PATH is relative to team/ (e.g.
                  tests/unit/test_semver.py; a team/-prefixed or absolute path
                  is also accepted) and must be an existing file under
                  tests/unit/ or pgai_agent_kanban/api/tests/. The fatal
                  pre-flight gates (breakage-class, Dockerfile static,
                  container-engine-free, API-parity) still run. Two
                  per-file test-authoring lints run over exactly the named
                  files -- the test anti-pattern lint and the
                  skip-cites-a-real-bug gate; their findings print and make
                  the run exit 1. Every other quality-class lint and every
                  whole-tree guard is skipped. The exit code is written to
                  <temp-root>/targeted-unit.exit; lint/findings-unit.json
                  is never claimed or written, and the green ledger is
                  never read or written. Refused with --coverage.
  --pre-merge     Pre-merge run -- not a certification run. Runs the
                  cheap whole-tree checks a change should pass before it
                  merges, through the pre-merge engine
                  (module pgai_agent_kanban.testing.pre_merge): the
                  by-path module test, the five ratchet lints in
                  fail-on-regression mode (inline Python, duplicated
                  Python, engine size/dupe, API route impl, echo/printf
                  piped into grep -q) and the tracked-files lint
                  (tracked files an ignore rule matches, tracked
                  generated files), four whole-tree guards run
                  whatever the diff (env-bootstrap,
                  operator-verb-docs, dashboard-window-docs,
                  docs-citations: lint_env_bootstrap.py,
                  generate_operator_verb_docs.py --check,
                  dashboard/generate_window_docs.py --check,
                  check_docs_citations.py; any finding fails the run),
                  and the unit test files that mention a name changed in
                  'git diff BASE...HEAD' or that list the directory a
                  changed file sits in (a test globbing
                  scripts/dashboard/*.sh runs for a change to
                  scripts/dashboard/attention.sh; ranked after the name
                  matches). Prints one summary block (each
                  check, its verdict and duration, the base and the cap,
                  and the guards' added wall time against a 5.0s budget
                  that never fails the run).
                  At most kanban.cfg [testing] pre_merge_affected_cap
                  affected test files run (default 60, 0 = no cap; above
                  the cap the most-referencing run); pytest uses the
                  [testing] workers budget, which --workers N may lower.
                  The fatal pre-flight gates and the rest of the
                  quality-class lints do not run, and --verbose and
                  --no-scratch-sweep have no effect. The exit code
                  (0 pass, 1 a check failed, 2 the engine could not
                  start) is written to
                  <temp-root>/pre-merge.exit; lint/findings-unit.json,
                  the green ledger and targeted-unit.exit are never
                  written. Refused with --files, --coverage,
                  --quarantined or --reuse-if-green. The integration
                  runner's --pre-merge runs the affected integration
                  tests.
  --base REF      With --pre-merge: the git ref the affected tests are
                  chosen against ('git diff REF...HEAD'). Default: the
                  local RC branch this worktree was cut from; with none,
                  the main branch. The summary names the base used.
                  Refused without --pre-merge.
  --quarantined   Run exactly the quarantine-marked tests and nothing else
                  (pytest -m "quarantine" in place of the default
                  -m "not quarantine"). Not a certification run of the
                  suite. Combined with --files, runs only the quarantined
                  tests inside the named files.
  --reuse-if-green
                  Skip pytest when this exact run is already certified green.
                  Every run without --files appends {commit, suite, exit,
                  findings_sha} to the per-RC green ledger at
                  <temp-root>/projects/<project>/green-ledger/<active-rc>/ledger.jsonl.
                  With this flag the pre-flight gates and quality-class
                  lints still run; then, if the ledger holds a green unit
                  run at git HEAD on a clean tree with the same marker
                  selection and the same lint findings hash, the runner
                  prints a line containing "reused" and exits 0 without
                  starting pytest. Any mismatch, a missing or unreadable
                  ledger, or --coverage runs the suite normally after one
                  "[green-ledger]" line. Ignored (with a note) on a --files
                  run, which never reads or writes the ledger.
  --help, -h      Show this help and exit 0. Prints usage only: no gate,
                  lint or pytest runs, lint/findings-unit.json and its
                  .runid claim are never claimed or written, and the
                  green ledger is never read or written.

Exit codes:
  0  = all tests passed (also: the quarantine marker selection left no test
       to run -- pytest exit code 5 -- which is narrated, not a failure)
  1  = test failure, or (--files only) a test anti-pattern or
       skip-citation finding in a named file, or (--pre-merge only) a
       failed pre-merge check
  2  = pytest not installed, or (--pre-merge only) the pre-merge engine
       could not start (unknown base, no RC or main branch to default to)
EOF
      exit 0
      ;;
    *)
      # Partial remedy (docs/coding-standards.md "Gates prescribe, not just
      # diagnose"): the offending argument is named deterministically, but which
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

# --- Validate a pre-merge run's flags ---
# Both refusals are AGENT-RESOLVABLE: the caller fixes its own argv and
# re-runs in the same session. A pre-merge run chooses its own tests and is
# never a certification run, so the flags that select tests (--files,
# --quarantined), measure coverage or reuse a certified result are refused
# beside it; --base names a pre-merge run's diff base and means nothing
# without one. Both refuse before any gate, lint or pytest runs.
if [[ "$_RU_PRE_MERGE" == "true" ]]; then
  _ru_pre_merge_conflicts=()
  [[ "$_RU_TARGETED" == "true" ]] && _ru_pre_merge_conflicts+=("--files")
  [[ "$COVERAGE" == "true" ]] && _ru_pre_merge_conflicts+=("--coverage")
  [[ "$QUARANTINED" == "true" ]] && _ru_pre_merge_conflicts+=("--quarantined")
  [[ "$REUSE_IF_GREEN" == "true" ]] && _ru_pre_merge_conflicts+=("--reuse-if-green")
  if [[ ${#_ru_pre_merge_conflicts[@]} -gt 0 ]]; then
    # CENSUS-ANCHOR: run-unit-tests-pre-merge-02
    # AGENT-RESOLVABLE: the caller drops the flags a pre-merge run does not take and re-runs.
    pgai_emit_error_contract "run-unit-tests.pre-merge-incompatible-flags" \
      "--pre-merge cannot be combined with ${_ru_pre_merge_conflicts[*]}: a pre-merge run chooses its own tests (the by-path module test, the ratchet lints, the whole-tree guards and the unit tests affected by git diff BASE...HEAD), is never a certification run, and measures no coverage." \
      "bash \"${BASH_SOURCE[0]}\" --pre-merge${PRE_MERGE_BASE:+ --base ${PRE_MERGE_BASE@Q}}"
    exit 1
  fi
  unset _ru_pre_merge_conflicts
  echo "pre-merge run — not a certification run"
  echo "Exit code will be written to: ${_RU_PRE_MERGE_EXIT_FILE}"
elif [[ -n "$PRE_MERGE_BASE" ]]; then
  # CENSUS-ANCHOR: run-unit-tests-pre-merge-03
  # AGENT-RESOLVABLE: the caller adds --pre-merge (or drops --base) and re-runs.
  pgai_emit_error_contract "run-unit-tests.base-without-pre-merge" \
    "--base ${PRE_MERGE_BASE} was given without --pre-merge; --base names the diff base a pre-merge run chooses its affected tests from, and a certification or --files run has none." \
    "bash \"${BASH_SOURCE[0]}\" --pre-merge --base ${PRE_MERGE_BASE@Q}"
  exit 1
fi

# --- Validate a targeted run's --files arguments ---
# Every refusal here is AGENT-RESOLVABLE: the caller fixes its own argv and
# re-runs in the same session. Paths are classified by
# gated_runner_targeted_resolve_path against this runner's own test roots
# (the directories its certification pytest argv collects), so a path that
# escapes them -- tests/../../etc/passwd -- is refused rather than collected.
_RU_TARGETED_ROOTS=("tests/unit" "pgai_agent_kanban/api/tests")
_RU_TARGETED_EXAMPLE="tests/unit/test_semver.py"
if [[ "$_RU_TARGETED" == "true" ]]; then
  if [[ ${#TARGETED_FILES[@]} -eq 0 ]]; then
    # CENSUS-ANCHOR: run-unit-tests-targeted-files-01
    # AGENT-RESOLVABLE: the caller names the test files it touched and re-runs.
    pgai_emit_error_contract "run-unit-tests.targeted-files-missing" \
      "--files was given without any test file path after it; a targeted run needs at least one test file under ${_RU_TARGETED_ROOTS[*]} (paths relative to team/)." \
      "bash \"${BASH_SOURCE[0]}\" --files ${_RU_TARGETED_EXAMPLE}"
    exit 1
  fi
  if [[ "$COVERAGE" == "true" ]]; then
    # CENSUS-ANCHOR: run-unit-tests-targeted-files-02
    # AGENT-RESOLVABLE: drop --coverage from the targeted run; coverage belongs to a full run.
    pgai_emit_error_contract "run-unit-tests.targeted-files-with-coverage" \
      "--files cannot be combined with --coverage: coverage measured over a handful of test files is not a meaningful coverage figure, so a targeted run never measures it." \
      "bash \"${BASH_SOURCE[0]}\" --files ${TARGETED_FILES[*]@Q}"
    exit 1
  fi
  _RU_TARGETED_RESOLVED=()
  for _ru_path in "${TARGETED_FILES[@]}"; do
    _ru_rc=0
    _ru_rel="$(gated_runner_targeted_resolve_path "$REPO_ROOT/team" "$_ru_path" "${_RU_TARGETED_ROOTS[@]}")" || _ru_rc=$?
    if [[ $_ru_rc -eq 3 ]]; then
      # CENSUS-ANCHOR: run-unit-tests-targeted-files-03
      # AGENT-RESOLVABLE: the caller passes a path under this runner's own test roots.
      pgai_emit_error_contract "run-unit-tests.targeted-file-outside-test-root" \
        "--files path '${_ru_path}' resolves outside this runner's test roots (${_RU_TARGETED_ROOTS[*]}, relative to ${REPO_ROOT}/team); the unit runner only runs unit test files. An integration test file belongs to run-integration-tests.sh --files." \
        "bash \"${BASH_SOURCE[0]}\" --files ${_RU_TARGETED_EXAMPLE}"
      exit 1
    fi
    if [[ $_ru_rc -ne 0 ]]; then
      # CENSUS-ANCHOR: run-unit-tests-targeted-files-04
      # AGENT-RESOLVABLE: the caller corrects the file name and re-runs.
      pgai_emit_error_contract "run-unit-tests.targeted-file-not-found" \
        "--files path '${_ru_path}' is not an existing test file (looked for ${REPO_ROOT}/team/${_ru_rel}); each --files path must name a file, not a directory, under ${_RU_TARGETED_ROOTS[*]}." \
        "ls \"${REPO_ROOT}/team/tests/unit\" && bash \"${BASH_SOURCE[0]}\" --files ${_RU_TARGETED_EXAMPLE}"
      exit 1
    fi
    _RU_TARGETED_RESOLVED+=("$_ru_rel")
  done
  unset _ru_path _ru_rc _ru_rel
  echo "targeted run — not a certification run"
  echo "Targeted test files: ${_RU_TARGETED_RESOLVED[*]}"
  echo "Exit code will be written to: ${_RU_TARGETED_EXIT_FILE}"
fi

# --- Resolve the kanban.cfg [testing] settings this run uses ---
# Read through config_testing_workers / config_testing_coverage
# (team/scripts/lib/config_loader.sh), which apply the documented defaults
# and refuse a malformed value with the dual-channel error/message/remedy
# on stderr. workers is read only by a full certification run -- a --files
# or --quarantined run is single-process and never consults it -- and
# coverage only when --coverage was given.
_RU_KANBAN_CFG="$TEAM_ROOT/kanban.cfg"
_RU_WORKERS=1
if [[ "$_RU_TARGETED" != "true" && "$QUARANTINED" != "true" ]]; then
  if ! _RU_WORKERS="$(config_testing_workers "$_RU_KANBAN_CFG")"; then
    gated_runner_record_finding "runner_incomplete" \
      "run-unit-tests.sh aborted before any quality-class lint or pytest ran: kanban.cfg [testing] workers is not an accepted value."
    # BLOCK-WORTHY: the worker budget bounds how many pytest processes this
    # run starts on a shared host; config_testing_workers has already
    # printed the error/message/remedy naming the value and the sed fix.
    # CENSUS-ANCHOR: run-unit-tests-testing-config-01
    exit 1
  fi
fi
# A pre-merge run also reads [testing] pre_merge_affected_cap through
# config_testing_pre_merge_affected_cap: the most affected test files it
# runs (0 = no cap), handed to the engine as --cap.
_RU_PRE_MERGE_CAP=""
if [[ "$_RU_PRE_MERGE" == "true" ]]; then
  if ! _RU_PRE_MERGE_CAP="$(config_testing_pre_merge_affected_cap "$_RU_KANBAN_CFG")"; then
    # BLOCK-WORTHY: the cap decides how many affected test files this
    # pre-merge run selects, so it is not guessed from a malformed value;
    # config_testing_pre_merge_affected_cap has already printed the
    # error/message/remedy naming the value and the sed fix.
    # CENSUS-ANCHOR: run-unit-tests-testing-config-03
    exit 1
  fi
fi
_RU_COVERAGE_CONFIG=false
if [[ "$COVERAGE" == "true" ]]; then
  if ! _RU_COVERAGE_CONFIG="$(config_testing_coverage "$_RU_KANBAN_CFG")"; then
    gated_runner_record_finding "runner_incomplete" \
      "run-unit-tests.sh aborted before any quality-class lint or pytest ran: kanban.cfg [testing] coverage is not an accepted value."
    # BLOCK-WORTHY: a coverage switch nobody set cannot be guessed;
    # config_testing_coverage has already printed the error/message/remedy.
    # CENSUS-ANCHOR: run-unit-tests-testing-config-02
    exit 1
  fi
fi

# --- Check pytest is available ---
# Full remedy (docs/coding-standards.md "Gates prescribe, not just
# diagnose"): pytest's absence has one universal, verbatim-runnable fix.
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
  # Records that this run aborted here (see the findings-file claim, top
  # of this script) so the findings file reads as an honestly-aborted run
  # rather than a clean pass with zero findings -- the two would otherwise
  # be byte-identical in the "findings" array.
  gated_runner_record_finding "runner_incomplete" \
    "run-unit-tests.sh aborted before any quality-class lint or pytest ran: pytest is not installed."
  # BLOCK-WORTHY: verification unable to run at all -- pytest missing means
  # the unit suite cannot execute, not that it ran clean.
  # CENSUS-ANCHOR: run-unit-tests-pytest-missing-01
  exit 2
fi

# --- Fatal pre-flight gates: skipped on a pre-merge run ---
# Everything from here to the matching "end of fatal pre-flight gates"
# marker is the four fatal gates a certification or --files run passes
# before pytest. A pre-merge run runs exactly the engine's checks (see the
# header, "Pre-merge runs"), so it skips them. The body is deliberately
# left at its original indentation.
if [[ "$_RU_PRE_MERGE" != "true" ]]; then
# --- Run breakage-class gate (fatal; one of two fatal pre-flight checks) ---
# Syntax errors, compile failures, and unresolved first-party imports mean
# running the suite is meaningless -- this is one of the two pre-flight
# checks that still block before pytest (the Dockerfile static lint just
# below is the other).  Every other pre-flight check below is
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
  # See the pytest-missing gate's matching comment, above, for why this
  # finding is recorded before the abort.
  gated_runner_record_finding "runner_incomplete" \
    "run-unit-tests.sh aborted before any quality-class lint or pytest ran: the breakage-class pre-flight gate exited ${BREAKAGE_EXIT}."
  # Dual-channel error contract (team/scripts/lib/error_contract.sh): this
  # is a fail-loud site -- the breakage-class gate is fatal, unlike every
  # quality-class lint below.
  pgai_emit_error_contract \
    "run-unit-tests.breakage-class-gate" \
    "The breakage-class pre-flight gate found a syntax error, compile failure, or unresolved first-party import; running the unit test suite would produce no meaningful signal until this is fixed." \
    "Scroll up to the Cause(s) list printed by check_breakage_class.py above, fix each file:line it names, then re-run bash \"${BASH_SOURCE[0]}\"."
  exit 1
fi

# --- Run Dockerfile static lint (fatal; BLOCK-WORTHY) ---
# A prior DEEP-VERIFY cold-build failure showed that a malformed
# docker/*/Dockerfile is an unusable build under team/SOP-core.md's
# "Unusable build blocks; an unavailable build environment does not"
# doctrine: the duplicated-FROM-keyword and symbolic-chmod shapes it
# records cannot be reduced to an image by ANY engine this project ships (docker/buildkit
# rejects the former outright; podman/buildah rejects both) -- there is no
# successful build hiding behind the malformed text, so this is doctrine's
# "failed to build" verdict, never "could not be attempted".  This lint is
# engine-free (never invokes or probes docker/podman) so it can run in the
# fast per-RC suite as a cheap, exact proxy for a failure class that would
# otherwise surface only in the weekly (opt-out-able) DEEP-VERIFY
# cold-build lane.  See team/scripts/lint_dockerfile_static.py for the two
# checked shapes.
echo "Running Dockerfile static lint..."
set +e
python3 scripts/lint_dockerfile_static.py
DOCKERFILE_STATIC_LINT_EXIT=$?
set -e

if [[ $DOCKERFILE_STATIC_LINT_EXIT -ne 0 ]]; then
  # See the pytest-missing gate's matching comment, above, for why this
  # finding is recorded before the abort.
  gated_runner_record_finding "runner_incomplete" \
    "run-unit-tests.sh aborted before any quality-class lint or pytest ran: the Dockerfile static lint exited ${DOCKERFILE_STATIC_LINT_EXIT}."
  # CENSUS-ANCHOR: dockerfile-static-lint-01
  # BLOCK-WORTHY (team/SOP-core.md "Gates and Refusals" -- "Unusable
  # build blocks; an unavailable build environment does not"): a
  # Dockerfile carrying either checked shape cannot be reduced to an
  # image by any engine this project ships, so this is an unusable
  # build, not merely an unavailable build environment -- a real,
  # doctrine-justified stop, not a quality-class finding.
  pgai_emit_error_contract \
    "run-unit-tests.dockerfile-static-lint" \
    "The engine-free Dockerfile static lint found a malformed FROM instruction or a non-octal COPY/ADD --chmod mode in a docker/*/Dockerfile; the offending Dockerfile cannot be reduced to an image by any engine this project ships." \
    "Scroll up to the Cause(s) list printed by lint_dockerfile_static.py above, fix each file:line it names, then re-run bash \"${BASH_SOURCE[0]}\"."
  exit 1
fi

# --- Run container-engine-free lint (fatal; BLOCK-WORTHY) ---
# A container-launching test in the per-RC sweep set builds
# multi-GB images and spins containers on EVERY gated run on a
# docker-present host (CODER pre-merge runs and TESTER verifies alike) --
# runaway build/container cost on every RC is real damage, so this is a
# real stop, not a quality-class finding. Scans team/tests/unit/,
# team/tests/integration/, and team/pgai_agent_kanban/api/tests/ (never
# team/tests/cold/, the DEEP-VERIFY tier those two prior modules were
# relocated into) for any subprocess invocation, shell command, or
# shutil.which() probe of docker/podman -- see
# team/scripts/lint_container_engine_free.py for the exemption that keeps
# a deliberate absent-binary test (one that redirects PATH to a location
# it controls before the call) from tripping this gate.
echo "Running container-engine-free lint..."
set +e
python3 scripts/lint_container_engine_free.py
CONTAINER_ENGINE_FREE_LINT_EXIT=$?
set -e

if [[ $CONTAINER_ENGINE_FREE_LINT_EXIT -ne 0 ]]; then
  # See the pytest-missing gate's matching comment, above, for why this
  # finding is recorded before the abort.
  gated_runner_record_finding "runner_incomplete" \
    "run-unit-tests.sh aborted before any quality-class lint or pytest ran: the container-engine-free lint exited ${CONTAINER_ENGINE_FREE_LINT_EXIT}."
  # CENSUS-ANCHOR: container-engine-free-lint-01
  # BLOCK-WORTHY: a test module in the per-RC sweep set that
  # invokes or probes a container engine imposes runaway build/container
  # cost on every RC -- a real, doctrine-justified stop, not a
  # quality-class finding.
  pgai_emit_error_contract \
    "run-unit-tests.container-engine-free-lint" \
    "The container-engine-free lint found a test in the per-RC sweep set that invokes or probes a container engine (docker/podman); this test class builds multi-GB images or launches real containers on every gated run on a docker-present host." \
    "Scroll up to the Cause(s) list printed by lint_container_engine_free.py above, relocate each named file to team/tests/cold/ (or redirect PATH before the call if it is a deliberate absent-binary test), then re-run bash \"${BASH_SOURCE[0]}\"."
  exit 1
fi

# --- Run API-parity census-walk gate (fatal; BLOCK-WORTHY) ---
# Parity lint and schema validation block: a missing REST twin is real API
# damage. lint_api_parity.py walks team/config/operator_verbs.
# manifest (the operator verb census) against the tree it describes -- a
# routed verb's script missing on disk, a script-fronted REST route with no
# census row, a route's response failing to validate against its declared
# schema_ref, or an operator-invocable script with no census row at all --
# plus its original flag/body-model parity checks. Any of these means the
# API's actual shape has drifted from what the census (and therefore the
# generated docs and the CLI/REST parity guarantee) claims, so this is a
# real stop, not a quality-class finding -- see docs/blocking-site-
# census.md row 34.
echo "Running API-parity census-walk gate..."
set +e
python3 scripts/lint_api_parity.py
API_PARITY_LINT_EXIT=$?
set -e

if [[ $API_PARITY_LINT_EXIT -ne 0 ]]; then
  # See the pytest-missing gate's matching comment, above, for why this
  # finding is recorded before the abort.
  gated_runner_record_finding "runner_incomplete" \
    "run-unit-tests.sh aborted before any quality-class lint or pytest ran: the API-parity census-walk gate exited ${API_PARITY_LINT_EXIT}."
  # CENSUS-ANCHOR: api-parity-census-gate-01
  # BLOCK-WORTHY (team/SOP-core.md "Gates and Refusals"): a missing or
  # schema-mismatched REST twin is real API damage, not a documentation-
  # accuracy note.
  pgai_emit_error_contract \
    "run-unit-tests.api-parity-census-gate" \
    "The API-parity census-walk gate found the operator verb census (team/config/operator_verbs.manifest) disagreeing with the scripts, routes, or response shapes the tree actually ships." \
    "Scroll up to the Cause(s) list printed by lint_api_parity.py above, fix each verb/script/route it names (or add a documented exemption to team/config/operator_verb_census_exempt.txt), then re-run bash \"${BASH_SOURCE[0]}\"."
  exit 1
fi
fi
# --- end of fatal pre-flight gates ---

# --- Claim this invocation's own pytest basetemp under the framework temp root ---
# Keeps pytest from creating /tmp/pytest-of-<user>/, and -- because pytest
# removes and recreates its basetemp at session start -- keeps this run's
# fixture directories out of reach of every other live pytest session on
# the host: run-integration-tests.sh, a second run of this script, or a
# sibling agent's run of either. gated_runner_pytest_basetemp
# (team/scripts/lib/gated_runner_lint.sh) resolves a directory that belongs
# to this invocation alone (its shape is in this script's header); the exit
# hook registered above removes it on every exit path, so the pytest/
# subtree never holds one directory per historical run. The variable stays
# set, unexported, so that hook can find it. PYTEST_ADDOPTS is appended to
# (not overwritten) in case the caller already has flags set -- and because
# it is in the replay family, the "Replay this exact run:" line below names
# the exact basetemp this run used. PGAI_AGENT_KANBAN_TEMP_DIR itself was
# already resolved and exported above, at the findings-file claim, which is
# also what lets conftest.py's PYTEST_DEBUG_TEMPROOT guard see it.
_RUT_PYTEST_BASETEMP="$(gated_runner_pytest_basetemp unit)"
export PYTEST_ADDOPTS="${PYTEST_ADDOPTS:+${PYTEST_ADDOPTS} }--basetemp=${_RUT_PYTEST_BASETEMP}"

# --- Pre-suite /tmp cleanliness snapshot ---
# Capture the current top-level /tmp contents BEFORE the suite runs.
# After the suite, pgai_tmp_cleanliness_check compares against this snapshot
# to detect any bare-/tmp residue left by kanban code.
# The snapshot file lives under the framework temp root (not bare /tmp).
_TMP_SNAPSHOT_FILE="$(pgai_mktemp tmp_cleanliness_snapshot)"
pgai_tmp_snapshot > "$_TMP_SNAPSHOT_FILE"

# --- Pre-suite listener snapshot ---
# Capture the pids of framework-rooted TCP listeners that exist BEFORE this
# suite launches pytest, scoped to _RUT_PROJECT_TEMP_SUBTREE (resolved above).
# The post-suite listener-cleanliness check (below) excludes these pids, so a
# pre-existing orphan left behind by an earlier, unrelated task is not
# attributed to this run — only a listener first observed after this
# snapshot (i.e. spawned during this run) is flagged.
_LISTENER_SNAPSHOT_FILE="$(pgai_mktemp listener_presuite_snapshot)"
pgai_listener_snapshot "${_RUT_PROJECT_TEMP_SUBTREE}" > "$_LISTENER_SNAPSHOT_FILE"

# --- Pre-suite sentinel snapshot (live kanban root) ---
# Record which HALT / HALT-AFTER / DEFER-LOW-BUGS files exist on the LIVE
# kanban root (and what they contain) BEFORE pytest runs. The post-suite
# sentinel check (below) compares against this, so an operator's own
# pre-existing HALT is never reported, only a sentinel this run added,
# changed or removed. Resolved here, before any test can redirect the root
# variables, so both ends of the comparison name the same live root.
_SENTINEL_LIVE_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-${KANBAN_ROOT:-}}"
_SENTINEL_SNAPSHOT_FILE="$(pgai_mktemp sentinel_presuite_snapshot)"
pgai_sentinel_snapshot "${_SENTINEL_LIVE_ROOT}" > "$_SENTINEL_SNAPSHOT_FILE"

# --- Build pytest arguments ---
# tests/unit/                      — the main unit test tree
# pgai_agent_kanban/api/tests/     — package-local tests for the operator API
#                                    (test_cors.py, test_fidelity.py, test_adapter.py);
#                                    these live next to the api package code, not under
#                                    tests/unit/, so they are listed explicitly.
# tests/test_pgai_agent_kanban_cm_*.py — module-level tests for extracted cm packages
#                  (these live at the tests/ top level, not under unit/, so they are
#                  listed explicitly rather than relying on glob discovery inside unit/)
# -p no:randomly: states the test order rather than randomising it. Both
# gated runners and verify-test-order-independence.sh (team/SOP-core.md
# "Verification never stops the line for quality") apply this identically
# so a failing run replays from the runner's own invocation with no seed
# to capture -- see the usage comment above.
# --ignore=tests/unit/test_entrypoint_shipped_image.py: this module builds
# real images and starts real containers by design (its own docker-gated
# runner, run-gated-tests.sh, exercises it on demand); tests/unit/'s glob
# would otherwise double-collect it into this fast per-RC sweep, which is
# exactly the cost class lint_container_engine_free.py forbids.
# It stays at this path (rather than moving to tests/cold/) because
# run-gated-tests.sh and lint_orphan_tests.py's _COLLECTED_ROOTS both
# already treat tests/unit/ as its home; moving it would strand one and
# misclassify it against the other's tests/cold/-is-cold-only invariant
# (see test_runner_pytest_scope_excludes_cold_build.py).
PYTEST_ARGS=(
  "-p" "no:randomly"
  "tests/unit/"
  "pgai_agent_kanban/api/tests/"
  "--ignore=tests/unit/test_entrypoint_shipped_image.py"
)
# A targeted run collects exactly the validated --files paths in place of
# the two suite roots; the ordering policy and the ignore stay identical.
if [[ "$_RU_TARGETED" == "true" ]]; then
  PYTEST_ARGS=(
    "-p" "no:randomly"
    "${_RU_TARGETED_RESOLVED[@]}"
    "--ignore=tests/unit/test_entrypoint_shipped_image.py"
  )
fi
# Quarantine marker selection (team/scripts/lib/gated_runner_lint.sh):
# every run, targeted or not, deselects quarantined tests by default;
# --quarantined selects exactly them instead.
_RU_MARKER_EXPRESSION="$(gated_runner_quarantine_marker_expression "$QUARANTINED")"
PYTEST_ARGS+=("-m" "$_RU_MARKER_EXPRESSION")
if [[ "$VERBOSE" == "true" ]]; then
  PYTEST_ARGS+=("--verbose")
fi
# PYTEST_ARGS is the single-process argv. The lanes actually run -- and
# whether coverage options join them -- are planned from it just before
# pytest starts (see "Plan the pytest lanes" below).

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

# --- Quality-class block: skipped on a targeted or pre-merge run ---
# Everything from here to the matching "end of quality-class block" marker
# is quality-class and is skipped on a --files or --pre-merge run, whose
# findings would be written nowhere (neither run writes the findings
# file). The body is deliberately left at its original indentation.
if [[ "$_RU_TARGETED" != "true" && "$_RU_PRE_MERGE" != "true" ]]; then
# --- Run quality-class pre-flight lints (non-fatal: warn, file, continue) ---
# Every lint below is quality-class per the breakage/quality split (see
# team/scripts/lib/gated_runner_lint.sh): findings print in full via
# gated_runner_run_quality_lint, are recorded into GATED_RUNNER_FINDINGS,
# and the runner ALWAYS continues to the next lint and eventually to
# pytest, regardless of any individual lint's exit code.  The breakage-class
# gate above is the only fatal pre-flight check.
cd "$REPO_ROOT/team"

# Test anti-pattern lint: scans team/tests/ for anti-pattern 1 (pattern-scan
# assertion loops) and anti-pattern 2 (hardcoded /tmp or $HOME paths), per
# roles/TESTER.md "Test Authoring Guidelines".
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

# Orphan-test lint: scans team/ for test_*.py files that are unreachable
# from either gated runner's pytest argv.  An orphan test means a
# regression it catches will never surface in the authoritative
# UNIT_EXIT/INTEGRATION_EXIT signal.
echo "Running orphan-test lint..."
gated_runner_run_quality_lint "orphan_tests" -- \
  python3 scripts/lint_orphan_tests.py

# API-parity lint has moved: it is now the BLOCK-WORTHY census-walk gate run
# in the fatal pre-flight section above (docs/blocking-site-census.md row 34,
# anchor api-parity-census-gate-01) rather than a quality-class finding here.

# Operator-verb-manifest reconciliation lint: asserts that
# team/config/operator_verbs.manifest agrees with the tree it describes --
# every active verb's script exists and its flags match, every declared
# REST twin resolves to a real router route, and every operations.py route
# fronting a script is named by some verb. CONVERT (team/SOP-core.md "Gates
# and Refusals"): manifest drift carries zero runtime-failure risk.
echo "Running operator-verb-manifest reconciliation lint..."
gated_runner_run_quality_lint "operator_verb_manifest" -- \
  python3 scripts/lint_operator_verb_manifest.py

# Upgrade no-temp-copy-guard lint: asserts that the retired temp-copy guard
# identifiers (_PGAI_UPGRADE_SELF_COPY, _PGAI_UPGRADE_REEXEC,
# _PGAI_UPGRADE_ORIG_DIR) are absent from executable lines in
# team/scripts/upgrade.sh.  A squash-merge conflict can silently resurrect
# the retired block.
echo "Running upgrade no-temp-copy-guard lint..."
gated_runner_run_quality_lint "upgrade_no_temp_copy_guard" -- \
  python3 scripts/lint_upgrade_no_temp_copy_guard.py

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

# Operator verb reference freshness gate: asserts that the census-derived
# generated sections of docs/operator-commands.md and docs/operator-api.md
# are byte-identical to a fresh regeneration from
# team/config/operator_verbs.manifest, each verb's own --help output, and
# docs/api/icd.json -- so a census row edited without regenerating is
# reported here, the same way a stale ICD is.  CONVERT (team/SOP-core.md
# "Gates and Refusals"): documentation drift carries zero runtime-failure
# risk, so this never blocks the suite -- it only records a finding.  Fix:
# run 'python3 team/scripts/generate_operator_verb_docs.py' and commit the
# result.
echo "Running operator verb reference freshness gate..."
gated_runner_run_quality_lint "operator_verb_docs_freshness" -- \
  python3 scripts/generate_operator_verb_docs.py --check

# Dashboard window-census freshness gate: asserts that the three surfaces
# generate_window_docs.py renders -- create.sh's "Window order:" comment,
# docs/DASHBOARD-PANES.md's per-window pane tables, and docs/dashboard.md's
# window-by-window tour -- are byte-identical to a fresh regeneration from
# team/config/dashboard_windows.census.  CONVERT (team/SOP-core.md "Gates
# and Refusals"): documentation drift here carries zero runtime-failure
# risk, so this never blocks the suite -- it only records a finding.  Fix:
# run 'python3 team/scripts/dashboard/generate_window_docs.py' and commit
# the result.
echo "Running dashboard window-census freshness gate..."
gated_runner_run_quality_lint "dashboard_window_census" -- \
  python3 scripts/dashboard/generate_window_docs.py --check

# Dashboard tmux format-job lint: no tmux option a dashboard script sets
# (status formats, status-left/right, window-status formats, pane borders,
# titles) may run a #() job, which tmux re-runs on almost every redraw, once
# per attached client. CONVERT (team/SOP-core.md "Gates and Refusals"): the
# cost is CPU, never a broken render, so this only records a finding.
echo "Running dashboard tmux format-job lint..."
gated_runner_run_quality_lint "dashboard_tmux_format_jobs" -- \
  python3 scripts/dashboard/lint_tmux_format_jobs.py

# Hold KANBAN_ROOT and PYTHONHASHSEED unset for the rest of the run, so a
# value the operator's shell exports (KANBAN_ROOT from shell-env, most
# often) reaches neither the lints below nor pytest, and the replay line
# forces both absent with `env -u` (gated_runner_replay_command,
# team/scripts/lib/gated_runner_lint.sh).
unset KANBAN_ROOT PYTHONHASHSEED

# Doc-truth lint: every command a document cites (README.md, HOW_TO.md,
# ARCHITECTURE.md, docs/docker.md, and the docs-manifest.txt-derived
# closure) must exist in this tree and answer --help cleanly; every
# configuration key and dashboard window/pane a document names must agree
# with the loader's Key Registry and the dashboard census, respectively.
# CONVERT-class (team/SOP-core.md "Gates and Refusals"; see
# lint_doc_command_truth.py's own "CENSUS CLASS" docstring note): a stale or
# false doc citation carries zero runtime-failure risk, so this warns,
# reports, and continues rather than blocking the suite. Previously an
# orphan verb nothing in the tree invoked -- its findings reached
# nobody unless a human ran it by hand.
echo "Running doc-truth lint..."
gated_runner_run_quality_lint "doc_command_truth" -- \
  python3 scripts/lint_doc_command_truth.py

# Engine size + dupe lint: asserts (1) no decomposed engine driver/stage
# file has grown back past 800 lines, and (2) no shell function name
# is defined in two or more team/scripts/*.sh files that could be loaded
# into the same bash process (bash's last-sourced-wins semantics would
# silently shadow one implementation). Supersedes the narrower
# lint_lib_function_dedupe.py (flat lib/*.sh only) as the gated-runner call
# site -- see lint_engine_size_dupe.py's module docstring for the
# relationship between the two.
echo "Running engine-size/dupe lint..."
gated_runner_run_quality_lint "engine_size_dupe" -- \
  python3 scripts/lint_engine_size_dupe.py

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

# Hygiene lint: consolidated per-instance quality scan covering provenance
# (code files in team/scripts/ and team/pgai_agent_kanban/, release-summary
# prose in release-notes/, and declarative config in team/config/ must not
# cite internal bug IDs, task IDs, priority IDs, or version-bundle names --
# Directive 8, docs/coding-standards.md), version-named-test naming, and
# comment-drift.
# Always exits 0 by design (like the dual-channel-pair and census-compliance
# lints above); it already emits per-instance structured findings via
# --findings-file, one record per hygiene finding each carrying its own
# class, so its output is merged directly into the aggregate rather than
# wrapped through gated_runner_run_quality_lint's coarse pass/fail shape --
# folding it into that wrapper would collapse every finding's class into a
# single exit-code-shaped record, destroying the per-class information the
# aggregate bug filer keys on.
echo "Running hygiene lint..."
_RU_HYGIENE_LINT_FINDINGS="${PGAI_AGENT_KANBAN_TEMP_DIR}/lint/hygiene_unit.json"
mkdir -p "$(dirname "$_RU_HYGIENE_LINT_FINDINGS")"
python3 scripts/lint_hygiene.py --findings-file "$_RU_HYGIENE_LINT_FINDINGS"
gated_runner_merge_findings_file "$_RU_HYGIENE_LINT_FINDINGS"
unset _RU_HYGIENE_LINT_FINDINGS

# Import-completeness lint: asserts that every third-party Python import
# across team/scripts/, team/pgai_agent_kanban/, and team/tests/ is
# declared in requirements.txt or requirements-test.txt.
echo "Running import-completeness lint..."
gated_runner_run_quality_lint "python_import_completeness" -- \
  python3 scripts/lint_python_import_completeness.py

# Dashboard network-git gate: asserts that no network-capable git verb
# (ls-remote, fetch, pull, push, clone) appears anywhere under
# team/scripts/dashboard/ — the dashboard render path must stay local-only.
# CONVERT-class per docs/blocking-site-census.md row 39: previously a
# standalone script with no call site in either gated runner; wired in here
# so a regression is filed as a finding instead of never surfacing at all.
echo "Running dashboard network-git gate..."
gated_runner_run_quality_lint "dashboard_network_git_gate" -- \
  python3 scripts/lint_dashboard_network_git_gate.py

# Dashboard hardcoded-color gate: asserts that no raw ANSI escape literal
# or tmux #[fg=...] markup literal appears anywhere under
# team/scripts/dashboard/ or team/scripts/lib/dashboard/ -- every color
# must be looked up by name through the single-source palette
# (team/config/default_palette.txt, team/scripts/lib/dashboard_palette.sh,
# pgai_agent_kanban.lib.palette) instead of a hand-transcribed copy.
# CONVERT-class per docs/blocking-site-census.md: a reintroduced literal
# renders correctly today (zero runtime-failure risk); the cost is
# exclusively to the single-source design's future drift resistance.
echo "Running dashboard hardcoded-color gate..."
gated_runner_run_quality_lint "dashboard_hardcode_colors" -- \
  python3 scripts/lint_dashboard_hardcode_colors.py

# HALT-scope-writers lint: reports a HALT-file creation site that targets
# the fleet-wide $KANBAN_ROOT/HALT sentinel from a function/script not
# itself named "global" -- the codebase's own self-documenting-scope
# convention (halt_global, GLOBAL_HALT_FILE, halt-global.sh). Catches a
# future reintroduction of the class of defect this lint was added to
# guard: a single-project autonomous repair touching the fleet-wide file
# and parking every sibling project's chain for a fault none of them has.
# CONVERT-class: a scope-naming regression is a maintainability defect, not
# a destructive or unusable-build failure, so it warns and never blocks.
echo "Running HALT-scope-writers lint..."
gated_runner_run_quality_lint "halt_scope_writers" -- \
  python3 scripts/lint_halt_scope_writers.py

# Raw-marker-read lint: asserts that the discovery pipeline's idle/RC-in-
# flight decision surface (scripts/lib/discovery_guards.sh,
# scripts/lib/discovery.sh) never extracts a queue-marker character to make
# a decision -- a queue marker is a rendering hint, not ground truth (SOP:
# "Per-Agent Queues and Marker Correctness"); the decision belongs to the
# named task's own status.md.  CONVERT-class: reports a finding and never
# blocks, because a stray marker-content test is a quality regression risk,
# not a runtime failure by itself.
echo "Running raw-marker-read lint..."
gated_runner_run_quality_lint "raw_marker_read" -- \
  python3 scripts/lint_raw_marker_read.py

# Status-writer-bypass lint: asserts that no agent-facing team/scripts/*.sh
# script mutates a task's ## State, ## Needs Human, ## Summary, or ##
# Blockers field directly (a bespoke re.sub/sed -i) instead of going
# through team/pgai_agent_kanban/task_status.py, the single sanctioned
# status writer.  CONVERT-class: reports a finding and never blocks,
# because a stray direct-mutation call site is a quality/maintainability
# regression risk, not a runtime failure by itself.
echo "Running status-writer-bypass lint..."
gated_runner_run_quality_lint "status_writer_bypass" -- \
  python3 scripts/lint_status_writer_bypass.py

# TESTER-report census lint: reports non-test team/ code outside
# pgai_agent_kanban/reports/tester_report.py that matches a report-parsing
# pattern (a finding or recommendation heading, a bold Systemic Risk / Fix
# Effort / Filed As / Regression label) -- a second reader of report.md
# beside the one parser module.  Exceptions are listed in
# team/config/tester-report-census-allowlist.txt.  CONVERT-class: always
# exits 0, so the wrapper only prints its narration in full; the
# per-finding entries come from its --findings-file, merged into the
# aggregate.
echo "Running TESTER-report census lint..."
_RU_TESTER_REPORT_CENSUS_FINDINGS="${PGAI_AGENT_KANBAN_TEMP_DIR}/lint/tester_report_census_unit.json"
mkdir -p "$(dirname "$_RU_TESTER_REPORT_CENSUS_FINDINGS")"
gated_runner_run_quality_lint "tester_report_census" -- \
  python3 scripts/lint_tester_report_census.py --findings-file "$_RU_TESTER_REPORT_CENSUS_FINDINGS"
gated_runner_merge_findings_file "$_RU_TESTER_REPORT_CENSUS_FINDINGS"
unset _RU_TESTER_REPORT_CENSUS_FINDINGS

# Docstring-coverage lint: reports every module without a module docstring
# and every public function, class or public method without a docstring
# under team/pgai_agent_kanban/, team/pm-agent/ and team/scripts/ (tests and
# fixtures excluded), plus every package add_argument call without help=.
# CONVERT-class: always exits 0, so the wrapper only prints its narration in
# full; the per-finding entries come from its --findings-file, merged into
# the aggregate.
echo "Running docstring-coverage lint..."
_RU_DOCSTRING_COVERAGE_FINDINGS="${PGAI_AGENT_KANBAN_TEMP_DIR}/lint/docstring_coverage_unit.json"
mkdir -p "$(dirname "$_RU_DOCSTRING_COVERAGE_FINDINGS")"
gated_runner_run_quality_lint "docstring_coverage" -- \
  python3 scripts/lint_docstring_coverage.py --findings-file "$_RU_DOCSTRING_COVERAGE_FINDINGS"
gated_runner_merge_findings_file "$_RU_DOCSTRING_COVERAGE_FINDINGS"
unset _RU_DOCSTRING_COVERAGE_FINDINGS

# Dual-channel error-contract pair lint: flags any fail-loud call site
# missing the error/message/remedy triple.  Always exits 0 by design; it
# already emits per-instance structured findings via --findings-file, so
# its output is merged directly into the aggregate rather than wrapped
# through gated_runner_run_quality_lint's coarse pass/fail shape.
echo "Running dual-channel error-contract pair lint..."
_RU_PAIR_LINT_FINDINGS="${PGAI_AGENT_KANBAN_TEMP_DIR}/lint/dual_channel_pair_unit.json"
mkdir -p "$(dirname "$_RU_PAIR_LINT_FINDINGS")"
python3 scripts/lint_dual_channel_pair.py --findings-file "$_RU_PAIR_LINT_FINDINGS"
gated_runner_merge_findings_file "$_RU_PAIR_LINT_FINDINGS"
unset _RU_PAIR_LINT_FINDINGS

# Runnable-remedy lint: flags any dual-channel call site whose remedy field
# still carries an angle-bracket placeholder ('<name>', '<sha>', ...) a
# stranger cannot paste into a shell and run as printed.  CONVERT-class
# (docs/blocking-site-census.md, runnable-remedy-placeholder-01): the
# dual-channel triple is already present and non-blank (lint_dual_channel_pair
# above already covers that), so a placeholder-laden remedy is a
# usability/quality regression, never a runtime failure -- wrapped through
# gated_runner_run_quality_lint so its own non-zero exit (unlike the two
# always-exit-0 lints above) never stops this runner.  Also emits a
# findings-file so per-instance detail (file, line, offending substring)
# merges into the aggregate exactly like the two lints above.
echo "Running runnable-remedy lint..."
_RU_REMEDY_LINT_FINDINGS="${PGAI_AGENT_KANBAN_TEMP_DIR}/lint/runnable_remedy_unit.json"
mkdir -p "$(dirname "$_RU_REMEDY_LINT_FINDINGS")"
gated_runner_run_quality_lint "runnable_remedy" -- \
  python3 scripts/lint_runnable_remedy.py --findings-file "$_RU_REMEDY_LINT_FINDINGS"
gated_runner_merge_findings_file "$_RU_REMEDY_LINT_FINDINGS"
unset _RU_REMEDY_LINT_FINDINGS

# Census-compliance lint: enforces the new-gate rule (roles/CODER.md,
# "Authoring a new gate") -- every gate/refusal site added to the
# census-documented loop-bearing surface must declare its classification
# comment and carry the dual-channel error contract.  Scoped by default to
# the exact files docs/blocking-site-census.md documents; pure operator-CLI
# utilities the census excludes are never scanned.  Always exits 0 by
# design (like the dual-channel-pair lint above); it already emits
# per-instance structured findings via --findings-file, so its output is
# merged directly into the aggregate rather than wrapped through
# gated_runner_run_quality_lint's coarse pass/fail shape -- that wrapper
# only records a finding when the underlying command's own exit code is
# non-zero, which this lint's exit code never is.
echo "Running census-compliance lint..."
_RU_CENSUS_LINT_FINDINGS="${PGAI_AGENT_KANBAN_TEMP_DIR}/lint/gate_census_compliance_unit.json"
mkdir -p "$(dirname "$_RU_CENSUS_LINT_FINDINGS")"
python3 scripts/lint_gate_census_compliance.py --findings-file "$_RU_CENSUS_LINT_FINDINGS"
gated_runner_merge_findings_file "$_RU_CENSUS_LINT_FINDINGS"
unset _RU_CENSUS_LINT_FINDINGS

# Block-record census lint: the census lint's sibling for block sites.
# Reports every BLOCKED state write in the shipped tree (set_state,
# transition_task_state, set_block State, a --section State --body BLOCKED
# render, or a Python "State"/"BLOCKED" write) with no block_record_write /
# write_block_record call beside it -- a block triage cannot classify.
# The status-writer tool records its own Block Record, so its BLOCKED calls
# are never reported.  CONVERT-class: always exits 0 and emits per-instance
# findings via --findings-file, merged directly into the aggregate like the
# census lint above.
echo "Running block-record census lint..."
_RU_BLOCK_RECORD_LINT_FINDINGS="${PGAI_AGENT_KANBAN_TEMP_DIR}/lint/block_record_sites_unit.json"
mkdir -p "$(dirname "$_RU_BLOCK_RECORD_LINT_FINDINGS")"
python3 scripts/lint_block_record_sites.py --findings-file "$_RU_BLOCK_RECORD_LINT_FINDINGS"
gated_runner_merge_findings_file "$_RU_BLOCK_RECORD_LINT_FINDINGS"
unset _RU_BLOCK_RECORD_LINT_FINDINGS

# Inline-Python ratchet lint: counts python3 heredocs and python3 -c
# programs in every non-test team/scripts/*.sh and compares each file with
# team/config/inline-python-allowlist.txt -- a file above its entry, an
# unlisted file carrying inline Python, an entry that should be tightened,
# and an unquoted <<PY delimiter are each reported.  CONVERT-class: always
# exits 0, so the wrapper only prints its narration in full; the per-finding
# entries come from its --findings-file, merged into the aggregate.
echo "Running inline-Python ratchet lint..."
_RU_INLINE_PYTHON_FINDINGS="${PGAI_AGENT_KANBAN_TEMP_DIR}/lint/inline_python_unit.json"
mkdir -p "$(dirname "$_RU_INLINE_PYTHON_FINDINGS")"
gated_runner_run_quality_lint "inline_python" -- \
  python3 scripts/lint_inline_python.py --findings-file "$_RU_INLINE_PYTHON_FINDINGS"
gated_runner_merge_findings_file "$_RU_INLINE_PYTHON_FINDINGS"
unset _RU_INLINE_PYTHON_FINDINGS

# Docker-path lint: every container-engine call in non-test team/ code must
# go through pgai_docker (team/scripts/lib/docker_use.sh), the one path to
# an engine and the writer of logs/docker-use.log; a direct docker/podman
# call is reported by file and line.  CONVERT-class: always exits 0; the
# per-finding entries come from its --findings-file.
echo "Running docker-path lint..."
_RU_DOCKER_PATH_FINDINGS="${PGAI_AGENT_KANBAN_TEMP_DIR}/lint/docker_helper_path_unit.json"
mkdir -p "$(dirname "$_RU_DOCKER_PATH_FINDINGS")"
gated_runner_run_quality_lint "docker_helper_path" -- \
  python3 scripts/lint_docker_helper_path.py --findings-file "$_RU_DOCKER_PATH_FINDINGS"
gated_runner_merge_findings_file "$_RU_DOCKER_PATH_FINDINGS"
unset _RU_DOCKER_PATH_FINDINGS

# Duplicated-Python ratchet lint: hashes every function body (AST-normalised)
# under team/pgai_agent_kanban, team/scripts and team/pm-agent, and checks a
# deny-list of helpers that may be defined only in their owning module
# (atomic write, path traversal, version parse, ISO parse, read_section,
# configparser instantiation, '## State' regexes).  Anything not recorded in
# team/config/python-duplicates-allowlist.txt, and any entry that should be
# tightened, is reported.  CONVERT-class: always exits 0, so the wrapper only
# prints its narration in full; the per-finding entries come from its
# --findings-file, merged into the aggregate.
echo "Running duplicated-Python ratchet lint..."
_RU_PYTHON_DUPLICATES_FINDINGS="${PGAI_AGENT_KANBAN_TEMP_DIR}/lint/python_duplicates_unit.json"
mkdir -p "$(dirname "$_RU_PYTHON_DUPLICATES_FINDINGS")"
gated_runner_run_quality_lint "python_duplicates" -- \
  python3 scripts/lint_python_duplicates.py --findings-file "$_RU_PYTHON_DUPLICATES_FINDINGS"
gated_runner_merge_findings_file "$_RU_PYTHON_DUPLICATES_FINDINGS"
unset _RU_PYTHON_DUPLICATES_FINDINGS

# API route / CLI shared-implementation lint: for every operator verb whose
# team/config/operator_verbs.manifest impl column is module:<function>, the
# REST route must call that function without starting a process and the CLI
# script must reach it through pp_run_ops; the number of impl shell rows may
# not rise above team/config/api-route-impl-baseline.txt.  CONVERT-class:
# always exits 0, so the wrapper only prints its narration in full; the
# per-finding entries come from its --findings-file, merged into the aggregate.
echo "Running API route implementation lint..."
_RU_API_ROUTE_IMPL_FINDINGS="${PGAI_AGENT_KANBAN_TEMP_DIR}/lint/api_route_impl_unit.json"
mkdir -p "$(dirname "$_RU_API_ROUTE_IMPL_FINDINGS")"
gated_runner_run_quality_lint "api_route_impl" -- \
  python3 scripts/lint_api_route_impl.py --findings-file "$_RU_API_ROUTE_IMPL_FINDINGS"
gated_runner_merge_findings_file "$_RU_API_ROUTE_IMPL_FINDINGS"
unset _RU_API_ROUTE_IMPL_FINDINGS

# API route documentation lint: every REST route the API declares must have
# a hand-written row (or heading) in docs/operator-api.md's Endpoint table
# section and a curl example whose URL matches it, and that page's
# starts-a-process list must match the census impl shell rows plus the
# dashboard pane route.  CONVERT-class (a sibling of the BLOCK-WORTHY
# API-parity gate, not part of it): always exits 0, so the wrapper only
# prints its narration in full; the per-finding entries come from its
# --findings-file, merged into the aggregate.
echo "Running API route documentation lint..."
_RU_API_ROUTE_DOCS_FINDINGS="${PGAI_AGENT_KANBAN_TEMP_DIR}/lint/api_route_docs_unit.json"
mkdir -p "$(dirname "$_RU_API_ROUTE_DOCS_FINDINGS")"
gated_runner_run_quality_lint "api_route_docs" -- \
  python3 scripts/lint_api_route_docs.py --findings-file "$_RU_API_ROUTE_DOCS_FINDINGS"
gated_runner_merge_findings_file "$_RU_API_ROUTE_DOCS_FINDINGS"
unset _RU_API_ROUTE_DOCS_FINDINGS

# echo/printf-into-quiet-grep ratchet lint: reports echo or printf output
# piped into grep -q / --quiet / --silent in non-test bash under team/ and
# install.sh -- under set -o pipefail that pipeline can report "no match"
# for text that matches -- against team/config/echo-pipe-grep-q-allowlist.txt
# (a file above its entry, an unlisted file carrying a site, an entry that
# should be tightened).  CONVERT-class: always exits 0, so the wrapper only
# prints its narration in full; the per-finding entries come from its
# --findings-file, merged into the aggregate.
echo "Running echo/printf-into-quiet-grep ratchet lint..."
_RU_ECHO_PIPE_GREP_Q_FINDINGS="${PGAI_AGENT_KANBAN_TEMP_DIR}/lint/echo_pipe_grep_q_unit.json"
mkdir -p "$(dirname "$_RU_ECHO_PIPE_GREP_Q_FINDINGS")"
gated_runner_run_quality_lint "echo_pipe_grep_q" -- \
  python3 scripts/lint_echo_pipe_grep_q.py --findings-file "$_RU_ECHO_PIPE_GREP_Q_FINDINGS"
gated_runner_merge_findings_file "$_RU_ECHO_PIPE_GREP_Q_FINDINGS"
unset _RU_ECHO_PIPE_GREP_Q_FINDINGS

# Governance byte-budget check: narrates each of the seven roles' assembled
# governance reading size against its configured budget_bytes_<role> in
# kanban.cfg_example [governance] -- a soft size heuristic, not evidence of
# damage (team/SOP-core.md "Blocking Severity: only real damage blocks"),
# so it always exits 0 (see the lint's own "WHY IT NEVER BLOCKS" header)
# and is wrapped through gated_runner_run_quality_lint purely so its table
# and any over-budget narration line print in full regardless of outcome.
# The wrapper alone records nothing, because it only files a finding on a
# non-zero exit and this lint never produces one -- the findings entry
# instead comes from the lint's own --findings-file mode, merged the same
# way the dual-channel-pair and census-compliance lints above are.
echo "Running governance byte-budget check..."
_RU_GOVERNANCE_BUDGET_FINDINGS="${PGAI_AGENT_KANBAN_TEMP_DIR}/lint/governance_byte_budget_unit.json"
mkdir -p "$(dirname "$_RU_GOVERNANCE_BUDGET_FINDINGS")"
gated_runner_run_quality_lint "governance_byte_budget" -- \
  bash scripts/lint_governance_byte_budgets.sh \
    --kanban-cfg "${REPO_ROOT}/kanban.cfg_example" \
    --findings-file "$_RU_GOVERNANCE_BUDGET_FINDINGS"
gated_runner_merge_findings_file "$_RU_GOVERNANCE_BUDGET_FINDINGS"
unset _RU_GOVERNANCE_BUDGET_FINDINGS

# Config-key coverage lint: cross-checks the kanban.cfg and project.cfg keys
# the code reads (read_ini, _pp_read_cfg_key, config_get, and Python read
# call sites) against the keys kanban.cfg_example, the project.cfg template
# and the project.cfg create-project.sh writes scaffold and the keys the two
# Key Registries accept, and reports unscaffolded and unread keys, stated
# defaults that disagree with the registries, keys under the wrong section,
# commented lines that do not show the default, and keys read only into an
# export nothing consumes.  CONVERT-class: such a key costs discoverability,
# never a running system (every reader falls back to its default), so it
# always exits 0; the wrapper only prints its narration in full and the
# per-finding entries come from its --findings-file, merged into the
# aggregate.
echo "Running config-key coverage lint..."
_RU_CONFIG_KEY_COVERAGE_FINDINGS="${PGAI_AGENT_KANBAN_TEMP_DIR}/lint/config_key_coverage_unit.json"
mkdir -p "$(dirname "$_RU_CONFIG_KEY_COVERAGE_FINDINGS")"
gated_runner_run_quality_lint "config_key_coverage" -- \
  python3 scripts/lint_config_key_coverage.py --findings-file "$_RU_CONFIG_KEY_COVERAGE_FINDINGS"
gated_runner_merge_findings_file "$_RU_CONFIG_KEY_COVERAGE_FINDINGS"
unset _RU_CONFIG_KEY_COVERAGE_FINDINGS

# Docs-citation manifest lint: asserts that every docs/<path>.md citation
# reachable from this kanban's governance files (the four flat root files
# plus roles/*.md, and their own transitive citations) is named in
# team/config/docs-manifest.txt -- the single source install.sh and
# team/scripts/upgrade.sh both read to deliver docs/ onto an installed or
# upgraded root. Runs directly against the checked-out source tree in
# seconds -- no install, no subprocess -- reusing the same citation regex
# and closure walk (pgai_agent_kanban.lib.docs_citation_scan) that
# team/tests/unit/test_installed_governance_docs_citations_resolve.py
# already proves against a real installed/upgraded root; that test remains
# the release-time backstop, and this lint is the authoring-time signal
# that catches the same omission in seconds instead of at RC time.
# CONVERT-class (team/SOP.md "Gates and Refusals"): a dangling citation
# carries zero runtime-failure risk until an operator actually installs or
# upgrades, so this warns, records, and continues -- it never blocks the
# suite.
echo "Running docs-citation manifest lint..."
gated_runner_run_quality_lint "docs_citation_manifest" -- \
  python3 scripts/check_docs_citations.py

fi
# --- end of quality-class block ---

# --- Per-file test-authoring lints over the named files (targeted run only) ---
# The two quality-class lints that judge one test file at a time run on a
# targeted run too, restricted to exactly the validated --files paths: no
# directory walk, no runtime-script scan, so the cost scales with the files
# named. Output prints in full here; nothing is recorded into the findings
# file or the ledger. The verdict that turns a finding into this run's exit
# code sits after the pytest verdict, below.
if [[ "$_RU_TARGETED" == "true" ]]; then
  _RU_TARGETED_ABS=()
  for _ru_rel in "${_RU_TARGETED_RESOLVED[@]}"; do
    _RU_TARGETED_ABS+=("${REPO_ROOT}/team/${_ru_rel}")
  done
  unset _ru_rel
  gated_runner_targeted_file_lints \
    "${REPO_ROOT}/team/scripts/lint_test_anti_patterns.py" \
    "${SCRIPT_DIR}/lint_skip_bug_gate.sh" \
    "${_RU_TARGETED_ABS[@]}"
fi

# --- Aggregate quality-class findings-file write is already registered ---
# _ru_write_findings_on_exit and CLEANUP_ON_EXIT_PRE_HOOK were installed at
# the top of this script, immediately after claiming the findings file --
# see that comment for the full ordering rationale. GATED_RUNNER_FINDINGS
# now holds every finding this invocation records before pytest; the
# CLEANUP_ON_EXIT_PRE_HOOK fires on every exit path from here forward --
# the three post-suite checks' own findings (recorded before the pytest
# verdict is acted on), the pytest-failure `exit 1` that follows them, and
# the normal `exit 0` at the bottom -- with exactly one write site instead
# of one per exit path. See
# team/scripts/lib/gated_runner_lint.sh for the findings-file schema.

# --- Scrub the git-boundary-guard's own exported variables before pytest ---
# The wake layer pins GIT_GUARD_REAL_GIT (team/scripts/lib/
# wake_guard_wiring.sh) and the shim (team/scripts/wake/git-guard/git) sets
# PGAI_GIT_GUARD_DEPTH, both exported into every non-CM agent's environment
# for the rest of that agent's session -- including this runner's own
# process, since it is invoked from inside that same session rather than a
# fresh shell. test_git_boundary_guard.py deliberately places its own
# PATH-shadowing stub "git" ahead of everything else and asserts on what the
# shim does with THAT stub; a pinned GIT_GUARD_REAL_GIT from this runner's
# own ambient environment outranks the stub's PATH position (the shim reads
# GIT_GUARD_REAL_GIT first, by design, so a real task's git calls keep
# resolving the one true binary no matter how a later PATH entry shuffles),
# and a leaked PGAI_GIT_GUARD_DEPTH short-circuits the recursion-sentinel
# test the same way. Unsetting both here -- always, whether or not this
# session is one the wake layer actually guarded -- is what makes the suite's
# pass/fail independent of whatever session launched this script, which is
# the property every other CODER/WRITER ticket's "exits 0" acceptance
# criterion depends on. Must happen before gated_runner_replay_command is
# called, below, so an unset variable is classified as absent (`-u NAME`)
# in the printed replay line rather than carried through as a stale value.
unset GIT_GUARD_REAL_GIT PGAI_GIT_GUARD_DEPTH

# --- Scrub the wake's exported [wake] tunables before pytest ---
# A wake's own load_config exports every [wake] tunable it resolves
# (CONFIG_LOADER_WAKE_ENV_NAMES, team/scripts/lib/config_loader.sh) into
# every agent it dispatches, so this runner inherits them whenever it runs
# inside an agent wake. load_config gives a set variable precedence over
# the cfg value -- that is the operator override -- so a test that builds
# a sandboxed kanban.cfg pinning, say, lock_wait_seconds = 0 would see its
# wake obey the inherited LOCK_WAIT_SECONDS instead, and wait where the
# test expects an immediate deferral. Unsetting every one of these names
# here makes the run behave as it does from a plain operator shell, where
# none of them is set. Registering them in the replay family makes the
# "Replay this exact run:" line below carry an `env -u` for each, so a
# paste into a wake-exported shell replays the scrubbed environment. Kept
# identical in run-unit-tests.sh and run-integration-tests.sh.
unset "${CONFIG_LOADER_WAKE_ENV_NAMES[@]}"
GATED_RUNNER_REPLAY_ENV_NAMES+=("${CONFIG_LOADER_WAKE_ENV_NAMES[@]}")

# --- Pre-merge run: hand over to the pre-merge engine ---
# The environment is now what any pytest run of this script gets (temp
# root exported, crontab seam engaged, git-guard and [wake] names scrubbed),
# and the engine's own pytest runs inherit it. The engine plans its pytest
# lanes with the same planner as this script, so no more than the worker
# budget's pytest workers run at once. It prints its narration and summary
# block on stdout, and its dual-channel error on stderr when a check fails
# or it cannot start. The run ends here: no lane planning, green ledger or
# post-suite check below applies to it, and the exit hook writes
# pre-merge.exit. pp_run_ops puts this tree's own team/ -- the tree --repo
# names -- ahead of KANBAN_ROOT on PYTHONPATH, and the cd makes team/ the
# interpreter's first sys.path entry, so the engine imports the package
# under check, never the live install, whatever PYTHONPATH arrived with.
if [[ "$_RU_PRE_MERGE" == "true" ]]; then
  _RU_PRE_MERGE_WORKERS="$_RU_WORKERS"
  if [[ -n "$REQUESTED_WORKERS" ]] && (( 10#$REQUESTED_WORKERS < _RU_PRE_MERGE_WORKERS )); then
    _RU_PRE_MERGE_WORKERS=$(( 10#$REQUESTED_WORKERS ))
  fi
  _RU_PRE_MERGE_ARGV=(--repo "$REPO_ROOT" --suite unit --cap "$_RU_PRE_MERGE_CAP" --workers "$_RU_PRE_MERGE_WORKERS")
  [[ -n "$PRE_MERGE_BASE" ]] && _RU_PRE_MERGE_ARGV+=(--base "$PRE_MERGE_BASE")
  echo "Pre-merge checks: the by-path module test, the five ratchet lints and the tracked-files lint, the always-run whole-tree guards (env-bootstrap, operator-verb-docs, dashboard-window-docs, docs-citations), the affected unit tests."
  echo "Pre-merge base: ${PRE_MERGE_BASE:-default (the local RC branch this worktree was cut from)}; cap ${_RU_PRE_MERGE_CAP} (kanban.cfg [testing] pre_merge_affected_cap; 0 = no cap); workers ${_RU_PRE_MERGE_WORKERS} (kanban.cfg [testing] workers${REQUESTED_WORKERS:+, lowered by --workers})."
  # shellcheck source=lib/pp_run_ops.sh
  source "${SCRIPT_DIR}/lib/pp_run_ops.sh"
  cd "$REPO_ROOT/team"
  set +e
  pp_run_ops pgai_agent_kanban.testing.pre_merge "${_RU_PRE_MERGE_ARGV[@]}"
  _RU_PRE_MERGE_RC=$?
  set -e
  if [[ $_RU_PRE_MERGE_RC -eq 0 ]]; then
    echo "Pre-merge checks PASSED"
  else
    # AGENT-RESOLVABLE within a ticket, CONVERT with respect to a release:
    # the change is fixed and the run repeated in the same session, and
    # nothing in the release path reads pre-merge.exit. The engine has
    # already printed the summary naming each failure and the dual-channel
    # error/message/remedy on stderr; this passes its exit code through.
    echo "Pre-merge checks FAILED (exit code: ${_RU_PRE_MERGE_RC}); the summary above names each failure." >&2
  fi
  exit "$_RU_PRE_MERGE_RC"
fi

# --- Plan the pytest lanes and the coverage decision ---
# gated_runner_plan_lanes (team/scripts/lib/gated_runner_lanes.sh) hands
# PYTEST_ARGS to the lane planner, team/scripts/lib/gated_runner_lanes.py,
# which narrates the worker and coverage decisions and returns the pytest
# invocations to run in order: "parallel" (-n <workers> --dist loadfile,
# tests not marked serial) then "serial" (serial-marked tests, one
# process), or one "single" lane whose argv is PYTEST_ARGS unchanged.
# Coverage options are added to the lanes only when --coverage was given
# and is allowed (header, "Coverage is opt-in"); COVERAGE is lowered to
# false when it is not, so the green-ledger block below sees the effective
# decision. Planned here, after the quality-class lints, so the narration
# sits next to the pytest output it describes.
_RU_PLAN_OPTS=(
  --suite unit
  --workers "$_RU_WORKERS"
  --coverage-config "$_RU_COVERAGE_CONFIG"
  --coverage-dir "${PGAI_AGENT_KANBAN_TEMP_DIR}/coverage"
  --durations-dir "${PGAI_AGENT_KANBAN_TEMP_DIR}/durations/unit.$$"
)
if [[ -n "$REQUESTED_WORKERS" ]]; then
  _RU_PLAN_OPTS+=(--requested-workers "$REQUESTED_WORKERS")
fi
if [[ "$_RU_TARGETED" == "true" ]]; then
  _RU_PLAN_OPTS+=(--targeted)
fi
if [[ "$QUARANTINED" == "true" ]]; then
  _RU_PLAN_OPTS+=(--quarantined)
fi
if [[ "$COVERAGE" == "true" ]]; then
  _RU_PLAN_OPTS+=(--coverage-requested)
fi
gated_runner_plan_lanes "${_RU_PLAN_OPTS[@]}" -- "${PYTEST_ARGS[@]}"
if [[ -n "$GATED_RUNNER_COVERAGE_DIR" ]]; then
  mkdir -p "$GATED_RUNNER_COVERAGE_DIR"
else
  COVERAGE=false
fi
# A certification run records its slowest tests (informational only): the
# planner gave every lane --durations=25 --durations-min=1.0 and a JUnit
# report in this directory; gated_runner_record_durations merges them into
# lint/durations-unit.json once the last lane is done. A --files or
# --quarantined run records nothing.
[[ -n "$GATED_RUNNER_DURATIONS_DIR" ]] && mkdir -p "$GATED_RUNNER_DURATIONS_DIR"
unset _RU_PLAN_OPTS

# --- Green ledger: prepare this run's record, and --reuse-if-green ---
# team/scripts/lib/green_ledger.sh owns the ledger, its location and the
# reuse rule. Prepared here -- after the quality-class lints, so the
# findings hash covers them, and before pytest -- on certification runs
# only; a --files run never reads or writes the ledger. A reuse exits 0
# through the normal exit hook, so the findings file is still written for
# TESTER. Ledger problems narrate one line and fall through to pytest.
if [[ "$_RU_TARGETED" == "true" ]]; then
  if [[ "$REUSE_IF_GREEN" == "true" ]]; then
    echo "[green-ledger] unit: --reuse-if-green ignored on a --files run (a targeted run never reads or writes the ledger)."
  fi
elif declare -F green_ledger_prepare >/dev/null 2>&1; then
  green_ledger_prepare unit "$REPO_ROOT" "$_RU_MARKER_EXPRESSION"
  if [[ "$REUSE_IF_GREEN" == "true" ]]; then
    if [[ "$COVERAGE" == "true" ]]; then
      echo "[green-ledger] unit: --coverage asks for a fresh coverage report, which a reused result cannot provide; running the unit suite normally."
    elif green_ledger_try_reuse; then
      echo "Unit tests PASSED (reused green ledger result; pytest not started)"
      exit 0
    fi
  fi
elif [[ "$REUSE_IF_GREEN" == "true" ]]; then
  echo "[green-ledger] unit: team/scripts/lib/green_ledger.sh is not available; running the unit suite normally."
fi

# --- Run tests ---
if [[ "$_RU_TARGETED" == "true" ]]; then
  echo "Running targeted unit tests in $(pwd): ${_RU_TARGETED_RESOLVED[*]}"
else
echo "Running unit tests in $(pwd)/tests/ (unit/ + cm module tests)"
fi
echo "Test order: stated (pytest-randomly neutralized via -p no:randomly)."
if [[ "$QUARANTINED" == "true" ]]; then
  echo "Marker selection: -m \"${_RU_MARKER_EXPRESSION}\" (quarantined tests only -- not a certification run of the suite)."
else
  echo "Marker selection: -m \"${_RU_MARKER_EXPRESSION}\" (quarantined tests excluded; --quarantined runs them)."
fi
# The replay line carries this runner's own PYTEST_ADDOPTS/PYTHONHASHSEED/
# PGAI_*/KANBAN_ROOT state (via gated_runner_replay_command, team/scripts/
# lib/gated_runner_lint.sh) rather than only the pytest argv -- pasting the
# bare argv into a shell that ambiently exports one of these differently
# (KANBAN_ROOT from a sourced shell-env, most notably) replays a materially
# different run than the one this script executed.
# The argv is shell-quoted (gated_runner_quote_argv) so the multi-word -m
# expression pastes back as the single argument pytest received.
# Each planned lane runs in order, each printing its own replay line just
# before its pytest starts; their exit codes combine into PYTEST_EXIT
# (gated_runner_combine_lane_exits), so everything below sees one result.
_RU_LANE_EXITS=()
_RU_LANE_TOTAL=${#GATED_RUNNER_LANE_NAMES[@]}
for _ru_lane in "${!GATED_RUNNER_LANE_NAMES[@]}"; do
  gated_runner_lane_args "$_ru_lane"
  if (( _RU_LANE_TOTAL > 1 )); then
    echo "Lane $((_ru_lane + 1)) of ${_RU_LANE_TOTAL}: ${GATED_RUNNER_LANE_NAMES[_ru_lane]}"
  fi
  echo "Replay this exact run: (cd \"$(pwd)\" && $(gated_runner_replay_command "python3 -m pytest $(gated_runner_quote_argv "${GATED_RUNNER_LANE_ARGS[@]}")"))"
  set +e
  python3 -m pytest "${GATED_RUNNER_LANE_ARGS[@]}"
  _RU_LANE_EXITS+=("$?")
  set -e
done
PYTEST_EXIT="$(gated_runner_combine_lane_exits "${_RU_LANE_EXITS[@]}")"
gated_runner_record_durations unit "${PGAI_AGENT_KANBAN_TEMP_DIR}/lint/durations-unit.json"
if (( _RU_LANE_TOTAL > 1 )); then
  echo "Lane exit codes: ${_RU_LANE_EXITS[*]} (in lane order) -> combined exit code ${PYTEST_EXIT}"
fi
unset _ru_lane _RU_LANE_TOTAL
# pytest exit 5 ("no tests ran") on a targeted or --quarantined run means
# the quarantine marker selection deselected every test the run named --
# a file whose tests are all quarantined, or a --quarantined run over files
# holding none. Nothing failed, so it is narrated and treated as a pass. On
# a full default run exit 5 would mean the suite collected nothing at all,
# which stays a failure.
if [[ $PYTEST_EXIT -eq 5 && ( "$_RU_TARGETED" == "true" || "$QUARANTINED" == "true" ) ]]; then
  echo "Unit tests: no test matched the marker selection -m \"${_RU_MARKER_EXPRESSION}\" (pytest exit code 5) -- treated as a pass; nothing ran, nothing failed."
  PYTEST_EXIT=0
fi

# --- Post-suite checks bracket the whole pytest session, whatever its exit code ---
# The three report-only checks below (bare-/tmp residue, framework-rooted
# TCP listeners, live-root sentinel set) compare against the snapshots
# taken before pytest launched, and they run BEFORE the pytest verdict is
# acted on so a red suite is checked exactly like a green one -- a stray
# fixture that reaches the live root is most likely on a failing run, and
# skipping the checks there is how a leaked or removed HALT goes
# unreported. Each is CONVERT-class (warn, file, continue): whatever it
# finds, the exit code that follows is still pytest's own.

# --- Post-suite /tmp cleanliness assertion (quality-class: warn, file, continue) ---
# CONVERT-class per docs/blocking-site-census.md row 52: previously a hard
# `exit 1` on residue detection.  Wrapped through gated_runner_run_quality_lint
# so a leak is recorded as a non-blocking finding instead of aborting the
# runner — pytest's own PYTEST_EXIT verdict below is the only fatal exit
# left in this script.  Runs whatever pytest's exit code was (see the
# bracket comment above), so a red suite is checked exactly like a green
# one.  The check uses the snapshot taken before the suite ran
# (_TMP_SNAPSHOT_FILE).
_run_unit_bare_tmp_cleanliness_check() {
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
gated_runner_run_quality_lint "bare_tmp_cleanliness" -- _run_unit_bare_tmp_cleanliness_check

# --- Post-suite listener cleanliness assertion (quality-class: warn, file, continue) ---
# CONVERT-class per docs/blocking-site-census.md row 53: previously a hard
# `exit 1` when a framework-rooted TCP listener survived the suite.  Same
# non-blocking treatment as the cleanliness check above.  Only listeners
# rooted in this project's own temp subtree are flagged; sibling projects'
# leaked listeners are not attributed to this runner.  The subtree was
# resolved once, before pytest launched (_RUT_PROJECT_TEMP_SUBTREE), so the
# pre-suite snapshot and this check agree on the same scope;
# PGAI_PROJECT_TEMP_SUBTREE is exported here — after pytest has already
# exited — so callers/tests that read the env var directly still see it,
# without pytest itself (or fixtures it spawns) ever inheriting a narrowed
# scope.  Listeners already present in the pre-suite snapshot
# (_LISTENER_SNAPSHOT_FILE) are pre-existing orphans and are not flagged;
# only a listener first observed after that snapshot — i.e. spawned by this
# run — is recorded as a finding.
export PGAI_PROJECT_TEMP_SUBTREE="${_RUT_PROJECT_TEMP_SUBTREE}"
_run_unit_listener_cleanliness_check() {
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
gated_runner_run_quality_lint "listener_cleanliness" -- _run_unit_listener_cleanliness_check

# --- Post-suite sentinel leak assertion (quality-class: warn, file, continue) ---
# CONVERT-class per docs/blocking-site-census.md ("Sentinel leak assertion"
# row): a HALT / HALT-AFTER / DEFER-LOW-BUGS file the suite added to, changed
# on, or removed from the LIVE kanban root is recorded as a finding and left
# in place -- only the operator removes a HALT file, and the evidence is the
# file itself. Compared against the pre-suite snapshot (_SENTINEL_SNAPSHOT_FILE)
# so a sentinel the operator armed before the run is never attributed to it.
# Runs on every pytest outcome: a fixture that reaches the live root is
# most likely on a red run, so the snapshot and this comparison bracket
# the whole session rather than only a passing one.
_run_unit_sentinel_leak_check() {
  if ! pgai_sentinel_leak_check "${_SENTINEL_LIVE_ROOT}" "${_SENTINEL_SNAPSHOT_FILE}"; then
    # Non-deterministic (exempt) per docs/coding-standards.md — which test
    # resolved the live root instead of its synthetic board is not computable
    # at detection time; pgai_sentinel_leak_check already printed each path.
    echo "Next diagnostic step: the '[pgai-sentinel]' lines above name each added," >&2
    echo "changed or removed sentinel; find the fixture that armed or cleared it" >&2
    echo "with PGAI_AGENT_KANBAN_ROOT_PATH / KANBAN_ROOT still pointing at the live" >&2
    echo "install rather than its own synthetic board, and restore the file by hand." >&2
    return 1
  fi
}
echo "Checking the live kanban root for leaked HALT / HALT-AFTER / DEFER-LOW-BUGS files..."
gated_runner_run_quality_lint "sentinel_leak" -- _run_unit_sentinel_leak_check

# --- Pytest verdict ---
# Acted on only after the post-suite checks above have run and recorded
# their findings; the exit code is pytest's, unchanged by any of them.
if [[ $PYTEST_EXIT -ne 0 ]]; then
  # Non-deterministic (exempt) per docs/coding-standards.md "The
  # non-deterministic exemption" — a real test/assertion failure has no
  # verbatim fix; point at the evidence pytest already printed above.
  echo "Unit tests FAILED (exit code: $PYTEST_EXIT)" >&2
  echo "Next diagnostic step: scroll up to the pytest output directly above —" >&2
  echo "its short-test-summary 'FAILED <file>::<test>' lines name the specific" >&2
  echo "test file and assertion that failed. Read that test's traceback for the" >&2
  echo "actual defect; there is no command that fixes a real test failure." >&2
  # BLOCK-WORTHY (team/SOP-core.md "Gates and Refusals" -- "Unusable build
  # blocks; an unavailable build environment does not"): a failing pytest
  # run means the suite validated nothing, so this is verification unable
  # to run to a trustworthy result -- a real stop, not a quality-class
  # finding. Kept outside the dual-channel emitter because the
  # non-deterministic exemption above already applies -- there is no
  # verbatim remedy to carry in the emitter's runnable-remedy channel, so
  # this site is tracked via the census Locator Index instead.
  # CENSUS-ANCHOR: run-unit-tests-pytest-failure-01
  exit 1
fi

# --- Targeted run: per-file lint verdict ---
# Every named test passed; a finding from the per-file lints above still
# fails the targeted run, so the author fixes it in the same session. The
# remedy re-runs exactly the lint(s) that reported, over the named files.
if [[ "$_RU_TARGETED" == "true" && ${#GATED_RUNNER_TARGETED_LINT_FAILURES[@]} -gt 0 ]]; then
  _ru_lint_remedy="$(printf '%s && ' "${GATED_RUNNER_TARGETED_LINT_COMMANDS[@]}")"
  # CENSUS-ANCHOR: run-unit-tests-targeted-files-05
  # AGENT-RESOLVABLE: the author fixes the reported lines (or adds the lint's per-instance opt-out marker) and re-runs.
  pgai_emit_error_contract "run-unit-tests.targeted-file-lint-findings" \
    "the named test files passed, but per-file test-authoring lint(s) reported findings in them: ${GATED_RUNNER_TARGETED_LINT_FAILURES[*]}. Each finding is printed above, naming file and line; fix those lines or add the lint's per-instance opt-out marker with a justification, then re-run this targeted run." \
    "cd \"${REPO_ROOT}/team\" && ${_ru_lint_remedy% && }"
  exit 1
fi

echo "Unit tests PASSED"

# --- Post-suite scratch-tests sweep (quality-class: warn, file, continue) ---
# CONVERT-class per docs/blocking-site-census.md ("Post-suite scratch-tests
# sweep" row): an aborted or crashed earlier run can leave a read-only
# (0555) leftover directory under ${PGAI_AGENT_KANBAN_TEMP_DIR}/tests,
# and pgai_temp_cleanup_tests's bare `rm -rf` used to abort under this
# script's `set -euo pipefail` on that permission error, turning a purely
# cosmetic housekeeping failure into this runner's own exit status even
# though every test passed and no lint was fatal. pgai_temp_cleanup_tests
# now recovers permissions itself (team/scripts/lib/temp.sh); wrapping the
# call through gated_runner_run_quality_lint, exactly like the two checks
# above, means that even a sweep problem it still can't recover from is
# recorded as a non-blocking finding instead of aborting the runner. Calls
# pgai_temp_cleanup_tests, which removes ONLY the tests/ subtree under
# ${PGAI_AGENT_KANBAN_TEMP_DIR} -- the scoped helper avoids wiping live agent
# session files (e.g. claude-<session>/ directories) that reside elsewhere in
# the temp root. Run only on success so temp files are available for
# debugging failed tests. --no-scratch-sweep leaves the sweep to the caller
# (header, "Scratch sweep").
# CENSUS-ANCHOR: run-unit-tests-scratch-sweep-01
if [[ "$NO_SCRATCH_SWEEP" != "true" && -f "${SCRIPT_DIR}/lib/temp.sh" ]]; then
    # shellcheck source=lib/temp.sh
    source "${SCRIPT_DIR}/lib/temp.sh"
    echo "Sweeping post-suite scratch under \${PGAI_AGENT_KANBAN_TEMP_DIR}/tests..."
    gated_runner_run_quality_lint "scratch_sweep" -- pgai_temp_cleanup_tests
fi
if [[ "$NO_SCRATCH_SWEEP" == "true" ]]; then
    echo "Post-suite scratch sweep of \${PGAI_AGENT_KANBAN_TEMP_DIR}/tests left to the caller (--no-scratch-sweep)."
fi

# --- Aggregate quality-class findings file ---
# Written by the CLEANUP_ON_EXIT_PRE_HOOK registered above, which fires on
# every exit path this script can take from that point forward (including
# this normal fall-through). No explicit write call belongs here — see the
# hook registration comment above the "Run tests" section for the rationale.

exit 0
