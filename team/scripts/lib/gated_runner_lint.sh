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
# anti-pattern, orphan-test, help-presence, doc/CHANGELOG/ICD freshness,
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
# stdout.  Internal helper -- not part of the public API.
#
# The Python script is passed via `-c` (an argument), not via a `<<PYEOF`
# heredoc.  A heredoc would redirect stdin to the script's own source text
# for the `python3 -` invocation, leaving nothing for the script's own
# `sys.stdin.read()` to consume -- silently producing an empty "detail" for
# every finding.  `-c` reads the script from argv instead, so stdin stays
# free for the piped detail text.
# ---------------------------------------------------------------------------
_gated_runner_build_finding() {
  local lint_name="$1"
  local exit_code="$2"
  python3 -c '
import json
import sys

lint_name, exit_code = sys.argv[1], int(sys.argv[2])
detail = sys.stdin.read()
summary_line = next((line.strip() for line in detail.splitlines() if line.strip()), "")
obj = {
    "lint": lint_name,
    "severity": "quality",
    "exit_code": exit_code,
    "summary": summary_line or f"{lint_name} exited {exit_code}",
    "detail": detail,
}
print(json.dumps(obj))
' "$lint_name" "$exit_code"
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
  done < <(python3 - "$path" <<'PYEOF'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as f:
    items = json.load(f)
for item in items:
    print(json.dumps(item))
PYEOF
)
  return 0
}

# ---------------------------------------------------------------------------
# gated_runner_write_findings_file <output-path> <runner-name>
# ---------------------------------------------------------------------------
gated_runner_write_findings_file() {
  local out_path="$1"
  local runner_name="$2"

  mkdir -p "$(dirname "$out_path")"

  local findings_json="[]"
  if [[ ${#GATED_RUNNER_FINDINGS[@]} -gt 0 ]]; then
    findings_json="$(printf '%s\n' "${GATED_RUNNER_FINDINGS[@]}" | python3 -c '
import json
import sys

items = [json.loads(line) for line in sys.stdin if line.strip()]
print(json.dumps(items))
')"
  fi

  python3 - "$runner_name" "$out_path" "$findings_json" <<'PYEOF'
import json
import sys
from datetime import datetime, timezone

runner_name, out_path, findings_raw = sys.argv[1], sys.argv[2], sys.argv[3]
findings = json.loads(findings_raw)
doc = {
    "schema_version": 1,
    "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "runner": runner_name,
    "findings": findings,
}
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(doc, f, indent=2, sort_keys=True)
    f.write("\n")
PYEOF

  echo "[gated-runner] findings file written: ${out_path} (${#GATED_RUNNER_FINDINGS[@]} finding(s))"
}
