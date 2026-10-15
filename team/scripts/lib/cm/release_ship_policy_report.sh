#!/usr/bin/env bash
# team/scripts/lib/cm/release_ship_policy_report.sh
#
# CM release ship-policy readers: the TESTER-report and TESTER-task readers
# that supply the ship-policy decision matrix (cm_ship_policy,
# release_ship_policy.sh) with its inputs. Sourced by release_ship_policy.sh
# itself; split out of that file so it stays under the shared per-file size
# ceiling (docs/coding-standards.md; enforced by
# team/scripts/lint_engine_size_dupe.py) rather than because reading the
# report is a separate release step — the production calls are all in
# cm_release_apply_ship_policy's Step 4d.
#
# Public entry points
# -------------------
#   read_ship_policy_finding_dimensions <report_path>
#       Prints "<regression> <integrity>" as its one and only stdout line;
#       narration goes to stderr ahead of it. Never fails. The field
#       contract and the merge rule across findings are in the function's
#       own header doc below.
#
#   read_ship_policy_report_values <report_path>
#       Prints one "key=value" line per key, in this fixed order:
#         recommendation=<pass|ship-with-concerns|ship-with-serious-concerns|none|unparseable>
#         recommendation_raw=<the raw value line, or "none" when absent>
#         systemic_risk=<low|medium|high|none|unparseable>
#         systemic_risk_raw=<the raw value line, or "none" when absent>
#         has_large_fix_effort=<yes|no>
#       The two *_raw lines carry the report's value line as written
#       (Markdown decorations kept, HTML comments removed) so a caller
#       filing a bug can quote it verbatim. The normalized token is what
#       the matrix reads; the normalization rules are in the function's own
#       header doc below. has_large_fix_effort is read from each finding's
#       own Fix Effort field — never a whole-report grep, which would match
#       negations ("not large"), "larger", the template legend
#       ("small / medium / large") and prose. Never fails: a missing or
#       unreadable report prints recommendation=none, systemic_risk=none,
#       has_large_fix_effort=no. Narration goes to stderr.
#
#   check_last_three_non_functional <release_notes_dir> [<active_rc>]
#       Step 4c's reader: prints its result lines and exits 0 when the last
#       three shipped releases (by semver order, the active RC's own notes
#       and any still-PENDING-RELEASE file passed over) all carry
#       ## Status NON-FUNCTIONAL, 1 otherwise. Its own header doc below.
#
#   cm_non_functional_pattern_window <check output>
#   cm_non_functional_pattern_window_acknowledged <window>
#   cm_record_non_functional_pattern_window <window>
#       Step 4c's halted-window record (their own header doc below): the
#       window a trigger-8 HALT fired on is recorded, the unhalt acknowledges
#       it, and a re-run over the same three releases passes.
#
#   read_ship_policy_tester_lookup <tasks_dir> <active_rc>
#       Locates the TESTER task for <active_rc> under <tasks_dir> in one
#       pass and prints "key=value" lines:
#         tester_task_dir=<absolute path, or empty>
#         tester_task_id=<folder basename, or "none">
#         tester_state=<status.md ## State, or "none">
#         tester_report_path=<path to artifacts/report.md, or empty>
#         tester_task_found=<yes|no>
#         mismatch_field=<Role|Release Version>      (zero or more triples,
#         mismatch_raw=<raw field text>                each triple in this
#         mismatch_path=<README.md path>               order)
#       Selection rule (unchanged from the original inline lookup): every
#       task whose README ## Role is TESTER and ## Release Version is
#       <active_rc> is a candidate; the most recently created DONE
#       candidate wins, else the most recently created candidate of any
#       state. Task IDs embed a zero-padded date+sequence, so ascending
#       folder-name order is creation order.
#       Matching is by NORMALIZED token: bold markers, backticks, trailing
#       annotations and letter case are ignored, and a bare "X.Y.Z" reads
#       as "vX.Y.Z". A candidate that matched only after normalization is a
#       Role / Release Version MISMATCH: it is still selected (the RC is
#       not treated as untested), the mismatch is narrated on stderr naming
#       the README path, the field and its raw text, and one mismatch_*
#       triple is printed for the caller to file a bug from. A TESTER-role
#       task whose Release Version is present but not version-shaped is
#       reported the same way and never selected — it cannot be tied to any
#       RC. A Release Version that reads "none" is a task that targets no RC
#       (a document-workflow task) and is skipped silently.
#
# Dependencies
# ------------
#   python3 on PATH. The two report readers run the package module
#   pgai_agent_kanban.cm.ship_policy_report_inputs through pp_run_ops
#   (pp_run_ops.sh, sourced here, self-located). That module takes every
#   report value from the one TESTER-report parser,
#   pgai_agent_kanban.reports.tester_report (parse_report), so the matrix
#   reads a report by that module's documented rules and nothing in this
#   file matches report text. The TESTER-task lookup is an embedded
#   Python program (_ship_policy_report_py) that reads task README.md and
#   status.md fields through pgai_agent_kanban.cm.read_state_field — the
#   single field reader release_preflight.sh's read_md_field also wraps —
#   and normalises Release Version through
#   pgai_agent_kanban.cm.release_version, on the PYTHONPATH pp_pythonpath
#   gives it; the release-notes reader
#   carries its own. This file reads no shell globals, so it can be sourced
#   on its own. release_ship_policy.sh sources it, so a caller that sources
#   only that file still resolves every function here.

# Guard against double-sourcing.
if declare -F read_ship_policy_finding_dimensions >/dev/null 2>&1; then
    return 0
fi

if ! declare -F pp_pythonpath >/dev/null 2>&1; then
    # shellcheck source=../pp_run_ops.sh
    source "$(dirname "${BASH_SOURCE[0]}")/../pp_run_ops.sh"
fi

# Helper: read the regression/integrity-damage dimensions cm_ship_policy
# needs for a systemic_risk=high report. Usage: read_ship_policy_finding_dimensions <report_path>
# Prints "<regression> <integrity>" for cm_ship_policy's is_regression/
# integrity_damage arguments as its one and only stdout line. Never fails — a
# missing/unreadable report, a report with no finding, or a report
# predating these fields all print "unknown unknown" (the "not clear to ship"
# default), failing loud like every other input on the systemic_risk=high row.
#
# Narration goes to stderr, written and flushed before the stdout line so a
# caller merging both streams always finds the values on the last line. Today
# the one narration is the zero-finding case: a readable report in which the
# parser finds no finding gets a line naming the report path and stating
# that regression/integrity defaulted to unknown for that reason, so an
# operator can tell a report-shape problem from a finding that genuinely
# reads unknown. A report with findings narrates nothing.
#
# Report field contract. TESTER writes a Regression and an Integrity field
# line inside each finding, alongside the Category, Fix Effort, Systemic
# Risk and Filed As fields (team/roles/TESTER.md Step 9). What starts and
# ends a finding, how a field line is recognised and how its value reduces
# to a token are the parser's rules, stated once in the docstring of
# pgai_agent_kanban.reports.tester_report (Rules 1 to 5): in short, the
# finding heading may sit at any level from 2 down, bold markers, quotes and
# backticks around a label or value are ignored, the label is
# case-insensitive, and an annotation after the token is ignored ("no
# (predates the RC)" reads as "no"). A finding nested inside another is read
# with its own fields and consulted like any other; the outer finding keeps
# its own.
#   Regression: yes|no — "yes" when this RC's own work introduced the
#     finding, "no" when it predates this RC; TESTER states this directly
#     from its knowledge of the RC's diff and history, never guessed here.
#   Integrity: breakage|destructive|none — bound to two of CM.md's four
#     "HALT is reserved for system-breaking states" classes (a breakage-class
#     suite failure, or a destructive-path failure), or "none" for neither.
#     The other two named classes there — verification unable to run, and a
#     defect too severe to absorb — are already covered by cm_ship_policy's
#     other inputs, so this field does not re-encode them.
#
# Only findings whose OWN Systemic Risk is "high" are consulted, so an
# unrelated low-risk finding's Regression value never leaks into the
# decision. Across every consulted finding, "yes"/"breakage"/"destructive"
# win over "no"/"none" — any one high-risk finding carrying the stronger
# value keeps the release from shipping, and an absent/unrecognized value on
# one finding never downgrades a value another finding already established.
# The merge is pgai_agent_kanban.cm.ship_policy_report_inputs.finding_dimensions.
read_ship_policy_finding_dimensions() {
  local report_path="$1"
  pp_run_ops pgai_agent_kanban.cm.ship_policy_report_inputs dims "$report_path"
}

# Helper: read the report-level Recommendation and Systemic Risk values the
# matrix's DONE rows read, normalized, plus the per-finding large-fix-effort
# flag. Usage: read_ship_policy_report_values <report_path>
# Output shape: see the file header. Never fails.
#
# Normalization is the parser's (pgai_agent_kanban.reports.tester_report,
# "Report level" and Rule 4), the same rule for both fields: the value is
# the first non-blank line of the report's Recommendation / Systemic Risk
# section, the template's HTML comment skipped; bold markers, backticks and
# a "Label:" prefix ("Recommendation: PASS", "**Decision:** PASS") are
# ignored; case is ignored and an underscore inside a token reads as a dash;
# the token must lead the value, and an annotation after it ("High —
# dashboard breaks", "low (isolated)") is ignored; and a line that still
# lists alternatives ("PASS | SHIP-WITH-CONCERNS", "low / medium / high") is
# the template's own placeholder, never a value.
# A token in the field's accepted set is printed as that token. An absent
# field (no section, no value line, or the literal "none") prints "none".
# Anything else prints "unparseable" — the caller decides what an
# unparseable value costs (cm_ship_policy floors it to SHIP-WITH-CONCERNS;
# Step 4d files a bug quoting the *_raw line and the report path).
read_ship_policy_report_values() {
  local report_path="$1"
  pp_run_ops pgai_agent_kanban.cm.ship_policy_report_inputs values "$report_path"
}

# Helper: locate the TESTER task for a release version in one pass.
# Usage: read_ship_policy_tester_lookup <tasks_dir> <active_rc>
# Output shape and selection rule: see the file header. Never fails: a
# missing <tasks_dir> prints tester_task_found=no with the "none"/empty
# values.
read_ship_policy_tester_lookup() {
  local tasks_dir="$1"
  local active_rc="$2"
  _ship_policy_report_py lookup "$tasks_dir" "$active_rc"
}

# Helper: scan release-notes/ directory to detect the last-3-NON-FUNCTIONAL pattern.
# Usage: check_last_three_non_functional <release_notes_dir> [<active_rc>]
# Reads the Status of the three newest shipped releases (by semver order)
# through the shared release-notes Status reader
# (pgai_agent_kanban.cm.notes_status_section non-functional-pattern): the
# value is the section's first visible line with HTML comments removed and
# Markdown emphasis ignored, so the template's guidance comment kept above
# the value never hides it. <active_rc>'s own notes, and any file still at
# PENDING-RELEASE, are not shipped releases and are passed over.
# Returns exit code 0 if pattern is detected (last 3 are all NON-FUNCTIONAL), 1 otherwise.
# Prints the result lines to stdout for the caller to log, ending with
# "NON-FUNCTIONAL pattern window: a.md,b.md,c.md" naming the three read.
check_last_three_non_functional() {
  local notes_dir="$1"
  local active_rc="${2:-}"
  local -a _rc_args=()
  [[ -n "$active_rc" ]] && _rc_args=(--active-rc "$active_rc")
  pp_run_ops pgai_agent_kanban.cm.notes_status_section non-functional-pattern "$notes_dir" "${_rc_args[@]}"
}

# Step 4c's halted-window record: once the trigger-8 guard halts on a window
# of three NON-FUNCTIONAL releases, the window is written here, and the
# operator's unhalt is the acknowledgment — a later run that reads the same
# three releases passes, and a newer NON-FUNCTIONAL release forms a new
# window that halts again. Per-install CM state beside release-state.md.
cm_non_functional_pattern_window_file() {
  printf '%s\n' "${_CM_PROJECT_ROOT}/release-state/non-functional-pattern-halted-window"
}

# Usage: cm_non_functional_pattern_window <check_last_three_non_functional output>
# Prints the window ("a.md,b.md,c.md") the output names, or nothing.
cm_non_functional_pattern_window() {
  local line
  while IFS= read -r line; do
    if [[ "$line" =~ NON-FUNCTIONAL[[:space:]]+pattern[[:space:]]+window:[[:space:]]*([^[:space:]]+) ]]; then
      printf '%s\n' "${BASH_REMATCH[1]}"
      return 0
    fi
  done <<< "$1"
}

# Usage: cm_non_functional_pattern_window_acknowledged <window>
# Status 0 when <window> is non-empty and is the window the guard last halted on.
cm_non_functional_pattern_window_acknowledged() {
  local window="$1" file recorded=""
  [[ -n "$window" ]] || return 1
  file="$(cm_non_functional_pattern_window_file)"
  [[ -f "$file" ]] || return 1
  IFS= read -r recorded < "$file" || true
  [[ "$recorded" == "$window" ]]
}

# Usage: cm_record_non_functional_pattern_window <window>
# Records <window> as the one the guard halts on. Writes nothing under a
# decide-only run (which writes no state) or for an empty window; a failed
# write is narrated and never stops the HALT that follows.
cm_record_non_functional_pattern_window() {
  local window="$1" file
  [[ -n "$window" && "${_CM_DECIDE_ONLY:-false}" != "true" ]] || return 0
  file="$(cm_non_functional_pattern_window_file)"
  if mkdir -p "$(dirname "$file")" && printf '%s\n' "$window" > "${file}.tmp.$$" && mv -f "${file}.tmp.$$" "$file"; then
    return 0
  fi
  rm -f "${file}.tmp.$$" 2>/dev/null || true
  echo "WARNING: could not record the NON-FUNCTIONAL pattern window at ${file}; the next run halts on it again." >&2
  return 0
}

# The embedded TESTER-task lookup program. Usage: _ship_policy_report_py lookup <tasks_dir> <active_rc>
# It reads task README.md / status.md fields, never a TESTER report.
_ship_policy_report_py() {
  local _pythonpath
  _pythonpath="$(pp_pythonpath 2>/dev/null || true)"
  PYTHONPATH="${_pythonpath}" python3 - "$@" <<'PY'
import os
import re
import sys

mode = sys.argv[1]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def first_word(value):
    """The first letters-and-dashes word of *value*, lowercased, with
    underscores read as dashes and trailing punctuation dropped."""
    m = re.match(r"([A-Za-z][A-Za-z_-]*)", value.strip())
    return m.group(1).lower().replace("_", "-").rstrip("-") if m else ""


def read_field(path, heading):
    """The single field reader every CM lookup uses. None when the file is
    missing or the heading has no value (read_state_field's "none")."""
    from pgai_agent_kanban.cm.read_state_field import read_state_field

    value = read_state_field(path, heading)
    return None if value == "none" else value


# ---------------------------------------------------------------------------
# Mode: lookup — the TESTER task for a release version
# ---------------------------------------------------------------------------

if mode == "lookup":
    tasks_dir = sys.argv[2]
    active_rc = sys.argv[3]
    active_rc_token = active_rc.strip().lower()
    # The one Release Version normaliser, shared with the release-time
    # bundled-item promotion so both read a written value the same way.
    from pgai_agent_kanban.cm.release_version import VERSION_SHAPE_RE as VERSION_RE
    from pgai_agent_kanban.cm.release_version import version_token

    candidates = []   # (task_dir, state)
    mismatches = []   # (field, raw, readme_path)
    if os.path.isdir(tasks_dir):
        for name in sorted(os.listdir(tasks_dir)):
            task_dir = os.path.join(tasks_dir, name)
            readme = os.path.join(task_dir, "README.md")
            status = os.path.join(task_dir, "status.md")
            if not (os.path.isdir(task_dir) and os.path.isfile(readme)
                    and os.path.isfile(status)):
                continue
            role_raw = read_field(readme, "Role")
            if role_raw is None or first_word(re.sub(r"[*`]", " ", role_raw)) != "tester":
                continue
            version_raw = read_field(readme, "Release Version")
            if version_raw is None:
                continue
            version = version_token(version_raw)
            if version == "none":
                continue
            if not VERSION_RE.match(version):
                mismatches.append(("Release Version", version_raw, readme))
                print(
                    f"TESTER task {name}: Release Version {version_raw!r} in "
                    f"{readme} is not version-shaped (expected vX.Y.Z); the task "
                    f"cannot be tied to any RC and is not a candidate for {active_rc}",
                    file=sys.stderr,
                )
                continue
            if version != active_rc_token:
                continue
            if role_raw.strip() != "TESTER":
                mismatches.append(("Role", role_raw, readme))
                print(
                    f"TESTER task {name}: Role {role_raw!r} in {readme} matched "
                    f"TESTER only after normalization",
                    file=sys.stderr,
                )
            if version_raw.strip() != active_rc.strip():
                mismatches.append(("Release Version", version_raw, readme))
                print(
                    f"TESTER task {name}: Release Version {version_raw!r} in "
                    f"{readme} matched {active_rc} only after normalization",
                    file=sys.stderr,
                )
            state = read_field(status, "State")
            candidates.append((task_dir, state if state is not None else "none"))
    sys.stderr.flush()

    selected = None
    for task_dir, state in candidates:
        if state.strip().lower() == "done":
            selected = (task_dir, state)
    if selected is None and candidates:
        selected = candidates[-1]

    if selected is None:
        print("tester_task_dir=")
        print("tester_task_id=none")
        print("tester_state=none")
        print("tester_report_path=")
        print("tester_task_found=no")
    else:
        task_dir, state = selected
        report = os.path.join(task_dir, "artifacts", "report.md")
        print(f"tester_task_dir={task_dir}")
        print(f"tester_task_id={os.path.basename(task_dir)}")
        print(f"tester_state={state}")
        print(f"tester_report_path={report if os.path.isfile(report) else ''}")
        print("tester_task_found=yes")
    for field, raw, readme in mismatches:
        print(f"mismatch_field={field}")
        print(f"mismatch_raw={raw}")
        print(f"mismatch_path={readme}")
    sys.exit(0)

print(f"unknown reader mode {mode!r}: expected lookup", file=sys.stderr)
sys.exit(2)
PY
}
