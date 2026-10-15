"""
test_cm_known_issue_carry_forward_bash.py
==========================================
Behavioral unit tests for the SHIP-KNOWN-ISSUE support functions in
team/scripts/lib/cm/release_known_issue.sh:

  - read_ship_policy_known_issue_bug_files: resolves a TESTER report's
    high-systemic-risk finding(s) to their own bug file(s) on disk via the
    finding's own Filed As field, read through parse_report.
  - build_known_issue_notes_block: renders the "## Known Issues" prose
    block for a known-issue ship, one bullet per bug file, by substance
    (never the bare internal ticket ID).
  - cm_stamp_release_notes_status: stamps a release-notes file's "##
    Status" placeholder, and (only for target_status=KNOWN-ISSUE with a
    non-empty block) the "## Known Issues" section body, in one write.
  - cm_stamp_known_issue_marker: stamps the "## Known Issue Carry Forward"
    field onto a bug file, idempotently.

Tests source the shell library and invoke each function via the bash
harness, asserting on stdout and on-disk file content. No CM release run is
involved here — these are the pure building blocks; the end-to-end wiring
through cm-release.sh is covered by the integration suite.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from tests.unit.shell_harness import run_bash

_LIB = "scripts/lib/cm/release_known_issue.sh"

# A hyphen preceded by whitespace or the start of the string and followed
# by an alphanumeric character is the exact shape a stranded task-ID slug
# fragment takes (e.g. "-verify-2-14-1") when the scrub's closing boundary
# stops at the identifier's numeric head instead of consuming the whole
# kebab-case slug. A clean scrub leaves none of these behind.
_STRANDED_HYPHEN_FRAGMENT_RE = re.compile(r"(?:^|\s)-[A-Za-z0-9]")

_REPORT_TEMPLATE = """# Verification Report: rc/v0.0.0

## Systemic Risk
high

## Findings

{findings}
## Lint Findings (non-blocking)

None.
"""

_FINDING_TEMPLATE = """### Finding {n}: {title}

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** {risk} ({risk_note})
**Regression:** {regression}
**Integrity:** {integrity}
**Filed As:** {filed_as} (Path C)

"""

_BUG_TEMPLATE = """# {bug_id}-{slug}

**Bug ID:** {bug_id}-{slug}
**Filed By:** TESTER
**Severity:** high

## Status
open

## Category
misc

---

## Symptom
{symptom}

## Expected
Expected behavior.

## Actual
Actual behavior.
"""


def _write_bug(bugs_dir: pathlib.Path, bug_id: str, slug: str, symptom: str, public_id: str = "") -> pathlib.Path:
    bugs_dir.mkdir(parents=True, exist_ok=True)
    body = _BUG_TEMPLATE.format(bug_id=bug_id, slug=slug, symptom=symptom)
    if public_id:
        body += f"\n## Public ID\n{public_id}\n"
    path = bugs_dir / f"{bug_id}-{slug}.md"
    path.write_text(body, encoding="utf-8")
    return path


def _write_report(tmp_path: pathlib.Path, findings: list[dict]) -> pathlib.Path:
    blocks = "".join(
        _FINDING_TEMPLATE.format(
            n=i + 1,
            title=f.get("title", "a finding"),
            risk=f.get("risk", "high"),
            risk_note=f.get("risk_note", "note"),
            regression=f.get("regression", "no"),
            integrity=f.get("integrity", "none"),
            filed_as=f.get("filed_as", ""),
        )
        for i, f in enumerate(findings)
    )
    report = tmp_path / "report.md"
    report.write_text(_REPORT_TEMPLATE.format(findings=blocks), encoding="utf-8")
    return report


# ---------------------------------------------------------------------------
# read_ship_policy_known_issue_bug_files
# ---------------------------------------------------------------------------


def test_resolves_high_risk_finding_to_its_own_bug_file(tmp_path):
    bugs_dir = tmp_path / "bugs"
    _write_bug(bugs_dir, "BUG-0042", "legacy-artefact-defect", "A pre-existing crash nobody has run.")
    report = _write_report(
        tmp_path,
        [{"filed_as": "BUG-0042"}],
    )
    result = run_bash(
        tmp_path,
        f"source {_LIB} && read_ship_policy_known_issue_bug_files '{report}' '{bugs_dir}'",
    )
    assert result.returncode == 0, result.stderr
    resolved = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    assert resolved == [str(bugs_dir / "BUG-0042-legacy-artefact-defect.md")]


def test_low_risk_finding_never_contributes_a_bug_file(tmp_path):
    bugs_dir = tmp_path / "bugs"
    _write_bug(bugs_dir, "BUG-0011", "unrelated-low-risk-item", "A minor cosmetic issue.")
    report = _write_report(
        tmp_path,
        [{"filed_as": "BUG-0011", "risk": "low", "risk_note": "isolated"}],
    )
    result = run_bash(
        tmp_path,
        f"source {_LIB} && read_ship_policy_known_issue_bug_files '{report}' '{bugs_dir}'",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""


def test_multiple_high_risk_findings_resolve_to_multiple_distinct_bug_files(tmp_path):
    bugs_dir = tmp_path / "bugs"
    _write_bug(bugs_dir, "BUG-0001", "first-defect", "First pre-existing defect.")
    _write_bug(bugs_dir, "BUG-0002", "second-defect", "Second pre-existing defect.")
    report = _write_report(
        tmp_path,
        [
            {"filed_as": "BUG-0001"},
            {"filed_as": "BUG-0002"},
        ],
    )
    result = run_bash(
        tmp_path,
        f"source {_LIB} && read_ship_policy_known_issue_bug_files '{report}' '{bugs_dir}'",
    )
    assert result.returncode == 0, result.stderr
    resolved = {line.strip() for line in result.stdout.splitlines() if line.strip()}
    assert resolved == {
        str(bugs_dir / "BUG-0001-first-defect.md"),
        str(bugs_dir / "BUG-0002-second-defect.md"),
    }


def test_duplicate_filed_as_across_findings_resolves_once(tmp_path):
    bugs_dir = tmp_path / "bugs"
    _write_bug(bugs_dir, "BUG-0007", "shared-defect", "One defect cited by two findings.")
    report = _write_report(
        tmp_path,
        [
            {"filed_as": "BUG-0007", "title": "first mention"},
            {"filed_as": "BUG-0007", "title": "second mention"},
        ],
    )
    result = run_bash(
        tmp_path,
        f"source {_LIB} && read_ship_policy_known_issue_bug_files '{report}' '{bugs_dir}'",
    )
    assert result.returncode == 0, result.stderr
    resolved = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    assert resolved == [str(bugs_dir / "BUG-0007-shared-defect.md")]


def test_unresolvable_filed_as_is_skipped_with_a_warning(tmp_path):
    bugs_dir = tmp_path / "bugs"
    bugs_dir.mkdir(parents=True, exist_ok=True)
    report = _write_report(
        tmp_path,
        [{"filed_as": "BUG-9999"}],
    )
    result = run_bash(
        tmp_path,
        f"source {_LIB} && read_ship_policy_known_issue_bug_files '{report}' '{bugs_dir}'",
    )
    assert result.returncode == 0
    assert result.stdout.strip() == ""
    assert "WARNING" in result.stderr
    assert "BUG-9999" in result.stderr


def test_missing_report_prints_nothing(tmp_path):
    bugs_dir = tmp_path / "bugs"
    bugs_dir.mkdir(parents=True, exist_ok=True)
    missing = tmp_path / "does-not-exist.md"
    result = run_bash(
        tmp_path,
        f"source {_LIB} && read_ship_policy_known_issue_bug_files '{missing}' '{bugs_dir}'",
    )
    assert result.returncode == 0
    assert result.stdout.strip() == ""


# A high-risk finding grouped one level down under a step heading, then a
# shallower step heading whose prose carries field-shaped lines of its own.
_STEP_GROUPED_KNOWN_ISSUE_REPORT = """# Verification Report: rc/v0.0.0

## Findings

### Step 6: verify the ship path

#### Finding 1: grouped under a step
**Category:** bug
**Systemic Risk:** high
**Filed As:** BUG-0021

#### Finding 2: grouped, no rating of its own
**Category:** bug

### Step 7: prose that quotes field lines
**Systemic Risk:** high
**Filed As:** BUG-0022

## Lint Findings (non-blocking)
None.
"""


def test_step_grouped_finding_resolves_and_a_shallower_heading_ends_its_block(tmp_path):
    """A "#### Finding" resolves to its bug exactly as a "### Finding" does,
    and the "### Step" heading after the last finding ends that block, so
    the step's quoted field lines are never read as the finding's own."""
    bugs_dir = tmp_path / "bugs"
    grouped = _write_bug(bugs_dir, "BUG-0021", "grouped", "A defect filed from a step.")
    _write_bug(bugs_dir, "BUG-0022", "step-prose", "Quoted only in a step's prose.")
    report = tmp_path / "report.md"
    report.write_text(_STEP_GROUPED_KNOWN_ISSUE_REPORT, encoding="utf-8")

    result = run_bash(
        tmp_path,
        f"source {_LIB} && read_ship_policy_known_issue_bug_files '{report}' '{bugs_dir}'",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [str(grouped)], (
        "expected only the level-4 finding's own bug; the step prose after "
        f"it must not lend Finding 2 a Systemic Risk or Filed As:\n{result.stdout}"
    )


# ---------------------------------------------------------------------------
# Both release-notes resolvers read the findings parse_report reads
# ---------------------------------------------------------------------------

# The parser's own scenario fixtures (tests/fixtures/tester_reports/scenarios),
# which pin the same shapes for parse_report.
_SCENARIOS = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "tester_reports" / "scenarios"

# The report's only finding, one level deeper than usual and grouped under
# no step heading at all.
_ONLY_FINDING_AT_LEVEL_FOUR_REPORT = """# Verification Report: rc/v0.0.0

## Findings

#### Finding 1: the report's only finding
**Category:** bug
**Fix Effort:** small
**Systemic Risk:** high
**Regression:** no
**Integrity:** none
**Filed As:** BUG-0031

## Lint Findings (non-blocking)
None.
"""


def _resolver_outputs(tmp_path: pathlib.Path, report: pathlib.Path, bugs_dir: pathlib.Path):
    """(known-issue bug files, Bugs Skipped rows) for *report*, each the
    stdout lines of its release_known_issue.sh resolver."""
    known_issue = run_bash(
        tmp_path,
        f"source {_LIB} && read_ship_policy_known_issue_bug_files '{report}' '{bugs_dir}'",
    )
    assert known_issue.returncode == 0, known_issue.stderr
    skipped = run_bash(
        tmp_path,
        f"source {_LIB} && read_ship_policy_skipped_bug_pairs '{report}' '{bugs_dir}'",
    )
    assert skipped.returncode == 0, skipped.stderr
    return known_issue.stdout.splitlines(), skipped.stdout.splitlines()


def test_only_finding_at_level_four_yields_its_skipped_row_and_known_issue_bug(tmp_path):
    """A report whose only finding sits under a level-4 "Finding 1:" heading
    yields that finding's bug file to the SHIP-KNOWN-ISSUE resolver and its
    row to the Bugs Skipped table."""
    bugs_dir = tmp_path / "bugs"
    bug_file = _write_bug(bugs_dir, "BUG-0031", "only-finding", "The one defect the report found.")
    report = tmp_path / "report.md"
    report.write_text(_ONLY_FINDING_AT_LEVEL_FOUR_REPORT, encoding="utf-8")

    known_issue, skipped = _resolver_outputs(tmp_path, report, bugs_dir)

    assert known_issue == [str(bug_file)]
    assert skipped == [
        f"{bug_file}\tPredates this release and was found during its verification, "
        "rated fix effort small and systemic risk high; deferred to a follow-up release."
    ]


@pytest.mark.parametrize(
    "inner_filed_as",
    ["", "**Filed As:** BUG-0004\n"],
    ids=["inner unfiled", "inner filed as another bug"],
)
def test_nested_finding_keeps_the_outer_findings_filed_as_in_both_outputs(tmp_path, inner_filed_as):
    """A level-3 finding holding a nested level-4 finding keeps its own
    Filed As in both outputs: the nested finding (a pass, filed or not)
    neither drops nor replaces the outer finding's bug."""
    bugs_dir = tmp_path / "bugs"
    outer = _write_bug(bugs_dir, "BUG-0003", "outer", "The outer finding's defect.")
    _write_bug(bugs_dir, "BUG-0004", "inner", "Named only by the nested finding.")
    text = (_SCENARIOS / "nested-finding-keeps-outer-fields.md").read_text(encoding="utf-8")
    marker = "**Category:** pass\n"
    assert marker in text
    report = tmp_path / "report.md"
    report.write_text(text.replace(marker, marker + inner_filed_as), encoding="utf-8")

    known_issue, skipped = _resolver_outputs(tmp_path, report, bugs_dir)

    assert known_issue == [str(outer)]
    assert skipped == [
        f"{outer}\tPredates this release and was found during its verification, "
        "rated fix effort small and systemic risk high; deferred to a follow-up release."
    ]


# ---------------------------------------------------------------------------
# read_ship_policy_skipped_bug_pairs
# ---------------------------------------------------------------------------
#
# The render-path counterpart of read_ship_policy_known_issue_bug_files:
# resolves every bug/gap/stale-assertion finding to a (bug_file, rationale)
# pair for "## Bugs Skipped", the section the release pipeline previously
# left CM to hand-author from the TESTER report with no reader and no scrub
# in front of it.


_SKIPPED_FINDING_TEMPLATE = """### Finding {n}: {title}

**Category:** {category}
**Fix Effort:** {fix_effort} (1-2 CODER tasks)
**Systemic Risk:** {risk} ({risk_note})
**Filed As:** {filed_as} (Path C)

{detail}
"""


def _write_skipped_report(tmp_path: pathlib.Path, findings: list[dict]) -> pathlib.Path:
    blocks = "".join(
        _SKIPPED_FINDING_TEMPLATE.format(
            n=i + 1,
            title=f.get("title", "a finding"),
            category=f.get("category", "bug"),
            fix_effort=f.get("fix_effort", "small"),
            risk=f.get("risk", "low"),
            risk_note=f.get("risk_note", "isolated"),
            filed_as=f.get("filed_as", ""),
            detail=f.get("detail", ""),
        )
        for i, f in enumerate(findings)
    )
    report = tmp_path / "report.md"
    report.write_text(
        f"# Verification Report: rc/v0.0.0\n\n## Systemic Risk\nlow\n\n"
        f"## Findings\n\n{blocks}## Lint Findings (non-blocking)\n\nNone.\n",
        encoding="utf-8",
    )
    return report


def test_skipped_pairs_rationale_is_a_skip_reason_built_from_the_findings_ratings(tmp_path):
    # The finding's free-text detail describes the defect, not why it was
    # skipped, so it never becomes the rationale; the rating fields do.
    bugs_dir = tmp_path / "bugs"
    _write_bug(bugs_dir, "BUG-0500", "deferred-defect", "A defect deferred to a future patch.")
    report = _write_skipped_report(
        tmp_path,
        [{"filed_as": "BUG-0500", "detail": "- Location: the importer. Low impact."}],
    )
    result = run_bash(
        tmp_path,
        f"source {_LIB} && read_ship_policy_skipped_bug_pairs '{report}' '{bugs_dir}'",
    )
    assert result.returncode == 0, result.stderr
    line = result.stdout.strip()
    path, rationale = line.split("\t")
    assert path == str(bugs_dir / "BUG-0500-deferred-defect.md")
    assert rationale == (
        "Found during verification of this release, rated fix effort small "
        "and systemic risk low; deferred to a follow-up release."
    )


def test_skipped_pairs_rationale_is_non_empty_with_no_detail_text(tmp_path):
    bugs_dir = tmp_path / "bugs"
    _write_bug(bugs_dir, "BUG-0501", "no-detail-defect", "A defect with no narrative detail.")
    report = _write_skipped_report(
        tmp_path,
        [{"filed_as": "BUG-0501", "detail": ""}],
    )
    result = run_bash(
        tmp_path,
        f"source {_LIB} && read_ship_policy_skipped_bug_pairs '{report}' '{bugs_dir}'",
    )
    assert result.returncode == 0, result.stderr
    line = result.stdout.strip()
    path, rationale = line.split("\t")
    assert path == str(bugs_dir / "BUG-0501-no-detail-defect.md")
    assert "fix effort small" in rationale
    assert "systemic risk low" in rationale
    assert rationale.endswith("deferred to a follow-up release.")


def test_skipped_pairs_excludes_a_pass_category_finding(tmp_path):
    bugs_dir = tmp_path / "bugs"
    bugs_dir.mkdir(parents=True, exist_ok=True)
    report = _write_skipped_report(
        tmp_path,
        [{"filed_as": "", "category": "pass", "detail": "Nothing to report."}],
    )
    result = run_bash(
        tmp_path,
        f"source {_LIB} && read_ship_policy_skipped_bug_pairs '{report}' '{bugs_dir}'",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""


def test_skipped_pairs_excludes_finding_already_covered_by_known_issue_block(tmp_path):
    bugs_dir = tmp_path / "bugs"
    known_issue_path = _write_bug(bugs_dir, "BUG-0502", "known-issue-cause", "The known-issue causing defect.")
    _write_bug(bugs_dir, "BUG-0503", "still-skipped", "A separate deferred defect.")
    report = _write_skipped_report(
        tmp_path,
        [
            {"filed_as": "BUG-0502", "detail": "Already rendered under Known Issues."},
            {"filed_as": "BUG-0503", "detail": "Deferred to a future release."},
        ],
    )
    result = run_bash(
        tmp_path,
        f"source {_LIB} && read_ship_policy_skipped_bug_pairs '{report}' '{bugs_dir}' '{known_issue_path}'",
    )
    assert result.returncode == 0, result.stderr
    lines = [l for l in result.stdout.splitlines() if l.strip()]
    assert len(lines) == 1
    path, rationale = lines[0].split("\t")
    assert path == str(bugs_dir / "BUG-0503-still-skipped.md")


@pytest.mark.parametrize("prefix", ["PM", "CODER", "WRITER", "CM", "TESTER", "PO"])
def test_skipped_pairs_rationale_never_carries_a_task_id_quoted_in_detail(tmp_path, prefix):
    # The rationale is composed from rating words alone, so an identifier
    # quoted in the finding's detail never reaches it — not even as the
    # downstream scrub's "another item" stand-in.
    task_id = f"{prefix}-20260910-011-verify-2-14-2"
    bugs_dir = tmp_path / "bugs"
    _write_bug(bugs_dir, "BUG-0504", "rationale-carries-task-id", "The importer crashes on a malformed header.")
    report = _write_skipped_report(
        tmp_path,
        [{"filed_as": "BUG-0504", "detail": f"Deferred; see {task_id} for the root cause."}],
    )
    result = run_bash(
        tmp_path,
        f"source {_LIB} && read_ship_policy_skipped_bug_pairs '{report}' '{bugs_dir}'",
    )
    assert result.returncode == 0, result.stderr
    line = result.stdout.strip()
    path, rationale = line.split("\t")
    assert task_id not in rationale
    assert f"{prefix}-20260910" not in rationale
    assert rationale.startswith("Found during verification of this release")


def test_skipped_pairs_missing_report_prints_nothing(tmp_path):
    bugs_dir = tmp_path / "bugs"
    bugs_dir.mkdir(parents=True, exist_ok=True)
    missing = tmp_path / "does-not-exist.md"
    result = run_bash(
        tmp_path,
        f"source {_LIB} && read_ship_policy_skipped_bug_pairs '{missing}' '{bugs_dir}'",
    )
    assert result.returncode == 0
    assert result.stdout.strip() == ""


# ---------------------------------------------------------------------------
# build_known_issue_notes_block
# ---------------------------------------------------------------------------


def test_block_names_the_bug_by_substance_not_bare_id(tmp_path):
    bugs_dir = tmp_path / "bugs"
    bug_path = _write_bug(
        bugs_dir, "BUG-0042", "legacy-artefact-defect",
        "The legacy artefact importer crashes on a malformed header it has never actually exercised.",
    )
    result = run_bash(
        tmp_path,
        f"source {_LIB} && build_known_issue_notes_block '{bug_path}'",
    )
    assert result.returncode == 0, result.stderr
    line = result.stdout.strip()
    assert "BUG-0042" not in line
    assert "malformed header" in line
    assert "internal-only, not yet disclosed" in line
    assert "No workaround available." in line


def test_block_cites_public_id_when_assigned(tmp_path):
    bugs_dir = tmp_path / "bugs"
    bug_path = _write_bug(
        bugs_dir, "BUG-0050", "disclosed-defect",
        "A disclosed pre-existing defect.",
        public_id="KI-2.12.0.3",
    )
    result = run_bash(
        tmp_path,
        f"source {_LIB} && build_known_issue_notes_block '{bug_path}'",
    )
    assert result.returncode == 0, result.stderr
    line = result.stdout.strip()
    assert "**KI-2.12.0.3**" in line
    assert "internal-only" not in line
    assert "BUG-0050" not in line


# The Known-Issues writing path must be structurally unable to emit a
# kanban task identifier, even when a bug's own Symptom prose quotes one
# inline (e.g. a "Source Task:" field or a pasted lint finding naming a
# task). This drives build_known_issue_notes_block with input carrying a
# task ID for each of the six kanban agent prefixes and asserts the
# emitted block contains none of it — general coverage, not just a single
# agent prefix.
#
# The prior coverage here asserted only that the full identifier was
# absent, which passed even against a mangled "-verify-2-14-1"
# stranded-slug output — the whole task ID really was gone, just not
# cleanly. The assertions below additionally pin the exact surviving
# sentence and a positive "no stranded hyphen fragment" check, so a
# regression that merely reintroduces the narrower boundary still fails
# the test even though the full identifier remains absent.
@pytest.mark.parametrize("prefix", ["PM", "CODER", "WRITER", "CM", "TESTER", "PO"])
def test_block_scrubs_task_id_from_symptom_text(tmp_path, prefix):
    task_id = f"{prefix}-20260910-008-verify-2-14-2"
    bugs_dir = tmp_path / "bugs"
    bug_path = _write_bug(
        bugs_dir, "BUG-0222", "release-notes-cite-task-id",
        f"The importer still crashes on a malformed header (Source Task: {task_id}).",
    )
    result = run_bash(
        tmp_path,
        f"source {_LIB} && build_known_issue_notes_block '{bug_path}'",
    )
    assert result.returncode == 0, result.stderr
    line = result.stdout.strip()
    assert task_id not in line
    assert prefix + "-20260910" not in line
    assert "malformed header" in line
    assert "BUG-0222" not in line
    # Positive assertion on the surviving text: the identifier's own
    # kebab-case slug must not survive as a fragment, and the removed
    # identifier is replaced with the scrub's neutral stand-in text
    # ("another item") rather than deleted outright, so the sentence
    # around it still reads as ordinary English instead of trailing off
    # after "Source Task: ".
    assert not _STRANDED_HYPHEN_FRAGMENT_RE.search(line), line
    assert "-verify-2-14-2" not in line
    assert (
        "The importer still crashes on a malformed header "
        "(Source Task: another item)."
        in line
    )


def test_block_scrubs_task_id_from_public_id_field(tmp_path):
    task_id = "TESTER-20260909-045-verify-2-14-1"
    bugs_dir = tmp_path / "bugs"
    bug_path = _write_bug(
        bugs_dir, "BUG-0223", "public-id-carries-task-id",
        "A disclosed pre-existing defect.",
        public_id=f"KI-2026.09 (see {task_id})",
    )
    result = run_bash(
        tmp_path,
        f"source {_LIB} && build_known_issue_notes_block '{bug_path}'",
    )
    assert result.returncode == 0, result.stderr
    line = result.stdout.strip()
    assert task_id not in line
    # Positive assertion on the surviving text: no stranded slug fragment,
    # and the "(see ...)" label text survives around the removed identifier,
    # now filled with the scrub's neutral stand-in ("another item") instead
    # of left empty.
    assert not _STRANDED_HYPHEN_FRAGMENT_RE.search(line), line
    assert "-verify-2-14-1" not in line
    assert "**KI-2026.09 (see another item)**" in line


def test_block_has_one_bullet_per_bug_file(tmp_path):
    bugs_dir = tmp_path / "bugs"
    a = _write_bug(bugs_dir, "BUG-0001", "first", "First substance.")
    b = _write_bug(bugs_dir, "BUG-0002", "second", "Second substance.")
    result = run_bash(
        tmp_path,
        f"source {_LIB} && build_known_issue_notes_block '{a}' '{b}'",
    )
    assert result.returncode == 0, result.stderr
    lines = [l for l in result.stdout.splitlines() if l.strip()]
    assert len(lines) == 2
    assert "First substance" in lines[0]
    assert "Second substance" in lines[1]


def test_block_is_empty_with_no_bug_files(tmp_path):
    result = run_bash(tmp_path, f"source {_LIB} && build_known_issue_notes_block")
    assert result.returncode == 0
    assert result.stdout.strip() == ""


# The task-ID slug rule governs only the task-ID alternative's closing
# boundary; a bug whose Symptom carries no internal identifier at all
# must render byte-identically to its plain Symptom text — pinned here as an
# explicit byte-identical expected line rather than only a negative
# "no ID" check.
def test_block_renders_normal_symptom_byte_identically(tmp_path):
    bugs_dir = tmp_path / "bugs"
    bug_path = _write_bug(
        bugs_dir, "BUG-0300", "no-internal-identifier",
        "A perfectly ordinary defect with no identifiers of any kind, spanning two clauses - here is the second.",
    )
    result = run_bash(
        tmp_path,
        f"source {_LIB} && build_known_issue_notes_block '{bug_path}'",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == (
        "- A perfectly ordinary defect with no identifiers of any kind, "
        "spanning two clauses - here is the second. "
        "(internal-only, not yet disclosed) — No workaround available."
    )


# ---------------------------------------------------------------------------
# build_bugs_skipped_notes_block
# ---------------------------------------------------------------------------
#
# The "## Bugs Skipped" block CM authors by hand at release time
# previously passed through no scrub at all -- a hand-authored rationale
# bullet that quoted another release's TESTER finding text could re-leak
# that finding's own task ID into the shipped notes.
# build_bugs_skipped_notes_block gives that authoring path a driveable,
# scrubbed surface, mirroring the coverage above for the Known-Issues
# block.


def test_bugs_skipped_block_renders_one_row_per_pair(tmp_path):
    bugs_dir = tmp_path / "bugs"
    a = _write_bug(bugs_dir, "BUG-0400", "first", "First substance.")
    b = _write_bug(bugs_dir, "BUG-0401", "second", "Second substance.")
    result = run_bash(
        tmp_path,
        f"source {_LIB} && build_bugs_skipped_notes_block "
        f"'{a}' 'Deferred to next release.' '{b}' 'Low impact.'",
    )
    assert result.returncode == 0, result.stderr
    lines = [l for l in result.stdout.splitlines() if l.strip()]
    assert len(lines) == 2
    assert "First substance" in lines[0]
    assert "Deferred to next release." in lines[0]
    assert "Second substance" in lines[1]
    assert "Low impact." in lines[1]
    assert "BUG-0400" not in lines[0]
    assert "BUG-0401" not in lines[1]


@pytest.mark.parametrize("prefix", ["PM", "CODER", "WRITER", "CM", "TESTER", "PO"])
def test_bugs_skipped_block_scrubs_task_id_from_rationale(tmp_path, prefix):
    task_id = f"{prefix}-20260910-009-verify-2-14-2"
    bugs_dir = tmp_path / "bugs"
    bug_path = _write_bug(
        bugs_dir, "BUG-0402", "rationale-carries-task-id",
        "The importer crashes on a malformed header.",
    )
    rationale = f"Deferred; see {task_id} for the root cause."
    result = run_bash(
        tmp_path,
        f"source {_LIB} && build_bugs_skipped_notes_block '{bug_path}' '{rationale}'",
    )
    assert result.returncode == 0, result.stderr
    line = result.stdout.strip()
    assert task_id not in line
    assert prefix + "-20260910" not in line
    assert not _STRANDED_HYPHEN_FRAGMENT_RE.search(line), line
    # The removed identifier is replaced with the scrub's neutral stand-in
    # ("another item"), not deleted -- "see for the root cause" would not
    # parse; "see another item for the root cause" does.
    assert "Deferred; see another item for the root cause." in line


@pytest.mark.parametrize("prefix", ["PM", "CODER", "WRITER", "CM", "TESTER", "PO"])
def test_bugs_skipped_block_scrubs_task_id_from_symptom(tmp_path, prefix):
    task_id = f"{prefix}-20260910-010-verify-2-14-2"
    bugs_dir = tmp_path / "bugs"
    bug_path = _write_bug(
        bugs_dir, "BUG-0403", "symptom-carries-task-id",
        f"The importer crashes on a malformed header (Source Task: {task_id}).",
    )
    result = run_bash(
        tmp_path,
        f"source {_LIB} && build_bugs_skipped_notes_block '{bug_path}' 'Deferred to next release.'",
    )
    assert result.returncode == 0, result.stderr
    line = result.stdout.strip()
    assert task_id not in line
    assert prefix + "-20260910" not in line
    assert not _STRANDED_HYPHEN_FRAGMENT_RE.search(line), line


def test_bugs_skipped_block_cites_public_id_when_assigned(tmp_path):
    bugs_dir = tmp_path / "bugs"
    bug_path = _write_bug(
        bugs_dir, "BUG-0404", "disclosed-skip",
        "A disclosed pre-existing defect.",
        public_id="KI-2.12.0.4",
    )
    result = run_bash(
        tmp_path,
        f"source {_LIB} && build_bugs_skipped_notes_block '{bug_path}' 'Low impact.'",
    )
    assert result.returncode == 0, result.stderr
    line = result.stdout.strip()
    assert "KI-2.12.0.4" in line
    assert "internal-only" not in line


def test_bugs_skipped_block_defaults_public_id_to_internal_only(tmp_path):
    bugs_dir = tmp_path / "bugs"
    bug_path = _write_bug(bugs_dir, "BUG-0405", "undisclosed-skip", "An undisclosed defect.")
    result = run_bash(
        tmp_path,
        f"source {_LIB} && build_bugs_skipped_notes_block '{bug_path}' 'Will not fix.'",
    )
    assert result.returncode == 0, result.stderr
    line = result.stdout.strip()
    assert "internal-only" in line


def _append_multiline_severity_section(bug_path: pathlib.Path, value: str) -> None:
    # Appends a "## Severity" section shaped the way roughly half of this
    # project's own bug files already write it: the value, a blank line, a
    # word-wrapped explanatory paragraph (a single newline inside the
    # paragraph, not a blank-line pair), and a trailing horizontal rule --
    # the shape that previously spilled its whole body, newlines intact,
    # into a single table cell. The single embedded newline matters: a
    # whitespace run of two or more characters collapses to one space
    # elsewhere in this pipeline, so a paragraph written as one long line
    # would mask the exposure this test exists to catch.
    text = bug_path.read_text(encoding="utf-8")
    text += (
        f"\n## Severity\n\n{value}\n\n"
        "The rating is explained here across a paragraph that wraps\n"
        "onto a second physical line, which a table cell must never absorb.\n\n---\n"
    )
    bug_path.write_text(text, encoding="utf-8")


def _split_table_row(line: str) -> list[str]:
    # Splits a "| a | b | c | d |" row into its cell list the way a markdown
    # table reader would: a backslash-escaped pipe is literal content, not a
    # column boundary, matching the escape build_bugs_skipped_notes_block
    # emits.
    cells = re.split(r"(?<!\\)\|", line)
    assert cells[0].strip() == "" and cells[-1].strip() == "", line
    return [c.strip() for c in cells[1:-1]]


def test_bugs_skipped_block_flattens_multiline_severity_to_one_line_word(tmp_path):
    bugs_dir = tmp_path / "bugs"
    bug_path = _write_bug(
        bugs_dir, "BUG-0407", "multiline-severity",
        "A defect whose severity section carries an explanatory paragraph.",
    )
    _append_multiline_severity_section(bug_path, "medium")

    result = run_bash(
        tmp_path,
        f"source {_LIB} && build_bugs_skipped_notes_block '{bug_path}' 'Deferred to next release.'",
    )
    assert result.returncode == 0, result.stderr
    lines = [l for l in result.stdout.splitlines() if l.strip()]
    assert len(lines) == 1
    cells = _split_table_row(lines[0])
    assert len(cells) == 4
    assert cells[2] == "MEDIUM"


def test_bugs_skipped_block_splits_into_four_cells_when_symptom_carries_a_pipe(tmp_path):
    bugs_dir = tmp_path / "bugs"
    bug_path = _write_bug(
        bugs_dir, "BUG-0408", "symptom-carries-pipe",
        "The parser reads a | delimited value and mis-splits the row.",
    )
    result = run_bash(
        tmp_path,
        f"source {_LIB} && build_bugs_skipped_notes_block '{bug_path}' 'Deferred to next release.'",
    )
    assert result.returncode == 0, result.stderr
    lines = [l for l in result.stdout.splitlines() if l.strip()]
    assert len(lines) == 1
    cells = _split_table_row(lines[0])
    assert len(cells) == 4


def test_bugs_skipped_ship_block_has_n_plus_2_lines_for_multiline_severity_pairs(tmp_path):
    bugs_dir = tmp_path / "bugs"
    bug_ids = ["BUG-0600", "BUG-0601", "BUG-0602"]
    for i, bug_id in enumerate(bug_ids):
        bug_path = _write_bug(bugs_dir, bug_id, f"multiline-severity-pair-{i}", f"Defect number {i}.")
        _append_multiline_severity_section(bug_path, "high")

    report = _write_skipped_report(
        tmp_path,
        [{"filed_as": bug_id, "detail": f"Deferred item {i}."} for i, bug_id in enumerate(bug_ids)],
    )
    result = run_bash(
        tmp_path,
        f"source {_LIB} && cm_build_bugs_skipped_ship_block '{report}' '{bugs_dir}' "
        "&& printf '%s\\n' \"$_BUGS_SKIPPED_NOTES_BLOCK\"",
    )
    assert result.returncode == 0, result.stderr
    lines = [l for l in result.stdout.splitlines() if l.strip()]
    assert len(lines) == len(bug_ids) + 2


def test_bugs_skipped_block_is_empty_with_no_arguments(tmp_path):
    result = run_bash(tmp_path, f"source {_LIB} && build_bugs_skipped_notes_block")
    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_bugs_skipped_block_rejects_odd_argument_count(tmp_path):
    bugs_dir = tmp_path / "bugs"
    bug_path = _write_bug(bugs_dir, "BUG-0406", "missing-rationale", "A defect with no paired rationale.")
    result = run_bash(
        tmp_path,
        f"source {_LIB} && build_bugs_skipped_notes_block '{bug_path}'; echo \"rc=$?\"",
    )
    assert result.returncode == 0
    assert "rc=1" in result.stdout
    assert "ERROR" in result.stderr


# ---------------------------------------------------------------------------
# cm_stamp_release_notes_status
# ---------------------------------------------------------------------------


def _write_notes(tmp_path: pathlib.Path, name: str, body: str) -> pathlib.Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def test_stamp_known_issue_replaces_status_and_known_issues_section(tmp_path):
    notes = _write_notes(
        tmp_path, "notes.md",
        "# Release Notes: demo v0.0.0\n\n"
        "## Status\nPENDING-RELEASE\n\n"
        "## Summary\nTest.\n\n"
        "## Known Issues\n\n"
        "Issues that exist in this release. Users should be aware of these.\n\n"
        "- **<Issue title>** — Description and any available workaround.\n",
    )
    block = "- The importer crashes on a malformed header. (internal-only, not yet disclosed) — No workaround available."
    result = run_bash(
        tmp_path,
        f"source {_LIB} && cm_stamp_release_notes_status '{notes}' 'KNOWN-ISSUE' '{block}'",
    )
    assert result.returncode == 0, result.stderr
    text = notes.read_text(encoding="utf-8")
    assert "## Status\nKNOWN-ISSUE" in text
    assert "PENDING-RELEASE" not in text
    assert "malformed header" in text
    assert "<Issue title>" not in text


def test_stamp_known_issue_appends_section_when_absent(tmp_path):
    notes = _write_notes(
        tmp_path, "notes.md",
        "# Release Notes: demo v0.0.0\n\n## Status\nPENDING-RELEASE\n\n## Summary\nTest.\n",
    )
    block = "- Substance bullet. (internal-only, not yet disclosed) — No workaround available."
    result = run_bash(
        tmp_path,
        f"source {_LIB} && cm_stamp_release_notes_status '{notes}' 'KNOWN-ISSUE' '{block}'",
    )
    assert result.returncode == 0, result.stderr
    text = notes.read_text(encoding="utf-8")
    assert "## Known Issues" in text
    assert "Substance bullet" in text


def test_stamp_functional_never_touches_known_issues_section(tmp_path):
    original_ki = (
        "## Known Issues\n\n"
        "Issues that exist in this release. Users should be aware of these.\n\n"
        "- **<Issue title>** — Description and any available workaround.\n"
    )
    notes = _write_notes(
        tmp_path, "notes.md",
        "# Release Notes: demo v0.0.0\n\n## Status\nPENDING-RELEASE\n\n## Summary\nTest.\n\n" + original_ki,
    )
    result = run_bash(
        tmp_path,
        f"source {_LIB} && cm_stamp_release_notes_status '{notes}' 'FUNCTIONAL' ''",
    )
    assert result.returncode == 0, result.stderr
    text = notes.read_text(encoding="utf-8")
    assert "## Status\nFUNCTIONAL" in text
    assert "<Issue title>" in text  # untouched


def test_stamp_reports_failure_when_placeholder_absent(tmp_path):
    notes = _write_notes(
        tmp_path, "notes.md",
        "# Release Notes: demo v0.0.0\n\n## Status\nFUNCTIONAL\n\n## Summary\nTest.\n",
    )
    result = run_bash(
        tmp_path,
        f"source {_LIB} && cm_stamp_release_notes_status '{notes}' 'KNOWN-ISSUE' 'x'; echo \"rc=$?\"",
    )
    assert result.returncode == 0
    assert "rc=1" in result.stdout
    assert "WARNING" in result.stderr


# ---------------------------------------------------------------------------
# cm_stamp_release_notes_status — 4th arg: bugs_skipped_block
# ---------------------------------------------------------------------------
#
# This is the render-path wiring itself: a non-empty bugs_skipped_block
# replaces "## Bugs Skipped" wholesale, whatever an author typed there —
# structurally closing the gap where the block previously reached shipped
# notes exactly as an author typed it. Independent of target_status, unlike
# "## Known Issues" (KNOWN-ISSUE-only).


def test_stamp_replaces_hand_authored_bugs_skipped_section(tmp_path):
    notes = _write_notes(
        tmp_path, "notes.md",
        "# Release Notes: demo v0.0.0\n\n"
        "## Status\nPENDING-RELEASE\n\n"
        "## Summary\nTest.\n\n"
        "## Bugs Skipped\n"
        "Hand-typed text that leaks CODER-20260910-023-some-task.\n\n"
        "## Known Issues\nNone.\n",
    )
    block = (
        "| Substance (what was wrong) | Public ID (if disclosed) | Severity | Rationale for Skipping |\n"
        "|---|---|---|---|\n"
        "| The importer crashes on a malformed header. | internal-only | LOW | Deferred to next release. |"
    )
    result = run_bash(
        tmp_path,
        f"source {_LIB} && cm_stamp_release_notes_status '{notes}' 'KNOWN-BUGS' '' '{block}'",
    )
    assert result.returncode == 0, result.stderr
    text = notes.read_text(encoding="utf-8")
    assert "## Status\nKNOWN-BUGS" in text
    assert "CODER-20260910-023" not in text
    assert "The importer crashes on a malformed header." in text
    assert "## Known Issues\nNone." in text  # untouched


def test_stamp_bugs_skipped_replacement_is_independent_of_target_status(tmp_path):
    notes = _write_notes(
        tmp_path, "notes.md",
        "# Release Notes: demo v0.0.0\n\n"
        "## Status\nPENDING-RELEASE\n\n"
        "## Summary\nTest.\n\n"
        "## Bugs Skipped\nNone.\n",
    )
    block = "| Substance | Public ID | Severity | Rationale |\n|---|---|---|---|\n| A defect. | internal-only | LOW | Deferred. |"
    result = run_bash(
        tmp_path,
        f"source {_LIB} && cm_stamp_release_notes_status '{notes}' 'FUNCTIONAL' '' '{block}'",
    )
    assert result.returncode == 0, result.stderr
    text = notes.read_text(encoding="utf-8")
    assert "## Status\nFUNCTIONAL" in text
    assert "A defect." in text


def test_stamp_leaves_bugs_skipped_untouched_when_block_empty(tmp_path):
    notes = _write_notes(
        tmp_path, "notes.md",
        "# Release Notes: demo v0.0.0\n\n"
        "## Status\nPENDING-RELEASE\n\n"
        "## Summary\nTest.\n\n"
        "## Bugs Skipped\nNone.\n",
    )
    result = run_bash(
        tmp_path,
        f"source {_LIB} && cm_stamp_release_notes_status '{notes}' 'FUNCTIONAL' ''",
    )
    assert result.returncode == 0, result.stderr
    text = notes.read_text(encoding="utf-8")
    assert "## Bugs Skipped\nNone." in text


# ---------------------------------------------------------------------------
# cm_stamp_known_issue_marker
# ---------------------------------------------------------------------------


def test_marker_stamps_new_field_and_leaves_status_open(tmp_path):
    bugs_dir = tmp_path / "bugs"
    bug_path = _write_bug(bugs_dir, "BUG-0042", "legacy-artefact-defect", "A pre-existing crash.")
    result = run_bash(
        tmp_path,
        f"source {_LIB} && cm_stamp_known_issue_marker 'v2.12.0' '{bug_path}'",
    )
    assert result.returncode == 0, result.stderr
    assert "1 stamped" in result.stdout
    text = bug_path.read_text(encoding="utf-8")
    assert "## Known Issue Carry Forward\nv2.12.0" in text
    assert "## Status\nopen" in text


def test_marker_is_idempotent_on_second_invocation(tmp_path):
    bugs_dir = tmp_path / "bugs"
    bug_path = _write_bug(bugs_dir, "BUG-0042", "legacy-artefact-defect", "A pre-existing crash.")
    run_bash(tmp_path, f"source {_LIB} && cm_stamp_known_issue_marker 'v2.12.0' '{bug_path}'")
    before = bug_path.read_text(encoding="utf-8")

    result = run_bash(
        tmp_path,
        f"source {_LIB} && cm_stamp_known_issue_marker 'v2.12.0' '{bug_path}'",
    )
    assert result.returncode == 0, result.stderr
    assert "already complete, continuing" in result.stdout
    after = bug_path.read_text(encoding="utf-8")
    assert before == after


def test_marker_never_overwrites_an_existing_value(tmp_path):
    bugs_dir = tmp_path / "bugs"
    bug_path = _write_bug(bugs_dir, "BUG-0042", "legacy-artefact-defect", "A pre-existing crash.")
    bug_path.write_text(
        bug_path.read_text(encoding="utf-8") + "\n## Known Issue Carry Forward\nv2.11.0\n",
        encoding="utf-8",
    )
    result = run_bash(
        tmp_path,
        f"source {_LIB} && cm_stamp_known_issue_marker 'v2.12.0' '{bug_path}'",
    )
    assert result.returncode == 0, result.stderr
    text = bug_path.read_text(encoding="utf-8")
    assert "v2.11.0" in text
    assert "v2.12.0" not in text


def test_marker_untouched_bugs_are_byte_identical(tmp_path):
    bugs_dir = tmp_path / "bugs"
    marked_bug = _write_bug(bugs_dir, "BUG-0001", "known-issue-cause", "The causing defect.")
    other_bug = _write_bug(bugs_dir, "BUG-0002", "unrelated-bug", "An unrelated open bug.")
    other_before = other_bug.read_bytes()

    result = run_bash(
        tmp_path,
        f"source {_LIB} && cm_stamp_known_issue_marker 'v2.12.0' '{marked_bug}'",
    )
    assert result.returncode == 0, result.stderr
    assert other_bug.read_bytes() == other_before


def test_marker_with_no_bug_files_is_a_silent_no_op(tmp_path):
    result = run_bash(tmp_path, f"source {_LIB} && cm_stamp_known_issue_marker 'v2.12.0'; echo \"rc=$?\"")
    assert result.returncode == 0
    assert "rc=0" in result.stdout
