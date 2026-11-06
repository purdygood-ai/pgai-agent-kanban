#!/usr/bin/env bash
# team/scripts/lib/gated_runner_lint.sh
#
# Shared helpers implementing the breakage-class / quality-class split for
# the gated test runners (run-unit-tests.sh, run-integration-tests.sh).
#
# THE SPLIT
# ---------
# Breakage class (fatal, exit 1 before pytest): syntax errors, compile
# failures, unresolved first-party imports -- states where running the test
# suite is meaningless.  Handled by team/scripts/check_breakage_class.py,
# invoked directly by each runner (not through this library) so a breakage
# failure aborts the runner immediately, before any quality-class lint runs.
#
# Quality class (never fatal): every other pre-flight lint -- provenance,
# anti-pattern, orphan-test, help-presence, doc/ICD freshness,
# API-parity, and the rest.  Findings print in full, are recorded into a
# machine-readable findings file, and the runner continues to pytest
# regardless of the underlying lint's exit code.  This library provides the
# quality-class half of the split.
#
# Source this file after `set -euo pipefail` is active (its functions do not
# themselves require strict mode, but the callers that use them do):
#   source "$(dirname "${BASH_SOURCE[0]}")/lib/gated_runner_lint.sh"
#
# Public API
# ----------
#   gated_runner_run_quality_lint <lint-name> -- <command...>
#       Runs <command...>, prints its combined stdout+stderr in full, and --
#       regardless of the command's exit code -- returns 0.  A non-zero exit
#       from <command...> is recorded as one finding in the in-memory
#       GATED_RUNNER_FINDINGS accumulator (one JSON object per line).  Never
#       aborts the caller: the whole point of quality-class is "warn, file,
#       continue."
#
#   gated_runner_merge_findings_file <path>
#       Reads a JSON array of already-structured findings (the shape
#       lint_dual_channel_pair.py's --findings-file writes) from <path> and
#       appends each element to GATED_RUNNER_FINDINGS.  Use this for lints
#       that already produce per-instance findings instead of one coarse
#       pass/fail result.  A missing file is a silent no-op (the lint may
#       have found nothing to report).
#
#   gated_runner_write_findings_file <output-path> <runner-name>
#       Assembles every accumulated finding into the findings-file schema
#       (below) and writes it to <output-path>.  Always writes the file,
#       even when GATED_RUNNER_FINDINGS is empty, so a downstream consumer
#       (TESTER's report, an operator script) can rely on the file's
#       existence as proof the quality-class pre-flight ran.
#
#   gated_runner_record_finding <lint-name> <summary>
#       Appends one hand-authored quality-class finding to
#       GATED_RUNNER_FINDINGS, in the same object shape
#       gated_runner_run_quality_lint produces for a failing command --
#       for a caller recording a finding with no command output to
#       summarize (a fatal pre-flight gate noting that it aborted the run
#       before any quality-class lint or pytest could execute).
#
#   gated_runner_record_instance_finding <lint-name> <cause> <remedy> [KEY=VALUE ...]
#       Appends one per-instance finding to GATED_RUNNER_FINDINGS, in the
#       shape lint_dual_channel_pair.py writes (lint, severity, summary,
#       cause, remedy) plus one key per KEY=VALUE argument (path, pid,
#       kind, ...). Built by gated_findings.py instance; never fails the
#       caller.
#
#   gated_runner_write_findings_array <output-path>
#       Writes every accumulated finding to <output-path> as one bare JSON
#       array -- the --findings-file shape gated_runner_merge_findings_file
#       reads back -- for a standalone quality-class tool that records its
#       own per-instance findings with gated_runner_record_instance_finding.
#       Always writes the file ("[]" when there are none); a write failure
#       is narrated on stderr and never fails the caller.
#
#   gated_runner_run_instance_check <lint-name> <array-name> <formatter> -- <command...>
#       Runs a post-suite hygiene check in the CURRENT shell (so the
#       per-instance array it fills is visible afterwards), printing its
#       stdout+stderr on stdout, and returns 0 whatever it returned. On a
#       non-zero return, calls `<formatter> <lint-name> <instance>` once per
#       element of the global array <array-name> -- each records one
#       per-instance finding -- or, when the check named no instance,
#       records one finding saying so. See "Post-suite hygiene checks"
#       below for the four checks and their formatters.
#
#   gated_runner_clear_run_evidence <temp-root> <suite>
#       Removes every evidence file an earlier run of <suite> (unit or
#       integration) left at the fixed paths a certification run writes:
#       <temp-root>/lint/*_<suite>.json (per-lint findings),
#       <temp-root>/lint/*-<suite>.json and its .runid sidecar (the
#       aggregate findings file, durations), <temp-root>/coverage/<suite>.json
#       and the coverage data files in <temp-root>/coverage/<suite>-data/.
#       Called at certification-run start, ahead of the findings-file
#       claim, so every file at those paths after the run was written by
#       that run and nothing an earlier run left is read or merged. The
#       other suite's files are never touched. Prints one line naming the
#       locations and the number of files removed; never fails the caller.
#
#   gated_runner_claim_findings_file <output-path> <runner-name>
#       "Claims" <output-path> for THIS invocation, before any fatal gate
#       runs: writes a fresh, empty findings document (via the unmodified
#       gated_runner_write_findings_file) so no abort between the claim
#       and the run's real exit can leave a PRIOR invocation's file in
#       place, and records a run-identity token in a sidecar
#       "<output-path>.runid" file.  Echoes the token on stdout -- the
#       caller holds onto it and passes it to
#       gated_runner_assert_findings_claim later, at the point it is about
#       to trust the file's final contents.
#
#   gated_runner_assert_findings_claim <output-path> <expected-token>
#       The consumer-side refusal: confirms the sidecar
#       "<output-path>.runid" still holds <expected-token> -- i.e. no
#       OTHER invocation of a gated runner, sharing the same host-wide
#       PGAI_AGENT_KANBAN_TEMP_DIR, reclaimed the same path in between.
#       Returns 1 and emits the dual-channel error contract (never exits
#       the caller's shell) when the token has moved; returns 0 silently
#       when it still matches.  Requires error_contract.sh to already be
#       sourced by the caller (both gated runners do this before sourcing
#       this file).
#
#   gated_runner_replay_command <pytest-invocation>
#       Returns (on stdout) a self-contained command line that reproduces
#       the environment the calling runner actually applied to
#       <pytest-invocation> -- not just the pytest argv. Both runners print
#       this in their "Replay this exact run:" banner, immediately before
#       launching pytest, so a failing run replays with the same
#       PYTEST_ADDOPTS/PYTHONHASHSEED/PGAI_*/KANBAN_ROOT state the runner
#       used, regardless of what the operator's own shell happens to carry
#       (e.g. an ambiently exported KANBAN_ROOT from having sourced
#       shell-env). See GATED_RUNNER_REPLAY_ENV_NAMES below for the exact
#       variable family and how a new one gets added.
#
#   gated_runner_pytest_basetemp <suite>
#       Creates, and prints on stdout, a pytest --basetemp directory that
#       belongs to THIS invocation alone, under the framework temp root's
#       pytest/ subtree. Both runners hand it to pytest via PYTEST_ADDOPTS,
#       so two runs of the same suite on one host never share a basetemp
#       (pytest removes and recreates its basetemp at session start, so a
#       shared one is deleted out from under whichever run is still live).
#       Requires temp.sh to be sourced already. Full shape and scoping
#       rules at the function's own comment block below.
#
#   gated_runner_pytest_basetemp_cleanup <path>
#       Removes the directory gated_runner_pytest_basetemp created, through
#       temp.sh's root-guarded pgai_temp_cleanup, on the runner's own exit
#       path. Never fails the caller. See the function's comment block.
#
#   gated_runner_argv_requests_targeted_run <arg...>
#   gated_runner_targeted_resolve_path <team-dir> <path> <test-root>...
#   gated_runner_write_targeted_exit_file <path> <exit-code>
#   gated_runner_targeted_file_lints <anti-pattern-lint> <skip-gate> <file>...
#       The `--files` targeted-run support both runners share: the argv
#       pre-scan that decides, before the findings-file claim, whether this
#       is a targeted run; the per-path test-root/existence classifier; the
#       targeted-<suite>.exit writer; and the runner of the two per-file
#       test-authoring lints over exactly the named files. See "Targeted
#       (non-certification) runs" at the bottom of this file.
#
#   gated_runner_argv_requests_pre_merge <arg...>
#       The argv pre-scan both runners call, before the findings-file claim,
#       to decide whether this invocation is a `--pre-merge` run. A pre-merge
#       run is not a certification run either: it claims nothing, writes
#       neither the findings file, the green ledger nor targeted-<suite>.exit,
#       and records its exit code in its own pre-merge exit file. See
#       "Pre-merge runs" at the bottom of this file.
#
#   gated_runner_argv_requests_help <arg...>
#       The argv pre-scan both runners call, before the findings-file claim,
#       to decide whether this invocation only prints --help/-h usage. A help
#       invocation is not a run: it claims nothing, registers no exit hook,
#       and leaves the findings file, its .runid claim and the green ledger
#       byte-identical. See "Help invocations" at the bottom of this file.
#
#   gated_runner_reject_unknown_arg <script-path> <arg> <example-command>
#       Prints the four runners' unrecognized-argument refusal on stderr,
#       naming <arg>, --help, and <example-command> as a corrected run.
#       The caller exits 1 after it.
#
#   gated_runner_pytest_available
#       Returns 0 when pytest runs (on PATH, or as python3 -m pytest).
#
#   gated_runner_report_pytest_missing <rerun-command> <what-cannot-run>
#       Prints the four runners' pytest-missing diagnosis on stderr, with
#       `pip install pytest` and <rerun-command> as the fix. The caller
#       records its own finding, carries the census anchor and exits 2.
#
#   gated_runner_quarantine_marker_expression <quarantined:true|false>
#   gated_runner_quote_argv <arg...>
#       The `quarantine` marker selection both runners share: the pytest -m
#       expression a run passes (every run passes one), and the shell-quoted
#       rendering of the pytest argv the "Replay this exact run:" line
#       prints, so a multi-word -m expression replays as one argument. See
#       "Quarantine marker selection" at the bottom of this file.
#
# FINDINGS FILE SCHEMA
# ---------------------
# A JSON object, written with a trailing newline:
#
#   {
#     "schema_version": 1,
#     "generated_at": "<ISO 8601 UTC timestamp>",
#     "runner": "<the calling script's basename, e.g. run-unit-tests.sh>",
#     "findings": [ <finding>, ... ]
#   }
#
# Every <finding> is a JSON object carrying, at minimum:
#   "lint"      (string, required) -- the check name that produced it.
#   "severity"  (string, required) -- always "quality" in this file.  A
#               breakage-class failure never reaches this file: it aborts
#               the runner (via check_breakage_class.py, exit 1) before any
#               quality-class lint -- and therefore this findings file --
#               is produced at all.
#   "summary"   (string, required) -- one-line human-readable description.
#
# A coarse (pass/fail-shaped) lint's finding additionally carries:
#   "exit_code" (integer) -- the lint's own process exit code.
#   "detail"    (string)  -- the lint's full captured stdout+stderr.
#
# A fine-grained (per-instance-shaped) lint's finding -- e.g.
# lint_dual_channel_pair.py, merged via gated_runner_merge_findings_file --
# instead carries whatever per-instance keys that lint already emits (for
# example "file", "line", "cause", "remedy"); this library does not alter
# those keys, it only appends the object as-is. A post-suite hygiene
# check's finding (gated_runner_record_instance_finding) carries "cause",
# "remedy", "kind" and the instance's own key ("path", or "pid" and
# "cmdline").
#
# Consumers (TESTER's "Lint Findings (non-blocking)" report section, or any
# other automated reader) should treat "lint" and "severity" as the only
# guaranteed keys and read the rest defensively.

# This file's directory, as an absolute path resolved once at load: no
# function depends on the caller's current directory or on whether this
# file was named by a relative path.
_GATED_RUNNER_LINT_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)" || _GATED_RUNNER_LINT_LIB_DIR=""

# The findings JSON is built, split and written by gated_findings.py beside
# this file (stdlib only, run by path like gated_runner_lanes.py), so a
# runner needs nothing outside its own scripts/ tree to record findings.
_GATED_RUNNER_FINDINGS_PY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/gated_findings.py"

# _gated_runner_findings <command> [args...]
_gated_runner_findings() {
  python3 "$_GATED_RUNNER_FINDINGS_PY" "$@"
}

# ---------------------------------------------------------------------------
# Accumulator: one already-serialized JSON object per array element.
# Declared with -a (not -A) since findings are an ordered list, not a map.
# ---------------------------------------------------------------------------
GATED_RUNNER_FINDINGS=()

# ---------------------------------------------------------------------------
# gated_runner_run_quality_lint <lint-name> -- <command...>
# ---------------------------------------------------------------------------
gated_runner_run_quality_lint() {
  local lint_name="$1"
  shift
  if [[ "${1:-}" == "--" ]]; then
    shift
  fi

  local output
  local exit_code
  set +e
  output="$("$@" 2>&1)"
  exit_code=$?
  set -e

  # Print in full regardless of outcome -- quality-class findings are never
  # hidden, only never fatal.
  printf '%s\n' "$output"

  if [[ $exit_code -ne 0 ]]; then
    echo "[gated-runner] ${lint_name}: quality-class finding(s) (exit ${exit_code}) — non-blocking, continuing." >&2
    local finding
    finding="$(printf '%s' "$output" | _gated_runner_build_finding "$lint_name" "$exit_code")"
    GATED_RUNNER_FINDINGS+=("$finding")
  else
    echo "[gated-runner] ${lint_name}: OK"
  fi
  return 0
}

# ---------------------------------------------------------------------------
# _gated_runner_build_finding <lint-name> <exit-code>
# Reads the lint's captured output from stdin; prints one JSON object to
# stdout (gated_findings.py build).  Internal helper --
# not part of the public API.
# ---------------------------------------------------------------------------
_gated_runner_build_finding() {
  local lint_name="$1"
  local exit_code="$2"
  _gated_runner_findings build "$lint_name" "$exit_code"
}

# ---------------------------------------------------------------------------
# gated_runner_merge_findings_file <path>
# ---------------------------------------------------------------------------
gated_runner_merge_findings_file() {
  local path="$1"
  [[ -f "$path" ]] || return 0

  local line
  while IFS= read -r line; do
    [[ -n "$line" ]] && GATED_RUNNER_FINDINGS+=("$line")
  done < <(_gated_runner_findings split "$path")
  return 0
}

# ---------------------------------------------------------------------------
# gated_runner_write_findings_file <output-path> <runner-name>
# ---------------------------------------------------------------------------
gated_runner_write_findings_file() {
  local out_path="$1"
  local runner_name="$2"

  mkdir -p "$(dirname "$out_path")"

  # The findings go to the writer on stdin, one JSON object per line (never
  # as argv: a lint whose detail spans hundreds of findings can exceed the
  # kernel's argv size limit, aborting the write and leaving a stale file).
  if [[ ${#GATED_RUNNER_FINDINGS[@]} -gt 0 ]]; then
    printf '%s\n' "${GATED_RUNNER_FINDINGS[@]}" | _gated_runner_findings write "$out_path" "$runner_name"
  else
    _gated_runner_findings write "$out_path" "$runner_name" < /dev/null
  fi

  echo "[gated-runner] findings file written: ${out_path} (${#GATED_RUNNER_FINDINGS[@]} finding(s))"
}

# ---------------------------------------------------------------------------
# gated_runner_record_finding <lint-name> <summary>
# ---------------------------------------------------------------------------
gated_runner_record_finding() {
  local lint_name="$1"
  local summary="$2"
  local finding
  finding="$(_gated_runner_findings record "$lint_name" "$summary")"
  GATED_RUNNER_FINDINGS+=("$finding")
}

# ---------------------------------------------------------------------------
# gated_runner_record_instance_finding <lint-name> <cause> <remedy> [KEY=VALUE ...]
# ---------------------------------------------------------------------------
gated_runner_record_instance_finding() {
  local finding=""
  if finding="$(_gated_runner_findings instance "$@")" && [[ -n "$finding" ]]; then
    GATED_RUNNER_FINDINGS+=("$finding")
  else
    echo "[gated-runner] ${1:-?}: could not record a per-instance finding (${2:-no cause given})" >&2
  fi
  return 0
}

# ---------------------------------------------------------------------------
# gated_runner_write_findings_array <output-path>
# ---------------------------------------------------------------------------
gated_runner_write_findings_array() {
  local out_path="$1"
  local rc=0
  if [[ ${#GATED_RUNNER_FINDINGS[@]} -gt 0 ]]; then
    printf '%s\n' "${GATED_RUNNER_FINDINGS[@]}" | _gated_runner_findings array "$out_path" || rc=$?
  else
    _gated_runner_findings array "$out_path" < /dev/null || rc=$?
  fi
  if [[ $rc -ne 0 ]]; then
    echo "[gated-runner] could not write findings file ${out_path} (${#GATED_RUNNER_FINDINGS[@]} finding(s) not recorded)" >&2
  fi
  return 0
}

# ---------------------------------------------------------------------------
# gated_runner_run_instance_check <lint-name> <array-name> <formatter> -- <command...>
# ---------------------------------------------------------------------------
gated_runner_run_instance_check() {
  local lint_name="$1"
  local array_name="$2"
  local formatter="$3"
  shift 3
  if [[ "${1:-}" == "--" ]]; then
    shift
  fi
  # The check fills the global array itself; empty it first so an earlier
  # call's instances are never recorded twice.
  local -n _grl_instances="$array_name"
  _grl_instances=()

  local rc=0
  "$@" 2>&1 || rc=$?

  if [[ $rc -eq 0 ]]; then
    echo "[gated-runner] ${lint_name}: OK"
    return 0
  fi
  if [[ ${#_grl_instances[@]} -eq 0 ]]; then
    gated_runner_record_finding "$lint_name" \
      "${lint_name} returned ${rc} without naming an instance; its own output is printed above"
    echo "[gated-runner] ${lint_name}: quality-class finding(s) (exit ${rc}, no instance named) — non-blocking, continuing." >&2
    return 0
  fi
  local instance
  for instance in "${_grl_instances[@]}"; do
    "$formatter" "$lint_name" "$instance"
  done
  echo "[gated-runner] ${lint_name}: quality-class finding(s) (${#_grl_instances[@]} per-instance) — non-blocking, continuing." >&2
  return 0
}

# ---------------------------------------------------------------------------
# Post-suite hygiene checks
# ---------------------------------------------------------------------------
# Both runners bracket pytest with four report-only checks, each run through
# gated_runner_run_instance_check so every thing it found is its own
# finding. Each check fills a global array (documented at the check itself)
# and has a formatter here that turns one element into one finding:
#
#   lint name             check (library)                     array                        formatter
#   bare_tmp_cleanliness  pgai_tmp_cleanliness_check (temp.sh) PGAI_TMP_RESIDUE_FOUND       gated_runner_tmp_residue_finding
#   listener_cleanliness  pgai_listener_cleanliness_check      PGAI_LISTENER_LEAKS_FOUND    gated_runner_listener_finding
#   sentinel_leak         pgai_sentinel_leak_check             PGAI_SENTINEL_LEAKS_FOUND    gated_runner_sentinel_finding
#                         (sentinel_leak_check.sh)
#   scratch_sweep         pgai_temp_cleanup_tests (temp.sh)    PGAI_TEMP_CLEANUP_LEFTOVERS  gated_runner_scratch_sweep_finding
#
# Each finding names one path or process and a command that inspects it;
# none repairs anything (a leaked live-root sentinel in particular is only
# ever removed by the operator).

# gated_runner_tmp_residue_finding <lint-name> <path>
#   <path> is one residue entry in bare /tmp, as pgai_tmp_cleanliness_check
#   names it. The remedy lists it and searches the test tree for the name's
#   prefix (or for mktemp, for a default tmp.XXXXXX name).
gated_runner_tmp_residue_finding() {
  local lint_name="$1" path="$2"
  local name="${path##*/}"
  local needle="${name%%.*}"
  [[ "$needle" == "tmp" || -z "$needle" ]] && needle="mktemp"
  local tests_dir="${REPO_ROOT:-.}/team/tests"
  gated_runner_record_instance_finding "$lint_name" \
    "${path} appeared in bare /tmp during the suite run, outside the configured temp root; a test wrote it directly instead of through pgai_mktemp / pgai_mktemp_d" \
    "ls -ld -- $(printf '%q' "$path") && grep -rnF -- $(printf '%q' "$needle") $(printf '%q' "$tests_dir")" \
    "kind=bare-tmp-residue" "path=${path}"
}

# gated_runner_listener_finding <lint-name> <pid><TAB><cmdline>
gated_runner_listener_finding() {
  local lint_name="$1" instance="$2"
  local pid="${instance%%$'\t'*}"
  local cmdline=""
  [[ "$instance" == *$'\t'* ]] && cmdline="${instance#*$'\t'}"
  gated_runner_record_instance_finding "$lint_name" \
    "TCP listener pid ${pid} (${cmdline:-cmdline unreadable}) rooted in the project temp subtree survived the suite; the test that started it did not stop it" \
    "ps -o pid,ppid,etime,args -p $(printf '%q' "$pid")" \
    "kind=surviving-listener" "pid=${pid}" "cmdline=${cmdline}"
}

# gated_runner_sentinel_finding <lint-name> <kind><TAB><path><TAB><note>
#   <kind> is added, changed or removed; <note> names the test that wrote
#   it when the provenance log recorded one, and is otherwise empty.
gated_runner_sentinel_finding() {
  local lint_name="$1" instance="$2"
  local kind path note rest
  kind="${instance%%$'\t'*}"
  rest="${instance#*$'\t'}"
  path="${rest%%$'\t'*}"
  note=""
  [[ "$rest" == *$'\t'* ]] && note="${rest#*$'\t'}"
  local inspect
  if [[ "$kind" == "removed" ]]; then
    inspect="ls -la -- $(printf '%q' "${path%/*}")"
  else
    inspect="cat -- $(printf '%q' "$path")"
  fi
  gated_runner_record_instance_finding "$lint_name" \
    "live kanban root sentinel ${path} was ${kind} during the suite run and left as found -- not repaired; restore or remove it by hand and find the fixture that resolved the live root instead of its synthetic board.${note:+ ${note}}" \
    "$inspect" \
    "kind=sentinel-${kind}" "path=${path}"
}

# gated_runner_scratch_sweep_finding <lint-name> <path>
#   <path> is one entry under the temp root's tests/ subtree the post-suite
#   sweep could not remove.
gated_runner_scratch_sweep_finding() {
  local lint_name="$1" path="$2"
  local quoted
  quoted="$(printf '%q' "$path")"
  gated_runner_record_instance_finding "$lint_name" \
    "the post-suite scratch sweep could not remove ${path}, even after restoring owner-write on it" \
    "chmod -R u+w -- ${quoted} && rm -rf -- ${quoted}" \
    "kind=scratch-leftover" "path=${path}"
}

# ---------------------------------------------------------------------------
# gated_runner_clear_run_evidence <temp-root> <suite>
#
# Every evidence path a certification run writes is fixed and shared by
# every run on the host, and gated_runner_merge_findings_file merges any
# per-lint file it finds -- so a lint that crashed, was skipped, or writes
# only when it has findings would otherwise hand an earlier run's content
# to this run's aggregate. Removing the suite's files first means a file
# present at one of those paths after the run is this run's own.
#
# The coverage data directory itself is kept (run-gated-suites.sh creates
# it before the runners start and points COVERAGE_FILE into it); only the
# files in it go.
# ---------------------------------------------------------------------------
gated_runner_clear_run_evidence() {
  local temp_root="${1:-}"
  local suite="${2:-}"
  if [[ -z "$temp_root" || ! "$suite" =~ ^(unit|integration)$ ]]; then
    local scripts_dir
    scripts_dir="$(cd "${_GATED_RUNNER_LINT_LIB_DIR}/.." && pwd)"
    # CONVERT: deleting nothing leaves the earlier run's files where they
    # were -- a stale-evidence risk, never a runtime failure -- so the
    # caller's run goes on. Fails closed: an unexpected scope removes nothing.
    # CENSUS-ANCHOR: gated-runner-run-evidence-01
    pgai_emit_error_contract \
      "gated_runner.run_evidence_scope" \
      "gated_runner_clear_run_evidence was given temp root '${temp_root}' and suite '${suite}'; it removes earlier runs' evidence only for a non-empty temp root and the suite unit or integration, so nothing was removed and files an earlier run left may still be read." \
      "grep -n gated_runner_clear_run_evidence ${scripts_dir}/run-unit-tests.sh ${scripts_dir}/run-integration-tests.sh"
    return 0
  fi

  local -a earlier=()
  local path
  for path in \
    "${temp_root}/lint/"*_"${suite}".json \
    "${temp_root}/lint/"*-"${suite}".json \
    "${temp_root}/lint/"*-"${suite}".json.runid \
    "${temp_root}/coverage/${suite}.json" \
    "${temp_root}/coverage/${suite}-data/".coverage*; do
    # An unmatched glob stays literal; only an existing file is listed.
    if [[ -f "$path" || -L "$path" ]]; then
      earlier+=("$path")
    fi
  done
  local removed=0 kept=0
  for path in "${earlier[@]}"; do
    if rm -f -- "$path" 2>/dev/null && [[ ! -e "$path" && ! -L "$path" ]]; then
      removed=$((removed + 1))
    else
      kept=$((kept + 1))
      echo "[gated-runner] run evidence: could not remove ${path}, left by an earlier run; it may be read as this run's." >&2
    fi
  done
  local not_removed=""
  if [[ $kept -gt 0 ]]; then
    not_removed="; ${kept} could not be removed"
  fi
  echo "[gated-runner] run evidence: this ${suite} run writes its findings to ${temp_root}/lint/findings-${suite}.json (per-lint files ${temp_root}/lint/*_${suite}.json) and its coverage to ${temp_root}/coverage/${suite}.json; removed ${removed} file(s) an earlier run left there${not_removed}."
  return 0
}

# ---------------------------------------------------------------------------
# gated_runner_claim_findings_file <output-path> <runner-name>
#
# The run token combines a UTC timestamp (nanosecond resolution) with this
# process's own PID -- unique per invocation on a single host without
# requiring a new dependency (uuid generation is not a shell builtin).
#
# Callers capture the token via command substitution
# (token="$(gated_runner_claim_findings_file ...)"), so the claim-time
# call to gated_runner_write_findings_file below is redirected to stderr:
# left on stdout, its own "[gated-runner] findings file written: ..."
# narration line would be captured into the token string too, corrupting
# it. The real, final write later (at the exit hook, called directly --
# not inside a substitution) still narrates on stdout exactly as before.
# ---------------------------------------------------------------------------
gated_runner_claim_findings_file() {
  local out_path="$1"
  local runner_name="$2"
  local token
  token="$(date -u +%Y%m%dT%H%M%S.%N)-$$"
  mkdir -p "$(dirname "$out_path")"
  printf '%s' "$token" > "${out_path}.runid"
  gated_runner_write_findings_file "$out_path" "$runner_name" 1>&2
  printf '%s' "$token"
}

# ---------------------------------------------------------------------------
# gated_runner_assert_findings_claim <output-path> <expected-token>
# ---------------------------------------------------------------------------
gated_runner_assert_findings_claim() {
  local out_path="$1"
  local expected_token="$2"
  local claim_path="${out_path}.runid"
  local actual_token=""
  [[ -f "$claim_path" ]] && actual_token="$(cat "$claim_path" 2>/dev/null || true)"

  if [[ "$actual_token" != "$expected_token" ]]; then
    # BLOCK-WORTHY (team/SOP-core.md "Gates and Refusals" -- an
    # approval/invariant by design): trusting a findings file whose
    # run-identity claim has moved would misattribute a DIFFERENT run's
    # quality-class state (or bugs filed from it) to this one, on a host
    # where PGAI_AGENT_KANBAN_TEMP_DIR is shared across every worktree and
    # project -- this is never a quality-class finding, it is a refusal to
    # trust data that may not describe the run being verified.
    # CENSUS-ANCHOR: gated-runner-stale-findings-claim-01
    pgai_emit_error_contract \
      "gated_runner.stale_findings_claim" \
      "The findings file at ${out_path} no longer carries this invocation's run-identity claim (this run's token: ${expected_token}; the file's current token: ${actual_token:-<absent>}) -- another invocation of a gated runner, sharing the same host-wide PGAI_AGENT_KANBAN_TEMP_DIR, reclaimed the same path after this run started." \
      "Re-run the gated runner that owns ${out_path} (run-unit-tests.sh or run-integration-tests.sh) by itself, with no other invocation of either script writing into the same PGAI_AGENT_KANBAN_TEMP_DIR at the same time, then re-read the resulting file."
    return 1
  fi
  return 0
}

# ---------------------------------------------------------------------------
# Replay-line environment parity
# ---------------------------------------------------------------------------
# Both gated runners set a fixed, known family of variables ahead of their
# pytest launch. Some stay exported through the pytest call (PYTEST_ADDOPTS,
# the PGAI_* path/seam variables); others are deliberately local/unexported
# or already unset again by the time pytest actually starts (KANBAN_ROOT and
# PYTHONHASHSEED -- both unset by each runner ahead of its later pre-flight
# lints; PGAI_LINT_CHANGELOG_MODE -- read only by the operator-run
# lint_changelog_freshness.py, which no runner sets; GIT_GUARD_REAL_GIT
# and PGAI_GIT_GUARD_DEPTH -- both scrubbed by each runner's own
# "Scrub the git-boundary-guard's own exported variables" block immediately
# ahead of the pytest launch, so the guard's stub-git test fixtures are
# reached independent of whatever the wake layer pinned for the rest of the
# agent session). Both states matter to reproducing the run: an operator's
# own shell may hold some of these ambiently exported already (KANBAN_ROOT
# in particular, from having sourced shell-env) -- pasting the bare pytest
# argv into such a shell runs under a DIFFERENT environment than the one the
# runner actually used.
#
# Adding a new family variable to either runner: add its name here once --
# both runners call gated_runner_replay_command, so one edit covers both.
GATED_RUNNER_REPLAY_ENV_NAMES=(
  PYTEST_ADDOPTS
  PYTHONHASHSEED
  PGAI_LINT_CHANGELOG_MODE
  KANBAN_ROOT
  PGAI_DEV_TREE_PATH
  PGAI_AGENT_KANBAN_TEMP_DIR
  PGAI_AGENT_KANBAN_ROOT_PATH
  PGAI_CRONTAB_CMD
  PGAI_CRONTAB_SEAM_STATE
  GIT_GUARD_REAL_GIT
  PGAI_GIT_GUARD_DEPTH
)

# ---------------------------------------------------------------------------
# gated_runner_replay_command <pytest-invocation>
#
# Prints, on stdout, <pytest-invocation> prefixed with an `env` call that
# pins every name in GATED_RUNNER_REPLAY_ENV_NAMES to this process's own
# current state at the point of the call:
#   - exported here  -> carried as a literal NAME='value' assignment, so a
#     paste reproduces the same value regardless of what the operator's own
#     shell exports for that name.
#   - not exported here (unset, or set but never exported) -> carried as an
#     `env -u NAME`, so a paste is not silently handed a DIFFERENT value the
#     operator's shell happens to export for that name (KANBAN_ROOT is the
#     motivating case: this library's callers assign it unexported and
#     unset it again before pytest runs, on purpose).
# Values are shell-quoted via `printf %q` so a path or seam value containing
# spaces or shell metacharacters still pastes and runs correctly.
# ---------------------------------------------------------------------------
gated_runner_replay_command() {
  local pytest_invocation="$1"
  # GNU env requires every `-u NAME` option to precede every `NAME=VALUE`
  # assignment -- once env sees an assignment token it stops parsing
  # options, so a later `-u` is read as the command to exec instead
  # ("env: '-u': No such file or directory"). Two passes keep unset_terms
  # first and assign_terms second regardless of GATED_RUNNER_REPLAY_ENV_NAMES'
  # own ordering.
  local -a unset_terms=()
  local -a assign_terms=()
  local name value declare_output
  for name in "${GATED_RUNNER_REPLAY_ENV_NAMES[@]}"; do
    # Classify by the export attribute alone, tolerating any other
    # attribute letter a name also carries (readonly, integer, array,
    # lowercase, ...): `declare -p` prints every attribute a variable
    # holds as one run of letters right after `declare -` (`-x`, `-rx`,
    # `-ix`, `-ax`, ...), so matching the literal prefix `declare -x `
    # missed every name that is exported AND carries a second attribute --
    # `declare -p` orders letters alphabetically, so export's `x` is not
    # reliably first. Capturing that whole attribute run and checking it
    # for an `x` anywhere decides export status independent of what else
    # is set.
    declare_output="$(declare -p "$name" 2>/dev/null)" || declare_output=""
    if [[ "$declare_output" =~ ^declare\ ([^\ ]+)\  ]] && [[ "${BASH_REMATCH[1]}" == *x* ]]; then
      value="${!name}"
      assign_terms+=("$(printf '%s=%q' "$name" "$value")")
    else
      unset_terms+=("-u" "$name")
    fi
  done
  if [[ ${#unset_terms[@]} -eq 0 && ${#assign_terms[@]} -eq 0 ]]; then
    printf '%s' "$pytest_invocation"
  else
    printf 'env %s %s %s' "${unset_terms[*]}" "${assign_terms[*]}" "$pytest_invocation"
  fi
}

# ---------------------------------------------------------------------------
# Per-invocation pytest basetemp
# ---------------------------------------------------------------------------
# pytest removes and recreates whatever --basetemp it is handed at session
# start. Any invocation that shares one basetemp path with another live
# invocation therefore deletes that other run's fixture directories out from
# under it, and the other run fails on whichever tmp_path-backed test it
# touches next -- indistinguishable from a flake. Both gated runners
# therefore claim a directory that belongs to THIS invocation alone through
# gated_runner_pytest_basetemp, and release it on their own exit path
# through gated_runner_pytest_basetemp_cleanup.
#
# gated_runner_pytest_basetemp <suite>
#   Creates, and prints on stdout, a fresh directory of the shape
#       ${PGAI_AGENT_KANBAN_TEMP_DIR}/pytest/<suite>-<scope>.XXXXXX
#   where <scope> is PGAI_TASK_ID when the wake layer exported one (so a
#   directory seen on disk names the task that made it) and pid<pid> of the
#   calling runner otherwise. The random suffix is mktemp's, so two
#   invocations under the SAME scope -- a nested runner spawned by an
#   integration test, or a second run started before the first finished --
#   still resolve different directories. The temp root is resolved through
#   pgai_temp_dir, so temp.sh must already be sourced (both gated runners
#   source it before this file); a caller that has not is refused with a
#   message naming the missing lib rather than handed a guessed path.
#   Because PYTEST_ADDOPTS is in GATED_RUNNER_REPLAY_ENV_NAMES, the runner's
#   "Replay this exact run:" line names the exact directory this run used;
#   pasting that line recreates it (pytest makes its basetemp on startup,
#   and the pytest/ parent survives the cleanup below).
#
# gated_runner_pytest_basetemp_cleanup <path>
#   Removes <path> if it exists, through pgai_temp_cleanup so nothing
#   outside the framework temp root is ever removed. Wired into each
#   runner's exit hook so that once a runner exits, the pytest/ subtree
#   holds nothing from that invocation -- the temp root never accumulates
#   one directory per historical run. Owner-write is restored across the
#   subtree first, so a read-only leftover from a failing test cannot make
#   the removal partial. An empty <path> (the runner exited before it
#   claimed a basetemp) is a silent no-op, and no failure inside this helper
#   reaches the caller: it always returns 0, because releasing scratch must
#   never change a runner's own exit code.
# ---------------------------------------------------------------------------
gated_runner_pytest_basetemp() {
  local suite="${1:-}"
  if [[ -z "$suite" ]]; then
    echo "gated_runner_lint.sh: gated_runner_pytest_basetemp requires a suite name (e.g. unit or integration)" >&2
    return 1
  fi
  if ! declare -F pgai_temp_dir >/dev/null 2>&1; then
    echo "gated_runner_lint.sh: gated_runner_pytest_basetemp needs pgai_temp_dir; source team/scripts/lib/temp.sh before calling it" >&2
    return 1
  fi
  local parent scope
  parent="$(pgai_temp_dir)/pytest"
  mkdir -p "$parent"
  scope="${PGAI_TASK_ID:-pid$$}"
  # Keep the on-disk label filesystem-plain whatever the environment holds.
  scope="${scope//[^A-Za-z0-9._-]/_}"
  mktemp -d "${parent}/${suite}-${scope}.XXXXXX"
}

gated_runner_pytest_basetemp_cleanup() {
  local path="${1:-}"
  [[ -n "$path" && -e "$path" ]] || return 0
  if ! declare -F pgai_temp_cleanup >/dev/null 2>&1; then
    echo "gated_runner_lint.sh: gated_runner_pytest_basetemp_cleanup left ${path} in place: pgai_temp_cleanup is not sourced (team/scripts/lib/temp.sh), and nothing is removed without its temp-root guard" >&2
    return 0
  fi
  chmod -R u+w "$path" 2>/dev/null || true
  pgai_temp_cleanup "$path" || true
  return 0
}

# ---------------------------------------------------------------------------
# gated_runner_tmux_socket_sweep
#
#   Removes stale private tmux sockets that test runs left in the current
#   user's tmux directory ($TMUX_TMPDIR/tmux-<uid>, /tmp when TMUX_TMPDIR is
#   unset): only files named pgai-*, only sockets with no live server, and
#   only ones at least a minute old, so a server another run is still
#   starting is left alone. It never kills a server and never touches a
#   socket under any other name. Prints one "[gated-runner]
#   tmux_socket_sweep: ..." line. Wired into each runner's exit hook; like
#   gated_runner_pytest_basetemp_cleanup it always returns 0, because a
#   cleanup step must never change a runner's own exit code. The rules live
#   in pgai_agent_kanban.testing.tmux_sockets.
# ---------------------------------------------------------------------------
gated_runner_tmux_socket_sweep() {
  if ! declare -F pp_run_ops >/dev/null 2>&1; then
    # shellcheck source=pp_run_ops.sh
    source "${_GATED_RUNNER_LINT_LIB_DIR}/pp_run_ops.sh" 2>/dev/null || {
      echo "[gated-runner] tmux_socket_sweep: skipped (pp_run_ops.sh not found beside gated_runner_lint.sh)"
      return 0
    }
  fi
  local line
  line="$(pp_run_ops pgai_agent_kanban.testing.tmux_sockets sweep 2>&1)" \
    || line="tmux_socket_sweep: skipped (${line:-the sweep exited non-zero})"
  echo "[gated-runner] ${line}"
  return 0
}

# ---------------------------------------------------------------------------
# Targeted (non-certification) runs
# ---------------------------------------------------------------------------
# Both gated runners accept `--files <path> [<path> ...]`: pytest runs only
# the named test files, the fatal pre-flight gates still run, the
# whole-tree quality-class lint block is skipped, and the run's exit code
# goes to its own targeted-<suite>.exit file instead of anything a
# certification run writes. A targeted run never claims or writes the
# aggregate findings file (lint/findings-<suite>.json), never reads or
# writes the green ledger, and never hands anything to the aggregate bug
# filer -- that file and the bugs filed from it describe certification runs
# only, which is what TESTER reads.
#
# The two test-authoring lints that judge one file at a time -- the
# anti-pattern lint and the skip-cites-a-real-bug gate -- DO run on a
# targeted run, over exactly the named files (gated_runner_targeted_file_lints
# below), so the author of a test file sees its findings in the same session
# rather than at certification. Their findings print in full and make the
# targeted run exit non-zero; they are read from a scratch findings file
# that is removed afterwards, and nothing about them is recorded anywhere.
#
# gated_runner_argv_requests_targeted_run <arg...>
#   Returns 0 when any argument is exactly `--files`, 1 otherwise. Each
#   runner calls this on its own "$@" BEFORE claiming the findings file --
#   the claim happens ahead of argument parsing so no early abort can leave a
#   prior run's findings file in place, which means the targeted/certification
#   decision has to be known before the parse loop runs. The flags that take
#   a value of their own -- --workers (decimal digits only) and --base (a git
#   ref, refused when it starts with "-") -- never accept `--files` as that
#   value, so a literal `--files` token is unambiguous.
#
# gated_runner_targeted_resolve_path <team-dir> <path> <test-root>...
#   Classifies one --files argument against the runner's own test roots
#   (team-relative directories such as tests/unit). <path> may be relative to
#   <team-dir> (tests/unit/test_x.py), relative to the repo root
#   (team/tests/unit/test_x.py), or absolute. It is normalized with
#   `realpath -m` -- `..` collapsed, symlinks resolved -- so a path that
#   only LOOKS like it sits under a test root (tests/../../etc/passwd) is
#   judged by where it really points. Prints the team-relative form on
#   stdout and returns:
#     0  inside a test root and an existing regular file
#     3  outside every test root
#     4  inside a test root but not an existing regular file
#   Never prints a diagnostic: the calling runner owns the refusal and its
#   dual-channel error contract.
#
# gated_runner_write_targeted_exit_file <path> <exit-code>
#   Writes "<exit-code>\n" to <path>, creating its parent. Wired into each
#   runner's exit hook on a targeted run, so every exit path -- a refusal, a
#   fatal gate, a pytest failure, a pass -- records its code; a pre-merge
#   run's exit hook writes its own pre-merge exit file through it the same
#   way. Never fails the caller: a write failure is narrated on stderr and
#   swallowed.
#
# gated_runner_targeted_file_lints <anti-pattern-lint> <skip-gate> <file>...
#   Runs the two per-file test-authoring lints over exactly <file>... --
#   `python3 <anti-pattern-lint> --findings-file <scratch> --files <file>...`
#   and `bash <skip-gate> --findings-file <scratch> --files <file>...` --
#   streaming each lint's output in full. Both lints exit 0 on findings, so
#   the verdict is read from each one's findings file: one
#   "[targeted-lint] <name>: OK", "[targeted-lint] <name>: finding(s) in the
#   named files (N)", or -- when the lint itself exited non-zero, so the
#   named files were not checked -- "[targeted-lint] <name>: could not check
#   the named files (exit N)" line. <file>... are the paths the runner
#   already validated (absolute paths are clearest in the findings); neither
#   lint walks a directory or scans runtime scripts in this mode. The
#   scratch findings files live in one pgai_mktemp_d directory (temp.sh must
#   be sourced) that is removed before returning.
#   Sets two parallel arrays for the caller's verdict:
#     GATED_RUNNER_TARGETED_LINT_FAILURES  names of the lints that reported
#                                          a finding or could not check
#                                          (test_anti_patterns,
#                                          skip_cites_real_bug); empty when
#                                          both were clean
#     GATED_RUNNER_TARGETED_LINT_COMMANDS  the shell-quoted command that
#                                          reproduces each failing lint's
#                                          printed findings (no
#                                          --findings-file)
#   Always returns 0 and never touches GATED_RUNNER_FINDINGS, the findings
#   file, or the ledger: the caller decides the exit code after pytest.
# ---------------------------------------------------------------------------
gated_runner_argv_requests_targeted_run() {
  local arg
  for arg in "$@"; do
    [[ "$arg" == "--files" ]] && return 0
  done
  return 1
}

# ---------------------------------------------------------------------------
# Help invocations
# ---------------------------------------------------------------------------
# gated_runner_argv_requests_help <arg...>
#   Returns 0 when any argument is exactly `--help` or `-h`, 1 otherwise.
#   Each runner calls this on its own "$@" BEFORE claiming the findings file,
#   for the same reason as gated_runner_argv_requests_targeted_run: the claim
#   happens ahead of argument parsing. When it returns 0 the runner skips the
#   claim and registers no exit hook, so printing usage never replaces an
#   existing lint/findings-<suite>.json with an empty one, never rewrites its
#   .runid claim, never appends to the green ledger, and never writes a
#   targeted-<suite>.exit file.
#
#   This is safe because an argv holding `--help`/`-h` always ends inside the
#   parse loop: either at the help arm (usage, exit 0) or, when an
#   unrecognized argument precedes it, at the unknown-argument refusal
#   (exit 1). Neither reaches a gate, a lint or pytest, so no run that should
#   have been recorded goes unrecorded. No runner flag takes a value that
#   could legitimately be `--help` or `-h` (--files stops consuming paths at
#   the first argument starting with "-", --workers accepts decimal digits
#   only, and --base refuses a value starting with "-", each refusing inside
#   the parse loop), so the match is unambiguous.
gated_runner_argv_requests_help() {
  local arg
  for arg in "$@"; do
    [[ "$arg" == "--help" || "$arg" == "-h" ]] && return 0
  done
  return 1
}

# ---------------------------------------------------------------------------
# Argument and pytest refusals shared by the four runners
# ---------------------------------------------------------------------------
# gated_runner_reject_unknown_arg <script-path> <arg> <example-command>
#   Partial remedy (docs/coding-standards.md "Gates prescribe, not just
#   diagnose"): the offending argument is named deterministically, but which
#   valid flag the operator meant is intent the runner cannot resolve, so
#   --help is the next step. Prints only; the caller exits 1.
gated_runner_reject_unknown_arg() {
  local script="${1:-}" arg="${2:-}" example="${3:-}"
  echo "FAIL: unrecognized argument '${arg}'." >&2
  echo "" >&2
  echo "Cause(s):" >&2
  echo "  - '${arg}' does not match any flag this runner accepts." >&2
  echo "" >&2
  echo "To fix:" >&2
  echo "  1. bash \"${script}\" --help          # list every accepted flag" >&2
  echo "  2. Re-run with the corrected flag, e.g.: ${example}" >&2
  echo "" >&2
  echo "Rule: this runner accepts only the flags listed in its --help output; an" >&2
  echo "unrecognized argument is rejected rather than silently ignored." >&2
}

# gated_runner_pytest_available
#   Returns 0 when pytest can run: a pytest executable on PATH, or
#   `python3 -m pytest --version` succeeding. 1 otherwise.
gated_runner_pytest_available() {
  command -v pytest >/dev/null 2>&1 && return 0
  python3 -m pytest --version >/dev/null 2>&1
}

# gated_runner_report_pytest_missing <rerun-command> <what-cannot-run>
#   Full remedy: pytest's absence has one universal, verbatim-runnable fix.
#   Prints the diagnosis naming <what-cannot-run> (e.g. "the test suite") and
#   <rerun-command> as the second step. Prints only; the caller records its
#   finding and exits 2.
gated_runner_report_pytest_missing() {
  local rerun="${1:-}" what="${2:-the test suite}"
  echo "FAIL: pytest is not installed in this Python environment." >&2
  echo "" >&2
  echo "Cause(s):" >&2
  echo "  - no 'pytest' executable found on PATH" >&2
  echo "  - 'python3 -m pytest --version' also failed (pytest not importable)" >&2
  echo "" >&2
  echo "To fix:" >&2
  echo "  1. pip install pytest" >&2
  echo "  2. ${rerun}" >&2
  echo "" >&2
  echo "Rule: this runner requires pytest as a hard dependency; it cannot execute" >&2
  echo "${what} without it." >&2
}

# ---------------------------------------------------------------------------
# Pre-merge runs
# ---------------------------------------------------------------------------
# Both gated runners accept `--pre-merge [--base REF]`: the cheap whole-tree
# checks a change should pass before it merges, run by the pre-merge engine
# (team/pgai_agent_kanban/testing/pre_merge.py, called as a module). The unit
# runner's run covers the by-path module test, the six ratchet lints and the
# tracked-files lint (lint_tracked_ignored.py), the four always-run whole-tree
# guards (env-bootstrap, operator-verb-docs,
# dashboard-window-docs, docs-citations -- lint_env_bootstrap.py,
# generate_operator_verb_docs.py --check, dashboard/generate_window_docs.py
# --check and check_docs_citations.py, run whatever the diff) and the
# affected unit tests; the integration runner's covers the affected
# integration tests only. Neither runs the fatal pre-flight gates or the
# quality-class lint block; the engine runs those four guard scripts
# itself. The run's exit code goes to its own exit file
# (pre-merge.exit for the unit runner, pre-merge-integration.exit for the
# integration runner) through gated_runner_write_targeted_exit_file, and
# nothing a certification or targeted run writes is touched: no findings
# file or .runid claim, no green ledger, no targeted-<suite>.exit. Nothing
# in the release path reads either pre-merge exit file.
#
# gated_runner_argv_requests_pre_merge <arg...>
#   Returns 0 when any argument is exactly `--pre-merge`, 1 otherwise. Each
#   runner calls this on its own "$@" BEFORE claiming the findings file, for
#   the same reason as gated_runner_argv_requests_targeted_run. No flag takes
#   `--pre-merge` as its value (see that function's comment), so the match
#   is unambiguous.
gated_runner_argv_requests_pre_merge() {
  local arg
  for arg in "$@"; do
    [[ "$arg" == "--pre-merge" ]] && return 0
  done
  return 1
}

gated_runner_targeted_resolve_path() {
  local team_dir="$1"
  local path="$2"
  shift 2
  local team_real candidate resolved root root_real rel
  team_real="$(realpath -m -- "$team_dir")"
  if [[ "$path" == /* ]]; then
    candidate="$path"
  elif [[ "$path" == team/* && ! -e "${team_real}/${path}" ]]; then
    # Repo-root-relative form: strip the leading team/ the runner's own
    # working directory already supplies.
    candidate="${team_real}/${path#team/}"
  else
    candidate="${team_real}/${path}"
  fi
  resolved="$(realpath -m -- "$candidate")"
  for root in "$@"; do
    root_real="$(realpath -m -- "${team_real}/${root}")"
    if [[ "$resolved" == "$root_real" || "$resolved" == "${root_real}/"* ]]; then
      rel="${resolved#"${team_real}/"}"
      printf '%s' "$rel"
      [[ -f "$resolved" ]] && return 0
      return 4
    fi
  done
  printf '%s' "$path"
  return 3
}

gated_runner_write_targeted_exit_file() {
  local out_path="$1"
  local exit_code="$2"
  if ! { mkdir -p "$(dirname "$out_path")" && printf '%s\n' "$exit_code" > "$out_path"; } 2>/dev/null; then
    echo "[gated-runner] could not write targeted exit file ${out_path} (exit code ${exit_code}); the run's own exit status is unaffected" >&2
  fi
  return 0
}

gated_runner_targeted_file_lints() {
  local ap_lint="$1"
  local skip_gate="$2"
  shift 2
  GATED_RUNNER_TARGETED_LINT_FAILURES=()
  GATED_RUNNER_TARGETED_LINT_COMMANDS=()
  local -a names=("test_anti_patterns" "skip_cites_real_bug")
  local -a labels=("test anti-pattern lint" "skip-cites-real-bug gate")
  local -a cmd shown
  local i rc count scratch="" findings
  if declare -F pgai_mktemp_d >/dev/null 2>&1; then
    scratch="$(pgai_mktemp_d targeted_lint_findings 2>/dev/null)" || scratch=""
  fi
  if [[ -z "$scratch" ]]; then
    echo "gated_runner_lint.sh: gated_runner_targeted_file_lints needs a scratch directory from pgai_mktemp_d; source team/scripts/lib/temp.sh before calling it" >&2
  fi
  for i in 0 1; do
    if [[ $i -eq 0 ]]; then
      shown=(python3 "$ap_lint" --files "$@")
    else
      shown=(bash "$skip_gate" --files "$@")
    fi
    findings="${scratch:+${scratch}/${names[$i]}.json}"
    cmd=("${shown[@]:0:2}")
    [[ -n "$findings" ]] && cmd+=(--findings-file "$findings")
    cmd+=("${shown[@]:2}")
    echo "Running ${labels[$i]} over the named files..."
    rc=0
    "${cmd[@]}" || rc=$?
    count=0
    if [[ -n "$findings" ]]; then
      count="$(_gated_runner_findings count "$findings" 2>/dev/null)" || count=0
      [[ "$count" =~ ^[0-9]+$ ]] || count=0
    fi
    if [[ $rc -ne 0 || -z "$findings" ]]; then
      echo "[targeted-lint] ${names[$i]}: could not check the named files (exit ${rc})"
    elif [[ $count -gt 0 ]]; then
      echo "[targeted-lint] ${names[$i]}: finding(s) in the named files (${count})"
    else
      echo "[targeted-lint] ${names[$i]}: OK"
      continue
    fi
    GATED_RUNNER_TARGETED_LINT_FAILURES+=("${names[$i]}")
    GATED_RUNNER_TARGETED_LINT_COMMANDS+=("$(gated_runner_quote_argv "${shown[@]}")")
  done
  if [[ -n "$scratch" ]] && declare -F pgai_temp_cleanup >/dev/null 2>&1; then
    pgai_temp_cleanup "$scratch" || true
  fi
  return 0
}

# ---------------------------------------------------------------------------
# Quarantine marker selection
# ---------------------------------------------------------------------------
# Tests whose verdict depends on test order or wall-clock timing rather than
# on the code under test carry `@pytest.mark.quarantine(reason="...")`, with
# the marker registered in team/pyproject.toml. Every run of either gated
# runner passes a -m expression built here:
#
#   default            -m "not quarantine"  -- quarantined tests are
#                                              deselected, so a flaky
#                                              verdict never reaches a
#                                              certification run
#   --quarantined      -m "quarantine"      -- exactly the quarantined tests
#                                              run, and nothing else
#
# The expression composes with --files: a targeted run honours the default
# exclusion, and `--files <path> --quarantined` runs only the quarantined
# tests inside the named files. A run whose marker selection leaves nothing
# to run (pytest exit code 5 -- every test in the named files is quarantined, or
# --quarantined found none) is a clean, narrated exit 0 rather than a
# failure; each runner handles that at its own pytest verdict.
#
# A --quarantined run is not a certification run of the suite: it exercises
# only the quarantined subset, so its result says nothing about the rest.
#
# gated_runner_quarantine_marker_expression <quarantined:true|false>
#   Prints the -m expression for the run: "quarantine" when the argument is
#   exactly "true", "not quarantine" otherwise. Never fails.
#
# gated_runner_quote_argv <arg...>
#   Prints every argument shell-quoted with printf %q, space-separated, with
#   no trailing space -- the argv text a pasted replay line needs. Arguments
#   that need no quoting (-p, no:randomly, tests/unit/) print unchanged.
# ---------------------------------------------------------------------------
gated_runner_quarantine_marker_expression() {
  if [[ "${1:-false}" == "true" ]]; then
    printf '%s' "quarantine"
  else
    printf '%s' "not quarantine"
  fi
}

gated_runner_quote_argv() {
  local quoted
  [[ $# -eq 0 ]] && return 0
  printf -v quoted '%q ' "$@"
  printf '%s' "${quoted% }"
}
