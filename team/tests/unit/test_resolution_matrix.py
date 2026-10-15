"""
test_resolution_matrix.py
==========================
Unit tests for pgai_agent_kanban.overwatch.resolution_matrix — the v2.0.0
Goal 1 ("the resolution matrix") config-declared matrix and fail-closed
matcher.

Coverage:
  - The shipped default matrix loads exactly one CANCEL-AND-REBUNDLE row
    from config, not a hardcoded value.
  - The 2026-07-18 golden fixture facts match CANCEL-AND-REBUNDLE.
  - Any single fact off (each of the six, one at a time) yields HALT-hard.
  - Unknown/unmatched shapes (unrecognized trigger_class, empty matrix)
    fail closed to HALT-hard.
  - Matrix structural validation: missing/extra row keys, wrong types, the
    reserved HALT-hard resolution literal, malformed top-level YAML.
  - Fact extraction: TESTER report findings, read from parse_report by
    matrix_findings (the same findings, in the same order and with the same
    fields, as the CM release path's readers take from the same report, for
    every heading shape and nesting, and never a field under a later
    shallower heading as a finding's own, and for every Filed As and
    Regression field-line shape -- backticked, annotated, irregularly
    spaced -- the same filed_as and regression the shared parser reads),
    the retry-ceiling
    Python counterpart of pp_max_rc_retries, and the git-backed evidence
    helpers (RC lineage cleanliness, tag existence) against real temp git
    repos.
  - End-to-end resolve_block() against a full BlockEvidence fixture,
    including the fail-closed defaults when evidence is simply absent.

All tests use tmp_path; no live filesystem paths. Function names describe
behaviour under test, never bug/task IDs (roles/TESTER.md "Test Authoring
Guidelines", Anti-pattern 6).
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess

import pytest

from pgai_agent_kanban.overwatch.resolution_matrix import (
    CANCEL_AND_REBUNDLE,
    HALT_HARD,
    BlockEvidence,
    BlockFacts,
    CeilingConfigError,
    EvidenceError,
    InvalidBlockTriageValue,
    InvalidOverwatchResolveValue,
    MatrixError,
    MatrixRow,
    extract_facts,
    findings_all_filed,
    git_tag_exists,
    load_matrix,
    match_resolution,
    matrix_findings,
    operator_commit_in_rc_lineage,
    resolve_block,
    resolve_block_triage,
    resolve_max_rc_retries,
    resolve_overwatch_resolve,
)
from pgai_agent_kanban.cm.ship_policy_report_inputs import all_findings
from pgai_agent_kanban.cm.stop_bug_files import filed_bug_id, report_findings
from pgai_agent_kanban.reports.tester_report import parse_report
from tests.fixtures.binary_guard import require_binary
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env


@pytest.fixture(autouse=True)
def _require_git() -> None:
    """Skip every test in this module when git is not on PATH.

    Every test here builds, inspects, or drives a real git repository; a
    module-wide autouse guard keeps that requirement in one place instead
    of repeating a per-test check at each git-dependent call site.
    """
    require_binary("git", label="resolution-matrix")

# ---------------------------------------------------------------------------
# Golden facts (mirrors the 2026-07-18 golden fixture: ship-policy HALT, all
# findings filed, nothing shipped, no approval gate, clean lineage, attempts
# below ceiling).
# ---------------------------------------------------------------------------


def _golden_facts(**overrides) -> BlockFacts:
    base = dict(
        trigger_class="ship-policy-halt",
        findings_all_filed=True,
        shipped_or_tagged=False,
        approval_gate_present=False,
        operator_commits_in_lineage=False,
        attempts_below_ceiling=True,
        attempt=1,
        ceiling=3,
    )
    base.update(overrides)
    return BlockFacts(**base)


def _shipped_matrix() -> list:
    from pgai_agent_kanban.overwatch.resolution_matrix import load_matrix as _lm

    return _lm()


# ---------------------------------------------------------------------------
# load_matrix — shipped default
# ---------------------------------------------------------------------------


def test_shipped_default_matrix_has_exactly_one_cancel_and_rebundle_row() -> None:
    """The shipped v2.0.0 default matrix ships exactly one row, and it is
    the CANCEL-AND-REBUNDLE golden row — not hardcoded in Python."""
    matrix = load_matrix()
    assert len(matrix) == 1
    row = matrix[0]
    assert isinstance(row, MatrixRow)
    assert row.resolution == CANCEL_AND_REBUNDLE
    assert row.trigger_class == "ship-policy-halt"
    assert row.findings_all_filed is True
    assert row.shipped_or_tagged is False
    assert row.approval_gate_present is False
    assert row.operator_commits_in_lineage is False
    assert row.attempts_below_ceiling is True


def test_kanban_root_operator_override_replaces_shipped_default(tmp_path: pathlib.Path) -> None:
    """An operator override at <kanban_root>/resolution-matrix.yaml wins over
    the shipped default when kanban_root is supplied."""
    override = tmp_path / "resolution-matrix.yaml"
    override.write_text(
        "rows:\n"
        "  - trigger_class: ship-policy-halt\n"
        "    findings_all_filed: true\n"
        "    shipped_or_tagged: false\n"
        "    approval_gate_present: false\n"
        "    operator_commits_in_lineage: false\n"
        "    attempts_below_ceiling: true\n"
        "    resolution: OPERATOR-CUSTOM-ROW\n",
        encoding="utf-8",
    )
    matrix = load_matrix(kanban_root=str(tmp_path))
    assert len(matrix) == 1
    assert matrix[0].resolution == "OPERATOR-CUSTOM-ROW"


def test_explicit_matrix_path_wins_over_kanban_root_override(tmp_path: pathlib.Path) -> None:
    """An explicit matrix_path argument takes precedence over kanban_root."""
    (tmp_path / "resolution-matrix.yaml").write_text(
        "rows: []\n", encoding="utf-8"
    )
    explicit = tmp_path / "explicit-matrix.yaml"
    explicit.write_text(
        "rows:\n"
        "  - trigger_class: ship-policy-halt\n"
        "    findings_all_filed: true\n"
        "    shipped_or_tagged: false\n"
        "    approval_gate_present: false\n"
        "    operator_commits_in_lineage: false\n"
        "    attempts_below_ceiling: true\n"
        "    resolution: EXPLICIT-ROW\n",
        encoding="utf-8",
    )
    matrix = load_matrix(matrix_path=str(explicit), kanban_root=str(tmp_path))
    assert len(matrix) == 1
    assert matrix[0].resolution == "EXPLICIT-ROW"


def test_empty_rows_list_is_a_valid_fully_fail_closed_matrix(tmp_path: pathlib.Path) -> None:
    """An empty 'rows' list is structurally valid — every block then
    resolves to HALT-hard, which is a legitimate fail-closed configuration."""
    matrix_file = tmp_path / "empty-matrix.yaml"
    matrix_file.write_text("rows: []\n", encoding="utf-8")
    matrix = load_matrix(matrix_path=str(matrix_file))
    assert matrix == []
    assert match_resolution(_golden_facts(), matrix) == HALT_HARD


# ---------------------------------------------------------------------------
# load_matrix — structural validation failures
# ---------------------------------------------------------------------------


def test_missing_matrix_file_raises_matrix_error(tmp_path: pathlib.Path) -> None:
    with pytest.raises(MatrixError, match="Cannot read"):
        load_matrix(matrix_path=str(tmp_path / "does-not-exist.yaml"))


def test_non_mapping_top_level_raises_matrix_error(tmp_path: pathlib.Path) -> None:
    matrix_file = tmp_path / "matrix.yaml"
    matrix_file.write_text("- not\n- a\n- mapping\n", encoding="utf-8")
    with pytest.raises(MatrixError, match="must be a YAML mapping"):
        load_matrix(matrix_path=str(matrix_file))


def test_missing_rows_key_raises_matrix_error(tmp_path: pathlib.Path) -> None:
    matrix_file = tmp_path / "matrix.yaml"
    matrix_file.write_text("not_rows: []\n", encoding="utf-8")
    with pytest.raises(MatrixError, match="missing the required 'rows'"):
        load_matrix(matrix_path=str(matrix_file))


def test_row_missing_required_key_raises_matrix_error(tmp_path: pathlib.Path) -> None:
    matrix_file = tmp_path / "matrix.yaml"
    matrix_file.write_text(
        "rows:\n"
        "  - trigger_class: ship-policy-halt\n"
        "    findings_all_filed: true\n"
        "    resolution: X\n",
        encoding="utf-8",
    )
    with pytest.raises(MatrixError, match="missing required key"):
        load_matrix(matrix_path=str(matrix_file))


def test_row_with_extra_key_raises_matrix_error(tmp_path: pathlib.Path) -> None:
    matrix_file = tmp_path / "matrix.yaml"
    matrix_file.write_text(
        "rows:\n"
        "  - trigger_class: ship-policy-halt\n"
        "    findings_all_filed: true\n"
        "    shipped_or_tagged: false\n"
        "    approval_gate_present: false\n"
        "    operator_commits_in_lineage: false\n"
        "    attempts_below_ceiling: true\n"
        "    resolution: X\n"
        "    extra_field: oops\n",
        encoding="utf-8",
    )
    with pytest.raises(MatrixError, match="unrecognized key"):
        load_matrix(matrix_path=str(matrix_file))


def test_row_with_non_boolean_fact_raises_matrix_error(tmp_path: pathlib.Path) -> None:
    matrix_file = tmp_path / "matrix.yaml"
    matrix_file.write_text(
        "rows:\n"
        "  - trigger_class: ship-policy-halt\n"
        "    findings_all_filed: \"yes\"\n"
        "    shipped_or_tagged: false\n"
        "    approval_gate_present: false\n"
        "    operator_commits_in_lineage: false\n"
        "    attempts_below_ceiling: true\n"
        "    resolution: X\n",
        encoding="utf-8",
    )
    with pytest.raises(MatrixError, match="must be a boolean"):
        load_matrix(matrix_path=str(matrix_file))


def test_row_resolution_cannot_be_the_halt_hard_sentinel(tmp_path: pathlib.Path) -> None:
    matrix_file = tmp_path / "matrix.yaml"
    matrix_file.write_text(
        "rows:\n"
        "  - trigger_class: ship-policy-halt\n"
        "    findings_all_filed: true\n"
        "    shipped_or_tagged: false\n"
        "    approval_gate_present: false\n"
        "    operator_commits_in_lineage: false\n"
        "    attempts_below_ceiling: true\n"
        "    resolution: HALT-hard\n",
        encoding="utf-8",
    )
    with pytest.raises(MatrixError, match="reserved no-match sentinel"):
        load_matrix(matrix_path=str(matrix_file))


# ---------------------------------------------------------------------------
# match_resolution — golden fixture and single-fact-off fixtures
# ---------------------------------------------------------------------------


def test_golden_facts_match_cancel_and_rebundle() -> None:
    matrix = _shipped_matrix()
    assert match_resolution(_golden_facts(), matrix) == CANCEL_AND_REBUNDLE


@pytest.mark.parametrize(
    "override",
    [
        {"trigger_class": "operator-halt"},
        {"findings_all_filed": False},
        {"shipped_or_tagged": True},
        {"approval_gate_present": True},
        {"operator_commits_in_lineage": True},
        {"attempts_below_ceiling": False},
    ],
)
def test_any_single_fact_off_yields_halt_hard(override: dict) -> None:
    matrix = _shipped_matrix()
    facts = _golden_facts(**override)
    assert match_resolution(facts, matrix) == HALT_HARD


def test_unrecognized_trigger_class_is_an_unknown_shape_and_fails_closed() -> None:
    matrix = _shipped_matrix()
    facts = _golden_facts(trigger_class="some-new-trigger-nobody-declared")
    assert match_resolution(facts, matrix) == HALT_HARD


# ---------------------------------------------------------------------------
# TESTER report finding parsing / findings_all_filed
# ---------------------------------------------------------------------------


_REPORT_ALL_FILED = """\
# Verification Report: rc/v2.0.1

## Recommendation
SHIP-WITH-CONCERNS

## Systemic Risk
low

## Findings

### Finding 1: stale assertion in dashboard test

**Category:** stale-assertion
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low
**Filed As:** BUG-0042 (Path C)

Detail text here.

### Finding 2: missing guard fixture

**Category:** bug
**Fix Effort:** medium
**Systemic Risk:** low
**Filed As:** BUG-0043 (Path C)

More detail.
"""

_REPORT_ONE_UNFILED = _REPORT_ALL_FILED.replace("**Filed As:** BUG-0043 (Path C)", "**Filed As:** N/A")

_REPORT_NO_FINDINGS = """\
# Verification Report: rc/v2.0.1

## Recommendation
PASS

## Systemic Risk
low

## Findings

None.
"""


def _matrix_findings(report_text: str) -> list:
    """The matrix's findings for *report_text*, read the way resolution_stage
    and extract_facts() read a report on disk."""
    return matrix_findings(parse_report(report_text))


def test_matrix_findings_carry_each_findings_filed_as_id() -> None:
    # A Filed As value with an annotation after its id reads as the id alone,
    # in canonical form (differential.md, "How to read a row": values are
    # given in the parser's vocabulary, filed_as being the canonical id a
    # Filed As value names).
    findings = _matrix_findings(_REPORT_ALL_FILED)
    assert len(findings) == 2
    assert findings[0]["filed_as"] == "BUG-0042"
    assert findings[1]["filed_as"] == "BUG-0043"


def test_findings_all_filed_true_when_every_finding_has_a_bug_id() -> None:
    findings = _matrix_findings(_REPORT_ALL_FILED)
    assert findings_all_filed(findings) is True


def test_findings_all_filed_false_when_one_finding_unfiled() -> None:
    findings = _matrix_findings(_REPORT_ONE_UNFILED)
    assert findings_all_filed(findings) is False


def test_findings_all_filed_vacuously_true_for_no_findings() -> None:
    findings = _matrix_findings(_REPORT_NO_FINDINGS)
    assert findings == []
    assert findings_all_filed(findings) is True


def test_matrix_findings_keep_the_title_after_the_heading_label() -> None:
    findings = _matrix_findings(_REPORT_ALL_FILED)
    assert [f["title"] for f in findings] == [
        "stale assertion in dashboard test",
        "missing guard fixture",
    ]


@pytest.mark.parametrize(
    "value, regression",
    [("yes", True), ("no", False), ("yes (introduced by this RC)", True), ("unclear", None)],
)
def test_matrix_findings_read_regression_as_a_flag(value: str, regression) -> None:
    # An annotated "yes (...)" reads as yes, as differential.md's regression
    # rows resolve it: the leading token is the reading.
    report = f"### Finding 1: a\n**Systemic Risk:** high\n**Regression:** {value}\n"
    assert _matrix_findings(report)[0]["regression"] is regression


def test_matrix_findings_name_a_priority_filing_as_filed() -> None:
    # differential.md's filed_as rows: a finding filed as a priority is filed.
    findings = _matrix_findings("### Finding 1: a\n**Filed As:** PRIORITY-0001\n")
    assert findings[0]["filed_as"] == "PRIORITY-0001"
    assert findings_all_filed(findings) is True


def _cm_reader_findings(tmp_path: pathlib.Path, report_text: str) -> dict:
    """The findings each CM release-path reader takes from *report_text*,
    projected onto the five values a matrix finding carries."""
    report = tmp_path / "report.md"
    report.write_text(report_text, encoding="utf-8")

    def project(findings: list) -> list:
        regression = {"yes": True, "no": False}
        return [
            {
                "title": f.title,
                "category": f.category,
                "filed_as": f.filed_as,
                "regression": regression.get(f.regression),
                "body": f.body,
            }
            for f in findings
        ]

    return {
        "stop list / Bugs Skipped (stop_bug_files.report_findings)": project(
            report_findings(str(report))
        ),
        "ship-policy inputs (ship_policy_report_inputs.all_findings)": project(
            all_findings(parse_report(report))
        ),
    }


# A finding at heading level 5, and a "### Finding" followed by a later
# "## Lint Findings" section whose field lines are not the finding's own.
_DEEP_FINDING_REPORT = """# Verification Report: rc/v9.9.9

## Findings

### Step 6

#### Acceptance criteria

##### Finding 1: deep finding
**Category:** bug
**Systemic Risk:** high
**Regression:** yes (introduced by this RC's fix of BUG-0042)
**Filed As:** BUG-0099

Traces to BUG-0042.
"""

_FINDING_THEN_LINT_SECTION_REPORT = """# Verification Report: rc/v9.9.9

## Findings

### Finding 1: flat finding
**Category:** bug
**Systemic Risk:** high
**Regression:** no
**Filed As:** none

## Lint Findings (non-blocking)
**Regression:** yes
**Filed As:** BUG-0007
"""


@pytest.mark.parametrize(
    "report_text, expected",
    [
        (
            _DEEP_FINDING_REPORT,
            [("deep finding", "BUG-0099", True)],
        ),
        (
            _FINDING_THEN_LINT_SECTION_REPORT,
            [("flat finding", None, False)],
        ),
    ],
    ids=["##### Finding 1:", "### Finding then ## Lint Findings"],
)
def test_matrix_findings_are_the_findings_the_cm_readers_read(
    tmp_path: pathlib.Path, report_text: str, expected: list
) -> None:
    """The resolution matrix and every CM release-path reader take the same
    findings, with the same values, from one report."""
    findings = _matrix_findings(report_text)

    assert [(f["title"], f["filed_as"], f["regression"]) for f in findings] == expected
    for reader, cm_findings in _cm_reader_findings(tmp_path, report_text).items():
        assert cm_findings == findings, f"{reader} read {cm_findings!r}, the matrix {findings!r}"
    assert all("Lint Findings" not in f["body"] for f in findings)


# Each heading shape, and whether the one TESTER-report parser
# (pgai_agent_kanban.reports.tester_report, docstring Rule 1) opens a finding
# for it. "###Finding 1:" and "## Finding 1:" open one: the parser's
# heading-variants scenario fixture
# (tests/fixtures/tester_reports/scenarios/heading-variants.md) pins both,
# and every CM release-path reader already reads them so.
_PARSER_HEADING_SHAPES = [
    ("### Finding 1:", True),
    ("#### Finding 1:", True),
    ("##### Finding 1:", True),
    ("###### Finding 1:", True),
    ("###Finding 1:", True),
    ("### Findings summary", False),
    ("## Findings", False),
    ("## Finding 1:", True),
    ("### Finding: no number", True),
]

_UNFILED_PROBE_REPORT = """## Findings

{heading} heading-shape probe
**Category:** bug
**Filed As:** none

{nested}## Lint Findings (non-blocking)
None.
"""

_NESTED_FILED_FINDING = "#### Finding 2: inner detail\n**Filed As:** BUG-0002\n\n"


@pytest.mark.parametrize(
    "heading, nested, opens",
    [(heading, "", opens) for heading, opens in _PARSER_HEADING_SHAPES]
    + [("### Finding 1:", _NESTED_FILED_FINDING, True)],
    ids=[heading for heading, _ in _PARSER_HEADING_SHAPES]
    + ["### Finding 1: with a nested #### Finding 2:"],
)
def test_matrix_findings_match_the_cm_readers_on_every_heading_shape(
    tmp_path: pathlib.Path, heading: str, nested: str, opens: bool
) -> None:
    """The matrix and the CM readers agree on every heading shape and
    nesting, and an unfiled finding the release path reads is unfiled to
    findings_all_filed too -- whatever its heading depth."""
    report = _UNFILED_PROBE_REPORT.format(heading=heading, nested=nested)
    findings = _matrix_findings(report)

    for reader, cm_findings in _cm_reader_findings(tmp_path, report).items():
        assert cm_findings == findings, f"{reader} read {cm_findings!r}, the matrix {findings!r}"
    assert bool(findings) is opens
    if opens:
        # "none" names no filing: the finding is unfiled (differential.md,
        # "How to read a row": a not-filed value reads as no filing).
        assert findings[0]["filed_as"] is None
        assert findings_all_filed(findings) is False
    if nested:
        assert [f["filed_as"] for f in findings] == [None, "BUG-0002"]


def test_field_under_a_later_shallower_heading_is_not_the_findings_own() -> None:
    report = "### Finding 1: a\n**Filed As:** BUG-0001\n\n### Step 7\n**Filed As:** none\n"
    findings = _matrix_findings(report)
    assert len(findings) == 1
    assert findings[0]["filed_as"] == "BUG-0001"
    assert "Step 7" not in findings[0]["body"]
    assert findings_all_filed(findings) is True


# Field-line shapes that once read differently through OVERWATCH's own
# field patterns than through the release path's reader: a backticked or
# annotated not-filed value, a label with two spaces inside it, a
# backticked or annotated Regression. Each row: the field line, then the
# filed_as and regression the matrix finding must carry and whether
# findings_all_filed counts the finding as filed. The last row is a control:
# a backticked bug id stays filed.
_FIELD_LINE_SHAPES = [
    pytest.param("**Filed As:** none", None, None, False, id="bold-none"),
    pytest.param("**Filed As:** `none`", None, None, False, id="backticked-none"),
    pytest.param("Filed As: none (not filed)", None, None, False, id="annotated-not-filed"),
    pytest.param("Filed  As: none", None, None, False, id="two-spaces-in-label"),
    pytest.param("Regression: `yes`", None, True, False, id="backticked-regression-yes"),
    pytest.param("**Regression:** Yes (introduced here)", None, True, False, id="annotated-regression-yes"),
    pytest.param("**Filed As:** `BUG-0905`", "BUG-0905", None, True, id="backticked-bug-id"),
]


@pytest.mark.parametrize("field_line, filed_as, regression, all_filed", _FIELD_LINE_SHAPES)
def test_matrix_findings_read_each_field_line_shape_like_the_shared_parser(
    field_line: str, filed_as, regression, all_filed: bool
) -> None:
    """OVERWATCH's matrix finding reads Filed As and Regression exactly as
    the shared parser's Finding does -- the reading the release path's Bugs
    Skipped and Known Issues readers take through filed_bug_id -- for every
    field-line shape, and findings_all_filed never counts a backticked or
    annotated not-filed value as filed."""
    report = parse_report(f"### Finding 1: x\n**Category:** bug\n{field_line}\n")
    (shared,) = all_findings(report)
    (finding,) = matrix_findings(report)

    assert finding["filed_as"] == filed_as
    assert finding["regression"] is regression
    assert findings_all_filed([finding]) is all_filed

    # The same reading as the shared parser and the release path's reader.
    assert finding["filed_as"] == shared.filed_as
    assert (filed_bug_id(shared) or None) == finding["filed_as"]
    assert finding["regression"] is {"yes": True, "no": False}.get(shared.regression)
    if "Filed" in field_line:
        assert "filed_as" in shared.raw, f"the parser did not read {field_line!r} as a Filed As line"


@pytest.mark.parametrize("sentinel", ["", "N/A", "none", "-", "TBD", "not filed"])
def test_findings_all_filed_recognizes_not_filed_sentinels(sentinel: str) -> None:
    assert findings_all_filed([{"filed_as": sentinel}]) is False


# A report as TESTER writes it: passing checks carry a Category but no Filed
# As line (team/roles/TESTER.md, "## Findings": only a bug, gap or
# stale-assertion carries Filed As), and the one defect is filed.
_REPORT_PASSES_AND_ONE_FILED_BUG = """\
# Verification Report: rc/v9.9.9

## Recommendation
SHIP-WITH-CONCERNS

## Findings

### Finding 1: unit suite green

**Category:** pass

All unit tests pass.

### Finding 2: integration suite green

**Category:** pass

All integration tests pass.

### Finding 3: dashboard renders a stale count

**Category:** bug
**Fix Effort:** small
**Systemic Risk:** low
**Filed As:** BUG-0001 (Path C)

Detail.
"""

_FILED_BUG_LINE = "**Filed As:** BUG-0001 (Path C)\n"


def test_matrix_findings_carry_each_findings_parsed_category() -> None:
    findings = _matrix_findings(_REPORT_PASSES_AND_ONE_FILED_BUG)
    assert [f["category"] for f in findings] == ["pass", "pass", "bug"]
    assert [f["filed_as"] for f in findings] == [None, None, "BUG-0001"]


def test_findings_all_filed_skips_pass_findings_when_every_defect_is_filed() -> None:
    findings = _matrix_findings(_REPORT_PASSES_AND_ONE_FILED_BUG)
    assert findings_all_filed(findings) is True


def test_findings_all_filed_false_when_the_bug_beside_pass_findings_is_unfiled() -> None:
    report = _REPORT_PASSES_AND_ONE_FILED_BUG.replace(_FILED_BUG_LINE, "")
    assert report != _REPORT_PASSES_AND_ONE_FILED_BUG
    findings = _matrix_findings(report)
    assert findings[2]["filed_as"] is None
    assert findings_all_filed(findings) is False


def test_findings_all_filed_skips_a_pass_with_caveat_finding_beside_filed_bugs() -> None:
    report = _REPORT_ALL_FILED + (
        "\n### Finding 3: retry path slow but correct\n\n"
        "**Category:** pass-with-caveat\n\n"
        "Works; the retry waits longer than it needs to.\n"
    )
    findings = _matrix_findings(report)
    assert [f["category"] for f in findings] == ["stale-assertion", "bug", "pass-with-caveat"]
    assert findings[2]["filed_as"] is None
    assert findings_all_filed(findings) is True


def test_findings_all_filed_counts_an_unfiled_finding_with_no_category_line() -> None:
    report = _REPORT_PASSES_AND_ONE_FILED_BUG + (
        "\n### Finding 4: observation with no category\n\nSomething looked off.\n"
    )
    findings = _matrix_findings(report)
    assert findings[3]["category"] is None
    assert findings[3]["filed_as"] is None
    assert findings_all_filed(findings) is False


def test_findings_all_filed_counts_an_unfiled_finding_with_an_unrecognised_category() -> None:
    report = _REPORT_PASSES_AND_ONE_FILED_BUG + (
        "\n### Finding 4: odd category\n\n**Category:** passing\n"
    )
    findings = _matrix_findings(report)
    assert findings[3]["category"] is None
    assert findings_all_filed(findings) is False


def test_findings_all_filed_counts_an_unfiled_pre_existing_bug() -> None:
    report = _REPORT_PASSES_AND_ONE_FILED_BUG + (
        "\n### Finding 4: old defect\n\n**Category:** pre-existing-bug\n"
    )
    findings = _matrix_findings(report)
    assert findings[3]["category"] == "pre-existing-bug"
    assert findings_all_filed(findings) is False


def test_findings_all_filed_vacuously_true_for_an_all_pass_report() -> None:
    report = _REPORT_PASSES_AND_ONE_FILED_BUG.split("### Finding 3:")[0]
    findings = _matrix_findings(report)
    assert [f["category"] for f in findings] == ["pass", "pass"]
    assert findings_all_filed(findings) is True


def test_findings_all_filed_vacuously_true_for_an_empty_findings_list() -> None:
    assert findings_all_filed([]) is True


@pytest.mark.parametrize(
    "finding",
    [
        pytest.param({"filed_as": None}, id="no-category-key"),
        pytest.param({"category": None, "filed_as": None}, id="none-category"),
        pytest.param({"category": "PASS", "filed_as": None}, id="non-token-case"),
        pytest.param({"category": ["pass"], "filed_as": None}, id="non-string-category"),
    ],
)
def test_findings_all_filed_counts_a_caller_finding_without_a_parser_pass_token(
    finding: dict,
) -> None:
    """A pre-parsed BlockEvidence.tester_findings item is skipped only for
    the parser's own pass tokens; anything else fails closed."""
    assert findings_all_filed([finding]) is False


# ---------------------------------------------------------------------------
# resolve_max_rc_retries — Python counterpart of pp_max_rc_retries
# ---------------------------------------------------------------------------


def _write_cfg(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_resolve_max_rc_retries_returns_kanban_default_when_no_project_override(
    tmp_path: pathlib.Path,
) -> None:
    _write_cfg(tmp_path / "kanban.cfg", "[release]\nmax_rc_retries = 3\n")
    assert resolve_max_rc_retries("myproj", str(tmp_path)) == 3


def test_resolve_max_rc_retries_project_override_wins_over_kanban_default(
    tmp_path: pathlib.Path,
) -> None:
    _write_cfg(tmp_path / "kanban.cfg", "[release]\nmax_rc_retries = 3\n")
    _write_cfg(
        tmp_path / "projects" / "pgai-agent-kanban" / "project.cfg",
        "[project]\nproject_name = pgai-agent-kanban\n\n[release]\nmax_rc_retries = 1\n",
    )
    assert resolve_max_rc_retries("pgai-agent-kanban", str(tmp_path)) == 1


def test_resolve_max_rc_retries_fails_loud_when_absent_from_both(tmp_path: pathlib.Path) -> None:
    with pytest.raises(CeilingConfigError, match="max_rc_retries is required but absent"):
        resolve_max_rc_retries("myproj", str(tmp_path))


def test_resolve_max_rc_retries_rejects_non_integer_value(tmp_path: pathlib.Path) -> None:
    _write_cfg(tmp_path / "kanban.cfg", "[release]\nmax_rc_retries = notanumber\n")
    with pytest.raises(CeilingConfigError, match="invalid value"):
        resolve_max_rc_retries("myproj", str(tmp_path))


# ---------------------------------------------------------------------------
# resolve_overwatch_resolve — Python counterpart of pp_overwatch_resolve.
# Same project.cfg-overrides-kanban.cfg precedence as
# resolve_max_rc_retries, opposite fail-safe direction: absence resolves to
# False rather than raising.
# ---------------------------------------------------------------------------


def test_resolve_overwatch_resolve_absent_from_both_defaults_false(
    tmp_path: pathlib.Path,
) -> None:
    assert resolve_overwatch_resolve("myproj", str(tmp_path)) is False


def test_resolve_overwatch_resolve_absent_kanban_cfg_missing_defaults_false(
    tmp_path: pathlib.Path,
) -> None:
    """No kanban.cfg on disk at all still resolves to False -- unlike
    resolve_max_rc_retries, an unreadable/missing kanban.cfg is not an
    error for this key."""
    assert resolve_overwatch_resolve("myproj", str(tmp_path)) is False


def test_resolve_overwatch_resolve_kanban_default_used_when_project_silent(
    tmp_path: pathlib.Path,
) -> None:
    _write_cfg(tmp_path / "kanban.cfg", "[release]\noverwatch_resolve = true\n")
    assert resolve_overwatch_resolve("myproj", str(tmp_path)) is True


def test_resolve_overwatch_resolve_project_true_wins_over_kanban_false(
    tmp_path: pathlib.Path,
) -> None:
    _write_cfg(tmp_path / "kanban.cfg", "[release]\noverwatch_resolve = false\n")
    _write_cfg(
        tmp_path / "projects" / "myproj" / "project.cfg",
        "[project]\nproject_name = myproj\n\n[release]\noverwatch_resolve = true\n",
    )
    assert resolve_overwatch_resolve("myproj", str(tmp_path)) is True


def test_resolve_overwatch_resolve_project_false_wins_over_kanban_true(
    tmp_path: pathlib.Path,
) -> None:
    _write_cfg(tmp_path / "kanban.cfg", "[release]\noverwatch_resolve = true\n")
    _write_cfg(
        tmp_path / "projects" / "myproj" / "project.cfg",
        "[project]\nproject_name = myproj\n\n[release]\noverwatch_resolve = false\n",
    )
    assert resolve_overwatch_resolve("myproj", str(tmp_path)) is False


def test_resolve_overwatch_resolve_rejects_unrecognized_value(tmp_path: pathlib.Path) -> None:
    _write_cfg(tmp_path / "kanban.cfg", "[release]\noverwatch_resolve = yes\n")
    with pytest.raises(InvalidOverwatchResolveValue, match="unrecognized value 'yes'"):
        resolve_overwatch_resolve("myproj", str(tmp_path))


@pytest.mark.parametrize(
    "kanban_cfg_text, project_cfg_text",
    [
        pytest.param(
            "[release]\noverwatch_resolve = false\n",
            "[project]\nproject_name = myproj\n\n[release]\noverwatch_resolve = true\n",
            id="project_true_over_kanban_false",
        ),
        pytest.param(
            "[release]\noverwatch_resolve = true\n",
            "[project]\nproject_name = myproj\n",
            id="project_absent_kanban_true",
        ),
        pytest.param(
            "",
            "[project]\nproject_name = myproj\n",
            id="absent_everywhere",
        ),
    ],
)
def test_resolve_overwatch_resolve_agrees_with_bash_accessor(
    tmp_path: pathlib.Path, kanban_cfg_text: str, project_cfg_text: str
) -> None:
    """The Python read path and the bash pp_overwatch_resolve accessor must
    return the same answer for the same cfg pair -- asserted here by
    running both rather than by inspection."""
    if kanban_cfg_text:
        _write_cfg(tmp_path / "kanban.cfg", kanban_cfg_text)
    _write_cfg(tmp_path / "projects" / "myproj" / "project.cfg", project_cfg_text)

    python_answer = "true" if resolve_overwatch_resolve("myproj", str(tmp_path)) else "false"

    dev_tree_root = pathlib.Path(__file__).resolve().parents[2]
    bash_lib = dev_tree_root / "scripts" / "lib" / "project_paths.sh"
    result = subprocess.run(
        ["bash", "-c", f"source {bash_lib} && pp_overwatch_resolve myproj"],
        env={**os.environ, "KANBAN_ROOT": str(tmp_path)},
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == python_answer


# ---------------------------------------------------------------------------
# resolve_block_triage — Python counterpart of pp_block_triage. Same
# project.cfg-overrides-kanban.cfg precedence under [overwatch]; absence
# resolves to True (the shipped default); a non-boolean value raises.
# ---------------------------------------------------------------------------


def test_resolve_block_triage_absent_from_both_defaults_true(tmp_path: pathlib.Path) -> None:
    _write_cfg(tmp_path / "kanban.cfg", "[wake]\nzero_progress_cap = 3\n")
    _write_cfg(tmp_path / "projects" / "myproj" / "project.cfg", "[project]\nproject_name = myproj\n")
    assert resolve_block_triage("myproj", str(tmp_path)) is True


def test_resolve_block_triage_no_cfg_files_at_all_defaults_true(tmp_path: pathlib.Path) -> None:
    assert resolve_block_triage("myproj", str(tmp_path)) is True


def test_resolve_block_triage_kanban_value_used_when_project_silent(tmp_path: pathlib.Path) -> None:
    _write_cfg(tmp_path / "kanban.cfg", "[overwatch]\nblock_triage = false\n")
    _write_cfg(tmp_path / "projects" / "myproj" / "project.cfg", "[project]\nproject_name = myproj\n")
    assert resolve_block_triage("myproj", str(tmp_path)) is False


def test_resolve_block_triage_project_false_wins_over_kanban_true(tmp_path: pathlib.Path) -> None:
    _write_cfg(tmp_path / "kanban.cfg", "[overwatch]\nblock_triage = true\n")
    _write_cfg(
        tmp_path / "projects" / "myproj" / "project.cfg",
        "[project]\nproject_name = myproj\n\n[overwatch]\nblock_triage = false\n",
    )
    assert resolve_block_triage("myproj", str(tmp_path)) is False


def test_resolve_block_triage_project_true_wins_over_kanban_false(tmp_path: pathlib.Path) -> None:
    _write_cfg(tmp_path / "kanban.cfg", "[overwatch]\nblock_triage = false\n")
    _write_cfg(
        tmp_path / "projects" / "myproj" / "project.cfg",
        "[project]\nproject_name = myproj\n\n[overwatch]\nblock_triage = true\n",
    )
    assert resolve_block_triage("myproj", str(tmp_path)) is True


def test_resolve_block_triage_rejects_non_boolean_value(tmp_path: pathlib.Path) -> None:
    project_cfg = tmp_path / "projects" / "myproj" / "project.cfg"
    _write_cfg(project_cfg, "[project]\nproject_name = myproj\n\n[overwatch]\nblock_triage = yes\n")
    with pytest.raises(InvalidBlockTriageValue, match="unrecognized value 'yes'") as excinfo:
        resolve_block_triage("myproj", str(tmp_path))
    assert str(project_cfg) in str(excinfo.value)
    assert "true|false" in str(excinfo.value)


_BLOCK_TRIAGE_PARITY_CASES = [
    pytest.param("", "[project]\nproject_name = myproj\n", id="absent_everywhere"),
    pytest.param(
        "[overwatch]\nblock_triage = true\n",
        "[project]\nproject_name = myproj\n\n[overwatch]\nblock_triage = false\n",
        id="project_false_over_kanban_true",
    ),
    pytest.param(
        "[overwatch]\nblock_triage = false\n",
        "[project]\nproject_name = myproj\n\n[overwatch]\nblock_triage = true\n",
        id="project_true_over_kanban_false",
    ),
    pytest.param(
        "[overwatch]\nblock_triage = false\n",
        "[project]\nproject_name = myproj\n",
        id="project_silent_kanban_false",
    ),
    pytest.param(
        "[overwatch]\nblock_triage = false\n",
        "[project]\nproject_name = myproj\n\n[overwatch]\nblock_triage =\n",
        id="project_blank_falls_through",
    ),
    pytest.param(
        "[overwatch]\nblock_triage = true\n",
        "[project]\nproject_name = myproj\n\n[overwatch]\nblock_triage = yes\n",
        id="project_non_boolean_refuses",
    ),
    pytest.param(
        "[overwatch]\nblock_triage = on\n",
        "[project]\nproject_name = myproj\n",
        id="kanban_non_boolean_refuses",
    ),
]


@pytest.mark.parametrize("kanban_cfg_text, project_cfg_text", _BLOCK_TRIAGE_PARITY_CASES)
def test_resolve_block_triage_agrees_with_bash_accessor(
    tmp_path: pathlib.Path, kanban_cfg_text: str, project_cfg_text: str
) -> None:
    """The Python resolver and the bash pp_block_triage accessor give the
    same answer for the same cfg pair -- 'true', 'false', or a refusal --
    so the triage sweep's two entry points can never disagree."""
    if kanban_cfg_text:
        _write_cfg(tmp_path / "kanban.cfg", kanban_cfg_text)
    _write_cfg(tmp_path / "projects" / "myproj" / "project.cfg", project_cfg_text)

    try:
        python_answer = "true" if resolve_block_triage("myproj", str(tmp_path)) else "false"
    except InvalidBlockTriageValue:
        python_answer = "refused"

    dev_tree_root = pathlib.Path(__file__).resolve().parents[2]
    bash_lib = dev_tree_root / "scripts" / "lib" / "project_paths.sh"
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    result = subprocess.run(
        ["bash", "-c", f"source {bash_lib} && pp_block_triage myproj"],
        env=build_scrubbed_env(
            home_dir=tmp_path / "home",
            extra_env={"KANBAN_ROOT": str(tmp_path), "PGAI_AGENT_KANBAN_TEMP_DIR": str(scratch)},
        ),
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        bash_answer = result.stdout.strip()
    else:
        assert "block_triage.invalid_value" in result.stderr, result.stderr
        bash_answer = "refused"
    assert bash_answer == python_answer, (
        f"bash said {bash_answer!r}, python said {python_answer!r}; bash stderr: {result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# Git-backed evidence helpers
# ---------------------------------------------------------------------------


def _git(repo: pathlib.Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def _init_repo_with_rc_branch(tmp_path: pathlib.Path, rc_branch: str = "ai_rc/v2.0.1") -> pathlib.Path:
    """Init a repo with an ai_main initial commit and an rc branch off it."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "ai_main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("init\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "initial commit")
    _git(repo, "checkout", "-q", "-b", rc_branch)
    return repo


def _merge_feature_branch(repo: pathlib.Path, target_branch: str, feature_name: str, filename: str) -> None:
    """Simulate a CODER task: branch from target, commit, merge back --no-ff."""
    _git(repo, "checkout", "-q", "-b", feature_name, target_branch)
    (repo / filename).write_text(f"{feature_name}\n")
    _git(repo, "add", filename)
    _git(repo, "commit", "-q", "-m", f"Add {filename}")
    _git(repo, "checkout", "-q", target_branch)
    _git(repo, "merge", "--no-ff", "-q", "-m", f"Merge {feature_name} into {target_branch}", feature_name)


def test_operator_commit_in_rc_lineage_false_when_only_merge_commits(tmp_path: pathlib.Path) -> None:
    repo = _init_repo_with_rc_branch(tmp_path)
    _merge_feature_branch(repo, "ai_rc/v2.0.1", "feature/CODER-1", "one.txt")
    _merge_feature_branch(repo, "ai_rc/v2.0.1", "feature/CODER-2", "two.txt")
    assert operator_commit_in_rc_lineage(str(repo), "ai_rc/v2.0.1", "ai_main") is False


def test_operator_commit_in_rc_lineage_true_when_direct_commit_present(tmp_path: pathlib.Path) -> None:
    repo = _init_repo_with_rc_branch(tmp_path)
    _merge_feature_branch(repo, "ai_rc/v2.0.1", "feature/CODER-1", "one.txt")
    # A direct commit on the RC branch, bypassing the feature-branch-and-merge
    # convention -- the operator-commit signal.
    _git(repo, "checkout", "-q", "ai_rc/v2.0.1")
    (repo / "direct.txt").write_text("hand-placed\n")
    _git(repo, "add", "direct.txt")
    _git(repo, "commit", "-q", "-m", "operator direct edit")
    assert operator_commit_in_rc_lineage(str(repo), "ai_rc/v2.0.1", "ai_main") is True


def test_operator_commit_in_rc_lineage_false_when_no_commits_ahead(tmp_path: pathlib.Path) -> None:
    """An RC branch that has diverged with zero commits yet (freshly opened) is clean."""
    repo = _init_repo_with_rc_branch(tmp_path)
    assert operator_commit_in_rc_lineage(str(repo), "ai_rc/v2.0.1", "ai_main") is False


def test_operator_commit_in_rc_lineage_raises_evidence_error_on_bad_ref(tmp_path: pathlib.Path) -> None:
    repo = _init_repo_with_rc_branch(tmp_path)
    with pytest.raises(EvidenceError):
        operator_commit_in_rc_lineage(str(repo), "no-such-branch", "ai_main")


def test_git_tag_exists_true_after_tagging(tmp_path: pathlib.Path) -> None:
    repo = _init_repo_with_rc_branch(tmp_path)
    _git(repo, "tag", "v2.0.1")
    assert git_tag_exists(str(repo), "v2.0.1") is True


def test_git_tag_exists_false_when_untagged(tmp_path: pathlib.Path) -> None:
    repo = _init_repo_with_rc_branch(tmp_path)
    assert git_tag_exists(str(repo), "v2.0.1") is False


def test_git_tag_exists_raises_evidence_error_on_bad_path(tmp_path: pathlib.Path) -> None:
    with pytest.raises(EvidenceError):
        git_tag_exists(str(tmp_path / "not-a-repo"), "v2.0.1")


# ---------------------------------------------------------------------------
# extract_facts / resolve_block — end-to-end BlockEvidence fixtures
# ---------------------------------------------------------------------------


def _write_halt_file(path: pathlib.Path, *, from_cm: bool) -> None:
    if from_cm:
        path.write_text(
            "# HALT created by CM at 2026-07-18T10:00:00Z\n"
            "# Reason: TESTER systemic_risk=high in rc/v2.0.1\n"
            "# Resolution: Operator review required.\n",
            encoding="utf-8",
        )
    else:
        path.write_text(
            "# HALT created by operator at 2026-07-18T09:00:00Z\n"
            "# Reason: governance review\n",
            encoding="utf-8",
        )


def _golden_evidence(tmp_path: pathlib.Path, **overrides) -> BlockEvidence:
    """Build a full BlockEvidence fixture matching the golden facts, with
    every field explicitly provided (real git repo, real files)."""
    repo = _init_repo_with_rc_branch(tmp_path, rc_branch="ai_rc/v2.0.1")
    _merge_feature_branch(repo, "ai_rc/v2.0.1", "feature/CODER-1", "one.txt")

    halt_file = tmp_path / "HALT"
    _write_halt_file(halt_file, from_cm=True)

    report_file = tmp_path / "report.md"
    report_file.write_text(_REPORT_ALL_FILED, encoding="utf-8")

    release_state = tmp_path / "release-state" / "v2.0.1.json"
    release_state.parent.mkdir(parents=True, exist_ok=True)
    release_state.write_text(
        json.dumps({"rc": "v2.0.1", "outcome": "in_progress", "attempt": 1}), encoding="utf-8"
    )

    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)

    evidence = BlockEvidence(
        halt_file_path=str(halt_file),
        tester_report_path=str(report_file),
        release_state_path=str(release_state),
        dev_tree_path=str(repo),
        target_version="v2.0.1",
        rc_branch="ai_rc/v2.0.1",
        base_branch="ai_main",
        project_tasks_dir=str(tasks_dir),
        max_rc_retries=3,
    )
    for key, value in overrides.items():
        setattr(evidence, key, value)
    return evidence


def test_extract_facts_golden_fixture_produces_golden_facts(tmp_path: pathlib.Path) -> None:
    evidence = _golden_evidence(tmp_path)
    facts = extract_facts(evidence)
    assert facts.trigger_class == "ship-policy-halt"
    assert facts.findings_all_filed is True
    assert facts.shipped_or_tagged is False
    assert facts.approval_gate_present is False
    assert facts.operator_commits_in_lineage is False
    assert facts.attempts_below_ceiling is True
    assert facts.attempt == 1
    assert facts.ceiling == 3


def test_resolve_block_golden_fixture_resolves_to_cancel_and_rebundle(tmp_path: pathlib.Path) -> None:
    evidence = _golden_evidence(tmp_path)
    result = resolve_block(evidence)
    assert result.resolution == CANCEL_AND_REBUNDLE
    assert result.matched_row is not None
    assert result.matched_row.resolution == CANCEL_AND_REBUNDLE


def test_resolve_block_operator_halt_file_declines_to_halt_hard(tmp_path: pathlib.Path) -> None:
    evidence = _golden_evidence(tmp_path)
    _write_halt_file(pathlib.Path(evidence.halt_file_path), from_cm=False)
    result = resolve_block(evidence)
    assert result.resolution == HALT_HARD
    assert result.facts.trigger_class == "operator-halt"


def test_resolve_block_unfiled_finding_declines_to_halt_hard(tmp_path: pathlib.Path) -> None:
    evidence = _golden_evidence(tmp_path)
    pathlib.Path(evidence.tester_report_path).write_text(_REPORT_ONE_UNFILED, encoding="utf-8")
    result = resolve_block(evidence)
    assert result.resolution == HALT_HARD
    assert result.facts.findings_all_filed is False


def test_resolve_block_shipped_outcome_declines_to_halt_hard(tmp_path: pathlib.Path) -> None:
    evidence = _golden_evidence(tmp_path)
    state_path = pathlib.Path(evidence.release_state_path)
    state_path.write_text(json.dumps({"rc": "v2.0.1", "outcome": "shipped", "attempt": 1}), encoding="utf-8")
    result = resolve_block(evidence)
    assert result.resolution == HALT_HARD
    assert result.facts.shipped_or_tagged is True


def test_resolve_block_approval_gate_present_declines_to_halt_hard(tmp_path: pathlib.Path) -> None:
    evidence = _golden_evidence(tmp_path)
    (pathlib.Path(evidence.project_tasks_dir) / "HUMAN-APPROVE-v2.0.1-001").mkdir(parents=True)
    result = resolve_block(evidence)
    assert result.resolution == HALT_HARD
    assert result.facts.approval_gate_present is True


def test_resolve_block_operator_commit_in_lineage_declines_to_halt_hard(tmp_path: pathlib.Path) -> None:
    evidence = _golden_evidence(tmp_path)
    repo = pathlib.Path(evidence.dev_tree_path)
    _git(repo, "checkout", "-q", "ai_rc/v2.0.1")
    (repo / "direct.txt").write_text("hand-placed\n")
    _git(repo, "add", "direct.txt")
    _git(repo, "commit", "-q", "-m", "operator direct edit")
    result = resolve_block(evidence)
    assert result.resolution == HALT_HARD
    assert result.facts.operator_commits_in_lineage is True


def test_resolve_block_attempt_at_ceiling_declines_to_halt_hard(tmp_path: pathlib.Path) -> None:
    evidence = _golden_evidence(tmp_path)
    pathlib.Path(evidence.release_state_path).write_text(
        json.dumps({"rc": "v2.0.1", "outcome": "in_progress", "attempt": 3}), encoding="utf-8"
    )
    result = resolve_block(evidence)
    assert result.resolution == HALT_HARD
    assert result.facts.attempts_below_ceiling is False
    assert result.facts.attempt == 3
    assert result.facts.ceiling == 3


def test_resolve_block_with_no_evidence_at_all_fails_closed_to_halt_hard(tmp_path: pathlib.Path) -> None:
    """An almost-empty BlockEvidence (only the ceiling resolvable) proves
    none of the other five facts -- every fail-closed default should push
    away from the golden row, resolving to HALT-hard."""
    evidence = BlockEvidence(max_rc_retries=3)
    result = resolve_block(evidence)
    assert result.resolution == HALT_HARD
    assert result.facts.trigger_class == "unknown"
    assert result.facts.findings_all_filed is False
    assert result.facts.shipped_or_tagged is True
    assert result.facts.approval_gate_present is True
    assert result.facts.operator_commits_in_lineage is True
    # attempt is undeterminable -> attempts_below_ceiling fails closed too.
    assert result.facts.attempts_below_ceiling is False
    assert result.facts.attempt is None


def test_resolve_block_raises_ceiling_config_error_when_ceiling_unresolvable(
    tmp_path: pathlib.Path,
) -> None:
    """The ceiling is the one fact that is never silently defaulted --
    absence raises rather than failing closed to a fact value."""
    evidence = BlockEvidence()
    with pytest.raises(CeilingConfigError):
        resolve_block(evidence)
