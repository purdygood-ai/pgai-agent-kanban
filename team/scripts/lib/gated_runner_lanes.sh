#!/usr/bin/env bash
# team/scripts/lib/gated_runner_lanes.sh
#
# Shell side of the gated runners' pytest lanes (run-unit-tests.sh,
# run-integration-tests.sh). The decisions -- how many pytest invocations
# run, with which argv, and whether --coverage is honoured -- are made by
# gated_runner_lanes.py beside this file; its module docstring is the full
# statement of the lane and coverage rules. This file runs that planner,
# reads its plan into shell arrays, combines the lanes' exit codes into the
# run's one exit code, and scrubs the operator's coverage switch from an
# agent wake's environment.
#
# Public API
# ----------
#   gated_runner_plan_lanes <planner-option>... -- <pytest-argv>...
#       Runs `python3 gated_runner_lanes.py plan <planner-option>... --
#       <pytest-argv>...`, prints the plan's narration (say records on
#       stdout, warn records on stderr) and fills:
#         GATED_RUNNER_LANE_NAMES   lane names in run order
#                                   (parallel, serial | single)
#         GATED_RUNNER_LANE_STARTS  each lane's offset into ..._LANE_ARGV
#         GATED_RUNNER_LANE_COUNTS  each lane's argv length
#         GATED_RUNNER_LANE_ARGV    every lane's argv, concatenated
#         GATED_RUNNER_COVERAGE_DIR the coverage report directory, or ""
#                                   when this run collects no coverage
#         GATED_RUNNER_DURATIONS_DIR the directory the lanes write their
#                                   timing reports into, or "" when this
#                                   run records no durations (a --files or
#                                   --quarantined run)
#       When the planner cannot run or returns no usable plan, prints one
#       line saying so and plans one single-process lane running
#       <pytest-argv> unchanged, without coverage -- a planner problem
#       never stops a test run. Always returns 0.
#
#   gated_runner_lane_args <index>
#       Sets GATED_RUNNER_LANE_ARGS to lane <index>'s argv (0-based).
#
#   gated_runner_combine_lane_exits <exit-code>...
#       Prints the run's one exit code from its lanes' pytest exit codes,
#       in run order: the first code that is neither 0 nor 5 (a lane that
#       failed or errored); else 0 when any lane passed; else 5 (no lane
#       collected a test). pytest's 5 means "no tests collected" -- a lane
#       whose marker selection matches nothing, such as a serial lane
#       before any test carries the serial marker, is not a failure on its
#       own. Each runner then applies its own exit-5 policy to the result.
#
#   gated_runner_coverage_scrub_env
#       Unsets PGAI_TESTING_COVERAGE in the current shell, so no process
#       started from it afterwards inherits it. The wake bootstrap calls it
#       so an agent can never switch coverage on.
#
#   gated_runner_record_durations <suite> <out-file>
#       After the last lane of a run that records durations
#       (GATED_RUNNER_DURATIONS_DIR set), merges the lanes' timing reports
#       into <out-file> (durations-<suite>.json beside the findings file)
#       through gated_runner_durations.py, prints its one narration line and
#       removes the lane reports. Informational only: it never fails a run
#       and always returns 0. Does nothing when no durations are recorded.

_GATED_RUNNER_LANES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

GATED_RUNNER_LANE_NAMES=()
GATED_RUNNER_LANE_STARTS=()
GATED_RUNNER_LANE_COUNTS=()
GATED_RUNNER_LANE_ARGV=()
GATED_RUNNER_LANE_ARGS=()
GATED_RUNNER_COVERAGE_DIR=""
GATED_RUNNER_DURATIONS_DIR=""

_gated_runner_reset_lanes() {
  GATED_RUNNER_LANE_NAMES=()
  GATED_RUNNER_LANE_STARTS=()
  GATED_RUNNER_LANE_COUNTS=()
  GATED_RUNNER_LANE_ARGV=()
  GATED_RUNNER_COVERAGE_DIR=""
  GATED_RUNNER_DURATIONS_DIR=""
}

gated_runner_plan_lanes() {
  local -a planner_opts=() pytest_argv=() records=()
  while [[ $# -gt 0 && "$1" != "--" ]]; do
    planner_opts+=("$1")
    shift
  done
  [[ "${1:-}" == "--" ]] && shift
  pytest_argv=("$@")

  _gated_runner_reset_lanes
  local planner="${_GATED_RUNNER_LANES_DIR}/gated_runner_lanes.py"
  # The planner's own exit status rides in as a final record, since a
  # process substitution's status is otherwise lost.
  mapfile -d '' records < <(
    python3 "$planner" plan "${planner_opts[@]}" -- "${pytest_argv[@]}"
    printf 'planner-exit\0%s\0' "$?"
  )

  local i=0 n=${#records[@]} kind argc planner_rc="" problem=""
  while (( i < n )); do
    kind="${records[i]}"
    case "$kind" in
      say)
        printf '%s\n' "${records[i+1]:-}"
        i=$((i + 2))
        ;;
      warn)
        printf '%s\n' "${records[i+1]:-}" >&2
        i=$((i + 2))
        ;;
      coverage-dir)
        GATED_RUNNER_COVERAGE_DIR="${records[i+1]:-}"
        i=$((i + 2))
        ;;
      durations-dir)
        GATED_RUNNER_DURATIONS_DIR="${records[i+1]:-}"
        i=$((i + 2))
        ;;
      lane)
        argc="${records[i+2]:-}"
        if [[ ! "$argc" =~ ^[0-9]+$ ]] || (( i + 3 + argc > n )); then
          problem="a malformed lane record"
          break
        fi
        GATED_RUNNER_LANE_NAMES+=("${records[i+1]}")
        GATED_RUNNER_LANE_STARTS+=("${#GATED_RUNNER_LANE_ARGV[@]}")
        GATED_RUNNER_LANE_COUNTS+=("$argc")
        GATED_RUNNER_LANE_ARGV+=("${records[@]:i+3:argc}")
        i=$((i + 3 + argc))
        ;;
      planner-exit)
        planner_rc="${records[i+1]:-}"
        i=$((i + 2))
        ;;
      *)
        problem="an unrecognized record '${kind}'"
        break
        ;;
    esac
  done

  if [[ -z "$problem" && "$planner_rc" != "0" ]]; then
    problem="exit status ${planner_rc:-unknown}"
  fi
  if [[ -z "$problem" && ${#GATED_RUNNER_LANE_NAMES[@]} -eq 0 ]]; then
    problem="no lane"
  fi
  if [[ -n "$problem" ]]; then
    _gated_runner_reset_lanes
    GATED_RUNNER_LANE_NAMES=("single")
    GATED_RUNNER_LANE_STARTS=(0)
    GATED_RUNNER_LANE_COUNTS=("${#pytest_argv[@]}")
    GATED_RUNNER_LANE_ARGV=("${pytest_argv[@]}")
    echo "[lanes] the lane planner ${planner} returned ${problem}; running one single-process pytest run without coverage (python3 \"${planner}\" plan --help shows its usage)."
  fi
  return 0
}

gated_runner_lane_args() {
  local index="$1"
  GATED_RUNNER_LANE_ARGS=(
    "${GATED_RUNNER_LANE_ARGV[@]:${GATED_RUNNER_LANE_STARTS[index]}:${GATED_RUNNER_LANE_COUNTS[index]}}"
  )
}

gated_runner_combine_lane_exits() {
  local code first_failure="" any_passed=false
  for code in "$@"; do
    case "$code" in
      0) any_passed=true ;;
      5) ;;
      *) [[ -z "$first_failure" ]] && first_failure="$code" ;;
    esac
  done
  if [[ -n "$first_failure" ]]; then
    printf '%s\n' "$first_failure"
  elif [[ "$any_passed" == "true" ]]; then
    printf '%s\n' 0
  else
    printf '%s\n' 5
  fi
}

gated_runner_coverage_scrub_env() {
  unset PGAI_TESTING_COVERAGE
}

gated_runner_record_durations() {
  local suite="$1" out_file="$2"
  [[ -n "$GATED_RUNNER_DURATIONS_DIR" ]] || return 0
  local -a reports=()
  local report
  for report in "${GATED_RUNNER_DURATIONS_DIR}/${suite}"-*.xml; do
    [[ -f "$report" ]] && reports+=("$report")
  done
  python3 "${_GATED_RUNNER_LANES_DIR}/gated_runner_durations.py" record \
    --suite "$suite" --out "$out_file" ${reports[@]+"${reports[@]}"} \
    || echo "[durations] ${suite}: recording the slowest tests failed; no durations file this run (informational only)."
  rm -f ${reports[@]+"${reports[@]}"}
  rmdir "$GATED_RUNNER_DURATIONS_DIR" 2>/dev/null || true
  return 0
}
