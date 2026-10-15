#!/usr/bin/env bash
# run-gated-suites.sh
# Run both gated test suites -- run-unit-tests.sh and run-integration-tests.sh,
# the two runners beside this script -- from one command, splitting the
# kanban.cfg [testing] workers budget between them.
#
# Usage:
#   run-gated-suites.sh [--coverage] [--verbose] [--reuse-if-green] [--out-dir DIR]
#   run-gated-suites.sh --help
#
# Worker budget: [testing] workers (config_testing_workers in
# team/scripts/lib/config_loader.sh; default 3) is the most pytest workers
# the two suites may have alive between them.
#   workers 2 or more -- both suites start at the same time. The unit suite
#       runs with --workers <workers - 1> and the integration suite with
#       --workers 1, so the two add up to exactly the budget and each gets
#       at least 1.
#   workers 0 or 1 -- the unit suite runs, then the integration suite, each
#       with --workers <workers>, i.e. single-process. Two suites cannot each
#       have a worker inside a budget of 1, so they take turns.
# A runner given --workers N uses the smaller of N and [testing] workers, so
# neither child can go over its share. The plan is made by
# team/scripts/lib/gated_suites.py; its module docstring is the full rule.
#
# Evidence: each suite writes what it writes when run on its own -- its
# findings file at ${PGAI_AGENT_KANBAN_TEMP_DIR}/lint/findings-<suite>.json,
# its green-ledger record, its coverage report when coverage is honoured.
# This script adds, per suite, under --out-dir (default
# ${PGAI_AGENT_KANBAN_TEMP_DIR}):
#   <suite>.log   the runner's stdout and stderr, as `runner > log 2>&1`
#   <suite>.exit  one line, the runner's exit code (written as soon as that
#                 runner exits; removed at start so a stale one is never read)
# where <suite> is unit or integration.
#
# Running together: concurrent children get --no-scratch-sweep (a green
# runner otherwise removes ${PGAI_AGENT_KANBAN_TEMP_DIR}/tests, which the
# other suite may still be using); this script sweeps once after both exit
# 0. With --coverage they also get a COVERAGE_FILE each, so the two suites'
# pytest-cov data files never mix. The post-suite bare-/tmp and TCP-listener
# checks in each runner can still report a file or listener the other suite
# was holding when the first one finished; those checks are quality-class
# findings and never change an exit code.
#
# Coverage: --coverage is passed to both runners unchanged; each runner's
# own gate decides whether it is honoured ([testing] coverage = true, or
# PGAI_TESTING_COVERAGE=1 outside an agent wake). This script adds no other
# way to turn it on.
#
# Exit codes:
#   0  both suites exited 0
#   N  otherwise the first non-zero suite exit code, unit first, then
#      integration (1 = test failure, 2 = pytest not installed -- see each
#      runner's --help)
#   1  also: an unrecognized argument, a refused [testing] workers value, a
#      missing runner, or an --out-dir that cannot be written; no suite ran
#
# Configuration:
#   PGAI_AGENT_KANBAN_ROOT_PATH  -- kanban root holding kanban.cfg (default: $HOME/pgai_agent_kanban)
#   PGAI_AGENT_KANBAN_TEMP_DIR   -- framework temp root (default: what pgai_temp_dir in lib/temp.sh resolves)

# --- Bootstrap: self-locate -> source shell-env -> fail loud ---
# shellcheck source=lib/env_bootstrap.sh
# BLOCK-WORTHY: an unreachable bootstrap prelude leaves the kanban root and
# temp root undefined, so neither the budget nor the evidence paths resolve.
# CENSUS-ANCHOR: run-gated-suites-env-bootstrap-01
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh" || exit 1

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEAM_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"

# shellcheck source=lib/temp.sh
source "${SCRIPT_DIR}/lib/temp.sh"
# shellcheck source=lib/error_contract.sh
source "${SCRIPT_DIR}/lib/error_contract.sh"
# shellcheck source=lib/config_loader.sh
source "${SCRIPT_DIR}/lib/config_loader.sh"

set -euo pipefail

_RGS_PLANNER="${SCRIPT_DIR}/lib/gated_suites.py"
_RGS_EXAMPLE_OUT_DIR="$(pgai_temp_dir)/tester"

# --- Parse arguments ---
COVERAGE=false
VERBOSE=false
REUSE_IF_GREEN=false
OUT_DIR=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --coverage)
      COVERAGE=true
      shift
      ;;
    --verbose|-v)
      VERBOSE=true
      shift
      ;;
    --reuse-if-green)
      REUSE_IF_GREEN=true
      shift
      ;;
    --out-dir)
      if [[ -z "${2:-}" || "${2:-}" == -* ]]; then
        # CENSUS-ANCHOR: run-gated-suites-args-02
        # AGENT-RESOLVABLE: the caller names a directory after --out-dir and re-runs.
        pgai_emit_error_contract "run-gated-suites.out-dir-missing" \
          "--out-dir needs a directory after it (got '${2:-}'); the per-suite .exit and .log files are written there." \
          "bash \"${BASH_SOURCE[0]}\" --out-dir ${_RGS_EXAMPLE_OUT_DIR}"
        exit 1
      fi
      OUT_DIR="$2"
      shift 2
      ;;
    --help|-h)
      cat <<EOF
Usage: $(basename "$0") [--coverage] [--verbose] [--reuse-if-green] [--out-dir DIR]

Run both gated test suites, run-unit-tests.sh and run-integration-tests.sh,
from one command, inside one kanban.cfg [testing] workers budget (default 3).

  workers 2 or more  Both suites start at the same time: unit with
                     --workers <workers - 1>, integration with --workers 1.
                     The two add up to exactly the budget.
  workers 0 or 1     Unit runs, then integration, one after the other, each
                     single-process -- two suites cannot each have a worker
                     inside a budget of 1.

Each suite writes its own findings file, lint/findings-<suite>.json under
the temp root, exactly as when it runs on its own. This script also writes,
per suite, into the output directory:
  <suite>.log    the runner's stdout and stderr
  <suite>.exit   one line: the runner's exit code
(<suite> is unit or integration). Both suites always run to the end, even
when one fails.

When the suites run at the same time, each gets --no-scratch-sweep and this
script removes <temp-root>/tests once, after both pass; with --coverage each
also gets its own COVERAGE_FILE so the two suites' coverage data never mix.
A post-suite bare-/tmp or TCP-listener check in one suite can report
something the other suite still held; those are quality-class findings and
never change an exit code.

Targeted runs are not offered here: use run-unit-tests.sh --files or
run-integration-tests.sh --files.

Options:
  --coverage        Passed to both runners. Each runner's own gate decides
                    whether coverage is collected: honoured only when
                    kanban.cfg [testing] coverage = true, or when
                    PGAI_TESTING_COVERAGE=1 is set in the operator's own
                    shell outside an agent wake. Otherwise each runner
                    prints "coverage disabled by config ([testing]
                    coverage)" and runs without it.
  --verbose, -v     Passed to both runners (pytest --verbose).
  --reuse-if-green  Passed to both runners: each skips pytest when its
                    per-RC green ledger already holds a green run of this
                    exact tree (see each runner's --help).
  --out-dir DIR     Where <suite>.log and <suite>.exit are written. Created
                    if absent. Default: \${PGAI_AGENT_KANBAN_TEMP_DIR}, the
                    temp root (currently $(pgai_temp_dir)).
  --help, -h        Show this help and exit 0. Runs nothing and writes
                    nothing.

Exit codes:
  0  both suites exited 0
  N  otherwise the first non-zero suite exit code -- unit first, then
     integration (1 = test failure, 2 = pytest not installed)
  1  also: an unrecognized argument, a refused [testing] workers value, a
     missing runner, or an output directory that cannot be written -- no
     suite ran
EOF
      exit 0
      ;;
    *)
      # CENSUS-ANCHOR: run-gated-suites-args-01
      # AGENT-RESOLVABLE: the caller drops or corrects the flag and re-runs.
      pgai_emit_error_contract "run-gated-suites.unrecognized-argument" \
        "unrecognized argument '$1'; run-gated-suites.sh accepts --coverage, --verbose, --reuse-if-green, --out-dir DIR and --help (a --files targeted run belongs to run-unit-tests.sh or run-integration-tests.sh)." \
        "bash \"${BASH_SOURCE[0]}\" --help"
      exit 1
      ;;
  esac
done

export PGAI_AGENT_KANBAN_TEMP_DIR="$(pgai_temp_dir)"
OUT_DIR="${OUT_DIR:-$PGAI_AGENT_KANBAN_TEMP_DIR}"

# --- Resolve the worker budget ---
if ! _RGS_WORKERS="$(config_testing_workers "$TEAM_ROOT/kanban.cfg")"; then
  # BLOCK-WORTHY: the budget bounds how many pytest workers both suites
  # start on a shared host; config_testing_workers has already printed the
  # error/message/remedy naming the value and the sed fix.
  # CENSUS-ANCHOR: run-gated-suites-testing-config-01
  exit 1
fi

# --- Both runners must be present ---
for _rgs_runner in run-unit-tests.sh run-integration-tests.sh; do
  if [[ ! -f "${SCRIPT_DIR}/${_rgs_runner}" ]]; then
    # CENSUS-ANCHOR: run-gated-suites-runner-missing-01
    # BLOCK-WORTHY: verification cannot run without both gated runners.
    pgai_emit_error_contract "run-gated-suites.runner-missing" \
      "the gated runner ${SCRIPT_DIR}/${_rgs_runner} does not exist; run-gated-suites.sh runs the two runners that sit beside it and starts neither when one is missing." \
      "ls -l \"${SCRIPT_DIR}\"/run-*-tests.sh"
    exit 1
  fi
done
unset _rgs_runner

if ! mkdir -p "$OUT_DIR" 2>/dev/null || [[ ! -w "$OUT_DIR" ]]; then
  # CENSUS-ANCHOR: run-gated-suites-out-dir-01
  # BLOCK-WORTHY: the suites' exit codes and logs would have nowhere to go.
  pgai_emit_error_contract "run-gated-suites.out-dir-unwritable" \
    "the output directory ${OUT_DIR} cannot be created or written; each suite's .exit and .log file go there." \
    "bash \"${BASH_SOURCE[0]}\" --out-dir ${_RGS_EXAMPLE_OUT_DIR}"
  exit 1
fi

# --- Plan the run ---
# gated_suites.py writes NUL-terminated records (its docstring, "CLI"); its
# own exit status rides in as a final record because a process
# substitution's status is otherwise lost.
_RGS_PLAN_OPTS=(plan --workers "$_RGS_WORKERS" --coverage-root "${PGAI_AGENT_KANBAN_TEMP_DIR}/coverage")
[[ "$COVERAGE" == "true" ]] && _RGS_PLAN_OPTS+=(--coverage)
[[ "$VERBOSE" == "true" ]] && _RGS_PLAN_OPTS+=(--verbose)
[[ "$REUSE_IF_GREEN" == "true" ]] && _RGS_PLAN_OPTS+=(--reuse-if-green)

mapfile -d '' _rgs_records < <(
  python3 "$_RGS_PLANNER" "${_RGS_PLAN_OPTS[@]}"
  printf 'planner-exit\0%s\0' "$?"
)

MODE=""
SWEEP_AFTER=false
SUITE_NAMES=()
SUITE_RUNNERS=()
SUITE_ENV_STARTS=()
SUITE_ENV_COUNTS=()
SUITE_ENV=()
SUITE_ARG_STARTS=()
SUITE_ARG_COUNTS=()
SUITE_ARGS=()
_rgs_problem=""
_rgs_planner_rc=""
_rgs_i=0
_rgs_n=${#_rgs_records[@]}
while (( _rgs_i < _rgs_n )); do
  case "${_rgs_records[_rgs_i]}" in
    mode)
      MODE="${_rgs_records[_rgs_i+1]:-}"
      _rgs_i=$((_rgs_i + 2))
      ;;
    say)
      printf '%s\n' "${_rgs_records[_rgs_i+1]:-}"
      _rgs_i=$((_rgs_i + 2))
      ;;
    dir)
      mkdir -p "${_rgs_records[_rgs_i+1]}"
      _rgs_i=$((_rgs_i + 2))
      ;;
    sweep-after)
      SWEEP_AFTER=true
      _rgs_i=$((_rgs_i + 1))
      ;;
    run)
      # run <suite> <runner> <envc> <env>... <argc> <arg>...
      _rgs_envc="${_rgs_records[_rgs_i+3]:-}"
      if [[ ! "$_rgs_envc" =~ ^[0-9]+$ ]]; then
        _rgs_problem="a malformed run record"
        break
      fi
      _rgs_argc="${_rgs_records[_rgs_i+4+_rgs_envc]:-}"
      if [[ ! "$_rgs_argc" =~ ^[0-9]+$ ]] || (( _rgs_i + 5 + _rgs_envc + _rgs_argc > _rgs_n )); then
        _rgs_problem="a malformed run record"
        break
      fi
      SUITE_NAMES+=("${_rgs_records[_rgs_i+1]}")
      SUITE_RUNNERS+=("${_rgs_records[_rgs_i+2]}")
      SUITE_ENV_STARTS+=("${#SUITE_ENV[@]}")
      SUITE_ENV_COUNTS+=("$_rgs_envc")
      SUITE_ENV+=("${_rgs_records[@]:_rgs_i+4:_rgs_envc}")
      SUITE_ARG_STARTS+=("${#SUITE_ARGS[@]}")
      SUITE_ARG_COUNTS+=("$_rgs_argc")
      SUITE_ARGS+=("${_rgs_records[@]:_rgs_i+5+_rgs_envc:_rgs_argc}")
      _rgs_i=$((_rgs_i + 5 + _rgs_envc + _rgs_argc))
      ;;
    planner-exit)
      _rgs_planner_rc="${_rgs_records[_rgs_i+1]:-}"
      _rgs_i=$((_rgs_i + 2))
      ;;
    *)
      _rgs_problem="an unrecognized record '${_rgs_records[_rgs_i]}'"
      break
      ;;
  esac
done
if [[ -z "$_rgs_problem" && "$_rgs_planner_rc" != "0" ]]; then
  _rgs_problem="exit status ${_rgs_planner_rc:-unknown}"
fi
if [[ -z "$_rgs_problem" && ( ${#SUITE_NAMES[@]} -ne 2 || ( "$MODE" != "concurrent" && "$MODE" != "sequential" ) ) ]]; then
  _rgs_problem="no usable plan"
fi
if [[ -n "$_rgs_problem" ]]; then
  # A planner problem never stops verification: run the two runners one
  # after the other, as an operator would by hand, which can never exceed
  # the budget because each reads it for itself.
  echo "[gated-suites] the run planner ${_RGS_PLANNER} returned ${_rgs_problem}; running unit, then integration, one after the other (python3 \"${_RGS_PLANNER}\" plan --help shows its usage)."
  MODE="sequential"
  SWEEP_AFTER=false
  _rgs_pass=()
  [[ "$COVERAGE" == "true" ]] && _rgs_pass+=(--coverage)
  [[ "$VERBOSE" == "true" ]] && _rgs_pass+=(--verbose)
  [[ "$REUSE_IF_GREEN" == "true" ]] && _rgs_pass+=(--reuse-if-green)
  SUITE_NAMES=(unit integration)
  SUITE_RUNNERS=(run-unit-tests.sh run-integration-tests.sh)
  SUITE_ENV_STARTS=(0 0)
  SUITE_ENV_COUNTS=(0 0)
  SUITE_ENV=()
  SUITE_ARG_STARTS=(0 0)
  SUITE_ARG_COUNTS=("${#_rgs_pass[@]}" "${#_rgs_pass[@]}")
  SUITE_ARGS=("${_rgs_pass[@]}")
  unset _rgs_pass
fi
unset _rgs_records _rgs_problem _rgs_planner_rc _rgs_i _rgs_n _rgs_envc _rgs_argc

# --- Run the suites ---
SUITE_PIDS=()
SUITE_EXITS=()

_rgs_exit_file() { printf '%s/%s.exit' "$OUT_DIR" "${SUITE_NAMES[$1]}"; }
_rgs_log_file() { printf '%s/%s.log' "$OUT_DIR" "${SUITE_NAMES[$1]}"; }

# Start suite <index> in the background; its pid goes to SUITE_PIDS[index].
_rgs_start() {
  local index="$1"
  local -a env_pairs=("${SUITE_ENV[@]:${SUITE_ENV_STARTS[index]}:${SUITE_ENV_COUNTS[index]}}")
  local -a args=("${SUITE_ARGS[@]:${SUITE_ARG_STARTS[index]}:${SUITE_ARG_COUNTS[index]}}")
  local runner="${SCRIPT_DIR}/${SUITE_RUNNERS[index]}"
  local log
  log="$(_rgs_log_file "$index")"
  env "${env_pairs[@]}" bash "$runner" "${args[@]}" > "$log" 2>&1 &
  SUITE_PIDS[index]=$!
  echo "${SUITE_NAMES[index]}: started ${SUITE_RUNNERS[index]} ${args[*]} (pid ${SUITE_PIDS[index]}${env_pairs[*]:+; ${env_pairs[*]}}); log: ${log}"
}

# Record suite <index>'s exit code <rc> and write its exit file.
_rgs_record() {
  local index="$1" rc="$2" exit_file
  SUITE_PIDS[index]=""
  SUITE_EXITS[index]="$rc"
  exit_file="$(_rgs_exit_file "$index")"
  if ! printf '%s\n' "$rc" > "$exit_file" 2>/dev/null; then
    echo "[gated-suites] could not write ${exit_file}; ${SUITE_NAMES[index]} exited ${rc}" >&2
  fi
  echo "${SUITE_NAMES[index]}: exit ${rc}; exit file: ${exit_file}; findings: ${PGAI_AGENT_KANBAN_TEMP_DIR}/lint/findings-${SUITE_NAMES[index]}.json"
}

# Stop any suite still running when this script is interrupted, so no
# runner (or the pytest it started) outlives the command that started it.
_rgs_stop_children() {
  local pid
  for pid in "${SUITE_PIDS[@]}"; do
    [[ -n "$pid" ]] || continue
    pkill -TERM -P "$pid" 2>/dev/null || true
    kill -TERM "$pid" 2>/dev/null || true
  done
}
# Exit with the shell's conventional status for the signal (128 + number).
_rgs_on_signal() {
  _rgs_stop_children
  exit "$((128 + $1))"
}
trap '_rgs_on_signal 2' INT
trap '_rgs_on_signal 15' TERM

# Remove last run's exit files first, so a crash here never leaves an old
# verdict looking like this run's.
for _rgs_index in "${!SUITE_NAMES[@]}"; do
  rm -f "$(_rgs_exit_file "$_rgs_index")"
done

# Wait for suite <index> alone and record it.
_rgs_wait_one() {
  local index="$1" rc=0
  wait "${SUITE_PIDS[index]}" || rc=$?
  _rgs_record "$index" "$rc"
}

# Wait for whichever running suite exits next and record it, so each exit
# file lands as soon as its own runner exits. Needs `wait -n -p` (bash 5.1
# or later); older bash waits in suite order instead.
_rgs_wait_next() {
  local done_pid="" rc=0 index
  local -a live=()
  for index in "${!SUITE_PIDS[@]}"; do
    [[ -n "${SUITE_PIDS[index]}" ]] && live+=("${SUITE_PIDS[index]}")
  done
  if (( BASH_VERSINFO[0] > 5 || (BASH_VERSINFO[0] == 5 && BASH_VERSINFO[1] >= 1) )); then
    wait -n -p done_pid "${live[@]}" || rc=$?
    for index in "${!SUITE_PIDS[@]}"; do
      if [[ -n "$done_pid" && "${SUITE_PIDS[index]}" == "$done_pid" ]]; then
        _rgs_record "$index" "$rc"
        return 0
      fi
    done
  fi
  for index in "${!SUITE_PIDS[@]}"; do
    if [[ -n "${SUITE_PIDS[index]}" ]]; then
      _rgs_wait_one "$index"
      return 0
    fi
  done
}

if [[ "$MODE" == "concurrent" ]]; then
  for _rgs_index in "${!SUITE_NAMES[@]}"; do
    _rgs_start "$_rgs_index"
  done
  for _rgs_index in "${!SUITE_NAMES[@]}"; do
    _rgs_wait_next
  done
else
  for _rgs_index in "${!SUITE_NAMES[@]}"; do
    _rgs_start "$_rgs_index"
    _rgs_wait_one "$_rgs_index"
  done
fi
unset _rgs_index
trap - INT TERM

# --- Combined exit code ---
if ! FINAL_EXIT="$(python3 "$_RGS_PLANNER" combine "${SUITE_EXITS[@]}")" || [[ ! "$FINAL_EXIT" =~ ^[0-9]+$ ]]; then
  # The planner could not combine; any non-zero suite still fails the run.
  FINAL_EXIT=0
  for _rgs_rc in "${SUITE_EXITS[@]}"; do
    if [[ "$_rgs_rc" != "0" ]]; then FINAL_EXIT="$_rgs_rc"; break; fi
  done
  unset _rgs_rc
fi

# --- Scratch sweep left to this script by concurrent children ---
# Green-only, as in each runner: a failing suite's scratch survives for
# debugging. A sweep problem is narrated and never changes the exit code.
if [[ "$SWEEP_AFTER" == "true" && "$FINAL_EXIT" == "0" ]]; then
  echo "Sweeping post-suite scratch under \${PGAI_AGENT_KANBAN_TEMP_DIR}/tests..."
  pgai_temp_cleanup_tests || echo "[gated-suites] the post-suite scratch sweep did not finish (see the line above); the suites' exit codes are unaffected." >&2
fi

# --- Summary ---
for _rgs_index in "${!SUITE_NAMES[@]}"; do
  if [[ "${SUITE_EXITS[_rgs_index]}" != "0" ]]; then
    echo ""
    echo "Last 20 lines of the ${SUITE_NAMES[_rgs_index]} log ($(_rgs_log_file "$_rgs_index")):"
    tail -n 20 "$(_rgs_log_file "$_rgs_index")" || true
  fi
done
_rgs_summary=""
for _rgs_index in "${!SUITE_NAMES[@]}"; do
  _rgs_summary+="${_rgs_summary:+, }${SUITE_NAMES[_rgs_index]} exit ${SUITE_EXITS[_rgs_index]}"
done
echo ""
echo "Gated suites: ${_rgs_summary} -> exit ${FINAL_EXIT}"
unset _rgs_index _rgs_summary
exit "$FINAL_EXIT"
