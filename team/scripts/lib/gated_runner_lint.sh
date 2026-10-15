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
# those keys, it only appends the object as-is.
#
# Consumers (TESTER's "Lint Findings (non-blocking)" report section, or any
# other automated reader) should treat "lint" and "severity" as the only
# guaranteed keys and read the rest defensively.

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
    source "$(dirname "${BASH_SOURCE[0]}")/pp_run_ops.sh" 2>/dev/null || {
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
# targeted run exit non-zero; nothing about them is recorded anywhere.
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
#   `python3 <anti-pattern-lint> --files <file>...` and
#   `bash <skip-gate> --files <file>...` -- streaming each lint's output in
#   full, then one "[targeted-lint] <name>: OK" or "[targeted-lint] <name>:
#   finding(s) in the named files (exit N)" line. <file>... are the paths the
#   runner already validated (absolute paths are clearest in the findings);
#   neither lint walks a directory or scans runtime scripts in this mode.
#   Sets two parallel arrays for the caller's verdict:
#     GATED_RUNNER_TARGETED_LINT_FAILURES  names of the lints that exited
#                                          non-zero (test_anti_patterns,
#                                          skip_cites_real_bug); empty when
#                                          both were clean
#     GATED_RUNNER_TARGETED_LINT_COMMANDS  the shell-quoted command that
#                                          reproduces each failing lint
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
# Pre-merge runs
# ---------------------------------------------------------------------------
# Both gated runners accept `--pre-merge [--base REF]`: the cheap whole-tree
# checks a change should pass before it merges, run by the pre-merge engine
# (team/pgai_agent_kanban/testing/pre_merge.py, called as a module). The unit
# runner's run covers the by-path module test, the five ratchet lints and the
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
  local -a cmd
  local i rc
  for i in 0 1; do
    if [[ $i -eq 0 ]]; then
      cmd=(python3 "$ap_lint" --files "$@")
    else
      cmd=(bash "$skip_gate" --files "$@")
    fi
    echo "Running ${labels[$i]} over the named files..."
    set +e
    "${cmd[@]}"
    rc=$?
    set -e
    if [[ $rc -ne 0 ]]; then
      echo "[targeted-lint] ${names[$i]}: finding(s) in the named files (exit ${rc})"
      GATED_RUNNER_TARGETED_LINT_FAILURES+=("${names[$i]}")
      GATED_RUNNER_TARGETED_LINT_COMMANDS+=("$(gated_runner_quote_argv "${cmd[@]}")")
    else
      echo "[targeted-lint] ${names[$i]}: OK"
    fi
  done
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
