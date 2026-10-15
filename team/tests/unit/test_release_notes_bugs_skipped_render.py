"""
test_release_notes_bugs_skipped_render.py
===========================================
Regression gate for the auto-generate release-notes path rendering the
"## Bugs Skipped" section -- the path this project actually ships on
whenever no WRITER-authored release-notes/<version>.md draft is waiting on
the RC branch (five of the last five releases at the time this test was
written).

The guarded defect: ``cm_build_bugs_skipped_ship_block`` (release_known_issue.sh)
builds the scrubbed "## Bugs Skipped" table on every release
(release_ship_policy.sh:528) but the only consumer of the resulting
``_BUGS_SKIPPED_NOTES_BLOCK`` is the Step 4e stamp
(``cm_stamp_release_notes_status``), reachable only on the WRITER-draft
path, while the auto-generate path's own Python generator
(``pgai_agent_kanban.cm.release_notes_generator``, which release_notes.sh
runs at Step 11a) emits no "## Bugs Skipped" section
at all, so the value computed once per release is thrown away on the path
this project actually ships on.

Fidelity approach: identical to test_release_notes_content_described.py and
test_release_notes_bundled_item_title.py -- the shipped generator module is
imported and its CLI entry point, ``main()``, is called with the same argv
contract ``cm_release_generate_notes`` uses (including the 10th positional
argument, ``_BUGS_SKIPPED_NOTES_BLOCK``). A regression in the shipped
generator fails these tests directly.

Covers:
  1. The generator renders a "## Bugs Skipped" section at all:
     ``render_notes`` emits the heading, with the block it is given as the
     section body, so dropping the section from ``SECTION_ORDER`` or its
     rendering fails here.
  2. Driving the generator with a non-empty ``_BUGS_SKIPPED_NOTES_BLOCK``
     built from real rows -- built the same way
     ``cm_build_bugs_skipped_ship_block`` builds it, via
     ``build_bugs_skipped_notes_block`` -- whose rationale text carries a
     task identifier of each of the six agent prefixes produces a notes
     file containing none of the six. The block is passed through
     unmodified (the generator does not re-scrub it), so this also proves
     the scrub delivered by the prerequisite ticket (repair internal-ID
     scrub to leave readable prose) survives the hand-off into the
     auto-generated file.
  3. Driving the generator with an empty ``_BUGS_SKIPPED_NOTES_BLOCK``
     (the common case -- no TESTER finding qualifies) produces an explicit
     empty state ("## Bugs Skipped" / "None") with no stray table header,
     separator, or trailing blank artefact -- byte-stability for the
     no-findings case.
  4. The rendered non-empty section preserves the table's own header and
     separator lines exactly as ``cm_build_bugs_skipped_ship_block``
     composed them (the generator is a pass-through, never a
     recomputation).
  5. Abbreviated ``<ROLE>-NNN`` task references and internal
     requirements-document paths in a finding's Symptom or rationale are
     scrubbed like the full task ID, so none reaches the rendered notes.
  6. Rendering the block end to end from a TESTER report fixture -- through
     ``cm_build_bugs_skipped_ship_block`` and the generator -- whose
     findings open with "- Traces to:" detail bullets yields a Rationale
     for Skipping cell per finding that is readable prose: non-empty, no
     leading list marker, no "Traces to" line, and no "another item"
     scrub stand-in.
  7. A finding at a heading deeper than level 3 ("#### Finding" grouped
     under a "### Step" heading) yields exactly the row the same finding
     yields at level 3.
  8. A shallower non-finding heading ("### Step") ends a preceding
     "#### Finding" block, so a field line under the step never becomes
     the finding's own.
  9. The five report readers -- read_ship_policy_skipped_bug_pairs (Bugs
     Skipped), resolve_stop_bug_files (the Known Issues stop list),
     read_ship_policy_known_issue_bug_files (the SHIP-KNOWN-ISSUE bug
     resolver), read_ship_policy_finding_dimensions (the ship-policy
     matrix) and OVERWATCH's matrix_findings (the resolution stage's
     findings) -- all take their findings from parse_report, so they agree
     on whether each of eight heading shapes opens a finding, and the shared
     answer is the parser's Rule 1 (which also opens "###Finding 1:" and
     "## Finding 1:"). They also agree when a finding heading is nested
     inside the probe finding: every reader still reads the outer finding's
     own fields.
 10. A filed "pre-existing-bug" finding shipped unfixed gets a Bugs Skipped
     row whose rationale says it predates the release, with or without a
     Regression line; only an explicit "Regression: yes" reads as
     introduced by the release, and an unfiled one adds no row.
 11. The four finding-field readers -- read_ship_policy_finding_dimensions,
     resolve_stop_bug_files (systemic-risk-high),
     read_ship_policy_known_issue_bug_files and
     read_ship_policy_skipped_bug_pairs -- agree on each field-line shape
     (bold labels, plain labels, a bold label outside its colon, a
     backticked id, "see BUG-NNNN", "cites the existing BUG-NNNN-slug", and
     labels with no colon): all read the finding's bug, or all skip it.
     Whenever cm_ship_policy decides SHIP-KNOWN-ISSUE from the finding, the
     SHIP-KNOWN-ISSUE resolver prints its bug file.

All temp paths use pytest's tmp_path fixture (redirected to the framework
temp root by conftest.py). No bare /tmp paths. No live kanban root is
touched.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from pgai_agent_kanban.cm.release_notes_generator import (
    SECTION_ORDER,
    main as release_notes_main,
    render_notes,
)
from pgai_agent_kanban.cm.stop_bug_files import (
    CAUSE_SYSTEMIC_RISK_HIGH,
    resolve_stop_bug_files,
)
from pgai_agent_kanban.overwatch.resolution_matrix import matrix_findings
from pgai_agent_kanban.reports.tester_report import parse_report
from tests.unit.shell_harness import run_bash

# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------

_KNOWN_ISSUE_LIB = "scripts/lib/cm/release_known_issue.sh"
_SHIP_POLICY_LIB = "scripts/lib/cm/release_ship_policy.sh"
_SHIP_POLICY_REPORT_LIB = "scripts/lib/cm/release_ship_policy_report.sh"

_BUGS_SKIPPED_HEADER = (
    "| Substance (what was wrong) | Public ID (if disclosed) | Severity | "
    "Rationale for Skipping |"
)
_BUGS_SKIPPED_SEPARATOR = (
    "|------------------------------|---------------------------|----------"
    "|-------------------------|"
)

_BUG_TEMPLATE = """# {bug_id}-{slug}

## Status
open

## Symptom
{symptom}

## Severity
MEDIUM
"""


# ---------------------------------------------------------------------------
# Acceptance criterion 1: the generator renders the section at all.
# ---------------------------------------------------------------------------


def test_generator_renders_bugs_skipped_section():
    """render_notes emits a "## Bugs Skipped" heading carrying the block it is given."""
    block = "| sentinel row for the Bugs Skipped section |"
    notes = render_notes(
        "test-project",
        "v9.9.9",
        "2026-09-10",
        "FUNCTIONAL",
        {section: [] for section in SECTION_ORDER},
        bugs_skipped_block=block,
    )

    assert "## Bugs Skipped" in notes.splitlines(), (
        "pgai_agent_kanban.cm.release_notes_generator.render_notes emitted no "
        "'## Bugs Skipped' heading -- the auto-generate path drops the value "
        f"cm_build_bugs_skipped_ship_block computes. Rendered notes:\n{notes}"
    )
    assert _parse_section(notes, "Bugs Skipped") == block


# ---------------------------------------------------------------------------
# Generator harness: the shipped module's CLI entry point, called with the
# argv cm_release_generate_notes passes (each sibling test module keeps its
# own copy of this small harness).
# ---------------------------------------------------------------------------


def _make_bundle(tmp_path: pathlib.Path, item_paths: list[pathlib.Path]) -> pathlib.Path:
    bundle_path = tmp_path / "bundle.md"
    lines = ["# Bundle", "", "## Bundled Items"]
    for item_path in item_paths:
        lines.append(f"- fixture item (`{item_path}`)")
    bundle_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return bundle_path


def _generate_notes(
    tmp_path: pathlib.Path,
    *,
    bundle_file: str,
    active_rc: str = "v9.9.9",
    bugs_skipped_block: str = "",
) -> str:
    """Run the shipped generator with the full 10-argument contract
    cm_release_generate_notes passes (argument 9 is
    _KNOWN_ISSUE_NOTES_BLOCK, left empty here since no test in this module
    exercises the SHIP-KNOWN-ISSUE path; argument 10 is
    _BUGS_SKIPPED_NOTES_BLOCK, the value under test)."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir(exist_ok=True)

    argv = [
        bundle_file,             # bundle_file_arg
        str(repo_root),          # repo_root
        active_rc,                # active_rc
        "FUNCTIONAL",              # status
        "2026-09-10",                # release_date
        "test-project",                # project_name
        "none",                          # merge_base
        "rc/" + active_rc,                 # rc_branch
        "",                                  # known_issue_block
        bugs_skipped_block,                    # bugs_skipped_block
    ]
    rc = release_notes_main(argv)
    assert rc == 0, f"release-notes generator returned {rc}"

    out_file = repo_root / "release-notes" / f"{active_rc}.md"
    assert out_file.exists(), f"Expected release notes file not written: {out_file}"
    return out_file.read_text(encoding="utf-8")


def _parse_section(text: str, heading: str) -> str:
    """Same section-extraction convention the generator itself uses."""
    pattern = re.compile(
        r"^##\s+" + re.escape(heading) + r"\s*$", re.IGNORECASE | re.MULTILINE
    )
    match = pattern.search(text)
    assert match, f"'## {heading}' heading not found in generated notes"
    start = match.end()
    next_heading = re.search(r"^##\s+", text[start:], re.MULTILINE)
    end = start + next_heading.start() if next_heading else len(text)
    return text[start:end].strip()


def _write_bug(bugs_dir: pathlib.Path, bug_id: str, slug: str, symptom: str) -> pathlib.Path:
    bugs_dir.mkdir(parents=True, exist_ok=True)
    path = bugs_dir / f"{bug_id}-{slug}.md"
    path.write_text(
        _BUG_TEMPLATE.format(bug_id=bug_id, slug=slug, symptom=symptom),
        encoding="utf-8",
    )
    return path


def _build_bugs_skipped_block(tmp_path: pathlib.Path, *pairs: tuple[pathlib.Path, str]) -> str:
    """Build the full "## Bugs Skipped" block the same way
    cm_build_bugs_skipped_ship_block does: build_bugs_skipped_notes_block's
    rows, wrapped in the table's own static header/separator. Sourcing the
    real shell library (rather than hand-rendering rows) means this test
    exercises the real scrub, not a re-implementation of it."""
    args = " ".join(f"'{p}' '{r}'" for p, r in pairs)
    result = run_bash(
        tmp_path,
        f"source {_KNOWN_ISSUE_LIB} && build_bugs_skipped_notes_block {args}",
    )
    assert result.returncode == 0, result.stderr
    rows = result.stdout.strip("\n")
    return f"{_BUGS_SKIPPED_HEADER}\n{_BUGS_SKIPPED_SEPARATOR}\n{rows}"


# ---------------------------------------------------------------------------
# Acceptance criterion 2: six-agent-prefix task identifiers in the rationale
# text are absent from the generated notes file.
# ---------------------------------------------------------------------------


_SIX_AGENT_PREFIXES = ["PM", "CODER", "WRITER", "CM", "TESTER", "PO"]


def test_six_agent_prefix_task_ids_in_rationale_do_not_survive_render(tmp_path):
    bugs_dir = tmp_path / "bugs"
    pairs = []
    task_ids = []
    for i, prefix in enumerate(_SIX_AGENT_PREFIXES):
        task_id = f"{prefix}-20260910-{100 + i:03d}-verify-2-15-3"
        task_ids.append(task_id)
        bug_path = _write_bug(
            bugs_dir,
            f"BUG-05{i:02d}",
            f"finding-{i}",
            f"Component {i} misbehaves under load.",
        )
        rationale = f"Deferred; see {task_id} for the root cause."
        pairs.append((bug_path, rationale))

    bugs_skipped_block = _build_bugs_skipped_block(tmp_path, *pairs)
    # Sanity: the block itself must already be scrubbed before it ever
    # reaches the generator (the generator is a pass-through, not a scrub).
    for task_id in task_ids:
        assert task_id not in bugs_skipped_block

    bundle = _make_bundle(tmp_path, [])
    notes = _generate_notes(
        tmp_path, bundle_file=str(bundle), bugs_skipped_block=bugs_skipped_block
    )

    assert "## Bugs Skipped" in notes
    for prefix, task_id in zip(_SIX_AGENT_PREFIXES, task_ids):
        assert task_id not in notes
        assert f"{prefix}-20260910" not in notes


# ---------------------------------------------------------------------------
# Acceptance criterion 3: empty block -> explicit empty state, no table
# header, separator, or trailing blank artefacts (byte-stability).
# ---------------------------------------------------------------------------


def test_empty_block_renders_explicit_empty_state_with_no_table_artefacts(tmp_path):
    bundle = _make_bundle(tmp_path, [])
    notes = _generate_notes(tmp_path, bundle_file=str(bundle), bugs_skipped_block="")

    assert "## Bugs Skipped" in notes
    section_body = _parse_section(notes, "Bugs Skipped")
    assert section_body == "None"
    assert "|" not in section_body
    assert "Substance" not in section_body


# ---------------------------------------------------------------------------
# Acceptance criterion 4: a non-empty block's own header/separator survive
# the render unchanged -- the generator places the block, it does not
# recompute or reformat it.
# ---------------------------------------------------------------------------


def test_non_empty_block_header_and_separator_pass_through_unchanged(tmp_path):
    bugs_dir = tmp_path / "bugs"
    bug_path = _write_bug(bugs_dir, "BUG-0600", "single-finding", "A minor rendering glitch.")
    bugs_skipped_block = _build_bugs_skipped_block(
        tmp_path, (bug_path, "Low impact; deferred to next release.")
    )

    bundle = _make_bundle(tmp_path, [])
    notes = _generate_notes(
        tmp_path, bundle_file=str(bundle), bugs_skipped_block=bugs_skipped_block
    )

    section_body = _parse_section(notes, "Bugs Skipped")
    assert _BUGS_SKIPPED_HEADER in section_body
    assert _BUGS_SKIPPED_SEPARATOR in section_body
    assert "A minor rendering glitch" in section_body
    assert "Low impact; deferred to next release." in section_body
    assert "BUG-0600" not in section_body


# ---------------------------------------------------------------------------
# Abbreviated task references and requirements-document paths in a finding's
# prose are scrubbed like the full task ID.
# ---------------------------------------------------------------------------


_SHORT_TASK_REF_RE = re.compile(r"\b(CODER|WRITER|TESTER|PM|CM)-[0-9]{3}\b", re.IGNORECASE)


def test_abbreviated_task_refs_do_not_survive_render(tmp_path):
    bugs_dir = tmp_path / "bugs"
    first = _write_bug(
        bugs_dir,
        "BUG-0700",
        "split-review",
        "The split was reviewed against CODER-019's split and WRITER-021's prose.",
    )
    second = _write_bug(bugs_dir, "BUG-0701", "probe-finding", "A probe confirmed the gap.")
    bugs_skipped_block = _build_bugs_skipped_block(
        tmp_path,
        (first, "Applied to CODER-019 split; Step 6.3 documentation-layer review."),
        (second, "Raised by CODER-016 status Summary, confirmed by TESTER-020 and pm-003."),
    )
    assert not _SHORT_TASK_REF_RE.search(bugs_skipped_block), bugs_skipped_block

    notes = _generate_notes(
        tmp_path, bundle_file=str(_make_bundle(tmp_path, [])), bugs_skipped_block=bugs_skipped_block
    )

    section_body = _parse_section(notes, "Bugs Skipped")
    assert "another item" in section_body
    assert "Step 6.3 documentation-layer review." in section_body
    assert not _SHORT_TASK_REF_RE.search(notes), (
        f"an abbreviated task reference survived the render:\n{section_body}"
    )


def test_requirements_document_path_does_not_survive_render(tmp_path):
    bug_path = _write_bug(
        tmp_path / "bugs",
        "BUG-0702",
        "requirement-intent",
        "The v2.26.0 requirement (`requirements/v2.26.0-agent-metrics-v2.md`, Intent) "
        "exists because the table says 1.",
    )
    bugs_skipped_block = _build_bugs_skipped_block(
        tmp_path, (bug_path, "Named in requirements/v2.26.0-agent-metrics-v2.md as intended.")
    )

    notes = _generate_notes(
        tmp_path, bundle_file=str(_make_bundle(tmp_path, [])), bugs_skipped_block=bugs_skipped_block
    )

    assert "requirements/" not in notes
    assert "the release's requirements document" in _parse_section(notes, "Bugs Skipped")


# ---------------------------------------------------------------------------
# The Rationale for Skipping cell is a readable skip reason, not the
# finding's first detail bullet. TESTER's report format opens each finding's
# detail with a "- Traces to:" bullet that quotes task and bug identifiers;
# the cell must carry none of that bullet, its list marker, or the scrub's
# "another item" stand-in for the identifiers it quoted.
# ---------------------------------------------------------------------------


# Shaped like a real TESTER report: rating fields, then detail bullets that
# open with "- Traces to:" and quote identifiers. One finding of each
# category the pairing step accepts, plus a gap whose ratings are absent or
# off-vocabulary so the sentence has to degrade without going empty.
_TRACES_FIRST_REPORT = """# Verification Report: v9.9.9

## Recommendation
SHIP-WITH-CONCERNS

## Systemic Risk
medium

## Findings

### Finding 1: Status reader fails open on a quoted opener

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** medium (the same defect class could recur in a sibling reader)
**Filed As:** BUG-0910 (Path C-A)
**Regression:** yes
**Integrity:** none

- Traces to: requirements AC "each bundled item is addressed" (BUG-0901 / BUG-0902),
  and the CODER-20260910-002-notes-status constraint "the guard stays a real stop".
- Location: `team/pgai_agent_kanban/cm/notes_status_section.py`.
- Expected: a quoted opener is text.
- Actual: the placeholder survived the stamp.

### Finding 2: Drill deletes the bundle doc after a failed cancel

**Category:** pre-existing-bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (isolated to one code path)
**Filed As:** BUG-0911 (Path C-A)
**Regression:** no
**Integrity:** none

- Traces to: the CODER-003 ticket Notes ("record it in status.md for TESTER to file").
- Location: `team/pgai_agent_kanban/overwatch/resolution_stage.py`.

### Finding 3: A test still asserts the old separator

**Category:** stale-assertion
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (isolated)
**Filed As:** BUG-0912 (Path C-A)

- Traces to: TESTER-20260910-007-verify-9-9-9 Step 7 evidence.
- Test file / line: `team/tests/unit/test_example.py:12`.

### Finding 4: A criterion is only partly met

**Category:** gap
**Fix Effort:** unknown
**Filed As:** BUG-0913 (Path C-A)

- Traces to: requirements AC "the report names every lane" (PM-20260910-001-decompose).

### Finding 5: Per-criterion results

**Category:** pass

- Traces to: every acceptance criterion.

## Lint Findings (non-blocking)

None.
"""

_TRACES_FIRST_BUG_IDS = ["BUG-0910", "BUG-0911", "BUG-0912", "BUG-0913"]


def _render_bugs_skipped_from_report(tmp_path: pathlib.Path) -> tuple[str, str]:
    """Drive the real resolve-and-render call release_ship_policy.sh makes
    (cm_build_bugs_skipped_ship_block) over the fixture report, then place
    the resulting block through the shipped notes generator. Returns the
    block and the rendered notes' "## Bugs Skipped" section body."""
    bugs_dir = tmp_path / "bugs"
    for i, bug_id in enumerate(_TRACES_FIRST_BUG_IDS):
        _write_bug(bugs_dir, bug_id, f"skipped-{i}", f"Defect number {i} misbehaves.")
    report = tmp_path / "report.md"
    report.write_text(_TRACES_FIRST_REPORT, encoding="utf-8")

    result = run_bash(
        tmp_path,
        f"source {_KNOWN_ISSUE_LIB} && "
        f"cm_build_bugs_skipped_ship_block '{report}' '{bugs_dir}' && "
        'printf "%s\\n" "$_BUGS_SKIPPED_NOTES_BLOCK"',
    )
    assert result.returncode == 0, result.stderr
    block = result.stdout.strip("\n")
    assert block, f"cm_build_bugs_skipped_ship_block rendered nothing.\nstderr:\n{result.stderr}"

    notes = _generate_notes(
        tmp_path, bundle_file=str(_make_bundle(tmp_path, [])), bugs_skipped_block=block
    )
    return block, _parse_section(notes, "Bugs Skipped")


def _rationale_cells(section_body: str) -> list[str]:
    """The last cell of every table row below the header and separator. A
    literal '|' inside a cell is escaped as '\\|' by the renderer, so only an
    unescaped '|' is a column boundary."""
    rows = [
        line for line in section_body.splitlines()
        if line.startswith("|") and line != _BUGS_SKIPPED_HEADER and line != _BUGS_SKIPPED_SEPARATOR
    ]
    cells = []
    for row in rows:
        parts = re.split(r"(?<!\\)\|", row.strip())
        assert len(parts) == 6, f"expected a four-cell row, got {row!r}"
        cells.append(parts[4].strip())
    return cells


def test_rationale_cells_from_traces_first_report_are_readable_prose(tmp_path):
    """No Rationale for Skipping cell is a finding's detail bullet: none
    opens with a list marker, quotes "Traces to", or carries the scrub's
    "another item" stand-in, and every cell is non-empty."""
    _block, section_body = _render_bugs_skipped_from_report(tmp_path)
    rationales = _rationale_cells(section_body)

    # One row per bug/gap/pre-existing-bug/stale-assertion finding; the pass
    # finding adds none.
    assert len(rationales) == len(_TRACES_FIRST_BUG_IDS), section_body
    for rationale in rationales:
        assert rationale, f"empty Rationale for Skipping cell:\n{section_body}"
        assert not rationale.startswith("- "), (
            f"rationale is a list bullet, not prose: {rationale!r}"
        )
        assert "Traces to" not in rationale, (
            f"rationale is the finding's Traces-to detail line: {rationale!r}"
        )
        assert "another item" not in rationale, (
            f"rationale carries a scrubbed identifier stand-in: {rationale!r}"
        )


def test_rationale_cells_state_why_each_finding_shipped_unfixed(tmp_path):
    """Each cell states the skip reason from the finding's own ratings, and
    a finding with absent or off-vocabulary ratings still gets a sentence."""
    _block, section_body = _render_bugs_skipped_from_report(tmp_path)
    regression, pre_existing, stale, gap = _rationale_cells(section_body)

    assert regression == (
        "Introduced by this release and found during its verification, "
        "rated fix effort small and systemic risk medium; deferred to a follow-up release."
    )
    assert pre_existing.startswith("Predates this release")
    assert "systemic risk low" in pre_existing
    assert stale.startswith("A test assertion left stale")
    assert gap == "Found during verification of this release; deferred to a follow-up release."


# ---------------------------------------------------------------------------
# A "pre-existing-bug" finding names a defect the release did not introduce
# but ships unfixed, so a filed one gets a Bugs Skipped row like any other
# filed bug, and its rationale says it predates the release. The Regression
# line is only required on Systemic-Risk-high findings, so the category
# alone must carry "predates"; an explicit Regression value still leads.
# ---------------------------------------------------------------------------


_PRE_EXISTING_BUG_REPORT = """# Verification Report: v9.9.9

## Recommendation
SHIP-WITH-CONCERNS

## Findings

### Finding 1: defect predating the RC
**Category:** pre-existing-bug
**Fix Effort:** small
**Systemic Risk:** medium
{regression_line}
**Filed As:** BUG-0905

### Finding 2: the same kind of defect, never filed
**Category:** pre-existing-bug
**Fix Effort:** small
**Systemic Risk:** low
**Filed As:** none

## Lint Findings (non-blocking)
None.
"""


@pytest.mark.parametrize(
    "regression_line, lead",
    [
        pytest.param("", "Predates this release and was found during its verification", id="no-regression-line"),
        pytest.param("**Regression:** no", "Predates this release and was found during its verification", id="regression-no"),
        pytest.param("**Regression:** yes", "Introduced by this release and found during its verification", id="regression-yes"),
    ],
)
def test_filed_pre_existing_bug_finding_gets_a_bugs_skipped_row(tmp_path, regression_line, lead):
    """A filed pre-existing-bug finding produces exactly one Bugs Skipped row
    naming its bug file; the rationale says it predates the release unless
    the finding itself rates Regression yes. The unfiled one adds no row."""
    bug_file = _write_bug(tmp_path / "bugs", "BUG-0905", "predates", "A defect older than the release.")
    report_text = _PRE_EXISTING_BUG_REPORT.format(regression_line=regression_line)

    rows = _skipped_bug_pairs(tmp_path, report_text, "pre-existing.md")

    assert rows == (
        f"{bug_file}\t{lead}, rated fix effort small and systemic risk medium; "
        "deferred to a follow-up release.\n"
    )


def test_pre_existing_bug_row_renders_into_the_release_notes(tmp_path):
    """End to end: cm_build_bugs_skipped_ship_block and the notes generator
    carry the pre-existing-bug finding's row into "## Bugs Skipped", its
    rationale cell saying the bug predates the release."""
    _write_bug(tmp_path / "bugs", "BUG-0905", "predates", "A defect older than the release.")
    report = tmp_path / "report.md"
    report.write_text(_PRE_EXISTING_BUG_REPORT.format(regression_line=""), encoding="utf-8")
    result = run_bash(
        tmp_path,
        f"source {_KNOWN_ISSUE_LIB} && "
        f"cm_build_bugs_skipped_ship_block '{report}' '{tmp_path / 'bugs'}' && "
        'printf "%s\\n" "$_BUGS_SKIPPED_NOTES_BLOCK"',
    )
    assert result.returncode == 0, result.stderr
    block = result.stdout.strip("\n")

    notes = _generate_notes(
        tmp_path, bundle_file=str(_make_bundle(tmp_path, [])), bugs_skipped_block=block
    )
    rationales = _rationale_cells(_parse_section(notes, "Bugs Skipped"))

    assert rationales == [
        "Predates this release and was found during its verification, "
        "rated fix effort small and systemic risk medium; deferred to a follow-up release."
    ]


# ---------------------------------------------------------------------------
# A finding block opens at a heading of level 3 to 6 whose text starts with
# the word "Finding", and runs to the next heading no deeper than its own --
# the rule TESTER's report contract allows when a step heading groups
# findings, and the one the Known Issues stop list and ship-policy matrix
# also read.
# ---------------------------------------------------------------------------


# One qualifying finding at "#### Finding 1:" grouped under a "### Step"
# heading. The same report with the heading raised to "### Finding 1:" is
# the level-3 shape every existing report uses.
_STEP_GROUPED_REPORT = """# Verification Report: rc/v9.9.9

## Findings

### Step 6.4 — acceptance criteria

#### Finding 1: a real defect
**Category:** bug
**Fix Effort:** small
**Systemic Risk:** high
**Regression:** no
**Integrity:** none
**Filed As:** BUG-0001

## Lint Findings (non-blocking)
None.
"""


# "#### Finding 1" is a bug with no Filed As line of its own. The "### Step
# 6.5" heading that follows is shallower, so it must end that block: the
# Filed As line inside the step's own prose belongs to the step, not to
# Finding 1. "#### Finding 2" under the same step is a block of its own.
_STEP_ENDS_FINDING_REPORT = """# Verification Report: rc/v9.9.9

## Findings

### Step 6.4 — acceptance criteria

#### Finding 1: an unfiled defect
**Category:** bug
**Fix Effort:** small
**Systemic Risk:** low
**Regression:** no

### Step 6.5 — earlier findings carried forward
**Filed As:** BUG-0002

#### Finding 2: a filed defect
**Category:** gap
**Fix Effort:** medium
**Systemic Risk:** medium
**Regression:** yes
**Filed As:** BUG-0003

## Lint Findings (non-blocking)
None.
"""


def _skipped_bug_pairs(tmp_path: pathlib.Path, report_text: str, name: str) -> str:
    """Write *report_text* as *name* under tmp_path and return the stdout of
    read_ship_policy_skipped_bug_pairs run over it against tmp_path/bugs."""
    report = tmp_path / name
    report.write_text(report_text, encoding="utf-8")
    result = run_bash(
        tmp_path,
        f"source {_KNOWN_ISSUE_LIB} && "
        f"read_ship_policy_skipped_bug_pairs '{report}' '{tmp_path / 'bugs'}'",
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_level_four_finding_under_step_heading_renders_its_level_three_row(tmp_path):
    """A "#### Finding" grouped under a "### Step" heading yields exactly the
    row the same finding yields under a "### Finding" heading."""
    bug_file = _write_bug(tmp_path / "bugs", "BUG-0001", "alpha", "A real defect.")
    level_three = _STEP_GROUPED_REPORT.replace("#### Finding 1:", "### Finding 1:")
    assert level_three != _STEP_GROUPED_REPORT

    deep_rows = _skipped_bug_pairs(tmp_path, _STEP_GROUPED_REPORT, "deep.md")
    level_three_rows = _skipped_bug_pairs(tmp_path, level_three, "level-three.md")

    assert level_three_rows == (
        f"{bug_file}\tPredates this release and was found during its verification, "
        "rated fix effort small and systemic risk high; deferred to a follow-up release.\n"
    )
    assert deep_rows == level_three_rows, (
        "a level-4 finding under a step heading rendered differently from the "
        f"same finding at level 3.\nlevel 4:\n{deep_rows!r}\nlevel 3:\n{level_three_rows!r}"
    )


def test_shallower_step_heading_ends_a_level_four_finding_block(tmp_path):
    """A "### Step" heading after a "#### Finding" closes that finding, so a
    field line under the step is never read as the finding's own; the next
    "#### Finding" still opens a block of its own."""
    bugs_dir = tmp_path / "bugs"
    step_bug = _write_bug(bugs_dir, "BUG-0002", "step-prose", "Quoted in a step's prose.")
    filed_bug = _write_bug(bugs_dir, "BUG-0003", "filed", "A filed defect.")

    rows = _skipped_bug_pairs(tmp_path, _STEP_ENDS_FINDING_REPORT, "report.md")

    assert str(step_bug) not in rows, (
        "the Filed As line under '### Step 6.5' was read as part of the "
        f"preceding '#### Finding 1' block:\n{rows}"
    )
    assert rows == (
        f"{filed_bug}\tIntroduced by this release and found during its verification, "
        "rated fix effort medium and systemic risk medium; deferred to a follow-up release.\n"
    )


# ---------------------------------------------------------------------------
# One finding-heading rule: every report reader agrees on every heading shape.
# ---------------------------------------------------------------------------

# Each heading shape, and whether the one TESTER-report parser
# (pgai_agent_kanban.reports.tester_report, docstring Rule 1) opens a finding
# for it. Every reader below takes its findings from parse_report, so this
# is every reader's answer. The "False" shapes are the deliberate
# non-findings Rule 1 names: the plural "Findings ..." heading. A finding
# heading needs no space after its hashes, and a level-2 "Finding" heading
# opens a finding; the parser's heading-variants scenario fixture
# (tests/fixtures/tester_reports/scenarios/heading-variants.md) pins both.
_HEADING_SHAPES = [
    ("### Finding 1:", True),
    ("#### Finding 1:", True),
    ("##### Finding 1:", True),
    ("###Finding 1:", True),
    ("### Findings summary", False),
    ("## Findings", False),
    ("## Finding 1:", True),
    ("### Finding: no number", True),
]

_SHIP_POLICY_READER = "ship-policy (read_ship_policy_finding_dimensions)"
_BUGS_SKIPPED_READER = "Bugs Skipped (read_ship_policy_skipped_bug_pairs)"
_STOP_LIST_READER = "Known Issues stop list (resolve_stop_bug_files)"
_KNOWN_ISSUE_READER = "SHIP-KNOWN-ISSUE resolver (read_ship_policy_known_issue_bug_files)"
_OVERWATCH_READER = "OVERWATCH (matrix_findings)"

# A finding heading nested inside the probe finding, after its fields. The
# parser reads it as a sub-finding with its own fields (Rule 2), so every
# reader still reads the probe finding's own fields.
_NESTED_FINDING = "#### Finding 2: inner detail\n**Category:** pass\n\n"

# (heading, text after the probe finding's fields, whether readers read it)
_READER_AGREEMENT_CASES = [(heading, "", opens) for heading, opens in _HEADING_SHAPES] + [
    ("### Finding 1:", _NESTED_FINDING, True),
]
_READER_AGREEMENT_IDS = [heading for heading, _ in _HEADING_SHAPES] + [
    "### Finding 1: with a nested #### Finding 2:",
]

# One finding every reader would act on: a bug (a Bugs Skipped row), with
# Systemic Risk high (a Known Issues stop-list entry) and Regression no /
# Integrity none (the ship-policy dimensions "no none").
_HEADING_SHAPE_REPORT = """# Verification Report: rc/v9.9.9

## Systemic Risk
high

## Findings

{heading} heading-shape probe
**Category:** bug
**Fix Effort:** small
**Systemic Risk:** high
**Regression:** no
**Integrity:** none
**Filed As:** BUG-0003

{nested}## Lint Findings (non-blocking)
None.
"""


@pytest.mark.parametrize(
    "heading, nested, opens", _READER_AGREEMENT_CASES, ids=_READER_AGREEMENT_IDS
)
def test_every_report_reader_agrees_on_each_heading_shape(tmp_path, heading, nested, opens):
    """The Bugs Skipped reader, the Known Issues stop list, the
    SHIP-KNOWN-ISSUE bug resolver, the ship-policy dimensions reader and
    OVERWATCH's matrix findings each either read the finding under *heading*
    or all skip it, and the shared answer is the parser's Rule 1 --
    including when a finding heading is nested inside it (*nested*)."""
    bug_file = _write_bug(tmp_path / "bugs", "BUG-0003", "probe", "A probe defect.")
    report = tmp_path / "report.md"
    report.write_text(
        _HEADING_SHAPE_REPORT.format(heading=heading, nested=nested), encoding="utf-8"
    )

    bugs_skipped = run_bash(
        tmp_path,
        f"source {_KNOWN_ISSUE_LIB} && "
        f"read_ship_policy_skipped_bug_pairs '{report}' '{tmp_path / 'bugs'}'",
    )
    assert bugs_skipped.returncode == 0, bugs_skipped.stderr
    known_issue = run_bash(
        tmp_path,
        f"source {_KNOWN_ISSUE_LIB} && "
        f"read_ship_policy_known_issue_bug_files '{report}' '{tmp_path / 'bugs'}'",
    )
    assert known_issue.returncode == 0, known_issue.stderr
    dims = run_bash(
        tmp_path,
        f"source {_SHIP_POLICY_REPORT_LIB} && read_ship_policy_finding_dimensions '{report}'",
    )
    assert dims.returncode == 0, dims.stderr
    stop_list = resolve_stop_bug_files(
        str(report), str(tmp_path / "bugs"), CAUSE_SYSTEMIC_RISK_HIGH
    )
    overwatch_findings = matrix_findings(parse_report(report))

    read_by = {
        _BUGS_SKIPPED_READER: str(bug_file) in bugs_skipped.stdout,
        _STOP_LIST_READER: stop_list == [str(bug_file.resolve())],
        _KNOWN_ISSUE_READER: known_issue.stdout.splitlines() == [str(bug_file)],
        _SHIP_POLICY_READER: dims.stdout.strip() == "no none",
        _OVERWATCH_READER: (
            bool(overwatch_findings) and overwatch_findings[0]["filed_as"] == "BUG-0003"
        ),
    }
    assert set(read_by.values()) == {opens}, (
        f"heading {heading!r}: expected every reader to "
        f"{'read' if opens else 'skip'} the finding, got {read_by}.\n"
        f"Bugs Skipped stdout: {bugs_skipped.stdout!r}\n"
        f"SHIP-KNOWN-ISSUE resolver stdout: {known_issue.stdout!r}\n"
        f"ship-policy stdout: {dims.stdout!r} stderr: {dims.stderr!r}\n"
        f"stop list: {stop_list!r}\n"
        f"OVERWATCH findings: {overwatch_findings!r}"
    )
    if not opens:
        # Skipped means "no block opened", never a block read as unknown.
        assert dims.stdout.strip() == "unknown unknown"
        assert "zero finding blocks" in dims.stderr


# ---------------------------------------------------------------------------
# One field-line rule: the four finding-field readers agree on every shape.
# ---------------------------------------------------------------------------

# Each field-line shape: an id, the field-line template ({label} and
# {value}), the Filed As value, and whether the one report parser
# (pgai_agent_kanban.reports.tester_report, docstring Rule 3), which all four
# readers read their fields through, reads the finding's fields. A label with no
# colon is prose, not a field line, so that shape is the one every reader
# skips -- the matrix included, so it never decides from it.
_FIELD_LINE_SHAPES = [
    ("bold labels, bare id", "**{label}:** {value}", "BUG-0003", True),
    ("plain labels", "{label}: {value}", "BUG-0003", True),
    ("bold label outside its colon", "**{label}**: {value}", "BUG-0003", True),
    ("backticked id", "**{label}:** {value}", "`BUG-0003`", True),
    ("see BUG-0003", "**{label}:** {value}", "see BUG-0003", True),
    (
        "cites the existing bug",
        "**{label}:** {value}",
        "cites the existing BUG-0003-probe (open)",
        True,
    ),
    ("labels with no colon", "**{label}** {value}", "BUG-0003", False),
]

# The probe finding: a bug (a Bugs Skipped row), Systemic Risk high (a
# Known Issues stop-list entry and the SHIP-KNOWN-ISSUE resolver's input),
# Fix Effort small with Regression no / Integrity none (the ship-policy
# dimensions "no none", so the matrix decides SHIP-KNOWN-ISSUE).
_FIELD_SHAPE_FIELDS = [
    ("Category", "bug"),
    ("Fix Effort", "small"),
    ("Systemic Risk", "high"),
    ("Regression", "no"),
    ("Integrity", "none"),
]


def _field_shape_report(template: str, filed_as: str) -> str:
    """The probe report with every field line written as *template*."""
    lines = [template.format(label=label, value=value) for label, value in _FIELD_SHAPE_FIELDS]
    lines.append(template.format(label="Filed As", value=filed_as))
    return (
        "# Verification Report: rc/v9.9.9\n\n## Systemic Risk\nhigh\n\n## Findings\n\n"
        "### Finding 1: field-shape probe\n" + "\n".join(lines) + "\n\n"
        "## Lint Findings (non-blocking)\nNone.\n"
    )


@pytest.mark.parametrize(
    "template, filed_as, reads",
    [shape[1:] for shape in _FIELD_LINE_SHAPES],
    ids=[shape[0] for shape in _FIELD_LINE_SHAPES],
)
def test_all_four_field_readers_agree_on_each_field_line_shape(tmp_path, template, filed_as, reads):
    """The ship-policy dimensions reader, the Known Issues stop list, the
    SHIP-KNOWN-ISSUE bug resolver and the Bugs Skipped reader all read the
    finding's fields written in each field-line shape, or all skip them,
    and whenever the matrix decides SHIP-KNOWN-ISSUE from the finding the
    resolver prints its bug file."""
    bug_file = _write_bug(tmp_path / "bugs", "BUG-0003", "probe", "A probe defect.")
    report = tmp_path / "report.md"
    report.write_text(_field_shape_report(template, filed_as), encoding="utf-8")

    bugs_skipped = run_bash(
        tmp_path,
        f"source {_KNOWN_ISSUE_LIB} && "
        f"read_ship_policy_skipped_bug_pairs '{report}' '{tmp_path / 'bugs'}'",
    )
    assert bugs_skipped.returncode == 0, bugs_skipped.stderr
    known_issue = run_bash(
        tmp_path,
        f"source {_KNOWN_ISSUE_LIB} && "
        f"read_ship_policy_known_issue_bug_files '{report}' '{tmp_path / 'bugs'}'",
    )
    assert known_issue.returncode == 0, known_issue.stderr
    matrix = run_bash(
        tmp_path,
        f"source {_SHIP_POLICY_LIB} && "
        f"read -r reg integ <<< \"$(read_ship_policy_finding_dimensions '{report}')\" && "
        'echo "$reg $integ" && '
        'cm_ship_policy done high ship-with-concerns no yes "$reg" "$integ"',
    )
    assert matrix.returncode == 0, matrix.stderr
    dims, decision = matrix.stdout.splitlines()[0], matrix.stdout.splitlines()[-1]
    stop_list = resolve_stop_bug_files(
        str(report), str(tmp_path / "bugs"), CAUSE_SYSTEMIC_RISK_HIGH
    )

    read_by = {
        "ship-policy (read_ship_policy_finding_dimensions)": dims == "no none",
        "Known Issues stop list (resolve_stop_bug_files)": stop_list == [str(bug_file.resolve())],
        "SHIP-KNOWN-ISSUE resolver (read_ship_policy_known_issue_bug_files)": (
            known_issue.stdout.splitlines() == [str(bug_file)]
        ),
        "Bugs Skipped (read_ship_policy_skipped_bug_pairs)": str(bug_file) in bugs_skipped.stdout,
    }
    assert set(read_by.values()) == {reads}, (
        f"field lines shaped {template!r} with Filed As {filed_as!r}: expected "
        f"every reader to {'read' if reads else 'skip'} the finding, got {read_by}.\n"
        f"ship-policy dims: {dims!r}, decision: {decision!r}\n"
        f"SHIP-KNOWN-ISSUE resolver stdout: {known_issue.stdout!r}\n"
        f"Bugs Skipped stdout: {bugs_skipped.stdout!r}\n"
        f"stop list: {stop_list!r}"
    )
    if decision == "SHIP-KNOWN-ISSUE":
        assert known_issue.stdout.splitlines() == [str(bug_file)], (
            "the matrix decided SHIP-KNOWN-ISSUE from a high-risk finding whose "
            f"bug file the resolver did not print: {known_issue.stdout!r}"
        )
    assert (decision == "SHIP-KNOWN-ISSUE") is reads, (
        f"expected the matrix {'to' if reads else 'not to'} decide SHIP-KNOWN-ISSUE; "
        f"got {decision!r} from dims {dims!r}"
    )
