#!/usr/bin/env bash
# team/scripts/lib/gated_runner_args.sh
#
# The gated runners' shared flag contract (run-unit-tests.sh,
# run-integration-tests.sh): one argument parser and the two validators that
# refuse a flag combination before any gate, lint or pytest runs. Each
# runner sources this file, keeps its own --help text in a function, and
# passes in the few words that differ between the two suites; every flag,
# refusal key, message, remedy and exit code is produced here, once.
#
# A refusal goes through pgai_emit_error_contract (lib/error_contract.sh,
# which the runner sources first) and exits the runner with status 1; every
# refusal here is AGENT-RESOLVABLE: the caller corrects its own argv and
# re-runs in the same session. Error keys are "<runner>.<reason>", with
# <runner> the runner's basename without .sh (run-unit-tests).
#
# Public API
# ----------
#   gated_runner_parse_args <runner> <script> <help-fn> [ARG ...]
#       Parses the runner's argv, one case arm per flag; --files consumes
#       every following argument up to the next one starting with "-".
#       Sets the globals VERBOSE, PRE_MERGE_BASE, COVERAGE, QUARANTINED,
#       REUSE_IF_GREEN, NO_SCRATCH_SWEEP, REQUESTED_WORKERS and the array
#       TARGETED_FILES. --help/-h calls <help-fn> (which prints the usage)
#       and exits 0. <script> is the runner path as invoked, named in every
#       remedy. --pre-merge is only consumed here: the runner decides a
#       pre-merge run from its argv pre-scan
#       (gated_runner_argv_requests_pre_merge, lib/gated_runner_lint.sh).
#
#   gated_runner_validate_pre_merge_flags <runner> <script> <pre-merge>
#                                         <targeted> <exit-file> <tests>
#       With <pre-merge> true, refuses --files (<targeted> true),
#       --coverage, --quarantined and --reuse-if-green, naming each, and
#       otherwise prints the pre-merge banner naming <exit-file>. Without
#       it, refuses --base. <tests> describes what the runner's pre-merge
#       run chooses, for the refusal message.
#
#   gated_runner_validate_targeted_files <runner> <script> <team-dir>
#                                        <exit-file> <example> <scope>
#                                        <root> [<root> ...]
#       Validates TARGETED_FILES for a --files run: refuses an empty list,
#       --coverage, a path resolving outside the <root>s (relative to
#       <team-dir>), and a path that is not an existing file. Fills the
#       array GATED_RUNNER_TARGETED_RESOLVED with each path relative to
#       <team-dir> and prints the targeted-run banner naming <exit-file>.
#       <example> is the test file every remedy names; <scope> is the
#       sentence the outside-the-roots refusal ends with.
#
# The pytest-free path classifier these call is gated_runner_targeted_
# resolve_path (lib/gated_runner_lint.sh), which the runner sources first.

[[ -n "${_GATED_RUNNER_ARGS_SH:-}" ]] && return 0
_GATED_RUNNER_ARGS_SH=1

gated_runner_parse_args() {
  local runner="$1" script="$2" help_fn="$3"
  shift 3
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
          # CENSUS-ANCHOR: gated-runner-args-workers-flag-01
          # AGENT-RESOLVABLE: the caller passes a whole number after --workers and re-runs.
          pgai_emit_error_contract "${runner}.workers-flag-invalid" \
            "--workers needs a non-negative whole number in decimal digits after it (got '${2:-}'); it lowers this run's worker budget below kanban.cfg [testing] workers and never raises it." \
            "bash \"${script}\" --workers 1"
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
        # Already decided by the runner's argv pre-scan.
        shift
        ;;
      --base)
        if [[ -z "${2:-}" || "${2:-}" == -* ]]; then
          # CENSUS-ANCHOR: gated-runner-args-pre-merge-01
          # AGENT-RESOLVABLE: the caller names a git ref after --base, or drops --base for the default base, and re-runs.
          pgai_emit_error_contract "${runner}.pre-merge-base-missing" \
            "--base needs a git ref after it (got '${2:-}'), e.g. --base ai_main; without --base a pre-merge run compares HEAD with the local RC branch this worktree was cut from." \
            "bash \"${script}\" --pre-merge"
          exit 1
        fi
        PRE_MERGE_BASE="$2"
        shift 2
        ;;
      --help|-h)
        "$help_fn"
        exit 0
        ;;
      *)
        gated_runner_reject_unknown_arg "${script}" "$1" "bash \"${script}\" --verbose"
        exit 1
        ;;
    esac
  done
}

# A pre-merge run chooses its own tests and is never a certification run,
# so the flags that select tests (--files, --quarantined), measure coverage
# or reuse a certified result are refused beside it; --base names a
# pre-merge run's diff base and means nothing without one.
gated_runner_validate_pre_merge_flags() {
  local runner="$1" script="$2" pre_merge="$3" targeted="$4" exit_file="$5" tests="$6"
  if [[ "$pre_merge" == "true" ]]; then
    local conflicts=()
    [[ "$targeted" == "true" ]] && conflicts+=("--files")
    [[ "$COVERAGE" == "true" ]] && conflicts+=("--coverage")
    [[ "$QUARANTINED" == "true" ]] && conflicts+=("--quarantined")
    [[ "$REUSE_IF_GREEN" == "true" ]] && conflicts+=("--reuse-if-green")
    if [[ ${#conflicts[@]} -gt 0 ]]; then
      # CENSUS-ANCHOR: gated-runner-args-pre-merge-02
      # AGENT-RESOLVABLE: the caller drops the flags a pre-merge run does not take and re-runs.
      pgai_emit_error_contract "${runner}.pre-merge-incompatible-flags" \
        "--pre-merge cannot be combined with ${conflicts[*]}: a pre-merge run chooses its own tests (${tests}), is never a certification run, and measures no coverage." \
        "bash \"${script}\" --pre-merge${PRE_MERGE_BASE:+ --base ${PRE_MERGE_BASE@Q}}"
      exit 1
    fi
    echo "pre-merge run — not a certification run"
    echo "Exit code will be written to: ${exit_file}"
  elif [[ -n "$PRE_MERGE_BASE" ]]; then
    # CENSUS-ANCHOR: gated-runner-args-pre-merge-03
    # AGENT-RESOLVABLE: the caller adds --pre-merge (or drops --base) and re-runs.
    pgai_emit_error_contract "${runner}.base-without-pre-merge" \
      "--base ${PRE_MERGE_BASE} was given without --pre-merge; --base names the diff base a pre-merge run chooses its affected tests from, and a certification or --files run has none." \
      "bash \"${script}\" --pre-merge --base ${PRE_MERGE_BASE@Q}"
    exit 1
  fi
}

# Paths are classified by gated_runner_targeted_resolve_path against the
# runner's own test roots (the directories its certification pytest argv
# collects), so a path that escapes them -- tests/../../etc/passwd -- is
# refused rather than collected.
gated_runner_validate_targeted_files() {
  local runner="$1" script="$2" team_dir="$3" exit_file="$4" example="$5" scope="$6"
  shift 6
  local roots=("$@")
  local root_noun="test roots"
  [[ ${#roots[@]} -eq 1 ]] && root_noun="test root"
  if [[ ${#TARGETED_FILES[@]} -eq 0 ]]; then
    # CENSUS-ANCHOR: gated-runner-args-targeted-files-01
    # AGENT-RESOLVABLE: the caller names the test files it touched and re-runs.
    pgai_emit_error_contract "${runner}.targeted-files-missing" \
      "--files was given without any test file path after it; a targeted run needs at least one test file under ${roots[*]} (paths relative to team/)." \
      "bash \"${script}\" --files ${example}"
    exit 1
  fi
  if [[ "$COVERAGE" == "true" ]]; then
    # CENSUS-ANCHOR: gated-runner-args-targeted-files-02
    # AGENT-RESOLVABLE: drop --coverage from the targeted run; coverage belongs to a full run.
    pgai_emit_error_contract "${runner}.targeted-files-with-coverage" \
      "--files cannot be combined with --coverage: coverage measured over a handful of test files is not a meaningful coverage figure, so a targeted run never measures it." \
      "bash \"${script}\" --files ${TARGETED_FILES[*]@Q}"
    exit 1
  fi
  GATED_RUNNER_TARGETED_RESOLVED=()
  local path rel rc
  for path in "${TARGETED_FILES[@]}"; do
    rc=0
    rel="$(gated_runner_targeted_resolve_path "$team_dir" "$path" "${roots[@]}")" || rc=$?
    if [[ $rc -eq 3 ]]; then
      # CENSUS-ANCHOR: gated-runner-args-targeted-files-03
      # AGENT-RESOLVABLE: the caller passes a path under this runner's own test roots.
      pgai_emit_error_contract "${runner}.targeted-file-outside-test-root" \
        "--files path '${path}' resolves outside this runner's ${root_noun} (${roots[*]}, relative to ${team_dir}); ${scope}" \
        "bash \"${script}\" --files ${example}"
      exit 1
    fi
    if [[ $rc -ne 0 ]]; then
      # CENSUS-ANCHOR: gated-runner-args-targeted-files-04
      # AGENT-RESOLVABLE: the caller corrects the file name and re-runs.
      pgai_emit_error_contract "${runner}.targeted-file-not-found" \
        "--files path '${path}' is not an existing test file (looked for ${team_dir}/${rel}); each --files path must name a file, not a directory, under ${roots[*]}." \
        "ls \"${team_dir}/${roots[0]}\" && bash \"${script}\" --files ${example}"
      exit 1
    fi
    GATED_RUNNER_TARGETED_RESOLVED+=("$rel")
  done
  echo "targeted run — not a certification run"
  echo "Targeted test files: ${GATED_RUNNER_TARGETED_RESOLVED[*]}"
  echo "Exit code will be written to: ${exit_file}"
}
