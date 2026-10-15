#!/usr/bin/env bash
# team/scripts/lib/cm/release_known_issue.sh
#
# CM release known-issue-ship stage: the pure functions that give a
# SHIP-KNOWN-ISSUE decision its two records — the release-notes ## Status
# stamp (extended to also name the causing finding under "## Known
# Issues") and the causing bug's own carry-forward marker. Sourced by
# team/scripts/cm/release.sh; split out of release_ship_policy.sh (which
# sourced it) so that file stays under the shared per-file size
# ceiling (docs/coding-standards.md; enforced by
# team/scripts/lint_engine_size_dupe.py) rather than because these
# functions are a separate release step — they are still called from
# cm_release_apply_ship_policy's Step 4e.
#
# Public entry points
# --------------------
#   read_ship_policy_known_issue_bug_files <report_path> <bugs_dir>
#       Resolves a TESTER report's systemic_risk=high finding(s) to their
#       own bug file(s) on disk via the BUG-NNNN id each finding's own
#       Filed As field names. Prints one path per line, in the form
#       <bugs_dir> was given in.
#
#   read_ship_policy_skipped_bug_pairs <report_path> <bugs_dir> [<exclude_path> ...]
#       Resolves every bug/gap/pre-existing-bug/stale-assertion finding in
#       a TESTER report to a (bug_file, rationale) pair for the "## Bugs Skipped" block —
#       the render-side counterpart of read_ship_policy_known_issue_bug_files.
#       The rationale is one readable sentence saying why the bug shipped
#       unfixed, composed from the finding's own Category, Regression, Fix
#       Effort and Systemic Risk fields (for example "Introduced by this
#       release and found during its verification, rated fix effort small
#       and systemic risk medium; deferred to a follow-up release."). The
#       finding's free-text detail bullets never reach it, so the cell
#       carries no list marker and no quoted identifier. The pairing and
#       the rationale wording live in pgai_agent_kanban.cm.skipped_bug_pairs
#       (team/pgai_agent_kanban/cm/skipped_bug_pairs.py). Any
#       <exclude_path> (typically the paths
#       read_ship_policy_known_issue_bug_files already resolved) is
#       skipped by its own BUG-NNNN id, so a bug already rendered under
#       Known Issues is never duplicated under Bugs Skipped. Prints one
#       "<bug_file>\t<rationale>" line per pair; never fails.
#
#   build_known_issue_notes_block <bug_file> [<bug_file> ...]
#       Renders the "## Known Issues" prose block for a known-issue ship,
#       one bullet per bug file, by substance — never the bare internal
#       ticket ID.
#
#   cm_build_bugs_skipped_ship_block <report_path> <bugs_dir> [<exclude_bug_file> ...]
#       Composes read_ship_policy_skipped_bug_pairs and
#       build_bugs_skipped_notes_block (plus the table's static
#       header/separator) into the one call release_ship_policy.sh makes.
#       Sets _SKIPPED_BUG_PAIRS and _BUGS_SKIPPED_NOTES_BLOCK as globals.
#
#   build_bugs_skipped_notes_block <bug_file> <rationale> [<bug_file> <rationale> ...]
#       Renders the "## Bugs Skipped" table body — one row per (bug_file,
#       rationale) pair, matching the RELEASE-NOTES-TEMPLATE.md
#       "KNOWN-BUGS BUG LIST" table shape — from the pairs
#       read_ship_policy_skipped_bug_pairs resolves. Both the bug's own
#       substance and the caller-supplied rationale text pass through the
#       same _strip_internal_ids scrub build_known_issue_notes_block uses,
#       because a caller-supplied rationale is free text and is exactly
#       where a pasted finding sentence can carry a task ID inline — a
#       rationale that quotes another release's finding text can re-leak
#       that finding's own task ID. Gives the release pipeline
#       a driveable, testable, structurally-scrubbed rendering surface for
#       a block that previously reached shipped notes exactly as an author
#       typed it.
#
#   cm_stamp_release_notes_status <notes_file> <target_status> [<known_issue_block>] [<bugs_skipped_block>]
#       Stamps a release-notes file's "## Status" PENDING-RELEASE
#       placeholder with the ship decision — passing over blank lines and
#       HTML comments (one-line or a multi-line block) between the heading
#       and the placeholder, as the release-notes template keeps one — and (only for
#       target_status=KNOWN-ISSUE or NON-FUNCTIONAL with a non-empty
#       known_issue_block — the caller sets one for NON-FUNCTIONAL only on a
#       ship-posture auto-ship) also replaces the "## Known Issues" section
#       body, and (whenever
#       bugs_skipped_block is non-empty, independent of target_status)
#       also replaces the "## Bugs Skipped" section body — all in the same
#       write, so the records land or fail together. An empty
#       bugs_skipped_block leaves any existing "## Bugs Skipped" section
#       untouched (e.g. a WRITER-authored "None.").
#
#   cm_release_notes_status_has_placeholder <notes_file>
#       Returns 0 when PENDING-RELEASE appears in the "## Status" section
#       of a release-notes file (outside HTML comments), 1 otherwise. The
#       word anywhere else in the file — a top-of-file template comment, a
#       rendered "## Bugs Skipped" rationale — is not counted. A "<!--"
#       quoted in prose or inline code is text, not a comment opener. When
#       the reader finds no "## Status" heading but the file has a line
#       shaped like one, or the reader cannot run, it warns on stderr and
#       searches the whole file instead, so a misread can only make the
#       answer "present". Step 4e's pre-stamp check and post-stamp guard
#       both use it.
#
#   cm_stamp_known_issue_marker <active_rc> <bug_file> [<bug_file> ...]
#       Stamps "## Known Issue Carry Forward: <active_rc>" onto each given
#       bug file, idempotently (never overwrites an existing value).
#
# Dependencies
# -------------
# python3 on PATH, plus team/scripts/lib/cm/internal_id_scrub.py, which
# must ship beside this file (same directory). The four renderers and
# stamps (build_known_issue_notes_block, build_bugs_skipped_notes_block,
# cm_stamp_release_notes_status, cm_stamp_known_issue_marker) are the
# commands of team/pgai_agent_kanban/cm/release_notes_blocks.py; the two
# renderers pass this file's directory as PGAI_CM_INTERNAL_ID_SCRUB_DIR so
# that module loads the scrub shipped beside this file. The "## Status" section
# reading — where the section starts and ends, which lines are comments —
# is team/pgai_agent_kanban/cm/notes_status_section.py, the "## Bugs
# Skipped" pairing and rationale are
# team/pgai_agent_kanban/cm/skipped_bug_pairs.py, and
# read_ship_policy_known_issue_bug_files is
# team/pgai_agent_kanban/cm/stop_bug_files.py's command line. Neither
# module reads the TESTER report text itself: both take every finding and
# field from the one report parser, parse_report
# (team/pgai_agent_kanban/reports/tester_report.py). All three modules are
# reached through team/scripts/lib/pp_run_ops.sh (pp_pythonpath /
# pp_run_ops), which this file sources itself below its double-sourcing
# guard. Otherwise these are pure
# functions: given explicit arguments, they read/write only the paths
# named in those arguments.
#
# The caller (release_ship_policy.sh's cm_release_apply_ship_policy) must
# have this file sourced before it runs.

# Guard against double-sourcing.
if declare -F cm_stamp_known_issue_marker >/dev/null 2>&1; then
    return 0
fi

# The "## Status" section helpers run pgai_agent_kanban.cm.notes_status_section,
# read_ship_policy_skipped_bug_pairs runs
# pgai_agent_kanban.cm.skipped_bug_pairs, and
# read_ship_policy_known_issue_bug_files runs
# pgai_agent_kanban.cm.stop_bug_files, from this file's own tree;
# pp_run_ops.sh composes that PYTHONPATH.
if ! declare -F pp_pythonpath >/dev/null 2>&1; then
    # shellcheck source=../pp_run_ops.sh
    source "$(dirname "${BASH_SOURCE[0]}")/../pp_run_ops.sh"
fi

# Helper: resolve the bug file(s) whose finding produced a SHIP-KNOWN-ISSUE
# decision — every finding in the report whose own Systemic Risk is "high"
# (by the time cm_ship_policy has returned SHIP-KNOWN-ISSUE, every one of
# those findings has already passed the regression=no/integrity=none/
# fix-effort=small gate, so each is a legitimate cause, not just the first).
# Nothing about the report is read here: this is the command line of
# pgai_agent_kanban.cm.stop_bug_files for the systemic-risk-high cause —
# the Known Issues stop list itself — run with --keep-path-form so each
# path keeps the form <bugs_dir> was given in. That module takes every
# finding and field from parse_report
# (team/pgai_agent_kanban/reports/tester_report.py), the one TESTER-report
# parser the ship-policy matrix (read_ship_policy_finding_dimensions) and
# the Bugs Skipped table read through too, nested sub-findings included and
# the outer finding's own fields kept. So the matrix never decides
# SHIP-KNOWN-ISSUE from a high-risk finding whose bug this resolver cannot
# read. The parser's module docstring states what starts a finding, how a
# field line is recognised and how the Filed As id is read.
# Usage: read_ship_policy_known_issue_bug_files <report_path> <bugs_dir>
# Prints one bug-file path per line, resolved from each finding's own Filed
# As BUG-NNNN id against <bugs_dir>/BUG-NNNN-*.md (a bare BUG-NNNN.md is the
# fallback), each bug once, in report order. A Filed As value that does
# not resolve to an existing bug file is skipped with a WARNING to stderr —
# this never fabricates a path, so the caller's list only ever names bugs
# it can actually mark. Prints nothing (never fails) for a
# missing/unreadable report or a report with no findings. Returns 1, with
# pp_run_ops's message on stderr, only when this file's own tree cannot
# be located to run the resolver.
read_ship_policy_known_issue_bug_files() {
  local report_path="$1"
  local bugs_dir="$2"
  pp_run_ops pgai_agent_kanban.cm.stop_bug_files --keep-path-form \
    "$report_path" "$bugs_dir" systemic-risk-high
}

# Helper: resolve every bug/gap/pre-existing-bug/stale-assertion finding in a
# TESTER report to a (bug_file, rationale) pair for the "## Bugs Skipped" table —
# read_ship_policy_known_issue_bug_files's render-path counterpart. A "pass"
# or "pass-with-caveat" finding never contributes a row (nothing was skipped
# there); any finding whose Filed As BUG-NNNN
# resolves to one of the excluded paths (the Known-Issues block's own bug
# files, passed in by the caller) is skipped so the same bug never appears
# twice across both sections. The rationale is one readable sentence built
# by skip_rationale from the finding's own rating fields — whether this
# release introduced it (Regression), its Fix Effort and Systemic Risk, and
# that it is deferred to a follow-up release. A rating field that is absent
# or outside the report template's vocabulary is left out of the sentence,
# so the rationale is never empty. The finding's free-text detail (its
# "- Traces to:", "- Location:" and similar bullets) describes the defect,
# not why it was skipped, and is not used.
# The pairing and the rationale (skip_rationale and its rating vocabularies)
# live in team/pgai_agent_kanban/cm/skipped_bug_pairs.py; this function is
# that module's command line, run from this file's own tree through
# pp_run_ops, and its module docstring is the full reading contract.
# Usage: read_ship_policy_skipped_bug_pairs <report_path> <bugs_dir> [<exclude_path> ...]
# Prints one "<bug_file>\t<rationale>" line per pair. Never fails — a
# missing/unreadable report or a report with no qualifying findings prints
# nothing.
read_ship_policy_skipped_bug_pairs() {
  pp_run_ops pgai_agent_kanban.cm.skipped_bug_pairs "$@"
}

# Helper: build the "## Known Issues" prose block for a SHIP-KNOWN-ISSUE
# release, one bullet per bug file, in the shape
# team/templates/project/release/RELEASE-NOTES-TEMPLATE.md's own
# "KNOWN-BUGS BUG LIST" convention already documents for an open item: the
# bug's own Symptom text (substance, never the bare internal ticket ID),
# its Public ID when one has been assigned, and the fixed fallback
# workaround note — bug files have no machine-readable Workaround field, so
# every bullet reads "No workaround available." rather than guessing one.
#
# Structural guard: a bug's free-text Symptom prose can itself quote a
# kanban task ID or bug ID inline — a "Source Task:" field, or a pasted
# lint finding that names one — even though the bullet this function
# renders is meant to hold only reader-facing substance.
# _strip_internal_ids scrubs any bug/priority/task-ID-shaped token out of
# the extracted substance before it is ever assembled into a bullet, so
# this function is structurally unable to emit one regardless of what a
# bug file's prose happens to contain — this is the same
# internal_id_scrub.py definition release_notes.sh's own generator and
# build_bugs_skipped_notes_block below both use, loaded by
# pgai_agent_kanban.cm.release_notes_blocks the way the generator loads it.
# Usage: build_known_issue_notes_block <bug_file> [<bug_file> ...]
# Prints the block text (one "- ..." bullet per line, no trailing bullet
# separator); prints nothing when called with no bug files.
build_known_issue_notes_block() {
  [[ $# -eq 0 ]] && return 0
  local _cm_lib_dir
  _cm_lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  PGAI_CM_INTERNAL_ID_SCRUB_DIR="$_cm_lib_dir" \
    pp_run_ops pgai_agent_kanban.cm.release_notes_blocks known-issues "$@"
}

# Helper: build the "## Bugs Skipped" table body CM authors by hand at
# release time from the TESTER report's gap summary — one row per
# (bug_file, rationale) pair, matching the RELEASE-NOTES-TEMPLATE.md
# "KNOWN-BUGS BUG LIST" table shape:
#   | Substance (what was wrong) | Public ID (if disclosed) | Severity | Rationale for Skipping |
#
# Structural guard: this block previously passed through no scrub at all —
# a hand-authored rationale bullet that quoted another release's TESTER
# finding text could re-leak that finding's own task ID into the shipped
# notes. Both the bug's own substance (Symptom, same extraction
# build_known_issue_notes_block uses) and the caller-supplied rationale text
# are run through the same _strip_internal_ids scrub, since the rationale —
# free text a human types describing why a bug was skipped — is exactly
# where a pasted finding sentence can carry a task ID inline.
#
# Table-safety guard: every one of the four cells (substance, public ID,
# severity, rationale) is passed through table_cell before assembly, so a
# row is exactly one line and never carries an unescaped '|' regardless of
# what the source bug file or the caller-supplied rationale contains. The
# severity cell specifically reads only the first whitespace-delimited
# token of the bug file's "## Severity" section — never the section's full
# body — so a bug file that explains its rating in a paragraph beneath the
# value (or ends the section with a '---' rule) still contributes just the
# severity word, matching the one-word vocabulary the column holds. A bug
# file already written in the template's bare-value shape renders
# byte-identically to before this guard existed.
#
# Usage: build_bugs_skipped_notes_block <bug_file> <rationale> [<bug_file> <rationale> ...]
# Prints one "| ... | ... | ... | ... |" table row per pair (no header, no
# separator — the caller owns the table's static header/separator lines,
# matching the template); prints nothing when called with no arguments.
# Exits 1 with an ERROR to stderr when given an odd number of arguments
# (a bug_file with no matching rationale).
build_bugs_skipped_notes_block() {
  [[ $# -eq 0 ]] && return 0
  if (( $# % 2 != 0 )); then
    echo "ERROR: build_bugs_skipped_notes_block requires <bug_file> <rationale> pairs (got an odd number of arguments)." >&2
    return 1
  fi
  local _cm_lib_dir
  _cm_lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  PGAI_CM_INTERNAL_ID_SCRUB_DIR="$_cm_lib_dir" \
    pp_run_ops pgai_agent_kanban.cm.release_notes_blocks bugs-skipped "$@"
}

# Helper: resolve and render the "## Bugs Skipped" table for a ship-policy
# run — read_ship_policy_skipped_bug_pairs plus build_bugs_skipped_notes_block
# plus the table's own static header/separator lines, composed into the one
# call release_ship_policy.sh's cm_release_apply_ship_policy makes, wired the
# same way that file already wires the Known-Issues resolution. Kept as its
# own function, rather than inlined at the call site, so that file's own
# line count stays under the engine-decomposition ceiling (docs/coding-standards.md;
# team/scripts/lint_engine_size_dupe.py) — the same reason
# release_known_issue.sh itself was split out of release_ship_policy.sh.
# Usage: cm_build_bugs_skipped_ship_block <report_path> <bugs_dir> [<exclude_bug_file> ...]
# Sets, as globals for the caller to read: _SKIPPED_BUG_PAIRS (the flat
# bug_file/rationale array build_bugs_skipped_notes_block takes) and
# _BUGS_SKIPPED_NOTES_BLOCK (the full table including header/separator,
# empty when no finding qualifies). Never fails — a missing/unreadable
# report_path leaves both empty.
cm_build_bugs_skipped_ship_block() {
  local report_path="$1"
  local bugs_dir="$2"
  shift 2
  _SKIPPED_BUG_PAIRS=()
  _BUGS_SKIPPED_NOTES_BLOCK=""
  [[ -z "$report_path" || ! -f "$report_path" ]] && return 0
  local _skip_bug_file _skip_rationale
  while IFS=$'\t' read -r _skip_bug_file _skip_rationale; do
    [[ -z "$_skip_bug_file" ]] && continue
    _SKIPPED_BUG_PAIRS+=("$_skip_bug_file" "$_skip_rationale")
  done < <(read_ship_policy_skipped_bug_pairs "$report_path" "$bugs_dir" "$@")
  if [[ "${#_SKIPPED_BUG_PAIRS[@]}" -gt 0 ]]; then
    _BUGS_SKIPPED_NOTES_BLOCK="| Substance (what was wrong) | Public ID (if disclosed) | Severity | Rationale for Skipping |
|------------------------------|---------------------------|----------|-------------------------|
$(build_bugs_skipped_notes_block "${_SKIPPED_BUG_PAIRS[@]}")"
  fi
}

# Helper: whether a release-notes file's "## Status" section still holds the
# PENDING-RELEASE placeholder — the question Step 4e asks before the stamp
# (is there anything to stamp?) and after it (did the placeholder survive?).
# Only the Status section counts: the body under every "## Status" heading
# up to the next level-1 or level-2 heading, with HTML comment text removed
# (pgai_agent_kanban.cm.notes_status_section). The word quoted anywhere else —
# the release-notes template's top-of-file guidance comment, a "## Bugs
# Skipped" rationale the stamp itself rendered from TESTER report text — is
# not the placeholder and never answers yes.
# Comments are read the way markdown renders them: a "<!--" quoted in prose
# or inside an inline code span is text, so it never hides the "## Status"
# heading below it.
# Usage: cm_release_notes_status_has_placeholder <notes_file>
# Returns 0 when the placeholder is present, 1 when it is absent (including
# a file with no "## Status" line at all or one that cannot be read). Two
# cases fail closed — they say so on stderr and fall back to a whole-file
# search for the word, so a misread can only make Step 4e stricter, never
# let a surviving placeholder through:
#   - the reader finds no "## Status" heading outside an HTML comment, yet
#     the file has a line shaped like one ("cannot tell" — the reader and
#     the file disagree about where the section is);
#   - the section reader cannot run at all.
cm_release_notes_status_has_placeholder() {
  local notes_file="$1"
  local _verdict=""
  _verdict="$(pp_run_ops pgai_agent_kanban.cm.notes_status_section has-placeholder "$notes_file")" || true
  case "$_verdict" in
    present) return 0 ;;
    absent) return 1 ;;
    no-section)
      if ! grep -qE '^##[[:blank:]]+Status[[:space:]]*$' "$notes_file" 2>/dev/null; then
        return 1
      fi
      echo "WARNING: ${notes_file} has a '## Status' line that the section reader did not read as a heading (for example, one inside an HTML comment); searching the whole file for PENDING-RELEASE instead." >&2
      grep -q "PENDING-RELEASE" "$notes_file" 2>/dev/null && return 0
      return 1
      ;;
  esac
  echo "WARNING: the '## Status' section reader could not check ${notes_file}; searching the whole file for PENDING-RELEASE instead." >&2
  grep -q "PENDING-RELEASE" "$notes_file" 2>/dev/null && return 0
  return 1
}

# Helper: stamp a release-notes file's "## Status" placeholder with the
# ship-policy decision, and — only when target_status is KNOWN-ISSUE or
# NON-FUNCTIONAL and known_issue_block is non-empty — also replace the "## Known Issues"
# section body with that block, and — whenever bugs_skipped_block is
# non-empty, independent of target_status — also replace the "## Bugs
# Skipped" section body with that block, all in the SAME write. The records
# land or fail together by construction: there is no partial-stamp state
# where the status changed but the known-issues or bugs-skipped prose did
# not, so the caller's existing idempotency guards (the Status-section
# placeholder check, cm_release_notes_status_has_placeholder) cover every
# half without a second check. A bugs_skipped_block
# is independent of target_status because a release can carry deferred bugs
# under any ship decision, not only KNOWN-ISSUE.
# The placeholder is the value of a "## Status" section: its first
# non-blank line once HTML comments are removed. Blank lines and comments
# between the heading and the placeholder — a one-line comment such as the
# release-notes template's guidance line, or a multi-line comment block — are
# passed over and kept unchanged; only the placeholder line is rewritten.
# A "## Status" heading inside an HTML comment is not a heading.
# Usage: cm_stamp_release_notes_status <notes_file> <target_status> [<known_issue_block>] [<bugs_skipped_block>]
# Exit 0 with a confirmation line on stdout on success. Exit 1 with a
# WARNING on stderr when the PENDING-RELEASE placeholder is not found under
# "## Status" (or the section reader cannot be located) — the caller HALTs
# on that (trigger 9).
cm_stamp_release_notes_status() {
  local notes_file="$1"
  local target_status="$2"
  local known_issue_block="${3:-}"
  local bugs_skipped_block="${4:-}"
  pp_run_ops pgai_agent_kanban.cm.release_notes_blocks stamp-status \
    "$notes_file" "$target_status" "$known_issue_block" "$bugs_skipped_block"
}

# Helper: stamp "## Known Issue Carry Forward: <active_rc>" onto each given
# bug file — the marker the next PM decomposition bundle honors to place
# the bug first (the release's other record: "mark the bug that caused it
# so the next bundle carries it first"). The field is new, never an
# overload of ## Status / ## Affects / ## Fixed In: the bug stays open
# and stays eligible for ordinary bundling either way.
# Usage: cm_stamp_known_issue_marker <active_rc> <bug_file> [<bug_file> ...]
# Idempotent: a bug file that already carries a non-empty value under the
# heading is left untouched (never overwritten) — the same "write once,
# never clobber" contract release_finalize.sh Step 16b uses for ## Fixed
# In, including surviving a bug file that is missing the section entirely.
# Prints one narration line to stdout in the "already complete, continuing"
# shape when every file was already marked, matching Step 4e's own
# already-complete phrasing.
cm_stamp_known_issue_marker() {
  local active_rc="$1"
  shift
  [[ $# -eq 0 ]] && return 0
  pp_run_ops pgai_agent_kanban.cm.release_notes_blocks carry-forward "$active_rc" "$@"
}
