#!/usr/bin/env bash
# team/scripts/lib/green_ledger.sh
#
# Per-RC green ledger for the gated test runners (run-unit-tests.sh,
# run-integration-tests.sh), and the --reuse-if-green read path both runners
# share.
#
# WHAT IT IS FOR
# --------------
# Every certification run of either gated runner (any run without --files)
# appends one record to a ledger: which commit it ran at, which suite, its
# exit code, and a hash of the quality-class lint findings it recorded
# before pytest. A later run of the same runner given --reuse-if-green
# consults the ledger after its own quality-class lints have run and,
# when the ledger already holds a green run of the same suite at the same
# commit with the same findings hash, returns that recorded green result
# without starting pytest. It prints a line containing "reused" when it does.
#
# LEDGER LOCATION
# ---------------
#   ${PGAI_AGENT_KANBAN_TEMP_DIR}/projects/<project>/green-ledger/<rc>/ledger.jsonl
#
#   <project>  PGAI_PROJECT_NAME, default pgai-agent-kanban (the same
#              default both runners use for their bug filer).
#   <rc>       The project's "## Active RC" value from release-state.md,
#              read only (never written): first
#              ${PGAI_PROJECT_ROOT}/release-state.md when PGAI_PROJECT_ROOT
#              is set, otherwise
#              ${PGAI_AGENT_KANBAN_ROOT_PATH}/projects/<project>/release-state.md,
#              then ${PGAI_AGENT_KANBAN_ROOT_PATH}/release-state.md
#              (single-project layout). Characters outside [A-Za-z0-9._-]
#              become "_". When no release-state file is readable, or the
#              field is absent, blank or "none", the scope is "no-active-rc".
#
# The ledger lives under the framework temp root, never bare /tmp. The
# temp-root purge (team/scripts/lib/temp_purge.sh) does not sweep the
# projects/ subtree, so a ledger survives cleanup.sh for the life of its RC;
# a new RC starts a new, empty ledger directory.
#
# RECORD FIELDS
# -------------
# One JSON object per line (JSON Lines), appended, never rewritten:
#
#   recorded_at   ISO 8601 UTC timestamp of the append.
#   commit        `git rev-parse HEAD` of the repo the runner ran in, read
#                 before pytest started.
#   suite         "unit" or "integration".
#   selection     The pytest -m marker expression the run used
#                 ("not quarantine" by default, "quarantine" for
#                 --quarantined) -- a --quarantined run is never mistaken for
#                 a run of the whole suite.
#   exit          The runner's own exit code (0 = green).
#   findings_sha  sha256 of the quality-class lint findings the run recorded
#                 before pytest (one JSON object per line, in record order).
#   tree_clean    true when `git status --porcelain` was empty before pytest
#                 started -- no tracked change and no non-ignored untracked
#                 file on top of the commit.
#
# WHEN A RUN IS REUSED
# --------------------
# --reuse-if-green reuses the most recent ledger record whose commit, suite
# and selection match this run only when all of these hold:
#   - that record's exit is 0 and its tree_clean is true;
#   - this run's working tree is clean too;
#   - this run's findings hash equals the record's findings_sha.
# Anything else -- no matching record, a recorded non-zero exit, a different
# commit or findings hash, a dirty tree, --coverage (which must produce a
# coverage report) -- runs the suite normally. A reused run appends no new
# record; the record it reused already says the same thing.
#
# QUALITY CLASS: THE LEDGER NEVER FAILS A RUN
# -------------------------------------------
# A missing ledger, an unreadable or malformed one, an unwritable directory,
# an unresolvable HEAD, or a missing python3 each narrate one
# "[green-ledger] ..." line and fall back to a normal run. No function here
# exits the caller or returns non-zero to a `set -e` caller, except
# green_ledger_try_reuse, whose non-zero return means "run the suite" and is
# meant to be read in an `if`. When an append finds the existing ledger
# malformed it moves that file aside to ledger.jsonl.malformed (replacing an
# older one) and starts a fresh ledger, so one bad write does not disable
# reuse for the rest of the RC.
#
# TARGETED RUNS
# -------------
# A --files run is not a certification run: it never reads or writes the
# ledger. The runners simply do not call green_ledger_prepare on a targeted
# run, and green_ledger_record_exit is a no-op until prepare has run.
#
# Public API
# ----------
#   green_ledger_prepare <suite> <repo-root> <selection>
#       Called by a certification run after its quality-class lints, just
#       before pytest. Resolves the ledger path, HEAD, the tree state and
#       the findings hash (from GATED_RUNNER_FINDINGS, see
#       team/scripts/lib/gated_runner_lint.sh) into the GREEN_LEDGER_*
#       globals below. Always returns 0.
#
#   green_ledger_try_reuse
#       Returns 0 after printing the "reused" line when the prepared run may
#       return the recorded green result; otherwise prints one line naming
#       why not and returns 1. Requires green_ledger_prepare first.
#
#   green_ledger_record_exit <exit-code>
#       Appends this run's record. Called from each runner's certification
#       exit hook; a no-op when prepare never ran (an abort before pytest,
#       or a targeted run) or when this run was itself a reuse. Always
#       returns 0.
#
#   green_ledger_path
#       Prints the ledger path for the current project and Active RC (see
#       LEDGER LOCATION). Returns 1, printing nothing, when no temp root is
#       resolvable.
#
# Globals set by green_ledger_prepare (read-only to callers):
#   GREEN_LEDGER_FILE GREEN_LEDGER_SUITE GREEN_LEDGER_SELECTION
#   GREEN_LEDGER_COMMIT GREEN_LEDGER_TREE_CLEAN GREEN_LEDGER_FINDINGS_SHA
#   GREEN_LEDGER_PREPARED GREEN_LEDGER_REUSED

# Include guard: safe to source multiple times (only loads once).
[[ -n "${_PGAI_GREEN_LEDGER_SH_LOADED:-}" ]] && return 0
_PGAI_GREEN_LEDGER_SH_LOADED=1

GREEN_LEDGER_FILE=""
GREEN_LEDGER_SUITE=""
GREEN_LEDGER_SELECTION=""
GREEN_LEDGER_COMMIT=""
GREEN_LEDGER_TREE_CLEAN=false
GREEN_LEDGER_FINDINGS_SHA=""
GREEN_LEDGER_PREPARED=false
GREEN_LEDGER_REUSED=false

# ---------------------------------------------------------------------------
# _green_ledger_say <text>
# The single narration channel: one line on stdout, prefixed so a reader of
# the runner output can grep every ledger decision.
# ---------------------------------------------------------------------------
_green_ledger_say() {
  printf '[green-ledger] %s\n' "$1"
}

# release_state_value reads Active RC through the one release-state reader.
# shellcheck source=cm/common.sh
source "$(dirname "${BASH_SOURCE[0]}")/cm/common.sh"

# ---------------------------------------------------------------------------
# _green_ledger_rc_scope
# Prints the per-RC directory name (see LEDGER LOCATION). Never fails.
# Reads "## Active RC" through release_state_value (lib/cm/common.sh): a
# heading with trailing whitespace, blank lines before the value and
# whitespace around the value are all tolerated.
# ---------------------------------------------------------------------------
_green_ledger_rc_scope() {
  local project="$1" root="${PGAI_AGENT_KANBAN_ROOT_PATH:-}" candidate value=""
  local -a candidates=()
  [[ -n "${PGAI_PROJECT_ROOT:-}" ]] && candidates+=("${PGAI_PROJECT_ROOT}/release-state.md")
  if [[ -n "$root" ]]; then
    candidates+=("${root}/projects/${project}/release-state.md" "${root}/release-state.md")
  fi
  for candidate in "${candidates[@]}"; do
    [[ -r "$candidate" ]] || continue
    value="$(release_state_value "$candidate" "Active RC")"
    value="${value//[[:space:]]/}"
    break
  done
  if [[ -z "$value" || "$value" == "none" ]]; then
    printf '%s' "no-active-rc"
  else
    printf '%s' "${value//[^A-Za-z0-9._-]/_}"
  fi
}

# ---------------------------------------------------------------------------
# green_ledger_path
# See "Public API" above.
# ---------------------------------------------------------------------------
green_ledger_path() {
  local temp_root="${PGAI_AGENT_KANBAN_TEMP_DIR:-}"
  if [[ -z "$temp_root" ]] && declare -F pgai_temp_dir >/dev/null 2>&1; then
    temp_root="$(pgai_temp_dir 2>/dev/null || true)"
  fi
  [[ -n "$temp_root" ]] || return 1
  local project="${PGAI_PROJECT_NAME:-pgai-agent-kanban}"
  project="${project//[^A-Za-z0-9._-]/_}"
  printf '%s/projects/%s/green-ledger/%s/ledger.jsonl' \
    "$temp_root" "$project" "$(_green_ledger_rc_scope "$project")"
}

# ---------------------------------------------------------------------------
# _green_ledger_findings_sha
# sha256 of GATED_RUNNER_FINDINGS, one serialized finding per line, in the
# order the run recorded them. An empty accumulator hashes the empty string.
# Prints nothing when no sha256 tool is available.
# ---------------------------------------------------------------------------
_green_ledger_findings_sha() {
  local -a findings=()
  if declare -p GATED_RUNNER_FINDINGS >/dev/null 2>&1; then
    findings=("${GATED_RUNNER_FINDINGS[@]}")
  fi
  {
    if [[ ${#findings[@]} -gt 0 ]]; then
      printf '%s\n' "${findings[@]}"
    fi
  } | sha256sum 2>/dev/null | awk '{print $1}' || true
}

# ---------------------------------------------------------------------------
# green_ledger_prepare <suite> <repo-root> <selection>
# See "Public API" above.
# ---------------------------------------------------------------------------
green_ledger_prepare() {
  GREEN_LEDGER_SUITE="${1:-}"
  local repo_root="${2:-.}"
  GREEN_LEDGER_SELECTION="${3:-}"
  GREEN_LEDGER_REUSED=false
  GREEN_LEDGER_FILE="$(green_ledger_path 2>/dev/null || true)"
  GREEN_LEDGER_COMMIT="$(git -C "$repo_root" rev-parse HEAD 2>/dev/null || true)"
  GREEN_LEDGER_TREE_CLEAN=false
  local porcelain rc=0
  porcelain="$(git -C "$repo_root" status --porcelain 2>/dev/null)" || rc=$?
  if [[ $rc -eq 0 && -z "$porcelain" ]]; then
    GREEN_LEDGER_TREE_CLEAN=true
  fi
  GREEN_LEDGER_FINDINGS_SHA="$(_green_ledger_findings_sha)"
  GREEN_LEDGER_PREPARED=true
  return 0
}

# ---------------------------------------------------------------------------
# green_ledger_try_reuse
# See "Public API" above. The ledger itself is parsed by one python3 call,
# which prints a single "<verdict>\t<detail>" line:
#   reuse    <recorded_at>            -- every reuse condition holds
#   norecord <text>                   -- nothing matching at this commit
#   mismatch <text>                   -- a matching record exists but fails
#   corrupt  <text>                   -- the ledger cannot be parsed
# ---------------------------------------------------------------------------
green_ledger_try_reuse() {
  local suite="${GREEN_LEDGER_SUITE:-suite}"
  local fallback="running the ${suite} suite normally."
  if [[ "$GREEN_LEDGER_PREPARED" != "true" ]]; then
    _green_ledger_say "${suite}: --reuse-if-green could not consult the ledger (not prepared for this run); ${fallback}"
    return 1
  fi
  if [[ -z "$GREEN_LEDGER_FILE" ]]; then
    _green_ledger_say "${suite}: no framework temp root resolved, so there is no ledger to consult; ${fallback}"
    return 1
  fi
  if [[ -z "$GREEN_LEDGER_COMMIT" ]]; then
    _green_ledger_say "${suite}: could not resolve git HEAD for this run; ${fallback}"
    return 1
  fi
  if [[ "$GREEN_LEDGER_TREE_CLEAN" != "true" ]]; then
    _green_ledger_say "${suite}: the working tree has changes on top of ${GREEN_LEDGER_COMMIT:0:12}, so no recorded result describes it; ${fallback}"
    return 1
  fi
  if [[ -z "$GREEN_LEDGER_FINDINGS_SHA" ]]; then
    _green_ledger_say "${suite}: could not hash this run's lint findings (sha256sum unavailable); ${fallback}"
    return 1
  fi
  if [[ ! -e "$GREEN_LEDGER_FILE" ]]; then
    _green_ledger_say "${suite}: no ledger yet at ${GREEN_LEDGER_FILE}; ${fallback}"
    return 1
  fi
  local verdict_line verdict detail
  verdict_line="$(python3 -c '
import json
import sys

path, commit, suite, selection, sha = sys.argv[1:6]
required = ("commit", "suite", "exit", "findings_sha")
try:
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
except (OSError, UnicodeDecodeError) as exc:
    print(f"corrupt\tledger {path} is unreadable ({exc.__class__.__name__})")
    sys.exit(0)
records = []
for number, line in enumerate(lines, start=1):
    if not line.strip():
        continue
    try:
        record = json.loads(line)
    except ValueError:
        record = None
    if not isinstance(record, dict) or any(key not in record for key in required):
        print(f"corrupt\tledger {path} line {number} is not a ledger record")
        sys.exit(0)
    records.append(record)
matching = [
    r for r in records
    if r["commit"] == commit and r["suite"] == suite and r.get("selection", "") == selection
]
if not matching:
    print(f"norecord\tno recorded {suite} run at commit {commit[:12]} with marker selection \"{selection}\"")
    sys.exit(0)
last = matching[-1]
recorded_exit = last["exit"]
recorded_sha = str(last["findings_sha"])
recorded_at = last.get("recorded_at") or "at an unknown time"
if recorded_exit != 0:
    print(f"mismatch\tthe most recent {suite} run at commit {commit[:12]} exited {recorded_exit}")
elif last.get("tree_clean") is not True:
    print(f"mismatch\tthe most recent {suite} run at commit {commit[:12]} ran on a tree with uncommitted changes")
elif recorded_sha != sha:
    print(f"mismatch\tthe lint findings hash differs from the recorded run at commit {commit[:12]} ({recorded_sha[:12]} recorded, {sha[:12]} now)")
else:
    print(f"reuse\t{recorded_at}")
' "$GREEN_LEDGER_FILE" "$GREEN_LEDGER_COMMIT" "$GREEN_LEDGER_SUITE" \
    "$GREEN_LEDGER_SELECTION" "$GREEN_LEDGER_FINDINGS_SHA" 2>/dev/null || true)"
  verdict="${verdict_line%%$'\t'*}"
  detail="${verdict_line#*$'\t'}"
  case "$verdict" in
    reuse)
      GREEN_LEDGER_REUSED=true
      _green_ledger_say "${suite}: reused the green result recorded ${detail} for commit ${GREEN_LEDGER_COMMIT:0:12} (findings ${GREEN_LEDGER_FINDINGS_SHA:0:12}, marker selection \"${GREEN_LEDGER_SELECTION}\") -- pytest not started; ledger ${GREEN_LEDGER_FILE}"
      return 0
      ;;
    norecord|mismatch|corrupt)
      _green_ledger_say "${suite}: ${detail}; ${fallback}"
      ;;
    *)
      _green_ledger_say "${suite}: could not read ledger ${GREEN_LEDGER_FILE} (python3 failed or is unavailable); ${fallback}"
      ;;
  esac
  return 1
}

# ---------------------------------------------------------------------------
# green_ledger_record_exit <exit-code>
# See "Public API" above.
# ---------------------------------------------------------------------------
green_ledger_record_exit() {
  local exit_code="${1:-0}"
  [[ "$GREEN_LEDGER_PREPARED" == "true" ]] || return 0
  [[ "$GREEN_LEDGER_REUSED" == "true" ]] && return 0
  local suite="${GREEN_LEDGER_SUITE:-suite}"
  if [[ -z "$GREEN_LEDGER_FILE" || -z "$GREEN_LEDGER_COMMIT" || -z "$GREEN_LEDGER_FINDINGS_SHA" ]]; then
    _green_ledger_say "${suite}: run not recorded (no temp root, git HEAD or findings hash resolved); the run's own exit status is unaffected."
    return 0
  fi
  local outcome
  outcome="$(python3 -c '
import json
import os
import sys
from datetime import datetime, timezone

path, commit, suite, selection, exit_code, sha, tree_clean = sys.argv[1:8]
required = ("commit", "suite", "exit", "findings_sha")
note = ""
try:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        malformed = False
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    if not line.strip():
                        continue
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        rec = None
                    if not isinstance(rec, dict) or any(k not in rec for k in required):
                        malformed = True
                        break
        except (OSError, UnicodeDecodeError):
            malformed = True
        if malformed:
            os.replace(path, path + ".malformed")
            note = f"; the malformed ledger was moved aside to {path}.malformed"
    record = {
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "commit": commit,
        "suite": suite,
        "selection": selection,
        "exit": int(exit_code),
        "findings_sha": sha,
        "tree_clean": tree_clean == "true",
    }
    line = (json.dumps(record, sort_keys=True) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, line)
    finally:
        os.close(fd)
except (OSError, ValueError) as exc:
    print(f"failed\t{exc.__class__.__name__}: {exc}")
    sys.exit(0)
print(f"ok\t{note}")
' "$GREEN_LEDGER_FILE" "$GREEN_LEDGER_COMMIT" "$GREEN_LEDGER_SUITE" \
    "$GREEN_LEDGER_SELECTION" "$exit_code" "$GREEN_LEDGER_FINDINGS_SHA" \
    "$GREEN_LEDGER_TREE_CLEAN" 2>/dev/null || true)"
  case "${outcome%%$'\t'*}" in
    ok)
      _green_ledger_say "${suite}: recorded exit ${exit_code} at commit ${GREEN_LEDGER_COMMIT:0:12} in ${GREEN_LEDGER_FILE}${outcome#*$'\t'}"
      ;;
    failed)
      _green_ledger_say "${suite}: could not append to ${GREEN_LEDGER_FILE} (${outcome#*$'\t'}); the run's own exit status is unaffected."
      ;;
    *)
      _green_ledger_say "${suite}: could not append to ${GREEN_LEDGER_FILE} (python3 failed or is unavailable); the run's own exit status is unaffected."
      ;;
  esac
  return 0
}
